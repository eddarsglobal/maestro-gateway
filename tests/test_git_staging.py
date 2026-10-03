import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maestro_gateway.git_staging import (
    GitStageApplyError,
    GitStageConflictError,
    GitStageProposalStore,
    GitStageStateError,
)
from maestro_gateway.workspace import (
    WorkspaceRegistry,
)


GIT = shutil.which("git")


@unittest.skipUnless(
    GIT,
    "git executable required",
)
class GitStageProposalTests(
    unittest.TestCase
):
    def git(
        self,
        root: Path,
        *args: str,
        check: bool = True,
    ):
        result = subprocess.run(
            [
                GIT,
                *args,
            ],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
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

        if (
            check
            and result.returncode
            != 0
        ):
            raise AssertionError(
                result.stderr.decode(
                    "utf-8",
                    errors="replace",
                )
            )

        return result

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
            (
                "user.email="
                "maestro@example.invalid"
            ),
            "commit",
            "--no-gpg-sign",
            "-q",
            "-m",
            "initial",
        )

        return root

    def store_for(
        self,
        base: Path,
        root: Path,
    ):
        registry = WorkspaceRegistry(
            base / "registry.json"
        )

        workspace = (
            registry.authorize_path(
                str(root)
            )
        )

        store = (
            GitStageProposalStore(
                base
                / "stage-proposals",
                registry,
            )
        )

        return (
            store,
            workspace,
        )

    def index_sha(
        self,
        root: Path,
    ) -> str:
        return hashlib.sha256(
            (
                root
                / ".git"
                / "index"
            ).read_bytes()
        ).hexdigest()

    def staged_names(
        self,
        root: Path,
    ) -> str:
        result = self.git(
            root,
            "diff",
            "--cached",
            "--name-only",
        )

        return result.stdout.decode(
            "utf-8"
        )

    def test_proposal_does_not_stage(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            before = self.index_sha(
                root
            )

            proposal = (
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    paths=[
                        "tracked.txt"
                    ],
                )
            )

            after = self.index_sha(
                root
            )

            self.assertEqual(
                proposal["status"],
                "AWAITING_APPROVAL",
            )

            self.assertFalse(
                proposal[
                    "stage_performed"
                ]
            )

            self.assertEqual(
                before,
                after,
            )

            self.assertEqual(
                self.staged_names(
                    root
                ),
                "",
            )

    def test_approval_is_state_only(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = (
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    paths=[
                        "tracked.txt"
                    ],
                )
            )

            before = self.index_sha(
                root
            )

            approved = (
                store.approve(
                    proposal["id"]
                )
            )

            after = self.index_sha(
                root
            )

            self.assertEqual(
                approved["status"],
                "APPROVED",
            )

            self.assertFalse(
                approved[
                    "stage_performed"
                ]
            )

            self.assertEqual(
                before,
                after,
            )

    def test_worktree_change_makes_approval_stale(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            path = (
                root / "tracked.txt"
            )

            path.write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            path.write_text(
                "three\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                GitStageConflictError
            ):
                store.approve(
                    proposal["id"]
                )

            stale = store.get(
                proposal["id"]
            )

            self.assertEqual(
                stale["status"],
                "STALE",
            )

    def test_index_change_makes_approval_stale(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            (
                root / "other.txt"
            ).write_text(
                "other\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            self.git(
                root,
                "add",
                "--",
                "other.txt",
            )

            with self.assertRaises(
                GitStageConflictError
            ):
                store.approve(
                    proposal["id"]
                )

            self.assertEqual(
                store.get(
                    proposal["id"]
                )["status"],
                "STALE",
            )

    def test_untracked_file_can_be_proposed(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "new.txt"
            ).write_text(
                "new\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=["new.txt"],
            )

            target = (
                proposal["snapshot"][
                    "targets"
                ][0]
            )

            self.assertEqual(
                target[
                    "stage_kind"
                ],
                "untracked",
            )

    def test_deleted_tracked_file_can_be_proposed(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).unlink()

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            target = (
                proposal["snapshot"][
                    "targets"
                ][0]
            )

            self.assertEqual(
                target[
                    "stage_kind"
                ],
                "deleted",
            )

            self.assertFalse(
                target[
                    "worktree"
                ]["exists"]
            )

    def test_unchanged_path_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            with self.assertRaises(
                ValueError
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    paths=[
                        "tracked.txt"
                    ],
                )

    def test_staged_only_path_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            self.git(
                root,
                "add",
                "--",
                "tracked.txt",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            with self.assertRaises(
                ValueError
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    paths=[
                        "tracked.txt"
                    ],
                )

    def test_repository_wide_paths_are_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            for paths in (
                [],
                ["."],
                ["../outside.txt"],
                [str(root / "tracked.txt")],
            ):
                with self.assertRaises(
                    (
                        ValueError,
                        PermissionError,
                    )
                ):
                    store.propose(
                        workspace_id=(
                            workspace[
                                "id"
                            ]
                        ),
                        paths=paths,
                    )

    def test_sensitive_path_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / ".env"
            ).write_text(
                "SECRET=1\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            with self.assertRaises(
                PermissionError
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    paths=[".env"],
                )

    def test_symlink_path_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            target = (
                root / "target.txt"
            )

            target.write_text(
                "target\n",
                encoding="utf-8",
            )

            (
                root / "link.txt"
            ).symlink_to(
                target
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            with self.assertRaises(
                PermissionError
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    paths=["link.txt"],
                )

    def test_clean_filter_is_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root
                / ".gitattributes"
            ).write_text(
                (
                    "tracked.txt "
                    "filter=evil\n"
                ),
                encoding="utf-8",
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            with self.assertRaises(
                PermissionError
            ):
                store.propose(
                    workspace_id=(
                        workspace["id"]
                    ),
                    paths=[
                        "tracked.txt"
                    ],
                )

    def test_reject_is_state_only(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            before = self.index_sha(
                root
            )

            rejected = (
                store.reject(
                    proposal["id"],
                    reason="no",
                )
            )

            self.assertEqual(
                rejected["status"],
                "REJECTED",
            )

            self.assertEqual(
                before,
                self.index_sha(
                    root
                ),
            )

    def test_apply_requires_approval(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            before = self.index_sha(
                root
            )

            with self.assertRaises(
                GitStageStateError
            ):
                store.apply(
                    proposal["id"],
                    confirm_stage=True,
                )

            self.assertEqual(
                self.index_sha(root),
                before,
            )

    def test_apply_requires_literal_confirmation(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            store.approve(
                proposal["id"]
            )

            before = self.index_sha(
                root
            )

            with self.assertRaises(
                GitStageStateError
            ):
                store.apply(
                    proposal["id"]
                )

            self.assertEqual(
                self.index_sha(root),
                before,
            )

    def test_approved_apply_stages_modified_file(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            store.approve(
                proposal["id"]
            )

            before = self.index_sha(
                root
            )

            applied = store.apply(
                proposal["id"],
                confirm_stage=True,
            )

            self.assertEqual(
                applied["status"],
                "APPLIED",
            )

            self.assertTrue(
                applied[
                    "stage_performed"
                ]
            )

            self.assertNotEqual(
                self.index_sha(root),
                before,
            )

            self.assertEqual(
                self.staged_names(root),
                "tracked.txt\n",
            )

            self.assertEqual(
                applied[
                    "apply_changed_paths"
                ],
                ["tracked.txt"],
            )

    def test_apply_stages_untracked_file(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "new.txt"
            ).write_text(
                "new\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=["new.txt"],
            )

            store.approve(
                proposal["id"]
            )

            store.apply(
                proposal["id"],
                confirm_stage=True,
            )

            self.assertEqual(
                self.staged_names(root),
                "new.txt\n",
            )

    def test_apply_stages_deleted_file(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).unlink()

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            store.approve(
                proposal["id"]
            )

            store.apply(
                proposal["id"],
                confirm_stage=True,
            )

            result = self.git(
                root,
                "diff",
                "--cached",
                "--name-status",
            )

            self.assertEqual(
                result.stdout.decode(
                    "utf-8"
                ),
                "D\ttracked.txt\n",
            )

    def test_apply_is_anti_replay(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            store.approve(
                proposal["id"]
            )

            store.apply(
                proposal["id"],
                confirm_stage=True,
            )

            after = self.index_sha(
                root
            )

            with self.assertRaises(
                GitStageStateError
            ):
                store.apply(
                    proposal["id"],
                    confirm_stage=True,
                )

            self.assertEqual(
                self.index_sha(root),
                after,
            )

    def test_change_after_approval_blocks_apply(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            path = (
                root / "tracked.txt"
            )

            path.write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            store.approve(
                proposal["id"]
            )

            before = self.index_sha(
                root
            )

            path.write_text(
                "three\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                GitStageConflictError
            ):
                store.apply(
                    proposal["id"],
                    confirm_stage=True,
                )

            self.assertEqual(
                self.index_sha(root),
                before,
            )

            self.assertEqual(
                store.get(
                    proposal["id"]
                )["status"],
                "STALE",
            )

    def test_change_during_candidate_preparation_blocks_apply(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            path = (
                root / "tracked.txt"
            )

            path.write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            store.approve(
                proposal["id"]
            )

            before = self.index_sha(
                root
            )

            original_changed = (
                store._changed_index_paths
            )

            def mutate_after_candidate(
                before_inventory,
                after_inventory,
            ):
                # Candidate already represents
                # "two". Change the worktree
                # before the final governed
                # snapshot.
                path.write_text(
                    "three\n",
                    encoding="utf-8",
                )

                return original_changed(
                    before_inventory,
                    after_inventory,
                )

            with mock.patch.object(
                store,
                "_changed_index_paths",
                side_effect=(
                    mutate_after_candidate
                ),
            ):
                with self.assertRaises(
                    GitStageConflictError
                ):
                    store.apply(
                        proposal["id"],
                        confirm_stage=True,
                    )

            self.assertEqual(
                self.index_sha(root),
                before,
            )

            self.assertEqual(
                self.staged_names(root),
                "",
            )

            row = store.get(
                proposal["id"]
            )

            self.assertEqual(
                row["status"],
                "STALE",
            )

            self.assertFalse(
                row["stage_performed"]
            )

    def test_apply_preserves_unrelated_staged_entry(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "other.txt"
            ).write_text(
                "other\n",
                encoding="utf-8",
            )

            self.git(
                root,
                "add",
                "--",
                "other.txt",
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            store.approve(
                proposal["id"]
            )

            store.apply(
                proposal["id"],
                confirm_stage=True,
            )

            names = set(
                self.staged_names(
                    root
                ).splitlines()
            )

            self.assertEqual(
                names,
                {
                    "other.txt",
                    "tracked.txt",
                },
            )

    def test_verification_failure_rolls_back_exact_index(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            store.approve(
                proposal["id"]
            )

            before = self.index_sha(
                root
            )

            with mock.patch.object(
                store,
                "_verify_committed_index",
                side_effect=RuntimeError(
                    "forced verification failure"
                ),
            ):
                with self.assertRaises(
                    GitStageApplyError
                ):
                    store.apply(
                        proposal["id"],
                        confirm_stage=True,
                    )

            self.assertEqual(
                self.index_sha(root),
                before,
            )

            row = store.get(
                proposal["id"]
            )

            self.assertEqual(
                row["status"],
                "APPLY_FAILED_ROLLED_BACK",
            )

            self.assertFalse(
                row[
                    "stage_performed"
                ]
            )

    def test_recovery_finalizes_committed_apply(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self.make_repo(
                base
            )

            (
                root / "tracked.txt"
            ).write_text(
                "two\n",
                encoding="utf-8",
            )

            store, workspace = (
                self.store_for(
                    base,
                    root,
                )
            )

            proposal = store.propose(
                workspace_id=(
                    workspace["id"]
                ),
                paths=[
                    "tracked.txt"
                ],
            )

            store.approve(
                proposal["id"]
            )

            applied = store.apply(
                proposal["id"],
                confirm_stage=True,
            )

            payload = store._load(
                proposal["id"]
            )

            payload[
                "status"
            ] = "APPLYING"

            payload[
                "stage_performed"
            ] = False

            payload.pop(
                "applied_at",
                None,
            )

            store._save(
                payload
            )

            recovered_store = (
                GitStageProposalStore(
                    store.root,
                    store.workspaces,
                )
            )

            recovered = (
                recovered_store.get(
                    proposal["id"]
                )
            )

            self.assertEqual(
                recovered["status"],
                "APPLIED",
            )

            self.assertTrue(
                recovered[
                    "stage_performed"
                ]
            )

            self.assertTrue(
                recovered[
                    "recovered_after_restart"
                ]
            )

            self.assertEqual(
                recovered[
                    "apply_after_index_sha256"
                ],
                applied[
                    "apply_after_index_sha256"
                ],
            )


if __name__ == "__main__":
    unittest.main()
