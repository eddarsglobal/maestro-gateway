import unittest

from maestro_gateway.capabilities import (
    CapabilityRegistry,
)


class CapabilityRegistryTests(unittest.TestCase):
    def test_write_disabled_in_p0_6a(self):
        rows = CapabilityRegistry().snapshot(
            workspace_active=True
        )
        index = {
            row["capability_id"]: row
            for row in rows
        }

        self.assertEqual(
            index["filesystem.write"]["status"],
            "not_available_p0_6a",
        )

    def test_read_requires_workspace(self):
        rows = CapabilityRegistry().snapshot(
            workspace_active=False
        )
        index = {
            row["capability_id"]: row
            for row in rows
        }

        self.assertEqual(
            index["filesystem.read"]["status"],
            "available_requires_workspace",
        )


if __name__ == "__main__":
    unittest.main()
