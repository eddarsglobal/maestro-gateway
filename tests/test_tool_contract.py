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


    def test_terminal_normalizes_arguments(self):
        args = _normalize_tool_arguments(
            "terminal.run",
            {
                "argv": ["pwd"],
                "cwd": ".",
                "timeout_ms": 2500,
            },
        )

        self.assertEqual(
            args,
            {
                "argv": ["pwd"],
                "cwd": ".",
                "timeout_ms": 2500,
            },
        )

    def test_terminal_requires_argv_list(self):
        with self.assertRaises(ValueError):
            _normalize_tool_arguments(
                "terminal.run",
                {
                    "argv": "pwd",
                },
            )

    def test_terminal_timeout_is_strictly_bounded(self):
        with self.assertRaises(ValueError):
            _normalize_tool_arguments(
                "terminal.run",
                {
                    "argv": ["pwd"],
                    "timeout_ms": 6000,
                },
            )


if __name__ == "__main__":
    unittest.main()


class GitToolContractTests(
    unittest.TestCase
):
    def test_status_and_branch_take_no_arguments(
        self,
    ):
        self.assertEqual(
            _normalize_tool_arguments(
                "git.status",
                {},
            ),
            {},
        )

        self.assertEqual(
            _normalize_tool_arguments(
                "git.branch_current",
                {},
            ),
            {},
        )

        with self.assertRaises(
            ValueError
        ):
            _normalize_tool_arguments(
                "git.status",
                {
                    "argv": [
                        "status"
                    ],
                },
            )

    def test_diff_scope_is_normalized(
        self,
    ):
        self.assertEqual(
            _normalize_tool_arguments(
                "git.diff",
                {},
            ),
            {
                "scope": "worktree",
            },
        )

        self.assertEqual(
            _normalize_tool_arguments(
                "git.diff",
                {
                    "scope": "staged",
                },
            ),
            {
                "scope": "staged",
            },
        )

    def test_diff_rejects_arbitrary_scope(
        self,
    ):
        with self.assertRaises(
            ValueError
        ):
            _normalize_tool_arguments(
                "git.diff",
                {
                    "scope": "HEAD~10",
                },
            )

    def test_log_max_count_is_bounded(
        self,
    ):
        self.assertEqual(
            _normalize_tool_arguments(
                "git.log",
                {
                    "max_count": 7,
                },
            ),
            {
                "max_count": 7,
            },
        )

    def test_log_rejects_invalid_count(
        self,
    ):
        for value in (
            0,
            51,
            True,
        ):
            with self.assertRaises(
                ValueError
            ):
                _normalize_tool_arguments(
                    "git.log",
                    {
                        "max_count": value,
                    },
                )
