#!/usr/bin/env python3
"""Live V8.11.4 sovereign deterministic Core lane validation.

Writes normal local MAESTRO conversation/evidence state. No model/provider is
hard-coded. For locally provable tiny DIRECT tasks, the governed Core must
return exact truth without executing a model or triggering semantic recovery.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

SCENARIOS = (
    ("EXACT", "Return exactly MAESTRO-DIRECT-OK and nothing else.", "MAESTRO-DIRECT-OK"),
    ("ARITHMETIC", "Compute 7 + 5. Return only the integer.", "12"),
    ("CLASSIFICATION", "Return exactly OUI if 4 is even, otherwise return exactly NON.", "OUI"),
    ("EXTRACTION", "Extract the second item from alpha|beta|gamma. Return only it.", "beta"),
)


def request(base: str, path: str, body: dict, session: str | None = None):
    data = json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if session:
        headers["X-Maestro-Session"] = session
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method="POST")
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


def output(obj) -> str:
    value = obj.get("output") if isinstance(obj, dict) else None
    return value.strip() if isinstance(value, str) else ""


def normalized(value: str) -> str:
    return value.strip().strip('"\'` ').rstrip(".!? ").casefold()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    args = ap.parse_args()

    print("V8.11.4 SOVEREIGN DETERMINISTIC CORE LANE VALIDATION")
    print("Locally provable tiny DIRECT requests must execute with zero provider/model calls.")

    status, session_payload = request(args.base, "/session", {})
    if status != 200:
        raise RuntimeError(f"/session failed with HTTP {status}: {session_payload}")
    token = session_payload.get("session_token")
    if not isinstance(token, str) or len(token) < 16:
        raise RuntimeError("Gateway /session returned no valid session token")

    rows = []
    for label, prompt, expected in SCENARIOS:
        started = time.monotonic()
        status, result = request(args.base, "/mission", {
            "prompt": prompt,
            "ui_language": "en",
            "max_tokens": 320,
        }, token)
        elapsed = (time.monotonic() - started) * 1000.0
        text = output(result)
        correct = status == 200 and normalized(text) == normalized(expected)
        row = {
            "label": label,
            "elapsed": elapsed,
            "status": status,
            "result": result,
            "correct": correct,
            "output": text,
        }
        rows.append(row)
        print(
            f"{label}: HTTP {status} | {elapsed:.3f} ms | "
            f"primary={find(result, 'selected_primary_resource_id')} | "
            f"reason={find(result, 'primary_selection_reason')} | "
            f"origin={find(result, 'response_origin')} | "
            f"core={find(result, 'deterministic_core_execution')} | "
            f"provider_called={find(result, 'provider_model_called')} | "
            f"resource_calls={find(result, 'resource_calls')} | "
            f"semantic={find(result, 'semantic_subtask')} | "
            f"recovery={find(result, 'semantic_recovery_attempts')} | "
            f"truth_verified={find(result, 'semantic_truth_verified')} | "
            f"output={text!r}"
        )

    correctness = all(r["correct"] for r in rows)
    no_provider = all(
        find(r["result"], "provider_model_called") is False
        and find(r["result"], "resource_calls") == 0
        for r in rows
    )
    core_origin = all(
        find(r["result"], "deterministic_core_execution") is True
        and find(r["result"], "response_origin") == "local_deterministic_core"
        and find(r["result"], "semantic_truth_verified") is True
        for r in rows
    )
    no_recovery = all(
        find(r["result"], "semantic_recovery_attempts") == 0
        and find(r["result"], "semantic_recovery_trial_selected") is False
        for r in rows
    )
    max_ms = max(r["elapsed"] for r in rows)
    subsecond = max_ms < 1000.0

    print("MAX DETERMINISTIC DIRECT MS:", round(max_ms, 3))
    print("SYSTEM SEMANTIC CORRECTNESS:", "PASS" if correctness else "FAIL")
    print("ZERO PROVIDER/MODEL CALLS:", "PASS" if no_provider else "FAIL")
    print("LOCAL DETERMINISTIC CORE ORIGIN:", "PASS" if core_origin else "FAIL")
    print("NO SEMANTIC COLD-SWITCH:", "PASS" if no_recovery else "FAIL")
    print("DETERMINISTIC DIRECT < 1000 MS:", "PASS" if subsecond else "NOT YET")
    gates = correctness and no_provider and core_origin and no_recovery and subsecond
    print("V8.11.4 SOVEREIGN FAST GATE:", "PASS" if gates else "FAIL")
    return 0 if gates else 3


if __name__ == "__main__":
    raise SystemExit(main())
