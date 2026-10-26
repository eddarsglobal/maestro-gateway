"""Bounded symbolic task proofs for MAESTRO's local sovereign Core.

A proof is returned only for a uniquely decoded, auditable symbolic request.
This module has no IO, LLM calls, credentials, network, external executors, or
operational permissions. It is NOT a natural-language comprehension engine.
Ambiguous or out-of-grammar requests return None to governed inference.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

@dataclass(frozen=True, slots=True)
class SymbolicProof:
    subtask: str
    result: str

# The localized intent markers are a SMALL, explicit, reviewable grammar.
# They do not contain expected answers or any particular test identifiers.
_ORDER_EDGE = re.compile(
    r'(?<![A-Za-z])([A-Z])\s+(?:arrives\s+before|arrive\s+avant|'
    r'kommt\s+vor|chega\s+antes\s+de|llega\s+antes\s+que|'
    r'stiže\s+prije|arriva\s+prima\s+di|before|avant|vor|'
    r'قبل|antes\s+de|antes\s+que|prije|prima\s+di)\s+([A-Z])(?![A-Za-z])', re.I)
_ORDER_END = re.compile(r'\b(?:last|dernier|zuletzt|último|ultimo|posljednji|'
                        r'الأخير|أخيرًا|al\s+final)\b|(?:من\s+يصل\s+أخير)', re.I)
_ORDER_NEGATION = re.compile(r'\b(?:not|ne\s+pas|nicht|no|non|nije|nemoj)\b|ليس|لا\s+يصل', re.I)
_SET_BLOCK = re.compile(r'(?<![A-Za-z])([A-Z])\s*=\s*\{([^{}]{1,200})\}', re.U)
_SET_QUERY = re.compile(r'all\s+three|trois\s+ensembles|allen\s+drei|'
    r'المجموعات\s+الثلاث|três\s+conjuntos|tres\s+conjuntos|'
    r'sva\s+tri\s+skupa|tutti\s+e\s+tre|allen\s+drei\s+mengen',re.I)
_SET_FORBID = re.compile(r'\b(?:not|except|outside|without|exclude|exclu|'
                         r'ausgeschlossen|osim|bez|sin|sem)\b|باستثناء|عدا',re.I)
_RULE_START = re.compile(r'\b(?:rule|règle|regel|regra|regla|pravilo|regola)\b|قاعدة',re.I)
_RULE_DIV = re.compile(r'divisib|divis[ií]v|teilbar|djeljiv|القسمة',re.I)
_RULE_ODD = re.compile(r'\b(?:odd|impair|ungerade|ímpar|impar|neparan|dispari)\b|فردي',re.I)
_RULE_ELSE = re.compile(r'\b(?:otherwise|sinon|sonst|caso\s+contrário|'
                        r'de\s+lo\s+contrario|inače|altrimenti)\b|وإلا',re.I)
_RULE_REPLY = re.compile(r'\b(?:reply|respond|answer|réponds?|antworte|'
                         r'responde|respond[a-e]|odgovori|rispondi)\s+([A-Z][A-Z0-9_-]{1,24})\b|'
                         r'(?:فأجب|فاجب|أجب)\s+([A-Z][A-Z0-9_-]{1,24})\b',re.I)
_TIME_START = re.compile(r'\b(?:begins?|starts?|commence|beginnt|com[eç]a|'
                         r'empieza|počinje|inizia|começa|comeca)\b|يبدأ',re.I)
_TIME_END = re.compile(r'\b(?:ending|endzeit|fin\s+au|hora\s+final|'
                       r'hora\s+de\s+fin|završetka|orario\s+finale)\b|وقت\s+النهاية',re.I)
_TIME_DURATION = re.compile(r'(?<!\d)(\d{1,4})\s*(?:(?:more|extra|additional)\s+)?(?:minutes?|minuten|'
                             r'minutos|minuti|minuta|دقيقة|دقائق)\b',re.I)
_TIME_CLOCK = re.compile(r'(?<!\d)(\d{1,2}):(\d{2})(?!\d)')
_REV_CUE = re.compile(r'\b(?:reverse|inverse|kehre|inverte|invierte|'
                      r'obrni|inverti)\b|اعكس',re.I)
_REV_THIRD = re.compile(r'\b(?:third|troisi[eè]me|dritt(?:e|en|es)|'
                        r'terceiro|tercer|tre[cć]i|terzo)\b|الثالث',re.I)
_REV_FORBID = re.compile(r'\b(?:do\s+not|without|not|pas|nicht|nemoj|'
                         r'non|sin|sem|bez)\b|لا\s+تعكس',re.I)


def _ordering(prompt: str) -> SymbolicProof | None:
    if not _ORDER_END.search(prompt) or _ORDER_NEGATION.search(prompt): return None
    head = re.split(r'[?؟]',prompt,maxsplit=1)[0]
    pairs=[(m.group(1),m.group(2)) for m in _ORDER_EDGE.finditer(head)]
    if not (2 <= len(pairs) <= 12): return None
    # Every source/target letter must be consumed by the explicit edge grammar.
    # Otherwise the prompt may contain negations, competing orders or unrelated
    # symbolic facts; never infer a chain from a partial match.
    letters = re.findall(r'(?<![A-Za-z])[A-Z](?![A-Za-z])',head)
    if letters != [v for e in pairs for v in e]: return None
    edges={}; nodes=set()
    for a,b in pairs:
        if a==b or a in edges: return None
        edges[a]=b; nodes.update((a,b))
    if len(nodes)!=len(pairs)+1: return None
    sinks=nodes-edges.keys()
    if len(sinks)!=1: return None
    last=next(iter(sinks))
    for node in nodes:
        seen=set(); cur=node
        while cur in edges:
            if cur in seen: return None
            seen.add(cur); cur=edges[cur]
        if cur!=last:return None
    return SymbolicProof('symbolic_order',last)


def _intersection(prompt: str) -> SymbolicProof | None:
    if not _SET_QUERY.search(prompt) or _SET_FORBID.search(prompt): return None
    blocks=_SET_BLOCK.findall(prompt)
    if len(blocks)!=3 or prompt.count('{')!=3 or prompt.count('}')!=3:return None
    if len({k for k,_ in blocks})!=3:return None
    parsed=[]
    for _, content in blocks:
        words=[v.strip() for v in content.split(',')]
        if not 2<=len(words)<=20 or any(not re.fullmatch(r'[A-Z][A-Z0-9_]{1,30}',v) for v in words):return None
        if len(set(words))!=len(words):return None
        parsed.append(set(words))
    common=set.intersection(*parsed)
    if len(common)!=1:return None
    return SymbolicProof('symbolic_intersection',next(iter(common)))


def _condition(prompt: str) -> SymbolicProof | None:
    if not (_RULE_START.search(prompt) and _RULE_DIV.search(prompt) and _RULE_ODD.search(prompt)):return None
    if re.search(r'\b(?:not|ne\s+pas|nicht|non|nemoj|isn.t|is\s+not)\b|ليس|غير\s+فرد',prompt,re.I):return None
    numbers=re.findall(r'(?<!\w)-?\d+(?!\w)',prompt)
    if len(numbers)!=2:return None
    n,d=map(int,numbers)
    if d==0 or abs(n)>10**9 or abs(d)>10**9:return None
    alt=_RULE_ELSE.search(prompt)
    if alt is None:return None
    left,right=prompt[:alt.start()],prompt[alt.end():]
    # Exactly one true/false output command, no defaulting to arbitrary codes.
    a=_RULE_REPLY.findall(left); b=_RULE_REPLY.findall(right)
    if len(a)!=1:return None
    yes=a[0][0] or a[0][1]
    if len(b)==1: no=b[0][0] or b[0][1]
    elif len(b)==0:
        m=re.match(r'\s*([A-Z][A-Z0-9_-]{1,24})\b',right)
        if not m:return None
        no=m.group(1)
    else:return None
    if yes==no:return None
    # Explicit both-conditions-as-conjunction only.
    conjunction = re.search(r'\b(?:and|et|und|e|y|i|ed)\b|وكان',left,re.I)
    if conjunction is None:return None
    return SymbolicProof('symbolic_rule',yes if n%d==0 and n%2!=0 else no)


def _clock(prompt: str) -> SymbolicProof | None:
    if not (_TIME_START.search(prompt) and _TIME_END.search(prompt) and 'HH:MM' in prompt): return None
    ts=_TIME_CLOCK.findall(prompt)
    if len(ts)!=1:return None
    h,m=map(int,ts[0]); durations=[int(v) for v in _TIME_DURATION.findall(prompt)]
    if len(durations)==1 and re.search(r'\b(?:la segunda|second|segundo)\s+(?:otros|plus|more)\s+(\d{1,4})\b',prompt,re.I):
        extra=re.search(r'\b(?:la segunda|second|segundo)\s+(?:otros|plus|more)\s+(\d{1,4})\b',prompt,re.I)
        durations.append(int(extra.group(1)))
    if not 0<=h<=23 or not 0<=m<=59 or len(durations)!=2:return None
    if not all(0<=n<=1440 for n in durations):return None
    # Preserve the interface's clock format; no date/time-zone claims.
    total=(h*60+m+sum(durations))%(24*60)
    return SymbolicProof('symbolic_clock',f'{total//60:02d}:{total%60:02d}')


def _reverse_third(prompt: str) -> SymbolicProof | None:
    if not (_REV_CUE.search(prompt) and _REV_THIRD.search(prompt)):return None
    if _REV_FORBID.search(prompt):return None
    tokens=re.findall(r'(?<![A-Za-z])([A-Z]{3,32})(?![A-Za-z])',prompt)
    if len(tokens)!=1:return None
    return SymbolicProof('symbolic_reverse_index',tokens[0][::-1][2])


def prove_symbolic_task(prompt: str) -> SymbolicProof | None:
    """Fail closed unless exactly one locally provable symbolic intent exists."""
    if not isinstance(prompt,str) or not 8 <= len(prompt) <= 480 or '\n' in prompt: return None
    if any(x in prompt for x in ('```','<MAESTRO_TOOL>','<script','http://','https://')):return None
    # No symbolic shortcut across mixed intents / additional instructions.
    # Reject potential file/network/model/control-plane requests even when a
    # small valid arithmetic or symbolic island happens to occur in the text.
    if re.search(
        r'\b(?:also|additionally|furthermore|außerdem|además|también|'
        r'également|aussi|inoltre|dodatno|osim|instead|except|without|'
        r'ignore|ignora|delete|remove|write|edit|install|execute|'
        r'run\s+a\s+command|workspace|folder|file|browser|model|'
        r'translate|explain|summarize|expliquer|traduis|erkläre)\b|'
        r'(?:وبالإضافة|بالإضافة|وتجاهل|واحذف)', prompt, re.I
    ): return None
    proofs=[p for fn in (_ordering,_intersection,_condition,_clock,_reverse_third)
            if (p:=fn(prompt)) is not None]
    return proofs[0] if len(proofs)==1 else None
