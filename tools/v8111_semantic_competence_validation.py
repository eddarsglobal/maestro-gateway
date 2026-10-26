#!/usr/bin/env python3
"""Live V8.11.1 semantic-competence + subsecond DIRECT validation.

Writes normal local MAESTRO conversation/evidence state. No model/provider is
hard-coded. Incorrect deterministic answers must be rejected by the Gateway,
never delivered with HTTP 200.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

BASE_SCENARIOS = (
    ("DIRECT-01", "Return exactly MAESTRO-DIRECT-OK and nothing else.", "MAESTRO-DIRECT-OK"),
    ("DIRECT-02", "Compute 7 + 5. Return only the integer.", "12"),
)
CLASSIFICATION = (
    "CLASSIFICATION",
    "Return exactly OUI if 4 is even, otherwise return exactly NON.",
    "OUI",
)


def request(base: str, path: str, body: dict, session: str | None = None):
    data = json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if session:
        headers["X-Maestro-Session"] = session
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as res:
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
    for key in ("output", "response", "content", "text"):
        value = find(obj, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalized(value: str) -> str:
    return value.strip().strip('"\'` ').rstrip(".!? ").casefold()


def run_one(base: str, session: str, label: str, prompt: str, expected: str):
    started = time.monotonic()
    status, result = request(base, "/mission", {
        "prompt": prompt,
        "ui_language": "en",
        "max_tokens": 320,
    }, session)
    elapsed = (time.monotonic() - started) * 1000.0
    text = output(result)
    correct = status == 200 and normalized(text) == normalized(expected)
    wrong_delivered = status == 200 and bool(text) and not correct
    return {
        "label": label, "status": status, "elapsed_ms": elapsed,
        "result": result, "output": text, "correct": correct,
        "wrong_delivered": wrong_delivered,
    }


def show(row):
    result = row["result"]
    print(
        f"{row['label']}: HTTP {row['status']} | {row['elapsed_ms']:.3f} ms | "
        f"resource={find(result, 'resource_id')} | "
        f"reason={find(result, 'primary_selection_reason')} | "
        f"fast_lane={find(result, 'direct_fast_lane')} | "
        f"semantic={find(result, 'semantic_subtask')} | "
        f"first_token_ms={find(result, 'first_token_ms')} | "
        f"output={row['output']!r}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    ap.add_argument("--max-semantic-trials", type=int, default=4)
    args = ap.parse_args()
    limit = max(1, min(8, args.max_semantic_trials))

    print("V8.11.1 SEMANTIC COMPETENCE ROUTING VALIDATION")
    print("No hard-coded model. Wrong deterministic answers must fail closed.")
    status, session_payload = request(args.base, "/session", {})
    if status != 200:
        raise RuntimeError(f"/session failed with HTTP {status}: {session_payload}")
    token = session_payload.get("session_token")
    if not isinstance(token, str) or len(token) < 16:
        raise RuntimeError("Gateway /session returned no valid session token")

    warm = []
    for label, prompt, expected in BASE_SCENARIOS:
        row = run_one(args.base, token, label, prompt, expected)
        show(row)
        if row["wrong_delivered"] or not row["correct"]:
            print("BASE DIRECT CONTRACT: FAIL")
            return 2
        warm.append(row["elapsed_ms"])

    print("BASE WARM MAX MS:", round(max(warm), 3))
    print("BASE DIRECT < 1000 MS:", "PASS" if max(warm) < 1000 else "NOT YET")

    label, prompt, expected = CLASSIFICATION
    success = None
    rejected = 0
    for i in range(1, limit + 1):
        row = run_one(args.base, token, f"{label}-{i}", prompt, expected)
        show(row)
        if row["wrong_delivered"]:
            print("SEMANTIC GUARD: FAIL — wrong deterministic answer was delivered")
            return 4
        if row["correct"]:
            success = row
            break
        if row["status"] == 422:
            rejected += 1
            continue
        print("SEMANTIC RECOVERY: FAIL — unexpected non-success response")
        return 5

    if success is None:
        print(f"SEMANTIC GUARD: PASS (wrong answers not delivered; {rejected} rejected missions)")
        print("SEMANTIC CORRECT MODEL: NOT YET FOUND IN BOUNDED TRIALS")
        return 6

    print("SEMANTIC CORRECTNESS: PASS")
    print("SEMANTIC RECOVERY ATTEMPTS:", rejected)

    # Reuse the now-correct primary once more. This converts a one-off success
    # into a warm correctness/latency measurement and second real competence
    # observation for future routing.
    confirm = run_one(args.base, token, "CLASSIFICATION-WARM-CONFIRM", prompt, expected)
    show(confirm)
    if confirm["wrong_delivered"] or not confirm["correct"]:
        print("WARM SEMANTIC CONFIRMATION: FAIL")
        return 7
    print("WARM SEMANTIC CONFIRMATION: PASS")
    print("CORRECT WARM CLASSIFICATION < 1000 MS:",
          "PASS" if confirm["elapsed_ms"] < 1000 else "NOT YET")
    return 0 if max(warm) < 1000 and confirm["elapsed_ms"] < 1000 else 3


if __name__ == "__main__":
    raise SystemExit(main())
