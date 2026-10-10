import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from app.agents.advice_flow import consult_then_decide
from app.domain.contracts import utcnow
from app.models.advice import AdviceRequest, FinalDecision
from app.models.jev_advisor import AdviceBinding, AdviceRejected, AdviceSession, failed_advice


def inputs():
    observation = uuid4()
    request = AdviceRequest.model_validate({
        "requester_role": "recovery",
        "scope": {"run_id": uuid4(), "session_id": uuid4(), "epoch": 1,
                  "context_version": 1, "ui_generation": 0, "observation_id": observation},
        "task": {"original_instruction": "Read the record", "normalized_goal": "Read only",
                 "identifiers": [], "restrictions": ["No mutation"], "fields_to_preserve": [],
                 "allowed_actions": ["click"], "allowed_changes": [], "preservation_rules": ["No writes"],
                 "success_criteria": ["Observed answer"], "response_requirements": "Value"},
        "progress": {"active_milestone": "Inspect record", "completed_criteria": [],
                     "remaining_criteria": ["Observed answer"]},
        "situation": {"current_view": "Record", "visible_controls": [], "relevant_routes": []},
        "observed_facts": [], "verified_facts": [], "history": [],
        "uncertainty": {"hypotheses": [], "contradictions": [], "missing_information": [], "pending_operation": None},
        "budgets": {"remaining_seconds": 10, "remaining_actions": 2},
        "questions": [{"question_id": "next_step", "instructions": "Which next step is safest?", "options": [
            {"option_id": "observe", "description": "Observe a fresh frame", "preconditions": [], "known_risk": "Latency"},
            {"option_id": "insufficient", "description": "Cannot decide", "preconditions": [], "known_risk": "No completion"}]}],
        "context_coverage": {"included_topics": ["task", "progress", "situation", "evidence", "history", "uncertainty", "budgets"],
                             "omitted_topics": [], "sufficiency_status": "sufficient"}})
    binding = AdviceBinding(scope=request.scope, task=request.task, pending_operation=None, evidence_ids=(observation,))
    session = AdviceSession(run_id=request.scope.run_id, action_cutoff_at=utcnow()+timedelta(seconds=10), advisor=None)
    return request, binding, session


def decision(request, binding):
    return FinalDecision(request_id=request.request_id, requester_role=request.requester_role,
        scope=request.scope, question_id="next_step", selected_option="observe", accepted_advice=False,
        brief_reason="Fresh visual evidence is required", evidence_ids=binding.evidence_ids,
        unmet_preconditions=(), next_step="reobserve")


async def test_optional_unavailable_advisor_returns_to_same_requester_for_own_decision():
    request, binding, session = inputs()
    calls = []

    async def decide(original, advice):
        calls.append((original.requester_role, advice.status))
        return decision(original, binding)

    result = await consult_then_decide(request, binding=binding, session=session,
        decide=decide, latest_binding=lambda: binding)
    assert calls == [("recovery", "unavailable")]
    assert result.decision.next_step == "reobserve" and result.reason_code == "decision_candidate"


async def test_context_changes_during_requester_inference_drop_candidate():
    request, binding, session = inputs()
    current = [binding]

    async def decide(original, advice):
        current[0] = binding.model_copy(update={"scope": binding.scope.model_copy(update={"context_version": 2})})
        return decision(original, binding)

    result = await consult_then_decide(request, binding=binding, session=session,
        decide=decide, latest_binding=lambda: current[0])
    assert result.decision is None and result.advice.status == "stale"


async def test_timeout_and_cancel_of_requester_do_not_restart_task_budget():
    request, binding, session = inputs()

    async def slow(*args):
        await asyncio.sleep(10)

    result = await consult_then_decide(request, binding=binding, session=session,
        decide=slow, latest_binding=lambda: binding, model_timeout_seconds=0.01)
    assert result.decision is None and result.reason_code == "decision_timeout"
    task = asyncio.create_task(consult_then_decide(request, binding=binding, session=session,
        decide=slow, latest_binding=lambda: binding))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_invalid_scope_never_calls_requester():
    request, binding, session = inputs()
    newer = binding.model_copy(update={"scope": binding.scope.model_copy(update={"context_version": 2})})

    async def must_not_call(*args):
        raise AssertionError("No inference on stale context")

    result = await consult_then_decide(request, binding=newer, session=session,
        decide=must_not_call, latest_binding=lambda: newer)
    assert result.decision is None and result.advice.status == "stale"


async def test_provider_error_and_requester_error_are_sanitized():
    request, binding, session = inputs()

    class Advisor:
        async def advise(self, request, **kwargs):
            return failed_advice(request, "unavailable", "provider_unavailable")

    session.advisor = Advisor()

    async def broken(*args):
        raise RuntimeError("canary-private-error")

    result = await consult_then_decide(request, binding=binding, session=session,
        decide=broken, latest_binding=lambda: binding)
    assert result.reason_code == "decision_unavailable"
    assert "canary-private-error" not in result.model_dump_json()


async def test_untrusted_callback_exception_code_cannot_leak_context():
    request, binding, session = inputs()

    async def broken(*args):
        raise AdviceRejected("canary-private-context")

    result = await consult_then_decide(request, binding=binding, session=session,
        decide=broken, latest_binding=lambda: binding)
    assert result.reason_code == "invalid_advice_context"
    assert "canary-private-context" not in result.model_dump_json()
