from uuid import uuid4

import pytest

from app.agents.context import Milestone, MilestoneAssignment
from app.models.schemas import VisualVerification
from app.models.turns import (
    CriterionCheck,
    OperatorTurn,
    RecoveryTurn,
    ReplanTurn,
    UIControlProposal,
    UIInterpretation,
    VerifierTurn,
)
from tests.unit.test_graph_v2 import action, planner, run, setup, verify

pytestmark = pytest.mark.unit


async def test_last_partial_criterion_routes_to_final_verification_without_new_assignment():
    def last_proof(context):
        cid = context["shared"]["task_contract"]["success_criteria"][0]["id"]
        return VerifierTurn(report=VisualVerification(status="incomplete", safe_to_execute=False,
            changes_business_data=False, facts=(), unresolved_identifiers=(), answer=None),
            effect_status="verified", effect_facts=("Synthetic transition confirmed",),
            observed_effect="Synthetic result visible", operation_resolution="applied",
            criterion_checks=(CriterionCheck(criterion_id=cid,
                             verified_facts=("Synthetic requested total and coverage proven",)),))

    d, _, model, rt = setup([planner(), action(), verify(), last_proof, verify(complete=True)])
    result = await run(d, rt)
    assert result.estado == "terminada" and len(d.actions) == 1
    assert [call[0] for call in model.calls] == ["coordinator", "operator", "verifier", "verifier", "verifier"]
    assert model.calls[-1][2]["mode"] == "completion"
    assert not model.calls[-1][2]["shared"]["progress"]["remaining_criterion_ids"]


async def test_rejected_ui_and_replan_supply_feedback_without_sending_their_actions():
    invalid_ui = OperatorTurn(**{**action().model_dump(), "ui": UIInterpretation(
        visible_controls=(UIControlProposal(id=uuid4(), label="Synthetic menu", kind="button",
                                          state="enabled", view_id=uuid4()),))})

    def recover_ui(context):
        assert context["last_rejection"] == {"stage": "ui", "reason_code": "unknown_control_view"}
        return RecoveryTurn(strategy="replan", brief_reason="Synthetic changed strategy")

    def invalid_plan(context):
        shared = context["shared"]
        old = Milestone.model_validate(shared["milestone_plan"][0])
        return ReplanTurn(milestone_plan=(old, old.model_copy(update={"id": uuid4()})),
                          active_assignment=MilestoneAssignment.model_validate(shared["active_assignment"]))

    def repair_plan(context):
        assert context["last_rejection"] == {"stage": "planning_failed", "reason_code": "invalid_contract",
                                             "diagnostic": "multiple_active_milestones"}
        assert len(context["shared"]["milestone_plan"]) == 1
        return RecoveryTurn(strategy="reobserve", brief_reason="Retry operator with preserved plan")

    d, recorder, _, rt = setup([planner(), invalid_ui, recover_ui, invalid_plan, repair_plan,
                                action(), verify(), verify(complete=True, resolution="applied")])
    result = await run(d, rt)
    assert result.estado == "terminada" and len(d.actions) == 1
    assert any(kind == "context_failure" for _, kind, _ in recorder.events)


async def test_rejected_initial_plan_replans_before_operator_can_execute():
    def rejected(context):
        first = planner()(context)
        assignment = first.active_assignment.model_copy(update={"allowed_changes": ("unauthorized",)})
        return first.model_copy(update={"active_assignment": assignment})

    def repair(context):
        shared = context["shared"]
        criterion = shared["task_contract"]["success_criteria"][0]["id"]
        milestone = Milestone(id=uuid4(), run_id=shared["run_id"], semantic_goal="Synthetic goal",
                             acceptance_criterion_ids=(criterion,), status="active")
        original = planner()({"run_id": shared["run_id"], "instruction": "Synthetic task"})
        assignment = original.active_assignment.model_copy(update={"milestone_id": milestone.id})
        return ReplanTurn(milestone_plan=(milestone,), active_assignment=assignment)

    d, _, model, rt = setup([rejected, RecoveryTurn(strategy="reobserve", brief_reason="Repair plan"),
                             repair, action(), verify(), verify(complete=True, resolution="applied")])
    result = await run(d, rt)
    assert result.estado == "terminada" and len(d.actions) == 1
    assert [c[0] for c in model.calls][:4] == ["coordinator", "recovery", "coordinator", "operator"]
    assert model.calls[1][2]["last_rejection"]["diagnostic"] == "expanded_assignment"


async def test_incomplete_final_assessment_reopens_criteria_and_repairs_missing_proof():
    def premature_proof(context):
        cid = context["shared"]["task_contract"]["success_criteria"][0]["id"]
        return VerifierTurn(report=VisualVerification(status="incomplete", safe_to_execute=False,
            changes_business_data=False, facts=(), unresolved_identifiers=(), answer=None),
            effect_status="verified", effect_facts=("Synthetic transition confirmed",),
            observed_effect="Synthetic result visible", operation_resolution="applied",
            criterion_checks=(CriterionCheck(criterion_id=cid, verified_facts=("Synthetic partial proof",)),))

    missing = VerifierTurn(report=VisualVerification(status="incomplete", safe_to_execute=False,
        changes_business_data=False, facts=("Synthetic first page lacks proof",),
        unresolved_identifiers=(), answer=None))

    def repair(context):
        assert context["shared"]["progress"]["remaining_criterion_ids"]
        assert context["verification_feedback"]["missing_evidence"] == ("Synthetic first page lacks proof",)
        assert not any(f["assertion"] == "Synthetic first page lacks proof" for f in context["shared"]["facts"])
        return action()

    d, recorder, _, rt = setup([planner(), action(), verify(), premature_proof, missing,
                                repair, verify(), verify(complete=True, resolution="applied")])
    result = await run(d, rt)
    assert result.estado == "terminada" and len(d.actions) == 2
    assert any(kind == "completion_reopened" for _, kind, _ in recorder.events)


async def test_verified_page_facts_are_retained_without_claiming_aggregate_complete():
    def page(context):
        return VerifierTurn(report=VisualVerification(status="incomplete", safe_to_execute=False,
            changes_business_data=False, facts=(), unresolved_identifiers=(), answer=None),
            effect_status="verified", effect_facts=("Synthetic transition confirmed",),
            observed_effect="Synthetic result visible", operation_resolution="applied",
            verified_observation_facts=("Synthetic page A contains two exact rows",))

    def next_page(context):
        assert context["shared"]["progress"]["remaining_criterion_ids"]
        assert any(f["assertion"] == "Synthetic page A contains two exact rows" and
                   f["status"] == "independently_verified" for f in context["shared"]["facts"])
        return action()

    d, _, _, rt = setup([planner(), action(), verify(), page, next_page, verify(),
                         verify(complete=True, resolution="applied")])
    result = await run(d, rt)
    assert result.estado == "terminada" and len(d.actions) == 2


async def test_ambiguous_ui_is_repaired_without_identity_abstention_or_wrong_action():
    rejected = VerifierTurn(report=VisualVerification(status="ambiguous", safe_to_execute=False,
        changes_business_data=False, facts=("Synthetic focus is on another field",),
        unresolved_identifiers=(), answer=None), ambiguity_kind="ui")
    d, _, model, rt = setup([planner(), action(), rejected,
        RecoveryTurn(strategy="reobserve", brief_reason="Relocalize input"), action(), verify(),
        verify(complete=True, resolution="applied")])
    result = await run(d, rt)
    assert result.estado == "terminada" and len(d.actions) == 1
    assert model.calls[3][0] == "recovery"
    assert model.calls[3][2]["verification_feedback"]["missing_evidence"] == ("Synthetic focus is on another field",)


async def test_identity_ambiguity_closes_without_sending_and_reports_identifiers():
    ambiguity = VerifierTurn(report=VisualVerification(status="ambiguous", safe_to_execute=False,
        changes_business_data=False, facts=("Synthetic distinct records share a name",),
        unresolved_identifiers=("SYNTHETIC-ID-A", "SYNTHETIC-ID-B"), answer=None), ambiguity_kind="identity")
    d, _, _, rt = setup([planner(), action(), ambiguity])
    result = await run(d, rt)
    assert result.estado == "fallida" and not d.actions
    assert "SYNTHETIC-ID-A" in result.resumen and "SYNTHETIC-ID-B" in result.resumen


async def test_verification_timeout_reobserves_and_never_replays_sent_action():
    def timeout(context):
        raise TimeoutError("PRIVATE_TIMEOUT_CANARY")

    d, recorder, _, rt = setup([planner(), action(), verify(), timeout,
                                verify(complete=True, resolution="applied")])
    result = await run(d, rt)
    assert result.estado == "terminada" and len(d.actions) == 1
    assert any(kind == "model_retry" for _, kind, _ in recorder.events)
    assert "PRIVATE_TIMEOUT_CANARY" not in str(recorder.events)
