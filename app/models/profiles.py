"""Immutable, credential-free OpenRouter profiles pinned for one run."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.prompts import PROMPT_VERSION

ROLES = ("coordinator", "operator", "verifier", "recovery", "analyst")


class ProfileContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True,
                              allow_inf_nan=False)


class RoleOverride(ProfileContract):
    timeout_seconds: float = Field(default=20, gt=0, le=60)
    max_output_tokens: int = Field(default=4096, ge=256, le=16384)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None


class ProfileOverrides(ProfileContract):
    coordinator: RoleOverride = Field(default_factory=RoleOverride)
    operator: RoleOverride = Field(default_factory=RoleOverride)
    verifier: RoleOverride = Field(default_factory=RoleOverride)
    recovery: RoleOverride = Field(default_factory=RoleOverride)
    analyst: RoleOverride = Field(default_factory=RoleOverride)


class ModelProfile(RoleOverride):
    role: Literal["coordinator", "operator", "verifier", "recovery", "analyst"]
    model: str = Field(min_length=3, max_length=128)
    provider: Literal["openrouter"] = "openrouter"
    provider_sort: Literal["latency", "price", "throughput"] = "latency"
    require_parameters: Literal[True] = True
    allow_fallbacks: Literal[False] = False
    prompt_version: str = PROMPT_VERSION
    graph_version: Literal["v1", "context-v2"] = "context-v2"

    @model_validator(mode="after")
    def reproducible(self):
        if not re.fullmatch(r"[a-z0-9-]+/[a-z0-9_.-]+", self.model) or (
            "latest" in self.model or self.model.startswith(("typesafe/", "openrouter/"))
        ):
            raise ValueError("A pinned visual model ID is required")
        # No assumed cross-family reasoning dialect. Validate any new family explicitly.
        if self.reasoning_effort is not None and not self.model.startswith("openai/"):
            raise ValueError("Reasoning effort requires a validated OpenAI-family profile")
        return self

    def extra_body(self):
        body = {"provider": {"sort": self.provider_sort,
                             "require_parameters": True, "allow_fallbacks": False}}
        if self.reasoning_effort is not None:
            body["reasoning"] = {"effort": self.reasoning_effort, "exclude": True}
        return body


class ResolvedProfiles(ProfileContract):
    profiles: tuple[ModelProfile, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def complete(self):
        if {p.role for p in self.profiles} != set(ROLES):
            raise ValueError("Exactly one profile per role is required")
        return self

    def for_role(self, role: str) -> ModelProfile:
        role = "coordinator" if role == "orchestrator" else role
        return next(p for p in self.profiles if p.role == role)


def resolve_profiles(settings) -> ResolvedProfiles:
    result = []
    overrides = getattr(settings, "model_profiles", None) or ProfileOverrides()
    for role in ROLES:
        model = getattr(settings, "model_" + role, settings.model_name)
        override = getattr(overrides, role)
        explicit = override.model_fields_set
        reasoning = override.reasoning_effort
        if "reasoning_effort" not in explicit and model.startswith("openai/"):
            reasoning = settings.model_reasoning_effort
        timeout = override.timeout_seconds if "timeout_seconds" in explicit else settings.model_timeout_seconds
        result.append(ModelProfile(role=role, model=model, timeout_seconds=timeout,
            max_output_tokens=override.max_output_tokens, reasoning_effort=reasoning,
            provider_sort=getattr(settings, "openrouter_provider_sort", "latency"),
            require_parameters=getattr(settings, "openrouter_require_parameters", True),
            allow_fallbacks=getattr(settings, "openrouter_allow_fallbacks", False),
            graph_version=getattr(settings, "graph_version", "context-v2")))
    return ResolvedProfiles(profiles=tuple(result))
