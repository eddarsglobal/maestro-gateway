"""P0.4.3c1: narrow, falsifiable final-answer regression screening.

This module cannot prove linguistic fluency, semantic competence or identity
truth. Its only purpose is to reject demonstrably wrong answers to a handful
of unambiguous intents observed in real Mac LIVE use. Never synthesize a reply.
"""
from __future__ import annotations
import re

_IDENTITY_TURN = re.compile(
    r"^\s*[¿¡]?\s*(?:(?:hi|hello|hallo|salut|bonjour|ciao|hola|ol[aá]|bok|pozdrav)[!,.:\s]+)?"
    r"(?:wer\s+bist\s+du|wie\s+hei[ßs]t\s+du|"
    r"who\s+are\s+you|what\s+are\s+you|"
    r"(?:tu\s+es\s+qui(?:\s+toi)?|qui\s+es[- ]tu|qui\s+[eê]tes[- ]vous)|"
    r"(?:tko|ko)\s+si(?:\s+ti)?|"
    r"chi\s+sei|qui[eé]n\s+eres|quem\s+[eé]s(?:\s+tu)?|quem\s+[eé]\s+voc[eê]|"
    r"من\s+(?:أنت|انت))\s*[?!.؟\s]*$", re.I,
)
_FIRST_PERSON_OTHER_MODEL = re.compile(
    r"\b(?:ich\s+bin|je\s+suis|i\s+am|sono|soy|eu\s+sou|ja\s+sam)\s+"
    r"(?:(?:ein(?:e)?|un(?:e)?|um(?:a)?|o|a|der|die|das)\s+)?"
    r"(?:qwen|llama|gemma|deepseek|chatgpt|gpt[- ]?[0-9]+|mistral|"
    r"phi[- ]?[0-9]+|claude)\b", re.I,
)
_ARABIC_FIRST_PERSON_MODEL = re.compile(
    r"(?:أنا|انا)\s+(?:(?:نموذج|مساعد)\s+)?(?:qwen|llama|gemma|chatgpt|deepseek|mistral|claude)\b", re.I,
)
# G1-SIQ identity+capabilities reconciliation: recognize the *existing* combined
# self-introduction intents in the same lexical backstop as a pure identity turn.
# This never authorizes capability claims or produces a canned answer.
_COMBINED_IDENTITY_TURN = re.compile(
    r"^\s*[¿¡]?\s*(?:wer\s+bist\s+du\s+und\s+was\s+kannst\s+du|"
    r"who\s+are\s+you\s+and\s+what\s+can\s+you\s+do|"
    r"qui\s+es[- ]tu\s+et\s+que\s+peux[- ]tu\s+faire|"
    r"tko\s+si\s+ti\s+i\s+[šs]to\s+mo[žz]e[šs]\s+raditi|"
    r"chi\s+sei\s+e\s+cosa\s+(?:puoi|sai)\s+fare|"
    r"qui[eé]n\s+eres\s+y\s+qu[eé]\s+puedes\s+hacer|"
    r"quem\s+[eé]s\s+tu\s+e\s+o\s+que\s+podes\s+fazer|"
    r"من\s+(?:أنت|انت)\s+وماذا\s+(?:يمكنك|تستطيع)\s+(?:أن\s+)?تفعل)"
    r"\s*[?!.؟\s]*$", re.I,
)

# A *positive first-person MAESTRO affiliation*, not mere mention of a model
# publisher. Example: "Ich bin MAESTRO, eine ... AI-Assistentin der Alibaba Cloud".
# Keep provenance such as "I use Qwen, developed by Alibaba" permissible.
_FALSE_ASSISTANT_OWNER = re.compile(
    r"\bmaestro\b[^.!?؟\n]{0,105}?\b"
    r"(?:ai[- ]?assistentin|ki[- ]?assistentin|ai[- ]?assistent|ki[- ]?assistent|"
    r"assistant(?:e)?|assistente|asistente)\s+"
    r"(?:der|von|from|de|do|da|d['’])\s*"
    r"(?:alibaba(?:\s+cloud)?|openai|google|anthropic|microsoft|meta)\b",
    re.I,
)

def _false_assistant_owner(output: str) -> bool:
    match = _FALSE_ASSISTANT_OWNER.search(output)
    if match is None:
        return False
    # Reject only affirmative ownership. Explicit denial within the same
    # short identity clause must not be counted as a false assertion.
    clause = match.group(0).casefold()
    if re.search(
        r"\b(?:kein(?:e|en|em|er|es)?|not|nicht|pas|non)\b"
        r"(?:\s+[\w-]+){0,4}\s+"
        r"(?:ai[- ]?assistentin|ki[- ]?assistentin|ai[- ]?assistent|"
        r"ki[- ]?assistent|assistant(?:e)?|assistente|asistente)\s+"
        r"(?:der|von|from|de|do|da|d['’])\s+", clause
    ):
        return False
    return True


_CROATIAN_GREETING = re.compile(
    r"^\s*(?:dobar\s+dan|dobro\s+jutro|dobra\s+ve[cč]er|"
    r"bok|pozdrav)[!?.\s]*$", re.I,
)
_OBVIOUS_FOREIGN_GREETING = re.compile(
    r"^\s*(?:hallo|guten\s+tag|guten\s+morgen|"
    r"bonjour|salut|hello|hi|ciao|hola|ol[aá])\b", re.I,
)
_FOREIGN_GREETING_FOLLOWUP = re.compile(
    r"\b(?:wie\s+geht\s+es\s+(?:dir|ihnen)|"
    r"how\s+are\s+you|comment\s+allez[- ]vous)\b", re.I,
)


def observed_regression_failure(prompt: str, output: str) -> str | None:
    """Reject only demonstrable false identity/clear mismatched greeting.

    `None` is UNKNOWN/NOT_REJECTED, never a fluency certificate.
    A positive answer must still pass the ordinary Response Quality Guard.
    """
    user = str(prompt or "").strip()
    answer = str(output or "").strip()
    if _IDENTITY_TURN.fullmatch(user) or _COMBINED_IDENTITY_TURN.fullmatch(user):
        if _FIRST_PERSON_OTHER_MODEL.search(answer) or _ARABIC_FIRST_PERSON_MODEL.search(answer):
            return "assistant_identity_claims_model_name"
        if re.search(r"\bMAESTRO\b", answer, re.I) is None:
            return "assistant_identity_not_maestro"
        if _false_assistant_owner(answer):
            return "assistant_identity_false_vendor_affiliation"
    if _CROATIAN_GREETING.fullmatch(user) and (
        _OBVIOUS_FOREIGN_GREETING.search(answer)
        or _FOREIGN_GREETING_FOLLOWUP.search(answer[:180])
    ):
        return "croatian_greeting_answer_in_foreign_language"
    return None
