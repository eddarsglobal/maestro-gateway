import unittest

from maestro_gateway.mission import (
    _normalize_tool_arguments,
)


class ToolContractTests(unittest.TestCase):
    def test_read_requires_real_file_path(self):
        with self.assertRaises(ValueError):
            _normalize_tool_arguments(
                "workspace.read",
                {"path": ""},
            )

        with self.assertRaises(ValueError):
            _normalize_tool_arguments(
                "workspace.read",
                {"path": "."},
            )

    def test_read_package_json_is_valid(self):
        args = _normalize_tool_arguments(
            "workspace.read",
            {"path": "package.json"},
        )

        self.assertEqual(
            args,
            {"path": "package.json"},
        )

    def test_search_requires_query(self):
        with self.assertRaises(ValueError):
            _normalize_tool_arguments(
                "workspace.search",
                {"query": ""},
            )

    def test_tree_normalizes_depth(self):
        args = _normalize_tool_arguments(
            "workspace.tree",
            {
                "path": "src",
                "max_depth": 99,
            },
        )

        self.assertEqual(args["path"], "src")
        self.assertEqual(args["max_depth"], 6)


if __name__ == "__main__":
    unittest.main()
