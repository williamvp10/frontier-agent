"""Bounded model outputs. No credentials, transport handles or images belong here."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.contracts import ActionArgs, ClickArgs, KeyArgs, ScrollArgs, TextArgs, TextTarget


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelOutputRejected(ValueError):
    """Static adapter diagnostic; never retains raw provider output or parse errors."""


class Plan(ModelOutput):
    intent: Literal["query", "create", "edit", "export"]
    milestones: tuple[str, ...] = Field(min_length=1, max_length=12)
    success_criteria: tuple[str, ...] = Field(min_length=1, max_length=12)
    preservation_rules: tuple[str, ...] = Field(max_length=12)


class ModelAction(ModelOutput):
    # Flat, required nullable fields avoid unsupported discriminator/oneOf JSON Schema.
    kind: Literal["click", "double_click", "press_key", "replace_text", "scroll"]
    x: int | None
    y: int | None
    hold_ms: int | None
    keys: tuple[str, ...] | None
    text: str | None
    text_targets: tuple[TextTarget, ...] | None = None
    direction: Literal["up", "down"] | None
    amount: int | None

    def to_action(self) -> ActionArgs:
        models = {"click": ClickArgs, "double_click": ClickArgs, "press_key": KeyArgs,
                  "replace_text": TextArgs, "scroll": ScrollArgs}
        fields = {"click": {"kind", "x", "y", "hold_ms"},
                  "double_click": {"kind", "x", "y", "hold_ms"},
                  "press_key": {"kind", "keys"}, "replace_text": {"kind", "text", "x", "y", "text_targets"},
                  "scroll": {"kind", "direction", "amount"}}
        # The flat provider schema has nullable slots for every action kind.
        # Only the selected kind's fields reach the strict transport contract.
        return models[self.kind].model_validate(self.model_dump(
            include=fields[self.kind], exclude_none=True))


class OperatorDecision(ModelOutput):
    disposition: Literal["action", "verify", "blocked", "ambiguous"]
    args: ModelAction | None
    expected_effect: str = Field(max_length=2048)
    scope_reason: str = Field(max_length=2048)
    risk: Literal["navigation", "edit", "commit", "export"]
    identifiers: tuple[str, ...] = Field(max_length=20)


class VisualVerification(ModelOutput):
    status: Literal["verified", "incomplete", "ambiguous", "unverifiable"]
    safe_to_execute: bool
    changes_business_data: bool
    facts: tuple[str, ...] = Field(max_length=20)
    unresolved_identifiers: tuple[str, ...] = Field(max_length=20)
    answer: str | None = Field(max_length=4096)


class Recovery(ModelOutput):
    strategy: Literal["reobserve", "replan", "abstain"]
    reason: str = Field(max_length=2048)
