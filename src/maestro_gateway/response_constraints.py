"""Narrow, provider-neutral output-shape checks for explicit user constraints.

This module never decides factual truth, fluency, model competence, routing,
permissions, or what constitutes a correct answer to an open-ended question.
An unrecognized/ambiguous request returns ABSTAIN, not a guessed verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal

from .multilingual_mission_contract import parse_mission_contract

Status = Literal['PASS_SHAPE', 'FAIL_SHAPE', 'ABSTAIN']


@dataclass(frozen=True, slots=True)
class ShapeCheck:
    status: Status
    reason: str
    factual_truth_verified: bool = False


# Only an explicit instruction triggers a shape contract.  Each supported
# language has a narrow intent + count/noun pair.  Adjectives may appear between
# the number and the sentence noun (for example German "zwei natürliche ...
# Sätze").  This validates count/shape only; it never certifies grammar.
_TWO_SENTENCE_PATTERNS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"\b(?:r[eé]ponds?|r[eé]pondez|[eé]cris|[eé]crivez|explique|d[eé]cris)\b", re.I),
     re.compile(r"\b(?:deux|2)\b.{0,100}\bphrases?\b", re.I | re.S)),
    (re.compile(r"\b(?:reply|respond|write|explain|describe|give)\b", re.I),
     re.compile(r"\b(?:two|2)\b.{0,100}\bsentences?\b", re.I | re.S)),
    (re.compile(r"\b(?:antworte|ntworte|schreibe|erkl[aä]re|beschreibe|gib)\b", re.I),
     re.compile(r"\b(?:zwei|2)\b.{0,120}\bs[aä]tze(?:n)?\b", re.I | re.S)),
    (re.compile(r"\b(?:odgovori|pi[šs]i|napi[šs]i|objasni|opi[šs]i|navedi)\b", re.I),
     re.compile(r"\b(?:dvije|dve|2)\b.{0,100}\bre[cč]enice\b", re.I | re.S)),
    (re.compile(r"\b(?:rispondi|scrivi|spiega|descrivi)\b", re.I),
     re.compile(r"\b(?:due|2)\b.{0,100}\bfrasi\b", re.I | re.S)),
    (re.compile(r"\b(?:responde|escribe|explica|describe)\b", re.I),
     re.compile(r"\b(?:dos|2)\b.{0,100}\bfrases\b", re.I | re.S)),
    (re.compile(r"\b(?:responde|escreve|explica|descreve)\b", re.I),
     re.compile(r"\b(?:duas|2)\b.{0,100}\bfrases\b", re.I | re.S)),
    (re.compile(r"(?:أجب|اجب|اكتب|اشرح|وضّح|وضح|صِف|صف)"),
     re.compile(r"(?:جملتين|جملتان|جملتين\s+فقط)")),
)

_THREE_STEPS = re.compile(
    r"(?:\b(?:trois|three|drei|tri|tre|tres|tr[eê]s|3)\b.{0,56}?"
    r"\b(?:[eé]tapes?|steps?|schritte|korak\w*|passaggi|passi|pasos?|passos?)\b|"
    r"ثلاث(?:ة)?\s+(?:خطوات|خطوة))", re.I | re.S,
)
_LIMIT_REQUEST = re.compile(
    r"\b(?:limite|limitation|limit|grenze|einschr[aä]nkung|"
    r"limitazione|limitaci[oó]n|limita[cç][aã]o|"
    r"l[ií]mite|ograni[cč]en\w*)\b|قيد", re.I,
)
_STEP = re.compile(r"^\s*(?:[-*]\s*)?([1-3])[.)]\s+(\S.*)$")
_LIMIT_LABEL = re.compile(
    r"^\s*(?:[-*]\s*)?(?:limite|limitation|limit|grenze|"
    r"einschr[aä]nkung|limitazione|limitaci[oó]n|limita[cç][aã]o|"
    r"ograni[cč]en\w*|قيد(?:ًا)?(?:\s+واحدًا)?)\s*[:—–-]\s*(\S.*)$",
    re.I,
)
_LIMIT_PROSE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:une\s+limite|la\s+(?:principale\s+)?limite|"
    r"one\s+limitation|a\s+limitation|one\s+limit|a\s+limit|"
    r"eine\s+einschr[aä]nkung|un\s+limite|una\s+limitazione|"
    r"una\s+limitaci[oó]n|uma\s+limita[cç][aã]o|"
    r"jedno\s+ograni[cč]en\w*)\s+"
    r"(?:est|reste|serait|vient|concerne|is|would\s+be|lies|occurs|"
    r"ist|[eè]|es|[ée]|je)\b.*$", re.I,
)
_DEFER = re.compile(
    r"^(?:citez|cite|citer|mentionnez|mentionner|donnez|donner|"
    r"indiquez|indiquer|[eé]crivez|write|please\s+cite|state|"
    r"mention|give|list|you\s+should\s+cite)\b", re.IGNORECASE,
)
_NONANSWER = re.compile(
    r"^(?:aucune|none|n/?a|n[eé]ant|pas\s+de\s+limite|"
    r"no\s+limitation|sans\s+objet|inconnue?)\.?$", re.IGNORECASE,
)
_AMBIGUOUS_SENTENCES = re.compile(
    r"(?:\b(?:e\.g\.|i\.e\.|p\.\s*ex\.|z\.\s*b\.|etc\.)|"
    r"https?://|\b\d+\.\d+\b)", re.IGNORECASE,
)



_EXACT_LITERAL_PATTERNS = (
    re.compile(r"(?:respond|reply|return|answer)\s+(?:only\s+with|exactly)\s*[:：]?\s*([^\n]+?)(?:\s+and\s+nothing\s+else)?$", re.I),
    re.compile(r"(?:r[eé]ponds?|r[eé]pondez|retourne|retournez)\s+(?:uniquement\s+avec|exactement|uniquement)\s*[:：]\s*([^\n]+)$", re.I),
    re.compile(r"(?:antworte|gib)\s+(?:ausschlie[ßs]lich|nur)\s+mit\s*[:：]\s*([^\n]+)$", re.I),
    re.compile(r"(?:odgovori|vrati)\s+(?:isklju[cč]ivo|samo)\s+(?:sa|s)\s*[:：]\s*([^\n]+)$", re.I),
    re.compile(r"(?:rispondi|restituisci)\s+(?:esclusivamente|solo)\s+con\s*[:：]\s*([^\n]+)$", re.I),
    re.compile(r"(?:responde|devuelve)\s+(?:exclusivamente|[uú]nicamente|solo)\s+con\s*[:：]\s*([^\n]+)$", re.I),
    re.compile(r"(?:responde|devolve)\s+(?:exclusivamente|apenas)\s+com\s*[:：]\s*([^\n]+)$", re.I),
    re.compile(r"(?:أجب|اجب)\s+(?:فقط|حصراً|حصرا)\s+(?:بـ?|ب)\s*[:：]?\s*([^\n]+)$"),
)

# FIX7: one-token literal syntax without a colon, e.g. ``Réponds uniquement OK.``.
# Anchoring + a single token prevents ``Réponds uniquement par le code`` from
# becoming a bogus literal contract.
_EXACT_BARE_TOKEN = re.compile(
    r"(?:\b(?:reply|respond|answer|return)\s+(?:only|exactly)|"
    r"\b(?:r[eé]ponds?|r[eé]pondez|retourne|retournez|donne|donnez)\s+(?:uniquement|seulement|exactement)|"
    r"\b(?:antworte|gib)\s+(?:nur|genau)|"
    r"\b(?:odgovori|vrati)\s+(?:samo|to[cč]no)|"
    r"\b(?:rispondi|restituisci)\s+(?:solo|esattamente)|"
    r"\b(?:responde|devuelve)\s+(?:solo|exactamente)|"
    r"\b(?:responde|responda|devolve|devolva)\s+(?:apenas|exatamente)|"
    r"(?:أجب|اجب|اكتب)\s+(?:فقط|حصراً|حصرا))"
    r"\s+([^\s.!?。！？،]{1,96})[.!?。！？،]?\s*$",
    re.I,
)


_CONDITIONAL_EXACT_TAIL = re.compile(
    r"(?:\s|[,;،]\s*)(?:if|otherwise|else|si|sinon|wenn|sonst|"
    r"ako|ina[cč]e|se|altrimenti|de\s+lo\s+contrario|si\s+no|"
    r"sen[aã]o|caso\s+contr[aá]rio|إذا|إن|وإلا|وإلّا)\b",
    re.I,
)
_NESTED_EXACT_DIRECTIVE = re.compile(
    r"\b(?:respond|reply|return|answer)\s+(?:only\s+with|exactly)\b|"
    r"\b(?:antworte|gib)\s+(?:ausschlie[ßs]lich|nur)\s+mit\b|"
    r"\b(?:r[eé]ponds?|r[eé]pondez|retourne|retournez)\s+"
    r"(?:uniquement|exactement)\b",
    re.I,
)


def explicit_exact_literal(prompt: str) -> str | None:
    value = str(prompt or "").strip()
    for pattern in (*_EXACT_LITERAL_PATTERNS, _EXACT_BARE_TOKEN):
        match = pattern.search(value)
        if match:
            literal = match.group(1).strip().strip('"“”')
            # SIQ-P0.2: bare lower-case nouns indicate an output *format*, not
            # provable literal content (Croatian kodom/slovom/riječju).
            # Explicit colon/quoted token contracts remain under older rules.
            if (pattern is _EXACT_BARE_TOKEN and literal.isalpha()
                    and literal.islower()
                    and all('LATIN' in unicodedata.name(ch, '') for ch in literal)):
                continue
            # A conditional classifier such as
            # ``Return exactly OUI if ..., otherwise return exactly NON``
            # is not a literal-output contract.  Its answer is decided by the
            # deterministic semantic verifier.  Treating the whole tail as an
            # exact literal would reject the *correct* zero-model Core answer.
            # Fail closed by abstaining from this narrow shape check whenever
            # branching language or another exact directive appears inside the
            # captured tail.
            if (_CONDITIONAL_EXACT_TAIL.search(literal)
                    or _NESTED_EXACT_DIRECTIVE.search(literal)):
                return None
            return literal or None
    return None


def _exact_literal_shape(request: str, answer: str) -> ShapeCheck | None:
    literal = explicit_exact_literal(request)
    if literal is None:
        return None
    actual = str(answer or "").strip()
    if actual == literal:
        return ShapeCheck('PASS_SHAPE', 'exact_literal_case_sensitive_match')
    return ShapeCheck('FAIL_SHAPE', 'exact_literal_case_sensitive_mismatch')

def explicit_two_sentences(prompt: str) -> bool:
    """Detect explicit two-sentence requests across the eight UI languages.

    Detection is intentionally about the *count contract* only.  Passing this
    function never means the generated prose is grammatical or semantically
    correct; it merely enables an objective two-sentence delivery gate.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return False
    return (parse_mission_contract(prompt).sentence_count == 2 or
            any(intent.search(prompt) and count.search(prompt)
                for intent, count in _TWO_SENTENCE_PATTERNS))


def _sentence_shape(answer: str) -> ShapeCheck:
    # Numbered lines can contain real sentences.  Inspect their complete
    # envelope before stripping ordinal markers; never count "1." as a
    # sentence or silently discard an unsolicited preamble/conclusion.
    if "```" in answer:
        return ShapeCheck('ABSTAIN', 'ambiguous_sentence_boundaries')
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    numbered = re.compile(r'^([0-9]+)[.)]\s+(.+?)\s*$')
    if any(numbered.fullmatch(line) for line in lines):
        # A supported explicit two-sentence contract demands exactly two
        # answer lines and no extra introduction, closing text, or list item.
        if len(lines) != 2:
            return ShapeCheck('FAIL_SHAPE', 'extra_text_outside_two_numbered_sentences')
        items = [numbered.fullmatch(line) for line in lines]
        if any(item is None for item in items):
            return ShapeCheck('FAIL_SHAPE', 'mixed_numbered_and_unstructured_text')
        if [item.group(1) for item in items] != ['1', '2']:
            return ShapeCheck('FAIL_SHAPE', 'expected_numbered_items_one_and_two')
        # Strip the numbering, not any of the candidate prose.  Reuse the
        # existing sentence counter and ambiguity handling for each answer.
        answer = ' '.join(item.group(2).strip() for item in items)
    elif re.search(r'(?m)^\s*(?:[-*]|[0-9]+[.)])\s+', answer):
        return ShapeCheck('ABSTAIN', 'ambiguous_sentence_boundaries')
    # URL, code and abbreviation boundaries remain unprovable by this narrow
    # parser.  ABSTAIN is not factual or linguistic certification.
    if _AMBIGUOUS_SENTENCES.search(answer):
        return ShapeCheck('ABSTAIN', 'ambiguous_sentence_boundaries')
    text = ' '.join(answer.strip().split())
    sentences = [part.strip() for part in re.split(r'(?<=[.!?؟。！？])\s*', text)
                 if part.strip()]
    if len(sentences) != 2:
        return ShapeCheck('FAIL_SHAPE', f'expected_two_sentences_got_{len(sentences)}')
    if any(not re.search(r'[.!?؟。！？]$', part) for part in sentences):
        return ShapeCheck('ABSTAIN', 'ambiguous_terminal_punctuation')
    return ShapeCheck('PASS_SHAPE', 'two_sentences_only_not_factual_proof')



# FIXED5.3: minimum syntactic completeness checks for a stated limitation.
# Deliberately narrow: these are rejection signals, not semantic verification.
_DANGLING_TAIL = re.compile(
    r"\b(?:de|des|du|d['’]|le|la|les|un|une|au|aux|en|a|à|pour|"
    r"avec|sans|par|sur|dans|vers|et|ou|ni|que|qui|dont|si|car|"
    r"the|an|to|of|with|and|or|for|in|on|at|from|if|when|because|"
    r"est|sont|peut|doit|is|are|can|should|will|has|have)\s*$",
    re.IGNORECASE,
)
_OPEN_CONDITION = re.compile(r"^(?:si|if|wenn)\b", re.IGNORECASE)
_CONSEQUENCE = re.compile(
    r"[,;]|\b(?:alors|then|dann|ainsi|therefore|folglich)\b",
    re.IGNORECASE,
)


def _incomplete_limit_clause(value: str) -> bool:
    """Catch clear fragments; never infer that a complete clause is *true*."""
    clean = value.strip().rstrip('.!?。！？').strip()
    if _DANGLING_TAIL.search(clean):
        return True
    # A conditional without a consequence is not a stated limitation.
    if _OPEN_CONDITION.match(clean) and not _CONSEQUENCE.search(clean):
        return True
    return False

def _substantive_limit(value: str) -> bool:
    v = value.strip().rstrip('.!?。！？').strip()
    if _DEFER.match(v) or _NONANSWER.fullmatch(v):
        return False
    # The label alone, a short token, or a command to the user is not a limit.
    if len(v.split()) < 4 or not re.search(r'[A-Za-zÀ-ÿ\u0600-\u06ff]', v):
        return False
    if re.search(r'\b(?:citez|mentionnez|indiquez|please\s+cite|you\s+must\s+cite)\b', v, re.I):
        return False
    # FIXED5.3: four words do not establish a finished limitation.
    if _incomplete_limit_clause(v):
        return False
    return True


def _step_shape(answer: str) -> ShapeCheck:
    steps: dict[int, str] = {}
    limits: list[str] = []
    for line in answer.splitlines():
        m = _STEP.match(line)
        if m:
            index = int(m.group(1))
            if index in steps:
                return ShapeCheck('FAIL_SHAPE', 'duplicate_step_number')
            steps[index] = m.group(2)
            continue
        m = _LIMIT_LABEL.match(line)
        if m:
            limits.append(m.group(1))
            continue
        if _LIMIT_PROSE.match(line):
            limits.append(line.strip())
    if set(steps) != {1, 2, 3}:
        return ShapeCheck('FAIL_SHAPE', 'three_numbered_steps_missing')
    if any(len(value.split()) < 3 or _DEFER.match(value) for value in steps.values()):
        return ShapeCheck('FAIL_SHAPE', 'step_not_substantive')
    if len(limits) != 1:
        return ShapeCheck('FAIL_SHAPE', 'explicit_limit_statement_missing')
    if not _substantive_limit(limits[0]):
        return ShapeCheck('FAIL_SHAPE', 'limit_not_substantive')
    return ShapeCheck('PASS_SHAPE', 'three_steps_and_limit_shape_only')




_ARABIC_ONLY_REQUEST = re.compile(
    r"(?:أجب|اجب|اكتب|تحدث).{0,120}(?:العربية|بالعربية)|"
    r"(?:لا|دون|بدون).{0,60}(?:كلمات?\s+إنجليزية|الإنجليزية|أحرف?\s+لاتينية)",
    re.S,
)
_GERMAN_ONLY_REQUEST = re.compile(
    r"\b(?:antworte|ntworte|schreibe|sprich)\b.{0,100}\b(?:nur\s+auf\s+deutsch|auf\s+deutsch|deutsch)\b",
    re.I | re.S,
)

def _explicit_language_script_shape(request: str, answer: str) -> ShapeCheck | None:
    """Reject only obvious script violations of an explicit language-only request.

    This is not a fluency detector.  It catches mechanically provable violations
    such as English/Latin words in an Arabic-only response (while allowing the
    product name MAESTRO) or Arabic/CJK text in a German-only response.
    """
    req = str(request or "")
    out = str(answer or "")
    if _ARABIC_ONLY_REQUEST.search(req):
        cleaned = re.sub(r"MAESTRO", "", out, flags=re.I)
        # FIX8: do not maintain an incomplete blacklist of Latin/CJK/Cyrillic.
        # For an explicit Arabic-only contract, every remaining alphabetic
        # character must belong to an Arabic Unicode repertoire.  Digits,
        # punctuation and whitespace remain allowed; MAESTRO is the sole
        # product-name exception removed above.
        foreign_letters = [
            ch for ch in cleaned
            if ch.isalpha() and "ARABIC" not in unicodedata.name(ch, "")
        ]
        if foreign_letters:
            return ShapeCheck('FAIL_SHAPE', 'arabic_only_foreign_script')
    if _GERMAN_ONLY_REQUEST.search(req):
        from .native_linguistic_revision import _english_fallback
        if _english_fallback(out):
            return ShapeCheck('FAIL_SHAPE', 'german_only_english_fallback')
        cleaned = re.sub(r"MAESTRO", "", out, flags=re.I)
        if re.search(r"[\u0600-\u06ff\u3400-\u9fff\u3040-\u30ff\u0400-\u04ff]", cleaned):
            return ShapeCheck('FAIL_SHAPE', 'german_only_foreign_script')
    return None

def _one_sentence_shape(answer: str) -> ShapeCheck:
    if '```' in answer or _AMBIGUOUS_SENTENCES.search(answer):
        return ShapeCheck('ABSTAIN', 'ambiguous_sentence_boundaries')
    text = ' '.join(answer.strip().split())
    parts = [p.strip() for p in re.split(r'(?<=[.!?؟。！？])\s*', text) if p.strip()]
    if len(parts) != 1:
        return ShapeCheck('FAIL_SHAPE', f'expected_one_sentence_got_{len(parts)}')
    if not re.search(r'[.!?؟。！？]$', parts[0]):
        return ShapeCheck('ABSTAIN', 'ambiguous_terminal_punctuation')
    return ShapeCheck('PASS_SHAPE', 'one_sentence_only_not_factual_proof')


def evaluate_shape(request: str, answer: str) -> ShapeCheck:
    if not isinstance(request, str) or not isinstance(answer, str) or not answer.strip():
        return ShapeCheck('FAIL_SHAPE', 'empty_or_invalid_text')
    if '```' in request:
        return ShapeCheck('ABSTAIN', 'quoted_code_or_instruction_ambiguous')
    exact = _exact_literal_shape(request, answer)
    if exact is not None:
        return exact
    script = _explicit_language_script_shape(request, answer)
    if script is not None:
        return script
    if explicit_two_sentences(request):
        return _sentence_shape(answer)
    if parse_mission_contract(request).sentence_count == 1:
        return _one_sentence_shape(answer)
    if _THREE_STEPS.search(request) and _LIMIT_REQUEST.search(request):
        return _step_shape(answer)
    return ShapeCheck('ABSTAIN', 'no_supported_explicit_shape_contract')
