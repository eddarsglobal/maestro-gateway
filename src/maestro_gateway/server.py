from __future__ import annotations

import json
import secrets
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .capabilities import CapabilityRegistry
from .changes import (
    ChangeProposalStore,
    ProposalConflictError,
    ProposalStateError,
)
from .config import GatewayConfig
from .conversations import ConversationStore
from .core import MaestroCoreBridge
from .mission import GovernedDirectMissionRunner
from .ollama import discover_ollama
from .workspace import WorkspaceRegistry


SERVER_NAME = "MAESTRO-Local-Gateway"
MAX_BODY_BYTES = 2 * 1024 * 1024


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def native_picker_script(kind: str) -> str:
    value = str(kind or "").strip().lower()

    if value == "folder":
        return (
            'POSIX path of (choose folder with prompt '
            '"Select a folder for MAESTRO")'
        )

    if value == "file":
        return (
            'POSIX path of (choose file with prompt '
            '"Select a file for MAESTRO")'
        )

    raise ValueError("kind must be 'file' or 'folder'")


def choose_native_path(kind: str) -> str | None:
    if sys.platform != "darwin":
        raise NotImplementedError(
            "Native workspace picker is currently implemented for macOS."
        )

    script = native_picker_script(kind)

    try:
        completed = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Native workspace picker timed out"
        ) from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    if completed.returncode != 0:
        lowered = stderr.casefold()

        if (
            "-128" in stderr
            or "user canceled" in lowered
            or "user cancelled" in lowered
        ):
            return None

        raise RuntimeError(
            stderr or "Native workspace picker failed"
        )

    if not stdout:
        return None

    return stdout


class GatewayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        config: GatewayConfig,
        core: MaestroCoreBridge,
    ):
        super().__init__(
            server_address,
            handler_class,
        )

        self.config = config
        self.core = core

        user_root = (
            Path.home()
            / ".maestro"
            / "user"
        )

        self.conversations = (
            ConversationStore(
                user_root / "conversations"
            )
        )

        self.workspaces = WorkspaceRegistry(
            user_root
            / "workspaces"
            / "registry.json"
        )

        self.changes = ChangeProposalStore(
            user_root / "changes",
            self.workspaces,
        )

        self.capabilities = CapabilityRegistry()

        self.mission_runner = (
            GovernedDirectMissionRunner(
                core,
                self.conversations,
                self.workspaces,
                self.capabilities,
            )
        )

        self.session_token = (
            secrets.token_urlsafe(32)
        )


class GatewayHandler(BaseHTTPRequestHandler):
    server: GatewayHTTPServer
    server_version = (
        f"{SERVER_NAME}/{__version__}"
    )
    sys_version = ""

    def log_message(
        self,
        fmt: str,
        *args: object,
    ) -> None:
        sys.stderr.write(
            f"[gateway] "
            f"{self.client_address[0]} "
            f"{self.log_date_time_string()} "
            f"{fmt % args}\n"
        )

    def _origin(self) -> str | None:
        value = self.headers.get("Origin")
        return (
            value.rstrip("/")
            if value
            else None
        )

    def _origin_allowed(self) -> bool:
        origin = self._origin()

        if origin is None:
            return True

        return (
            origin
            in self.server.config.allowed_origins
        )

    def _common_headers(self) -> None:
        self.send_header(
            "Cache-Control",
            "no-store",
        )
        self.send_header(
            "X-Content-Type-Options",
            "nosniff",
        )
        self.send_header(
            "Referrer-Policy",
            "no-referrer",
        )
        self.send_header(
            "X-Frame-Options",
            "DENY",
        )

        origin = self._origin()

        if (
            origin
            and origin
            in self.server.config.allowed_origins
        ):
            self.send_header(
                "Access-Control-Allow-Origin",
                origin,
            )
            self.send_header(
                "Vary",
                "Origin",
            )

    def _send_json(
        self,
        status: HTTPStatus | int,
        payload: Any,
    ) -> None:
        body = _json_bytes(payload)

        self.send_response(int(status))
        self._common_headers()
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)

    def _reject_origin(self) -> bool:
        if self._origin_allowed():
            return False

        self._send_json(
            HTTPStatus.FORBIDDEN,
            {
                "error": (
                    "origin_not_allowed"
                ),
                "message": (
                    "Browser origin is not "
                    "allowed by MAESTRO "
                    "Local Gateway."
                ),
            },
        )

        return True

    def _read_json(
        self,
    ) -> dict[str, Any]:
        raw_length = self.headers.get(
            "Content-Length"
        )

        if raw_length is None:
            raise ValueError(
                "Content-Length is required"
            )

        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError(
                "Invalid Content-Length"
            ) from exc

        if (
            length < 0
            or length > MAX_BODY_BYTES
        ):
            raise ValueError(
                "Request body exceeds "
                "the 64 KiB limit"
            )

        raw = self.rfile.read(length)

        try:
            payload = json.loads(
                raw.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "Request body must be "
                "valid UTF-8 JSON"
            ) from exc

        if not isinstance(
            payload,
            dict,
        ):
            raise ValueError(
                "JSON request body must "
                "be an object"
            )

        return payload

    def _session_authorized(
        self,
    ) -> bool:
        supplied = self.headers.get(
            "X-Maestro-Session"
        )

        return bool(
            supplied
            and secrets.compare_digest(
                supplied,
                self.server.session_token,
            )
        )

    def _require_session(
        self,
    ) -> bool:
        if self._session_authorized():
            return True

        self._send_json(
            HTTPStatus.UNAUTHORIZED,
            {
                "error": "invalid_session",
                "message": (
                    "A valid "
                    "X-Maestro-Session "
                    "token is required."
                ),
            },
        )

        return False

    @staticmethod
    def _resource_id(
        path: str,
        prefix: str,
    ) -> str | None:
        if not path.startswith(prefix):
            return None

        value = path[
            len(prefix):
        ].strip("/")

        return value or None

    def do_OPTIONS(self) -> None:
        if self._reject_origin():
            return

        self.send_response(
            HTTPStatus.NO_CONTENT
        )
        self._common_headers()
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PATCH, "
            "DELETE, OPTIONS",
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Accept, "
            "X-Maestro-Session",
        )
        self.send_header(
            "Access-Control-Max-Age",
            "600",
        )
        self.end_headers()

    def do_GET(self) -> None:
        if self._reject_origin():
            return

        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(
            parsed.query
        )

        if path == "/":
            self._send_json(
                HTTPStatus.OK,
                {
                    "name": (
                        "MAESTRO Local Gateway"
                    ),
                    "gateway_version": (
                        __version__
                    ),
                    "mission_execution": True,
                    "mission_protocol": (
                        "P0.6A"
                    ),
                    "change_protocol": (
                        "P0.6B.1"
                    ),
                    "workspace_write_execution": False,
                    "conversation_storage": (
                        "local"
                    ),
                    "workspace": (
                        "read_only_governed"
                    ),
                    "native_workspace_picker": (
                        "macos" if sys.platform == "darwin" else "unsupported"
                    ),
                    "endpoints": [
                        "/health",
                        "/capabilities",
                        "/providers",
                        "/models",
                        "/session",
                        "/mission",
                        "/conversations",
                        "/workspaces",
                        "/changes",
                    ],
                },
            )
            return

        if path == "/health":
            try:
                health = (
                    self.server.core.health()
                )
            except Exception as exc:
                self._send_json(
                    HTTPStatus
                    .SERVICE_UNAVAILABLE,
                    {
                        "status": "ERROR",
                        "gateway_version": (
                            __version__
                        ),
                        "error": (
                            type(exc).__name__
                        ),
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.OK,
                {
                    **health,
                    "gateway_version": (
                        __version__
                    ),
                    "mission_protocol": (
                        "P0.6A"
                    ),
                    "change_protocol": (
                        "P0.6B.1"
                    ),
                    "workspace_write_execution": False,
                    "conversation_storage": (
                        "local"
                    ),
                    "workspace": (
                        "read_only_governed"
                    ),
                    "native_workspace_picker": (
                        "macos" if sys.platform == "darwin" else "unsupported"
                    ),
                },
            )
            return

        if path == "/capabilities":
            if not self._require_session():
                return

            active = bool(
                query.get(
                    "workspace_id",
                    [""],
                )[0]
            )

            self._send_json(
                HTTPStatus.OK,
                {
                    "capabilities": (
                        self.server
                        .capabilities
                        .snapshot(
                            workspace_active=active
                        )
                    )
                },
            )
            return

        if path == "/providers":
            ollama = discover_ollama()

            self._send_json(
                HTTPStatus.OK,
                {
                    "providers": [
                        ollama,
                        {
                            "id": "lmstudio",
                            "status": (
                                "not_configured"
                            ),
                        },
                        {
                            "id": "llamacpp",
                            "status": (
                                "not_configured"
                            ),
                        },
                        {
                            "id": "custom",
                            "status": (
                                "not_configured"
                            ),
                        },
                    ]
                },
            )
            return

        if path == "/models":
            ollama = discover_ollama()

            self._send_json(
                HTTPStatus.OK,
                {
                    "provider": "ollama",
                    "status": (
                        ollama["status"]
                    ),
                    "models": (
                        ollama["models"]
                    ),
                },
            )
            return

        if path == "/changes":
            if not self._require_session():
                return

            workspace_id = query.get(
                "workspace_id",
                [None],
            )[0]

            self._send_json(
                HTTPStatus.OK,
                {
                    "changes": (
                        self.server
                        .changes
                        .list(
                            workspace_id=workspace_id
                        )
                    )
                },
            )
            return

        change_id = (
            self._resource_id(
                path,
                "/changes/",
            )
        )

        if change_id:
            if not self._require_session():
                return

            try:
                proposal = (
                    self.server
                    .changes
                    .get(change_id)
                )
            except KeyError:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": "change_not_found",
                        "change_id": change_id,
                    },
                )
                return
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": "invalid_change_id",
                        "message": str(exc),
                    },
                )
                return

            self._send_json(HTTPStatus.OK, proposal)
            return

        if path == "/workspaces":
            if not self._require_session():
                return

            self._send_json(
                HTTPStatus.OK,
                {
                    "workspaces": (
                        self.server
                        .workspaces
                        .list()
                    )
                },
            )
            return

        workspace_id = (
            self._resource_id(
                path,
                "/workspaces/",
            )
        )

        if workspace_id:
            if not self._require_session():
                return

            operation = query.get(
                "op",
                [""],
            )[0]

            try:
                if operation == "tree":
                    result = (
                        self.server
                        .workspaces
                        .tree(
                            workspace_id,
                            relative=query.get(
                                "path",
                                [""],
                            )[0],
                            max_depth=int(
                                query.get(
                                    "depth",
                                    ["3"],
                                )[0]
                            ),
                        )
                    )
                elif operation == "read":
                    result = (
                        self.server
                        .workspaces
                        .read(
                            workspace_id,
                            relative=query.get(
                                "path",
                                [""],
                            )[0],
                        )
                    )
                elif operation == "search":
                    result = (
                        self.server
                        .workspaces
                        .search(
                            workspace_id,
                            query=query.get(
                                "q",
                                [""],
                            )[0],
                        )
                    )
                elif operation:
                    raise ValueError(
                        "Unknown workspace "
                        "operation"
                    )
                else:
                    result = (
                        self.server
                        .workspaces
                        .get(
                            workspace_id
                        )
                    )
            except KeyError:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": (
                            "workspace_not_found"
                        ),
                        "workspace_id": (
                            workspace_id
                        ),
                    },
                )
                return
            except (
                ValueError,
                PermissionError,
                FileNotFoundError,
            ) as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": (
                            "workspace_operation_"
                            "rejected"
                        ),
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.OK,
                result,
            )
            return

        if path == "/conversations":
            if not self._require_session():
                return

            include_archived = (
                query.get(
                    "archived",
                    ["0"],
                )[0]
                in {
                    "1",
                    "true",
                    "yes",
                }
            )
            q = query.get(
                "q",
                [""],
            )[0]

            self._send_json(
                HTTPStatus.OK,
                {
                    "conversations": (
                        self.server
                        .conversations
                        .list(
                            include_archived=(
                                include_archived
                            ),
                            query=q,
                        )
                    )
                },
            )
            return

        conversation_id = (
            self._resource_id(
                path,
                "/conversations/",
            )
        )

        if conversation_id:
            if not self._require_session():
                return

            try:
                record = (
                    self.server
                    .conversations
                    .get(
                        conversation_id
                    )
                )
            except KeyError:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": (
                            "conversation_not_"
                            "found"
                        ),
                        "conversation_id": (
                            conversation_id
                        ),
                    },
                )
                return
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": (
                            "invalid_"
                            "conversation_id"
                        ),
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.OK,
                record,
            )
            return

        self._send_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": "not_found",
                "path": path,
            },
        )

    def do_POST(self) -> None:
        if self._reject_origin():
            return

        path = urlsplit(
            self.path
        ).path

        if path == "/session":
            self._send_json(
                HTTPStatus.OK,
                {
                    "session_token": (
                        self.server
                        .session_token
                    ),
                    "scope": (
                        "local_gateway_process"
                    ),
                    "mission_protocol": (
                        "P0.6A"
                    ),
                },
            )
            return

        if path == "/changes/propose":
            if not self._require_session():
                return

            try:
                payload = self._read_json()
                proposal = (
                    self.server
                    .changes
                    .propose(
                        workspace_id=str(
                            payload.get("workspace_id") or ""
                        ),
                        relative=str(
                            payload.get("path") or ""
                        ),
                        content=payload.get("content"),
                        operation=str(
                            payload.get("operation") or "edit"
                        ),
                    )
                )
            except KeyError as exc:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": "workspace_not_found",
                        "message": str(exc),
                    },
                )
                return
            except (
                ValueError,
                PermissionError,
                FileNotFoundError,
                UnicodeDecodeError,
            ) as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": "change_proposal_rejected",
                        "message": str(exc),
                    },
                )
                return

            self._send_json(HTTPStatus.CREATED, proposal)
            return

        if path.startswith("/changes/"):
            parts = path.strip("/").split("/")

            if (
                len(parts) == 3
                and parts[0] == "changes"
                and parts[2] in {"approve", "reject"}
            ):
                if not self._require_session():
                    return

                change_id = parts[1]
                action = parts[2]

                try:
                    payload = self._read_json()

                    if action == "approve":
                        proposal = (
                            self.server
                            .changes
                            .approve(change_id)
                        )
                    else:
                        proposal = (
                            self.server
                            .changes
                            .reject(
                                change_id,
                                reason=payload.get("reason"),
                            )
                        )
                except KeyError:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "error": "change_not_found",
                            "change_id": change_id,
                        },
                    )
                    return
                except ProposalConflictError as exc:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": "change_proposal_stale",
                            "message": str(exc),
                            "proposal": exc.proposal,
                        },
                    )
                    return
                except ProposalStateError as exc:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": "invalid_change_state",
                            "message": str(exc),
                        },
                    )
                    return
                except (ValueError, PermissionError) as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": "change_action_rejected",
                            "message": str(exc),
                        },
                    )
                    return

                self._send_json(HTTPStatus.OK, proposal)
                return

        if path == "/workspaces/choose":
            if not self._require_session():
                return

            try:
                payload = self._read_json()
                kind = str(
                    payload.get("kind") or "folder"
                ).strip().lower()

                selected = choose_native_path(kind)

                if selected is None:
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "cancelled": True,
                            "kind": kind,
                        },
                    )
                    return

                row = (
                    self.server
                    .workspaces
                    .authorize_path(selected)
                )

            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": "invalid_workspace_picker_request",
                        "message": str(exc),
                    },
                )
                return

            except NotImplementedError as exc:
                self._send_json(
                    HTTPStatus.NOT_IMPLEMENTED,
                    {
                        "error": "native_picker_not_supported",
                        "message": str(exc),
                    },
                )
                return

            except RuntimeError as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "error": "native_picker_failed",
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.CREATED,
                {
                    **row,
                    "cancelled": False,
                    "picker_kind": kind,
                },
            )
            return

        if path == "/workspaces/open":
            if not self._require_session():
                return

            try:
                payload = self._read_json()
                row = (
                    self.server
                    .workspaces
                    .authorize_path(
                        str(
                            payload.get(
                                "path"
                            )
                            or ""
                        )
                    )
                )
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": (
                            "workspace_authorization_"
                            "rejected"
                        ),
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.CREATED,
                row,
            )
            return

        if path == "/conversations":
            if not self._require_session():
                return

            try:
                payload = self._read_json()
                record = (
                    self.server
                    .conversations
                    .create(
                        title=payload.get(
                            "title"
                        ),
                    )
                )
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": (
                            "invalid_conversation_"
                            "request"
                        ),
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.CREATED,
                record,
            )
            return

        if path == "/mission":
            if not self._require_session():
                return

            try:
                payload = self._read_json()

                result = (
                    self.server
                    .mission_runner
                    .run(
                        prompt=payload.get(
                            "prompt"
                        ),
                        model=payload.get(
                            "model"
                        ),
                        max_tokens=payload.get(
                            "max_tokens",
                            512,
                        ),
                        conversation_id=(
                            payload.get(
                                "conversation_id"
                            )
                        ),
                        workspace_id=(
                            payload.get(
                                "workspace_id"
                            )
                        ),
                    )
                )
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": (
                            "invalid_mission_"
                            "request"
                        ),
                        "message": str(exc),
                    },
                )
                return
            except KeyError as exc:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": (
                            "resource_not_found"
                        ),
                        "message": str(exc),
                    },
                )
                return
            except Exception as exc:
                self._send_json(
                    HTTPStatus
                    .INTERNAL_SERVER_ERROR,
                    {
                        "error": (
                            "mission_execution_"
                            "error"
                        ),
                        "message": str(exc),
                        "type": (
                            type(exc).__name__
                        ),
                    },
                )
                return

            status = (
                HTTPStatus.OK
                if result.get("ok")
                else HTTPStatus
                .UNPROCESSABLE_ENTITY
            )

            self._send_json(
                status,
                result,
            )
            return

        self._send_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": "not_found",
                "path": path,
            },
        )

    def do_PATCH(self) -> None:
        if self._reject_origin():
            return

        path = urlsplit(
            self.path
        ).path
        conversation_id = (
            self._resource_id(
                path,
                "/conversations/",
            )
        )

        if conversation_id is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {
                    "error": "not_found",
                    "path": path,
                },
            )
            return

        if not self._require_session():
            return

        try:
            payload = self._read_json()
            record = (
                self.server
                .conversations
                .update(
                    conversation_id,
                    title=payload.get(
                        "title"
                    ),
                    pinned=payload.get(
                        "pinned"
                    ),
                    archived=payload.get(
                        "archived"
                    ),
                )
            )
        except KeyError:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {
                    "error": (
                        "conversation_not_found"
                    ),
                    "conversation_id": (
                        conversation_id
                    ),
                },
            )
            return
        except ValueError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": (
                        "invalid_conversation_"
                        "update"
                    ),
                    "message": str(exc),
                },
            )
            return

        self._send_json(
            HTTPStatus.OK,
            record,
        )

    def do_DELETE(self) -> None:
        if self._reject_origin():
            return

        path = urlsplit(
            self.path
        ).path

        workspace_id = (
            self._resource_id(
                path,
                "/workspaces/",
            )
        )

        if workspace_id:
            if not self._require_session():
                return

            try:
                (
                    self.server
                    .workspaces
                    .revoke(
                        workspace_id
                    )
                )
            except KeyError:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": (
                            "workspace_not_found"
                        ),
                        "workspace_id": (
                            workspace_id
                        ),
                    },
                )
                return

            self._send_json(
                HTTPStatus.OK,
                {
                    "revoked": True,
                    "workspace_id": (
                        workspace_id
                    ),
                },
            )
            return

        conversation_id = (
            self._resource_id(
                path,
                "/conversations/",
            )
        )

        if conversation_id is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {
                    "error": "not_found",
                    "path": path,
                },
            )
            return

        if not self._require_session():
            return

        try:
            (
                self.server
                .conversations
                .delete(
                    conversation_id
                )
            )
        except KeyError:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {
                    "error": (
                        "conversation_not_found"
                    ),
                    "conversation_id": (
                        conversation_id
                    ),
                },
            )
            return
        except ValueError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": (
                        "invalid_conversation_id"
                    ),
                    "message": str(exc),
                },
            )
            return

        self._send_json(
            HTTPStatus.OK,
            {
                "deleted": True,
                "conversation_id": (
                    conversation_id
                ),
            },
        )


def build_server(
    config: GatewayConfig | None = None,
) -> GatewayHTTPServer:
    config = (
        config
        or GatewayConfig.from_env()
    )
    core = MaestroCoreBridge(
        config.runtime_dir
    )

    return GatewayHTTPServer(
        (
            config.host,
            config.port,
        ),
        GatewayHandler,
        config=config,
        core=core,
    )


def main() -> int:
    config = GatewayConfig.from_env()
    server = build_server(config)

    print("=" * 72)
    print(
        "MAESTRO LOCAL GATEWAY — P0.6B.1"
    )
    print("=" * 72)
    print(
        f"Gateway : "
        f"http://{config.host}:{config.port}"
    )
    print(
        f"Runtime : {config.runtime_dir}"
    )
    print(
        "Workspace: governed read + staged change proposals"
    )
    print(
        "Quality : anti-echo + recoverable tool guard enabled"
    )
    print(
        "Picker  : macOS native file/folder chooser"
    )
    print(
        "Changes : diff + SHA-256 + approval state; no workspace write"
    )
    print(
        "Write   : proposal/approval only; workspace apply DISABLED"
    )
    print("=" * 72)

    try:
        server.serve_forever(
            poll_interval=0.25
        )
    except KeyboardInterrupt:
        print(
            "\nStopping MAESTRO "
            "Local Gateway..."
        )
    finally:
        server.server_close()

    return 0
