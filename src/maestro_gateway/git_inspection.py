from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .sensitive_policy import (
    is_sensitive_path,
)


GIT_PROTOCOL = "P0.6E.1"
GIT_MODE = "inspection_only"

MAX_GIT_OUTPUT_BYTES = 64_000

_ALLOWED_OPERATIONS = {
    "git.status",
    "git.diff",
    "git.log",
    "git.branch_current",
}

_TIMEOUT_MS = {
    "git.status": 5_000,
    "git.diff": 8_000,
    "git.log": 5_000,
    "git.branch_current": 3_000,
}


class _BoundedCollector:
    def __init__(
        self,
        limit: int,
    ) -> None:
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


def _terminate_group(
    process: subprocess.Popen[bytes],
) -> bool:
    if process.poll() is not None:
        return False

    terminated = False

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
            timeout=0.5
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
            timeout=1.0
        )
    except subprocess.TimeoutExpired:
        pass

    return terminated


def _run(
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
            "Git inspection pipes "
            "were not created"
        )

    stdout_collector = (
        _BoundedCollector(
            MAX_GIT_OUTPUT_BYTES
        )
    )

    stderr_collector = (
        _BoundedCollector(
            MAX_GIT_OUTPUT_BYTES
        )
    )

    stdout_thread = threading.Thread(
        target=stdout_collector.consume,
        args=(process.stdout,),
        daemon=True,
    )

    stderr_thread = threading.Thread(
        target=stderr_collector.consume,
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
            _terminate_group(
                process
            )
        )

    stdout_thread.join(
        timeout=2.0
    )

    stderr_thread.join(
        timeout=2.0
    )

    if (
        stdout_thread.is_alive()
        or stderr_thread.is_alive()
    ):
        group_terminated = (
            _terminate_group(
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
        "argv": list(argv),
        "shell": False,
        "stdin": "disabled",
        "exit_code": (
            process.returncode
        ),
        "timed_out": timed_out,
        "process_group_terminated": (
            group_terminated
        ),
        "duration_ms": duration_ms,
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


def _git_path() -> Path:
    value = shutil.which("git")

    if not value:
        raise FileNotFoundError(
            "Git executable is not available"
        )

    path = Path(
        value
    ).expanduser()

    if not path.is_absolute():
        path = path.absolute()

    return path


def _environment(
    *,
    git_path: Path,
    private_home: Path,
) -> dict[str, str]:
    lexical_parent = str(
        git_path.parent
    )

    resolved_parent = str(
        git_path.resolve().parent
    )

    path_entries: list[str] = []

    for value in (
        lexical_parent,
        resolved_parent,
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ):
        if (
            value
            and value not in path_entries
        ):
            path_entries.append(
                value
            )

    return {
        "PATH": ":".join(
            path_entries
        ),
        "HOME": str(
            private_home
        ),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "TERM": "dumb",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _base_argv(
    git_path: Path,
) -> list[str]:
    return [
        str(git_path),
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "maintenance.auto=false",
        "-c",
        "gc.auto=0",
    ]


def _call(
    *,
    git_path: Path,
    cwd: Path,
    env: dict[str, str],
    args: list[str],
    timeout_ms: int,
) -> dict[str, Any]:
    return _run(
        argv=(
            _base_argv(
                git_path
            )
            + args
        ),
        cwd=cwd,
        env=env,
        timeout_ms=timeout_ms,
    )


def _error_message(
    result: dict[str, Any],
) -> str:
    stderr = str(
        result.get("stderr")
        or ""
    ).strip()

    stdout = str(
        result.get("stdout")
        or ""
    ).strip()

    message = (
        stderr
        or stdout
        or "Git inspection failed"
    )

    return message[:2000]


def _require_success(
    result: dict[str, Any],
    *,
    operation: str,
) -> None:
    if result.get(
        "timed_out"
    ):
        raise TimeoutError(
            f"{operation} timed out"
        )

    if result.get(
        "exit_code"
    ) != 0:
        raise ValueError(
            f"{operation} failed: "
            + _error_message(
                result
            )
        )


def _confined_repo(
    *,
    workspace_root: Path,
    git_path: Path,
    env: dict[str, str],
) -> tuple[
    Path,
    list[dict[str, Any]],
]:
    root = (
        Path(workspace_root)
        .expanduser()
        .resolve()
    )

    if not root.is_dir():
        raise ValueError(
            "Git inspection requires "
            "a directory workspace"
        )

    evidence: list[
        dict[str, Any]
    ] = []

    inside = _call(
        git_path=git_path,
        cwd=root,
        env=env,
        args=[
            "rev-parse",
            "--is-inside-work-tree",
        ],
        timeout_ms=3_000,
    )

    evidence.append(inside)

    _require_success(
        inside,
        operation=(
            "git repository detection"
        ),
    )

    if (
        inside["stdout"].strip()
        != "true"
    ):
        raise ValueError(
            "Authorized workspace is "
            "not a Git work tree"
        )

    top = _call(
        git_path=git_path,
        cwd=root,
        env=env,
        args=[
            "rev-parse",
            "--show-toplevel",
        ],
        timeout_ms=3_000,
    )

    evidence.append(top)

    _require_success(
        top,
        operation=(
            "git repository root detection"
        ),
    )

    repository_root = Path(
        top["stdout"].strip()
    ).resolve()

    if repository_root != root:
        raise PermissionError(
            "Authorized workspace must "
            "be the Git repository root"
        )

    git_dir_result = _call(
        git_path=git_path,
        cwd=root,
        env=env,
        args=[
            "rev-parse",
            "--git-dir",
        ],
        timeout_ms=3_000,
    )

    evidence.append(
        git_dir_result
    )

    _require_success(
        git_dir_result,
        operation=(
            "git metadata detection"
        ),
    )

    git_dir_value = (
        git_dir_result[
            "stdout"
        ].strip()
    )

    git_dir = Path(
        git_dir_value
    )

    if not git_dir.is_absolute():
        git_dir = (
            root / git_dir
        )

    git_dir = git_dir.resolve()

    if not git_dir.is_relative_to(
        root
    ):
        raise PermissionError(
            "Git metadata escapes "
            "the authorized workspace"
        )

    return (
        root,
        evidence,
    )


def _evidence(
    *,
    operation: str,
    commands: list[
        dict[str, Any]
    ],
) -> dict[str, Any]:
    return {
        "protocol": GIT_PROTOCOL,
        "mode": GIT_MODE,
        "operation": operation,
        "shell": False,
        "network_operation": False,
        "arbitrary_argv": False,
        "global_git_config": (
            "disabled"
        ),
        "system_git_config": (
            "disabled"
        ),
        "optional_locks": False,
        "commands": commands,
    }


def inspect_git(
    *,
    workspace_root: Path,
    operation: str,
    options: dict[str, Any]
    | None = None,
) -> dict[str, Any]:
    operation = str(
        operation
    ).strip()

    if operation not in (
        _ALLOWED_OPERATIONS
    ):
        raise ValueError(
            "Git operation is not "
            "available in P0.6E.1"
        )

    options = dict(
        options or {}
    )

    git_path = _git_path()

    with tempfile.TemporaryDirectory(
        prefix="maestro-git-"
    ) as td:
        private_home = Path(td)

        os.chmod(
            private_home,
            0o700,
        )

        env = _environment(
            git_path=git_path,
            private_home=private_home,
        )

        (
            root,
            commands,
        ) = _confined_repo(
            workspace_root=(
                workspace_root
            ),
            git_path=git_path,
            env=env,
        )

        if operation == "git.status":
            result = _call(
                git_path=git_path,
                cwd=root,
                env=env,
                args=[
                    "status",
                    "--porcelain=v2",
                    "--branch",
                    "--untracked-files=all",
                ],
                timeout_ms=(
                    _TIMEOUT_MS[
                        operation
                    ]
                ),
            )

            commands.append(result)

            _require_success(
                result,
                operation=operation,
            )

            return {
                "protocol": (
                    GIT_PROTOCOL
                ),
                "operation": (
                    operation
                ),
                "repository_root": str(
                    root
                ),
                "porcelain_v2": (
                    result["stdout"]
                ),
                "execution_evidence": (
                    _evidence(
                        operation=(
                            operation
                        ),
                        commands=commands,
                    )
                ),
            }

        if operation == "git.diff":
            scope = str(
                options.get(
                    "scope"
                )
                or "worktree"
            )

            if scope not in {
                "worktree",
                "staged",
            }:
                raise ValueError(
                    "git.diff scope must "
                    "be worktree or staged"
                )

            # First discover only the changed
            # repository-relative path names.
            #
            # No patch content is requested at
            # this stage. Sensitive paths must
            # be rejected before a content-
            # producing `git diff` is allowed.
            preflight_args = [
                "diff",
                "--name-only",
                "-z",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
            ]

            if scope == "staged":
                preflight_args.append(
                    "--cached"
                )

            preflight_args.append("--")

            preflight = _call(
                git_path=git_path,
                cwd=root,
                env=env,
                args=preflight_args,
                timeout_ms=(
                    _TIMEOUT_MS[
                        operation
                    ]
                ),
            )

            commands.append(
                preflight
            )

            _require_success(
                preflight,
                operation=(
                    "git.diff path preflight"
                ),
            )

            if preflight.get(
                "stdout_truncated"
            ):
                raise PermissionError(
                    "git.diff content access "
                    "denied because changed "
                    "path discovery exceeded "
                    "the safety limit"
                )

            changed_paths = [
                value
                for value
                in str(
                    preflight.get(
                        "stdout"
                    )
                    or ""
                ).split("\x00")
                if value
            ]

            if any(
                is_sensitive_path(
                    Path(value)
                )
                for value
                in changed_paths
            ):
                # Do not reveal the sensitive
                # path in the exception and do
                # not execute a content diff.
                raise PermissionError(
                    "git.diff content access "
                    "to sensitive credential "
                    "paths is denied"
                )

            args = [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
            ]

            if scope == "staged":
                args.append(
                    "--cached"
                )

            args.append("--")

            result = _call(
                git_path=git_path,
                cwd=root,
                env=env,
                args=args,
                timeout_ms=(
                    _TIMEOUT_MS[
                        operation
                    ]
                ),
            )

            commands.append(result)

            _require_success(
                result,
                operation=operation,
            )

            return {
                "protocol": (
                    GIT_PROTOCOL
                ),
                "operation": (
                    operation
                ),
                "repository_root": str(
                    root
                ),
                "scope": scope,
                "diff": (
                    result["stdout"]
                ),
                "truncated": (
                    result[
                        "stdout_truncated"
                    ]
                ),
                "execution_evidence": (
                    _evidence(
                        operation=(
                            operation
                        ),
                        commands=commands,
                    )
                ),
            }

        if (
            operation
            == "git.branch_current"
        ):
            branch_result = _call(
                git_path=git_path,
                cwd=root,
                env=env,
                args=[
                    "symbolic-ref",
                    "--quiet",
                    "--short",
                    "HEAD",
                ],
                timeout_ms=(
                    _TIMEOUT_MS[
                        operation
                    ]
                ),
            )

            commands.append(
                branch_result
            )

            if branch_result.get(
                "timed_out"
            ):
                raise TimeoutError(
                    "git.branch_current "
                    "timed out"
                )

            if (
                branch_result.get(
                    "exit_code"
                )
                not in {
                    0,
                    1,
                }
            ):
                raise ValueError(
                    "git.branch_current "
                    "failed: "
                    + _error_message(
                        branch_result
                    )
                )

            branch = (
                branch_result[
                    "stdout"
                ].strip()
                or None
            )

            head_result = _call(
                git_path=git_path,
                cwd=root,
                env=env,
                args=[
                    "rev-parse",
                    "--verify",
                    "HEAD",
                ],
                timeout_ms=(
                    _TIMEOUT_MS[
                        operation
                    ]
                ),
            )

            commands.append(
                head_result
            )

            if head_result.get(
                "timed_out"
            ):
                raise TimeoutError(
                    "git.branch_current "
                    "HEAD lookup timed out"
                )

            head = None

            if (
                head_result.get(
                    "exit_code"
                )
                == 0
            ):
                head = (
                    head_result[
                        "stdout"
                    ].strip()
                    or None
                )

            return {
                "protocol": (
                    GIT_PROTOCOL
                ),
                "operation": (
                    operation
                ),
                "repository_root": str(
                    root
                ),
                "branch": branch,
                "head": head,
                "detached": (
                    branch is None
                    and head is not None
                ),
                "execution_evidence": (
                    _evidence(
                        operation=(
                            operation
                        ),
                        commands=commands,
                    )
                ),
            }

        # git.log
        raw_max_count = options.get(
            "max_count",
            10,
        )

        if isinstance(
            raw_max_count,
            bool,
        ):
            raise ValueError(
                "git.log max_count "
                "must be an integer"
            )

        try:
            max_count = int(
                raw_max_count
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "git.log max_count "
                "must be an integer"
            ) from exc

        if not 1 <= max_count <= 50:
            raise ValueError(
                "git.log max_count "
                "must be between 1 and 50"
            )

        head_check = _call(
            git_path=git_path,
            cwd=root,
            env=env,
            args=[
                "rev-parse",
                "--verify",
                "HEAD",
            ],
            timeout_ms=3_000,
        )

        commands.append(
            head_check
        )

        if head_check.get(
            "timed_out"
        ):
            raise TimeoutError(
                "git.log HEAD lookup "
                "timed out"
            )

        if (
            head_check.get(
                "exit_code"
            )
            != 0
        ):
            return {
                "protocol": (
                    GIT_PROTOCOL
                ),
                "operation": (
                    operation
                ),
                "repository_root": str(
                    root
                ),
                "commits": [],
                "execution_evidence": (
                    _evidence(
                        operation=(
                            operation
                        ),
                        commands=commands,
                    )
                ),
            }

        result = _call(
            git_path=git_path,
            cwd=root,
            env=env,
            args=[
                "log",
                "--no-decorate",
                f"--max-count={max_count}",
                "--format="
                "%H%x1f%h%x1f%aI%x1f%s",
            ],
            timeout_ms=(
                _TIMEOUT_MS[
                    operation
                ]
            ),
        )

        commands.append(result)

        _require_success(
            result,
            operation=operation,
        )

        commits = []

        for line in (
            result["stdout"]
            .splitlines()
        ):
            if not line:
                continue

            parts = line.split(
                "\x1f",
                3,
            )

            if len(parts) != 4:
                continue

            (
                commit_hash,
                short_hash,
                authored_at,
                subject,
            ) = parts

            commits.append(
                {
                    "hash": (
                        commit_hash
                    ),
                    "short_hash": (
                        short_hash
                    ),
                    "authored_at": (
                        authored_at
                    ),
                    "subject": (
                        subject
                    ),
                }
            )

        return {
            "protocol": (
                GIT_PROTOCOL
            ),
            "operation": (
                operation
            ),
            "repository_root": str(
                root
            ),
            "commits": commits,
            "execution_evidence": (
                _evidence(
                    operation=(
                        operation
                    ),
                    commands=commands,
                )
            ),
        }
