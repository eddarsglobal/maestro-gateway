from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .git_inspection import (
    _call,
    _confined_repo,
    _environment,
    _require_success,
)
from .sensitive_policy import (
    is_sensitive_path,
)
from .workspace import WorkspaceRegistry


GIT_COMMIT_PROTOCOL = "P0.6E.3A"
GIT_COMMIT_MODE = "proposal_approval_only"

MAX_COMMIT_MESSAGE_BYTES = 16 * 1024
MAX_COMMIT_PATHS = 1024

_COMMIT_ID = re.compile(
    r"^GCM-[A-F0-9]{12}$"
)

_TRUSTED_GIT_CANDIDATES = (
    Path("/usr/bin/git"),
    Path("/opt/homebrew/bin/git"),
    Path("/usr/local/bin/git"),
)

_IN_PROGRESS_FILES = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
)

_IN_PROGRESS_DIRS = (
    "rebase-merge",
    "rebase-apply",
    "sequencer",
)


class GitCommitConflictError(
    RuntimeError
):
    def __init__(
        self,
        message: str,
        proposal: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.proposal = proposal


class GitCommitStateError(
    RuntimeError
):
    pass


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _commit_id() -> str:
    return (
        "GCM-"
        + uuid.uuid4().hex[:12].upper()
    )


def _sha256_bytes(
    raw: bytes,
) -> str:
    return hashlib.sha256(
        raw
    ).hexdigest()


def _sha256_file(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            block = handle.read(
                1024 * 1024
            )

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


def _canonical_sha256(
    value: Any,
) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return _sha256_bytes(raw)


def _fsync_directory(
    directory: Path,
) -> None:
    try:
        fd = os.open(
            str(directory),
            os.O_RDONLY,
        )
    except OSError:
        return

    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)


def _trusted_git_path() -> Path:
    for candidate in (
        _TRUSTED_GIT_CANDIDATES
    ):
        if (
            candidate.exists()
            and candidate.is_file()
        ):
            resolved = candidate.resolve()

            if resolved.is_file():
                return candidate

    raise FileNotFoundError(
        "No trusted Git executable was "
        "found for governed commit proposals"
    )


def _file_state(
    path: Path,
    *,
    display: str,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": display,
            "exists": False,
            "size": 0,
            "mode": None,
            "sha256": None,
        }

    if path.is_symlink():
        raise PermissionError(
            "Symlink-backed Git state is "
            "not allowed"
        )

    if not path.is_file():
        raise ValueError(
            "Expected Git state file is "
            "not a regular file"
        )

    info = path.stat()

    return {
        "path": display,
        "exists": True,
        "size": int(info.st_size),
        "mode": stat.S_IMODE(
            info.st_mode
        ),
        "sha256": _sha256_file(path),
    }


def _normalize_message(
    value: Any,
) -> str:
    if not isinstance(value, str):
        raise ValueError(
            "Git commit message must be "
            "a UTF-8 text string"
        )

    if "\x00" in value:
        raise ValueError(
            "Git commit message cannot "
            "contain NUL"
        )

    normalized = (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    if not normalized.strip():
        raise ValueError(
            "Git commit message cannot "
            "be empty"
        )

    raw = normalized.encode("utf-8")

    if len(raw) > MAX_COMMIT_MESSAGE_BYTES:
        raise ValueError(
            "Git commit message exceeds "
            f"{MAX_COMMIT_MESSAGE_BYTES} "
            "byte limit"
        )

    return normalized


def _require_complete_success(
    result: dict[str, Any],
    *,
    operation: str,
) -> None:
    if (
        result.get("stdout_reader_error")
        or result.get(
            "stderr_reader_error"
        )
    ):
        raise ValueError(
            f"{operation} output collection "
            "failed"
        )

    _require_success(
        result,
        operation=operation,
    )

    if (
        result.get("stdout_truncated")
        or result.get("stderr_truncated")
    ):
        raise ValueError(
            f"{operation} output exceeded "
            "the bounded capture"
        )


def _nul_items(
    result: dict[str, Any],
    *,
    operation: str,
) -> list[str]:
    _require_complete_success(
        result,
        operation=operation,
    )

    return [
        item
        for item in str(
            result.get("stdout") or ""
        ).split("\x00")
        if item
    ]


def _validate_git_relative_path(
    value: str,
) -> str:
    path = str(value)

    if not path:
        raise ValueError(
            "Empty Git path is not allowed"
        )

    candidate = Path(path)

    if candidate.is_absolute():
        raise PermissionError(
            "Absolute Git paths are not "
            "allowed"
        )

    if ".." in candidate.parts:
        raise PermissionError(
            "Git path escape is not allowed"
        )

    return path


class GitCommitProposalStore:
    """
    P0.6E.3A governed Git commit proposal
    ledger.

    Proposal creation, approval and
    rejection are strictly repository
    mutation-free.

    Commit creation and ref mutation are
    intentionally unavailable in P0.6E.3A.
    """

    def __init__(
        self,
        root: Path,
        workspaces: WorkspaceRegistry,
    ) -> None:
        self.root = Path(
            root
        ).expanduser()

        self.root.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            os.chmod(
                self.root,
                0o700,
            )
        except OSError:
            pass

        self.workspaces = workspaces
        self._lock = threading.RLock()

    def _validate_id(
        self,
        commit_id: str,
    ) -> str:
        value = str(
            commit_id or ""
        ).strip().upper()

        if not _COMMIT_ID.fullmatch(
            value
        ):
            raise ValueError(
                "Invalid Git commit "
                "proposal id"
            )

        return value

    def _path(
        self,
        commit_id: str,
    ) -> Path:
        return (
            self.root
            / (
                self._validate_id(
                    commit_id
                )
                + ".json"
            )
        )

    def _save(
        self,
        payload: dict[str, Any],
    ) -> None:
        path = self._path(
            str(payload["id"])
        )

        tmp = (
            self.root
            / (
                "."
                + path.name
                + "."
                + uuid.uuid4().hex
                + ".tmp"
            )
        )

        raw = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

        try:
            with tmp.open("xb") as handle:
                try:
                    os.chmod(
                        tmp,
                        0o600,
                    )
                except OSError:
                    pass

                handle.write(raw)
                handle.flush()
                os.fsync(
                    handle.fileno()
                )

            os.replace(
                tmp,
                path,
            )

            try:
                os.chmod(
                    path,
                    0o600,
                )
            except OSError:
                pass

            _fsync_directory(
                self.root
            )

        finally:
            try:
                tmp.unlink(
                    missing_ok=True
                )
            except OSError:
                pass

    def _load(
        self,
        commit_id: str,
    ) -> dict[str, Any]:
        path = self._path(
            commit_id
        )

        if not path.exists():
            raise KeyError(
                self._validate_id(
                    commit_id
                )
            )

        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Corrupted Git commit "
                "proposal"
            ) from exc

        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                "Corrupted Git commit "
                "proposal"
            )

        return payload

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

    @staticmethod
    def _public(
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return dict(payload)

    def list(
        self,
        *,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[
            dict[str, Any]
        ] = []

        with self._lock:
            for path in self.root.glob(
                "GCM-*.json"
            ):
                try:
                    payload = json.loads(
                        path.read_text(
                            encoding="utf-8"
                        )
                    )
                except (
                    OSError,
                    json.JSONDecodeError,
                ):
                    continue

                if not isinstance(
                    payload,
                    dict,
                ):
                    continue

                if (
                    workspace_id
                    and payload.get(
                        "workspace_id"
                    )
                    != workspace_id
                ):
                    continue

                rows.append(
                    self._public(
                        payload
                    )
                )

        rows.sort(
            key=lambda row: str(
                row.get(
                    "updated_at"
                )
                or ""
            ),
            reverse=True,
        )

        return rows

    def get(
        self,
        commit_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self._public(
                self._load(
                    commit_id
                )
            )

    def _snapshot(
        self,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        workspace = self.workspaces.get(
            workspace_id
        )

        if (
            workspace.get("kind")
            != "directory"
        ):
            raise ValueError(
                "Git commit proposals require "
                "an authorized directory "
                "workspace"
            )

        workspace_root = Path(
            str(workspace["path"])
        ).expanduser().resolve()

        if not workspace_root.is_dir():
            raise FileNotFoundError(
                workspace_root
            )

        git_path = _trusted_git_path()
        resolved_git_path = (
            git_path.resolve()
        )

        runner_state = _file_state(
            resolved_git_path,
            display=str(
                resolved_git_path
            ),
        )

        with tempfile.TemporaryDirectory(
            prefix="maestro-git-commit-"
        ) as td:
            private_home = Path(td)

            env = _environment(
                git_path=git_path,
                private_home=private_home,
            )

            env[
                "GIT_LITERAL_PATHSPECS"
            ] = "1"

            (
                repository_root,
                _,
            ) = _confined_repo(
                workspace_root=(
                    workspace_root
                ),
                git_path=git_path,
                env=env,
            )

            git_dir_result = _call(
                git_path=git_path,
                cwd=repository_root,
                env=env,
                args=[
                    "rev-parse",
                    "--absolute-git-dir",
                ],
                timeout_ms=3_000,
            )

            _require_complete_success(
                git_dir_result,
                operation=(
                    "Git directory detection"
                ),
            )

            git_dir = Path(
                str(
                    git_dir_result.get(
                        "stdout"
                    )
                    or ""
                ).strip()
            ).resolve()

            try:
                git_dir.relative_to(
                    repository_root
                )
            except ValueError as exc:
                raise PermissionError(
                    "Git metadata escapes the "
                    "authorized workspace"
                ) from exc

            index_path = (
                git_dir / "index"
            )

            if not index_path.exists():
                raise ValueError(
                    "P0.6E.3A requires an "
                    "existing Git index"
                )

            for name in _IN_PROGRESS_FILES:
                marker = git_dir / name

                if os.path.lexists(
                    marker
                ):
                    raise ValueError(
                        "Git operation in progress: "
                        + name
                    )

            for name in _IN_PROGRESS_DIRS:
                marker = git_dir / name

                if os.path.lexists(
                    marker
                ):
                    raise ValueError(
                        "Git operation in progress: "
                        + name
                    )

            config_path = (
                git_dir / "config"
            )

            config_state = _file_state(
                config_path,
                display=".git/config",
            )

            index_state = _file_state(
                index_path,
                display=".git/index",
            )

            head_file_state = _file_state(
                git_dir / "HEAD",
                display=".git/HEAD",
            )

            includes = _call(
                git_path=git_path,
                cwd=repository_root,
                env=env,
                args=[
                    "config",
                    "--local",
                    "--get-regexp",
                    "^include.*\\.path$",
                ],
                timeout_ms=3_000,
            )

            if includes.get(
                "timed_out"
            ):
                raise TimeoutError(
                    "Git config include "
                    "inspection timed out"
                )

            if (
                includes.get(
                    "stdout_reader_error"
                )
                or includes.get(
                    "stderr_reader_error"
                )
                or includes.get(
                    "stdout_truncated"
                )
                or includes.get(
                    "stderr_truncated"
                )
            ):
                raise ValueError(
                    "Git config include "
                    "inspection was incomplete"
                )

            if includes.get(
                "exit_code"
            ) not in {
                0,
                1,
            }:
                _require_success(
                    includes,
                    operation=(
                        "Git config include "
                        "inspection"
                    ),
                )

            if (
                includes.get(
                    "exit_code"
                )
                == 0
                and str(
                    includes.get(
                        "stdout"
                    )
                    or ""
                ).strip()
            ):
                raise PermissionError(
                    "Repository-local Git config "
                    "includes are not allowed for "
                    "governed commit proposals"
                )

            head_result = _call(
                git_path=git_path,
                cwd=repository_root,
                env=env,
                args=[
                    "rev-parse",
                    "--verify",
                    "HEAD^{commit}",
                ],
                timeout_ms=3_000,
            )

            _require_complete_success(
                head_result,
                operation=(
                    "Git HEAD resolution"
                ),
            )

            head_commit = str(
                head_result.get(
                    "stdout"
                )
                or ""
            ).strip()

            if not re.fullmatch(
                r"(?:[0-9a-fA-F]{40}|"
                r"[0-9a-fA-F]{64})",
                head_commit,
            ):
                raise ValueError(
                    "Git HEAD did not resolve "
                    "to a supported object id"
                )

            ref_result = _call(
                git_path=git_path,
                cwd=repository_root,
                env=env,
                args=[
                    "symbolic-ref",
                    "-q",
                    "HEAD",
                ],
                timeout_ms=3_000,
            )

            if ref_result.get(
                "timed_out"
            ):
                raise TimeoutError(
                    "Git branch resolution "
                    "timed out"
                )

            if (
                ref_result.get(
                    "stdout_reader_error"
                )
                or ref_result.get(
                    "stderr_reader_error"
                )
                or ref_result.get(
                    "stdout_truncated"
                )
                or ref_result.get(
                    "stderr_truncated"
                )
            ):
                raise ValueError(
                    "Git branch resolution "
                    "was incomplete"
                )

            if ref_result.get(
                "exit_code"
            ) == 1:
                raise ValueError(
                    "Detached HEAD is not "
                    "supported in P0.6E.3A"
                )

            _require_success(
                ref_result,
                operation=(
                    "Git branch resolution"
                ),
            )

            head_ref = str(
                ref_result.get(
                    "stdout"
                )
                or ""
            ).strip()

            if not head_ref.startswith(
                "refs/heads/"
            ):
                raise ValueError(
                    "P0.6E.3A requires HEAD "
                    "to reference refs/heads/*"
                )

            unmerged_result = _call(
                git_path=git_path,
                cwd=repository_root,
                env=env,
                args=[
                    "ls-files",
                    "--unmerged",
                    "-z",
                ],
                timeout_ms=5_000,
            )

            unmerged = _nul_items(
                unmerged_result,
                operation=(
                    "Git unmerged index "
                    "inspection"
                ),
            )

            if unmerged:
                raise ValueError(
                    "Unmerged Git index entries "
                    "are not allowed"
                )

            staged_paths_result = _call(
                git_path=git_path,
                cwd=repository_root,
                env=env,
                args=[
                    "diff",
                    "--cached",
                    "--name-only",
                    "-z",
                    "--no-renames",
                    "--no-ext-diff",
                    "--no-textconv",
                    "HEAD",
                    "--",
                ],
                timeout_ms=8_000,
            )

            staged_paths = [
                _validate_git_relative_path(
                    item
                )
                for item in _nul_items(
                    staged_paths_result,
                    operation=(
                        "Git staged path "
                        "inspection"
                    ),
                )
            ]

            if not staged_paths:
                raise ValueError(
                    "Git commit proposal "
                    "requires staged changes"
                )

            if (
                len(staged_paths)
                > MAX_COMMIT_PATHS
            ):
                raise ValueError(
                    "Git commit proposal exceeds "
                    f"{MAX_COMMIT_PATHS} staged "
                    "path limit"
                )

            for relative in staged_paths:
                if is_sensitive_path(
                    relative
                ):
                    raise PermissionError(
                        "Git commit proposal "
                        "contains a sensitive path"
                    )

            staged_inventory = _call(
                git_path=git_path,
                cwd=repository_root,
                env=env,
                args=[
                    "diff",
                    "--cached",
                    "--raw",
                    "-z",
                    "--no-renames",
                    "--no-ext-diff",
                    "--no-textconv",
                    "HEAD",
                    "--",
                ],
                timeout_ms=8_000,
            )

            _require_complete_success(
                staged_inventory,
                operation=(
                    "Git staged inventory "
                    "inspection"
                ),
            )

            deterministic = {
                "workspace_id": (
                    workspace_id
                ),
                "repository_root": str(
                    repository_root
                ),
                "git_dir": str(
                    git_dir
                ),
                "runner": runner_state,
                "config": config_state,
                "head_file": (
                    head_file_state
                ),
                "head_commit": (
                    head_commit.lower()
                ),
                "head_ref": head_ref,
                "index": index_state,
                "staged_paths": (
                    staged_paths
                ),
                "staged_path_count": (
                    len(staged_paths)
                ),
                (
                    "staged_inventory_sha256"
                ): staged_inventory[
                    "stdout_sha256"
                ],
            }

            return {
                **deterministic,
                "snapshot_sha256": (
                    _canonical_sha256(
                        deterministic
                    )
                ),
            }


    def _mark_stale(
        self,
        payload: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        now = _utc_now()

        payload["status"] = "STALE"
        payload["stale_reason"] = reason
        payload["updated_at"] = now

        payload.setdefault(
            "history",
            [],
        ).append(
            self._event(
                "STALE",
                reason=reason,
            )
        )

        self._save(payload)

        return self._public(
            payload
        )

    @staticmethod
    def _approval_binding(
        *,
        snapshot_sha256: str,
        message: str,
    ) -> str:
        return _canonical_sha256(
            {
                "snapshot_sha256": (
                    snapshot_sha256
                ),
                "message": message,
            }
        )

    def propose(
        self,
        *,
        workspace_id: str,
        message: Any,
    ) -> dict[str, Any]:
        normalized_message = (
            _normalize_message(
                message
            )
        )

        snapshot = self._snapshot(
            workspace_id=workspace_id
        )

        snapshot_sha256 = str(
            snapshot[
                "snapshot_sha256"
            ]
        )

        binding = (
            self._approval_binding(
                snapshot_sha256=(
                    snapshot_sha256
                ),
                message=(
                    normalized_message
                ),
            )
        )

        now = _utc_now()
        commit_id = _commit_id()

        workspace = (
            self.workspaces.get(
                workspace_id
            )
        )

        payload: dict[
            str,
            Any,
        ] = {
            "id": commit_id,
            "protocol": (
                GIT_COMMIT_PROTOCOL
            ),
            "operation": "git.commit",
            "workspace_id": (
                workspace_id
            ),
            "workspace_label": (
                workspace.get(
                    "label"
                )
            ),
            "message": (
                normalized_message
            ),
            "message_sha256": (
                _sha256_bytes(
                    normalized_message.encode(
                        "utf-8"
                    )
                )
            ),
            "status": (
                "AWAITING_APPROVAL"
            ),
            "approval_required": True,
            "commit_performed": False,
            "snapshot_sha256": (
                snapshot_sha256
            ),
            "approval_binding_sha256": (
                binding
            ),
            "head_commit": (
                snapshot[
                    "head_commit"
                ]
            ),
            "head_ref": (
                snapshot[
                    "head_ref"
                ]
            ),
            "staged_paths": list(
                snapshot[
                    "staged_paths"
                ]
            ),
            "staged_path_count": (
                snapshot[
                    "staged_path_count"
                ]
            ),
            "snapshot": snapshot,
            "created_at": now,
            "updated_at": now,
            "history": [
                self._event(
                    "PROPOSED"
                )
            ],
        }

        with self._lock:
            self._save(payload)

        return self._public(
            payload
        )

    def approve(
        self,
        commit_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._load(
                commit_id
            )

            if (
                payload.get(
                    "protocol"
                )
                != GIT_COMMIT_PROTOCOL
            ):
                raise GitCommitStateError(
                    "Legacy or foreign Git "
                    "commit proposal cannot "
                    "be approved"
                )

            if (
                payload.get("status")
                != "AWAITING_APPROVAL"
            ):
                raise GitCommitStateError(
                    "Only AWAITING_APPROVAL "
                    "Git commit proposals "
                    "can be approved"
                )

            stored_message = (
                payload.get("message")
            )

            if not isinstance(
                stored_message,
                str,
            ):
                raise GitCommitStateError(
                    "Git commit proposal "
                    "message is invalid"
                )

            stored_snapshot_sha = str(
                payload.get(
                    "snapshot_sha256"
                )
                or ""
            )

            stored_binding = str(
                payload.get(
                    "approval_binding_sha256"
                )
                or ""
            )

            expected_binding = (
                self._approval_binding(
                    snapshot_sha256=(
                        stored_snapshot_sha
                    ),
                    message=stored_message,
                )
            )

            if (
                stored_binding
                != expected_binding
            ):
                raise GitCommitStateError(
                    "Git commit proposal "
                    "integrity check failed"
                )

            try:
                current = self._snapshot(
                    workspace_id=str(
                        payload[
                            "workspace_id"
                        ]
                    )
                )
            except (
                KeyError,
                ValueError,
                PermissionError,
                FileNotFoundError,
                TimeoutError,
            ) as exc:
                reason = (
                    "Git commit target became "
                    "invalid after proposal: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                public = self._mark_stale(
                    payload,
                    reason,
                )

                raise (
                    GitCommitConflictError(
                        reason,
                        public,
                    )
                ) from exc

            if (
                current[
                    "snapshot_sha256"
                ]
                != stored_snapshot_sha
            ):
                reason = (
                    "Git HEAD, branch, index, "
                    "config, or runner changed "
                    "after proposal"
                )

                public = self._mark_stale(
                    payload,
                    reason,
                )

                raise GitCommitConflictError(
                    reason,
                    public,
                )

            now = _utc_now()

            payload["status"] = (
                "APPROVED"
            )

            payload[
                "approved_at"
            ] = now

            payload[
                "approval_source"
            ] = (
                "local_user_session"
            )

            payload[
                "updated_at"
            ] = now

            payload.setdefault(
                "history",
                [],
            ).append(
                self._event(
                    "APPROVED"
                )
            )

            self._save(payload)

            return self._public(
                payload
            )

    def reject(
        self,
        commit_id: str,
        *,
        reason: Any = None,
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._load(
                commit_id
            )

            if (
                payload.get(
                    "protocol"
                )
                != GIT_COMMIT_PROTOCOL
            ):
                raise GitCommitStateError(
                    "Legacy or foreign Git "
                    "commit proposal cannot "
                    "be rejected"
                )

            if (
                payload.get("status")
                != "AWAITING_APPROVAL"
            ):
                raise GitCommitStateError(
                    "Only AWAITING_APPROVAL "
                    "Git commit proposals "
                    "can be rejected"
                )

            value = str(
                reason or ""
            ).strip()

            now = _utc_now()

            payload["status"] = (
                "REJECTED"
            )

            payload[
                "rejected_at"
            ] = now

            payload[
                "updated_at"
            ] = now

            payload.setdefault(
                "history",
                [],
            ).append(
                self._event(
                    "REJECTED",
                    reason=(
                        value or None
                    ),
                )
            )

            if value:
                payload[
                    "rejection_reason"
                ] = value

            self._save(payload)

            return self._public(
                payload
            )
