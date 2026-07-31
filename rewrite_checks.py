"""
rewrite_checks.py
=================
Deterministische code-check voor een herschreven training (de 9 kopjes).
Geen LLM: plain Python-functies die een lijst `Issue`-objecten teruggeven,
gesplitst in HARD-FAIL (moet terug naar de schrijver) en FLAG (mag mee naar
judge/review). Zelfde geest als `finalize_scores` in score_trainings.py:
de code beslist deterministisch, het model schrijft alleen.

Verwachte input `rewrite` (de gestructureerde schrijver-output, `submit_rewrite`):
    {
      "overzicht":  "Wil je ... (55-65 woorden, 1 alinea)",
      "inleiding":  "... (180-210 woorden)",
      "modules":   { "modules": [ {"titel": "...", "bullets": ["...", "..."]}, ... ] },
      "aanpak_invulling":        "... (alleen de [....]-invulling)",
      "doelgroep":              "Deze training is voor ...",
      "voorkennis":             "... (1 zin) of de vaste fallbackzin",
      "doelen":                 ["... te ...en", "... te ...en", ...],    # 4-5 bullets, te-infinitief
      "vervolgstappen_titels": ["Titel A", "Titel B", ...],              # uit de catalogus
      "kortste_omschrijving":   "Wil je ... (<=200 tekens)",
      "nieuwe_titel":           "Training ...",                          # nooit cursus/opleiding
    }

Context `ctx` (optioneel):
    { "catalog_titles": {"Titel A", ...}, "naam": "Trainingsnaam" }

Gebruik:
    issues = check_rewrite(rewrite, ctx)
    if hard_fails(issues):
        # terug naar de schrijver met format_issues(hard_fails(issues))
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Severity + Issue
# ---------------------------------------------------------------------------

HARD = "hard"   # moet gerepareerd worden -> terug naar schrijver
FLAG = "flag"   # signaal -> mee naar judge / menselijke review


@dataclass(frozen=True)
class Issue:
    section: str      # kopje-sleutel of "algemeen"
    severity: str     # HARD | FLAG
    code: str         # korte machine-code, bv. "lengte_woorden"
    message: str      # leesbare uitleg (gaat mee terug naar de schrijver)

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.section}: {self.message}"


def hard_fails(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == HARD]


def flags(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == FLAG]


def format_issues(issues: list[Issue]) -> str:
    """Bundelt issues tot tekst die als revisie-instructie de schrijver in kan."""
    return "\n".join(f"- {i}" for i in issues)


# ---------------------------------------------------------------------------
# Tekst-helpers
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+(?:['-][A-Za-zÀ-ÿ0-9]+)*")
_HTML_RE = re.compile(r"<[^>]+>")
_SENTENCE_END_RE = re.compile(r"[.!?]+(?:\s|$)")
# placeholder-resten: [....], [naam training], {{ oplnaam }}, {iets}
_PLACEHOLDER_RE = re.compile(r"\[[^\]]*\.{2,}[^\]]*\]|\[naam[^\]]*\]|\{\{[^}]*\}\}|\{\s*\.{2,}\s*\}")
_BULLET_PREFIX_RE = re.compile(r"^\s*[-*•]\s+", re.M)
# "u"/"uw" als los tweede-persoons-woord (niet "u-vormig" e.d.)
_U_VORM_RE = re.compile(r"(?<![\w-])[Uu]w?(?![\w-])")

# Verboden LLM-frasen (uit humanisering_nl.md), hoofdletter-ongevoelig -> FLAG
BANNED_PATTERNS = [
    r"in de (?:snel veranderende|hedendaagse|moderne|dynamische) wereld van",
    r"in het (?:huidige|digitale) landschap",
    r"of het nu gaat om",
    r"duiken we (?:dieper )?in",
    r"ontdek de kracht van",
    r"ontgrendel het potentieel",
    r"naar een hoger niveau",
    r"niet alleen .{0,60}? maar ook",
    r"een (?:breed|ruim) scala aan",
    r"een schat aan",
    r"in een handomdraai",
    r"in no[- ]?time",
    r"waar wacht je nog op",
    r"zet (?:vandaag )?(?:nog )?de eerste stap",
    r"\b(?:naadloos|moeiteloos|cruciaal|essentieel|baanbrekend|revolutionair|ongekend)\b",
    r"\b(?:simpelweg|daadwerkelijk|gewoonweg)\b",
]
_BANNED_RE = [re.compile(p, re.I) for p in BANNED_PATTERNS]

# Marketingtaal / superlatieven -> FLAG
MARKETING_WORDS = ["de beste", "uniek", "gegarandeerd", "ongeëvenaard", "toonaangevend",
                   "wereldklasse", "state-of-the-art", "next-level", "game-changer"]

# Alles heet een training. "Examentraining" bevat geen van deze woorden en mag dus blijven.
_SOORTWOORD_RE = re.compile(
    r"\b(?:cursus(?:sen|se)?|opleiding(?:en)?|gebruikerscursus|examencursus|leergang(?:en)?)\b",
    re.I)

# Doelen staan in de te-infinitief, aansluitend op "Na deze training ben je in staat om:".
# Twee vormen: aaneengesloten ("te formuleren") en gesplitst ("voor te bereiden"). De meeste
# infinitieven eindigen op -en; de onregelmatige korte vormen staan er expliciet bij.
_TE_INFINITIEF_RE = re.compile(
    r"\bte\s+(?:\w+en|zijn|doen|gaan|staan|slaan|zien|hebben)\b", re.I)

# "In staat zijn om ..." is een van de aanbevolen causale constructies, maar hij zit al in de
# introzin: als bullet levert hij "ben je in staat om in staat te zijn om ...". Flag, geen hard
# fail -- de zin is niet fout, alleen dubbel.
_IN_STAAT_RE = re.compile(r"\bin staat\b", re.I)


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def char_count(text: str) -> int:
    return len(text or "")


def sentence_count(text: str) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    n = len(_SENTENCE_END_RE.findall(t))
    return n if n > 0 else 1  # tekst zonder eindteken telt als 1 zin


def _norm(text) -> str:
    return (text or "").strip() if isinstance(text, str) else ""


def _startswith_ci(text: str, prefix: str) -> bool:
    return _norm(text).lower().startswith(prefix.lower())


# ---------------------------------------------------------------------------
# Generieke checks over alle tekstvelden (HTML, placeholders, u-vorm, LLM-taal)
# ---------------------------------------------------------------------------

def _all_text_fields(rw: dict) -> list[tuple[str, str]]:
    """(sectie, tekst) voor elk tekstueel veld, incl. modules- en doelen-onderdelen."""
    out: list[tuple[str, str]] = []
    for key in ("overzicht", "inleiding", "aanpak_invulling",
                "doelgroep", "voorkennis", "kortste_omschrijving"):
        out.append((key, _norm(rw.get(key))))
    for mod in _modules(rw):
        out.append(("modules", _norm(mod.get("titel"))))
        for b in mod.get("bullets", []) or []:
            out.append(("modules", _norm(b)))
    for b in _doelen(rw):
        out.append(("doelen", _norm(b)))
    return [(s, t) for s, t in out if t]


def check_generic(rw: dict) -> list[Issue]:
    issues: list[Issue] = []
    for section, text in _all_text_fields(rw):
        if _HTML_RE.search(text):
            issues.append(Issue(section, HARD, "html", "bevat HTML-tags; lever platte tekst."))
        if _PLACEHOLDER_RE.search(text):
            issues.append(Issue(section, HARD, "placeholder",
                                "onvervulde placeholder ([....] / {{ oplnaam }}) blijven staan."))
        if _U_VORM_RE.search(text):
            issues.append(Issue(section, FLAG, "u_vorm",
                                "gebruikt mogelijk de 'u'-vorm; schrijf in 'je'-vorm."))
        for rx in _BANNED_RE:
            m = rx.search(text)
            if m:
                issues.append(Issue(section, FLAG, "llm_taal",
                                    f"LLM-frase gevonden: '{m.group(0)}' (zie humanisering_nl.md)."))
        low = text.lower()
        for w in MARKETING_WORDS:
            if w in low:
                issues.append(Issue(section, FLAG, "marketing", f"marketingtaal: '{w}'."))
    return issues


# ---------------------------------------------------------------------------
# Normalisatie van samengestelde velden
# ---------------------------------------------------------------------------

def _modules(rw: dict) -> list[dict]:
    prog = rw.get("modules")
    if isinstance(prog, dict):
        mods = prog.get("modules")
    elif isinstance(prog, list):
        mods = prog
    else:
        mods = None
    return [m for m in (mods or []) if isinstance(m, dict)]


def _doelen(rw: dict) -> list[str]:
    d = rw.get("doelen")
    if isinstance(d, dict):
        d = d.get("bullets")
    if isinstance(d, list):
        return [x for x in d if isinstance(x, str) and x.strip()]
    return []


def _titels(rw: dict) -> list[str]:
    t = rw.get("vervolgstappen_titels")
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str) and x.strip()]
    return []


# ---------------------------------------------------------------------------
# Per-kopje checks
# ---------------------------------------------------------------------------

# voorkennis staat hier bewust NIET in: een lege voorkennis is geldig -> de code
# voegt dan de vaste fallbackzin in (zie assemble_document / sjabloon.VOORKENNIS_FALLBACK).
REQUIRED_SECTIONS = ["overzicht", "inleiding", "modules",
                     "doelgroep", "doelen", "kortste_omschrijving"]


def check_presence(rw: dict) -> list[Issue]:
    issues = []
    for key in REQUIRED_SECTIONS:
        val = rw.get(key)
        empty = val is None or (isinstance(val, str) and not val.strip()) \
            or (isinstance(val, (list, dict)) and not val)
        if empty:
            issues.append(Issue(key, HARD, "ontbreekt", "kopje ontbreekt of is leeg."))
    return issues


def check_overzicht(rw: dict) -> list[Issue]:
    t = _norm(rw.get("overzicht"))
    if not t:
        return []
    issues = []
    wc = word_count(t)
    if not (55 <= wc <= 65):
        issues.append(Issue("overzicht", HARD, "lengte_woorden",
                            f"{wc} woorden; moet 55-65 zijn."))
    if not _startswith_ci(t, "wil je"):
        issues.append(Issue("overzicht", HARD, "opening",
                            'moet beginnen met een vraag die start met "Wil je …".'))
    if _BULLET_PREFIX_RE.search(t):
        issues.append(Issue("overzicht", HARD, "opsomming",
                            "mag geen opsomming/bullets bevatten."))
    return issues


def check_inleiding(rw: dict) -> list[Issue]:
    t = _norm(rw.get("inleiding"))
    if not t:
        return []
    wc = word_count(t)
    if not (180 <= wc <= 210):
        return [Issue("inleiding", HARD, "lengte_woorden",
                      f"{wc} woorden; moet 180-210 zijn.")]
    return []


def check_modules(rw: dict) -> list[Issue]:
    mods = _modules(rw)
    issues = []
    if not (4 <= len(mods) <= 6):
        issues.append(Issue("modules", HARD, "modules_aantal",
                            f"{len(mods)} modules; moet 4-6 zijn."))
    bullet_counts = []
    for idx, m in enumerate(mods, start=1):
        bullets = [b for b in (m.get("bullets") or []) if isinstance(b, str) and b.strip()]
        n = len(bullets)
        bullet_counts.append(n)
        if not (3 <= n <= 6):
            issues.append(Issue("modules", HARD, "bullets_aantal",
                                f"module {idx} heeft {n} sub-bullets; moet 3-6 zijn."))
    if len(bullet_counts) >= 2 and len(set(bullet_counts)) == 1:
        issues.append(Issue("modules", HARD, "bullets_variatie",
                            "aantal sub-bullets moet variëren tussen modules; nu overal gelijk."))
    return issues


def check_doelgroep(rw: dict) -> list[Issue]:
    t = _norm(rw.get("doelgroep"))
    if not t:
        return []
    issues = []
    if not _startswith_ci(t, "deze training is voor"):
        issues.append(Issue("doelgroep", HARD, "opening",
                            'moet beginnen met "Deze training is voor …".'))
    # "professionals" stond hier als losse doelgroep-regel; die geldt inmiddels voor élk
    # kopje en zit in check_verboden_woorden, inclusief de uitzondering op de trainingstitel.
    if sentence_count(t) > 1:
        issues.append(Issue("doelgroep", FLAG, "een_zin", "moet één compacte zin zijn."))
    return issues


def check_voorkennis(rw: dict) -> list[Issue]:
    t = _norm(rw.get("voorkennis"))
    if not t:
        return []
    if sentence_count(t) > 1:
        return [Issue("voorkennis", FLAG, "een_zin", "moet één compacte zin zijn.")]
    return []


def check_doelen(rw: dict) -> list[Issue]:
    bullets = _doelen(rw)
    issues = []
    if not (4 <= len(bullets) <= 5):
        issues.append(Issue("doelen", HARD, "aantal", f"{len(bullets)} doelen; moet 4-5 zijn."))
    for idx, b in enumerate(bullets, start=1):
        first = b.strip().split()[0] if b.strip().split() else ""
        if first and not first[0].isupper():
            issues.append(Issue("doelen", HARD, "hoofdletter",
                                f"doel {idx} begint niet met een hoofdletter."))
        if not _TE_INFINITIEF_RE.search(b):
            issues.append(Issue("doelen", HARD, "geen_te_infinitief",
                                f"doel {idx} staat niet in de infinitief met 'te'; het moet "
                                f"aansluiten op \"Na deze training ben je in staat om:\" "
                                f"(bv. 'Dashboards te bouwen die de juiste vraag beantwoorden')."))
        if re.search(r"\binzicht toepassen\b", b, re.I):
            issues.append(Issue("doelen", FLAG, "vaag", f"doel {idx} is vaag ('inzicht toepassen')."))
        if _IN_STAAT_RE.search(b):
            issues.append(Issue("doelen", FLAG, "dubbel_in_staat",
                                f"doel {idx} herhaalt 'in staat'; dat staat al in de introzin "
                                f"(\"... ben je in staat om in staat te zijn om ...\")."))
    return issues


def check_kortste_omschrijving(rw: dict) -> list[Issue]:
    t = _norm(rw.get("kortste_omschrijving"))
    if not t:
        return []
    issues = []
    n = len(t)
    if n > 200:
        issues.append(Issue("kortste_omschrijving", HARD, "lengte_tekens",
                            f"{n} tekens; mag maximaal 200 zijn."))
    if not _startswith_ci(t, "wil je"):
        issues.append(Issue("kortste_omschrijving", HARD, "opening",
                            'moet beginnen met een vraag die start met "Wil je …".'))
    return issues


def check_vervolgstappen(rw: dict, ctx: dict | None) -> list[Issue]:
    titels = _titels(rw)
    catalog = (ctx or {}).get("catalog_titles")
    if catalog is None:
        if titels:
            return [Issue("vervolgstappen", FLAG, "catalogus_ontbreekt",
                          "geen catalogus geladen; titels niet te valideren.")]
        return []
    catalog_norm = {c.strip().lower() for c in catalog}
    issues = []
    for titel in titels:
        if titel.strip().lower() not in catalog_norm:
            issues.append(Issue("vervolgstappen", HARD, "titel_onbekend",
                                f"'{titel}' staat niet in de catalogus; verzin geen titels."))
    return issues


def check_verboden_woorden(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """De verbodslijst uit humanisering_nl.md Sectie D.

    Draait bewust alléén over de prozavelden, dus zonder `nieuwe_titel`: 18 van de 779
    catalogustitels bevatten "Professional" ("Training PHP Professional", "Training C#
    Professional"). Een harde titelcheck zou die trainingen onherstelbaar laten falen.

    Om dezelfde reden degradeert het verbod naar een flag zodra de trainingsnaam zelf het
    woord bevat: zo'n training moet zichzelf in de lopende tekst kunnen noemen.
    """
    naam = (ctx or {}).get("naam") or ""
    prof_severity = FLAG if re.search(r"professional", naam, re.I) else HARD
    regels = (
        (re.compile(r"\bprofessionals?\b", re.I), prof_severity, "professionals",
         'gebruik het woord "professional(s)" niet; schrijf waar iemand naartoe wil.'),
        (re.compile(r"\bje\s+hou[dt]t?\s+je\s+bezig\s+met\b", re.I), HARD, "bezig_met",
         '"je houdt je bezig met" zegt niets; noem de handeling zelf.'),
        (re.compile(r"\bmeetings?\b", re.I), FLAG, "meeting",
         'vermijd "meeting"; gebruik "overleg", "sessie" of "bijeenkomst".'),
    )
    issues = []
    for section, text in _all_text_fields(rw):
        for rx, severity, code, boodschap in regels:
            if rx.search(text):
                issues.append(Issue(section, severity, code, boodschap))
    return issues


def check_soortwoorden(rw: dict) -> list[Issue]:
    """Niks heet nog een opleiding of een cursus -- alles is een training.

    De brontekst zit er vol mee, dus dit is precies het soort woord dat de schrijver
    ongemerkt overneemt. Hard, ook in de titel. "Examentraining" mag wel.
    """
    issues = []
    velden = _all_text_fields(rw) + [("nieuwe_titel", _norm(rw.get("nieuwe_titel")))]
    for section, text in velden:
        m = _SOORTWOORD_RE.search(text or "")
        if m:
            issues.append(Issue(section, HARD, "soortwoord",
                                f"gebruikt '{m.group(0)}'; noem het een training."))
    return issues


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def check_rewrite(rewrite: dict, ctx: dict | None = None) -> list[Issue]:
    """Draait alle checks en geeft één gecombineerde issue-lijst terug (HARD + FLAG)."""
    rw = rewrite or {}
    issues: list[Issue] = []
    issues += check_presence(rw)
    issues += check_overzicht(rw)
    issues += check_inleiding(rw)
    issues += check_modules(rw)
    issues += check_doelgroep(rw)
    issues += check_voorkennis(rw)
    issues += check_doelen(rw)
    issues += check_kortste_omschrijving(rw)
    issues += check_vervolgstappen(rw, ctx)
    issues += check_soortwoorden(rw)
    issues += check_verboden_woorden(rw, ctx)
    issues += check_generic(rw)
    return issues


if __name__ == "__main__":
    # Mini-demo (zonder API-key). Voer test_rewrite.py uit voor de echte tests.
    demo = {
        "overzicht": "Wil je " + "woord " * 58 + "?",
        "inleiding": "zin " * 195,
        "modules": {"modules": [
            {"titel": "M1", "bullets": ["a", "b", "c"]},
            {"titel": "M2", "bullets": ["a", "b", "c", "d"]},
            {"titel": "M3", "bullets": ["a", "b", "c"]},
            {"titel": "M4", "bullets": ["a", "b", "c", "d", "e"]},
        ]},
        "doelgroep": "Deze training is voor professionals die data beter willen benutten.",
        "voorkennis": "Specifieke voorkennis voor het volgen van deze training is niet noodzakelijk.",
        "doelen": ["Dashboards te bouwen die de juiste vraag beantwoorden",
                   "Datasets op te schonen en samen te voegen voor analyse",
                   "Trends te analyseren en te vertalen naar keuzes",
                   "Resultaten te presenteren aan je team"],
        "vervolgstappen_titels": ["Training Power BI"],
        "kortste_omschrijving": "Wil je slimmer met data werken en betere keuzes maken?",
        "nieuwe_titel": "Training Data",
    }
    ctx = {"catalog_titles": {"Training Power BI"}, "naam": "Training Data"}
    for issue in check_rewrite(demo, ctx):
        print(issue)
    print("hard fails:", len(hard_fails(check_rewrite(demo, ctx))))

    # Dezelfde tekst, maar nu heet de training zelf "Professional": het verbod degradeert
    # naar een flag, zodat de training zichzelf mag noemen.
    ctx_prof = dict(ctx, naam="Training PHP Professional")
    print("hard fails met 'Professional' in de titel:",
          len(hard_fails(check_rewrite(demo, ctx_prof))))
