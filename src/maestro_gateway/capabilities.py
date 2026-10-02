from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True, slots=True)
class Capability:
    capability_id: str
    domain: str
    status: str
    description: str
    approval: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapabilityRegistry:
    """Gateway-visible reach registry.

    MAESTRO Core remains the authority boundary. This registry describes
    capabilities exposed through the Local Gateway; it does not grant a model
    authority by itself.
    """

    def snapshot(
        self,
        *,
        workspace_active: bool = False,
        workspace_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        workspace_state = (
            "available_in_authorized_workspace"
            if workspace_active
            else "available_requires_workspace"
        )

        governed_mutation_state = (
            "available_governed_requires_user_approval"
            if workspace_active
            else "available_requires_workspace"
        )

        if not workspace_active:
            terminal_state = "available_requires_workspace"
        elif workspace_kind == "directory":
            terminal_state = (
                "available_inspection_only_in_authorized_workspace"
            )
        else:
            terminal_state = (
                "not_available_requires_directory_workspace"
            )

        capabilities = [
            Capability(
                "conversation.context",
                "memory",
                "available",
                "Persistent local conversation context.",
            ),
            Capability(
                "local_inference.execute",
                "intelligence",
                "available",
                "Governed local model execution through MAESTRO.",
            ),
            Capability(
                "workspace.open",
                "workspace",
                "available_requires_user_approval",
                "Authorize one explicit local file or directory.",
                approval="user",
            ),
            Capability(
                "workspace.tree",
                "workspace",
                workspace_state,
                "List files within an authorized workspace.",
            ),
            Capability(
                "filesystem.read",
                "filesystem",
                workspace_state,
                "Read bounded text files inside an authorized workspace.",
            ),
            Capability(
                "filesystem.search",
                "filesystem",
                workspace_state,
                "Search filenames and text inside an authorized workspace.",
            ),
            Capability(
                "filesystem.write",
                "filesystem",
                governed_mutation_state,
                "Propose, approve, and separately atomically apply a governed write/create inside an authorized workspace.",
                approval="user",
            ),
            Capability(
                "filesystem.edit",
                "filesystem",
                governed_mutation_state,
                "Propose, approve, and separately atomically apply a governed full-content edit inside an authorized workspace.",
                approval="user",
            ),
            Capability(
                "filesystem.patch",
                "filesystem",
                governed_mutation_state,
                "Propose, approve, and separately atomically apply a governed patch with diff and SHA-256 evidence.",
                approval="user",
            ),
            Capability(
                "filesystem.rename",
                "filesystem",
                governed_mutation_state,
                "Propose, approve, and separately apply a governed same-directory file rename with fingerprint and collision protection.",
                approval="user",
            ),
            Capability(
                "filesystem.move",
                "filesystem",
                governed_mutation_state,
                "Propose, approve, and separately apply a governed same-workspace file move with fingerprint and collision protection.",
                approval="user",
            ),
            Capability(
                "filesystem.delete",
                "filesystem",
                governed_mutation_state,
                "Propose, explicitly confirm, approve, and separately apply a governed recoverable file delete with fingerprint verification and private recovery staging.",
                approval="user",
            ),
            Capability(
                "terminal.run",
                "development",
                terminal_state,
                "Run allowlisted, non-interactive, read-only inspection commands inside an authorized directory workspace.",
                approval="workspace_authorization",
            ),
            Capability(
                "git.execute",
                "development",
                "not_available_p0_6c2",
                "Git execution is planned for a later governed stage.",
                approval="future_sandbox",
            ),
            Capability(
                "computer_use",
                "computer",
                "not_available",
                "Desktop/browser computer-use capability is not enabled.",
                approval="future",
            ),
        ]

        return [item.as_dict() for item in capabilities]

    def prompt_summary(
        self,
        *,
        workspace_active: bool = False,
        workspace_kind: str | None = None,
    ) -> str:
        rows = [
            "MAESTRO CAPABILITY SNAPSHOT",
            "Do not claim capabilities that are not listed as available.",
            "A capability marked requires_user_approval may only be used after "
            "the user explicitly authorizes its scope.",
            "",
        ]

        for item in self.snapshot(
            workspace_active=workspace_active,
            workspace_kind=workspace_kind,
        ):
            rows.append(
                f"- {item['capability_id']}: {item['status']}"
            )

        return "\n".join(rows)
