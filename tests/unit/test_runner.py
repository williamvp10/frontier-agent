import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.domain.contracts import TaskResult, utcnow
from app.worker.runner import TaskRunner

pytestmark = pytest.mark.unit


class Tasks:
    def __init__(self, timeline):
        self.timeline = timeline
        self.summary = None

    async def credentials_for_authority(self, id):
        return {"url": "wss://private.example/vnc", "password": "canary-password"}

    async def begin_closing(self, id):
        self.timeline.append("closing")

    async def finalize_after_close(self, id, **kwargs):
        self.timeline.append("terminal")
        self.summary = kwargs["summary"]


class Sessions:
    def __init__(self, timeline):
        self.timeline = timeline
        self.id = uuid4()
        self.active = True

    async def reserve(self, *args):
        return SimpleNamespace(id=self.id, epoch=1)

    async def mark_connected(self, *args):
        self.timeline.append("connected")

    async def revoke(self, id):
        self.active = False
        self.timeline.append("revoked")

    async def mark_closed(self, *args, **kwargs):
        assert kwargs["close_evidence"]["owner_stopped"]
        self.timeline.append("confirmed")


class Events:
    async def append(self, *args, **kwargs):
        return uuid4()


class Desktop:
    def __init__(self, *args, **kwargs):
        pass


async def setup_run(tmp_path, graph, seconds=30):
    timeline = []

    class Gateway:
        def __init__(self, *args):
            pass

        async def open(self, *args, **kwargs):
            timeline.append("open")

        async def close_and_confirm(self):
            timeline.append("socket_closed")
            return {"transport_closed": True, "owner_stopped": True}

    claim = SimpleNamespace(
        task_id=uuid4(),
        run_id=uuid4(),
        instruction="Consulta",
        action_cutoff_at=utcnow() + timedelta(seconds=seconds),
    )
    tasks = Tasks(timeline)
    runner = TaskRunner(
        Settings(artifact_dir=tmp_path),
        tasks,
        Sessions(timeline),
        None,
        Events(),
        None,
        gateway_factory=Gateway,
        desktop_factory=Desktop,
        graph_runner=graph,
    )
    return runner, claim, timeline, tasks


async def success(*args, **kwargs):
    return TaskResult(estado="terminada", resumen="respuesta: 7", reason_code="verified")


async def test_result_only_published_after_owner_closed(tmp_path):
    runner, claim, timeline, tasks = await setup_run(tmp_path, success)
    await runner.run(claim, "owner")
    assert (
        timeline.index("socket_closed") < timeline.index("confirmed") < timeline.index("terminal")
    )


async def test_deadline_does_not_wait_for_slow_model(tmp_path):
    async def slow(*args, **kwargs):
        await asyncio.sleep(60)

    runner, claim, timeline, tasks = await setup_run(tmp_path, slow, seconds=2.1)
    result = await asyncio.wait_for(runner.run(claim, "owner"), 2)
    assert result.estado == "fallida" and result.reason_code == "deadline"
    assert timeline[-1] == "terminal" and "socket_closed" in timeline


async def test_graph_failure_still_closes_and_reports_failure(tmp_path):
    async def fail(*args, **kwargs):
        raise ValueError("private internal exception")

    runner, claim, timeline, tasks = await setup_run(tmp_path, fail)
    result = await runner.run(claim, "owner")
    assert result.estado == "fallida" and timeline[-1] == "terminal"
    assert "private internal" not in tasks.summary


async def test_secret_from_model_output_redacted_before_persistence(tmp_path):
    async def leak(*args, **kwargs):
        return TaskResult(
            estado="terminada",
            resumen="canary-password wss://private.example/vnc",
            reason_code="verified",
        )

    runner, claim, timeline, tasks = await setup_run(tmp_path, leak)
    await runner.run(claim, "owner")
    assert "canary-password" not in tasks.summary and "private.example" not in tasks.summary
