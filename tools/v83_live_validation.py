#!/usr/bin/env python3
"""MAESTRO V8.3: explicit, local-only live operational assessment.

--inspect makes one passive /models request. --run-live performs real foreground
missions, which may load local models and persist MAESTRO conversations and
operational qualifications. It does NOT select a provider/model or authorize
workspaces/cloud, and cannot verify semantic quality automatically.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

LANGUAGES = ('en', 'fr', 'de', 'ar', 'pt', 'es', 'hr', 'it')
# Same substantive task across languages; all responses require human review.
PROMPTS = {
    'en': 'Explain in three practical steps how to check that a web service works entirely on my computer, without cloud services or downloading software. Mention one limitation.',
    'fr': 'Explique en trois étapes pratiques comment vérifier qu’un service web fonctionne entièrement sur mon ordinateur, sans cloud ni téléchargement. Cite une limite.',
    'de': 'Erkläre in drei praktischen Schritten, wie man einen Webdienst ausschließlich auf dem eigenen Computer ohne Cloud oder Downloads überprüft. Nenne eine Grenze.',
    'ar': 'اشرح في ثلاث خطوات عملية كيفية التحقق من أن خدمة ويب تعمل محلياً على حاسوبي دون سحابة أو تنزيل برامج. اذكر قيداً واحداً.',
    'pt': 'Explica em três passos práticos como verificar um serviço web inteiramente no meu computador, sem cloud nem descarregar programas. Indica uma limitação.',
    'es': 'Explica en tres pasos prácticos cómo comprobar que un servicio web funciona enteramente en mi ordenador, sin nube ni descargar programas. Indica una limitación.',
    'hr': 'Objasni u tri praktična koraka kako provjeriti radi li web-usluga samo na mom računalu, bez oblaka i preuzimanja programa. Navedi jedno ograničenje.',
    'it': 'Spiega in tre passaggi pratici come verificare che un servizio web funzioni solo sul mio computer, senza cloud o scaricare programmi. Indica un limite.',
}
MAX_HTTP_RESPONSE = 4 * 1024 * 1024


class ProbeError(RuntimeError):
    pass


def validate_url(value: str) -> str:
    """Require a literal loopback IP; avoid proxy/DNS rebinding/credential URLs."""
    try:
        p = urlsplit(value)
        port = p.port
    except ValueError as exc:
        raise ProbeError('Invalid local Gateway URL') from exc
    if (p.scheme != 'http' or p.hostname not in ('127.0.0.1', '::1')
            or not port or not 1 <= port <= 65535 or p.username or p.password
            or p.query or p.fragment or p.path not in ('', '/')):
        raise ProbeError('Only http://127.0.0.1:PORT or http://[::1]:PORT is allowed')
    return f'http://{p.netloc}'


def parse_languages(value: str) -> tuple[str, ...]:
    names = tuple(part.strip() for part in value.split(','))
    if not names or any(item not in LANGUAGES for item in names) or len(set(names)) != len(names):
        raise ProbeError('Languages must be a unique comma-separated subset of: ' + ','.join(LANGUAGES))
    return names


class LocalGatewayClient:
    def __init__(self, url: str, timeout: float = 120.0):
        self.url = validate_url(url)
        if not (1.0 <= timeout <= 240.0):
            raise ProbeError('Timeout must be 1..240 seconds')
        self.timeout = timeout
        # Explicitly disable environment HTTP proxies for local-only requests.
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path: str, *, body: dict | None = None,
                session: str | None = None) -> dict:
        if path not in ('/models', '/session', '/mission'):
            raise ProbeError('Unsupported Gateway path')
        headers = {'Accept': 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if session:
            headers['X-Maestro-Session'] = session
        req = Request(self.url + path, data=data, headers=headers,
                      method='POST' if data is not None else 'GET')
        try:
            with self.opener.open(req, timeout=self.timeout if path == '/mission' else min(self.timeout, 8)) as response:
                content = response.read(MAX_HTTP_RESPONSE + 1)
                status = response.status
        except HTTPError as exc:
            # HTTP 422 is the expected envelope for failed/blocked missions.
            if path != '/mission' or exc.code != 422:
                raise ProbeError(f'{path} returned HTTP {exc.code}') from None
            content = exc.read(MAX_HTTP_RESPONSE + 1)
            status = exc.code
        except (URLError, OSError, TimeoutError) as exc:
            raise ProbeError(f'{path} transport failure: {type(exc).__name__}') from None
        if len(content) > MAX_HTTP_RESPONSE:
            raise ProbeError('Gateway response exceeds 4 MiB')
        try:
            obj = json.loads(content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProbeError('Gateway returned invalid JSON') from exc
        if not isinstance(obj, dict):
            raise ProbeError('Gateway returned a non-object JSON value')
        if status != 200 and path != '/mission':
            raise ProbeError(f'{path} failed with HTTP {status}')
        return obj


def inspect_inventory(data: dict) -> tuple[set[str], dict]:
    primary = data.get('primary')
    resources = data.get('models')
    if not isinstance(primary, dict) or not isinstance(resources, list):
        raise ProbeError('Invalid /models schema')
    if primary.get('cloud_enabled') is not False:
        raise ProbeError('Cloud/locality invariant missing from /models')
    if primary.get('mode') != 'single_local_primary':
        raise ProbeError('Unexpected primary manager mode')
    if any(not isinstance(model, str) or not 1 <= len(model) <= 256 for model in resources):
        raise ProbeError('Invalid local model descriptor')
    return set(resources), primary


def assess_result(result: dict, allowed: set[str], language: str, elapsed: float,
                  *, keep_output: bool = False) -> dict:
    metrics = result.get('metrics')
    metrics = metrics if isinstance(metrics, dict) else {}
    rid = result.get('resource_id')
    response = result.get('output')
    valid_resource = isinstance(rid, str) and rid in allowed
    valid_output = isinstance(response, str) and bool(response.strip())
    guarded = result.get('semantic_truth_verified') is False
    local_origin = metrics.get('response_origin') == 'local_model'
    language_reported = metrics.get('routing_language')
    correct_language_routing = language_reported == language
    passed = (result.get('ok') is True and valid_resource and valid_output
              and guarded and local_origin and correct_language_routing)
    details = {
        'language': language, 'operational_pass': passed,
        'gateway_ok': result.get('ok') is True,
        'resource_id': rid if valid_resource else None,
        'local_resource_verified': valid_resource,
        'model_output_nonempty': valid_output,
        'response_origin_local': local_origin,
        'language_routing_matches': correct_language_routing,
        'semantic_truth_verified': False,
        'human_reviewed': False, 'human_semantic_acceptance': None,
        'elapsed_s': round(elapsed, 3),
        'gateway_wall_ms': metrics.get('wall_ms'),
        'first_token_ms': metrics.get('first_token_ms'),
        'transport_wall_ms': metrics.get('transport_wall_ms'),
        'decode_after_first_ms': metrics.get('decode_after_first_ms'),
        'generated_characters': metrics.get('generated_characters'),
        'stream_fragments': metrics.get('stream_fragments'),
        'characters_per_second_after_first': metrics.get('characters_per_second_after_first'),
        'adaptive_num_ctx': metrics.get('adaptive_num_ctx'),
        'adaptive_num_predict': metrics.get('adaptive_num_predict'),
        'residency_keep_alive': metrics.get('residency_keep_alive'),
        'early_completion': metrics.get('early_completion'),
        'prompt_tokens': metrics.get('prompt_tokens'),
        'generated_tokens': metrics.get('generated_tokens'),
        'tokens_per_second': metrics.get('tokens_per_second'),
        'load_ms': metrics.get('load_ms'),
        'primary_selection_reason': metrics.get('primary_selection_reason'),
        'primary_selection_epoch': metrics.get('primary_selection_epoch'),
        'error_category': None if result.get('ok') is True else 'gateway_mission_rejected',
        'output_sha256': hashlib.sha256(response.encode('utf-8')).hexdigest() if isinstance(response, str) else None,
        'output_characters': len(response) if isinstance(response, str) else 0,
    }
    if keep_output and isinstance(response, str):
        details['output_for_human_review'] = response[:12000]
        details['output_truncated_in_report'] = len(response) > 12000
    return details


def write_private_report(directory: Path, data: dict) -> Path:
    if directory.is_symlink():
        raise ProbeError('Refusing a symlinked report directory')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    filename = 'V83-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4) + '.json'
    path = directory / filename
    blob = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(blob)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def run_live(client: LocalGatewayClient, languages: tuple[str, ...], *,
             max_tokens: int = 320, keep_output: bool = False,
             report_dir: Path | None = None) -> tuple[dict, Path]:
    if not 64 <= max_tokens <= 2048:
        raise ProbeError('Response token budget must be 64..2048')
    initial = client.request('/models')
    available, primary_before = inspect_inventory(initial)
    if not available:
        raise ProbeError('No local model currently selectable. No missions were submitted.')
    session = client.request('/session', body={}).get('session_token')
    if not isinstance(session, str) or len(session) < 16:
        raise ProbeError('Gateway did not provide a valid session token')
    rows = []
    for language in languages:
        prompt = PROMPTS[language]
        start = time.monotonic()
        try:
            result = client.request('/mission', body={'prompt': prompt, 'ui_language': language,
                                                        'max_tokens': max_tokens}, session=session)
            rows.append(assess_result(result, available, language,
                                      time.monotonic() - start, keep_output=keep_output))
        except ProbeError as exc:
            rows.append({'language': language, 'operational_pass': False,
                         'error_category': str(exc), 'elapsed_s': round(time.monotonic() - start, 3),
                         'human_reviewed': False, 'human_semantic_acceptance': None})
        print(f"{language}: {'OPERATIONAL PASS' if rows[-1]['operational_pass'] else 'FAILED'} "
              f"({rows[-1]['elapsed_s']:.2f}s)", flush=True)
    final_error = None
    try:
        last = client.request('/models')
        _, primary_after = inspect_inventory(last)
    except ProbeError as exc:
        primary_after = {}
        final_error = str(exc)
    report = {
        'schema': 'maestro-v8.3-live-validation-1',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'source': 'actual MAESTRO local Gateway /mission responses',
        'gateway_url': client.url, 'model_count_at_start': len(available),
        'primary_before': primary_before.get('primary_resource_id'),
        'primary_after': primary_after.get('primary_resource_id'),
        'primary_epoch_before': primary_before.get('epoch'),
        'primary_epoch_after': primary_after.get('epoch'),
        'final_inventory_error': final_error,
        'cloud_enabled': False, 'model_override_used': False,
        'real_inference_executed': True,
        'semantic_language_quality_certified': False,
        'human_review_required': True,
        'operational_pass': final_error is None and all(row['operational_pass'] for row in rows),
        'results': rows,
    }
    if report_dir is None:
        report_dir = Path.home() / '.maestro' / 'v83_reports'
    path = write_private_report(report_dir, report)
    return report, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--inspect', action='store_true', help='Passive GET /models only')
    action.add_argument('--run-live', action='store_true', help='Explicitly execute real local missions')
    parser.add_argument('--url', default='http://127.0.0.1:8787')
    parser.add_argument('--languages', default=','.join(LANGUAGES))
    parser.add_argument('--timeout', type=float, default=120.0, help='Per-mission wall time, max 240s')
    parser.add_argument('--max-tokens', type=int, default=320)
    parser.add_argument('--keep-output', action='store_true', help='Privately save model output for human review')
    parser.add_argument('--report-dir', type=Path, default=None)
    opts = parser.parse_args(argv)
    try:
        client = LocalGatewayClient(opts.url, opts.timeout)
        languages = parse_languages(opts.languages)
        if opts.inspect:
            models, primary = inspect_inventory(client.request('/models'))
            print('V8.3 LOCAL GATEWAY INSPECTION: PASS')
            print('Selectable models:', len(models))
            print('Primary:', primary.get('primary_resource_id'))
            print('Cloud enabled:', primary.get('cloud_enabled'))
            print('No missions sent. No model launched or downloaded by this inspector.')
            return 0
        print('V8.3 LIVE MISSIONS: writes to MAESTRO local conversation/evidence state.')
        report, path = run_live(client, languages, max_tokens=opts.max_tokens,
                                keep_output=opts.keep_output, report_dir=opts.report_dir)
        print('Private report:', path)
        print('Semantic/language quality: HUMAN REVIEW REQUIRED; not certified.')
        print('V8.3 OPERATIONAL:', 'PASS' if report['operational_pass'] else 'FAILED')
        return 0 if report['operational_pass'] else 1
    except (ProbeError, OSError, ValueError) as exc:
        print('V8.3 BLOCKED:', exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
