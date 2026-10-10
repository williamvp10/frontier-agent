"""Run the suite against disposable Compose dependencies; never load a private .env."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if not shutil.which("docker") or not shutil.which("uv"):
        print("Requires Docker Compose v2 and uv.", file=sys.stderr)
        return 1
    # Unique project isolates parallel checkouts/runs and all cleanup operations.
    project = "frontier-agent-test-" + uuid4().hex[:12]
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("COMPOSE_")
    }
    command = [
        "docker", "compose", "--env-file", "/dev/null", "--project-name", project,
        "-f", str(ROOT / "infra/compose.test.yaml"),
    ]

    def compose(*args: str, capture: bool = False):
        return subprocess.run(
            [*command, *args], cwd=ROOT, env=env, check=True, text=True,
            capture_output=capture,
        )

    try:
        compose("up", "-d", "--wait", "--wait-timeout", "90")
        pg = compose("port", "frontier-agent-postgres-test", "5432", capture=True)
        redis = compose("port", "frontier-agent-redis-test", "6379", capture=True)
        pg_port = int(pg.stdout.strip().rsplit(":", 1)[1])
        redis_port = int(redis.stdout.strip().rsplit(":", 1)[1])
        if not (0 < pg_port <= 65535 and 0 < redis_port <= 65535):
            raise ValueError("Compose did not publish valid test ports")
        native_uri = (
            f"postgresql://frontier_test:synthetic-test-password@127.0.0.1:{pg_port}"
            "/frontier_test"
        )
        env.update(
            TEST_DATABASE_URL=native_uri.replace("postgresql://", "postgresql+psycopg://", 1),
            BOOTSTRAP_TEST_DATABASE_URI=native_uri,
            TEST_REDIS_URL=f"redis://127.0.0.1:{redis_port}/0",
            LANGSMITH_TRACING="false",
            LANGFUSE_ENABLED="false",
        )
        args = sys.argv[1:] or ["-m", "not e2e and not release", "-q"]
        return subprocess.run(
            ["uv", "run", "--frozen", "--extra", "dev", "pytest", *args], cwd=ROOT, env=env,
        ).returncode
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        print(f"Test infrastructure failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        # Only this run's test project; no persistent production volumes involved.
        try:
            compose("down", "--volumes", "--remove-orphans")
        except (subprocess.CalledProcessError, OSError):
            print(f"Cleanup failed; remove test project {project}.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
