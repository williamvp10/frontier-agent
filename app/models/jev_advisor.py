"""OpenRouter Decisions HTTP advisor. Advice cannot authorize desktop events."""
import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import Field, SecretStr, ValidationError

from app.core.observability import observed, record_usage, update_current
from app.domain.contracts import utcnow
from app.models.advice import (
    AdviceContract,
    AdviceRequest,
    AdviceResult,
    AdviceScope,
    AdviceTask,
    ChoiceAnswer,
    FinalDecision,
    Text,
)
from app.models.openrouter_keys import OpenRouterKeys, is_credit_exhausted
from app.models.prompts import JEV_CRITERIA

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
REQUIRED_TOPICS = {"task", "progress", "situation", "evidence", "history", "uncertainty", "budgets"}
PRIVATE_PATTERN = re.compile(
    r"(?:https?|wss?|postgresql(?:\+psycopg)?|redis)://|data:image/|"
    r"(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{12,}|ftr_[a-f0-9]{20,})|"
    r"-----BEGIN .*PRIVATE KEY-----|\bBearer\s+\S+|"
    r"\b(?:password|passwd|api[_ -]?key|access[_ -]?token|secret)\s*[:=]|"
    r"<think>|chain.of.thought|[A-Za-z0-9+/]{256,}={0,2}", re.I,
)


class AdviceRejected(ValueError):
    """Only a fixed reason code; never attach provider payloads or request context."""
    CODES = frozenset({"private_context", "invalid_contract", "stale_context",
        "critical_context_mismatch", "incomplete_context", "foreign_evidence",
        "invalid_provenance", "oversized_response", "invalid_response", "foreign_run",
        "invalid_decision", "stale_decision", "invalid_option", "advice_mismatch",
        "unmet_preconditions", "invalid_advice_context"})

    def __init__(self, code: str):
        self.code = code if code in self.CODES else "invalid_advice_context"
        super().__init__(self.code)


class AdviceBinding(AdviceContract):
    """Runtime-owned critical data, assembled from accepted context, never LLM authority."""
    scope: AdviceScope
    task: AdviceTask
    pending_operation: Text | None
    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=200)


def assert_safe(value, secrets: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"password", "authorization", "api_key", "token", "url", "dsn", "image", "base64"}:
                raise AdviceRejected("private_context")
            assert_safe(item, secrets)
    elif isinstance(value, (tuple, list)):
        for item in value:
            assert_safe(item, secrets)
    elif isinstance(value, str):
        if PRIVATE_PATTERN.search(value) or any(secret and secret in value for secret in secrets):
            raise AdviceRejected("private_context")


def validate_context(request: AdviceRequest, binding: AdviceBinding, secrets=()):
    try:
        request = AdviceRequest.model_validate(request.model_dump(mode="python", warnings="none"))
        binding = AdviceBinding.model_validate(binding.model_dump(mode="python", warnings="none"))
    except (ValidationError, TypeError, ValueError):
        raise AdviceRejected("invalid_contract") from None
    if request.scope != binding.scope:
        raise AdviceRejected("stale_context")
    if request.task != binding.task or request.uncertainty.pending_operation != binding.pending_operation:
        raise AdviceRejected("critical_context_mismatch")
    coverage = request.context_coverage
    if (coverage.sufficiency_status != "sufficient" or coverage.omitted_topics
            or not REQUIRED_TOPICS.issubset(coverage.included_topics)):
        raise AdviceRejected("incomplete_context")
    allowed = set(binding.evidence_ids)
    cited = {request.scope.observation_id}
    for fact in (*request.observed_facts, *request.verified_facts):
        cited.update(fact.evidence_ids)
    for attempt in request.history:
        cited.update(attempt.evidence_ids)
        cited.update(attempt.reconciliation_evidence_ids)
    if not cited.issubset(allowed):
        raise AdviceRejected("foreign_evidence")
    if any(f.status != "independently_verified" for f in request.verified_facts):
        raise AdviceRejected("invalid_provenance")
    if any(f.status == "independently_verified" for f in request.observed_facts):
        raise AdviceRejected("invalid_provenance")
    assert_safe(request.model_dump(mode="json"), secrets)


def advice_payload(request: AdviceRequest, model: str) -> dict:
    state = request.model_dump(mode="json", exclude={"request_id", "questions"})
    questions = {
        question.question_id: {
            "type": "choice", "instructions": JEV_CRITERIA + "\n" + question.instructions,
            "criteria": {
                option.option_id: {"description": option.description,
                    "preconditions": list(option.preconditions), "known_risk": option.known_risk}
                for option in question.options
            },
        }
        for question in request.questions
    }
    return {"model": model, "state": state, "questions": questions}


def failed_advice(request, status, reason, latency=0):
    return AdviceResult(request_id=request.request_id, requester_role=request.requester_role,
        scope=request.scope, status=status, reason_code=reason, latency_seconds=latency)


class Advisor(Protocol):
    async def advise(self, request: AdviceRequest, *, timeout_seconds: float) -> AdviceResult: ...


class JevAdvisor:
    def __init__(self, *, api_key: SecretStr, model="typesafe/jev-1.13", timeout_seconds=3,
                  client: httpx.AsyncClient | None = None, secrets: tuple[str, ...] = (),
                  base_url="https://openrouter.ai/api/v1", endpoint="/api/alpha/decisions",
                  keys: OpenRouterKeys | None = None):
        if timeout_seconds <= 0 or model != "typesafe/jev-1.13" or (
            base_url.rstrip("/") != "https://openrouter.ai/api/v1"
            or endpoint != "/api/alpha/decisions"
        ):
            raise ValueError("Invalid advisor configuration")
        origin = urlsplit(base_url)
        self.endpoint = origin.scheme + "://" + origin.netloc + endpoint
        self._key, self.model, self.timeout_seconds = api_key, model, timeout_seconds
        self._client = client
        self.keys = keys or OpenRouterKeys(api_key)
        self._secrets = secrets + tuple(self.keys.key(i).get_secret_value()
                                       for i in range(2 if self.keys.has_backup else 1))

    @observed("generate-jev-advice", as_type="generation", counter="jev_calls")
    async def advise(self, request: AdviceRequest, *, timeout_seconds: float) -> AdviceResult:
        record_usage({}, model=self.model)
        if not self._key.get_secret_value().strip():
            return failed_advice(request, "unavailable", "missing_credential")
        try:
            assert_safe(request.model_dump(mode="json"), self._secrets)
        except AdviceRejected:
            return failed_advice(request, "invalid", "private_context")
        if timeout_seconds <= 0:
            return failed_advice(request, "timeout", "deadline")
        payload_out = advice_payload(request, self.model)
        if len(json.dumps(payload_out).encode()) > 65536:
            return failed_advice(request, "invalid", "oversized_context")
        start = time.monotonic()
        client = self._client or httpx.AsyncClient(trust_env=False, follow_redirects=False)
        try:
            limit = min(self.timeout_seconds, timeout_seconds)
            async with asyncio.timeout(limit):
                for attempt in range(2):
                    slot = self.keys.active
                    async with client.stream("POST", self.endpoint, json=payload_out,
                        headers={"Authorization": "Bearer " + self.keys.key(slot).get_secret_value()},
                        timeout=limit, follow_redirects=False) as response:
                        if response.status_code not in {200, 402}:
                            return failed_advice(request, "unavailable", "provider_unavailable",
                                                 time.monotonic() - start)
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 65536:
                                raise AdviceRejected("oversized_response")
                        if response.status_code == 402:
                            error = json.loads(data)
                            if attempt == 0 and is_credit_exhausted(402, error) and self.keys.failover(slot):
                                continue
                            return failed_advice(request, "unavailable", "provider_unavailable",
                                                 time.monotonic() - start)
                        break
                payload = json.loads(data)
                assert_safe(payload, self._secrets)
                answers = []
                if set(payload["answers"]) != {q.question_id for q in request.questions}:
                    raise AdviceRejected("invalid_response")
                for question in request.questions:
                    raw = payload["answers"][question.question_id]
                    if raw["type"] != "choice" or set(raw["probabilities"]) != {o.option_id for o in question.options}:
                        raise AdviceRejected("invalid_response")
                    answers.append(ChoiceAnswer(question_id=question.question_id,
                        choice=raw["choice"], confidence=raw["confidence"],
                        probabilities=tuple(raw["probabilities"].items())))
                result = AdviceResult(request_id=request.request_id, requester_role=request.requester_role,
                    scope=request.scope, model_version=payload["model"], answers=tuple(answers),
                     status="ok", reason_code="advice_only", latency_seconds=time.monotonic() - start,
                     input_tokens=payload["usage"]["input_tokens"], output_tokens=payload["usage"]["output_tokens"],
                     cost_usd=payload["usage"].get("cost"), provider_request_id=payload.get("id"),
                      provider=payload.get("provider"))
                record_usage({"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                              "cost_usd": result.cost_usd}, model=self.model)
                return result
        except (TimeoutError, httpx.TimeoutException):
            return failed_advice(request, "timeout", "provider_timeout", time.monotonic() - start)
        except httpx.HTTPError:
            return failed_advice(request, "unavailable", "provider_unavailable", time.monotonic() - start)
        except (AdviceRejected, ValidationError, ValueError, KeyError, TypeError):
            return failed_advice(request, "invalid", "invalid_response", time.monotonic() - start)
        finally:
            if self._client is None:
                await client.aclose()


class AdviceSession:
    """One ledger per run. Runtime checks updated binding again before FinalDecision."""
    def __init__(self, *, run_id: UUID, action_cutoff_at: datetime, advisor: Advisor | None,
                 max_calls=4, timeout_seconds=3, now=utcnow, monotonic=time.monotonic,
                 secrets: tuple[str, ...] = ()):
        if max_calls < 0 or timeout_seconds <= 0:
            raise ValueError("Invalid advice budget")
        self.run_id, self.deadline, self.advisor = run_id, action_cutoff_at, advisor
        self.max_calls, self.timeout_seconds, self.now = max_calls, timeout_seconds, now
        self.monotonic, self.secrets = monotonic, secrets
        self._monotonic_deadline = monotonic() + max(0, (action_cutoff_at - now()).total_seconds())
        self.calls = 0
        self._seen = set()
        self._lock = asyncio.Lock()

    def remaining(self):
        return min((self.deadline - self.now()).total_seconds(), self._monotonic_deadline - self.monotonic())

    @observed("consult-jev")
    async def consult(self, request: AdviceRequest, *, binding: AdviceBinding) -> AdviceResult:
        try:
            if request.scope.run_id != self.run_id:
                raise AdviceRejected("foreign_run")
            validate_context(request, binding, self.secrets)
        except AdviceRejected as exc:
            reason = str(exc)
            return failed_advice(request, "stale" if reason == "stale_context" else "invalid", reason)
        if self.advisor is None:
            return failed_advice(request, "unavailable", "disabled")
        canonical = request.model_dump(mode="json", exclude={"request_id", "requester_role", "budgets"})
        canonical["scope"].pop("context_version")
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        async with self._lock:
            if self.remaining() <= 0.25:
                return failed_advice(request, "timeout", "deadline")
            if fingerprint in self._seen:
                return failed_advice(request, "unavailable", "duplicate_question")
            if self.calls >= self.max_calls:
                return failed_advice(request, "unavailable", "advice_budget")
            self._seen.add(fingerprint)
            self.calls += 1
        timeout = min(self.timeout_seconds, self.remaining() - 0.25, request.budgets.remaining_seconds)
        try:
            async with asyncio.timeout(max(0, timeout)):
                result = await self.advisor.advise(request, timeout_seconds=max(0, timeout))
                update_current({"status": result.status})
                return result
        except TimeoutError:
            return failed_advice(request, "timeout", "provider_timeout")
        except Exception:
            return failed_advice(request, "unavailable", "advisor_unavailable")


def validate_final_decision(decision: FinalDecision, request: AdviceRequest,
                            advice: AdviceResult, *, binding: AdviceBinding, secrets=()):
    validate_context(request, binding, secrets)
    try:
        decision = FinalDecision.model_validate(decision.model_dump(mode="python"))
        advice = AdviceResult.model_validate(advice.model_dump(mode="python"))
    except (ValidationError, TypeError, ValueError):
        raise AdviceRejected("invalid_decision") from None
    if (decision.scope != binding.scope or decision.request_id != request.request_id
            or decision.requester_role != request.requester_role
            or advice.request_id != request.request_id or advice.scope != binding.scope
            or advice.requester_role != request.requester_role):
        raise AdviceRejected("stale_decision")
    question = next((q for q in request.questions if q.question_id == decision.question_id), None)
    if question is None or decision.selected_option not in {o.option_id for o in question.options}:
        raise AdviceRejected("invalid_option")
    if not set(decision.evidence_ids).issubset(binding.evidence_ids):
        raise AdviceRejected("foreign_evidence")
    if decision.accepted_advice:
        answer = next((a for a in advice.answers if a.question_id == decision.question_id), None)
        if advice.status != "ok" or answer is None or answer.choice != decision.selected_option:
            raise AdviceRejected("advice_mismatch")
    if decision.next_step == "propose_action" and (decision.unmet_preconditions or decision.selected_option == "insufficient"):
        raise AdviceRejected("unmet_preconditions")
    assert_safe(decision.model_dump(mode="json"), secrets)
    # Passing this check does not authorize an action: normal verifier/gateway guards still apply.


def revalidate_advice(result: AdviceResult, binding: AdviceBinding) -> AdviceResult:
    if result.scope != binding.scope:
        return result.model_copy(update={"status": "stale", "reason_code": "stale_context", "answers": ()})
    return result


def build_jev_advisor(settings, *, keys=None) -> JevAdvisor | None:
    if not settings.jev_enabled:
        return None
    # A missing key is an unavailable optional advisor, not a fake successful answer.
    return JevAdvisor(api_key=settings.openrouter_api_key, model=settings.model_jev,
                      timeout_seconds=settings.jev_timeout_seconds,
                      base_url=settings.openrouter_base_url, endpoint=settings.openrouter_jev_endpoint,
                      keys=keys or OpenRouterKeys(settings.openrouter_api_key, settings.openrouter_api_key_backup))
