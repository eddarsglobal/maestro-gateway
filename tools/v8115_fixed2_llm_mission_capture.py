#!/usr/bin/env python3
"""Observe three LLM missions on loopback; capture is not semantic certification."""
from __future__ import annotations
import json, urllib.request, urllib.error

BASE = 'http://127.0.0.1:8787'
CASES = (
    ('FR-RAM-SSD', 'fr', 'Explique-moi simplement en deux phrases la différence entre la RAM et le stockage SSD.'),
    ('DE-RAM-SSD', 'de', 'Antworte auf Deutsch: Was ist der Unterschied zwischen RAM und SSD? Zwei kurze Sätze.'),
    ('FR-LOCAL-WEB', 'fr', 'Donne-moi trois étapes pratiques pour vérifier qu’un service web fonctionne entièrement en local sur mon ordinateur, puis cite une limite.'),
)

def post(path, payload, token=None):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if token:
        headers['X-Maestro-Session'] = token
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode('utf-8'),
                                 headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.status, json.loads(response.read().decode('utf-8'))

def main():
    print('FIXED2 — LLM MISSION OBSERVATIONS (ISOLATED AND SHARED CONTEXT)')
    print('This is evidence collection ONLY. No automatic semantic pass is claimed.')
    all_ok = True
    for mode in ('isolated', 'shared'):
        shared = None
        if mode == 'shared':
            _, session = post('/session', {})
            shared = session.get('session_token')
        print(f'=== {mode.upper()} SESSIONS ===')
        for label, lang, prompt in CASES:
            try:
                if mode == 'isolated':
                    _, session = post('/session', {})
                    token = session.get('session_token')
                else:
                    token = shared
                if not token:
                    raise ValueError('missing session_token')
                status, result = post('/mission', {
                    'prompt': prompt, 'ui_language': lang, 'max_tokens': 320,
                }, token)
                output = str(result.get('output') or '').strip()
                print(f'{label} | HTTP {status} | output={json.dumps(output, ensure_ascii=False)}')
                if status != 200 or not output:
                    all_ok = False
            except (urllib.error.URLError, ValueError) as exc:
                all_ok = False
                print(f'{label} | ERROR {exc}')
    print('GATEWAY CAPTURE:', 'COLLECTED' if all_ok else 'INCOMPLETE')
    print('LLM QUALITY CERTIFICATION: NOT PERFORMED — requires factual, linguistic and context review.')
    raise SystemExit(0 if all_ok else 1)

if __name__ == '__main__':
    main()
