import json
import tempfile
import unittest
from pathlib import Path

from maestro_gateway.execution import (
    ExecutionProposalStore,
    ExecutionStaleError,
)
from maestro_gateway.workspace import (
    WorkspaceRegistry,
)


class GovernedExecutionTests(
    unittest.TestCase
):
    def make_store(
        self,
        td: str,
    ):
        base = Path(td)

        workspaces = WorkspaceRegistry(
            base / "registry.json"
        )

        executions = (
            ExecutionProposalStore(
                base / "executions",
                workspaces,
            )
        )

        return (
            base,
            workspaces,
            executions,
        )

    def make_python_project(
        self,
        root: Path,
    ):
        root.mkdir()

        (
            root / "src"
        ).mkdir()

        (
            root / "tests"
        ).mkdir()

        (
            root / "pyproject.toml"
        ).write_text(
            "[project]\n"
            'name = "demo"\n'
            'version = "0.0.1"\n',
            encoding="utf-8",
        )

        (
            root
            / "src"
            / "app.py"
        ).write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )

        (
            root
            / "tests"
            / "test_app.py"
        ).write_text(
            "import unittest\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )

    def test_profile_detects_python_unittest(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = base / "project"

            self.make_python_project(
                root
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            result = executions.profiles(
                workspace["id"]
            )

            profile_ids = {
                row["profile"]
                for row in result[
                    "profiles"
                ]
            }

            self.assertIn(
                "python_unittest",
                profile_ids,
            )

    def test_profile_detects_node_build_lint_without_test(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = base / "ui"
            root.mkdir()

            (
                root / "src"
            ).mkdir()

            (
                root
                / "src"
                / "main.ts"
            ).write_text(
                "export const x = 1\n",
                encoding="utf-8",
            )

            (
                root / "package.json"
            ).write_text(
                json.dumps(
                    {
                        "scripts": {
                            "build": (
                                "tsc -b && vite build"
                            ),
                            "lint": "oxlint",
                        }
                    }
                ),
                encoding="utf-8",
            )

            (
                root / "package-lock.json"
            ).write_text(
                "{}\n",
                encoding="utf-8",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            profiles = {
                row["profile"]
                for row in (
                    executions
                    .profiles(
                        workspace["id"]
                    )["profiles"]
                )
            }

            self.assertIn(
                "node_npm_build",
                profiles,
            )

            self.assertIn(
                "node_npm_lint",
                profiles,
            )

            self.assertNotIn(
                "node_npm_test",
                profiles,
            )

    def test_file_workspace_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            target = (
                base / "single.py"
            )

            target.write_text(
                "print('x')\n",
                encoding="utf-8",
            )

            workspace = (
                workspaces.authorize_path(
                    str(target)
                )
            )

            with self.assertRaises(
                ValueError
            ):
                executions.profiles(
                    workspace["id"]
                )

    def test_proposal_is_pending_without_execution(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = base / "project"

            self.make_python_project(
                root
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            proposal = (
                executions.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    kind="test",
                    profile=(
                        "python_unittest"
                    ),
                )
            )

            self.assertEqual(
                proposal["status"],
                "AWAITING_APPROVAL",
            )

            self.assertFalse(
                proposal[
                    "execution_performed"
                ]
            )

            self.assertTrue(
                proposal[
                    "approval_required"
                ]
            )

    def test_approval_is_state_only(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = base / "project"

            self.make_python_project(
                root
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            proposal = (
                executions.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    kind="test",
                    profile=(
                        "python_unittest"
                    ),
                )
            )

            approved = (
                executions.approve(
                    proposal["id"]
                )
            )

            self.assertEqual(
                approved["status"],
                "APPROVED",
            )

            self.assertFalse(
                approved[
                    "execution_performed"
                ]
            )

            self.assertEqual(
                [
                    event["action"]
                    for event in (
                        approved[
                            "history"
                        ]
                    )
                ],
                [
                    "PROPOSED",
                    "APPROVED",
                ],
            )

    def test_workspace_change_makes_approval_stale(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = base / "project"

            self.make_python_project(
                root
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            proposal = (
                executions.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    kind="test",
                    profile=(
                        "python_unittest"
                    ),
                )
            )

            (
                root
                / "src"
                / "app.py"
            ).write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                ExecutionStaleError
            ):
                executions.approve(
                    proposal["id"]
                )

            stale = executions.get(
                proposal["id"]
            )

            self.assertEqual(
                stale["status"],
                "STALE",
            )

            self.assertFalse(
                stale[
                    "execution_performed"
                ]
            )

    def test_rejection_is_state_only(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = base / "project"

            self.make_python_project(
                root
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            proposal = (
                executions.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    kind="test",
                    profile=(
                        "python_unittest"
                    ),
                )
            )

            rejected = (
                executions.reject(
                    proposal["id"],
                    reason="not now",
                )
            )

            self.assertEqual(
                rejected["status"],
                "REJECTED",
            )

            self.assertFalse(
                rejected[
                    "execution_performed"
                ]
            )

    def test_unknown_profile_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = base / "project"

            self.make_python_project(
                root
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            with self.assertRaises(
                ValueError
            ):
                executions.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    kind="test",
                    profile=(
                        "arbitrary_command"
                    ),
                )



class GovernedExecutionRuntimeTests(
    unittest.TestCase
):
    def make_store(
        self,
        td: str,
    ):
        base = Path(td)

        workspaces = WorkspaceRegistry(
            base / "registry.json"
        )

        executions = (
            ExecutionProposalStore(
                base / "executions",
                workspaces,
            )
        )

        return (
            base,
            workspaces,
            executions,
        )

    def project(
        self,
        base: Path,
        test_source: str,
    ) -> Path:
        root = base / "project"
        root.mkdir()

        (root / "src").mkdir()
        (root / "tests").mkdir()

        (
            root / "pyproject.toml"
        ).write_text(
            "[project]\n"
            'name = "runtime-cert"\n'
            'version = "0.0.1"\n',
            encoding="utf-8",
        )

        (
            root
            / "src"
            / "app.py"
        ).write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )

        (
            root
            / "tests"
            / "test_runtime.py"
        ).write_text(
            test_source,
            encoding="utf-8",
        )

        return root

    def propose_and_approve(
        self,
        executions,
        workspace_id,
    ):
        proposal = executions.propose(
            workspace_id=workspace_id,
            kind="test",
            profile="python_unittest",
        )

        return executions.approve(
            proposal["id"]
        )

    def test_execute_requires_approval(self):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = self.project(
                base,
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            proposal = executions.propose(
                workspace_id=(
                    workspace["id"]
                ),
                kind="test",
                profile=(
                    "python_unittest"
                ),
            )

            from maestro_gateway.execution import (
                ExecutionStateError,
            )

            with self.assertRaises(
                ExecutionStateError
            ):
                executions.execute(
                    proposal["id"],
                    confirm_execution=True,
                )

    def test_execute_requires_literal_confirmation(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = self.project(
                base,
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            approved = (
                self.propose_and_approve(
                    executions,
                    workspace["id"],
                )
            )

            with self.assertRaises(
                ValueError
            ):
                executions.execute(
                    approved["id"],
                    confirm_execution=False,
                )

            current = executions.get(
                approved["id"]
            )

            self.assertEqual(
                current["status"],
                "APPROVED",
            )

            self.assertFalse(
                current[
                    "execution_performed"
                ]
            )

    def test_approved_test_executes_and_passes(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            marker = (
                base / "project"
                / "executed.txt"
            )

            root = self.project(
                base,
                "import unittest\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text("
                "'EXECUTED\\n', encoding='utf-8')\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            approved = (
                self.propose_and_approve(
                    executions,
                    workspace["id"],
                )
            )

            self.assertFalse(
                marker.exists()
            )

            result = executions.execute(
                approved["id"],
                confirm_execution=True,
            )

            self.assertEqual(
                result["status"],
                "PASSED",
            )

            self.assertEqual(
                result["exit_code"],
                0,
            )

            self.assertTrue(
                result[
                    "execution_performed"
                ]
            )

            self.assertTrue(
                marker.exists()
            )

            self.assertTrue(
                result[
                    "workspace_changed_during_execution"
                ]
            )

            self.assertEqual(
                [
                    event["action"]
                    for event in (
                        result["history"]
                    )
                ],
                [
                    "PROPOSED",
                    "APPROVED",
                    "EXECUTION_STARTED",
                    "PASSED",
                ],
            )

    def test_failed_test_records_failed(self):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = self.project(
                base,
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_fail(self):\n"
                "        self.assertEqual(1, 2)\n",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            approved = (
                self.propose_and_approve(
                    executions,
                    workspace["id"],
                )
            )

            result = executions.execute(
                approved["id"],
                confirm_execution=True,
            )

            self.assertEqual(
                result["status"],
                "FAILED",
            )

            self.assertNotEqual(
                result["exit_code"],
                0,
            )

    def test_change_after_approval_blocks_execute(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = self.project(
                base,
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            approved = (
                self.propose_and_approve(
                    executions,
                    workspace["id"],
                )
            )

            (
                root
                / "src"
                / "app.py"
            ).write_text(
                "VALUE = 999\n",
                encoding="utf-8",
            )

            from maestro_gateway.execution import (
                ExecutionStaleError,
            )

            with self.assertRaises(
                ExecutionStaleError
            ):
                executions.execute(
                    approved["id"],
                    confirm_execution=True,
                )

            stale = executions.get(
                approved["id"]
            )

            self.assertEqual(
                stale["status"],
                "STALE",
            )

            self.assertFalse(
                stale[
                    "execution_performed"
                ]
            )

    def test_execution_is_anti_replay(self):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = self.project(
                base,
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            approved = (
                self.propose_and_approve(
                    executions,
                    workspace["id"],
                )
            )

            passed = executions.execute(
                approved["id"],
                confirm_execution=True,
            )

            self.assertEqual(
                passed["status"],
                "PASSED",
            )

            from maestro_gateway.execution import (
                ExecutionStateError,
            )

            with self.assertRaises(
                ExecutionStateError
            ):
                executions.execute(
                    approved["id"],
                    confirm_execution=True,
                )

    def test_secret_environment_not_inherited(
        self,
    ):
        import os

        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = self.project(
                base,
                "import os\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_secret(self):\n"
                "        self.assertIsNone("
                "os.environ.get("
                "'MAESTRO_TEST_SECRET'"
                "))\n",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            approved = (
                self.propose_and_approve(
                    executions,
                    workspace["id"],
                )
            )

            previous = os.environ.get(
                "MAESTRO_TEST_SECRET"
            )

            os.environ[
                "MAESTRO_TEST_SECRET"
            ] = "DO_NOT_INHERIT"

            try:
                result = executions.execute(
                    approved["id"],
                    confirm_execution=True,
                )

            finally:
                if previous is None:
                    os.environ.pop(
                        "MAESTRO_TEST_SECRET",
                        None,
                    )
                else:
                    os.environ[
                        "MAESTRO_TEST_SECRET"
                    ] = previous

            self.assertEqual(
                result["status"],
                "PASSED",
            )

            self.assertEqual(
                result[
                    "environment_policy"
                ],
                "minimal_no_user_secrets",
            )

    def test_timeout_terminates_process_group(
        self,
    ):
        from unittest.mock import patch

        from maestro_gateway.execution import (
            PROFILE_TIMEOUT_MS,
        )

        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = self.project(
                base,
                "import time\n"
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_slow(self):\n"
                "        time.sleep(5)\n",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            with patch.dict(
                PROFILE_TIMEOUT_MS,
                {
                    "python_unittest": 150
                },
            ):
                approved = (
                    self.propose_and_approve(
                        executions,
                        workspace["id"],
                    )
                )

                result = (
                    executions.execute(
                        approved["id"],
                        confirm_execution=True,
                    )
                )

            self.assertEqual(
                result["status"],
                "TIMED_OUT",
            )

            self.assertTrue(
                result["timed_out"]
            )

            self.assertTrue(
                result[
                    "process_group_terminated"
                ]
            )

    def test_output_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                workspaces,
                executions,
            ) = self.make_store(td)

            root = self.project(
                base,
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_output(self):\n"
                "        print('X' * 100000)\n"
                "        self.assertTrue(True)\n",
            )

            workspace = (
                workspaces.authorize_path(
                    str(root)
                )
            )

            approved = (
                self.propose_and_approve(
                    executions,
                    workspace["id"],
                )
            )

            result = executions.execute(
                approved["id"],
                confirm_execution=True,
            )

            self.assertEqual(
                result["status"],
                "PASSED",
            )

            self.assertTrue(
                result[
                    "stdout_truncated"
                ]
            )

            self.assertGreater(
                result[
                    "stdout_bytes"
                ],
                len(
                    result["stdout"]
                ),
            )

    def test_execution_environment_preserves_symlink_launcher_directory(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                base,
                _workspaces,
                executions,
            ) = self.make_store(td)

            launcher_dir = (
                base / "runtime-bin"
            )

            real_dir = (
                base / "runtime-lib"
            )

            launcher_dir.mkdir()
            real_dir.mkdir()

            target = (
                real_dir / "npm-cli.js"
            )

            target.write_text(
                "#!/usr/bin/env node\n",
                encoding="utf-8",
            )

            launcher = (
                launcher_dir / "npm"
            )

            launcher.symlink_to(
                target
            )

            environment, _runtime = (
                executions
                ._execution_environment(
                    "RUN-ABCDEF123456",
                    str(launcher),
                )
            )

            entries = (
                environment["PATH"]
                .split(":")
            )

            self.assertEqual(
                entries[0],
                str(launcher_dir),
            )

            self.assertIn(
                str(real_dir.resolve()),
                entries,
            )


if __name__ == "__main__":
    unittest.main()
