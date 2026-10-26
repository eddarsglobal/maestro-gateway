"""Lossless delivery-shape repair for explicit two-sentence contracts.

This module never invents, removes, translates, or reorders model words.  It may
only add missing terminal punctuation when the model already returned exactly
two substantive prose lines.  If the structure is ambiguous, it abstains.
"""
from __future__ import annotations
import re
from .response_constraints import explicit_two_sentences, evaluate_shape

_TERMINAL = re.compile(r'[.!?。！？]$')
_LIST = re.compile(r'^\s*(?:[-*]|\d+[.)])\s+')


def propose_lossless_two_sentence_line_repair(prompt: str, answer: str) -> str | None:
    if not explicit_two_sentences(prompt):
        return None
    if not isinstance(answer, str) or '```' in answer:
        return None
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    if len(lines) != 2 or any(_LIST.match(line) for line in lines):
        return None
    # Do not split prose or reinterpret punctuation.  Each line must already be
    # a plausible standalone clause and contain no internal terminal boundary.
    repaired=[]; changed=False
    for line in lines:
        if len(line.split()) < 3:
            return None
        body=line.rstrip()
        if re.search(r'[.!?。！？].+\S', body):
            return None
        if not _TERMINAL.search(body):
            body += '.'
            changed=True
        repaired.append(body)
    if not changed:
        return None
    candidate='\n'.join(repaired)
    return candidate if evaluate_shape(prompt,candidate).status == 'PASS_SHAPE' else None
