from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import maestro_gateway.execution as execution_module
from maestro_gateway.execution import (
    ExecutionProposalStore,
    ExecutionStateError,
)
from maestro_gateway.workspace import (
    WorkspaceRegistry,
)


class ExecutionStoreConcurrencyTests(
    unittest.TestCase
):
    def make_python_project(
        self,
        base: Path,
    ) -> Path:
        root = base / "project"
        root.mkdir()

        (
            root / "pyproject.toml"
        ).write_text(
            "[project]\n"
            'name = "maestro-lock-test"\n'
            'version = "0.0.0"\n',
            encoding="utf-8",
        )

        tests = root / "tests"
        tests.mkdir()

        (
            tests / "test_sample.py"
        ).write_text(
            "import unittest\n\n"
            "class SampleTests("
            "unittest.TestCase"
            "):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )

        return root

    @staticmethod
    def successful_result():
        empty_sha = hashlib.sha256(
            b""
        ).hexdigest()

        return {
            "exit_code": 0,
            "timed_out": False,
            "process_group_terminated": False,
            "duration_ms": 1,
            "stdout": "",
            "stderr": "",
            "stdout_sha256": empty_sha,
            "stderr_sha256": empty_sha,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "stdout_reader_error": None,
            "stderr_reader_error": None,
        }

    def test_get_and_list_remain_available_while_process_runs(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            root = self.make_python_project(
                base
            )

            workspaces = WorkspaceRegistry(
                base / "workspaces.json"
            )

            executions = (
                ExecutionProposalStore(
                    base / "executions",
                    workspaces,
                )
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
                profile="python_unittest",
            )

            executions.approve(
                proposal["id"]
            )

            runner_entered = (
                threading.Event()
            )

            release_runner = (
                threading.Event()
            )

            execution_done = (
                threading.Event()
            )

            reader_done = (
                threading.Event()
            )

            execution_result = {}
            reader_result = {}

            def blocking_runner(
                **kwargs,
            ):
                del kwargs

                runner_entered.set()

                if not release_runner.wait(
                    timeout=3.0
                ):
                    raise RuntimeError(
                        "test runner release "
                        "timed out"
                    )

                return (
                    self.successful_result()
                )

            def execution_worker():
                try:
                    execution_result[
                        "result"
                    ] = executions.execute(
                        proposal["id"],
                        confirm_execution=True,
                    )

                except BaseException as exc:
                    execution_result[
                        "error"
                    ] = exc

                finally:
                    execution_done.set()

            def reader_worker():
                try:
                    row = executions.get(
                        proposal["id"]
                    )

                    rows = executions.list()

                    reader_result[
                        "status"
                    ] = row["status"]

                    reader_result[
                        "listed"
                    ] = any(
                        item.get("id")
                        == proposal["id"]
                        for item in rows
                    )

                except BaseException as exc:
                    reader_result[
                        "error"
                    ] = exc

                finally:
                    reader_done.set()

            with mock.patch.object(
                execution_module,
                "_run_bounded_process",
                side_effect=blocking_runner,
            ):
                execution_thread = (
                    threading.Thread(
                        target=execution_worker,
                        daemon=True,
                    )
                )

                execution_thread.start()

                self.assertTrue(
                    runner_entered.wait(
                        timeout=2.0
                    ),
                    "execution never entered "
                    "the bounded runner",
                )

                reader_thread = (
                    threading.Thread(
                        target=reader_worker,
                        daemon=True,
                    )
                )

                reader_thread.start()

                # With the current bug, get()
                # blocks here because execute()
                # still owns the global store
                # RLock while the subprocess is
                # running.
                readers_available = (
                    reader_done.wait(
                        timeout=0.50
                    )
                )

                release_runner.set()

                self.assertTrue(
                    execution_done.wait(
                        timeout=3.0
                    ),
                    "execution thread did not "
                    "finish",
                )

                execution_thread.join(
                    timeout=1.0
                )

                reader_thread.join(
                    timeout=1.0
                )

            self.assertTrue(
                readers_available,
                "ExecutionProposalStore.get/list "
                "were blocked while the "
                "project process was running",
            )

            self.assertNotIn(
                "error",
                reader_result,
            )

            self.assertEqual(
                reader_result["status"],
                "EXECUTION_STARTED",
            )

            self.assertTrue(
                reader_result["listed"]
            )

            self.assertNotIn(
                "error",
                execution_result,
            )

            self.assertEqual(
                execution_result[
                    "result"
                ]["status"],
                "PASSED",
            )


    def test_active_attempt_rejects_replay_and_preserves_identity(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            root = self.make_python_project(
                base
            )

            workspaces = WorkspaceRegistry(
                base / "workspaces.json"
            )

            executions = (
                ExecutionProposalStore(
                    base / "executions",
                    workspaces,
                )
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
                profile="python_unittest",
            )

            executions.approve(
                proposal["id"]
            )

            runner_entered = (
                threading.Event()
            )

            release_runner = (
                threading.Event()
            )

            execution_done = (
                threading.Event()
            )

            execution_result = {}
            runner_calls = []

            def blocking_runner(
                **kwargs,
            ):
                runner_calls.append(
                    dict(kwargs)
                )

                runner_entered.set()

                if not release_runner.wait(
                    timeout=3.0
                ):
                    raise RuntimeError(
                        "test runner release "
                        "timed out"
                    )

                return (
                    self.successful_result()
                )

            def execution_worker():
                try:
                    execution_result[
                        "result"
                    ] = executions.execute(
                        proposal["id"],
                        confirm_execution=True,
                    )

                except BaseException as exc:
                    execution_result[
                        "error"
                    ] = exc

                finally:
                    execution_done.set()

            with mock.patch.object(
                execution_module,
                "_run_bounded_process",
                side_effect=blocking_runner,
            ):
                thread = threading.Thread(
                    target=execution_worker,
                    daemon=True,
                )

                thread.start()

                self.assertTrue(
                    runner_entered.wait(
                        timeout=2.0
                    ),
                    "execution never entered "
                    "the bounded runner",
                )

                active = executions.get(
                    proposal["id"]
                )

                self.assertEqual(
                    active["status"],
                    "EXECUTION_STARTED",
                )

                attempt_id = active.get(
                    "execution_attempt_id"
                )

                self.assertIsInstance(
                    attempt_id,
                    str,
                )

                self.assertTrue(
                    attempt_id.startswith(
                        "ATT-"
                    )
                )

                self.assertEqual(
                    len(attempt_id),
                    20,
                )

                self.assertEqual(
                    active[
                        "execution_attempts"
                    ],
                    1,
                )

                with self.assertRaises(
                    ExecutionStateError
                ):
                    executions.execute(
                        proposal["id"],
                        confirm_execution=True,
                    )

                self.assertEqual(
                    len(runner_calls),
                    1,
                    "replay started a second "
                    "project runner",
                )

                still_active = executions.get(
                    proposal["id"]
                )

                self.assertEqual(
                    still_active["status"],
                    "EXECUTION_STARTED",
                )

                self.assertEqual(
                    still_active[
                        "execution_attempt_id"
                    ],
                    attempt_id,
                )

                self.assertEqual(
                    still_active[
                        "execution_attempts"
                    ],
                    1,
                )

                release_runner.set()

                self.assertTrue(
                    execution_done.wait(
                        timeout=3.0
                    ),
                    "execution thread did not "
                    "finish",
                )

                thread.join(
                    timeout=1.0
                )

            self.assertNotIn(
                "error",
                execution_result,
            )

            result = execution_result[
                "result"
            ]

            self.assertEqual(
                result["status"],
                "PASSED",
            )

            self.assertEqual(
                result[
                    "execution_attempt_id"
                ],
                attempt_id,
            )

            self.assertEqual(
                result[
                    "execution_attempts"
                ],
                1,
            )

            persisted = executions.get(
                proposal["id"]
            )

            self.assertEqual(
                persisted[
                    "execution_attempt_id"
                ],
                attempt_id,
            )

            self.assertEqual(
                persisted["status"],
                "PASSED",
            )

            started_events = [
                row
                for row in persisted[
                    "history"
                ]
                if row.get("action")
                == "EXECUTION_STARTED"
            ]

            passed_events = [
                row
                for row in persisted[
                    "history"
                ]
                if row.get("action")
                == "PASSED"
            ]

            self.assertEqual(
                len(started_events),
                1,
            )

            self.assertEqual(
                len(passed_events),
                1,
            )


if __name__ == "__main__":
    unittest.main()
