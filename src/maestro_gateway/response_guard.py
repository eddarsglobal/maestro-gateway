from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


# An imperative request to reproduce text is different from a frustrated
# user's "je te répète" / "I repeat my question". The old substring-based
# detector treated the latter as permission to bypass every quality check.
_REPEAT_COMMAND = re.compile(
    r"^\s*(?:r[eé]p[eè]te|repete|recopie|reproduis|repeat|copy|"
    r"wiederhole|ponovi|ripeti|repite|repita|أعد)\b",
    re.IGNORECASE,
)
_REPEAT_POLITE = re.compile(
    r"^\s*(?:(?:peux|pourrais|pouvez|pourriez)[- ]?(?:tu|vous)\s+|"
    r"(?:can|could|would)\s+you\s+)(?:me\s+)?"
    r"(?:r[eé]p[eè]ter|repeter|recopier|repeat|copy)\b",
    re.IGNORECASE,
)
# A bounded spelling variant (qu'esst) is intentional: a user typo must not
# bypass capability grounding, normal routing, or the response quality gate.
_CAPABILITY_INTENT = re.compile(
    r"(?:qu['’]es{1,2}t[- ]ce\s+que\s+tu\s+peux\s+faire|"
    r"que\s+peux[- ]?tu\s+faire|qu['’]es{1,2}t[- ]ce\s+que\s+vous\s+"
    r"pouvez\s+faire|que\s+pouvez[- ]?vous\s+faire|"
    r"quelles?\s+sont\s+(?:tes|vos)\s+(?:capacit[eé]s|fonctions|"
    r"fonctionnalit[eé]s)|de\s+quoi\s+es[- ]?tu\s+capable|"
    r"what\s+can\s+you\s+do|what\s+are\s+your\s+capabilities|"
    r"qu['’]es{1,2}t[- ]ce\s+que\s+tu\s+sais\s+faire|"
    r"(?:reci\s+mi\s+)?(?:ukratk+o\s+)?(?:[šs]to|[šs]ta)\s+"
    r"(?:sve\s+)?mo[žz]e[šs]|"
    r"(?:koje\s+su\s+tvoje\s+mogucnosti|koje\s+su\s+tvoje\s+mogućnosti)|"
    r"[šs]to\s+sve\s+zna[šs]\s+raditi|"
    r"was\s+kannst\s+du|welche\s+funktionen\s+hast\s+du|"
    r"cosa\s+(?:sai|puoi)\s+fare|che\s+cosa\s+puoi\s+fare|"
    r"quali\s+sono\s+le\s+tue\s+capacità|"
    r"qu[eé]\s+puedes\s+hacer|cu[aá]les\s+son\s+tus\s+funciones|"
    r"o\s+que\s+(?:podes|consegues|voc[eê]\s+pode)\s+fazer|"
    r"ماذا\s+يمكنك\s+أن\s+تفعل|ما\s+هي\s+قدراتك|"
    r"ما\s+الذي\s+تستطيع\s+فعله)",
    re.IGNORECASE,
)
# These classify the question, not the language selected in SETTINGS. A question
# about the previous answer must never be mistaken for a greeting or a request
# to reproduce the previous defective output.
_LANGUAGE_QUESTION = re.compile(
    r"(?:\b(?:na\s+kojem(?:\s+si\s+mi)?(?:\s+to)?\s+jeziku|"
    r"koj[ie]\s+(?:je\s+to\s+)?jezik|kojim\s+jezikom|"
    r"which\s+language|what\s+language|quelle\s+langue|"
    r"welche\s+sprache)\b)",
    re.IGNORECASE,
)
_CROATIAN_MALFORMED = re.compile(r"\b(?:ašu|jaš|moždaj)\b", re.I)
_CROATIAN_QUERY = re.compile(
    r"\b(?:reci\s+mi|ukratk+o|[šs]ta\s+sve|[šs]to\s+sve|"
    r"mo[žz]e[šs]|odgovorio|koj[ie]\s+(?:je\s+to\s+)?jezik|"
    r"jeziku|hrvatsk\w*)\b", re.I,
)
_CROATIAN_OUTPUT = re.compile(
    r"\b(?:mogu|mo[žz]e[šsmt]?|pomo[ćc]\w*|"
    r"odgovor\w*|odgovorio|hrvatsk\w*|jeziku|"
    r"dokument\w*|datotek\w*|pretra\w*|"
    r"isprič\w*|ispric\w*|neispravn\w*|"
    r"nerazumljiv\w*|pogrešn\w*)\b", re.I,
)
_LANGUAGE_NAMES = re.compile(
    r"\b(?:hrvatsk\w*|croatian|bosansk\w*|serbian|srpsk\w*|"
    r"englesk\w*|english|francusk\w*|french|njemačk\w*|german|"
    r"nerazumljiv\w*|neprepoznatljiv\w*)\b", re.I,
)
_LANGUAGE_ERROR_ACK = re.compile(
    r"\b(?:pogrešn\w*|neispravn\w*|nerazumljiv\w*|"
    r"loš\w*|ispričav\w*|nisam\s+usp\w*|"
    r"incorrect|broken|incoherent|gibberish)\b", re.I,
)

# Examples that correspond to recognizable, concrete capability categories.
# This is a fail-closed minimum check, not a semantic quality guarantee.
_CAPABILITY_CATEGORIES = (
    re.compile(r"\b(?:fichiers?|files?|documents?|dossiers?|workspace|datotek\w*|dokument\w*)\b", re.I),
    re.compile(r"\b(?:code|coder|programmation|programming|scripts?|python|git|kod\w*|programiran\w*|c[oó]digo\w*|\u0628\u0631\u0645\u062c\u064a\u0629|\u0627\u0644\u0628\u0631\u0645\u062c\u0629)\b", re.I),
    re.compile(r"\b(?:analyser?|analyse|analyze|analysi[sz]e|inspecter?|inspection|analiz\w*|pregled\w*|revisar|inspecionar|verificar|\u0641\u062d\u0635)\b", re.I),
    re.compile(r"\b(?:chercher|rechercher|search|recherche|retrouver|find|pretraž\w*|pretraz\w*)\b", re.I),
    re.compile(r"\b(?:conversations?|historique|memory|m[eé]moire|context|razgovor\w*|povijest)\b", re.I),
    re.compile(r"\b(?:mod[eè]les?|models?|ollama|local|locaux|runtime|moteurs?|model\w*|lokaln\w*)\b", re.I),
    re.compile(r"\b(?:workspace|autorisations?|permissions?|approbation|approval|odobren\w*|dopušten\w*)\b", re.I),
    re.compile(r"\b(?:corriger|debug|d[eé]boguer|r[eé]parer|fix|isprav\w*)\b", re.I),
    re.compile(r"\b(?:gespr[aä]ch\w*|unterhalt\w*|conversar|conversazion\w*|conversaciones|conversas)\b", re.I),
    re.compile(r"\b(?:sprachmodell\w*|modelos|modelli|modelle|modelos\s+de\s+linguagem)\b", re.I),
    re.compile(r"\b(?:dateien|arbeitsbereich|repository|repositorio|repositorio|ficheiros|archivos|file\w*)\b", re.I),
    re.compile(r"(?:المحادثات|الملفات|نماذج|العمل|مستودع)", re.I),
    re.compile(r"(?:البرمجية|البرمجة|برمجيات|التعليمات\s+البرمجية)", re.I),
)

# Purposeful software-building intent (not a short-message heuristic).
# Includes eight supported UI languages. This is conservative: a software
# name alone does not authorize writing files or running commands.
_SOFTWARE_SUBJECT = re.compile(
    r"(?:\b(?:three\s*\.?\s*js|threejs|react|django|next\.?js|webgl|"
    r"website|web\s*site|site\s*web|site\s*internet|application|"
    r"app|project|projet|webpage|homepage|webseite|webprojekt|"
    r"webstranic\w*|stranic\w*|sajt|sito\s*web|sitio\s*web|"
    r"página\s*web|pagina\s*web|site\s*web|"
    r"aplikacij\w*|applicazion\w*|aplicaci[oó]n)\b|"
    r"موقع\s*(?:ويب|إلكتروني)|تطبيق)", re.I)
_SOFTWARE_ACTION = re.compile(
    r"(?:\b(?:cr[eé]e[rz]?|cr[eé]ation|construis|construire|d[eé]veloppe[rz]?|"
    r"faire|impl[eé]menter|g[eé]n[eé]re[rz]?|"
    r"build|create|develop|make|implement|generate|"
    r"erstell\w*|entwickel\w*|bau\w*|"
    r"naprav\w*|izrad\w*|kreir\w*|razvij\w*|"
    r"crea\w*|sviluppa\w*|realizza\w*|"
    r"crear|desarrollar|construir|"
    r"criar|desenvolver|construir|fazer)\b|"
    r"أنشئ|إنشاء|اصنع|طوّر|برمج)", re.I)


def asks_for_software_project(prompt: str) -> bool:
    """Route creation intent; never confers write permission or success."""
    value = str(prompt or "")
    return bool(_SOFTWARE_SUBJECT.search(value) and _SOFTWARE_ACTION.search(value))

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
    value = str(prompt or "").strip()
    return bool(_REPEAT_COMMAND.search(value) or _REPEAT_POLITE.search(value))


def asks_about_capabilities(prompt: str) -> bool:
    return bool(_CAPABILITY_INTENT.search(str(prompt or "")))


def asks_about_previous_language(prompt: str) -> bool:
    return bool(_LANGUAGE_QUESTION.search(str(prompt or "")))


def substantive_capability_answer(output: str) -> bool:
    # Require multiple concrete examples, rather than an identity slogan.
    return sum(bool(category.search(output)) for category in _CAPABILITY_CATEGORIES) >= 2


# A lexical backstop for unverified high-risk capability assertions. These
# checks reject a few recognizable false *positive* claims; they cannot prove
# that arbitrary natural-language descriptions are semantically correct.
_SYSTEM_FILES = re.compile(
    r"\b(?:fichiers?\s+(?:du\s+)?syst[eè]me|"
    r"system\s+files?|systemdateien|file\s+di\s+sistema|"
    r"archivos?\s+del\s+sistema|ficheiros?\s+do\s+sistema)\b", re.I)
_MUTATION = re.compile(
    r"\b(?:copier|renommer|supprimer|cr[eé]er|modifier|[eé]diter|d[eé]placer|"
    r"effacer|[eé]crire|delete|remove|rename|move|write|edit|modify|copy|"
    r"create|erase|l[oö]schen|umbenennen|bearbeiten|eliminare|"
    r"cancellare|borrar|eliminar|apagar)\b", re.I)
_FILE_TARGET = re.compile(
    r"\b(?:fichiers?|dossiers?|files?|folders?|directories|dateien|"
    r"datei|cartelle|archivos?|carpetas?|ficheiros?|pastas?)\b", re.I)
_NEGATIVE = re.compile(
    r"(?:\b(?:ne\s+(?:peux|peut|puis|sais|dois|dispose|permet|permis|sont)"
    r".{0,40}\bpas|aucun\s+acc[eè]s|interdit|impossible|"
    r"sans\s+pouvoir|non\s+disponible|"
    r"cannot|can't|not\s+(?:able|allowed|available)|"
    r"does\s+not\s+have\s+access|no\s+access|"
    r"nicht\s+(?:m[oö]glich|erlaubt)|non\s+posso|"
    r"no\s+puedo|n[aã]o\s+posso)\b)", re.I)
_SCOPE = re.compile(
    r"\b(?:workspace|espace\s+de\s+travail|r[eé]pertoire|"
    r"dossier\s+autoris[eé]|authorized\s+(?:workspace|folder)|"
    r"authorised\s+(?:workspace|folder)|arbeitsbereich|"
    r"spazio\s+di\s+lavoro|espacio\s+de\s+trabajo)\b", re.I)
_APPROVAL = re.compile(
    r"\b(?:approbation|autorisation|confirmation|accord|"
    r"approval|permission|consent|consentement|genehmigung|"
    r"autorizzazione|autorizaci[oó]n|autoriza[cç][aã]o)\b", re.I)
_DESKTOP_CONTROL = re.compile(
    r"\b(?:contr[oô]ler\s+(?:ton|votre|le)\s+(?:ordinateur|bureau)|"
    r"piloter\s+(?:ton|votre|le)\s+(?:ordinateur|bureau)|"
    r"control\s+(?:your|the)\s+(?:computer|desktop)|"
    r"remote\s+control\s+(?:your|the)\s+computer)\b", re.I)
_UNRESTRICTED_ACCESS = re.compile(
    r"\b(?:sans\s+(?:aucune\s+)?autorisation|sans\s+restriction|"
    r"without\s+(?:any\s+)?permission|unrestricted\s+access|"
    r"acc[eè]s\s+(?:total|illimit[eé]|sans\s+limite))\b", re.I)
_GIT_COMMIT = re.compile(
    r"\b(?:faire|cr[eé]er|ex[eé]cuter|effectuer|lancer|create|make|execute)"
    r".{0,24}\b(?:commits?|git\s+commit)\b", re.I)
_GIT_PROPOSAL = re.compile(
    r"\b(?:proposition|proposer|propose|proposal|sans\s+l.ex[eé]cuter|"
    r"not\s+execute|non\s+ex[eé]cut[eé])\b", re.I)


def _unverified_capability_claim(output: str, capability_snapshot: list[dict] | None) -> str | None:
    """Do not accept specific unsupported high-risk assertions as model truth.

    The live registry is input data, NEVER a reason to grant permission. A
    status of `available_requires_workspace` is not runtime authorization.
    """
    if capability_snapshot is None:
        return None
    text = str(output or "")
    if re.search(r"\\[\*\_]{1,2}", text):
        return "literal_markdown_escapes"
    clauses = [cl.strip() for cl in re.split(r"[\n;.!?]+|\b(?:mais|but|jedoch|per[oò])\b", text) if cl.strip()]
    statuses = {str(c.get('capability_id')): str(c.get('status'))
                for c in capability_snapshot if isinstance(c, dict)}
    scoped = bool(_SCOPE.search(text) and _APPROVAL.search(text))
    for clause in clauses:
        if _NEGATIVE.search(clause):
            continue
        if _SYSTEM_FILES.search(clause):
            return "unsupported_system_file_access"
        if _UNRESTRICTED_ACCESS.search(clause):
            return "unrestricted_authority_claim"
        if _DESKTOP_CONTROL.search(clause) and statuses.get('computer_use') != 'available':
            return "unverified_computer_use"
        if _GIT_COMMIT.search(clause) and not _GIT_PROPOSAL.search(clause) and statuses.get('git.execute') != 'available':
            return "unverified_git_commit_execution"
        if (_MUTATION.search(clause) and _FILE_TARGET.search(clause)
                and not scoped):
            return "unscoped_filesystem_mutation_claim"
    return None


def evaluate_response(
    prompt: str,
    output: str,
    *,
    prior_output: str | None = None,
    prior_user_prompt: str | None = None,
    capability_snapshot: list[dict] | None = None,
    completion_reason: str | None = None,
) -> QualityResult:
    source = _normalize(prompt)
    answer = _normalize(output)

    # The model can return fluent-looking but incomplete text when the token
    # budget ends. Ollama uses done_reason=length; compatible APIs commonly
    # use finish_reason=length. Never certify such text as a delivered answer,
    # even if the lexical content would otherwise pass the quality guard.
    if (completion_reason or "").strip().casefold() in {
        "length", "max_tokens", "max_output_tokens", "token_limit"
    }:
        return QualityResult(False, "generation_token_limit", 0.0)

    if not answer:
        return QualityResult(
            False,
            "empty_output",
            1.0,
        )

    if (explicit_repeat_request(prompt)
            and not asks_about_capabilities(prompt)
            and not asks_about_previous_language(prompt)):
        return QualityResult(
            True,
            "explicit_repeat_requested",
            0.0,
        )

    # Do not accept copied internal identity-policy text as a response.
    # A question about the model's developer/provider is the exception.
    disclosure_request = re.search(
        r"\b(?:d[eé]veloppeur|fournisseur|developer|provider|"
        r"created\s+you|qui\s+t.a\s+cr[eé][eé])\b",
        prompt,
        re.IGNORECASE,
    )
    unsolicited_disclaimer = re.search(
        r"(?:je\s+ne\s+suis\s+pas|i\s+am\s+not|i.m\s+not)"
        r".{0,75}(?:d[eé]veloppeur|fournisseur|developer|provider)",
        output,
        re.IGNORECASE | re.DOTALL,
    )
    if unsolicited_disclaimer and not disclosure_request:
        return QualityResult(False, "identity_policy_leak", 0.0)

    # A failure that reads like Croatian-looking nonsense must not be
    # certified as a legitimate response. This is a known-regression check,
    # not an assertion that we can prove grammatical correctness with regex.
    if _CROATIAN_MALFORMED.search(output):
        return QualityResult(False, "malformed_croatian_output", 0.0)

    # Minimal language consistency signal for *explicit Croatian questions*.
    # A regex cannot certify Croatian fluency; it can stop obvious English
    # template responses on these two intents. Full fluency requires tests
    # with real models and native-speaker review.
    if ((_CROATIAN_QUERY.search(prompt) and
         (asks_about_capabilities(prompt) or asks_about_previous_language(prompt)))
            and not _CROATIAN_OUTPUT.search(output)):
        return QualityResult(False, "croatian_language_mismatch", 0.0)

    if asks_about_previous_language(prompt):
        if not _LANGUAGE_NAMES.search(output):
            return QualityResult(False, "language_question_not_answered", 0.0)
        if prior_output and _CROATIAN_MALFORMED.search(prior_output):
            if not _LANGUAGE_ERROR_ACK.search(output):
                return QualityResult(False, "previous_language_error_not_acknowledged", 0.0)

    if asks_about_capabilities(prompt):
        # A public capabilities explanation is not a raw internal API dump.
        # The model can read technical identifiers as grounding data but
        # should explain user-visible abilities in ordinary language.
        # Explicit questions about API names/identifiers remain exempt.
        technical_ids = re.findall(
            r"\b(?:filesystem|workspace|terminal|git)\.[a-z_]+\b",
            output, re.I,
        )
        explicitly_technical = re.search(
            r"\b(?:api|ids?|identifiants?|outils?|tools?|commandes?|"
            r"technical|techniques?|protocoles?)\b",
            prompt, re.I,
        )
        if len(technical_ids) >= 3 and not explicitly_technical:
            return QualityResult(False, "internal_api_dump", 0.0)
        if not substantive_capability_answer(output):
            return QualityResult(False, "capabilities_not_answered", 0.0)
        claim_error = _unverified_capability_claim(output, capability_snapshot)
        if claim_error is not None:
            return QualityResult(False, claim_error, 0.0)

    if asks_for_software_project(prompt):
        # Pre-filter only. The word "Three.js" alone is not a useful answer.
        topic = re.search(
            r"(?:\b(?:three\s*\.?\s*js|webgl|react|django|"
            r"site\s+web|website|webstranic\w*|webseite|"
            r"sito\s+web|sitio\s+web)\b|موقع)", output, re.I)
        technical = re.findall(
            r"(?:\b(?:sc[eè]ne|scene|escena|scena|kamera|camera|cam[eé]ra|"
            r"renderer|rendering|rendu|canvas|3d|animation\w*|"
            r"fichier\w*|files?|datotek\w*|komponent\w*|"
            r"javascript|typescript|html|css|webgl|maquette|prototype)\b|"
            r"مشهد|ملف|كاميرا|تحريك)", output, re.I)
        if not topic or len(set(token.casefold() for token in technical)) < 2:
            return QualityResult(False, "software_request_not_substantively_addressed", 0.0)
    # Reject stale assistant replies when the user has asked a different
    # question. This check does not compare against or reveal private content
    # outside the current conversation.
    if prior_output and prior_user_prompt:
        previous = _normalize(prior_output)
        if len(previous.split()) >= 7 and len(answer.split()) >= 7:
            overlap = SequenceMatcher(None, previous, answer).ratio()
            if overlap >= 0.79:
                return QualityResult(False, "stale_previous_answer", overlap)

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
