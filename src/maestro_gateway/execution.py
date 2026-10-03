from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace import WorkspaceRegistry


EXECUTION_PROTOCOL = "P0.6D.2B"

MAX_EXECUTION_OUTPUT_BYTES = 64_000

PROFILE_TIMEOUT_MS = {
    "python_unittest": 60_000,
    "node_npm_build": 120_000,
    "node_npm_lint": 60_000,
    "node_npm_test": 120_000,
}

_RUN_ID = re.compile(
    r"^RUN-[A-F0-9]{12}$"
)

_ALLOWED_KINDS = {
    "build",
    "test",
    "validation",
}

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".next",
    "dist",
    "build",
    "coverage",
}

SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
}

SENSITIVE_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}

MAX_SNAPSHOT_FILES = 5_000
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 1_048_576


def _now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _sha256_file(path: Path) -> str:
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


def _safe_json_write(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    os.chmod(
        path.parent,
        0o700,
    )

    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")

    temporary = (
        path.parent
        / (
            "."
            + path.name
            + "."
            + secrets.token_hex(6)
            + ".tmp"
        )
    )

    with temporary.open("wb") as handle:
        os.chmod(
            temporary,
            0o600,
        )

        handle.write(data)
        handle.flush()
        os.fsync(
            handle.fileno()
        )

    os.replace(
        temporary,
        path,
    )

    os.chmod(
        path,
        0o600,
    )

    try:
        directory_fd = os.open(
            str(path.parent),
            os.O_DIRECTORY,
        )
    except OSError:
        return

    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


class ExecutionStateError(
    RuntimeError
):
    pass


class ExecutionStaleError(
    RuntimeError
):
    def __init__(
        self,
        message: str,
        proposal: dict[str, Any],
    ):
        super().__init__(message)
        self.proposal = proposal


class ExecutionProfileDetector:
    def __init__(
        self,
        workspaces: WorkspaceRegistry,
    ):
        self._workspaces = workspaces

    def _scope(
        self,
        workspace_id: str,
    ) -> tuple[
        dict[str, Any],
        Path,
    ]:
        row = self._workspaces.get(
            workspace_id
        )

        if not row.get("exists"):
            raise FileNotFoundError(
                str(row.get("path") or "")
            )

        if row.get("kind") != "directory":
            raise ValueError(
                "Governed project execution "
                "requires a directory workspace"
            )

        root = Path(
            str(row["path"])
        ).resolve()

        if not root.is_dir():
            raise ValueError(
                "Authorized execution workspace "
                "must be a directory"
            )

        return row, root

    def _read_package_json(
        self,
        root: Path,
    ) -> dict[str, Any] | None:
        path = root / "package.json"

        if not path.is_file():
            return None

        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError(
                "package.json exceeds "
                "the execution manifest limit"
            )

        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "package.json is not valid UTF-8 JSON"
            ) from exc

        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                "package.json must contain "
                "a JSON object"
            )

        return payload

    def profiles(
        self,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        _, root = self._scope(
            workspace_id
        )

        profiles: list[
            dict[str, Any]
        ] = []

        python_manifest = any(
            (
                root / name
            ).is_file()
            for name in (
                "pyproject.toml",
                "setup.py",
                "setup.cfg",
            )
        )

        test_files = (
            sorted(
                (
                    root / "tests"
                ).rglob("test_*.py")
            )
            if (
                root / "tests"
            ).is_dir()
            else []
        )

        if (
            python_manifest
            and test_files
        ):
            profiles.append(
                {
                    "profile": (
                        "python_unittest"
                    ),
                    "kind": "test",
                    "runner": (
                        "python_unittest"
                    ),
                    "planned_argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "tests",
                        "-v",
                    ],
                    "timeout_ms": (
                        PROFILE_TIMEOUT_MS[
                            "python_unittest"
                        ]
                    ),
                    "project_code_execution": True,
                    "outer_shell": False,
                    "network_isolation": False,
                    "os_filesystem_sandbox": False,
                    "approval_required": True,
                    "risk": "high",
                }
            )

        package = (
            self._read_package_json(
                root
            )
        )

        npm = shutil.which("npm")

        if (
            package is not None
            and npm
        ):
            scripts = (
                package.get("scripts")
                or {}
            )

            if not isinstance(
                scripts,
                dict,
            ):
                raise ValueError(
                    "package.json scripts "
                    "must be an object"
                )

            npm_profiles = (
                (
                    "build",
                    "node_npm_build",
                    "build",
                ),
                (
                    "lint",
                    "node_npm_lint",
                    "validation",
                ),
                (
                    "test",
                    "node_npm_test",
                    "test",
                ),
            )

            for (
                script_name,
                profile_name,
                kind,
            ) in npm_profiles:
                script = scripts.get(
                    script_name
                )

                if not isinstance(
                    script,
                    str,
                ):
                    continue

                script = script.strip()

                if not script:
                    continue

                profiles.append(
                    {
                        "profile": profile_name,
                        "kind": kind,
                        "runner": "npm",
                        "planned_argv": [
                            npm,
                            "run",
                            script_name,
                        ],
                        "project_script": (
                            script
                        ),
                        "timeout_ms": (
                            PROFILE_TIMEOUT_MS[
                                profile_name
                            ]
                        ),
                        "project_code_execution": True,
                        "outer_shell": False,
                        "runner_may_spawn_shell": True,
                        "network_isolation": False,
                        "os_filesystem_sandbox": False,
                        "approval_required": True,
                        "risk": "high",
                    }
                )

        return profiles

    def resolve_profile(
        self,
        workspace_id: str,
        profile: str,
        kind: str,
    ) -> dict[str, Any]:
        profile_name = str(
            profile or ""
        ).strip()

        requested_kind = str(
            kind or ""
        ).strip().lower()

        if (
            requested_kind
            not in _ALLOWED_KINDS
        ):
            raise ValueError(
                "Execution kind must be "
                "build, test, or validation"
            )

        for item in self.profiles(
            workspace_id
        ):
            if (
                item["profile"]
                == profile_name
            ):
                if (
                    item["kind"]
                    != requested_kind
                ):
                    raise ValueError(
                        "Execution profile kind "
                        "does not match request"
                    )

                return dict(item)

        raise ValueError(
            "Requested governed execution "
            f"profile is unavailable: "
            f"{profile_name}"
        )

    def snapshot(
        self,
        workspace_id: str,
    ) -> dict[str, Any]:
        _, root = self._scope(
            workspace_id
        )

        digest = hashlib.sha256()
        count = 0
        total_bytes = 0

        for (
            current,
            directories,
            filenames,
        ) in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(
                current
            )

            filtered: list[str] = []

            for directory in sorted(
                directories
            ):
                path = (
                    current_path
                    / directory
                )

                if (
                    directory
                    in IGNORED_DIRS
                ):
                    continue

                if path.is_symlink():
                    raise PermissionError(
                        "Governed project execution "
                        "does not accept workspace "
                        "symlink directories"
                    )

                filtered.append(
                    directory
                )

            directories[:] = filtered

            for filename in sorted(
                filenames
            ):
                path = (
                    current_path
                    / filename
                )

                if path.is_symlink():
                    raise PermissionError(
                        "Governed project execution "
                        "does not accept workspace "
                        "symlink files"
                    )

                if (
                    filename.casefold()
                    in SENSITIVE_NAMES
                    or path.suffix.casefold()
                    in SENSITIVE_SUFFIXES
                ):
                    raise PermissionError(
                        "Governed project execution "
                        "proposal rejected because "
                        "the workspace contains a "
                        "sensitive credential file"
                    )

                try:
                    relative = (
                        path.relative_to(
                            root
                        )
                    )
                except ValueError as exc:
                    raise PermissionError(
                        "Execution snapshot escaped "
                        "the authorized workspace"
                    ) from exc

                info = path.stat()

                if not stat.S_ISREG(
                    info.st_mode
                ):
                    continue

                count += 1

                if (
                    count
                    > MAX_SNAPSHOT_FILES
                ):
                    raise ValueError(
                        "Execution snapshot exceeds "
                        f"{MAX_SNAPSHOT_FILES} files"
                    )

                total_bytes += (
                    info.st_size
                )

                if (
                    total_bytes
                    > MAX_SNAPSHOT_BYTES
                ):
                    raise ValueError(
                        "Execution snapshot exceeds "
                        f"{MAX_SNAPSHOT_BYTES} bytes"
                    )

                file_sha = (
                    _sha256_file(path)
                )

                digest.update(
                    str(relative).encode(
                        "utf-8"
                    )
                )
                digest.update(b"\0")
                digest.update(
                    str(
                        stat.S_IMODE(
                            info.st_mode
                        )
                    ).encode("ascii")
                )
                digest.update(b"\0")
                digest.update(
                    str(
                        info.st_size
                    ).encode("ascii")
                )
                digest.update(b"\0")
                digest.update(
                    file_sha.encode(
                        "ascii"
                    )
                )
                digest.update(b"\n")

        return {
            "workspace_sha256": (
                digest.hexdigest()
            ),
            "snapshot_files": count,
            "snapshot_bytes": total_bytes,
            "snapshot_policy": (
                "regular_files_except_generated_"
                "protected_directories"
            ),
        }


class _BoundedStreamCollector:
    def __init__(
        self,
        limit: int,
    ):
        self.limit = int(limit)
        self.total_bytes = 0
        self.buffer = bytearray()
        self.digest = hashlib.sha256()
        self.error: str | None = None

    def consume(
        self,
        stream: Any,
    ) -> None:
        try:
            while True:
                block = stream.read(
                    64 * 1024
                )

                if not block:
                    break

                self.total_bytes += len(
                    block
                )

                self.digest.update(
                    block
                )

                remaining = (
                    self.limit
                    - len(self.buffer)
                )

                if remaining > 0:
                    self.buffer.extend(
                        block[:remaining]
                    )

        except Exception as exc:
            self.error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        finally:
            try:
                stream.close()
            except Exception:
                pass

    def result(
        self,
    ) -> dict[str, Any]:
        return {
            "text": bytes(
                self.buffer
            ).decode(
                "utf-8",
                errors="replace",
            ),
            "sha256": (
                self.digest.hexdigest()
            ),
            "bytes": (
                self.total_bytes
            ),
            "truncated": (
                self.total_bytes
                > self.limit
            ),
            "reader_error": (
                self.error
            ),
        }


def _terminate_process_group(
    process: subprocess.Popen[bytes],
) -> bool:
    terminated = False

    if process.poll() is not None:
        return terminated

    try:
        if os.name == "posix":
            os.killpg(
                process.pid,
                signal.SIGTERM,
            )
        else:
            process.terminate()

        terminated = True

    except (
        ProcessLookupError,
        PermissionError,
    ):
        pass

    try:
        process.wait(
            timeout=0.75
        )
        return terminated

    except subprocess.TimeoutExpired:
        pass

    try:
        if os.name == "posix":
            os.killpg(
                process.pid,
                signal.SIGKILL,
            )
        else:
            process.kill()

        terminated = True

    except (
        ProcessLookupError,
        PermissionError,
    ):
        pass

    try:
        process.wait(
            timeout=2.0
        )
    except subprocess.TimeoutExpired:
        pass

    return terminated


def _run_bounded_process(
    *,
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_ms: int,
) -> dict[str, Any]:
    started = time.perf_counter()

    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
        start_new_session=True,
    )

    if (
        process.stdout is None
        or process.stderr is None
    ):
        raise RuntimeError(
            "Execution pipes were not created"
        )

    stdout_collector = (
        _BoundedStreamCollector(
            MAX_EXECUTION_OUTPUT_BYTES
        )
    )

    stderr_collector = (
        _BoundedStreamCollector(
            MAX_EXECUTION_OUTPUT_BYTES
        )
    )

    stdout_thread = threading.Thread(
        target=(
            stdout_collector.consume
        ),
        args=(process.stdout,),
        daemon=True,
    )

    stderr_thread = threading.Thread(
        target=(
            stderr_collector.consume
        ),
        args=(process.stderr,),
        daemon=True,
    )

    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    group_terminated = False

    try:
        process.wait(
            timeout=(
                timeout_ms
                / 1000.0
            )
        )

    except subprocess.TimeoutExpired:
        timed_out = True

        group_terminated = (
            _terminate_process_group(
                process
            )
        )

    stdout_thread.join(
        timeout=3.0
    )

    stderr_thread.join(
        timeout=3.0
    )

    if (
        stdout_thread.is_alive()
        or stderr_thread.is_alive()
    ):
        group_terminated = (
            _terminate_process_group(
                process
            )
            or group_terminated
        )

        stdout_thread.join(
            timeout=1.0
        )

        stderr_thread.join(
            timeout=1.0
        )

    stdout = (
        stdout_collector.result()
    )

    stderr = (
        stderr_collector.result()
    )

    duration_ms = int(
        (
            time.perf_counter()
            - started
        )
        * 1000
    )

    return {
        "exit_code": (
            process.returncode
        ),
        "timed_out": timed_out,
        "process_group_terminated": (
            group_terminated
        ),
        "duration_ms": (
            duration_ms
        ),
        "stdout": stdout["text"],
        "stderr": stderr["text"],
        "stdout_sha256": (
            stdout["sha256"]
        ),
        "stderr_sha256": (
            stderr["sha256"]
        ),
        "stdout_bytes": (
            stdout["bytes"]
        ),
        "stderr_bytes": (
            stderr["bytes"]
        ),
        "stdout_truncated": (
            stdout["truncated"]
        ),
        "stderr_truncated": (
            stderr["truncated"]
        ),
        "stdout_reader_error": (
            stdout["reader_error"]
        ),
        "stderr_reader_error": (
            stderr["reader_error"]
        ),
    }



class ExecutionProposalStore:
    def __init__(
        self,
        root: Path,
        workspaces: WorkspaceRegistry,
    ):
        self.root = Path(
            root
        )

        self.root.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        os.chmod(
            self.root,
            0o700,
        )

        self._workspaces = workspaces
        self._detector = (
            ExecutionProfileDetector(
                workspaces
            )
        )

        self._lock = (
            threading.RLock()
        )

    def _validate_id(
        self,
        run_id: str,
    ) -> str:
        value = str(
            run_id or ""
        ).strip().upper()

        if not _RUN_ID.fullmatch(
            value
        ):
            raise KeyError(value)

        return value

    def _path(
        self,
        run_id: str,
    ) -> Path:
        return (
            self.root
            / f"{run_id}.json"
        )

    def _load(
        self,
        run_id: str,
    ) -> dict[str, Any]:
        value = self._validate_id(
            run_id
        )

        path = self._path(
            value
        )

        if not path.is_file():
            raise KeyError(value)

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    def _save(
        self,
        payload: dict[str, Any],
    ) -> None:
        _safe_json_write(
            self._path(
                payload["id"]
            ),
            payload,
        )

    def _event(
        self,
        action: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "action": action,
            "at": _now(),
        }

        if reason:
            row["reason"] = reason

        return row

    def profiles(
        self,
        workspace_id: str,
    ) -> dict[str, Any]:
        row = self._workspaces.get(
            workspace_id
        )

        return {
            "protocol": (
                EXECUTION_PROTOCOL
            ),
            "workspace_id": row["id"],
            "workspace_label": (
                row["label"]
            ),
            "profiles": (
                self._detector.profiles(
                    workspace_id
                )
            ),
        }

    def list(
        self,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows: list[
                dict[str, Any]
            ] = []

            for path in sorted(
                self.root.glob(
                    "RUN-*.json"
                )
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

                rows.append(payload)

            return sorted(
                rows,
                key=lambda item: (
                    str(
                        item.get(
                            "created_at"
                        )
                        or ""
                    )
                ),
                reverse=True,
            )

    def get(
        self,
        run_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self._load(
                run_id
            )

    def propose(
        self,
        *,
        workspace_id: str,
        kind: str,
        profile: str,
    ) -> dict[str, Any]:
        with self._lock:
            workspace = (
                self._workspaces.get(
                    workspace_id
                )
            )

            definition = (
                self._detector
                .resolve_profile(
                    workspace_id,
                    profile,
                    kind,
                )
            )

            snapshot = (
                self._detector
                .snapshot(
                    workspace_id
                )
            )

            run_id = (
                "RUN-"
                + secrets
                .token_hex(6)
                .upper()
            )

            created = _now()

            payload: dict[
                str,
                Any,
            ] = {
                "id": run_id,
                "protocol": (
                    EXECUTION_PROTOCOL
                ),
                "workspace_id": (
                    workspace["id"]
                ),
                "workspace_label": (
                    workspace["label"]
                ),
                "kind": (
                    definition["kind"]
                ),
                "profile": (
                    definition["profile"]
                ),
                "runner": (
                    definition["runner"]
                ),
                "planned_argv": (
                    definition[
                        "planned_argv"
                    ]
                ),
                "project_script": (
                    definition.get(
                        "project_script"
                    )
                ),
                "timeout_ms": int(
                    definition[
                        "timeout_ms"
                    ]
                ),
                "project_code_execution": (
                    True
                ),
                "outer_shell": False,
                "runner_may_spawn_shell": (
                    bool(
                        definition.get(
                            "runner_may_spawn_shell"
                        )
                    )
                ),
                "network_isolation": False,
                "os_filesystem_sandbox": False,
                "risk": "high",
                "approval_required": True,
                "status": (
                    "AWAITING_APPROVAL"
                ),
                "execution_performed": (
                    False
                ),
                "created_at": created,
                "updated_at": created,
                **snapshot,
                "history": [
                    self._event(
                        "PROPOSED"
                    )
                ],
            }

            self._save(payload)

            return dict(payload)

    def _mark_stale(
        self,
        payload: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        payload["status"] = "STALE"
        payload["stale_reason"] = (
            reason
        )
        payload["updated_at"] = _now()
        payload["history"].append(
            self._event(
                "STALE",
                reason=reason,
            )
        )

        self._save(payload)

        return dict(payload)

    def approve(
        self,
        run_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._load(
                run_id
            )

            if (
                payload["status"]
                != "AWAITING_APPROVAL"
            ):
                raise ExecutionStateError(
                    "Execution proposal must be "
                    "AWAITING_APPROVAL before approval"
                )

            try:
                definition = (
                    self._detector
                    .resolve_profile(
                        payload[
                            "workspace_id"
                        ],
                        payload[
                            "profile"
                        ],
                        payload[
                            "kind"
                        ],
                    )
                )

                snapshot = (
                    self._detector
                    .snapshot(
                        payload[
                            "workspace_id"
                        ]
                    )
                )

            except (
                ValueError,
                PermissionError,
                FileNotFoundError,
            ) as exc:
                stale = (
                    self._mark_stale(
                        payload,
                        str(exc),
                    )
                )

                raise ExecutionStaleError(
                    str(exc),
                    stale,
                ) from exc

            if (
                definition[
                    "planned_argv"
                ]
                != payload[
                    "planned_argv"
                ]
                or definition.get(
                    "project_script"
                )
                != payload.get(
                    "project_script"
                )
                or int(
                    definition[
                        "timeout_ms"
                    ]
                )
                != int(
                    payload[
                        "timeout_ms"
                    ]
                )
                or snapshot[
                    "workspace_sha256"
                ]
                != payload[
                    "workspace_sha256"
                ]
            ):
                reason = (
                    "Execution profile or "
                    "workspace changed after proposal"
                )

                stale = (
                    self._mark_stale(
                        payload,
                        reason,
                    )
                )

                raise ExecutionStaleError(
                    reason,
                    stale,
                )

            approved = _now()

            payload["status"] = (
                "APPROVED"
            )
            payload["approved_at"] = (
                approved
            )
            payload["approval_source"] = (
                "user"
            )
            payload["updated_at"] = (
                approved
            )
            payload["history"].append(
                self._event(
                    "APPROVED"
                )
            )

            self._save(payload)

            return dict(payload)

    def _execution_environment(
        self,
        run_id: str,
        executable: str,
    ) -> tuple[
        dict[str, str],
        Path,
    ]:
        runtime = (
            self.root
            / "_runtime"
            / run_id
        )

        home = runtime / "home"
        temporary = runtime / "tmp"
        npm_cache = (
            runtime / "npm-cache"
        )

        for directory in (
            runtime,
            home,
            temporary,
            npm_cache,
        ):
            directory.mkdir(
                parents=True,
                exist_ok=True,
                mode=0o700,
            )

            os.chmod(
                directory,
                0o700,
            )

        npmrc = runtime / "npmrc"

        if not npmrc.exists():
            npmrc.write_text(
                "",
                encoding="utf-8",
            )

        os.chmod(
            npmrc,
            0o600,
        )

        executable_path = Path(
            executable
        ).expanduser()

        executable_parent = str(
            executable_path.parent
        )

        resolved_executable_parent = str(
            executable_path.resolve().parent
        )

        path_entries: list[str] = []

        for value in (
            executable_parent,
            resolved_executable_parent,
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ):
            if (
                value
                and value
                not in path_entries
            ):
                path_entries.append(
                    value
                )

        environment = {
            "PATH": ":".join(
                path_entries
            ),
            "HOME": str(home),
            "TMPDIR": str(
                temporary
            ),
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "CI": "1",
            "NO_COLOR": "1",
            "PYTHONNOUSERSITE": "1",
            "PIP_CONFIG_FILE": (
                "/dev/null"
            ),
            "npm_config_userconfig": (
                str(npmrc)
            ),
            "npm_config_cache": (
                str(npm_cache)
            ),
            "npm_config_update_notifier": (
                "false"
            ),
            "npm_config_fund": "false",
            "npm_config_audit": "false",
        }

        return (
            environment,
            runtime,
        )

    def _execution_stale_check(
        self,
        payload: dict[str, Any],
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
    ]:
        try:
            definition = (
                self._detector
                .resolve_profile(
                    payload[
                        "workspace_id"
                    ],
                    payload[
                        "profile"
                    ],
                    payload[
                        "kind"
                    ],
                )
            )

            snapshot = (
                self._detector
                .snapshot(
                    payload[
                        "workspace_id"
                    ]
                )
            )

        except (
            ValueError,
            PermissionError,
            FileNotFoundError,
        ) as exc:
            stale = (
                self._mark_stale(
                    payload,
                    str(exc),
                )
            )

            raise ExecutionStaleError(
                str(exc),
                stale,
            ) from exc

        if (
            definition[
                "planned_argv"
            ]
            != payload[
                "planned_argv"
            ]
            or definition.get(
                "project_script"
            )
            != payload.get(
                "project_script"
            )
            or int(
                definition[
                    "timeout_ms"
                ]
            )
            != int(
                payload[
                    "timeout_ms"
                ]
            )
            or snapshot[
                "workspace_sha256"
            ]
            != payload[
                "workspace_sha256"
            ]
        ):
            reason = (
                "Execution profile or "
                "workspace changed after approval"
            )

            stale = (
                self._mark_stale(
                    payload,
                    reason,
                )
            )

            raise ExecutionStaleError(
                reason,
                stale,
            )

        return (
            definition,
            snapshot,
        )

    def _load_active_execution_attempt(
        self,
        run_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        payload = self._load(
            run_id
        )

        if (
            payload.get("status")
            != "EXECUTION_STARTED"
            or payload.get(
                "execution_attempt_id"
            )
            != attempt_id
        ):
            raise ExecutionStateError(
                "Execution attempt state "
                "changed before finalization"
            )

        return payload

    def execute(
        self,
        run_id: str,
        *,
        confirm_execution: bool = False,
    ) -> dict[str, Any]:
        # Phase 1:
        # validate and persist the immutable
        # execution attempt while holding the
        # store lock.
        with self._lock:
            payload = self._load(
                run_id
            )

            if (
                payload.get(
                    "protocol"
                )
                != EXECUTION_PROTOCOL
            ):
                raise ExecutionStateError(
                    "Only P0.6D.2B execution "
                    "proposals may be executed"
                )

            if (
                payload["status"]
                != "APPROVED"
            ):
                raise ExecutionStateError(
                    "Execution proposal must "
                    "be APPROVED before execute"
                )

            if (
                confirm_execution
                is not True
            ):
                raise ValueError(
                    "Execution requires literal "
                    "confirm_execution=true"
                )

            (
                definition,
                before_snapshot,
            ) = (
                self._execution_stale_check(
                    payload
                )
            )

            workspace = (
                self._workspaces.get(
                    payload[
                        "workspace_id"
                    ]
                )
            )

            if (
                not workspace.get(
                    "exists"
                )
                or workspace.get(
                    "kind"
                )
                != "directory"
            ):
                stale = (
                    self._mark_stale(
                        payload,
                        "Execution workspace "
                        "is no longer an "
                        "available directory",
                    )
                )

                raise ExecutionStaleError(
                    stale[
                        "stale_reason"
                    ],
                    stale,
                )

            root = Path(
                str(
                    workspace[
                        "path"
                    ]
                )
            ).resolve()

            if not root.is_dir():
                stale = (
                    self._mark_stale(
                        payload,
                        "Execution workspace "
                        "directory is unavailable",
                    )
                )

                raise ExecutionStaleError(
                    stale[
                        "stale_reason"
                    ],
                    stale,
                )

            started = _now()

            attempt_id = (
                "ATT-"
                + secrets.token_hex(
                    8
                ).upper()
            )

            payload[
                "status"
            ] = "EXECUTION_STARTED"

            payload[
                "execution_attempt_id"
            ] = attempt_id

            payload[
                "execution_started_at"
            ] = started

            payload[
                "execution_source"
            ] = "user"

            payload[
                "execution_performed"
            ] = True

            payload[
                "execution_attempts"
            ] = (
                int(
                    payload.get(
                        "execution_attempts"
                    )
                    or 0
                )
                + 1
            )

            payload[
                "workspace_sha256_before_execution"
            ] = (
                before_snapshot[
                    "workspace_sha256"
                ]
            )

            payload["updated_at"] = (
                started
            )

            payload[
                "history"
            ].append(
                self._event(
                    "EXECUTION_STARTED"
                )
            )

            self._save(payload)

            argv = list(
                definition[
                    "planned_argv"
                ]
            )

            environment, runtime = (
                self._execution_environment(
                    payload["id"],
                    argv[0],
                )
            )

            payload[
                "environment_policy"
            ] = (
                "minimal_no_user_secrets"
            )

            payload[
                "runtime_path"
            ] = str(runtime)

            self._save(payload)

            workspace_id = str(
                payload[
                    "workspace_id"
                ]
            )

            timeout_ms = int(
                definition[
                    "timeout_ms"
                ]
            )

        # Phase 2:
        # project execution is deliberately
        # outside the global proposal-store
        # lock. Read-only get/list operations
        # remain available while it runs.
        try:
            result = (
                _run_bounded_process(
                    argv=argv,
                    cwd=root,
                    env=environment,
                    timeout_ms=timeout_ms,
                )
            )

        except Exception as exc:
            finished = _now()

            with self._lock:
                payload = (
                    self._load_active_execution_attempt(
                        run_id,
                        attempt_id,
                    )
                )

                payload[
                    "status"
                ] = "EXECUTION_ERROR"

                payload[
                    "execution_finished_at"
                ] = finished

                payload[
                    "execution_error"
                ] = (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                payload[
                    "updated_at"
                ] = finished

                payload[
                    "history"
                ].append(
                    self._event(
                        "EXECUTION_ERROR",
                        reason=(
                            payload[
                                "execution_error"
                            ]
                        ),
                    )
                )

                self._save(payload)

                return dict(payload)

        # The post-execution workspace scan can
        # also be comparatively expensive, so
        # it remains outside the store lock.
        post_snapshot_error = None
        after_snapshot = None

        try:
            after_snapshot = (
                self._detector
                .snapshot(
                    workspace_id
                )
            )

        except Exception as exc:
            post_snapshot_error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        if result[
            "timed_out"
        ]:
            final_status = (
                "TIMED_OUT"
            )

        elif (
            result[
                "exit_code"
            ]
            == 0
        ):
            final_status = (
                "PASSED"
            )

        else:
            final_status = (
                "FAILED"
            )

        finished = _now()

        # Phase 3:
        # reacquire the lock and prove this is
        # still the same EXECUTION_STARTED
        # attempt before writing any result.
        with self._lock:
            payload = (
                self._load_active_execution_attempt(
                    run_id,
                    attempt_id,
                )
            )

            payload.update(
                {
                    "exit_code": (
                        result[
                            "exit_code"
                        ]
                    ),
                    "timed_out": (
                        result[
                            "timed_out"
                        ]
                    ),
                    "process_group_terminated": (
                        result[
                            "process_group_terminated"
                        ]
                    ),
                    "duration_ms": (
                        result[
                            "duration_ms"
                        ]
                    ),
                    "stdout": (
                        result[
                            "stdout"
                        ]
                    ),
                    "stderr": (
                        result[
                            "stderr"
                        ]
                    ),
                    "stdout_sha256": (
                        result[
                            "stdout_sha256"
                        ]
                    ),
                    "stderr_sha256": (
                        result[
                            "stderr_sha256"
                        ]
                    ),
                    "stdout_bytes": (
                        result[
                            "stdout_bytes"
                        ]
                    ),
                    "stderr_bytes": (
                        result[
                            "stderr_bytes"
                        ]
                    ),
                    "stdout_truncated": (
                        result[
                            "stdout_truncated"
                        ]
                    ),
                    "stderr_truncated": (
                        result[
                            "stderr_truncated"
                        ]
                    ),
                    "stdout_reader_error": (
                        result[
                            "stdout_reader_error"
                        ]
                    ),
                    "stderr_reader_error": (
                        result[
                            "stderr_reader_error"
                        ]
                    ),
                }
            )

            if (
                after_snapshot
                is not None
            ):
                payload[
                    "workspace_sha256_after_execution"
                ] = (
                    after_snapshot[
                        "workspace_sha256"
                    ]
                )

                payload[
                    "workspace_changed_during_execution"
                ] = (
                    after_snapshot[
                        "workspace_sha256"
                    ]
                    != before_snapshot[
                        "workspace_sha256"
                    ]
                )

            else:
                payload[
                    "workspace_sha256_after_execution"
                ] = None

                payload[
                    "workspace_changed_during_execution"
                ] = True

                payload[
                    "post_snapshot_error"
                ] = (
                    post_snapshot_error
                )

            payload["status"] = (
                final_status
            )

            payload[
                "execution_finished_at"
            ] = finished

            payload["updated_at"] = (
                finished
            )

            payload[
                "history"
            ].append(
                self._event(
                    final_status
                )
            )

            self._save(payload)

            return dict(payload)

    def reject(
        self,
        run_id: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._load(
                run_id
            )

            if payload["status"] not in {
                "AWAITING_APPROVAL",
                "APPROVED",
            }:
                raise ExecutionStateError(
                    "Only pending or approved "
                    "execution proposals may "
                    "be rejected"
                )

            rejected = _now()

            payload["status"] = (
                "REJECTED"
            )
            payload["rejected_at"] = (
                rejected
            )
            payload[
                "rejection_reason"
            ] = (
                str(reason).strip()
                if reason
                else None
            )
            payload["updated_at"] = (
                rejected
            )
            payload["history"].append(
                self._event(
                    "REJECTED",
                    reason=(
                        payload[
                            "rejection_reason"
                        ]
                    ),
                )
            )

            self._save(payload)

            return dict(payload)
