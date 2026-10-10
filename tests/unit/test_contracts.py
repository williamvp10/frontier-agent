from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.sanitization import sanitize
from app.core.timing import ActionDeadline, timing_for
from app.domain.contracts import TaskRequest, utcnow
from app.domain.states import public_state, transition

pytestmark = pytest.mark.unit


def test_secret_fields_hidden_in_repr_and_limits():
    request = TaskRequest(
        tarea="Consulta",
        escritorio={"url": "wss://desktop.example/vnc", "password": "canary-private"},
    )
    assert "canary-private" not in repr(request)
    assert "desktop.example" not in repr(request)
    with pytest.raises(ValidationError):
        TaskRequest(tarea="a" * 16385, escritorio=request.escritorio)
    with pytest.raises(ValidationError):
        TaskRequest(tarea=" ", escritorio=request.escritorio)
    settings = Settings(openrouter_api_key="canary-provider", database_url="canary-db")
    assert "canary-provider" not in repr(settings) and "canary-db" not in repr(settings)


@pytest.mark.parametrize(
    "url", ["http://example.com/vnc", "wss://user:pass@example.com/vnc", "wss://example.com/#frag"]
)
def test_invalid_desktop_urls(url):
    with pytest.raises(ValidationError):
        TaskRequest(tarea="Consulta", escritorio={"url": url, "password": "x"})


def test_budget_consumes_queue_and_backward_wall_jump_cannot_extend():
    accepted = utcnow()
    timing = timing_for(accepted)
    wall = [accepted + timedelta(seconds=265)]
    monotonic = [100.0]
    budget = ActionDeadline(
        timing.action_cutoff_at, now=lambda: wall[0], monotonic=lambda: monotonic[0]
    )
    assert budget.remaining() == 5
    wall[0] = accepted
    monotonic[0] += 5
    with pytest.raises(TimeoutError):
        budget.require()


def test_terminal_state_cannot_reopen():
    assert public_state("closing") == "en_curso"
    assert transition("closing", "succeeded") == "succeeded"
    with pytest.raises(ValueError):
        transition("succeeded", "running")


def test_sanitize_secrets_and_images_in_all_shapes():
    output = sanitize(
        {
            "password": "x",
            "nested": [{"fact": "canary-secret wss://desktop.example/vnc"}],
            "extra": "data:image/png;base64,ABC",
        },
        ("canary-secret",),
    )
    assert "password" not in output
    assert "canary-secret" not in str(output)
    assert "desktop.example" not in str(output)
    assert "ABC" not in str(output)
