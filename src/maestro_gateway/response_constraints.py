"""Narrow, provider-neutral output-shape checks for explicit user constraints.

This module never decides factual truth, fluency, model competence, routing,
permissions, or what constitutes a correct answer to an open-ended question.
An unrecognized/ambiguous request returns ABSTAIN, not a guessed verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

Status = Literal['PASS_SHAPE', 'FAIL_SHAPE', 'ABSTAIN']


@dataclass(frozen=True, slots=True)
class ShapeCheck:
    status: Status
    reason: str
    factual_truth_verified: bool = False


# Only an explicit instruction triggers a shape contract.  The supported
# fragments are intentionally limited; other UI languages safely abstain.
_TWO_SENTENCES = re.compile(
    r"(?:\b(?:en\s+)?(?:exactement\s+)?(?:deux|2)\s+(?:courtes?\s+)?phrases?\b|"
    r"\b(?:in\s+)?(?:exactly\s+)?(?:two|2)\s+(?:short\s+)?sentences?\b|"
    r"\b(?:in\s+)?(?:genau\s+)?(?:zwei|2)\s+(?:kurzen?\s+)?s[aä]tzen?\b|"
    r"\b(?:u\s+)?(?:to[cč]no\s+)?(?:dvije|dve|2)\s+re[cč]enice\b|"
    r"\b(?:in\s+)?(?:esattamente\s+)?(?:due|2)\s+frasi\b|"
    r"\b(?:en\s+)?(?:exactamente\s+)?(?:dos|2)\s+frases\b|"
    r"\b(?:em\s+)?(?:exatamente\s+)?(?:duas|2)\s+frases\b|"
    r"(?:في\s+)?جملتين\s*(?:فقط)?\b)", re.I,
)
_TWO_INTENT = re.compile(
    r"\b(?:explique(?:[- ]?moi)?|expliquer|r[eé]ponds?|r[eé]pondez|"
    r"d[eé]cris|r[eé]sume|donne(?:[- ]?moi)?|antworte|erkl[aä]re|"
    r"beschreibe|schreibe|fasse|reply|respond|explain|summarize|"
    r"describe|write|give|objasni|navedi|spiega|spiegami|"
    r"explica|explique|descreve|escreve)\b|اشرح|وضّح", re.I,
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


def explicit_two_sentences(prompt: str) -> bool:
    """Detect only narrow, explicit two-sentence instructions."""
    return bool(isinstance(prompt, str) and _TWO_SENTENCES.search(prompt)
                and _TWO_INTENT.search(prompt))


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
    sentences = [part.strip() for part in re.split(r'(?<=[.!?。！？])\s+', text)
                 if part.strip()]
    if len(sentences) != 2:
        return ShapeCheck('FAIL_SHAPE', f'expected_two_sentences_got_{len(sentences)}')
    if any(not re.search(r'[.!?。！？]$', part) for part in sentences):
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


def evaluate_shape(request: str, answer: str) -> ShapeCheck:
    if not isinstance(request, str) or not isinstance(answer, str) or not answer.strip():
        return ShapeCheck('FAIL_SHAPE', 'empty_or_invalid_text')
    if '```' in request:
        return ShapeCheck('ABSTAIN', 'quoted_code_or_instruction_ambiguous')
    if explicit_two_sentences(request):
        return _sentence_shape(answer)
    if _THREE_STEPS.search(request) and _LIMIT_REQUEST.search(request):
        return _step_shape(answer)
    return ShapeCheck('ABSTAIN', 'no_supported_explicit_shape_contract')
