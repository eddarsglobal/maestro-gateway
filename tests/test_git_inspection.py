import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import maestro_gateway.git_inspection as git_inspection_module
from maestro_gateway.git_inspection import (
    MAX_GIT_OUTPUT_BYTES,
    inspect_git,
)
from maestro_gateway.workspace import (
    WorkspaceRegistry,
)


GIT = shutil.which("git")


@unittest.skipUnless(
    GIT,
    "git executable required",
)
class GitInspectionTests(
    unittest.TestCase
):
    def git(
        self,
        root: Path,
        *args: str,
    ):
        return subprocess.run(
            [
                GIT,
                *args,
            ],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            env={
                "PATH": os.environ.get(
                    "PATH",
                    "/usr/bin:/bin",
                ),
                "HOME": str(root),
                "LANG": "C",
                "LC_ALL": "C",
                "GIT_TERMINAL_PROMPT": "0",
            },
        )

    def make_repo(
        self,
        base: Path,
    ) -> Path:
        root = base / "repo"
        root.mkdir()

        self.git(
            root,
            "init",
            "-q",
        )

        (
            root / "tracked.txt"
        ).write_text(
            "one\n",
            encoding="utf-8",
        )

        self.git(
            root,
            "add",
            "--",
            "tracked.txt",
        )

        self.git(
            root,
            "-c",
            "user.name=MAESTRO Test",
            "-c",
            "user.email=maestro@example.invalid",
            "commit",
            "--no-gpg-sign",
            "-q",
            "-m",
            "initial",
        )

        return root

    def test_status_reports_changes(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_repo(
                Path(td)
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            (
                root / "untracked.txt"
            ).write_text(
                "new\n",
                encoding="utf-8",
            )

            result = inspect_git(
                workspace_root=root,
                operation="git.status",
            )

            self.assertIn(
                "tracked.txt",
                result["porcelain_v2"],
            )

            self.assertIn(
                "untracked.txt",
                result["porcelain_v2"],
            )

    def test_diff_reports_worktree(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_repo(
                Path(td)
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            result = inspect_git(
                workspace_root=root,
                operation="git.diff",
                options={
                    "scope": "worktree",
                },
            )

            self.assertIn(
                "-one",
                result["diff"],
            )

            self.assertIn(
                "+two",
                result["diff"],
            )

    def test_sensitive_worktree_diff_is_rejected_before_content_diff(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_repo(
                Path(td)
            )

            sensitive = (
                root / ".env"
            )

            sensitive.write_text(
                "TOKEN=initial\n",
                encoding="utf-8",
            )

            self.git(
                root,
                "add",
                "--",
                ".env",
            )

            self.git(
                root,
                "-c",
                "user.name=MAESTRO Test",
                "-c",
                (
                    "user.email="
                    "maestro@example.invalid"
                ),
                "commit",
                "--no-gpg-sign",
                "-q",
                "-m",
                "track sensitive fixture",
            )

            secret = (
                "MAESTRO_SECRET_"
                "WORKTREE_SENTINEL"
            )

            sensitive.write_text(
                f"TOKEN={secret}\n",
                encoding="utf-8",
            )

            calls = []

            original_call = (
                git_inspection_module._call
            )

            def recording_call(
                *args,
                **kwargs,
            ):
                calls.append(
                    list(
                        kwargs.get(
                            "args"
                        )
                        or []
                    )
                )

                return original_call(
                    *args,
                    **kwargs,
                )

            with mock.patch.object(
                git_inspection_module,
                "_call",
                side_effect=recording_call,
            ):
                with self.assertRaises(
                    PermissionError
                ) as ctx:
                    inspect_git(
                        workspace_root=root,
                        operation="git.diff",
                        options={
                            "scope": (
                                "worktree"
                            ),
                        },
                    )

            self.assertNotIn(
                secret,
                str(ctx.exception),
            )

            diff_calls = [
                argv
                for argv in calls
                if (
                    argv
                    and argv[0]
                    == "diff"
                )
            ]

            self.assertTrue(
                diff_calls
            )

            self.assertTrue(
                all(
                    "--name-only"
                    in argv
                    for argv
                    in diff_calls
                )
            )

    def test_sensitive_staged_diff_is_rejected_without_secret_leak(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_repo(
                Path(td)
            )

            sensitive = (
                root
                / "credentials.json"
            )

            sensitive.write_text(
                '{"token":"initial"}\n',
                encoding="utf-8",
            )

            self.git(
                root,
                "add",
                "--",
                "credentials.json",
            )

            self.git(
                root,
                "-c",
                "user.name=MAESTRO Test",
                "-c",
                (
                    "user.email="
                    "maestro@example.invalid"
                ),
                "commit",
                "--no-gpg-sign",
                "-q",
                "-m",
                "track sensitive fixture",
            )

            secret = (
                "MAESTRO_SECRET_"
                "STAGED_SENTINEL"
            )

            sensitive.write_text(
                (
                    '{"token":"'
                    + secret
                    + '"}\n'
                ),
                encoding="utf-8",
            )

            self.git(
                root,
                "add",
                "--",
                "credentials.json",
            )

            with self.assertRaises(
                PermissionError
            ) as ctx:
                inspect_git(
                    workspace_root=root,
                    operation="git.diff",
                    options={
                        "scope": "staged",
                    },
                )

            message = str(
                ctx.exception
            )

            self.assertNotIn(
                secret,
                message,
            )

            self.assertNotIn(
                "credentials.json",
                message,
            )

    def test_external_diff_is_disabled(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(base)

            marker = (
                base
                / "EXTERNAL_DIFF_EXECUTED"
            )

            helper = (
                base
                / "external-diff.sh"
            )

            helper.write_text(
                "#!/bin/sh\n"
                f"touch {str(marker)!r}\n"
                "exit 0\n",
                encoding="utf-8",
            )

            helper.chmod(0o700)

            self.git(
                root,
                "config",
                "diff.external",
                str(helper),
            )

            (
                root / "tracked.txt"
            ).write_text(
                "changed\n",
                encoding="utf-8",
            )

            result = inspect_git(
                workspace_root=root,
                operation="git.diff",
            )

            self.assertFalse(
                marker.exists()
            )

            self.assertIn(
                "tracked.txt",
                result["diff"],
            )

    def test_log_returns_commit(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_repo(
                Path(td)
            )

            result = inspect_git(
                workspace_root=root,
                operation="git.log",
                options={
                    "max_count": 5,
                },
            )

            self.assertEqual(
                len(
                    result["commits"]
                ),
                1,
            )

            self.assertEqual(
                result["commits"][0][
                    "subject"
                ],
                "initial",
            )

    def test_branch_current(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_repo(
                Path(td)
            )

            result = inspect_git(
                workspace_root=root,
                operation=(
                    "git.branch_current"
                ),
            )

            self.assertTrue(
                result["branch"]
            )

            self.assertRegex(
                result["head"],
                r"^[0-9a-f]{40}$",
            )

            self.assertFalse(
                result["detached"]
            )

    def test_non_repository_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = (
                Path(td)
                / "not-repo"
            )

            root.mkdir()

            with self.assertRaises(
                ValueError
            ):
                inspect_git(
                    workspace_root=root,
                    operation="git.status",
                )

    def test_nested_workspace_root_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_repo(
                Path(td)
            )

            child = root / "src"
            child.mkdir()

            with self.assertRaises(
                PermissionError
            ):
                inspect_git(
                    workspace_root=child,
                    operation="git.status",
                )

    def test_file_workspace_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            file_path = (
                base / "single.txt"
            )

            file_path.write_text(
                "x\n",
                encoding="utf-8",
            )

            registry = WorkspaceRegistry(
                base / "registry.json"
            )

            workspace = (
                registry.authorize_path(
                    str(file_path)
                )
            )

            with self.assertRaises(
                ValueError
            ):
                registry.execute_tool(
                    workspace["id"],
                    "git.status",
                    {},
                )

    def test_large_diff_is_bounded(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_repo(
                Path(td)
            )

            (
                root / "tracked.txt"
            ).write_text(
                "X" * 100_000
                + "\n",
                encoding="utf-8",
            )

            result = inspect_git(
                workspace_root=root,
                operation="git.diff",
            )

            command = (
                result[
                    "execution_evidence"
                ]["commands"][-1]
            )

            self.assertTrue(
                command[
                    "stdout_truncated"
                ]
            )

            self.assertGreater(
                command[
                    "stdout_bytes"
                ],
                MAX_GIT_OUTPUT_BYTES,
            )

            self.assertEqual(
                len(
                    command[
                        "stdout_sha256"
                    ]
                ),
                64,
            )

    def test_inspection_preserves_index(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_repo(
                Path(td)
            )

            index = (
                root
                / ".git"
                / "index"
            )

            before = hashlib.sha256(
                index.read_bytes()
            ).hexdigest()

            inspect_git(
                workspace_root=root,
                operation="git.status",
            )

            inspect_git(
                workspace_root=root,
                operation="git.diff",
            )

            inspect_git(
                workspace_root=root,
                operation="git.log",
            )

            inspect_git(
                workspace_root=root,
                operation=(
                    "git.branch_current"
                ),
            )

            after = hashlib.sha256(
                index.read_bytes()
            ).hexdigest()

            self.assertEqual(
                before,
                after,
            )


if __name__ == "__main__":
    unittest.main()
