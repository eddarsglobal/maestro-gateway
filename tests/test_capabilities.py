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

    def test_write_is_proposal_only_in_p0_6b1(self):
        index = self.index(workspace_active=True)
        self.assertEqual(
            index["filesystem.write"]["status"],
            "proposal_only_requires_user_approval",
        )
        self.assertEqual(
            index["filesystem.write"]["approval"],
            "user",
        )

    def test_edit_and_patch_are_proposal_only(self):
        index = self.index(workspace_active=True)
        self.assertEqual(
            index["filesystem.edit"]["status"],
            "proposal_only_requires_user_approval",
        )
        self.assertEqual(
            index["filesystem.patch"]["status"],
            "proposal_only_requires_user_approval",
        )

    def test_delete_remains_disabled(self):
        index = self.index(workspace_active=True)
        self.assertEqual(
            index["filesystem.delete"]["status"],
            "not_available_p0_6b1",
        )


if __name__ == "__main__":
    unittest.main()
