"""Explicit, metadata-only Langfuse boundary; never serialize application objects.

Manual observations deliberately do not activate OTel context: provider/framework
instrumentation cannot inherit this trace and capture private inputs or exceptions.
"""

import asyncio
import logging
import math
import re
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from uuid import UUID

logger = logging.getLogger(__name__)
ROLES = {"coordinator", "orchestrator", "operator", "verifier", "recovery", "analyst"}
ENUMS = {
    "role": ROLES,
    "graph_version": {"v1", "context-v2"},
    "state": {"terminada", "fallida", "unconfirmed"},
    "status": {"ok", "error", "cancelled", "timeout", "unavailable", "invalid", "stale"},
    "reason_code": {"verified", "execution_failed", "deadline", "cancelled", "ambiguous",
        "abstained", "model_budget", "action_budget", "recovery_budget", "milestone_budget",
        "observation_failed", "planning_failed", "operator_failed", "verification_failed",
        "recovery_failed", "authorization_failed", "context_rejected", "advice_failed",
        "missing_answer", "scope_violation", "private_context", "stale_observation",
        "checkpoint_requires_reconciliation", "foreign_context", "graph_version", "other"},
}
NUMBERS = {
    "duration_seconds", "queue_wait_seconds", "task_duration_seconds", "run_duration_seconds",
    "agent_calls", "model_calls", "jev_calls", "vnc_calls", "action_attempts",
    "no_progress_events", "no_progress_max_streak", "recovery_escalations", "jev_escalations",
    "input_tokens", "output_tokens", "total_tokens", "cost_usd", "completed_criteria",
    "remaining_criteria", "reported_no_progress_count", "repeated_frames",
}
_run = ContextVar("safe_telemetry_run", default=None)
_parent = ContextVar("safe_telemetry_parent", default=None)
_metadata = ContextVar("safe_telemetry_metadata", default=None)


def safe_metadata(values):
    """Deny by default, including nested objects and arbitrary/free-form strings."""
    result = {}
    for key, value in values.items():
        if key in ENUMS and isinstance(value, str) and value in ENUMS[key]:
            result[key] = value
        elif key in {"task_id", "run_id", "session_id", "observation_id"}:
            if isinstance(value, UUID):
                result[key] = str(value)
        elif key in NUMBERS and type(value) in {int, float} and 0 <= value <= 1e15 and math.isfinite(value):
            result[key] = value
        elif key in {"transport_closed", "owner_stopped"} and type(value) is bool:
            result[key] = value
    return result


def safe_model(value):
    # Public model identifiers only, never endpoint URLs or provider response text.
    if isinstance(value, str) and re.fullmatch(
        r"(?:openai|anthropic|google|typesafe|qwen|meta-llama|mistralai|deepseek)/[A-Za-z0-9_.:-]{1,100}", value
    ) and not re.search(r"(?:sk-|pk-|token|secret|password)", value, re.I):
        return value
    return None


def _attempt(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except Exception:
        # Do not use exc_info or interpolate exception/provider/credential values.
        logger.warning("Observability operation unavailable; execution continues.")
        return None


@dataclass
class TraceRun:
    telemetry: object
    root: object
    metadata: dict = field(default_factory=dict)
    counters: Counter = field(default_factory=Counter)
    active: bool = True
    streak: int = 0


class Observability:
    def __init__(self, client=None):
        self.client = client

    @classmethod
    def from_settings(cls, settings):
        enabled = getattr(settings, "langfuse_enabled", True)
        public = getattr(settings, "langfuse_public_key", None)
        secret = getattr(settings, "langfuse_secret_key", None)
        if not enabled or not public or not secret or not (
            public.get_secret_value().strip() and secret.get_secret_value().strip()
        ):
            return cls()
        try:
            from langfuse import Langfuse
            from opentelemetry.sdk.trace import TracerProvider

            client = Langfuse(
                public_key=public.get_secret_value(), secret_key=secret.get_secret_value(),
                base_url=settings.langfuse_base_url,
                environment=settings.langfuse_tracing_environment,
                timeout=2, debug=False, tracer_provider=TracerProvider(),
                # Defense in depth: only our explicit observations may be exported.
                should_export_span=lambda span: span.instrumentation_scope.name == "langfuse-sdk",
            )
            return cls(client)
        except Exception:
            logger.warning("Langfuse initialization unavailable; execution continues.")
            return cls()

    @contextmanager
    def task(self, task_id, run_id, graph_version):
        root = None
        metadata = safe_metadata({"task_id": task_id, "run_id": run_id, "graph_version": graph_version})
        if self.client:
            trace_id = _attempt(self.client.create_trace_id, seed=str(task_id))
            root = _attempt(self.client.start_observation, name="execute-task", as_type="span",
                trace_context={"trace_id": trace_id},
                metadata=metadata)
        run = TraceRun(self, root, metadata=metadata)
        token, parent_token = _run.set(run), _parent.set(root)
        try:
            yield run
        finally:
            run.active = False  # Late/cancelled model completions cannot alter final counters.
            if root:
                _attempt(root.end)
            _parent.reset(parent_token)
            _run.reset(token)

    def finish(self, run, *, state, queue_wait, task_duration, run_duration, reason_code="other"):
        metrics = {key: run.counters[key] for key in (
            "agent_calls", "model_calls", "jev_calls", "vnc_calls", "action_attempts",
            "no_progress_events", "no_progress_max_streak", "recovery_escalations",
            "jev_escalations", "repeated_frames")}
        metrics.update(queue_wait_seconds=queue_wait, task_duration_seconds=task_duration,
                       run_duration_seconds=run_duration)
        reason_code = reason_code if reason_code in ENUMS["reason_code"] else "other"
        metadata = {**run.metadata, **safe_metadata({**metrics, "state": state, "reason_code": reason_code})}
        if not run.root:
            return
        _attempt(run.root.update, metadata=metadata, output={"state": metadata["state"]},
                 level="DEFAULT" if state == "terminada" else "ERROR")
        for name, value in safe_metadata(metrics).items():
            _attempt(self.client.create_score, trace_id=run.root.trace_id,
                observation_id=run.root.id, name=name, value=float(value), data_type="NUMERIC")
        _attempt(self.client.create_score, trace_id=run.root.trace_id,
            observation_id=run.root.id, name="task_success", value=float(state == "terminada"),
            data_type="BOOLEAN")
        _attempt(self.client.create_score, trace_id=run.root.trace_id,
            observation_id=run.root.id, name="terminal_state", value=state, data_type="CATEGORICAL")

    async def shutdown(self):
        if self.client:
            await asyncio.to_thread(_attempt, self.client.shutdown)


def count(name):
    run = _run.get()
    if run and run.active and name in NUMBERS:
        run.counters[name] += 1


def progress(*, advanced, completed=0, remaining=0, reported=0):
    """A verification cycle with no newly confirmed criterion/transition is stalled.

    This is an observability signal, not a new execution/termination policy.
    """
    run = _run.get()
    if run and run.active:
        run.streak = 0 if advanced else run.streak + 1
        if not advanced:
            count("no_progress_events")
        run.counters["no_progress_max_streak"] = max(
            run.counters["no_progress_max_streak"], run.streak)
        update_current({"completed_criteria": completed, "remaining_criteria": remaining,
                        "reported_no_progress_count": reported})


def update_current(metadata):
    run, parent = _run.get(), _parent.get()
    if run and run.active and parent:
        current = _metadata.get()
        if current is not None:
            current.update(safe_metadata(metadata))
        _attempt(parent.update, metadata=current if current is not None else safe_metadata(metadata))


def record_usage(metadata, *, model=None):
    run, parent = _run.get(), _parent.get()
    if not run or not run.active or not parent:
        return
    safe = safe_metadata(metadata)
    current = _metadata.get()
    if current is not None:
        current.update(safe)
    usage = {target: safe[source] for source, target in (
        ("input_tokens", "input"), ("output_tokens", "output")) if source in safe}
    kwargs = {"metadata": current if current is not None else safe}
    if usage:
        kwargs["usage_details"] = usage
    if "cost_usd" in safe:
        kwargs["cost_details"] = {"total": safe["cost_usd"]}
    if safe_model(model):
        kwargs["model"] = safe_model(model)
    _attempt(parent.update, **kwargs)


def observed(name, *, as_type="span", role_arg=None, counter=None):
    """Async wrapper with zero argument/result/exception capture, even on cancellation."""
    def decorate(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            run = _run.get()
            if not run or not run.active:
                return await func(*args, **kwargs)
            if counter:
                count(counter)
            role = kwargs.get("role")
            if role_arg is not None and len(args) > role_arg:
                role = args[role_arg]
            metadata = safe_metadata({"role": role})
            parent = _parent.get()
            span = _attempt(parent.start_observation, name=name, as_type=as_type,
                            metadata=metadata) if parent else None
            token = _parent.set(span or parent)
            metadata_token = _metadata.set(metadata)
            started, status = time.monotonic(), "ok"
            try:
                result = await func(*args, **kwargs)
                result_status = getattr(result, "status", None)
                if isinstance(result_status, str) and result_status in ENUMS["status"]:
                    metadata["status"] = result_status
                return result
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            except BaseException:
                status = "error"
                raise
            finally:
                if span:
                    final_status = metadata.get("status", status) if status == "ok" else status
                    _attempt(span.update, metadata=safe_metadata({**metadata, "status": final_status,
                             "duration_seconds": time.monotonic() - started}),
                             level="DEFAULT" if final_status == "ok" else "ERROR")
                    _attempt(span.end)
                _metadata.reset(metadata_token)
                _parent.reset(token)
        return wrapped
    return decorate


def traced_task(func):
    @wraps(func)
    async def wrapped(self, claim, owner_id):
        from app.domain.contracts import utcnow

        started = time.monotonic()
        accepted = getattr(claim, "accepted_at", utcnow())
        queue_wait = max(0, (utcnow() - accepted).total_seconds())
        state = "unconfirmed"
        reason_code = "other"
        with self.observability.task(claim.task_id, claim.run_id, self.settings.graph_version) as run:
            try:
                result = await func(self, claim, owner_id)
                state = result.estado if result.estado in {"terminada", "fallida"} else "unconfirmed"
                reason_code = result.reason_code
                return result
            finally:
                self.observability.finish(run, state=state, queue_wait=queue_wait,
                    task_duration=max(0, (utcnow() - accepted).total_seconds()),
                    run_duration=time.monotonic() - started, reason_code=reason_code)
    return wrapped
