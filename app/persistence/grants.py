"""Explicit administrator bootstrap for fixed NOLOGIN privilege groups.

Run separately with GRANTS_DATABASE_URL; do not grant table ownership to runtime
logins. A combined demo worker/authority may inherit frontier_authority as well
as frontier_worker. A model is never given a database connection.
"""

import asyncio
import os

from sqlalchemy import text

from app.persistence.database import database_pool

ROLES = (
    "frontier_api",
    "frontier_worker",
    "frontier_authority",
    "frontier_dispatcher",
    "frontier_supervisor",
    "frontier_auditor",
)


async def setup_grants(connection) -> None:
    for role in ROLES:
        # Names are constants, not caller-controlled identifiers.
        await connection.execute(
            text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"
            )
        )
        await connection.execute(text(f"GRANT USAGE ON SCHEMA app TO {role}"))
    await connection.execute(text("REVOKE ALL ON SCHEMA private FROM PUBLIC"))
    await connection.execute(text("REVOKE ALL ON private.task_credentials FROM PUBLIC"))
    await connection.execute(text("GRANT SELECT, INSERT, UPDATE ON app.tasks TO frontier_api"))
    await connection.execute(text("GRANT INSERT ON app.outbox TO frontier_api"))
    await connection.execute(text("GRANT SELECT ON app.service_heartbeats TO frontier_api"))
    await connection.execute(
        text(
            "GRANT USAGE ON SCHEMA private TO frontier_api, frontier_authority, frontier_supervisor, frontier_worker"
        )
    )
    await connection.execute(text("GRANT INSERT ON private.task_credentials TO frontier_api"))
    await connection.execute(text("GRANT SELECT ON private.task_credentials TO frontier_authority"))
    await connection.execute(
        text("GRANT DELETE ON private.task_credentials TO frontier_supervisor, frontier_worker")
    )
    await connection.execute(
        text(
            "GRANT SELECT(task_id) ON private.task_credentials TO frontier_supervisor, frontier_worker"
        )
    )
    await connection.execute(
        text(
            "GRANT SELECT ON ALL TABLES IN SCHEMA app TO frontier_worker, frontier_supervisor, frontier_authority, frontier_auditor"
        )
    )
    await connection.execute(
        text(
            "GRANT INSERT, UPDATE ON app.task_runs, app.desktop_sessions, app.artifacts, app.observations, app.action_attempts, app.service_heartbeats TO frontier_worker"
        )
    )
    await connection.execute(
        text("GRANT UPDATE ON app.tasks TO frontier_worker, frontier_supervisor")
    )
    await connection.execute(
        text("GRANT INSERT ON app.agent_events TO frontier_worker, frontier_supervisor")
    )
    await connection.execute(
        text("GRANT UPDATE ON app.task_runs, app.desktop_sessions TO frontier_supervisor")
    )
    await connection.execute(
        text("GRANT INSERT, UPDATE ON app.desktop_sessions TO frontier_authority")
    )
    await connection.execute(text("GRANT UPDATE(version) ON app.tasks TO frontier_authority"))
    await connection.execute(
        text("GRANT UPDATE(heartbeat_at) ON app.task_runs TO frontier_authority")
    )
    await connection.execute(text("GRANT SELECT, UPDATE ON app.outbox TO frontier_dispatcher"))
    for schema in ("graph_checkpoints", "graph_store"):
        exists = await connection.scalar(
            text("SELECT EXISTS (SELECT FROM pg_namespace WHERE nspname=:name)"), {"name": schema}
        )
        if exists:
            await connection.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO frontier_worker, frontier_api"))
            await connection.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO frontier_worker"
                )
            )
            await connection.execute(
                text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO frontier_worker")
            )


async def main():
    url = os.environ.get("GRANTS_DATABASE_URL")
    if not url:
        raise RuntimeError("GRANTS_DATABASE_URL with role-administration privileges is required")
    engine, _ = database_pool(url, pool_size=1)
    try:
        async with engine.begin() as connection:
            await setup_grants(connection)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
