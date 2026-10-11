"""UI interpretation regressions, synthetic evidence only; no provider or desktop."""

from datetime import timedelta
from uuid import uuid4

import pytest
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.agents.context_service import ContextService
from app.agents.graph_v2 import UIRejected, envelope, grounded_ui
from app.models.turns import OperatorTurn, RecoveryTurn, UIControlProposal, UIInterpretation
from tests.unit.test_context_service import map_snapshot
from tests.unit.test_graph_v2 import planner, run, setup, verify

pytestmark = pytest.mark.unit


def proposal(source, **updates):
    control = source.current_ui.visible_controls[0]
    value = UIControlProposal.model_validate(control.model_dump(exclude={"run_id", "observation_id", "ui_generation"}))
    return UIInterpretation(view_id=source.current_ui.view_id, visible_controls=(value,), **updates)


def observation(source):
    from app.domain.contracts import Observation

    ui = source.current_ui
    return Observation(id=ui.observation_id, run_id=source.run_id, session_id=source.session_id,
        epoch=source.epoch, frame_revision=ui.frame_revision, captured_at=ui.captured_at,
        width=800, height=600, artifact_id=source.evidence[-1].artifact_id, sha256="0" * 64)


def test_runtime_owns_control_provenance_and_nullable_empty_ui_is_normalized():
    source, _ = map_snapshot()
    before = source.model_dump_json()
    interpreted = proposal(source, title="", expanded_menu="", active_tab="", modal="", visible_error="")
    ui = grounded_ui(source, interpreted, observation(source))
    assert ui.title is None and ui.visible_error is None and ui.modal is None
    assert ui.visible_controls[0].run_id == source.run_id
    assert ui.visible_controls[0].observation_id == source.current_ui.observation_id
    assert ui.visible_controls[0].ui_generation == source.ui_generation
    assert ui.visible_controls[0].label == source.current_ui.visible_controls[0].label
    accepted = ContextService().apply(source, envelope(source, current_ui=ui), actor_role="runtime")
    assert accepted.current_ui == ui and source.model_dump_json() == before


def test_model_control_schema_has_no_transport_authority_fields():
    source, _ = map_snapshot()
    value = proposal(source).visible_controls[0]
    with pytest.raises(ValidationError):
        UIControlProposal.model_validate({**value.model_dump(), "observation_id": uuid4()})
    assert not {"run_id", "observation_id", "epoch", "ui_generation"} & set(UIControlProposal.model_fields)


@pytest.mark.parametrize("fault,code", [
    ("focus", "unknown_focus_control"), ("view", "unknown_ui_view"),
    ("control_view", "unknown_control_view"), ("duplicate", "duplicate_control_id"),
    ("bounds", "control_outside_frame"), ("frame", "stale_ui_frame"),
])
def test_invalid_ui_diagnostics_are_static_and_original_snapshot_is_intact(fault, code):
    source, _ = map_snapshot()
    ui, obs = proposal(source), observation(source)
    before = source.model_dump_json()
    if fault == "focus":
        ui = ui.model_copy(update={"focused_control_id": uuid4()})
    elif fault == "view":
        ui = ui.model_copy(update={"view_id": uuid4()})
    elif fault == "control_view":
        ui = ui.model_copy(update={"visible_controls": (ui.visible_controls[0].model_copy(update={"view_id": uuid4()}),)})
    elif fault == "duplicate":
        ui = ui.model_copy(update={"visible_controls": ui.visible_controls * 2})
    elif fault == "bounds":
        control = ui.visible_controls[0]
        box = control.bounding_box.model_copy(update={"x": 800})
        ui = ui.model_copy(update={"visible_controls": (control.model_copy(update={"bounding_box": box}),)})
    else:
        obs = obs.model_copy(update={"id": uuid4()})
    with pytest.raises(UIRejected, match=code):
        grounded_ui(source, ui, obs)
    assert source.model_dump_json() == before


async def test_bad_ui_proposal_recovers_from_new_evidence_without_sending_action():
    bad = OperatorTurn(disposition="action", ui=UIInterpretation(focused_control_id=uuid4()))
    d, recorder, model, rt = setup([planner(), bad,
        RecoveryTurn(strategy="verify", brief_reason="Recheck current evidence after invalid UI metadata"),
        verify(complete=True)])
    result = await run(d, rt)
    assert result.estado == "terminada" and not d.actions and not d.approvals
    assert len(d.frames) == 2
    assert [role for role, _, _ in model.calls] == ["coordinator", "operator", "recovery", "verifier"]
    assert any(kind == "ui_proposal_rejected" and payload["reason_code"] == "unknown_focus_control"
               for _, kind, payload in recorder.events)


async def test_planning_latency_refreshes_screen_before_first_operator_call(monkeypatch):
    from app.agents import graph_v2
    from app.agents.context import RoleCalls

    d, _, _, rt = setup([])
    obs = await d.observe()
    state = {"instruction": "Read requested value", "run_id": d.run_id, "session_id": d.session_id,
             "epoch": 1, "observation": obs, "calls": 1, "calls_by_role": RoleCalls(coordinator=1),
             "actions": 0, "recoveries": 0}
    rt.now = lambda: obs.captured_at + timedelta(seconds=5)
    planned = planner()({"run_id": str(d.run_id), "instruction": state["instruction"]})

    async def prepared_output(*args, **kwargs):
        return planned

    monkeypatch.setattr(graph_v2, "delegate", prepared_output)
    result = await graph_v2.coordinator(state, Runtime(context=rt))
    assert result["route"] == "observe" and result["mode"] == "navigation"
    assert not d.actions
