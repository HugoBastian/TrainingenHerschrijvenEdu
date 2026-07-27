"""
rewrite_trainings.py
====================
Herschrijft trainingen naar de nieuwe stijl (tien kopjes), op basis van de brontekst,
het score-oordeel en de besluiten van de reviewer. Hybride opzet, spiegelbeeld van
score_trainings.py:

  - Python assembleert de vaste sjabloon-secties uit `sjabloon.py` (Modules-openingszin,
    Aanpak-alinea's, bedrijfstrainingblok, Voorkennis-fallback, Vervolgstappen-boilerplate,
    Certificatie) en de catalogus-titels.
  - De LLM schrijft ALLEEN de generatieve secties via het tool `submit_rewrite`.
  - Een deterministische code-check (rewrite_checks.py) bewaakt lengte/format/placeholders.
  - Een judge-LLM oordeelt inhoudelijk (feitgetrouwheid, persona, per-sectie) en routeert.
  - `rewrite_output.py` zet het resultaat om naar de CMS-`content`-JSON.

De briefing krijgt alleen wat de reviewer heeft goedgekeurd (via besluiten.xlsx), plus een
expliciete NIET DOEN-lijst. `actualiteit_specifiek` en `actualiteit_samenvatting` blijven er
bewust buiten: dat is onderbouwing van de scorer, geen besluit.

Ontwerpprincipe (zelfde DNA als de scorer): "LLM schrijft/oordeelt, Python assembleert
en beslist". Gecachete spec-prefix, gestructureerde tool-output, append/skip/resume-harness.

Gebruik:
    python rewrite_trainings.py --scored scoresheet.xlsx --source bronsheet.xlsx \
        --besluiten besluiten.xlsx --out-dir herschreven --limit 5

Open punt: `vervolgtraining_catalog.json` ontbreekt nog, dus de Vervolgstappen-titels
blijven leeg en geflagd.
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

import besluiten as bes
import rewrite_checks as checks
import rewrite_output as uit
import sjabloon

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

# De vaste sjabloonteksten en de kopstructuur staan in sjabloon.py, afgeleid van
# `Template trainingen nieuwe opbouw.md`. Eén bron, zodat spec, schrijver, judge en
# CMS-output niet uit elkaar lopen.


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


def bepaal_dagen(source_content: dict, scored_dagen: Any = None) -> int | None:
    """Aantal dagen: de bron-JSON is gezaghebbend, daarna pas de schatting van de scorer.

    `extract_days` in het scoringsproject zoekt op de sleutel "dagen", maar in de bron heet
    hij "days" — daardoor viel dit altijd terug op de scorer-schatting. Hier eerst zelf de
    juiste sleutel proberen; het scoringsproject blijft ongemoeid.
    """
    for kandidaat in (source_content.get("days"), source_content.get("dagen")):
        if kandidaat is None or kandidaat == "":
            continue
        try:
            return int(float(kandidaat))
        except (ValueError, TypeError):
            continue
    return extract_days(source_content, scored_dagen)


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
    goedgekeurd: list[bes.Besluit] = field(default_factory=list)
    afgewezen: list[bes.Besluit] = field(default_factory=list)
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


def build_briefing(scored: dict, source_content: dict, naam: str,
                   besluiten: list[bes.Besluit] | None = None) -> RewriteBriefing:
    """Scorer-rij + brontekst + reviewer-besluiten -> alles wat de schrijver krijgt.

    LET OP — `actualiteit_specifiek` en `actualiteit_samenvatting` gaan hier BEWUST niet in.
    Dat zijn de onderbouwing van de scorer, geen besluit. Alleen wat in `actie_besluit` is
    goedgekeurd (via besluiten.xlsx) mag worden doorgevoerd; zouden die velden meegaan, dan
    kan het model alsnog een afgewezen actualisering oppikken.
    """
    goedgekeurd, afgewezen = bes.splits(besluiten or [])
    return RewriteBriefing(
        training_id=scored.get("training_id"),
        titel=naam,
        persona=str(scored.get("vermoedelijk_persona", "") or "").strip() or "B",
        dagen=bepaal_dagen(source_content, scored.get("aantal_dagen_bron")),
        kern=str(scored.get("kern", "") or ""),
        verdict=str(scored.get("verdict", "") or ""),
        actualiteit_type=str(scored.get("actualiteit_type", "") or "none"),
        source_text=build_source_text(source_content, naam),
        bruikbaar=_split_pipe(scored.get("bruikbaar")),
        strippen=_split_pipe(scored.get("strippen")),
        gaten=_split_pipe(scored.get("gaten")),
        goedgekeurd=goedgekeurd,
        afgewezen=afgewezen,
        rewrite_guidance=str(scored.get("rewrite_guidance", "") or ""),
        menselijke_input_nodig=bool(scored.get("menselijke_input_nodig")),
    )


# ---------------------------------------------------------------------------
# 5. HET SCHRIJF-TOOL (dwingt de generatieve secties af)
# ---------------------------------------------------------------------------

SUBMIT_REWRITE = {
    "name": "submit_rewrite",
    "description": "Lever de herschreven, generatieve kopjes. De code voegt de vaste "
                   "sjabloonteksten (Modules-openingszin, Aanpak-alinea's, het "
                   "bedrijfstrainingblok, Vervolgstappen, Certificatie en de "
                   "catalogus-titels) zelf in. Schrijf in 'je'-vorm, geen marketingtaal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "overzicht": {"type": "string",
                "description": "Kopje Overzicht. Één alinea, 55-65 woorden, begint met 'Wil je …'. Geen bullets."},
            "inleiding": {"type": "string",
                "description": "Kopje Inleiding. 180-210 woorden, verdiepend op Overzicht. "
                               "Schrijf NIET het bedrijfstrainingblok; dat plaatst de code."},
            "modules": {
                "type": "object",
                "description": "Kopje Modules. 4-6 modules; per module 3-6 sub-bullets, aantal moet variëren.",
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
            "doelgroep": {"type": "string",
                "description": "Kopje Doelgroep. Één zin, begint met 'Deze training is voor …'. Geen functietitels/'professionals'."},
            "voorkennis": {"type": "string",
                "description": "Kopje Voorkennis. Één zin. Laat leeg als geen voorkennis nodig is (code plaatst de fallbackzin)."},
            "aanpak_invulling": {"type": "string",
                "description": "Kopje Aanpak. Alleen de [.....]-invulling: één woord of enkele woorden."},
            "doelen": {"type": "array", "items": {"type": "string"},
                "description": "Kopje Doelen. 4-5 doelen, elk begint met een werkwoord + hoofdletter (zonder de vaste introzin)."},
            "kortste_omschrijving": {"type": "string",
                "description": "Kopje Kortste omschrijving. Max 200 tekens, begint met 'Wil je …'. Ingedikte versie van Overzicht."},
            "notities": {"type": "string",
                "description": "Optioneel: signaleer 'thin' (dunne bron, veel geconstrueerd) of een structurele twijfel."},
        },
        "required": ["overzicht", "inleiding", "modules", "doelgroep",
                     "aanpak_invulling", "doelen", "kortste_omschrijving"],
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


def _opsomming(regels, leeg: str = "(geen)") -> str:
    regels = [r for r in regels if str(r).strip()]
    return "\n".join(f"- {r}" for r in regels) or leeg


def build_writer_user(b: RewriteBriefing) -> str:
    dagen = str(b.dagen) if b.dagen is not None else "ONBEKEND (schat plausibel)"
    return (
        f"Titel: {b.titel}\n"
        f"Persona: {b.persona}\n"
        f"Aantal dagen: {dagen}\n"
        f"Verdict scorer: {b.verdict}{'  (THIN: markeer constructie)' if b.thin else ''}\n"
        f"Kern: {b.kern}\n\n"
        f"Te verwerken feiten (bruikbaar):\n{_opsomming(b.bruikbaar)}\n\n"
        f"Weglaten (strippen):\n{_opsomming(b.strippen)}\n\n"
        f"Gaten (vul plausibel waar afleidbaar):\n{_opsomming(b.gaten)}\n\n"
        "ACTUALISERINGEN — door de reviewer goedgekeurd. Voer deze uit; staat er een\n"
        "VOORWAARDE bij, dan is die bindend en gaat hij vóór de actietekst:\n"
        f"{_opsomming(x.als_instructie() for x in b.goedgekeurd)}\n\n"
        "NIET DOEN — door de reviewer afgewezen. Voer deze NIET uit, ook niet als de\n"
        "brontekst er aanleiding toe geeft:\n"
        f"{_opsomming(x.als_instructie() for x in b.afgewezen)}\n\n"
        f"Rewrite-guidance: {b.rewrite_guidance or '(geen)'}\n\n"
        f"Brontekst:\n{b.source_text}"
    )


def build_judge_user(b: RewriteBriefing, document: dict) -> str:
    return (
        f"Persona: {b.persona}\n"
        f"Feiten (bruikbaar): " + (" | ".join(b.bruikbaar) or "(geen)") + "\n\n"
        "Goedgekeurde actualiseringen (moeten verwerkt zijn):\n"
        f"{_opsomming(x.als_instructie() for x in b.goedgekeurd)}\n\n"
        "Afgewezen actualiseringen (mogen NIET terugkomen):\n"
        f"{_opsomming(x.actie for x in b.afgewezen)}\n\n"
        f"CONCEPT:\n{uit.render_markdown(document, b.titel)}"
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
    for k in ("overzicht", "inleiding", "aanpak_invulling",
              "doelgroep", "kortste_omschrijving"):
        if not str(inp.get(k, "")).strip():
            return False
    mod = inp.get("modules") or {}
    if not (isinstance(mod, dict) and mod.get("modules")):
        return False
    return bool(inp.get("doelen"))


# ---------------------------------------------------------------------------
# 8. ASSEMBLAGE (LLM-secties + vaste template + catalogus -> volledig document)
# ---------------------------------------------------------------------------

def assemble_document(writer_out: dict, b: RewriteBriefing, titels: list[str]) -> dict:
    """Bouwt het complete tien-kopjes-document; vaste teksten door de code ingevoegd."""
    invulling = str(writer_out.get("aanpak_invulling", "")).strip() or sjabloon.AANPAK_FALLBACK
    voorkennis = str(writer_out.get("voorkennis", "") or "").strip() or sjabloon.VOORKENNIS_FALLBACK
    return {
        "overzicht": str(writer_out.get("overzicht", "")).strip(),
        "inleiding": str(writer_out.get("inleiding", "")).strip(),
        "modules": {
            "opening": sjabloon.modules_opening(b.titel),
            "modules": (writer_out.get("modules") or {}).get("modules", []),
        },
        "doelgroep": str(writer_out.get("doelgroep", "")).strip(),
        "voorkennis": voorkennis,
        "aanpak": (sjabloon.AANPAK_ALINEA_1.format(invulling=invulling)
                   + "\n\n" + sjabloon.AANPAK_ALINEA_2),
        "doelen": {"intro": sjabloon.DOELEN_INTRO, "bullets": writer_out.get("doelen", [])},
        "vervolgstappen": {
            "alineas": [sjabloon.VERVOLG_ALINEA_1, sjabloon.VERVOLG_ALINEA_2],
            "titels": titels,
            "afsluiter": sjabloon.VERVOLG_AFSLUITER,
        },
        "kortste_omschrijving": str(writer_out.get("kortste_omschrijving", "")).strip(),
        "certificatie": sjabloon.CERTIFICATIE,
    }


def build_check_input(writer_out: dict, titels: list[str]) -> dict:
    """Platte structuur voor rewrite_checks (op de door de LLM geschreven velden)."""
    return {
        "overzicht": writer_out.get("overzicht"),
        "inleiding": writer_out.get("inleiding"),
        "modules": writer_out.get("modules"),
        "aanpak_invulling": writer_out.get("aanpak_invulling"),
        "doelgroep": writer_out.get("doelgroep"),
        "voorkennis": writer_out.get("voorkennis"),
        "doelen": writer_out.get("doelen"),
        "vervolgstappen_titels": titels,
        "kortste_omschrijving": writer_out.get("kortste_omschrijving"),
    }


def render_document(doc: dict, titel: str = "") -> str:
    """Leesbare weergave met de kopstructuur van het template (kop 1/2/3)."""
    return uit.render_markdown(doc, titel)


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
    toegepaste_acties: list[str] = field(default_factory=list)


def rewrite_one(client, b: RewriteBriefing, catalog: list[dict]) -> RewriteResult:
    # harde routes eruit (structureel / onbruikbaar / menselijke_input_nodig)
    route = b.route_out
    if route:
        return RewriteResult(b.training_id, b.titel, HUMAN_QUEUE, reden=route, thin=b.thin)

    # audit-spoor: welke actualiseringen zijn meegegaan, en onder welke voorwaarde
    toegepast = [f"{x.nr}. {x.actie}" + (f" [{x.voorwaarde}]" if x.voorwaarde else "")
                 for x in b.goedgekeurd]

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
                                 thin=b.thin or judgment.get("feitgetrouw", {}).get("thin", False),
                                 toegepaste_acties=toegepast)
        if verdict == NEEDS_REVISION and attempt < MAX_REVISIONS:
            notes = ["Judge-revisie:"] + list(judgment.get("revisie_notities", []))
            continue
        # human-queue of revisies op -> mens
        reden = judgment.get("human_reden") or "judge: needs-revision na max revisies"
        return RewriteResult(b.training_id, b.titel, HUMAN_QUEUE, reden=reden,
                             document=document, flags=flags, judgment=judgment, thin=b.thin,
                             toegepaste_acties=toegepast)

    return RewriteResult(b.training_id, b.titel, HUMAN_QUEUE,
                         reden="geen valide concept na max pogingen",
                         document=document, judgment=last_judgment, thin=b.thin,
                         toegepaste_acties=toegepast)


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


def load_source(source_path: str) -> tuple[dict, dict]:
    """Bronsheet -> ({id: rij}, kolomnamen). Levert de content-JSON per training."""
    src_df, cols = read_source_input(source_path)
    src_by_id = {row[cols["id"]] if cols["id"] else None: row
                 for _, row in src_df.iterrows()}
    return src_by_id, cols


def build_briefing_for(scored_df, src_by_id: dict, cols: dict, training_id: Any,
                       besluiten_per_training: dict | None = None) -> RewriteBriefing:
    """Eén rij -> briefing, zonder de hele batch te draaien.

    Het notebook en `rewrite_file` gebruiken dezelfde functie, zodat wat je in een cel
    inspecteert precies is wat de batch verstuurt.
    """
    rijen = scored_df[scored_df["training_id"] == training_id]
    if rijen.empty:
        raise KeyError(f"training_id {training_id} staat niet in het scoresheet")
    scored_dict = {k: rijen.iloc[0][k] for k in scored_df.columns}
    src_row = src_by_id.get(training_id)
    content = parse_content(src_row[cols["content"]]) if src_row is not None else {}
    naam = str(scored_dict.get("titel", "") or "")
    if not naam and src_row is not None:
        naam = str(src_row[cols["name"]])
    return build_briefing(scored_dict, content, naam,
                          (besluiten_per_training or {}).get(training_id, []))


def build_briefing_for_id(scored_path: str, source_path: str, training_id: Any,
                          besluiten_path: str | None = None) -> RewriteBriefing:
    """Zelfde als build_briefing_for, maar vanaf de bestandspaden. Doet geen API-calls."""
    src_by_id, cols = load_source(source_path)
    per_training = bes.load_besluiten(besluiten_path) if besluiten_path else {}
    return build_briefing_for(_load_scored(scored_path), src_by_id, cols,
                              training_id, per_training)


def export_goud_corpus(source_path: str, out_dir: str, verbose: bool = True) -> int:
    """Schrijft de trainingen die al in de nieuwe stijl staan (`herschreven=1`) weg.

    Referentiemateriaal om spec en judge aan te kalibreren; niet om te herschrijven.
    """
    src_df, cols = read_source_input(source_path)
    if "herschreven" not in src_df.columns:
        if verbose:
            print("bronsheet heeft geen kolom 'herschreven' -> geen goud-corpus")
        return 0
    goud_dir = os.path.join(out_dir, "goud")
    os.makedirs(goud_dir, exist_ok=True)
    n = 0
    for _, row in src_df[src_df["herschreven"] == 1].iterrows():
        tid = row[cols["id"]]
        with open(os.path.join(goud_dir, f"{tid}.json"), "w", encoding="utf-8") as f:
            json.dump({"training_id": tid, "titel": str(row[cols["name"]]),
                       "content": parse_content(row[cols["content"]])},
                      f, ensure_ascii=False, indent=2)
        n += 1
    if verbose:
        print(f"Goud-corpus: {n} trainingen in {goud_dir}/")
    return n


def _review_rij(res: RewriteResult, content: dict) -> dict:
    """Eén rij voor het review-tabblad: status + elk kopje in platte tekst."""
    rij = {
        "training_id": res.training_id, "titel": res.titel, "status": res.status,
        "reden": res.reden, "thin": res.thin,
        "n_flags": len(res.flags), "flags": " | ".join(res.flags),
        "judge_confidence": (res.judgment or {}).get("judge_confidence", ""),
        "toegepaste_acties": " | ".join(res.toegepaste_acties),
        "approve_edit": "",   # reviewer vult in: approve / edit / reject
    }
    plat = uit.content_naar_platte_tekst(content, res.titel) if content else {}
    for kopje in sjabloon.KOPJES:
        rij[kopje.kop] = plat.get(kopje.cms, "")
    return rij


def rewrite_file(scored_path: str, source_path: str, out_dir: str, *,
                 besluiten_path: str | None = None, start: int = 0,
                 limit: int | None = None, skip_herschreven: bool = True,
                 append: bool = True, skip_existing: bool = True, verbose: bool = True):
    """Herschrijft de trainingen en schrijft drie artefacten in `out_dir`.

    - trainingen/<id>.json   lossless: document + CMS-content + oordeel
    - herschreven.xlsx       tabblad `cms` (id/name/content) + tabblad `review`
    """
    import pandas as pd
    os.makedirs(out_dir, exist_ok=True)
    json_dir = os.path.join(out_dir, "trainingen")
    os.makedirs(json_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "herschreven.xlsx")

    scored = _load_scored(scored_path)
    src_by_id, cols = load_source(source_path)

    if besluiten_path is None:
        raise ValueError(
            "Geen --besluiten opgegeven. Draai eerst besluiten.write_besluiten_sheet(); "
            "zonder dat sheet is niet vast te stellen wat de reviewer heeft goedgekeurd.")
    per_training = bes.load_besluiten(besluiten_path)

    # al herschreven trainingen niet opnieuw genereren
    if skip_herschreven and "herschreven" in scored.columns:
        overslaan = scored["herschreven"] == 1
        if verbose and overslaan.any():
            print(f"{int(overslaan.sum())} trainingen met herschreven=1 overgeslagen")
        scored = scored[~overslaan]

    # hervatten: rijen die al in de output staan overslaan
    bestaand_cms, bestaand_review = None, None
    if append and os.path.exists(out_path):
        vorige = pd.read_excel(out_path, sheet_name=None)
        bestaand_cms = vorige.get("cms")
        bestaand_review = vorige.get("review")
        if skip_existing and bestaand_review is not None:
            klaar = set(bestaand_review["training_id"])
            scored = scored[~scored["training_id"].isin(klaar)]
            if verbose and klaar:
                print(f"{len(klaar)} trainingen stonden al in {out_path} -> overgeslagen")

    scored = scored.iloc[start:]
    if limit:
        scored = scored.iloc[:limit]

    catalog = load_catalog()
    if verbose and not catalog:
        print(f"LET OP: {CATALOG_PATH} ontbreekt -> Vervolgstappen-titels leeg/geflagd.")
    client = make_client() if len(scored) else None

    cms_records, review_records = [], []
    for n, (_, srow) in enumerate(scored.iterrows(), start=1):
        scored_dict = {k: srow[k] for k in scored.columns}
        tid = scored_dict.get("training_id")
        naam = str(scored_dict.get("titel", "") or "")
        src_row = src_by_id.get(tid)
        content_bron = parse_content(src_row[cols["content"]]) if src_row is not None else {}

        if scored_dict.get("ok") is False:
            res = RewriteResult(tid, naam, "error", reden="scoring mislukt")
        else:
            if src_row is None and verbose:
                print(f"  (geen bron gevonden voor id {tid}; alleen scorer-feiten)")
            if not naam and src_row is not None:
                naam = str(src_row[cols["name"]])
            b = build_briefing(scored_dict, content_bron, naam, per_training.get(tid, []))
            res = rewrite_one(client, b, catalog)

        content_uit = uit.document_to_content(res.document, content_bron) if res.document else {}

        with open(os.path.join(json_dir, f"{tid}.json"), "w", encoding="utf-8") as f:
            json.dump({
                "training_id": tid, "titel": res.titel, "status": res.status,
                "reden": res.reden, "thin": res.thin, "flags": res.flags,
                "toegepaste_acties": res.toegepaste_acties,
                "document": res.document, "content": content_uit,
                "judgment": res.judgment,
            }, f, ensure_ascii=False, indent=2)

        if res.status == APPROVED and content_uit:
            cms_records.append({"id": tid, "name": res.titel,
                                "content": json.dumps(content_uit, ensure_ascii=False)})
        review_records.append(_review_rij(res, content_uit))
        if verbose:
            print(f"[{n}/{len(scored)}] {naam[:45]:45} -> {res.status}"
                  + (f" ({res.reden})" if res.reden else ""))

    cms = pd.DataFrame.from_records(cms_records)
    review = pd.DataFrame.from_records(review_records)
    if bestaand_cms is not None:
        cms = pd.concat([bestaand_cms, cms], ignore_index=True).drop_duplicates(
            subset="id", keep="last")
    if bestaand_review is not None:
        review = pd.concat([bestaand_review, review], ignore_index=True).drop_duplicates(
            subset="training_id", keep="last")

    with pd.ExcelWriter(out_path) as writer:
        cms.to_excel(writer, sheet_name="cms", index=False)
        review.to_excel(writer, sheet_name="review", index=False)
    if verbose:
        print(f"\nGeschreven: {out_path} — cms {len(cms)} rijen, review {len(review)} rijen; "
              f"JSON in {json_dir}/")
    return review


def main():
    from dotenv import load_dotenv
    load_dotenv()
    p = argparse.ArgumentParser(description="Herschrijf trainingen naar de nieuwe stijl.")
    p.add_argument("--scored", required=True, help="scoresheet xlsx (feiten + actie_besluit)")
    p.add_argument("--source", required=True, help="bron-xlsx met content-JSON (brontekst)")
    p.add_argument("--besluiten", required=True, help="genormaliseerde besluiten.xlsx")
    p.add_argument("--out-dir", default="herschreven")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-append", action="store_true", help="overschrijf i.p.v. hervatten")
    p.add_argument("--goud", action="store_true", help="exporteer alleen het goud-corpus")
    a = p.parse_args()
    if a.goud:
        export_goud_corpus(a.source, a.out_dir)
        return
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Zet ANTHROPIC_API_KEY (in een .env-bestand of je omgeving).")
    rewrite_file(a.scored, a.source, a.out_dir, besluiten_path=a.besluiten,
                 start=a.start, limit=a.limit, append=not a.no_append)


if __name__ == "__main__":
    main()
