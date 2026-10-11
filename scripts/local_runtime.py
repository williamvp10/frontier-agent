"""Prepare/start/status/stop local Docker runtime. Never operates a remote desktop.

Loads model credentials locally, writes ignored private configuration/certificates,
and uses explicit project scoping. No evaluator execution, reset or task submission.
"""

import argparse
import json
import os
import secrets
import ssl
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".private/local-docker"
ENV = LOCAL / "runtime.env"
PROJECT = "frontier-agent-local"


def write_private(path, data, *, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as output:
        output.write(data)


def certificates():
    import ipaddress

    directory = LOCAL / "tls"
    if (directory / "server.key").exists():
        if not all((directory / name).is_file() for name in ("server.pem", "ca.pem")):
            raise RuntimeError("Incomplete local certificate configuration")
        return directory
    now = datetime.now(UTC)
    ca_key, server_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Frontier local development CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_name).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=90))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    write_private(directory / "server.key", server_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    write_private(directory / "server.pem", leaf.public_bytes(serialization.Encoding.PEM), mode=0o644)
    write_private(directory / "ca.pem", ca.public_bytes(serialization.Encoding.PEM), mode=0o644)
    return directory


def prepare(model_env, port):
    if not 1 <= port <= 65535:
        raise RuntimeError("Invalid local port")
    if ENV.exists():
        print("Private local configuration already exists; preserving keys and data.")
        return
    values = {**dotenv_values(model_env, interpolate=False), **os.environ}
    if not values.get("OPENROUTER_API_KEY"):
        raise RuntimeError("Missing OpenRouter configuration")
    tls = certificates()
    password = secrets.token_hex(24)
    local = {
        "RELEASE_TAG": "local-docker", "POSTGRES_PASSWORD": password,
        "TEAM_ENTRYPOINT_TOKEN": values.get("TEAM_ENTRYPOINT_TOKEN") or secrets.token_urlsafe(32),
        "CREDENTIALS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "DESTINATION_HMAC_KEY": secrets.token_urlsafe(32),
        "OPENROUTER_API_KEY": values["OPENROUTER_API_KEY"],
        "LOCAL_API_PORT": str(port), "LOCAL_UID": str(os.getuid()), "LOCAL_GID": str(os.getgid()),
        "LOCAL_TLS_DIR": str(tls), "DOKPLOY_NETWORK": PROJECT + "-ingress",
        "API_REPLICAS": "1", "JEV_ENABLED": "false", "LANGFUSE_ENABLED": "false",
        "LANGSMITH_TRACING": "false", "GRAPH_VERSION": "context-v2",
    }
    endpoint = "frontier-agent-postgres:5432/frontier"
    admin = f"postgresql+psycopg://frontier:{password}@{endpoint}"
    local.update(MIGRATION_DATABASE_URL=admin, GRANTS_DATABASE_URL=admin,
                 BOOTSTRAP_LANGGRAPH_DATABASE_URI=admin.replace("postgresql+psycopg", "postgresql"))
    for role in ("api", "dispatcher", "worker"):
        credential = secrets.token_hex(24)
        local[f"LOCAL_{role.upper()}_PASSWORD"] = credential
        dsn = f"postgresql+psycopg://frontier_{role}_login:{credential}@{endpoint}"
        local[f"{role.upper()}_DATABASE_URL"] = dsn
        if role == "worker":
            local["LANGGRAPH_DATABASE_URI"] = dsn.replace("postgresql+psycopg", "postgresql")
    for key in ("MODEL_COORDINATOR", "MODEL_OPERATOR", "MODEL_VERIFIER", "MODEL_RECOVERY", "MODEL_ANALYST",
                "MODEL_JEV", "OPENROUTER_API_KEY_BACKUP", "MODEL_PROFILES", "MODEL_REASONING_EFFORT", "MODEL_TIMEOUT_SECONDS"):
        if values.get(key):
            local[key] = values[key]
    data = "\n".join(key + "=" + json.dumps(value) for key, value in local.items()) + "\n"
    write_private(ENV, data.encode())
    print("Local configuration and TLS saved in ignored .private/local-docker; no credentials printed.")


def evaluator_config(vnc_env):
    """Prepare a PRIVATE client config only; never connect/reset/submit a task."""
    private = {**dotenv_values(vnc_env, interpolate=False), **os.environ}
    local = dotenv_values(ENV, interpolate=False)
    keys = ("VNC_BASE_URL", "VNC_WS_URL", "VNC_PASSWORD", "VNC_DESKTOP_TOKEN")
    if not all(private.get(key) for key in keys):
        raise RuntimeError("Incomplete private VNC configuration")
    values = {key: private[key] for key in keys}
    values.update(TEAM_ENTRYPOINT_TOKEN=local["TEAM_ENTRYPOINT_TOKEN"],
                  TEAM_ENTRYPOINT_URL="https://127.0.0.1:" + local["LOCAL_API_PORT"])
    target = LOCAL / "evaluator.env"
    if target.exists():
        print("Existing private evaluator configuration preserved; no desktop operation.")
        return
    write_private(target, ("\n".join(key + "=" + json.dumps(value) for key, value in values.items()) + "\n").encode())
    print("Private evaluator configuration prepared; no task or reset executed.")


def compose(*args, input=None):
    result = subprocess.run(["docker", "compose", "--project-name", PROJECT, "--env-file", str(ENV),
        "-f", str(ROOT / "infra/compose.yaml"), "-f", str(ROOT / "infra/compose.local.yaml"), *args],
        cwd=ROOT, input=input, text=True, capture_output=True)
    if result.returncode:
        # Docker/driver errors can contain resolved configuration. Keep raw diagnostics private.
        target = LOCAL / ("docker-error-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".log")
        write_private(target, (result.stdout + result.stderr).encode())
        raise RuntimeError("Local Docker command failed; diagnostics kept private")
    return result.stdout


def start():
    compose("config", "--quiet")
    print("Building local image…", flush=True)
    # All app services share one image/tag; build once to avoid parallel export races.
    compose("build", "frontier-agent-api")
    print("Starting local PostgreSQL and Redis…", flush=True)
    compose("up", "-d", "--wait", "frontier-agent-postgres", "frontier-agent-redis")
    compose("run", "--rm", "frontier-agent-bootstrap")
    local = dotenv_values(ENV, interpolate=False)
    commands = []
    for role, groups in (("api", "frontier_api"), ("dispatcher", "frontier_dispatcher"),
                         ("worker", "frontier_worker, frontier_authority, frontier_supervisor")):
        name, password = f"frontier_{role}_login", local[f"LOCAL_{role.upper()}_PASSWORD"]
        # Passwords are generated hex; identifiers/groups are constants. SQL only on stdin.
        if not password or any(ch not in "0123456789abcdef" for ch in password):
            raise RuntimeError("Invalid local database configuration")
        commands.append(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{name}') "
                        f"THEN CREATE ROLE {name} LOGIN; END IF; END $$;")
        commands.append(f"ALTER ROLE {name} PASSWORD '{password}'; GRANT {groups} TO {name};")
    compose("exec", "-T", "frontier-agent-postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", "frontier",
            "-d", "frontier", input="\n".join(commands))
    compose("up", "-d", "--wait", "--wait-timeout", "120")
    status()


def status():
    values = dotenv_values(ENV, interpolate=False)
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(LOCAL / "tls/ca.pem"))
    url = "https://127.0.0.1:" + values["LOCAL_API_PORT"]
    with httpx.Client(verify=context, trust_env=False, timeout=5) as client:
        response = client.get(url + "/health", headers={"Authorization": "Bearer " + values["TEAM_ENTRYPOINT_TOKEN"]})
        if response.status_code != 200 or response.json() != {"ok": True}:
            raise RuntimeError("Local API is not ready")
    print(f"Local API ready at {url}; TLS verified. No task submitted.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "start", "status", "configure-evaluator", "stop"))
    parser.add_argument("--model-env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--port", type=int, default=18443)
    parser.add_argument("--vnc-env-file", type=Path, default=ROOT / ".env.vnc")
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            prepare(args.model_env_file, args.port)
        elif not ENV.exists():
            raise RuntimeError("Prepare local configuration first")
        elif args.operation == "start":
            start()
        elif args.operation == "status":
            status()
        elif args.operation == "configure-evaluator":
            evaluator_config(args.vnc_env_file)
        else:
            compose("down")
            print("Local runtime stopped; persistent volumes and keys preserved.")
        return 0
    except Exception as exc:
        print(f"Local operation unavailable ({type(exc).__name__}); private details withheld.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
