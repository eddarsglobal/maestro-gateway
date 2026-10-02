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


if __name__ == "__main__":
    unittest.main()
