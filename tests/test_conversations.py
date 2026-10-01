import tempfile
import unittest
from pathlib import Path

from maestro_gateway.conversations import (
    ConversationStore,
    provisional_title,
)


class ConversationStoreTests(unittest.TestCase):
    def test_provisional_title(self):
        title = provisional_title(
            "Peux-tu analyser mon projet Django et corriger les erreurs ?"
        )
        self.assertTrue(title.startswith("Peux-tu analyser"))
        self.assertLessEqual(len(title), 59)

    def test_create_append_reload(self):
        with tempfile.TemporaryDirectory() as td:
            store = ConversationStore(Path(td))
            row = store.create(
                first_message="Analyse mon projet Django"
            )

            store.append_message(
                row["id"],
                role="user",
                content="Analyse mon projet Django",
            )
            store.append_message(
                row["id"],
                role="assistant",
                content="D'accord.",
                metadata={"mission": {"ok": True}},
            )

            loaded = store.get(row["id"])
            self.assertEqual(len(loaded["messages"]), 2)
            self.assertEqual(
                loaded["messages"][1]["metadata"]["mission"]["ok"],
                True,
            )

    def test_pin_archive_rename(self):
        with tempfile.TemporaryDirectory() as td:
            store = ConversationStore(Path(td))
            row = store.create(first_message="hello")

            updated = store.update(
                row["id"],
                title="Renamed",
                pinned=True,
                archived=True,
            )

            self.assertEqual(updated["title"], "Renamed")
            self.assertTrue(updated["pinned"])
            self.assertTrue(updated["archived"])

    def test_search_in_message_content(self):
        with tempfile.TemporaryDirectory() as td:
            store = ConversationStore(Path(td))
            row = store.create(first_message="hello")
            store.append_message(
                row["id"],
                role="user",
                content="UniqueWorkspaceNeedle",
            )

            results = store.list(
                include_archived=True,
                query="workspace",
            )

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["id"], row["id"])


if __name__ == "__main__":
    unittest.main()
