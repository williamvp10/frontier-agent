"""Runtime assembly of complete advice context. Never trust an LLM context dump.

This bridge consumes accepted v2 snapshots; it does not activate advice in the baseline graph.
No mutation of the context service's state and no model/DB/desktop I/O.
"""

from app.agents.context import SharedTaskContext
from app.agents.context_service import ContextService
from app.models.advice import (
    AdviceBudget,
    AdviceCoverage,
    AdviceFact,
    AdviceIdentifier,
    AdviceProgress,
    AdviceRequest,
    AdviceScope,
    AdviceSituation,
    AdviceTask,
    AdviceUncertainty,
)
from app.models.jev_advisor import AdviceBinding, AdviceRejected, validate_context


def build_advice_context(snapshot: SharedTaskContext, *, requester_role, questions,
                         remaining_seconds: float, secrets: tuple[str, ...] = ()):
    """Return request/binding from the same validated, immutable snapshot.

    Sends all task-local facts/history needed by v2 without silently dropping entries.
    Oversized contexts fail before network; targeted compression needs a later explicit policy.
    Remaining time comes from the runtime's original deadline, never the proposing model.
    """
    try:
        source = SharedTaskContext.model_validate(snapshot.model_dump(mode="python"))
        role = "coordinator" if requester_role == "orchestrator" else requester_role
        projected = ContextService(recent_actions=100).project(source, role=role)
        scope = AdviceScope(run_id=source.run_id, session_id=source.session_id, epoch=source.epoch,
            context_version=source.context_version, ui_generation=source.ui_generation,
            observation_id=source.current_ui.observation_id)
        contract = projected.task_contract
        task = AdviceTask(original_instruction=contract.original_instruction,
            normalized_goal=contract.normalized_goal,
            identifiers=tuple(AdviceIdentifier(name=i.name, value=i.value) for i in contract.identifiers),
            allowed_changes=contract.allowed_changes, fields_to_preserve=contract.fields_to_preserve,
            allowed_actions=contract.allowed_actions, restrictions=contract.restrictions,
            preservation_rules=contract.preservation_rules,
            success_criteria=tuple(c.description for c in contract.success_criteria),
            response_requirements="\n".join(contract.response_requirements))
        criteria = {c.id: c.description for c in contract.success_criteria}
        assignment = projected.active_assignment
        milestones = {m.id: m.semantic_goal for m in projected.milestone_plan}
        active = milestones[assignment.milestone_id] if assignment else "Plan the next task milestone"
        progress = AdviceProgress(active_milestone=active,
            completed_criteria=tuple(criteria[c.criterion_id] for c in projected.progress.satisfied_criteria),
            remaining_criteria=tuple(criteria[c] for c in projected.progress.remaining_criterion_ids))
        ui = projected.current_ui
        view_description = " | ".join(x for x in (ui.title, ui.active_tab, ui.modal, ui.visible_error) if x)
        # Loading/focus/viewport absence are uncertainty, not proof of a missing feature.
        state_note = "Loading" if ui.loading else "Current visible view"
        situation = AdviceSituation(current_view=view_description or state_note,
            visible_controls=tuple(c.label + " (" + c.state + ")" for c in ui.visible_controls),
            relevant_routes=tuple(e.model_dump_json() for e in projected.app_map.transitions),
            view_capabilities=tuple(v.model_dump_json() for v in projected.app_map.views),
            unexplored_alternatives=tuple(v.model_dump_json() for v in projected.app_map.frontier),
            ui_details=tuple(x for x in (
                "Breadcrumbs: " + " / ".join(ui.breadcrumbs) if ui.breadcrumbs else None,
                "Expanded menu: " + ui.expanded_menu if ui.expanded_menu else None,
                "Loading: " + str(ui.loading),
                "Focus: " + (str(ui.focused_control_id) if ui.focused_control_id else "unknown"),
            ) if x))
        observed, verified = [], []
        for fact in projected.facts:
            item = AdviceFact(statement=fact.assertion, status=fact.status, evidence_ids=fact.evidence_ids)
            (verified if fact.status == "independently_verified" else observed).append(item)
        pending = projected.pending_operation.model_dump_json() if projected.pending_operation else None
        history = tuple({"action": a.semantic_goal, "send_status": a.outcome,
                         "expected_effect": a.expected_effect,
                          "observed_effect": a.observed_effect or (
                              "Verifier reconciliation: " + a.reconciliation.outcome if a.reconciliation
                              else "Not yet observed; outcome is unconfirmed"),
                          "reconciliation_outcome": a.reconciliation.outcome if a.reconciliation else None,
                          "reconciliation_evidence_ids": a.reconciliation.evidence_ids if a.reconciliation else (),
                          "evidence_ids": tuple(dict.fromkeys((a.source_observation_id,
                              *((a.subsequent_observation_id,) if a.subsequent_observation_id else ()),
                              *a.effect_evidence_ids,
                              *(a.reconciliation.evidence_ids if a.reconciliation else ())))) }
                        for a in projected.action_history)
        uncertainty = AdviceUncertainty(hypotheses=tuple(h.model_dump_json() for h in projected.hypotheses),
            contradictions=tuple(f.assertion for f in projected.facts if f.status == "contradicted"),
            missing_information=projected.unresolved_questions, pending_operation=pending)
        request = AdviceRequest(requester_role=requester_role, scope=scope, task=task,
            progress=progress, situation=situation, observed_facts=tuple(observed), verified_facts=tuple(verified),
            history=history, uncertainty=uncertainty,
            budgets=AdviceBudget(remaining_seconds=min(remaining_seconds, source.budgets.remaining_seconds),
                                remaining_actions=source.budgets.remaining_actions),
            questions=questions, context_coverage=AdviceCoverage(included_topics=("task", "progress", "situation",
                "evidence", "history", "uncertainty", "budgets"), omitted_topics=(), sufficiency_status="sufficient"))
        binding = AdviceBinding(scope=scope, task=task, pending_operation=pending,
                                evidence_ids=tuple(e.id for e in projected.evidence))
        validate_context(request, binding, secrets)
        return request, binding
    except AdviceRejected:
        raise
    except Exception:
        # Context-service errors/validation may include private values if third-party types change.
        raise AdviceRejected("invalid_advice_context") from None
