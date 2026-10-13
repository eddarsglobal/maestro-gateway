from __future__ import annotations

import threading
import time
from maestro.sovereign.contracts import IntelligenceCapability, ResourceDescriptor, ResourceStatus
from maestro.sovereign.discovery import SovereignDiscoveryBridge
from .provider_inventory import discover_runtime_inventory
from .openai_local_adapter import OpenAICompatibleLocalAdapter
from .primary_intelligence import PrimaryIntelligenceManager
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from maestro import ProductionGenesisSystem
from maestro.providers.ollama.sovereign_discovery_bridge import (
    OllamaSovereignDiscoveryBridge,
)
from maestro.local_intelligence import LocalIntelligence
from maestro.providers.ollama.client import OllamaHTTPError
from maestro.research.web_fetch import (
    build_governed_web_fetch_binding,
)

from .ollama_gateway_client import GatewayOllamaClient


class MaestroCoreBridge:
    def __init__(self, runtime_dir: Path):
        self._runtime_dir = runtime_dir
        self._boot_lock = threading.RLock()
        self._mission_lock = threading.RLock()
        self._lane_guard = threading.Lock()
        self._conversation_lanes: dict[str, tuple[threading.Lock, int]] = {}
        self._system: ProductionGenesisSystem | None = None
        self.primary_intelligence = PrimaryIntelligenceManager()
        self._boot_result: dict[str, Any] | None = None
        self._ollama_imported = False
        self._last_catalog_refresh = 0.0
        self._catalog: list[dict[str, Any]] = []
        self._managed_local_ids: set[str] = set()
        self._catalog_refresh_thread: threading.Thread | None = None
        self._catalog_applied_at = 0.0

    def boot(self) -> dict[str, Any]:
        with self._boot_lock:
            if self._boot_result is not None:
                return self._boot_result

            self._runtime_dir.mkdir(parents=True, exist_ok=True)
            system = ProductionGenesisSystem(self._runtime_dir)
            result = system.start()

            if not isinstance(result, dict):
                raise RuntimeError(
                    "ProductionGenesisSystem.start() returned an unexpected value."
                )

            if result.get("status") != "READY":
                raise RuntimeError(
                    f"MAESTRO Core did not reach READY: {result!r}"
                )

            self._system = system
            self._boot_result = result
            return result

    @property
    def system(self) -> ProductionGenesisSystem:
        self.boot()
        assert self._system is not None
        return self._system

    @property
    def mission_lock(self) -> threading.RLock:
        return self._mission_lock

    @contextmanager
    def conversation_lane(
        self,
        conversation_id: str | None,
        cancellation: threading.Event | None,
        deadline: float,
    ) -> Iterator[None]:
        """Serialize requests for ONE conversation, not unrelated conversations.

        Identity-free new conversations receive a private lane. The lane table
        retains a lock while waiters exist and releases memory after the last
        user finishes. A cancelled queued call never enters the Core.
        """
        import uuid

        lane_id = conversation_id if conversation_id else "new:" + uuid.uuid4().hex
        with self._lane_guard:
            lock, users = self._conversation_lanes.get(
                lane_id, (threading.Lock(), 0)
            )
            self._conversation_lanes[lane_id] = (lock, users + 1)
        acquired = False
        try:
            while not acquired:
                if cancellation is not None and cancellation.is_set():
                    from .mission_control import MissionCancelledError
                    raise MissionCancelledError("Mission cancelled while queued for conversation")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Mission expired waiting for its conversation lane")
                acquired = lock.acquire(timeout=min(0.05, max(0.001, deadline-time.monotonic())))
            if cancellation is not None and cancellation.is_set():
                from .mission_control import MissionCancelledError
                raise MissionCancelledError("Mission cancelled before conversation execution")
            yield
        finally:
            if acquired:
                lock.release()
            with self._lane_guard:
                active, users = self._conversation_lanes[lane_id]
                assert active is lock and users > 0
                if users == 1:
                    del self._conversation_lanes[lane_id]
                else:
                    self._conversation_lanes[lane_id] = (lock, users-1)

    @contextmanager
    def unlocked_during_model_io(self) -> Iterator[None]:
        """Release only the current Core serialization lock during model I/O.

        CALLER MUST hold exactly one level of mission_lock. Shared runtime
        mutation, Council routing, durable TX, and conversation writes occur
        outside this scope while the Core lock is held. This does not authorize
        any external effect, and does not apply to research or tool execution.
        """
        self._mission_lock.release()
        try:
            yield
        finally:
            self._mission_lock.acquire()

    def prewarm_runtime_catalogue(self) -> None:
        """Launch passive concurrent discovery without booting the sovereign Core."""
        def load() -> None:
            catalogue = discover_runtime_inventory()
            with self._boot_lock:
                if not self._last_catalog_refresh:
                    self._catalog = catalogue
                    self._last_catalog_refresh = time.monotonic()
        worker = threading.Thread(target=load, daemon=True, name="MAESTRO-Runtime-Discovery")
        self._catalog_refresh_thread = worker
        worker.start()

    def ensure_runtime_resources(self, *, force: bool = False) -> int:
        """Synchronize a neutral local inference catalogue with the Core.

        All network probing takes place outside the governed mutation lock.
        Core Registry is mutated only while holding its serialization lock.
        Caller must NOT already hold mission_lock on a cold refresh.
        """
        with self._boot_lock:
            if not force and self._catalog_applied_at and time.monotonic() - self._catalog_applied_at < 30.0:
                return len(self.system.sovereign_intelligence.registry.all())
            catalogue = (self._catalog if not force and self._catalog and not self._catalog_applied_at else discover_runtime_inventory())
            system = self.system
            registry = system.sovereign_intelligence.registry
            new_bindings = []
            ollama_live = False
            for runtime in catalogue:
                if not runtime.get("selection_enabled"):
                    continue
                if runtime["id"] == "ollama":
                    ollama_live = True
                    continue
                for model_id in runtime["models"]:
                    if not model_id:
                        continue
                    adapter = OpenAICompatibleLocalAdapter(runtime["id"], model_id, runtime["url"])
                    descriptor = ResourceDescriptor(
                        resource_id=adapter.resource_id, provider_id=runtime["id"],
                        model_id=model_id, capabilities=frozenset({
                            IntelligenceCapability.DIRECT,
                            IntelligenceCapability.GENERAL_REASONING,
                        }), status=ResourceStatus.AVAILABLE, local=True,
                        metadata={"endpoint": runtime["url"], "readiness": "models_api_observed"},
                    )
                    new_bindings.append((descriptor, adapter))
            with self._mission_lock:
                for old_id in self._managed_local_ids:
                    registry.unregister(old_id)
                self._managed_local_ids.clear()
                self._managed_local_ids.update(SovereignDiscoveryBridge().register_bindings(registry, new_bindings))
                # The Ollama edge is optional and never necessary to reach READY.
                if ollama_live:
                    # Each refresh reconciles added, removed and disappeared
                    # Ollama models, exactly as for every other provider.
                    for item in registry.all():
                        if item.provider_id == "ollama":
                            registry.unregister(item.resource_id)
                    self._ollama_imported = False
                    local = LocalIntelligence.ollama(base_url="http://127.0.0.1:11434")
                    edge_client = GatewayOllamaClient(base_url="http://127.0.0.1:11434")
                    local.client = edge_client
                    local.discovery.client = edge_client
                    local.health.client = edge_client
                    local.embeddings.client = edge_client
                    local.provider.client = edge_client
                    try:
                        OllamaSovereignDiscoveryBridge().import_ollama(
                            registry, local.discovery, local.provider,
                        )
                    except OllamaHTTPError:
                        pass  # A race during detection; no provider is mandatory.
                    else:
                        system.local_intelligence = local
                        self._ollama_imported = True
                # Stale Ollama registrations must not survive an offline event.
                if not ollama_live and self._ollama_imported:
                    for item in registry.all():
                        if item.provider_id == "ollama":
                            registry.unregister(item.resource_id)
                    self._ollama_imported = False
                self._catalog = catalogue
                # V8: model digest changes invalidate low-trust autonomous
                # qualification evidence without any added Core dependency.
                from .autonomous_qualification import update_model_versions
                update_model_versions({
                    f"{row['id']}::{model}": digest
                    for row in catalogue for model, digest in
                    (row.get('model_fingerprints') or {}).items()
                })
                self._last_catalog_refresh = time.monotonic()
                self._catalog_applied_at = time.monotonic()
                self.primary_intelligence.reconcile(registry)
            return len(registry.all())

    def runtime_inventory(self) -> list[dict[str, Any]]:
        self.ensure_runtime_resources()
        return [dict(row) for row in self._catalog]

    def ensure_ollama_resources(self) -> int:
        """Compatibility alias, no longer grants Ollama priority."""
        return self.ensure_runtime_resources()

    def ensure_web_research(self):
        """
        Explicitly activate governed Web Research
        on the existing production runtime.

        This method is called only by an explicit
        Research mission. Normal Gateway boot and
        DIRECT missions remain Web-OFF.
        """

        with self._boot_lock:
            runtime = self.system.os.runtime

            if runtime.research is None:
                runtime.activate_web_research(
                    build_governed_web_fetch_binding()
                )

            if (
                runtime.research is None
                or runtime.research_evidence
                is None
            ):
                raise RuntimeError(
                    "Governed Web Research "
                    "activation failed."
                )

            return runtime

    def health(self) -> dict[str, Any]:
        result = self.boot()
        boot = result.get("boot") or {}

        return {
            "status": result.get("status", "UNKNOWN"),
            "version": result.get("version"),
            "core": "maestro-core",
            "councils": boot.get("genesis_councils"),
            "boot": {
                "kernel": boot.get("kernel"),
                "registry": boot.get("registry"),
                "unique_variables": boot.get("unique_variables"),
            },
        }
