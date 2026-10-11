"""Role router. Profiles are resolved once, never from environment during a turn."""
import asyncio
from types import MappingProxyType

import httpx

from app.models.openai_visual import VisualModel
from app.models.openrouter_keys import OpenRouterKeys
from app.models.openrouter_visual import OpenRouterVisualModel
from app.models.profiles import resolve_profiles


class RoleModelRouter:
    def __init__(self, profiles, models):
        self.profiles = profiles
        self.models = MappingProxyType(dict(models))

    async def decide(self, role, schema, context, image):
        profile = self.profiles.for_role(role)
        async with asyncio.timeout(profile.timeout_seconds):
            return await self.models[profile.role].decide(role, schema, context, image)

    def metadata_for(self, role):
        profile = self.profiles.for_role(role)
        return {"model": profile.model, "prompt_version": profile.prompt_version,
                "graph_version": profile.graph_version, "provider": "openrouter",
                **self.models[profile.role].last_metadata}

    async def validate_capabilities(self, *, client=None):
        """Public catalog, no keys or task data. Fail closed before readiness."""
        owned = client is None
        client = client or httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10)
        try:
            response = await client.get("https://openrouter.ai/api/v1/models")
            if response.status_code != 200:
                raise ValueError("Model catalog unavailable")
            catalog = {m["id"]: m for m in response.json()["data"]}
            for profile in self.profiles.profiles:
                item = catalog.get(profile.model)
                if not item or "image" not in item.get("architecture", {}).get("input_modalities", ()):
                    raise ValueError("Configured visual model lacks catalog image support")
                if not {"structured_outputs", "response_format"} <= set(item.get("supported_parameters", ())):
                    raise ValueError("Configured visual model lacks structured output support")
        finally:
            if owned:
                await client.aclose()


def build_visual_model(settings) -> VisualModel:
    if settings.model_provider != "openrouter":
        raise ValueError("Only OpenRouter is supported")
    if not settings.openrouter_api_key.get_secret_value().strip():
        raise ValueError("Missing OpenRouter model credential")
    profiles = resolve_profiles(settings)
    keys = OpenRouterKeys(settings.openrouter_api_key, getattr(settings, "openrouter_api_key_backup", None))
    models = {p.role: OpenRouterVisualModel(api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url, profile=p, keys=keys) for p in profiles.profiles}
    router = RoleModelRouter(profiles, models)
    router.keys = keys
    return router
