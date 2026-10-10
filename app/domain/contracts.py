from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DesktopCredentials(Contract):
    url: str = Field(repr=False, min_length=1, max_length=4096)
    password: SecretStr = Field(repr=False)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError("Invalid desktop URL") from None
        if (
            parsed.scheme != "wss"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or port == 0
        ):
            raise ValueError("Desktop must be an absolute WSS URL without credentials")
        return value

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value().encode()) <= 4096:
            raise ValueError("Invalid desktop password length")
        return value


class TaskRequest(Contract):
    tarea: str = Field(repr=False, min_length=1)
    escritorio: DesktopCredentials = Field(repr=False)

    @field_validator("tarea")
    @classmethod
    def instruction_limit(cls, value: str) -> str:
        if not value.strip() or len(value.encode()) > 16384:
            raise ValueError("Invalid task length")
        return value


class TaskAccepted(Contract):
    task_id: UUID


class PublicState(StrEnum):
    RUNNING = "en_curso"
    SUCCEEDED = "terminada"
    FAILED = "fallida"


class TaskResponse(Contract):
    estado: PublicState
    resumen: str


class Observation(Contract):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    session_id: UUID
    epoch: int = Field(gt=0)
    frame_revision: int = Field(ge=0)
    captured_at: datetime
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    artifact_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ClickArgs(Contract):
    kind: Literal["click", "double_click"] = "click"
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    hold_ms: int = Field(default=180, ge=100, le=1000)


class KeyArgs(Contract):
    kind: Literal["press_key"] = "press_key"
    keys: tuple[str, ...] = Field(min_length=1, max_length=4)


class TextArgs(Contract):
    kind: Literal["replace_text"] = "replace_text"
    text: str = Field(repr=False, max_length=4096)


class ScrollArgs(Contract):
    kind: Literal["scroll"] = "scroll"
    direction: Literal["up", "down"]
    amount: int = Field(default=3, ge=1, le=10)


ActionArgs = Annotated[ClickArgs | KeyArgs | TextArgs | ScrollArgs, Field(discriminator="kind")]


class ActionProposal(Contract):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    session_id: UUID
    epoch: int = Field(gt=0)
    observation_id: UUID
    args: ActionArgs
    expected_effect: str = Field(min_length=1, max_length=2048)
    scope_reason: str = Field(min_length=1, max_length=2048)
    risk: Literal["navigation", "edit", "commit", "export"]
    expires_at: datetime

    @property
    def kind(self) -> str:
        return self.args.kind


class Verification(Contract):
    status: Literal["verified", "incomplete", "ambiguous", "unverifiable"]
    evidence_ids: tuple[UUID, ...] = ()
    facts: tuple[str, ...] = ()
    unresolved_identifiers: tuple[str, ...] = ()


class RecoveryDecision(Contract):
    strategy: Literal["reobserve", "replan", "abstain"]
    reason: str


class TaskResult(Contract):
    estado: Literal["terminada", "fallida"]
    resumen: str
    evidence_ids: tuple[UUID, ...] = ()
    reason_code: str


class TaskTiming(Contract):
    accepted_at: datetime
    action_cutoff_at: datetime
    close_target_at: datetime
    deadline_at: datetime

    @model_validator(mode="after")
    def ordered(self):
        dates = (self.accepted_at, self.action_cutoff_at, self.close_target_at, self.deadline_at)
        if any(d.tzinfo is None for d in dates) or not all(a < b for a, b in zip(dates, dates[1:])):
            raise ValueError("Timing must be UTC-aware and strictly ordered")
        return self
