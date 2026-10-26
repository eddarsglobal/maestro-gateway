"""Provider-neutral, conservative multilingual mission contract.

No model execution, dictionaries of canned final answers or fluency certification.
Only verifies observable obligations; unsupported/unrecognized language abstains.
Language tags are separate from MAESTRO's eight *UI* localizations.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
import unicodedata

@dataclass(frozen=True, slots=True)
class MissionLanguageContract:
    language: str | None
    sentence_count: int | None
    identity_request: bool
    local_explanation: bool

# Explicit *instruction* evidence, not a guess from short stop-words.
# Extended languages are exploratory; using a tag never asserts model competence.
LANGUAGE_HINTS = (
    ('en', r'\b(?:english|in english)\b'),
    ('fr', r'\b(?:fran[cç]ais|fran[cç]aise|fran[cç]aises|french)\b'),
    ('de', r'\b(?:deutsch\w*|deutschen)\b'),
    ('ar', r'(?:\b(?:arabic)\b|عربي(?:ة|تين)?|العربي(?:ة|تين)?|بالعربية)'),
    ('pt', r'\bportugu[eê]s\b'),
    ('es', r'\bespa[nñ]ol\b'),
    ('hr', r'\bhrvatsk\w*\b'),
    ('it', r'\bitalian\w*\b'),
    ('zh', r'中文|汉语|漢語|\bchinese\b'),
    ('ja', r'日本語|\bjapanese\b'),
    ('hi', r'हिंदी|हिन्दी|\bhindi\b'),
    ('tr', r'türkçe\w*|türkçeyle|\bturkish\b'),
    ('sw', r'kiswahili|\bswahili\b'),
    ('ru', r'русск\w*|\brussian\b'),
)

# Sentence count + noun are deliberately coupled within each language.
TWO_COUNT = (
    r'\b(?:exactly\s+)?two\b.{0,100}\bsentences?\b',
    r'\b(?:exactement\s+)?deux\b.{0,100}\bphrases?\b',
    r'\b(?:genau\s+)?zwei\b.{0,120}\bs[aä]tze(?:n)?\b',
    r'\b(?:točno\s+)?dvije\b.{0,100}\bre[cč]enice\b',
    r'\b(?:esattamente\s+)?due\b.{0,90}\bfrasi\b',
    r'\b(?:exactamente\s+)?dos\b.{0,90}\bfrases\b',
    r'\b(?:exatamente\s+)?duas\b.{0,90}\bfrases\b',
    r'جملتين|جملتان|جملتان فقط|جملتين فقط',
    r'(?:恰好|正好|刚好|剛好)?\s*(?:两|兩|二|2)\s*(?:句|句话|句話)',
    r'ちょうど\s*(?:二つ|2つ|二|２)\s*(?:の)?\s*(?:文|つの文)|(?:二つ|2つ)\s*の\s*文|文をちょうど\s*(?:二つ|2つ)',
    r'ठीक\s*(?:दो|2)\s*वाक्य\w*',
    r'(?:tam\s+)?iki\s+cümle\w*',
    r'\bsentensi\s+mbili\b|\bmbili\s+sentensi\b',
    r'\b(?:двух|два|2)\s+предложени\w*',
)
ONE_COUNT = (
    r'\b(?:exactly\s+)?one\b.{0,90}\bsentence\b',
    r'\b(?:une\s+seule|une|1)\s+phrase\b',
    r'\b(?:genau\s+)?eine[mnr]?\b.{0,80}\bsatz\b',
    r'\b(?:jednoj|jednu|1)\b.{0,70}\bre[cč]enic\w*',
    r'\b(?:una\s+sola|una)\b.{0,70}\bfrase\b',
    r'\b(?:una\s+sola|una)\b.{0,70}\bfrase\b',
    r'\b(?:[úu]nica|uma\s+[úu]nica|uma)\b.{0,70}\bfrase\b',
    r'جملة\s+(?:عربية\s+فصيحة\s+)?واحدة|في\s+جملة\s+واحدة',
)

_INTENT = re.compile(
    r'(?:\b(?:introduce|present|presenta\w*|présente\w*|stell\w*|vorstell\w*|'
    r'predstavi\w*|apresenta\w*|describe|explain|erkläre|explique|'
    r'spiega|explica|objasni|write|give|answer|respond|'
    r'представь|напиши|объясни|andika|eleza|açıklay\w*)\b|'
    r'عرّف|عرف|اشرح|وضّح|وضح|(?:请|介绍|説明|書いて|बताएं|परिचय|लिखें))',
    re.I,
)
_IDENTITY = re.compile(
    r'(?:\b(?:introduce|presenta\w*|présente\w*|stell\w*|vorstell\w*|'
    r'predstavi\w*|apresenta\w*|представь)\b|'
    r'عرّف|عرف|介绍|自己紹介|紹介|説明|परिचय|anayeitwa|adlı)', re.I,
)
_LOCAL = re.compile(
    r'(?:\b(?:locally|local\w*|lokal\w*|localmente|lokalno|'
    r'fonctionner\s+localement|funzionare\s+localmente|'
    r'ordinateur|computer|rechner|računal\w*|ordenador|computador\w*)\b|'
    r'محلي|محلياً|محليًا|الحاسوب|本地|ローカル|स्थानीय|yerel|внутри|локальн\w*)', re.I,
)


def parse_mission_contract(prompt: str) -> MissionLanguageContract:
    value = str(prompt or '')
    if not value.strip() or '```' in value:
        return MissionLanguageContract(None, None, False, False)
    candidates = [(lang, pattern) for lang, pattern in LANGUAGE_HINTS
                  if re.search(pattern, value, re.I | re.S)]
    # With multiple named languages, do not silently choose one.
    language = candidates[0][0] if len(candidates) == 1 else None
    # Count detection requires an active instruction and an unambiguous count.
    one = any(re.search(p, value, re.I | re.S) for p in ONE_COUNT)
    two = any(re.search(p, value, re.I | re.S) for p in TWO_COUNT)
    count = 1 if one and not two else 2 if two and not one else None
    if count and not _INTENT.search(value):
        count = None
    identity = ('maestro' in value.casefold() and bool(_IDENTITY.search(value)))
    locality = bool(_LOCAL.search(value))
    return MissionLanguageContract(language, count, identity, locality)


def _script(ch: str) -> str:
    if not ch.isalpha(): return 'other'
    name=unicodedata.name(ch, '')
    for k in ('ARABIC','DEVANAGARI','CYRILLIC','HIRAGANA','KATAKANA','CJK','HANGUL','LATIN'):
        if k in name:return k
    return 'other'


def _alpha_scripts(answer: str) -> dict[str,int]:
    cleaned = re.sub(r'(?i)\bMAESTRO\b','', str(answer or ''))
    counts={}
    for c in cleaned:
        k=_script(c)
        if k!='other': counts[k]=counts.get(k,0)+1
    return counts

# Only high-confidence mismatches may lead to rejection. These checks
# cannot infer grammar, regional dialect or semantic correctness.
_ENGLISH_MARKERS = re.compile(r'\b(?:the|with|that|this|on|and|running|local|computer|work|without|internet|access|i|you|means|can|help)\b', re.I)
_NONANSWER = re.compile(r'^\s*(?:أعتذر|اعتذر|عذرا|لا\s+أستطيع|sorry[, ]|i\s+(?:am\s+)?sorry|'
                        r'cannot\s+|i\s+cannot\b|lo\s+siento|desculpe|mi\s+dispiace)',re.I)


def obvious_contract_failure(prompt: str, answer: str) -> str | None:
    contract = parse_mission_contract(prompt)
    output = str(answer or '').strip()
    if not output:
        return 'contract_empty_output'
    if contract.identity_request and not re.search(r'(?i)\bMAESTRO\b', output):
        return 'contract_identity_name_missing'
    if (contract.identity_request or (contract.local_explanation and contract.sentence_count)) and _NONANSWER.search(output):
        return 'contract_safe_definition_refusal'
    scripts = _alpha_scripts(output)
    total = sum(scripts.values())
    lang = contract.language
    if total >= 20 and lang in {'ar','hi','ru','zh','ja'}:
        acceptable = {
            'ar': {'ARABIC'}, 'hi': {'DEVANAGARI'}, 'ru': {'CYRILLIC'},
            'zh': {'CJK'}, 'ja': {'HIRAGANA','KATAKANA','CJK'},
        }[lang]
        share=sum(scripts.get(s,0) for s in acceptable)/total
        if share < .42:
            return 'contract_output_language_script_mismatch'
    # Latin-script languages cannot be confirmed by Unicode alone.
    # Require unmistakable English dominance before rejecting non-English.
    if lang in {'hr','sw','it','es','pt','de','fr','tr'} and total >= 25:
        tokens=re.findall(r"[a-zA-Z]+", re.sub(r'(?i)\bMAESTRO\b','',output))
        hits=sum(bool(_ENGLISH_MARKERS.fullmatch(t)) for t in tokens)
        if len(tokens) >= 5 and hits >= 4 and hits / len(tokens) >= .27:
            return 'contract_obvious_english_substitution'
        # Very short English-only formula such as the Croatian LIVE case.
        if len(tokens) >= 6 and hits >= 3 and hits / len(tokens) >= .38:
            return 'contract_obvious_english_substitution'
    return None
