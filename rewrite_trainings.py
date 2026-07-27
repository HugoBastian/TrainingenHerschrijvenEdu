"""
rewrite_trainings.py
====================
Herschrijft trainingen naar de nieuwe stijl (9 kopjes), op basis van de brontekst
+ het score-oordeel + de reviewer-besluiten. Hybride opzet, spiegelbeeld van
score_trainings.py:

  - Python assembleert de vaste sjabloon-secties (Opzet, Programma-openingszin,
    Voorkennis-fallback, Vervolgtraining-boilerplate) en de catalogus-titels.
  - De LLM schrijft ALLEEN de generatieve secties via het tool `submit_rewrite`.
  - Een deterministische code-check (rewrite_checks.py) bewaakt lengte/format/placeholders.
  - Een judge-LLM oordeelt inhoudelijk (feitgetrouwheid, persona, per-sectie) en routeert.

Ontwerpprincipe (zelfde DNA als de scorer): "LLM schrijft/oordeelt, Python assembleert
en beslist". Gecachete spec-prefix, gestructureerde tool-output, append/skip/resume-harness.

Gebruik:
    python rewrite_trainings.py --scored trainingen_scored_Top15_WebSearch.xlsx \
        --source /pad/TrainingenLijst_50.xlsx --out-dir herschreven --limit 5

Status: WALKING SKELETON. De catalogus (vervolgtraining_catalog.json) en een goud-corpus
moeten nog gevuld worden; zie het plan.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

# Deze herschrijf-module hergebruikt de content-ingestie van de scorer
# (parse_content / build_source_text / extract_days / make_client / read_input) zodat
# schrijver en scorer EXACT dezelfde brontekst zien. score_trainings.py leeft in het
# scoring-project (een aparte map). Standaard zoeken we het als zustermap onder
# .../Eduvision/; override met de omgevingsvariabele SCORE_TRAININGEN_DIR.
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_SCORE_DIR = os.path.normpath(
    os.path.join(_HERE, "..", "Trainingen Scoren", "TrainingenScorenEdu"))
_SCORE_DIR = os.environ.get("SCORE_TRAININGEN_DIR", _DEFAULT_SCORE_DIR)
if _SCORE_DIR and _SCORE_DIR not in sys.path:
    sys.path.insert(0, _SCORE_DIR)
try:
    from score_trainings import (
        parse_content, build_source_text, extract_days, make_client,
        read_input as read_source_input,
    )
except ModuleNotFoundError as e:  # pragma: no cover
    raise ModuleNotFoundError(
        "Kon score_trainings.py niet vinden. Zet de omgevingsvariabele "
        f"SCORE_TRAININGEN_DIR naar de map met score_trainings.py (geprobeerd: {_SCORE_DIR})."
    ) from e

import rewrite_checks as checks

# ---------------------------------------------------------------------------
# 1. CONFIG (tune-knoppen bovenaan, net als de scorer)
# ---------------------------------------------------------------------------

MODEL = "claude-opus-4-8"          # generatie profiteert van Opus; makkelijk te wisselen
MAX_TOKENS = 16000
THINKING = {"type": "adaptive"}    # adaptieve thinking voor schrijf-/oordeelskwaliteit
MAX_REVISIONS = 2                  # code-check + judge revisies vóór mens-wachtrij
N_VERVOLG = 4                      # aantal vervolgtrainingen dat de retrieval kiest

# specs + catalogus liggen naast dit script (resolven onafhankelijk van de CWD)
SCHRIJFSPEC = os.path.join(_HERE, "schrijfspec_herschrijven_v1.md")
HUMANISERING = os.path.join(_HERE, "humanisering_nl.md")
BEOORDELINGSSPEC = os.path.join(_HERE, "beoordelingsspec_herschrijven_v1.md")
CATALOG_PATH = os.path.join(_HERE, "vervolgtraining_catalog.json")

# statussen voor routing
APPROVED = "approved"
NEEDS_REVISION = "needs-revision"
HUMAN_QUEUE = "human-queue"

# ---------------------------------------------------------------------------
# 2. VASTE SJABLOONTEKSTEN (de "template" — code voegt deze in, niet de LLM)
# ---------------------------------------------------------------------------

PROGRAMMA_OPENING = (
    "Tijdens de Training {naam} komen in basis onderstaande onderwerpen aan bod. "
    "Afhankelijk van ontwikkelingen op het vakgebied, kan de feitelijke trainingsinhoud "
    "hier echter van afwijken. Bel ons gerust voor meer informatie over de actuele inhoud."
)

OPZET_ALINEA_1 = (
    "De training is interactief en praktijkgericht opgezet. Je werkt actief aan herkenbare "
    "situaties, met veel ruimte voor vragen en eigen voorbeelden. Door te oefenen en "
    "bespreken leer je hoe {invulling}."
)
OPZET_ALINEA_2 = (
    "De training wordt verzorgd door trainers uit de praktijk, die ervaring hebben in "
    "verschillende organisatiecontexten. We houden altijd rekening met jouw verwachtingen, "
    "zodat de training aansluit bij wat voor jou relevant is."
)

VOORKENNIS_FALLBACK = "Specifieke voorkennis voor het volgen van deze training is niet noodzakelijk."

DOELEN_INTRO = "Na deze training heb je handvatten om:"

VERVOLG_INTRO = (
    "Binnen dit vakgebied beschikken wij over ruime praktijkervaring en specialistische "
    "kennis. Zoek je meer diepgang of een andere insteek? Neem gerust contact met ons op "
    "voor een vrijblijvende verkenning. We denken graag met je mee.\n\n"
    "Er zijn verschillende vervolgtrainingen die aansluiten op specifieke onderwerpen, "
    "toepassingen en werkcontexten. Zo bieden we onder andere:"
)
VERVOLG_AFSLUITER = (
    "Zo kies je een vervolgstap die past bij jouw rol, interesses en werksituatie. Wil je "
    "verder verdiepen, verbreden of juist werken aan een specifieke vraag of eigen casus "
    "binnen je organisatie, dan denken we graag met je mee. Neem gerust contact met ons op "
    "om te verkennen welke vorm van training het beste aansluit bij jouw praktijk."
)


# ---------------------------------------------------------------------------
# 3. CATALOGUS (Kopje 8) — laden + eenvoudige retrieval
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]{3,}")


def _tokens(*parts: str) -> set[str]:
    text = " ".join(p for p in parts if p).lower()
    return set(_TOKEN_RE.findall(text))


def load_catalog(path: str = CATALOG_PATH) -> list[dict]:
    """Catalogus: lijst van {titel, categorie, populariteit, omschrijving, url}.
    Ontbreekt het bestand, dan lege lijst (code-check flagt dit)."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("trainingen", [])


def select_vervolgtrainingen(catalog: list[dict], titel: str, kern: str,
                             n: int = N_VERVOLG) -> list[str]:
    """Naïeve retrieval: keyword-overlap met titel/kern, meest-gegeven eerst.
    Later te vervangen door semantische retrieval. Verzint nooit titels."""
    want = _tokens(titel, kern)
    scored = []
    for entry in catalog:
        etitel = str(entry.get("titel", "")).strip()
        if not etitel or etitel.strip().lower() == (titel or "").strip().lower():
            continue
        overlap = len(want & _tokens(etitel, entry.get("omschrijving", ""),
                                     entry.get("categorie", "")))
        pop = int(entry.get("populariteit", 0) or 0)
        scored.append((overlap, pop, etitel))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [t[2] for t in scored[:n]]


def catalog_titles(catalog: list[dict]) -> set[str]:
    return {str(e.get("titel", "")).strip() for e in catalog if e.get("titel")}


# ---------------------------------------------------------------------------
# 4. INFO-PASSING: scorer-rij + brontekst -> RewriteBriefing
# ---------------------------------------------------------------------------

def _split_pipe(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if val is None or (isinstance(val, float)):
        return []
    return [p.strip() for p in str(val).split(" | ") if p.strip()]


def _parse_acties(actie_actie: Any, actie_besluit: Any) -> tuple[list[str], str]:
    """Koppelt de genummerde actualiteit-acties aan de (vrije-tekst) reviewer-besluiten.
    Geeft (goedgekeurde actie-teksten, ruwe voorwaarden-tekst) terug.
    Alleen goedgekeurde nummers gaan mee; de ruwe besluit-tekst gaat als voorwaarden mee."""
    text = "" if actie_actie is None else str(actie_actie)
    genummerd: dict[int, str] = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
        if m:
            genummerd[int(m.group(1))] = m.group(2).strip()
    besluit = "" if actie_besluit is None else str(actie_besluit).strip()
    if not besluit:  # geen besluit-kolom (oudere sheet): neem alle acties mee
        approved_nums = set(genummerd)
    else:
        approved_nums = {int(x) for x in re.findall(r"\d+", besluit)}
    approved = [genummerd[n] for n in sorted(approved_nums) if n in genummerd]
    return approved, besluit


@dataclass
class RewriteBriefing:
    training_id: Any
    titel: str
    persona: str
    dagen: int | None
    kern: str
    verdict: str
    actualiteit_type: str
    source_text: str
    bruikbaar: list[str] = field(default_factory=list)
    strippen: list[str] = field(default_factory=list)
    gaten: list[str] = field(default_factory=list)
    approved_acties: list[str] = field(default_factory=list)
    reviewer_condities: str = ""
    rewrite_guidance: str = ""
    menselijke_input_nodig: bool = False

    @property
    def thin(self) -> bool:
        return self.verdict in ("dun", "redelijk")

    @property
    def route_out(self) -> str | None:
        """Harde routes die NIET de auto-herschrijving in gaan."""
        if self.actualiteit_type == "structureel":
            return "structurele actualiteitsbreuk — beslissing nodig"
        if self.verdict == "onbruikbaar":
            return "verdict onbruikbaar — te weinig bron"
        if self.menselijke_input_nodig:
            return "scorer markeerde menselijke_input_nodig"
        return None


def build_briefing(scored: dict, source_content: dict, naam: str) -> RewriteBriefing:
    dagen = extract_days(source_content, scored.get("aantal_dagen_bron"))
    approved, condities = _parse_acties(scored.get("actualiteit_actie"),
                                        scored.get("actie_besluit"))
    return RewriteBriefing(
        training_id=scored.get("training_id"),
        titel=naam,
        persona=str(scored.get("vermoedelijk_persona", "") or "").strip() or "B",
        dagen=dagen,
        kern=str(scored.get("kern", "") or ""),
        verdict=str(scored.get("verdict", "") or ""),
        actualiteit_type=str(scored.get("actualiteit_type", "") or "none"),
        source_text=build_source_text(source_content, naam),
        bruikbaar=_split_pipe(scored.get("bruikbaar")),
        strippen=_split_pipe(scored.get("strippen")),
        gaten=_split_pipe(scored.get("gaten")),
        approved_acties=approved,
        reviewer_condities=condities,
        rewrite_guidance=str(scored.get("rewrite_guidance", "") or ""),
        menselijke_input_nodig=bool(scored.get("menselijke_input_nodig")),
    )


# ---------------------------------------------------------------------------
# 5. HET SCHRIJF-TOOL (dwingt de generatieve secties af)
# ---------------------------------------------------------------------------

SUBMIT_REWRITE = {
    "name": "submit_rewrite",
    "description": "Lever de herschreven, generatieve kopjes. De code voegt de vaste "
                   "sjabloonteksten (Opzet-alinea's, Programma-openingszin, Vervolgtraining, "
                   "catalogus-titels) zelf in. Schrijf in 'je'-vorm, geen marketingtaal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "korte_omschrijving": {"type": "string",
                "description": "Kopje 1. Één alinea, 55-65 woorden, begint met 'Wil je …'. Geen bullets."},
            "algemene_omschrijving": {"type": "string",
                "description": "Kopje 2. 180-210 woorden, verdiepend op kopje 1."},
            "programma": {
                "type": "object",
                "description": "Kopje 3. 4-6 modules; per module 3-6 sub-bullets, aantal moet variëren.",
                "properties": {
                    "modules": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "titel": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["titel", "bullets"],
                        },
                    },
                },
                "required": ["modules"],
            },
            "opzet_invulling": {"type": "string",
                "description": "Kopje 4. Alleen de [....]-invulling: één woord of enkele woorden."},
            "doelgroep": {"type": "string",
                "description": "Kopje 5. Één zin, begint met 'Deze training is voor …'. Geen functietitels/'professionals'."},
            "voorkennis": {"type": "string",
                "description": "Kopje 6. Één zin. Laat leeg als geen voorkennis nodig is (code plaatst de fallbackzin)."},
            "doelen": {"type": "array", "items": {"type": "string"},
                "description": "Kopje 7. 4-5 doelen, elk begint met een werkwoord + hoofdletter (zonder de vaste introzin)."},
            "kortste_omschrijving": {"type": "string",
                "description": "Kopje 9. Max 200 tekens, begint met 'Wil je …'. Ingedikte versie van kopje 1."},
            "notities": {"type": "string",
                "description": "Optioneel: signaleer 'thin' (dunne bron, veel geconstrueerd) of een structurele twijfel."},
        },
        "required": ["korte_omschrijving", "algemene_omschrijving", "programma",
                     "opzet_invulling", "doelgroep", "doelen", "kortste_omschrijving"],
    },
}

SUBMIT_JUDGMENT = {
    "name": "submit_judgment",
    "description": "Lever het inhoudelijke oordeel over het concept. De code-check op lengte/"
                   "format is al gedaan; oordeel over feitgetrouwheid, persona/toon en per sectie.",
    "input_schema": {
        "type": "object",
        "properties": {
            "feitgetrouw": {
                "type": "object",
                "properties": {
                    "pass": {"type": "boolean"},
                    "problemen": {"type": "array", "items": {"type": "string"}},
                    "thin": {"type": "boolean"},
                },
                "required": ["pass", "problemen", "thin"],
            },
            "persona_toon": {
                "type": "object",
                "properties": {"pass": {"type": "boolean"}, "reden": {"type": "string"}},
                "required": ["pass", "reden"],
            },
            "verdict": {"type": "string", "enum": [APPROVED, NEEDS_REVISION, HUMAN_QUEUE]},
            "revisie_notities": {"type": "array", "items": {"type": "string"},
                "description": "Bij needs-revision: per kopje één concrete, atomaire instructie."},
            "human_reden": {"type": "string"},
            "judge_confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["feitgetrouw", "persona_toon", "verdict"],
    },
}


# ---------------------------------------------------------------------------
# 6. PROMPTS (gecachete spec-prefix + korte werkinstructie)
# ---------------------------------------------------------------------------

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def build_writer_system() -> list[dict]:
    prefix = (_read(SCHRIJFSPEC) + "\n\n---\n\n" + _read(HUMANISERING))
    instr = ("Je herschrijft één training naar de nieuwe stijl. Volg de schrijfspec hierboven "
             "letterlijk (lengtes, verplichte openingszinnen, persona-toon, 'je'-vorm). Schrijf "
             "ALLEEN de generatieve kopjes en roep tot slot het tool `submit_rewrite` aan. Verzin "
             "geen feiten (versies/vendors/cijfers) die niet in de bron of de feiten staan.")
    return [{"type": "text", "text": instr + "\n\n---\n\n" + prefix,
             "cache_control": {"type": "ephemeral"}}]


def build_judge_system() -> list[dict]:
    prefix = _read(BEOORDELINGSSPEC)
    return [{"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}}]


def build_writer_user(b: RewriteBriefing) -> str:
    dagen = str(b.dagen) if b.dagen is not None else "ONBEKEND (schat plausibel)"
    acties = "\n".join(f"- {a}" for a in b.approved_acties) or "(geen)"
    return (
        f"Titel: {b.titel}\n"
        f"Persona: {b.persona}\n"
        f"Aantal dagen: {dagen}\n"
        f"Verdict scorer: {b.verdict}{'  (THIN: markeer constructie)' if b.thin else ''}\n"
        f"Kern: {b.kern}\n\n"
        f"Te verwerken feiten (bruikbaar):\n" + ("\n".join(f"- {x}" for x in b.bruikbaar) or "(geen)") + "\n\n"
        f"Weglaten (strippen):\n" + ("\n".join(f"- {x}" for x in b.strippen) or "(geen)") + "\n\n"
        f"Gaten (vul plausibel waar afleidbaar):\n" + ("\n".join(f"- {x}" for x in b.gaten) or "(geen)") + "\n\n"
        f"Goedgekeurde actualiteit-acties (refresh):\n{acties}\n"
        f"Reviewer-voorwaarden: {b.reviewer_condities or '(geen)'}\n\n"
        f"Rewrite-guidance: {b.rewrite_guidance or '(geen)'}\n\n"
        f"Brontekst:\n{b.source_text}"
    )


def build_judge_user(b: RewriteBriefing, document: dict) -> str:
    return (
        f"Persona: {b.persona}\n"
        f"Feiten (bruikbaar): " + (" | ".join(b.bruikbaar) or "(geen)") + "\n"
        f"Goedgekeurde actualiteit-acties: " + (" | ".join(b.approved_acties) or "(geen)") + "\n\n"
        f"CONCEPT (9 kopjes):\n{render_document(document)}"
    )


# ---------------------------------------------------------------------------
# 7. API-CALL (tool-output, retry met budgetverdubbeling; zelfde geest als de scorer)
# ---------------------------------------------------------------------------

def _extract_tool_input(response, tool_name: str) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    return None


def _call_tool(client, system, user_text: str, tools: list[dict], tool_name: str,
               max_tokens: int = MAX_TOKENS) -> dict | None:
    """Roept het model tot het `tool_name` aanroept. Verdubbelt budget bij afkapping."""
    messages = [{"role": "user", "content": user_text}]
    budget = max_tokens
    for _ in range(3):
        resp = client.messages.create(
            model=MODEL, max_tokens=budget, system=system,
            messages=messages, tools=tools, thinking=THINKING,
        )
        tool_input = _extract_tool_input(resp, tool_name)
        if tool_input is not None:
            return tool_input
        if resp.stop_reason == "max_tokens":
            budget *= 2
            continue
        # tekst zonder tool-aanroep -> voer terug + duw aan
        messages = messages + [
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": f"Roep nu het tool `{tool_name}` aan met je resultaat."},
        ]
    return None


def rewrite_input_complete(inp: dict) -> bool:
    if not isinstance(inp, dict):
        return False
    for k in ("korte_omschrijving", "algemene_omschrijving", "opzet_invulling",
              "doelgroep", "kortste_omschrijving"):
        if not str(inp.get(k, "")).strip():
            return False
    prog = inp.get("programma") or {}
    if not (isinstance(prog, dict) and prog.get("modules")):
        return False
    return bool(inp.get("doelen"))


# ---------------------------------------------------------------------------
# 8. ASSEMBLAGE (LLM-secties + vaste template + catalogus -> volledig document)
# ---------------------------------------------------------------------------

def assemble_document(writer_out: dict, b: RewriteBriefing, titels: list[str]) -> dict:
    """Bouwt het complete 9-kopjes-document; vaste teksten door de code ingevoegd."""
    modules = (writer_out.get("programma") or {}).get("modules", [])
    voorkennis = str(writer_out.get("voorkennis", "") or "").strip() or VOORKENNIS_FALLBACK
    return {
        "korte_omschrijving": str(writer_out.get("korte_omschrijving", "")).strip(),
        "algemene_omschrijving": str(writer_out.get("algemene_omschrijving", "")).strip(),
        "programma": {
            "opening": PROGRAMMA_OPENING.format(naam=b.titel),
            "modules": modules,
        },
        "opzet": (
            OPZET_ALINEA_1.format(invulling=str(writer_out.get("opzet_invulling", "")).strip()
                                  or "je dit toepast in de praktijk")
            + "\n\n" + OPZET_ALINEA_2
        ),
        "doelgroep": str(writer_out.get("doelgroep", "")).strip(),
        "voorkennis": voorkennis,
        "doelen": {"intro": DOELEN_INTRO, "bullets": writer_out.get("doelen", [])},
        "vervolgtraining": {
            "intro": VERVOLG_INTRO,
            "titels": titels,
            "afsluiter": VERVOLG_AFSLUITER,
        },
        "kortste_omschrijving": str(writer_out.get("kortste_omschrijving", "")).strip(),
    }


def build_check_input(writer_out: dict, titels: list[str]) -> dict:
    """Platte structuur voor rewrite_checks (op de door de LLM geschreven velden)."""
    return {
        "korte_omschrijving": writer_out.get("korte_omschrijving"),
        "algemene_omschrijving": writer_out.get("algemene_omschrijving"),
        "programma": writer_out.get("programma"),
        "opzet_invulling": writer_out.get("opzet_invulling"),
        "doelgroep": writer_out.get("doelgroep"),
        "voorkennis": writer_out.get("voorkennis"),
        "doelen": writer_out.get("doelen"),
        "vervolgtraining_titels": titels,
        "kortste_omschrijving": writer_out.get("kortste_omschrijving"),
    }


def render_document(doc: dict) -> str:
    """Leesbare platte-tekst weergave voor judge + review."""
    prog = doc["programma"]
    prog_lines = [prog["opening"]]
    for m in prog["modules"]:
        prog_lines.append(f"• {m.get('titel','')}")
        for bul in m.get("bullets", []):
            prog_lines.append(f"   - {bul}")
    doelen = doc["doelen"]
    doelen_lines = [doelen["intro"]] + [f"• {x}" for x in doelen["bullets"]]
    verv = doc["vervolgtraining"]
    verv_lines = [verv["intro"]] + [f"• {t}" for t in verv["titels"]] + [verv["afsluiter"]]
    parts = [
        ("Korte omschrijving", doc["korte_omschrijving"]),
        ("Algemene omschrijving", doc["algemene_omschrijving"]),
        ("Programma", "\n".join(prog_lines)),
        ("Opzet", doc["opzet"]),
        ("Doelgroep", doc["doelgroep"]),
        ("Voorkennis", doc["voorkennis"]),
        ("Doelen", "\n".join(doelen_lines)),
        ("Vervolgtraining", "\n".join(verv_lines)),
        ("Kortste omschrijving", doc["kortste_omschrijving"]),
    ]
    return "\n\n".join(f"## {kop}\n{tekst}" for kop, tekst in parts)


# ---------------------------------------------------------------------------
# 9. ORCHESTRATIE (write -> code-check -> judge -> revisie/route)
# ---------------------------------------------------------------------------

@dataclass
class RewriteResult:
    training_id: Any
    titel: str
    status: str                       # approved | human-queue | error
    reden: str = ""
    document: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    judgment: dict = field(default_factory=dict)
    thin: bool = False


def rewrite_one(client, b: RewriteBriefing, catalog: list[dict]) -> RewriteResult:
    # harde routes eruit (structureel / onbruikbaar / menselijke_input_nodig)
    route = b.route_out
    if route:
        return RewriteResult(b.training_id, b.titel, HUMAN_QUEUE, reden=route, thin=b.thin)

    titels = select_vervolgtrainingen(catalog, b.titel, b.kern)
    ctx = {"catalog_titles": catalog_titles(catalog) if catalog else None, "naam": b.titel}
    writer_system = build_writer_system()
    base_user = build_writer_user(b)

    notes: list[str] = []
    document: dict = {}
    last_judgment: dict = {}
    for attempt in range(MAX_REVISIONS + 1):
        user_text = base_user if not notes else base_user + "\n\n---\nHERSTEL:\n" + "\n".join(notes)
        writer_out = _call_tool(client, writer_system, user_text, [SUBMIT_REWRITE], "submit_rewrite")
        if not rewrite_input_complete(writer_out):
            notes = ["De submit_rewrite-output was onvolledig; lever alle verplichte kopjes."]
            continue

        issues = checks.check_rewrite(build_check_input(writer_out, titels), ctx)
        hard = checks.hard_fails(issues)
        if hard:
            notes = ["Los deze code-check fouten op:"] + [str(i) for i in hard]
            continue

        document = assemble_document(writer_out, b, titels)
        flags = [str(i) for i in checks.flags(issues)]

        judgment = judge_document(client, b, document)
        last_judgment = judgment
        verdict = judgment.get("verdict", HUMAN_QUEUE)
        if verdict == APPROVED:
            return RewriteResult(b.training_id, b.titel, APPROVED, reden="",
                                 document=document, flags=flags, judgment=judgment,
                                 thin=b.thin or judgment.get("feitgetrouw", {}).get("thin", False))
        if verdict == NEEDS_REVISION and attempt < MAX_REVISIONS:
            notes = ["Judge-revisie:"] + list(judgment.get("revisie_notities", []))
            continue
        # human-queue of revisies op -> mens
        reden = judgment.get("human_reden") or "judge: needs-revision na max revisies"
        return RewriteResult(b.training_id, b.titel, HUMAN_QUEUE, reden=reden,
                             document=document, flags=flags, judgment=judgment, thin=b.thin)

    return RewriteResult(b.training_id, b.titel, HUMAN_QUEUE,
                         reden="geen valide concept na max pogingen",
                         document=document, judgment=last_judgment, thin=b.thin)


def judge_document(client, b: RewriteBriefing, document: dict) -> dict:
    system = build_judge_system()
    user_text = build_judge_user(b, document)
    out = _call_tool(client, system, user_text, [SUBMIT_JUDGMENT], "submit_judgment")
    if not isinstance(out, dict) or "verdict" not in out:
        return {"verdict": HUMAN_QUEUE, "human_reden": "judge leverde geen bruikbaar oordeel"}
    return out


# ---------------------------------------------------------------------------
# 10. I/O (scored + source joinen; per-training JSON + samenvattings-xlsx)
# ---------------------------------------------------------------------------

def _load_scored(path: str):
    import pandas as pd
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def rewrite_file(scored_path: str, source_path: str, out_dir: str,
                 start: int = 0, limit: int | None = None, verbose: bool = True):
    import pandas as pd
    os.makedirs(out_dir, exist_ok=True)
    json_dir = os.path.join(out_dir, "trainingen")
    os.makedirs(json_dir, exist_ok=True)
    summary_path = os.path.join(out_dir, "herschreven_samenvatting.xlsx")

    scored = _load_scored(scored_path)
    src_df, cols = read_source_input(source_path)
    # bron indexeren op id
    src_by_id = {}
    for _, row in src_df.iterrows():
        tid = row[cols["id"]] if cols["id"] else None
        src_by_id[tid] = row

    scored = scored.iloc[start:]
    if limit:
        scored = scored.iloc[:limit]

    catalog = load_catalog()
    if verbose and not catalog:
        print(f"LET OP: {CATALOG_PATH} ontbreekt -> Vervolgtraining-titels leeg/geflagd.")
    client = make_client()

    records = []
    for i, srow in scored.iterrows():
        scored_dict = {k: srow[k] for k in scored.columns}
        tid = scored_dict.get("training_id")
        naam = str(scored_dict.get("titel", "") or "")
        if scored_dict.get("ok") is False:
            res = RewriteResult(tid, naam, "error", reden="scoring mislukt")
        else:
            src_row = src_by_id.get(tid)
            content = parse_content(src_row[cols["content"]]) if src_row is not None else {}
            if src_row is None and verbose:
                print(f"  (geen bron gevonden voor id {tid}; alleen scorer-feiten)")
            b = build_briefing(scored_dict, content, naam or (str(src_row[cols["name"]]) if src_row is not None else ""))
            res = rewrite_one(client, b, catalog)

        # per-training lossless JSON
        with open(os.path.join(json_dir, f"{tid}.json"), "w", encoding="utf-8") as f:
            json.dump({
                "training_id": tid, "titel": res.titel, "status": res.status,
                "reden": res.reden, "thin": res.thin, "flags": res.flags,
                "document": res.document, "judgment": res.judgment,
            }, f, ensure_ascii=False, indent=2)

        records.append({
            "training_id": tid, "titel": res.titel, "status": res.status,
            "reden": res.reden, "thin": res.thin,
            "n_flags": len(res.flags), "flags": " | ".join(res.flags),
            "judge_confidence": (res.judgment or {}).get("judge_confidence", ""),
            "approve_edit": "",   # reviewer vult in: approve / edit / reject
        })
        if verbose:
            print(f"[{i+1}] {naam[:45]:45} -> {res.status}"
                  + (f" ({res.reden})" if res.reden else ""))

    out = pd.DataFrame.from_records(records)
    out.to_excel(summary_path, index=False)
    if verbose:
        print(f"\nGeschreven: {summary_path} ({len(out)} rijen), JSON in {json_dir}/")
    return out


def main():
    from dotenv import load_dotenv
    load_dotenv()
    p = argparse.ArgumentParser(description="Herschrijf trainingen naar de nieuwe stijl.")
    p.add_argument("--scored", required=True, help="scorer-output xlsx (feiten + besluiten)")
    p.add_argument("--source", required=True, help="bron-xlsx met content-JSON (voor brontekst)")
    p.add_argument("--out-dir", default="herschreven")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args()
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Zet ANTHROPIC_API_KEY (in een .env-bestand of je omgeving).")
    rewrite_file(a.scored, a.source, a.out_dir, start=a.start, limit=a.limit)


if __name__ == "__main__":
    main()
