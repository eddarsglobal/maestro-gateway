"""Gateway-side adaptive routing; models remain subordinate to the Core.

Only advisory scores are computed here.  Core resource registration and the
normal governed execution/acceptance path remain mandatory.
"""
from __future__ import annotations

import json
import hashlib
import time
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .response_guard import asks_about_capabilities, asks_about_previous_language, asks_for_software_project
from .multilingual_mission_contract import parse_mission_contract
from .response_effort import conversation_context_required
from .model_qualification import qualification, task_qualification, warm_qualifications
from .semantic_competence import semantic_qualification, warm_semantic_qualifications
from .autonomous_qualification import rank_adjustment, model_version


_COMPLEX = re.compile(
    r"\b(?:analyse[rz]?|compare[rz]?|explique[rz]?\s+en\s+d[ée]tail|"
    r"architecture|impl[ée]mente[rz]?|d[ée]bogu[ea]|programme[rz]?|"
    r"debug|code|python|typescript|javascript|sql|refactor|security|"
    r"analyse|compare|architecture|implement|design|complex|reasoning)\b",
    re.IGNORECASE,
)
_CODE = re.compile(
    r"\b(?:python|typescript|javascript|sql|rust|django|react|code|"
    r"programme[rz]?|script|fonction|function|bug|debug|refactor|"
    r"impl[ée]mente[rz]?|tests?|compile|terminal|git|api)\b",
    re.IGNORECASE,
)
_EXPLANATION = re.compile(
    r"\b(?:pourquoi|why|wieso|warum|perch[eé]|por\s+qu[eé]|"
    r"zašto|zasto|لماذا|لم|explique|explain|because|raison|cause)\b",
    re.IGNORECASE,
)
_HR = re.compile(
    r"\b(?:reci\s+mi|ukratk+o|[šs]ta\s+sve|[šs]to\s+sve|"
    r"mo[žz]e[šs]|odgovorio|koj[ie]\s+jezik|jeziku|"
    r"hrvatsk\w*|ispričavam|ispricavam)\b", re.IGNORECASE,
)
_FR = re.compile(
    r"(?:[éèêëàâùûçîïôœ]|\b(?:bonjour|salut|merci|pourquoi|comment|"
    r"quelle?s?|suis|es-tu|peux-tu|vous|avec|réponds|aide|mon|ma|"
    r"notre|fran[çc]ais|le|la|les|un|une|est|quoi)\b)", re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteDecision:
    resource_id: str
    task_class: str
    prompt_budget: int
    evidence_count: int
    policy: str = "qualified_auto_v2"
    qualification_status: str = "provisional"
    language: str = "en"


def classify_request(prompt: str, *, workspace_active: bool = False,
                     ui_language: str | None = None) -> str:
    if asks_for_software_project(prompt):
        return "code"
    if workspace_active:
        return "code" if _CODE.search(prompt) else "complex"
    # Conversation references must be resolved before lexical software routing.
    # A phrase such as "Quel était le code fictif ?" uses the ordinary noun
    # "code" but is a memory/follow-up request, not a programming task.
    if conversation_context_required(prompt):
        return "normal"
    if _COMPLEX.search(prompt):
        return "code" if _CODE.search(prompt) else "complex"
    # Self-capabilities require enough model capacity and a real live catalogue.
    # A small greeting model must not invent or repeat a slogan instead.
    if asks_about_capabilities(prompt) or asks_about_previous_language(prompt):
        return "normal"
    if explicit_requested_language(prompt) is not None:
        return "normal"
    # Never send Croatian multiword requests through the minimal greeting
    # model on the strength of a language-agnostic length threshold alone.
    # This only influences performance routing, not authorization or truth.
    if ((ui_language or "").lower().startswith("hr") or _HR.search(prompt)) and len(prompt.split()) > 1:
        return "normal"
    # Explanations and causal follow-ups require more than a tiny greeting model.
    if _EXPLANATION.search(prompt):
        return "normal"
    words = prompt.split()
    if len(prompt) <= 160 and len(words) <= 22:
        return "quick"
    return "normal"


_EXPLICIT_LANGUAGE_REQUESTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("French", re.compile(r"\b(?:r[eé]ponds?|r[eé]pondez|parle|[eé]cris)\b.{0,60}\bfran[cç]ais\b", re.I | re.S)),
    ("German", re.compile(r"\b(?:antworte|ntworte|schreibe|sprich|wechsle)\b.{0,60}\b(?:deutsch|auf\s+deutsch)\b", re.I | re.S)),
    ("Croatian", re.compile(r"\b(?:odgovaraj|odgovori|pi[šs]i|govori)\b.{0,60}\bhrvatsk\w*\b", re.I | re.S)),
    ("Italian", re.compile(r"\b(?:rispondi|scrivi|parla)\b.{0,60}\bitalian\w*\b", re.I | re.S)),
    ("Spanish", re.compile(r"\b(?:responde|escribe|habla)\b.{0,60}\bespa[nñ]ol\b", re.I | re.S)),
    ("Portuguese", re.compile(r"\b(?:responde|escreve|fala)\b.{0,60}\bportugu[eê]s\b", re.I | re.S)),
    ("Arabic", re.compile(r"(?:أجب|اجب|اكتب|تحدث).{0,60}(?:العربية|باللغة\s+العربية)", re.S)),
    ("English", re.compile(r"\b(?:answer|reply|write|speak)\b.{0,60}\benglish\b", re.I | re.S)),
)


def explicit_requested_language(prompt: str) -> str | None:
    value = str(prompt or "")
    for language, pattern in _EXPLICIT_LANGUAGE_REQUESTS:
        if pattern.search(value):
            return language
    # Explicit language names in natural instructions (not inferred from UI
    # locale or generic words) may be routed independently. Unknown languages
    # abstain and are never asserted as qualified.
    contract = parse_mission_contract(value)
    if contract.language is not None and (contract.sentence_count is not None or contract.identity_request):
        return {
            'en': 'English', 'fr': 'French', 'de': 'German', 'ar': 'Arabic',
            'pt': 'Portuguese', 'es': 'Spanish', 'hr': 'Croatian', 'it': 'Italian',
            'zh': 'Chinese', 'ja': 'Japanese', 'hi': 'Hindi',
            'tr': 'Turkish', 'sw': 'Swahili', 'ru': 'Russian',
        }.get(contract.language)
    return None


def response_language_hint(prompt: str, ui_language: str | None) -> str:
    """Use reliable language cues, then the selected UI language.

    FIXED5.6: broad French stopwords/accents alone are not sufficient evidence
    to override an explicitly selected supported UI language. In particular,
    Italian/Spanish/Portuguese common words must not be indexed as French.
    This is a conservative hint, NOT multilingual fluency certification.
    """
    ui = (ui_language or "").lower()[:2]
    names = {"fr": "French", "en": "English", "de": "German", "ar": "Arabic",
             "pt": "Portuguese", "es": "Spanish", "hr": "Croatian", "it": "Italian",
             "zh": "Chinese", "ja": "Japanese", "hi": "Hindi", "tr": "Turkish",
             "sw": "Swahili", "ru": "Russian"}
    explicit = explicit_requested_language(prompt)
    if explicit is not None:
        return explicit
    if _LANGUAGE_AR.search(prompt):
        return "Arabic"
    # P0.4.3c1-FIXED3: conservative current-turn cues for all eight UI
    # languages.  Fullmatch prevents incidental quotations, code snippets or
    # one foreign word inside an unrelated long prompt from rerouting it.
    # This is NOT statistical language identification or fluency evidence.
    for language, pattern in _HIGH_CONFIDENCE_TURN_CUES:
        if pattern.fullmatch(str(prompt or "").strip()):
            return language
    # P0.4.3c1: current explicit Croatian/German utterance beats UI locale.
    # These bounded high-confidence cues are not general language detection.
    if _HR_CURRENT_UTTERANCE.search(prompt):
        return "Croatian"
    if (_FRENCH_CAPABILITY.search(prompt)
            or re.search(r"\b(?:bonjour|salut|merci|pourquoi|comment|"
                         r"quelles?|r[eé]ponds?|explique|décris|aide[- ]moi)\b",
                         prompt, re.I)):
        return "French"
    if _HR.search(prompt):
        return "Croatian"
    if _LANGUAGE_DE.search(prompt):
        return "German"
    if _LANGUAGE_IT.search(prompt):
        return "Italian"
    if _LANGUAGE_ES.search(prompt):
        return "Spanish"
    if _LANGUAGE_PT.search(prompt):
        return "Portuguese"
    if _LANGUAGE_EN.search(prompt):
        return "English"
    if ui in names:
        return names[ui]
    if _FR.search(prompt):
        return "French"
    return "the same language as the user"

_FRENCH_CAPABILITY = re.compile(
    r"\b(?:qu['’]es{1,2}t[- ]ce\s+que|tu\s+peux|peux[- ]tu|vous\s+pouvez|pouvez[- ]vous)\b", re.I)
_HR_CURRENT_UTTERANCE = re.compile(
    r"^\s*(?:(?:dobar\s+dan|dobro\s+jutro|dobra\s+ve[cč]er|"
    r"laku\s+no[cć]|bok|pozdrav)[!?.\s]*|"
    r"(?:[šs]ta|[šs]to)\s+(?:sve\s+)?mo[žz]e[šs]\s+(?:raditi|napraviti)[!?.\s]*|"
    r"(?:tko|ko)\s+si(?:\s+ti)?[!?.\s]*)$", re.I,
)
# These cues only target high-confidence common user turns. Unknown requests
# continue through the existing hints/UI fallback; an ordinary match must
# never grant qualification or override runtime/model locality restrictions.
_HIGH_CONFIDENCE_TURN_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("English", re.compile(
        r"(?:good\s+(?:morning|afternoon|evening)|hello|hi|thank\s+you|thanks|"
        r"who\s+are\s+you|what\s+can\s+you\s+do)[!?.\s]*", re.I)),
    ("French", re.compile(
        r"(?:bonjour|bonsoir|salut|merci(?:\s+beaucoup)?|"
        r"qui\s+es[- ]tu|qui\s+[eê]tes[- ]vous|"
        r"que\s+peux[- ]tu\s+faire|que\s+pouvez[- ]vous\s+faire)[!?.\s]*", re.I)),
    ("German", re.compile(
        r"(?:guten\s+(?:morgen|tag|abend)|hallo|"
        r"vielen\s+dank|danke(?:\s+sch[oö]n)?|"
        r"wer\s+bist\s+du|was\s+kannst\s+du\s+(?:tun|machen))[!?.\s]*", re.I)),
    ("Portuguese", re.compile(
        r"(?:bom\s+dia|boa\s+(?:tarde|noite)|ol[aá]|"
        r"muito\s+obrigad[oa]|quem\s+(?:[eé]s\s+tu|[eé]\s+voc[eê])|"
        r"o\s+que\s+(?:voc[eê]\s+pode|podes)\s+fazer)[!?.\s]*", re.I)),
    ("Spanish", re.compile(
        r"[¿¡]?(?:buenos\s+d[ií]as|buenas\s+(?:tardes|noches)|hola|"
        r"muchas\s+gracias|qui[eé]n\s+eres|"
        r"qu[eé]\s+puedes\s+hacer)[!?.\s]*", re.I)),
    ("Croatian", re.compile(
        r"(?:dobar\s+dan|dobro\s+jutro|dobra\s+ve[cč]er|bok|pozdrav|"
        r"hvala(?:\s+lijepa)?|(?:tko|ko)\s+si(?:\s+ti)?|"
        r"(?:[šs]to|[šs]ta)\s+(?:sve\s+)?mo[žz]e[šs]\s+"
        r"(?:raditi|napraviti))[!?.\s]*", re.I)),
    ("Italian", re.compile(
        r"(?:buongiorno|buonasera|ciao|grazie(?:\s+mille)?|"
        r"chi\s+sei|cosa\s+(?:puoi|sai)\s+fare)[!?.\s]*", re.I)),
)
_LANGUAGE_AR = re.compile(r"[\u0600-\u06ff]")
_LANGUAGE_DE = re.compile(r"\b(?:wer\s+bist\s+du|wie\s+hei[ßs]t\s+du|was\s+kannst\s+du|hallo|danke|warum)\b", re.I)
_LANGUAGE_IT = re.compile(r"\b(?:cosa\s+(?:sai|puoi)\s+fare|ciao|grazie)\b", re.I)
_LANGUAGE_ES = re.compile(r"\b(?:qu[eé]\s+puedes\s+hacer|hola|gracias)\b", re.I)
_LANGUAGE_PT = re.compile(r"\b(?:o\s+que\s+(?:podes|consegues|voc[eê]\s+pode)\s+fazer|ol[aá]|obrigad[oa])\b", re.I)
_LANGUAGE_EN = re.compile(r"\b(?:hello|thanks|what|why|how|please)\b", re.I)

_EVIDENCE_CACHE: dict[tuple[str, int], tuple[float, dict[str, list[float]], dict[str, int]]] = {}


def warm_route_history(conversation_root: Path) -> None:
    # Index on launch, then use in-memory snapshots on the hot routing path.
    _cached_evidence(conversation_root)
    warm_qualifications(conversation_root)
    warm_semantic_qualifications(conversation_root)


def _cached_evidence(conversation_root: Path) -> tuple[dict[str, list[float]], dict[str, int]]:
    key = (str(conversation_root), 100)
    value = _EVIDENCE_CACHE.get(key)
    if value is not None and time.monotonic() - value[0] < 2.0:
        return value[1], value[2]
    metrics = _read_model_metrics(conversation_root)
    failures = _read_quality_failures(conversation_root)
    if len(_EVIDENCE_CACHE) > 128:
        _EVIDENCE_CACHE.clear()
    _EVIDENCE_CACHE[key] = (time.monotonic(), metrics, failures)
    return metrics, failures


def recent_model_metrics(conversation_root: Path, limit: int = 100) -> dict[str, list[float]]:
    return _read_model_metrics(conversation_root, limit)


def _read_model_metrics(conversation_root: Path, limit: int = 100) -> dict[str, list[float]]:
    """Read only historical timing evidence, never persist or transmit chat text."""
    timings: dict[str, list[float]] = {}
    try:
        files = sorted(
            conversation_root.glob("CHAT-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
    except (OSError, ValueError):
        return timings
    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                continue
            for message in (record.get("messages") or [])[-24:]:
                if not isinstance(message, dict) or message.get("role") not in {"assistant", "system"}:
                    continue
                mission = (message.get("metadata") or {}).get("mission") or {}
                if not isinstance(mission, dict):
                    continue
                metrics = mission.get("metrics") or {}
                if not isinstance(metrics, dict):
                    continue
                attempts = metrics.get("model_attempt_metrics")
                if isinstance(attempts, list) and attempts:
                    # Never assign a multi-model cumulative time solely to
                    # the final model. Track each measured attempt separately.
                    source = (
                        (item.get("resource_id"),
                         item.get("total_ms") if isinstance(item.get("total_ms"), (int, float))
                         else item.get("wall_ms"))
                        for item in attempts if isinstance(item, dict)
                    )
                else:
                    # Legacy records: only single-model timings are attributable.
                    seq = metrics.get("model_sequence")
                    if isinstance(seq, list) and len(seq) > 1:
                        continue
                    source = ((mission.get("resource_id"), metrics.get("total_ms")),)
                for rid, elapsed in source:
                    if (isinstance(rid, str) and rid and isinstance(elapsed, (float, int))
                            and not isinstance(elapsed, bool) and math.isfinite(elapsed)
                            and 0 < elapsed < 1_200_000):
                        timings.setdefault(rid, []).append(float(elapsed))
        except (OSError, TypeError, ValueError):
            continue
    return timings


def recent_model_quality_failures(conversation_root: Path, limit: int = 100) -> dict[str, int]:
    return _read_quality_failures(conversation_root, limit)


def _read_quality_failures(conversation_root: Path, limit: int = 100) -> dict[str, int]:
    """Count failed AUTO model attempts from local mission metadata only.

    The model response and message content are neither read for scoring nor
    sent elsewhere; a verified escalation is retained as routing evidence.
    """
    failures: dict[str, int] = {}
    try:
        files = sorted(conversation_root.glob("CHAT-*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except (OSError, ValueError):
        return failures
    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                continue
            for message in (record.get("messages") or [])[-24:]:
                if not isinstance(message, dict) or message.get("role") not in {"assistant", "system"}:
                    continue
                mission = (message.get("metadata") or {}).get("mission") or {}
                metrics = mission.get("metrics") or {}
                if not isinstance(metrics, dict):
                    continue
                attempts = metrics.get("model_attempt_metrics")
                if isinstance(attempts, list) and attempts:
                    for item in attempts:
                        if not isinstance(item, dict):
                            continue
                        rid = item.get("resource_id")
                        outcome = item.get("outcome")
                        if (isinstance(rid, str) and rid
                                and outcome in {"transport_failure", "timeout", "quality_rejected"}):
                            failures[rid] = failures.get(rid, 0) + 1
                seq = metrics.get("model_sequence")
                if (isinstance(seq, list) and len(seq) >= 2
                        and isinstance(seq[0], str) and seq[0]):
                    # Older histories don't contain per-attempt outcomes.
                    # Do not double count a failure already present above.
                    if (not isinstance(attempts, list) or not any(
                        isinstance(a, dict) and a.get("resource_id") == seq[0]
                        and a.get("outcome") in {"transport_failure", "timeout", "quality_rejected"}
                        for a in attempts
                    )):
                        escalated = bool(metrics.get("quality_escalations"))
                        timed_out = bool(metrics.get("timeout_recoveries"))
                        if escalated or timed_out:
                            failures[seq[0]] = failures.get(seq[0], 0) + 1
        except (OSError, TypeError, ValueError):
            continue
    return failures


def _size_bytes(descriptor: Any) -> int:
    value = (getattr(descriptor, "metadata", None) or {}).get("size")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _caps(descriptor: Any) -> set[str]:
    return {str(getattr(c, "value", c)).upper() for c in (getattr(descriptor, "capabilities", ()) or ())}


_LANG_CODES = {"French": "fr", "English": "en", "Croatian": "hr", "German": "de",
               "Italian": "it", "Spanish": "es", "Portuguese": "pt", "Arabic": "ar",
               "Chinese": "zh", "Japanese": "ja", "Hindi": "hi", "Turkish": "tr",
               "Swahili": "sw", "Russian": "ru"}


def _language_code(prompt: str, ui_language: str | None) -> str:
    hint = response_language_hint(prompt, ui_language)
    return _LANG_CODES.get(hint, (ui_language or "en")[:2].lower())


def _eligible(registry: Any, task_class: str) -> list[Any]:
    """Classify each descriptor once, preserving neutral filtering semantics.

    For a request with a corresponding model class, prefer that class; if
    unavailable, preserve the original eligible fallback candidates and their
    ordering. No provider identity or guessed model score influences filtering.
    """
    resources: list[Any] = []
    code_resources: list[Any] = []
    general_resources: list[Any] = []
    for d in registry.all():
        if not getattr(d, "resource_id", None):
            continue
        status = getattr(d, "status", "AVAILABLE")
        if str(getattr(status, "value", status)) in {"UNAVAILABLE", "OFFLINE"}:
            continue
        # Boolean capability classification avoids allocating a set for every
        # resource and still accepts enum-like and mixed-case descriptors.
        conversational = False
        code = False
        for cap in (getattr(d, "capabilities", ()) or ()):
            value = getattr(cap, "value", cap)
            name = value.upper() if isinstance(value, str) else str(value).upper()
            if name == "DIRECT" or name == "GENERAL_REASONING":
                conversational = True
            elif name == "CODE":
                code = True
        if not conversational:
            continue
        resources.append(d)
        if code:
            code_resources.append(d)
        else:
            general_resources.append(d)
    if task_class == "code" and code_resources:
        return code_resources
    # A CODE capability is additional competence, NOT disqualification from
    # ordinary conversation. Excluding coder models hid measured fast local
    # candidates from quick/normal requests. The code preference stays intact.
    return resources


def _rank_from_qualification(d: Any, q: Any, task: str) -> float:
    """Score an already-read immutable qualification; no hidden I/O."""
    degraded = str(getattr(getattr(d, 'status', None), 'value', getattr(d, 'status', ''))) == 'DEGRADED'
    if q.qualified:
        risk = 0.0
    elif q.attempts > 0:
        # Sparse real-world failures must not permanently blacklist a small
        # local model while a much heavier never-tested model receives the
        # lower generic cold-start risk.  Increase distrust progressively with
        # repeated rejected/timeout outcomes instead of using the old fixed 90.
        failures = max(0, q.attempts - q.accepted)
        risk = 30.0 + min(failures, 4) * 6.0
        if q.accepted:
            risk -= min(q.accepted, 2) * 5.0
    else:
        risk = 45.0
    failure_fraction = (q.attempts - q.accepted) / q.attempts if q.attempts else 0.0
    latency = q.p95_ms / 1000 if q.p95_ms is not None else 12.0
    utility = risk + failure_fraction * 8 + min(latency, 60) * (1.0 if task in {'quick', 'normal'} else .25)
    # Extreme end-to-end latency is itself operational evidence.  For
    # demanding tasks we still prefer proven competence up to a generous
    # threshold, but a model taking over a minute must not become immortal
    # merely because it once passed the response guard.  This surcharge only
    # opens bounded competition; it does not certify an unmeasured candidate.
    extreme_seconds = {'quick': 15.0, 'normal': 25.0, 'code': 45.0, 'complex': 60.0}
    if q.p95_ms is not None and latency > extreme_seconds.get(task, 25.0):
        utility += 40.0
    if degraded:
        utility += 50
    # Two distinct bounded local probes are only weak operational evidence.
    # Never equate them with verified general intelligence or fluency.

    return utility


def _provisional_efficiency_cost(d: Any, task: str, q: Any) -> float:
    """Cold-start scheduling cost, never a semantic competence score.

    With NO real outcomes for this task/language, model weight bytes can serve
    as a weak inference-cost proxy on constrained CPUs. Only break otherwise
    equal *unproven* choices; known real mission outcomes always dominate.
    The lower envelope for nontrivial tasks protects against blindly promoting
    tiny models. No hardcoded provider, model name, GPU or download behavior.
    """
    if q.attempts or q.qualified:
        return 0.0
    size = _size_bytes(d)
    if not size:
        return 0.0  # Unknown is not presumed to be heavy or low quality.
    size_gb = size / 1_000_000_000.0
    floors = {'quick': 0.75, 'normal': 1.10,
              'code': 1.40, 'complex': 2.00}
    floor = floors.get(task, 1.10)
    # The first penalty only captures estimated CPU/working-set cost.
    # The second penalty protects a bounded minimum resource envelope,
    # *not* an asserted model IQ or actual benchmark.
    return min(size_gb, 60.0) * 0.85 + (12.0 if size_gb < floor else 0.0)




def _cold_start_pressure_cost(d: Any, task: str, q: Any) -> float:
    """Estimate first-turn local CPU/load pressure without claiming quality.

    A newly started Gateway has no proof that a previously good model is still
    resident in memory.  Model bytes are therefore a bounded cold-start cost
    proxy, added only to the *initial* primary choice. Real low-latency mission
    evidence can still overcome this pressure.  No model/provider names are
    inspected and the value never becomes semantic competence evidence.
    """
    size = _size_bytes(d)
    if not size:
        return 0.0
    size_gb = min(size / 1_000_000_000.0, 60.0)
    # Conversational turns are latency-sensitive. Code/complex work gives more
    # weight to already-observed competence while still accounting for load.
    weight = {'quick': 12.0, 'normal': 12.0, 'code': 4.0, 'complex': 2.5}.get(task, 6.0)
    return size_gb * weight

def _operational_rank(d: Any, task: str, language: str) -> float:
    """Observed task/language outcomes before resource efficiency.

    No provider name, model size, declared generic quality score or probe
    order determines competence. New models get provisional exploration,
    never fabricated positive quality or latency evidence.
    """
    return _rank_from_qualification(d, qualification(str(d.resource_id), task, language), task)


def choose_adaptive_resource(registry: Any, prompt: str,
                             conversation_root: Path | None = None,
                             *, workspace_active: bool = False,
                             max_tokens: int = 512,
                             ui_language: str | None = None,
                             cold_start: bool = False) -> RouteDecision:
    task_class = classify_request(prompt, workspace_active=workspace_active, ui_language=ui_language)
    language = _language_code(prompt, ui_language)
    eligible = _eligible(registry, task_class)
    if not eligible:
        raise RuntimeError('No local DIRECT/GENERAL_REASONING intelligence resource is available.')
    # The only hot-path inputs are in-memory registry descriptors and previously
    # qualified immutable records. NEVER perform disk/network I/O here.
    # Conservatively exclude very small, still-unmeasured resources from
    # nontrivial conversation IF a better-capacity provisional option exists.
    # This is an eligibility risk guard, never a quality score. Demonstrated
    # same-language success allows any size to compete without restriction.
    # One immutable evidence lookup per candidate, shared between normal-task
    # safety filtering, ranking and the reported evidence count.  Do not cache
    # eligibility between turns: Core registrations and permissions can change.
    qualified_candidates = [
        (d, qualification(str(d.resource_id), task_class, language))
        for d in eligible
    ]
    if task_class == 'normal':
        safer = [(d, q) for d, q in qualified_candidates
                 if _size_bytes(d) >= 900_000_000 or q.qualified]
        if safer:
            qualified_candidates = safer

    # Scores are evaluated once; stable prompt-dependent digest tie-breaking
    # preserves provider neutrality and order-independent exploration.
    best_score = float('inf')
    tied: list[tuple[Any, Any]] = []
    for d, q_candidate in qualified_candidates:
        score = (_rank_from_qualification(d, q_candidate, task_class)
                 + _provisional_efficiency_cost(d, task_class, q_candidate)
                 + (_cold_start_pressure_cost(d, task_class, q_candidate) if cold_start else 0.0)
                 + rank_adjustment(str(d.resource_id), model_version(d), language))
        if score < best_score:
            best_score = score
            tied = [(d, q_candidate)]
        elif score == best_score:
            tied.append((d, q_candidate))
    if len(tied) == 1:
        chosen, q = tied[0]
    else:
        chosen, q = min(
            tied, key=lambda pair: hashlib.blake2s(
                (prompt + '\0' + str(pair[0].resource_id)).encode(), digest_size=8).digest())
    # Do not truncate ordinary conversational answers at 160 tokens.
    # Route selection remains a hot in-memory operation; output generation
    # has its own caller-controlled budget.
    budget = min(max_tokens, 160) if task_class == 'quick' else max_tokens
    return RouteDecision(resource_id=str(chosen.resource_id), task_class=task_class,
                         prompt_budget=budget, evidence_count=q.attempts,
                         qualification_status='operationally_qualified' if q.qualified else 'provisional',
                         language=language)


def _qualified_fallback(registry: Any, current_resource_id: str, *,
                        task_class: str, language: str, remaining_seconds: float | None,
                        prompt: str, semantic_subtask: str | None = None) -> str | None:
    if remaining_seconds is not None and remaining_seconds <= 0:
        return None
    candidates = []
    for descriptor in _eligible(registry, task_class):
        # A registered descriptor is not automatically an authorized LOCAL
        # fallback. Never let a cloud/non-local or unknown-locality candidate
        # reach the retry adapter, even when its past quality is high.
        if getattr(descriptor, 'local', None) is not True:
            continue
        rid = str(descriptor.resource_id)
        if rid == current_resource_id:
            continue
        q = qualification(rid, task_class, language)
        if not q.qualified or q.p95_ms is None:
            continue
        if semantic_subtask is not None:
            semantic = semantic_qualification(rid, semantic_subtask, language)
            if semantic.disqualified:
                continue
        if remaining_seconds is not None and q.p95_ms / 1000 + .5 >= remaining_seconds:
            continue
        candidates.append(descriptor)
    if not candidates:
        return None
    if semantic_subtask is not None:
        proven = [d for d in candidates if semantic_qualification(
            str(d.resource_id), semantic_subtask, language).qualified]
        if proven:
            candidates = proven
    ranked = [(_operational_rank(d, task_class, language), d) for d in candidates]
    best = min(score for score, _ in ranked)
    tied = [d for score, d in ranked if score == best]
    chosen = tied[0] if len(tied) == 1 else min(
        tied, key=lambda d: hashlib.blake2s(
            (prompt + '\0' + str(d.resource_id)).encode(), digest_size=8).digest())
    return str(chosen.resource_id)


_LANGUAGE_TRIAL_CODES = frozenset({'fr', 'en', 'hr', 'de', 'it', 'es', 'pt', 'ar'})


def _provisional_language_fallback(registry: Any, current_resource_id: str, *,
                                   task_class: str, language: str,
                                   remaining_seconds: float | None) -> str | None:
    """Choose ONE local evidence-building recovery trial after a language-contract failure.

    This is deliberately weaker than qualification: it never declares fluency.
    It is only used inside the existing single quality-retry budget after the
    current model has already produced an objectively rejected answer for an
    explicitly requested language.  Known same-language failures are avoided;
    if all alternatives are unmeasured, prefer the smallest local model larger
    than the failed incumbent as a bounded capacity escalation.
    """
    if task_class not in {'quick', 'normal'} or language not in _LANGUAGE_TRIAL_CODES:
        return None
    if remaining_seconds is not None and remaining_seconds <= 0:
        return None
    descriptors = [d for d in _eligible(registry, task_class)
                   if getattr(d, 'local', None) is True]
    current = next((d for d in descriptors
                    if str(getattr(d, 'resource_id', '')) == current_resource_id), None)
    current_size = _size_bytes(current) if current is not None else 0
    rows: list[tuple[Any, Any, Any]] = []
    for d in descriptors:
        rid = str(getattr(d, 'resource_id', ''))
        if not rid or rid == current_resource_id:
            continue
        q = qualification(rid, task_class, language)
        cross = task_qualification(rid, task_class)
        # Do not repeatedly gamble on a model with only negative evidence in
        # this language. A prior accepted turn remains usable but unproven.
        if q.attempts and q.accepted == 0:
            continue
        if remaining_seconds is not None and q.p95_ms is not None:
            if q.p95_ms / 1000 + .5 >= remaining_seconds:
                continue
        rows.append((d, q, cross))
    if not rows:
        return None

    same_language_positive = [row for row in rows if row[1].accepted > 0]
    if same_language_positive:
        rows = same_language_positive
    else:
        cross_proven = [row for row in rows if row[2].qualified]
        if cross_proven:
            rows = cross_proven
        elif current_size:
            larger = [row for row in rows if _size_bytes(row[0]) > current_size]
            if larger:
                # Bounded escalation: do not jump straight to the heaviest
                # model when a closer local capacity step exists.
                next_size = min(_size_bytes(row[0]) for row in larger)
                rows = [row for row in larger if _size_bytes(row[0]) == next_size]

    def key(row: tuple[Any, Any, Any]):
        d, q, cross = row
        size = _size_bytes(d)
        return (
            -(q.accepted),
            -(cross.accepted),
            q.rejected + q.timed_out,
            size if size else 2**63 - 1,
            hashlib.blake2s(str(d.resource_id).encode(), digest_size=8).digest(),
        )
    return str(min(rows, key=key)[0].resource_id)


def choose_quality_fallback(registry: Any, current_resource_id: str,
                            conversation_root: Path | None = None,
                            *, task_class: str = 'normal', language: str = 'en',
                            remaining_seconds: float | None = None,
                            semantic_subtask: str | None = None,
                            allow_provisional_language_trial: bool = False) -> str | None:
    """Choose a governed local fallback after a rejected answer.

    Qualified same-language evidence always wins.  For an explicit language
    request only, one unqualified LOCAL recovery trial may be selected when no
    qualified alternative exists.  The trial does not certify language skill;
    its output must pass the same response/shape guards and its real outcome is
    then recorded by the existing qualification machinery.
    """
    qualified = _qualified_fallback(
        registry, current_resource_id, task_class=task_class, language=language,
        remaining_seconds=remaining_seconds, prompt='quality_retry',
        semantic_subtask=semantic_subtask)
    if qualified is not None or not allow_provisional_language_trial:
        return qualified
    return _provisional_language_fallback(
        registry, current_resource_id, task_class=task_class, language=language,
        remaining_seconds=remaining_seconds)


def choose_timeout_fallback(registry: Any, current_resource_id: str,
                            conversation_root: Path | None = None,
                            *, task_class: str = 'normal', language: str = 'en',
                            remaining_seconds: float | None = None) -> str | None:
    """Timeout recovery never gambles the remaining budget on an unproven model."""
    if task_class not in {'quick', 'normal'}:
        return None
    return _qualified_fallback(registry, current_resource_id, task_class=task_class,
                               language=language, remaining_seconds=remaining_seconds,
                               prompt='timeout_retry')
