from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_READ_BYTES = 1_048_576
MAX_TOOL_CHARS = 32_000
MAX_TREE_ENTRIES = 2_000
MAX_SEARCH_FILES = 2_000
MAX_SEARCH_RESULTS = 40

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

_WORKSPACE_ID = re.compile(r"^WS-[A-F0-9]{12}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_id() -> str:
    return "WS-" + uuid.uuid4().hex[:12].upper()


def _is_sensitive(path: Path) -> bool:
    name = path.name.casefold()

    if name in DENIED_NAMES:
        return True

    if path.suffix.casefold() in DENIED_SUFFIXES:
        return True

    return False


class WorkspaceRegistry:
    def __init__(self, store_path: Path):
        self.store_path = Path(store_path).expanduser()
        self.store_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._lock = threading.RLock()

        if not self.store_path.exists():
            self._save({"workspaces": []})

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(
                self.store_path.read_text(encoding="utf-8")
            )
        except (
            FileNotFoundError,
            json.JSONDecodeError,
        ):
            payload = {"workspaces": []}

        if not isinstance(payload, dict):
            payload = {"workspaces": []}

        payload.setdefault("workspaces", [])
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        tmp = self.store_path.with_suffix(".tmp")
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
        tmp.replace(self.store_path)

    def _validate_id(self, workspace_id: str) -> str:
        value = str(workspace_id or "").strip().upper()

        if not _WORKSPACE_ID.fullmatch(value):
            raise ValueError("Invalid workspace id")

        return value

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._load()
            rows: list[dict[str, Any]] = []

            for item in payload["workspaces"]:
                path = Path(str(item["path"])).expanduser()
                row = dict(item)
                row["exists"] = path.exists()
                rows.append(row)

            rows.sort(
                key=lambda row: str(
                    row.get("updated_at") or ""
                ),
                reverse=True,
            )

            return rows

    def get(self, workspace_id: str) -> dict[str, Any]:
        value = self._validate_id(workspace_id)

        with self._lock:
            for item in self._load()["workspaces"]:
                if item.get("id") == value:
                    row = dict(item)
                    row["exists"] = Path(
                        str(row["path"])
                    ).expanduser().exists()
                    return row

        raise KeyError(value)

    def authorize_path(
        self,
        path_value: str,
    ) -> dict[str, Any]:
        raw = str(path_value or "").strip()

        if not raw:
            raise ValueError("path must not be empty")

        selected = Path(
            os.path.expandvars(raw)
        ).expanduser().resolve()

        if not selected.exists():
            raise ValueError(
                f"Path does not exist: {selected}"
            )

        home = Path.home().resolve()

        if selected == Path("/"):
            raise ValueError(
                "Filesystem root cannot be authorized as a workspace"
            )

        if selected == home:
            raise ValueError(
                "The entire home directory cannot be authorized. "
                "Choose a narrower file or folder."
            )

        sensitive_parts = {
            ".ssh",
            ".gnupg",
            "Library/Keychains",
        }

        selected_text = str(selected)

        if any(
            selected_text == str(home / part)
            or selected_text.startswith(
                str(home / part) + os.sep
            )
            for part in sensitive_parts
        ):
            raise ValueError(
                "Sensitive credential directories cannot be authorized"
            )

        if _is_sensitive(selected):
            raise ValueError(
                "Sensitive credential files cannot be authorized"
            )

        kind = "directory" if selected.is_dir() else "file"
        now = _utc_now()

        with self._lock:
            payload = self._load()

            for item in payload["workspaces"]:
                if (
                    Path(
                        str(item["path"])
                    ).expanduser().resolve()
                    == selected
                ):
                    item["updated_at"] = now
                    self._save(payload)
                    return dict(item)

            row = {
                "id": _workspace_id(),
                "path": str(selected),
                "label": selected.name or str(selected),
                "kind": kind,
                "created_at": now,
                "updated_at": now,
                "permissions": [
                    "tree",
                    "read",
                    "search",
                ],
            }

            payload["workspaces"].append(row)
            self._save(payload)

            return dict(row)

    def revoke(self, workspace_id: str) -> None:
        value = self._validate_id(workspace_id)

        with self._lock:
            payload = self._load()
            rows = payload["workspaces"]
            updated = [
                row
                for row in rows
                if row.get("id") != value
            ]

            if len(updated) == len(rows):
                raise KeyError(value)

            payload["workspaces"] = updated
            self._save(payload)

    def _scope(
        self,
        workspace_id: str,
    ) -> tuple[dict[str, Any], Path]:
        row = self.get(workspace_id)
        selected = Path(str(row["path"])).resolve()

        if not selected.exists():
            raise FileNotFoundError(selected)

        return row, selected

    def _resolve_relative(
        self,
        workspace_id: str,
        relative: str | None,
    ) -> Path:
        row, selected = self._scope(workspace_id)
        rel = str(relative or "").strip()

        if row["kind"] == "file":
            if rel not in {
                "",
                ".",
                selected.name,
            }:
                raise PermissionError(
                    "This workspace authorizes one file only"
                )

            target = selected
        else:
            target = (
                selected
                if rel in {"", "."}
                else (selected / rel).resolve()
            )

            try:
                target.relative_to(selected)
            except ValueError as exc:
                raise PermissionError(
                    "Path escapes the authorized workspace"
                ) from exc

        if _is_sensitive(target):
            raise PermissionError(
                "Sensitive credential file access denied"
            )

        return target

    def resolve_change_target(
        self,
        workspace_id: str,
        relative: str,
    ) -> dict[str, Any]:
        row, selected = self._scope(workspace_id)
        rel = str(relative or "").strip()

        if rel in {"", ".", "/"}:
            raise ValueError("Change target must identify a file")

        if row["kind"] == "file":
            if rel not in {selected.name}:
                raise PermissionError(
                    "This workspace authorizes one file only"
                )
            target = selected
            display = selected.name
        else:
            rel_path = Path(rel)

            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise PermissionError(
                    "Path escapes the authorized workspace"
                )

            if any(part in IGNORED_DIRS for part in rel_path.parts):
                raise PermissionError(
                    "Change target is inside a protected/ignored directory"
                )

            cursor = selected
            for part in rel_path.parts:
                cursor = cursor / part
                if cursor.exists() and cursor.is_symlink():
                    raise PermissionError(
                        "Symlink mutation targets are not allowed"
                    )

            candidate = selected / rel_path
            target = candidate.resolve(strict=False)

            try:
                target.relative_to(selected)
            except ValueError as exc:
                raise PermissionError(
                    "Path escapes the authorized workspace"
                ) from exc

            display = str(target.relative_to(selected))

        if _is_sensitive(target):
            raise PermissionError(
                "Sensitive credential file mutation denied"
            )

        parent = target.parent
        if not parent.exists() or not parent.is_dir():
            raise ValueError(
                "Parent directory must already exist in P0.6B.1"
            )

        if target.exists() and target.is_dir():
            raise ValueError("Change target must be a file")

        return {
            "workspace_id": row["id"],
            "workspace_label": row["label"],
            "path": display,
            "absolute_path": str(target),
            "exists": target.exists(),
        }

    def tree(
        self,
        workspace_id: str,
        *,
        relative: str = "",
        max_depth: int = 3,
    ) -> dict[str, Any]:
        max_depth = max(0, min(int(max_depth), 6))
        row, selected = self._scope(workspace_id)
        target = self._resolve_relative(
            workspace_id,
            relative,
        )

        if target.is_file():
            return {
                "workspace_id": row["id"],
                "root": row["label"],
                "entries": [
                    {
                        "path": target.name,
                        "type": "file",
                        "size": target.stat().st_size,
                    }
                ],
                "truncated": False,
            }

        if not target.is_dir():
            raise ValueError("tree target must be a directory")

        entries: list[dict[str, Any]] = []
        truncated = False

        def walk(directory: Path, depth: int) -> None:
            nonlocal truncated

            if truncated or depth > max_depth:
                return

            try:
                children = sorted(
                    directory.iterdir(),
                    key=lambda p: (
                        not p.is_dir(),
                        p.name.casefold(),
                    ),
                )
            except PermissionError:
                return

            for child in children:
                if len(entries) >= MAX_TREE_ENTRIES:
                    truncated = True
                    return

                if (
                    child.is_dir()
                    and child.name in IGNORED_DIRS
                ):
                    continue

                if _is_sensitive(child):
                    continue

                try:
                    rel = child.relative_to(selected)
                except ValueError:
                    continue

                item: dict[str, Any] = {
                    "path": str(rel),
                    "type": (
                        "directory"
                        if child.is_dir()
                        else "file"
                    ),
                }

                if child.is_file():
                    try:
                        item["size"] = child.stat().st_size
                    except OSError:
                        item["size"] = None

                entries.append(item)

                if child.is_dir():
                    walk(child, depth + 1)

        walk(target, 0)

        return {
            "workspace_id": row["id"],
            "root": row["label"],
            "entries": entries,
            "truncated": truncated,
        }

    def read(
        self,
        workspace_id: str,
        relative: str = "",
    ) -> dict[str, Any]:
        row, selected = self._scope(workspace_id)
        target = self._resolve_relative(
            workspace_id,
            relative,
        )

        if not target.is_file():
            raise ValueError("read target must be a file")

        size = target.stat().st_size

        if size > MAX_READ_BYTES:
            raise ValueError(
                f"File exceeds {MAX_READ_BYTES} byte read limit"
            )

        raw = target.read_bytes()

        if b"\x00" in raw:
            raise ValueError(
                "Binary file reading is not enabled in P0.6A"
            )

        text = raw.decode(
            "utf-8",
            errors="replace",
        )

        try:
            rel = target.relative_to(
                selected if selected.is_dir()
                else selected.parent
            )
        except ValueError:
            rel = Path(target.name)

        truncated = len(text) > MAX_TOOL_CHARS

        return {
            "workspace_id": row["id"],
            "path": (
                target.name
                if row["kind"] == "file"
                else str(rel)
            ),
            "size": size,
            "content": text[:MAX_TOOL_CHARS],
            "truncated": truncated,
        }

    def search(
        self,
        workspace_id: str,
        query: str,
    ) -> dict[str, Any]:
        q = str(query or "").strip()

        if not q:
            raise ValueError("search query must not be empty")

        row, selected = self._scope(workspace_id)

        candidates: list[Path]

        if selected.is_file():
            candidates = [selected]
        else:
            candidates = []
            for root, dirs, files in os.walk(selected):
                dirs[:] = [
                    name
                    for name in dirs
                    if name not in IGNORED_DIRS
                ]

                for name in files:
                    path = Path(root) / name

                    if _is_sensitive(path):
                        continue

                    candidates.append(path)

                    if (
                        len(candidates)
                        >= MAX_SEARCH_FILES
                    ):
                        break

                if len(candidates) >= MAX_SEARCH_FILES:
                    break

        results: list[dict[str, Any]] = []
        q_fold = q.casefold()

        for path in candidates:
            try:
                rel = (
                    path.name
                    if selected.is_file()
                    else str(path.relative_to(selected))
                )
            except ValueError:
                continue

            filename_match = q_fold in rel.casefold()
            snippets: list[str] = []

            try:
                if path.stat().st_size <= 512_000:
                    raw = path.read_bytes()

                    if b"\x00" not in raw:
                        text = raw.decode(
                            "utf-8",
                            errors="replace",
                        )

                        for line_no, line in enumerate(
                            text.splitlines(),
                            start=1,
                        ):
                            if q_fold in line.casefold():
                                snippets.append(
                                    f"{line_no}: "
                                    f"{line.strip()[:240]}"
                                )

                                if len(snippets) >= 3:
                                    break
            except (
                OSError,
                PermissionError,
            ):
                pass

            if filename_match or snippets:
                results.append(
                    {
                        "path": rel,
                        "filename_match": filename_match,
                        "matches": snippets,
                    }
                )

                if len(results) >= MAX_SEARCH_RESULTS:
                    break

        return {
            "workspace_id": row["id"],
            "query": q,
            "results": results,
            "truncated": (
                len(candidates) >= MAX_SEARCH_FILES
                or len(results) >= MAX_SEARCH_RESULTS
            ),
        }

    def execute_tool(
        self,
        workspace_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if name == "workspace.tree":
            return self.tree(
                workspace_id,
                relative=str(
                    arguments.get("path") or ""
                ),
                max_depth=int(
                    arguments.get("max_depth") or 3
                ),
            )

        if name == "workspace.read":
            return self.read(
                workspace_id,
                relative=str(
                    arguments.get("path") or ""
                ),
            )

        if name == "workspace.search":
            return self.search(
                workspace_id,
                query=str(
                    arguments.get("query") or ""
                ),
            )

        raise ValueError(
            f"Tool is not available in P0.6A: {name}"
        )
