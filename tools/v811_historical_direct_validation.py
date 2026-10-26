#!/usr/bin/env python3
"""Compare current MAESTRO Gateway DIRECT latency with the historical v1.0.7 contract.

This sends three real local missions and therefore writes normal MAESTRO
conversation/evidence state. It never selects a hard-coded model/provider and
never downloads or enables cloud resources.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request

SCENARIOS = (
    ("DIRECT-01", "Return exactly MAESTRO-DIRECT-OK and nothing else.", "MAESTRO-DIRECT-OK"),
    ("DIRECT-02", "Calcule 7 + 5. Réponds uniquement par le nombre.", "12"),
    ("DIRECT-03", "Réponds uniquement OUI si 4 est pair, sinon NON.", "OUI"),
)


def request(base: str, path: str, body: dict | None = None, session: str | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if session:
        headers["X-Maestro-Session"] = session
    req = urllib.request.Request(
        base.rstrip("/") + path, data=data, headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {payload[:500]}") from exc


def find_output(obj):
    if isinstance(obj, dict):
        for key in ("output", "response", "content", "text"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in obj.values():
            found = find_output(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_output(value)
            if found:
                return found
    return ""


def find_value(obj, wanted):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == wanted:
                return value
            found = find_value(value, wanted)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_value(value, wanted)
            if found is not None:
                return found
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    args = ap.parse_args()

    print("V8.11 HISTORICAL DIRECT VALIDATION")
    print("Historical evidence: qwen2.5-coder:1.5b mean 997.812 ms; warm calls 246.639/253.441 ms.")
    print("This run measures the CURRENT governed Gateway and does not hard-code a model.")
    session = request(args.base, "/session", {})
    session_token = session.get("session_token")
    if not isinstance(session_token, str) or len(session_token) < 16:
        raise RuntimeError("Gateway /session returned no valid session token")

    rows = []
    for label, prompt, expected in SCENARIOS:
        t0 = time.monotonic()
        result = request(args.base, "/mission", {
            "prompt": prompt,
            "ui_language": "fr" if label != "DIRECT-01" else "en",
            "max_tokens": 320,
        }, session=session_token)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        output = find_output(result)
        normalized = output.strip().rstrip(".\n ")
        passed = normalized.casefold() == expected.casefold()
        rid = find_value(result, "resource_id")
        lane = find_value(result, "direct_fast_lane")
        first = find_value(result, "first_token_ms")
        transport = find_value(result, "transport_wall_ms")
        rows.append(elapsed_ms)
        print(
            f"{label}: {'PASS' if passed else 'FAIL'} | {elapsed_ms:.3f} ms | "
            f"resource={rid} | fast_lane={lane} | first_token_ms={first} | "
            f"transport_ms={transport} | output={output!r}"
        )
        if not passed:
            return 2

    warm = rows[1:]
    print(f"ALL-3 MEAN MS : {statistics.mean(rows):.3f}")
    print(f"WARM MEAN MS  : {statistics.mean(warm):.3f}")
    print(f"WARM MAX MS   : {max(warm):.3f}")
    target = max(warm) < 1000.0
    print("WARM DIRECT < 1000 MS:", "PASS" if target else "NOT YET")
    return 0 if target else 3


if __name__ == "__main__":
    raise SystemExit(main())
