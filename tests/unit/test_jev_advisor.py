import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.domain.contracts import utcnow
from app.models.advice import AdviceRequest, FinalDecision
from app.models.jev_advisor import (
    AdviceBinding,
    AdviceRejected,
    AdviceSession,
    JevAdvisor,
    build_jev_advisor,
    revalidate_advice,
    validate_final_decision,
)


def request_data():
    obs = uuid4()
    return {
        "requester_role": "operator",
        "scope": {"run_id": str(uuid4()), "session_id": str(uuid4()), "epoch": 1,
                  "context_version": 2, "ui_generation": 1, "observation_id": str(obs)},
        "task": {"original_instruction": "Consulta el cliente indicado.",
                 "normalized_goal": "Locate the requested customer without changing records",
                 "identifiers": [], "restrictions": ["No business API"], "fields_to_preserve": [],
                 "allowed_actions": ["click"], "allowed_changes": [], "preservation_rules": ["No mutations"],
                 "success_criteria": ["Confirmed identity"], "response_requirements": "Observed value"},
        "progress": {"active_milestone": "Confirm identity", "completed_criteria": [],
                     "remaining_criteria": ["Confirmed identity"]},
        "situation": {"current_view": "Inicio", "visible_controls": ["Comercial", "Directorio"],
                      "relevant_routes": []},
        "observed_facts": [{"statement": "Two menu entries are visible", "evidence_ids": [str(obs)]}],
        "verified_facts": [], "history": [],
        "uncertainty": {"hypotheses": [], "contradictions": [],
                        "missing_information": ["Menu contents"], "pending_operation": None},
        "budgets": {"remaining_seconds": 20, "remaining_actions": 8},
        "questions": [{"question_id": "next_route", "type": "choice",
            "instructions": "Which visible route should we inspect next?",
            "options": [{"option_id": "directory", "description": "Inspect visible Directorio",
                         "preconditions": ["Control visible"], "known_risk": "Unknown menu contents"},
                        {"option_id": "insufficient", "description": "Gather targeted evidence",
                         "preconditions": [], "known_risk": "Adds observation latency"}]}],
        "context_coverage": {"included_topics": ["task", "progress", "situation", "evidence",
                                                 "history", "uncertainty", "budgets"],
                             "omitted_topics": [], "sufficiency_status": "sufficient"},
    }


def example():
    request = AdviceRequest.model_validate(request_data())
    binding = AdviceBinding(scope=request.scope, task=request.task,
                            pending_operation=None, evidence_ids=(request.scope.observation_id,))
    return request, binding


def response_data(choice="directory"):
    return {"model": "typesafe/jev-1.13-20260917", "id": "gen-dec-synthetic", "provider": "TypeSafe", "answers": {"next_route": {"type": "choice", "choice": choice,
        "confidence": 0.8, "probabilities": {"directory": 0.9, "insufficient": 0.1}}},
        "usage": {"input_tokens": 20, "output_tokens": 2, "cost": 0.000001}}


def session(request, advisor, **kwargs):
    return AdviceSession(run_id=request.scope.run_id, action_cutoff_at=utcnow()+timedelta(seconds=10),
                         advisor=advisor, **kwargs)


async def test_http_contract_contains_full_text_context_and_english_question_no_images():
    req, binding = example()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=response_data())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        advisor = JevAdvisor(api_key=SecretStr("canary-secret"), client=client)
        result = await session(req, advisor).consult(req, binding=binding)
    assert result.status == "ok" and result.model_version == "typesafe/jev-1.13-20260917"
    sent = calls[0]
    assert str(sent.url) == "https://openrouter.ai/api/alpha/decisions"
    assert sent.headers["Authorization"] == "Bearer canary-secret"
    body = json.loads(sent.content)
    assert set(body) == {"model", "state", "questions"}
    assert body["state"]["task"]["original_instruction"] == "Consulta el cliente indicado."
    assert body["state"]["situation"]["visible_controls"] == ["Comercial", "Directorio"]
    assert body["state"]["uncertainty"]["pending_operation"] is None
    assert "Your answer is advice" in body["questions"]["next_route"]["instructions"]
    assert "canary-secret" not in sent.content.decode()
    assert "image" not in sent.content.decode()
    assert result.input_tokens == 20
    assert result.cost_usd == 0.000001 and result.provider_request_id == "gen-dec-synthetic"


@pytest.mark.parametrize("missing", ["task", "progress", "history", "uncertainty", "budgets"])
def test_required_sections_cannot_be_omitted(missing):
    data = request_data()
    del data[missing]
    with pytest.raises(ValidationError):
        AdviceRequest.model_validate(data)


@pytest.mark.parametrize("alter", ["coverage", "scope", "task", "pending", "evidence", "secret"])
async def test_context_guards_reject_before_provider(alter):
    req, binding = example()
    data = req.model_dump(mode="json")
    if alter == "coverage":
        data["context_coverage"]["omitted_topics"] = ["Pending operation"]
    elif alter == "scope":
        data["scope"]["context_version"] += 1
    elif alter == "task":
        data["task"]["allowed_changes"] = ["Delete customers"]
    elif alter == "pending":
        data["uncertainty"]["pending_operation"] = "Save may have completed"
    elif alter == "evidence":
        data["observed_facts"][0]["evidence_ids"] = [str(uuid4())]
    else:
        data["situation"]["current_view"] = "canary-private"

    class MustNotCall:
        async def advise(self, *args, **kwargs):
            raise AssertionError("Guard must block before provider")

    ledger = session(req, MustNotCall(), secrets=("canary-private",))
    result = await ledger.consult(AdviceRequest.model_validate(data), binding=binding)
    assert result.status in {"invalid", "stale"} and ledger.calls == 0
    assert "canary-private" not in result.model_dump_json()


@pytest.mark.parametrize("value", ["wss://private.example/vnc", "data:image/png;base64,abcdef",
                                   "postgresql://private", "sk-or-v1-abcdefghijklmnop"])
async def test_private_payloads_never_reach_http(value):
    req, binding = example()
    data = req.model_dump(mode="json")
    data["situation"]["current_view"] = value
    result = await session(req, object()).consult(AdviceRequest.model_validate(data), binding=binding)
    assert result.status == "invalid"
    assert value not in result.model_dump_json()


@pytest.mark.parametrize("status", [401, 422, 429, 529, 503, 302])
async def test_http_errors_are_optional_sanitized_and_not_retried_or_redirected(status):
    req, binding = example()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, text="canary-provider-error",
                              headers={"location": "https://untrusted.example/steal"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        result = await session(req, JevAdvisor(api_key=SecretStr("canary-secret"), client=client)).consult(req, binding=binding)
    assert len(calls) == 1 and result.status == "unavailable"
    assert "canary-provider-error" not in result.model_dump_json()


@pytest.mark.parametrize("fault", ["probabilities", "nan", "foreign_option", "question", "type", "secret", "size"])
async def test_malformed_provider_output_is_never_advice(fault):
    req, binding = example()
    payload = response_data()
    answer = payload["answers"]["next_route"]
    if fault == "probabilities":
        answer["probabilities"]["directory"] = 0.2
    elif fault == "nan":
        answer["confidence"] = float("nan")
    elif fault == "foreign_option":
        answer["choice"] = "delete"
    elif fault == "question":
        payload["answers"]["unasked"] = answer
    elif fault == "type":
        answer["type"] = "noul"
    elif fault == "secret":
        payload["model"] = "canary-secret"
    else:
        payload["padding"] = "x" * 66000
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        result = await session(req, JevAdvisor(api_key=SecretStr("canary-secret"), client=client)).consult(req, binding=binding)
    assert result.status == "invalid" and result.answers == ()
    assert "canary-secret" not in result.model_dump_json()


async def test_timeout_cancel_and_original_deadline():
    req, binding = example()

    async def slow(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json=response_data())

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        advisor = JevAdvisor(api_key=SecretStr("canary-secret"), client=client)
        result = await session(req, advisor, timeout_seconds=0.01).consult(req, binding=binding)
        assert result.status == "timeout"
        ledger = session(req, advisor)
        task = asyncio.create_task(ledger.consult(req, binding=binding))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    expired = AdviceSession(run_id=req.scope.run_id, action_cutoff_at=utcnow()-timedelta(seconds=1), advisor=advisor)
    assert (await expired.consult(req, binding=binding)).reason_code == "deadline"
    assert expired.calls == 0


async def test_ledger_quota_duplicate_and_concurrent_claim():
    req, binding = example()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_data()))) as client:
        ledger = session(req, JevAdvisor(api_key=SecretStr("canary-secret"), client=client), max_calls=1)
        outcomes = await asyncio.gather(ledger.consult(req, binding=binding), ledger.consult(req, binding=binding))
        assert sorted(x.reason_code for x in outcomes) == ["advice_only", "duplicate_question"]
        another = req.model_copy(update={"request_id": uuid4()})
        assert (await ledger.consult(another, binding=binding)).reason_code == "duplicate_question"
        another = req.model_copy(update={"questions": (req.questions[0].model_copy(update={"instructions": "Is more evidence needed?"}),)})
        assert (await ledger.consult(another, binding=binding)).reason_code == "advice_budget"
        assert ledger.calls == 1


async def test_final_decision_is_owned_by_requester_and_revalidated():
    req, binding = example()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_data()))) as client:
        advice = await session(req, JevAdvisor(api_key=SecretStr("canary-secret"), client=client)).consult(req, binding=binding)
    decision = FinalDecision(request_id=req.request_id, requester_role=req.requester_role,
        scope=req.scope, question_id="next_route", selected_option="directory", accepted_advice=True,
        brief_reason="Visible menu is relevant; relocalize before proposing navigation",
        evidence_ids=binding.evidence_ids, unmet_preconditions=(), next_step="propose_action")
    validate_final_decision(decision, req, advice, binding=binding)
    rejected = decision.model_copy(update={"selected_option": "insufficient", "accepted_advice": False, "next_step": "reobserve"})
    validate_final_decision(rejected, req, advice, binding=binding)
    with pytest.raises(AdviceRejected, match="advice_mismatch"):
        validate_final_decision(rejected.model_copy(update={"accepted_advice": True}), req, advice, binding=binding)
    with pytest.raises(AdviceRejected, match="unmet_preconditions"):
        validate_final_decision(decision.model_copy(update={"unmet_preconditions": ("Identity missing",)}), req, advice, binding=binding)
    new_binding = binding.model_copy(update={"scope": binding.scope.model_copy(update={"ui_generation": 2})})
    assert revalidate_advice(advice, new_binding).status == "stale"
    with pytest.raises(AdviceRejected, match="stale_context"):
        validate_final_decision(decision, req, advice, binding=new_binding)


async def test_disabled_missing_key_insufficient_and_factory():
    req, binding = example()
    assert build_jev_advisor(Settings()) is None
    advisor = build_jev_advisor(Settings(jev_enabled=True))
    assert (await session(req, advisor).consult(req, binding=binding)).reason_code == "missing_credential"
    assert (await session(req, None).consult(req, binding=binding)).reason_code == "disabled"
    payload = response_data("insufficient")
    payload["answers"]["next_route"]["probabilities"] = {"directory": 0.1, "insufficient": 0.9}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        result = await session(req, JevAdvisor(api_key=SecretStr("canary-secret"), client=client)).consult(req, binding=binding)
    assert result.status == "ok" and result.answers[0].choice == "insufficient"


def test_option_and_error_contracts_are_bounded_and_hide_inputs():
    data = request_data()
    data["questions"][0]["options"] = data["questions"][0]["options"][:1]
    with pytest.raises(ValidationError):
        AdviceRequest.model_validate(data)
    data = request_data()
    data["private_token"] = "canary-secret"
    with pytest.raises(ValidationError) as failure:
        AdviceRequest.model_validate(data)
    assert "canary-secret" not in str(failure.value)
    assert "canary-secret" not in failure.value.json()
    assert "canary-secret" not in repr(failure.value.errors())


async def test_backwards_clock_does_not_extend_advisor_deadline():
    req, binding = example()
    clock = [utcnow()]
    tick = [0.0]
    ledger = AdviceSession(run_id=req.scope.run_id, action_cutoff_at=clock[0]+timedelta(seconds=2),
        advisor=object(), now=lambda: clock[0], monotonic=lambda: tick[0])
    clock[0] -= timedelta(hours=1)
    tick[0] = 3
    assert (await ledger.consult(req, binding=binding)).reason_code == "deadline"


async def test_unvalidated_copy_cannot_bypass_required_option_or_coverage_contract():
    req, binding = example()
    forged = req.model_copy(update={"questions": (req.questions[0].model_copy(update={"options": ()}),)})
    ledger = session(req, object())
    result = await ledger.consult(forged, binding=binding)
    assert result.status == "invalid" and result.reason_code == "invalid_contract" and ledger.calls == 0


async def test_same_question_cannot_be_reasked_by_another_role_or_cosmetic_version_bump():
    req, binding = example()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_data()))) as client:
        ledger = session(req, JevAdvisor(api_key=SecretStr("canary-secret"), client=client))
        assert (await ledger.consult(req, binding=binding)).status == "ok"
        other = req.model_copy(update={"request_id": uuid4(), "requester_role": "recovery"})
        assert (await ledger.consult(other, binding=binding)).reason_code == "duplicate_question"
        newer = binding.model_copy(update={"scope": binding.scope.model_copy(update={"context_version": 3})})
        other = req.model_copy(update={"request_id": uuid4(), "scope": newer.scope})
        assert (await ledger.consult(other, binding=newer)).reason_code == "duplicate_question"
        assert ledger.calls == 1


async def test_jev_uses_shared_credit_failover_and_does_not_expose_either_key():
    from app.models.openrouter_keys import OpenRouterKeys

    req, binding = example()
    calls = []
    keys = OpenRouterKeys(SecretStr('synthetic-primary-key'), SecretStr('synthetic-backup-key'))

    def respond(request):
        calls.append(request)
        if request.headers['Authorization'] == 'Bearer synthetic-primary-key':
            return httpx.Response(402, json={'error': {'message': 'Insufficient credits'}})
        return httpx.Response(200, json=response_data())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        advisor = JevAdvisor(api_key=keys.key(0), keys=keys, client=client)
        result = await session(req, advisor).consult(req, binding=binding)
        req2 = req.model_copy(update={'request_id': uuid4()})
        result2 = await advisor.advise(req2, timeout_seconds=3)
    assert result.status == result2.status == 'ok'
    assert [c.headers['Authorization'] for c in calls] == [
        'Bearer synthetic-primary-key', 'Bearer synthetic-backup-key', 'Bearer synthetic-backup-key']
    assert calls[0].content == calls[1].content
    assert 'synthetic-backup-key' not in result.model_dump_json()
