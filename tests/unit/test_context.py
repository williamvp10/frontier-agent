"""Synthetic contract tests; no desktop, provider or organizer data."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agents.context import (
    Budgets,
    ContextPatch,
    CurrentUI,
    EvidenceReference,
    Fact,
    RoleCalls,
    SharedTaskContext,
    SuccessCriterion,
    TaskContract,
)
from app.domain.contracts import Observation

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 10, tzinfo=UTC)


def make_snapshot(**overrides):
    run, session, obs_id = uuid4(), uuid4(), uuid4()
    observation = Observation(id=obs_id, run_id=run, session_id=session, epoch=1,
                              frame_revision=0, captured_at=NOW, width=800, height=600,
                              artifact_id=uuid4(), sha256="0" * 64)
    values = dict(
        run_id=run, session_id=session, epoch=1,
        task_contract=TaskContract(
            original_instruction="Consulta el cliente indicado.",
            normalized_goal="Read the specified customer", intent="query",
            success_criteria=(SuccessCriterion(id=uuid4(), description="Identity confirmed"),),
            response_requirements=("Return the requested value",),
            restrictions=("Do not change business data",), allowed_actions=("click",),
        ),
        current_ui=CurrentUI(observation_id=obs_id, captured_at=NOW, frame_revision=0,
                             ui_generation=0),
        evidence=(EvidenceReference.from_observation(observation, ui_generation=0),),
        budgets=Budgets(deadline_at=NOW, remaining_seconds=250,
                        remaining_calls=RoleCalls(coordinator=5, operator=10, verifier=10,
                                                  recovery=2),
                        remaining_actions=30, remaining_recoveries=2, remaining_advice_calls=4),
    )
    values.update(overrides)
    return SharedTaskContext(**values)


def make_patch(snapshot, **overrides):
    values = dict(run_id=snapshot.run_id, session_id=snapshot.session_id, epoch=snapshot.epoch,
                  base_context_version=snapshot.context_version,
                  observation_id=snapshot.current_ui.observation_id,
                  frame_revision=snapshot.current_ui.frame_revision,
                  ui_generation=snapshot.ui_generation)
    values.update(overrides)
    return ContextPatch(**values)


def make_fact(snapshot, **overrides):
    values = dict(run_id=snapshot.run_id, id=uuid4(), assertion="Customer label is visible",
                  scope="record", evidence_ids=(snapshot.current_ui.observation_id,),
                  author="operator", ui_generation=snapshot.ui_generation)
    values.update(overrides)
    return Fact(**values)


def test_roundtrip_and_nested_immutability():
    snapshot = make_snapshot()
    assert SharedTaskContext.model_validate_json(snapshot.model_dump_json()) == snapshot
    with pytest.raises(ValidationError):
        snapshot.budgets.remaining_actions = 999
    with pytest.raises(ValidationError):
        snapshot.task_contract.normalized_goal = "Changed"
    assert isinstance(snapshot.evidence, tuple)


def test_extra_fields_and_limits():
    snapshot = make_snapshot()
    with pytest.raises(ValidationError):
        make_patch(snapshot, task_contract=snapshot.task_contract)
    with pytest.raises(ValidationError):
        make_patch(snapshot, facts=tuple(make_fact(snapshot) for _ in range(21)))
    with pytest.raises(ValidationError):
        make_fact(snapshot, assertion="x" * 2049)


def test_cross_run_records_and_unknown_evidence_rejected():
    snapshot = make_snapshot()
    for fact in (make_fact(snapshot, run_id=uuid4()),
                 make_fact(snapshot, evidence_ids=(uuid4(),))):
        with pytest.raises(ValidationError):
            SharedTaskContext.model_validate({**snapshot.model_dump(), "facts": (fact,)})


@pytest.mark.parametrize("content", [
    "password=CANARY_DO_NOT_STORE", "Bearer CANARY_DO_NOT_STORE",
    "wss://private.invalid/CANARY_DO_NOT_STORE", "data:image/png;base64,CANARY_DO_NOT_STORE",
    "<think>CANARY_DO_NOT_STORE</think>", "A" * 300,
])
def test_forbidden_content_and_validation_errors_are_sanitized(content):
    with pytest.raises(ValidationError) as caught:
        make_fact(make_snapshot(), assertion=content)
    assert content not in str(caught.value)
    assert content not in caught.value.json()
    assert content not in repr(caught.value.errors())
    assert caught.value.__context__ is None


def test_extra_field_canary_not_in_errors():
    with pytest.raises(ValidationError) as caught:
        make_fact(make_snapshot(), password="CANARY_DO_NOT_STORE")
    assert "CANARY_DO_NOT_STORE" not in caught.value.json()


def test_private_business_literals_preserved_but_not_in_repr():
    fact = make_fact(make_snapshot(), assertion="Customer identifier: PRIVATE_BUSINESS_LITERAL")
    assert "PRIVATE_BUSINESS_LITERAL" in fact.model_dump_json()
    assert "PRIVATE_BUSINESS_LITERAL" not in repr(fact)
    assert "PRIVATE_BUSINESS_LITERAL" not in str(fact)


def test_naive_dates_and_nonfinite_budget_rejected():
    snapshot = make_snapshot()
    for updates in ({"deadline_at": datetime(2026, 10, 10)}, {"remaining_seconds": float("nan")}):
        with pytest.raises(ValidationError):
            Budgets.model_validate({**snapshot.budgets.model_dump(), **updates})
