"""Native schema isolation and bootstrap integration (explicit disposable database)."""

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from scripts.bootstrap import BOOTSTRAP_LOCK, bootstrap, schema_dsn


@pytest.mark.unit
def test_native_dsn_is_fixed_schema_only():
    assert (
        conninfo_to_dict(schema_dsn("postgresql://localhost/test", "graph_store"))["options"]
        == "-c search_path=graph_store"
    )
    with pytest.raises(ValueError):
        schema_dsn("postgresql://localhost/test", "public; DROP SCHEMA app CASCADE")


@pytest.mark.integration
async def test_bootstrap_idempotence_schema_isolation_and_exclusion():
    dsn = os.environ.get("BOOTSTRAP_TEST_DATABASE_URI")
    if not dsn:
        pytest.skip("Requires disposable BOOTSTRAP_TEST_DATABASE_URI")
    url = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    await bootstrap(url, dsn)
    await bootstrap(url, dsn)
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        rows = await (
            await conn.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_name IN ('checkpoints','store')"
            )
        ).fetchall()
        assert set(rows) == {("graph_checkpoints", "checkpoints"), ("graph_store", "store")}
        await conn.execute("SELECT pg_advisory_lock(%s)", (BOOTSTRAP_LOCK,))
        try:
            with pytest.raises(RuntimeError, match="Another bootstrap"):
                await bootstrap(url, dsn)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (BOOTSTRAP_LOCK,))
