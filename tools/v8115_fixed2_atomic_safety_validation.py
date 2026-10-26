#!/usr/bin/env python3
"""Live loopback-only zero-model smoke test; never certifies LLM quality."""
from __future__ import annotations
import json, time, urllib.request, urllib.error

BASE = 'http://127.0.0.1:8787'
CASES = (
    ('FR-USER-EXACT', 'Dans alpha|beta|gamma, retourne uniquement le deuxième élément.', 'fr', 'beta'),
    ('EN-INSTRUCTION-FIRST', 'Extract the second item from alpha|beta|gamma. Return only it.', 'en', 'beta'),
    ('EN-SEQUENCE-FIRST', 'From alpha|beta|gamma, return only the second item.', 'en', 'beta'),
    ('FR-THIRD', 'Dans alpha|beta|gamma, retourne uniquement le troisième élément.', 'fr', 'gamma'),
    ('FR-NEGATED-FIRST', 'Dans alpha|beta|gamma, retourne le deuxième élément, pas le premier.', 'fr', 'beta'),
    ('FR-MULTIPLE-LITERALS', 'Voici un exemple x|y|z. Dans alpha|beta|gamma, retourne le deuxième élément.', 'fr', 'beta'),
    ('FR-NUMERIC-ORDINAL', 'Dans alpha|beta|gamma, retourne le 2e élément.', 'fr', 'beta'),
    ('EN-NEGATED-FIRST', 'From alpha|beta|gamma, return the second item, not the first item.', 'en', 'beta'),
)

def post(path, payload, token=None):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if token:
        headers['X-Maestro-Session'] = token
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode('utf-8'),
                                 headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode('utf-8'))

def find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = find(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find(v, key)
            if found is not None:
                return found
    return None

def main():
    try:
        code, sess = post('/session', {})
        token = sess.get('session_token')
        if code != 200 or not token:
            raise ValueError('Gateway did not return an authenticated local session')
    except (urllib.error.URLError, ValueError) as exc:
        print(f'GATEWAY NOT READY: {exc}')
        raise SystemExit(2)
    print('MAESTRO V8.11.5 FIXED2 — LIVE ZERO-MODEL EXTRACTION')
    ok = True
    for label, prompt, lang, expected in CASES:
        started = time.monotonic()
        try:
            status, data = post('/mission', {
                'prompt': prompt, 'ui_language': lang, 'max_tokens': 320,
            }, token)
        except (urllib.error.URLError, ValueError) as exc:
            print(f'{label}: FAIL ({exc})')
            ok = False
            continue
        observed = str(data.get('output') or '').strip()
        calls = find(data, 'resource_calls')
        origin = find(data, 'response_origin')
        truth = data.get('semantic_truth_verified')
        passed = (status == 200 and observed == expected and data.get('resource_id') is None
                  and calls == 0 and origin == 'local_deterministic_core' and truth is True)
        ok &= passed
        print(f"{label}: {'PASS' if passed else 'FAIL'} | {observed!r} | calls={calls!r} | "
              f"origin={origin!r} | truth={truth!r} | {(time.monotonic()-started)*1000:.1f}ms")
    print('FIXED2 LIVE ZERO-MODEL EXTRACTION:', 'PASS' if ok else 'FAIL')
    print('NOTE: LLM factual accuracy, UI behavior and cross-mission context are NOT validated here.')
    raise SystemExit(0 if ok else 1)

if __name__ == '__main__':
    main()
