"""OpenRouter Chat Completions adapter; no hosted agents or computer tools."""
import asyncio
import logging

from langchain_openai import ChatOpenAI
from openai import APIStatusError
from pydantic import SecretStr

from app.models.openai_visual import OpenAIVisualModel
from app.models.openrouter_keys import OpenRouterKeys, is_credit_exhausted
from app.models.profiles import ModelProfile

logger = logging.getLogger(__name__)


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
                  profile: ModelProfile | None = None,
                  keys: OpenRouterKeys | None = None, backup_client: ChatOpenAI | None = None):
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
        self.keys = keys or OpenRouterKeys(api_key)
        self._timeout = timeout
        router = client or ChatOpenAI(
            api_key=api_key, model=model, base_url=base_url, use_responses_api=False,
            timeout=timeout, max_retries=0, max_tokens=output_tokens, extra_body=extra,
        )
        super().__init__(api_key=api_key, client=router)
        self._clients = [router]
        if self.keys.has_backup:
            self._clients.append(backup_client or ChatOpenAI(
                api_key=self.keys.key(1), model=model, base_url=base_url, use_responses_api=False,
                timeout=timeout, max_retries=0, max_tokens=output_tokens, extra_body=extra,
            ))

    async def decide(self, role, schema, context, image):
        # Primary rejection plus backup share one call deadline, not two full timeouts.
        async with asyncio.timeout(self._timeout):
            for attempt in range(2):
                slot = self.keys.active
                self.client = self._clients[slot]
                self._credential = self.keys.key(slot)
                try:
                    result = await super().decide(role, schema, context, image)
                    self.last_metadata["credential_slot"] = "backup" if slot else "primary"
                    return result
                except APIStatusError as exc:
                    status = exc.status_code
                    if is_credit_exhausted(status, exc.body):
                        category, features = "credits_exhausted", ()
                        if attempt == 0 and self.keys.failover(slot):
                            logger.warning("OpenRouter primary credit limit exhausted; switching to backup credential")
                            continue
                    else:
                        body = exc.body.get("error", exc.body) if isinstance(exc.body, dict) else {}
                        message = str(body.get("message", "")) if isinstance(body, dict) else ""
                        if isinstance(body, dict) and isinstance(body.get("metadata"), dict):
                            message += str(body["metadata"].get("raw", ""))
                        category = "schema_rejected" if "schema" in message.lower() else "provider_rejected"
                        features = tuple(name for name in ("format", "minItems", "maxItems", "minLength", "maxLength",
                            "additionalProperties", "default", "anyOf", "$ref", "pattern", "minimum", "maximum") if name in message)
                raise ProviderCallError(status, category, features) from None
