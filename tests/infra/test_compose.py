"""Validate the real Compose parser without reading private environment files."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.integration
def test_compose_has_isolated_secret_roles_and_replica_safe_config():
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is unavailable")
    source = Path("infra/compose.yaml").read_text()
    names = re.findall(r"\$\{([A-Z_]+):\?", source)
    env = {"PATH": os.environ["PATH"], **dict.fromkeys(names, "synthetic-test-value")}
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "bootstrap",
            "--env-file",
            "/dev/null",
            "-f",
            "infra/compose.yaml",
            "config",
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    services = json.loads(completed.stdout)["services"]
    for service in services.values():
        assert "container_name" not in service
        assert not service.get("ports")
        assert not any("docker.sock" in str(v) for v in service.get("volumes", []))
    assert "OPENAI_API_KEY" not in services["api"]["environment"]
    assert "OPENROUTER_API_KEY" not in services["api"]["environment"]
    assert "TYPESAFE_API_KEY" not in services["api"]["environment"]
    assert "TYPESAFE_API_KEY" not in services["dispatcher"]["environment"]
    assert services["worker"]["environment"]["JEV_ENABLED"] == "false"
    assert services["worker"]["environment"]["MODEL_PROVIDER"] == "openrouter"
    assert "CREDENTIALS_ENCRYPTION_KEY" not in services["dispatcher"]["environment"]
    assert "TEAM_ENTRYPOINT_TOKEN" not in services["worker"]["environment"]
    assert "bootstrap" in services
    assert services["bootstrap"]["restart"] == "no"
    assert services["worker"]["init"]
    assert services["worker"]["read_only"]
