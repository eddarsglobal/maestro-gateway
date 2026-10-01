import tempfile
import unittest
from pathlib import Path

from maestro_gateway.changes import (
    ChangeProposalStore,
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


if __name__ == "__main__":
    unittest.main()
