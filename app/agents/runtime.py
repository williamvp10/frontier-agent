import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.contracts import ActionProposal, Observation, Verification, utcnow
from app.models.openai_visual import VisualModel


class DesktopPort(Protocol):
    async def observe(self) -> Observation: ...
    async def image_bytes(self, observation_id: UUID) -> bytes: ...
    async def evidence_image_bytes(self, observation_id: UUID) -> bytes: ...
    async def execute(self, proposal: ActionProposal) -> None: ...
    async def authorize(self, proposal: ActionProposal, verification: Verification) -> None: ...


class EventRecorder(Protocol):
    async def append(self, role: str, event_type: str, payload: dict) -> None: ...


@dataclass
class AgentRuntime:
    model: VisualModel
    desktop: DesktopPort
    recorder: EventRecorder
    action_cutoff_at: datetime
    max_steps: int = 40
    max_actions: int = 60
    max_recoveries: int = 2
    model_timeout_s: float = 20
    proposal_ttl_s: float = 30
    now: object = utcnow
    graph_version: str = "v1"
    task_deadline_at: datetime | None = None
    advice_session: object | None = None
    secrets: tuple[str, ...] = field(default=(), repr=False)
    context_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    context_snapshot: object | None = field(default=None, repr=False)
    prepared_action_ids: set = field(default_factory=set, repr=False)
    assignment_deadlines: dict = field(default_factory=dict, repr=False)

    def remaining(self) -> float:
        return (self.action_cutoff_at - self.now()).total_seconds()
