"""Check staged content and untracked public candidates without printing secrets."""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def local_secrets() -> list[bytes]:
    values = []
    for path in ROOT.glob(".env*"):
        if path.name == ".env.example" or not path.is_file():
            continue
        for line in path.read_text().splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("\"'")
            # Only these exact public origins are configuration, never credentials.
            if (key.strip(), value) in {
                ("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                ("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com"),
                ("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
            }:
                continue
            if value and len(value) >= 6 and any(
                word in key.upper() for word in ("PASSWORD", "TOKEN", "KEY", "URL")
            ):
                values.append(value.encode())
    return values


def main() -> int:
    staged = {p for p in git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\0") if p}
    untracked = {p for p in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    secrets = local_secrets()
    problems = []
    patterns = (
        rb"ftr_[a-f0-9]{20,}",
        rb"(?:sk|pk)-lf-[A-Za-z0-9_-]{16,}",
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        rb"frontier-r1-tm\d+[^\s/]*\.up\.railway\.app",
    )
    for raw_name in sorted(staged | untracked):
        name = raw_name.decode()
        path = Path(name)
        if (path.name.startswith(".env") and path.name != ".env.example") or ".private" in path.parts:
            problems.append((name, "archivo privado incluido"))
            continue
        full_path = ROOT / name
        if name.startswith("artifacts/") or name.startswith("screenshots/"):
            problems.append((name, "evidencia local incluida"))
            continue
        if raw_name in staged:
            data = git("show", f":{name}")
        elif full_path.is_symlink():
            problems.append((name, "enlace simbólico requiere revisión"))
            continue
        else:
            data = full_path.read_bytes()
        if path.suffix == ".ipynb":
            try:
                notebook = json.loads(data)
                if any(cell.get("outputs") or cell.get("execution_count") is not None
                       or cell.get("attachments") for cell in notebook.get("cells", [])):
                    problems.append((name, "notebook con outputs o adjuntos; limpiar antes de publicar"))
            except (ValueError, TypeError):
                problems.append((name, "notebook inválido"))
        if any(value in data for value in secrets) or any(re.search(pattern, data) for pattern in patterns):
            problems.append((name, "posible secreto o acceso privado"))
    for name, reason in problems:
        print(f"BLOQUEADO: {name}: {reason}")
    if problems:
        return 1
    print(f"OK: {len(staged | untracked)} archivos revisados; sin coincidencias privadas detectadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
