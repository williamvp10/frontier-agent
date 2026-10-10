"""Authorized, zero-input live VNC query with isolated PostgreSQL and confirmed close.

Model/VNC credentials are loaded privately by their transports. Never print the
answer, screenshot, URL or password. No reset/check, business API or UI mutation.
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.graph_memory import graph_memory
from app.core.observability import Observability
from app.desktop.gateway import GatewayError, ProcessGateway
from app.domain.contracts import utcnow
from app.models.factory import build_visual_model
from app.persistence.database import database_pool
from app.persistence.models import ActionAttempt, DesktopSession, ObservationRow
from app.persistence.repositories import (
    EventRecorder,
    EvidenceRepository,
    SessionRepository,
    TaskRepository,
)
from app.persistence.secrets import CredentialCipher
from app.worker.runner import TaskRunner


class ReadOnlyGateway(ProcessGateway):
    async def request(self, kind, **fields):
        if kind != "observe":
            raise GatewayError("Read-only smoke forbids desktop input")
        return await super().request(kind, **fields)


async def smoke(model_env, vnc_env):
    url = os.environ.get("TEST_DATABASE_URL", "")
    if "test" not in url.rsplit("/", 1)[-1]:
        raise RuntimeError("An explicit isolated TEST_DATABASE_URL is required")
    settings = Settings(_env_file=model_env).model_copy(update={
        "graph_version": "context-v2", "max_actions": 0, "max_model_calls": 8,
        "max_recoveries": 1, "jev_enabled": False, "langfuse_enabled": False,
        "langsmith_tracing": False, "artifact_dir": Path(".private/artifacts")})
    model = build_visual_model(settings)
    await model.validate_capabilities()
    # Transport-only credential access, not model context or stdout.
    private = dotenv_values(vnc_env)
    target, password = private.get("VNC_WS_URL"), private.get("VNC_PASSWORD")
    if not target or not password:
        raise RuntimeError("VNC credential configuration is incomplete")
    engine, sessions = database_pool(url, pool_size=5)
    cipher = CredentialCipher(Fernet.generate_key().decode(), Fernet.generate_key().decode())
    repository = TaskRepository(sessions, cipher)
    accepted_at = utcnow()
    started = time.monotonic()
    try:
        task_id = await repository.accept_with_outbox(
            instruction="Read only the visible application page title. Do not navigate or change anything. "
                        "Exclude URLs, credentials, personal names and business record values from the answer.",
            url=target, password=password, team_id="isolated-live-smoke", accepted_at=accepted_at)
        del private, target, password
        claim = await repository.claim(task_id, "isolated-live-smoke", graph_version="context-v2",
                                       model_version=settings.model_operator)
        if claim is None:
            raise RuntimeError("Isolated smoke task could not be claimed")
        uri = url.replace("postgresql+psycopg://", "postgresql://")
        async with graph_memory(uri) as (saver, _):
            runner = TaskRunner(settings, repository, SessionRepository(sessions), EvidenceRepository(sessions),
                EventRecorder(sessions), model, checkpointer=saver, gateway_factory=ReadOnlyGateway,
                observability=Observability())
            result = await runner.run(claim, "isolated-live-smoke")
        async with sessions() as db:
            session = await db.scalar(select(DesktopSession).where(DesktopSession.run_id == claim.run_id))
            attempts = await db.scalar(select(func.count()).select_from(ActionAttempt).where(
                ActionAttempt.run_id == claim.run_id))
            observations = list((await db.scalars(select(ObservationRow).where(
                ObservationRow.run_id == claim.run_id))).all())
        proof = bool(session and session.status == "closed" and session.close_evidence.get("owner_stopped"))
        metadata = {"estado": result.estado, "reason_code": result.reason_code,
            "action_attempts": attempts, "observations": len(observations), "owner_stopped": proof,
            "duration_seconds": round(time.monotonic() - started, 2)}
        target = Path(".private/evidencias") / f"live-query-{claim.run_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(metadata, output)
        print(json.dumps(metadata))
        return 0 if result.estado == "terminada" and attempts == 0 and proof else 1
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-env-file", default=None)
    parser.add_argument("--vnc-env-file", default=".env.vnc")
    args = parser.parse_args()
    try:
        return asyncio.run(smoke(args.model_env_file, args.vnc_env_file))
    except Exception as exc:
        print(f"Live query unavailable ({type(exc).__name__}); private details withheld")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
