"""Disposable integration services only; never defaults to a user's existing DB."""

import asyncio
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from redis.asyncio import Redis
from sqlalchemy import text

from app.persistence.database import database_pool
from app.persistence.repositories import TaskRepository
from app.persistence.secrets import CredentialCipher


@pytest.fixture
async def platform_db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL for a disposable PostgreSQL database")
    env = {**os.environ, "DATABASE_URL": url}
    # These tests intentionally reset all app tables in the explicit test database.
    if "test" not in url.rsplit("/", 1)[-1]:
        raise RuntimeError("TEST_DATABASE_URL must name a disposable database containing 'test'")
    root = Path(__file__).resolve().parents[2]
    result = await asyncio.to_thread(
        subprocess.run,
        [str(root / ".venv/bin/alembic"), "upgrade", "head"],
        env=env,
        cwd=root,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, "Alembic upgrade failed (driver diagnostics withheld)"
    engine, sessions = database_pool(url, pool_size=5)
    async with engine.begin() as db:
        await db.execute(
            text(
                "TRUNCATE app.tasks, app.task_runs, app.desktop_sessions, app.agent_events, app.artifacts, app.observations, app.action_attempts, app.outbox, app.service_heartbeats, private.task_credentials CASCADE"
            )
        )
    yield engine, sessions
    await engine.dispose()


@pytest.fixture
async def platform_redis():
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for disposable Redis")
    redis = Redis.from_url(url, decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def cipher():
    return CredentialCipher(Fernet.generate_key().decode(), "h" * 32)


@pytest.fixture
async def repository(platform_db, cipher):
    return TaskRepository(platform_db[1], cipher)


@pytest.fixture
def stream_name():
    return "frontier:test:" + uuid4().hex
