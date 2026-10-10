"""Automatic logins with real disposable PostgreSQL and safe connection loading."""

import json
import os
import stat

import psycopg
import pytest
from sqlalchemy.engine import make_url

from app.persistence import compose_runtime as runtime
from app.persistence.database import database_pool
from app.persistence.grants import setup_grants
from scripts.bootstrap import bootstrap


@pytest.mark.unit
def test_urls_encode_password_and_runtime_respects_private_overrides(tmp_path, monkeypatch):
    password = "synthetic@:/?#%password"
    urls = runtime.connection_urls("frontier", password)
    assert make_url(urls["DATABASE_URL"]).password == password
    assert make_url(urls["LANGGRAPH_DATABASE_URI"]).password == password
    directory = tmp_path / "worker"
    directory.mkdir()
    (directory / "connection.json").write_text(json.dumps(urls))
    monkeypatch.setenv("DATABASE_URL", "explicit-orm-override")
    monkeypatch.setenv("LANGGRAPH_DATABASE_URI", "")
    runtime.load_connection("worker", tmp_path)
    assert os.environ["DATABASE_URL"] == "explicit-orm-override"
    assert os.environ["LANGGRAPH_DATABASE_URI"] == urls["LANGGRAPH_DATABASE_URI"]
    with pytest.raises(ValueError):
        runtime.load_connection("unknown", tmp_path)


@pytest.mark.integration
async def test_automatic_logins_survive_retry_and_have_only_service_permissions(tmp_path, monkeypatch):
    dsn = os.environ.get("BOOTSTRAP_TEST_DATABASE_URI")
    if not dsn:
        pytest.skip("Requires disposable BOOTSTRAP_TEST_DATABASE_URI")
    orm = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    await bootstrap(orm, dsn)
    engine, _ = database_pool(orm, pool_size=1)
    try:
        async with engine.begin() as conn:
            await setup_grants(conn)
    finally:
        await engine.dispose()
    admin_url = make_url(dsn)

    def urls(user, password):
        url = admin_url.set(username=user, password=password)
        return {
            "DATABASE_URL": url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False),
            "LANGGRAPH_DATABASE_URI": url.render_as_string(hide_password=False),
        }

    monkeypatch.setattr(runtime, "connection_urls", urls)
    await runtime.provision_connections(dsn, tmp_path)
    original = {role: (tmp_path / role / "connection.json").read_bytes()
                for role in runtime.ROLE_GROUPS}
    await runtime.provision_connections(dsn, tmp_path)
    for role in runtime.ROLE_GROUPS:
        path = tmp_path / role / "connection.json"
        assert path.read_bytes() == original[role]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        uri = json.loads(path.read_text())["LANGGRAPH_DATABASE_URI"]
        async with await psycopg.AsyncConnection.connect(uri, autocommit=True) as conn:
            result = await (await conn.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user"
            )).fetchone()
            assert result == (False, False, False)
            await conn.execute("SELECT 1 FROM app.tasks LIMIT 1" if role != "dispatcher"
                               else "SELECT 1 FROM app.outbox LIMIT 1")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("CREATE TABLE app.forbidden_runtime_ddl (id int)")
            if role == "api":
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await conn.execute("SELECT * FROM private.task_credentials")
            if role == "worker":
                await conn.execute("SELECT 1 FROM private.task_credentials LIMIT 1")
                await conn.execute("SELECT 1 FROM graph_checkpoints.checkpoints LIMIT 1")
