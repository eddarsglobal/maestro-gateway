"""Conservative, local, independent language evidence for MAESTRO.

No second model, vendor heuristic, cloud endpoint, downloads, network, or background
inference. Exact observed defect patterns are negative evidence, NOT a positive
fluency certificate. This module never stores prompt or generated text.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from threading import RLock
import re

_SUPPORTED = frozenset({'de', 'ar', 'fr', 'en', 'it', 'pt', 'es', 'hr'})
# These are observed failures, not a general-purpose language or grammar model.
_DE_BAD = (
    ('linguistic_greeting_unnatural', re.compile(r'\bhallo\s+dir\b', re.I)),
    ('linguistic_meta_german', re.compile(r'^\s*(?:antwort|mein satz|neuer satz)\s*:', re.I)),
    # A real LIVE slot inverted who is helping whom; direct-address alone is
    # not evidence that the grammatical actor was correct.
    ('linguistic_reversed_agent', re.compile(
        r'\bwenn\s+du\b[^.!?]{0,150}\bdie\s+du\b[^.!?]{0,85}\bunterstützen\s+kannst\b', re.I)),
)
_AR_BAD = (
    ('linguistic_false_translated_identity', re.compile(r'المعلم\s+الكبير')),
    ('linguistic_unnatural_local_operation', re.compile(r'تشغيله\s+محلي(?![ًة])')),
    ('linguistic_wrong_maestro_agreement', re.compile(r'(?:^|[.!؟]\s*)\s*تتيح\s+MAESTRO\b', re.I)),
    ('linguistic_meta_arabic', re.compile(r'^\s*الجملة\s+الجديدة\s*:')),
    # Additional verified negative examples, documented by real Mac Gateway
    # executions. These rules are negative evidence, never fluency certificates.
    ('linguistic_unnatural_purpose', re.compile(r'مخصص\s+لتشغيل\s+محلي(?:[ًَُ]?ا|اً)')),
    ('linguistic_meta_explanatory', re.compile(r'^\s*هذا\s+هو\s+الجملة\s+الخبرية')),
    ('linguistic_question_provision', re.compile(r'توفير\s+الأسئلة')),
    ('linguistic_unfounded_superlative', re.compile(r'(?:خدمة\s+ممتازة|روعة\s+استخدامه)')),
    ('linguistic_hallucinated_prior_context', re.compile(r'كما\s+ذكرت\s+سائرتنا\s+السابقة')),
    ('linguistic_unsupported_ai_control', re.compile(r'التحكم\s+في\s+الذكاء\s+الاصطناعي')),
)


def verified_surface_violation(text: str, language: str | None) -> str | None:
    """Reject only independently known *negative* patterns; None != fluent."""
    if not isinstance(text, str) or not isinstance(language, str):
        return None
    rules = _DE_BAD if language == 'de' else _AR_BAD if language == 'ar' else ()
    for reason, pattern in rules:
        if pattern.search(text):
            return reason
    if language == 'ar':
        # Repeating the same 4-word phrase twice in one short identity answer
        # was observed on LIVE. Conservative: ignore runs shorter than 4 words.
        words = re.findall(r'[\u0621-\u064a]+', text)
        windows = (' '.join(words[i:i+4]) for i in range(max(0, len(words)-3)))
        seen = set()
        for phrase in windows:
            if phrase in seen:
                return 'linguistic_repeated_phrase'
            seen.add(phrase)
    return None


@dataclass(frozen=True)
class LanguageQualityFinding:
    # "unverified" is not a success/qualification claim. Absence of a known
    # defect DOES NOT constitute a grammatical/naturalness assessment.
    status: str
    reason: str | None = None
    certified: bool = False


def assess_language_quality(text: str, language: str | None) -> LanguageQualityFinding:
    if not text or not isinstance(language, str) or language not in _SUPPORTED:
        return LanguageQualityFinding('not_assessed')
    defect = verified_surface_violation(text, language)
    if defect:
        return LanguageQualityFinding('known_defect', defect)
    return LanguageQualityFinding('unverified')


@dataclass(frozen=True)
class LanguageOutcome:
    completed: int = 0
    rejected: int = 0
    consecutive_rejected: int = 0
    # Technical acceptance is not proof of grammatical fluency.


class SessionLanguageEvidence:
    """Version-scoped, lock-guarded, user-mission evidence (not persistence)."""
    def __init__(self):
        self._lock=RLock()
        # technical acceptance, attributed rejection, consecutive rejections
        self._counts = defaultdict(lambda: [0,0,0])

    def observe(self, resource_id: str, language: str, version: str | None,
                *, accepted: bool, reason: str) -> bool:
        if not isinstance(resource_id,str) or not resource_id or language not in _SUPPORTED:
            return False
        if not isinstance(version, str) or len(version) < 16:
            return False
        if not isinstance(accepted,bool) or not isinstance(reason,str):
            return False
        # Neutral failures (cancel, timeout) must not poison grammar evidence.
        if not accepted and not (reason.startswith('linguistic_') or reason.startswith('maestro_identity_') or reason.startswith('maestro_role_')
                                 or reason.startswith('identity_slot_') or reason.startswith('explicit_shape_')):
            return False
        with self._lock:
            count=self._counts[(resource_id,language,version)]
            if accepted:
                count[0] += 1
                # A technically accepted mission interrupts the failure streak,
                # but never establishes fluency certification.
                count[2] = 0
            else:
                count[1] += 1
                count[2] += 1
        return True

    def evidence(self, resource_id: str, language: str, version: str | None) -> LanguageOutcome:
        with self._lock:
            ok,bad,streak=self._counts.get((resource_id,language,version), (0,0,0))
        return LanguageOutcome(ok,bad,streak)

    def should_trial_alternative(self, resource_id: str, language: str, version: str | None) -> bool:
        e=self.evidence(resource_id,language,version)
        # Only consecutive attributed failures trigger a candidate trial.
        # Historical TECHNICAL acceptance is unverified as language-quality
        # evidence: it must not cancel two genuinely rejected recent missions.
        # One observation is recorded per mission (never per slot/retry).
        return e.consecutive_rejected >= 2

    def scoped_snapshot(self, resource_id: str, version: str | None) -> dict:
        # Aggregate counters only, no prompts, responses or stored identities.
        if not isinstance(version, str) or len(version) < 16:
            return {'version_bound': False, 'languages': {}, 'fluency_certified': False}
        with self._lock:
            records = {
                lang: {'technical_accepted': counts[0],
                       'rejected': counts[1],
                       'consecutive_rejected': counts[2],
                       'trial_due': counts[2] >= 2}
                for (rid, lang, digest), counts in self._counts.items()
                if rid == resource_id and digest == version
            }
        return {'version_bound': True, 'languages': records,
                'fluency_certified': False}

    def snapshot(self) -> dict[str, int|bool]:
        with self._lock:
            return {'scoped_profiles':len(self._counts), 'fluency_certified':False}
