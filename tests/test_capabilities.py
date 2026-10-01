import unittest

from maestro_gateway.capabilities import CapabilityRegistry


class CapabilityRegistryTests(unittest.TestCase):
    def index(self, *, workspace_active: bool):
        rows = CapabilityRegistry().snapshot(
            workspace_active=workspace_active
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

    def test_delete_remains_disabled(self):
        index = self.index(workspace_active=True)

        self.assertEqual(
            index["filesystem.delete"]["status"],
            "not_available_p0_6b3",
        )


if __name__ == "__main__":
    unittest.main()
