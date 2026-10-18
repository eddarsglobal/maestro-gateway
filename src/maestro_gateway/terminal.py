from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .mission_control import MissionCancelledError


TERMINAL_PROTOCOL = "P0.6D.1"
TERMINAL_MODE = "inspection_only"
TERMINAL_SHELL = False

DEFAULT_TIMEOUT_MS = 3000
MIN_TIMEOUT_MS = 100
MAX_TIMEOUT_MS = 5000

MAX_OUTPUT_BYTES = 32_000
MAX_INPUT_BYTES = 1_048_576
MAX_ARGC = 8
MAX_ARG_CHARS = 256


COMMAND_PATHS = {
    "pwd": ("/bin/pwd", "/usr/bin/pwd"),
    "head": ("/usr/bin/head", "/bin/head"),
    "tail": ("/usr/bin/tail", "/bin/tail"),
    "wc": ("/usr/bin/wc", "/bin/wc"),
    "stat": ("/usr/bin/stat", "/bin/stat"),
}


PROTECTED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".ssh",
    ".gnupg",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".next",
    "dist",
    "build",
}


DENIED_NAMES = {
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


DENIED_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}


def _command_path(name: str) -> str:
    for candidate in COMMAND_PATHS.get(name, ()):
        path = Path(candidate)

        if (
            path.is_file()
            and os.access(path, os.X_OK)
        ):
            return str(path)

    raise FileNotFoundError(
        "Allowlisted terminal executable "
        f"is unavailable: {name}"
    )


def _display_path(
    root: Path,
    path: Path,
) -> str:
    rel = path.relative_to(root)
    value = str(rel)

    return "." if value in {"", "."} else value


def _assert_safe_path(
    root: Path,
    target: Path,
) -> None:
    try:
        rel = target.relative_to(root)
    except ValueError as exc:
        raise PermissionError(
            "Terminal path escapes the "
            "authorized workspace"
        ) from exc

    parts = {
        part.casefold()
        for part in rel.parts
    }

    if parts & PROTECTED_PARTS:
        raise PermissionError(
            "Terminal access to protected "
            "workspace paths is denied"
        )

    name = target.name.casefold()

    if (
        name in DENIED_NAMES
        or target.suffix.casefold()
        in DENIED_SUFFIXES
    ):
        raise PermissionError(
            "Terminal access to sensitive "
            "credential files is denied"
        )


def _resolve_operand(
    root: Path,
    cwd: Path,
    value: str,
    *,
    require_file: bool,
) -> Path:
    raw = str(value or "").strip()

    if (
        not raw
        or "\x00" in raw
    ):
        raise ValueError(
            "Terminal path operand must "
            "not be empty"
        )

    relative = Path(raw)

    if (
        relative.is_absolute()
        or ".." in relative.parts
    ):
        raise PermissionError(
            "Terminal path operands must "
            "remain inside the workspace"
        )

    target = (
        cwd / relative
    ).resolve(strict=False)

    _assert_safe_path(
        root,
        target,
    )

    if not target.exists():
        raise FileNotFoundError(target)

    if (
        require_file
        and not target.is_file()
    ):
        raise ValueError(
            "Terminal command requires "
            "a file operand"
        )

    return target


def _bounded_file(
    root: Path,
    cwd: Path,
    value: str,
) -> Path:
    target = _resolve_operand(
        root,
        cwd,
        value,
        require_file=True,
    )

    if target.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(
            "Terminal inspection file exceeds "
            f"{MAX_INPUT_BYTES} bytes"
        )

    return target


def _line_count(value: str) -> int:
    try:
        count = int(value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "head/tail line count must "
            "be an integer"
        ) from exc

    if not 1 <= count <= 200:
        raise ValueError(
            "head/tail line count must "
            "be between 1 and 200"
        )

    return count


def _normalize_argv(
    argv: list[str],
) -> list[str]:
    if (
        not isinstance(argv, list)
        or not argv
    ):
        raise ValueError(
            "terminal.run requires a "
            "non-empty argv list"
        )

    if len(argv) > MAX_ARGC:
        raise ValueError(
            "terminal.run accepts at most "
            f"{MAX_ARGC} argv items"
        )

    normalized: list[str] = []

    for item in argv:
        if not isinstance(item, str):
            raise ValueError(
                "terminal.run argv items "
                "must be strings"
            )

        value = item.strip()

        if (
            not value
            or "\x00" in value
        ):
            raise ValueError(
                "terminal.run argv items "
                "must be non-empty strings"
            )

        if len(value) > MAX_ARG_CHARS:
            raise ValueError(
                "terminal.run argv item "
                "exceeds size limit"
            )

        normalized.append(value)

    return normalized


def _plan_command(
    root: Path,
    cwd: Path,
    argv: list[str],
) -> tuple[str, list[str], str]:
    argv = _normalize_argv(argv)

    command = argv[0]

    if (
        "/" in command
        or command not in COMMAND_PATHS
    ):
        raise PermissionError(
            "Terminal command is not "
            "allowlisted for P0.6D.1"
        )

    executable = _command_path(command)

    if command == "pwd":
        if len(argv) != 1:
            raise ValueError(
                "pwd does not accept arguments "
                "in P0.6D.1"
            )

        return (
            executable,
            [executable],
            command,
        )

    if command in {
        "head",
        "tail",
    }:
        if len(argv) == 2:
            count = 10
            operand = argv[1]

        elif (
            len(argv) == 4
            and argv[1] == "-n"
        ):
            count = _line_count(
                argv[2]
            )
            operand = argv[3]

        else:
            raise ValueError(
                f"{command} accepts only "
                f"'{command} <file>' or "
                f"'{command} -n <1..200> <file>'"
            )

        target = _bounded_file(
            root,
            cwd,
            operand,
        )

        return (
            executable,
            [
                executable,
                "-n",
                str(count),
                str(target),
            ],
            command,
        )

    if command == "wc":
        rest = list(argv[1:])
        flag: str | None = None

        if (
            rest
            and rest[0].startswith("-")
        ):
            if rest[0] not in {
                "-l",
                "-w",
                "-c",
                "-m",
            }:
                raise ValueError(
                    "wc only permits "
                    "-l, -w, -c, or -m"
                )

            flag = rest.pop(0)

        if not 1 <= len(rest) <= 4:
            raise ValueError(
                "wc requires between one "
                "and four file operands"
            )

        targets = [
            str(
                _bounded_file(
                    root,
                    cwd,
                    operand,
                )
            )
            for operand in rest
        ]

        planned = [executable]

        if flag:
            planned.append(flag)

        planned.extend(targets)

        return (
            executable,
            planned,
            command,
        )

    if command == "stat":
        if len(argv) != 2:
            raise ValueError(
                "stat requires exactly one "
                "workspace path operand"
            )

        target = _resolve_operand(
            root,
            cwd,
            argv[1],
            require_file=False,
        )

        return (
            executable,
            [
                executable,
                str(target),
            ],
            command,
        )

    raise PermissionError(
        "Terminal command is not "
        "allowlisted for P0.6D.1"
    )



def _cancel_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop only this invocation's isolated process group, including children.

    SIGTERM is followed by SIGKILL even if the group leader exited: an
    inherited pipe or child process must not survive a cancelled terminal call.
    """
    if os.name == "posix":
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            # PermissionError must propagate; never claim successful abort.
            if sig == signal.SIGTERM:
                try:
                    process.wait(timeout=0.15)
                except subprocess.TimeoutExpired:
                    pass
    else:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=0.15)
        except subprocess.TimeoutExpired:
            process.kill()


def _run_cancelable_inspection(
    planned_argv: list[str],
    workdir: Path,
    environment: dict[str, str],
    timeout: int,
    cancellation: threading.Event,
) -> subprocess.CompletedProcess[bytes]:
    """P0.6D.1 run with mission-scoped cooperative 50 ms cancellation.

    This covers process lifetime, not atomic external filesystem rollback.
    """
    if cancellation.is_set():
        raise MissionCancelledError("terminal.run cancelled before process launch")
    process = subprocess.Popen(
        planned_argv,
        cwd=str(workdir),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout / 1000.0
    aborted = False
    try:
        while True:
            if cancellation.is_set():
                aborted = True
                raise MissionCancelledError("terminal.run cancelled during execution")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                aborted = True
                raise TimeoutError(f"terminal.run exceeded {timeout} ms")
            try:
                stdout, stderr = process.communicate(timeout=min(0.05, remaining))
                if cancellation.is_set():
                    aborted = True
                    raise MissionCancelledError("terminal.run cancelled before result delivery")
                return subprocess.CompletedProcess(
                    planned_argv, process.returncode, stdout, stderr
                )
            except subprocess.TimeoutExpired:
                continue
    finally:
        if aborted or cancellation.is_set():
            _cancel_process_group(process)
            # Reap the group leader and drain pipes, but never wait indefinitely.
            try:
                process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                _cancel_process_group(process)
                try:
                    process.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    # Fail closed; the caller cannot claim completed execution.
                    raise RuntimeError("terminal.run cancelled but pipes did not close")


def run_inspection_command(
    *,
    workspace_root: Path,
    cwd: Path,
    argv: list[str],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    cancellation: threading.Event | None = None,
) -> dict[str, Any]:
    root = Path(
        workspace_root
    ).resolve()

    workdir = Path(
        cwd
    ).resolve()

    if not root.is_dir():
        raise ValueError(
            "terminal.run requires an "
            "authorized directory workspace"
        )

    _assert_safe_path(
        root,
        workdir,
    )

    if not workdir.is_dir():
        raise ValueError(
            "terminal.run cwd must "
            "be a directory"
        )

    if isinstance(
        timeout_ms,
        bool,
    ):
        raise ValueError(
            "terminal.run timeout_ms "
            "must be an integer"
        )

    try:
        timeout = int(timeout_ms)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError(
            "terminal.run timeout_ms "
            "must be an integer"
        ) from exc

    if not (
        MIN_TIMEOUT_MS
        <= timeout
        <= MAX_TIMEOUT_MS
    ):
        raise ValueError(
            "terminal.run timeout_ms must "
            f"be between {MIN_TIMEOUT_MS} "
            f"and {MAX_TIMEOUT_MS}"
        )

    (
        executable,
        planned_argv,
        command,
    ) = _plan_command(
        root,
        workdir,
        argv,
    )

    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
    }

    started = time.perf_counter()

    if cancellation is None:
        # Preserve legacy inspection behavior for calls without a mission ID.
        try:
            completed = subprocess.run(
                planned_argv,
                cwd=str(workdir),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                check=False,
                timeout=timeout / 1000.0,
                close_fds=True,
                start_new_session=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                "terminal.run exceeded "
                f"{timeout} ms"
            ) from exc
    else:
        completed = _run_cancelable_inspection(
            planned_argv, workdir, environment, timeout, cancellation
        )

    if cancellation is not None and cancellation.is_set():
        raise MissionCancelledError("terminal.run cancelled before evidence recording")

    duration_ms = int(
        (
            time.perf_counter()
            - started
        )
        * 1000
    )

    stdout_raw = (
        completed.stdout
        or b""
    )

    stderr_raw = (
        completed.stderr
        or b""
    )

    stdout_truncated = (
        len(stdout_raw)
        > MAX_OUTPUT_BYTES
    )

    stderr_truncated = (
        len(stderr_raw)
        > MAX_OUTPUT_BYTES
    )

    stdout = (
        stdout_raw[
            :MAX_OUTPUT_BYTES
        ]
        .decode(
            "utf-8",
            errors="replace",
        )
    )

    stderr = (
        stderr_raw[
            :MAX_OUTPUT_BYTES
        ]
        .decode(
            "utf-8",
            errors="replace",
        )
    )

    cwd_display = _display_path(
        root,
        workdir,
    )

    execution_evidence = {
        "protocol": TERMINAL_PROTOCOL,
        "policy": (
            "allowlisted_read_only"
        ),
        "shell": TERMINAL_SHELL,
        "stdin": "disabled",
        "command": command,
        "executable": executable,
        "cwd": cwd_display,
        "exit_code": int(
            completed.returncode
        ),
        "duration_ms": duration_ms,
        "timeout_ms": timeout,
        "stdout_sha256": (
            hashlib.sha256(
                stdout_raw
            ).hexdigest()
        ),
        "stderr_sha256": (
            hashlib.sha256(
                stderr_raw
            ).hexdigest()
        ),
        "stdout_bytes": len(
            stdout_raw
        ),
        "stderr_bytes": len(
            stderr_raw
        ),
        "stdout_truncated": (
            stdout_truncated
        ),
        "stderr_truncated": (
            stderr_truncated
        ),
    }

    if cancellation is not None and cancellation.is_set():
        raise MissionCancelledError("terminal.run cancelled before returning result")

    return {
        "protocol": TERMINAL_PROTOCOL,
        "mode": TERMINAL_MODE,
        "command": command,
        "cwd": cwd_display,
        "exit_code": int(
            completed.returncode
        ),
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": (
            stdout_truncated
        ),
        "stderr_truncated": (
            stderr_truncated
        ),
        "duration_ms": duration_ms,
        "execution_evidence": (
            execution_evidence
        ),
    }
