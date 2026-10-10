"""OpenRouter Chat Completions adapter; no hosted agents or computer tools."""
from langchain_openai import ChatOpenAI
from openai import APIStatusError
from pydantic import SecretStr

from app.models.openai_visual import OpenAIVisualModel
from app.models.profiles import ModelProfile


class ProviderCallError(RuntimeError):
    def __init__(self, status_code, category="provider_rejected", schema_features=()):
        self.status_code = int(status_code)
        self.category = category
        self.schema_features = schema_features
        super().__init__("OpenRouter request rejected")


class OpenRouterVisualModel(OpenAIVisualModel):
    def __init__(self, *, api_key: SecretStr, model: str = "openai/gpt-6.1-sol",
                 base_url: str = "https://openrouter.ai/api/v1", timeout: float = 20,
                  reasoning_effort: str | None = "low", client: ChatOpenAI | None = None,
                  profile: ModelProfile | None = None):
        if base_url.rstrip("/") != "https://openrouter.ai/api/v1":
            raise ValueError("Only OpenRouter transport is supported")
        if profile:
            model, timeout = profile.model, profile.timeout_seconds
            extra = profile.extra_body()
            output_tokens = profile.max_output_tokens
        else:
            extra = {"provider": {"sort": "latency", "require_parameters": True,
                                  "allow_fallbacks": False}}
            if reasoning_effort is not None and model.startswith("openai/"):
                extra["reasoning"] = {"effort": reasoning_effort, "exclude": True}
            output_tokens = 4096
        router = client or ChatOpenAI(
            api_key=api_key, model=model, base_url=base_url, use_responses_api=False,
            timeout=timeout, max_retries=0, max_tokens=output_tokens, extra_body=extra,
        )
        super().__init__(api_key=api_key, client=router)

    async def decide(self, role, schema, context, image):
        try:
            return await super().decide(role, schema, context, image)
        except APIStatusError as exc:
            status = exc.status_code
            body = exc.body.get("error", exc.body) if isinstance(exc.body, dict) else {}
            message = str(body.get("message", "")) if isinstance(body, dict) else ""
            if isinstance(body, dict) and isinstance(body.get("metadata"), dict):
                message += str(body["metadata"].get("raw", ""))
            category = "schema_rejected" if "schema" in message.lower() else "provider_rejected"
            features = tuple(name for name in ("format", "minItems", "maxItems", "minLength", "maxLength",
                "additionalProperties", "default", "anyOf", "$ref", "pattern", "minimum", "maximum") if name in message)
        raise ProviderCallError(status, category, features) from None
