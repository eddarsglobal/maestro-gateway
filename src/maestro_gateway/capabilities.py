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
    ) -> list[dict[str, Any]]:
        workspace_state = (
            "available_in_authorized_workspace"
            if workspace_active
            else "available_requires_workspace"
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
                "proposal_only_requires_user_approval",
                "Stage a governed write/create proposal; application is disabled in P0.6B.1.",
                approval="user",
            ),
            Capability(
                "filesystem.edit",
                "filesystem",
                "proposal_only_requires_user_approval",
                "Stage a governed full-content edit proposal; application is disabled in P0.6B.1.",
                approval="user",
            ),
            Capability(
                "filesystem.patch",
                "filesystem",
                "proposal_only_requires_user_approval",
                "Stage a governed patch proposal with diff/hash evidence; application is disabled in P0.6B.1.",
                approval="user",
            ),
            Capability(
                "filesystem.delete",
                "filesystem",
                "not_available_p0_6b1",
                "Delete remains intentionally disabled in P0.6B.1.",
                approval="future_governed_delete",
            ),
            Capability(
                "terminal.run",
                "development",
                "not_available_p0_6b1",
                "Terminal execution is planned for a later governed stage.",
                approval="future_sandbox",
            ),
            Capability(
                "git.execute",
                "development",
                "not_available_p0_6b1",
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
    ) -> str:
        rows = [
            "MAESTRO CAPABILITY SNAPSHOT",
            "Do not claim capabilities that are not listed as available.",
            "A capability marked requires_user_approval may only be used after "
            "the user explicitly authorizes its scope.",
            "",
        ]

        for item in self.snapshot(
            workspace_active=workspace_active
        ):
            rows.append(
                f"- {item['capability_id']}: {item['status']}"
            )

        return "\n".join(rows)
