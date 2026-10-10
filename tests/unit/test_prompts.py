import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models import prompts


def test_current_prompt_roles_do_not_advertise_pending_tools():
    for role in ("orchestrator", "operator", "verifier", "recovery"):
        prompt = prompts.live_prompt(role)
        assert "Runtime capabilities: typed output schema only" in prompt
        assert "not available in this graph version" in prompt
        assert "Keep literal labels/IDs and user-supplied values unchanged" in prompt
        assert "Decision prose is English" in prompt
        assert "respuesta:" in prompt


def test_english_jev_templates_separate_advice_and_evidence():
    assert "Do not include secrets or screenshots" in prompts.JEV_QUESTION_BUILDER
    assert "it neither authorizes an action nor establishes task completion" in prompts.JEV_CRITERIA
    assert "make your own final decision" in prompts.COORDINATOR


def test_settings_reject_direct_openai_provider():
    with pytest.raises(ValidationError):
        Settings(model_provider="openai")
