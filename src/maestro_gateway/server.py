from __future__ import annotations

import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
from importlib.resources import files
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .capabilities import CapabilityRegistry
from .changes import (
    ProposalApplyError,
    ChangeProposalStore,
    ProposalConflictError,
    ProposalStateError,
)
from .config import GatewayConfig
from .conversations import ConversationStore
from .core import MaestroCoreBridge
from .execution import (
    EXECUTION_PROTOCOL,
    ExecutionProposalStore,
    ExecutionStaleError,
    ExecutionStateError,
)
from .git_commit import (
    GIT_COMMIT_MODE,
    GIT_COMMIT_PROTOCOL,
    GitCommitConflictError,
    GitCommitProposalStore,
    GitCommitStateError,
)
from .git_inspection import GIT_MODE, GIT_PROTOCOL
from .git_staging import (
    GIT_STAGE_MODE,
    GIT_STAGE_PROTOCOL,
    GitStageApplyError,
    GitStageConflictError,
    GitStageProposalStore,
    GitStageStateError,
)
from .mission import GovernedDirectMissionRunner
from .request_journal import RequestReplayBlocked
from .mission_control import MissionControl, MissionConflictError, MissionCancelledError
from .native_admission import NativeAdmissionBarrier, ForegroundAdmissionBusy
from .cross_process_foreground import CrossProcessForegroundAdmission
from .gateway_owner_lease import GatewayOwnerLease, OwnerBlocked
from .software_artifact_verifier import (
    VerifiedSoftwareArtifacts, ArtifactVerificationError,
)
from .provider_inventory import discover_runtime_inventory
from .autonomous_qualification import (AutonomousQualificationService,
    execute_local_probe, model_version, update_model_versions,
    probe_evidence, LANGUAGES)
from .qualification_inspection import inspect_operational_evidence
from .terminal import (
    TERMINAL_MODE,
    TERMINAL_PROTOCOL,
    TERMINAL_SHELL,
)
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
    # Ownership must outlive every request thread that can publish durable state.
    # A daemon thread could outlive server_close() and write AFTER the owner
    # lease is released. Never claim safety if an active request has not exited.
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        config: GatewayConfig,
        core: MaestroCoreBridge,
        owner_lease: GatewayOwnerLease,
    ):
        # An unowned server must never initialize any durable user journal.
        if not isinstance(owner_lease, GatewayOwnerLease) or owner_lease.fd is None:
            raise OwnerBlocked("Gateway server requires a held process owner lease")
        self._owner_lease = owner_lease
        super().__init__(
            server_address,
            handler_class,
        )

        self.config = config
        self.core = core
        # Passive detection at every launch, without eagerly booting the Core.
        # Gateway health/invalid mission requests preserve zero-model safety.
        core.prewarm_runtime_catalogue()

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

        # Read language benchmark evidence synchronously before accepting a
        # first mission. It is local, digest-bound metadata only: no model is
        # started, selected, downloaded, or contacted by this read.
        from .language_benchmark_evidence import load_language_benchmark_evidence
        load_language_benchmark_evidence(
            Path.home() / ".maestro" / "runtime" / "language_benchmark_evidence.json"
        )

        # Read metrics before prompts; routing hot path uses only cached evidence.
        from .adaptive_router import warm_route_history
        threading.Thread(target=warm_route_history, args=(self.conversations.root,),
                         daemon=True, name="MAESTRO-Route-Evidence").start()

        self.workspaces = WorkspaceRegistry(
            user_root
            / "workspaces"
            / "registry.json"
        )

        self.changes = ChangeProposalStore(
            user_root / "changes",
            self.workspaces,
        )

        self.executions = ExecutionProposalStore(
            user_root / "executions",
            self.workspaces,
        )

        self.git_stages = GitStageProposalStore(
            user_root / "git-stages",
            self.workspaces,
        )

        self.git_commits = GitCommitProposalStore(
            user_root / "git-commits",
            self.workspaces,
        )

        self.artifact_verifier = VerifiedSoftwareArtifacts(
            self.workspaces, self.changes, self.executions,
        )

        self.mission_control = MissionControl(user_root / "mission-controls")
        # P0.4.3b2c: native admission is atomic within this Gateway process.
        # No provider-quiescence proof exists; autonomous inference remains BLOCKED.
        self.model_admission = CrossProcessForegroundAdmission(
            lock_root=Path.home() / ".maestro" / "runtime" / "admission-p043b2g",
            adapter_cancellation_verified=False,
        )
        # V8: automatic, idle-only and local-only qualification. An idle worker
        # never boots Core, installs a model, or bypasses the authorized registry.
        def approved_candidates():
            if getattr(self.core, '_boot_result', None) is None:
                return []
            # Avoid the Core's ongoing guarded mutation and foreground traffic.
            if self.mission_control._active:
                return []
            registry = self.core.system.sovereign_intelligence.registry
            catalogue = list(getattr(self.core, '_catalog', ()))
            fingerprints = {
                f"{runtime['id']}::{name}": digest
                for runtime in catalogue for name, digest in
                (runtime.get('model_fingerprints') or {}).items()
            }
            update_model_versions(fingerprints)
            return [(item, model_version(item, fingerprints)) for item in registry.all()]

        def local_probe(descriptor, prompt, cancel, deadline):
            catalogue = list(getattr(self.core, '_catalog', ()))
            allowed = {runtime['id']: runtime['url'] for runtime in catalogue
                       if runtime.get('selection_enabled') and runtime.get('status') == 'online'}
            return execute_local_probe(descriptor, prompt, cancel, deadline, allowed)

        self.autonomous_qualification = AutonomousQualificationService(
            candidate_supplier=approved_candidates, runner=local_probe,
            state_path=user_root / 'model-qualifications' / 'v8.json',
            idle_seconds=float(os.getenv('MAESTRO_PROBE_IDLE_SECONDS', '90')),
            probe_timeout=float(os.getenv('MAESTRO_PROBE_TIMEOUT_SECONDS', '25')),
            enabled=os.getenv('MAESTRO_AUTO_QUALIFICATION', '1') != '0',
            admission=self.model_admission)
        self.autonomous_qualification.start()
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

    def server_close(self) -> None:
        # Refuse to relinquish ownership unless all services and request threads
        # have stopped cleanly. On failure, the lock stays held until process exit.
        self.model_admission.shutdown()
        self.autonomous_qualification.shutdown()
        super().server_close()
        owned = getattr(self, "_owner_lease", None)
        if owned is not None:
            self._owner_lease = None
            owned.__exit__(None, None, None)



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
        expected_port = self.server.server_address[1]
        same_origin = {
            f"http://127.0.0.1:{expected_port}",
            f"http://localhost:{expected_port}",
            f"http://[::1]:{expected_port}",
        }
        return origin in same_origin or origin in self.server.config.allowed_origins

    def _host_allowed(self) -> bool:
        # DNS rebinding protection: loopback socket binding alone is insufficient.
        supplied = self.headers.get("Host", "")
        allowed_port = self.server.server_address[1]
        return supplied in {
            f"127.0.0.1:{allowed_port}",
            f"localhost:{allowed_port}",
            f"[::1]:{allowed_port}",
        }

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
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("X-Permitted-Cross-Domain-Policies", "none")

        origin = self._origin()

        if origin and self._origin_allowed():
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
        if not self._host_allowed():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "host_not_allowed", "message": "Invalid loopback Host header."},
            )
            return True
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
                "the 2 MiB limit"
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

    def _serve_original_ui(self, path: str) -> bool:
        """Serve the restored React production bundle, not the replacement UI.

        Assets may only be read below the configured build directory.
        No directory listing or SPA fallback outside /app/ is permitted.
        """
        configured = os.environ.get("MAESTRO_UI_DIST_DIR", "")
        if not configured or path not in {"/app", "/app/"} and not path.startswith("/app/"):
            return False

        from urllib.parse import unquote
        root = Path(configured).expanduser().resolve()
        if not (root / "index.html").is_file():
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "original_ui_not_built", "message": "Run INSTALLER_MAC.command to compile the original MAESTRO UI."},
            )
            return True

        raw_relative = "index.html" if path in {"/app", "/app/"} else unquote(path[len("/app/"):])
        if (
            not raw_relative or "\\" in raw_relative or "\x00" in raw_relative
            or any(part in {".", "..", ""} for part in raw_relative.split("/"))
        ):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "ui_asset_not_found"})
            return True

        requested = (root / raw_relative).resolve()
        if not requested.is_relative_to(root) or not requested.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "ui_asset_not_found"})
            return True

        content_type = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
        if requested.suffix == ".js":
            content_type = "text/javascript"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        body = requested.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; "
            "script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; "
            "font-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'; object-src 'none'",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self) -> None:
        if self._reject_origin():
            return

        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(
            parsed.query
        )

        if self._serve_original_ui(path):
            return

        if path in {"/app", "/app/", "/app/app.css", "/app/app.js"}:
            resource = (
                "index.html" if path in {"/app", "/app/"}
                else path.rsplit("/", 1)[-1]
            )
            content_type = {
                "index.html": "text/html; charset=utf-8",
                "app.css": "text/css; charset=utf-8",
                "app.js": "text/javascript; charset=utf-8",
            }[resource]
            body = files("maestro_gateway").joinpath(
                "webui", resource
            ).read_bytes()
            self.send_response(HTTPStatus.OK)
            self._common_headers()
            self.send_header("Content-Type", content_type)
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; "
                "script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; "
                "base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'; object-src 'none'",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

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
                        "P0.6C.2"
                    ),
                    "workspace_write_execution": True,
                    "workspace_delete_execution": True,
                    "terminal_protocol": TERMINAL_PROTOCOL,
                    "terminal_execution": TERMINAL_MODE,
                    "terminal_shell": TERMINAL_SHELL,
                    "git_protocol": GIT_PROTOCOL,
                    "git_execution": GIT_MODE,
                    "git_stage_protocol": GIT_STAGE_PROTOCOL,
                    "git_stage_execution": GIT_STAGE_MODE,
                    "git_commit_protocol": GIT_COMMIT_PROTOCOL,
                    "git_commit_execution": GIT_COMMIT_MODE,
                    "execution_protocol": EXECUTION_PROTOCOL,
                    "project_execution": "approved_explicit_execute",
                    "conversation_storage": (
                        "local"
                    ),
                    "workspace": (
                        "governed_atomic_mutation"
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
                        "/git-stages",
                        "/git-commits",
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
                        "P0.6C.2"
                    ),
                    "workspace_write_execution": True,
                    "workspace_delete_execution": True,
                    "terminal_protocol": TERMINAL_PROTOCOL,
                    "terminal_execution": TERMINAL_MODE,
                    "terminal_shell": TERMINAL_SHELL,
                    "git_protocol": GIT_PROTOCOL,
                    "git_execution": GIT_MODE,
                    "git_stage_protocol": GIT_STAGE_PROTOCOL,
                    "git_stage_execution": GIT_STAGE_MODE,
                    "git_commit_protocol": GIT_COMMIT_PROTOCOL,
                    "git_commit_execution": GIT_COMMIT_MODE,
                    "execution_protocol": EXECUTION_PROTOCOL,
                    "project_execution": "approved_explicit_execute",
                    "conversation_storage": (
                        "local"
                    ),
                    "workspace": (
                        "governed_atomic_mutation"
                    ),
                    "native_workspace_picker": (
                        "macos" if sys.platform == "darwin" else "unsupported"
                    ),
                },
            )
            return

        if path == "/execution-profiles":
            if not self._require_session():
                return

            workspace_id = str(
                query.get(
                    "workspace_id",
                    [""],
                )[0]
                or ""
            ).strip()

            if not workspace_id:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": "workspace_id_required",
                    },
                )
                return

            try:
                payload = (
                    self.server
                    .executions
                    .profiles(
                        workspace_id
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
            ) as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": "execution_profile_detection_rejected",
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.OK,
                payload,
            )
            return

        if path == "/executions":
            if not self._require_session():
                return

            self._send_json(
                HTTPStatus.OK,
                {
                    "protocol": EXECUTION_PROTOCOL,
                    "executions": (
                        self.server
                        .executions
                        .list()
                    ),
                },
            )
            return

        if path.startswith("/executions/"):
            if not self._require_session():
                return

            parts = path.strip("/").split("/")

            if len(parts) == 2:
                run_id = parts[1]

                try:
                    payload = (
                        self.server
                        .executions
                        .get(run_id)
                    )
                except KeyError:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "error": "execution_not_found",
                            "run_id": run_id,
                        },
                    )
                    return

                self._send_json(
                    HTTPStatus.OK,
                    payload,
                )
                return

        if path == "/git-stages":
            if not self._require_session():
                return

            workspace_id = query.get(
                "workspace_id",
                [None],
            )[0]

            self._send_json(
                HTTPStatus.OK,
                {
                    "protocol": GIT_STAGE_PROTOCOL,
                    "mode": GIT_STAGE_MODE,
                    "git_stages": (
                        self.server
                        .git_stages
                        .list(
                            workspace_id=workspace_id
                        )
                    ),
                },
            )
            return

        if path.startswith("/git-stages/"):
            if not self._require_session():
                return

            parts = path.strip("/").split("/")

            if len(parts) == 2:
                stage_id = parts[1]

                try:
                    proposal = (
                        self.server
                        .git_stages
                        .get(stage_id)
                    )

                except KeyError:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "error": (
                                "git_stage_not_found"
                            ),
                            "stage_id": stage_id,
                        },
                    )
                    return

                except ValueError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": (
                                "invalid_git_stage_id"
                            ),
                            "message": str(exc),
                        },
                    )
                    return

                self._send_json(
                    HTTPStatus.OK,
                    proposal,
                )
                return

        if path == "/git-commits":
            if not self._require_session():
                return

            workspace_id = query.get(
                "workspace_id",
                [None],
            )[0]

            self._send_json(
                HTTPStatus.OK,
                {
                    "protocol": (
                        GIT_COMMIT_PROTOCOL
                    ),
                    "mode": (
                        GIT_COMMIT_MODE
                    ),
                    "git_commits": (
                        self.server
                        .git_commits
                        .list(
                            workspace_id=(
                                workspace_id
                            )
                        )
                    ),
                },
            )
            return

        if path.startswith(
            "/git-commits/"
        ):
            if not self._require_session():
                return

            parts = (
                path.strip("/")
                .split("/")
            )

            if len(parts) == 2:
                commit_id = parts[1]

                try:
                    proposal = (
                        self.server
                        .git_commits
                        .get(commit_id)
                    )

                except KeyError:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "error": (
                                "git_commit_not_found"
                            ),
                            "commit_id": (
                                commit_id
                            ),
                        },
                    )
                    return

                except ValueError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": (
                                "invalid_git_commit_id"
                            ),
                            "message": str(
                                exc
                            ),
                        },
                    )
                    return

                self._send_json(
                    HTTPStatus.OK,
                    proposal,
                )
                return

        if path == "/mission/status":
            if not self._require_session():
                return
            try:
                record = self.server.mission_control.status(query.get("request_id", [None])[0])
            except (ValueError, KeyError) as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"error":"mission_status_not_found", "message":str(exc)})
                return
            self._send_json(HTTPStatus.OK, record)
            return

        if path == "/capabilities":
            if not self._require_session():
                return

            workspace_id = str(
                query.get(
                    "workspace_id",
                    [""],
                )[0]
                or ""
            ).strip()

            active = False
            workspace_kind = None

            if workspace_id:
                try:
                    workspace_row = (
                        self.server.workspaces.get(
                            workspace_id
                        )
                    )
                except KeyError:
                    workspace_row = None

                if (
                    workspace_row
                    and workspace_row.get("exists")
                ):
                    active = True
                    workspace_kind = str(
                        workspace_row.get("kind")
                        or ""
                    )

            self._send_json(
                HTTPStatus.OK,
                {
                    "capabilities": (
                        self.server
                        .capabilities
                        .snapshot(
                            workspace_active=active,
                            workspace_kind=workspace_kind,
                        )
                    )
                },
            )
            return

        if path == "/providers":
            self._send_json(
                HTTPStatus.OK,
                {"providers": self.server.core.runtime_inventory()},
            )
            return

        if path == "/models":
            inventory = self.server.core.runtime_inventory()
            resources = [
                f"{row['id']}::{model}"
                for row in inventory if row.get("selection_enabled")
                for model in row.get("models", [])
            ]
            self._send_json(HTTPStatus.OK, {
                "provider": "multi", "status": "online" if resources else "offline",
                "models": sorted(resources),
                "sources": len([row for row in inventory if row.get("selection_enabled")]),
                "primary": self.server.core.primary_intelligence.snapshot(),
                "autonomous_qualification": self.server.autonomous_qualification.status(),
                "native_model_admission": self.server.model_admission.status(),
                "operational_qualification_evidence": inspect_operational_evidence(
                    self.server.core.system.sovereign_intelligence.registry.all()),
                "qualification_evidence": [
                    {"resource_id": d.resource_id,
                     "languages": {lang: probe_evidence(d.resource_id,
                        model_version(d), lang) for lang in LANGUAGES}}
                    for d in self.server.core.system.sovereign_intelligence.registry.all()
                    if getattr(d, 'local', None) is True and any(
                        str(getattr(c, 'value', c)).upper() in ('DIRECT', 'GENERAL_REASONING')
                        for c in (getattr(d, 'capabilities', ()) or ()))
                ][:128],
            })
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

        if path == "/executions/propose":
            if not self._require_session():
                return

            try:
                payload = self._read_json()

                proposal = (
                    self.server
                    .executions
                    .propose(
                        workspace_id=str(
                            payload.get(
                                "workspace_id"
                            )
                            or ""
                        ),
                        kind=str(
                            payload.get(
                                "kind"
                            )
                            or ""
                        ),
                        profile=str(
                            payload.get(
                                "profile"
                            )
                            or ""
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
            ) as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": "execution_proposal_rejected",
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.CREATED,
                proposal,
            )
            return

        if path.startswith("/executions/"):
            parts = path.strip("/").split("/")

            if (
                len(parts) == 3
                and parts[0] == "executions"
                and parts[2] in {
                    "approve",
                    "reject",
                    "execute",
                }
            ):
                if not self._require_session():
                    return

                run_id = parts[1]
                action = parts[2]

                try:
                    payload = self._read_json()

                    if action == "approve":
                        proposal = (
                            self.server
                            .executions
                            .approve(
                                run_id
                            )
                        )

                    elif action == "execute":
                        proposal = (
                            self.server
                            .executions
                            .execute(
                                run_id,
                                confirm_execution=(
                                    payload.get(
                                        "confirm_execution"
                                    )
                                    is True
                                ),
                            )
                        )

                    else:
                        proposal = (
                            self.server
                            .executions
                            .reject(
                                run_id,
                                reason=(
                                    payload.get(
                                        "reason"
                                    )
                                ),
                            )
                        )

                except KeyError:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "error": "execution_not_found",
                            "run_id": run_id,
                        },
                    )
                    return

                except ExecutionStaleError as exc:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": "execution_proposal_stale",
                            "message": str(exc),
                            "proposal": exc.proposal,
                        },
                    )
                    return

                except ExecutionStateError as exc:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": "invalid_execution_state",
                            "message": str(exc),
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
                            "error": "execution_action_rejected",
                            "message": str(exc),
                        },
                    )
                    return

                self._send_json(
                    HTTPStatus.OK,
                    proposal,
                )
                return

        if path == "/git-commits/propose":
            if not self._require_session():
                return

            try:
                payload = self._read_json()

                proposal = (
                    self.server
                    .git_commits
                    .propose(
                        workspace_id=str(
                            payload.get(
                                "workspace_id"
                            )
                            or ""
                        ),
                        message=payload.get(
                            "message"
                        ),
                    )
                )

            except KeyError as exc:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": (
                            "workspace_not_found"
                        ),
                        "message": str(exc),
                    },
                )
                return

            except (
                ValueError,
                PermissionError,
                FileNotFoundError,
                TimeoutError,
            ) as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": (
                            "git_commit_proposal_"
                            "rejected"
                        ),
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.CREATED,
                proposal,
            )
            return

        if path.startswith(
            "/git-commits/"
        ):
            parts = (
                path.strip("/")
                .split("/")
            )

            if (
                len(parts) == 3
                and parts[0]
                == "git-commits"
                and parts[2]
                in {
                    "approve",
                    "reject",
                }
            ):
                if not self._require_session():
                    return

                commit_id = parts[1]
                action = parts[2]

                try:
                    payload = (
                        self._read_json()
                    )

                    if action == "approve":
                        proposal = (
                            self.server
                            .git_commits
                            .approve(
                                commit_id
                            )
                        )
                    else:
                        proposal = (
                            self.server
                            .git_commits
                            .reject(
                                commit_id,
                                reason=(
                                    payload.get(
                                        "reason"
                                    )
                                ),
                            )
                        )

                except KeyError:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "error": (
                                "git_commit_not_found"
                            ),
                            "commit_id": (
                                commit_id
                            ),
                        },
                    )
                    return

                except (
                    GitCommitConflictError
                ) as exc:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": (
                                "git_commit_proposal_"
                                "stale"
                            ),
                            "message": str(exc),
                            "proposal": (
                                exc.proposal
                            ),
                        },
                    )
                    return

                except (
                    GitCommitStateError
                ) as exc:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": (
                                "invalid_git_commit_"
                                "state"
                            ),
                            "message": str(exc),
                        },
                    )
                    return

                except (
                    ValueError,
                    PermissionError,
                    FileNotFoundError,
                    TimeoutError,
                ) as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": (
                                "git_commit_action_"
                                "rejected"
                            ),
                            "message": str(exc),
                        },
                    )
                    return

                self._send_json(
                    HTTPStatus.OK,
                    proposal,
                )
                return

        if path == "/git-stages/propose":
            if not self._require_session():
                return

            try:
                payload = self._read_json()

                proposal = (
                    self.server
                    .git_stages
                    .propose(
                        workspace_id=str(
                            payload.get(
                                "workspace_id"
                            )
                            or ""
                        ),
                        paths=payload.get(
                            "paths"
                        ),
                    )
                )

            except KeyError as exc:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": (
                            "workspace_not_found"
                        ),
                        "message": str(exc),
                    },
                )
                return

            except (
                ValueError,
                PermissionError,
                FileNotFoundError,
                TimeoutError,
            ) as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": (
                            "git_stage_proposal_"
                            "rejected"
                        ),
                        "message": str(exc),
                    },
                )
                return

            self._send_json(
                HTTPStatus.CREATED,
                proposal,
            )
            return

        if path.startswith("/git-stages/"):
            parts = path.strip("/").split("/")

            if (
                len(parts) == 3
                and parts[0] == "git-stages"
                and parts[2]
                in {
                    "approve",
                    "reject",
                    "apply",
                }
            ):
                if not self._require_session():
                    return

                stage_id = parts[1]
                action = parts[2]

                try:
                    payload = self._read_json()

                    if action == "approve":
                        proposal = (
                            self.server
                            .git_stages
                            .approve(
                                stage_id
                            )
                        )

                    elif action == "reject":
                        proposal = (
                            self.server
                            .git_stages
                            .reject(
                                stage_id,
                                reason=(
                                    payload.get(
                                        "reason"
                                    )
                                ),
                            )
                        )

                    else:
                        if (
                            payload.get(
                                "confirm_stage"
                            )
                            is not True
                        ):
                            raise ValueError(
                                "Git staging apply "
                                "requires literal "
                                "confirm_stage=true"
                            )

                        proposal = (
                            self.server
                            .git_stages
                            .apply(
                                stage_id,
                                confirm_stage=True,
                            )
                        )

                except KeyError:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "error": (
                                "git_stage_not_found"
                            ),
                            "stage_id": stage_id,
                        },
                    )
                    return

                except GitStageConflictError as exc:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": (
                                "git_stage_proposal_"
                                "stale"
                            ),
                            "message": str(exc),
                            "proposal": (
                                exc.proposal
                            ),
                        },
                    )
                    return

                except GitStageStateError as exc:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {
                            "error": (
                                "invalid_git_stage_"
                                "state"
                            ),
                            "message": str(exc),
                        },
                    )
                    return

                except GitStageApplyError as exc:
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {
                            "error": (
                                "git_stage_apply_failed"
                            ),
                            "message": str(exc),
                            "proposal": (
                                exc.proposal
                            ),
                        },
                    )
                    return

                except (
                    ValueError,
                    PermissionError,
                    FileNotFoundError,
                    TimeoutError,
                ) as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": (
                                "git_stage_action_"
                                "rejected"
                            ),
                            "message": str(exc),
                        },
                    )
                    return

                self._send_json(
                    HTTPStatus.OK,
                    proposal,
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
                        destination=str(
                            payload.get("destination") or ""
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
                FileExistsError,
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
                and parts[2] in {"approve", "reject", "apply"}
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
                            .approve(
                                change_id,
                                confirm_delete=(
                                    payload.get("confirm_delete") is True
                                ),
                            )
                        )
                    elif action == "reject":
                        proposal = (
                            self.server
                            .changes
                            .reject(
                                change_id,
                                reason=payload.get("reason"),
                            )
                        )
                    else:
                        proposal = (
                            self.server
                            .changes
                            .apply(change_id)
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
                except ProposalApplyError as exc:
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {
                            "error": "change_apply_failed",
                            "message": str(exc),
                            "proposal": exc.proposal,
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

        if path == "/software-project/verify-threejs":
            if not self._require_session():
                return
            try:
                payload = self._read_json()
                result = self.server.artifact_verifier.verify_threejs(
                    workspace_id=payload.get("workspace_id"),
                    change_ids=payload.get("change_ids"),
                    build_run_id=payload.get("build_run_id"),
                )
            except (ValueError, ArtifactVerificationError, KeyError) as exc:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {
                    "ok": False, "error": "artifact_proof_incomplete",
                    "message": str(exc), "semantic_truth_verified": False,
                })
                return
            self._send_json(HTTPStatus.OK, result)
            return

        if path == "/mission/cancel":
            if not self._require_session():
                return
            try:
                payload = self._read_json()
                record = self.server.mission_control.cancel(payload.get("request_id"))
            except (ValueError, KeyError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_cancel_request", "message": str(exc)})
                return
            self._send_json(HTTPStatus.OK, record)
            return

        if path == "/mission":
            if not self._require_session():
                return
            # Fail closed before request registration if a server was assembled
            # without the native admission barrier. In particular, do NOT
            # silently run Core when an adapter or legacy HTTP fixture omitted it.
            if not isinstance(getattr(self.server, "model_admission", None), NativeAdmissionBarrier):
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                    "error": "native_model_admission_unavailable",
                    "message": "Native mission admission was not initialized",
                })
                return

            try:
                payload = self._read_json()

                request_id = payload.get("request_id")
                result = None
                cancellation = None
                # Claim the bounded foreground lease FIRST, before any durable
                # MissionControl request ID registration. A rejected admission
                # must leave no queued/ghost receipt and must execute no Core.
                foreground_token = self.server.model_admission.begin_foreground(wait_seconds=2.0)
                try:
                    if request_id is not None:
                        # Stable HTTP identity must reach the durable DIRECT lease.
                        # MissionControl records the cancellation lifecycle separately.
                        try:
                            cancellation = self.server.mission_control.register(request_id)
                        except MissionConflictError:
                            # A completed request may be returned only after read-only
                            # verification of the durable request *and* conversation
                            # receipts. Never call the ordinary run() path here: if
                            # the request receipt is missing it could execute Core.
                            record = self.server.mission_control.status(request_id)
                            if (record["state"] == "completed"
                                    and payload.get("mode", "M0_DIRECT") == "M0_DIRECT"):
                                replayed = self.server.mission_runner.replay_completed_only(
                                    request_id=request_id,
                                    prompt=payload.get("prompt"),
                                    model=payload.get("model"),
                                    max_tokens=payload.get("max_tokens", 512),
                                    conversation_id=payload.get("conversation_id"),
                                    workspace_id=payload.get("workspace_id"),
                                    ui_language=payload.get("ui_language"),
                                )
                                self._send_json(
                                    HTTPStatus.OK if replayed.get("ok") else HTTPStatus.UNPROCESSABLE_ENTITY,
                                    replayed,
                                )
                                return
                            raise
                    foreground_engaged = False
                    try:
                        if request_id is not None:
                            self.server.mission_control.start(request_id)
                        self.server.autonomous_qualification.user_activity(True)
                        foreground_engaged = True
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
                            mode=payload.get(
                                "mode",
                                "M0_DIRECT",
                            ),
                            research_url=(
                                payload.get(
                                    "research_url"
                                )
                            ),
                            ui_language=payload.get("ui_language"),
                            cancellation=cancellation,
                            request_id=(request_id if payload.get("mode", "M0_DIRECT") == "M0_DIRECT"
                                        else None),
                        )
                        )
                    finally:
                        if foreground_engaged:
                            self.server.autonomous_qualification.user_activity(False)
                        if request_id is not None and cancellation is not None:
                            self.server.mission_control.finish(
                                request_id, success=bool(result and result.get("ok")),
                                cancelled=cancellation.is_set(),
                                durable_committed=bool(result and result.get("tx_committed")),
                            )
                finally:
                    # Includes duplicate completed replay, validation errors,
                    # cancellation, Core failure, and durable finish failures.
                    self.server.model_admission.end_foreground(foreground_token)
            except RequestReplayBlocked as exc:
                # A conflict is not proof of a failed or rolled-back Core TX.
                self._send_json(HTTPStatus.CONFLICT,
                                {"error": "mission_reconciliation_required", "message": str(exc)})
                return
            except ForegroundAdmissionBusy as exc:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {
                    "error": "native_model_admission_busy", "message": str(exc)})
                return
            except MissionConflictError as exc:
                self._send_json(HTTPStatus.CONFLICT, {"error":"duplicate_mission_request", "message":str(exc)})
                return
            except MissionCancelledError as exc:
                self._send_json(HTTPStatus.CONFLICT, {"error":"mission_cancelled", "message":str(exc)})
                return
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
    # The same local identity is used for all ports and all Gateway processes
    # sharing ~/.maestro/user/mission-controls. This path is NOT env-tunable.
    # A future authenticated installer must provision this directory (0700).
    # Until then failure to find an owner-only path deliberately blocks boot.
    owner_root = Path.home() / ".maestro" / "runtime" / "gateway-owner-p043b2j"
    owner_lease = GatewayOwnerLease(owner_root)
    owner_lease.__enter__()
    try:
        # Acquire journal ownership BEFORE any Core or journal construction,
        # socket bind, background worker, or route-history initialization.
        core = MaestroCoreBridge(config.runtime_dir)
        return GatewayHTTPServer(
            (config.host, config.port), GatewayHandler,
            config=config, core=core, owner_lease=owner_lease,
        )
    except BaseException:
        owner_lease.__exit__(None, None, None)
        raise


def main() -> int:
    config = GatewayConfig.from_env()
    server = build_server(config)

    print("=" * 72)
    print(
        "MAESTRO LOCAL GATEWAY — P0.6E.3A"
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
        "Workspace: governed writes + relocation + recoverable delete"
    )
    print(
        "Quality : anti-echo + recoverable tool guard enabled"
    )
    print(
        "Picker  : macOS native file/folder chooser"
    )
    print(
        "Changes : write/edit/patch + rename/move + recoverable delete"
    )
    print(
        "Mutate  : APPROVE != APPLY; RECOVERABLE DELETE ENABLED"
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
