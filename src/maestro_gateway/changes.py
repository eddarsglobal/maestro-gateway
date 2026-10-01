from __future__ import annotations

import difflib
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace import WorkspaceRegistry


MAX_CHANGE_BYTES = 1_048_576
MAX_DIFF_CHARS = 120_000
_CHANGE_ID = re.compile(r"^CHG-[A-F0-9]{12}$")
_ALLOWED_OPERATIONS = {"write", "edit", "patch"}


class ProposalConflictError(RuntimeError):
    """Raised when an approval target changed after proposal creation."""

    def __init__(
        self,
        message: str,
        proposal: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.proposal = proposal


class ProposalStateError(RuntimeError):
    """Raised for an invalid proposal state transition."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _change_id() -> str:
    return "CHG-" + uuid.uuid4().hex[:12].upper()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalize_content(value: Any) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise ValueError("content must be a UTF-8 text string")

    if "\x00" in value:
        raise ValueError("Binary/NUL content is not allowed")

    raw = value.encode("utf-8")

    if len(raw) > MAX_CHANGE_BYTES:
        raise ValueError(
            f"Proposed content exceeds {MAX_CHANGE_BYTES} byte limit"
        )

    return value, raw


class ChangeProposalStore:
    """Local proposal ledger for governed filesystem changes.

    P0.6B.1 is proposal-only: this class NEVER writes to the authorized
    workspace. It may write proposal metadata under ~/.maestro/user/changes.
    """

    def __init__(
        self,
        root: Path,
        workspaces: WorkspaceRegistry,
    ) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspaces = workspaces
        self._lock = threading.RLock()

    def _validate_id(self, change_id: str) -> str:
        value = str(change_id or "").strip().upper()

        if not _CHANGE_ID.fullmatch(value):
            raise ValueError("Invalid change proposal id")

        return value

    def _path(self, change_id: str) -> Path:
        return self.root / f"{self._validate_id(change_id)}.json"

    def _save(self, payload: dict[str, Any]) -> None:
        path = self._path(str(payload["id"]))
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)

    def _load(self, change_id: str) -> dict[str, Any]:
        path = self._path(change_id)

        if not path.exists():
            raise KeyError(self._validate_id(change_id))

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Corrupted change proposal") from exc

        if not isinstance(payload, dict):
            raise ValueError("Corrupted change proposal")

        return payload

    @staticmethod
    def _public(payload: dict[str, Any]) -> dict[str, Any]:
        result = dict(payload)
        result.pop("proposed_content", None)
        return result

    @staticmethod
    def _event(
        action: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "action": action,
            "at": _utc_now(),
        }

        if reason:
            row["reason"] = reason

        return row

    def list(
        self,
        *,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        with self._lock:
            for path in self.root.glob("CHG-*.json"):
                try:
                    payload = json.loads(
                        path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    continue

                if not isinstance(payload, dict):
                    continue

                if (
                    workspace_id
                    and payload.get("workspace_id") != workspace_id
                ):
                    continue

                rows.append(self._public(payload))

        rows.sort(
            key=lambda row: str(row.get("updated_at") or ""),
            reverse=True,
        )
        return rows

    def get(self, change_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public(self._load(change_id))

    def _current_fingerprint(
        self,
        *,
        workspace_id: str,
        relative: str,
    ) -> tuple[bool, str | None]:
        target_info = self.workspaces.resolve_change_target(
            workspace_id,
            relative,
        )
        target = Path(str(target_info["absolute_path"]))

        if not target.exists():
            return False, None

        if not target.is_file():
            raise ValueError("Change target must be a file")

        size = target.stat().st_size
        if size > MAX_CHANGE_BYTES:
            raise ValueError(
                f"File exceeds {MAX_CHANGE_BYTES} byte change limit"
            )

        raw = target.read_bytes()
        if b"\x00" in raw:
            raise ValueError("Binary files are not writable in P0.6B.1")

        return True, _sha256(raw)

    def propose(
        self,
        *,
        workspace_id: str,
        relative: str,
        content: str,
        operation: str = "edit",
    ) -> dict[str, Any]:
        op = str(operation or "").strip().lower()

        if op not in _ALLOWED_OPERATIONS:
            raise ValueError(
                "operation must be one of: write, edit, patch"
            )

        rel = str(relative or "").strip()
        if rel in {"", ".", "/"}:
            raise ValueError("path must identify a file")

        proposed_text, proposed_raw = _normalize_content(content)
        target_info = self.workspaces.resolve_change_target(
            workspace_id,
            rel,
        )
        target = Path(str(target_info["absolute_path"]))
        exists = target.exists()

        if exists and not target.is_file():
            raise ValueError("Change target must be a file")

        if op in {"edit", "patch"} and not exists:
            raise FileNotFoundError(
                "edit/patch requires an existing file"
            )

        before_raw = b""
        before_text = ""
        before_sha256: str | None = None

        if exists:
            size = target.stat().st_size
            if size > MAX_CHANGE_BYTES:
                raise ValueError(
                    f"File exceeds {MAX_CHANGE_BYTES} byte change limit"
                )

            before_raw = target.read_bytes()
            if b"\x00" in before_raw:
                raise ValueError(
                    "Binary files are not writable in P0.6B.1"
                )

            before_text = before_raw.decode("utf-8", errors="strict")
            before_sha256 = _sha256(before_raw)

        if before_raw == proposed_raw and exists:
            raise ValueError("Proposed content is identical to current file")

        display_path = str(target_info["path"])
        from_name = display_path if exists else "/dev/null"
        to_name = display_path

        diff = "".join(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                proposed_text.splitlines(keepends=True),
                fromfile=from_name,
                tofile=to_name,
                lineterm="\n",
            )
        )

        diff_truncated = len(diff) > MAX_DIFF_CHARS
        now = _utc_now()
        change_id = _change_id()

        payload: dict[str, Any] = {
            "id": change_id,
            "protocol": "P0.6B.1",
            "workspace_id": workspace_id,
            "workspace_label": target_info["workspace_label"],
            "operation": op,
            "effect": "edit" if exists else "create",
            "path": display_path,
            "before_exists": exists,
            "before_sha256": before_sha256,
            "after_sha256": _sha256(proposed_raw),
            "before_size": len(before_raw) if exists else 0,
            "after_size": len(proposed_raw),
            "diff": diff[:MAX_DIFF_CHARS],
            "diff_truncated": diff_truncated,
            "status": "AWAITING_APPROVAL",
            "approval_required": True,
            "workspace_write_performed": False,
            "created_at": now,
            "updated_at": now,
            "history": [self._event("PROPOSED")],
            "proposed_content": proposed_text,
        }

        with self._lock:
            self._save(payload)

        return self._public(payload)

    def approve(self, change_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._load(change_id)

            if payload.get("status") != "AWAITING_APPROVAL":
                raise ProposalStateError(
                    "Only AWAITING_APPROVAL proposals can be approved"
                )

            current_exists, current_sha256 = self._current_fingerprint(
                workspace_id=str(payload["workspace_id"]),
                relative=str(payload["path"]),
            )

            expected_exists = bool(payload.get("before_exists"))
            expected_sha256 = payload.get("before_sha256")

            if (
                current_exists != expected_exists
                or current_sha256 != expected_sha256
            ):
                now = _utc_now()
                reason = (
                    "Workspace file changed after the proposal was created"
                )
                payload["status"] = "STALE"
                payload["updated_at"] = now
                payload["stale_reason"] = reason
                payload.setdefault("history", []).append(
                    self._event("STALE", reason=reason)
                )
                self._save(payload)
                public = self._public(payload)
                raise ProposalConflictError(reason, public)

            now = _utc_now()
            payload["status"] = "APPROVED"
            payload["approved_at"] = now
            payload["approval_source"] = "local_user_session"
            payload["updated_at"] = now
            payload.setdefault("history", []).append(
                self._event("APPROVED")
            )
            self._save(payload)
            return self._public(payload)

    def reject(
        self,
        change_id: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._load(change_id)

            if payload.get("status") != "AWAITING_APPROVAL":
                raise ProposalStateError(
                    "Only AWAITING_APPROVAL proposals can be rejected"
                )

            now = _utc_now()
            clean_reason = str(reason or "").strip() or None
            payload["status"] = "REJECTED"
            payload["rejected_at"] = now
            payload["updated_at"] = now

            if clean_reason:
                payload["rejection_reason"] = clean_reason

            payload.setdefault("history", []).append(
                self._event("REJECTED", reason=clean_reason)
            )
            self._save(payload)
            return self._public(payload)
