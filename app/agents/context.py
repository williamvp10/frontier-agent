"""Private, task-local memory contracts; no images or transport credentials.

Normalized goals/claims are English; original instructions and UI literals are
preserved. All collections are tuples, including nested values. JSON dumps are
PRIVATE run data, not logging/event payloads. Evidence is registered by runtime
from the existing Observation contract, never by a model or an advisor.
"""

import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.domain.contracts import Observation

Role = Literal["coordinator", "operator", "verifier", "recovery", "analyst"]
ActorRole = Literal["coordinator", "operator", "verifier", "recovery", "runtime"]
ActionKind = Literal["click", "double_click", "press_key", "replace_text", "scroll"]
Text = Annotated[str, Field(min_length=1, max_length=2048)]
Label = Annotated[str, Field(min_length=1, max_length=256)]
Texts = Annotated[tuple[Text, ...], Field(max_length=20)]
Labels = Annotated[tuple[Label, ...], Field(max_length=20)]
IDs = Annotated[tuple[UUID, ...], Field(max_length=40)]
EvidenceIDs = Annotated[tuple[UUID, ...], Field(min_length=1, max_length=20)]
FactStatus = Literal["observed", "independently_verified", "contradicted", "stale"]

# Deliberately conservative: context is not a credential/URL/blob carrier.
_FORBIDDEN = re.compile(
    r"(?:\b(?:https?|wss?|postgres(?:ql)?|redis)://|data:image|;base64,|"
    r"\bBearer\s+\S+|\b(?:password|passwd|api[_ -]?key|access[_ -]?token|secret)\s*[:=]|"
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]{20,})|"
    r"<think>|chain.of.thought|iVBORw0KGgo|/9j/)", re.IGNORECASE,
)


def _check_text(value: object) -> None:
    if isinstance(value, str):
        if _FORBIDDEN.search(value) or re.search(r"[A-Za-z0-9+/]{256,}={0,2}", value):
            raise ValueError("Unsafe context content")
    elif isinstance(value, dict):
        for item in value.values():
            _check_text(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _check_text(item)


def _invalid() -> ValidationError:
    # No input, field location, arbitrary exception text or private value escapes.
    return ValidationError.from_exception_data("ContextContract", [{
        "type": "value_error", "loc": (), "input": "[redacted]",
        "ctx": {"error": ValueError("Invalid context contract")},
    }], hide_input=True)


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True,
                              revalidate_instances="always", allow_inf_nan=False)

    @model_validator(mode="wrap")
    @classmethod
    def safe_validation(cls, value, handler):
        try:
            result = handler(value)
            _check_text(result.model_dump(mode="python", warnings="none"))
            return result
        except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
            pass
        # Raised outside the handler: do not retain a raw ValidationError in __context__.
        raise _invalid() from None

    def __repr_args__(self):
        # Business values may be present in this PRIVATE model; repr is not a dump.
        return ()


class RunRecord(ContextModel):
    run_id: UUID
    id: UUID


class EvidenceReference(RunRecord):
    """Metadata/reference only, not a duplicate Observation or an image location."""

    session_id: UUID
    epoch: int = Field(gt=0)
    frame_revision: int = Field(ge=0)
    ui_generation: int = Field(ge=0)
    captured_at: AwareDatetime
    artifact_id: UUID

    @classmethod
    def from_observation(cls, observation: Observation, *, ui_generation: int):
        return cls(id=observation.id, run_id=observation.run_id,
                   session_id=observation.session_id, epoch=observation.epoch,
                   frame_revision=observation.frame_revision, captured_at=observation.captured_at,
                   artifact_id=observation.artifact_id, ui_generation=ui_generation)


class Identifier(ContextModel):
    name: Label
    value: Text


class SuccessCriterion(ContextModel):
    id: UUID
    description: Text


class TaskContract(ContextModel):
    original_instruction: Annotated[str, Field(min_length=1, max_length=16384)]
    normalized_goal: Text
    intent: Literal["query", "create", "edit", "export"]
    identifiers: tuple[Identifier, ...] = Field(default=(), max_length=20)
    allowed_changes: Labels = ()
    fields_to_preserve: Labels = ()
    preservation_rules: Texts = ()
    success_criteria: tuple[SuccessCriterion, ...] = Field(min_length=1, max_length=12)
    response_requirements: Texts
    restrictions: Texts
    allowed_actions: tuple[ActionKind, ...] = Field(max_length=5)

    @model_validator(mode="after")
    def scope_consistent(self):
        if (self.intent == "query" and self.allowed_changes) or (
            set(self.allowed_changes) & set(self.fields_to_preserve)
        ) or len({c.id for c in self.success_criteria}) != len(self.success_criteria):
            raise ValueError("Invalid scope")
        return self


class BoundingBox(ContextModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class VisibleControl(RunRecord):
    label: Label
    kind: Label
    state: Literal["enabled", "disabled", "unknown"] = "unknown"
    bounding_box: BoundingBox | None = None
    observation_id: UUID
    ui_generation: int = Field(ge=0)
    view_id: UUID


class CurrentUI(ContextModel):
    observation_id: UUID
    captured_at: AwareDatetime
    frame_revision: int = Field(ge=0)
    ui_generation: int = Field(ge=0)
    view_id: UUID | None = None
    title: Label | None = None
    breadcrumbs: Labels = ()
    expanded_menu: Label | None = None
    active_tab: Label | None = None
    modal: Label | None = None
    loading: bool = False
    visible_error: Text | None = None
    focused_control_id: UUID | None = None
    visible_controls: tuple[VisibleControl, ...] = Field(default=(), max_length=80)


class Provenance(ContextModel):
    author: ActorRole
    context_version: int = Field(ge=0)
    status: FactStatus
    evidence_ids: EvidenceIDs


class Fact(RunRecord):
    assertion: Text
    scope: Literal["task", "view", "record"]
    status: FactStatus = "observed"
    evidence_ids: EvidenceIDs
    author: ActorRole
    ui_generation: int = Field(ge=0)
    valid_until: AwareDatetime | None = None
    view_id: UUID | None = None
    record_identifiers: tuple[Identifier, ...] = Field(default=(), max_length=20)
    contradicts: IDs = ()
    provenance: tuple[Provenance, ...] = Field(default=(), max_length=40)


class FactUpdate(ContextModel):
    fact_id: UUID
    status: FactStatus
    evidence_ids: EvidenceIDs
    contradicts: IDs = ()


class Hypothesis(RunRecord):
    assertion: Text
    supporting_evidence_ids: IDs = ()
    opposing_evidence_ids: IDs = ()
    next_check: Text
    author: Role


class ViewNode(RunRecord):
    labels: Labels
    kind: Literal["page", "tab", "menu", "modal"]
    capabilities: Texts = ()
    evidence_ids: EvidenceIDs
    discovery_state: Literal["observed", "visited", "partially_inspected", "blocked"]
    inspected_regions: Texts
    last_validated_generation: int = Field(ge=0)


class TransitionEdge(RunRecord):
    source_view_id: UUID
    semantic_control: Label
    required_conditions: Texts = ()
    expected_view_id: UUID | None = None
    observed_view_id: UUID | None = None
    before_evidence_ids: EvidenceIDs
    after_evidence_ids: IDs = ()
    outcome: Literal["observed", "confirmed", "blocked", "unexpected", "stale"]
    reversible: bool | None = None
    last_validated_generation: int = Field(ge=0)


class FrontierCandidate(RunRecord):
    view_id: UUID
    observed_option: Label
    goal_relation: Text
    requirements: Texts = ()
    estimated_actions: int = Field(ge=0, le=60)
    evidence_ids: EvidenceIDs
    status: Literal["pending", "discarded_for_task", "inspected"] = "pending"


class PartialAppMap(ContextModel):
    views: tuple[ViewNode, ...] = Field(default=(), max_length=40)
    transitions: tuple[TransitionEdge, ...] = Field(default=(), max_length=80)
    frontier: tuple[FrontierCandidate, ...] = Field(default=(), max_length=40)


class Milestone(RunRecord):
    semantic_goal: Text
    dependencies: IDs = ()
    preconditions: Texts = ()
    acceptance_criterion_ids: EvidenceIDs
    status: Literal["pending", "active", "completed", "blocked"] = "pending"


class AssignmentBudget(ContextModel):
    seconds: float = Field(ge=0, le=300)
    actions: int = Field(ge=0, le=60)
    model_calls: int = Field(ge=0, le=100)


class MilestoneAssignment(RunRecord):
    milestone_id: UUID
    required_inputs: Texts
    identity_fact_ids: IDs = ()
    evidence_ids: IDs = ()
    sought_capabilities: Texts
    allowed_actions: tuple[ActionKind, ...] = Field(max_length=5)
    allowed_changes: Labels = ()
    stop_conditions: Annotated[tuple[Text, ...], Field(min_length=1, max_length=20)]
    budget: AssignmentBudget


class PendingResolution(ContextModel):
    action_id: UUID
    outcome: Literal["applied", "not_applied"]
    author: Literal["verifier"] = "verifier"
    evidence_ids: EvidenceIDs
    verified_fact_ids: EvidenceIDs


class ActionRecord(RunRecord):
    # ID references the existing ActionProposal/action attempt; no args duplicated.
    actor: ActorRole
    milestone_id: UUID | None = None
    source_observation_id: UUID
    semantic_goal: Text
    expected_effect: Text
    outcome: Literal["rejected", "not_sent", "sent", "confirmed", "uncertain"]
    subsequent_observation_id: UUID | None = None
    observed_effect: Text | None = None
    effect_evidence_ids: IDs = ()
    effect_fact_ids: IDs = ()
    reconciliation: PendingResolution | None = None

    @model_validator(mode="after")
    def confirmed_effect(self):
        if self.outcome == "confirmed" and not (
            self.subsequent_observation_id and self.observed_effect and self.effect_evidence_ids
            and self.effect_fact_ids
        ):
            raise ValueError("Confirmation requires post-action evidence")
        if self.reconciliation and (self.reconciliation.action_id != self.id or (
            self.reconciliation.outcome == "not_applied" and self.outcome == "confirmed"
        )):
            raise ValueError("Invalid reconciliation")
        return self


class PendingOperation(RunRecord):
    action_id: UUID
    kind: Literal["mutation", "export"]
    expected_effect: Text
    evidence_ids: EvidenceIDs
    reconciliation_requirement: Text
    status: Literal["awaiting_observation", "uncertain"] = "uncertain"


class CriterionProgress(ContextModel):
    criterion_id: UUID
    fact_ids: EvidenceIDs
    evidence_ids: EvidenceIDs


class Progress(ContextModel):
    satisfied_criteria: tuple[CriterionProgress, ...] = Field(default=(), max_length=12)
    remaining_criterion_ids: IDs = ()
    useful_transition_ids: IDs = ()
    no_progress_count: int = Field(default=0, ge=0, le=1000)


class RoleCalls(ContextModel):
    coordinator: int = Field(default=0, ge=0, le=100)
    operator: int = Field(default=0, ge=0, le=100)
    verifier: int = Field(default=0, ge=0, le=100)
    recovery: int = Field(default=0, ge=0, le=100)
    analyst: int = Field(default=0, ge=0, le=100)


class Budgets(ContextModel):
    deadline_at: AwareDatetime
    remaining_seconds: float = Field(ge=0, le=300)
    remaining_calls: RoleCalls
    remaining_actions: int = Field(ge=0, le=60)
    remaining_recoveries: int = Field(ge=0, le=10)
    remaining_advice_calls: int = Field(ge=0, le=10)


class AdviceReference(RunRecord):
    """Reserved reference only. Never usable as evidence or authorization."""

    source_context_version: int = Field(ge=0)
    observation_id: UUID
    status: Literal["ok", "timeout", "unavailable", "invalid", "stale"]


class SharedTaskContext(ContextModel):
    run_id: UUID
    session_id: UUID
    epoch: int = Field(gt=0)
    context_version: int = Field(default=0, ge=0)
    ui_generation: int = Field(default=0, ge=0)
    task_contract: TaskContract
    milestone_plan: tuple[Milestone, ...] = Field(default=(), max_length=12)
    active_assignment: MilestoneAssignment | None = None
    current_ui: CurrentUI
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=200)
    app_map: PartialAppMap = Field(default_factory=PartialAppMap)
    facts: tuple[Fact, ...] = Field(default=(), max_length=100)
    hypotheses: tuple[Hypothesis, ...] = Field(default=(), max_length=40)
    unresolved_questions: Texts = ()
    action_history: tuple[ActionRecord, ...] = Field(default=(), max_length=100)
    pending_operation: PendingOperation | None = None
    progress: Progress = Field(default_factory=Progress)
    budgets: Budgets
    advice_records: tuple[AdviceReference, ...] = Field(default=(), max_length=20)

    @model_validator(mode="before")
    @classmethod
    def initial_progress(cls, value):
        if isinstance(value, dict) and "progress" not in value and "task_contract" in value:
            contract = value["task_contract"]
            if not isinstance(contract, (TaskContract, dict)):
                return value
            criteria = (contract.success_criteria if isinstance(contract, TaskContract)
                        else contract.get("success_criteria", ()))
            if not isinstance(criteria, (tuple, list)):
                return value
            ids = tuple(c.id if isinstance(c, SuccessCriterion)
                        else c.get("id") if isinstance(c, dict) else None for c in criteria)
            return {**value, "progress": {"remaining_criterion_ids": ids}}
        return value

    @model_validator(mode="after")
    def local_references(self):
        records = (*self.evidence, *self.facts, *self.hypotheses, *self.milestone_plan,
                   *self.app_map.views, *self.app_map.transitions, *self.app_map.frontier,
                   *self.action_history, *self.advice_records, *self.current_ui.visible_controls)
        records += tuple(x for x in (self.active_assignment, self.pending_operation) if x)
        if any(r.run_id != self.run_id for r in records):
            raise ValueError("Foreign run")
        for group in (self.evidence, self.facts, self.hypotheses, self.milestone_plan,
                      self.app_map.views, self.app_map.transitions, self.app_map.frontier,
                      self.action_history, self.advice_records, self.current_ui.visible_controls):
            if len({r.id for r in group}) != len(group):
                raise ValueError("Duplicate IDs")
        evidence = {r.id: r for r in self.evidence}
        if any((r.session_id, r.epoch) != (self.session_id, self.epoch)
               or r.ui_generation > self.ui_generation for r in self.evidence):
            raise ValueError("Foreign evidence")
        ui = self.current_ui
        origin = evidence.get(ui.observation_id)
        if not origin or (ui.frame_revision, ui.ui_generation, ui.captured_at) != (
            origin.frame_revision, self.ui_generation, origin.captured_at
        ) or origin.ui_generation != self.ui_generation:
            raise ValueError("Invalid current frame")

        def refs(ids, known):
            if not set(ids) <= set(known):
                raise ValueError("Unknown reference")

        views = {v.id for v in self.app_map.views}
        facts = {f.id for f in self.facts}
        milestones = {m.id for m in self.milestone_plan}
        criteria = {c.id for c in self.task_contract.success_criteria}
        for f in self.facts:
            refs(f.evidence_ids, evidence)
            refs(f.contradicts, facts - {f.id})
            refs((f.view_id,) if f.view_id else (), views)
            if f.ui_generation > self.ui_generation:
                raise ValueError("Future fact")
            if f.valid_until and f.valid_until <= ui.captured_at and f.status in {
                "observed", "independently_verified",
            }:
                raise ValueError("Expired fact")
            for p in f.provenance:
                refs(p.evidence_ids, evidence)
                if p.context_version > self.context_version:
                    raise ValueError("Future provenance")
                if p.status == "independently_verified" and p.author != "verifier":
                    raise ValueError("Unauthorized verification provenance")
            if f.provenance and f.provenance[-1].status != f.status:
                raise ValueError("Inconsistent provenance")
            if f.status == "independently_verified" and (
                not f.provenance or f.provenance[-1].author != "verifier"
                or f.provenance[-1].status != f.status
            ):
                raise ValueError("Unverified fact")
        for h in self.hypotheses:
            refs((*h.supporting_evidence_ids, *h.opposing_evidence_ids), evidence)
        for v in self.app_map.views:
            refs(v.evidence_ids, evidence)
            if v.last_validated_generation > self.ui_generation:
                raise ValueError("Future view")
        for edge in self.app_map.transitions:
            refs((edge.source_view_id,), views)
            refs(tuple(x for x in (edge.expected_view_id, edge.observed_view_id) if x), views)
            refs((*edge.before_evidence_ids, *edge.after_evidence_ids), evidence)
            if edge.outcome == "confirmed" and not edge.after_evidence_ids:
                raise ValueError("Missing transition evidence")
            if edge.last_validated_generation > self.ui_generation:
                raise ValueError("Future edge")
        for option in self.app_map.frontier:
            refs((option.view_id,), views)
            refs(option.evidence_ids, evidence)
        for control in ui.visible_controls:
            refs((control.view_id,), views)
            if (control.observation_id, control.ui_generation) != (
                ui.observation_id, self.ui_generation
            ):
                raise ValueError("Old localization")
        refs((ui.view_id,) if ui.view_id else (), views)
        refs((ui.focused_control_id,) if ui.focused_control_id else (),
             {c.id for c in ui.visible_controls})
        for m in self.milestone_plan:
            refs(m.dependencies, milestones - {m.id})
            refs(m.acceptance_criterion_ids, criteria)
        if sum(m.status == "active" for m in self.milestone_plan) > 1:
            raise ValueError("Multiple active milestones")
        # Kahn-style traversal rejects indirect dependency cycles as well.
        todo = {m.id: set(m.dependencies) for m in self.milestone_plan}
        while todo:
            ready = {key for key, deps in todo.items() if not deps}
            if not ready:
                raise ValueError("Cyclic milestones")
            todo = {key: deps - ready for key, deps in todo.items() if key not in ready}
        assignment = self.active_assignment
        if assignment:
            refs((assignment.milestone_id,), milestones)
            refs(assignment.identity_fact_ids, facts)
            refs(assignment.evidence_ids, evidence)
            if not any(m.id == assignment.milestone_id and m.status in {"active", "blocked"}
                       for m in self.milestone_plan):
                raise ValueError("Inactive assignment")
            if not set(assignment.allowed_actions) <= set(self.task_contract.allowed_actions) or (
                not set(assignment.allowed_changes) <= set(self.task_contract.allowed_changes)
            ):
                raise ValueError("Expanded assignment")
            if assignment.budget.seconds > self.budgets.remaining_seconds or (
                assignment.budget.actions > self.budgets.remaining_actions
            ) or assignment.budget.model_calls > self.budgets.remaining_calls.operator:
                raise ValueError("Expanded budget")
        for action in self.action_history:
            refs((action.source_observation_id,), evidence)
            refs((action.milestone_id,) if action.milestone_id else (), milestones)
            refs((action.subsequent_observation_id,) if action.subsequent_observation_id else (),
                 evidence)
            refs(action.effect_evidence_ids, evidence)
            refs(action.effect_fact_ids, facts)
            if action.reconciliation:
                refs(action.reconciliation.evidence_ids, evidence)
                refs(action.reconciliation.verified_fact_ids, facts)
            if action.outcome == "confirmed":
                before = evidence[action.source_observation_id]
                after = evidence[action.subsequent_observation_id]
                if after.frame_revision <= before.frame_revision:
                    raise ValueError("Missing later observation")
        if self.pending_operation:
            refs((self.pending_operation.action_id,), {a.id for a in self.action_history})
            refs(self.pending_operation.evidence_ids, evidence)
        satisfied = set()
        for c in self.progress.satisfied_criteria:
            refs((c.criterion_id,), criteria)
            refs(c.fact_ids, facts)
            refs(c.evidence_ids, evidence)
            supporting_facts = [f for f in self.facts if f.id in c.fact_ids]
            if any(f.status != "independently_verified" for f in supporting_facts):
                raise ValueError("Unverified criterion")
            supported = {eid for f in supporting_facts for eid in f.provenance[-1].evidence_ids}
            if not set(c.evidence_ids) <= supported:
                raise ValueError("Unsupported criterion")
            if c.criterion_id in satisfied:
                raise ValueError("Duplicate criterion")
            satisfied.add(c.criterion_id)
        refs(self.progress.remaining_criterion_ids, criteria - satisfied)
        if satisfied | set(self.progress.remaining_criterion_ids) != criteria:
            raise ValueError("Missing task criteria")
        refs(self.progress.useful_transition_ids, {e.id for e in self.app_map.transitions})
        for milestone in self.milestone_plan:
            if milestone.status == "completed" and (
                not set(milestone.acceptance_criterion_ids) <= satisfied or self.pending_operation
            ):
                raise ValueError("Unsupported completion")
            completed = {m.id for m in self.milestone_plan if m.status == "completed"}
            if milestone.status in {"active", "completed"} and not set(milestone.dependencies) <= completed:
                raise ValueError("Unmet milestone dependencies")
        for advice in self.advice_records:
            refs((advice.observation_id,), evidence)
            if advice.id in evidence:
                raise ValueError("Advice is not evidence")
        return self


class ContextPatch(ContextModel):
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
    unresolved_questions: Texts | None = None
    milestone_plan: tuple[Milestone, ...] | None = Field(default=None, max_length=12)
    active_assignment: MilestoneAssignment | None = None
    progress: Progress | None = None
    # Runtime-only authority fields. Presence (including explicit null) is checked.
    current_ui: CurrentUI | None = None
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=20)
    next_ui_generation: int | None = Field(default=None, ge=0)
    invalidated_view_ids: IDs = ()
    budgets: Budgets | None = None
    action_records: tuple[ActionRecord, ...] = Field(default=(), max_length=20)
    pending_operation: PendingOperation | None = None
    clear_pending_operation: bool = False
    reconciliation_evidence_ids: IDs = ()
    pending_resolution: PendingResolution | None = None


class ContextSlice(ContextModel):
    role: Role
    run_id: UUID
    session_id: UUID
    epoch: int = Field(gt=0)
    context_version: int = Field(ge=0)
    ui_generation: int = Field(ge=0)
    task_contract: TaskContract
    milestone_plan: tuple[Milestone, ...] = Field(max_length=12)
    active_assignment: MilestoneAssignment | None
    current_ui: CurrentUI
    app_map: PartialAppMap
    facts: tuple[Fact, ...] = Field(max_length=100)
    hypotheses: tuple[Hypothesis, ...] = Field(max_length=40)
    unresolved_questions: Texts
    action_history: tuple[ActionRecord, ...] = Field(max_length=100)
    pending_operation: PendingOperation | None
    progress: Progress
    budgets: Budgets
    evidence: tuple[EvidenceReference, ...] = Field(max_length=200)
    omitted_action_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    coverage: Literal["sufficient"] = "sufficient"

    @model_validator(mode="after")
    def validate_retained_context(self):
        data = self.model_dump(mode="python")
        for key in ("role", "omitted_action_ids", "coverage"):
            data.pop(key)
        SharedTaskContext.model_validate(data)
        return self
