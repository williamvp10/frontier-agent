"""MC01–MC03 and critical handoff invariants, with task-local synthetic evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.agents.context import (
    ActionRecord,
    AdviceReference,
    AssignmentBudget,
    BoundingBox,
    CriterionProgress,
    CurrentUI,
    EvidenceReference,
    FactUpdate,
    Milestone,
    MilestoneAssignment,
    PartialAppMap,
    PendingOperation,
    PendingResolution,
    Progress,
    SharedTaskContext,
    TransitionEdge,
    ViewNode,
    VisibleControl,
)
from app.agents.context_service import ContextService, PatchRejected, ProjectionError
from tests.unit.test_context import make_fact, make_patch, make_snapshot

pytestmark = pytest.mark.unit


def next_frame(snapshot, *, generation=None, invalidated=(), revision_delta=0):
    old = snapshot.evidence[-1]
    generation = snapshot.ui_generation if generation is None else generation
    ref = EvidenceReference(id=uuid4(), run_id=snapshot.run_id, session_id=snapshot.session_id,
                            epoch=snapshot.epoch, frame_revision=old.frame_revision + revision_delta,
                            captured_at=old.captured_at + timedelta(seconds=1),
                            artifact_id=uuid4(), ui_generation=generation)
    ui = CurrentUI(observation_id=ref.id, frame_revision=ref.frame_revision,
                   captured_at=ref.captured_at, ui_generation=generation)
    changes = dict(current_ui=ui, evidence=(ref,))
    if generation != snapshot.ui_generation:
        changes.update(next_ui_generation=generation, invalidated_view_ids=invalidated)
    return ContextService().apply(snapshot, make_patch(snapshot, **changes), actor_role="runtime")


def with_pending(snapshot):
    action = ActionRecord(id=uuid4(), run_id=snapshot.run_id, actor="runtime",
                          source_observation_id=snapshot.current_ui.observation_id,
                          semantic_goal="Export selected records", expected_effect="Export completes",
                          outcome="uncertain")
    pending = PendingOperation(id=uuid4(), run_id=snapshot.run_id, action_id=action.id,
                               kind="export", expected_effect=action.expected_effect,
                               evidence_ids=(snapshot.current_ui.observation_id,),
                               reconciliation_requirement="Verify whether the export completed")
    result = ContextService().apply(snapshot, make_patch(snapshot, action_records=(action,),
                                                        pending_operation=pending),
                                    actor_role="runtime")
    return result, action


def verified_fact(snapshot, **overrides):
    fact = make_fact(snapshot, author="verifier", status="independently_verified", **overrides)
    result = ContextService().apply(snapshot, make_patch(snapshot, facts=(fact,)),
                                    actor_role="verifier")
    return result, result.facts[-1]


@pytest.mark.parametrize(("field", "value", "code"), [
    ("run_id", uuid4(), "stale_run"), ("session_id", uuid4(), "stale_session"),
    ("epoch", 2, "stale_epoch"), ("base_context_version", 10, "stale_version"),
    ("observation_id", uuid4(), "stale_observation"), ("frame_revision", 10, "stale_frame"),
    ("ui_generation", 10, "stale_generation"),
])
def test_mc01_stale_patches_rejected_atomically(field, value, code):
    source = make_snapshot()
    before = source.model_dump_json()
    with pytest.raises(PatchRejected) as caught:
        ContextService().apply(source, make_patch(source, **{field: value}), actor_role="operator")
    assert caught.value.code == code
    assert source.model_dump_json() == before


def test_pure_reducer_and_version_fencing():
    source = make_snapshot()
    patch = make_patch(source, facts=(make_fact(source),))
    result = ContextService().apply(source, patch, actor_role="operator")
    assert result is not source and source.facts == ()
    assert result.context_version == source.context_version + 1
    assert result.facts[0].provenance[0].author == "operator"
    with pytest.raises(PatchRejected, match="stale_version"):
        ContextService().apply(result, patch, actor_role="operator")


@pytest.mark.parametrize("role", ["operator", "coordinator", "recovery", "runtime"])
def test_mc02_only_verifier_can_elevate(role):
    source = make_snapshot()
    fact = make_fact(source)
    source = ContextService().apply(source, make_patch(source, facts=(fact,)), actor_role="operator")
    update = FactUpdate(fact_id=fact.id, status="independently_verified",
                        evidence_ids=fact.evidence_ids)
    with pytest.raises(PatchRejected, match="permission_denied"):
        ContextService().apply(source, make_patch(source, fact_updates=(update,)), actor_role=role)
    verified = ContextService().apply(source, make_patch(source, fact_updates=(update,)),
                                      actor_role="verifier")
    assert verified.facts[0].status == "independently_verified"
    assert [p.author for p in verified.facts[0].provenance] == ["operator", "verifier"]


def test_mc02_contradictions_preserve_assertions_evidence_and_authors():
    source, old = verified_fact(make_snapshot(), assertion="Selected record matches requested identity")
    source = next_frame(source)
    new = make_fact(source, assertion="Selected record has a different identifier", contradicts=(old.id,))
    result = ContextService().apply(source, make_patch(source, facts=(new,)), actor_role="operator")
    first, second = result.facts
    assert first.status == "contradicted" and second.status == "observed"
    assert first.assertion == old.assertion and first.author == "verifier"
    assert old.evidence_ids[0] in first.evidence_ids
    assert new.evidence_ids[0] in first.evidence_ids
    assert first.contradicts == (new.id,) and second.contradicts == (old.id,)
    assert first.provenance[-1].author == "operator"
    # An unrelated patch must not change the new assertion into a contradiction.
    again = ContextService().apply(result, make_patch(result), actor_role="operator")
    assert again.facts == result.facts
    with pytest.raises(PatchRejected, match="invalid_provenance"):
        ContextService().apply(result, make_patch(result, fact_updates=(FactUpdate(
            fact_id=old.id, status="independently_verified", evidence_ids=new.evidence_ids),)),
            actor_role="verifier")


def test_forged_provenance_or_foreign_evidence_is_rejected():
    source = make_snapshot()
    for fact in (make_fact(source, author="verifier"),
                 make_fact(source, run_id=uuid4()),
                 make_fact(source, evidence_ids=(uuid4(),))):
        before = source.model_dump_json()
        with pytest.raises(PatchRejected):
            ContextService().apply(source, make_patch(source, facts=(fact,)), actor_role="operator")
        assert source.model_dump_json() == before


def map_snapshot():
    source = make_snapshot()
    view = ViewNode(id=uuid4(), run_id=source.run_id, labels=("Directorio",), kind="page",
                    evidence_ids=(source.current_ui.observation_id,), discovery_state="visited",
                    inspected_regions=("Visible viewport",), last_validated_generation=0)
    source = ContextService().apply(source, make_patch(source, views=(view,)), actor_role="operator")
    ui = CurrentUI.model_validate({**source.current_ui.model_dump(), "view_id": view.id,
                                  "visible_controls": (VisibleControl(
                                      id=uuid4(), run_id=source.run_id, view_id=view.id,
                                      label="Clientes", kind="button", state="unknown",
                                      observation_id=source.current_ui.observation_id, ui_generation=0,
                                      bounding_box=BoundingBox(x=10, y=20, width=30, height=40)),)})
    return SharedTaskContext.model_validate({**source.model_dump(), "current_ui": ui}), view


def test_mc03_new_generation_drops_localizations_and_preserves_semantic_map():
    source, view = map_snapshot()
    source, fact = verified_fact(source, scope="view", view_id=view.id)
    result = next_frame(source, generation=1, invalidated=(view.id,))
    assert source.current_ui.visible_controls[0].bounding_box.x == 10
    assert result.current_ui.visible_controls == ()
    assert result.app_map.views[0].labels == ("Directorio",)
    assert result.facts[0].status == "stale"
    assert result.facts[0].provenance[-1].author == "runtime"
    assert result.facts[0].evidence_ids == fact.evidence_ids
    assert result.run_id == source.run_id
    other = make_snapshot()
    assert other.app_map == PartialAppMap() and other.facts == ()
    with pytest.raises(PatchRejected):
        ContextService().apply(other, make_patch(other, views=(view,)), actor_role="operator")


def test_partial_invalidation_keeps_unaffected_routes_and_marks_affected_stale():
    source, view = map_snapshot()
    other = ViewNode.model_validate({**view.model_dump(), "id": uuid4(), "labels": ("Other",)})
    source = ContextService().apply(source, make_patch(source, views=(other,)), actor_role="operator")
    before = source.current_ui.observation_id
    source = next_frame(source)
    after = source.current_ui.observation_id
    edges = tuple(TransitionEdge(id=uuid4(), run_id=source.run_id, source_view_id=v.id,
                                 semantic_control="Open tab", observed_view_id=v.id,
                                 before_evidence_ids=(before,), after_evidence_ids=(after,),
                                 outcome="confirmed", last_validated_generation=0) for v in (view, other))
    source = ContextService().apply(source, make_patch(source, transitions=edges), actor_role="operator")
    result = next_frame(source, generation=1, invalidated=(view.id,))
    assert [e.outcome for e in result.app_map.transitions] == ["stale", "confirmed"]
    assert result.app_map.transitions[0].semantic_control == "Open tab"
    assert result.app_map.transitions[0].before_evidence_ids == (before,)


@pytest.mark.parametrize("field", ["current_ui", "budgets", "evidence", "next_ui_generation",
                                   "pending_operation", "clear_pending_operation"])
def test_operator_cannot_write_authority_fields_even_explicit_defaults(field):
    source = make_snapshot()
    values = {"current_ui": source.current_ui, "budgets": source.budgets, "evidence": (),
              "next_ui_generation": None, "pending_operation": None,
              "clear_pending_operation": False}
    with pytest.raises(PatchRejected, match="permission_denied"):
        ContextService().apply(source, make_patch(source, **{field: values[field]}), actor_role="operator")


def test_runtime_budget_only_decreases_and_deadline_is_preserved():
    source = make_snapshot()
    for changed in ({"remaining_actions": 31}, {"deadline_at": source.budgets.deadline_at + timedelta(1)},
                    {"remaining_seconds": 251}):
        budgets = type(source.budgets).model_validate({**source.budgets.model_dump(), **changed})
        with pytest.raises(PatchRejected, match="budget_violation"):
            ContextService().apply(source, make_patch(source, budgets=budgets), actor_role="runtime")
    budgets = type(source.budgets).model_validate({**source.budgets.model_dump(), "remaining_actions": 29})
    result = ContextService().apply(source, make_patch(source, budgets=budgets), actor_role="runtime")
    assert result.budgets.remaining_actions == 29
    assert result.task_contract == source.task_contract


def test_assignment_cannot_expand_scope_or_budget():
    source = make_snapshot()
    milestone = Milestone(id=uuid4(), run_id=source.run_id, semantic_goal="Confirm identity",
                          acceptance_criterion_ids=(source.task_contract.success_criteria[0].id,),
                          status="active")
    assignment = MilestoneAssignment(id=uuid4(), run_id=source.run_id, milestone_id=milestone.id,
                                     required_inputs=("Requested identifier",), sought_capabilities=(),
                                     allowed_actions=("click",), stop_conditions=("Identity is ambiguous",),
                                     budget=AssignmentBudget(seconds=10, actions=2, model_calls=1))
    source = ContextService().apply(source, make_patch(source, milestone_plan=(milestone,),
                                                      active_assignment=assignment),
                                    actor_role="coordinator")
    for overrides in ({"allowed_changes": ("address",)}, {"allowed_actions": ("replace_text",)},
                      {"budget": AssignmentBudget(seconds=300, actions=2, model_calls=1)}):
        expanded = type(assignment).model_validate({**assignment.model_dump(), **overrides})
        with pytest.raises(PatchRejected):
            ContextService().apply(source, make_patch(source, active_assignment=expanded),
                                    actor_role="coordinator")


def test_uncertain_operation_survives_operator_progress_and_runtime_null():
    source, action = with_pending(make_snapshot())
    result = ContextService().apply(source, make_patch(source, unresolved_questions=("Check export",)),
                                    actor_role="operator")
    assert result.pending_operation == source.pending_operation
    assert result.action_history[0].outcome == "uncertain"
    with pytest.raises(PatchRejected, match="pending_operation"):
        ContextService().apply(source, make_patch(source, pending_operation=None), actor_role="runtime")
    with pytest.raises(PatchRejected, match="permission_denied"):
        ContextService().apply(source, make_patch(source, clear_pending_operation=True), actor_role="operator")
    with pytest.raises(PatchRejected):
        ContextService().apply(source, make_patch(source, action_records=(ActionRecord.model_validate({
            **action.model_dump(), "outcome": "not_sent"}),)), actor_role="runtime")


def test_send_is_not_completion_and_verifier_reconciles_from_new_evidence():
    source, action = with_pending(make_snapshot())
    assert not source.progress.satisfied_criteria
    source = next_frame(source, revision_delta=1)
    source, fact = verified_fact(source, assertion="Export completed for requested selection")
    confirmed = ActionRecord.model_validate({**action.model_dump(), "outcome": "confirmed",
                                             "subsequent_observation_id": source.current_ui.observation_id,
                                             "observed_effect": fact.assertion,
                                             "effect_evidence_ids": fact.evidence_ids,
                                             "effect_fact_ids": (fact.id,)})
    resolution = PendingResolution(action_id=action.id, outcome="applied", evidence_ids=fact.evidence_ids,
                                   verified_fact_ids=(fact.id,))
    patch = make_patch(source, action_records=(confirmed,), clear_pending_operation=True,
                       pending_resolution=resolution, reconciliation_evidence_ids=fact.evidence_ids)
    with pytest.raises(PatchRejected, match="permission_denied"):
        ContextService().apply(source, patch, actor_role="operator")
    result = ContextService().apply(source, patch, actor_role="verifier")
    assert result.pending_operation is None
    assert result.action_history[0].outcome == "confirmed"
    assert source.pending_operation is not None


def test_reconciliation_before_frame_or_without_verified_facts_rejected():
    source, action = with_pending(make_snapshot())
    source, fact = verified_fact(source)
    resolution = PendingResolution(action_id=action.id, outcome="not_applied",
                                   evidence_ids=fact.evidence_ids, verified_fact_ids=(fact.id,))
    with pytest.raises(PatchRejected, match="invalid_evidence"):
        ContextService().apply(source, make_patch(source, clear_pending_operation=True,
                                                 pending_resolution=resolution,
                                                 reconciliation_evidence_ids=fact.evidence_ids),
                                actor_role="verifier")


def test_only_verifier_can_satisfy_criteria_and_contradiction_invalidates_progress():
    source, fact = verified_fact(make_snapshot())
    criterion = source.task_contract.success_criteria[0].id
    progress = Progress(satisfied_criteria=(CriterionProgress(criterion_id=criterion, fact_ids=(fact.id,),
                                                             evidence_ids=fact.evidence_ids),))
    with pytest.raises(PatchRejected, match="permission_denied"):
        ContextService().apply(source, make_patch(source, progress=progress), actor_role="operator")
    source = ContextService().apply(source, make_patch(source, progress=progress), actor_role="verifier")
    result = ContextService().apply(source, make_patch(source, facts=(make_fact(
        source, assertion="Identity mismatch", contradicts=(fact.id,)),)), actor_role="operator")
    assert result.progress.satisfied_criteria == ()
    assert result.progress.remaining_criterion_ids == (criterion,)


@pytest.mark.parametrize("role", ["coordinator", "operator", "verifier", "recovery", "analyst"])
def test_projection_preserves_all_critical_information_and_evidence_closure(role):
    source, action = with_pending(make_snapshot())
    source, first = verified_fact(source, assertion="Identity initially matches")
    source = ContextService().apply(source, make_patch(source, facts=(make_fact(
        source, assertion="Identifier conflicts", contradicts=(first.id,)),)), actor_role="operator")
    result = ContextService(recent_actions=0).project(source, role=role)
    assert result.task_contract == source.task_contract
    assert result.run_id == source.run_id and result.session_id == source.session_id
    assert result.budgets == source.budgets
    assert result.pending_operation == source.pending_operation
    assert result.facts == source.facts
    assert result.action_history[0].id == action.id
    assert {e.id for e in result.evidence} >= {e for f in result.facts for e in f.evidence_ids}
    assert result.coverage == "sufficient"


def test_projection_declares_omissions_and_preserves_uncertain_old_actions():
    source, pending_action = with_pending(make_snapshot())
    newer = ActionRecord.model_validate({**pending_action.model_dump(), "id": uuid4(), "outcome": "not_sent"})
    source = ContextService().apply(source, make_patch(source, action_records=(newer,)), actor_role="runtime")
    result = ContextService(recent_actions=0).project(source, role="operator")
    assert result.omitted_action_ids == (newer.id,)
    assert result.action_history == (pending_action,)


def test_role_projection_removes_coordinates_for_textual_planning():
    source, _ = map_snapshot()
    assert ContextService().project(source, role="operator").current_ui.visible_controls[0].bounding_box
    assert ContextService().project(source, role="coordinator").current_ui.visible_controls[0].bounding_box is None
    assert source.current_ui.visible_controls[0].state == "unknown"


def test_projection_limit_errors_without_truncation_or_private_payload(caplog, capsys):
    source = make_snapshot()
    source = ContextService().apply(source, make_patch(source, facts=(make_fact(
        source, assertion="Required business value: PRIVATE_BUSINESS_CANARY"),)), actor_role="operator")
    with pytest.raises(ProjectionError, match="insufficient_context") as caught:
        ContextService(max_projection_bytes=100).project(source, role="operator")
    assert "PRIVATE_BUSINESS_CANARY" not in repr(caught.value)
    assert "PRIVATE_BUSINESS_CANARY" not in caplog.text + capsys.readouterr().out
    assert source.facts[0].assertion.endswith("PRIVATE_BUSINESS_CANARY")


def test_bypassed_models_with_secrets_fail_closed_without_error_leak():
    source = make_snapshot()
    bad_fact = make_fact(source).model_copy(update={"assertion": "password=CANARY_DO_NOT_STORE"})
    patch = make_patch(source).model_copy(update={"facts": (bad_fact,)})
    with pytest.raises(PatchRejected) as caught:
        ContextService().apply(source, patch, actor_role="operator")
    assert "CANARY_DO_NOT_STORE" not in str(caught.value) + repr(caught.value)
    poisoned = source.model_copy(update={"facts": (bad_fact,)})
    with pytest.raises(ProjectionError) as caught:
        ContextService().project(poisoned, role="operator")
    assert "CANARY_DO_NOT_STORE" not in str(caught.value) + repr(caught.value)


def test_unknown_role_and_advisor_cannot_write_memory():
    source = make_snapshot()
    for role in ("analyst", "jev", "unknown"):
        with pytest.raises(PatchRejected, match="invalid_role"):
            ContextService().apply(source, make_patch(source), actor_role=role)
    with pytest.raises(ProjectionError, match="invalid_role"):
        ContextService().project(source, role="jev")


def test_evidence_registration_is_runtime_only_and_cross_run_rejected():
    source, foreign = make_snapshot(), make_snapshot()
    with pytest.raises(PatchRejected):
        ContextService().apply(source, make_patch(source, evidence=foreign.evidence), actor_role="runtime")
    with pytest.raises(PatchRejected, match="permission_denied"):
        ContextService().apply(source, make_patch(source, evidence=foreign.evidence), actor_role="operator")


def test_advice_cannot_be_used_as_fact_evidence_or_scope_authority():
    source = make_snapshot()
    advice = AdviceReference(id=uuid4(), run_id=source.run_id, source_context_version=0,
                             observation_id=source.current_ui.observation_id, status="ok")
    source = SharedTaskContext.model_validate({**source.model_dump(), "advice_records": (advice,)})
    with pytest.raises(PatchRejected, match="invalid_evidence"):
        ContextService().apply(source, make_patch(source, facts=(make_fact(
            source, author="verifier", status="independently_verified",
            evidence_ids=(advice.id,)),)), actor_role="verifier")
    result = next_frame(source, generation=1)
    assert result.advice_records[0].status == "stale"
    assert result.task_contract == source.task_contract


def test_old_control_cannot_be_carried_to_new_generation():
    source, _ = map_snapshot()
    new = next_frame(source, generation=1)
    invalid_ui = CurrentUI.model_validate({**new.current_ui.model_dump(),
                                           "visible_controls": source.current_ui.visible_controls})
    poisoned = new.model_copy(update={"current_ui": invalid_ui})
    with pytest.raises(ProjectionError, match="invalid_contract"):
        ContextService().project(poisoned, role="operator")
    assert source.current_ui.visible_controls[0].bounding_box is not None


def test_pixel_change_does_not_reset_no_progress_counter():
    source = make_snapshot()
    source = ContextService().apply(source, make_patch(source, progress=Progress(
        remaining_criterion_ids=source.progress.remaining_criterion_ids, no_progress_count=4)),
        actor_role="operator")
    source = next_frame(source)
    with pytest.raises(PatchRejected, match="invalid_provenance"):
        ContextService().apply(source, make_patch(source, progress=Progress(
            remaining_criterion_ids=source.progress.remaining_criterion_ids, no_progress_count=0)),
            actor_role="operator")


def test_critical_limits_fail_without_eviction_of_prior_provenance():
    source = make_snapshot()
    for _ in range(5):
        source = ContextService().apply(source, make_patch(source, facts=tuple(
            make_fact(source, assertion="Observed control label") for _ in range(20))),
            actor_role="operator")
    before = source.model_dump_json()
    with pytest.raises(PatchRejected, match="limit_exceeded"):
        ContextService().apply(source, make_patch(source, facts=(make_fact(source),)), actor_role="operator")
    assert source.model_dump_json() == before and len(source.facts) == 100


def test_runtime_does_not_record_a_second_send_while_operation_is_pending():
    source, action = with_pending(make_snapshot())
    repeated = ActionRecord.model_validate({**action.model_dump(), "id": uuid4(), "outcome": "sent"})
    with pytest.raises(PatchRejected, match="pending_operation"):
        ContextService().apply(source, make_patch(source, action_records=(repeated,)), actor_role="runtime")


def test_verifier_can_reconcile_not_applied_without_claiming_effect_confirmed():
    source, action = with_pending(make_snapshot())
    source = next_frame(source, revision_delta=1)
    source, fact = verified_fact(source, assertion="The operation did not apply")
    resolution = PendingResolution(action_id=action.id, outcome="not_applied",
                                   evidence_ids=fact.evidence_ids, verified_fact_ids=(fact.id,))
    result = ContextService().apply(source, make_patch(source, clear_pending_operation=True,
                                                      pending_resolution=resolution,
                                                      reconciliation_evidence_ids=fact.evidence_ids),
                                    actor_role="verifier")
    assert result.pending_operation is None
    assert result.action_history[0].outcome == "uncertain"
    assert result.action_history[0].reconciliation.outcome == "not_applied"
    assert result.action_history[0].reconciliation.author == "verifier"
    assert result.facts[0].provenance[-1].author == "verifier"


def test_milestone_cannot_complete_from_a_sent_action_alone():
    source, _ = with_pending(make_snapshot())
    milestone = Milestone(id=uuid4(), run_id=source.run_id, semantic_goal="Read requested value",
                          acceptance_criterion_ids=source.progress.remaining_criterion_ids,
                          status="completed")
    with pytest.raises(PatchRejected, match="invalid_provenance"):
        ContextService().apply(source, make_patch(source, milestone_plan=(milestone,)),
                                actor_role="coordinator")


def test_poisoned_wrong_type_does_not_emit_serialization_warning(recwarn):
    source = make_snapshot()
    bad = source.model_copy(update={"budgets": "password=CANARY_DO_NOT_STORE"})
    with pytest.raises(ProjectionError):
        ContextService().project(bad, role="operator")
    assert not recwarn


def test_generation_invalidates_completed_milestone_and_its_progress():
    source, view = map_snapshot()
    source, fact = verified_fact(source, scope="view", view_id=view.id)
    criterion_id = source.task_contract.success_criteria[0].id
    progress = Progress(satisfied_criteria=(CriterionProgress(criterion_id=criterion_id,
                                                             fact_ids=(fact.id,),
                                                             evidence_ids=fact.evidence_ids),))
    source = ContextService().apply(source, make_patch(source, progress=progress), actor_role="verifier")
    milestone = Milestone(id=uuid4(), run_id=source.run_id, semantic_goal="Confirm identity",
                          acceptance_criterion_ids=(criterion_id,), status="completed")
    source = ContextService().apply(source, make_patch(source, milestone_plan=(milestone,)),
                                    actor_role="coordinator")
    result = next_frame(source, generation=1)
    assert result.milestone_plan[0].status == "blocked"
    assert result.progress.remaining_criterion_ids == (criterion_id,)
    assert result.progress.satisfied_criteria == ()


def test_completed_milestone_is_blocked_after_contradiction_with_history_preserved():
    source, fact = verified_fact(make_snapshot())
    criterion_id = source.task_contract.success_criteria[0].id
    progress = Progress(satisfied_criteria=(CriterionProgress(criterion_id=criterion_id,
                                                             fact_ids=(fact.id,),
                                                             evidence_ids=fact.evidence_ids),))
    source = ContextService().apply(source, make_patch(source, progress=progress), actor_role="verifier")
    milestone = Milestone(id=uuid4(), run_id=source.run_id, semantic_goal="Confirm identity",
                          acceptance_criterion_ids=(criterion_id,), status="completed")
    source = ContextService().apply(source, make_patch(source, milestone_plan=(milestone,)),
                                    actor_role="coordinator")
    result = ContextService().apply(source, make_patch(source, facts=(make_fact(
        source, assertion="Observed identifier contradicts identity", contradicts=(fact.id,)),)),
        actor_role="operator")
    assert result.milestone_plan[0].status == "blocked"
    assert result.facts[0].provenance[-1].status == "contradicted"


def test_all_role_call_budgets_are_runtime_monotonic():
    source = make_snapshot()
    calls = source.budgets.remaining_calls.model_dump()
    for role, count in calls.items():
        expanded = type(source.budgets).model_validate({**source.budgets.model_dump(),
                                                       "remaining_calls": {**calls, role: count + 1}})
        with pytest.raises(PatchRejected, match="budget_violation"):
            ContextService().apply(source, make_patch(source, budgets=expanded), actor_role="runtime")


def test_projection_byte_boundary_is_explicit_and_deterministic():
    source = make_snapshot()
    size = len(ContextService().project(source, role="operator").model_dump_json().encode())
    assert ContextService(max_projection_bytes=size).project(source, role="operator").run_id == source.run_id
    with pytest.raises(ProjectionError, match="insufficient_context"):
        ContextService(max_projection_bytes=size - 1).project(source, role="operator")


def test_provenance_cannot_be_forged_by_reusing_accepted_fact():
    source, fact = verified_fact(make_snapshot())
    forged = make_fact(source, author="operator", provenance=fact.provenance)
    with pytest.raises(PatchRejected, match="invalid_provenance"):
        ContextService().apply(source, make_patch(source, facts=(forged,)), actor_role="operator")


def test_decreasing_runtime_budget_clamps_assignment_without_rewriting_scope():
    source = make_snapshot()
    milestone = Milestone(id=uuid4(), run_id=source.run_id, semantic_goal="Locate identity",
                          acceptance_criterion_ids=source.progress.remaining_criterion_ids, status="active")
    assignment = MilestoneAssignment(id=uuid4(), run_id=source.run_id, milestone_id=milestone.id,
                                     required_inputs=(), sought_capabilities=(), allowed_actions=("click",),
                                     stop_conditions=("Stop on ambiguity",),
                                     budget=AssignmentBudget(seconds=20, actions=10, model_calls=8))
    source = ContextService().apply(source, make_patch(source, milestone_plan=(milestone,),
                                                      active_assignment=assignment),
                                    actor_role="coordinator")
    budget = type(source.budgets).model_validate({**source.budgets.model_dump(), "remaining_seconds": 5,
                                                "remaining_actions": 1})
    result = ContextService().apply(source, make_patch(source, budgets=budget), actor_role="runtime")
    assert result.active_assignment.budget.seconds == 5
    assert result.active_assignment.budget.actions == 1
    assert result.active_assignment.allowed_actions == assignment.allowed_actions
    assert result.active_assignment.stop_conditions == assignment.stop_conditions


def test_passive_reobservation_accepts_same_frame_revision_and_fences_old_observation():
    source = make_snapshot()
    old_patch = make_patch(source)
    result = next_frame(source)
    assert result.current_ui.frame_revision == source.current_ui.frame_revision
    assert result.current_ui.observation_id != source.current_ui.observation_id
    # Both matching version and revision cannot make an old observation current.
    rebased = old_patch.model_copy(update={"base_context_version": result.context_version})
    with pytest.raises(PatchRejected, match="stale_observation"):
        ContextService().apply(result, rebased, actor_role="operator")


def test_fact_validity_is_evaluated_from_runtime_observation_time_without_clock_access():
    source = make_snapshot()
    source, fact = verified_fact(source, valid_until=source.current_ui.captured_at + timedelta(seconds=1))
    result = next_frame(source)
    assert source.facts[0].status == "independently_verified"
    assert result.facts[0].status == "stale"
    assert result.facts[0].evidence_ids == fact.evidence_ids
    assert result.facts[0].provenance[-1].evidence_ids == (result.current_ui.observation_id,)
