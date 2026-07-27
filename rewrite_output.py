"""
rewrite_output.py
=================
Zet het herschreven document om naar (a) de `content`-JSON die het CMS in gaat en
(b) een leesbaar document met de kopstructuur uit het template.

De bron-`content` heeft elf sleutels, waarvan er tien exact op de tien kopjes van
`Template trainingen nieuwe opbouw.md` mappen (zie `sjabloon.KOPJES`):

    summary          <- Overzicht             (platte tekst)
    intro            <- Inleiding             (<p> + <h3>-bedrijfstrainingblok)
    modules          <- Modules               (<p> + geneste <ul>)
    target_audience  <- Doelgroep             (<p>)
    prior_knowledge  <- Voorkennis            (<p>)
    setup            <- Aanpak                (<p>)
    objectives       <- Doelen                (<p> + <ul>)
    follow_up        <- Vervolgstappen        (<p> + <ul> + <p>)
    summary_edudex   <- Kortste omschrijving  (platte tekst)
    certification    <- Certificatie          (<p>, vaste tekst)
    days             <- ongewijzigd uit de bron

De HTML-vorm is afgekeken van de trainingen die al in de nieuwe stijl staan
(`herschreven=1`): platte tekst voor `summary`/`summary_edudex`, de rest HTML, en het
programma met geneste `<ul>` -- geen `<h3>` per module.
"""

from __future__ import annotations

import html
from typing import Any

import sjabloon

# volgorde van de sleutels in de output, gelijk aan die van de bron
CONTENT_KEYS = ("days", "intro", "setup", "modules", "summary", "follow_up",
                "objectives", "certification", "summary_edudex", "prior_knowledge",
                "target_audience")


def _esc(tekst: Any) -> str:
    """Tekst -> HTML-veilige tekst. Alleen op de inhoud, nooit op de opmaak."""
    return html.escape(str(tekst or "").strip(), quote=False)


def _blokken(tekst: Any) -> list[str]:
    return [b.strip() for b in str(tekst or "").split("\n\n") if b.strip()]


def _paragrafen(tekst: Any, scheiding: str = "\n") -> str:
    """Platte tekst met lege regels -> losse <p>-blokken."""
    return scheiding.join(f"<p>{_esc(b)}</p>" for b in _blokken(tekst))


def _lijst(items) -> str:
    regels = ["<ul>"]
    regels += [f"<li>{_esc(i)}</li>" for i in items if str(i or "").strip()]
    regels.append("</ul>")
    return "\n".join(regels)


# ---------------------------------------------------------------------------
# Per kopje: platte tekst / structuur -> HTML
# ---------------------------------------------------------------------------

def render_inleiding(tekst: Any) -> str:
    """Kopje 2: de geschreven inleiding + het vaste bedrijfstrainingblok onder een <h3>."""
    return "\n".join([
        _paragrafen(tekst),
        "",
        f"<h3>{_esc(sjabloon.BEDRIJFSTRAINING_KOP)}</h3>",
        f"<p>{_esc(sjabloon.BEDRIJFSTRAINING_TEKST)}</p>",
    ]).strip()


def render_modules(opening: str, modules: list[dict]) -> str:
    """Kopje 3: openingszin + geneste <ul> (module -> sub-bullets)."""
    regels = [f"<p>{_esc(opening)}</p>", "", "<ul>"]
    for module in modules or []:
        titel = _esc(module.get("titel", ""))
        bullets = [b for b in (module.get("bullets") or []) if str(b or "").strip()]
        if not titel and not bullets:
            continue
        regels.append(f"  <li>{titel}")
        if bullets:
            regels.append("    <ul>")
            regels += [f"      <li>{_esc(b)}</li>" for b in bullets]
            regels.append("    </ul>")
        regels.append("  </li>")
    regels.append("</ul>")
    return "\n".join(regels)


def render_doelen(intro: str, bullets: list[str]) -> str:
    """Kopje 7: vaste introzin + <ul> met de doelen."""
    return f"<p>{_esc(intro)}</p>\n{_lijst(bullets)}"


def render_vervolgstappen(alineas: list[str], titels: list[str], afsluiter: str) -> str:
    """Kopje 8: vaste alinea's + aankondiging + <ul> met catalogustitels + afsluiter."""
    delen = [f"<p>{_esc(a)}</p>" for a in alineas if str(a or "").strip()]
    if titels:
        delen.append(f"<p>{_esc(sjabloon.VERVOLG_LIJST_INTRO)}</p>\n{_lijst(titels)}")
    if str(afsluiter or "").strip():
        delen.append(f"<p>{_esc(afsluiter)}</p>")
    return "\n\n".join(delen)


# ---------------------------------------------------------------------------
# Document -> CMS-content
# ---------------------------------------------------------------------------

def document_to_content(document: dict, source_content: dict | None = None) -> dict:
    """Het tien-kopjes-document -> de CMS-`content`-dict.

    `source_content` levert alleen wat we niet herschrijven (`days`). De certificatietekst
    komt uit het sjabloon, niet uit de bron -- die is in het template vastgelegd.
    """
    bron = source_content or {}
    modules = document.get("modules") or {}
    doelen = document.get("doelen") or {}
    vervolg = document.get("vervolgstappen") or {}

    content = {
        # platte tekst, precies zoals in de bron
        "summary": str(document.get("overzicht", "") or "").strip(),
        "summary_edudex": str(document.get("kortste_omschrijving", "") or "").strip(),
        # HTML
        "intro": render_inleiding(document.get("inleiding")),
        "modules": render_modules(modules.get("opening", ""), modules.get("modules") or []),
        "target_audience": _paragrafen(document.get("doelgroep")),
        "prior_knowledge": _paragrafen(document.get("voorkennis")),
        "setup": _paragrafen(document.get("aanpak"), scheiding="\n\n"),
        "objectives": render_doelen(doelen.get("intro", ""), doelen.get("bullets") or []),
        "follow_up": render_vervolgstappen(vervolg.get("alineas") or [],
                                           vervolg.get("titels") or [],
                                           vervolg.get("afsluiter", "")),
        "certification": f"<p>{_esc(document.get('certificatie') or sjabloon.CERTIFICATIE)}</p>",
    }
    for sleutel in sjabloon.BEHOUDEN_UIT_BRON:
        if sleutel in bron:
            content[sleutel] = bron[sleutel]

    # zelfde sleutelvolgorde als de bron; onbekende extra sleutels blijven achteraan
    geordend = {k: content[k] for k in CONTENT_KEYS if k in content}
    geordend.update({k: v for k, v in content.items() if k not in geordend})
    return geordend


# ---------------------------------------------------------------------------
# Document -> leesbaar markdown (kopstructuur van het template)
# ---------------------------------------------------------------------------

def render_markdown(document: dict, titel: str) -> str:
    """Volledig document met de kopniveaus uit het template.

    Titel als kop 1, elk kopje als kop 2, het bedrijfstrainingblok als kop 3.
    Dit is wat de judge beoordeelt en wat een reviewer leest.
    """
    modules = document.get("modules") or {}
    doelen = document.get("doelen") or {}
    vervolg = document.get("vervolgstappen") or {}

    secties: dict[str, str] = {
        "overzicht": str(document.get("overzicht", "") or "").strip(),
        "inleiding": "\n\n".join(
            _blokken(document.get("inleiding"))
            + [f"### **{sjabloon.BEDRIJFSTRAINING_KOP}**", sjabloon.BEDRIJFSTRAINING_TEKST]),
        "doelgroep": str(document.get("doelgroep", "") or "").strip(),
        "voorkennis": str(document.get("voorkennis", "") or "").strip(),
        "aanpak": str(document.get("aanpak", "") or "").strip(),
        "kortste_omschrijving": str(document.get("kortste_omschrijving", "") or "").strip(),
        "certificatie": str(document.get("certificatie") or sjabloon.CERTIFICATIE),
    }

    regels = [modules.get("opening", ""), ""]
    for module in modules.get("modules") or []:
        regels.append(f"* {module.get('titel', '')}")
        regels += [f"  * {b}" for b in module.get("bullets") or []]
    secties["modules"] = "\n".join(regels).strip()

    secties["doelen"] = "\n\n".join([
        doelen.get("intro", ""),
        "\n".join(f"* {b}" for b in doelen.get("bullets") or []),
    ]).strip()

    vervolg_regels = list(vervolg.get("alineas") or [])
    if vervolg.get("titels"):
        vervolg_regels.append(sjabloon.VERVOLG_LIJST_INTRO)
        vervolg_regels.append("\n".join(f"* {t}" for t in vervolg["titels"]))
    if str(vervolg.get("afsluiter") or "").strip():
        vervolg_regels.append(vervolg["afsluiter"])
    secties["vervolgstappen"] = "\n\n".join(vervolg_regels).strip()

    delen = [f"# {titel}".rstrip()]
    for kopje in sjabloon.KOPJES:
        delen.append(f"## {kopje.kop}\n\n{secties.get(kopje.veld, '').strip()}")
    return "\n\n---\n\n".join(delen) + "\n"


def content_naar_platte_tekst(content: dict, naam: str = "") -> dict:
    """Leesbare weergave per veld -- voor het review-tabblad en voor diffen met de bron."""
    from score_trainings import clean_text
    return {k: clean_text(v, naam) if isinstance(v, str) else v
            for k, v in content.items()}
