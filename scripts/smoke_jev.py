"""Opt-in paid Jev request on text-only synthetic state; no VNC or business data."""
import argparse
import asyncio
from uuid import uuid4

from app.core.config import Settings
from app.models.advice import AdviceRequest
from app.models.jev_advisor import AdviceBinding, AdviceSession, JevAdvisor


async def smoke(env_file):
    settings = Settings(_env_file=env_file)
    if not settings.openrouter_api_key.get_secret_value().strip():
        print("Jev smoke unavailable: missing OPENROUTER_API_KEY")
        return 1
    from datetime import timedelta

    from app.domain.contracts import utcnow

    obs = uuid4()
    request = AdviceRequest.model_validate({
        "requester_role": "operator",
        "scope": {"run_id": uuid4(), "session_id": uuid4(), "epoch": 1,
                  "context_version": 1, "ui_generation": 0, "observation_id": obs},
        "task": {"original_instruction": "Read the count shown in the synthetic report.",
                 "normalized_goal": "Read a report without changing records", "identifiers": [],
                 "restrictions": ["No mutations"], "fields_to_preserve": [], "allowed_actions": [],
                 "allowed_changes": [], "preservation_rules": ["Preserve records"],
                 "success_criteria": ["Read visible count"], "response_requirements": "Observed value"},
        "progress": {"active_milestone": "Read the count", "completed_criteria": [],
                     "remaining_criteria": ["Read visible count"]},
        "situation": {"current_view": "Synthetic report", "visible_controls": [], "relevant_routes": []},
        "observed_facts": [{"statement": "The report visibly says count: 7", "evidence_ids": [obs]}],
        "verified_facts": [], "history": [],
        "uncertainty": {"hypotheses": [], "contradictions": [], "missing_information": [], "pending_operation": None},
        "budgets": {"remaining_seconds": 10, "remaining_actions": 0},
        "questions": [{"question_id": "next_step", "instructions": "Which next step fits the task and evidence?",
                       "options": [{"option_id": "verify", "description": "Ask independent verifier to check the visible count",
                                    "preconditions": [], "known_risk": "No desktop change"},
                                   {"option_id": "insufficient", "description": "Evidence is insufficient",
                                    "preconditions": [], "known_risk": "No completion"}]}],
        "context_coverage": {"included_topics": ["task", "progress", "situation", "evidence", "history", "uncertainty", "budgets"],
                             "omitted_topics": [], "sufficiency_status": "sufficient"},
    })
    binding = AdviceBinding(scope=request.scope, task=request.task, pending_operation=None, evidence_ids=(obs,))
    advisor = JevAdvisor(api_key=settings.openrouter_api_key, model=settings.model_jev,
                          timeout_seconds=settings.jev_timeout_seconds,
                          base_url=settings.openrouter_base_url, endpoint=settings.openrouter_jev_endpoint)
    session = AdviceSession(run_id=request.scope.run_id, action_cutoff_at=utcnow()+timedelta(seconds=10),
                            advisor=advisor, max_calls=1, timeout_seconds=settings.jev_timeout_seconds)
    result = await session.consult(request, binding=binding)
    if result.status != "ok":
        print(f"Jev smoke unavailable: {result.reason_code}")
        return 1
    print(f"Jev HTTP/schema smoke passed; model={result.model_version}, latency_s={result.latency_seconds:.2f}")
    # This tests API compatibility; it does not approve task completion or desktop actions.
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args()
    try:
        return asyncio.run(smoke(args.env_file))
    except Exception:
        print("Jev smoke unavailable; private details withheld")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
