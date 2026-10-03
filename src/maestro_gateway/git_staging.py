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
from .workspace import WorkspaceRegistry


GIT_STAGE_PROTOCOL = "P0.6E.2A"
GIT_STAGE_MODE = "proposal_approval_only"

MAX_STAGE_PATHS = 32
MAX_STAGE_PATH_CHARS = 512

_STAGE_ID = re.compile(
    r"^GST-[A-F0-9]{12}$"
)

_TRUSTED_GIT_CANDIDATES = (
    Path("/usr/bin/git"),
    Path("/opt/homebrew/bin/git"),
    Path("/usr/local/bin/git"),
)


class GitStageConflictError(
    RuntimeError
):
    def __init__(
        self,
        message: str,
        proposal: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.proposal = proposal


class GitStageStateError(
    RuntimeError
):
    pass


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _stage_id() -> str:
    return (
        "GST-"
        + uuid.uuid4().hex[
            :12
        ].upper()
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


def _trusted_git_path() -> Path:
    for candidate in (
        _TRUSTED_GIT_CANDIDATES
    ):
        if (
            candidate.exists()
            and candidate.is_file()
        ):
            resolved = (
                candidate.resolve()
            )

            if resolved.is_file():
                return candidate

    raise FileNotFoundError(
        "No trusted Git executable "
        "was found for governed staging"
    )


def _file_state(
    path: Path,
    *,
    display: str,
) -> dict[str, Any]:
    if path.is_symlink():
        raise PermissionError(
            f"Symlink rejected: {display}"
        )

    if not path.exists():
        return {
            "path": display,
            "exists": False,
            "sha256": None,
            "size": 0,
            "mode": None,
        }

    info = path.lstat()

    if not stat.S_ISREG(
        info.st_mode
    ):
        raise ValueError(
            f"Regular file required: "
            f"{display}"
        )

    return {
        "path": display,
        "exists": True,
        "sha256": _sha256_file(
            path
        ),
        "size": int(
            info.st_size
        ),
        "mode": stat.S_IMODE(
            info.st_mode
        ),
    }


def _normalize_paths(
    paths: Any,
) -> list[str]:
    if not isinstance(paths, list):
        raise ValueError(
            "paths must be a list"
        )

    if not paths:
        raise ValueError(
            "At least one explicit "
            "Git staging path is required"
        )

    if len(paths) > MAX_STAGE_PATHS:
        raise ValueError(
            f"At most {MAX_STAGE_PATHS} "
            "staging paths are allowed"
        )

    normalized: list[str] = []

    for item in paths:
        if not isinstance(item, str):
            raise ValueError(
                "Every staging path "
                "must be a string"
            )

        value = item.strip()

        if (
            not value
            or "\x00" in value
        ):
            raise ValueError(
                "Staging paths must be "
                "non-empty strings"
            )

        if (
            len(value)
            > MAX_STAGE_PATH_CHARS
        ):
            raise ValueError(
                "Staging path exceeds "
                "the size limit"
            )

        path = Path(value)

        if path.is_absolute():
            raise PermissionError(
                "Absolute staging paths "
                "are not allowed"
            )

        if ".." in path.parts:
            raise PermissionError(
                "Staging path escapes "
                "the authorized workspace"
            )

        value = path.as_posix()

        if value in {
            "",
            ".",
            "/",
        }:
            raise ValueError(
                "Staging requires "
                "explicit file paths; "
                "repository-wide staging "
                "is forbidden"
            )

        if value not in normalized:
            normalized.append(value)

    return normalized


def _stage_kind(
    raw_status: str,
) -> str:
    if not raw_status:
        raise ValueError(
            "Path has no unstaged "
            "change to stage"
        )

    first = raw_status.split(
        "\x00",
        1,
    )[0]

    if first.startswith("? "):
        return "untracked"

    if first.startswith("! "):
        raise PermissionError(
            "Ignored files cannot be "
            "staged in P0.6E.2A"
        )

    if first.startswith("u "):
        raise ValueError(
            "Unmerged/conflicted paths "
            "cannot be staged in P0.6E.2A"
        )

    if first.startswith("2 "):
        raise ValueError(
            "Rename/copy staging is "
            "not supported in P0.6E.2A"
        )

    if first.startswith("1 "):
        fields = first.split(
            " ",
            2,
        )

        if len(fields) < 2:
            raise ValueError(
                "Unexpected Git status "
                "record"
            )

        xy = fields[1]

        if (
            len(xy) != 2
            or xy[1] == "."
        ):
            raise ValueError(
                "Path has no unstaged "
                "change to stage"
            )

        if xy[1] == "D":
            return "deleted"

        if xy[1] == "T":
            return "type_changed"

        return "modified"

    raise ValueError(
        "Unsupported Git status "
        "for governed staging"
    )


class GitStageProposalStore:
    """
    P0.6E.2A governed Git staging ledger.

    Proposal creation and approval are
    strictly index-mutation-free.

    There is intentionally no staging
    apply implementation in P0.6E.2A.
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
        stage_id: str,
    ) -> str:
        value = str(
            stage_id or ""
        ).strip().upper()

        if not _STAGE_ID.fullmatch(
            value
        ):
            raise ValueError(
                "Invalid Git staging "
                "proposal id"
            )

        return value

    def _path(
        self,
        stage_id: str,
    ) -> Path:
        return (
            self.root
            / (
                self._validate_id(
                    stage_id
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
            with tmp.open(
                "xb"
            ) as handle:
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
        stage_id: str,
    ) -> dict[str, Any]:
        path = self._path(
            stage_id
        )

        if not path.exists():
            raise KeyError(
                self._validate_id(
                    stage_id
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
                "Corrupted Git staging "
                "proposal"
            ) from exc

        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                "Corrupted Git staging "
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
                "GST-*.json"
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
        stage_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self._public(
                self._load(
                    stage_id
                )
            )

    def _snapshot(
        self,
        *,
        workspace_id: str,
        paths: list[str],
    ) -> dict[str, Any]:
        workspace = (
            self.workspaces.get(
                workspace_id
            )
        )

        if (
            workspace.get("kind")
            != "directory"
        ):
            raise ValueError(
                "Git staging requires "
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

        git_path = (
            _trusted_git_path()
        )

        resolved_git_path = (
            git_path.resolve()
        )

        runner_state = (
            _file_state(
                resolved_git_path,
                display=str(
                    resolved_git_path
                ),
            )
        )

        with tempfile.TemporaryDirectory(
            prefix=(
                "maestro-git-stage-"
            )
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

            _require_success(
                git_dir_result,
                operation=(
                    "Git directory "
                    "detection"
                ),
            )

            git_dir = Path(
                git_dir_result[
                    "stdout"
                ].strip()
            ).resolve()

            try:
                git_dir.relative_to(
                    repository_root
                )
            except ValueError as exc:
                raise PermissionError(
                    "Git metadata escapes "
                    "the authorized "
                    "workspace"
                ) from exc

            index_path = (
                git_dir / "index"
            )

            if not index_path.exists():
                raise ValueError(
                    "P0.6E.2A requires "
                    "an existing Git index"
                )

            index_state = (
                _file_state(
                    index_path,
                    display=".git/index",
                )
            )

            config_path = (
                git_dir / "config"
            )

            config_state = (
                _file_state(
                    config_path,
                    display=".git/config",
                )
            )

            includes = _call(
                git_path=git_path,
                cwd=repository_root,
                env=env,
                args=[
                    "config",
                    "--local",
                    "--get-regexp",
                    (
                        "^include.*"
                        "\\.path$"
                    ),
                ],
                timeout_ms=3_000,
            )

            if (
                includes.get(
                    "timed_out"
                )
            ):
                raise TimeoutError(
                    "Git config include "
                    "inspection timed out"
                )

            if (
                includes.get(
                    "exit_code"
                )
                not in {
                    0,
                    1,
                }
            ):
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
                    "Repository-local Git "
                    "config includes are "
                    "not allowed for "
                    "governed staging"
                )

            attributes_file = (
                _call(
                    git_path=git_path,
                    cwd=repository_root,
                    env=env,
                    args=[
                        "config",
                        "--local",
                        "--get",
                        (
                            "core."
                            "attributesfile"
                        ),
                    ],
                    timeout_ms=3_000,
                )
            )

            if (
                attributes_file.get(
                    "timed_out"
                )
            ):
                raise TimeoutError(
                    "Git attributes config "
                    "inspection timed out"
                )

            if (
                attributes_file.get(
                    "exit_code"
                )
                not in {
                    0,
                    1,
                }
            ):
                _require_success(
                    attributes_file,
                    operation=(
                        "Git attributes "
                        "config inspection"
                    ),
                )

            if (
                attributes_file.get(
                    "exit_code"
                )
                == 0
                and str(
                    attributes_file.get(
                        "stdout"
                    )
                    or ""
                ).strip()
            ):
                raise PermissionError(
                    "External "
                    "core.attributesFile "
                    "is not allowed for "
                    "governed staging"
                )

            targets: list[
                dict[str, Any]
            ] = []

            attribute_candidates: dict[
                str,
                tuple[Path, str],
            ] = {}

            attribute_candidates[
                str(
                    repository_root
                    / ".gitattributes"
                )
            ] = (
                repository_root
                / ".gitattributes",
                ".gitattributes",
            )

            info_attributes = (
                git_dir
                / "info"
                / "attributes"
            )

            attribute_candidates[
                str(info_attributes)
            ] = (
                info_attributes,
                ".git/info/attributes",
            )

            for relative in paths:
                target_info = (
                    self.workspaces
                    .resolve_change_target(
                        workspace_id,
                        relative,
                    )
                )

                target = Path(
                    str(
                        target_info[
                            "absolute_path"
                        ]
                    )
                )

                worktree_state = (
                    _file_state(
                        target,
                        display=relative,
                    )
                )

                parent = (
                    repository_root
                )

                rel_parent = Path(
                    relative
                ).parent

                if str(
                    rel_parent
                ) not in {
                    "",
                    ".",
                }:
                    for part in (
                        rel_parent.parts
                    ):
                        parent = (
                            parent / part
                        )

                        candidate = (
                            parent
                            / ".gitattributes"
                        )

                        display = str(
                            candidate.relative_to(
                                repository_root
                            )
                        )

                        attribute_candidates[
                            str(candidate)
                        ] = (
                            candidate,
                            display,
                        )

                filter_result = (
                    _call(
                        git_path=git_path,
                        cwd=repository_root,
                        env=env,
                        args=[
                            "check-attr",
                            "-z",
                            "filter",
                            "--",
                            relative,
                        ],
                        timeout_ms=3_000,
                    )
                )

                _require_success(
                    filter_result,
                    operation=(
                        "Git filter "
                        "attribute inspection"
                    ),
                )

                filter_parts = (
                    str(
                        filter_result.get(
                            "stdout"
                        )
                        or ""
                    )
                    .split("\x00")
                )

                filter_value = (
                    filter_parts[2]
                    if len(
                        filter_parts
                    )
                    >= 3
                    else ""
                )

                if filter_value not in {
                    "",
                    "unspecified",
                    "unset",
                }:
                    raise PermissionError(
                        "External Git clean "
                        "filters are not "
                        "allowed for "
                        "governed staging"
                    )

                status_result = (
                    _call(
                        git_path=git_path,
                        cwd=repository_root,
                        env=env,
                        args=[
                            "status",
                            "--porcelain=v2",
                            "-z",
                            (
                                "--untracked-"
                                "files=all"
                            ),
                            "--",
                            relative,
                        ],
                        timeout_ms=5_000,
                    )
                )

                _require_success(
                    status_result,
                    operation=(
                        "Git path status "
                        "inspection"
                    ),
                )

                stage_kind = (
                    _stage_kind(
                        str(
                            status_result[
                                "stdout"
                            ]
                        )
                    )
                )

                index_entry = _call(
                    git_path=git_path,
                    cwd=repository_root,
                    env=env,
                    args=[
                        "ls-files",
                        "--stage",
                        "-z",
                        "--",
                        relative,
                    ],
                    timeout_ms=3_000,
                )

                _require_success(
                    index_entry,
                    operation=(
                        "Git index entry "
                        "inspection"
                    ),
                )

                targets.append(
                    {
                        "path": relative,
                        "stage_kind": (
                            stage_kind
                        ),
                        "worktree": (
                            worktree_state
                        ),
                        (
                            "index_entry_"
                            "sha256"
                        ): index_entry[
                            "stdout_sha256"
                        ],
                        (
                            "status_"
                            "sha256"
                        ): status_result[
                            "stdout_sha256"
                        ],
                        "filter": (
                            filter_value
                            or "unspecified"
                        ),
                    }
                )

            attributes: list[
                dict[str, Any]
            ] = []

            for (
                _,
                (
                    candidate,
                    display,
                ),
            ) in sorted(
                attribute_candidates
                .items(),
                key=lambda item: (
                    item[1][1]
                ),
            ):
                attributes.append(
                    _file_state(
                        candidate,
                        display=display,
                    )
                )

            deterministic = {
                "workspace_id": (
                    workspace_id
                ),
                "repository_root": str(
                    repository_root
                ),
                "runner": {
                    "path": str(
                        git_path
                    ),
                    "resolved_path": str(
                        resolved_git_path
                    ),
                    "sha256": (
                        runner_state[
                            "sha256"
                        ]
                    ),
                    "size": (
                        runner_state[
                            "size"
                        ]
                    ),
                },
                "index": index_state,
                "config": config_state,
                "attributes": (
                    attributes
                ),
                "targets": targets,
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
        payload["updated_at"] = now
        payload[
            "stale_reason"
        ] = reason

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

    def propose(
        self,
        *,
        workspace_id: str,
        paths: Any,
    ) -> dict[str, Any]:
        normalized = (
            _normalize_paths(
                paths
            )
        )

        snapshot = self._snapshot(
            workspace_id=workspace_id,
            paths=normalized,
        )

        now = _utc_now()
        stage_id = _stage_id()

        workspace = (
            self.workspaces.get(
                workspace_id
            )
        )

        payload: dict[
            str,
            Any,
        ] = {
            "id": stage_id,
            "protocol": (
                GIT_STAGE_PROTOCOL
            ),
            "operation": "git.stage",
            "workspace_id": (
                workspace_id
            ),
            "workspace_label": (
                workspace.get(
                    "label"
                )
            ),
            "paths": normalized,
            "status": (
                "AWAITING_APPROVAL"
            ),
            "approval_required": True,
            "stage_performed": False,
            "snapshot_sha256": (
                snapshot[
                    "snapshot_sha256"
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
        stage_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._load(
                stage_id
            )

            if (
                payload.get(
                    "protocol"
                )
                != GIT_STAGE_PROTOCOL
            ):
                raise GitStageStateError(
                    "Legacy or foreign "
                    "Git staging proposal "
                    "cannot be approved"
                )

            if (
                payload.get("status")
                != "AWAITING_APPROVAL"
            ):
                raise GitStageStateError(
                    "Only "
                    "AWAITING_APPROVAL "
                    "Git staging proposals "
                    "can be approved"
                )

            try:
                current = (
                    self._snapshot(
                        workspace_id=str(
                            payload[
                                "workspace_id"
                            ]
                        ),
                        paths=list(
                            payload[
                                "paths"
                            ]
                        ),
                    )
                )

            except (
                ValueError,
                PermissionError,
                FileNotFoundError,
                TimeoutError,
            ) as exc:
                reason = (
                    "Git staging target "
                    "became invalid after "
                    "proposal: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                public = (
                    self._mark_stale(
                        payload,
                        reason,
                    )
                )

                raise (
                    GitStageConflictError(
                        reason,
                        public,
                    )
                ) from exc

            if (
                current[
                    "snapshot_sha256"
                ]
                != payload.get(
                    "snapshot_sha256"
                )
            ):
                reason = (
                    "Git index, worktree, "
                    "attributes, config, "
                    "or runner changed "
                    "after proposal"
                )

                public = (
                    self._mark_stale(
                        payload,
                        reason,
                    )
                )

                raise GitStageConflictError(
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
        stage_id: str,
        *,
        reason: Any = None,
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._load(
                stage_id
            )

            if (
                payload.get("status")
                != "AWAITING_APPROVAL"
            ):
                raise GitStageStateError(
                    "Only "
                    "AWAITING_APPROVAL "
                    "Git staging proposals "
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

    def apply(
        self,
        stage_id: str,
    ) -> dict[str, Any]:
        del stage_id

        raise GitStageStateError(
            "Git staging apply is "
            "intentionally unavailable "
            "in P0.6E.2A"
        )
