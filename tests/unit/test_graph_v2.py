"""Behavioral v2 tests; fake vision/VNC, real reducer and LangGraph checkpointer."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.context import (
    AssignmentBudget,
    Milestone,
    MilestoneAssignment,
    SuccessCriterion,
    TaskContract,
)
from app.agents.graph import run_agent
from app.agents.runtime import AgentRuntime
from app.domain.contracts import Observation, utcnow
from app.models.advice import FinalDecision
from app.models.jev_advisor import AdviceSession, failed_advice
from app.models.schemas import ModelAction, VisualVerification
from app.models.turns import (
    CoordinatorTurn,
    OperatorTurn,
    RecoveryTurn,
    UIInterpretation,
    VerifierTurn,
)
from tests.unit.test_jev_advisor import example

pytestmark = pytest.mark.unit


class Desktop:
    def __init__(self):
        self.run_id, self.session_id = uuid4(), uuid4()
        self.frames, self.actions, self.approvals = [], [], []
        self.uncertain = False

    async def observe(self):
        obs = Observation(run_id=self.run_id, session_id=self.session_id, epoch=1,
            captured_at=utcnow(), frame_revision=len(self.actions), width=1280, height=800,
            artifact_id=uuid4(), sha256="0" * 64)
        self.frames.append(obs)
        return obs

    async def image_bytes(self, obs_id):
        assert obs_id in {o.id for o in self.frames}
        return b"SYNTHETIC_IMAGE_CANARY"

    async def authorize(self, proposal, verification):
        assert verification.evidence_ids == (proposal.observation_id,)
        self.approvals.append(proposal.id)

    async def execute(self, proposal):
        assert proposal.id in self.approvals
        self.actions.append(proposal)
        if self.uncertain:
            self.uncertain = False
            raise OSError("password=PRIVATE_TRANSPORT_CANARY")


class Recorder:
    def __init__(self):
        self.events = []

    async def append(self, role, kind, payload):
        self.events.append((role, kind, payload))


class Model:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def decide(self, role, schema, context, image):
        assert image == b"SYNTHETIC_IMAGE_CANARY" or image == (b"SYNTHETIC_IMAGE_CANARY", b"SYNTHETIC_IMAGE_CANARY")
        self.calls.append((role, schema, context))
        value = self.outputs.pop(0)
        if callable(value):
            value = value(context)
        return schema.model_validate(value)


def planner(intent="query"):
    def output(context):
        run_id = context["run_id"]
        criterion = SuccessCriterion(id=uuid4(), description="Requested result and identity verified")
        milestone = Milestone(id=uuid4(), run_id=run_id, semantic_goal="Read requested result",
                              acceptance_criterion_ids=(criterion.id,), status="active")
        assignment = MilestoneAssignment(id=uuid4(), run_id=run_id, milestone_id=milestone.id,
            required_inputs=("Original instruction",), sought_capabilities=("Requested records",),
            allowed_actions=("click",), allowed_changes=("address",) if intent == "edit" else (),
            stop_conditions=("Stop on ambiguity",), budget=AssignmentBudget(seconds=20, actions=5, model_calls=5))
        return CoordinatorTurn(task_contract=TaskContract(
            original_instruction=context["instruction"], normalized_goal="Read the requested result",
            intent=intent, identifiers=(), allowed_changes=assignment.allowed_changes,
            success_criteria=(criterion,), fields_to_preserve=("name",), preservation_rules=("Preserve name",),
            response_requirements=("Return the requested result",), restrictions=("No unrelated changes",),
            allowed_actions=("click",)), milestone_plan=(milestone,), active_assignment=assignment)
    return output


def verify(*, complete=False, resolution=None, changes=False, status="verified"):
    def output(context):
        criterion_ids = tuple(c["id"] for c in context["shared"]["task_contract"]["success_criteria"])
        return VerifierTurn(report=VisualVerification(status=status, safe_to_execute=True,
            changes_business_data=changes, facts=("Requested identity and visible value match",) if status == "verified" else (),
            unresolved_identifiers=(), answer="7" if complete else None),
            satisfied_criterion_ids=criterion_ids if complete else (),
            operation_resolution=resolution, observed_effect="Expected effect verified" if resolution else None)
    return output


def action(risk="navigation"):
    return OperatorTurn(disposition="action", args=ModelAction(kind="click", x=20, y=20, hold_ms=180,
        keys=None, text=None, direction=None, amount=None), expected_effect="Open requested record",
        scope_reason="Inspect requested identity", risk=risk)


def setup(outputs, **kwargs):
    desktop, recorder, model = Desktop(), Recorder(), Model(outputs)
    cutoff = utcnow() + timedelta(seconds=270)
    rt = AgentRuntime(model=model, desktop=desktop, recorder=recorder, action_cutoff_at=cutoff,
        graph_version="context-v2", secrets=("PRIVATE_TRANSPORT_CANARY",), **kwargs)
    rt.advice_session = AdviceSession(run_id=desktop.run_id, action_cutoff_at=cutoff, advisor=None)
    return desktop, recorder, model, rt


async def run(desktop, runtime, saver=None):
    return await run_agent("Consulta el resultado solicitado.", desktop.run_id, desktop.session_id,
                           1, runtime, checkpointer=saver)


async def test_query_uses_milestone_slice_independent_criteria_and_private_checkpoint():
    d, recorder, model, rt = setup([planner(), OperatorTurn(disposition="verify"), verify(complete=True)])
    saver = InMemorySaver()
    result = await run(d, rt, saver)
    assert result.estado == "terminada" and result.resumen == "respuesta: 7"
    assert [r for r, _, _ in model.calls] == ["coordinator", "operator", "verifier"]
    assert not d.actions
    shared = rt.context_snapshot
    assert shared.progress.remaining_criterion_ids == ()
    assert shared.active_assignment is None
    assert all(m.status == "completed" for m in shared.milestone_plan)
    assert shared.facts[0].provenance[-1].author == "verifier"
    assert model.calls[1][2]["shared"]["active_assignment"]["stop_conditions"] == ["Stop on ambiguity"]
    checkpoints = [item async for item in saver.alist({"configurable": {"thread_id": str(d.run_id)}})]
    assert checkpoints and any("shared" in item.checkpoint["channel_values"] for item in checkpoints)
    assert "SYNTHETIC_IMAGE_CANARY" not in repr(checkpoints) + repr(recorder.events)
    assert "PRIVATE_TRANSPORT_CANARY" not in repr(checkpoints) + repr(recorder.events)


async def test_action_requires_verifier_and_effect_is_confirmed_only_after_later_frame():
    d, _, model, rt = setup([planner("edit"), action("commit"), verify(changes=True),
                             verify(complete=True, resolution="applied")])
    result = await run(d, rt)
    assert result.estado == "terminada"
    assert len(d.actions) == len(d.approvals) == 1
    assert [r for r, _, _ in model.calls] == ["coordinator", "operator", "verifier", "verifier"]
    assert rt.context_snapshot.pending_operation is None
    record = rt.context_snapshot.action_history[0]
    assert record.outcome == "confirmed" and record.reconciliation.outcome == "applied"
    assert record.subsequent_observation_id != record.source_observation_id


async def test_uncertain_save_keeps_pending_until_new_frame_independent_reconciliation():
    d, _, _, rt = setup([planner("edit"), action("commit"), verify(changes=True),
                         verify(status="incomplete"), RecoveryTurn(strategy="verify", brief_reason="Reconcile before repeat"),
                         verify(complete=True, resolution="applied")])
    d.uncertain = True
    result = await run(d, rt)
    assert result.estado == "terminada" and len(d.actions) == 1
    assert len(d.frames) >= 3
    assert rt.context_snapshot.action_history[0].reconciliation is not None


async def test_operator_cannot_repeat_uncertain_mutation_or_declare_success():
    d, _, _, rt = setup([planner("edit"), action("commit"), verify(changes=True),
                         verify(status="incomplete"), RecoveryTurn(strategy="abstain", brief_reason="Unverifiable operation")])
    d.uncertain = True
    result = await run(d, rt)
    assert result.estado == "fallida" and len(d.actions) == 1
    assert rt.context_snapshot.pending_operation is not None
    assert not rt.context_snapshot.progress.satisfied_criteria


async def test_query_cannot_mutate_even_if_operator_calls_it_navigation():
    d, _, _, rt = setup([planner(), action(), verify(changes=True),
                         RecoveryTurn(strategy="abstain", brief_reason="Unsafe query")])
    result = await run(d, rt)
    assert result.estado == "fallida" and not d.actions and not d.approvals


async def test_existing_checkpoint_is_not_replayed_migrated_or_overwritten():
    d, _, _, rt = setup([planner(), OperatorTurn(disposition="verify"), verify(complete=True)])
    saver = InMemorySaver()
    await run(d, rt, saver)
    before = await saver.aget({"configurable": {"thread_id": str(d.run_id)}})
    fresh = AgentRuntime(model=Model([]), desktop=d, recorder=Recorder(), action_cutoff_at=rt.action_cutoff_at,
                         graph_version="context-v2")
    result = await run(d, fresh, saver)
    assert result.reason_code == "checkpoint_requires_reconciliation"
    assert await saver.aget({"configurable": {"thread_id": str(d.run_id)}}) == before
    assert not d.actions and fresh.context_snapshot is None


async def test_advice_returns_to_same_operator_then_normal_verification_no_direct_click():
    questions = example()[0].questions

    def decision(context):
        request = context["advice_request"]
        assert context["advice_result"]["status"] == "unavailable"
        return FinalDecision(request_id=request["request_id"], requester_role="operator", scope=request["scope"],
            question_id=questions[0].question_id, selected_option="insufficient", accepted_advice=False,
            brief_reason="Observe sufficient evidence without relying on advice", evidence_ids=(request["scope"]["observation_id"],),
            unmet_preconditions=(), next_step="verify")

    d, _, model, rt = setup([planner(), OperatorTurn(disposition="consult_jev", questions=questions),
                             decision, verify(complete=True)])
    result = await run(d, rt)
    assert result.estado == "terminada" and not d.actions
    assert [role for role, _, _ in model.calls] == ["coordinator", "operator", "operator", "verifier"]
    assert model.calls[2][1] is FinalDecision


async def test_advice_timeout_does_not_extend_deadline_or_approve_vnc():
    questions = example()[0].questions

    class TimeoutAdvisor:
        async def advise(self, request, **kwargs):
            return failed_advice(request, "timeout", "provider_timeout")

    def decision(context):
        request = context["advice_request"]
        assert context["advice_result"]["status"] == "timeout"
        return FinalDecision(request_id=request["request_id"], requester_role="operator", scope=request["scope"],
            question_id=questions[0].question_id, selected_option="insufficient", accepted_advice=False,
            brief_reason="Evidence is insufficient", evidence_ids=(request["scope"]["observation_id"],),
            unmet_preconditions=(), next_step="abstain")

    d, _, _, rt = setup([planner(), OperatorTurn(disposition="consult_jev", questions=questions), decision])
    rt.advice_session.advisor = TimeoutAdvisor()
    deadline = rt.action_cutoff_at
    result = await run(d, rt)
    assert result.estado == "fallida" and not d.actions
    assert rt.action_cutoff_at == deadline and rt.advice_session.calls == 1


async def test_expired_budget_stops_before_observe_and_model_timeout_is_sanitized():
    d, _, _, rt = setup([])
    rt.action_cutoff_at = utcnow() - timedelta(seconds=1)
    assert (await run(d, rt)).reason_code == "deadline"
    assert not d.frames
    d, _, _, rt = setup([], model_timeout_s=0.01)

    class Slow:
        async def decide(self, *args):
            await asyncio.sleep(1)

    rt.model = Slow()
    assert (await run(d, rt)).reason_code == "planning_failed"
    assert not d.actions


async def test_scope_and_private_canary_never_reach_checkpoint_or_desktop():
    d, _, _, rt = setup([])
    saver = InMemorySaver()
    result = await run_agent("PRIVATE_TRANSPORT_CANARY", d.run_id, d.session_id, 1, rt, checkpointer=saver)
    assert result.reason_code == "private_context" and not d.frames
    assert await saver.aget({"configurable": {"thread_id": str(d.run_id)}}) is None
    assert "PRIVATE_TRANSPORT_CANARY" not in result.model_dump_json()


async def test_reset_suspicion_reobserves_and_increments_generation_before_action():
    d, _, model, rt = setup([planner(), OperatorTurn(disposition="action", ui=UIInterpretation(reset_suspected=True)),
                             OperatorTurn(disposition="verify"), verify(complete=True)])
    result = await run(d, rt)
    assert result.estado == "terminada" and not d.actions
    assert len(d.frames) == 2 and rt.context_snapshot.ui_generation == 1
    assert model.calls[2][2]["shared"]["ui_generation"] == 1


async def test_assignment_call_quota_is_consumed_even_without_an_action():
    def small_plan(context):
        output = planner()(context)
        assignment = output.active_assignment.model_copy(update={"budget": AssignmentBudget(seconds=20, actions=5, model_calls=1)})
        return output.model_copy(update={"active_assignment": assignment})

    d, _, model, rt = setup([small_plan, OperatorTurn(disposition="verify"), verify(status="incomplete")])
    result = await run(d, rt)
    assert result.reason_code == "milestone_budget"
    assert len(model.calls) == 3 and not d.actions


async def test_query_ui_edit_can_be_verified_without_changing_business_records():
    d, _, _, rt = setup([planner(), action("edit"), verify(changes=False),
                         verify(complete=True, resolution="applied")])
    result = await run(d, rt)
    assert result.estado == "terminada" and len(d.actions) == 1
    assert rt.context_snapshot.pending_operation is None


async def test_post_action_verifier_receives_original_before_and_after_images_transiently():
    d, recorder, model, rt = setup([planner("edit"), action("commit"), verify(changes=True),
                                   verify(complete=True, resolution="applied")])
    d.evidence_image_bytes = d.image_bytes
    saver = InMemorySaver()
    result = await run(d, rt, saver)
    assert result.estado == "terminada"
    assert model.calls[-1][2]["image_evidence_ids"] == [str(d.frames[0].id), str(d.frames[1].id)]
    snapshot = await saver.aget({"configurable": {"thread_id": str(d.run_id)}})
    assert "SYNTHETIC_IMAGE_CANARY" not in repr(snapshot) + repr(recorder.events)


async def test_changed_original_instruction_is_not_an_authorization():
    def forged(context):
        output = planner()(context)
        return output.model_copy(update={"task_contract": output.task_contract.model_copy(
            update={"original_instruction": "Delete all records"})})

    d, _, _, rt = setup([forged])
    result = await run(d, rt)
    assert result.reason_code == "scope_violation" and not d.actions
