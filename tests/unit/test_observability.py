import asyncio
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.config import Settings
from app.core.observability import (
    Observability,
    count,
    observed,
    progress,
    record_usage,
    safe_metadata,
    safe_model,
)

pytestmark = pytest.mark.unit
CANARY = "private-canary-value"


@pytest.fixture
def telemetry():
    exporter = InMemorySpanExporter()
    client = Langfuse(public_key="test-" + uuid4().hex, secret_key="synthetic-test-key",
        base_url="https://us.cloud.langfuse.com", tracer_provider=TracerProvider(),
        span_exporter=exporter, environment="test")
    client.create_score = Mock()
    yield Observability(client), exporter
    client.shutdown()


def spans(exporter, client):
    client.flush()
    return {span.name: span for span in exporter.get_finished_spans()}


def test_deny_by_default_and_invalid_numeric_values():
    identifier = uuid4()
    assert safe_metadata({"task_id": identifier, "role": "operator", "input_tokens": 7,
        "context": {"password": CANARY}, "model": CANARY, "status": CANARY,
        "output_tokens": float("inf"), "cost_usd": float("nan"),
        "total_tokens": 10**1000, "duration_seconds": -1, "state": {"nested": CANARY}}) == {
            "task_id": str(identifier), "role": "operator", "input_tokens": 7}
    assert safe_model("typesafe/jev-1.13") == "typesafe/jev-1.13"
    assert safe_model("https://private.example/" + CANARY) is None
    assert safe_model("openai/sk-private-secret") is None


async def test_real_sdk_hierarchy_tokens_scores_and_no_private_io(telemetry):
    obs, exporter = telemetry
    task_id, run_id = uuid4(), uuid4()

    @observed("generation", as_type="generation", counter="model_calls")
    async def generation(context, image):
        record_usage({"input_tokens": 11, "output_tokens": 3, "cost_usd": 0.01,
                      "context": context}, model="openai/gpt-6-luna")
        return CANARY

    @observed("agent", as_type="agent", counter="agent_calls")
    async def agent():
        return await generation({"instruction": CANARY}, b"private-image-canary")

    with obs.task(task_id, run_id, "context-v2") as run:
        assert await agent() == CANARY
        progress(advanced=False)
        progress(advanced=False)
        progress(advanced=True)
        count("recovery_escalations")
        obs.finish(run, state="terminada", queue_wait=2, task_duration=3, run_duration=1)
    exported = spans(exporter, obs.client)
    root, agent_span, gen = (exported[name] for name in ("execute-task", "agent", "generation"))
    assert agent_span.parent.span_id == root.context.span_id
    assert gen.parent.span_id == agent_span.context.span_id
    assert {span.context.trace_id for span in exported.values()} == {root.context.trace_id}
    assert format(root.context.trace_id, "032x") == obs.client.create_trace_id(seed=str(task_id))
    assert gen.attributes["langfuse.observation.type"] == "generation"
    assert json.loads(gen.attributes["langfuse.observation.usage_details"]) == {"input": 11, "output": 3}
    assert root.attributes["langfuse.observation.metadata.task_id"] == str(task_id)
    scores = {call.kwargs["name"]: call.kwargs["value"] for call in obs.client.create_score.call_args_list}
    assert scores["model_calls"] == scores["agent_calls"] == 1
    assert scores["no_progress_events"] == scores["no_progress_max_streak"] == 2
    assert scores["recovery_escalations"] == scores["task_success"] == 1
    assert scores["task_duration_seconds"] == 3
    payload = str([dict(span.attributes) for span in exported.values()]) + str(obs.client.create_score.call_args_list)
    assert CANARY not in payload and "private-image-canary" not in payload
    assert all(not span.events for span in exported.values())


async def test_exception_and_cancellation_never_capture_private_text(telemetry):
    obs, exporter = telemetry

    @observed("failing-agent", as_type="agent")
    async def fail():
        raise ValueError(CANARY)

    @observed("cancelled-generation", as_type="generation", counter="model_calls")
    async def wait():
        await asyncio.sleep(60)

    with obs.task(uuid4(), uuid4(), "v1") as run:
        with pytest.raises(ValueError, match=CANARY):
            await fail()
        pending = asyncio.create_task(wait())
        await asyncio.sleep(0)
        pending.cancel(CANARY)
        with pytest.raises(asyncio.CancelledError):
            await pending
        obs.finish(run, state="fallida", queue_wait=0, task_duration=0, run_duration=0)
    exported = spans(exporter, obs.client)
    assert exported["failing-agent"].attributes["langfuse.observation.metadata.status"] == "error"
    assert exported["cancelled-generation"].attributes["langfuse.observation.metadata.status"] == "cancelled"
    assert CANARY not in str([dict(span.attributes) for span in exported.values()])
    assert all(not span.events for span in exported.values())


async def test_parallel_tasks_isolated_and_late_completion_ignored(telemetry):
    obs, exporter = telemetry
    release = asyncio.Event()

    async def late():
        await release.wait()
        count("model_calls")

    async def task(calls):
        with obs.task(uuid4(), uuid4(), "v1") as run:
            for _ in range(calls):
                count("model_calls")
            await asyncio.sleep(0)
            pending = asyncio.create_task(late())
            obs.finish(run, state="fallida", queue_wait=0, task_duration=0, run_duration=0)
        return run, pending

    outputs = await asyncio.gather(task(1), task(4))
    release.set()
    await asyncio.gather(*(pending for _, pending in outputs))
    assert [run.counters["model_calls"] for run, _ in outputs] == [1, 4]


async def test_export_failure_does_not_change_runner_closure_or_result(tmp_path, caplog):
    from tests.unit.test_runner import setup_run, success

    client = Mock()
    client.start_observation.side_effect = RuntimeError(CANARY)
    runner, claim, timeline, _ = await setup_run(tmp_path, success)
    runner.observability = Observability(client)
    result = await runner.run(claim, "owner")
    assert result.estado == "terminada"
    assert timeline.index("socket_closed") < timeline.index("terminal")
    assert CANARY not in caplog.text


async def test_unconfirmed_owner_closure_never_scores_success(tmp_path, telemetry):
    from tests.unit.test_runner import setup_run, success

    obs, exporter = telemetry
    runner, claim, timeline, _ = await setup_run(tmp_path, success)
    runner.observability = obs
    claim.instruction = CANARY

    async def unconfirmed():
        raise RuntimeError(CANARY)

    factory = runner.gateway_factory

    def gateway(*args):
        result = factory(*args)
        result.close_and_confirm = unconfirmed
        return result

    runner.gateway_factory = gateway
    with pytest.raises(RuntimeError, match=CANARY):
        await runner.run(claim, "owner")
    assert "terminal" not in timeline
    root = spans(exporter, obs.client)["execute-task"]
    assert root.attributes["langfuse.observation.metadata.state"] == "unconfirmed"
    scores = {call.kwargs["name"]: call.kwargs["value"] for call in obs.client.create_score.call_args_list}
    assert scores["task_success"] == 0
    assert CANARY not in str(dict(root.attributes))


def test_missing_keys_or_disabled_do_not_initialize_sdk(monkeypatch):
    import langfuse

    factory = Mock(side_effect=AssertionError("Must not initialize"))
    monkeypatch.setattr(langfuse, "Langfuse", factory)
    assert Observability.from_settings(Settings(langfuse_public_key="", langfuse_secret_key="")).client is None
    assert Observability.from_settings(Settings(langfuse_enabled=False,
        langfuse_public_key="synthetic", langfuse_secret_key="synthetic")).client is None
    assert not factory.called
