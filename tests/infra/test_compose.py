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
    assert set(names) == {
        "POSTGRES_PASSWORD", "TEAM_ENTRYPOINT_TOKEN", "CREDENTIALS_ENCRYPTION_KEY",
        "DESTINATION_HMAC_KEY", "OPENROUTER_API_KEY",
    }
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
    config = json.loads(completed.stdout)
    services = {
        name.removeprefix("frontier-agent-"): service
        for name, service in config["services"].items()
    }
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
    # Prevent startup before schema/grants readiness, including every API replica.
    for name in ("api", "dispatcher", "worker"):
        assert "POSTGRES_PASSWORD" not in services[name]["environment"]
        connections = [v for v in services[name]["volumes"] if "connections" in v["target"]]
        assert len(connections) == 1
        assert connections[0]["read_only"]
        assert connections[0]["target"].endswith("/" + name)
        assert services[name]["environment"]["DATABASE_URL"] == ""
        assert services[name]["depends_on"]["frontier-agent-bootstrap"]["condition"] == (
            "service_completed_successfully"
        )
        assert services[name]["environment"]["REDIS_URL"] == (
            "redis://frontier-agent-redis:6379/0"
        )
    assert "profiles" not in services["bootstrap"]
    assert services["worker"]["deploy"]["replicas"] == 1
    for service in services.values():
        assert service["deploy"]["resources"]["limits"]["memory"]
        assert float(service["deploy"]["resources"]["limits"]["cpus"]) > 0
    # Only the uniquely named API can share Dokploy's proxy network.
    for name, service in services.items():
        assert ("ingress" in service["networks"]) == (name == "api")
    assert config["networks"]["backend"]["internal"]
    for volume in config["volumes"].values():
        assert volume["name"].startswith("frontier-agent_frontier_agent_")


@pytest.mark.integration
def test_disposable_services_do_not_need_production_configuration():
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is unavailable")
    completed = subprocess.run(
        [
            "docker", "compose", "--env-file", "/dev/null", "-f",
            "infra/compose.test.yaml", "config", "--format", "json",
        ],
        env={"PATH": os.environ["PATH"]}, capture_output=True, text=True, check=True,
    )
    config = json.loads(completed.stdout)
    assert not config.get("volumes")
    assert all(not network.get("external") for network in config["networks"].values())
    for service in config["services"].values():
        for port in service["ports"]:
            assert port["host_ip"] == "127.0.0.1"
            assert not port.get("published")
        assert all(volume["type"] != "bind" for volume in service.get("volumes", []))
