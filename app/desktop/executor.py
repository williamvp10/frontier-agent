import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from app.core.observability import count, observed
from app.core.timing import ActionDeadline
from app.domain.contracts import ActionProposal, Observation, Verification, utcnow


class ActionRejected(RuntimeError):
    pass


class ActionUncertain(RuntimeError):
    pass


class DesktopSessionAdapter:
    def __init__(
        self,
        gateway,
        session_repository,
        evidence_repository,
        *,
        run_id,
        session_id,
        epoch,
        cutoff_at,
        artifact_dir: Path,
    ):
        self.gateway, self.sessions, self.evidence = (
            gateway,
            session_repository,
            evidence_repository,
        )
        self.run_id, self.session_id, self.epoch = run_id, session_id, epoch
        self.deadline = ActionDeadline(cutoff_at)
        self.artifact_dir = artifact_dir / str(run_id)
        self.latest = None
        self._image = None
        self._authorized = set()
        self._attempted = set()
        self._revision = 0
        self._known_artifacts = {}

    @observed("observe-vnc", as_type="tool", counter="vnc_calls")
    async def observe(self) -> Observation:
        self.deadline.require()
        if not await self.sessions.validate(self.session_id, self.epoch):
            raise ActionRejected("Session revoked")
        frame = await self.gateway.request("observe")
        self.reconnected_on_last_observation = frame.get("capture_reconnected", False)
        self.deadline.require()
        artifact_id = uuid4()
        self.artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.artifact_dir / f"{artifact_id}.png"
        # Exclusive file creation with restricted permissions.
        import os

        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(frame["image"])
        observation = Observation(
            run_id=self.run_id,
            session_id=self.session_id,
            epoch=self.epoch,
            frame_revision=self._revision,
            captured_at=utcnow(),
            width=frame["width"],
            height=frame["height"],
            artifact_id=artifact_id,
            sha256=hashlib.sha256(frame["image"]).hexdigest(),
        )
        await self.evidence.save_observation(
            observation,
            storage_key=str(path),
            byte_size=len(frame["image"]),
            expires_at=utcnow() + timedelta(hours=24),
        )
        if self.latest and self.latest.sha256 == observation.sha256:
            count("repeated_frames")
        self.latest, self._image = observation, frame["image"]
        self._known_artifacts[observation.id] = observation.artifact_id
        self._authorized.clear()
        return observation

    async def image_bytes(self, observation_id: UUID) -> bytes:
        if self.latest is None or observation_id != self.latest.id:
            raise ActionRejected("Stale observation")
        return self._image

    async def evidence_image_bytes(self, observation_id: UUID) -> bytes:
        """Only registered screenshots of THIS adapter/run; never arbitrary paths."""
        artifact_id = self._known_artifacts.get(observation_id)
        if artifact_id is None:
            raise ActionRejected("Foreign or unavailable screenshot reference")
        if self.latest and observation_id == self.latest.id:
            return self._image
        return (self.artifact_dir / f"{artifact_id}.png").read_bytes()

    async def authorize(self, proposal: ActionProposal, verification: Verification):
        self._validate(proposal)
        if (
            verification.status != "verified"
            or proposal.observation_id not in verification.evidence_ids
        ):
            raise ActionRejected("Mutation lacks current verification")
        self._authorized.add(proposal.id)

    async def confirm_effect(self, action_id: UUID, observation_id: UUID):
        """Persist independently verified effects; this never sends desktop input."""
        if (action_id not in self._attempted or self.latest is None
                or observation_id != self.latest.id
                or not await self.sessions.validate(self.session_id, self.epoch)):
            raise ActionRejected("Foreign or stale effect evidence")
        await self.evidence.mark_action_verified(action_id, self.run_id, observation_id)

    def _validate(self, proposal: ActionProposal):
        self.deadline.require(3 + 2 * len(getattr(proposal.args, "text_targets", ())))
        if proposal.kind == "replace_text" and proposal.risk == "navigation":
            raise ActionRejected("Text replacement cannot be navigation")
        if (
            proposal.run_id != self.run_id
            or proposal.session_id != self.session_id
            or proposal.epoch != self.epoch
            or self.latest is None
            or proposal.observation_id != self.latest.id
            or self.latest.frame_revision != self._revision
            or proposal.expires_at.tzinfo is None
            or proposal.expires_at <= utcnow()
            or proposal.id in self._attempted
        ):
            raise ActionRejected("Stale, duplicate or foreign proposal")
        if getattr(proposal.args, "x", None) is not None:
            if proposal.args.x >= self.latest.width or proposal.args.y >= self.latest.height:
                raise ActionRejected("Coordinates outside framebuffer")
        if any(t.x >= self.latest.width or t.y >= self.latest.height
               for t in getattr(proposal.args, "text_targets", ())):
            raise ActionRejected("Coordinates outside framebuffer")

    @observed("execute-vnc", as_type="tool", counter="action_attempts")
    async def execute(self, proposal: ActionProposal):
        self._validate(proposal)
        if proposal.risk != "navigation" and proposal.id not in self._authorized:
            raise ActionRejected("Unverified mutation")
        if not await self.sessions.validate(self.session_id, self.epoch):
            raise ActionRejected("Session revoked")
        recorded = await self.evidence.record_intent(proposal)
        if not recorded:
            raise ActionRejected("Duplicate proposal")
        self._attempted.add(proposal.id)
        self._revision += 1
        self._authorized.clear()
        try:
            count("vnc_calls")
            await self.gateway.request("execute", args=proposal.args.model_dump(mode="json"))
            await self.evidence.mark_action_sent(proposal.id)
        except BaseException:
            # Best effort recording; never retry external side effect automatically.
            try:
                await self.evidence.mark_action_uncertain(proposal.id)
            except Exception:
                pass
            raise ActionUncertain("Action outcome uncertain; observe before replanning") from None
