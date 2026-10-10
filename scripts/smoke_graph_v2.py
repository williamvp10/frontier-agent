"""Opt-in paid v2 query on a synthetic in-memory desktop. No live VNC/DB/reset."""

import argparse
import asyncio
import re
import time
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from PIL import Image, ImageDraw

from app.agents.graph import run_agent
from app.agents.runtime import AgentRuntime
from app.core.config import Settings
from app.domain.contracts import Observation, utcnow
from app.models.factory import build_visual_model
from app.models.jev_advisor import AdviceSession


class SyntheticDesktop:
    def __init__(self):
        self.run_id, self.session_id = uuid4(), uuid4()
        image = Image.new("RGB", (1280, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((100, 100), "SYNTHETIC REPORT", fill="black", font_size=48)
        draw.text((100, 220), "Invoice count: 7", fill="black", font_size=48)
        stream = BytesIO()
        image.save(stream, "PNG")
        self.image = stream.getvalue()
        self.actions = 0

    async def observe(self):
        return Observation(run_id=self.run_id, session_id=self.session_id, epoch=1,
            frame_revision=0, captured_at=utcnow(), width=1280, height=800,
            artifact_id=uuid4(), sha256="0" * 64)

    async def image_bytes(self, observation_id):
        return self.image

    async def authorize(self, *args):
        raise RuntimeError("Synthetic read-only desktop cannot authorize actions")

    async def execute(self, *args):
        self.actions += 1
        raise RuntimeError("Synthetic read-only desktop cannot execute actions")


class Recorder:
    def __init__(self):
        self.roles = []
        self.usage = []
        self.failures = []
        self.context_failures = []

    async def append(self, role, kind, payload):
        if kind == "delegation":
            self.roles.append(payload["specialist"])
        elif kind == "model_usage":
            self.usage.append((role, payload))
        elif kind == "model_failure":
            self.failures.append(payload)
        elif kind == "context_failure":
            self.context_failures.append(payload)


async def smoke(env_file):
    settings = Settings(_env_file=env_file)
    model = build_visual_model(settings)
    await model.validate_capabilities()
    desktop, recorder = SyntheticDesktop(), Recorder()
    cutoff = utcnow() + timedelta(seconds=60)
    runtime = AgentRuntime(model=model, desktop=desktop, recorder=recorder,
        action_cutoff_at=cutoff, task_deadline_at=cutoff, graph_version="context-v2",
        max_steps=6, max_actions=0, max_recoveries=1, secrets=(settings.openrouter_api_key.get_secret_value(),),
        advice_session=AdviceSession(run_id=desktop.run_id, action_cutoff_at=cutoff, advisor=None))
    start = time.monotonic()
    saver = InMemorySaver()
    result = await run_agent("Read the invoice count in the current report. Do not change any data.",
        desktop.run_id, desktop.session_id, 1, runtime, checkpointer=saver)
    # Public contract: the first number after respuesta: is the numeric result.
    answer = re.search(r"-?\d+(?:[.,]\d+)?", result.resumen.removeprefix("respuesta:"))
    matches = bool(answer and float(answer.group().replace(",", ".")) == 7)
    if result.estado != "terminada" or not result.resumen.startswith("respuesta:") or not matches or desktop.actions:
        print(f"V2 synthetic query failed: reason={result.reason_code}; actions={desktop.actions}")
        for failure in recorder.failures:
            print(f"OpenRouter HTTP {failure['http_status']}; category={failure['category']}; "
                  f"schema_features={','.join(failure['schema_features'])}")
        for failure in recorder.context_failures:
            print(f"context_stage={failure['stage']}; rejection={failure['context_error']}")
        return 1
    print(f"V2 synthetic query passed; roles={','.join(recorder.roles)}, actions=0, latency_s={time.monotonic()-start:.2f}")
    for role, usage in recorder.usage:
        print(f"role={role}, model={usage['model']}, input_tokens={usage.get('input_tokens', 'unknown')}, "
              f"output_tokens={usage.get('output_tokens', 'unknown')}, cost_usd={usage.get('cost_usd', 'unknown')}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args()
    try:
        return asyncio.run(smoke(args.env_file))
    except Exception as exc:
        print(f"V2 synthetic query unavailable ({type(exc).__name__}); private details withheld")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
