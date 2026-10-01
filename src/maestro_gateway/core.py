from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from maestro import ProductionGenesisSystem
from maestro.sovereign.discovery import SovereignDiscoveryBridge


class MaestroCoreBridge:
    def __init__(self, runtime_dir: Path):
        self._runtime_dir = runtime_dir
        self._boot_lock = threading.RLock()
        self._mission_lock = threading.RLock()
        self._system: ProductionGenesisSystem | None = None
        self._boot_result: dict[str, Any] | None = None
        self._ollama_imported = False

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

    def ensure_ollama_resources(self) -> int:
        with self._boot_lock:
            system = self.system

            if not self._ollama_imported:
                SovereignDiscoveryBridge().import_ollama(
                    system.sovereign_intelligence.registry,
                    system.local_intelligence.discovery,
                    system.local_intelligence.provider,
                )
                self._ollama_imported = True

            return len(system.sovereign_intelligence.registry.all())

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
