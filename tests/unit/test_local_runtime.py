"""Local setup/TLS tests only: no Docker, task submission, reset or real VNC."""

import ssl
import stat
from dataclasses import replace

import pytest
from dotenv import dotenv_values

from scripts import evaluate, local_runtime
from tests.unit.test_harness import CONFIG

pytestmark = pytest.mark.unit


@pytest.fixture
def local(tmp_path, monkeypatch):
    directory = tmp_path / ".private/local-docker"
    monkeypatch.setattr(local_runtime, "LOCAL", directory)
    monkeypatch.setattr(local_runtime, "ENV", directory / "runtime.env")
    model_env = tmp_path / "model.env"
    model_env.write_text("OPENROUTER_API_KEY=synthetic-router-key\nTEAM_ENTRYPOINT_TOKEN=synthetic-team-token\n")
    # Parent environment must not override the synthetic fixture.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("TEAM_ENTRYPOINT_TOKEN", raising=False)
    return directory, model_env


def test_local_prepare_is_private_idempotent_and_never_prints_keys(local, capsys):
    directory, model_env = local
    local_runtime.prepare(model_env, 18443)
    original = local_runtime.ENV.read_bytes()
    assert stat.S_IMODE(local_runtime.ENV.stat().st_mode) == 0o600
    assert stat.S_IMODE((directory / "tls/server.key").stat().st_mode) == 0o600
    local_runtime.prepare(model_env, 18444)
    assert local_runtime.ENV.read_bytes() == original
    output = capsys.readouterr().out
    assert "synthetic-router-key" not in output and "synthetic-team-token" not in output


def test_evaluator_custom_ca_extends_trust_without_disabling_verification(local):
    _, model_env = local
    local_runtime.prepare(model_env, 18443)
    ca = local_runtime.LOCAL / "tls/ca.pem"
    options = evaluate.client_options(replace(CONFIG, ca_file=ca))
    assert isinstance(options["verify"], ssl.SSLContext)
    assert options["verify"].check_hostname
    assert options["verify"].verify_mode == ssl.CERT_REQUIRED
    assert options["trust_env"] is False and options["follow_redirects"] is False


def test_invalid_ca_is_sanitized(tmp_path):
    ca = tmp_path / "invalid.pem"
    ca.write_text("CANARY_PRIVATE_CERTIFICATE")
    with pytest.raises(evaluate.HarnessError, match="invalid_config") as caught:
        evaluate.client_options(replace(CONFIG, ca_file=ca))
    assert "CANARY_PRIVATE_CERTIFICATE" not in str(caught.value)
    assert str(ca) not in str(caught.value)


def test_evaluator_config_only_prepares_file_without_calling_desktop(local, tmp_path, capsys):
    _, model_env = local
    local_runtime.prepare(model_env, 18443)
    vnc = tmp_path / "vnc.env"
    vnc.write_text("VNC_BASE_URL=https://desktop.example\nVNC_WS_URL=wss://desktop.example/websockify\n"
                   "VNC_PASSWORD=CANARY_PASSWORD\nVNC_DESKTOP_TOKEN=CANARY_TOKEN\n")
    local_runtime.evaluator_config(vnc)
    target = local_runtime.LOCAL / "evaluator.env"
    config = dotenv_values(target, interpolate=False)
    assert config["TEAM_ENTRYPOINT_URL"] == "https://127.0.0.1:18443"
    assert config["VNC_PASSWORD"] == "CANARY_PASSWORD"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "CANARY_PASSWORD" not in capsys.readouterr().out
