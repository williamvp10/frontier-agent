import time
from collections.abc import Callable
from datetime import datetime, timedelta

from app.domain.contracts import TaskTiming, utcnow


def timing_for(accepted_at: datetime, *, cutoff=270.0, close=285.0, deadline=300.0):
    return TaskTiming(
        accepted_at=accepted_at,
        action_cutoff_at=accepted_at + timedelta(seconds=cutoff),
        close_target_at=accepted_at + timedelta(seconds=close),
        deadline_at=accepted_at + timedelta(seconds=deadline),
    )


class ActionDeadline:
    """Use both clocks: queue time is consumed, backward wall jumps cannot extend a run."""

    def __init__(
        self, cutoff_at: datetime, *, now: Callable = utcnow, monotonic: Callable = time.monotonic
    ):
        self.cutoff_at = cutoff_at
        self.now = now
        self.monotonic = monotonic
        self.monotonic_cutoff = monotonic() + max(0, (cutoff_at - now()).total_seconds())

    def remaining(self) -> float:
        return max(
            0.0,
            min(
                (self.cutoff_at - self.now()).total_seconds(),
                self.monotonic_cutoff - self.monotonic(),
            ),
        )

    def require(self, seconds: float = 0.0):
        if self.remaining() <= seconds:
            raise TimeoutError("Action deadline reached")
