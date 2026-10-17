"""Bounded user-facing failure text, not a quality bypass or delivery success.

This module never incorporates a prompt, model response, provider id, or arbitrary
quality reason into a user-facing error. The internal quality reason remains
in mission metrics, and the original attempt evidence stays unchanged.
"""
from __future__ import annotations

class QualityContractFailure(RuntimeError):
    def __init__(self, reason: str, language: str):
        self.reason = str(reason or "")
        self.language = str(language or "en").lower().split("-")[0].split("_")[0]
        super().__init__("quality_contract_exhausted")

    def public_message(self) -> str:
        language = self.language if self.language in MESSAGES else "en"
        key = (
            "two_paragraphs" if self.reason == "explicit_format_two_paragraphs"
            else "digits_only" if self.reason == "explicit_format_digits_only"
            else "other"
        )
        return MESSAGES[language][key]

MESSAGES = {
    'en': {
        'two_paragraphs': "I couldn't produce a response in exactly two paragraphs after the permitted retry. No nonconforming answer was delivered.",
        'digits_only': "I couldn't produce a digits-only response after the permitted retry. No nonconforming answer was delivered.",
        'other': "I couldn't produce a response that passed the quality requirements within the permitted attempts. No rejected answer was delivered.",
    },
    'fr': {
        'two_paragraphs': "Je n'ai pas pu produire une réponse en exactement deux paragraphes après la reprise autorisée. Aucune réponse non conforme n'a été livrée.",
        'digits_only': "Je n'ai pas pu produire une réponse composée uniquement de chiffres après la reprise autorisée. Aucune réponse non conforme n'a été livrée.",
        'other': "Je n'ai pas pu produire une réponse conforme dans la limite des tentatives autorisées. Aucune réponse rejetée n'a été livrée.",
    },
    'de': {
        'two_paragraphs': "Auch nach dem erlaubten erneuten Versuch konnte ich keine Antwort mit genau zwei Absätzen erstellen. Die abgelehnte Antwort wurde nicht ausgegeben.",
        'digits_only': "Auch nach dem erlaubten erneuten Versuch konnte ich keine Antwort nur mit Ziffern erstellen. Die abgelehnte Antwort wurde nicht ausgegeben.",
        'other': "Innerhalb der erlaubten Versuche konnte ich keine passende Antwort erstellen. Die abgelehnte Antwort wurde nicht ausgegeben.",
    },
    'ar': {
        'two_paragraphs': "لم أتمكن من تقديم إجابة من فقرتين بالضبط بعد إعادة المحاولة المسموح بها. لم يتم عرض الإجابة غير المطابقة.",
        'digits_only': "لم أتمكن من تقديم إجابة تتكون من أرقام فقط بعد إعادة المحاولة المسموح بها. لم يتم عرض الإجابة غير المطابقة.",
        'other': "لم أتمكن من تقديم إجابة مطابقة ضمن عدد المحاولات المسموح به. لم يتم عرض الإجابة المرفوضة.",
    },
    'pt': {
        'two_paragraphs': "Não consegui produzir uma resposta com exatamente dois parágrafos após a nova tentativa permitida. A resposta não conforme não foi apresentada.",
        'digits_only': "Não consegui produzir uma resposta apenas com algarismos após a nova tentativa permitida. A resposta não conforme não foi apresentada.",
        'other': "Não consegui produzir uma resposta conforme dentro das tentativas permitidas. A resposta rejeitada não foi apresentada.",
    },
    'es': {
        'two_paragraphs': "No pude generar una respuesta de exactamente dos párrafos tras el reintento permitido. No se mostró ninguna respuesta que incumpliera el formato.",
        'digits_only': "No pude generar una respuesta solo con dígitos tras el reintento permitido. No se mostró ninguna respuesta que incumpliera el formato.",
        'other': "No pude generar una respuesta válida dentro de los intentos permitidos. No se mostró la respuesta rechazada.",
    },
    'hr': {
        'two_paragraphs': "Nisam uspio sastaviti odgovor u točno dva odlomka ni nakon dopuštenog ponovnog pokušaja. Neodgovarajući odgovor nije prikazan.",
        'digits_only': "Nisam uspio sastaviti odgovor samo od znamenki ni nakon dopuštenog ponovnog pokušaja. Neodgovarajući odgovor nije prikazan.",
        'other': "Nisam uspio sastaviti odgovarajući odgovor unutar dopuštenog broja pokušaja. Odbijeni odgovor nije prikazan.",
    },
    'it': {
        'two_paragraphs': "Non sono riuscito a produrre una risposta in esattamente due paragrafi dopo il nuovo tentativo consentito. La risposta non conforme non è stata mostrata.",
        'digits_only': "Non sono riuscito a produrre una risposta composta solo da cifre dopo il nuovo tentativo consentito. La risposta non conforme non è stata mostrata.",
        'other': "Non sono riuscito a produrre una risposta conforme entro i tentativi consentiti. La risposta rifiutata non è stata mostrata.",
    },
}
