"""Short transactional units; no inference or VNC waits inside an ORM session."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.sanitization import sanitize
from app.persistence.models import (
    AgentEvent,
    DesktopSession,
    Outbox,
    ServiceHeartbeat,
    Task,
    TaskCredential,
    TaskRun,
)
from app.persistence.secrets import CredentialCipher

TERMINAL = {"succeeded", "failed"}


def utcnow() -> datetime:
    return datetime.now(UTC)


class LifecycleConflict(RuntimeError):
    """A requested lifecycle transition is unsafe or stale."""


class DestinationBusy(LifecycleConflict):
    """An existing destination owner must close before this task can reserve."""


@dataclass(frozen=True)
class ClaimedTask:
    task_id: UUID
    run_id: UUID
    instruction: str
    destination_key: str
    accepted_at: datetime
    action_cutoff_at: datetime
    close_target_at: datetime
    deadline_at: datetime


class TaskRepository:
    def __init__(
        self,
        sessions: async_sessionmaker,
        cipher: CredentialCipher,
        *,
        cutoff_seconds: float = 290,
        close_seconds: float = 295,
        task_seconds: float = 300,
    ):
        self.sessions = sessions
        self.cipher = cipher
        self.cutoff_seconds, self.close_seconds, self.task_seconds = (
            cutoff_seconds,
            close_seconds,
            task_seconds,
        )

    async def accept_with_outbox(
        self, *, instruction: str, url: str, password: str, team_id: str, accepted_at: datetime
    ) -> UUID:
        task_id = uuid4()
        cutoff = accepted_at + timedelta(seconds=self.cutoff_seconds)
        target = accepted_at + timedelta(seconds=self.close_seconds)
        deadline = accepted_at + timedelta(seconds=self.task_seconds)
        encrypted = self.cipher.encrypt(url=url, password=password)
        async with self.sessions.begin() as db:
            db.add(
                Task(
                    id=task_id,
                    team_id=team_id,
                    instruction_private=instruction,
                    destination_key=self.cipher.destination_key(url),
                    accepted_at=accepted_at,
                    action_cutoff_at=cutoff,
                    close_target_at=target,
                    deadline_at=deadline,
                )
            )
            await db.flush()
            db.add(
                TaskCredential(
                    task_id=task_id,
                    encrypted_payload=encrypted,
                    key_version=self.cipher.key_version,
                    expires_at=deadline,
                )
            )
            db.add(
                Outbox(
                    id=uuid4(),
                    task_id=task_id,
                    event_type="task.ready",
                    created_at=accepted_at,
                    available_at=accepted_at,
                )
            )
        return task_id

    async def expire_queued(self, *, now: datetime | None = None, batch_size: int = 100) -> int:
        """Queue timeout needs no close proof: queued tasks have never reserved sockets."""
        now = now or utcnow()
        async with self.sessions.begin() as db:
            rows = list(
                (
                    await db.scalars(
                        select(Task)
                        .where(Task.internal_status == "queued", Task.action_cutoff_at <= now)
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for task in rows:
                task.internal_status, task.completed_at = "failed", now
                task.terminal_reason = "expired_in_queue"
                task.sanitized_summary = (
                    "El plazo seguro expiró en cola; no se abrió el escritorio."
                )
                task.version += 1
            if rows:
                await db.execute(
                    delete(TaskCredential).where(
                        TaskCredential.task_id.in_([task.id for task in rows])
                    )
                )
            return len(rows)

    async def mark_stale_runs_uncertain(
        self, *, before: datetime, now: datetime | None = None, batch_size: int = 100
    ) -> list[UUID]:
        """Alarm only: stale heartbeats do not establish socket/process closure.

        The supervisor still needs to isolate and confirm the old owner before
        finalization. Never create another run or release destination exclusion.
        """
        now = now or utcnow()
        marked = []
        async with self.sessions.begin() as db:
            tasks = list(
                (
                    await db.scalars(
                        select(Task)
                        .join(TaskRun, TaskRun.task_id == Task.id)
                        .where(
                            Task.internal_status.in_(["running", "closing"]),
                            TaskRun.status.in_(["running", "closing"]),
                            TaskRun.heartbeat_at < before,
                        )
                        .limit(batch_size)
                        .with_for_update(of=Task, skip_locked=True)
                    )
                ).all()
            )
            for task in tasks:
                runs = list(
                    (
                        await db.scalars(
                            select(TaskRun)
                            .where(
                                TaskRun.task_id == task.id,
                                TaskRun.status.in_(["running", "closing"]),
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                for run in runs:
                    if run.heartbeat_at >= before:
                        continue
                    task.internal_status = "closing"
                    task.version += 1
                    run.status = "uncertain"
                    desktops = list(
                        (
                            await db.scalars(
                                select(DesktopSession)
                                .where(DesktopSession.run_id == run.id)
                                .with_for_update()
                            )
                        ).all()
                    )
                    for desktop in desktops:
                        if desktop.status != "closed":
                            desktop.status = "uncertain"
                            desktop.revoked_at = now
                    marked.append(run.id)
            return marked

    async def get_claim(self, run_id: UUID) -> ClaimedTask | None:
        async with self.sessions() as db:
            row = (
                await db.execute(
                    select(Task, TaskRun)
                    .join(TaskRun, TaskRun.task_id == Task.id)
                    .where(TaskRun.id == run_id)
                )
            ).first()
            if row is None:
                return None
            task, run = row
            return ClaimedTask(
                task.id,
                run.id,
                task.instruction_private,
                task.destination_key,
                task.accepted_at,
                task.action_cutoff_at,
                task.close_target_at,
                task.deadline_at,
            )

    async def heartbeat_run(self, run_id: UUID) -> None:
        async with self.sessions.begin() as db:
            await db.execute(
                update(TaskRun)
                .where(TaskRun.id == run_id, TaskRun.status.in_(["running", "closing"]))
                .values(heartbeat_at=utcnow())
            )

    async def get_for_team(self, task_id: UUID, team_id: str) -> dict | None:
        async with self.sessions() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id, Task.team_id == team_id))
            if task is None:
                return None
            return {
                "estado": {"succeeded": "terminada", "failed": "fallida"}.get(
                    task.internal_status, "en_curso"
                ),
                "resumen": task.sanitized_summary,
            }

    async def claim(
        self,
        task_id: UUID,
        worker_id: str,
        *,
        graph_version: str = "v1",
        model_version: str = "unset",
        now: datetime | None = None,
    ) -> ClaimedTask | None:
        now = now or utcnow()
        async with self.sessions.begin() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None or task.internal_status != "queued":
                return None
            if now >= task.action_cutoff_at:
                task.internal_status = "failed"
                task.completed_at = now
                task.terminal_reason = "expired_in_queue"
                task.sanitized_summary = (
                    "El plazo seguro expiró en cola; no se abrió el escritorio."
                )
                task.version += 1
                await db.execute(delete(TaskCredential).where(TaskCredential.task_id == task_id))
                return None
            run_id = uuid4()
            task.internal_status = "running"
            task.version += 1
            db.add(
                TaskRun(
                    id=run_id,
                    task_id=task_id,
                    attempt_no=1,
                    thread_id=str(run_id),
                    worker_id=worker_id,
                    graph_version=graph_version,
                    model_version=model_version,
                    status="running",
                    started_at=now,
                    heartbeat_at=now,
                )
            )
            return ClaimedTask(
                task_id,
                run_id,
                task.instruction_private,
                task.destination_key,
                task.accepted_at,
                task.action_cutoff_at,
                task.close_target_at,
                task.deadline_at,
            )

    async def begin_closing(self, task_id: UUID) -> bool:
        async with self.sessions.begin() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise LifecycleConflict("Unknown task")
            if task.internal_status in TERMINAL:
                return False
            task.internal_status = "closing"
            task.version += 1
            await db.execute(
                update(TaskRun)
                .where(TaskRun.task_id == task_id, TaskRun.status == "running")
                .values(status="closing")
            )
            return True

    async def finalize_after_close(
        self,
        task_id: UUID,
        *,
        succeeded: bool,
        summary: str,
        reason_code: str,
        now: datetime | None = None,
    ) -> None:
        now = now or utcnow()
        status = "succeeded" if succeeded else "failed"
        async with self.sessions.begin() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None:
                raise LifecycleConflict("Unknown task")
            if task.internal_status in TERMINAL:
                if (task.internal_status, task.sanitized_summary, task.terminal_reason) == (
                    status,
                    summary,
                    reason_code,
                ):
                    return
                raise LifecycleConflict("Terminal task is immutable")
            if task.internal_status != "closing":
                raise LifecycleConflict("Task must enter closing before terminal publication")
            runs = list(
                (
                    await db.scalars(
                        select(TaskRun).where(TaskRun.task_id == task_id).with_for_update()
                    )
                ).all()
            )
            if runs:
                sessions = list(
                    (
                        await db.scalars(
                            select(DesktopSession)
                            .where(DesktopSession.run_id.in_([run.id for run in runs]))
                            .with_for_update()
                        )
                    ).all()
                )
                if any(
                    session.status != "closed" or not session.close_evidence for session in sessions
                ):
                    raise LifecycleConflict("Desktop closure is not confirmed")
                # No session means no socket was ever reserved. Otherwise the owner has supplied closure evidence.
                for run in runs:
                    run.status = "closed"
                    run.stopped_at = now
            task.internal_status, task.completed_at = status, now
            task.sanitized_summary, task.terminal_reason = summary, reason_code
            task.version += 1
            await db.execute(delete(TaskCredential).where(TaskCredential.task_id == task_id))

    async def credentials_for_authority(
        self, task_id: UUID, *, now: datetime | None = None
    ) -> dict[str, str]:
        """Use only from socket authority, never pass the return value to graph/model."""
        async with self.sessions() as db:
            row = await db.scalar(
                select(TaskCredential).where(
                    TaskCredential.task_id == task_id, TaskCredential.expires_at > (now or utcnow())
                )
            )
            if row is None or row.key_version != self.cipher.key_version:
                raise LifecycleConflict("Credentials unavailable or expired")
            return self.cipher.decrypt(row.encrypted_payload)


class SessionRepository:
    def __init__(self, sessions: async_sessionmaker):
        self.sessions = sessions

    async def reserve(
        self, run_id: UUID, owner_id: str, *, now: datetime | None = None, lease_seconds: int = 10
    ) -> DesktopSession:
        now = now or utcnow()
        async with self.sessions.begin() as db:
            task_id = await db.scalar(select(TaskRun.task_id).where(TaskRun.id == run_id))
            task = await db.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if task is None or task.internal_status != "running" or now >= task.action_cutoff_at:
                raise LifecycleConflict("Task cannot reserve a desktop")
            run = await db.scalar(select(TaskRun).where(TaskRun.id == run_id).with_for_update())
            if run.status != "running":
                raise LifecycleConflict("Run is not active")
            # One transactional lock per hashed destination, independent from lease expiry.
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": int(task.destination_key[:16], 16) % (2**63)},
            )
            busy = await db.scalar(
                select(DesktopSession.id).where(
                    DesktopSession.destination_key == task.destination_key,
                    DesktopSession.status != "closed",
                )
            )
            if busy is not None:
                raise DestinationBusy("Destination already has an owner; expiry is not closure")
            epoch = (
                await db.scalar(
                    select(func.max(DesktopSession.epoch)).where(
                        DesktopSession.destination_key == task.destination_key
                    )
                )
                or 0
            ) + 1
            session = DesktopSession(
                id=uuid4(),
                run_id=run_id,
                destination_key=task.destination_key,
                epoch=epoch,
                owner_id=owner_id,
                status="reserved",
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            db.add(session)
            await db.flush()
            return session

    async def mark_connected(self, session_id: UUID, epoch: int) -> None:
        async with self.sessions.begin() as db:
            row = await db.scalar(
                select(DesktopSession).where(DesktopSession.id == session_id).with_for_update()
            )
            if row is None or row.epoch != epoch or row.status != "reserved":
                raise LifecycleConflict("Session cannot become connected")
            row.status, row.opened_at = "connected", utcnow()

    async def validate(self, session_id: UUID, epoch: int) -> bool:
        async with self.sessions() as db:
            session = await db.scalar(select(DesktopSession).where(DesktopSession.id == session_id))
            return (
                session is not None
                and session.epoch == epoch
                and session.status in {"reserved", "connected"}
            )

    async def revoke(self, session_id: UUID, *, now: datetime | None = None) -> None:
        async with self.sessions.begin() as db:
            session = await db.scalar(
                select(DesktopSession).where(DesktopSession.id == session_id).with_for_update()
            )
            if session is None:
                raise LifecycleConflict("Unknown desktop session")
            if session.status != "closed":
                session.status, session.revoked_at = "revoking", now or utcnow()

    async def mark_closed(
        self, session_id: UUID, *, epoch: int, close_evidence: dict, now: datetime | None = None
    ) -> None:
        # Evidence originates from supervisor/authority, never from a language model.
        if (
            close_evidence.get("transport_closed") is not True
            or close_evidence.get("owner_stopped") is not True
        ):
            raise LifecycleConflict("Transport and owner closure must both be confirmed")
        async with self.sessions.begin() as db:
            row = await db.scalar(
                select(DesktopSession).where(DesktopSession.id == session_id).with_for_update()
            )
            if row is None or row.epoch != epoch:
                raise LifecycleConflict("Unknown or stale desktop session")
            if row.status == "closed":
                return
            if row.status not in {"revoking", "uncertain"}:
                raise LifecycleConflict("Session must be revoked before closure")
            row.status, row.closed_at, row.close_evidence = (
                "closed",
                now or utcnow(),
                close_evidence,
            )


class EventRecorder:
    def __init__(self, sessions: async_sessionmaker):
        self.sessions = sessions

    async def append(
        self,
        run_id: UUID,
        event_type: str,
        *,
        agent_name: str = "system",
        parent_event_id: UUID | None = None,
        payload: dict | None = None,
        node_name: str | None = None,
        duration_ms: int | None = None,
    ) -> UUID:
        event_id = uuid4()
        async with self.sessions.begin() as db:
            sequence = await db.scalar(
                update(TaskRun)
                .where(TaskRun.id == run_id)
                .values(next_event_sequence=TaskRun.next_event_sequence + 1)
                .returning(TaskRun.next_event_sequence)
            )
            if sequence is None:
                raise LifecycleConflict("Unknown run")
            db.add(
                AgentEvent(
                    id=event_id,
                    run_id=run_id,
                    sequence=sequence,
                    agent_name=agent_name,
                    event_type=event_type,
                    parent_event_id=parent_event_id,
                    sanitized_payload=sanitize(payload or {}),
                    node_name=node_name,
                    duration_ms=duration_ms,
                    occurred_at=utcnow(),
                )
            )
        return event_id


class HeartbeatRepository:
    def __init__(self, sessions: async_sessionmaker):
        self.sessions = sessions

    async def beat(
        self, component: str, instance_id: str, *, ready: bool, now: datetime | None = None
    ) -> None:
        async with self.sessions.begin() as db:
            stmt = insert(ServiceHeartbeat).values(
                component=component,
                instance_id=instance_id,
                heartbeat_at=now or utcnow(),
                ready=ready,
                sanitized_details={},
            )
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["component", "instance_id"],
                    set_={"heartbeat_at": stmt.excluded.heartbeat_at, "ready": ready},
                )
            )

    async def ready(
        self,
        components: tuple[str, ...] = ("worker", "gateway", "supervisor"),
        *,
        now: datetime | None = None,
        window_seconds: int = 10,
    ) -> bool:
        cutoff = (now or utcnow()) - timedelta(seconds=window_seconds)
        async with self.sessions() as db:
            rows = set(
                (
                    await db.scalars(
                        select(ServiceHeartbeat.component).where(
                            ServiceHeartbeat.ready.is_(True),
                            ServiceHeartbeat.heartbeat_at >= cutoff,
                        )
                    )
                ).all()
            )
            return set(components).issubset(rows)


class EvidenceRepository:
    def __init__(self, sessions: async_sessionmaker):
        self.sessions = sessions

    async def save_observation(
        self, observation, storage_key: str, byte_size: int, expires_at: datetime
    ) -> None:
        from app.persistence.models import Artifact, ObservationRow

        async with self.sessions.begin() as db:
            db.add(
                Artifact(
                    id=observation.artifact_id,
                    run_id=observation.run_id,
                    private_storage_key=storage_key,
                    sha256=observation.sha256,
                    mime_type="image/png",
                    byte_size=byte_size,
                    created_at=observation.captured_at,
                    expires_at=expires_at,
                )
            )
            await db.flush()
            db.add(
                ObservationRow(
                    id=observation.id,
                    run_id=observation.run_id,
                    session_id=observation.session_id,
                    epoch=observation.epoch,
                    frame_revision=observation.frame_revision,
                    artifact_id=observation.artifact_id,
                    captured_at=observation.captured_at,
                    width=observation.width,
                    height=observation.height,
                )
            )

    async def record_intent(self, proposal) -> bool:
        from app.persistence.models import ActionAttempt, ObservationRow

        now = utcnow()
        async with self.sessions.begin() as db:
            task_id = await db.scalar(select(TaskRun.task_id).where(TaskRun.id == proposal.run_id))
            task = await db.scalar(select(Task).where(Task.id == task_id).with_for_update())
            if (
                task is None
                or task.internal_status != "running"
                or now >= task.action_cutoff_at
                or now >= proposal.expires_at
            ):
                return False
            session = await db.scalar(
                select(DesktopSession)
                .where(DesktopSession.id == proposal.session_id)
                .with_for_update()
            )
            if (
                session is None
                or session.run_id != proposal.run_id
                or session.epoch != proposal.epoch
                or session.status != "connected"
            ):
                return False
            observed = await db.scalar(
                select(ObservationRow).where(
                    ObservationRow.id == proposal.observation_id,
                    ObservationRow.run_id == proposal.run_id,
                )
            )
            if (
                observed is None
                or observed.session_id != session.id
                or observed.epoch != session.epoch
            ):
                return False
            latest = await db.scalar(
                select(ObservationRow.id)
                .where(ObservationRow.session_id == session.id)
                .order_by(ObservationRow.frame_revision.desc(), ObservationRow.captured_at.desc())
                .limit(1)
            )
            if latest != observed.id:
                return False
            args = proposal.args
            if proposal.kind in {"click", "double_click"} and (
                args.x >= observed.width or args.y >= observed.height
            ):
                return False
            already_acted = await db.scalar(
                select(ActionAttempt.id)
                .where(
                    ActionAttempt.observation_id == observed.id,
                    ActionAttempt.status.in_(["intent", "sent", "uncertain", "verified"]),
                )
                .limit(1)
            )
            if already_acted is not None:
                return False
            result = await db.scalar(
                insert(ActionAttempt)
                .values(
                    id=proposal.id,
                    run_id=proposal.run_id,
                    session_id=session.id,
                    observation_id=observed.id,
                    epoch=session.epoch,
                    kind=proposal.kind,
                    risk=proposal.risk,
                    sanitized_arguments=sanitize(proposal.args.model_dump()),
                    expected_effect=sanitize(proposal.expected_effect),
                    status="intent",
                    proposed_at=now,
                    expires_at=proposal.expires_at,
                )
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(ActionAttempt.id)
            )
            return result is not None

    async def mark_action_sent(self, action_id: UUID) -> None:
        from app.persistence.models import ActionAttempt

        async with self.sessions.begin() as db:
            await db.execute(
                update(ActionAttempt)
                .where(ActionAttempt.id == action_id, ActionAttempt.status == "intent")
                .values(status="sent", sent_at=utcnow())
            )

    async def mark_action_uncertain(self, action_id: UUID) -> None:
        from app.persistence.models import ActionAttempt

        async with self.sessions.begin() as db:
            await db.execute(
                update(ActionAttempt)
                .where(ActionAttempt.id == action_id, ActionAttempt.status.in_(["intent", "sent"]))
                .values(status="uncertain")
            )

    async def mark_action_verified(self, action_id: UUID, run_id: UUID,
                                   observation_id: UUID) -> None:
        """Record post-action evidence without conflating send and effect confirmation."""
        from app.persistence.models import ActionAttempt, ObservationRow

        async with self.sessions.begin() as db:
            action = await db.scalar(select(ActionAttempt).where(
                ActionAttempt.id == action_id, ActionAttempt.run_id == run_id).with_for_update())
            observed = await db.scalar(select(ObservationRow).where(
                ObservationRow.id == observation_id, ObservationRow.run_id == run_id))
            before = await db.get(ObservationRow, action.observation_id) if action else None
            if (action is None or observed is None or before is None
                    or action.status not in {"sent", "uncertain", "verified"}
                    or observed.session_id != action.session_id or observed.epoch != action.epoch
                    or observed.frame_revision <= before.frame_revision
                    or observed.captured_at <= before.captured_at):
                raise ValueError("Invalid post-action evidence")
            if action.status == "verified":
                if action.outcome_observation_id != observation_id:
                    raise ValueError("Effect already bound to another observation")
                return
            action.status = "verified"
            action.verified_at = utcnow()
            action.outcome_observation_id = observation_id
