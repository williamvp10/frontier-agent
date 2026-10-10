import asyncio
from datetime import timedelta
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from app.agents import AgentRuntime, run_agent
from app.domain.contracts import Observation, utcnow
from app.models.schemas import ModelAction, OperatorDecision, Plan, Recovery, VisualVerification

PLAN = Plan(intent="query", milestones=("Observar lista",),
            success_criteria=("Valor visible",), preservation_rules=("No cambiar negocio",))


def operator(disposition="verify", risk="navigation"):
    return OperatorDecision(disposition=disposition,
        args=ModelAction(kind="click", x=20, y=20, hold_ms=180, keys=None, text=None,
                         direction=None, amount=None) if disposition == "action" else None,
        expected_effect="Abrir lista", scope_reason="Consultar dato solicitado", risk=risk,
        identifiers=("ID-A", "ID-B") if disposition == "ambiguous" else ())


def verification(status="verified", *, changes=False, answer="45"):
    return VisualVerification(status=status, safe_to_execute=status == "verified",
        changes_business_data=changes, facts=("Conteo visible",),
        unresolved_identifiers=("ID-A", "ID-B") if status == "ambiguous" else (), answer=answer)


class FakeModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.roles = []
        self.contexts = []

    async def decide(self, role, schema, context, image):
        self.roles.append(role)
        self.contexts.append(context)
        assert image == b"private-image-canary"
        return schema.model_validate(self.outputs.pop(0))


class Recorder:
    def __init__(self):
        self.events = []

    async def append(self, role, event_type, payload):
        self.events.append((role, event_type, payload))


class Desktop:
    def __init__(self):
        self.run_id, self.session_id = uuid4(), uuid4()
        self.actions = []
        self.authorizations = []
        self.frames = []
        self.uncertain = False

    async def observe(self):
        obs = Observation(run_id=self.run_id, session_id=self.session_id, epoch=1,
            captured_at=utcnow(), width=1280, height=800, artifact_id=uuid4(),
            sha256="a" * 64, frame_revision=len(self.frames))
        self.frames.append(obs)
        return obs

    async def image_bytes(self, observation_id):
        return b"private-image-canary"

    async def authorize(self, proposal, verification):
        assert verification.status == "verified"
        assert verification.evidence_ids == (proposal.observation_id,)
        self.authorizations.append(proposal.id)

    async def execute(self, proposal):
        if proposal.risk != "navigation":
            assert proposal.id in self.authorizations
        self.actions.append(proposal)
        if self.uncertain:
            self.uncertain = False
            raise OSError("Do not log secret transport exception")


def setup(outputs, **kwargs):
    desktop, recorder, model = Desktop(), Recorder(), FakeModel(outputs)
    runtime = AgentRuntime(model=model, desktop=desktop, recorder=recorder,
                           action_cutoff_at=utcnow() + timedelta(seconds=270), **kwargs)
    return desktop, recorder, model, runtime


async def run(desktop, runtime, checkpointer=None):
    return await run_agent("Cuenta los resultados", desktop.run_id, desktop.session_id, 1,
                           runtime, checkpointer=checkpointer)


async def test_query_success_requires_verifier_and_current_evidence():
    d, events, m, rt = setup([PLAN, operator(), verification()])
    result = await run(d, rt)
    assert result.estado == "terminada"
    assert result.resumen == "respuesta: 45"
    assert result.evidence_ids == (d.frames[-1].id,)
    assert m.roles == ["orchestrator", "operator", "verifier"]
    assert not d.actions
    assert len([e for e in events.events if e[1] == "delegation"]) == 3


async def test_ambiguity_fails_without_mutation_and_visible_ids():
    d, _, _, rt = setup([PLAN, operator(), verification("ambiguous")])
    result = await run(d, rt)
    assert result.estado == "fallida"
    assert "ID-A" in result.resumen and "ID-B" in result.resumen
    assert not d.actions


async def test_budget_expired_does_not_call_model_or_desktop():
    d, _, m, rt = setup([])
    rt.action_cutoff_at = utcnow() - timedelta(seconds=1)
    result = await run(d, rt)
    assert result.reason_code == "deadline"
    assert not m.roles and not d.frames and not d.actions


async def test_precommit_verification_before_execution():
    edit_plan = PLAN.model_copy(update={"intent": "edit"})
    d, _, m, rt = setup([edit_plan, operator("action", "commit"), verification(answer=None),
                         operator(), verification(answer=None)])
    result = await run(d, rt)
    assert result.estado == "terminada"
    assert len(d.actions) == len(d.authorizations) == 1
    assert m.roles == ["orchestrator", "operator", "verifier", "operator", "verifier"]


async def test_query_business_mutation_is_rejected():
    d, _, _, rt = setup([PLAN, operator("action", "edit"), verification(changes=True),
                         Recovery(strategy="abstain", reason="Unsafe query")])
    result = await run(d, rt)
    assert result.estado == "fallida"
    assert not d.actions


async def test_uncertain_save_reobserves_before_recovery_without_blind_replay():
    d, _, m, rt = setup([PLAN.model_copy(update={"intent": "edit"}),
                         operator("action", "commit"), verification(answer=None),
                         Recovery(strategy="reobserve", reason="Reconcile visible saved state"),
                         operator(), verification(answer=None)])
    d.uncertain = True
    result = await run(d, rt)
    assert result.estado == "terminada"
    assert len(d.actions) == 1
    assert len(d.frames) >= 2
    assert "recovery" in m.roles


async def test_checkpoint_has_no_image_bytes_or_model_prompts():
    d, events, _, rt = setup([PLAN, operator(), verification()])
    checkpointer = InMemorySaver()
    await run(d, rt, checkpointer)
    snapshots = [x async for x in checkpointer.alist({"configurable": {"thread_id": str(d.run_id)}})]
    assert snapshots
    for item in snapshots:
        assert "private-image-canary" not in repr(item.checkpoint)
        assert "data:image" not in repr(item.checkpoint)
        assert "runtime" not in item.checkpoint["channel_values"]
    assert "private-image-canary" not in repr(events.events)


async def test_model_hang_times_out_without_actions():
    d, _, _, rt = setup([])
    class Hanging:
        async def decide(self, *args):
            await asyncio.sleep(60)
    rt.model = Hanging()
    rt.model_timeout_s = 0.01
    result = await run(d, rt)
    assert result.reason_code == "planning_failed"
    assert not d.actions


async def test_model_budget_stops_repeated_observations():
    d, _, m, rt = setup([PLAN, operator("blocked")], max_steps=2)
    result = await run(d, rt)
    assert result.reason_code == "model_budget"
    assert len(m.roles) == 2


async def test_unverified_success_and_query_without_answer_cannot_complete():
    d, _, _, rt = setup([PLAN, operator(), verification(answer=None)])
    result = await run(d, rt)
    assert result.estado == "fallida"
    assert result.reason_code == "missing_answer"


async def test_navigation_label_cannot_bypass_verifier_for_business_mutation():
    # A proposed click might target Save even when operator calls it navigation.
    d, _, m, rt = setup([PLAN, operator("action", "navigation"),
                        verification(changes=True),
                        Recovery(strategy="abstain", reason="Unsafe mutation")])
    result = await run(d, rt)
    assert result.estado == "fallida"
    assert not d.actions and not d.authorizations
    assert m.roles == ["orchestrator", "operator", "verifier", "recovery"]


async def test_navigation_requires_independent_verifier_before_execute():
    d, _, m, rt = setup([PLAN, operator("action", "navigation"),
                        verification(), operator(), verification()])
    result = await run(d, rt)
    assert result.estado == "terminada"
    assert len(d.actions) == len(d.authorizations) == 1
    assert m.roles == ["orchestrator", "operator", "verifier", "operator", "verifier"]


async def test_verified_page_facts_survive_navigation_and_stay_in_run_checkpoint():
    page_fact = "Página 1, filtro vencidas: registros A/B observados, cantidad 2."
    pre = verification().model_copy(update={"facts": (page_fact,)})
    complete = verification(answer="3").model_copy(update={
        "facts": ("Página 2: registro C; A/B/C sin duplicados, total 3.",)})
    d, _, m, rt = setup([PLAN, operator("action"), pre, operator(), complete])
    checkpointer = InMemorySaver()
    result = await run(d, rt, checkpointer)
    assert result.resumen == "respuesta: 3"
    assert page_fact in m.contexts[3]["known_facts"]
    assert m.contexts[3]["action_history"] == [{
        "kind": "click", "expected_effect": "Abrir lista"}]
    snapshot = await checkpointer.aget({"configurable": {"thread_id": str(d.run_id)}})
    assert page_fact in snapshot["channel_values"]["verified_facts"]
    assert "private-image-canary" not in repr(snapshot)
    # A new run starts empty even when the process and checkpointer are reused.
    other, _, another_model, another_rt = setup([PLAN, operator(), verification()])
    await run(other, another_rt, checkpointer)
    assert another_model.contexts[0]["known_facts"] == []
    assert another_model.contexts[0]["action_history"] == []


async def test_verified_facts_and_history_are_bounded_and_do_not_store_raw_action_args():
    from langgraph.runtime import Runtime

    from app.agents.graph import verifier

    output = verification().model_copy(update={"facts": tuple("x" * 700 + str(i)
                                                             for i in range(60))})
    d, _, _, rt = setup([output])
    obs = await d.observe()
    state = {"instruction": "Count", "plan": PLAN, "observation": obs,
             "run_id": d.run_id, "session_id": d.session_id, "epoch": 1,
             "verified_facts": tuple("old" + str(i) for i in range(50)),
             "precommit": False}
    delta = await verifier(state, Runtime(context=rt))
    assert len(delta["verified_facts"]) <= 40
    assert all(len(fact) <= 512 for fact in delta["verified_facts"])


async def test_action_history_keeps_only_20_brief_effects_without_coordinates():
    from langgraph.runtime import Runtime

    from app.agents.graph import execute
    from app.domain.contracts import ActionProposal, ClickArgs

    d, _, _, rt = setup([])
    obs = await d.observe()
    state = {"run_id": d.run_id, "session_id": d.session_id, "epoch": 1,
             "observation": obs, "actions": 0, "action_history": ()}
    for i in range(25):
        state["proposal"] = ActionProposal(run_id=d.run_id, session_id=d.session_id, epoch=1,
            observation_id=obs.id, args=ClickArgs(x=20, y=30), risk="navigation",
            expected_effect=f"Page {i}", scope_reason="Read requested page",
            expires_at=utcnow() + timedelta(seconds=10))
        delta = await execute(state, Runtime(context=rt))
        state.update(delta)
    assert len(state["action_history"]) == 20
    assert state["action_history"][0]["expected_effect"] == "Page 5"
    assert all(set(item) == {"kind", "expected_effect"} for item in state["action_history"])
