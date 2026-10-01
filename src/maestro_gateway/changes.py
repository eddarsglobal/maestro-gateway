from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
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
_ALLOWED_OPERATIONS = {"write", "edit", "patch", "rename", "move"}
_RELOCATION_OPERATIONS = {"rename", "move"}


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


class ProposalApplyError(RuntimeError):
    """Raised when an approved proposal cannot be applied safely."""

    def __init__(
        self,
        message: str,
        proposal: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.proposal = proposal


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _change_id() -> str:
    return "CHG-" + uuid.uuid4().hex[:12].upper()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)

    return digest.hexdigest()


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


def _fsync_directory(directory: Path) -> None:
    """Best-effort durability barrier for a directory entry."""

    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return

    try:
        try:
            os.fsync(fd)
        except OSError:
            # Some filesystems/platforms do not allow fsync on directories.
            pass
    finally:
        os.close(fd)


def _write_transaction_temp(
    target: Path,
    raw: bytes,
    *,
    mode: int | None,
    tag: str,
) -> Path:
    """Create and fsync a temporary file beside the target."""

    fd, name = tempfile.mkstemp(
        prefix=f".{target.name}.maestro-{tag}-",
        suffix=".tmp",
        dir=str(target.parent),
    )

    temp_path = Path(name)
    fd_open = True

    try:
        if mode is not None:
            os.fchmod(fd, mode)

        with os.fdopen(fd, "wb") as handle:
            fd_open = False

            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

        return temp_path

    except Exception:
        if fd_open:
            try:
                os.close(fd)
            except OSError:
                pass

        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass

        raise


class ChangeProposalStore:
    """Local proposal ledger for governed filesystem changes.

    P0.6B.3 supports governed atomic apply after explicit approval. Proposal creation and approval remain write-free; only apply() may mutate the authorized workspace.
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
        result.pop("before_content", None)
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
            raise ValueError("Binary files are not writable in P0.6B.3")

        return True, _sha256(raw)

    def _relocation_snapshot(
        self,
        *,
        workspace_id: str,
        source_relative: str,
        destination_relative: str,
        operation: str,
    ) -> dict[str, Any]:
        pair = self.workspaces.resolve_relocation_targets(
            workspace_id,
            source_relative,
            destination_relative,
            operation,
        )

        source = Path(
            str(pair["source_absolute_path"])
        )

        destination = Path(
            str(pair["destination_absolute_path"])
        )

        def fingerprint(path: Path) -> dict[str, Any]:
            if not path.exists():
                return {
                    "exists": False,
                    "sha256": None,
                    "size": 0,
                    "inode": None,
                    "device": None,
                }

            if not path.is_file():
                raise ValueError(
                    "P0.6C.1 relocation supports regular files only"
                )

            stat = path.stat()

            return {
                "exists": True,
                "sha256": _sha256_file(path),
                "size": stat.st_size,
                "inode": stat.st_ino,
                "device": stat.st_dev,
            }

        return {
            **pair,
            "source": source,
            "destination": destination,
            "source_state": fingerprint(source),
            "destination_state": fingerprint(destination),
        }

    def _raise_relocation_stale(
        self,
        payload: dict[str, Any],
        reason: str,
    ) -> None:
        now = _utc_now()

        payload["status"] = "STALE"
        payload["updated_at"] = now
        payload["stale_reason"] = reason

        payload.setdefault("history", []).append(
            self._event(
                "STALE",
                reason=reason,
            )
        )

        self._save(payload)

        raise ProposalConflictError(
            reason,
            self._public(payload),
        )

    def _assert_relocation_fresh(
        self,
        payload: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        snapshot = self._relocation_snapshot(
            workspace_id=str(payload["workspace_id"]),
            source_relative=str(payload["path"]),
            destination_relative=str(
                payload["destination_path"]
            ),
            operation=str(payload["operation"]),
        )

        source_state = snapshot["source_state"]
        destination_state = snapshot["destination_state"]

        source_ok = (
            source_state["exists"] is True
            and source_state["sha256"]
            == payload.get("before_sha256")
            and source_state["inode"]
            == payload.get("before_inode")
            and source_state["device"]
            == payload.get("before_device")
        )

        destination_ok = (
            destination_state["exists"] is False
        )

        if not source_ok or not destination_ok:
            self._raise_relocation_stale(
                payload,
                (
                    "Relocation source or destination changed "
                    f"before {phase}"
                ),
            )

        return snapshot

    def _propose_relocation(
        self,
        *,
        workspace_id: str,
        source_relative: str,
        destination_relative: str,
        operation: str,
    ) -> dict[str, Any]:
        destination_relative = str(
            destination_relative or ""
        ).strip()

        if destination_relative in {"", ".", "/"}:
            raise ValueError(
                "destination must identify a file"
            )

        snapshot = self._relocation_snapshot(
            workspace_id=workspace_id,
            source_relative=source_relative,
            destination_relative=destination_relative,
            operation=operation,
        )

        source_state = snapshot["source_state"]
        destination_state = snapshot["destination_state"]

        if not source_state["exists"]:
            raise FileNotFoundError(
                "Relocation source does not exist"
            )

        if destination_state["exists"]:
            raise FileExistsError(
                "Relocation destination already exists"
            )

        source_path = str(snapshot["source_path"])
        destination_path = str(
            snapshot["destination_path"]
        )

        diff = (
            f"--- {source_path}\n"
            f"+++ {destination_path}\n"
            f"@@ MAESTRO {operation.upper()} @@\n"
            f"-{source_path}\n"
            f"+{destination_path}\n"
        )

        now = _utc_now()
        change_id = _change_id()

        payload: dict[str, Any] = {
            "id": change_id,
            "protocol": "P0.6C.1",
            "workspace_id": workspace_id,
            "workspace_label": (
                snapshot["workspace_label"]
            ),
            "operation": operation,
            "effect": operation,
            "path": source_path,
            "destination_path": destination_path,
            "before_exists": True,
            "before_sha256": source_state["sha256"],
            "after_sha256": source_state["sha256"],
            "before_size": source_state["size"],
            "after_size": source_state["size"],
            "before_inode": source_state["inode"],
            "before_device": source_state["device"],
            "destination_before_exists": False,
            "diff": diff,
            "diff_truncated": False,
            "status": "AWAITING_APPROVAL",
            "approval_required": True,
            "workspace_write_performed": False,
            "created_at": now,
            "updated_at": now,
            "history": [
                self._event("PROPOSED")
            ],
        }

        with self._lock:
            self._save(payload)

        return self._public(payload)

    def propose(
        self,
        *,
        workspace_id: str,
        relative: str,
        content: Any = None,
        operation: str = "edit",
        destination: str | None = None,
    ) -> dict[str, Any]:
        op = str(operation or "").strip().lower()

        if op not in _ALLOWED_OPERATIONS:
            raise ValueError(
                "operation must be one of: write, edit, patch, rename, move"
            )

        rel = str(relative or "").strip()
        if rel in {"", ".", "/"}:
            raise ValueError("path must identify a file")

        if op in _RELOCATION_OPERATIONS:
            return self._propose_relocation(
                workspace_id=workspace_id,
                source_relative=rel,
                destination_relative=str(destination or ""),
                operation=op,
            )

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
                    "Binary files are not writable in P0.6B.3"
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
            "protocol": "P0.6B.3",
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
            "before_content": before_text if exists else None,
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

            if (
                payload.get("operation")
                in _RELOCATION_OPERATIONS
            ):
                self._assert_relocation_fresh(
                    payload,
                    phase="approval",
                )
            else:
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

    def _verify_relocation_target(
        self,
        source: Path,
        destination: Path,
        expected_sha256: str,
    ) -> str:
        if source.exists():
            raise OSError(
                "Relocation source still exists"
            )

        if not destination.exists():
            raise OSError(
                "Relocation destination does not exist"
            )

        if not destination.is_file():
            raise OSError(
                "Relocation destination is not a regular file"
            )

        actual_sha256 = _sha256_file(destination)

        if actual_sha256 != expected_sha256:
            raise OSError(
                "Relocation SHA-256 verification failed"
            )

        return actual_sha256

    def _commit_relocation_recovery(
        self,
        payload: dict[str, Any],
        *,
        applied_sha256: str,
        recovered_action: str,
    ) -> dict[str, Any]:
        now = _utc_now()

        payload["status"] = "APPLIED"
        payload["applied_at"] = now
        payload["updated_at"] = now
        payload["workspace_write_performed"] = True
        payload["workspace_state_restored"] = False
        payload["applied_sha256"] = applied_sha256
        payload["applied_path"] = payload[
            "destination_path"
        ]
        payload["apply_protocol"] = "P0.6C.1"
        payload["recovered_after_restart"] = True

        payload.setdefault("history", []).append(
            self._event(recovered_action)
        )

        self._save(payload)
        return self._public(payload)

    def _apply_relocation(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        history = payload.setdefault(
            "history",
            [],
        )

        expected_sha256 = str(
            payload.get("before_sha256") or ""
        )

        snapshot = self._relocation_snapshot(
            workspace_id=str(payload["workspace_id"]),
            source_relative=str(payload["path"]),
            destination_relative=str(
                payload["destination_path"]
            ),
            operation=str(payload["operation"]),
        )

        source = snapshot["source"]
        destination = snapshot["destination"]
        source_state = snapshot["source_state"]
        destination_state = snapshot[
            "destination_state"
        ]

        had_apply_start = any(
            isinstance(row, dict)
            and row.get("action") == "APPLY_STARTED"
            for row in history
        )

        # ----------------------------------------------------
        # Crash recovery after destination link creation.
        # ----------------------------------------------------

        if had_apply_start:
            if (
                not source_state["exists"]
                and destination_state["exists"]
                and destination_state["sha256"]
                == expected_sha256
            ):
                return self._commit_relocation_recovery(
                    payload,
                    applied_sha256=expected_sha256,
                    recovered_action="APPLIED_RECOVERED",
                )

            if (
                source_state["exists"]
                and destination_state["exists"]
                and source_state["sha256"]
                == expected_sha256
                and destination_state["sha256"]
                == expected_sha256
            ):
                try:
                    same_file = os.path.samefile(
                        source,
                        destination,
                    )
                except OSError:
                    same_file = False

                if same_file:
                    os.unlink(source)
                    _fsync_directory(source.parent)

                    actual = (
                        self._verify_relocation_target(
                            source,
                            destination,
                            expected_sha256,
                        )
                    )

                    return (
                        self._commit_relocation_recovery(
                            payload,
                            applied_sha256=actual,
                            recovered_action=(
                                "APPLIED_RECOVERED"
                            ),
                        )
                    )

        snapshot = self._assert_relocation_fresh(
            payload,
            phase="apply",
        )

        source = snapshot["source"]
        destination = snapshot["destination"]

        now = _utc_now()

        payload["apply_started_at"] = now
        payload["updated_at"] = now
        payload["apply_protocol"] = "P0.6C.1"
        payload["apply_attempts"] = (
            int(payload.get("apply_attempts") or 0)
            + 1
        )

        history.append(
            self._event("APPLY_STARTED")
        )

        self._save(payload)

        destination_linked = False
        source_removed = False

        try:
            # Final policy/fingerprint check.
            snapshot = self._assert_relocation_fresh(
                payload,
                phase="relocation execution",
            )

            source = snapshot["source"]
            destination = snapshot["destination"]

            # ------------------------------------------------
            # NO-OVERWRITE transaction.
            #
            # os.replace() is deliberately NOT used here:
            # it could overwrite a destination that appeared
            # concurrently.
            #
            # os.link() fails atomically if destination exists.
            # The second step removes the original path.
            # APPLY_STARTED makes the two-step transaction
            # crash-recoverable.
            # ------------------------------------------------

            os.link(
                source,
                destination,
            )

            destination_linked = True
            _fsync_directory(destination.parent)

            if not os.path.samefile(
                source,
                destination,
            ):
                raise OSError(
                    "Relocation destination inode mismatch"
                )

            if _sha256_file(destination) != expected_sha256:
                raise OSError(
                    "Relocation destination SHA mismatch "
                    "before source removal"
                )

            os.unlink(source)
            source_removed = True

            _fsync_directory(source.parent)

            actual_after_sha256 = (
                self._verify_relocation_target(
                    source,
                    destination,
                    expected_sha256,
                )
            )

        except ProposalConflictError:
            raise

        except FileExistsError:
            if not destination_linked:
                self._raise_relocation_stale(
                    payload,
                    (
                        "Relocation destination appeared "
                        "immediately before execution"
                    ),
                )
            raise

        except Exception as exc:
            rollback_ok = False
            rollback_error: str | None = None

            if destination_linked:
                try:
                    source_exists = source.exists()
                    destination_exists = (
                        destination.exists()
                    )

                    if (
                        source_exists
                        and destination_exists
                    ):
                        if not os.path.samefile(
                            source,
                            destination,
                        ):
                            raise OSError(
                                "Rollback refused: source path "
                                "is occupied by another file"
                            )

                        # Source still exists: just remove the
                        # transaction-created destination link.
                        os.unlink(destination)
                        _fsync_directory(
                            destination.parent
                        )

                    elif (
                        not source_exists
                        and destination_exists
                    ):
                        # Source was already removed. Restore it
                        # without overwriting anything.
                        os.link(
                            destination,
                            source,
                        )
                        _fsync_directory(source.parent)

                        os.unlink(destination)
                        _fsync_directory(
                            destination.parent
                        )

                    else:
                        raise OSError(
                            "Rollback state is inconsistent"
                        )

                    if (
                        not source.exists()
                        or not source.is_file()
                        or _sha256_file(source)
                        != expected_sha256
                        or destination.exists()
                    ):
                        raise OSError(
                            "Relocation rollback verification "
                            "failed"
                        )

                    rollback_ok = True

                except Exception as rollback_exc:
                    rollback_error = (
                        f"{type(rollback_exc).__name__}: "
                        f"{rollback_exc}"
                    )

            else:
                rollback_ok = True

            now = _utc_now()

            if destination_linked:
                status = (
                    "ROLLED_BACK"
                    if rollback_ok
                    else "ROLLBACK_FAILED"
                )
            else:
                status = "APPLY_FAILED"

            reason = (
                f"{type(exc).__name__}: {exc}"
            )

            payload["status"] = status
            payload["updated_at"] = now
            payload["apply_error"] = reason
            payload["workspace_write_performed"] = (
                destination_linked
                or source_removed
            )
            payload["workspace_state_restored"] = (
                rollback_ok
            )

            if rollback_error:
                payload["rollback_error"] = (
                    rollback_error
                )

            history.append(
                self._event(
                    status,
                    reason=reason,
                )
            )

            self._save(payload)

            raise ProposalApplyError(
                (
                    "Relocation failed; workspace "
                    "was restored"
                    if rollback_ok
                    else
                    "Relocation failed and rollback "
                    "could not be verified"
                ),
                self._public(payload),
            ) from exc

        now = _utc_now()

        payload["status"] = "APPLIED"
        payload["applied_at"] = now
        payload["updated_at"] = now
        payload["workspace_write_performed"] = True
        payload["workspace_state_restored"] = False
        payload["applied_sha256"] = (
            actual_after_sha256
        )
        payload["applied_path"] = payload[
            "destination_path"
        ]
        payload["recovered_after_restart"] = False

        history.append(
            self._event("APPLIED")
        )

        self._save(payload)

        return self._public(payload)

    def _verify_applied_target(
        self,
        target: Path,
        expected_sha256: str,
    ) -> str:
        if not target.exists():
            raise OSError("Applied target does not exist")

        if not target.is_file():
            raise OSError("Applied target is not a regular file")

        raw = target.read_bytes()
        actual_sha256 = _sha256(raw)

        if actual_sha256 != expected_sha256:
            raise OSError(
                "Post-write SHA-256 verification failed"
            )

        return actual_sha256

    def apply(self, change_id: str) -> dict[str, Any]:
        """Atomically apply one explicitly approved proposal.

        APPROVE and APPLY remain separate operations.

        Guarantees:
        - only APPROVED proposals may execute;
        - workspace policy is resolved again;
        - current SHA is verified again immediately before write;
        - data is fsynced to a temp file in the same directory;
        - os.replace() performs the atomic target switch;
        - written bytes are re-read and SHA-256 verified;
        - failures after replacement trigger rollback;
        - APPLIED proposals cannot be replayed.
        """

        with self._lock:
            payload = self._load(change_id)

            if payload.get("status") != "APPROVED":
                raise ProposalStateError(
                    "Only APPROVED proposals can be applied"
                )

            if (
                payload.get("operation")
                in _RELOCATION_OPERATIONS
            ):
                return self._apply_relocation(payload)

            workspace_id = str(payload["workspace_id"])
            relative = str(payload["path"])

            # Re-run workspace authorization at execution time.
            target_info = self.workspaces.resolve_change_target(
                workspace_id,
                relative,
            )

            target = Path(
                str(target_info["absolute_path"])
            )

            expected_exists = bool(
                payload.get("before_exists")
            )

            expected_before_sha256 = payload.get(
                "before_sha256"
            )

            expected_after_sha256 = str(
                payload.get("after_sha256") or ""
            )

            current_exists, current_sha256 = (
                self._current_fingerprint(
                    workspace_id=workspace_id,
                    relative=relative,
                )
            )

            history = payload.setdefault(
                "history",
                [],
            )

            had_apply_start = any(
                isinstance(row, dict)
                and row.get("action") == "APPLY_STARTED"
                for row in history
            )

            # ------------------------------------------------
            # Crash recovery:
            # if a previous APPLY reached the atomic replace
            # but the process died before ledger commit, the
            # target may already exactly equal after_sha256.
            # ------------------------------------------------

            if (
                (
                    current_exists != expected_exists
                    or current_sha256
                    != expected_before_sha256
                )
                and current_exists
                and current_sha256
                == expected_after_sha256
                and had_apply_start
            ):
                now = _utc_now()

                payload["status"] = "APPLIED"
                payload["applied_at"] = now
                payload["updated_at"] = now
                payload["workspace_write_performed"] = True
                payload["workspace_state_restored"] = False
                payload["applied_sha256"] = current_sha256
                payload["apply_protocol"] = "P0.6B.3"
                payload["recovered_after_restart"] = True

                history.append(
                    self._event("APPLIED_RECOVERED")
                )

                self._save(payload)
                return self._public(payload)

            # ------------------------------------------------
            # Normal stale protection.
            # ------------------------------------------------

            if (
                current_exists != expected_exists
                or current_sha256
                != expected_before_sha256
            ):
                now = _utc_now()

                reason = (
                    "Workspace file changed after approval "
                    "and before apply"
                )

                payload["status"] = "STALE"
                payload["updated_at"] = now
                payload["stale_reason"] = reason

                history.append(
                    self._event(
                        "STALE",
                        reason=reason,
                    )
                )

                self._save(payload)

                public = self._public(payload)

                raise ProposalConflictError(
                    reason,
                    public,
                )

            # ------------------------------------------------
            # Proposal integrity check.
            # ------------------------------------------------

            proposed_content = payload.get(
                "proposed_content"
            )

            try:
                _, proposed_raw = _normalize_content(
                    proposed_content
                )
            except Exception as exc:
                now = _utc_now()
                reason = (
                    "Stored proposal content failed "
                    f"integrity validation: {exc}"
                )

                payload["status"] = "INTEGRITY_ERROR"
                payload["updated_at"] = now
                payload["apply_error"] = reason

                history.append(
                    self._event(
                        "INTEGRITY_ERROR",
                        reason=reason,
                    )
                )

                self._save(payload)

                raise ProposalApplyError(
                    reason,
                    self._public(payload),
                ) from exc

            if _sha256(proposed_raw) != expected_after_sha256:
                now = _utc_now()

                reason = (
                    "Stored proposal content does not match "
                    "after_sha256"
                )

                payload["status"] = "INTEGRITY_ERROR"
                payload["updated_at"] = now
                payload["apply_error"] = reason

                history.append(
                    self._event(
                        "INTEGRITY_ERROR",
                        reason=reason,
                    )
                )

                self._save(payload)

                raise ProposalApplyError(
                    reason,
                    self._public(payload),
                )

            # ------------------------------------------------
            # Capture exact before bytes for rollback.
            # ------------------------------------------------

            before_raw = b""
            target_mode: int | None = None

            if current_exists:
                before_raw = target.read_bytes()

                # Fingerprint once more from the actual bytes
                # that would be used for rollback.
                if (
                    _sha256(before_raw)
                    != expected_before_sha256
                ):
                    now = _utc_now()

                    reason = (
                        "Workspace file changed during "
                        "apply preparation"
                    )

                    payload["status"] = "STALE"
                    payload["updated_at"] = now
                    payload["stale_reason"] = reason

                    history.append(
                        self._event(
                            "STALE",
                            reason=reason,
                        )
                    )

                    self._save(payload)

                    raise ProposalConflictError(
                        reason,
                        self._public(payload),
                    )

                target_mode = (
                    target.stat().st_mode & 0o7777
                )

            # ------------------------------------------------
            # Record execution intent BEFORE physical effect.
            # Status remains APPROVED so a process restart can
            # recover safely using SHA + APPLY_STARTED.
            # ------------------------------------------------

            now = _utc_now()

            payload["apply_started_at"] = now
            payload["updated_at"] = now
            payload["apply_protocol"] = "P0.6B.3"
            payload["apply_attempts"] = (
                int(payload.get("apply_attempts") or 0)
                + 1
            )

            history.append(
                self._event("APPLY_STARTED")
            )

            self._save(payload)

            temp_path: Path | None = None
            rollback_temp: Path | None = None
            target_replaced = False

            try:
                # Same-directory temporary file is required
                # for atomic os.replace semantics.
                temp_path = _write_transaction_temp(
                    target,
                    proposed_raw,
                    mode=target_mode,
                    tag=str(payload["id"]).lower(),
                )

                # Final stale check immediately before replace.
                latest_exists, latest_sha256 = (
                    self._current_fingerprint(
                        workspace_id=workspace_id,
                        relative=relative,
                    )
                )

                if (
                    latest_exists != expected_exists
                    or latest_sha256
                    != expected_before_sha256
                ):
                    try:
                        temp_path.unlink(
                            missing_ok=True
                        )
                    finally:
                        temp_path = None

                    now = _utc_now()

                    reason = (
                        "Workspace file changed immediately "
                        "before atomic replace"
                    )

                    payload["status"] = "STALE"
                    payload["updated_at"] = now
                    payload["stale_reason"] = reason

                    history.append(
                        self._event(
                            "STALE",
                            reason=reason,
                        )
                    )

                    self._save(payload)

                    raise ProposalConflictError(
                        reason,
                        self._public(payload),
                    )

                os.replace(
                    temp_path,
                    target,
                )

                temp_path = None
                target_replaced = True

                _fsync_directory(
                    target.parent
                )

                actual_after_sha256 = (
                    self._verify_applied_target(
                        target,
                        expected_after_sha256,
                    )
                )

            except ProposalConflictError:
                raise

            except Exception as exc:
                rollback_ok = False
                rollback_error: str | None = None

                if target_replaced:
                    try:
                        if expected_exists:
                            rollback_temp = (
                                _write_transaction_temp(
                                    target,
                                    before_raw,
                                    mode=target_mode,
                                    tag=(
                                        str(payload["id"]).lower()
                                        + "-rollback"
                                    ),
                                )
                            )

                            os.replace(
                                rollback_temp,
                                target,
                            )

                            rollback_temp = None

                            _fsync_directory(
                                target.parent
                            )

                            restored_raw = (
                                target.read_bytes()
                            )

                            if (
                                _sha256(restored_raw)
                                != expected_before_sha256
                            ):
                                raise OSError(
                                    "Rollback SHA-256 "
                                    "verification failed"
                                )

                        else:
                            if target.exists():
                                if not target.is_file():
                                    raise OSError(
                                        "Rollback target became "
                                        "a non-file"
                                    )

                                target.unlink()

                            _fsync_directory(
                                target.parent
                            )

                            if target.exists():
                                raise OSError(
                                    "Rollback failed to remove "
                                    "newly-created target"
                                )

                        rollback_ok = True

                    except Exception as rollback_exc:
                        rollback_error = (
                            f"{type(rollback_exc).__name__}: "
                            f"{rollback_exc}"
                        )

                else:
                    # Target was never replaced.
                    rollback_ok = True

                for candidate in (
                    temp_path,
                    rollback_temp,
                ):
                    if candidate is not None:
                        try:
                            candidate.unlink(
                                missing_ok=True
                            )
                        except OSError:
                            pass

                now = _utc_now()

                if target_replaced:
                    status = (
                        "ROLLED_BACK"
                        if rollback_ok
                        else "ROLLBACK_FAILED"
                    )
                else:
                    status = "APPLY_FAILED"

                reason = (
                    f"{type(exc).__name__}: {exc}"
                )

                payload["status"] = status
                payload["updated_at"] = now
                payload["apply_error"] = reason
                payload["workspace_write_performed"] = (
                    target_replaced
                )
                payload["workspace_state_restored"] = (
                    rollback_ok
                )

                if rollback_error:
                    payload["rollback_error"] = (
                        rollback_error
                    )

                history.append(
                    self._event(
                        status,
                        reason=reason,
                    )
                )

                self._save(payload)

                message = (
                    "Atomic apply failed; workspace was "
                    "restored"
                    if rollback_ok
                    else
                    "Atomic apply failed and rollback "
                    "could not be verified"
                )

                raise ProposalApplyError(
                    message,
                    self._public(payload),
                ) from exc

            # ------------------------------------------------
            # Commit successful execution to ledger.
            # ------------------------------------------------

            now = _utc_now()

            payload["status"] = "APPLIED"
            payload["applied_at"] = now
            payload["updated_at"] = now
            payload["workspace_write_performed"] = True
            payload["workspace_state_restored"] = False
            payload["applied_sha256"] = (
                actual_after_sha256
            )
            payload["recovered_after_restart"] = False

            history.append(
                self._event("APPLIED")
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
