"""Explicit multiagent delegation. The runtime is deliberately outside checkpoint state.

This returns a terminal *candidate*. The worker must revoke, close and confirm VNC
before publishing it. DesktopPort owns wire-level fencing and the independent watchdog.
"""
import asyncio
from datetime import timedelta
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agents.runtime import AgentRuntime
from app.core.observability import count, observed, progress
from app.domain.contracts import ActionProposal, Observation, TaskResult, Verification
from app.models.schemas import OperatorDecision, Plan, Recovery, VisualVerification


class AgentState(TypedDict, total=False):
    instruction: str
    run_id: UUID
    session_id: UUID
    epoch: int
    observation: Observation
    plan: Plan
    proposal: ActionProposal | None
    verification: VisualVerification
    calls: int
    actions: int
    recoveries: int
    precommit: bool
    route: str
    result: TaskResult
    verified_facts: tuple[str, ...]
    action_history: tuple[dict[str, str], ...]


def failed(code: str, summary: str, state: AgentState) -> dict:
    observation = state.get("observation")
    return {"route": "end", "proposal": None, "result": TaskResult(
        estado="fallida", resumen=summary, reason_code=code,
        evidence_ids=(observation.id,) if observation else (),
    )}


def budget(state: AgentState, rt: AgentRuntime, *, model=False) -> dict | None:
    if rt.remaining() <= 1:
        return failed("deadline", "Presupuesto de tiempo agotado.", state)
    if model and state.get("calls", 0) >= rt.max_steps:
        return failed("model_budget", "Presupuesto de llamadas agotado.", state)
    return None


@observed("delegate-agent", as_type="agent", role_arg=0, counter="agent_calls")
async def delegate(role, schema, state, rt, extra=None):
    obs = state["observation"]
    context = {
        "instruction": state["instruction"],
        "screen": {"width": obs.width, "height": obs.height},
        "plan": state["plan"].model_dump(mode="json") if state.get("plan") else None,
        "known_facts": list(state.get("verified_facts", ())),
        "action_history": list(state.get("action_history", ())),
        "last_verification": state["verification"].model_dump(mode="json")
        if state.get("verification") else None,
        **(extra or {}),
    }
    # Never record prompts, arguments, image bytes, generated text or exception messages.
    await rt.recorder.append("orchestrator", "delegation", {
        "specialist": role, "observation_id": str(obs.id),
    })
    async with asyncio.timeout(min(rt.model_timeout_s, rt.remaining() - 0.5)):
        image = await rt.desktop.image_bytes(obs.id)
        output = await rt.model.decide(role, schema, context, image)
    await rt.recorder.append(role, "return", {"output_type": schema.__name__})
    return output


async def observe(state: AgentState, runtime: Runtime[AgentRuntime]):
    rt = runtime.context
    if stop := budget(state, rt):
        return stop
    try:
        obs = await rt.desktop.observe()
    except Exception:  # noqa: BLE001 — fail closed across external adapters; never log payloads.
        return failed("observation_failed", "No se pudo observar el escritorio.", state)
    if (obs.run_id, obs.session_id, obs.epoch) != (
            state["run_id"], state["session_id"], state["epoch"]):
        return failed("stale_observation", "Observación de otra sesión rechazada.", state)
    return {"observation": obs, "proposal": None,
            "route": "orchestrator" if not state.get("plan") else "operator"}


async def orchestrator(state: AgentState, runtime: Runtime[AgentRuntime]):
    rt = runtime.context
    if stop := budget(state, rt, model=True):
        return stop
    try:
        plan = await delegate("orchestrator", Plan, state, rt)
    except Exception:  # noqa: BLE001 — fail closed across external adapters; never log payloads.
        return failed("planning_failed", "No se pudo elaborar un plan válido.", state)
    return {"plan": plan, "calls": state.get("calls", 0) + 1, "route": "operator"}


async def operator(state: AgentState, runtime: Runtime[AgentRuntime]):
    rt = runtime.context
    if stop := budget(state, rt, model=True):
        return stop
    calls = state.get("calls", 0) + 1
    try:
        decision = await delegate("operator", OperatorDecision, state, rt)
    except Exception:  # noqa: BLE001 — fail closed across external adapters; never log payloads.
        return failed("operator_failed", "El operador no devolvió una decisión válida.", state)
    if decision.disposition == "ambiguous":
        return {**failed("ambiguous", "Identidad ambigua: " +
                        "; ".join(decision.identifiers), state), "calls": calls}
    if decision.disposition == "blocked":
        return {"route": "recovery", "calls": calls, "proposal": None}
    if decision.disposition == "verify":
        return {"route": "verifier", "calls": calls, "precommit": False, "proposal": None}
    if decision.args is None or not decision.expected_effect or not decision.scope_reason:
        return failed("invalid_action", "Propuesta incompleta rechazada.", state)
    if state.get("actions", 0) >= rt.max_actions:
        return failed("action_budget", "Presupuesto de acciones agotado.", state)
    try:
        args = decision.args.to_action()
    except ValueError:
        return failed("invalid_action", "Argumentos de acción inválidos.", state)
    obs = state["observation"]
    if hasattr(args, "x") and not (args.x < obs.width and args.y < obs.height):
        return failed("invalid_coordinates", "Coordenadas fuera de la pantalla.", state)
    # Freshness is measured from capture, NOT from the end of model inference.
    expires = min(obs.captured_at + timedelta(seconds=10), rt.action_cutoff_at)
    if rt.now() >= expires:
        return {"route": "observe", "calls": calls, "proposal": None}
    proposal = ActionProposal(
        run_id=state["run_id"], session_id=state["session_id"], epoch=state["epoch"],
        observation_id=obs.id, args=args, expected_effect=decision.expected_effect,
        scope_reason=decision.scope_reason, risk=decision.risk, expires_at=expires,
    )
    return {"proposal": proposal, "calls": calls,
            "precommit": True,
            "route": "verifier"}


async def verifier(state: AgentState, runtime: Runtime[AgentRuntime]):
    rt = runtime.context
    if stop := budget(state, rt, model=True):
        return stop
    try:
        proposal = state.get("proposal")
        output = await delegate("verifier", VisualVerification, state, rt, {
            "mode": "pre_action" if state.get("precommit") else "completion",
            "proposed_action": proposal.model_dump(mode="json") if proposal else None,
        })
    except Exception:  # noqa: BLE001 — fail closed across external adapters; never log payloads.
        return failed("verification_failed", "No se pudo verificar la evidencia.", state)
    # Only independent verification contributes facts. Bound size and deduplicate in-run.
    facts = state.get("verified_facts", ())
    if output.status == "verified":
        facts = tuple(dict.fromkeys((*facts, *(fact[:512] for fact in output.facts))))[-40:]
    base = {"verification": output, "calls": state.get("calls", 0) + 1,
            "verified_facts": facts}
    if not state.get("precommit"):
        progress(advanced=output.status == "verified" and bool(output.facts))
    if output.status == "ambiguous":
        return {**base, **failed("ambiguous", "Identidad ambigua: " +
                               "; ".join(output.unresolved_identifiers), state)}
    if state.get("precommit"):
        if (not output.safe_to_execute or output.status != "verified" or not output.facts
                or (state["plan"].intent == "query" and output.changes_business_data)):
            return {**base, "route": "recovery", "proposal": None}
        try:
            await rt.desktop.authorize(proposal, Verification(
                status="verified", evidence_ids=(state["observation"].id,),
                facts=output.facts, unresolved_identifiers=output.unresolved_identifiers,
            ))
        except Exception:  # noqa: BLE001 — fail closed across external adapters; never log payloads.
            return {**base, **failed("authorization_failed", "Acción rechazada por el gateway.", state)}
        return {**base, "route": "execute"}
    if output.status == "verified" and output.facts:
        if state["plan"].intent == "query" and not output.answer:
            return {**base, **failed("missing_answer", "Consulta sin respuesta verificable.", state)}
        summary = "respuesta: " + output.answer if output.answer else "; ".join(output.facts)
        return {**base, "route": "end", "result": TaskResult(
            estado="terminada", resumen=summary, evidence_ids=(state["observation"].id,),
            reason_code="verified",
        )}
    return {**base, "route": "recovery" if output.status == "unverifiable" else "operator"}


async def execute(state: AgentState, runtime: Runtime[AgentRuntime]):
    rt = runtime.context
    if stop := budget(state, rt):
        return stop
    proposal = state.get("proposal")
    if proposal is None:
        return failed("missing_action", "Falta una propuesta válida.", state)
    if rt.now() >= proposal.expires_at:
        return {"route": "observe", "proposal": None}
    history = (*state.get("action_history", ()), {
        "kind": proposal.kind, "expected_effect": proposal.expected_effect[:512],
    })[-20:]
    try:
        await rt.desktop.execute(proposal)
    except Exception:  # noqa: BLE001 — fail closed across external adapters; never log payloads.
        # The action may already have been sent; never retain it for blind replay.
        return {"route": "recovery", "proposal": None,
                "actions": state.get("actions", 0) + 1, "action_history": history}
    return {"route": "observe", "proposal": None,
            "actions": state.get("actions", 0) + 1, "action_history": history}


async def recovery(state: AgentState, runtime: Runtime[AgentRuntime]):
    rt = runtime.context
    count("recovery_escalations")
    if stop := budget(state, rt, model=True):
        return stop
    if state.get("recoveries", 0) >= rt.max_recoveries:
        return failed("recovery_budget", "No se pudo recuperar con evidencia suficiente.", state)
    # Recovery analyzes a NEW frame, never the pre-action frame of an uncertain save.
    try:
        obs = await rt.desktop.observe()
        if (obs.run_id, obs.session_id, obs.epoch) != (
                state["run_id"], state["session_id"], state["epoch"]):
            return failed("stale_observation", "Observación de otra sesión rechazada.", state)
        current = {**state, "observation": obs, "proposal": None}
        output = await delegate("recovery", Recovery, current, rt)
    except Exception:  # noqa: BLE001 — fail closed across external adapters; never log payloads.
        return failed("recovery_failed", "La recuperación no produjo evidencia válida.", state)
    base = {"observation": obs, "proposal": None, "calls": state.get("calls", 0) + 1,
            "recoveries": state.get("recoveries", 0) + 1}
    if output.strategy == "abstain":
        return {**base, **failed("abstained", "No hay evidencia suficiente para continuar.", current)}
    if output.strategy == "replan":
        return {**base, "plan": None, "route": "orchestrator"}
    return {**base, "route": "operator"}


def build_graph(checkpointer=None):
    graph = StateGraph(AgentState, context_schema=AgentRuntime)
    for name, node in {"observe": observe, "orchestrator": orchestrator,
                       "operator": operator, "verifier": verifier,
                       "execute": execute, "recovery": recovery}.items():
        graph.add_node(name, node)
        graph.add_conditional_edges(name, lambda s: s["route"], {
            "observe": "observe", "orchestrator": "orchestrator", "operator": "operator",
            "verifier": "verifier", "execute": "execute", "recovery": "recovery", "end": END,
        })
    graph.add_edge(START, "observe")
    return graph.compile(checkpointer=checkpointer)


async def run_agent(instruction: str, run_id: UUID, session_id: UUID, epoch: int,
                     runtime: AgentRuntime, *, checkpointer=None) -> TaskResult:
    if runtime.graph_version == "context-v2":
        from app.agents.graph_v2 import run_agent as run_v2

        return await run_v2(instruction, run_id, session_id, epoch, runtime, checkpointer=checkpointer)
    if runtime.graph_version != "v1":
        return TaskResult(estado="fallida", resumen="Versión de grafo no soportada.", reason_code="graph_version")
    state = await build_graph(checkpointer).ainvoke(
        {"instruction": instruction, "run_id": run_id, "session_id": session_id,
         "epoch": epoch, "calls": 0, "actions": 0, "recoveries": 0,
         "verified_facts": (), "action_history": ()},
        context=runtime,
        config={"configurable": {"thread_id": str(run_id)}, "recursion_limit": 500},
    )
    return state["result"]
