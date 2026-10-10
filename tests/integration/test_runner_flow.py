"""Real DB/Redis/checkpoints and isolated socket-owner process, with synthetic model/transport.
This verifies integration and closure, not OpenAI accuracy or the real desktop app.
"""

from io import BytesIO

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.api.application import Readiness, create_app
from app.core.config import Settings
from app.core.graph_memory import graph_memory
from app.desktop.gateway import ProcessGateway
from app.models.schemas import OperatorDecision, Plan, VisualVerification
from app.models.turns import OperatorTurn
from app.persistence.models import (
    AgentEvent,
    DesktopSession,
    ObservationRow,
    TaskRun,
)
from app.persistence.repositories import (
    EventRecorder,
    EvidenceRepository,
    HeartbeatRepository,
    SessionRepository,
)
from app.queue.streams import OutboxDispatcher, TaskStream
from app.worker.runner import TaskRunner
from scripts.bootstrap import bootstrap
from tests.unit.test_graph_v2 import planner, verify

pytestmark = pytest.mark.integration


class SyntheticTransport:
    def __init__(self, *args):
        pass

    async def open(self):
        pass

    async def capture(self):
        stream = BytesIO()
        Image.new("RGB", (1280, 800), "white").save(stream, "PNG")
        return stream.getvalue(), 1280, 800

    async def action(self, args):
        raise AssertionError("Read-only query must not act")

    async def close(self):
        pass


class SyntheticModel:
    async def decide(self, role, schema, context, image):
        assert image.startswith(b"\x89PNG")
        if role == "orchestrator":
            return Plan(
                intent="query",
                milestones=("Read visible answer",),
                success_criteria=("Observed answer",),
                preservation_rules=("Do not modify",),
            )
        if role == "operator":
            return OperatorDecision(
                disposition="verify",
                args=None,
                expected_effect="",
                scope_reason="Read-only",
                risk="navigation",
                identifiers=(),
            )
        assert role == "verifier"
        return VisualVerification(
            status="verified",
            safe_to_execute=False,
            changes_business_data=False,
            facts=("Synthetic visible answer",),
            unresolved_identifiers=(),
            answer="7",
        )


class SyntheticModelV2:
    async def decide(self, role, schema, context, image):
        assert image.startswith(b"\x89PNG")
        if role == "coordinator":
            return planner()(context)
        if role == "operator":
            return OperatorTurn(disposition="verify")
        assert role == "verifier"
        return verify(complete=True)(context)


@pytest.mark.parametrize("graph_version", ["v1", "context-v2"])
async def test_durable_queue_graph_checkpoints_and_close_before_terminal(
    platform_db, platform_redis, repository, stream_name, tmp_path, graph_version
):
    engine, sessions = platform_db
    # Render DSN only for local library use; str(URL) masks credentials, so use explicit.
    uri = engine.url.render_as_string(hide_password=False).replace(
        "postgresql+psycopg://", "postgresql://"
    )
    await bootstrap(engine.url.render_as_string(hide_password=False), uri)
    for component in ("worker", "gateway", "supervisor"):
        await HeartbeatRepository(sessions).beat(component, "test-worker", ready=True)
    app = create_app(
        Settings(team_id="test", team_entrypoint_token="synthetic-api-token"),
        repository=repository,
        readiness=Readiness(sessions, platform_redis),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://synthetic-api.example",
        headers={"Authorization": "Bearer synthetic-api-token"},
    ) as client:
        assert (await client.get("/health")).status_code == 200
        accepted = await client.post("/tasks", json={
            "tarea": "Consulta el dato visible.",
            "escritorio": {"url": "wss://synthetic.example/vnc", "password": "synthetic"},
        })
        assert accepted.status_code == 200
        task_id = accepted.json()["task_id"]
    stream = TaskStream(platform_redis, stream=stream_name, group="test")
    await stream.setup()
    assert await OutboxDispatcher(sessions, stream, "test-dispatcher").dispatch_once() == 1
    messages = await stream.read("test-worker")
    claim = await stream.claim_and_ack(messages[0], repository, "test-worker", model_version="fake",
                                       graph_version=graph_version)
    assert claim is not None

    def gateway_factory(*args):
        return ProcessGateway(*args, transport_factory=SyntheticTransport)

    async with graph_memory(uri) as (saver, _store):
        runner = TaskRunner(
            Settings(artifact_dir=tmp_path, graph_version=graph_version),
            repository,
            SessionRepository(sessions),
            EvidenceRepository(sessions),
            EventRecorder(sessions),
            SyntheticModelV2() if graph_version == "context-v2" else SyntheticModel(),
            checkpointer=saver,
            gateway_factory=gateway_factory,
        )
        result = await runner.run(claim, "test-worker")
        assert result.estado == "terminada"
        snapshot = await saver.aget_tuple({"configurable": {"thread_id": str(claim.run_id)}})
        assert snapshot is not None
        assert "data:image" not in str(snapshot.checkpoint)
        assert "synthetic.example" not in str(snapshot.checkpoint)
        assert "password" not in str(snapshot.checkpoint)
        if graph_version == "context-v2":
            assert snapshot.checkpoint["channel_values"]["graph_version"] == graph_version
            assert snapshot.checkpoint["channel_values"]["shared"].progress.remaining_criterion_ids == ()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://synthetic-api.example",
        headers={"Authorization": "Bearer synthetic-api-token"},
    ) as client:
        response = await client.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json() == {"estado": "terminada", "resumen": "respuesta: 7"}
    async with sessions() as db:
        session = await db.scalar(
            select(DesktopSession).where(DesktopSession.run_id == claim.run_id)
        )
        run = await db.get(TaskRun, claim.run_id)
        assert session.status == "closed" and run.status == "closed"
        assert run.graph_version == graph_version
        assert session.close_evidence["owner_stopped"] is True
        assert await db.scalar(
            select(ObservationRow.id).where(ObservationRow.run_id == claim.run_id)
        )
        events = list(
            (await db.scalars(select(AgentEvent).where(AgentEvent.run_id == claim.run_id))).all()
        )
        assert any(e.parent_event_id for e in events)
        assert any(e.event_type == "terminal" for e in events)
