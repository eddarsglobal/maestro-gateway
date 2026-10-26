#!/usr/bin/env python3
"""V8.11.5 live + isolated model-churn certification.

The live part sends one normal governed atomic mission to the running Gateway.
The isolated part uses a temporary MAESTRO runtime and synthetic adapters only:
it does NOT install, delete, download, start, or stop any real model/provider.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = GATEWAY_ROOT.parent / "maestro" / "src"
for entry in (GATEWAY_ROOT / "src", CORE_SRC):
    value = str(entry)
    if value not in sys.path:
        sys.path.insert(0, value)

from maestro.sovereign.contracts import (  # noqa: E402
    IntelligenceCapability,
    ResourceDescriptor,
    ResourceStatus,
)
from maestro_gateway.capabilities import CapabilityRegistry  # noqa: E402
from maestro_gateway.conversations import ConversationStore  # noqa: E402
from maestro_gateway.core import MaestroCoreBridge  # noqa: E402
from maestro_gateway.mission import GovernedDirectMissionRunner  # noqa: E402
from maestro_gateway.workspace import WorkspaceRegistry  # noqa: E402


class TrapAdapter:
    def __init__(self, provider_id: str, model_id: str):
        self.provider_id = provider_id
        self.model_id = model_id
        self.resource_id = f"{provider_id}::{model_id}"
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        raise RuntimeError("V8.11.5 trap adapter must never execute on atomic Core lane")


def descriptor(provider_id: str, model_id: str) -> ResourceDescriptor:
    return ResourceDescriptor(
        resource_id=f"{provider_id}::{model_id}",
        provider_id=provider_id,
        model_id=model_id,
        capabilities=frozenset({
            IntelligenceCapability.DIRECT,
            IntelligenceCapability.GENERAL_REASONING,
        }),
        status=ResourceStatus.AVAILABLE,
        local=True,
        metadata={"certification": "V8.11.5"},
    )


def request(base: str, path: str, body: dict, session: str | None = None):
    data = json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if session:
        headers["X-Maestro-Session"] = session
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw[:1000]}
        return exc.code, payload
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Gateway unavailable at " + base + ". Start maestro_gateway first."
        ) from exc


def find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = find(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find(value, key)
            if found is not None:
                return found
    return None


def make_runner(root: Path):
    core = MaestroCoreBridge(root / "runtime")
    core.ensure_runtime_resources = lambda **kwargs: len(
        core.system.sovereign_intelligence.registry.all()
    )
    runner = GovernedDirectMissionRunner(
        core,
        ConversationStore(root / "conversations"),
        WorkspaceRegistry(root / "workspaces.json"),
        CapabilityRegistry(),
    )
    return core, runner


def atomic(runner: GovernedDirectMissionRunner) -> dict:
    return runner.run(
        prompt="Compute 7 + 5. Return only the integer.", ui_language="en"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    args = ap.parse_args()

    print("V8.11.5 MODEL CHURN + ATOMIC INDEPENDENCE VALIDATION")
    print("No real model is installed, removed, downloaded, started, or stopped by this validator.")

    status, session_payload = request(args.base, "/session", {})
    if status != 200:
        raise RuntimeError(f"/session failed with HTTP {status}: {session_payload}")
    token = session_payload.get("session_token")
    if not isinstance(token, str) or len(token) < 16:
        raise RuntimeError("Gateway /session returned no valid session token")

    started = time.monotonic()
    status, live = request(
        args.base,
        "/mission",
        {"prompt": "Compute 7 + 5. Return only the integer.", "ui_language": "en", "max_tokens": 320},
        token,
    )
    live_ms = (time.monotonic() - started) * 1000.0
    live_pass = (
        status == 200
        and str(live.get("output") or "").strip() == "12"
        and find(live, "resource_calls") == 0
        and find(live, "provider_model_called") is False
        and find(live, "response_origin") == "local_deterministic_core"
        and find(live, "model_independent_execution") is True
        and find(live, "model_inventory_required") is False
        and find(live, "selected_primary_resource_id") is None
        and find(live, "primary_selection_reason") == "deterministic_core_no_model_required"
        and live_ms < 1000.0
    )
    print(
        "LIVE ATOMIC: HTTP %s | %.3f ms | resource=%r | primary=%r | calls=%r | "
        "origin=%r | inventory_required=%r | output=%r"
        % (
            status,
            live_ms,
            live.get("resource_id"),
            find(live, "selected_primary_resource_id"),
            find(live, "resource_calls"),
            find(live, "response_origin"),
            find(live, "model_inventory_required"),
            live.get("output"),
        )
    )
    print("LIVE ATOMIC MODEL-INDEPENDENCE:", "PASS" if live_pass else "FAIL")

    with tempfile.TemporaryDirectory(prefix="maestro-v8115-churn-") as td:
        root = Path(td)
        core, runner = make_runner(root)
        registry = core.system.sovereign_intelligence.registry

        # A present.
        a_desc = descriptor("future-alpha", "model-A")
        a_adapter = TrapAdapter(a_desc.provider_id, a_desc.model_id)
        registry.register(a_desc, a_adapter)
        a_result = atomic(runner)

        # A removed -> ZERO MODEL.
        registry.unregister(a_desc.resource_id)
        core.primary_intelligence.reconcile(registry)
        zero_result = atomic(runner)

        # Completely unrelated unknown future provider/model appears.
        z_desc = descriptor("future-provider-2099", "model-Z-2099")
        z_adapter = TrapAdapter(z_desc.provider_id, z_desc.model_id)
        registry.register(z_desc, z_adapter)
        z_result = atomic(runner)

        churn_outputs = [a_result.get("output"), zero_result.get("output"), z_result.get("output")]
        churn_pass = (
            churn_outputs == ["12", "12", "12"]
            and a_adapter.calls == 0
            and z_adapter.calls == 0
            and all(find(r, "resource_calls") == 0 for r in (a_result, zero_result, z_result))
            and all(r.get("resource_id") is None for r in (a_result, zero_result, z_result))
        )
        print(
            "SIMULATED CHURN A -> ZERO -> Z: outputs=%r | adapter_calls=(%d,%d) | %s"
            % (churn_outputs, a_adapter.calls, z_adapter.calls, "PASS" if churn_pass else "FAIL")
        )

        # ZERO MODEL must not invent generative intelligence.
        registry.unregister(z_desc.resource_id)
        open_fail_closed = False
        try:
            runner.run(prompt="Explain quantum gravity in depth.", ui_language="en")
        except RuntimeError as exc:
            open_fail_closed = "Aucun modèle" in str(exc)
        print("ZERO-MODEL GENERATIVE FAIL-CLOSED:", "PASS" if open_fail_closed else "FAIL")

    # Reconciliation proof uses synthetic inventory only; no host runtime is touched.
    inv_a = [{
        "id": "future-local", "name": "Future Local", "url": "http://127.0.0.1:19999",
        "status": "online", "models": ["model-A"], "inference_supported": True,
        "selection_enabled": True, "installed": True, "reason": "certification",
        "model_fingerprints": {},
    }]
    inv_z = [{
        "id": "future-local", "name": "Future Local", "url": "http://127.0.0.1:19999",
        "status": "online", "models": ["model-Z-2099"], "inference_supported": True,
        "selection_enabled": True, "installed": True, "reason": "certification",
        "model_fingerprints": {},
    }]
    with tempfile.TemporaryDirectory(prefix="maestro-v8115-refresh-") as td:
        refresh_core = MaestroCoreBridge(Path(td) / "runtime")
        with patch(
            "maestro_gateway.core.discover_runtime_inventory",
            side_effect=[inv_a, inv_z, []],
        ):
            n_a = refresh_core.ensure_runtime_resources(force=True)
            reg = refresh_core.system.sovereign_intelligence.registry
            ids_a = [d.resource_id for d in reg.all()]
            selected = refresh_core.primary_intelligence.select(reg, "Hello", ui_language="en")
            n_z = refresh_core.ensure_runtime_resources(force=True)
            ids_z = [d.resource_id for d in reg.all()]
            after_remove = refresh_core.primary_intelligence.snapshot()
            n_zero = refresh_core.ensure_runtime_resources(force=True)
            ids_zero = [d.resource_id for d in reg.all()]
        refresh_pass = (
            n_a == 1
            and ids_a == ["future-local::model-A"]
            and selected.decision.resource_id == "future-local::model-A"
            and n_z == 1
            and ids_z == ["future-local::model-Z-2099"]
            and after_remove["primary_resource_id"] is None
            and after_remove["reason"] == "resource_disappeared"
            and n_zero == 0
            and ids_zero == []
        )
        print(
            "SYNTHETIC INVENTORY RECONCILIATION A -> Z -> ZERO:",
            "PASS" if refresh_pass else "FAIL",
        )

    all_pass = live_pass and churn_pass and open_fail_closed and refresh_pass
    print("ATOMIC RESULT INDEPENDENT OF MODEL INVENTORY:", "PASS" if churn_pass else "FAIL")
    print("ZERO-MODEL ATOMIC EXECUTION:", "PASS" if churn_pass else "FAIL")
    print("REMOVED PRIMARY INVALIDATED:", "PASS" if refresh_pass else "FAIL")
    print("NEW RESOURCE DISCOVERABLE:", "PASS" if refresh_pass else "FAIL")
    print("DYNAMIC REGISTER / UNREGISTER:", "PASS" if churn_pass else "FAIL")
    print("DETERMINISTIC ATOMIC < 1000 MS:", "PASS" if live_ms < 1000.0 else "NOT YET")
    print("V8.11.5 MODEL CHURN + ATOMIC INDEPENDENCE GATE:", "PASS" if all_pass else "FAIL")
    return 0 if all_pass else 3


if __name__ == "__main__":
    raise SystemExit(main())
