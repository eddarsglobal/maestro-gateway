import tempfile
import unittest
from pathlib import Path

from maestro_gateway.workspace import (
    WorkspaceRegistry,
)


class WorkspaceRegistryTests(unittest.TestCase):
    def test_authorize_tree_read_search(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text(
                "print('MAESTRO needle')\n",
                encoding="utf-8",
            )

            store = WorkspaceRegistry(
                Path(td) / "registry.json"
            )
            row = store.authorize_path(
                str(root)
            )

            tree = store.tree(
                row["id"],
                max_depth=2,
            )
            paths = {
                item["path"]
                for item in tree["entries"]
            }

            self.assertIn(
                "src/app.py",
                paths,
            )

            read = store.read(
                row["id"],
                "src/app.py",
            )
            self.assertIn(
                "MAESTRO",
                read["content"],
            )

            search = store.search(
                row["id"],
                "needle",
            )
            self.assertEqual(
                search["results"][0]["path"],
                "src/app.py",
            )

    def test_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()

            store = WorkspaceRegistry(
                Path(td) / "registry.json"
            )
            row = store.authorize_path(
                str(root)
            )

            with self.assertRaises(
                PermissionError
            ):
                store.read(
                    row["id"],
                    "../outside.txt",
                )

    def test_sensitive_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            root.mkdir()
            (root / ".env").write_text(
                "SECRET=1\n",
                encoding="utf-8",
            )

            store = WorkspaceRegistry(
                Path(td) / "registry.json"
            )
            row = store.authorize_path(
                str(root)
            )

            with self.assertRaises(
                PermissionError
            ):
                store.read(
                    row["id"],
                    ".env",
                )


if __name__ == "__main__":
    unittest.main()
