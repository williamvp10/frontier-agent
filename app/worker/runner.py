"""Lifecycle integration: graph candidates cannot publish their own terminal state."""

import asyncio
from contextlib import suppress
from datetime import timedelta

from app.agents.graph import run_agent
from app.agents.runtime import AgentRuntime
from app.core.observability import Observability, traced_task
from app.core.sanitization import sanitize
from app.desktop.executor import DesktopSessionAdapter
from app.desktop.gateway import ProcessGateway
from app.domain.contracts import TaskResult, utcnow
from app.models.jev_advisor import AdviceSession, build_jev_advisor
from app.persistence.repositories import DestinationBusy


class RunRecorder:
    def __init__(self, recorder, run_id):
        self.recorder, self.run_id = recorder, run_id
        self.parents = {}

    async def append(self, role, event_type, payload):
        parent = self.parents.get(role) if event_type == "return" else None
        event_id = await self.recorder.append(
            self.run_id, event_type, agent_name=role, parent_event_id=parent, payload=payload
        )
        if event_type == "delegation":
            self.parents[payload["specialist"]] = event_id


class TaskRunner:
    def __init__(
        self,
        settings,
        tasks,
        sessions,
        evidence,
        events,
        model,
        *,
        checkpointer=None,
        gateway_factory=ProcessGateway,
        desktop_factory=DesktopSessionAdapter,
        graph_runner=run_agent,
        observability=None,
    ):
        self.settings, self.tasks, self.sessions = settings, tasks, sessions
        self.evidence, self.events, self.model = evidence, events, model
        self.checkpointer = checkpointer
        self.gateway_factory, self.desktop_factory, self.graph_runner = (
            gateway_factory,
            desktop_factory,
            graph_runner,
        )
        self.active = set()
        self.active_runs = set()
        self.stopping = False
        self.observability = observability or Observability.from_settings(settings)

    @traced_task
    async def run(self, claim, owner_id):
        session = None
        gateway = None
        pending = None
        result = TaskResult(
            estado="fallida",
            resumen="No se pudo ejecutar la tarea con seguridad.",
            reason_code="execution_failed",
        )
        secrets = ()
        self.active_runs.add(claim.run_id)
        try:
            if self.stopping:
                raise RuntimeError("Worker draining")
            profiles = getattr(self.model, "profiles", None)
            if profiles:
                await self.events.append(claim.run_id, "run_profile", payload={
                    "graph_version": self.settings.graph_version, "profile": profiles.model_dump_json()})
            while session is None:
                try:
                    session = await self.sessions.reserve(claim.run_id, owner_id)
                except DestinationBusy:
                    if self.stopping or utcnow() >= claim.action_cutoff_at - timedelta(seconds=3):
                        raise TimeoutError("Destination unavailable within safe deadline") from None
                    await asyncio.sleep(0.5)
            credentials = await self.tasks.credentials_for_authority(claim.task_id)
            secrets = (credentials["url"], credentials["password"])
            # Queue wait consumes the same budget; never extend a persisted cutoff.
            drain_at = min(
                claim.action_cutoff_at,
                claim.accepted_at + timedelta(seconds=self.settings.drain_seconds),
            )
            gateway = self.gateway_factory(session.id, session.epoch, drain_at)
            self.active.add(gateway)
            await gateway.open(
                credentials["url"],
                credentials["password"],
                allow_private=self.settings.allow_private_desktops,
                text_input=self.settings.vnc_text_input,
            )
            del credentials
            await self.sessions.mark_connected(session.id, session.epoch)
            desktop = self.desktop_factory(
                gateway,
                self.sessions,
                self.evidence,
                run_id=claim.run_id,
                session_id=session.id,
                epoch=session.epoch,
                cutoff_at=drain_at,
                artifact_dir=self.settings.artifact_dir,
            )
            runtime = AgentRuntime(
                model=self.model,
                desktop=desktop,
                recorder=RunRecorder(self.events, claim.run_id),
                action_cutoff_at=drain_at,
                max_steps=self.settings.max_model_calls,
                max_actions=self.settings.max_actions,
                max_recoveries=self.settings.max_recoveries,
                model_timeout_s=self.settings.model_timeout_seconds,
                proposal_ttl_s=self.settings.action_proposal_ttl_seconds,
                graph_version=self.settings.graph_version,
                task_deadline_at=getattr(claim, "deadline_at", claim.action_cutoff_at),
                advice_session=AdviceSession(run_id=claim.run_id, action_cutoff_at=drain_at,
                    advisor=build_jev_advisor(self.settings), max_calls=self.settings.jev_max_calls,
                    timeout_seconds=self.settings.jev_timeout_seconds,
                    secrets=secrets + (self.settings.openrouter_api_key.get_secret_value(),)),
                secrets=secrets + (self.settings.openrouter_api_key.get_secret_value(),),
            )
            pending = asyncio.create_task(
                self.graph_runner(
                    claim.instruction,
                    claim.run_id,
                    session.id,
                    session.epoch,
                    runtime,
                    checkpointer=self.checkpointer,
                )
            )
            # Do not wait for cancellation acknowledgement of an unresponsive model.
            done, _ = await asyncio.wait(
                [pending], timeout=max(0, (drain_at - utcnow()).total_seconds())
            )
            if done:
                result = pending.result()
            else:
                result = TaskResult(
                    estado="fallida",
                    resumen="El plazo seguro se agotó; ejecución detenida.",
                    reason_code="deadline",
                )
        except asyncio.CancelledError:
            result = TaskResult(
                estado="fallida",
                resumen="Ejecución cancelada para cerrar el escritorio.",
                reason_code="cancelled",
            )
        except Exception:
            # Exception messages may contain provider keys or destination values.
            pass
        finally:
            if pending and not pending.done():
                pending.cancel()
                pending.add_done_callback(_consume_exception)
            close_evidence = None
            if gateway:
                # Socket shutdown must happen even if DB is unavailable.
                close_evidence = await gateway.close_and_confirm()
                self.active.discard(gateway)

            # No session => no owner/socket was created. Durable terminal may be retried;
            # there is never another external action in this loop.
            async def persist_closed():
                await self.tasks.begin_closing(claim.task_id)
                if session:
                    await self.sessions.revoke(session.id)
                    proof = close_evidence or {
                        "transport_closed": True,
                        "owner_stopped": True,
                        "never_opened": True,
                    }
                    await self.sessions.mark_closed(
                        session.id, epoch=session.epoch, close_evidence=proof
                    )
                summary = sanitize(result.resumen, secrets)
                await self.tasks.finalize_after_close(
                    claim.task_id,
                    succeeded=result.estado == "terminada",
                    summary=summary,
                    reason_code=result.reason_code,
                )
                await self.events.append(
                    claim.run_id,
                    "terminal",
                    payload={"state": result.estado, "reason": result.reason_code},
                )

            for attempt in range(5):
                try:
                    await persist_closed()
                    break
                except Exception:
                    if attempt == 4:
                        raise RuntimeError(
                            "Closed execution result could not be persisted"
                        ) from None
                    await asyncio.sleep(min(2**attempt, 5))
        self.active_runs.discard(claim.run_id)
        return result

    async def close_all(self):
        self.stopping = True
        await asyncio.gather(*(g.close_and_confirm() for g in list(self.active)))


def _consume_exception(task):
    with suppress(BaseException):
        task.result()
