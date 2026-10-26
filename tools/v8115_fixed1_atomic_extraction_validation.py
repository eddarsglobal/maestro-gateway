#!/usr/bin/env python3
from __future__ import annotations
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8787"
CASES = (
    ("FR-SEQUENCE-FIRST", "Dans alpha|beta|gamma, retourne uniquement le deuxième élément.", "fr", "beta"),
    ("EN-INSTRUCTION-FIRST", "Extract the second item from alpha|beta|gamma. Return only it.", "en", "beta"),
    ("EN-SEQUENCE-FIRST", "From alpha|beta|gamma, return only the second item.", "en", "beta"),
    ("FR-THIRD", "Dans alpha|beta|gamma, retourne uniquement le troisième élément.", "fr", "gamma"),
)

def post(path, body, session=None):
    data = json.dumps(body).encode()
    headers = {"Content-Type":"application/json", "Accept":"application/json"}
    if session:
        headers["X-Maestro-Session"] = session
    req = urllib.request.Request(BASE+path, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, json.loads(r.read().decode())

def find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            x = find(v, key)
            if x is not None:
                return x
    elif isinstance(obj, list):
        for v in obj:
            x = find(v, key)
            if x is not None:
                return x
    return None

def main():
    _, session_obj = post('/session', {})
    session = session_obj.get('session_token')
    print('V8.11.5 FIXED1 ATOMIC EXTRACTION ORDER-INDEPENDENCE VALIDATION')
    print('No model is required for these locally provable pipe-extraction requests.')
    ok_all = True
    for label, prompt, lang, expected in CASES:
        t0 = time.monotonic()
        try:
            status, result = post('/mission', {
                'prompt': prompt,
                'ui_language': lang,
                'max_tokens': 320,
            }, session)
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                result = json.loads(exc.read().decode())
            except Exception:
                result = {}
        elapsed = (time.monotonic()-t0)*1000
        output = str(result.get('output') or '').strip()
        resource = result.get('resource_id')
        calls = find(result, 'resource_calls')
        origin = find(result, 'response_origin')
        truth = result.get('semantic_truth_verified')
        passed = (
            status == 200 and output == expected and resource is None and
            calls == 0 and origin == 'local_deterministic_core' and truth is True
        )
        ok_all = ok_all and passed
        print(f"{label}: {'PASS' if passed else 'FAIL'} | HTTP {status} | {elapsed:.3f} ms | output={output!r} | resource={resource!r} | calls={calls!r} | origin={origin!r} | truth={truth!r}")
    print('ATOMIC EXTRACTION ORDER-INDEPENDENCE:', 'PASS' if ok_all else 'FAIL')
    raise SystemExit(0 if ok_all else 1)

if __name__ == '__main__':
    main()
