import tempfile
import unittest
from pathlib import Path

from maestro_gateway.terminal import (
    MAX_INPUT_BYTES,
)
from maestro_gateway.workspace import (
    WorkspaceRegistry,
)


class SandboxedTerminalTests(
    unittest.TestCase
):
    def make_directory_workspace(
        self,
        td: str,
    ):
        root = Path(td) / "project"
        root.mkdir()

        store = WorkspaceRegistry(
            Path(td) / "registry.json"
        )

        row = store.authorize_path(
            str(root)
        )

        return root, store, row

    def test_pwd_runs_inside_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root, store, row = (
                self.make_directory_workspace(td)
            )

            result = store.execute_tool(
                row["id"],
                "terminal.run",
                {
                    "argv": ["pwd"],
                    "cwd": ".",
                    "timeout_ms": 1000,
                },
            )

            self.assertEqual(
                result["protocol"],
                "P0.6D.1",
            )

            self.assertEqual(
                result["mode"],
                "inspection_only",
            )

            self.assertEqual(
                result["exit_code"],
                0,
            )

            self.assertEqual(
                result["stdout"].strip(),
                str(root.resolve()),
            )

            evidence = result[
                "execution_evidence"
            ]

            self.assertFalse(
                evidence["shell"]
            )

            self.assertEqual(
                evidence["stdin"],
                "disabled",
            )

    def test_nested_cwd_is_confined(self):
        with tempfile.TemporaryDirectory() as td:
            root, store, row = (
                self.make_directory_workspace(td)
            )

            src = root / "src"
            src.mkdir()

            result = store.execute_tool(
                row["id"],
                "terminal.run",
                {
                    "argv": ["pwd"],
                    "cwd": "src",
                },
            )

            self.assertEqual(
                result["cwd"],
                "src",
            )

            self.assertEqual(
                result["stdout"].strip(),
                str(src.resolve()),
            )

    def test_cwd_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            _, store, row = (
                self.make_directory_workspace(td)
            )

            with self.assertRaises(
                PermissionError
            ):
                store.execute_tool(
                    row["id"],
                    "terminal.run",
                    {
                        "argv": ["pwd"],
                        "cwd": "../",
                    },
                )

    def test_non_allowlisted_commands_are_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            _, store, row = (
                self.make_directory_workspace(td)
            )

            for command in (
                "sh",
                "bash",
                "zsh",
                "python3",
                "git",
                "curl",
                "rm",
                "npm",
            ):
                with self.subTest(
                    command=command
                ):
                    with self.assertRaises(
                        PermissionError
                    ):
                        store.execute_tool(
                            row["id"],
                            "terminal.run",
                            {
                                "argv": [
                                    command
                                ],
                            },
                        )

    def test_sensitive_operand_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root, store, row = (
                self.make_directory_workspace(td)
            )

            (
                root / ".env"
            ).write_text(
                "SECRET=1\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                PermissionError
            ):
                store.execute_tool(
                    row["id"],
                    "terminal.run",
                    {
                        "argv": [
                            "head",
                            ".env",
                        ],
                    },
                )

    def test_head_reads_bounded_file(self):
        with tempfile.TemporaryDirectory() as td:
            root, store, row = (
                self.make_directory_workspace(td)
            )

            (
                root / "notes.txt"
            ).write_text(
                "one\ntwo\nthree\n",
                encoding="utf-8",
            )

            result = store.execute_tool(
                row["id"],
                "terminal.run",
                {
                    "argv": [
                        "head",
                        "-n",
                        "2",
                        "notes.txt",
                    ],
                },
            )

            self.assertEqual(
                result["exit_code"],
                0,
            )

            self.assertEqual(
                result["stdout"],
                "one\ntwo\n",
            )

    def test_large_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root, store, row = (
                self.make_directory_workspace(td)
            )

            (
                root / "huge.txt"
            ).write_bytes(
                b"x"
                * (
                    MAX_INPUT_BYTES
                    + 1
                )
            )

            with self.assertRaises(
                ValueError
            ):
                store.execute_tool(
                    row["id"],
                    "terminal.run",
                    {
                        "argv": [
                            "head",
                            "huge.txt",
                        ],
                    },
                )

    def test_file_workspace_cannot_run_terminal(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            target = (
                Path(td)
                / "single.txt"
            )

            target.write_text(
                "hello\n",
                encoding="utf-8",
            )

            store = WorkspaceRegistry(
                Path(td)
                / "registry.json"
            )

            row = store.authorize_path(
                str(target)
            )

            with self.assertRaises(
                ValueError
            ):
                store.execute_tool(
                    row["id"],
                    "terminal.run",
                    {
                        "argv": ["pwd"],
                    },
                )


if __name__ == "__main__":
    unittest.main()
