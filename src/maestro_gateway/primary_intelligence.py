"""Sovereign single-primary local inference policy, subordinate to the Core.

Only one primary conversational model is selected at once. The manager owns NO
model execution, credentials or file writes and cannot grant permissions. It
selects from the Core's already-authorized local resource descriptors. An
explicit user model selection remains a separate, governed per-call override.

The first selection and subsequent decisions use in-memory evidence only.
Failures or stale resource removal may trigger an autonomous, atomic switch;
models are NEVER installed, started or routed to a cloud service here.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
import time
from typing import Any

from .adaptive_router import (
    RouteDecision, choose_adaptive_resource, classify_request,
    _language_code, _eligible,
)
from .model_qualification import qualification
from .autonomous_qualification import probe_evidence, model_version


@dataclass(frozen=True)
class PrimarySelection:
    decision: RouteDecision
    epoch: int
    reason: str


class PrimaryIntelligenceManager:
    def __init__(self) -> None:
        self._lock = RLock()
        self._primary: str | None = None
        self._epoch = 0
        self._reason = "no_model_selected"
        self._quarantine: dict[str, float] = {}
        self._cooldown_seconds = 90.0
        self._inference_proven = False

    @staticmethod
    def _eligible_local(registry: Any, task_class: str) -> dict[str, Any]:
        """Inspect live Core descriptors once, without caching permissions.

        Locality is an absolute eligibility constraint; class preferences
        apply only within the authorized local pool. No cloud-only CODE
        candidate may shadow an otherwise eligible local conversational model.
        """
        general: dict[str, Any] = {}
        preferred: dict[str, Any] = {}
        for d in registry.all():
            rid = getattr(d, 'resource_id', None)
            if not rid or getattr(d, 'local', None) is not True:
                continue
            status = getattr(d, 'status', 'AVAILABLE')
            if str(getattr(status, 'value', status)) in {'UNAVAILABLE', 'OFFLINE'}:
                continue
            conversational = False
            code = False
            for cap in (getattr(d, 'capabilities', ()) or ()):
                value = getattr(cap, 'value', cap)
                name = value.upper() if isinstance(value, str) else str(value).upper()
                if name in {'DIRECT', 'GENERAL_REASONING'}:
                    conversational = True
                elif name == 'CODE':
                    code = True
            if not conversational:
                continue
            rid = str(rid)
            general[rid] = d
            if ((task_class == 'code' and code)
                    or (task_class in {'quick', 'normal'} and not code)):
                preferred[rid] = d
        return preferred if preferred else general

    def select(self, registry: Any, prompt: str, *, workspace_active: bool = False,
               max_tokens: int = 512, ui_language: str | None = None) -> PrimarySelection:
        """Reuse the single primary; switch for proven task/language mismatch.

        This does not trigger a load, disk access, evaluation or network call.
        A provisional model is not considered proven fast or fluent.
        """
        task = classify_request(prompt, workspace_active=workspace_active,
                                ui_language=ui_language)
        language = _language_code(prompt, ui_language)
        with self._lock:
            now = time.monotonic()
            # A timeout is not proof that a model is permanently defective;
            # it is grounds for a bounded cooldown, not endless retries.
            if self._quarantine:
                self._quarantine = {rid: until for rid, until in self._quarantine.items()
                                    if until > now}
            available = self._eligible_local(registry, task)
            if self._quarantine:
                available = {rid: d for rid, d in available.items()
                             if rid not in self._quarantine}
            if not available:
                if self._primary is not None:
                    self._primary = None
                    self._epoch += 1
                    self._inference_proven = False
                self._reason = "all_local_models_unavailable_or_cooling_down"
                raise RuntimeError(
                    "Aucun modèle local de conversation disponible : moteur absent ou "
                    "modèles temporairement en récupération après échec."
                )

            old = self._primary
            if old not in available:
                old = None

            # Select only when necessary; ordinary chat keeps its ONE primary.
            # A model with real same-task/language evidence may replace an
            # unqualified incumbent. This is competence-based, not brand-based.
            candidate = None
            if old is None:
                candidate = choose_adaptive_resource(
                    _RestrictedRegistry(available.values()), prompt,
                    workspace_active=workspace_active, max_tokens=max_tokens,
                    ui_language=ui_language)
                selected = candidate.resource_id
                reason = "initial_selection" if self._primary is None else "resource_unavailable"
            else:
                selected = old
                reason = "primary_reused"
                current_q = qualification(old, task, language)
                if not current_q.qualified:
                    # Only replace a provisional/nonperforming incumbent if
                    # alternative task+language evidence is actually qualified.
                    qualified = {rid: d for rid, d in available.items()
                                 if qualification(rid, task, language).qualified}
                    # Probe evidence alone never declares a model semantically
                    # qualified. If the primary repeatedly failed both simple
                    # operational probes, however, a two-probe-passing
                    # alternative may be selected for a PROVISIONAL trial.
                    if not qualified:
                        failing = probe_evidence(old, model_version(available[old]), language)
                        if failing['fail'] >= 3 and failing['pass'] == 0:
                            qualified = {rid: d for rid, d in available.items()
                                         if rid != old and
                                         probe_evidence(rid, model_version(d), language)['pass'] >= 3
                                         and probe_evidence(rid, model_version(d), language)['fail'] == 0}
                    if qualified:
                        best = choose_adaptive_resource(
                            _RestrictedRegistry(qualified.values()), prompt,
                            workspace_active=workspace_active, max_tokens=max_tokens,
                            ui_language=ui_language)
                        if best.resource_id != old:
                            selected = best.resource_id
                            candidate = best
                            reason = "task_language_evidence"

            if selected != self._primary:
                self._primary = selected
                self._epoch += 1
                self._inference_proven = False
            self._reason = reason
            q = qualification(selected, task, language)
            # Output tokens are a RESPONSE budget, not routing computation.
            # The previous 160-token ceiling on normal conversation produced
            # accepted mid-sentence answers on real small local models.
            # Keep the explicit caller bound; only truly brief tasks may use
            # a smaller budget. No model/provider is preferred here.
            budget = min(max_tokens, 160) if task == "quick" else max_tokens
            decision = RouteDecision(
                resource_id=selected, task_class=task, prompt_budget=budget,
                evidence_count=q.attempts, policy="single_local_primary_v1",
                qualification_status="operationally_qualified" if q.qualified else "provisional",
                language=language)
            return PrimarySelection(decision=decision, epoch=self._epoch, reason=reason)

    def failover(self, *, failed_id: str, replacement_id: str | None,
                 expected_epoch: int, registry: Any, reason: str) -> bool:
        """Compare-and-swap prevents late parallel attempts undoing a switch."""
        if reason not in {"timeout", "transport_failure", "quality_rejected"}:
            raise ValueError("Unsupported primary switch reason")
        with self._lock:
            if self._epoch != expected_epoch or self._primary != failed_id:
                return False
            if replacement_id is not None:
                # Fallback is already qualified by the mission's retry policy.
                allowed = self._eligible_local(registry, "normal")
                if (replacement_id not in allowed or replacement_id == failed_id
                        or self._quarantine.get(replacement_id, 0.0) > time.monotonic()):
                    return False
            self._quarantine[failed_id] = time.monotonic() + self._cooldown_seconds
            # None invalidates the failed model, forcing requalification/choice
            # on the next request. This does not claim a replacement succeeded.
            self._primary = replacement_id
            self._epoch += 1
            self._inference_proven = False
            self._reason = "switched_after_" + reason if replacement_id else "invalidated_after_" + reason
            return True

    def reconcile(self, registry: Any) -> None:
        """Registry refresh may remove engines; never retain a ghost model."""
        with self._lock:
            if self._primary is not None and self._primary not in self._eligible_local(registry, "normal"):
                self._primary = None
                self._epoch += 1
                self._inference_proven = False
                self._reason = "resource_disappeared"

    def note_success(self, resource_id: str, *, expected_epoch: int | None = None) -> bool:
        """Proof of one completed local conversation, not semantic qualification."""
        with self._lock:
            if self._primary != resource_id or (expected_epoch is not None and self._epoch != expected_epoch):
                return False
            self._inference_proven = True
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"mode": "single_local_primary", "primary_resource_id": self._primary,
                    "epoch": self._epoch, "reason": self._reason,
                    "cloud_enabled": False, "primary_selected": self._primary is not None,
                    "inference_ready": self._inference_proven,
                    "cooling_down_models": len(self._quarantine)}


class _RestrictedRegistry:
    def __init__(self, descriptors: Any) -> None:
        self._descriptors = tuple(descriptors)

    def all(self) -> tuple[Any, ...]:
        return self._descriptors
