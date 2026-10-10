import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.api.application import Readiness, create_app
from app.core.config import Settings
from app.persistence.models import AgentEvent, DesktopSession, Outbox, Task, TaskCredential, TaskRun
from app.persistence.repositories import (
    EventRecorder,
    HeartbeatRepository,
    LifecycleConflict,
    SessionRepository,
    utcnow,
)
from app.queue.streams import OutboxDispatcher, TaskStream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def accept(repository, *, now=None, url="wss://desktop.example/custom/path"):
    return await repository.accept_with_outbox(
        instruction="Count visible rows",
        url=url,
        password="secret-canary-test",
        team_id="test",
        accepted_at=now or utcnow(),
    )


async def test_d01_all_application_tables_and_migration_preserves_data(platform_db, repository):
    task_id = await accept(repository)
    async with platform_db[1]() as db:
        tables = set(
            (
                await db.execute(
                    text(
                        "SELECT schemaname||'.'||tablename FROM pg_tables WHERE schemaname IN ('app','private')"
                    )
                )
            ).scalars()
        )
        assert len(tables) == 10
        assert await db.get(Task, task_id) is not None
        assert await db.scalar(text("SELECT version_num FROM alembic_version")) == "003"


async def test_d02_acceptance_rollback_on_outbox_failure(platform_db, repository):
    async with platform_db[0].begin() as db:
        await db.execute(
            text(
                "ALTER TABLE app.outbox ADD CONSTRAINT reject_test CHECK (event_type <> 'task.ready')"
            )
        )
    try:
        with pytest.raises(IntegrityError):
            await accept(repository)
        async with platform_db[1]() as db:
            assert await db.scalar(select(func.count()).select_from(Task)) == 0
            assert await db.scalar(select(func.count()).select_from(TaskCredential)) == 0
    finally:
        async with platform_db[0].begin() as db:
            await db.execute(text("ALTER TABLE app.outbox DROP CONSTRAINT reject_test"))


async def test_d03_concurrent_claim_exactly_one_run(platform_db, repository):
    task_id = await accept(repository)
    result = await asyncio.gather(
        repository.claim(task_id, "worker1"), repository.claim(task_id, "worker2")
    )
    assert sum(claim is not None for claim in result) == 1
    async with platform_db[1]() as db:
        assert await db.scalar(select(func.count()).select_from(TaskRun)) == 1


async def test_d04_session_exclusion_lease_expiry_is_not_closure(platform_db, repository):
    claims = [await repository.claim(await accept(repository), f"worker{i}") for i in range(2)]
    authority = SessionRepository(platform_db[1])
    outcomes = await asyncio.gather(
        *(authority.reserve(claim.run_id, "owner") for claim in claims), return_exceptions=True
    )
    assert sum(isinstance(outcome, DesktopSession) for outcome in outcomes) == 1
    winner = next(outcome for outcome in outcomes if isinstance(outcome, DesktopSession))
    async with platform_db[1].begin() as db:
        await db.execute(
            update(DesktopSession)
            .where(DesktopSession.id == winner.id)
            .values(lease_expires_at=utcnow() - timedelta(seconds=60), status="uncertain")
        )
    other = next(claim for claim in claims if claim.run_id != winner.run_id)
    with pytest.raises(LifecycleConflict):
        await authority.reserve(other.run_id, "new-owner")


async def test_d05_events_concurrent_sequence_and_cross_run_parent(platform_db, repository):
    claims = [await repository.claim(await accept(repository), f"worker{i}") for i in range(2)]
    events = EventRecorder(platform_db[1])
    ids = await asyncio.gather(
        *(
            events.append(
                claims[0].run_id,
                "delegation",
                payload={"password": "secret-canary-test", "safe": "ok"},
            )
            for _ in range(12)
        )
    )
    async with platform_db[1]() as db:
        rows = list((await db.scalars(select(AgentEvent).order_by(AgentEvent.sequence))).all())
        assert [row.sequence for row in rows] == list(range(1, 13))
        assert all(row.sanitized_payload == {"safe": "ok"} for row in rows)
    with pytest.raises(IntegrityError):
        await events.append(claims[1].run_id, "return", parent_event_id=ids[0])
    async with platform_db[1]() as db:
        assert (await db.get(TaskRun, claims[1].run_id)).next_event_sequence == 0


async def test_d06_finalize_requires_confirmed_closure_and_terminal_immutable(
    platform_db, repository
):
    claim = await repository.claim(await accept(repository), "worker")
    authority = SessionRepository(platform_db[1])
    session = await authority.reserve(claim.run_id, "owner")
    await repository.begin_closing(claim.task_id)
    with pytest.raises(LifecycleConflict):
        await repository.finalize_after_close(
            claim.task_id, succeeded=True, summary="respuesta: 2", reason_code="verified"
        )
    await authority.revoke(session.id)
    with pytest.raises(LifecycleConflict):
        await authority.mark_closed(
            session.id, epoch=session.epoch, close_evidence={"transport_closed": True}
        )
    await authority.mark_closed(
        session.id,
        epoch=session.epoch,
        close_evidence={"transport_closed": True, "owner_stopped": True},
    )
    await repository.finalize_after_close(
        claim.task_id, succeeded=True, summary="respuesta: 2", reason_code="verified"
    )
    await repository.finalize_after_close(
        claim.task_id, succeeded=True, summary="respuesta: 2", reason_code="verified"
    )
    with pytest.raises(LifecycleConflict):
        await repository.finalize_after_close(
            claim.task_id, succeeded=False, summary="different", reason_code="error"
        )
    assert await repository.get_for_team(claim.task_id, "test") == {
        "estado": "terminada",
        "resumen": "respuesta: 2",
    }
    assert await repository.get_for_team(claim.task_id, "other-team") is None
    async with platform_db[1]() as db:
        assert await db.get(TaskCredential, claim.task_id) is None


async def test_expired_queue_never_creates_run(platform_db, repository):
    task_id = await accept(repository, now=utcnow() - timedelta(seconds=271))
    assert await repository.claim(task_id, "worker") is None
    assert (await repository.get_for_team(task_id, "test"))["estado"] == "fallida"
    async with platform_db[1]() as db:
        assert await db.scalar(select(func.count()).select_from(TaskRun)) == 0
        assert await db.get(TaskCredential, task_id) is None


async def test_credentials_encrypted_and_authority_only(platform_db, repository, cipher):
    task_id = await accept(repository)
    async with platform_db[1]() as db:
        row = await db.get(TaskCredential, task_id)
        assert b"secret-canary-test" not in row.encrypted_payload
        assert b"desktop.example" not in row.encrypted_payload
    assert (await repository.credentials_for_authority(task_id))["password"] == "secret-canary-test"
    assert cipher.destination_key(
        "wss://DESKTOP.example:443/custom/path"
    ) == cipher.destination_key("wss://desktop.example/custom/path")


async def test_q01_duplicate_stream_delivery_db_claim_once(
    platform_db, platform_redis, repository, stream_name
):
    task_id = await accept(repository)
    stream = TaskStream(platform_redis, stream=stream_name)
    await stream.setup()
    dispatcher = OutboxDispatcher(platform_db[1], stream, "dispatcher")
    assert await dispatcher.dispatch_once() == 1
    await stream.publish(task_id, uuid4())
    messages = await stream.read("worker", count=2)
    claims = await asyncio.gather(
        *(stream.claim_and_ack(message, repository, "worker") for message in messages)
    )
    assert sum(claim is not None for claim in claims) == 1
    assert (await platform_redis.xpending(stream_name, stream.group))["pending"] == 0


async def test_q02_pending_reclaim_after_consumer_crash(
    platform_db, platform_redis, repository, stream_name
):
    task_id = await accept(repository)
    stream = TaskStream(platform_redis, stream=stream_name)
    await stream.setup()
    await stream.publish(task_id, uuid4())
    first = await stream.read("dead-worker")
    second = await stream.reclaim("replacement", idle_ms=0)
    assert first == second
    assert await stream.claim_and_ack(second[0], repository, "replacement") is not None


async def test_q03_publish_failure_keeps_outbox_recoverable(
    platform_db, platform_redis, repository, stream_name
):
    await accept(repository)
    stream = TaskStream(platform_redis, stream=stream_name)
    await stream.setup()

    class FailingStream:
        async def publish(self, *args):
            raise ConnectionError("synthetic unavailable")

    dispatcher = OutboxDispatcher(platform_db[1], FailingStream(), "dead")
    with pytest.raises(ConnectionError):
        await dispatcher.dispatch_once()
    async with platform_db[1].begin() as db:
        row = await db.scalar(select(Outbox))
        assert row.published_at is None
        row.claimed_until = utcnow() - timedelta(seconds=1)
    assert await OutboxDispatcher(platform_db[1], stream, "replacement").dispatch_once() == 1


async def test_c03_api_durable_acceptance_and_projection(platform_db, platform_redis, repository):
    heartbeats = HeartbeatRepository(platform_db[1])
    for component in ("worker", "gateway", "supervisor"):
        await heartbeats.beat(component, "test-instance", ready=True)
    readiness = Readiness(platform_db[1], platform_redis, require_memory=False)
    settings = Settings(team_id="test", team_entrypoint_token="test-bearer")
    app = create_app(settings, repository=repository, readiness=readiness)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        start = asyncio.get_running_loop().time()
        response = await client.post(
            "/tasks",
            headers={"Authorization": "Bearer test-bearer"},
            json={
                "tarea": "Count rows",
                "escritorio": {
                    "url": "wss://new-desktop.example/another-route",
                    "password": "secret-canary-test",
                },
            },
        )
        assert response.status_code == 200
        assert asyncio.get_running_loop().time() - start < 2
        task_id = response.json()["task_id"]
        async with platform_db[1]() as db:
            assert await db.get(Task, task_id) is not None
            assert await db.scalar(select(Outbox).where(Outbox.task_id == task_id)) is not None
        state = await client.get(
            f"/tasks/{task_id}", headers={"Authorization": "Bearer test-bearer"}
        )
        assert state.json() == {"estado": "en_curso", "resumen": ""}


async def test_c05_bootstrap_heartbeat_and_dependencies_readiness(platform_db, platform_redis):
    readiness = Readiness(platform_db[1], platform_redis, require_memory=False)
    assert not await readiness()
    beats = HeartbeatRepository(platform_db[1])
    for component in ("worker", "gateway", "supervisor"):
        await beats.beat(component, "test", ready=True)
    assert await readiness()
    await beats.beat("gateway", "test", ready=True, now=utcnow() - timedelta(seconds=11))
    assert not await readiness()
    await beats.beat("gateway", "test", ready=True)
    assert await readiness()
    strict = Readiness(platform_db[1], platform_redis, require_memory=True)
    async with platform_db[1]() as db:
        initialized = await db.scalar(
            text(
                "SELECT to_regclass('graph_checkpoints.checkpoints') IS NOT NULL AND to_regclass('graph_store.store') IS NOT NULL"
            )
        )
    assert await strict() is initialized


async def test_intent_duplicate_stale_observation_and_deadline(platform_db, repository):
    from app.domain.contracts import ActionProposal, ClickArgs, Observation
    from app.persistence.models import ActionAttempt
    from app.persistence.repositories import EvidenceRepository

    claim = await repository.claim(await accept(repository), "worker")
    authority = SessionRepository(platform_db[1])
    session = await authority.reserve(claim.run_id, "owner")
    await authority.mark_connected(session.id, session.epoch)
    evidence = EvidenceRepository(platform_db[1])

    def observed(revision):
        return Observation(
            run_id=claim.run_id,
            session_id=session.id,
            epoch=session.epoch,
            frame_revision=revision,
            captured_at=utcnow(),
            width=1280,
            height=800,
            artifact_id=uuid4(),
            sha256="a" * 64,
        )

    first = observed(1)
    await evidence.save_observation(
        first, "test/" + str(first.artifact_id), 100, utcnow() + timedelta(hours=1)
    )

    def proposal(observation, **overrides):
        values = {
            "run_id": claim.run_id,
            "session_id": session.id,
            "epoch": session.epoch,
            "observation_id": observation.id,
            "args": ClickArgs(x=10, y=20),
            "risk": "navigation",
            "expected_effect": "Open menu",
            "scope_reason": "Find count",
            "expires_at": utcnow() + timedelta(seconds=10),
        }
        return ActionProposal(**(values | overrides))

    action = proposal(first)
    assert await evidence.record_intent(action)
    assert not await evidence.record_intent(action)
    await evidence.mark_action_uncertain(action.id)
    assert not await evidence.record_intent(proposal(first))
    second = observed(2)
    await evidence.save_observation(
        second, "test/" + str(second.artifact_id), 100, utcnow() + timedelta(hours=1)
    )
    assert not await evidence.record_intent(proposal(first))
    assert not await evidence.record_intent(proposal(second, args=ClickArgs(x=1280, y=0)))
    assert not await evidence.record_intent(proposal(second, epoch=session.epoch + 1))
    async with platform_db[1].begin() as db:
        task = await db.get(Task, claim.task_id)
        task.accepted_at = utcnow() - timedelta(seconds=271)
        task.action_cutoff_at = utcnow() - timedelta(seconds=1)
    assert not await evidence.record_intent(proposal(second))
    async with platform_db[1]() as db:
        assert await db.scalar(select(func.count()).select_from(ActionAttempt)) == 1


async def test_cross_run_artifact_rejected(platform_db, repository):
    from app.persistence.models import Artifact, ObservationRow

    claims = [
        await repository.claim(
            await accept(repository, url=f"wss://desktop{i}.example/route"), f"worker{i}"
        )
        for i in range(2)
    ]
    authority = SessionRepository(platform_db[1])
    sessions = [await authority.reserve(claim.run_id, "owner") for claim in claims]
    artifact_id = uuid4()
    async with platform_db[1].begin() as db:
        db.add(
            Artifact(
                id=artifact_id,
                run_id=claims[0].run_id,
                private_storage_key="private/synthetic",
                sha256="a" * 64,
                mime_type="image/png",
                byte_size=1,
                created_at=utcnow(),
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
    with pytest.raises(IntegrityError):
        async with platform_db[1].begin() as db:
            db.add(
                ObservationRow(
                    id=uuid4(),
                    run_id=claims[1].run_id,
                    session_id=sessions[1].id,
                    epoch=sessions[1].epoch,
                    frame_revision=1,
                    artifact_id=artifact_id,
                    captured_at=utcnow(),
                    width=1280,
                    height=800,
                )
            )


async def test_dispatcher_crash_after_xadd_redelivers_without_reexecution(
    platform_db, platform_redis, repository, stream_name
):
    await accept(repository)
    stream = TaskStream(platform_redis, stream=stream_name)
    await stream.setup()

    class CrashAfterPublish:
        async def publish(self, *args):
            await stream.publish(*args)
            raise ConnectionError("crash after XADD before outbox acknowledgement")

    with pytest.raises(ConnectionError):
        await OutboxDispatcher(platform_db[1], CrashAfterPublish(), "dead").dispatch_once()
    async with platform_db[1].begin() as db:
        row = await db.scalar(select(Outbox))
        row.claimed_until = utcnow() - timedelta(seconds=1)
    assert await OutboxDispatcher(platform_db[1], stream, "recovered").dispatch_once() == 1
    messages = await stream.read("worker", count=2)
    assert len(messages) == 2
    results = [await stream.claim_and_ack(message, repository, "worker") for message in messages]
    assert sum(result is not None for result in results) == 1


async def test_d08_worker_cannot_read_credentials_or_modify_events(platform_db, repository):
    from sqlalchemy.exc import ProgrammingError

    from app.persistence.grants import setup_grants

    await accept(repository)
    async with platform_db[0].begin() as db:
        await setup_grants(db)
    async with platform_db[0].connect() as db:
        await db.execute(text("SET ROLE frontier_worker"))
        assert await db.scalar(select(func.count()).select_from(Task)) == 1
        await db.rollback()
    async with platform_db[0].connect() as db:
        await db.execute(text("SET ROLE frontier_worker"))
        with pytest.raises(ProgrammingError):
            await db.execute(text("SELECT encrypted_payload FROM private.task_credentials"))
        await db.rollback()
    async with platform_db[0].connect() as db:
        await db.execute(text("SET ROLE frontier_worker"))
        with pytest.raises(ProgrammingError):
            await db.execute(text("DELETE FROM app.agent_events"))
        await db.rollback()
    async with platform_db[0].connect() as db:
        await db.execute(text("SET ROLE frontier_authority"))
        payload = await db.scalar(text("SELECT encrypted_payload FROM private.task_credentials"))
        assert b"secret-canary-test" not in payload
        await db.rollback()


async def test_d01_upgrade_from_prior_revision_preserves_accepted_task(platform_db, repository):
    import os
    import subprocess
    from pathlib import Path

    task_id = await accept(repository)
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    for revision in ("001", "head"):
        command = "downgrade" if revision == "001" else "upgrade"
        result = await asyncio.to_thread(
            subprocess.run,
            [str(root / ".venv/bin/alembic"), command, revision],
            env=env,
            cwd=root,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, "Disposable migration failed"
    assert await repository.get_for_team(task_id, "test") == {"estado": "en_curso", "resumen": ""}
    async with platform_db[1]() as db:
        assert await db.get(TaskCredential, task_id) is not None
        assert await db.scalar(select(Outbox).where(Outbox.task_id == task_id)) is not None


async def test_metadata_matches_application_migrations(platform_db):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.persistence.models import Base

    def compare(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": lambda name, type_, parent: (
                    type_ != "schema" or name in {"app", "private"}
                ),
            },
        )
        return compare_metadata(context, Base.metadata)

    async with platform_db[0].connect() as connection:
        assert await connection.run_sync(compare) == []


async def test_reserve_races_with_finalization_never_leaves_terminal_open(platform_db, repository):
    claim = await repository.claim(await accept(repository), "worker")
    authority = SessionRepository(platform_db[1])

    async def close():
        await repository.begin_closing(claim.task_id)
        await repository.finalize_after_close(
            claim.task_id, succeeded=False, summary="Closed", reason_code="test_close"
        )

    results = await asyncio.gather(
        close(), authority.reserve(claim.run_id, "owner"), return_exceptions=True
    )
    async with platform_db[1]() as db:
        task = await db.get(Task, claim.task_id)
        sessions = list(
            (
                await db.scalars(
                    select(DesktopSession).where(DesktopSession.run_id == claim.run_id)
                )
            ).all()
        )
        assert task.internal_status not in {"failed", "succeeded"} or all(
            session.status == "closed" for session in sessions
        )
    assert any(isinstance(result, LifecycleConflict) for result in results)


async def test_d07_result_survives_new_repository_and_connection_pool(
    platform_db, repository, cipher
):
    import os

    from app.persistence.database import database_pool
    from app.persistence.repositories import TaskRepository

    task_id = await accept(repository)
    await repository.begin_closing(task_id)
    await repository.finalize_after_close(
        task_id, succeeded=False, summary="No action performed", reason_code="test"
    )
    new_engine, new_sessions = database_pool(os.environ["TEST_DATABASE_URL"], pool_size=1)
    try:
        fresh_repository = TaskRepository(new_sessions, cipher)
        assert await fresh_repository.get_for_team(task_id, "test") == {
            "estado": "fallida",
            "resumen": "No action performed",
        }
    finally:
        await new_engine.dispose()


async def test_two_distinct_proposals_race_one_observation_one_intent(platform_db, repository):
    from app.domain.contracts import ActionProposal, ClickArgs, Observation
    from app.persistence.models import ActionAttempt
    from app.persistence.repositories import EvidenceRepository

    claim = await repository.claim(await accept(repository), "worker")
    authority = SessionRepository(platform_db[1])
    session = await authority.reserve(claim.run_id, "owner")
    await authority.mark_connected(session.id, session.epoch)
    evidence = EvidenceRepository(platform_db[1])
    observation = Observation(
        run_id=claim.run_id,
        session_id=session.id,
        epoch=session.epoch,
        frame_revision=1,
        captured_at=utcnow(),
        width=1280,
        height=800,
        artifact_id=uuid4(),
        sha256="a" * 64,
    )
    await evidence.save_observation(
        observation, "synthetic/" + uuid4().hex, 1, utcnow() + timedelta(hours=1)
    )
    proposals = [
        ActionProposal(
            run_id=claim.run_id,
            session_id=session.id,
            epoch=session.epoch,
            observation_id=observation.id,
            args=ClickArgs(x=10, y=20),
            risk="navigation",
            expected_effect="Open menu",
            scope_reason="Find rows",
            expires_at=utcnow() + timedelta(seconds=10),
        )
        for _ in range(2)
    ]
    results = await asyncio.gather(*(evidence.record_intent(proposal) for proposal in proposals))
    assert results.count(True) == 1
    async with platform_db[1]() as db:
        assert await db.scalar(select(func.count()).select_from(ActionAttempt)) == 1


async def test_queue_reaper_and_claim_race_safe_without_runs(platform_db, repository):
    task_id = await accept(repository, now=utcnow() - timedelta(seconds=271))
    await asyncio.gather(repository.expire_queued(), repository.claim(task_id, "worker"))
    assert (await repository.get_for_team(task_id, "test"))["estado"] == "fallida"
    assert await repository.expire_queued() == 0
    async with platform_db[1]() as db:
        assert await db.scalar(select(func.count()).select_from(TaskRun)) == 0
        assert await db.get(TaskCredential, task_id) is None


async def test_stale_run_is_uncertain_never_fake_closed_or_replayed(platform_db, repository):
    claim = await repository.claim(await accept(repository), "dead-worker")
    authority = SessionRepository(platform_db[1])
    session = await authority.reserve(claim.run_id, "unknown-owner")
    await authority.mark_connected(session.id, session.epoch)
    async with platform_db[1].begin() as db:
        run = await db.get(TaskRun, claim.run_id)
        run.heartbeat_at = utcnow() - timedelta(seconds=60)
    marked = await repository.mark_stale_runs_uncertain(before=utcnow() - timedelta(seconds=10))
    assert marked == [claim.run_id]
    assert not await authority.validate(session.id, session.epoch)
    assert await repository.claim(claim.task_id, "replacement") is None
    assert (await repository.get_for_team(claim.task_id, "test"))["estado"] == "en_curso"
    with pytest.raises(LifecycleConflict):
        await repository.finalize_after_close(
            claim.task_id, succeeded=False, summary="Stopped", reason_code="lost_worker"
        )
    async with platform_db[1]() as db:
        row = await db.get(DesktopSession, session.id)
        assert row.status == "uncertain"
        assert row.closed_at is None and row.close_evidence is None
