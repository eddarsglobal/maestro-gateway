from __future__ import annotations

import hashlib
import json
import re
from typing import Any

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

from .capabilities import CapabilityRegistry
from .conversations import ConversationStore
from .core import MaestroCoreBridge
from .response_guard import evaluate_response
from .workspace import WorkspaceRegistry


PREFERRED_GENERAL_RESOURCES = (
    "ollama::qwen3:8b",
    "ollama::gpt-oss:20b",
    "ollama::gemma4:e4b",
    "ollama::llama3.2:latest",
    "ollama::qwen3:0.6b",
)

_TOOL_PATTERN = re.compile(
    r"^\s*<MAESTRO_TOOL>\s*(\{.*\})\s*</MAESTRO_TOOL>\s*$",
    re.DOTALL,
)

MAX_TOOL_ROUNDS = 5
MAX_QUALITY_RETRIES = 1


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

    if value.startswith("ollama::"):
        return value

    return f"ollama::{value}"


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

    requested = _requested_resource_id(model)

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

    for resource_id in PREFERRED_GENERAL_RESOURCES:
        if resource_id in eligible:
            return resource_id

    if eligible:
        return sorted(eligible)[0]

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

        with self._core.mission_lock:
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

            try:
                evidence = (
                    runtime.research_fetch(
                        mission,
                        url,
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
                url=research_url
            )

        if normalized_mode != "M0_DIRECT":
            raise ValueError(
                "Unsupported mission mode."
            )

        prompt, max_tokens = validate_input(
            prompt,
            max_tokens,
        )

        with self._core.mission_lock:
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

            conversation = (
                self._conversations.append_message(
                    conversation["id"],
                    role="user",
                    content=prompt,
                )
            )

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

            self._core.ensure_ollama_resources()
            system = self._core.system

            runtime = system.os.runtime
            kernel = runtime.kernel
            registry = (
                system.sovereign_intelligence.registry
            )

            resource_id = choose_direct_resource(
                registry,
                model=model,
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

            capability_text = (
                self._capabilities.prompt_summary(
                    workspace_active=bool(
                        active_workspace
                    ),
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

            base_instruction = (
                capability_text
                + "\n\n"
                + language_rule
                + "\n"
                + behavior_rule
                + tool_rule
                + context_block
                + "\n\nCURRENT USER REQUEST:\n"
                + prompt
            )

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

            try:
                adapter = registry.adapter(
                    resource_id
                )

                for _round in range(
                    MAX_TOOL_ROUNDS
                    + MAX_QUALITY_RETRIES
                    + 1
                ):
                    request = IntelligenceRequest(
                        task_id=mission.task_id,
                        capability=(
                            IntelligenceCapability.DIRECT
                        ),
                        instruction=transcript,
                        max_tokens=max_tokens,
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
                        },
                    )

                    response = adapter.execute(
                        request
                    )
                    resource_calls += 1

                    candidate = str(
                        response.output or ""
                    ).strip()

                    raw_metrics = dict(
                        getattr(
                            response,
                            "metadata",
                            {},
                        )
                        or {}
                    )
                    metrics = {
                        key: raw_metrics.get(key)
                        for key in (
                            "total_ms",
                            "load_ms",
                            "prompt_tokens",
                            "generated_tokens",
                            "tokens_per_second",
                            "think_policy",
                        )
                        if key in raw_metrics
                    }

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
                                )
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
                    )
                    quality_reason = quality.reason

                    if (
                        not quality.accepted
                        and quality_retries
                        < MAX_QUALITY_RETRIES
                    ):
                        quality_retries += 1
                        transcript += (
                            "\n\nMAESTRO QUALITY GUARD:\n"
                            "The previous candidate was "
                            "rejected because it merely "
                            "repeated or closely paraphrased "
                            "the user's request. Answer the "
                            "question substantively instead. "
                            "Do not repeat the request.\n"
                            "REJECTED CANDIDATE:\n"
                            + candidate
                        )
                        continue

                    if not quality.accepted:
                        raise RuntimeError(
                            "Response Quality Guard rejected "
                            f"the final output: {quality.reason}"
                        )

                    output = candidate
                    break

                if not output:
                    raise RuntimeError(
                        "No final user-facing response was "
                        "produced"
                    )

                passed = True

            except Exception as exc:
                passed = False
                error = (
                    f"{type(exc).__name__}: {exc}"
                )

            metrics = {
                **metrics,
                "resource_calls": resource_calls,
                "tool_calls": len(tool_calls),
                "tool_failures": tool_failures,
                "quality_retries": (
                    quality_retries
                ),
                "quality_reason": quality_reason,
            }

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
                        "resource_execution_pass": bool(
                            response is not None
                            and getattr(
                                response,
                                "success",
                                False,
                            )
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
                "semantic_truth_verified": (
                    False
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
                conversation = (
                    self._conversations.append_message(
                        conversation["id"],
                        role="assistant",
                        content=output,
                        metadata={
                            "mission": result
                        },
                    )
                )
                result["conversation_title"] = (
                    conversation["title"]
                )

            return result
