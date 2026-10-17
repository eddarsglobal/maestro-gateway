"""G4A: conservative, text-preserving layout-only repair of explicit two-paragraph output.

No calls to any model, storage, Core or router. This is NOT semantic verification.
Only reflows whitespace at a sentence boundary; rejects markdown, markup, code,
ambiguous boundaries, excessive output and anything not covered by the contract.
"""
from __future__ import annotations

import re
from .response_guard import explicit_format_evidence

MAX_CHARS = 16000
_BANNED_LINES = re.compile(r'(?m)^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||```|~~~|>\s|\*\*[^\n]*\*\*\s*$)')
_BANNED_INLINE = re.compile(r'[`<>]|\|\s*[-:]|\[(?:.+?)\]\(https?://', re.I)
_SENTENCE_END = re.compile(r'[.!?。！？](?:[”"»’\)]*)\s+(?=[^\W\d_])', re.UNICODE)


def propose_lossless_two_paragraph_repair(prompt: str, output: str) -> str | None:
    """Propose layout-only repair or return None; final normal Guard still decides.

    The normalized concatenation of all *non-whitespace characters* is exactly
    unchanged; this cannot add facts, remove text, fix arithmetic or claim truth.
    """
    if not isinstance(prompt, str) or not isinstance(output, str):
        return None
    if not output.strip() or len(output) > MAX_CHARS or '\x00' in output:
        return None
    original_evidence = explicit_format_evidence(prompt, output)
    if (original_evidence['format_contract'] != 'two_paragraphs' or
            original_evidence['format_conformance'] != 'fail'):
        return None
    if _BANNED_LINES.search(output) or _BANNED_INLINE.search(output):
        return None
    # Avoid mangling existing explicitly structured output, including indentation
    # indicative of lists or code; allow ordinary hard-wrapped prose lines.
    lines = output.splitlines()
    if any((x.startswith(('    ', '\t')) and x.strip()) for x in lines):
        return None
    plain = re.sub(r'\s+', ' ', output).strip()
    if len(plain) < 100 or len(plain) > MAX_CHARS:
        return None
    if re.search(r'(?m)(?:^|\s)(?:\{["\']|</|<script|^\[)', plain, re.I):
        return None
    # Require a clearly bounded full sentence on BOTH sides of the split.
    # Strong conservative gate: don't split after e.g. "e.g." or a decimal.
    positions = []
    for match in _SENTENCE_END.finditer(plain):
        pos = match.start() + 1
        left, right = plain[:pos].strip(), plain[match.end():].strip()
        if min(len(left), len(right)) < 35:
            continue
        if not re.search(r'[.!?。！？][”"»’\)]?$', left):
            continue
        if not re.search(r'[.!?。！？][”"»’\)]?$', right):
            continue
        # Refuse suspicious abbreviation boundaries.
        if re.search(r'\b(?:M|Mme|Mr|Dr|Prof|etc|e\.g|i\.e|p\.ex)\.$', left, re.I):
            continue
        positions.append((abs(pos - len(plain) / 2), pos, match.end()))
    if not positions:
        return None
    _, pos, end = min(positions)
    candidate = plain[:pos].strip() + '\n\n' + plain[end:].strip()
    if re.sub(r'\s+', '', candidate) != re.sub(r'\s+', '', output):
        return None
    evidence = explicit_format_evidence(prompt, candidate)
    if evidence['format_conformance'] != 'pass':
        return None
    return candidate
