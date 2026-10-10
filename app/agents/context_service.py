"""Pure reducer/projections. Runtime owns serialization, observation and persistence.

actor_role is trusted caller metadata, never a role supplied by a model payload.
No clock, network, desktop, DB, logging or provider is accessed here.
"""

from typing import Literal

from pydantic import ValidationError

from app.agents.context import (
    ContextPatch,
    ContextSlice,
    Fact,
    Milestone,
    PartialAppMap,
    Progress,
    Provenance,
    SharedTaskContext,
)

ErrorCode = Literal[
    "invalid_contract", "invalid_role", "stale_run", "stale_session", "stale_epoch",
    "stale_version", "stale_observation", "stale_frame", "stale_generation",
    "permission_denied", "invalid_evidence", "invalid_reference", "duplicate_id",
    "invalid_provenance", "scope_violation", "budget_violation", "pending_operation",
    "invalid_action_transition", "insufficient_context", "limit_exceeded",
]


class ContextError(Exception):
    """Safe code only; never attach inputs, values, UUIDs or underlying messages."""

    def __init__(self, code: ErrorCode):
        self.code = code
        super().__init__(code)


class PatchRejected(ContextError):
    pass


class ProjectionError(ContextError):
    pass


_HEADERS = {"run_id", "session_id", "epoch", "base_context_version", "observation_id",
            "frame_revision", "ui_generation"}
_MEMORY = {"facts", "fact_updates", "hypotheses", "views", "transitions", "frontier",
           "unresolved_questions", "progress"}
_PERMISSIONS = {
    "coordinator": _MEMORY | {"milestone_plan", "active_assignment"},
    "operator": _MEMORY,
    "verifier": {"facts", "fact_updates", "progress", "unresolved_questions", "action_records",
                 "clear_pending_operation", "reconciliation_evidence_ids", "pending_resolution"},
    "recovery": _MEMORY,
    "runtime": {"current_ui", "evidence", "next_ui_generation", "invalidated_view_ids", "budgets",
                "action_records", "pending_operation", "active_assignment"},
}
_ROLES = {"coordinator", "operator", "verifier", "recovery", "analyst"}


def _fail(code: ErrorCode):
    raise PatchRejected(code)


def _validated(snapshot):
    # Revalidate even model_copy/model_construct outputs; do not trust instances.
    if not isinstance(snapshot, SharedTaskContext):
        raise ContextError("invalid_contract")
    return SharedTaskContext.model_validate(snapshot.model_dump(mode="python", warnings="none"))


def _append_unique(old, new):
    known = {item.id for item in old}
    ids = [item.id for item in new]
    if len(ids) != len(set(ids)) or known & set(ids):
        _fail("duplicate_id")
    return (*old, *new)


def _union(old, new):
    return tuple(dict.fromkeys((*old, *new)))


def _merge_map(old, new, *, evidence_fields):
    """Upsert discovery without losing observed labels/regions or evidence."""
    result = {item.id: item for item in old}
    if len({item.id for item in new}) != len(new):
        _fail("duplicate_id")
    for item in new:
        prior = result.get(item.id)
        if prior:
            data = item.model_dump()
            for name in evidence_fields:
                data[name] = _union(getattr(prior, name), getattr(item, name))
            # A semantic identity cannot be reassigned to a different endpoint/view.
            for name in ("run_id", "kind", "source_view_id", "semantic_control", "view_id",
                         "observed_option", "expected_view_id", "observed_view_id"):
                if hasattr(prior, name) and getattr(prior, name) is not None and (
                    getattr(prior, name) != getattr(item, name)
                ):
                    _fail("invalid_reference")
            item = type(item).model_validate(data)
        result[item.id] = item
    return tuple(result.values())


class ContextService:
    def __init__(self, *, max_projection_bytes: int = 65536, recent_actions: int = 10):
        if (not isinstance(max_projection_bytes, int) or max_projection_bytes <= 0
                or not isinstance(recent_actions, int) or not 0 <= recent_actions <= 100):
            raise ContextError("invalid_contract")
        self.max_projection_bytes = max_projection_bytes
        self.recent_actions = recent_actions

    def apply(self, snapshot: SharedTaskContext, patch: ContextPatch,
              *, actor_role: str) -> SharedTaskContext:
        """Validate a current-frame patch, returning a new, fully validated snapshot.

        Historical/stale patches are rejected, not silently rebased. Runtime-only
        fields require actor_role='runtime', a trusted internal capability. All
        failures are PatchRejected with a static code; the original is untouched.
        """
        try:
            source = _validated(snapshot)
            if not isinstance(patch, ContextPatch):
                _fail("invalid_contract")
            patch = ContextPatch.model_validate(patch.model_dump(exclude_unset=True, warnings="none"))
            return self._apply(source, patch, actor_role)
        except PatchRejected:
            raise
        except (ValidationError, ValueError, TypeError, ContextError):
            raise PatchRejected("invalid_contract") from None

    def _apply(self, snapshot, patch, actor):
        if actor not in _PERMISSIONS:
            _fail("invalid_role")
        for name, expected, actual, code in (
            ("run", snapshot.run_id, patch.run_id, "stale_run"),
            ("session", snapshot.session_id, patch.session_id, "stale_session"),
            ("epoch", snapshot.epoch, patch.epoch, "stale_epoch"),
            ("version", snapshot.context_version, patch.base_context_version, "stale_version"),
            ("observation", snapshot.current_ui.observation_id, patch.observation_id,
             "stale_observation"),
            ("frame", snapshot.current_ui.frame_revision, patch.frame_revision, "stale_frame"),
            ("generation", snapshot.ui_generation, patch.ui_generation, "stale_generation"),
        ):
            if expected != actual:
                _fail(code)
        touched = patch.model_fields_set - _HEADERS
        if touched - _PERMISSIONS[actor]:
            _fail("permission_denied")

        version = snapshot.context_version + 1
        data = snapshot.model_dump(mode="python")
        data["context_version"] = version
        data["evidence"] = _append_unique(snapshot.evidence, patch.evidence)
        generation = patch.next_ui_generation
        if generation is not None:
            if generation != snapshot.ui_generation + 1 or patch.current_ui is None:
                _fail("stale_generation")
            data["ui_generation"] = generation
        if patch.invalidated_view_ids and generation is None:
            _fail("stale_generation")
        if not set(patch.invalidated_view_ids) <= {v.id for v in snapshot.app_map.views}:
            _fail("invalid_reference")
        if patch.current_ui is not None:
            old, new = snapshot.current_ui, patch.current_ui
            if new.observation_id == old.observation_id:
                # Runtime may register grounded semantic interpretation of THIS frame.
                if (new.frame_revision, new.captured_at, new.ui_generation) != (
                    old.frame_revision, old.captured_at, old.ui_generation
                ) or generation is not None:
                    _fail("stale_observation")
            elif new.frame_revision < old.frame_revision or new.captured_at <= old.captured_at:
                _fail("stale_observation")
            if new.ui_generation != data["ui_generation"]:
                _fail("stale_generation")
            data["current_ui"] = new
        elif "current_ui" in touched:
            _fail("invalid_contract")

        evidence = {item.id: item for item in data["evidence"]}

        def check_evidence(ids, *, current=False):
            if not ids or not set(ids) <= evidence.keys():
                _fail("invalid_evidence")
            for key in ids:
                ref = evidence[key]
                if (ref.run_id, ref.session_id, ref.epoch) != (
                    snapshot.run_id, snapshot.session_id, snapshot.epoch
                ) or ref.ui_generation > data["ui_generation"]:
                    _fail("invalid_evidence")
            if current and patch.observation_id not in ids:
                _fail("invalid_evidence")

        if patch.budgets is not None:
            old, new = snapshot.budgets, patch.budgets
            if old.deadline_at != new.deadline_at:
                _fail("budget_violation")
            for name in ("remaining_seconds", "remaining_actions", "remaining_recoveries",
                         "remaining_advice_calls"):
                if getattr(new, name) > getattr(old, name):
                    _fail("budget_violation")
            if any(value > old.remaining_calls.model_dump()[name]
                   for name, value in new.remaining_calls.model_dump().items()):
                _fail("budget_violation")
            data["budgets"] = new
            # Assignment quotas are subordinate to the remaining runtime budget.
            if snapshot.active_assignment:
                assignment = snapshot.active_assignment.model_dump()
                assignment["budget"] = {
                    "seconds": min(snapshot.active_assignment.budget.seconds, new.remaining_seconds),
                    "actions": min(snapshot.active_assignment.budget.actions, new.remaining_actions),
                    "model_calls": min(snapshot.active_assignment.budget.model_calls,
                                       new.remaining_calls.operator),
                }
                data["active_assignment"] = assignment
        elif "budgets" in touched:
            _fail("invalid_contract")

        facts = {fact.id: fact for fact in snapshot.facts}
        _append_unique(snapshot.facts, patch.facts)
        for fact in patch.facts:
            check_evidence(fact.evidence_ids, current=True)
            if fact.author != actor or fact.provenance or fact.ui_generation != snapshot.ui_generation:
                _fail("invalid_provenance")
            if fact.status == "independently_verified" and actor != "verifier":
                _fail("permission_denied")
            if fact.status in {"contradicted", "stale"}:
                _fail("invalid_provenance")
            facts[fact.id] = Fact.model_validate({**fact.model_dump(), "provenance": (
                Provenance(author=actor, context_version=version, status=fact.status,
                           evidence_ids=fact.evidence_ids),
            )})
        if len({update.fact_id for update in patch.fact_updates}) != len(patch.fact_updates):
            _fail("duplicate_id")
        for update in patch.fact_updates:
            if update.fact_id not in facts:
                _fail("invalid_reference")
            prior = facts[update.fact_id]
            check_evidence(update.evidence_ids, current=True)
            if update.status == "independently_verified" and actor != "verifier":
                _fail("permission_denied")
            # Contradicted claims remain available with their original provenance.
            if prior.status == "contradicted" and update.status != "contradicted":
                _fail("invalid_provenance")
            if prior.status == "independently_verified" and update.status == "observed":
                _fail("invalid_provenance")
            facts[prior.id] = Fact.model_validate({
                **prior.model_dump(), "status": update.status,
                "evidence_ids": _union(prior.evidence_ids, update.evidence_ids),
                "contradicts": _union(prior.contradicts, update.contradicts),
                "ui_generation": snapshot.ui_generation,
                "provenance": (*prior.provenance, Provenance(
                    author=actor, context_version=version, status=update.status,
                    evidence_ids=update.evidence_ids)),
            })
        # Explicit contradictory assertions are symmetric, never a lossy summary.
        contradiction_sources = {f.id for f in patch.facts if f.contradicts}
        contradiction_sources.update(u.fact_id for u in patch.fact_updates if u.contradicts)
        for fact in tuple(facts[key] for key in contradiction_sources):
            for target in fact.contradicts:
                if target not in facts or target == fact.id:
                    _fail("invalid_reference")
                prior = facts[target]
                if fact.id not in prior.contradicts:
                    facts[target] = Fact.model_validate({
                        **prior.model_dump(), "status": "contradicted",
                        "evidence_ids": _union(prior.evidence_ids, fact.evidence_ids),
                        "contradicts": (*prior.contradicts, fact.id),
                        "provenance": (*prior.provenance, Provenance(
                            author=actor, context_version=version, status="contradicted",
                            evidence_ids=fact.evidence_ids)),
                    })
        data["facts"] = tuple(facts.values())
        for hypothesis in patch.hypotheses:
            if hypothesis.author != actor:
                _fail("invalid_provenance")
            for ids in (hypothesis.supporting_evidence_ids, hypothesis.opposing_evidence_ids):
                if ids:
                    check_evidence(ids)
        data["hypotheses"] = _append_unique(snapshot.hypotheses, patch.hypotheses)

        for view in patch.views:
            check_evidence(view.evidence_ids, current=True)
            if view.last_validated_generation != snapshot.ui_generation:
                _fail("stale_generation")
        for edge in patch.transitions:
            check_evidence(edge.before_evidence_ids)
            if edge.after_evidence_ids:
                check_evidence(edge.after_evidence_ids, current=True)
                if min(evidence[e].frame_revision for e in edge.after_evidence_ids) < max(
                    evidence[e].frame_revision for e in edge.before_evidence_ids
                ) or min(evidence[e].captured_at for e in edge.after_evidence_ids) <= max(
                    evidence[e].captured_at for e in edge.before_evidence_ids
                ):
                    _fail("invalid_evidence")
            if edge.last_validated_generation != snapshot.ui_generation:
                _fail("stale_generation")
        for option in patch.frontier:
            check_evidence(option.evidence_ids, current=True)
        app_map = PartialAppMap(
            views=_merge_map(snapshot.app_map.views, patch.views,
                             evidence_fields=("evidence_ids", "labels", "capabilities",
                                              "inspected_regions")),
            transitions=_merge_map(snapshot.app_map.transitions, patch.transitions,
                                   evidence_fields=("before_evidence_ids", "after_evidence_ids")),
            frontier=_merge_map(snapshot.app_map.frontier, patch.frontier,
                               evidence_fields=("evidence_ids",)),
        )

        if generation is not None:
            affected = set(patch.invalidated_view_ids)
            # No affected set means a broad reset; explicit set preserves unaffected routes.
            if not affected:
                affected = {view.id for view in app_map.views}
            app_map = PartialAppMap(
                views=app_map.views,
                transitions=tuple(type(edge).model_validate({
                    **edge.model_dump(), "outcome": "stale",
                }) if {edge.source_view_id, edge.observed_view_id, edge.expected_view_id} & affected
                    else edge for edge in app_map.transitions),
                frontier=app_map.frontier,
            )
            for fact in tuple(facts.values()):
                if fact.status != "contradicted" and fact.scope != "task" and (
                    fact.view_id is None or fact.view_id in affected
                ):
                    facts[fact.id] = Fact.model_validate({
                        **fact.model_dump(), "status": "stale",
                        "provenance": (*fact.provenance, Provenance(
                            author="runtime", context_version=version, status="stale",
                            evidence_ids=(patch.current_ui.observation_id,))),
                    })
            data["facts"] = tuple(facts.values())
            data["advice_records"] = tuple(type(advice).model_validate({
                **advice.model_dump(), "status": "stale",
            }) for advice in snapshot.advice_records)
        data["app_map"] = app_map
        reference_ui = patch.current_ui or snapshot.current_ui
        for fact in tuple(facts.values()):
            if fact.valid_until and fact.valid_until <= reference_ui.captured_at and fact.status in {
                "observed", "independently_verified",
            }:
                facts[fact.id] = Fact.model_validate({
                    **fact.model_dump(), "status": "stale",
                    "provenance": (*fact.provenance, Provenance(
                        author="runtime", context_version=version, status="stale",
                        evidence_ids=(reference_ui.observation_id,))),
                })
        data["facts"] = tuple(facts.values())
        if patch.unresolved_questions is not None:
            data["unresolved_questions"] = patch.unresolved_questions

        if patch.progress is not None:
            old_satisfied = {c.criterion_id: c for c in snapshot.progress.satisfied_criteria}
            new_satisfied = {c.criterion_id: c for c in patch.progress.satisfied_criteria}
            if actor != "verifier" and new_satisfied != old_satisfied:
                _fail("permission_denied")
            if patch.progress.no_progress_count < snapshot.progress.no_progress_count:
                newly_useful = set(patch.progress.useful_transition_ids) - set(
                    snapshot.progress.useful_transition_ids)
                useful_edges = [edge for edge in app_map.transitions if edge.id in newly_useful
                                and edge.outcome == "confirmed"
                                and patch.observation_id in edge.after_evidence_ids]
                if not useful_edges and not (new_satisfied.keys() - old_satisfied.keys()):
                    _fail("invalid_provenance")
            data["progress"] = patch.progress
        # A criterion is never kept satisfied after its support becomes stale/contradicted.
        progress = data["progress"]
        if isinstance(progress, dict):
            progress = Progress.model_validate(progress)
        valid_criteria = []
        lost = []
        for criterion in progress.satisfied_criteria:
            if not set(criterion.fact_ids) <= facts.keys():
                _fail("invalid_reference")
            verified = all(facts[fid].status == "independently_verified"
                           for fid in criterion.fact_ids)
            if not verified:
                if patch.progress is not None:
                    _fail("invalid_provenance")
                lost.append(criterion.criterion_id)
                continue
            check_evidence(criterion.evidence_ids)
            supported = {eid for fid in criterion.fact_ids
                         for eid in facts[fid].provenance[-1].evidence_ids}
            if not set(criterion.evidence_ids) <= supported:
                _fail("invalid_evidence")
            valid_criteria.append(criterion)
        data["progress"] = {**progress.model_dump(), "satisfied_criteria": tuple(valid_criteria),
                            "remaining_criterion_ids": _union(progress.remaining_criterion_ids, lost)}

        if patch.milestone_plan is not None:
            old = {m.id: m for m in snapshot.milestone_plan}
            new = {m.id: m for m in patch.milestone_plan}
            if not old.keys() <= new.keys():
                _fail("invalid_reference")
            criteria = {c.id for c in snapshot.task_contract.success_criteria}
            if any(m.run_id != snapshot.run_id for m in patch.milestone_plan):
                _fail("stale_run")
            if any(not set(m.acceptance_criterion_ids) <= criteria for m in patch.milestone_plan):
                _fail("invalid_reference")
            data["milestone_plan"] = patch.milestone_plan
        if "active_assignment" in touched:
            old, new = snapshot.active_assignment, patch.active_assignment
            if new:
                milestones = data["milestone_plan"]
                known = {m.id if isinstance(m, Milestone) else m["id"] for m in milestones}
                if new.run_id != snapshot.run_id:
                    _fail("stale_run")
                if new.milestone_id not in known:
                    _fail("invalid_reference")
            if actor == "runtime" and (old is None or new is None or {
                **new.model_dump(), "budget": old.budget.model_dump()
            } != old.model_dump()):
                _fail("permission_denied")
            if old and new and old.milestone_id == new.milestone_id and any(
                getattr(new.budget, name) > getattr(old.budget, name)
                for name in ("seconds", "actions", "model_calls")
            ):
                _fail("budget_violation")
            data["active_assignment"] = patch.active_assignment

        actions = {record.id: record for record in snapshot.action_history}
        if len({a.id for a in patch.action_records}) != len(patch.action_records):
            _fail("duplicate_id")
        for record in patch.action_records:
            prior = actions.get(record.id)
            check_evidence((record.source_observation_id,))
            if prior is None:
                if actor != "runtime" or record.actor != "runtime" or record.outcome == "confirmed":
                    _fail("permission_denied")
                if record.reconciliation is not None:
                    _fail("permission_denied")
                if snapshot.pending_operation and record.outcome in {"sent", "uncertain"}:
                    _fail("pending_operation")
            else:
                if record.reconciliation != prior.reconciliation:
                    _fail("invalid_action_transition")
                for name in ("run_id", "actor", "milestone_id", "source_observation_id",
                             "semantic_goal", "expected_effect"):
                    if getattr(record, name) != getattr(prior, name):
                        _fail("invalid_action_transition")
                allowed = {
                    "rejected": {"rejected"}, "not_sent": {"not_sent", "sent", "uncertain"},
                    "sent": {"sent", "uncertain", "confirmed"},
                    # Trusted acknowledgement of the SAME attempt is not a replay
                    # and still does not confirm an effect or clear pending.
                    "uncertain": {"uncertain", "sent", "confirmed"}, "confirmed": {"confirmed"},
                }
                if record.outcome not in allowed[prior.outcome]:
                    _fail("invalid_action_transition")
                if prior.outcome == "confirmed" and record != prior:
                    _fail("invalid_action_transition")
            if record.outcome == "confirmed":
                if actor != "verifier":
                    _fail("permission_denied")
                self._verified_support(record.effect_fact_ids, record.effect_evidence_ids,
                                       facts, evidence, record.source_observation_id)
                if record.subsequent_observation_id not in record.effect_evidence_ids:
                    _fail("invalid_evidence")
            elif actor != "runtime":
                _fail("permission_denied")
            actions[record.id] = record
        data["action_history"] = tuple(actions.values())

        pending = snapshot.pending_operation
        if "pending_operation" in touched:
            if patch.pending_operation is None:
                _fail("pending_operation")
            proposed = patch.pending_operation
            if pending and proposed != pending:
                if {**proposed.model_dump(), "status": pending.status} != pending.model_dump() or (
                    proposed.status != "uncertain"
                ):
                    _fail("pending_operation")
            if proposed.action_id not in actions or actions[proposed.action_id].outcome not in {
                "sent", "uncertain",
            }:
                _fail("pending_operation")
            check_evidence(proposed.evidence_ids)
            data["pending_operation"] = proposed
        if patch.clear_pending_operation:
            resolution = patch.pending_resolution
            if actor != "verifier" or pending is None or resolution is None or (
                resolution.action_id != pending.action_id
            ):
                _fail("pending_operation")
            if tuple(patch.reconciliation_evidence_ids) != tuple(resolution.evidence_ids):
                _fail("invalid_evidence")
            self._verified_support(resolution.verified_fact_ids, resolution.evidence_ids, facts,
                                   evidence, actions[pending.action_id].source_observation_id)
            if patch.observation_id not in resolution.evidence_ids:
                _fail("invalid_evidence")
            if resolution.outcome == "applied" and actions[pending.action_id].outcome != "confirmed":
                _fail("pending_operation")
            if resolution.outcome == "not_applied" and (
                actions[pending.action_id].outcome == "confirmed"
            ):
                _fail("pending_operation")
            data["pending_operation"] = None
            original_action = actions[pending.action_id]
            actions[pending.action_id] = type(original_action).model_validate({
                **original_action.model_dump(), "reconciliation": resolution,
            })
        elif patch.pending_resolution is not None or patch.reconciliation_evidence_ids:
            _fail("pending_operation")
        data["action_history"] = tuple(actions.values())

        satisfied = {c.criterion_id for c in valid_criteria}
        milestones = data["milestone_plan"]
        checked_milestones = []
        for milestone in milestones:
            if isinstance(milestone, dict):
                milestone = Milestone.model_validate(milestone)
            if milestone.status == "completed" and (
                not set(milestone.acceptance_criterion_ids) <= satisfied
                or data["pending_operation"] is not None
            ):
                if patch.milestone_plan is not None:
                    _fail("invalid_provenance")
                milestone = Milestone.model_validate({**milestone.model_dump(), "status": "blocked"})
            checked_milestones.append(milestone)
        while True:
            completed_ids = {m.id for m in checked_milestones if m.status == "completed"}
            blocked_ids = {m.id for m in checked_milestones if m.status in {"active", "completed"}
                           and not set(m.dependencies) <= completed_ids}
            if not blocked_ids:
                break
            if patch.milestone_plan is not None:
                _fail("invalid_provenance")
            checked_milestones = [Milestone.model_validate({**m.model_dump(), "status": "blocked"})
                                  if m.id in blocked_ids else m for m in checked_milestones]
        data["milestone_plan"] = tuple(checked_milestones)
        # Bounded models fail closed, rather than dropping old provenance silently.
        if any(len(data[key]) > limit for key, limit in (
            ("facts", 100), ("evidence", 200), ("action_history", 100), ("hypotheses", 40),
        )):
            _fail("limit_exceeded")
        return SharedTaskContext.model_validate(data)

    @staticmethod
    def _verified_support(fact_ids, evidence_ids, facts, evidence, source_observation_id):
        if not fact_ids or not evidence_ids or not set(fact_ids) <= facts.keys() or (
            not set(evidence_ids) <= evidence.keys() or source_observation_id not in evidence
        ):
            _fail("invalid_evidence")
        if any(facts[key].status != "independently_verified" for key in fact_ids):
            _fail("invalid_provenance")
        supported = {eid for key in fact_ids for eid in facts[key].provenance[-1].evidence_ids}
        if not set(evidence_ids) <= supported or any(
            evidence[key].frame_revision <= evidence[source_observation_id].frame_revision
            for key in evidence_ids
        ):
            _fail("invalid_evidence")

    def project(self, snapshot: SharedTaskContext, *, role: str) -> ContextSlice:
        """Keep every critical field; omit only reconciled old action records by ID.

        Coordinator gets the full semantic map without actionable coordinates.
        Verifier/Recovery get before/after references for retained actions. All
        roles receive identity, restrictions, contradictions and pending state.
        A byte limit is a deterministic transport bound, not a token estimator.
        """
        try:
            if role not in _ROLES:
                raise ProjectionError("invalid_role")
            source = _validated(snapshot)
            recent_ids = {a.id for a in source.action_history[-self.recent_actions:]}
            if self.recent_actions == 0:
                recent_ids = set()
            if source.pending_operation:
                recent_ids.add(source.pending_operation.action_id)
            # Uncertain/sent actions stay critical even if a pending marker was not registered yet.
            recent_ids.update(a.id for a in source.action_history
                              if a.outcome in {"uncertain", "sent"} and a.reconciliation is None)
            actions = tuple(a for a in source.action_history if a.id in recent_ids)
            ui = source.current_ui
            if role in {"coordinator", "analyst"}:
                ui = type(ui).model_validate({**ui.model_dump(), "visible_controls": tuple(
                    type(control).model_validate({**control.model_dump(), "bounding_box": None})
                    for control in ui.visible_controls
                )})
            data = source.model_dump()
            data.pop("advice_records")
            data.update(role=role, current_ui=ui, action_history=actions,
                        omitted_action_ids=tuple(a.id for a in source.action_history
                                                 if a.id not in recent_ids))
            # Evidence closure for all retained information (no missing fetch references).
            needed = {ui.observation_id}

            def collect(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in {"evidence_ids", "supporting_evidence_ids", "opposing_evidence_ids",
                                   "before_evidence_ids", "after_evidence_ids", "effect_evidence_ids"}:
                            needed.update(item)
                        elif key in {"source_observation_id", "subsequent_observation_id",
                                     "observation_id"} and item is not None:
                            needed.add(item)
                        else:
                            collect(item)
                elif isinstance(value, (tuple, list)):
                    for item in value:
                        collect(item)

            for key in ("facts", "hypotheses", "app_map", "progress", "active_assignment",
                        "pending_operation"):
                collect(data[key])
            for action in actions:
                collect(action.model_dump())
            data["evidence"] = tuple(ref for ref in source.evidence if ref.id in needed)
            result = ContextSlice.model_validate(data)
            if len(result.model_dump_json().encode("utf-8")) > self.max_projection_bytes:
                raise ProjectionError("insufficient_context")
            return result
        except ProjectionError:
            raise
        except (ValidationError, ValueError, TypeError, ContextError):
            raise ProjectionError("invalid_contract") from None
