import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.models.factory import RoleModelRouter, build_visual_model
from app.models.openrouter_visual import OpenRouterVisualModel
from app.models.schemas import Plan

ANSWER = {"intent": "query", "milestones": ["Count"], "success_criteria": ["Visible value"],
          "preservation_rules": ["No writes"]}


async def test_openrouter_real_sdk_uses_chat_images_strict_schema_and_required_provider_params():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "chat_synthetic", "object": "chat.completion",
            "created": 1, "model": "openai/gpt-6.1-sol", "choices": [{"index": 0,
            "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(ANSWER)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = ChatOpenAI(api_key="synthetic-router-key", model="openai/gpt-6.1-sol",
            base_url="https://openrouter.ai/api/v1", use_responses_api=False, max_retries=0,
            max_tokens=4096, http_async_client=http,
            extra_body={"reasoning": {"effort": "low", "exclude": True},
                        "provider": {"require_parameters": True, "allow_fallbacks": False}})
        model = OpenRouterVisualModel(api_key=SecretStr("synthetic-router-key"), client=client)
        output = await model.decide("orchestrator", Plan, {"instruction": "Count"}, b"PNG")
    assert output.intent == "query"
    request = requests[0]
    assert request.url.path == "/api/v1/chat/completions"
    payload = json.loads(request.content)
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["provider"] == {"require_parameters": True, "allow_fallbacks": False}
    assert payload["reasoning"] == {"effort": "low", "exclude": True}
    assert "data:image/png;base64," in json.dumps(payload["messages"])
    assert "tools" not in payload
    assert "synthetic-router-key" not in json.dumps(payload)


def settings(**updates):
    data = dict(model_provider="openrouter", openrouter_api_key=SecretStr("synthetic-router-key"),
        model_name="openai/gpt-6.1-sol", openrouter_base_url="https://openrouter.ai/api/v1",
        model_timeout_seconds=20, model_reasoning_effort="low")
    return SimpleNamespace(**{**data, **updates})


def test_factory_uses_only_openrouter_and_requires_its_key():
    model = build_visual_model(settings())
    assert isinstance(model, RoleModelRouter)
    assert model.models["operator"].client.openai_api_base == "https://openrouter.ai/api/v1"
    assert model.models["operator"].client.max_retries == 0
    with pytest.raises(ValueError, match="Missing OpenRouter"):
        build_visual_model(settings(openrouter_api_key=SecretStr(""),
            openai_api_key=SecretStr("synthetic-ignored-backup")))
    with pytest.raises(ValueError, match="Only OpenRouter"):
        build_visual_model(settings(model_provider="openai"))
