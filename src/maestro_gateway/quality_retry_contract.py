"""FIXED5.8G3A: bounded, provider-neutral retry guidance for verified format contracts.

Only used after an explicit format failure and only within the EXISTING quality
retry budget. No calls, authority, model preference or qualification decisions.
"""
from __future__ import annotations

_CONTRACTS = {
    "explicit_format_two_paragraphs": {
        "en": "Write EXACTLY TWO prose paragraphs, separated by one blank line. No title, heading, list, table, or third paragraph.",
        "fr": "Rédige EXACTEMENT DEUX paragraphes de prose, séparés par une ligne vide. Sans titre, sous-titre, liste, tableau ni troisième paragraphe.",
        "de": "Schreibe GENAU ZWEI Fließtext-Absätze, getrennt durch eine Leerzeile. Keine Überschrift, Liste, Tabelle oder dritter Absatz.",
        "ar": "اكتب فقرتين نثريتين فقط، تفصل بينهما سطر فارغ. دون عنوان أو قائمة أو جدول أو فقرة ثالثة.",
        "pt": "Escreve EXATAMENTE DOIS parágrafos de prosa, separados por uma linha em branco. Sem título, lista, tabela ou terceiro parágrafo.",
        "es": "Escribe EXACTAMENTE DOS párrafos de prosa, separados por una línea en blanco. Sin título, lista, tabla ni tercer párrafo.",
        "hr": "Napiši TOČNO DVA odlomka običnog teksta, odvojena praznim retkom. Bez naslova, popisa, tablice ili trećeg odlomka.",
        "it": "Scrivi ESATTAMENTE DUE paragrafi di prosa, separati da una riga vuota. Senza titolo, elenco, tabella o terzo paragrafo.",
    },
    "explicit_format_digits_only": {
        "en": "Return DIGITS ONLY: one unsigned integer, without explanatory words, equation, punctuation or Markdown.",
        "fr": "Réponds UNIQUEMENT PAR DES CHIFFRES : un entier positif ou nul, sans mots, calcul, ponctuation ni Markdown.",
        "de": "Antworte NUR MIT ZIFFERN: eine nichtnegative ganze Zahl, ohne Worte, Gleichung, Satzzeichen oder Markdown.",
        "ar": "أجب بالأرقام فقط: عدد صحيح غير سالب دون كلمات أو معادلة أو علامات ترقيم أو تنسيق.",
        "pt": "Responde APENAS COM ALGARISMOS: um inteiro não negativo, sem palavras, contas, pontuação ou Markdown.",
        "es": "Responde SOLO CON DÍGITOS: un entero no negativo, sin palabras, ecuaciones, puntuación ni Markdown.",
        "hr": "Odgovori SAMO ZNAMENKAMA: jedan nenegativan cijeli broj, bez riječi, jednadžbe, interpunkcije ili MarkDowna.",
        "it": "Rispondi SOLO CON CIFRE: un intero non negativo, senza parole, equazioni, punteggiatura o Markdown.",
    },
}


def contract_retry_hint(reason: str, language: str) -> str:
    """Return a tiny static hint, not the prior (possibly unsafe) model output.

    Unknown contracts and languages are safe: no fabricated constraints; the
    original user request remains authoritative in the transcript.
    """
    if not isinstance(reason, str) or not isinstance(language, str):
        return ""
    hints = _CONTRACTS.get(reason)
    if hints is None:
        return ""
    lang = language.strip().casefold().split("-", 1)[0].split("_", 1)[0]
    return hints.get(lang, hints["en"])
