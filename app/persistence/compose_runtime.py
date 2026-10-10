"""Compose bootstrap and private per-service connection files; never log DSNs."""

import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from app.persistence.database import database_pool
from app.persistence.grants import setup_grants

ROLE_GROUPS = {
    "api": ("frontier_api",),
    "dispatcher": ("frontier_dispatcher",),
    "worker": ("frontier_worker", "frontier_authority", "frontier_supervisor"),
}
CONFIG_ROOT = Path("/var/lib/frontier/connections")


def connection_urls(user: str, password: str) -> dict[str, str]:
    # URL.create handles reserved characters in administrator passwords.
    url = URL.create(
        "postgresql+psycopg", username=user, password=password,
        host="frontier-agent-postgres", port=5432, database="frontier",
    )
    return {
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "LANGGRAPH_DATABASE_URI": url.set(drivername="postgresql").render_as_string(
            hide_password=False
        ),
    }


async def provision_connections(admin_uri: str, root: Path = CONFIG_ROOT) -> None:
    """Persist passwords before DDL so retry after a crash uses the same credentials."""
    configs = {}
    for role in ROLE_GROUPS:
        directory = root / role
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "connection.json"
        if path.exists():
            configs[role] = json.loads(path.read_text())
        else:
            configs[role] = connection_urls(f"frontier_compose_{role}_login", secrets.token_hex(32))
            # Exclusive creation and 0600 permissions even before the first write.
            temporary = directory / "connection.tmp"
            with open(temporary, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                json.dump(configs[role], stream)
            temporary.replace(path)
    async with await psycopg.AsyncConnection.connect(admin_uri) as conn:
        for role, groups in ROLE_GROUPS.items():
            name = f"frontier_compose_{role}_login"
            password = make_url(configs[role]["DATABASE_URL"]).password
            exists = await conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (name,))
            if not await exists.fetchone():
                await conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(name)))
            await conn.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}")
                .format(sql.Identifier(name), sql.Literal(password))
            )
            await conn.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.SQL(", ").join(map(sql.Identifier, groups)), sql.Identifier(name)
                )
            )


async def bootstrap_compose() -> None:
    # Import the existing one-off bootstrap; this module is also the runtime entrypoint.
    from scripts.bootstrap import bootstrap

    admin = connection_urls("frontier", os.environ["POSTGRES_PASSWORD"])
    orm = os.environ.get("DATABASE_URL") or admin["DATABASE_URL"]
    native = os.environ.get("LANGGRAPH_DATABASE_URI") or admin["LANGGRAPH_DATABASE_URI"]
    grants = os.environ.get("GRANTS_DATABASE_URL") or admin["DATABASE_URL"]
    await bootstrap(orm, native)
    engine, _ = database_pool(grants, pool_size=1)
    try:
        async with engine.begin() as connection:
            await setup_grants(connection)
    finally:
        await engine.dispose()
    await provision_connections(grants.replace("postgresql+psycopg://", "postgresql://", 1))


def load_connection(role: str, root: Path = CONFIG_ROOT) -> None:
    if role not in ROLE_GROUPS:
        raise ValueError("Unsupported runtime role")
    if os.environ.get("DATABASE_URL") and (
        role != "worker" or os.environ.get("LANGGRAPH_DATABASE_URI")
    ):
        return  # Existing private DSN overrides remain supported.
    config = json.loads((root / role / "connection.json").read_text())
    for key in ("DATABASE_URL", "LANGGRAPH_DATABASE_URI"):
        if key == "LANGGRAPH_DATABASE_URI" and role != "worker":
            continue
        if not os.environ.get(key):
            os.environ[key] = config[key]


def main() -> int:
    try:
        if sys.argv[1:] == ["bootstrap"]:
            asyncio.run(bootstrap_compose())
            print("Compose bootstrap completed: schemas, grants and runtime logins.")
            return 0
        role = os.environ["FRONTIER_SERVICE_ROLE"]
        load_connection(role)
        if not sys.argv[1:]:
            raise ValueError("Runtime command is required")
    except Exception as exc:
        print(f"Compose initialization failed ({type(exc).__name__}); details withheld.",
              file=sys.stderr)
        return 1
    os.execvp(sys.argv[1], sys.argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
