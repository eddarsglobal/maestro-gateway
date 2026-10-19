from __future__ import annotations

import hashlib
import os
import json
import re
import time
from typing import Any

from maestro.autonomous.runtime import ResearchFetchCancelled
from maestro.enums import (
    CouncilId,
    EvidenceKind,
    MissionState,
    VerdictScope,
    VerdictValue,
)
from maestro.models import Evidence, RoutingRequest, Verdict
from maestro.orchestration.dag import DagNode
from maestro.sovereign import IntelligenceCapability, IntelligenceRequest
from maestro.providers.ollama.client import OllamaHTTPError

from .ollama_gateway_client import (MISSION_DEADLINE, MISSION_CANCEL, MISSION_STREAM_POLICY,
                                    MISSION_FOREGROUND, reset_stream_metrics, current_stream_metrics)
from .mission_control import MissionCancelledError
from .primary_intelligence import PrimarySelection
from .capabilities import CapabilityRegistry
from .adaptive_router import (choose_adaptive_resource, choose_quality_fallback,
                              choose_timeout_fallback, _language_code, classify_request,
                              response_language_hint)
from .model_qualification import observe_attempt
from .semantic_competence import observe_semantic_attempt
from .semantic_verifier import (
    deterministic_expectation, deterministic_truth_projection,
    verify_deterministic_response,
)
from .response_effort import (bounded_response_tokens, local_execution_profile,
                             reconcile_bounded_structure_budget,
                              quality_retry_allowed, quality_attempt_outcome,
                              compact_recent_context, compact_chat_instruction,
                              direct_fast_lane_eligible, direct_fast_instruction,
                              stream_completion_policy)
from .conversations import ConversationStore
from .core import MaestroCoreBridge
from .response_guard import (QualityResult, evaluate_response, asks_about_capabilities,
                             asks_about_previous_language)
from .workspace import WorkspaceRegistry


def _semantic_recovery_attempt_count(selection: PrimarySelection | None) -> int:
    """Count governed semantic recovery selections for externally visible telemetry."""
    return int(selection is not None and selection.reason == "semantic_recovery_trial")


def _response_origin_from_attempts(
    attempts: list[dict[str, Any]], *, deterministic_core: bool = False
) -> str:
    if deterministic_core:
        return "local_deterministic_core"
    if attempts and attempts[-1].get("semantic_projection_applied"):
        return "local_deterministic_verifier"
    return "local_model"




def _routing_semantic_subtask(contract: Any | None, semantic_subtask: str | None) -> str | None:
    """Do not trigger an LLM competence switch for locally executable truth."""
    return None if contract is not None else semantic_subtask


def _deterministic_core_allowed(contract: Any | None, model: str | None) -> bool:
    """AUTO may use the Core lane; an explicit model override remains authoritative."""
    return contract is not None and not str(model or "").strip()


def _deterministic_core_preflight(
    prompt: str, *, workspace_active: bool, ui_language: str | None, model: str | None
) -> tuple[str, bool, Any | None, str | None, str | None]:
    """Resolve model-independent deterministic eligibility before model routing.

    V8.11.5 invariant: locally proven atomic work is a Core capability, not a
    model capability.  Therefore zero models, model removal, or complete model
    inventory replacement cannot disable this lane.  Explicit user model
    selection still opts out and is handled by ordinary governed inference.
    """
    task = classify_request(
        prompt, workspace_active=workspace_active, ui_language=ui_language
    )
    fast_lane = (
        not asks_about_capabilities(prompt)
        and not asks_about_previous_language(prompt)
        and direct_fast_lane_eligible(
            prompt, task, workspace_active=workspace_active
        )
    )
    # FIXED3: an exact, independently proven extraction is Core eligible in AUTO.
    # The default fast-lane classifier may miss negations and ordinal variants.
    # Never override a user-selected model, workspace policy or prior-turn intent.
    if (
        not fast_lane
        and not workspace_active
        and not str(model or "").strip()
        and not asks_about_previous_language(prompt)
    ):
        extraction_proof = deterministic_expectation(prompt)
        if extraction_proof is not None and extraction_proof.subtask == "extraction":
            fast_lane = True
    contract = deterministic_expectation(prompt) if fast_lane else None
    subtask = contract.subtask if contract is not None else None
    candidate = (
        _deterministic_core_candidate(contract)
        if fast_lane and _deterministic_core_allowed(contract, model)
        else None
    )
    return task, fast_lane, contract, subtask, candidate


def _should_note_model_success(*, ok: bool, primary_selection: Any | None, resource_calls: int) -> bool:
    """Model evidence is legal only when a model actually executed."""
    return bool(ok and primary_selection is not None and resource_calls > 0)
def _deterministic_core_candidate(contract: Any | None) -> str | None:
    """Return only a locally proven DIRECT answer; never infer open-ended text.

    V8.11.4 uses this lane only after ``deterministic_expectation`` has already
    proved the tiny request.  Unsupported prompts remain on the normal single-
    primary LLM path.  This helper performs no routing, I/O, model loading or
    authority mutation.
    """
    if contract is None:
        return None
    value = str(getattr(contract, "expected", "") or "").strip()
    return value or None



_TOOL_PATTERN = re.compile(
    r"^\s*<MAESTRO_TOOL>\s*(\{.*\})\s*</MAESTRO_TOOL>\s*$",
    re.DOTALL,
)

MAX_TOOL_ROUNDS = 5
MAX_QUALITY_RETRIES = 1


# A general "who are you / what can you do" requires evidence, but not a
# 20-capability API dump or a 512-token monologue. This is *only* a prompt and
# bounded output optimization: a real authorized LLM must still answer.
_FAST_INTRO_DETAIL = re.compile(
    r"\b(?:d[eé]tail(?:le[rz]?)?|exhausti(?:f|ve)|complet(?:e|ement)?|"
    r"pr[eé]cis[eé]ment|toutes?\s+(?:tes|vos)\s+capacit[eé]s|"
    r"en\s+profondeur|in\s+detail|comprehensive|exhaustive|"
    r"ausf[uü]hrlich|dettagliat[oa]|detaljn\w*)\b", re.I,
)
_FAST_INTRO_IDS = (
    "conversation.context", "local_inference.execute", "workspace.open",
    "filesystem.read", "filesystem.search", "filesystem.write",
    "git.status", "git.commit", "computer_use",
)


def _fast_capability_intro(prompt: str, workspace_active: bool) -> bool:
    """Broad, short, no-workspace self-introduction; no canned response."""
    return (not workspace_active and asks_about_capabilities(prompt)
            and len(prompt.strip()) <= 115
            and not _FAST_INTRO_DETAIL.search(prompt))


def _intro_capability_facts(snapshot: list[dict[str, Any]]) -> str:
    """Live registry facts, never a static list of asserted permissions."""
    by_id = {str(row.get("capability_id")): row for row in snapshot}
    lines = ["VERIFIED MAESTRO CAPABILITIES (CURRENT SESSION):"]
    for name in _FAST_INTRO_IDS:
        item = by_id.get(name)
        if item is None:
            continue
        # Keep exact operational status (including disabled and approval-only)
        # from the current registry. Omit lengthy internal schema/protocol text.
        description = str(item.get("description", "")).strip()
        lines.append(f"- {name} [{item.get('status', 'unknown')}]: "
                     + description[:150])
    return "\n".join(lines)


def _capability_names(resource: Any) -> set[str]:
    values: set[str] = set()

    for item in getattr(resource, "capabilities", ()) or ():
        name = getattr(item, "name", None)
        value = getattr(item, "value", None)
        values.add(
            str(name or value or item).upper()
        )

    return values


def _requested_resource_id(
    model: str | None,
) -> str | None:
    if model is None:
        return None

    value = model.strip()

    if not value:
        return None

    # Never imply a provider for a bare model name.
    return value


def choose_direct_resource(
    registry: Any,
    model: str | None = None,
) -> str:
    resources = list(registry.all())
    by_id = {
        str(
            getattr(
                resource,
                "resource_id",
                "",
            )
        ): resource
        for resource in resources
    }

    requested = model.strip() if isinstance(model, str) and model.strip() else None
    if requested is not None and requested not in by_id and "::" not in requested:
        matches = [rid for rid in by_id if rid.split("::", 1)[-1] == requested]
        if len(matches) > 1:
            raise ValueError("Model name ambiguous across runtimes; select an exact resource ID")
        requested = matches[0] if matches else requested

    if requested is not None:
        resource = by_id.get(requested)

        if resource is None:
            raise ValueError(
                "Requested local resource is not "
                f"registered: {requested}"
            )

        caps = _capability_names(resource)

        if (
            "DIRECT" not in caps
            and "GENERAL_REASONING" not in caps
        ):
            raise ValueError(
                "Requested resource is not eligible "
                f"for DIRECT work: {requested}"
            )

        return requested

    eligible: list[str] = []

    for resource in resources:
        resource_id = str(
            getattr(
                resource,
                "resource_id",
                "",
            )
        )
        caps = _capability_names(resource)

        if (
            resource_id
            and (
                "DIRECT" in caps
                or "GENERAL_REASONING" in caps
            )
        ):
            eligible.append(resource_id)

    if eligible:
        return min(eligible, key=lambda rid: __import__("hashlib").sha256(rid.encode()).digest())  # vendor-neutral deterministic fallback

    raise RuntimeError(
        "No local DIRECT/GENERAL_REASONING "
        "intelligence resource is available."
    )


def validate_input(
    prompt: Any,
    max_tokens: Any,
) -> tuple[str, int]:
    if not isinstance(prompt, str):
        raise ValueError(
            "prompt must be a string"
        )

    prompt = prompt.strip()

    if not prompt:
        raise ValueError(
            "prompt must not be empty"
        )

    if len(prompt) > 32_000:
        raise ValueError(
            "prompt exceeds the P0.6A "
            "32,000-character limit"
        )

    if isinstance(max_tokens, bool):
        raise ValueError(
            "max_tokens must be an integer"
        )

    try:
        tokens = int(max_tokens)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "max_tokens must be an integer"
        ) from exc

    if not 1 <= tokens <= 2048:
        raise ValueError(
            "max_tokens must be between 1 and 2048"
        )

    return prompt, tokens


def _conversation_context(
    messages: list[dict[str, Any]],
) -> str:
    rows: list[str] = []

    for item in messages[-16:]:
        role = str(
            item.get("role") or ""
        ).upper()
        content = str(
            item.get("content") or ""
        ).strip()

        if (
            role in {"USER", "ASSISTANT"}
            and content
        ):
            label = (
                "USER"
                if role == "USER"
                else "MAESTRO"
            )
            rows.append(
                f"{label}: {content}"
            )

    return "\n".join(rows)


def _normalize_tool_arguments(
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Validate and normalize one model-requested MAESTRO tool call."""

    if name == "workspace.read":
        path = str(arguments.get("path") or "").strip()

        if path in {"", ".", "/"}:
            raise ValueError(
                "workspace.read requires a non-empty relative FILE path, "
                'for example {"path":"package.json"}. '
                "Do not use workspace.read on the workspace root or a directory."
            )

        return {"path": path}

    if name == "workspace.search":
        query = str(arguments.get("query") or "").strip()

        if not query:
            raise ValueError(
                "workspace.search requires a non-empty query, "
                'for example {"query":"MAESTRO"}.'
            )

        return {"query": query}

    if name == "workspace.tree":
        path = str(arguments.get("path") or "").strip()

        try:
            max_depth = int(arguments.get("max_depth") or 3)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "workspace.tree max_depth must be an integer"
            ) from exc

        return {
            "path": path,
            "max_depth": max(0, min(max_depth, 6)),
        }

    if name in {
        "git.status",
        "git.branch_current",
    }:
        if arguments:
            raise ValueError(
                f"{name} does not accept arguments"
            )

        return {}

    if name == "git.diff":
        unknown = (
            set(arguments)
            - {"scope"}
        )

        if unknown:
            raise ValueError(
                "git.diff accepts only scope"
            )

        scope = str(
            arguments.get("scope")
            or "worktree"
        ).strip()

        if scope not in {
            "worktree",
            "staged",
        }:
            raise ValueError(
                "git.diff scope must be "
                "worktree or staged"
            )

        return {
            "scope": scope,
        }

    if name == "git.log":
        unknown = (
            set(arguments)
            - {"max_count"}
        )

        if unknown:
            raise ValueError(
                "git.log accepts only max_count"
            )

        raw_max_count = arguments.get(
            "max_count",
            10,
        )

        if isinstance(
            raw_max_count,
            bool,
        ):
            raise ValueError(
                "git.log max_count must "
                "be an integer"
            )

        try:
            max_count = int(
                raw_max_count
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "git.log max_count must "
                "be an integer"
            ) from exc

        if not 1 <= max_count <= 50:
            raise ValueError(
                "git.log max_count must "
                "be between 1 and 50"
            )

        return {
            "max_count": max_count,
        }

    if name == "terminal.run":
        raw_argv = arguments.get("argv")

        if (
            not isinstance(raw_argv, list)
            or not raw_argv
        ):
            raise ValueError(
                "terminal.run requires a non-empty argv list"
            )

        if len(raw_argv) > 8:
            raise ValueError(
                "terminal.run accepts at most 8 argv items"
            )

        argv: list[str] = []

        for item in raw_argv:
            if not isinstance(item, str):
                raise ValueError(
                    "terminal.run argv items must be strings"
                )

            value = item.strip()

            if (
                not value
                or "\x00" in value
            ):
                raise ValueError(
                    "terminal.run argv items must be non-empty strings"
                )

            if len(value) > 256:
                raise ValueError(
                    "terminal.run argv item exceeds size limit"
                )

            argv.append(value)

        cwd = str(
            arguments.get("cwd") or "."
        ).strip()

        if (
            not cwd
            or "\x00" in cwd
        ):
            raise ValueError(
                "terminal.run cwd must be a relative workspace path"
            )

        raw_timeout = arguments.get(
            "timeout_ms",
            3000,
        )

        if isinstance(
            raw_timeout,
            bool,
        ):
            raise ValueError(
                "terminal.run timeout_ms must be an integer"
            )

        try:
            timeout_ms = int(
                raw_timeout
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "terminal.run timeout_ms must be an integer"
            ) from exc

        if not 100 <= timeout_ms <= 5000:
            raise ValueError(
                "terminal.run timeout_ms must be between 100 and 5000"
            )

        return {
            "argv": argv,
            "cwd": cwd,
            "timeout_ms": timeout_ms,
        }

    raise ValueError(
        f"Tool is not available in P0.6E.1: {name}"
    )


def _parse_tool_call(
    output: str,
) -> tuple[str, dict[str, Any]] | None:
    match = _TOOL_PATTERN.fullmatch(
        str(output or "")
    )

    if not match:
        return None

    try:
        payload = json.loads(
            match.group(1)
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Invalid MAESTRO tool request JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            "Tool request must be a JSON object"
        )

    name = str(
        payload.get("name") or ""
    ).strip()
    arguments = payload.get(
        "arguments",
        {},
    )

    if not name:
        raise ValueError(
            "Tool request name is required"
        )

    if not isinstance(
        arguments,
        dict,
    ):
        raise ValueError(
            "Tool arguments must be an object"
        )

    return name, arguments


class GovernedDirectMissionRunner:
    """P0.6A governed conversational DIRECT execution.

    Workspace read/search/tree calls are executed by MAESTRO Gateway,
    never directly by a model.
    """

    def __init__(
        self,
        core: MaestroCoreBridge,
        conversations: ConversationStore,
        workspaces: WorkspaceRegistry,
        capabilities: CapabilityRegistry,
    ):
        self._core = core
        self._conversations = conversations
        self._workspaces = workspaces
        self._capabilities = capabilities

    def run_research(
        self,
        *,
        url: str | None,
        cancellation: Any = None,
    ) -> dict[str, Any]:
        """
        Execute one explicit governed Web Research
        mission through the existing MAESTRO
        production runtime.

        AY validates capture/provenance integrity
        only. External semantic truth is never
        asserted here.
        """

        if not isinstance(url, str):
            raise ValueError(
                "research_url must be a string."
            )

        if (
            not url
            or url != url.strip()
        ):
            raise ValueError(
                "research_url must be a non-empty "
                "URL without surrounding whitespace."
            )

        if len(url) > 4096:
            raise ValueError(
                "research_url is too long."
            )

        if cancellation is not None and cancellation.is_set():
            raise MissionCancelledError("Research cancelled before Core entry")

        with self._core.mission_lock:
            if cancellation is not None and cancellation.is_set():
                raise MissionCancelledError("Research cancelled waiting for Core")
            runtime = (
                self._core.ensure_web_research()
            )

            kernel = runtime.kernel

            mission = runtime.create_mission(
                objective=(
                    "Observe one explicit governed "
                    "Web source."
                ),
                scope="EX -> RX -> AY -> TX",
                requested_output=(
                    "One durable unverified "
                    "external research evidence "
                    "record."
                ),
            )

            mission.acceptance_criteria = [
                (
                    "AC-01: RX performs one "
                    "governed Web fetch"
                ),
                (
                    "AC-02: Research Evidence is "
                    "durably persisted"
                ),
                (
                    "AC-03: External semantic truth "
                    "is not asserted"
                ),
                (
                    "AC-04: AY validates capture "
                    "integrity only"
                ),
                (
                    "AC-05: TX commits the mission "
                    "trace"
                ),
            ]

            kernel.move(
                mission,
                MissionState.CONTEXT_LOADING,
            )

            kernel.move(
                mission,
                MissionState.ANALYSIS,
            )

            kernel.move(
                mission,
                MissionState.CONTRACT_PENDING,
            )

            mission.strike_team = [
                CouncilId.RX,
                CouncilId.AY,
                CouncilId.TX,
            ]

            kernel.move(
                mission,
                MissionState.STRIKE_TEAM_CONVENED,
            )

            kernel.route(
                RoutingRequest(
                    task_id=mission.task_id,
                    requested_by=CouncilId.EX,
                    targets=[
                        CouncilId.RX
                    ],
                    action="GOVERNED_WEB_FETCH",
                    reason=(
                        "Execute one explicit "
                        "read-only governed Web "
                        "observation."
                    ),
                )
            )

            kernel.move(
                mission,
                MissionState.DISPATCHED,
            )

            kernel.move(
                mission,
                MissionState.EXECUTING,
            )

            kernel.add_dag_node(
                mission,
                DagNode(
                    node_id="rx-web-fetch",
                    council=CouncilId.RX,
                    action="GOVERNED_WEB_FETCH",
                    criteria_ids=[
                        "AC-01",
                        "AC-02",
                        "AC-03",
                    ],
                ),
            )

            kernel.start_dag_node(
                mission,
                "rx-web-fetch",
            )

            evidence = None
            failure_evidence_id = None
            error_type = None
            passed = False
            cancelled_fetch = False

            try:
                evidence = (
                    runtime.research_fetch(
                        mission,
                        url,
                        cancellation=cancellation,
                    )
                )

                verification = (
                    evidence.payload[
                        "verification"
                    ]
                )

                if (
                    verification.get("status")
                    != "UNVERIFIED_EXTERNAL"
                ):
                    raise RuntimeError(
                        "Research Evidence has an "
                        "unexpected verification "
                        "status."
                    )

                if (
                    verification.get(
                        "system_truth_asserted"
                    )
                    is not False
                ):
                    raise RuntimeError(
                        "External semantic truth "
                        "was improperly asserted."
                    )

                durable_rows = (
                    runtime.research_evidence
                    .for_task(
                        mission.task_id
                    )
                )

                if (
                    len(durable_rows) != 1
                    or durable_rows[0]
                    .evidence_id
                    != evidence.evidence_id
                ):
                    raise RuntimeError(
                        "Durable Research Evidence "
                        "verification failed."
                    )

                if cancellation is not None and cancellation.is_set():
                    raise ResearchFetchCancelled("Research cancelled before verification")

                kernel.succeed_dag_node(
                    mission,
                    "rx-web-fetch",
                    {
                        "evidence_id": (
                            evidence.evidence_id
                        ),
                        "source_ref": (
                            evidence.source_ref
                        ),
                    },
                )

                passed = True

            except Exception as exc:
                cancelled_fetch = isinstance(exc, ResearchFetchCancelled)
                error_type = (
                    type(exc).__name__
                )

                kernel.fail_dag_node(
                    mission,
                    "rx-web-fetch",
                    error_type,
                )

                failure_evidence_id = (
                    kernel.add_evidence(
                        Evidence(
                            task_id=(
                                mission.task_id
                            ),
                            council=CouncilId.RX,
                            kind=(
                                EvidenceKind
                                .TEST_RESULT
                            ),
                            claim=(
                                "Governed Web "
                                "Research execution "
                                "failed."
                            ),
                            payload={
                                "error_type": (
                                    error_type
                                ),
                                "url_sha256": (
                                    hashlib.sha256(
                                        url.encode(
                                            "utf-8"
                                        )
                                    ).hexdigest()
                                ),
                                (
                                    "semantic_truth_"
                                    "verified"
                                ): False,
                            },
                            source_ref=(
                                "MAESTRO_GATEWAY_"
                                "GW0_2"
                            ),
                        )
                    )
                )

            audit_evidence_id = (
                evidence.evidence_id
                if evidence is not None
                else failure_evidence_id
            )

            kernel.move(
                mission,
                MissionState.PAUSED_FOR_AUDIT,
            )

            kernel.move(
                mission,
                MissionState.AUDITING,
            )

            verdict_value = (
                VerdictValue.JUSTE
                if passed
                else VerdictValue.FAUTE
            )

            kernel.submit_verdict(
                mission,
                Verdict(
                    task_id=mission.task_id,
                    council=CouncilId.RX,
                    value=verdict_value,
                    scope=VerdictScope.DOMAIN,
                    evidence_ids=[
                        str(audit_evidence_id)
                    ],
                    findings=[
                        {
                            "criterion": (
                                "GW0.2_RX_FETCH"
                            ),
                            "result": (
                                "PASS"
                                if passed
                                else "FAIL"
                            ),
                        }
                    ],
                ),
            )

            kernel.submit_verdict(
                mission,
                Verdict(
                    task_id=mission.task_id,
                    council=CouncilId.AY,
                    value=verdict_value,
                    scope=VerdictScope.SYSTEM,
                    evidence_ids=[
                        str(audit_evidence_id)
                    ],
                    findings=[
                        {
                            "criterion": (
                                "GW0.2_RESEARCH_"
                                "CAPTURE_INTEGRITY"
                            ),
                            "result": (
                                "JUSTE"
                                if passed
                                else "FAUTE"
                            ),
                            "scope": (
                                "PROVENANCE_AND_"
                                "CAPTURE_INTEGRITY_"
                                "ONLY"
                            ),
                            (
                                "semantic_truth_"
                                "verified"
                            ): False,
                        }
                    ],
                ),
            )

            if passed:
                kernel.move(
                    mission,
                    MissionState.READY_FOR_RELEASE,
                )

                kernel.move(
                    mission,
                    MissionState.STATE_COMMIT_PENDING,
                )

                kernel.authorize_durable_commit(
                    mission
                )

                kernel.commit_durable_state(
                    mission
                )

                kernel.close(
                    mission,
                    durable_change_required=True,
                )

                runtime.persist(
                    mission,
                    (
                        "GOVERNED_WEB_RESEARCH_"
                        "MISSION_COMPLETE"
                    ),
                    "MAESTRO_GATEWAY",
                    {
                        "evidence_id": (
                            evidence.evidence_id
                        ),
                        "source_ref": (
                            evidence.source_ref
                        ),
                        (
                            "semantic_truth_"
                            "verified"
                        ): False,
                    },
                )

            else:
                kernel.move(
                    mission,
                    MissionState.FAULT_RETURNED,
                )

                kernel.move(
                    mission,
                    MissionState.TASK_ABORTED,
                )

                runtime.persist(
                    mission,
                    (
                        "GOVERNED_WEB_RESEARCH_"
                        "MISSION_FAILED"
                    ),
                    "MAESTRO_GATEWAY",
                    {
                        "evidence_id": (
                            failure_evidence_id
                        ),
                        "error_type": (
                            error_type
                        ),
                        (
                            "semantic_truth_"
                            "verified"
                        ): False,
                    },
                )

                if cancelled_fetch:
                    raise MissionCancelledError(
                        "Research cancelled before durable TX commit"
                    )

            return {
                "ok": passed,
                "protocol": "GW0.2",
                "mode": "M0_RESEARCH_FETCH",
                "task_id": mission.task_id,
                "state": mission.state.value,
                "evidence_id": (
                    evidence.evidence_id
                    if evidence is not None
                    else failure_evidence_id
                ),
                "source_ref": (
                    evidence.source_ref
                    if evidence is not None
                    else None
                ),
                "verification_status": (
                    evidence.payload[
                        "verification"
                    ]["status"]
                    if evidence is not None
                    else None
                ),
                "ay_verdict": (
                    mission.system_verdict
                    .value.value
                    if mission.system_verdict
                    else "NONE"
                ),
                "tx_authorized": bool(
                    mission.tx_commit_authorized
                ),
                "tx_committed": bool(
                    mission.tx_committed
                ),
                "semantic_truth_verified": False,
                "error_type": error_type,
                "stages": [
                    "CONTRACT",
                    "ROUTING_RX",
                    "GOVERNED_WEB_FETCH",
                    "DURABLE_EVIDENCE",
                    "AY_CAPTURE_INTEGRITY",
                    (
                        "TX_COMMIT"
                        if passed
                        else "FAULT_RETURNED"
                    ),
                ],
            }

    def run(
        self,
        *,
        prompt: str,
        model: str | None = None,
        max_tokens: int = 512,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        mode: str = "M0_DIRECT",
        research_url: str | None = None,
        ui_language: str | None = None,
        cancellation: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(mode, str):
            raise ValueError(
                "mode must be a string."
            )

        normalized_mode = (
            mode.strip().upper()
        )

        if normalized_mode == "M0_RESEARCH_FETCH":
            return self.run_research(
                url=research_url,
                cancellation=cancellation,
            )

        if normalized_mode != "M0_DIRECT":
            raise ValueError(
                "Unsupported mission mode."
            )

        prompt, max_tokens = validate_input(
            prompt,
            max_tokens,
        )
        # FIXED4: preserve the requested output ceiling before Core routing.
        caller_max_tokens = max_tokens

        # The budget includes waiting for the shared governed mission lock.
        # This is a bounded inference envelope, NOT a claim that other Core
        # operations or an external Ollama process can always be killed.
        # Simpler requests should fail promptly; meaningful explanations and
        # workspace work deserve more time on resource-constrained local Macs.
        # User-configured environment budget overrides these defaults.
        budget_class = classify_request(
            prompt, workspace_active=bool(workspace_id), ui_language=ui_language
        )
        default_budget = 90.0 if budget_class == "quick" else (
            240.0 if workspace_id or budget_class == "code" else 210.0
        )
        raw_budget = os.getenv("MAESTRO_MISSION_BUDGET_SECONDS")
        try:
            mission_budget = float(raw_budget) if raw_budget is not None else default_budget
        except (ValueError, TypeError):
            mission_budget = default_budget
        if not (5.0 <= mission_budget <= 300.0):
            mission_budget = default_budget
        mission_deadline = time.monotonic() + mission_budget

        if cancellation is not None and cancellation.is_set():
            raise MissionCancelledError("Mission cancelled before execution")
        # Runtime discovery is outside Core mission_lock. Hot routing below is
        # purely in-memory and performs no network operations.
        # All conversational requests, including MAESTRO self-description,
        # must use a real governed inference resource. No prewritten replies.
        self._core.ensure_runtime_resources()
        with self._core.conversation_lane(conversation_id, cancellation, mission_deadline), \
                self._core.mission_lock:
            if cancellation is not None and cancellation.is_set():
                raise MissionCancelledError("Mission cancelled while waiting for Core")
            if time.monotonic() >= mission_deadline:
                raise TimeoutError("Mission expired waiting for governed Core")
            conversation = (
                self._conversations.ensure(
                    conversation_id,
                    first_message=prompt,
                )
            )

            previous_messages = list(
                conversation.get(
                    "messages",
                    [],
                )
            )
            context = _conversation_context(
                previous_messages
            )

            # Keep the current user input in the mission's in-memory prompt.
            # Publishing it here would leak a half-finished turn if cancelled
            # before the Core verdict. Conversation publication occurs below.

            active_workspace = None

            if workspace_id:
                active_workspace = (
                    self._workspaces.get(
                        workspace_id
                    )
                )

                if not active_workspace.get(
                    "exists"
                ):
                    raise ValueError(
                        "Selected workspace no longer exists"
                    )

            system = self._core.system
            runtime = system.os.runtime
            kernel = runtime.kernel
            registry = system.sovereign_intelligence.registry

            # V8.11.5: establish Core-lane eligibility BEFORE requiring an
            # inference resource.  ZERO MODEL must still allow deterministic
            # governance and locally proven atomic execution.
            (
                pre_route_task,
                pre_fast_lane,
                deterministic_contract,
                semantic_subtask,
                preflight_core_candidate,
            ) = _deterministic_core_preflight(
                prompt,
                workspace_active=bool(active_workspace),
                ui_language=ui_language,
                model=model,
            )
            deterministic_without_model = preflight_core_candidate is not None

            # Open-ended/generative work still fails closed when no inference
            # resource exists.  We never fabricate language-model capability.
            if not registry.all() and not deterministic_without_model:
                raise RuntimeError(
                    "Aucun modèle d'inférence disponible. Vérifiez les moteurs dans SETTINGS."
                )

            routing_decision = None
            primary_selection = None
            resource_id = None
            if model is None or not str(model).strip():
                if not deterministic_without_model:
                    selection_semantic_subtask = _routing_semantic_subtask(
                        deterministic_contract, semantic_subtask
                    )
                    primary_selection = self._core.primary_intelligence.select(
                        registry, prompt,
                        workspace_active=bool(active_workspace),
                        max_tokens=max_tokens,
                        ui_language=ui_language,
                        semantic_subtask=selection_semantic_subtask,
                    )
                    routing_decision = primary_selection.decision
                    resource_id = routing_decision.resource_id
                    max_tokens = routing_decision.prompt_budget
            else:
                resource_id = choose_direct_resource(registry, model=model)

            route_task = (
                routing_decision.task_class if routing_decision else pre_route_task
            )
            route_language = (
                routing_decision.language
                if routing_decision
                else _language_code(prompt, ui_language)
            )

            fast_intro = _fast_capability_intro(prompt, bool(active_workspace))
            direct_fast_lane = (
                not fast_intro
                and not asks_about_capabilities(prompt)
                and not asks_about_previous_language(prompt)
                and direct_fast_lane_eligible(
                    prompt, route_task, workspace_active=bool(active_workspace)
                )
            )
            # FIXED3: keep the previously established proof after route classification.
            # This cannot authorize an unproved extraction or a model override.
            if (
                not direct_fast_lane
                and deterministic_contract is not None
                and deterministic_contract.subtask == "extraction"
                and preflight_core_candidate is not None
                and not bool(active_workspace)
                and not str(model or "").strip()
                and not asks_about_previous_language(prompt)
            ):
                direct_fast_lane = True
            if not direct_fast_lane:
                semantic_subtask = None
                deterministic_contract = None
            if fast_intro:
                # Self-introduction is a short conversational explanation, not
                # an exhaustive inventory. Never change the normal/code budget.
                max_tokens = min(max_tokens, 192)
            # V8.4: spending a 320+ token budget for a one-sentence answer
            # burns time on CPU inference. Do not truncate deep/code requests.
            max_tokens = bounded_response_tokens(route_task, prompt, max_tokens)
            # FIXED4: reclaim room only for explicit, bounded multi-part answers.
            max_tokens = reconcile_bounded_structure_budget(
                route_task, prompt, max_tokens, caller_max_tokens
            )

            mission = runtime.create_mission(
                objective=(
                    "Execute one bounded local "
                    "conversational DIRECT request."
                ),
                scope="EX -> AI -> AY -> TX",
                requested_output=(
                    "One user-facing local intelligence "
                    "response."
                ),
            )

            mission.acceptance_criteria = [
                "AC-01: bounded local intelligence execution",
                "AC-02: non-empty substantive response",
                "AC-03: execution evidence recorded",
                "AC-04: AY validates delivery integrity",
                "AC-05: TX commits mission trace",
                "AC-06: local conversation context explicit",
                "AC-07: tools execute only inside authorized workspace",
                "AC-08: prompt echo is rejected unless explicitly requested",
            ]

            kernel.move(
                mission,
                MissionState.CONTEXT_LOADING,
            )
            kernel.move(
                mission,
                MissionState.ANALYSIS,
            )
            kernel.move(
                mission,
                MissionState.CONTRACT_PENDING,
            )

            mission.strike_team = [
                CouncilId.AI,
                CouncilId.AY,
                CouncilId.TX,
            ]

            kernel.move(
                mission,
                MissionState.STRIKE_TEAM_CONVENED,
            )

            kernel.route(
                RoutingRequest(
                    task_id=mission.task_id,
                    requested_by=CouncilId.EX,
                    targets=[CouncilId.AI],
                    action=(
                        "INTERACTIVE_TOOL_AWARE_"
                        "DIRECT_INFERENCE"
                    ),
                    reason=(
                        "Execute one bounded local request "
                        "with MAESTRO-governed capability "
                        "awareness."
                    ),
                )
            )

            kernel.move(
                mission,
                MissionState.DISPATCHED,
            )
            kernel.move(
                mission,
                MissionState.EXECUTING,
            )

            kernel.add_dag_node(
                mission,
                DagNode(
                    node_id="ai-direct",
                    council=CouncilId.AI,
                    action=(
                        "INTERACTIVE_TOOL_AWARE_"
                        "DIRECT_INFERENCE"
                    ),
                    criteria_ids=[
                        "AC-01",
                        "AC-02",
                        "AC-03",
                        "AC-06",
                        "AC-07",
                        "AC-08",
                    ],
                ),
            )
            kernel.start_dag_node(
                mission,
                "ai-direct",
            )

            capability_question = asks_about_capabilities(prompt)
            # V8.7: ordinary conversation no longer pays the token/prefill cost
            # of a full capability catalogue.  Build it only when the user asks
            # about capabilities or a Workspace mission actually needs it.
            capability_text = ""
            if active_workspace or capability_question:
                capability_text = (
                    self._capabilities.prompt_summary(
                        workspace_active=bool(active_workspace),
                        workspace_kind=(
                            str(active_workspace["kind"])
                            if active_workspace
                            else None
                        ),
                    )
                )

            language_rule = (
                "Detect the natural language of the "
                "CURRENT USER REQUEST and the user's "
                "conversation history. Reply in the same "
                "language. If the current user clearly "
                "switches language, follow the current "
                "user. For an ambiguous short utterance, "
                "prefer prior user messages. "
                "Do not mention this language rule."
            )

            behavior_rule = (
                "Answer the user's actual question. "
                "Do not merely repeat or paraphrase the "
                "request. Do not claim a capability that "
                "MAESTRO has not granted."
            )

            tool_rule = ""

            if active_workspace:
                tool_rule = (
                    "\n\nAUTHORIZED WORKSPACE\n"
                    f"id={active_workspace['id']}\n"
                    f"label={active_workspace['label']}\n"
                    f"kind={active_workspace['kind']}\n"
                    "\nAVAILABLE WORKSPACE TOOLS\n"
                    "- workspace.tree: "
                    '{"path":"relative/path","max_depth":3}\n'
                    "- workspace.read: "
                    '{"path":"relative/file"}\n'
                    "  IMPORTANT: workspace.read requires a real FILE path. "
                    "Never call it with an empty path, '.', '/', or a directory. "
                    'Example for package.json: {"path":"package.json"}.\n'
                    "- workspace.search: "
                    '{"query":"text"}\n'
                    "- terminal.run (P0.6D.1 inspection-only): "
                    '{"argv":["pwd"],"cwd":".","timeout_ms":3000}\n'
                    "  Allowed terminal commands: pwd, head, tail, wc, stat. "
                    "No shell, pipes, redirects, Git, interpreters, package "
                    "managers, network commands, or mutating commands. "
                    "Use workspace.tree for directory listings.\n"
                    "\nIf you do not yet know the exact file path, use "
                    "workspace.tree or workspace.search first. "
                    "When you need workspace data, "
                    "respond with ONLY exactly one tool "
                    "request using this syntax:\n"
                    "<MAESTRO_TOOL>"
                    '{"name":"workspace.read",'
                    '"arguments":{"path":"..."}}'
                    "</MAESTRO_TOOL>\n"
                    "MAESTRO executes the tool and returns "
                    "the result. Never invent a tool result. "
                    "Mission tools are inspection-only in P0.6E.1. "
                    "Git inspection tools are git.status, git.diff, git.log, "
                    "and git.branch_current. "
                    "write/edit/delete and mutating terminal commands "
                    "are not available through this tool channel."
                )

            context_block = (
                "\n\nCONVERSATION HISTORY:\n"
                + context
                if context
                else ""
            )

            capability_snapshot = None
            if capability_question:
                # Dynamic factual grounding, no new permission is granted.
                observed = self._capabilities.snapshot(
                    workspace_active=bool(active_workspace),
                    workspace_kind=(str(active_workspace["kind"])
                                    if active_workspace else None),
                )
                capability_snapshot = observed
                capability_text = (_intro_capability_facts(observed) if fast_intro
                                   else "\n".join(
                                       ["VERIFIED MAESTRO CAPABILITIES (CURRENT SESSION):"]
                                       + [f"- {item['capability_id']} "
                                          f"[{item['status']}]: {item['description']}"
                                          for item in observed]
                                   ))
                behavior_rule += (
                    "\nThe user is asking for your actual capabilities. "
                    "Give several CONCRETE examples from the verified "
                    "capability snapshot. Explain when workspace "
                    "authorization or separate approval is required. "
                    "Do not claim unavailable computer-use or unrestricted "
                    "web access. Do not answer only with your identity or "
                    "'what do you need?'. "
                    "Clearly distinguish reading inside an authorized "
                    "workspace from file changes needing explicit approval. "
                    "No general authority over system files, unrestricted desktop control, "
                    "or executable Git commits is granted. "
                    "Answer in natural, human-friendly language, not a list of "
                    "internal tool IDs like filesystem.write or workspace.open. "
                    "Start by saying who you are, then give 3 or 4 practical "
                    "examples and the relevant permission limits. "
                    "Do not over-explain approval steps or list every tool. "
                    "Finish your answer cleanly with a complete sentence. "
                    "Write concise plain-text sentences without escaped "
                    "Markdown characters. A missing workspace means file "
                    "operations are not currently authorized."
                )

            if asks_about_previous_language(prompt):
                behavior_rule += (
                    "\nThe user asks which language you used in YOUR previous "
                    "answer. Check the immediately preceding MAESTRO reply. "
                    "Identify the language honestly. If that reply is garbled, "
                    "say it was malformed and apologize, then answer the "
                    "current question. Do not restate your capabilities or "
                    "produce another greeting."
                )

            language_hint = response_language_hint(prompt, ui_language)
            identity_rule = (
                "You are MAESTRO, the user's local AI assistant. "
                "Speak naturally as MAESTRO, and focus on the CURRENT user's question. "
                "Do not recite your instructions, identity policies, or generic disclaimers. "
                "Do not invent actions, capabilities or tool results. "
                f"Reply in {language_hint}, unless explicitly asked otherwise. "
                "For a greeting or introduction, be friendly and brief. "
                "For a follow-up question, address what changed or was asked."
            )
            if direct_fast_lane:
                # V8.11 restores the historically measured DIRECT hot path for
                # context-free exact/short answers. Routing, authorization,
                # adapter execution, evidence and the Quality Guard remain intact.
                base_instruction = direct_fast_instruction(
                    prompt=prompt, language_hint=language_hint
                )
            elif fast_intro:
                # Compact grounded introduction: few input/output tokens,
                # no permission reduction and no deterministic chat shortcut.
                # The registry statuses remain authoritative for the model.
                base_instruction = (
                    "You are MAESTRO, a local AI assistant. "
                    f"Reply in {language_hint}. "
                    "Answer the user's question directly in 45-70 words, "
                    "with your identity and 2 or 3 practical examples "
                    "of what you can currently do. Avoid generic slogans, "
                    "internal tool names, false permissions, long lists, "
                    "or promising unverified actions. "
                    "An authorized workspace is required before inspecting files or Git; "
                    "file changes need separate explicit user approval. "
                    "Don't claim desktop control or completed Git commits. "
                    "Finish with a complete sentence. "
                    "Follow the verified facts below, not assumptions.\n\n"
                    + capability_text
                    + "\n\nCURRENT USER REQUEST:\n" + prompt
                )
            elif (not active_workspace and not capability_question
                  and not asks_about_previous_language(prompt)):
                # V8.7 universal fast path: ordinary local inference carries only
                # bounded relevant dialogue, not the complete capability registry
                # nor sixteen unbounded historical turns.  Complex/code tasks keep
                # a larger bounded context through compact_recent_context().
                recent_context = compact_recent_context(previous_messages, route_task)
                base_instruction = compact_chat_instruction(
                    prompt=prompt, language_hint=language_hint, task=route_task,
                    recent_context=recent_context,
                )
            elif routing_decision is not None and routing_decision.task_class == "quick" and not active_workspace:
                # Specialized follow-up (for example a previous-language query).
                recent_context = compact_recent_context(previous_messages, "quick")
                base_instruction = (
                    identity_rule + "\n" + behavior_rule
                    + ("\n\nRECENT CONVERSATION:\n" + recent_context if recent_context else "")
                    + "\n\nCURRENT USER REQUEST:\n" + prompt
                )
            else:
                base_instruction = (
                    identity_rule + "\n\n" + capability_text
                    + "\n\n" + language_rule + "\n" + behavior_rule
                    + tool_rule + context_block
                    + "\n\nCURRENT USER REQUEST:\n" + prompt
                )

            prior_assistant = next((str(item.get("content") or "")
                                    for item in reversed(previous_messages)
                                    if item.get("role") == "assistant"
                                    and str(item.get("content") or "").strip()), None)
            prior_user = next((str(item.get("content") or "")
                               for item in reversed(previous_messages)
                               if item.get("role") == "user"
                               and str(item.get("content") or "").strip()), None)
            transcript = base_instruction
            response = None
            output = ""
            error: str | None = None
            metrics: dict[str, Any] = {}
            tool_calls: list[dict[str, Any]] = []
            tool_failures = 0
            quality_retries = 0
            resource_calls = 0
            quality_reason = "not_evaluated"
            quality_escalations = 0
            timeout_recoveries = 0
            model_sequence: list[str] = []
            cumulative_total_ms = 0.0
            cumulative_load_ms = 0.0
            model_attempt_metrics: list[dict[str, Any]] = []
            deterministic_core_delivery = False
            deterministic_core_started = 0.0
            inference_start = time.monotonic()
            mission_deadline_token = MISSION_DEADLINE.set(mission_deadline)
            mission_cancel_token = MISSION_CANCEL.set(cancellation)
            foreground_token = MISSION_FOREGROUND.set(True)
            stream_policy_token = MISSION_STREAM_POLICY.set(
                stream_completion_policy(
                    route_task, prompt, workspace_active=bool(active_workspace),
                    max_tokens=max_tokens, direct_fast_lane=direct_fast_lane,
                )
            )

            try:
                core_candidate = (
                    preflight_core_candidate
                    if direct_fast_lane
                    else None
                )
                if core_candidate is not None:
                    # V8.11.4 sovereign deterministic execution lane.  The
                    # requested truth is already proved locally, so invoking or
                    # switching an LLM would add latency and reduce reliability.
                    # The selected primary remains logical state only; no adapter
                    # is executed and no model success evidence is fabricated.
                    deterministic_core_started = time.monotonic()
                    quality = evaluate_response(
                        prompt, core_candidate,
                        prior_output=prior_assistant,
                        prior_user_prompt=prior_user,
                        capability_snapshot=capability_snapshot,
                        completion_reason=None,
                    )
                    quality_reason = quality.reason
                    if not quality.accepted:
                        raise RuntimeError(
                            "Deterministic Core delivery failed Response Quality "
                            f"Guard: {quality.reason}"
                        )
                    output = core_candidate
                    deterministic_core_delivery = True
                    metrics = {
                        "deterministic_execution": True,
                        "deterministic_core_execution": True,
                        "deterministic_core_subtask": semantic_subtask,
                        "deterministic_core_ms": round(
                            (time.monotonic() - deterministic_core_started) * 1000, 3
                        ),
                        "provider_model_called": False,
                    }
                else:
                    adapter = registry.adapter(resource_id)

                    for _round in range(
                    MAX_TOOL_ROUNDS
                    + MAX_QUALITY_RETRIES
                    + 1
                    ):
                        if cancellation is not None and cancellation.is_set():
                            raise MissionCancelledError("Mission cancelled before inference")
                        if time.monotonic() >= mission_deadline:
                            raise TimeoutError("Governed mission wall-clock budget exhausted")
                        request = IntelligenceRequest(
                            task_id=mission.task_id,
                            capability=(
                                IntelligenceCapability.DIRECT
                            ),
                            instruction=transcript,
                            max_tokens=max_tokens,
                            execution_profile=local_execution_profile(
                                route_task, quality_retry=quality_retries,
                                manual_override=(model is not None and bool(str(model).strip())),
                                deterministic_direct=bool(
                                    direct_fast_lane and deterministic_contract is not None
                                ),
                            ),
                            requires_tools=False,
                            preferred_local=True,
                            metadata={
                                "council": "AI",
                                "task_family": "DIRECT",
                                "purpose": (
                                    "maestro_ui_tool_aware_"
                                    "conversation"
                                ),
                                "gateway_protocol": "P0.6A",
                                "conversation_id": (
                                    conversation["id"]
                                ),
                                "workspace_id": (
                                    active_workspace["id"]
                                    if active_workspace
                                    else None
                                ),
                                "context_messages": len(
                                    previous_messages
                                ),
                                "language_policy": (
                                    "conversation_detect"
                                ),
                                "direct_fast_lane": direct_fast_lane,
                            },
                        )

                        from .model_qualification import verified_version
                        model_sequence.append(resource_id)
                        attempt_fingerprint = verified_version(resource_id)
                        model_call_start = time.monotonic()
                        reset_stream_metrics()
                        try:
                            # Core shared state is NOT accessed during remote inference.
                            # Other conversations can now enter their own guarded
                            # transitions while this Ollama request is in flight.
                            if getattr(adapter, "io_only", False) or resource_id.startswith("ollama::"):
                                with self._core.unlocked_during_model_io():
                                    response = adapter.execute(request)
                            else:
                                # Unknown future adapters may mutate governed Core
                                # state. Fail conservatively: serialize them until
                                # their I/O-only contract is separately audited.
                                response = adapter.execute(request)
                        except (TimeoutError, OllamaHTTPError) as exc:
                            resource_calls += 1
                            elapsed_ms = round((time.monotonic() - model_call_start) * 1000, 3)
                            from .quality_evidence import capture_transport_failure
                            model_attempt_metrics.append({
                                "resource_id": resource_id,
                                "model_fingerprint": attempt_fingerprint,
                                "quality_evidence": capture_transport_failure(attempt_fingerprint),
                                "wall_ms": elapsed_ms,
                                "outcome": "timeout" if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() else "transport_failure",
                                "error_type": type(exc).__name__,
                            })
                            timed_out = (isinstance(exc, TimeoutError)
                                         or "timed out" in str(exc).lower()
                                         or "deadline" in str(exc).lower())
                            # Never retry a manually selected model or an
                            # authorized Workspace mission. For conversational
                            # AUTO, at most ONE retry is permitted in total.
                            alternative = None
                            if (timed_out and routing_decision is not None
                                    and routing_decision.task_class in {"quick", "normal"}
                                    and not active_workspace
                                    and timeout_recoveries == 0
                                    and quality_retries == 0):
                                alternative = choose_timeout_fallback(
                                    registry, resource_id, self._conversations.root,
                                    task_class=routing_decision.task_class,
                                    language=route_language,
                                    remaining_seconds=max(0., mission_deadline-time.monotonic()),
                                )
                            if primary_selection is not None:
                                promoted = self._core.primary_intelligence.failover(
                                    failed_id=resource_id, replacement_id=alternative,
                                    expected_epoch=primary_selection.epoch,
                                    registry=registry,
                                    reason="timeout" if timed_out else "transport_failure",
                                )
                                if promoted and alternative is not None:
                                    # This same mission may timeout again after its
                                    # single authorized fallback. Track the new
                                    # epoch so the second failure can invalidate it.
                                    primary_selection = PrimarySelection(
                                        primary_selection.decision,
                                        primary_selection.epoch + 1,
                                        "promoted_after_timeout",
                                    )
                            if alternative is not None:
                                timeout_recoveries += 1
                                resource_id = alternative
                                adapter = registry.adapter(resource_id)
                                continue
                            if timed_out:
                                raise RuntimeError(
                                    "Le moteur d’inférence a dépassé le délai autorisé. "
                                    "Aucun autre modèle qualifié et compatible avec "
                                    "la langue et le délai restant n'est disponible. "
                                    "Consultez les modèles dans SETTINGS."
                                ) from exc
                            raise
                        resource_calls += 1
                        if cancellation is not None and cancellation.is_set():
                            raise MissionCancelledError("Mission cancelled after inference")

                        candidate = str(
                            response.output or ""
                        ).strip()
                        # G4A: no additional inference, provider changes or semantic claims.
                        # Only replace layout when the original text is otherwise preserved.
                        _g4a_repaired = False
                        if not active_workspace and not direct_fast_lane:
                            from .quality_shape_repair import propose_lossless_two_paragraph_repair
                            _g4a_candidate = propose_lossless_two_paragraph_repair(prompt, candidate)
                            if _g4a_candidate is not None:
                                candidate = _g4a_candidate
                                _g4a_repaired = True

                        raw_metrics = dict(
                            getattr(
                                response,
                                "metadata",
                                {},
                            )
                            or {}
                        )
                        raw_metrics.update(current_stream_metrics())
                        metrics = {
                            key: raw_metrics.get(key)
                            for key in (
                                "total_ms",
                                "load_ms",
                                "prompt_tokens",
                                "generated_tokens",
                                "tokens_per_second",
                                "think_policy",
                                "done_reason",
                                "finish_reason",
                                "first_token_ms",
                                "transport_wall_ms",
                                "decode_after_first_ms",
                                "generated_characters",
                                "stream_fragments",
                                "characters_per_second_after_first",
                                "early_completion",
                                "adaptive_num_ctx",
                                "adaptive_num_predict",
                                "residency_keep_alive",
                                "deterministic_execution",
                                "execution_profile_ref",
                                "residency_policy",
                                "provider_resident_before_request",
                                "cross_process_residency_preserved",
                            )
                            if key in raw_metrics
                        }

                        # Ollama exposes load, decoded tokens and decoding speed.
                        # This yields an approximate prompt/evaluation breakdown
                        # without touching the sovereign Core provider adapter.
                        decoded = metrics.get("generated_tokens")
                        speed = metrics.get("tokens_per_second")
                        total = metrics.get("total_ms")
                        loaded = metrics.get("load_ms")
                        decode_ms = (round(1000 * decoded / speed, 3)
                                     if isinstance(decoded, (int, float))
                                     and isinstance(speed, (int, float))
                                     and not isinstance(decoded, bool)
                                     and not isinstance(speed, bool) and speed > 0 else None)
                        setup_ms = (round(max(0.0, total - loaded - decode_ms), 3)
                                    if decode_ms is not None
                                    and isinstance(total, (int, float))
                                    and isinstance(loaded, (int, float)) else None)
                        from .quality_evidence import capture_attempt_quality
                        model_attempt_metrics.append({
                            "resource_id": resource_id,
                            "model_fingerprint": attempt_fingerprint,
                            "quality_shape_repair": (
                                "g4a_lossless_spacing_two_paragraphs" if _g4a_repaired else None
                            ),
                            "quality_evidence": capture_attempt_quality(
                                prompt, candidate,
                                metrics.get("done_reason") or metrics.get("finish_reason"),
                                attempt_fingerprint,
                            ),
                            "total_ms": total,
                            "wall_ms": round((time.monotonic() - model_call_start) * 1000, 3),
                            "load_ms": loaded,
                            "prompt_and_overhead_ms_estimate": setup_ms,
                            "decode_ms_estimate": decode_ms,
                            "prompt_tokens": metrics.get("prompt_tokens"),
                            "generated_tokens": decoded,
                            "tokens_per_second": speed,
                            "done_reason": metrics.get("done_reason") or metrics.get("finish_reason"),
                            "first_token_ms": metrics.get("first_token_ms"),
                            "transport_wall_ms": metrics.get("transport_wall_ms"),
                            "decode_after_first_ms": metrics.get("decode_after_first_ms"),
                            "generated_characters": metrics.get("generated_characters"),
                            "stream_fragments": metrics.get("stream_fragments"),
                            "characters_per_second_after_first": metrics.get("characters_per_second_after_first"),
                            "early_completion": metrics.get("early_completion"),
                            "adaptive_num_ctx": metrics.get("adaptive_num_ctx"),
                            "adaptive_num_predict": metrics.get("adaptive_num_predict"),
                            "residency_keep_alive": metrics.get("residency_keep_alive"),
                            "deterministic_execution": metrics.get("deterministic_execution"),
                            "execution_profile_ref": metrics.get("execution_profile_ref"),
                            "residency_policy": metrics.get("residency_policy"),
                            "provider_resident_before_request": metrics.get("provider_resident_before_request"),
                            "cross_process_residency_preserved": metrics.get("cross_process_residency_preserved"),
                            "outcome": "pending_quality_review",
                        })
                        for metric_key, accumulator in (("total_ms", "total"),
                                                        ("load_ms", "load")):
                            value = metrics.get(metric_key)
                            if isinstance(value, (int, float)) and not isinstance(value, bool):
                                if accumulator == "total":
                                    cumulative_total_ms += float(value)
                                else:
                                    cumulative_load_ms += float(value)

                        if not response.success:
                            raise RuntimeError(
                                "Intelligence resource "
                                "reported execution failure"
                            )

                        try:
                            tool_call = _parse_tool_call(
                                candidate
                            )
                        except ValueError as exc:
                            tool_failures += 1

                            if tool_failures > MAX_TOOL_ROUNDS:
                                raise RuntimeError(
                                    "Too many malformed workspace tool requests"
                                ) from exc

                            transcript += (
                                "\n\nMAESTRO TOOL PROTOCOL ERROR:\n"
                                + json.dumps(
                                    {
                                        "ok": False,
                                        "error": type(exc).__name__,
                                        "message": str(exc),
                                    },
                                    ensure_ascii=False,
                                )
                                + "\nCorrect the tool request and try again. "
                                "Return exactly one <MAESTRO_TOOL> JSON request "
                                "or, if no tool is needed, answer the user."
                            )
                            continue

                        if tool_call is not None:
                            if not active_workspace:
                                transcript += (
                                    "\n\nMAESTRO TOOL RESULT:\n"
                                    '{"ok":false,"error":"workspace_not_authorized",'
                                    '"message":"No workspace is authorized for this mission."}'
                                    "\nAnswer truthfully about the unavailable capability."
                                )
                                continue

                            if len(tool_calls) >= MAX_TOOL_ROUNDS:
                                raise RuntimeError(
                                    "Workspace tool round limit exceeded"
                                )

                            tool_name, raw_arguments = tool_call

                            attempt: dict[str, Any] = {
                                "name": tool_name,
                                "arguments": raw_arguments,
                                "ok": False,
                            }
                            tool_calls.append(attempt)

                            try:
                                arguments = _normalize_tool_arguments(
                                    tool_name,
                                    raw_arguments,
                                )
                                attempt["arguments"] = arguments

                                tool_result = (
                                    self._workspaces.execute_tool(
                                        active_workspace["id"],
                                        tool_name,
                                        arguments,
                                        cancellation=cancellation,
                                    )
                                )

                                if cancellation is not None and cancellation.is_set():
                                    raise MissionCancelledError(
                                        "Mission cancelled after workspace tool"
                                    )
                                attempt["ok"] = True

                                if isinstance(
                                    tool_result,
                                    dict,
                                ):
                                    execution_evidence = (
                                        tool_result.get(
                                            "execution_evidence"
                                        )
                                    )

                                    if isinstance(
                                        execution_evidence,
                                        dict,
                                    ):
                                        attempt[
                                            "execution_evidence"
                                        ] = (
                                            execution_evidence
                                        )

                                transcript += (
                                    "\n\nMAESTRO TOOL REQUEST:\n"
                                    + candidate
                                    + "\n\nMAESTRO TOOL RESULT:\n"
                                    + json.dumps(
                                        {
                                            "ok": True,
                                            "tool": tool_name,
                                            "result": tool_result,
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n\nUse this verified tool result to "
                                    "continue the user's request. Request another "
                                    "tool only if necessary."
                                )

                            except (
                                ValueError,
                                PermissionError,
                                FileNotFoundError,
                                OSError,
                            ) as exc:
                                tool_failures += 1
                                attempt["error"] = (
                                    f"{type(exc).__name__}: {exc}"
                                )

                                if tool_failures > MAX_TOOL_ROUNDS:
                                    raise RuntimeError(
                                        "Workspace tool correction limit exceeded"
                                    ) from exc

                                transcript += (
                                    "\n\nMAESTRO TOOL REQUEST:\n"
                                    + candidate
                                    + "\n\nMAESTRO TOOL RESULT:\n"
                                    + json.dumps(
                                        {
                                            "ok": False,
                                            "tool": tool_name,
                                            "arguments": raw_arguments,
                                            "error": type(exc).__name__,
                                            "message": str(exc),
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n\nThe tool was NOT executed successfully. "
                                    "Correct the arguments and request the proper "
                                    "tool again. If the exact path is unknown, use "
                                    "workspace.tree or workspace.search. Do not "
                                    "invent file contents."
                                )

                            continue

                        quality = evaluate_response(
                            prompt,
                            candidate,
                            prior_output=prior_assistant,
                            prior_user_prompt=prior_user,
                            capability_snapshot=capability_snapshot,
                            completion_reason=(
                                metrics.get("done_reason") or metrics.get("finish_reason")
                            ),
                        )
                        semantic_check = (
                            verify_deterministic_response(prompt, candidate)
                            if direct_fast_lane and deterministic_contract is not None
                            else None
                        )
                        if semantic_check is not None:
                            model_attempt_metrics[-1]["semantic_subtask"] = semantic_check.subtask
                            model_attempt_metrics[-1]["semantic_outcome"] = (
                                "pass" if semantic_check.accepted else "fail")
                            if not semantic_check.accepted:
                                # V8.11.2: never deliver a locally proven-wrong tiny
                                # DIRECT answer. The model still receives scoped
                                # negative competence evidence. When the Core already
                                # has an exact deterministic proof, project that truth
                                # instead of turning a trivial, fully-provable request
                                # into an HTTP 422 or burning a second LLM generation.
                                projected = deterministic_truth_projection(semantic_check)
                                if projected is not None:
                                    candidate = projected
                                    model_attempt_metrics[-1]["semantic_projection_applied"] = True
                                    model_attempt_metrics[-1]["semantic_delivery_source"] = (
                                        "local_deterministic_verifier")
                                    quality = evaluate_response(
                                        prompt, candidate,
                                        prior_output=prior_assistant,
                                        prior_user_prompt=prior_user,
                                        capability_snapshot=capability_snapshot,
                                        completion_reason=None,
                                    )
                                else:
                                    quality = QualityResult(
                                        False,
                                        "deterministic_semantic_mismatch:" + semantic_check.subtask,
                                        0.0,
                                    )
                        quality_reason = quality.reason
                        # A successful HTTP request is not yet a successful answer.
                        # Deterministic semantic failures are scoped evidence and do
                        # not poison the model's generic conversational qualification.
                        if (semantic_check is not None and not semantic_check.accepted):
                            model_attempt_metrics[-1]["outcome"] = "semantic_rejected"
                        else:
                            model_attempt_metrics[-1]["outcome"] = quality_attempt_outcome(
                                quality.accepted, quality.reason
                            )
                        # G4A: delivery can be repaired; do not count that as a
                        # successful unassisted model-format attempt.
                        if _g4a_repaired and quality.accepted:
                            model_attempt_metrics[-1]["outcome"] = "quality_repaired_by_core_formatting"

                        # V8.5.1: length is a caller/output-budget problem, not a
                        # semantic model failure. V8.5 incorrectly re-ran the
                        # entire prompt on a different, often slower LLM. Preserve
                        # fail-closed delivery but do not promote/quarantine a
                        # model or burn another generation attempt for this cause.
                        if not quality.accepted and not quality_retry_allowed(quality.reason):
                            raise RuntimeError(
                                "Response generation exhausted its configured token "
                                f"budget ({max_tokens}); incomplete text was not "
                                "delivered and cross-model retry was suppressed."
                            )

                        if (
                            not quality.accepted
                            and quality_retries
                            < MAX_QUALITY_RETRIES
                            and timeout_recoveries == 0
                        ):
                            quality_retries += 1
                            # Only AUTO may escalate, and only to a previously
                            # registered resource with existing DIRECT capability.
                            alternative = None
                            if routing_decision is not None and not active_workspace:
                                alternative = choose_quality_fallback(
                                    registry, resource_id, self._conversations.root,
                                    task_class=routing_decision.task_class,
                                    language=route_language,
                                    remaining_seconds=max(0., mission_deadline-time.monotonic()),
                                    semantic_subtask=semantic_subtask,
                                )
                            if alternative is not None:
                                if primary_selection is not None:
                                    promoted = self._core.primary_intelligence.failover(
                                        failed_id=resource_id, replacement_id=alternative,
                                        expected_epoch=primary_selection.epoch,
                                        registry=registry,
                                        reason=("semantic_rejected"
                                                if quality.reason.startswith("deterministic_semantic_mismatch:")
                                                else "quality_rejected"),
                                    )
                                    if promoted:
                                        primary_selection = PrimarySelection(
                                            primary_selection.decision,
                                            primary_selection.epoch + 1,
                                            "promoted_after_quality_rejection",
                                        )
                                adapter = registry.adapter(alternative)
                                resource_id = alternative
                                quality_escalations += 1
                            # Never feed a bad draft back into the model: it can
                            # otherwise reinforce exactly the repeated disclaimer.
                            transcript = (
                                base_instruction
                                + (
                                    "\n\nRESPONSE QUALITY RETRY: The previous answer "
                                    "had EXTRA TEXT OUTSIDE the two requested sentences. "
                                    "Answer the CURRENT user question in EXACTLY TWO "
                                    "complete sentences in the requested language. "
                                    "Begin immediately with the first sentence; do NOT "
                                    "include an introduction, heading, numbered list, "
                                    "preface, extra explanation, or conclusion. "
                                    "Use terminal punctuation after both sentences. "
                                    "Do not copy the rejected draft."
                                    if quality.reason == (
                                        "explicit_shape_extra_text_outside_two_numbered_sentences"
                                    )
                                    else (
                                        "\n\nRESPONSE QUALITY RETRY: The last output "
                                        "failed to address the CURRENT user question "
                                        "(reason: " + quality.reason + "). "
                                        "Answer the present question concretely, using "
                                        "recent conversation only when relevant. "
                                        "Do not repeat identity policies or the previous answer."
                                    )
                                )
                            )
                            # G3A FIXED1: honor verified explicit shape on an existing
                            # bounded retry; preserve the pre-existing two-sentence rule.
                            from .quality_retry_contract import contract_retry_hint
                            _g3a_hint = contract_retry_hint(quality.reason, route_language)
                            if _g3a_hint:
                                transcript += "\n\nEXACT OUTPUT CONTRACT: " + _g3a_hint
                            continue

                        if not quality.accepted:
                            from .quality_contract_failure import QualityContractFailure
                            raise QualityContractFailure(quality.reason, route_language)

                        output = candidate
                        break

                if not output:
                    raise RuntimeError(
                        "No final user-facing response was "
                        "produced"
                    )

                if cancellation is not None and cancellation.is_set():
                    raise MissionCancelledError("Mission cancelled before verification")
                passed = True

            except Exception as exc:
                passed = False
                from .quality_contract_failure import QualityContractFailure
                if isinstance(exc, QualityContractFailure):
                    error = exc.public_message()
                else:
                    error = (
                        f"{type(exc).__name__}: {exc}"
                    )
            finally:
                MISSION_STREAM_POLICY.reset(stream_policy_token)
                MISSION_FOREGROUND.reset(foreground_token)
                MISSION_CANCEL.reset(mission_cancel_token)
                MISSION_DEADLINE.reset(mission_deadline_token)

            metrics = {
                **metrics,
                **({"total_ms": cumulative_total_ms,
                    "load_ms": cumulative_load_ms} if resource_calls else {}),
                "model_sequence": model_sequence,
                "mission_budget_ms": round(mission_budget * 1000, 3),
                "model_attempt_metrics": model_attempt_metrics,
                "wall_ms": round((time.monotonic() - inference_start) * 1000, 3),
                "quality_escalations": quality_escalations,
                "timeout_recoveries": timeout_recoveries,
                "resource_calls": resource_calls,
                "tool_calls": len(tool_calls),
                "tool_failures": tool_failures,
                "quality_retries": (
                    quality_retries
                ),
                "quality_reason": quality_reason,
                "routing_policy": (
                    routing_decision.policy
                    if routing_decision
                    else ("deterministic_core" if deterministic_core_delivery else "manual")
                ),
                "primary_selection_reason": (
                    primary_selection.reason
                    if primary_selection
                    else (
                        "deterministic_core_no_model_required"
                        if deterministic_core_delivery
                        else "manual_override"
                    )
                ),
                "primary_selection_epoch": primary_selection.epoch if primary_selection else None,
                "fast_capabilities_intro": fast_intro,
                "direct_fast_lane": direct_fast_lane,
                "direct_fast_lane_contract": (
                    "warm_complete_under_1000ms_target" if direct_fast_lane else None
                ),
                "response_token_budget": max_tokens,
                "routing_class": (
                    routing_decision.task_class
                    if routing_decision
                    else (route_task if deterministic_core_delivery else "manual")
                ),
                "qualification_task": route_task,
                "routing_language": route_language,
                "semantic_subtask": semantic_subtask,
                "deterministic_semantic_guard": bool(deterministic_contract is not None),
                "semantic_recovery_attempts": _semantic_recovery_attempt_count(
                    primary_selection
                ),
                "semantic_recovery_trial_selected": bool(
                    primary_selection is not None
                    and primary_selection.reason == "semantic_recovery_trial"
                ),
                "routing_qualification_status": (
                    routing_decision.qualification_status
                    if routing_decision
                    else ("not_required" if deterministic_core_delivery else "manually_selected")
                ),
                "response_origin": _response_origin_from_attempts(
                    model_attempt_metrics, deterministic_core=deterministic_core_delivery
                ),
                "deterministic_core_execution": deterministic_core_delivery,
                "provider_model_called": bool(resource_calls),
                "selected_primary_resource_id": resource_id,
                "model_inventory_required": not deterministic_core_delivery,
                "model_inventory_size_at_route": len(registry.all()),
                "model_independent_execution": deterministic_core_delivery,
                "routing_evidence_count": routing_decision.evidence_count if routing_decision else 0,
            }
            # Surface final-attempt transport telemetry at mission level so
            # operational validators/UI do not need to parse private attempt
            # internals.  Multi-model missions still retain the full list.
            if model_attempt_metrics:
                final_attempt = model_attempt_metrics[-1]
                for telemetry_key in (
                    "first_token_ms", "transport_wall_ms", "decode_after_first_ms",
                    "generated_characters", "stream_fragments",
                    "characters_per_second_after_first", "early_completion",
                    "adaptive_num_ctx", "adaptive_num_predict", "residency_keep_alive",
                    "prompt_tokens", "generated_tokens",
                    "tokens_per_second", "load_ms",
                    "residency_policy", "provider_resident_before_request",
                    "cross_process_residency_preserved",
                ):
                    if final_attempt.get(telemetry_key) is not None:
                        metrics[telemetry_key] = final_attempt.get(telemetry_key)

            if cancellation is not None and cancellation.is_set():
                passed = False
                output = ""
                error = "MissionCancelledError: Mission cancelled before durable commit"
            if passed:
                kernel.succeed_dag_node(
                    mission,
                    "ai-direct",
                    {
                        "resource_id": resource_id,
                        "output_length": len(output),
                        "conversation_id": (
                            conversation["id"]
                        ),
                        "workspace_id": (
                            active_workspace["id"]
                            if active_workspace
                            else None
                        ),
                        "tool_calls": len(
                            tool_calls
                        ),
                        "quality_retries": (
                            quality_retries
                        ),
                    },
                )
            else:
                kernel.fail_dag_node(
                    mission,
                    "ai-direct",
                    error
                    or "Local intelligence "
                    "execution failed.",
                )

            prompt_sha256 = hashlib.sha256(
                prompt.encode("utf-8")
            ).hexdigest()

            evidence_id = kernel.add_evidence(
                Evidence(
                    task_id=mission.task_id,
                    council=CouncilId.AI,
                    kind=EvidenceKind.TEST_RESULT,
                    claim=(
                        "Tool-aware conversational DIRECT "
                        "execution "
                        + (
                            "satisfied"
                            if passed
                            else "failed"
                        )
                        + " the P0.6A contract."
                    ),
                    payload={
                        "resource_id": resource_id,
                        "conversation_id": (
                            conversation["id"]
                        ),
                        "workspace_id": (
                            active_workspace["id"]
                            if active_workspace
                            else None
                        ),
                        "tool_calls": tool_calls,
                        "tool_failures": tool_failures,
                        "quality_retries": (
                            quality_retries
                        ),
                        "quality_reason": (
                            quality_reason
                        ),
                        "resource_execution_pass": (
                            response is not None and getattr(response, "success", False)
                        ),
                        "deterministic_core_execution_pass": bool(
                            deterministic_core_delivery and passed
                        ),
                        "non_empty_output": bool(
                            output
                        ),
                        "output_length": len(
                            output
                        ),
                        "prompt_sha256": (
                            prompt_sha256
                        ),
                        "error": error,
                        "metrics": metrics,
                        "verification_scope": (
                            "execution_delivery_"
                            "integrity"
                        ),
                    },
                    source_ref=(
                        "MAESTRO_GATEWAY_P0_6A"
                    ),
                )
            )

            kernel.move(
                mission,
                MissionState.PAUSED_FOR_AUDIT,
            )
            kernel.move(
                mission,
                MissionState.AUDITING,
            )

            verdict_value = (
                VerdictValue.JUSTE
                if passed
                else VerdictValue.FAUTE
            )

            kernel.submit_verdict(
                mission,
                Verdict(
                    task_id=mission.task_id,
                    council=CouncilId.AI,
                    value=verdict_value,
                    scope=VerdictScope.DOMAIN,
                    evidence_ids=[evidence_id],
                    findings=[
                        {
                            "criterion": "AC-02",
                            "result": (
                                "PASS"
                                if passed
                                else "FAIL"
                            ),
                        },
                        {
                            "criterion": "AC-08",
                            "result": (
                                "PASS"
                                if passed
                                else "FAIL"
                            ),
                            "quality_reason": (
                                quality_reason
                            ),
                        },
                    ],
                ),
            )

            kernel.submit_verdict(
                mission,
                Verdict(
                    task_id=mission.task_id,
                    council=CouncilId.AY,
                    value=verdict_value,
                    scope=VerdictScope.SYSTEM,
                    evidence_ids=[evidence_id],
                    findings=[
                        {
                            "criterion": (
                                "P0.6A_DELIVERY_CONTRACT"
                            ),
                            "result": (
                                "JUSTE"
                                if passed
                                else "FAUTE"
                            ),
                            "scope": (
                                "EXECUTION_DELIVERY_"
                                "INTEGRITY_ONLY"
                            ),
                        }
                    ],
                ),
            )

            if passed:
                kernel.move(
                    mission,
                    MissionState.READY_FOR_RELEASE,
                )
                kernel.move(
                    mission,
                    MissionState.STATE_COMMIT_PENDING,
                )

                kernel.authorize_durable_commit(
                    mission
                )
                kernel.commit_durable_state(
                    mission
                )

                kernel.close(
                    mission,
                    durable_change_required=True,
                )

                runtime.persist(
                    mission,
                    "TOOL_AWARE_DIRECT_"
                    "MISSION_COMPLETE",
                    "MAESTRO_GATEWAY",
                    {
                        "resource_id": (
                            resource_id
                        ),
                        "evidence_id": (
                            evidence_id
                        ),
                        "conversation_id": (
                            conversation["id"]
                        ),
                        "workspace_id": (
                            active_workspace["id"]
                            if active_workspace
                            else None
                        ),
                        "tool_calls": tool_calls,
                        "prompt_sha256": (
                            prompt_sha256
                        ),
                        "output_length": len(
                            output
                        ),
                    },
                )
            else:
                kernel.move(
                    mission,
                    MissionState.FAULT_RETURNED,
                )
                kernel.move(
                    mission,
                    MissionState.TASK_ABORTED,
                )

                runtime.persist(
                    mission,
                    "TOOL_AWARE_DIRECT_"
                    "MISSION_FAILED",
                    "MAESTRO_GATEWAY",
                    {
                        "resource_id": (
                            resource_id
                        ),
                        "evidence_id": (
                            evidence_id
                        ),
                        "conversation_id": (
                            conversation["id"]
                        ),
                        "workspace_id": (
                            active_workspace["id"]
                            if active_workspace
                            else None
                        ),
                        "prompt_sha256": (
                            prompt_sha256
                        ),
                        "error": error,
                    },
                )

            ay_value = (
                mission.system_verdict.value.value
                if mission.system_verdict
                else "NONE"
            )

            result = {
                "ok": passed,
                "protocol": "P0.6A",
                "mode": "M0_DIRECT",
                "task_id": mission.task_id,
                "state": mission.state.value,
                "resource_id": resource_id,
                "output": output,
                "metrics": metrics,
                "evidence_id": str(
                    evidence_id
                ),
                "ay_verdict": ay_value,
                "tx_authorized": bool(
                    mission.tx_commit_authorized
                ),
                "tx_committed": bool(
                    mission.tx_committed
                ),
                "verification_scope": (
                    "execution_delivery_"
                    "integrity_only"
                ),
                "semantic_truth_verified": bool(
                    deterministic_core_delivery
                ),
                "conversation_id": (
                    conversation["id"]
                ),
                "conversation_title": (
                    conversation["title"]
                ),
                "workspace_id": (
                    active_workspace["id"]
                    if active_workspace
                    else None
                ),
                "workspace_label": (
                    active_workspace["label"]
                    if active_workspace
                    else None
                ),
                "tool_calls": tool_calls,
                "stages": [
                    "UNDERSTANDING",
                    "CONTEXT",
                    "CAPABILITIES",
                    "CONTRACT",
                    "ROUTING",
                    "CONDUCTING",
                    (
                        "TOOLS"
                        if tool_calls
                        else "NO_TOOLS"
                    ),
                    "QUALITY_GUARD",
                    "VERIFYING",
                    (
                        "TX_COMMIT"
                        if passed
                        else "FAULT_RETURNED"
                    ),
                ],
                "error": error,
            }

            if passed:
                # Publish user + assistant as one conversation-file revision,
                # only AFTER Core has returned its durable verdict. A late
                # cancel after proven Core commit cannot erase that commitment.
                conversation = self._conversations.append_messages(
                    conversation["id"], mission_id=str(result["task_id"]), messages=[
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": output,
                         "metadata": {"mission": result}},
                    ],
                )
                result["conversation_title"] = conversation["title"]
            elif cancellation is None or not cancellation.is_set():
                # A failed, non-cancelled attempt remains visible. Preserve
                # model-failure telemetry as a hidden system entry in the same
                # atomic publication, never as a dangling user-only write.
                messages = [{"role": "user", "content": prompt}]
                if resource_calls:
                    messages.append({
                        "role": "system", "content": "MAESTRO_MISSION_FAILED",
                        "metadata": {"mission": result},
                    })
                conversation = self._conversations.append_messages(
                    conversation["id"], mission_id=str(result["task_id"]), messages=messages,
                )
                result["conversation_title"] = conversation["title"]
            # An observed cancellation before the Core verdict publishes no
            # conversation turn. This does NOT roll back Workspace effects.

            # Record trusted Gateway observations after the governed verdict.
            # No chat content or user prompt is sent to the qualification store.
            for attempt in model_attempt_metrics:
                observe_attempt(attempt.get("resource_id"), route_task,
                                route_language, attempt.get("outcome"),
                                attempt.get("wall_ms"),
                                model_fingerprint=attempt.get("model_fingerprint"))
                if attempt.get("semantic_outcome") in {"pass", "fail"}:
                    observe_semantic_attempt(
                        attempt.get("resource_id"), attempt.get("semantic_subtask"),
                        route_language, attempt.get("semantic_outcome"),
                        attempt.get("wall_ms"),
                        model_fingerprint=attempt.get("model_fingerprint"),
                    )
            if _should_note_model_success(
                ok=bool(result.get("ok")),
                primary_selection=primary_selection,
                resource_calls=resource_calls,
            ):
                # Never fabricate model-success evidence for a response produced
                # entirely by the deterministic Core lane.
                self._core.primary_intelligence.note_success(
                    resource_id, expected_epoch=primary_selection.epoch
                )
            return result
