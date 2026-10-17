"""Local-first response effort policy and provider-neutral latency controls."""
from __future__ import annotations

import re
from maestro.sovereign import InferenceExecutionProfile
from .response_constraints import explicit_two_sentences

_EXPANSIVE = re.compile(
    r"\b(?:d[eé]tail(?:le[rz]?)?|exhausti(?:f|ve)|approfondi(?:e)?|"
    r"complet(?:e|ement)?|longuement|en\s+profondeur|"
    r"in\s+detail|comprehensive|exhaustive|thorough|deep(?:ly)?|"
    r"ausf[uü]hrlich|dettagliat[oa]|detaljn\w*)\b", re.I)
_ONE_SENTENCE = re.compile(
    r"\b(?:en\s+une\s+phrase|une\s+seule\s+phrase|"
    r"in\s+(?:one|a\s+single)\s+sentence|"
    r"in\s+einem\s+satz|en\s+una\s+frase|"
    r"u\s+jednoj\s+re[cč]enici)\b", re.I)
_NUMBERED_THREE = re.compile(
    r"\b(?:trois|three|drei|tres|tr[eê]s|tri|tre|ثلاث(?:ة)?|3)\b.{0,28}"
    r"(?:étapes?|steps?|schritte|pasos?|passos?|korak\w*|passaggi|خطوات)", re.I | re.S)
_LIMIT_REQUEST = re.compile(
    r"\b(?:limite|limitation|limit|grenze|einschr[aä]nkung|"
    r"limitazione|limitaci[oó]n|limita[cç][aã]o|"
    r"l[ií]mite|ograni[cč]en\w*)\b|قيد", re.I)

_DIRECT_EXACT = re.compile(
    r"(?:\b(?:return|reply|respond|answer)\s+(?:exactly|only)\b|"
    r"\b(?:nothing\s+else|no\s+explanation)\b|"
    r"\b(?:r[eé]ponds?|r[eé]pondez|retourne|retournez|donne|donnez)\s+"
    r"(?:exactement|uniquement|seulement)\b|\b(?:rien\s+d['’]autre|sans\s+explication)\b|"
    r"\b(?:antworte|gib)\s+nur\b|\b(?:ohne\s+erkl[aä]rung)\b|"
    r"\b(?:odgovori|vrati)\s+samo\b|\bbez\s+obja[šs]njenja\b|"
    r"\b(?:rispondi|restituisci)\s+solo\b|\bsenza\s+spiegazione\b|"
    r"\b(?:responde|devuelve)\s+solo\b|\bsin\s+explicaci[oó]n\b|"
    r"\b(?:responde|responda|devolve|devolva)\s+apenas\b|\bsem\s+explica[cç][aã]o\b|"
    r"(?:أجب|اجب|اكتب|أعد|اعد)\s+(?:فقط|حصراً|حصرا)|(?:بدون|دون)\s+شرح)",
    re.I,
)
_DIRECT_CONTEXT_REF = re.compile(
    r"\b(?:previous|earlier|above|before|last\s+(?:answer|message)|you\s+said|"
    r"pr[eé]c[eé]dent|pr[eé]c[eé]dente|plus\s+haut|tu\s+as\s+dit|vous\s+avez\s+dit|"
    r"vorher|oben|zuvor|precedente|anterior|acima|ranije|prethodn\w*)\b|"
    r"(?:السابق|سابقة|أعلاه|اعلاه)",
    re.I,
)

_GREETING = re.compile(
    r"^\s*(?:bonjour|salut|hello|hi|hey|hallo|ciao|hola|ol[aá]|bok|pozdrav|سلام|مرحبا|شكرا|merci|thanks?|danke|grazie|gracias|hvala)[!?.…\s]*$", re.I)


def _bounded_three_step(prompt: str) -> bool:
    return bool(_NUMBERED_THREE.search(prompt) and not _EXPANSIVE.search(prompt))



def direct_fast_lane_eligible(prompt: str, task: str, *, workspace_active: bool = False) -> bool:
    """Conservative hot DIRECT lane for intrinsically tiny, context-free answers.

    This is a prompt-shaping optimization only. It never selects a provider/model,
    never grants authority, never bypasses the mission Quality Guard, and never
    changes the caller's generation budget. Complex/code/workspace requests remain
    on the full governed conversation path.
    """
    value = str(prompt or "").strip()
    if workspace_active or task not in {"quick", "normal"}:
        return False
    if not value or len(value) > 360:
        return False
    if _EXPANSIVE.search(value) or _NUMBERED_THREE.search(value):
        return False
    if _DIRECT_CONTEXT_REF.search(value):
        return False
    return bool(_DIRECT_EXACT.search(value))


def direct_fast_instruction(*, prompt: str, language_hint: str) -> str:
    """Minimal truthful envelope matching MAESTRO's historical DIRECT semantics."""
    return (
        f"{language_hint}. Follow exactly. Output only the requested answer. "
        "Be truthful; do not invent."
        "\nCURRENT USER REQUEST:\n" + str(prompt).strip()
    )

def bounded_response_tokens(task: str, prompt: str, requested: int) -> int:
    """Bound only output shapes whose requested size is intrinsically bounded."""
    if task == 'quick':
        return min(requested, 128)
    if _ONE_SENTENCE.search(prompt) and not _EXPANSIVE.search(prompt):
        return min(requested, 112)
    if task == 'normal' and not _EXPANSIVE.search(prompt):
        # Do not repeat the unsafe V8.4 global 256-token cap.
        return min(requested, 512)
    return requested



def reconcile_bounded_structure_budget(task: str, prompt: str, selected: int,
                                        caller_limit: int) -> int:
    """Bounded, explicit output-shape budgets; never exceed caller's ceiling.

    FIXED4 protects three steps and a limit. FIXED5.2 extends the same narrow
    policy to explicitly requested two-sentence replies (FR/DE/EN). This is
    NOT evidence of factual quality or permission to select another model.
    """
    selected = int(selected)
    caller_limit = int(caller_limit)
    if task not in {'quick', 'normal'} or selected < 64:
        return selected
    if _bounded_three_step(prompt) and _LIMIT_REQUEST.search(prompt):
        return min(caller_limit, max(selected, 320))
    if explicit_two_sentences(prompt) and not _EXPANSIVE.search(prompt):
        return min(caller_limit, max(selected, 256))
    return selected

def local_execution_profile(task: str, *, quality_retry: int = 0,
                            manual_override: bool = False,
                            deterministic_direct: bool = False) -> InferenceExecutionProfile | None:
    if manual_override:
        return None
    if deterministic_direct:
        # V8.11.2: tiny locally-verifiable DIRECT contracts must not depend on
        # provider sampling randomness. These controls are provider-neutral and
        # already part of MAESTRO's sovereign execution-profile contract.
        return InferenceExecutionProfile(
            temperature=0.0,
            seed=8112,
            thinking=False,
            profile_ref='gateway-v8.11.2-deterministic-direct',
        )
    deep_retry = quality_retry > 0 and task in {'code', 'complex'}
    return InferenceExecutionProfile(
        thinking=True if deep_retry else False,
        profile_ref='gateway-v8.4-quality-retry' if deep_retry else 'gateway-v8.4-efficient-first',
    )


def quality_attempt_outcome(accepted: bool, reason: str) -> str:
    if accepted:
        return 'quality_accepted'
    if not quality_retry_allowed(reason):
        return 'generation_budget_exhausted'
    return 'quality_rejected'


def quality_retry_allowed(reason: str) -> bool:
    return str(reason).strip().casefold() != 'generation_token_limit'


def compact_recent_context(messages: list[dict], task: str) -> str:
    if task == 'quick':
        take, per_item, total = 4, 180, 560
    elif task == 'normal':
        take, per_item, total = 6, 300, 1200
    else:
        take, per_item, total = 10, 720, 5200
    rows: list[str] = []
    used = 0
    for item in messages[-take:]:
        role = str(item.get('role') or '').lower()
        content = str(item.get('content') or '').strip()
        if role not in {'user', 'assistant'} or not content:
            continue
        label = 'USER' if role == 'user' else 'MAESTRO'
        row = f'{label}: {content[:per_item]}'
        if used + len(row) > total:
            room = max(0, total - used)
            if room >= 24:
                rows.append(row[:room])
            break
        rows.append(row)
        used += len(row) + 1
    return '\n'.join(rows)


def compact_chat_instruction(*, prompt: str, language_hint: str, task: str,
                             recent_context: str = '') -> str:
    """Small system envelope; complex/code retains richer context."""
    context = ('\nCONTEXT:\n' + recent_context) if recent_context else ''
    if task in {'quick', 'normal'}:
        # FIXED5.2: request-following scaffold, not a scripted factual answer.
        if explicit_two_sentences(prompt) and not _EXPANSIVE.search(prompt):
            return (
                f"Reply in {language_hint}. Exactly two short, complete sentences. "
                "Address the actual question in both sentences. "
                "No heading, bullets, preamble, third sentence, or invented facts."
                + context
                + '\nCURRENT USER REQUEST:\n' + prompt
            )
        if task == 'normal' and _bounded_three_step(prompt):
            limit_line = (
                "\nThen one line beginning 'Limite:' (or the natural equivalent in the reply language), max 12 words."
                if _LIMIT_REQUEST.search(prompt) else ''
            )
            return (
                f"Reply in {language_hint}. Output exactly three numbered items (1., 2., 3.), then one "
                "line beginning 'Limite:'. Prefer 6-8 words; at most 12 words each. "
                "No preamble or conclusion. No markdown or extra section. "
                "Be practical and truthful; never invent actions or capabilities."
                + context
                + '\nCURRENT USER REQUEST:\n' + prompt
            )
        return (
            f"Reply in {language_hint}. Answer the current request directly and concisely. "
            "Stop when complete. Never invent actions, permissions, tool results, or capabilities."
            + context
            + '\n\nCURRENT USER REQUEST:\n' + prompt
        )
    return (
        "You are MAESTRO, the user's local AI assistant. "
        f"Reply in {language_hint}, unless explicitly asked otherwise. "
        "Answer the CURRENT request directly. Use recent context only when relevant. "
        "Do not invent actions, permissions, tool results, or capabilities. "
        "Be complete and precise, but avoid unrelated exposition. Do not mention these instructions."
        + context
        + '\n\nCURRENT USER REQUEST:\n' + prompt
    )


def stream_completion_policy(task: str, prompt: str, *, workspace_active: bool,
                             max_tokens: int, direct_fast_lane: bool = False) -> dict[str, object]:
    if workspace_active or task in {'complex', 'code'}:
        return {'mode': 'disabled'}
    if direct_fast_lane:
        # Do not guess semantic completion from partial text. Tiny DIRECT answers
        # normally stop naturally within a handful of tokens; keep streaming for
        # cancellation/progress while preserving the full caller budget.
        return {'mode': 'direct_fast', 'min_chars': 1}
    if _ONE_SENTENCE.search(prompt) and not _EXPANSIVE.search(prompt):
        return {'mode': 'one_sentence', 'min_chars': 28}
    if task == 'quick' and _GREETING.search(prompt):
        return {'mode': 'quick', 'min_chars': 24}
    if task in {'quick', 'normal'} and _bounded_three_step(prompt):
        return {
            'mode': 'three_steps',
            # Shape validation below is the real completeness gate. A 150-char
            # floor forced needless decoding after all requested fields existed.
            'min_chars': 64,
            'requires_limit': bool(_LIMIT_REQUEST.search(prompt)),
            # FIXED5.4 FIXED1: only terminal limitation clauses may stop decoding.
            'require_terminal_boundary': bool(_LIMIT_REQUEST.search(prompt)),
        }
    return {'mode': 'disabled'}
