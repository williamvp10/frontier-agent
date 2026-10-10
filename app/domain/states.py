from app.domain.contracts import PublicState

_ALLOWED = {
    "queued": {"running", "closing"},
    "running": {"closing"},
    "closing": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}


def transition(current: str, target: str) -> str:
    if target not in _ALLOWED.get(current, set()):
        raise ValueError("Illegal task state transition")
    return target


def public_state(internal: str) -> PublicState:
    if internal not in _ALLOWED:
        raise ValueError("Unknown task state")
    return {"succeeded": PublicState.SUCCEEDED, "failed": PublicState.FAILED}.get(
        internal, PublicState.RUNNING
    )
