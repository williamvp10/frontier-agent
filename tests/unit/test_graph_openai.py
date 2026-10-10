import json

import httpx
import pytest
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.models.openai_visual import OpenAIVisualModel
from app.models.schemas import OperatorDecision, Plan


async def test_real_langchain_adapter_sends_transient_image_and_strict_output_to_responses():
    requests = []
    answer = {"intent": "query", "milestones": ["Find count"],
              "success_criteria": ["Visible count"], "preservation_rules": ["No writes"]}

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={
            "id": "resp_synthetic", "object": "response", "created_at": 1,
            "status": "completed", "model": "gpt-6.1-sol",
            "output": [{"id": "msg_synthetic", "type": "message", "status": "completed",
                        "role": "assistant", "content": [{"type": "output_text",
                        "text": json.dumps(answer), "annotations": []}]}],
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = ChatOpenAI(api_key="synthetic-test-key", model="gpt-6.1-sol",
                            use_responses_api=True, store=False, max_retries=0,
                            http_async_client=http, reasoning={"effort": "low"},
                            max_tokens=4096)
        adapter = OpenAIVisualModel(api_key=SecretStr("synthetic-test-key"), client=client)
        result = await adapter.decide("orchestrator", Plan, {"instruction": "Count"}, b"PNG")
    assert result.intent == "query"
    payload = requests[0]
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["max_output_tokens"] == 4096
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert "data:image/png;base64," in json.dumps(payload["input"])
    assert "tools" not in payload


def test_operator_schema_has_no_unsupported_discriminator_or_oneof():
    schema = json.dumps(OperatorDecision.model_json_schema())
    assert '"discriminator"' not in schema
    assert '"oneOf"' not in schema


def test_direct_manufacturer_constructor_is_disabled():
    with pytest.raises(ValueError, match="Direct manufacturer transport is disabled"):
        OpenAIVisualModel(api_key=SecretStr("synthetic-test-key"))
