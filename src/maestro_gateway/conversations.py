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

    @staticmethod
    def _validate_mission_id(mission_id: str) -> str:
        if not isinstance(mission_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", mission_id):
            raise ValueError("Invalid mission id")
        return mission_id

    @staticmethod
    def _mission_digest(entries: list[tuple[str, str, dict[str, Any]]]) -> str:
        """Fingerprint the exact logical turn, rejecting ambiguous JSON values."""
        import hashlib
        value = [{"role": role, "content": content, "metadata": meta}
                 for role, content, meta in entries]
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _mission_receipt_state(self, record: dict[str, Any], mission_id: str) -> dict[str, Any]:
        receipts = record.get("mission_turns", {})
        if not isinstance(receipts, dict):
            raise ValueError("Invalid mission receipts collection")
        messages = record.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Invalid conversation messages collection")
        receipt = receipts.get(mission_id)
        if receipt is None:
            # Earlier releases stored the Core task id in assistant metadata,
            # without a receipt. Never automatically duplicate such a turn.
            for item in messages:
                if not isinstance(item, dict):
                    raise ValueError("Invalid stored conversation message")
                meta = item.get("metadata", {})
                if isinstance(meta, dict) and isinstance(meta.get("mission"), dict):
                    if meta["mission"].get("task_id") == mission_id:
                        return {"status": "LEGACY_UNCERTAIN", "mission_id": mission_id}
            return {"status": "ABSENT", "mission_id": mission_id}
        if not isinstance(receipt, dict):
            raise ValueError("Invalid mission receipt")
        start, count, digest = (receipt.get("start"), receipt.get("count"),
                                receipt.get("sha256"))
        if (type(start) is not int or type(count) is not int or start < 0
                or count < 1 or start + count > len(messages)
                or not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ValueError("Corrupt mission receipt")
        extracted = []
        for item in messages[start:start + count]:
            if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
                raise ValueError("Corrupt receipted message")
            extracted.append((item.get("role"), item.get("content"), item["metadata"]))
        if self._mission_digest(extracted) != digest:
            raise ValueError("Mission receipt message fingerprint mismatch")
        return {"status": "PUBLISHED", "mission_id": mission_id,
                "sha256": digest, "start": start, "count": count}

    def inspect_mission_turn(self, conversation_id: str, *, mission_id: str) -> dict[str, Any]:
        """Read-only reconciliation evidence. Never use ABSENT to replay Core.

        This verifies only the conversation file. Core/Workspace/Git must be
        reconciled independently before an operation can be considered safe.
        """
        mission_id = self._validate_mission_id(mission_id)
        with self._lock:
            record = self.get(conversation_id)
            return self._mission_receipt_state(record, mission_id)

    def append_messages(
        self,
        conversation_id: str,
        *,
        messages: list[dict[str, Any]],
        mission_id: str | None = None,
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

        if mission_id is not None:
            mission_id = self._validate_mission_id(mission_id)
            for _role, _content, meta in validated:
                if (isinstance(meta.get("mission"), dict)
                        and meta["mission"].get("task_id") not in (None, mission_id)):
                    raise ValueError("Mission identifier mismatches message metadata")
            incoming_digest = self._mission_digest(validated)

        with self._lock:
            record = self.get(conversation_id)
            if not isinstance(record.get("messages"), list):
                raise ValueError("Invalid conversation messages collection")
            if mission_id is not None:
                state = self._mission_receipt_state(record, mission_id)
                if state["status"] == "PUBLISHED":
                    if (state["sha256"] != incoming_digest
                            or state["count"] != len(validated)):
                        raise ValueError("Conflicting retry for existing mission")
                    return record  # Durable turn already present: no duplicate write.
                if state["status"] != "ABSENT":
                    raise RuntimeError("Legacy mission ambiguous; manual reconciliation required")
                receipts = record.setdefault("mission_turns", {})
                receipts[mission_id] = {"start": len(record["messages"]),
                                        "count": len(validated),
                                        "sha256": incoming_digest}
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
