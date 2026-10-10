"""Consult -> same requester -> final proposal. No VNC or public terminal side effects.

The v2 graph will supply its delegate callback and latest accepted-context binding.
The callback must consume its ordinary per-role/model budget; this flow only caps time.
"""
import asyncio
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from app.models.advice import AdviceContract, AdviceRequest, AdviceResult, FinalDecision
from app.models.jev_advisor import (
    AdviceBinding,
    AdviceRejected,
    AdviceSession,
    revalidate_advice,
    validate_final_decision,
)


class AdviceFlowResult(AdviceContract):
    advice: AdviceResult
    decision: FinalDecision | None
    reason_code: str


async def consult_then_decide(
    request: AdviceRequest,
    *,
    binding: AdviceBinding,
    session: AdviceSession,
    decide: Callable[[AdviceRequest, AdviceResult], Awaitable[FinalDecision]],
    latest_binding: Callable[[], AdviceBinding],
    model_timeout_seconds: float = 20,
) -> AdviceFlowResult:
    advice = await session.consult(request, binding=binding)
    current = latest_binding()
    advice = revalidate_advice(advice, current)
    if advice.status in {"invalid", "stale"}:
        return AdviceFlowResult(advice=advice, decision=None, reason_code=advice.reason_code)
    timeout = min(model_timeout_seconds, session.remaining() - 0.25)
    if timeout <= 0:
        return AdviceFlowResult(advice=advice, decision=None, reason_code="deadline")
    try:
        async with asyncio.timeout(timeout):
            candidate = await decide(request, advice)
        decision = FinalDecision.model_validate(candidate.model_dump(mode="python"))
        # Another specialist or observation may have updated context during inference.
        current = latest_binding()
        validate_final_decision(decision, request, advice, binding=current, secrets=session.secrets)
        if session.remaining() <= 0.25:
            return AdviceFlowResult(advice=advice, decision=None, reason_code="deadline")
        return AdviceFlowResult(advice=advice, decision=decision, reason_code="decision_candidate")
    except AdviceRejected as exc:
        return AdviceFlowResult(advice=revalidate_advice(advice, current), decision=None, reason_code=str(exc))
    except TimeoutError:
        return AdviceFlowResult(advice=advice, decision=None, reason_code="decision_timeout")
    except (ValidationError, ValueError, TypeError, AttributeError):
        return AdviceFlowResult(advice=advice, decision=None, reason_code="invalid_decision")
    except Exception:
        return AdviceFlowResult(advice=advice, decision=None, reason_code="decision_unavailable")
