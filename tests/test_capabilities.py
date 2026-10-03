import unittest

from maestro_gateway.capabilities import CapabilityRegistry


class CapabilityRegistryTests(unittest.TestCase):
    def index(
        self,
        *,
        workspace_active: bool,
        workspace_kind: str | None = None,
    ):
        if workspace_active and workspace_kind is None:
            workspace_kind = "directory"

        rows = CapabilityRegistry().snapshot(
            workspace_active=workspace_active,
            workspace_kind=workspace_kind,
        )
        return {
            row["capability_id"]: row
            for row in rows
        }

    def test_governed_write_is_available_with_workspace(self):
        index = self.index(workspace_active=True)

        self.assertEqual(
            index["filesystem.write"]["status"],
            "available_governed_requires_user_approval",
        )

        self.assertEqual(
            index["filesystem.write"]["approval"],
            "user",
        )

    def test_governed_edit_and_patch_are_available(self):
        index = self.index(workspace_active=True)

        self.assertEqual(
            index["filesystem.edit"]["status"],
            "available_governed_requires_user_approval",
        )

        self.assertEqual(
            index["filesystem.patch"]["status"],
            "available_governed_requires_user_approval",
        )

    def test_mutations_require_workspace(self):
        index = self.index(workspace_active=False)

        for capability_id in (
            "filesystem.write",
            "filesystem.edit",
            "filesystem.patch",
        ):
            self.assertEqual(
                index[capability_id]["status"],
                "available_requires_workspace",
            )

    def test_rename_and_move_are_governed(self):
        index = self.index(workspace_active=True)

        for capability_id in (
            "filesystem.rename",
            "filesystem.move",
        ):
            self.assertEqual(
                index[capability_id]["status"],
                "available_governed_requires_user_approval",
            )
            self.assertEqual(
                index[capability_id]["approval"],
                "user",
            )

    def test_delete_is_governed(self):
        index = self.index(workspace_active=True)

        self.assertEqual(
            index["filesystem.delete"]["status"],
            "available_governed_requires_user_approval",
        )

        self.assertEqual(
            index["filesystem.delete"]["approval"],
            "user",
        )

    def test_terminal_is_inspection_only_with_workspace(self):
        index = self.index(
            workspace_active=True
        )

        self.assertEqual(
            index["terminal.run"]["status"],
            "available_inspection_only_in_authorized_workspace",
        )

        self.assertEqual(
            index["terminal.run"]["approval"],
            "workspace_authorization",
        )

    def test_terminal_requires_workspace(self):
        index = self.index(
            workspace_active=False
        )

        self.assertEqual(
            index["terminal.run"]["status"],
            "available_requires_workspace",
        )


    def test_terminal_rejects_file_workspace_capability(self):
        index = self.index(
            workspace_active=True,
            workspace_kind="file",
        )

        self.assertEqual(
            index["terminal.run"]["status"],
            "not_available_requires_directory_workspace",
        )


class GitInspectionCapabilityTests(
    unittest.TestCase
):
    def index(
        self,
        *,
        workspace_active,
        workspace_kind=None,
    ):
        if (
            workspace_active
            and workspace_kind is None
        ):
            workspace_kind = "directory"

        return {
            row["capability_id"]: row
            for row in (
                CapabilityRegistry()
                .snapshot(
                    workspace_active=(
                        workspace_active
                    ),
                    workspace_kind=(
                        workspace_kind
                    ),
                )
            )
        }

    def test_git_inspection_available_in_directory(
        self,
    ):
        index = self.index(
            workspace_active=True,
        )

        for capability in (
            "git.status",
            "git.diff",
            "git.log",
            "git.branch_current",
        ):
            self.assertEqual(
                index[capability]["status"],
                (
                    "available_inspection_only_"
                    "in_authorized_workspace"
                ),
            )

            self.assertEqual(
                index[capability][
                    "approval"
                ],
                "workspace_authorization",
            )

    def test_git_inspection_requires_workspace(
        self,
    ):
        index = self.index(
            workspace_active=False,
        )

        self.assertEqual(
            index["git.status"]["status"],
            "available_requires_workspace",
        )

    def test_git_inspection_rejects_file_workspace(
        self,
    ):
        index = self.index(
            workspace_active=True,
            workspace_kind="file",
        )

        self.assertEqual(
            index["git.status"]["status"],
            (
                "not_available_requires_"
                "directory_workspace"
            ),
        )

    def test_git_mutation_remains_unavailable(
        self,
    ):
        index = self.index(
            workspace_active=True,
        )

        self.assertEqual(
            index["git.execute"]["status"],
            "not_available_general_git_mutation_p0_6e2a",
        )



class GitStageCapabilityTests(
    unittest.TestCase
):
    def index(
        self,
        *,
        workspace_active,
        workspace_kind=None,
    ):
        if (
            workspace_active
            and workspace_kind is None
        ):
            workspace_kind = "directory"

        return {
            row["capability_id"]: row
            for row in (
                CapabilityRegistry()
                .snapshot(
                    workspace_active=(
                        workspace_active
                    ),
                    workspace_kind=(
                        workspace_kind
                    ),
                )
            )
        }

    def test_git_stage_available_in_directory(
        self,
    ):
        index = self.index(
            workspace_active=True,
        )

        self.assertEqual(
            index["git.stage"]["status"],
            (
                "available_governed_"
                "proposal_approval_only"
            ),
        )

        self.assertEqual(
            index["git.stage"]["approval"],
            "user",
        )

    def test_git_stage_requires_workspace(
        self,
    ):
        index = self.index(
            workspace_active=False,
        )

        self.assertEqual(
            index["git.stage"]["status"],
            "available_requires_workspace",
        )

    def test_git_stage_rejects_file_workspace(
        self,
    ):
        index = self.index(
            workspace_active=True,
            workspace_kind="file",
        )

        self.assertEqual(
            index["git.stage"]["status"],
            (
                "not_available_requires_"
                "directory_workspace"
            ),
        )
