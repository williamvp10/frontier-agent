"""Bridge tests on synthetic accepted context, independent of the supporting agent's fixtures."""
from datetime import timedelta
from uuid import uuid4

import pytest

from app.agents.advice_context import build_advice_context
from app.agents.context import (
    ActionRecord,
    Budgets,
    CurrentUI,
    EvidenceReference,
    Fact,
    Identifier,
    PendingOperation,
    PendingResolution,
    Progress,
    RoleCalls,
    SharedTaskContext,
    SuccessCriterion,
    TaskContract,
)
from app.agents.context_service import ContextService
from app.agents.graph_v2 import envelope
from app.domain.contracts import Observation, utcnow
from app.models.advice import AdviceOption, AdviceQuestion
from app.models.jev_advisor import AdviceRejected


def snapshot():
    now, run, session, obs_id, criterion = utcnow(), uuid4(), uuid4(), uuid4(), uuid4()
    observation = Observation(id=obs_id, run_id=run, session_id=session, epoch=1,
        frame_revision=0, captured_at=now, width=1280, height=800,
        artifact_id=uuid4(), sha256="0"*64)
    return SharedTaskContext(run_id=run, session_id=session, epoch=1,
        task_contract=TaskContract(original_instruction="Consulta el cliente indicado.",
            normalized_goal="Locate the requested customer", intent="query",
            identifiers=(Identifier(name="NIT", value="synthetic-identity"),),
            success_criteria=(SuccessCriterion(id=criterion, description="Confirmed identity"),),
            fields_to_preserve=("address",), preservation_rules=("Do not modify fields",),
            response_requirements=("Return observed values",),
            restrictions=("No business API",), allowed_actions=("click",)),
        current_ui=CurrentUI(observation_id=obs_id, captured_at=now, frame_revision=0,
            ui_generation=0, title="Inicio"),
        evidence=(EvidenceReference.from_observation(observation, ui_generation=0),),
        progress=Progress(remaining_criterion_ids=(criterion,)),
        budgets=Budgets(deadline_at=now+timedelta(seconds=20), remaining_seconds=20,
            remaining_calls=RoleCalls(coordinator=2, operator=5, verifier=5, recovery=2),
            remaining_actions=4, remaining_recoveries=2, remaining_advice_calls=4))


def questions():
    return (AdviceQuestion(question_id="next_route", instructions="Which route should be inspected?",
        options=(AdviceOption(option_id="reobserve", description="Gather targeted evidence",
                              preconditions=(), known_risk="Observation latency"),
                 AdviceOption(option_id="insufficient", description="Insufficient evidence",
                              preconditions=(), known_risk="Cannot select a route"))),)


def test_binding_comes_from_accepted_snapshot_with_identity_constraints_and_budget():
    source = snapshot()
    before = source.model_dump_json()
    request, binding = build_advice_context(source, requester_role="operator",
                                          questions=questions(), remaining_seconds=12)
    assert source.model_dump_json() == before
    assert request.task.original_instruction == source.task_contract.original_instruction
    assert request.task.identifiers[0].value == "synthetic-identity"
    assert request.task.fields_to_preserve == ("address",)
    assert request.task.restrictions == ("No business API",)
    assert request.task.allowed_actions == ("click",)
    assert request.progress.remaining_criteria == ("Confirmed identity",)
    assert request.budgets.remaining_seconds == 12
    assert request.scope == binding.scope
    assert request.situation.current_view == "Inicio"


def test_uncertain_operation_and_expected_vs_observed_are_not_omitted():
    source = snapshot()
    obs = source.current_ui.observation_id
    action_id = uuid4()
    data = source.model_dump(mode="python")
    data["action_history"] = (ActionRecord(run_id=source.run_id, id=action_id, actor="runtime",
        source_observation_id=obs, semantic_goal="Inspect selected record", expected_effect="Record visible",
        outcome="uncertain"),)
    data["pending_operation"] = PendingOperation(run_id=source.run_id, id=uuid4(), action_id=action_id,
        kind="mutation", expected_effect="Save applied", evidence_ids=(obs,),
        reconciliation_requirement="Verify persistence before repeating")
    source = SharedTaskContext.model_validate(data)
    request, binding = build_advice_context(source, requester_role="recovery", questions=questions(), remaining_seconds=10)
    assert request.history[0].send_status == "uncertain"
    assert "unconfirmed" in request.history[0].observed_effect
    assert "Verify persistence before repeating" in request.uncertainty.pending_operation
    assert binding.pending_operation == request.uncertainty.pending_operation


def test_missing_budget_and_critical_secret_fail_before_provider():
    source = snapshot()
    with pytest.raises(AdviceRejected):
        build_advice_context(source, requester_role="operator", questions=questions(), remaining_seconds=0)
    with pytest.raises(AdviceRejected, match="private_context"):
        build_advice_context(source, requester_role="operator", questions=questions(), remaining_seconds=10,
                             secrets=("synthetic-identity",))


def test_overflow_does_not_silently_truncate_facts():
    source = snapshot()
    facts = tuple(Fact(run_id=source.run_id, id=uuid4(), assertion="Observed synthetic label",
                      scope="view", evidence_ids=(source.current_ui.observation_id,),
                      author="operator", ui_generation=0) for _ in range(41))
    source = SharedTaskContext.model_validate({**source.model_dump(mode="python"), "facts": facts})
    with pytest.raises(AdviceRejected, match="invalid_advice_context"):
        build_advice_context(source, requester_role="operator", questions=questions(), remaining_seconds=10)


def test_historical_reconciliation_and_its_evidence_survive_the_advice_bridge():
    source = snapshot()
    service = ContextService()
    initial = source.current_ui.observation_id
    action = ActionRecord(run_id=source.run_id, id=uuid4(), actor="runtime", source_observation_id=initial,
        semantic_goal="Export requested records", expected_effect="Export completes", outcome="uncertain")
    pending = PendingOperation(run_id=source.run_id, id=uuid4(), action_id=action.id, kind="export",
        expected_effect=action.expected_effect, evidence_ids=(initial,), reconciliation_requirement="Verify export")
    source = service.apply(source, envelope(source, action_records=(action,), pending_operation=pending), actor_role="runtime")
    obs = Observation(run_id=source.run_id, session_id=source.session_id, epoch=1, frame_revision=1,
        captured_at=source.current_ui.captured_at+timedelta(seconds=1), width=1280, height=800,
        artifact_id=uuid4(), sha256="0"*64)
    source = service.apply(source, envelope(source,
        evidence=(EvidenceReference.from_observation(obs, ui_generation=0),),
        current_ui=CurrentUI(observation_id=obs.id, captured_at=obs.captured_at, frame_revision=1, ui_generation=0)),
        actor_role="runtime")
    fact = Fact(id=uuid4(), run_id=source.run_id, scope="record", assertion="Export did not apply",
                author="verifier", status="independently_verified", evidence_ids=(obs.id,), ui_generation=0)
    source = service.apply(source, envelope(source, facts=(fact,)), actor_role="verifier")
    resolution = PendingResolution(action_id=action.id, outcome="not_applied", evidence_ids=(obs.id,),
                                   verified_fact_ids=(fact.id,))
    source = service.apply(source, envelope(source, clear_pending_operation=True,
        reconciliation_evidence_ids=(obs.id,), pending_resolution=resolution), actor_role="verifier")
    request, binding = build_advice_context(source, requester_role="recovery", questions=questions(), remaining_seconds=10)
    assert request.uncertainty.pending_operation is None
    assert request.history[0].send_status == "uncertain"
    assert request.history[0].reconciliation_outcome == "not_applied"
    assert request.history[0].reconciliation_evidence_ids == (obs.id,)
    assert set(request.history[0].evidence_ids) == {initial, obs.id}
    assert {initial, obs.id} <= set(binding.evidence_ids)
