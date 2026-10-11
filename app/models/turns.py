"""V2 specialist outputs: no transport/budget/ownership authority in model patches."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.agents.context import (
    BoundingBox,
    ContextModel,
    Fact,
    FactUpdate,
    FrontierCandidate,
    Hypothesis,
    Milestone,
    MilestoneAssignment,
    Progress,
    TaskContract,
    TransitionEdge,
    ViewNode,
)
from app.models.advice import AdviceQuestion
from app.models.schemas import ModelAction, VisualVerification


class MemoryPatch(ContextModel):
    run_id: UUID
    session_id: UUID
    epoch: int = Field(gt=0)
    base_context_version: int = Field(ge=0)
    observation_id: UUID
    frame_revision: int = Field(ge=0)
    ui_generation: int = Field(ge=0)
    facts: tuple[Fact, ...] = Field(default=(), max_length=20)
    fact_updates: tuple[FactUpdate, ...] = Field(default=(), max_length=20)
    hypotheses: tuple[Hypothesis, ...] = Field(default=(), max_length=20)
    views: tuple[ViewNode, ...] = Field(default=(), max_length=20)
    transitions: tuple[TransitionEdge, ...] = Field(default=(), max_length=20)
    frontier: tuple[FrontierCandidate, ...] = Field(default=(), max_length=20)
    unresolved_questions: tuple[str, ...] | None = Field(default=None, max_length=20)
    progress: Progress | None = None


class VerifierPatch(ContextModel):
    """Role-specific proof updates; runtime creates facts from report assertions."""

    run_id: UUID
    session_id: UUID
    epoch: int = Field(gt=0)
    base_context_version: int = Field(ge=0)
    observation_id: UUID
    frame_revision: int = Field(ge=0)
    ui_generation: int = Field(ge=0)
    fact_updates: tuple[FactUpdate, ...] = Field(default=(), max_length=20)


class UIControlProposal(ContextModel):
    """Semantic/localization proposal; runtime owns run/frame/generation metadata."""

    id: UUID
    label: str = Field(min_length=1, max_length=256)
    kind: str = Field(min_length=1, max_length=256)
    state: Literal["enabled", "disabled", "unknown"] = "unknown"
    bounding_box: BoundingBox | None = None
    view_id: UUID


class UIInterpretation(ContextModel):
    reset_suspected: bool = False
    view_id: UUID | None = None
    title: str | None = Field(default=None, max_length=256)
    breadcrumbs: tuple[str, ...] = Field(default=(), max_length=20)
    expanded_menu: str | None = Field(default=None, max_length=256)
    active_tab: str | None = Field(default=None, max_length=256)
    modal: str | None = Field(default=None, max_length=256)
    loading: bool = False
    visible_error: str | None = Field(default=None, max_length=2048)
    focused_control_id: UUID | None = None
    visible_controls: tuple[UIControlProposal, ...] = Field(default=(), max_length=80)


class CoordinatorTurn(ContextModel):
    task_contract: TaskContract
    milestone_plan: tuple[Milestone, ...] = Field(min_length=1, max_length=12)
    active_assignment: MilestoneAssignment
    questions: tuple[AdviceQuestion, ...] = Field(default=(), max_length=4)


class ReplanTurn(ContextModel):
    milestone_plan: tuple[Milestone, ...] = Field(min_length=1, max_length=12)
    active_assignment: MilestoneAssignment | None
    questions: tuple[AdviceQuestion, ...] = Field(default=(), max_length=4)


class OperatorTurn(ContextModel):
    patch: MemoryPatch | None = None
    ui: UIInterpretation | None = None
    disposition: Literal["action", "consult_jev", "verify", "recover", "milestone_done", "abstain"]
    args: ModelAction | None = None
    expected_effect: str = Field(default="", max_length=2048)
    scope_reason: str = Field(default="", max_length=2048)
    risk: Literal["navigation", "edit", "commit", "export"] = "navigation"
    questions: tuple[AdviceQuestion, ...] = Field(default=(), max_length=4)


class CriterionCheck(ContextModel):
    criterion_id: UUID
    verified_facts: tuple[str, ...] = Field(min_length=1, max_length=20)


class VerifierTurn(ContextModel):
    report: VisualVerification
    ambiguity_kind: Literal["identity", "ui", "evidence"] | None = None
    patch: VerifierPatch | None = None
    satisfied_criterion_ids: tuple[UUID, ...] = Field(default=(), max_length=12)
    operation_resolution: Literal["applied", "not_applied"] | None = None
    observed_effect: str | None = Field(default=None, max_length=2048)
    effect_status: Literal["verified", "incomplete", "unverifiable"] | None = None
    effect_facts: tuple[str, ...] = Field(default=(), max_length=20)
    verified_observation_facts: tuple[str, ...] = Field(default=(), max_length=20)
    criterion_checks: tuple[CriterionCheck, ...] = Field(default=(), max_length=12)
    questions: tuple[AdviceQuestion, ...] = Field(default=(), max_length=4)


class RecoveryTurn(ContextModel):
    patch: MemoryPatch | None = None
    strategy: Literal["reobserve", "verify", "replan", "consult_jev", "abstain"]
    brief_reason: str = Field(min_length=1, max_length=2048)
    questions: tuple[AdviceQuestion, ...] = Field(default=(), max_length=4)
