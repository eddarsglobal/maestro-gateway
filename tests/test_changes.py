import tempfile
import unittest
from unittest import mock
from pathlib import Path

from maestro_gateway.changes import (
    ChangeProposalStore,
    ProposalApplyError,
    ProposalConflictError,
    ProposalStateError,
)
from maestro_gateway.workspace import WorkspaceRegistry


class ChangeProposalTests(unittest.TestCase):
    def make_store(self, td: str):
        root = Path(td) / "project"
        root.mkdir()
        workspaces = WorkspaceRegistry(Path(td) / "workspaces.json")
        row = workspaces.authorize_path(str(root))
        changes = ChangeProposalStore(Path(td) / "changes", workspaces)
        return root, row, changes

    def test_edit_proposal_does_not_write_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)
            target = root / "hello.txt"
            target.write_text("before\n", encoding="utf-8")

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="after\n",
                operation="edit",
            )

            self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(proposal["status"], "AWAITING_APPROVAL")
            self.assertFalse(proposal["workspace_write_performed"])
            self.assertIn("-before", proposal["diff"])
            self.assertIn("+after", proposal["diff"])

    def test_create_proposal_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)
            target = root / "new.txt"

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="new.txt",
                content="new content\n",
                operation="write",
            )

            self.assertFalse(target.exists())
            self.assertEqual(proposal["effect"], "create")
            self.assertIsNone(proposal["before_sha256"])

    def test_approval_is_state_only_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)
            target = root / "hello.txt"
            target.write_text("before\n", encoding="utf-8")

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="after\n",
                operation="patch",
            )
            approved = changes.approve(proposal["id"])

            self.assertEqual(approved["status"], "APPROVED")
            self.assertFalse(approved["workspace_write_performed"])
            self.assertEqual(target.read_text(encoding="utf-8"), "before\n")

    def test_stale_proposal_is_rejected_at_approval(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)
            target = root / "hello.txt"
            target.write_text("v1\n", encoding="utf-8")

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="v2\n",
                operation="edit",
            )
            target.write_text("external-change\n", encoding="utf-8")

            with self.assertRaises(ProposalConflictError):
                changes.approve(proposal["id"])

            self.assertEqual(
                changes.get(proposal["id"])["status"],
                "STALE",
            )

    def test_rejected_proposal_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)
            target = root / "hello.txt"
            target.write_text("v1\n", encoding="utf-8")

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="v2\n",
                operation="edit",
            )
            changes.reject(proposal["id"], reason="not now")

            with self.assertRaises(ProposalStateError):
                changes.approve(proposal["id"])

    def test_escape_and_sensitive_targets_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("x\n", encoding="utf-8")

            with self.assertRaises(PermissionError):
                changes.propose(
                    workspace_id=row["id"],
                    relative="../outside.txt",
                    content="x\n",
                    operation="write",
                )

            with self.assertRaises(PermissionError):
                changes.propose(
                    workspace_id=row["id"],
                    relative=".git/config",
                    content="x\n",
                    operation="edit",
                )

            with self.assertRaises(PermissionError):
                changes.propose(
                    workspace_id=row["id"],
                    relative=".env",
                    content="SECRET=2\n",
                    operation="write",
                )

    def test_edit_requires_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            _, row, changes = self.make_store(td)

            with self.assertRaises(FileNotFoundError):
                changes.propose(
                    workspace_id=row["id"],
                    relative="missing.txt",
                    content="x\n",
                    operation="edit",
                )


    def test_apply_requires_explicit_approval(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "hello.txt"
            target.write_text(
                "before\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="after\n",
                operation="edit",
            )

            with self.assertRaises(
                ProposalStateError
            ):
                changes.apply(
                    proposal["id"]
                )

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "before\n",
            )

    def test_approved_edit_is_atomically_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "hello.txt"
            target.write_text(
                "before\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="after\n",
                operation="edit",
            )

            approved = changes.approve(
                proposal["id"]
            )

            self.assertEqual(
                approved["status"],
                "APPROVED",
            )

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "before\n",
            )

            applied = changes.apply(
                proposal["id"]
            )

            self.assertEqual(
                applied["status"],
                "APPLIED",
            )

            self.assertTrue(
                applied["workspace_write_performed"]
            )

            self.assertEqual(
                applied["applied_sha256"],
                applied["after_sha256"],
            )

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "after\n",
            )

    def test_approved_create_is_atomically_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "created.txt"

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="created.txt",
                content="created\n",
                operation="write",
            )

            changes.approve(
                proposal["id"]
            )

            self.assertFalse(
                target.exists()
            )

            applied = changes.apply(
                proposal["id"]
            )

            self.assertEqual(
                applied["status"],
                "APPLIED",
            )

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "created\n",
            )

    def test_apply_rechecks_sha_after_approval(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "hello.txt"
            target.write_text(
                "v1\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="v2\n",
                operation="edit",
            )

            changes.approve(
                proposal["id"]
            )

            target.write_text(
                "external-change\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                ProposalConflictError
            ):
                changes.apply(
                    proposal["id"]
                )

            self.assertEqual(
                changes.get(
                    proposal["id"]
                )["status"],
                "STALE",
            )

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "external-change\n",
            )

    def test_applied_proposal_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "hello.txt"
            target.write_text(
                "before\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="after\n",
                operation="edit",
            )

            changes.approve(
                proposal["id"]
            )

            changes.apply(
                proposal["id"]
            )

            with self.assertRaises(
                ProposalStateError
            ):
                changes.apply(
                    proposal["id"]
                )

    def test_rejected_proposal_cannot_be_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "hello.txt"
            target.write_text(
                "before\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="after\n",
                operation="edit",
            )

            changes.reject(
                proposal["id"],
                reason="no",
            )

            with self.assertRaises(
                ProposalStateError
            ):
                changes.apply(
                    proposal["id"]
                )

    def test_verification_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "hello.txt"
            target.write_text(
                "before\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="hello.txt",
                content="after\n",
                operation="edit",
            )

            changes.approve(
                proposal["id"]
            )

            with mock.patch.object(
                changes,
                "_verify_applied_target",
                side_effect=OSError(
                    "forced verification failure"
                ),
            ):
                with self.assertRaises(
                    ProposalApplyError
                ):
                    changes.apply(
                        proposal["id"]
                    )

            final = changes.get(
                proposal["id"]
            )

            self.assertEqual(
                final["status"],
                "ROLLED_BACK",
            )

            self.assertTrue(
                final["workspace_write_performed"]
            )

            self.assertTrue(
                final["workspace_state_restored"]
            )

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "before\n",
            )


    def test_rename_proposal_and_approval_do_not_move(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            source = root / "old.txt"
            destination = root / "new.txt"

            source.write_text(
                "rename me\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="old.txt",
                destination="new.txt",
                operation="rename",
            )

            self.assertEqual(
                proposal["protocol"],
                "P0.6C.1",
            )

            self.assertEqual(
                proposal["status"],
                "AWAITING_APPROVAL",
            )

            self.assertTrue(source.exists())
            self.assertFalse(destination.exists())

            approved = changes.approve(
                proposal["id"]
            )

            self.assertEqual(
                approved["status"],
                "APPROVED",
            )

            self.assertTrue(source.exists())
            self.assertFalse(destination.exists())

    def test_approved_rename_is_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            source = root / "old.txt"
            destination = root / "new.txt"

            source.write_text(
                "rename me\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="old.txt",
                destination="new.txt",
                operation="rename",
            )

            changes.approve(
                proposal["id"]
            )

            applied = changes.apply(
                proposal["id"]
            )

            self.assertEqual(
                applied["status"],
                "APPLIED",
            )

            self.assertFalse(source.exists())
            self.assertTrue(destination.exists())

            self.assertEqual(
                destination.read_text(
                    encoding="utf-8"
                ),
                "rename me\n",
            )

            self.assertEqual(
                applied["applied_sha256"],
                applied["after_sha256"],
            )

            self.assertEqual(
                applied["applied_path"],
                "new.txt",
            )

    def test_approved_move_is_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            (root / "archive").mkdir()

            source = root / "move.txt"
            destination = (
                root / "archive" / "move.txt"
            )

            source.write_text(
                "move me\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="move.txt",
                destination="archive/move.txt",
                operation="move",
            )

            changes.approve(
                proposal["id"]
            )

            applied = changes.apply(
                proposal["id"]
            )

            self.assertEqual(
                applied["status"],
                "APPLIED",
            )

            self.assertFalse(source.exists())
            self.assertEqual(
                destination.read_text(
                    encoding="utf-8"
                ),
                "move me\n",
            )

    def test_rename_must_remain_in_same_parent(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            (root / "archive").mkdir()

            source = root / "old.txt"
            source.write_text(
                "x\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                changes.propose(
                    workspace_id=row["id"],
                    relative="old.txt",
                    destination="archive/new.txt",
                    operation="rename",
                )

    def test_relocation_destination_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            source = root / "old.txt"
            destination = root / "new.txt"

            source.write_text(
                "source\n",
                encoding="utf-8",
            )

            destination.write_text(
                "existing\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                FileExistsError
            ):
                changes.propose(
                    workspace_id=row["id"],
                    relative="old.txt",
                    destination="new.txt",
                    operation="rename",
                )

            self.assertEqual(
                destination.read_text(
                    encoding="utf-8"
                ),
                "existing\n",
            )

    def test_destination_created_after_approval_makes_move_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            source = root / "old.txt"
            destination = root / "new.txt"

            source.write_text(
                "source\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="old.txt",
                destination="new.txt",
                operation="rename",
            )

            changes.approve(
                proposal["id"]
            )

            destination.write_text(
                "external\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                ProposalConflictError
            ):
                changes.apply(
                    proposal["id"]
                )

            self.assertEqual(
                changes.get(
                    proposal["id"]
                )["status"],
                "STALE",
            )

            self.assertTrue(source.exists())

            self.assertEqual(
                destination.read_text(
                    encoding="utf-8"
                ),
                "external\n",
            )

    def test_source_changed_after_approval_makes_move_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            source = root / "old.txt"

            source.write_text(
                "v1\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="old.txt",
                destination="new.txt",
                operation="rename",
            )

            changes.approve(
                proposal["id"]
            )

            source.write_text(
                "external-change\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                ProposalConflictError
            ):
                changes.apply(
                    proposal["id"]
                )

            self.assertEqual(
                changes.get(
                    proposal["id"]
                )["status"],
                "STALE",
            )

            self.assertFalse(
                (root / "new.txt").exists()
            )

    def test_relocation_verification_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            source = root / "old.txt"
            destination = root / "new.txt"

            source.write_text(
                "before\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="old.txt",
                destination="new.txt",
                operation="rename",
            )

            changes.approve(
                proposal["id"]
            )

            with mock.patch.object(
                changes,
                "_verify_relocation_target",
                side_effect=OSError(
                    "forced relocation verification failure"
                ),
            ):
                with self.assertRaises(
                    ProposalApplyError
                ):
                    changes.apply(
                        proposal["id"]
                    )

            final = changes.get(
                proposal["id"]
            )

            self.assertEqual(
                final["status"],
                "ROLLED_BACK",
            )

            self.assertTrue(
                final["workspace_state_restored"]
            )

            self.assertTrue(source.exists())
            self.assertFalse(destination.exists())

            self.assertEqual(
                source.read_text(
                    encoding="utf-8"
                ),
                "before\n",
            )


    def test_delete_requires_high_risk_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "delete.txt"
            target.write_text(
                "keep until explicitly approved\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="delete.txt",
                operation="delete",
            )

            with self.assertRaises(
                ProposalStateError
            ):
                changes.approve(
                    proposal["id"]
                )

            self.assertTrue(target.exists())

            self.assertEqual(
                changes.get(
                    proposal["id"]
                )["status"],
                "AWAITING_APPROVAL",
            )

    def test_delete_proposal_and_approval_do_not_delete(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "delete.txt"
            target.write_text(
                "delete later\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="delete.txt",
                operation="delete",
            )

            self.assertEqual(
                proposal["protocol"],
                "P0.6C.2",
            )

            self.assertEqual(
                proposal["status"],
                "AWAITING_APPROVAL",
            )

            self.assertEqual(
                proposal["risk_level"],
                "high",
            )

            self.assertTrue(target.exists())

            approved = changes.approve(
                proposal["id"],
                confirm_delete=True,
            )

            self.assertEqual(
                approved["status"],
                "APPROVED",
            )

            self.assertFalse(
                approved["workspace_write_performed"]
            )

            self.assertTrue(target.exists())

    def test_approved_delete_is_recoverably_applied(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "delete.txt"
            target.write_bytes(
                b"recoverable-delete\n"
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="delete.txt",
                operation="delete",
            )

            changes.approve(
                proposal["id"],
                confirm_delete=True,
            )

            applied = changes.apply(
                proposal["id"]
            )

            self.assertEqual(
                applied["status"],
                "APPLIED",
            )

            self.assertFalse(target.exists())

            self.assertTrue(
                applied["recovery_available"]
            )

            self.assertEqual(
                applied["deleted_sha256"],
                proposal["before_sha256"],
            )

            stage = changes._recovery_path(
                proposal["id"]
            )

            self.assertTrue(stage.exists())

            self.assertEqual(
                stage.read_bytes(),
                b"recoverable-delete\n",
            )

            self.assertNotIn(
                "recovery_stage_path",
                applied,
            )

    def test_delete_supports_binary_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "binary.bin"
            content = (
                b"\x00\x01\x02MAESTRO\xff\xfe"
            )

            target.write_bytes(content)

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="binary.bin",
                operation="delete",
            )

            changes.approve(
                proposal["id"],
                confirm_delete=True,
            )

            applied = changes.apply(
                proposal["id"]
            )

            self.assertEqual(
                applied["status"],
                "APPLIED",
            )

            self.assertFalse(target.exists())

            self.assertEqual(
                changes._recovery_path(
                    proposal["id"]
                ).read_bytes(),
                content,
            )

    def test_delete_nonexistent_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            with self.assertRaises(
                FileNotFoundError
            ):
                changes.propose(
                    workspace_id=row["id"],
                    relative="missing.txt",
                    operation="delete",
                )

    def test_delete_source_changed_after_approval_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "delete.txt"

            target.write_text(
                "version one\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="delete.txt",
                operation="delete",
            )

            changes.approve(
                proposal["id"],
                confirm_delete=True,
            )

            target.write_text(
                "external change\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                ProposalConflictError
            ):
                changes.apply(
                    proposal["id"]
                )

            self.assertEqual(
                changes.get(
                    proposal["id"]
                )["status"],
                "STALE",
            )

            self.assertEqual(
                target.read_text(
                    encoding="utf-8"
                ),
                "external change\n",
            )

    def test_delete_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            real = root / "real.txt"
            link = root / "link.txt"

            real.write_text(
                "real\n",
                encoding="utf-8",
            )

            link.symlink_to(real.name)

            with self.assertRaises(
                PermissionError
            ):
                changes.propose(
                    workspace_id=row["id"],
                    relative="link.txt",
                    operation="delete",
                )

            self.assertTrue(real.exists())
            self.assertTrue(link.is_symlink())

    def test_delete_verification_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "delete.txt"

            target.write_text(
                "must be restored\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="delete.txt",
                operation="delete",
            )

            changes.approve(
                proposal["id"],
                confirm_delete=True,
            )

            with mock.patch.object(
                changes,
                "_verify_delete_applied",
                side_effect=OSError(
                    "forced delete verification failure"
                ),
            ):
                with self.assertRaises(
                    ProposalApplyError
                ):
                    changes.apply(
                        proposal["id"]
                    )

            final = changes.get(
                proposal["id"]
            )

            self.assertEqual(
                final["status"],
                "ROLLED_BACK",
            )

            self.assertTrue(
                final["workspace_state_restored"]
            )

            self.assertTrue(target.exists())

            self.assertEqual(
                target.read_text(
                    encoding="utf-8"
                ),
                "must be restored\n",
            )

    def test_delete_crash_completion_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "delete.txt"

            target.write_text(
                "crash recovery\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="delete.txt",
                operation="delete",
            )

            changes.approve(
                proposal["id"],
                confirm_delete=True,
            )

            payload = changes._load(
                proposal["id"]
            )

            payload["apply_started_at"] = (
                "simulated"
            )

            payload.setdefault(
                "history",
                [],
            ).append(
                changes._event(
                    "APPLY_STARTED"
                )
            )

            changes._save(payload)

            stage = changes._recovery_path(
                proposal["id"]
            )

            changes._write_delete_stage(
                target,
                stage,
                proposal["before_sha256"],
            )

            target.unlink()

            applied = changes.apply(
                proposal["id"]
            )

            self.assertEqual(
                applied["status"],
                "APPLIED",
            )

            self.assertTrue(
                applied["recovered_after_restart"]
            )

            self.assertFalse(target.exists())
            self.assertTrue(stage.exists())

    def test_applied_delete_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as td:
            root, row, changes = self.make_store(td)

            target = root / "delete.txt"
            target.write_text(
                "one shot\n",
                encoding="utf-8",
            )

            proposal = changes.propose(
                workspace_id=row["id"],
                relative="delete.txt",
                operation="delete",
            )

            changes.approve(
                proposal["id"],
                confirm_delete=True,
            )

            changes.apply(
                proposal["id"]
            )

            with self.assertRaises(
                ProposalStateError
            ):
                changes.apply(
                    proposal["id"]
                )


if __name__ == "__main__":
    unittest.main()
