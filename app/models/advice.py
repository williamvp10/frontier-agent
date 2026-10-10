"""Private, task-local advisory contracts. No action or transport authority here."""
from math import isclose
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Text = Annotated[str, Field(min_length=1, max_length=2048)]
Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
Role = Literal["orchestrator", "coordinator", "operator", "verifier", "recovery"]


class AdviceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True,
                              revalidate_instances="always", allow_inf_nan=False)

    @model_validator(mode="wrap")
    @classmethod
    def safe_validation(cls, value, handler):
        try:
            return handler(value)
        except (ValidationError, ValueError, TypeError, AttributeError, KeyError):
            pass
        raise ValidationError.from_exception_data("AdviceContract", [{
            "type": "value_error", "loc": (), "input": "[redacted]",
            "ctx": {"error": ValueError("Invalid advice contract")},
        }], hide_input=True) from None

    def __repr_args__(self):
        return ()


class AdviceScope(AdviceContract):
    run_id: UUID
    session_id: UUID
    epoch: int = Field(ge=1)
    context_version: int = Field(ge=0)
    ui_generation: int = Field(ge=0)
    observation_id: UUID


class AdviceIdentifier(AdviceContract):
    name: Text
    value: Text


class AdviceTask(AdviceContract):
    original_instruction: Annotated[str, Field(min_length=1, max_length=16384)]
    normalized_goal: Text
    identifiers: tuple[AdviceIdentifier, ...] = Field(max_length=20)
    restrictions: tuple[Text, ...] = Field(max_length=20)
    fields_to_preserve: tuple[Text, ...] = Field(max_length=20)
    allowed_actions: tuple[Text, ...] = Field(max_length=5)
    allowed_changes: tuple[Text, ...] = Field(max_length=20)
    preservation_rules: tuple[Text, ...] = Field(max_length=20)
    success_criteria: tuple[Text, ...] = Field(min_length=1, max_length=20)
    response_requirements: Text


class AdviceProgress(AdviceContract):
    active_milestone: Text
    completed_criteria: tuple[Text, ...] = Field(max_length=20)
    remaining_criteria: tuple[Text, ...] = Field(max_length=20)


class AdviceSituation(AdviceContract):
    current_view: Text
    visible_controls: tuple[Text, ...] = Field(max_length=60)
    relevant_routes: tuple[Text, ...] = Field(max_length=20)
    view_capabilities: tuple[Text, ...] = Field(default=(), max_length=40)
    unexplored_alternatives: tuple[Text, ...] = Field(default=(), max_length=40)
    ui_details: tuple[Text, ...] = Field(default=(), max_length=20)


class AdviceFact(AdviceContract):
    statement: Text
    status: Literal["observed", "independently_verified", "contradicted", "stale"] = "observed"
    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=10)


class AdviceAttempt(AdviceContract):
    action: Text
    send_status: Literal["rejected", "not_sent", "sent", "confirmed", "uncertain"]
    expected_effect: Text
    observed_effect: Text
    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=10)
    reconciliation_outcome: Literal["applied", "not_applied"] | None = None
    reconciliation_evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=20)


class AdviceUncertainty(AdviceContract):
    hypotheses: tuple[Text, ...] = Field(max_length=20)
    contradictions: tuple[Text, ...] = Field(max_length=20)
    missing_information: tuple[Text, ...] = Field(max_length=20)
    pending_operation: Text | None


class AdviceBudget(AdviceContract):
    remaining_seconds: float = Field(gt=0, le=300, allow_inf_nan=False)
    remaining_actions: int = Field(ge=0, le=1000)


class AdviceCoverage(AdviceContract):
    # Every section is required, including empty history/contradictions/pending=null.
    included_topics: tuple[Identifier, ...] = Field(max_length=20)
    omitted_topics: tuple[Text, ...] = Field(max_length=20)
    sufficiency_status: Literal["sufficient", "missing_context"]


class AdviceOption(AdviceContract):
    option_id: Identifier
    description: Text
    preconditions: tuple[Text, ...] = Field(max_length=10)
    known_risk: Text


class AdviceQuestion(AdviceContract):
    question_id: Identifier
    type: Literal["choice"] = "choice"
    instructions: Text
    options: tuple[AdviceOption, ...] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def validate_options(self):
        ids = [option.option_id for option in self.options]
        if len(set(ids)) != len(ids) or "insufficient" not in ids:
            raise ValueError("Unique options including insufficient are required")
        return self


class AdviceRequest(AdviceContract):
    request_id: UUID = Field(default_factory=uuid4)
    requester_role: Role
    scope: AdviceScope
    task: AdviceTask
    progress: AdviceProgress
    situation: AdviceSituation
    observed_facts: tuple[AdviceFact, ...] = Field(max_length=40)
    verified_facts: tuple[AdviceFact, ...] = Field(max_length=40)
    history: tuple[AdviceAttempt, ...] = Field(max_length=20)
    uncertainty: AdviceUncertainty
    budgets: AdviceBudget
    questions: tuple[AdviceQuestion, ...] = Field(min_length=1, max_length=4)
    context_coverage: AdviceCoverage

    @model_validator(mode="after")
    def unique_questions(self):
        ids = [question.question_id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("Question IDs must be unique")
        return self


class ChoiceAnswer(AdviceContract):
    question_id: Identifier
    choice: Identifier
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    # Tuple storage prevents mutable maps inside frozen checkpoint contracts.
    probabilities: tuple[tuple[Identifier, Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]], ...]

    @model_validator(mode="after")
    def distribution(self):
        values = dict(self.probabilities)
        if len(values) != len(self.probabilities) or self.choice not in values:
            raise ValueError("Invalid choice distribution")
        if not isclose(sum(values.values()), 1, abs_tol=0.001):
            raise ValueError("Probabilities must sum to one")
        if values[self.choice] + 0.001 < max(values.values()):
            raise ValueError("Choice must be a highest-probability option")
        return self


class AdviceResult(AdviceContract):
    request_id: UUID
    requester_role: Role
    scope: AdviceScope
    model_version: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    answers: tuple[ChoiceAnswer, ...] = ()
    latency_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    status: Literal["ok", "timeout", "unavailable", "invalid", "stale"]
    reason_code: Identifier
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    provider_request_id: Annotated[str, Field(max_length=128, pattern=r"^[A-Za-z0-9_-]+$")] | None = None
    provider: Annotated[str, Field(max_length=80, pattern=r"^[A-Za-z0-9_. -]+$")] | None = None


class FinalDecision(AdviceContract):
    request_id: UUID
    requester_role: Role
    scope: AdviceScope
    question_id: Identifier
    selected_option: Identifier
    accepted_advice: bool
    brief_reason: Text
    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)
    unmet_preconditions: tuple[Text, ...] = Field(max_length=10)
    next_step: Literal["propose_action", "reobserve", "verify", "recover", "abstain"]
