"""Deterministic resolution for bounded questions about the immediately prior language.

No model choice, network access, or semantic fluency claim lives here.  The
resolver only uses the already persisted local conversation transcript plus an
explicit-language detector supplied by the caller.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

_LANG_CODE = {
    "English": "en", "French": "fr", "German": "de", "Arabic": "ar",
    "Portuguese": "pt", "Spanish": "es", "Croatian": "hr", "Italian": "it",
}

_NAMES = {
    "en": {"en":"English","fr":"French","de":"German","ar":"Arabic","pt":"Portuguese","es":"Spanish","hr":"Croatian","it":"Italian"},
    "fr": {"en":"anglais","fr":"français","de":"allemand","ar":"arabe","pt":"portugais","es":"espagnol","hr":"croate","it":"italien"},
    "de": {"en":"Englisch","fr":"Französisch","de":"Deutsch","ar":"Arabisch","pt":"Portugiesisch","es":"Spanisch","hr":"Kroatisch","it":"Italienisch"},
    "ar": {"en":"الإنجليزية","fr":"الفرنسية","de":"الألمانية","ar":"العربية","pt":"البرتغالية","es":"الإسبانية","hr":"الكرواتية","it":"الإيطالية"},
    "pt": {"en":"inglês","fr":"francês","de":"alemão","ar":"árabe","pt":"português","es":"espanhol","hr":"croata","it":"italiano"},
    "es": {"en":"inglés","fr":"francés","de":"alemán","ar":"árabe","pt":"portugués","es":"español","hr":"croata","it":"italiano"},
    "hr": {"en":"engleski","fr":"francuski","de":"njemački","ar":"arapski","pt":"portugalski","es":"španjolski","hr":"hrvatski","it":"talijanski"},
    "it": {"en":"inglese","fr":"francese","de":"tedesco","ar":"arabo","pt":"portoghese","es":"spagnolo","hr":"croato","it":"italiano"},
}


_PREVIOUS_LANGUAGE_QUESTION = re.compile(
    r"(?:what\s+language.{0,100}(?:previous|before|immediately)|"
    r"quelle\s+langue.{0,100}(?:pr[eé]c[eé]dent|avant|juste\s+avant)|"
    r"welche\s+sprache.{0,120}(?:vorher|zuvor|vor\s+dieser\s+nachricht|unmittelbar)|"
    r"koji.{0,40}jezik.{0,100}(?:prije|prethod)|"
    r"quale\s+lingua.{0,100}(?:prima|precedent)|"
    r"qué\s+idioma.{0,100}(?:antes|anterior)|"
    r"que\s+idioma.{0,100}(?:antes|anterior)|"
    r"ما\s+اللغة.{0,120}(?:قبل|السابقة|السابق))",
    re.I | re.S,
)

def is_previous_language_question(prompt: str) -> bool:
    return bool(_PREVIOUS_LANGUAGE_QUESTION.search(str(prompt or "")))

_USER_REQUEST_PERSPECTIVE = re.compile(
    r"(?:what\s+language\s+did\s+i\s+(?:ask|request)|"
    r"quelle\s+langue\s+ai[- ]?je\s+demand|"
    r"welche\s+sprache\s+habe\s+ich.{0,40}(?:verlangt|gebeten|angefordert)|"
    r"koji\s+sam\s+jezik.{0,40}(?:tražio|tražio|zatražio)|"
    r"quale\s+lingua\s+ho\s+chiesto|qué\s+idioma\s+(?:pedí|te\s+pedí)|"
    r"que\s+idioma\s+(?:pedi|te\s+pedi)|"
    r"ما\s+اللغة\s+التي\s+طلبت|اللغة\s+التي\s+طلبت)",
    re.I | re.S,
)


def _last(messages: Iterable[dict], role: str) -> str | None:
    for item in reversed(list(messages)):
        if str(item.get("role") or "").lower() == role:
            value = str(item.get("content") or "").strip()
            if value:
                return value
    return None


def _infer_written_language(text: str) -> str | None:
    value = str(text or "")
    letters = [c for c in value if c.isalpha()]
    if not letters:
        return None
    if sum('\u0600' <= c <= '\u06ff' for c in letters) / len(letters) >= .45:
        return "ar"
    low = value.casefold()
    lex = (
        ("de", (" ich ", " du ", " deutsch", " ist ", " und ", " sprache")),
        ("fr", (" je ", " tu ", " vous ", " français", " est ", " et ")),
        ("hr", (" hrvats", " je ", " i ", " sam ", " si ")),
        ("it", (" italiano", " sono ", " è ", " che ", " e ")),
        ("es", (" español", " soy ", " es ", " que ", " y ")),
        ("pt", (" português", " sou ", " é ", " que ", " e ")),
        ("en", (" english", " i ", " you ", " is ", " and ")),
    )
    padded = " " + re.sub(r"\s+", " ", low) + " "
    scored = [(sum(token in padded for token in tokens), code) for code, tokens in lex]
    score, code = max(scored)
    return code if score >= 2 else None


def _render(code: str, output_language: str, *, user_requested: bool) -> str:
    out = output_language if output_language in _NAMES else "en"
    name = _NAMES[out][code]
    if out == "de":
        return (f"Unmittelbar davor hast du {name} verlangt." if user_requested
                else f"Die unmittelbar vorherige MAESTRO-Antwort war auf {name}.")
    if out == "ar":
        return (f"كانت اللغة المطلوبة مباشرةً قبل هذه الرسالة هي {name}." if user_requested
                else f"كانت إجابة MAESTRO السابقة مباشرةً باللغة {name}.")
    if out == "fr":
        return (f"La langue demandée juste avant était le {name}." if user_requested
                else f"La réponse MAESTRO précédente était en {name}.")
    if out == "hr":
        return (f"Neposredno prije tražen je {name}." if user_requested
                else f"Prethodni MAESTRO odgovor bio je na jeziku: {name}.")
    if out == "it":
        return (f"La lingua richiesta subito prima era {name}." if user_requested
                else f"La risposta MAESTRO precedente era in {name}.")
    if out == "es":
        return (f"El idioma solicitado justo antes era {name}." if user_requested
                else f"La respuesta anterior de MAESTRO estaba en {name}.")
    if out == "pt":
        return (f"O idioma pedido imediatamente antes era {name}." if user_requested
                else f"A resposta anterior do MAESTRO estava em {name}.")
    return (f"The language requested immediately before was {name}." if user_requested
            else f"The immediately previous MAESTRO answer was in {name}.")


def resolve_previous_language_answer(
    prompt: str,
    messages: list[dict],
    output_language: str,
    detect_explicit_language: Callable[[str], str | None],
) -> str | None:
    """Return a locally provable answer, or None when transcript evidence is insufficient."""
    user_requested = bool(_USER_REQUEST_PERSPECTIVE.search(str(prompt or "")))
    if user_requested:
        previous_user = _last(messages, "user")
        if not previous_user:
            return None
        named = detect_explicit_language(previous_user)
        code = _LANG_CODE.get(str(named)) if named else None
    else:
        previous_assistant = _last(messages, "assistant")
        code = _infer_written_language(previous_assistant or "")
    if code not in _NAMES["en"]:
        return None
    return _render(code, output_language, user_requested=user_requested)
