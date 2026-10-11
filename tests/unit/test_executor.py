from datetime import timedelta
from uuid import uuid4

import pytest

from app.desktop.executor import ActionRejected, ActionUncertain, DesktopSessionAdapter
from app.domain.contracts import ActionProposal, ClickArgs, TextArgs, Verification, utcnow

pytestmark = pytest.mark.unit


class Session:
    active = True

    async def validate(self, *args):
        return self.active


class Evidence:
    def __init__(self):
        self.intents = []
        self.uncertain = []
        self.observations = []

    async def save_observation(self, obs, **kw):
        self.observations.append(obs)

    async def record_intent(self, proposal):
        self.intents.append(proposal.id)
        return True

    async def mark_action_sent(self, id):
        pass

    async def mark_action_uncertain(self, id):
        self.uncertain.append(id)


class Gateway:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def request(self, kind, **fields):
        self.calls.append(kind)
        if kind == "observe":
            return {"image": b"synthetic-frame", "width": 1280, "height": 800}
        if self.fail:
            raise ConnectionError("connection-lost")
        return {"ok": True}


async def make(tmp_path):
    gateway = Gateway()
    evidence = Evidence()
    session = Session()
    desktop = DesktopSessionAdapter(
        gateway,
        session,
        evidence,
        run_id=uuid4(),
        session_id=uuid4(),
        epoch=1,
        cutoff_at=utcnow() + timedelta(seconds=60),
        artifact_dir=tmp_path,
    )
    obs = await desktop.observe()
    return desktop, gateway, evidence, session, obs


def proposal(d, obs, **overrides):
    fields = dict(
        run_id=d.run_id,
        session_id=d.session_id,
        epoch=1,
        observation_id=obs.id,
        args=ClickArgs(x=10, y=10),
        expected_effect="Open visible form",
        scope_reason="Task scope",
        risk="navigation",
        expires_at=utcnow() + timedelta(seconds=5),
    )
    return ActionProposal(**(fields | overrides))


async def test_mutation_needs_verified_current_evidence(tmp_path):
    d, g, e, s, obs = await make(tmp_path)
    p = proposal(d, obs, risk="commit")
    with pytest.raises(ActionRejected):
        await d.execute(p)
    assert not e.intents and g.calls == ["observe"]
    with pytest.raises(ActionRejected):
        await d.authorize(p, Verification(status="verified", evidence_ids=(uuid4(),)))
    await d.authorize(p, Verification(status="verified", evidence_ids=(obs.id,)))
    await d.execute(p)
    assert e.intents == [p.id] and g.calls[-1] == "execute"


async def test_stale_foreign_out_of_bounds_and_duplicate_are_never_sent(tmp_path):
    d, g, e, s, obs = await make(tmp_path)
    for p in [
        proposal(d, obs, epoch=2),
        proposal(d, obs, run_id=uuid4()),
        proposal(d, obs, args=ClickArgs(x=1280, y=0)),
        proposal(d, obs, expires_at=utcnow() - timedelta(seconds=1)),
    ]:
        with pytest.raises(ActionRejected):
            await d.execute(p)
    p = proposal(d, obs)
    await d.execute(p)
    with pytest.raises(ActionRejected):
        await d.execute(p)
    assert g.calls.count("execute") == 1


async def test_uncertain_send_cannot_replay_even_after_new_capture(tmp_path):
    d, g, e, s, obs = await make(tmp_path)
    p = proposal(d, obs)
    g.fail = True
    with pytest.raises(ActionUncertain):
        await d.execute(p)
    assert e.uncertain == [p.id]
    await d.observe()
    with pytest.raises(ActionRejected):
        await d.execute(p)
    assert g.calls.count("execute") == 1


async def test_revoked_session_has_no_wire_action(tmp_path):
    d, g, e, s, obs = await make(tmp_path)
    s.active = False
    with pytest.raises(ActionRejected):
        await d.execute(proposal(d, obs))
    assert not e.intents and g.calls == ["observe"]


async def test_historical_images_are_private_run_registered_references_only(tmp_path):
    first, _, _, _, before = await make(tmp_path)
    await first.observe()
    assert await first.evidence_image_bytes(before.id) == b"synthetic-frame"
    with pytest.raises(ActionRejected):
        await first.image_bytes(before.id)
    second, _, _, _, foreign = await make(tmp_path)
    with pytest.raises(ActionRejected):
        await second.evidence_image_bytes(before.id)
    with pytest.raises(ActionRejected):
        await first.evidence_image_bytes(foreign.id)


async def test_targeted_text_outside_frame_never_records_intent_or_sends(tmp_path):
    d, g, e, _, obs = await make(tmp_path)
    p = proposal(d, obs, risk="edit", args=TextArgs(text="Synthetic", x=1280, y=0))
    with pytest.raises(ActionRejected):
        await d.authorize(p, Verification(status="verified", evidence_ids=(obs.id,)))
    with pytest.raises(ActionRejected):
        await d.execute(p)
    assert not e.intents and g.calls == ["observe"]
