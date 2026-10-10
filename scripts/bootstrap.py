"""One-off application and native LangGraph schema bootstrap, never per replica."""

import asyncio
import os
import subprocess
import sys

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

BOOTSTRAP_LOCK = 73810921
GRAPH_SCHEMAS = ("graph_checkpoints", "graph_store")


def schema_dsn(dsn: str, schema: str) -> str:
    """Restrict native tables to a fixed namespace; runtime must use the same helper."""
    if schema not in GRAPH_SCHEMAS:
        raise ValueError("Unsupported graph schema")
    return make_conninfo(dsn, options=f"-c search_path={schema}")


async def bootstrap(database_url: str, native_uri: str) -> None:
    if not database_url or not native_uri:
        raise ValueError("DATABASE_URL and LANGGRAPH_DATABASE_URI are required")
    # Lock on application DB even if graph tables live in another DB.
    lock_uri = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    async with await psycopg.AsyncConnection.connect(lock_uri, autocommit=True) as lock:
        cursor = await lock.execute("SELECT pg_try_advisory_lock(%s)", (BOOTSTRAP_LOCK,))
        if not (await cursor.fetchone())[0]:
            raise RuntimeError("Another bootstrap is running; retry after it completes")
        try:
            # Alembic's asyncio.run must run in its own process, not the running loop.
            env = {**os.environ, "DATABASE_URL": database_url}
            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                check=True,
                env=env,
                capture_output=True,
            )
            async with await psycopg.AsyncConnection.connect(native_uri, autocommit=True) as conn:
                for schema in GRAPH_SCHEMAS:
                    await conn.execute(
                        psycopg.sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                            psycopg.sql.Identifier(schema)
                        )
                    )
            for schema, factory in (
                ("graph_checkpoints", AsyncPostgresSaver),
                ("graph_store", AsyncPostgresStore),
            ):
                async with await psycopg.AsyncConnection.connect(
                    schema_dsn(native_uri, schema),
                    autocommit=True,
                    prepare_threshold=0,
                    row_factory=dict_row,
                ) as conn:
                    await factory(conn).setup()
        finally:
            await lock.execute("SELECT pg_advisory_unlock(%s)", (BOOTSTRAP_LOCK,))


def main() -> int:
    try:
        asyncio.run(
            bootstrap(
                os.environ.get("DATABASE_URL", ""), os.environ.get("LANGGRAPH_DATABASE_URI", "")
            )
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary must redact every error
        # Provider/DB errors can contain credentials and URLs. No raw exceptions.
        print(
            f"Bootstrap failed ({type(exc).__name__}); inspect private diagnostics.",
            file=sys.stderr,
        )
        return 1
    print("Bootstrap completed: Alembic and isolated LangGraph schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
