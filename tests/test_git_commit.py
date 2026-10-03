from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from maestro_gateway.git_commit import (
    GIT_COMMIT_MODE,
    GIT_COMMIT_PROTOCOL,
    GitCommitConflictError,
    GitCommitProposalStore,
    GitCommitStateError,
)
from maestro_gateway.workspace import (
    WorkspaceRegistry,
)


class GitCommitProposalTests(
    unittest.TestCase
):
    def git(
        self,
        repo: Path,
        *args: str,
        input_text: str | None = None,
        check: bool = True,
    ) -> str:
        completed = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(repo),
                *args,
            ],
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )

        if (
            check
            and completed.returncode != 0
        ):
            self.fail(
                "git command failed: "
                + " ".join(args)
                + "\nstdout:\n"
                + completed.stdout
                + "\nstderr:\n"
                + completed.stderr
            )

        return completed.stdout.strip()

    @staticmethod
    def sha256_file(
        path: Path,
    ) -> str:
        return hashlib.sha256(
            path.read_bytes()
        ).hexdigest()

    def make_store(
        self,
        td: str,
        *,
        stage_change: bool = True,
    ):
        root = Path(td)
        repo = root / "repo"
        repo.mkdir()

        self.git(
            repo,
            "init",
            "-q",
        )

        self.git(
            repo,
            "config",
            "user.name",
            "MAESTRO Test",
        )

        self.git(
            repo,
            "config",
            "user.email",
            "maestro@example.invalid",
        )

        target = repo / "tracked.txt"

        target.write_text(
            "v1\n",
            encoding="utf-8",
        )

        self.git(
            repo,
            "add",
            "--",
            "tracked.txt",
        )

        self.git(
            repo,
            "commit",
            "-q",
            "-m",
            "baseline",
        )

        if stage_change:
            target.write_text(
                "v2-staged\n",
                encoding="utf-8",
            )

            self.git(
                repo,
                "add",
                "--",
                "tracked.txt",
            )

        workspaces = WorkspaceRegistry(
            root / "workspaces.json"
        )

        workspace = (
            workspaces.authorize_path(
                str(repo)
            )
        )

        store = GitCommitProposalStore(
            root / "git-commits",
            workspaces,
        )

        return (
            root,
            repo,
            target,
            workspaces,
            workspace,
            store,
        )

    def test_protocol_and_mode(
        self,
    ):
        self.assertEqual(
            GIT_COMMIT_PROTOCOL,
            "P0.6E.3A",
        )

        self.assertEqual(
            GIT_COMMIT_MODE,
            "proposal_approval_only",
        )

    def test_proposal_is_state_only(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                repo,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            head_before = self.git(
                repo,
                "rev-parse",
                "HEAD",
            )

            index_before = self.sha256_file(
                repo / ".git" / "index"
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message=(
                    "subject\r\n\r\nbody"
                ),
            )

            self.assertEqual(
                proposal["status"],
                "AWAITING_APPROVAL",
            )

            self.assertEqual(
                proposal["operation"],
                "git.commit",
            )

            self.assertEqual(
                proposal["message"],
                "subject\n\nbody",
            )

            self.assertFalse(
                proposal[
                    "commit_performed"
                ]
            )

            self.assertEqual(
                proposal["staged_paths"],
                ["tracked.txt"],
            )

            self.assertEqual(
                self.git(
                    repo,
                    "rev-parse",
                    "HEAD",
                ),
                head_before,
            )

            self.assertEqual(
                self.sha256_file(
                    repo
                    / ".git"
                    / "index"
                ),
                index_before,
            )

    def test_approval_is_state_only(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                repo,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message="approved message",
            )

            head_before = self.git(
                repo,
                "rev-parse",
                "HEAD",
            )

            index_before = self.sha256_file(
                repo / ".git" / "index"
            )

            approved = store.approve(
                proposal["id"]
            )

            self.assertEqual(
                approved["status"],
                "APPROVED",
            )

            self.assertEqual(
                approved[
                    "approval_source"
                ],
                "local_user_session",
            )

            self.assertFalse(
                approved[
                    "commit_performed"
                ]
            )

            self.assertEqual(
                self.git(
                    repo,
                    "rev-parse",
                    "HEAD",
                ),
                head_before,
            )

            self.assertEqual(
                self.sha256_file(
                    repo
                    / ".git"
                    / "index"
                ),
                index_before,
            )

    def test_approval_is_anti_replay(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                _,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message="one approval",
            )

            store.approve(
                proposal["id"]
            )

            with self.assertRaises(
                GitCommitStateError
            ):
                store.approve(
                    proposal["id"]
                )

    def test_reject_and_replay(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                _,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message="reject me",
            )

            rejected = store.reject(
                proposal["id"],
                reason="not ready",
            )

            self.assertEqual(
                rejected["status"],
                "REJECTED",
            )

            self.assertEqual(
                rejected[
                    "rejection_reason"
                ],
                "not ready",
            )

            with self.assertRaises(
                GitCommitStateError
            ):
                store.reject(
                    proposal["id"]
                )

            with self.assertRaises(
                GitCommitStateError
            ):
                store.approve(
                    proposal["id"]
                )

    def test_worktree_only_change_does_not_stale(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                _,
                target,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message="index only",
            )

            target.write_text(
                "worktree-only\n",
                encoding="utf-8",
            )

            approved = store.approve(
                proposal["id"]
            )

            self.assertEqual(
                approved["status"],
                "APPROVED",
            )

    def test_index_change_makes_approval_stale(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                repo,
                target,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message="index binding",
            )

            target.write_text(
                "v3-restaged\n",
                encoding="utf-8",
            )

            self.git(
                repo,
                "add",
                "--",
                "tracked.txt",
            )

            with self.assertRaises(
                GitCommitConflictError
            ) as ctx:
                store.approve(
                    proposal["id"]
                )

            self.assertEqual(
                ctx.exception.proposal[
                    "status"
                ],
                "STALE",
            )

    def test_head_commit_change_makes_stale(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                repo,
                target,
                _,
                workspace,
                store,
            ) = self.make_store(
                td,
                stage_change=False,
            )

            baseline = self.git(
                repo,
                "rev-parse",
                "HEAD",
            )

            self.git(
                repo,
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                "future",
            )

            future = self.git(
                repo,
                "rev-parse",
                "HEAD",
            )

            self.git(
                repo,
                "reset",
                "--hard",
                "-q",
                baseline,
            )

            target.write_text(
                "v2-staged\n",
                encoding="utf-8",
            )

            self.git(
                repo,
                "add",
                "--",
                "tracked.txt",
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message="head binding",
            )

            head_ref = self.git(
                repo,
                "symbolic-ref",
                "HEAD",
            )

            self.git(
                repo,
                "update-ref",
                head_ref,
                future,
                baseline,
            )

            with self.assertRaises(
                GitCommitConflictError
            ) as ctx:
                store.approve(
                    proposal["id"]
                )

            self.assertEqual(
                ctx.exception.proposal[
                    "status"
                ],
                "STALE",
            )

    def test_branch_change_makes_stale(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                repo,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            self.git(
                repo,
                "branch",
                "other",
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message="branch binding",
            )

            (
                repo
                / ".git"
                / "HEAD"
            ).write_text(
                "ref: refs/heads/other\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                GitCommitConflictError
            ) as ctx:
                store.approve(
                    proposal["id"]
                )

            self.assertEqual(
                ctx.exception.proposal[
                    "status"
                ],
                "STALE",
            )

    def test_empty_staged_set_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                _,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(
                td,
                stage_change=False,
            )

            with self.assertRaises(
                ValueError
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    message="nothing staged",
                )

    def test_sensitive_staged_path_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                repo,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            (
                repo / ".env"
            ).write_text(
                "SECRET=value\n",
                encoding="utf-8",
            )

            self.git(
                repo,
                "add",
                "--",
                ".env",
            )

            with self.assertRaises(
                PermissionError
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    message="secret",
                )

    def test_detached_head_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                repo,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            head = self.git(
                repo,
                "rev-parse",
                "HEAD",
            )

            (
                repo
                / ".git"
                / "HEAD"
            ).write_text(
                head + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "Detached HEAD",
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    message="detached",
                )

    def test_git_operation_in_progress_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                repo,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            (
                repo
                / ".git"
                / "MERGE_HEAD"
            ).write_text(
                "0" * 40 + "\n",
                encoding="ascii",
            )

            with self.assertRaisesRegex(
                ValueError,
                "operation in progress",
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    message="merge state",
                )

    def test_unmerged_index_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                repo,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(
                td,
                stage_change=False,
            )

            base = self.git(
                repo,
                "rev-parse",
                "HEAD:tracked.txt",
            )

            ours = self.git(
                repo,
                "hash-object",
                "-w",
                "--stdin",
                input_text="ours\n",
            )

            theirs = self.git(
                repo,
                "hash-object",
                "-w",
                "--stdin",
                input_text="theirs\n",
            )

            zeros = "0" * 40

            index_info = (
                f"0 {zeros}\ttracked.txt\n"
                f"100644 {base} 1\ttracked.txt\n"
                f"100644 {ours} 2\ttracked.txt\n"
                f"100644 {theirs} 3\ttracked.txt\n"
            )

            self.git(
                repo,
                "update-index",
                "--index-info",
                input_text=index_info,
            )

            with self.assertRaisesRegex(
                ValueError,
                "Unmerged Git index",
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    message="conflicted",
                )

    def test_invalid_messages_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                _,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            for value in (
                "",
                "   ",
                "\x00",
                None,
                123,
            ):
                with self.subTest(
                    value=value
                ):
                    with self.assertRaises(
                        ValueError
                    ):
                        store.propose(
                            workspace_id=(
                                workspace["id"]
                            ),
                            message=value,
                        )

    def test_file_workspace_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            file_path = (
                root / "single.txt"
            )

            file_path.write_text(
                "x\n",
                encoding="utf-8",
            )

            workspaces = (
                WorkspaceRegistry(
                    root
                    / "workspaces.json"
                )
            )

            workspace = (
                workspaces.authorize_path(
                    str(file_path)
                )
            )

            store = (
                GitCommitProposalStore(
                    root / "git-commits",
                    workspaces,
                )
            )

            with self.assertRaises(
                ValueError
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    message="no directory",
                )

    def test_get_and_list(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                _,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message="persisted",
            )

            loaded = store.get(
                proposal["id"]
            )

            self.assertEqual(
                loaded["id"],
                proposal["id"],
            )

            rows = store.list(
                workspace_id=(
                    workspace["id"]
                )
            )

            self.assertEqual(
                [
                    row["id"]
                    for row in rows
                ],
                [proposal["id"]],
            )

            self.assertEqual(
                store.list(
                    workspace_id=(
                        "WS-FFFFFFFFFFFF"
                    )
                ),
                [],
            )

    def test_message_binding_detects_tamper(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            (
                _,
                _,
                _,
                _,
                workspace,
                store,
            ) = self.make_store(td)

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                message="original",
            )

            ledger = (
                store.root
                / (
                    proposal["id"]
                    + ".json"
                )
            )

            payload = json.loads(
                ledger.read_text(
                    encoding="utf-8"
                )
            )

            payload["message"] = (
                "tampered"
            )

            ledger.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                GitCommitStateError,
                "integrity check failed",
            ):
                store.approve(
                    proposal["id"]
                )


if __name__ == "__main__":
    unittest.main()
