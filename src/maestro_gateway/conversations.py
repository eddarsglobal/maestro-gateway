from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_CHAT_ID = re.compile(r"^CHAT-[A-F0-9]{12}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_chat_id() -> str:
    return "CHAT-" + uuid.uuid4().hex[:12].upper()


def provisional_title(text: str, limit: int = 58) -> str:
    clean = " ".join(str(text or "").strip().split())
    if not clean:
        return "New chat"

    # Keep a compact, deterministic provisional title.
    words = clean.split()
    title = " ".join(words[:8])

    if len(title) > limit:
        title = title[:limit].rstrip()

    if len(words) > 8 or len(clean) > len(title):
        title = title.rstrip(" .,:;!?-") + "…"

    return title or "New chat"


class ConversationStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        if self.root.is_symlink():
            raise ValueError("Symlinked conversation directory refused")
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, conversation_id: str) -> Path:
        value = str(conversation_id or "").strip().upper()
        if not _CHAT_ID.fullmatch(value):
            raise ValueError("Invalid conversation id")
        return self.root / f"{value}.json"

    def _read_path(self, path: Path) -> dict[str, Any]:
        if path.is_symlink():
            raise ValueError("Symlinked conversation record refused")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KeyError(path.stem) from exc

        if not isinstance(payload, dict):
            raise ValueError(f"Invalid conversation record: {path}")

        return payload

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        """Atomic one-conversation publication, durable on fsync-capable filesystems.

        This is not an atomic transaction with the Core or Workspace. Only one
        Gateway process may own this store; filesystem-level locking between
        independent Gateway processes is not provided here.
        """
        path = self._path(str(record["id"]))
        if path.is_symlink():
            raise ValueError("Symlinked conversation record refused")
        data = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n").encode("utf-8")
        tmp = self.root / (path.name + "." + uuid.uuid4().hex + ".tmp")
        fd = None
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            dir_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if fd is not None:
                os.close(fd)
            tmp.unlink(missing_ok=True)
        return record

    def create(
        self,
        *,
        first_message: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            now = _utc_now()
            conversation_id = _new_chat_id()

            record: dict[str, Any] = {
                "id": conversation_id,
                "title": (
                    str(title).strip()[:120]
                    if title and str(title).strip()
                    else provisional_title(first_message or "")
                ),
                "created_at": now,
                "updated_at": now,
                "pinned": False,
                "archived": False,
                "messages": [],
            }

            return self._write(record)

    def ensure(
        self,
        conversation_id: str | None,
        *,
        first_message: str | None = None,
    ) -> dict[str, Any]:
        if conversation_id:
            return self.get(conversation_id)

        return self.create(first_message=first_message)

    def get(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            return self._read_path(self._path(conversation_id))

    def append_messages(
        self,
        conversation_id: str,
        *,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Validate then publish all messages with exactly one atomic rename.

        Caller must invoke only after the governed mission outcome is known.
        A crash before rename leaves no partial turn; after rename the entire
        turn is visible. A Core transaction may already have committed; this
        method does NOT promise cross-store atomicity.
        """
        if not isinstance(messages, list) or not messages:
            raise ValueError("Messages must be a nonempty list")
        validated = []
        for item in messages:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant", "system"}:
                raise ValueError("Invalid conversation message role")
            content = str(item.get("content") or "").strip()
            if not content:
                raise ValueError("Conversation message content must not be empty")
            metadata = item.get("metadata", {})
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, dict):
                raise ValueError("Conversation metadata must be an object")
            validated.append((item["role"], content, metadata))

        with self._lock:
            record = self.get(conversation_id)
            if not isinstance(record.get("messages"), list):
                raise ValueError("Invalid conversation messages collection")
            now = _utc_now()
            for role, content, metadata in validated:
                record["messages"].append({
                    "id": "MSG-" + uuid.uuid4().hex[:12].upper(),
                    "role": role, "content": content,
                    "created_at": now, "metadata": metadata,
                })
                if (role == "user" and len(record["messages"]) == 1
                        and record.get("title") in {"", "New chat"}):
                    record["title"] = provisional_title(content)
            record["updated_at"] = now
            return self._write(record)

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.append_messages(
            conversation_id,
            messages=[{"role": role, "content": content, "metadata": metadata or {}}],
        )

    def update(
        self,
        conversation_id: str,
        *,
        title: Any = None,
        pinned: Any = None,
        archived: Any = None,
    ) -> dict[str, Any]:
        with self._lock:
            record = self.get(conversation_id)

            if title is not None:
                value = " ".join(str(title).strip().split())
                if not value:
                    raise ValueError("Conversation title must not be empty")
                record["title"] = value[:120]

            if pinned is not None:
                if not isinstance(pinned, bool):
                    raise ValueError("pinned must be boolean")
                record["pinned"] = pinned

            if archived is not None:
                if not isinstance(archived, bool):
                    raise ValueError("archived must be boolean")
                record["archived"] = archived

            record["updated_at"] = _utc_now()
            return self._write(record)

    def delete(self, conversation_id: str) -> None:
        with self._lock:
            path = self._path(conversation_id)
            if not path.exists():
                raise KeyError(conversation_id)
            path.unlink()

    def list(
        self,
        *,
        include_archived: bool = False,
        query: str = "",
    ) -> list[dict[str, Any]]:
        q = " ".join(str(query or "").casefold().split())
        rows: list[dict[str, Any]] = []

        with self._lock:
            for path in self.root.glob("CHAT-*.json"):
                try:
                    record = self._read_path(path)
                except Exception:
                    continue

                if record.get("archived") and not include_archived:
                    continue

                messages = list(record.get("messages") or [])
                haystack = " ".join(
                    [
                        str(record.get("title") or ""),
                        *[
                            str(item.get("content") or "")
                            for item in messages
                        ],
                    ]
                ).casefold()

                if q and q not in haystack:
                    continue

                preview = ""
                for item in reversed(messages):
                    content = str(item.get("content") or "").strip()
                    if content:
                        preview = content[:180]
                        break

                rows.append(
                    {
                        "id": record["id"],
                        "title": record.get("title") or "New chat",
                        "created_at": record.get("created_at"),
                        "updated_at": record.get("updated_at"),
                        "pinned": bool(record.get("pinned")),
                        "archived": bool(record.get("archived")),
                        "message_count": len(messages),
                        "preview": preview,
                    }
                )

        rows.sort(
            key=lambda row: (
                not bool(row["pinned"]),
                str(row.get("updated_at") or ""),
            ),
            reverse=False,
        )

        # Pinned first, then newest within each group.
        pinned_rows = sorted(
            [row for row in rows if row["pinned"]],
            key=lambda row: str(row.get("updated_at") or ""),
            reverse=True,
        )
        normal_rows = sorted(
            [row for row in rows if not row["pinned"]],
            key=lambda row: str(row.get("updated_at") or ""),
            reverse=True,
        )

        return pinned_rows + normal_rows
