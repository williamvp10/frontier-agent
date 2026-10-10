from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.profiles import ProfileOverrides


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)
    team_id: str = "frontier"
    team_entrypoint_token: SecretStr = Field(default=SecretStr(""), repr=False)
    database_url: SecretStr = Field(default=SecretStr(""), repr=False)
    langgraph_database_uri: SecretStr = Field(default=SecretStr(""), repr=False)
    redis_url: SecretStr = Field(default=SecretStr(""), repr=False)
    credentials_encryption_key: SecretStr = Field(default=SecretStr(""), repr=False)
    destination_hmac_key: SecretStr = Field(default=SecretStr(""), repr=False)
    model_provider: Literal["openrouter"] = "openrouter"
    openrouter_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    model_name: str = "openai/gpt-6.1-sol"
    model_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] = "low"
    model_timeout_seconds: float = Field(default=20, gt=0, le=60)
    model_coordinator: str = "openai/gpt-6-luna"
    model_operator: str = "openai/gpt-6-luna"
    model_verifier: str = "anthropic/claude-sonnet-5.5"
    model_recovery: str = "openai/gpt-6.1-sol"
    model_analyst: str = "openai/gpt-6-luna"
    model_jev: Literal["typesafe/jev-1.13"] = "typesafe/jev-1.13"
    model_profiles: ProfileOverrides | None = Field(default_factory=ProfileOverrides)
    openrouter_provider_sort: Literal["latency", "price", "throughput"] = "latency"
    openrouter_require_parameters: Literal[True] = True
    openrouter_allow_fallbacks: Literal[False] = False
    openrouter_jev_endpoint: Literal["/api/alpha/decisions"] = "/api/alpha/decisions"
    graph_version: Literal["v1", "context-v2"] = "context-v2"
    jev_enabled: bool = False
    jev_timeout_seconds: float = Field(default=3, gt=0, le=20)
    jev_max_calls: int = Field(default=4, ge=0, le=16)
    artifact_dir: Path = Path(".private/artifacts")
    allow_private_desktops: bool = False
    heartbeat_max_age_seconds: float = 10.0
    admission_timeout_seconds: float = 1.5
    task_seconds: float = 300.0
    action_cutoff_seconds: float = 270.0
    close_target_seconds: float = 285.0
    drain_seconds: float = 268.0
    max_actions: int = 60
    max_model_calls: int = 40
    max_recoveries: int = 2
    langsmith_tracing: bool = False
    langfuse_enabled: bool = True
    langfuse_public_key: SecretStr = Field(default=SecretStr(""), repr=False)
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""), repr=False)
    langfuse_base_url: str = "https://us.cloud.langfuse.com"
    langfuse_tracing_environment: Literal["development", "test", "staging", "production"] = "development"

    @field_validator("openrouter_require_parameters", "openrouter_allow_fallbacks", mode="before")
    @classmethod
    def routing_bool(cls, value):
        if isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
            return value.lower() in {"true", "1"}
        return value

    @field_validator("openrouter_base_url")
    @classmethod
    def router_origin(cls, value):
        if value.rstrip("/") != "https://openrouter.ai/api/v1":
            raise ValueError("Only the public OpenRouter API origin is supported")
        return value.rstrip("/")
