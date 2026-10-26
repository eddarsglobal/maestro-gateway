from __future__ import annotations
from .language_quality_governor import verified_surface_violation

import re

from .response_constraints import explicit_two_sentences

_DE_QUALITY = re.compile(r"(?:nat[üu]rlich|grammatikalisch(?:\s+korrekt)?)", re.I)
_AR_QUALITY = re.compile(r"(?:الفصحى\s+الطبيعية|طبيعي(?:ة|اً|ا)?)")
_DE_IDENTITY = re.compile(r"(?:was\s+ist\s+maestro|wozu\s+dient\s+(?:es|maestro)|stell\w*\s+dich\s+als\s+maestro.{0,140}vor|stell\w*\s+dich\s+als\s+maestro)", re.I)
_AR_IDENTITY = re.compile(r"(?:ما\s+هو\s+MAESTRO|ما\s+وظيفته|وظيفة\s+MAESTRO|عرّف\s+بنفسك\s+بصفتك\s+MAESTRO|عرّف\s+بنفسك\s+.*MAESTRO)", re.I)


def normalized_language(language: str | None) -> str:
    value = str(language or '').strip().casefold().split('-', 1)[0].split('_', 1)[0]
    return {'german': 'de', 'arabic': 'ar'}.get(value, value)


def _sentence_parts(text: str) -> list[str]:
    raw = ' '.join(str(text or '').split())
    if not raw:
        return []
    return [part.strip() for part in re.split(r'(?<=[.!?؟。！？])\s+', raw) if part.strip()]


def _sanitize_arabic_revision_draft(text: str) -> str:
    """Remove mixed-script alphabetic runs from an internal Arabic draft.

    This is input hygiene for the revision model, never a delivered-answer repair.
    Clean Arabic text and the exact MAESTRO brand are preserved verbatim.
    """
    import unicodedata
    brand = '§§§'
    raw = re.sub(r'MAESTRO', brand, str(text or ''), flags=re.I)
    runs = re.findall(r'[^\W\d_]+', raw, flags=re.UNICODE)
    for run in sorted(set(runs), key=len, reverse=True):
        if run == brand:
            continue
        letters = [ch for ch in run if ch.isalpha()]
        if letters and not all('ARABIC' in unicodedata.name(ch, '') for ch in letters):
            raw = raw.replace(run, ' ')
    raw = raw.replace(brand, 'MAESTRO')
    return ' '.join(raw.split())

def foreign_alpha_tokens_except_maestro(text: str) -> tuple[str, ...]:
    """Return distinct non-Arabic alphabetic runs except the MAESTRO brand.

    Diagnostic only: this helper never mutates or accepts a response. Final
    delivery still goes through the full response guard.
    """
    import unicodedata
    clean = re.sub(r"MAESTRO", " ", str(text or ''), flags=re.I)
    runs = re.findall(r"[^\W\d_]+", clean, flags=re.UNICODE)
    out: list[str] = []
    for run in runs:
        letters = [ch for ch in run if ch.isalpha()]
        if letters and all("ARABIC" in unicodedata.name(ch, "") for ch in letters):
            continue
        if run not in out:
            out.append(run)
    return tuple(out[:8])


def linguistic_revision_requested(prompt: str, language: str | None) -> bool:
    """Request one same-resource native-language polish pass for explicit quality contracts.

    This is intentionally narrow: only explicit two-sentence DE/AR requests that ask
    for natural/correct language.  It does not choose a model, add permissions, or
    alter unrelated conversation turns.
    """
    lang = normalized_language(language)
    text = str(prompt or '')
    if not explicit_two_sentences(text):
        return False
    if lang == 'de':
        return bool(_DE_QUALITY.search(text))
    if lang == 'ar':
        return bool(_AR_QUALITY.search(text))
    return False


def maestro_identity_grounding_required(prompt: str, language: str | None) -> bool:
    lang = normalized_language(language)
    text = str(prompt or '')
    if 'maestro' not in text.casefold():
        return False
    if lang == 'de':
        return bool(_DE_IDENTITY.search(text))
    if lang == 'ar':
        return bool(_AR_IDENTITY.search(text))
    return False


# FIX16-FIXED2 — bounded multilingual concept verification.
# These checks are conservative lexical evidence, NOT a claim to general semantic
# understanding or a license to grant extra MAESTRO capabilities.
_ARABIC_MARKS = re.compile(r"[\u064b-\u065f\u0670\u0640]")
_DE_AI = re.compile(r"\b(?:ki[- ]?(?:assistent\w*|assistenz(?:system\w*)?|hilfe)|"
                    r"künstlich\w*\s+intelligenz|ai[- ]?assistent\w*)\b", re.I)
_DE_LOCAL = re.compile(r"\blokal\w*\b|\blocal\b|\bauf\s+deinem\s+rechner\b", re.I)
_DE_MACHINE = re.compile(r"\b(?:computer|rechner|mac|pc|gerät)\b", re.I)
_DE_ROLE = re.compile(
    r"\b(?:hilf\w*|helf\w*|unterstütz\w*|assist\w*|beantwort\w*|"
    r"ordn\w*|verarbeit\w*|organisier\w*|erklär\w*|such\w*|find\w*|"
    r"fragen?\s+stell\w*|aufgaben?\s+(?:bearbeit\w*|erledig\w*|lös\w*))\b", re.I)
_DE_DIRECT = re.compile(r"\b(?:du|dir|dich|dein\w*)\b", re.I)
_AR_ROLE = ('يساعد', 'مساعدة', 'يساعدك', 'يدعم', 'تنظيم', 'ينظم',
            'الاجابة', 'يجيب', 'تنفيذ', 'الاسئلة', 'مهام', 'معلومات', 'يقدم')
_AR_FALSE_KINDS = re.compile(r"شخص|انسان|شركة|موسيقى|تطبيق\s+هاتف")
_DE_FALSE_KINDS = re.compile(r"\b(?:person|mensch|firma|unternehmen|musik\w*|telefon-?app)\b", re.I)


def _arabic_fold(value: str) -> str:
    value = _ARABIC_MARKS.sub('', str(value or ''))
    value = re.sub('[أإآٱ]', 'ا', value)
    return ' '.join(value.split())


def _identity_features(sentence: str, lang: str) -> bool:
    if lang == 'de':
        low = sentence.casefold()
        return bool(_DE_LOCAL.search(low) and _DE_AI.search(low) and _DE_MACHINE.search(low))
    if lang == 'ar':
        compact = _arabic_fold(sentence)
        # Both ذكاء اصطناعي and بالذكاء الاصطناعي are ordinary Arabic usage.
        ai = bool(re.search(r'(?:ال)?ذكاء\s+(?:ال)?اصطناع\w*', compact))
        assistant = bool(re.search(r'\b(?:ال)?مساعد\w*\b', compact))
        local = bool(re.search(r'محلي\w*', compact))
        computer = any(word in compact for word in ('حاسوب','كمبيوتر','الحاسب'))
        return bool(ai and assistant and local and computer)
    return True


def _role_features(sentence: str, lang: str) -> bool:
    if lang == 'de':
        low = sentence.casefold()
        # Positive role must be related to the addressee; a pronoun alone is
        # insufficient, and infinitives such as helfen/bearbeiten are allowed.
        return bool(_DE_ROLE.search(low) and _DE_DIRECT.search(low))
    if lang == 'ar':
        compact = _arabic_fold(sentence)
        return any(token in compact for token in _AR_ROLE)
    return True


def maestro_identity_contradicted(output: str, language: str | None) -> bool:
    """Reject positive false identities while preserving explicit negation.

    We inspect the local clause around a false identity, not merely co-occurrence
    with the MAESTRO name. This avoids classifying « not a person » as « person ».
    """
    lang = normalized_language(language)
    text = ' '.join(str(output or '').split())
    if 'maestro' not in text.casefold():
        return False
    if lang == 'de':
        low = text.casefold()
        if re.search(r'\b(?:personen?|menschen?)\s+(?:wie\s+)?maestro\b', low):
            return True  # Real historical model refusal: « Personen wie MAESTRO ».
        for name in re.finditer(r'\bmaestro\b', low):
            neighborhood = low[name.end():name.end()+85]
            for kind in _DE_FALSE_KINDS.finditer(neighborhood):
                prefix = neighborhood[max(0,kind.start()-27):kind.start()]
                # Negation must attach directly to the false noun; a negation
                # elsewhere in the sentence cannot mask « sondern eine Person ».
                if re.search(r'\b(?:kein\w*|nicht)\s+(?:(?:ein\w*|der|die|das)\s+)?$', prefix):
                    continue
                return True
        return False
    if lang == 'ar':
        compact = _arabic_fold(text)
        for name in re.finditer('maestro', compact, re.I):
            after = compact[name.end():name.end()+85]
            before = compact[max(0,name.start()-55):name.start()]
            for kind in _AR_FALSE_KINDS.finditer(after):
                prefix = after[max(0,kind.start()-33):kind.start()]
                if re.search(r'(?:ليس|ليست|غير|لا\s+يعد|لا\s+يعتبر)\s+(?:[\u0621-\u064a]+\s+){0,2}$', prefix):
                    continue
                return True
            # « شخص يدعى MAESTRO » / « شركة MAESTRO » is a positive identity.
            if _AR_FALSE_KINDS.search(before) and not re.search(r'(?:ليس|ليست|غير)\s*$', before):
                return True
        return False
    return False


# FIX16-FIXED4: refusing to state an identity is not an identity statement.
# Do NOT treat the mere co-occurrence of identity fact keywords as grounding.
# This is a conservative, language-specific anti-evasion screen rather than a
# general purpose semantic classifier. A truthful negative such as
# "MAESTRO ist kein Mensch" or "MAESTRO ليس شخصًا" is not a refusal.
_DE_REFUSAL = re.compile(
    r"\b(?:ich\s+(?:kann|darf|werde|möchte)\s+(?:dir\s+|ihnen\s+|dabei\s+)?(?:nicht(?!\s+nur\b)|keine?)(?:\s+\w+){0,7}\s+(?:helfen|beantworten|formulieren|geben|unterstützen|sagen|erstellen)|"
    r"ich\s+bin\s+nicht\s+in\s+der\s+lage|"
    r"(?:es\s+tut\s+mir\s+leid|tut\s+mir\s+leid|ich\s+entschuldige\s+mich)|"
    r"ich\s+kann\s+nicht\s+dabei\s+helfen|"
    r"ich\s+kann\s+keine?\s+(?:auskunft|antwort|informationen))\b", re.I
)
_AR_REFUSAL = re.compile(
    r"(?:لا\s*(?:استطيع|يمكنني|اقدر|اسمح)|"
    r"(?:ليس|غير)\s+(?:بامكاني|باستطاعتي)|"
    r"(?:اعتذر|عذرا|اسف)\s*(?:عن|،|:|\.|$)|"
    r"لا\s+يسمح\s+لي|"
    r"لن\s+(?:اجيب|اساعد))"
)

def identity_answer_evasion(output: str, language: str | None) -> bool:
    """Detect an answer-level refusal or meta-evasion, never a negative fact."""
    lang = normalized_language(language)
    content = str(output or '').strip()
    if lang == 'de':
        return bool(_DE_REFUSAL.search(content))
    if lang == 'ar':
        return bool(_AR_REFUSAL.search(_arabic_fold(content)))
    return False


def maestro_identity_grounded(output: str, language: str | None) -> bool:
    """Two separately generated sentences: first identity, second useful role."""
    lang = normalized_language(language)
    text = str(output or '')
    if maestro_identity_contradicted(text, lang) or identity_answer_evasion(text, lang):
        return False
    parts = _sentence_parts(text)
    if len(parts) != 2 or 'maestro' not in parts[0].casefold():
        return False
    return _identity_features(parts[0], lang) and _role_features(parts[1], lang)


_REPAIRABLE_DRAFT_REASONS = {
    "direct_answer_meta_preamble",
    "direct_answer_question_restatement",
    "user_instruction_echo",
    "explicit_shape_arabic_only_foreign_script",
    "explicit_shape_german_only_foreign_script",
    "maestro_identity_not_grounded",
    "maestro_identity_contradicted",
}


def repairable_linguistic_draft(reason: str | None, draft: str) -> bool:
    """Allow a non-publishable DE/AR draft to enter the native revision lane.

    This does NOT accept the answer for delivery.  It only distinguishes narrow
    language/format defects that the dedicated revision pass is allowed to repair
    from semantic, capability, safety, transport, or completion failures.
    """
    if not str(draft or '').strip():
        return False
    value = str(reason or '')
    if value in _REPAIRABLE_DRAFT_REASONS:
        return True
    return value.startswith('explicit_shape_expected_two_sentences_got_')


def native_revision_instruction(*, prompt: str, draft: str, language: str | None) -> str:
    """Build a compact same-model native revision request for an internal draft.

    Explicit identity questions use two semantic slots rather than a canned
    answer: sentence 1 identifies MAESTRO, sentence 2 explains its role.
    """
    lang = normalized_language(language)
    identity_required = maestro_identity_grounding_required(prompt, lang)
    if lang == 'ar':
        clean_draft = _sanitize_arabic_revision_draft(draft)
        identity = (maestro_identity_fact_capsule(lang) + ' ' if identity_required else '')
        slot = (
            'الجملة الأولى يجب أن تذكر MAESTRO وتعرّفه كمساعد ذكاء اصطناعي محلي على هذا الحاسوب. '
            'الجملة الثانية فقط تشرح للمستخدم وظيفته ضمن القدرات والصلاحيات المتاحة. لا تكتب جملة ثالثة. '
            if identity_required else ''
        )
        return (
            'مراجعة لغوية داخلية: حوّل المسودة إلى جواب عربي فصيح طبيعي من جملتين خبريتين فقط. '
            + slot +
            'حافظ على المعنى والحقائق ولا تضف قدرات أو صلاحيات. '
            'لا تستخدم أي أحرف غير عربية باستثناء اسم MAESTRO فقط، وعبّر بالعربية عن أي كلمة أجنبية أخرى. '
            + identity +
            'أعد الجواب فقط بلا عنوان أو مقدمة أو سؤال أو تعليمات.\n'
            'المسودة:\n' + clean_draft
        )
    if lang == 'de':
        identity = (maestro_identity_fact_capsule(lang) + ' ' if identity_required else '')
        slot = (
            'Satz 1 muss MAESTRO selbst als lokalen KI-Assistenten auf diesem Computer einordnen. '
            'Satz 2 erklärt in du-Form seine Aufgabe innerhalb verfügbarer und autorisierter Fähigkeiten. '
            'Behandle MAESTRO nicht als Person und lehne die Identitätsfrage nicht als Anfrage nach persönlichen Daten ab. '
            if identity_required else ''
        )
        return (
            'Interne sprachliche Überarbeitung: Überarbeite den folgenden Entwurf in natürlichem, grammatikalisch korrektem Deutsch. '
            'Schreibe genau zwei vollständige Aussagesätze, bewahre Bedeutung und Fakten und füge nichts Neues hinzu. '
            + slot + identity +
            'Verwende durchgehend „du“. Beginne direkt mit der Antwort; keine Überschrift, Einleitung, Frage, '
            'Anweisung oder Bemerkung über die Überarbeitung.\n'
            'Entwurf:\n' + str(draft or '').strip()
        )
    return str(draft or '')


def native_revision_retry_instruction(*, prompt: str, draft: str, language: str | None, reason: str) -> str:
    """One final progressive polish on the same resource.

    For fact-grounded identity, the most recent revision candidate is preferred
    by mission.py and the governed facts are reasserted without scripting prose.
    """
    lang = normalized_language(language)
    identity_required = maestro_identity_grounding_required(prompt, lang)
    if lang == 'ar':
        clean_draft = _sanitize_arabic_revision_draft(draft)
        identity = (maestro_identity_fact_capsule(lang) + ' ' if identity_required else '')
        slot = (
            'اكتب جملتين فقط لا ثلاثاً: الأولى تذكر MAESTRO وتعرّفه كمساعد ذكاء اصطناعي محلي على هذا الحاسوب، '
            'والثانية تشرح وظيفته للمستخدم ضمن القدرات والصلاحيات المتاحة. '
            if identity_required else ''
        )
        return (
            'محاولة مراجعة لغوية أخيرة داخلية: صحح النص التالي فقط. '
            'اكتب جملتين خبريتين بالعربية الفصحى الطبيعية. '
            + slot +
            'لا تستخدم أي أحرف غير عربية باستثناء اسم MAESTRO فقط. '
            'حوّل كل كلمة أجنبية أخرى إلى تعبير عربي، ولا تضف معلومات جديدة. '
            + identity +
            'أعد الجواب فقط بلا مقدمة أو سؤال أو تعليق.\n'
            'النص:\n' + clean_draft
        )
    if lang == 'de':
        identity = (maestro_identity_fact_capsule(lang) + ' ' if identity_required else '')
        slot = (
            'Satz 1 identifiziert MAESTRO selbst als lokalen KI-Assistenten auf diesem Computer; '
            'Satz 2 erklärt in du-Form seine Aufgabe. Keine Personen- oder Datenschutzablehnung. '
            if identity_required else ''
        )
        return (
            'Letzter interner Sprachkorrekturversuch: Überarbeite denselben Entwurf in natürlichem, grammatikalisch korrektem Deutsch. '
            'Schreibe genau zwei Aussagesätze, bewahre Bedeutung und Fakten und füge keine Fähigkeiten hinzu. '
            + slot + identity +
            'Verwende „du“ und antworte direkt, ohne Überschrift, Einleitung, Frage, Anweisung oder Hinweis auf die Überarbeitung.\n'
            'Entwurf:\n' + str(draft or '').strip()
        )
    return str(draft or '')



def maestro_identity_fact_capsule(language: str | None) -> str:
    """Return governed identity FACTS, never a user-facing answer.

    The facts are stable sovereign state. Wording remains the responsibility of
    the selected local intelligence resource and is validated after generation.
    """
    lang = normalized_language(language)
    if lang == 'de':
        return (
            'Verbindliche Fakten: Name MAESTRO; Identität lokaler KI-Assistent auf diesem Computer; '
            'Aufgaben nur innerhalb verfügbarer und ausdrücklich autorisierter Fähigkeiten.'
        )
    if lang == 'ar':
        return (
            'حقائق ملزمة: الاسم MAESTRO؛ الهوية مساعد ذكاء اصطناعي محلي على هذا الحاسوب؛ '
            'المهام ضمن القدرات والصلاحيات المتاحة فقط.'
        )
    return (
        'GOVERNED FACTS: name=MAESTRO; type=local_ai_assistant; location=this_computer; '
        'authority=available_and_explicitly_authorized_capabilities_only.'
    )


def fact_grounded_identity_instruction(prompt: str, language: str | None) -> str | None:
    """Build a model instruction from sovereign facts, never a canned answer.

    The response remains generative, but the two requested sentences have
    explicit semantic jobs so a small local model cannot satisfy the shape with
    a refusal/disclaimer plus an unrelated identity fragment.
    """
    lang = normalized_language(language)
    if not linguistic_revision_requested(prompt, lang):
        return None
    if not maestro_identity_grounding_required(prompt, lang):
        return None
    facts = maestro_identity_fact_capsule(lang)
    if lang == 'de':
        return (
            'Formuliere selbstständig eine direkte, natürliche und grammatikalisch korrekte Antwort auf die aktuelle Frage. '
            'Schreibe genau zwei vollständige Aussagesätze auf Deutsch. '
            'Satz 1 nennt MAESTRO ausdrücklich und beschreibt MAESTRO als lokalen KI-Assistenten auf diesem Computer. '
            'Satz 2 erklärt in der du-Form, wozu MAESTRO innerhalb seiner verfügbaren und ausdrücklich autorisierten Fähigkeiten dient. '
            'MAESTRO ist keine Person; lehne die Frage nicht als Anfrage nach persönlichen Informationen ab. '
            'kopiere keinen vorgegebenen Mustersatz und erfinde keine Fähigkeiten. ' + facts +
            '\nAntworte nur mit deiner frei formulierten Antwort.'
        )
    if lang == 'ar':
        return (
            'صغ بنفسك جواباً مباشراً وطبيعياً بالعربية الفصحى من جملتين خبريتين فقط. '
            'الجملة الأولى تذكر MAESTRO صراحة وتعرّفه كمساعد ذكاء اصطناعي محلي على هذا الحاسوب. '
            'الجملة الثانية تشرح للمستخدم وظيفة MAESTRO ضمن القدرات والصلاحيات المتاحة. '
            'لا تكتب جملة ثالثة، ولا تخترع قدرات أو صلاحيات، ولا تنسخ صياغة جاهزة. ' + facts +
            ' لا تستخدم أحرفاً غير عربية باستثناء اسم MAESTRO. '
            'أعد جوابك الذي صغته بنفسك فقط بلا عنوان أو مقدمة أو سؤال.'
        )
    return None


# FIX16 — structured generative identity composition.
# Identity facts remain sovereign, but user-facing prose is always model-generated.

def structured_identity_slot_count(prompt: str, language: str | None) -> int:
    """Return the number of independently generated identity sentences.

    Explicit two-sentence contracts are decomposed into identity + role. Other
    DE/AR identity questions use one identity sentence. No user-facing wording
    is stored here.
    """
    lang = normalized_language(language)
    if lang not in {'de', 'ar'} or not maestro_identity_grounding_required(prompt, lang):
        return 0
    return 2 if explicit_two_sentences(str(prompt or '')) else 1


def structured_identity_slot_instruction(*, prompt: str, language: str | None, slot: int) -> str:
    """Small-model-compatible fact-grounded generation, never canned prose.

    FIX16-FIXED5: No mention of forbidden personas or refusals in the ordinary
    generation prompt. Past negative instructions primed an observed German
    refusal loop. The post-generation guards still reject every false identity.
    """
    lang = normalized_language(language)
    if slot not in {1, 2}:
        raise ValueError('identity slot must be 1 or 2')
    if lang == 'de':
        if slot == 1:
            return (
                'Schreibe genau einen deutschen Aussagesatz über MAESTRO. '
                'Verbindliche Fakten: Name=MAESTRO; Art=KI-Assistent; '
                'Betrieb=lokal; Ort=Computer des Nutzers. '
                'Beschreibe mit eigenen Worten alle vier Fakten in einem Satz. '
                'Gib nur diesen einen Satz aus.'
            )
        return (
            'Schreibe genau einen deutschen Aussagesatz direkt an den Nutzer in du-Form mit du/dir. '
            'Beschreibe, wie der lokale Assistent MAESTRO bei Fragen, Informationen '
            'oder Aufgaben im Rahmen vorhandener, autorisierter Fähigkeiten hilft. '
            'Gib nur diesen einen Satz aus.'
        )
    if lang == 'ar':
        if slot == 1:
            return (
                'اكتب جملة خبرية عربية فصحى واحدة تعرف MAESTRO. '
                'حقائق ملزمة: الاسم=MAESTRO؛ النوع=مساعد ذكاء اصطناعي؛ '
                'التشغيل=محلي؛ المكان=حاسوب المستخدم. '
                'اجمع الحقائق الأربع في جملة طبيعية من صياغتك. '
                'أخرج الجملة وحدها بالعربية باستثناء اسم MAESTRO.'
            )
        return (
            'اكتب جملة خبرية عربية فصحى واحدة توضح وظيفة MAESTRO وفائدته للمستخدم. '
            'المجال: المساعدة في الأسئلة والمعلومات والمهام ضمن القدرات والصلاحيات المتاحة. '
            'اكتب الجملة الطبيعية وحدها بالعربية باستثناء اسم MAESTRO.'
        )
    raise ValueError('structured identity generation supports only de/ar')


def structured_identity_slot_retry_instruction(*, prompt: str, language: str | None,
                                               slot: int, reason: str, attempt: int = 1) -> str:
    """A bounded, reason-specific fresh request, without quoting a failed draft.

    The model remains the selected sovereign local resource. Retried content is
    not promoted into instructions. No model names, user-facing canonical answers,
    or changes to the existing bounded retry policy are introduced.
    """
    base = structured_identity_slot_instruction(prompt=prompt, language=language, slot=slot)
    lang = normalized_language(language)
    if reason.startswith('linguistic_'):
        return base + (' اكتب الجملة بصياغة عربية فصحى طبيعية مباشرة.' if lang == 'ar' else ' Formuliere den Satz natürlich und sachlich.')
    if lang == 'ar':
        details = {
            'maestro_identity_not_grounded': (
                'استعمل كل حقائق الهوية: اسم MAESTRO ونوع مساعد ذكاء اصطناعي '
                'وتشغيل محلي على حاسوب المستخدم.'),
            'maestro_role_not_grounded': 'اذكر بوضوح كيف يساعد المستخدم في مهمة مسموحة.',
            'identity_slot_not_one_sentence': 'صياغة واحدة مكتملة في جملة واحدة.',
            'identity_slot_meta_preamble': 'اكتب مضمون الجملة مباشرةً.',
            'explicit_shape_arabic_only_foreign_script': 'اكتب بالعربية فقط مع اسم MAESTRO.',
            'maestro_identity_contradicted': 'استخدم الحقائق الملزمة فقط.',
            'maestro_identity_refusal': 'أجب عن السؤال التعريفي بمعلومة مباشرة عن MAESTRO.'
        }.get(reason, 'التزم بالحقائق واللغة المطلوبة.')
        return base + ' ' + details + (' اكتب الجملة مباشرةً.' if attempt == 1 else ' راجع الحقائق الأربع قبل إخراج الجملة.')
    details = {
        'maestro_identity_not_grounded': (
            'Nenne alle Identitätsfakten: MAESTRO, KI-Assistent, lokal, Computer oder Rechner.'),
        'maestro_role_not_grounded': 'Erkläre eine erlaubte Hilfe direkt mit du oder dir.',
        'identity_slot_not_one_sentence': 'Formuliere einen einzigen vollständigen Aussagesatz.',
        'identity_slot_meta_preamble': 'Beginne unmittelbar mit dem Inhalt des Satzes.',
        'explicit_shape_german_only_english_fallback': 'Schreibe ausschließlich auf Deutsch.',
        'maestro_identity_contradicted': 'Nutze ausschließlich die verbindlichen Fakten.',
        'maestro_identity_refusal': 'Gib eine direkte sachliche Beschreibung von MAESTRO anhand der Fakten.'
    }.get(reason, 'Kläre die Identitätsfakten in einem deutschen Satz.')
    return base + ' ' + details + (' Nur ein Satz.' if attempt == 1 else ' Prüfe alle vier Identitätsfelder vor der Ausgabe.')


def identity_slot_meta_preamble(text: str, language: str | None) -> bool:
    """Reject instructions about the output instead of the requested content."""
    lang = normalized_language(language)
    value = str(text or '').strip().lstrip('\"«“')
    if lang == 'ar':
        return bool(re.match(r'^(?:الجملة\s+الجديدة|الجواب|الإجابة|الاجابة|النص\s+الجديد)\s*[:：]', _arabic_fold(value)))
    if lang == 'de':
        return bool(re.match(r'^(?:neuer\s+satz|antwort|mein\s+satz|ausgabe)\s*[:：]', value, re.I))
    return False


def _single_sentence_candidate(text: str) -> str | None:
    raw = ' '.join(str(text or '').split()).strip()
    if not raw:
        return None
    parts = _sentence_parts(raw)
    if len(parts) != 1:
        return None
    # Guard against two glued sentences that lack whitespace after punctuation.
    if len(re.findall(r'[.!?؟。！？]', raw)) > 1:
        return None
    if raw[-1:] not in '.!?؟。！？':
        raw += '.'
    return raw


def _english_fallback(text: str) -> bool:
    """Reject conspicuously English prose in an explicitly German slot.

    This is intentionally only a high-confidence screen. It does not certify
    German grammar or attempt to classify short mixed technical phrases.
    """
    words = re.findall(r"[a-zA-Z]+", re.sub('MAESTRO', '', text, flags=re.I).casefold())
    en = {'the','this','that','your','you','it','with','and','can','helps','assistant',
          'computer','providing','various','such','tasks','answering','information'}
    de = {'ist','ein','eine','der','die','das','du','dir','dich','dein','deinem',
          'und','auf','mit','hilft','kannst','fragen','aufgaben','lokaler','lokales'}
    return len(set(words) & en) >= 3 and not (set(words) & de)


def validate_structured_identity_slot(text: str, language: str | None, slot: int) -> tuple[bool, str, str]:
    """Validate a generated slot without changing its semantic content."""
    lang = normalized_language(language)
    sentence = _single_sentence_candidate(text)
    if sentence is None:
        return False, 'identity_slot_not_one_sentence', ''
    if identity_slot_meta_preamble(sentence, lang):
        return False, 'identity_slot_meta_preamble', ''
    surface_issue = verified_surface_violation(sentence, lang)
    if surface_issue is not None:
        return False, surface_issue, ''
    if maestro_identity_contradicted(sentence, lang):
        return False, 'maestro_identity_contradicted', ''
    if identity_answer_evasion(sentence, lang):
        return False, 'maestro_identity_refusal', ''
    if lang == 'ar' and foreign_alpha_tokens_except_maestro(sentence):
        return False, 'explicit_shape_arabic_only_foreign_script', ''
    if lang == 'de' and _english_fallback(sentence):
        return False, 'explicit_shape_german_only_english_fallback', ''
    if slot == 1:
        if 'maestro' not in sentence.casefold() or not _identity_features(sentence, lang):
            return False, 'maestro_identity_not_grounded', ''
        return True, 'accepted', sentence
    if slot == 2:
        if not _role_features(sentence, lang):
            return False, 'maestro_role_not_grounded', ''
        return True, 'accepted', sentence
    return False, 'identity_slot_invalid', ''


def compose_structured_identity_slots(slots: list[str]) -> str:
    """Mechanical composition only; semantic content comes from model calls."""
    return ' '.join(' '.join(str(item or '').split()).strip() for item in slots if str(item or '').strip())
