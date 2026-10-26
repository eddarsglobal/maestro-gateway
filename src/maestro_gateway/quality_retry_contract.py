"""FIXED5.8G3A: bounded, provider-neutral retry guidance for verified format contracts.

Only used after an explicit format failure and only within the EXISTING quality
retry budget. No calls, authority, model preference or qualification decisions.
"""
from __future__ import annotations

_CONTRACTS = {
    "explicit_shape_two_sentences": {
        "en": "Write EXACTLY TWO complete sentences in English. End both with terminal punctuation. No heading, list, preface, or third sentence.",
        "fr": "Écris EXACTEMENT DEUX phrases complètes en français. Termine chacune par une ponctuation finale. Sans titre, liste, préambule ni troisième phrase.",
        "de": "Schreibe GENAU ZWEI vollständige deutsche Sätze. Beende beide mit Satzzeichen. Keine Überschrift, Liste, Einleitung oder dritter Satz.",
        "ar": "اكتب جملتين عربيتين كاملتين فقط، وأنهِ كل جملة بعلامة ترقيم. دون عنوان أو قائمة أو مقدمة أو جملة ثالثة.",
        "pt": "Escreve EXATAMENTE DUAS frases completas em português. Termina ambas com pontuação final. Sem título, lista, introdução ou terceira frase.",
        "es": "Escribe EXACTAMENTE DOS frases completas en español. Termina ambas con puntuación final. Sin título, lista, introducción ni tercera frase.",
        "hr": "Napiši TOČNO DVIJE potpune rečenice na hrvatskom. Obje završi interpunkcijskim znakom. Bez naslova, popisa, uvoda ili treće rečenice.",
        "it": "Scrivi ESATTAMENTE DUE frasi complete in italiano. Termina entrambe con punteggiatura finale. Senza titolo, elenco, introduzione o terza frase.",
    },
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
    key = reason
    if (reason.startswith("explicit_shape_expected_two_sentences_got_")
            or reason in {
                "explicit_shape_extra_text_outside_two_numbered_sentences",
                "explicit_shape_expected_numbered_items_one_and_two",
                "explicit_shape_mixed_numbered_and_unstructured_text",
            }):
        key = "explicit_shape_two_sentences"
    lang = language.strip().casefold().split("-", 1)[0].split("_", 1)[0]
    if reason == "explicit_shape_arabic_only_foreign_script":
        return (
            "قاعدة داخلية للمحاولة الجديدة، لا تذكرها في الإجابة: "
            "أجب عن سؤال المستخدم نفسه بالعربية فقط، مع السماح باسم MAESTRO وحده بغير العربية."
        )
    if reason == "user_instruction_echo":
        if lang == "ar":
            return (
                "قاعدة داخلية للمحاولة الجديدة، لا تذكرها في الإجابة: "
                "أجب عن السؤال نفسه فقط بالعربية المطلوبة، ولا تكرر تعليمات المستخدم أو تعيد صياغتها."
            )
        if lang == "de":
            return (
                "Interne Regel für den neuen Versuch, nicht in der Antwort erwähnen: "
                "Beantworte nur die eigentliche Frage auf Deutsch und wiederhole die Nutzeranweisungen nicht."
            )
        return (
            "INTERNAL RETRY RULE — do not quote or paraphrase user instructions: "
            "answer only the semantic question itself in the requested language and shape."
        )
    if reason == "maestro_identity_not_grounded":
        if lang == "ar":
            return (
                "قاعدة داخلية للمحاولة الجديدة، لا تذكرها في الإجابة: "
                "اذكر بوضوح أن MAESTRO مساعد ذكاء اصطناعي محلي على هذا الحاسوب، ثم اشرح وظيفته مباشرة من دون اختراع قدرات."
            )
        if lang == "de":
            return (
                "Interne Regel für den neuen Versuch, nicht in der Antwort erwähnen: "
                "Sage klar, dass MAESTRO ein lokaler KI-Assistent auf diesem Computer ist, und erkläre seine Aufgabe direkt ohne erfundene Fähigkeiten."
            )
        return "State MAESTRO's verified local-assistant identity directly without inventing capabilities."
    if reason in {"direct_answer_meta_preamble", "direct_answer_question_restatement"}:
        if lang == "ar":
            return (
                "قاعدة داخلية للمحاولة الجديدة، لا تذكرها في الإجابة: "
                "أجب مباشرة بجملتين خبريتين فقط، من دون مقدمة ومن دون إعادة السؤال."
            )
        if lang == "de":
            return (
                "Interne Regel für den neuen Versuch, nicht in der Antwort erwähnen: "
                "Antworte direkt in zwei Aussagesätzen, ohne Einleitung und ohne Wiederholung der Frage."
            )
        return (
            "INTERNAL RETRY RULE — answer directly in the requested shape; "
            "do not add a preamble or restate the user's question."
        )
    if reason == "explicit_shape_german_only_foreign_script":
        return "Antworte ausschließlich auf Deutsch. Verwende keine arabische, kyrillische oder CJK-Schrift."
    hints = _CONTRACTS.get(key)
    if hints is None:
        return ""
    return hints.get(lang, hints["en"])
