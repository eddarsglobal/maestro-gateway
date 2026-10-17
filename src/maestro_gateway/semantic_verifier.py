"""Deterministic semantic verification for MAESTRO's tiny DIRECT fast lane.

This module intentionally covers only prompts whose requested answer can be
proved locally without an LLM. Unsupported natural-language requests return
``None`` and continue through the normal governed response-quality path.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import math
import operator
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class DeterministicExpectation:
    subtask: str
    expected: str


@dataclass(frozen=True, slots=True)
class SemanticVerification:
    accepted: bool
    subtask: str
    expected: str
    observed: str


_CONDITIONAL_EN = re.compile(
    r"(?:return|reply|respond|answer)\s+(?:exactly\s+|only\s+)?(?P<yes>[\w-]+)\s+"
    r"if\s+(?P<n>-?\d+)\s+is\s+(?:an?\s+)?(?P<prop>even|odd|prime)\b.*?"
    r"otherwise\s+(?:return|reply|respond|answer)\s+(?:exactly\s+|only\s+)?(?P<no>[\w-]+)",
    re.I | re.S,
)
_CONDITIONAL_FR = re.compile(
    r"(?:r[eé]ponds?|retourne|donne)\s+(?:exactement\s+|uniquement\s+|seulement\s+)?(?P<yes>[\w-]+)\s+"
    r"si\s+(?P<n>-?\d+)\s+est\s+(?:un\s+nombre\s+)?(?P<prop>pair|impair|premier)\b.*?"
    r"sinon\s+(?:r[eé]ponds?|retourne|donne)?\s*(?:exactement\s+|uniquement\s+|seulement\s+)?(?P<no>[\w-]+)",
    re.I | re.S,
)
_ARITH = re.compile(
    r"(?:compute|calculate|calcule|calculer|berechne|calcola|calcula|izračunaj|izracunaj|احسب)\s+"
    r"(?P<expr>[-+*/%()\d\s.]{3,80})",
    re.I,
)
_SORT = re.compile(
    r"(?:sort|trie|trier|ordonne|ordena|sortiraj).{0,50}?(?P<nums>-?\d+(?:\s*[,;]\s*-?\d+){1,20})",
    re.I | re.S,
)
_PIPE_SEQUENCE = re.compile(
    r"(?<![\w.|-])(?P<seq>[\w.-]+(?:\|[\w.-]+){1,20})(?![\w.|-])",
    re.UNICODE,
)
_EXTRACTION_ORDINALS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (0, re.compile(
        r"(?:first\s+(?:item|element)|premi(?:er|ère)\s+(?:[eé]l[eé]ment|item)|"
        r"erst(?:e|es|en)\s+(?:element|eintrag)|primo\s+(?:elemento|item)|"
        r"(?:primer|primero)\s+(?:elemento|item)|primeiro\s+(?:elemento|item)|"
        r"prvi\s+(?:element|unos)|العنصر\s+الأول)",
        re.I | re.UNICODE,
    )),
    (1, re.compile(
        r"(?:second\s+(?:item|element)|deuxi[eè]me\s+(?:[eé]l[eé]ment|item)|"
        r"zweit(?:e|es|en)\s+(?:element|eintrag)|secondo\s+(?:elemento|item)|"
        r"segundo\s+(?:elemento|item)|drugi\s+(?:element|unos)|العنصر\s+الثاني)",
        re.I | re.UNICODE,
    )),
    (2, re.compile(
        r"(?:third\s+(?:item|element)|troisi[eè]me\s+(?:[eé]l[eé]ment|item)|"
        r"dritt(?:e|es|en)\s+(?:element|eintrag)|terzo\s+(?:elemento|item)|"
        r"tercer(?:o)?\s+(?:elemento|item)|terceiro\s+(?:elemento|item)|"
        r"tre[cć]i\s+(?:element|unos)|العنصر\s+الثالث)",
        re.I | re.UNICODE,
    )),
)
# FIXED2: Only certify extraction when its directive and source are unambiguous.
# "Find the first ordinal anywhere" is not a proof of user intent.
_EXTRACTION_COMMAND = re.compile(
    r"\b(?:return|reply|respond|answer|extract|give|pick|select|"
    r"retourne|retourner|r[eé]ponds?|donne|extrais|extrait|renvoie|choisis|"
    r"gib|extrahiere|nimm|"
    r"estrai|restituisci|scegli|"
    r"devuelve|extrae|elige|retorna|devolve|"
    r"vrati|izdvoji|odaberi|antworte|nenne|responde|rispondi|odgovori)\b|"
    r"(?:استخرج|أرجع|ارجع|أعط|اعط|أعد|اعد|اختر)",
    re.I | re.UNICODE,
)
_EXTRACTION_NEGATION = re.compile(
    r"(?:\b(?:not|no|pas|nicht|ne|non|nemoj|nije|ni|sin|sem)\s+"
    r"(?:(?:the|le|la|les|l['’]|de|el|lo|il|das|den|die|der|o|a|samo|only|uniquement)\s+){0,3}"
    r"|\b(?:instead\s+of|rather\s+than|au\s+lieu\s+de|plut[oô]t\s+que)\s+"
    r"(?:(?:the|le|la|l['’])\s+)?"
    r"|(?:ليس|وليس|لا|غير)\s*)$",
    re.I | re.UNICODE,
)
_EXTRACTION_SOURCE_CUE = re.compile(
    r"(?:\b(?:dans|from|in|aus|da|de|do|iz|od)\b\s+"
    r"(?:(?:la|le|the|die|der|das|lista|liste|list|sequence|s[eé]quence)\s+){0,3}"
    r"|(?:في|من)\s*)$",
    re.I | re.UNICODE,
)
_EXTRACTION_COORDINATED = re.compile(
    r"\b(?:first|second|third|premi\w*|deuxi\w*|troisi\w*|1st|2nd|3rd)\b"
    r"\s+(?:(?:item|element|[eé]l[eé]ment)\s+)?(?:or|ou|oder|ili|o|أو)\s+"
    r"(?:(?:the|le|la|das|der)\s+)?(?:first|second|third|premi\w*|deuxi\w*|troisi\w*|1st|2nd|3rd)\b",
    re.I | re.UNICODE,
)
_EXTRACTION_MULTITASK = re.compile(
    r"\b(?:and\s+(?:then\s+)?(?:explain|summarize|translate|compare)|"
    r"et\s+(?:puis\s+)?(?:explique|r[eé]sume|traduis|compare)|"
    r"und\s+(?:dann\s+)?(?:erkl[aä]re|[uü]bersetze))\b",
    re.I | re.UNICODE,
)
_EXTRACTION_DIGITAL_ORDINALS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (0, re.compile(r"(?<!\w)1\s*(?:st|er|re|e|[eè]me|eme|\u1d49)?\s+(?:[eé]l[eé]ment|element|item|eintrag|elemento|unos)(?!\w)", re.I)),
    (1, re.compile(r"(?<!\w)2\s*(?:nd|e|[eè]me|eme|\u1d49)?\s+(?:[eé]l[eé]ment|element|item|eintrag|elemento|unos)(?!\w)", re.I)),
    (2, re.compile(r"(?<!\w)3\s*(?:rd|e|[eè]me|eme|\u1d49)?\s+(?:[eé]l[eé]ment|element|item|eintrag|elemento|unos)(?!\w)", re.I)),
)


def _deterministic_pipe_extraction(value: str) -> DeterministicExpectation | None:
    """Conservatively bind exactly one requested ordinal to one pipe sequence.

    A negative example must not become an instruction; competing targets or
    ordinals are intentionally returned to the normal governed path (None).
    """
    sequences = list(_PIPE_SEQUENCE.finditer(value))
    if not sequences:
        return None
    if _EXTRACTION_COORDINATED.search(value) or _EXTRACTION_MULTITASK.search(value):
        return None

    ordinal_hits: list[tuple[int, int, int]] = []
    for index, pattern in (*_EXTRACTION_ORDINALS, *_EXTRACTION_DIGITAL_ORDINALS):
        ordinal_hits.extend((m.start(), m.end(), index) for m in pattern.finditer(value))
    if not ordinal_hits:
        return None
    ordinal_hits.sort()

    active_indices: set[int] = set()
    forbidden_indices: set[int] = set()
    for start, _, index in ordinal_hits:
        # A directive may live in a separate clause; punctuation prevents an
        # earlier instruction from legitimizing an unrelated mention.
        boundary = max(value.rfind(c, 0, start) for c in ".!?;:\n")
        preceding = value[boundary + 1:start]
        commands = list(_EXTRACTION_COMMAND.finditer(preceding))
        if not commands:
            continue
        command = commands[-1]
        # A bound applies only to a nearby instruction, not to arbitrary prose.
        if len(preceding) - command.end() > 90:
            continue
        if _EXTRACTION_NEGATION.search(preceding):
            forbidden_indices.add(index)
            continue
        # Reject "do not return the first" even when the negation appears
        # before the imperative; accepting the forbidden target is unsafe.
        immediate_prefix = preceding[max(0, command.start() - 12):command.start()]
        if re.search(r"(?:\b(?:ne|do\s+not|don't|nicht|no)\s*)$", immediate_prefix, re.I):
            forbidden_indices.add(index)
            continue
        active_indices.add(index)

    if len(active_indices) != 1 or active_indices & forbidden_indices:
        return None
    index = next(iter(active_indices))

    if len(sequences) == 1:
        source = sequences[0]
    else:
        # With multiple literals, only a unique, *explicitly* introduced
        # source is safe. Do not assume the first/last literal is intended.
        cued = [m for m in sequences if _EXTRACTION_SOURCE_CUE.search(value[max(0, m.start() - 65):m.start()])]
        if len(cued) != 1:
            return None
        source = cued[0]

    items = source.group("seq").split("|")
    if index >= len(items):
        return None
    return DeterministicExpectation("extraction", items[index])


_EXACT_EN = re.compile(
    r"(?:return|reply|respond|answer)\s+exactly\s+(?P<value>.+?)\s+(?:and\s+)?(?:nothing\s+else|only)\b",
    re.I | re.S,
)
_EXACT_FR = re.compile(
    r"(?:r[eé]ponds?|retourne|donne)\s+exactement\s+(?P<value>.+?)\s+(?:et\s+)?(?:rien\s+d['’]autre|uniquement|seulement)\b",
    re.I | re.S,
)

_ALLOWED_BINOPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY: dict[type[ast.unaryop], Any] = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_number(node: ast.AST, depth: int = 0) -> float | int:
    if depth > 12:
        raise ValueError("expression too deep")
    if isinstance(node, ast.Expression):
        return _safe_number(node.body, depth + 1)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        if not math.isfinite(float(node.value)) or abs(float(node.value)) > 1e9:
            raise ValueError("number out of bounds")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_safe_number(node.operand, depth + 1))
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _safe_number(node.left, depth + 1)
        right = _safe_number(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > 8:
            raise ValueError("power out of bounds")
        result = _ALLOWED_BINOPS[type(node.op)](left, right)
        if not math.isfinite(float(result)) or abs(float(result)) > 1e12:
            raise ValueError("result out of bounds")
        return result
    raise ValueError("unsupported expression")


def _format_number(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    limit = int(math.isqrt(n))
    for d in range(3, limit + 1, 2):
        if n % d == 0:
            return False
    return True


def _classification(match: re.Match[str]) -> DeterministicExpectation:
    n = int(match.group("n"))
    prop = match.group("prop").casefold()
    if prop in {"even", "pair"}:
        truth = n % 2 == 0
    elif prop in {"odd", "impair"}:
        truth = n % 2 != 0
    else:
        truth = _is_prime(n)
    return DeterministicExpectation("classification", match.group("yes") if truth else match.group("no"))


def deterministic_expectation(prompt: str) -> DeterministicExpectation | None:
    """Return a locally provable expectation, otherwise ``None``.

    Ordering matters: conditional classification is checked before exact-output
    syntax so ``return exactly OUI if ... otherwise ...`` is not misread as an
    echo request.
    """
    value = str(prompt or "").strip()
    if not value or len(value) > 480:
        return None

    for pattern in (_CONDITIONAL_EN, _CONDITIONAL_FR):
        m = pattern.search(value)
        if m:
            try:
                return _classification(m)
            except (ValueError, OverflowError):
                return None

    m = _ARITH.search(value)
    if m:
        expr = m.group("expr").strip().rstrip(". ,;")
        try:
            tree = ast.parse(expr, mode="eval")
            return DeterministicExpectation("arithmetic", _format_number(_safe_number(tree)))
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
            return None

    m = _SORT.search(value)
    if m:
        try:
            nums = [int(x.strip()) for x in re.split(r"[,;]", m.group("nums"))]
        except ValueError:
            return None
        return DeterministicExpectation("sorting", ",".join(str(x) for x in sorted(nums)))

    extraction = _deterministic_pipe_extraction(value)
    if extraction is not None:
        return extraction

    for pattern in (_EXACT_EN, _EXACT_FR):
        m = pattern.search(value)
        if m:
            expected = m.group("value").strip().strip('"\'` ')
            if expected and len(expected) <= 96 and "\n" not in expected:
                return DeterministicExpectation("exact_echo", expected.rstrip(". "))
    return None


def semantic_subtask(prompt: str) -> str | None:
    expected = deterministic_expectation(prompt)
    return expected.subtask if expected else None


def _normalized_answer(value: str) -> str:
    # Semantic verification ignores harmless terminal punctuation/quotes but
    # not lexical content. Format-specific requirements remain the response
    # guard's responsibility.
    return str(value or "").strip().strip('"\'` ').rstrip(".!?。، ").casefold()


def verify_deterministic_response(prompt: str, output: str) -> SemanticVerification | None:
    expectation = deterministic_expectation(prompt)
    if expectation is None:
        return None
    observed = _normalized_answer(output)
    expected = _normalized_answer(expectation.expected)
    return SemanticVerification(
        accepted=observed == expected,
        subtask=expectation.subtask,
        expected=expectation.expected,
        observed=str(output or "").strip(),
    )


def deterministic_truth_projection(verification: SemanticVerification | None) -> str | None:
    """Return only a locally proven replacement for a proven-wrong tiny DIRECT answer.

    This is not an LLM correction and does not infer open-ended truth. It can
    project only the exact expectation already proven by deterministic_expectation().
    Unsupported prompts and already-correct responses return ``None``.
    """
    if verification is None or verification.accepted:
        return None
    value = str(verification.expected or "").strip()
    return value or None
