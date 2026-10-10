"""OpenAI image interpretation only; all computer actions remain our own tools."""
import base64
import json
import math
import re
import time
from typing import Protocol, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from pydantic import BaseModel, SecretStr

from app.core.observability import observed, record_usage
from app.models.prompts import live_prompt

T = TypeVar("T", bound=BaseModel)


class VisualModel(Protocol):
    async def decide(self, role: str, schema: type[T], context: dict,
                     image: bytes | tuple[bytes, ...]) -> T: ...




class OpenAIVisualModel:
    def __init__(self, *, api_key: SecretStr, model: str = "gpt-6.1-sol",
                 timeout: float = 20, reasoning_effort: str = "low",
                 client: ChatOpenAI | None = None):
        if client is None:
            raise ValueError("Direct manufacturer transport is disabled; use OpenRouter factory")
        self.client = client
        self._credential = api_key
        self.last_metadata = {}

    @observed("generate-decision", as_type="generation", role_arg=1, counter="model_calls")
    async def decide(self, role: str, schema: type[T], context: dict,
                     image: bytes | tuple[bytes, ...]) -> T:
        # This payload is transient: do not return it, checkpoint it or enable model tracing.
        images = image if isinstance(image, tuple) else (image,)
        blocks = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
        for data in images:
            encoded = base64.b64encode(data).decode("ascii")
            blocks.append({"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + encoded, "detail": "high"}})
        messages = [
            SystemMessage(content=live_prompt(role, graph_version=context.get("graph_version", "v1"))),
            HumanMessage(content=blocks),
        ]
        structured = self.client.with_structured_output(schema, method="json_schema", strict=True, include_raw=True)
        started = time.monotonic()
        self.last_metadata = {}
        record_usage({}, model=getattr(self.client, "model_name", None))
        with tracing_context(enabled=False):
            result = await structured.ainvoke(messages, config={"callbacks": []})
        if isinstance(result, dict) and "parsed" in result:
            if result.get("parsing_error") or result["parsed"] is None:
                raise ValueError("Invalid structured model output")
            raw = result["raw"]
            usage = getattr(raw, "usage_metadata", None) or {}
            meta = {"duration_seconds": time.monotonic() - started}
            for name in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(name)
                if isinstance(value, int) and value >= 0:
                    meta[name] = value
            response_meta = getattr(raw, "response_metadata", {})
            cost = response_meta.get("token_usage", {}).get("cost")
            if isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0:
                meta["cost_usd"] = cost
            model = response_meta.get("model_name")
            if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model) and (
                not self._credential.get_secret_value() or self._credential.get_secret_value() not in model
            ):
                meta["resolved_model"] = model
            self.last_metadata = meta
            record_usage(meta, model=meta.get("resolved_model", getattr(self.client, "model_name", None)))
            result = result["parsed"]
        return result if isinstance(result, schema) else schema.model_validate(result)
