"""Milestone graph with private run-local snapshots. Returns a terminal candidate.

No replay/migration of an existing checkpoint. Gateway authority/closure remain
outside the model process. Every action still requires independent verification.
"""

import asyncio
from datetime import timedelta
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from app.agents.advice_context import build_advice_context
from app.agents.advice_flow import consult_then_decide
from app.agents.context import (
    ActionRecord,
    AssignmentBudget,
    Budgets,
    ContextPatch,
    CriterionProgress,
    CurrentUI,
    EvidenceReference,
    Fact,
    Milestone,
    MilestoneAssignment,
    PendingOperation,
    PendingResolution,
    Progress,
    RoleCalls,
    SharedTaskContext,
)
from app.agents.context_service import ContextError, ContextService
from app.agents.runtime import AgentRuntime
from app.core.observability import count, observed, progress
from app.domain.contracts import ActionProposal, Observation, TaskResult, Verification
from app.models.advice import FinalDecision
from app.models.jev_advisor import AdviceRejected, assert_safe
from app.models.openrouter_visual import ProviderCallError
from app.models.profiles import ROLES
from app.models.turns import CoordinatorTurn, OperatorTurn, RecoveryTurn, ReplanTurn, VerifierTurn

GRAPH_VERSION = "context-v2"


class State(TypedDict, total=False):
    instruction: str
    run_id: object
    session_id: object
    epoch: int
    graph_version: str
    profiles: dict
    observation: Observation
    shared: SharedTaskContext
    calls: int
    calls_by_role: RoleCalls
    actions: int
    recoveries: int
    proposal: ActionProposal | None
    mode: str
    verification: VerifierTurn
    requester_role: str
    questions: tuple
    last_decision: FinalDecision | None
    reobserve_generation: bool
    route: str
    result: TaskResult


class StopRun(Exception):
    pass


def terminal(state, code, summary="No se pudo completar la tarea con evidencia suficiente."):
    obs = state.get("observation")
    state.update(route="end", proposal=None, result=TaskResult(
        estado="fallida", resumen=summary, reason_code=code,
        evidence_ids=(obs.id,) if obs else ()))
    return state


def guarded(code):
    def decorate(func):
        async def node(state: State, runtime: Runtime[AgentRuntime]):
            try:
                return await func(state, runtime.context)
            except StopRun as exc:
                return terminal(state, str(exc))
            except (ContextError, AdviceRejected) as exc:
                await runtime.context.recorder.append("runtime", "context_failure", {
                    "stage": code, "context_error": exc.code})
                return terminal(state, "context_rejected")
            except ProviderCallError as exc:
                await runtime.context.recorder.append("runtime", "model_failure", {
                    "reason": code, "http_status": exc.status_code, "category": exc.category,
                    "schema_features": exc.schema_features})
                return terminal(state, code)
            except Exception:
                # Never checkpoint exception text, output payload or provider response.
                return terminal(state, code)
        node.__name__ = func.__name__
        return node
    return decorate


def envelope(source, **changes):
    return ContextPatch(run_id=source.run_id, session_id=source.session_id, epoch=source.epoch,
        base_context_version=source.context_version, observation_id=source.current_ui.observation_id,
        frame_revision=source.current_ui.frame_revision, ui_generation=source.ui_generation, **changes)


async def apply(state, rt, patch, *, role):
    async with rt.context_lock:
        source = rt.context_snapshot
        if source is None or source.run_id != state["run_id"]:
            raise StopRun("foreign_context")
        updated = ContextService().apply(source, patch, actor_role=role)
        rt.context_snapshot = updated
        state["shared"] = updated
    # Events expose references only. Full snapshot persists solely in private checkpoints.
    await rt.recorder.append(role, "context_applied", {
        "context_version": updated.context_version, "observation_id": str(updated.current_ui.observation_id)})


def require_budget(state, rt, *, model=False):
    if rt.remaining() <= 1 or ("shared" in state and state["shared"].budgets.remaining_seconds <= 1):
        raise StopRun("deadline")
    if model and state["calls"] >= rt.max_steps:
        raise StopRun("model_budget")


def budgets(state, rt):
    used = state["calls_by_role"].model_dump()
    ledger = rt.advice_session
    return Budgets(deadline_at=rt.task_deadline_at or rt.action_cutoff_at,
        remaining_seconds=max(0, min(300, rt.remaining())),
        remaining_calls=RoleCalls(**{role: max(0, min(100, rt.max_steps - used[role])) for role in ROLES}),
        remaining_actions=max(0, rt.max_actions - state["actions"]),
        remaining_recoveries=max(0, rt.max_recoveries - state["recoveries"]),
        remaining_advice_calls=max(0, ledger.max_calls - ledger.calls) if ledger else 0)


async def refresh_budget(state, rt, *, operator_call=False):
    if "shared" not in state:
        return
    old, new = state["shared"].budgets, budgets(state, rt)
    # UTC may move backwards: never increase a previously accepted remaining budget.
    data = new.model_dump()
    for name in ("remaining_seconds", "remaining_actions", "remaining_recoveries", "remaining_advice_calls"):
        data[name] = min(data[name], getattr(old, name))
    data["remaining_calls"] = {role: min(value, old.remaining_calls.model_dump()[role])
                              for role, value in data["remaining_calls"].items()}
    await apply(state, rt, envelope(state["shared"], budgets=Budgets.model_validate(data)), role="runtime")
    assignment = state["shared"].active_assignment
    if assignment:
        expires = rt.assignment_deadlines.get(assignment.id, rt.action_cutoff_at)
        quota = AssignmentBudget(seconds=max(0, min(assignment.budget.seconds, (expires - rt.now()).total_seconds())),
            actions=assignment.budget.actions,
            model_calls=max(0, assignment.budget.model_calls - int(operator_call)))
        await apply(state, rt, envelope(state["shared"], active_assignment=MilestoneAssignment.model_validate({
            **assignment.model_dump(), "budget": quota})), role="runtime")


@observed("delegate-agent", as_type="agent", role_arg=2, counter="agent_calls")
async def delegate(state, rt, role, schema, *, extra=None, refresh=True, before_observation_id=None):
    require_budget(state, rt, model=True)
    if role == "operator" and "shared" in state:
        assignment = state["shared"].active_assignment
        if assignment is None or assignment.budget.model_calls <= 0 or assignment.budget.seconds <= 1:
            raise StopRun("milestone_budget")
    state["calls"] += 1
    counts = state["calls_by_role"].model_dump()
    counts[role] += 1
    state["calls_by_role"] = RoleCalls(**counts)
    if refresh:
        await refresh_budget(state, rt, operator_call=role == "operator")
    obs = state["observation"]
    if "shared" in state:
        context = {"shared": ContextService().project(state["shared"], role=role).model_dump(mode="json")}
    else:
        context = {"instruction": state["instruction"], "run_id": str(state["run_id"]),
                   "session_id": str(state["session_id"]), "epoch": state["epoch"],
                   "observation": obs.model_dump(mode="json"), "budgets": budgets(state, rt).model_dump(mode="json")}
    context.update(graph_version=GRAPH_VERSION, mode=state.get("mode"),
                   capabilities={"advice": bool(rt.advice_session and rt.advice_session.advisor)},
                   screen={"width": obs.width, "height": obs.height}, **(extra or {}))
    if before_observation_id and hasattr(rt.desktop, "evidence_image_bytes"):
        context["image_evidence_ids"] = [str(before_observation_id), str(obs.id)]
    else:
        context["image_evidence_ids"] = [str(obs.id)]
    assert_safe(context, rt.secrets)
    await rt.recorder.append("coordinator", "delegation", {"specialist": role, "observation_id": str(obs.id)})
    profiles = getattr(rt.model, "profiles", None)
    timeout = profiles.for_role(role).timeout_seconds if profiles else rt.model_timeout_s
    async with asyncio.timeout(min(timeout, rt.remaining() - 0.5)):
        image = await rt.desktop.image_bytes(obs.id)
        if before_observation_id and hasattr(rt.desktop, "evidence_image_bytes"):
            before = await rt.desktop.evidence_image_bytes(before_observation_id)
            image = (before, image)
        output = await rt.model.decide(role, schema, context, image)
    output = schema.model_validate(output.model_dump(mode="python", warnings="none"))
    assert_safe(output.model_dump(mode="json"), rt.secrets)
    require_budget(state, rt)
    await rt.recorder.append(role, "return", {"output_type": schema.__name__})
    if hasattr(rt.model, "metadata_for"):
        metadata = rt.model.metadata_for(role)
        assert_safe(metadata, rt.secrets)
        await rt.recorder.append(role, "model_usage", metadata)
    return output


async def accept_memory(state, rt, memory, role):
    if memory is not None:
        data = memory.model_dump(exclude_none=True)
        headers = {"run_id", "session_id", "epoch", "base_context_version", "observation_id",
                   "frame_revision", "ui_generation"}
        # Empty schema-required collections are no-ops, not privileged writes.
        data = {key: value for key, value in data.items() if key in headers or value != ()}
        await apply(state, rt, ContextPatch.model_validate(data), role=role)


@guarded("observation_failed")
async def observe(state, rt):
    require_budget(state, rt)
    obs = await rt.desktop.observe()
    if (obs.run_id, obs.session_id, obs.epoch) != (state["run_id"], state["session_id"], state["epoch"]):
        raise StopRun("stale_observation")
    if "shared" in state:
        source = state["shared"]
        generation = source.ui_generation + int(state.get("reobserve_generation", False))
        reference = EvidenceReference.from_observation(obs, ui_generation=generation)
        ui = CurrentUI(observation_id=obs.id, captured_at=obs.captured_at,
                       frame_revision=obs.frame_revision, ui_generation=generation)
        changes = {"evidence": (reference,), "current_ui": ui}
        if generation != source.ui_generation:
            changes["next_ui_generation"] = generation
        await apply(state, rt, envelope(source, **changes), role="runtime")
        state["reobserve_generation"] = False
    state["observation"] = obs
    state["proposal"] = None
    if "shared" not in state:
        state["route"] = "coordinator"
    elif state["shared"].pending_operation:
        state.update(route="verifier", mode="reconcile")
    elif state.get("mode") == "effect":
        state["route"] = "verifier"
    else:
        state["route"] = "operator"
    return state


@guarded("planning_failed")
async def coordinator(state, rt):
    initial = "shared" not in state
    output = await delegate(state, rt, "coordinator", CoordinatorTurn if initial else ReplanTurn)
    if initial:
        if output.task_contract.original_instruction != state["instruction"]:
            raise StopRun("scope_violation")
        obs = state["observation"]
        source = SharedTaskContext(run_id=state["run_id"], session_id=state["session_id"], epoch=state["epoch"],
            task_contract=output.task_contract,
            current_ui=CurrentUI(observation_id=obs.id, captured_at=obs.captured_at,
                                 frame_revision=obs.frame_revision, ui_generation=0),
            evidence=(EvidenceReference.from_observation(obs, ui_generation=0),), budgets=budgets(state, rt))
        async with rt.context_lock:
            if rt.context_snapshot is not None:
                raise StopRun("foreign_context")
            rt.context_snapshot = source
            state["shared"] = source
    assignment = output.active_assignment
    if assignment:
        available = state["shared"].budgets
        # Inference consumes time after the input snapshot. Clamp proposed quotas
        # DOWN to current runtime availability; never rebase evidence or scope.
        assignment = MilestoneAssignment.model_validate({**assignment.model_dump(), "budget": AssignmentBudget(
            seconds=min(assignment.budget.seconds, available.remaining_seconds),
            actions=min(assignment.budget.actions, available.remaining_actions),
            model_calls=min(assignment.budget.model_calls, available.remaining_calls.operator))})
    await apply(state, rt, envelope(state["shared"], milestone_plan=output.milestone_plan,
                                   active_assignment=assignment), role="coordinator")
    if assignment:
        candidate = min(rt.action_cutoff_at, rt.now() + timedelta(seconds=assignment.budget.seconds))
        rt.assignment_deadlines[assignment.id] = min(rt.assignment_deadlines.get(assignment.id, candidate), candidate)
    if output.questions:
        state.update(route="consult", requester_role="coordinator", questions=output.questions)
    else:
        if state["shared"].active_assignment is None:
            raise StopRun("missing_assignment")
        state["route"] = "operator"
    return state


@guarded("operator_failed")
async def operator(state, rt):
    turn = await delegate(state, rt, "operator", OperatorTurn,
                          extra={"last_decision": state["last_decision"].model_dump(mode="json")
                                 if state.get("last_decision") else None})
    await accept_memory(state, rt, turn.patch, "operator")
    if turn.ui is not None:
        if turn.ui.reset_suspected:
            # Suspicion can invalidate, never authorize: obtain another frame first.
            state.update(route="observe", mode="navigation", proposal=None, reobserve_generation=True)
            return state
        ui = CurrentUI.model_validate({**state["shared"].current_ui.model_dump(),
                                      **turn.ui.model_dump(exclude={"reset_suspected"})})
        await apply(state, rt, envelope(state["shared"], current_ui=ui), role="runtime")
    state["last_decision"] = None
    if turn.disposition == "consult_jev":
        if not turn.questions:
            raise StopRun("invalid_advice_context")
        state.update(route="consult", requester_role="operator", questions=turn.questions)
    elif turn.disposition in {"verify", "milestone_done"}:
        state.update(route="verifier", mode="completion", proposal=None)
    elif turn.disposition == "recover":
        state.update(route="recovery", proposal=None)
    elif turn.disposition == "abstain":
        return terminal(state, "abstained")
    else:
        source, obs = state["shared"], state["observation"]
        if source.pending_operation:
            raise StopRun("pending_operation")
        if state["actions"] >= rt.max_actions:
            raise StopRun("action_budget")
        if turn.args is None or not turn.expected_effect or not turn.scope_reason:
            raise StopRun("invalid_action")
        args = turn.args.to_action()
        assignment = source.active_assignment
        if assignment is None or args.kind not in assignment.allowed_actions:
            raise StopRun("scope_violation")
        if assignment.budget.actions <= 0 or assignment.budget.seconds <= 1:
            raise StopRun("milestone_budget")
        if source.task_contract.intent == "query" and turn.risk in {"commit", "export"}:
            raise StopRun("scope_violation")
        if hasattr(args, "x") and (args.x >= obs.width or args.y >= obs.height):
            raise StopRun("invalid_coordinates")
        expires = min(obs.captured_at + timedelta(seconds=10), rt.action_cutoff_at)
        if rt.now() >= expires:
            state.update(route="observe", mode="navigation")
        else:
            state.update(proposal=ActionProposal(run_id=source.run_id, session_id=source.session_id,
                epoch=source.epoch, observation_id=obs.id, args=args, expected_effect=turn.expected_effect,
                scope_reason=turn.scope_reason, risk=turn.risk, expires_at=expires),
                route="verifier", mode="pre_action")
    return state


@guarded("verification_failed")
@observed("verify-progress")
async def verifier(state, rt):
    proposal = state.get("proposal")
    source = state["shared"]
    before_progress = (len(source.progress.satisfied_criteria), len(source.progress.useful_transition_ids))
    relevant_action = next((a for a in reversed(source.action_history)
                            if a.outcome in {"sent", "uncertain"} and a.reconciliation is None), None)
    turn = await delegate(state, rt, "verifier", VerifierTurn, extra={
        "proposed_action": proposal.model_dump(mode="json") if proposal else None,
        "affected_action": relevant_action.model_dump(mode="json") if relevant_action else None},
        before_observation_id=relevant_action.source_observation_id if relevant_action and state["mode"] != "pre_action" else None)
    await accept_memory(state, rt, turn.patch, "verifier")
    state["verification"] = turn
    report = turn.report
    if report.status == "ambiguous":
        return terminal(state, "ambiguous", "Identidad ambigua: " + "; ".join(report.unresolved_identifiers))
    new_facts = tuple(Fact(id=uuid4(), run_id=state["run_id"], assertion=text, scope="record",
                           status="independently_verified", author="verifier",
                           evidence_ids=(state["observation"].id,), ui_generation=state["shared"].ui_generation)
                      for text in report.facts) if report.status == "verified" else ()
    if new_facts:
        await apply(state, rt, envelope(state["shared"], facts=new_facts), role="verifier")
    if turn.questions:
        state.update(route="consult", requester_role="verifier", questions=turn.questions)
        return state
    if state["mode"] == "pre_action":
        if turn.satisfied_criterion_ids or turn.operation_resolution:
            raise StopRun("invalid_verification_mode")
        if not proposal or not report.safe_to_execute or report.status != "verified" or not new_facts:
            state.update(route="recovery", proposal=None)
            return state
        if source.task_contract.intent == "query" and report.changes_business_data:
            state.update(route="recovery", proposal=None)
            return state
        if rt.now() >= proposal.expires_at:
            state.update(route="observe", mode="navigation", proposal=None)
            return state
        await rt.desktop.authorize(proposal, Verification(status="verified", facts=report.facts,
            evidence_ids=(state["observation"].id,), unresolved_identifiers=()))
        state["route"] = "prepare"
        return state
    if turn.operation_resolution:
        if relevant_action is None or not new_facts or not turn.observed_effect:
            raise StopRun("invalid_reconciliation")
        fids, eids = tuple(f.id for f in new_facts), (state["observation"].id,)
        changes = {}
        if turn.operation_resolution == "applied":
            confirmed = ActionRecord.model_validate({**relevant_action.model_dump(), "outcome": "confirmed",
                "subsequent_observation_id": state["observation"].id, "observed_effect": turn.observed_effect,
                "effect_fact_ids": fids, "effect_evidence_ids": eids})
            changes["action_records"] = (confirmed,)
        if state["shared"].pending_operation:
            changes.update(clear_pending_operation=True, reconciliation_evidence_ids=eids,
                pending_resolution=PendingResolution(action_id=relevant_action.id,
                    outcome=turn.operation_resolution, evidence_ids=eids, verified_fact_ids=fids))
        if changes:
            await apply(state, rt, envelope(state["shared"], **changes), role="verifier")
    if state["shared"].pending_operation:
        state.update(route="recovery", proposal=None)
        return state
    if turn.satisfied_criterion_ids:
        if report.status != "verified" or not new_facts:
            raise StopRun("invalid_provenance")
        source = state["shared"]
        criteria = {c.id for c in source.task_contract.success_criteria}
        satisfied = {c.criterion_id: c for c in source.progress.satisfied_criteria}
        if not set(turn.satisfied_criterion_ids) <= criteria:
            raise StopRun("invalid_criterion")
        for cid in turn.satisfied_criterion_ids:
            satisfied[cid] = CriterionProgress(criterion_id=cid, fact_ids=tuple(f.id for f in new_facts),
                                               evidence_ids=(state["observation"].id,))
        await apply(state, rt, envelope(source, progress=Progress(
            satisfied_criteria=tuple(satisfied.values()), remaining_criterion_ids=tuple(
                c.id for c in source.task_contract.success_criteria if c.id not in satisfied),
            useful_transition_ids=source.progress.useful_transition_ids, no_progress_count=0)), role="verifier")
    source = state["shared"]
    after_progress = (len(source.progress.satisfied_criteria), len(source.progress.useful_transition_ids))
    progress(advanced=after_progress[0] > before_progress[0] or after_progress[1] > before_progress[1],
             completed=after_progress[0], remaining=len(source.progress.remaining_criterion_ids),
             reported=source.progress.no_progress_count)
    if not source.progress.remaining_criterion_ids and report.status == "verified":
        if source.task_contract.intent == "query" and not report.answer:
            raise StopRun("missing_answer")
        state.update(route="finalize_candidate", proposal=None, result=TaskResult(estado="terminada",
            resumen="respuesta: " + report.answer if report.answer else "Tarea completada y verificada.",
            reason_code="verified", evidence_ids=(state["observation"].id,)))
    elif report.status == "unverifiable":
        state.update(route="recovery", proposal=None)
    else:
        active = source.active_assignment
        milestone = next((m for m in source.milestone_plan if active and m.id == active.milestone_id), None)
        satisfied = {c.criterion_id for c in source.progress.satisfied_criteria}
        state.update(route="coordinator" if milestone and set(milestone.acceptance_criterion_ids) <= satisfied
                     else "operator", mode="navigation", proposal=None)
    return state


@guarded("completion_failed")
async def finalize_candidate(state, rt):
    """Coordinator's deterministic status bookkeeping, not a model permission grant."""
    require_budget(state, rt)
    source = state["shared"]
    if source.pending_operation or source.progress.remaining_criterion_ids:
        raise StopRun("incomplete_context")
    milestones = tuple(Milestone.model_validate({**m.model_dump(), "status": "completed"})
                       for m in source.milestone_plan)
    await apply(state, rt, envelope(source, milestone_plan=milestones, active_assignment=None), role="coordinator")
    state["route"] = "end"
    return state


@guarded("intent_failed")
async def prepare(state, rt):
    require_budget(state, rt)
    proposal = state["proposal"]
    if proposal is None or rt.now() >= proposal.expires_at:
        state.update(route="observe", mode="navigation", proposal=None)
        return state
    source = state["shared"]
    action = ActionRecord(id=proposal.id, run_id=source.run_id, actor="runtime",
        milestone_id=source.active_assignment.milestone_id, source_observation_id=proposal.observation_id,
        semantic_goal=proposal.scope_reason, expected_effect=proposal.expected_effect, outcome="uncertain")
    changes = {"action_records": (action,)}
    if proposal.risk != "navigation" or state["verification"].report.changes_business_data:
        changes["pending_operation"] = PendingOperation(id=uuid4(), run_id=source.run_id, action_id=action.id,
            kind="export" if proposal.risk == "export" else "mutation", expected_effect=proposal.expected_effect,
            evidence_ids=(proposal.observation_id,), reconciliation_requirement="Independently reconcile the effect")
    await apply(state, rt, envelope(source, **changes), role="runtime")
    assignment = state["shared"].active_assignment
    await apply(state, rt, envelope(state["shared"], active_assignment=MilestoneAssignment.model_validate({
        **assignment.model_dump(), "budget": AssignmentBudget(
            seconds=assignment.budget.seconds, actions=max(0, assignment.budget.actions - 1),
            model_calls=assignment.budget.model_calls)})), role="runtime")
    rt.prepared_action_ids.add(proposal.id)
    state["route"] = "execute"
    return state


@guarded("execution_failed")
async def execute(state, rt):
    require_budget(state, rt)
    proposal = state["proposal"]
    if proposal is None or proposal.id not in rt.prepared_action_ids:
        raise StopRun("replay_forbidden")
    rt.prepared_action_ids.remove(proposal.id)
    if rt.now() >= proposal.expires_at:
        raise StopRun("expired_prepared_action")
    state["actions"] += 1
    source = state["shared"]
    action = next(a for a in source.action_history if a.id == proposal.id)
    try:
        await rt.desktop.execute(proposal)
    except Exception:
        # Actual adapter owns intent durability and distinguishes uncertain send.
        if source.pending_operation is None:
            await apply(state, rt, envelope(source, pending_operation=PendingOperation(
                id=uuid4(), run_id=source.run_id, action_id=action.id, kind="mutation",
                expected_effect=action.expected_effect, evidence_ids=(proposal.observation_id,),
                reconciliation_requirement="Reobserve before any repeat")), role="runtime")
    else:
        await apply(state, rt, envelope(source, action_records=(ActionRecord.model_validate({
            **action.model_dump(), "outcome": "sent"}),)), role="runtime")
    state.update(route="observe", proposal=None, mode="effect")
    return state


@guarded("recovery_failed")
async def recovery(state, rt):
    count("recovery_escalations")
    require_budget(state, rt, model=True)
    if state["recoveries"] >= rt.max_recoveries:
        raise StopRun("recovery_budget")
    state["recoveries"] += 1
    # Always acquire NEW evidence before recovery reasoning; do not reuse pre-send screen.
    updated = await observe(state, Runtime(context=rt))
    if updated["route"] == "end":
        return updated
    turn = await delegate(state, rt, "recovery", RecoveryTurn)
    await accept_memory(state, rt, turn.patch, "recovery")
    if turn.strategy == "abstain":
        return terminal(state, "abstained")
    if turn.strategy == "consult_jev":
        if not turn.questions:
            raise StopRun("invalid_advice_context")
        state.update(route="consult", requester_role="recovery", questions=turn.questions)
    elif state["shared"].pending_operation:
        state.update(route="observe" if turn.strategy == "reobserve" else "verifier", mode="reconcile")
    else:
        state.update(route={"reobserve": "observe", "verify": "verifier", "replan": "coordinator"}[turn.strategy],
                     mode="completion")
    return state


@guarded("advice_failed")
async def consult(state, rt):
    count("jev_escalations")
    require_budget(state, rt, model=True)
    if rt.advice_session is None:
        raise StopRun("missing_advice_session")
    role = state["requester_role"]
    request, binding = build_advice_context(state["shared"], requester_role=role,
        questions=state["questions"], remaining_seconds=rt.remaining(), secrets=rt.secrets)

    async def decide(original, advice):
        return await delegate(state, rt, role, FinalDecision, refresh=False, extra={
            "advice_request": original.model_dump(mode="json"), "advice_result": advice.model_dump(mode="json")})

    def latest_binding():
        return build_advice_context(rt.context_snapshot, requester_role=role, questions=state["questions"],
                                    remaining_seconds=rt.remaining(), secrets=rt.secrets)[1]

    before_final = state["calls"]
    result = await consult_then_decide(request, binding=binding, session=rt.advice_session,
        decide=decide, latest_binding=latest_binding,
        model_timeout_seconds=rt.model.profiles.for_role(role).timeout_seconds
        if hasattr(rt.model, "profiles") else rt.model_timeout_s)
    await refresh_budget(state, rt, operator_call=role == "operator" and state["calls"] > before_final)
    await rt.recorder.append(role, "advice_return", {"status": result.advice.status,
        "reason_code": result.reason_code, "input_tokens": result.advice.input_tokens,
        "output_tokens": result.advice.output_tokens, "cost_usd": result.advice.cost_usd})
    if result.decision is None:
        state.update(route="observe", mode="navigation", questions=())
        return state
    state["last_decision"] = result.decision
    next_step = result.decision.next_step
    if next_step == "abstain":
        return terminal(state, "abstained")
    state.update(route={"propose_action": "operator", "reobserve": "observe", "verify": "verifier",
                        "recover": "recovery"}[next_step], questions=(),
                 mode="reconcile" if state["shared"].pending_operation else "completion")
    return state


def build_graph(checkpointer=None):
    graph = StateGraph(State, context_schema=AgentRuntime)
    names = {"observe": observe, "coordinator": coordinator, "operator": operator, "verifier": verifier,
             "prepare": prepare, "execute": execute, "recovery": recovery, "consult": consult,
             "finalize_candidate": finalize_candidate}
    for name, node in names.items():
        graph.add_node(name, node)
        graph.add_conditional_edges(name, lambda s: s["route"], {**{n: n for n in names}, "end": END})
    graph.add_edge(START, "observe")
    return graph.compile(checkpointer=checkpointer)


async def run_agent(instruction, run_id, session_id, epoch, runtime, *, checkpointer=None):
    config = {"configurable": {"thread_id": str(run_id)}, "recursion_limit": 500, "callbacks": []}
    try:
        assert_safe(instruction, runtime.secrets)
    except AdviceRejected:
        return TaskResult(estado="fallida", resumen="Instrucción con contenido privado no permitido.",
                          reason_code="private_context")
    # No automatic migration/resume: a restarted run must first reconcile ownership
    # and pending effects. Reading this checkpoint never creates another action.
    if checkpointer is not None and await checkpointer.aget_tuple(config) is not None:
        return TaskResult(estado="fallida", resumen="Run previa requiere reconciliación antes de continuar.",
                          reason_code="checkpoint_requires_reconciliation")
    if runtime.context_snapshot is not None:
        return TaskResult(estado="fallida", resumen="Contexto de otra ejecución rechazado.", reason_code="foreign_context")
    profiles = getattr(runtime.model, "profiles", None)
    with tracing_context(enabled=False):
        state = await build_graph(checkpointer).ainvoke({"instruction": instruction, "run_id": run_id,
            "session_id": session_id, "epoch": epoch, "graph_version": GRAPH_VERSION,
            "profiles": profiles.model_dump(mode="json") if profiles else {},
            "calls": 0, "calls_by_role": RoleCalls(), "actions": 0, "recoveries": 0,
            "proposal": None, "last_decision": None}, context=runtime, config=config)
    return state["result"]
