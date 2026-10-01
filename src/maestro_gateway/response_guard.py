from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


_REPEAT_MARKERS = (
    "repeat",
    "repeat exactly",
    "say exactly",
    "copy exactly",
    "répète",
    "repete",
    "recopie",
    "wiederhole",
    "ponovi",
    "ripeti",
    "repite",
    "repita",
    "أعد",
)

_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class QualityResult:
    accepted: bool
    reason: str
    similarity: float


def _normalize(value: str) -> str:
    words = _WORD.findall(
        str(value or "").casefold()
    )
    return " ".join(words)


def explicit_repeat_request(prompt: str) -> bool:
    value = str(prompt or "").casefold()
    return any(
        marker in value
        for marker in _REPEAT_MARKERS
    )


def evaluate_response(
    prompt: str,
    output: str,
) -> QualityResult:
    source = _normalize(prompt)
    answer = _normalize(output)

    if not answer:
        return QualityResult(
            False,
            "empty_output",
            1.0,
        )

    if explicit_repeat_request(prompt):
        return QualityResult(
            True,
            "explicit_repeat_requested",
            0.0,
        )

    similarity = SequenceMatcher(
        None,
        source,
        answer,
    ).ratio()

    if source and answer == source:
        return QualityResult(
            False,
            "exact_prompt_echo",
            similarity,
        )

    if (
        len(source) >= 20
        and similarity >= 0.90
    ):
        return QualityResult(
            False,
            "near_prompt_echo",
            similarity,
        )

    return QualityResult(
        True,
        "accepted",
        similarity,
    )
