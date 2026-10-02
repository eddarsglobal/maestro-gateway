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
