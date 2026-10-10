import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.models.factory import RoleModelRouter, build_visual_model
from app.models.profiles import ModelProfile, resolve_profiles
from app.models.schemas import Plan

pytestmark = pytest.mark.unit


def test_settings_loads_role_overrides_only_from_injected_environment(monkeypatch):
    monkeypatch.setenv("MODEL_OPERATOR", "openai/gpt-6.1-sol")
    monkeypatch.setenv("MODEL_PROFILES", json.dumps({"operator": {
        "timeout_seconds": 5, "max_output_tokens": 2048, "reasoning_effort": "minimal"}}))
    settings = Settings()
    profiles = resolve_profiles(settings)
    operator = profiles.for_role("operator")
    assert operator.model == "openai/gpt-6.1-sol" and operator.timeout_seconds == 5
    assert operator.max_output_tokens == 2048 and operator.reasoning_effort == "minimal"
    assert profiles.for_role("verifier").reasoning_effort is None
    assert profiles.for_role("orchestrator").role == "coordinator"
    settings.model_operator = "openai/gpt-6-luna"
    assert profiles.for_role("operator") == operator
    assert "api_key" not in profiles.model_dump_json()
    with pytest.raises(ValidationError):
        operator.model = "other/invalid"


@pytest.mark.parametrize("model", ["typesafe/jev-1.13", "openai/gpt:free", "~openai/latest",
                                    "openrouter/auto", "https://private.invalid"])
def test_unpinned_or_known_textual_model_is_rejected(model):
    with pytest.raises(ValidationError):
        ModelProfile(role="operator", model=model)


def test_family_specific_parameters_and_no_direct_endpoint():
    sonnet = ModelProfile(role="verifier", model="anthropic/claude-sonnet-5.5")
    assert "reasoning" not in sonnet.extra_body()
    with pytest.raises(ValidationError):
        ModelProfile(role="verifier", model=sonnet.model, reasoning_effort="low")
    with pytest.raises(ValidationError):
        Settings(openrouter_base_url="https://api.openai.com/v1")
    with pytest.raises(ValidationError):
        Settings(openrouter_allow_fallbacks=True)


def test_policy_booleans_from_environment_are_parsed_then_validated(monkeypatch):
    monkeypatch.setenv("OPENROUTER_REQUIRE_PARAMETERS", "true")
    monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", "false")
    assert Settings().openrouter_require_parameters is True
    assert Settings().openrouter_allow_fallbacks is False
    monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", "true")
    with pytest.raises(ValidationError):
        Settings()


async def test_independent_roles_route_schemas_and_enforce_profile_timeout():
    profiles = resolve_profiles(Settings(model_profiles={"operator": {"timeout_seconds": 0.01}}))
    calls = []

    class Model:
        async def decide(self, role, schema, context, image):
            calls.append((role, schema, image))
            return schema(intent="query", milestones=("Read",), success_criteria=("Read value",),
                          preservation_rules=())

    class Slow:
        async def decide(self, *args):
            await asyncio.sleep(1)

    models = {p.role: Model() for p in profiles.profiles}
    models["operator"] = Slow()
    router = RoleModelRouter(profiles, models)
    assert (await router.decide("verifier", Plan, {}, b"synthetic")).intent == "query"
    assert calls == [("verifier", Plan, b"synthetic")]
    with pytest.raises(TimeoutError):
        await router.decide("operator", Plan, {}, b"synthetic")


@pytest.mark.parametrize("failure", ["missing", "text_only", "schema"])
async def test_catalog_capability_gate_fails_closed_before_visual_call(failure):
    router = build_visual_model(Settings(openrouter_api_key=SecretStr("synthetic-key")))
    catalog = [{"id": p.model, "architecture": {"input_modalities": ["text", "image"]},
                "supported_parameters": ["structured_outputs", "response_format"]}
               for p in router.profiles.profiles]
    for item in catalog:
        if failure == "missing":
            item["id"] = "unknown/model"
        elif failure == "text_only":
            item["architecture"]["input_modalities"] = ["text"]
        else:
            item["supported_parameters"] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": catalog}))) as client:
        with pytest.raises(ValueError):
            await router.validate_capabilities(client=client)
