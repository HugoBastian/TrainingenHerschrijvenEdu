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
import re
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


def render_vervolgstappen(alineas: list[str], titels: list[str], afsluiter: str,
                          groepen: list[dict] | None = None) -> str:
    """Kopje 8: vaste alinea's + de catalogustitels + afsluiter.

    Twee vormen. Leverde de retrieval `groepen` ([{intro, titels}]), dan krijgt elke groep
    een eigen intro-zin boven zijn lijst -- zo staat het in de al herschreven trainingen.
    Zonder groepen valt het terug op één lijst onder de vaste aankondiging.
    """
    delen = [f"<p>{_esc(a)}</p>" for a in alineas if str(a or "").strip()]
    schone_groepen = [g for g in (groepen or [])
                      if [t for t in (g.get("titels") or []) if str(t or "").strip()]]
    if schone_groepen:
        for groep in schone_groepen:
            intro = str(groep.get("intro") or "").strip() or sjabloon.VERVOLG_LIJST_INTRO
            delen.append(f"<p>{_esc(intro)}</p>\n{_lijst(groep['titels'])}")
    elif titels:
        delen.append(f"<p>{_esc(sjabloon.VERVOLG_LIJST_INTRO)}</p>\n{_lijst(titels)}")
    if str(afsluiter or "").strip():
        delen.append(f"<p>{_esc(afsluiter)}</p>")
    return "\n\n".join(delen)


# ---------------------------------------------------------------------------
# Document -> CMS-content
# ---------------------------------------------------------------------------

# Per kopje: hoe ziet dat ene CMS-veld eruit? Zelfde renderers als `document_to_content`,
# maar dan voor één veld tegelijk. Nodig om een goedgekeurde actualisering door te voeren in
# een training die verder ongemoeid blijft: dan wil je precies dat ene veld opnieuw
# renderen en elk ander veld byte-voor-byte laten staan.
_VELD_RENDER = {
    "overzicht": lambda w, ctx: str(w or "").strip(),
    "kortste_omschrijving": lambda w, ctx: str(w or "").strip(),
    "inleiding": lambda w, ctx: render_inleiding(w),
    "doelgroep": lambda w, ctx: _paragrafen(w),
    "voorkennis": lambda w, ctx: _paragrafen(w or sjabloon.VOORKENNIS_FALLBACK),
    "doelen": lambda w, ctx: render_doelen(sjabloon.DOELEN_INTRO, list(w or [])),
    "modules": lambda w, ctx: render_modules(
        ctx.get("modules_opening") or sjabloon.modules_opening(ctx.get("titel", "")),
        (w or {}).get("modules") if isinstance(w, dict) else (w or [])),
}

VELD_NAAR_CMS = {k.veld: k.cms for k in sjabloon.KOPJES}


def render_veld(veld: str, waarde: Any, ctx: dict | None = None) -> tuple[str, str]:
    """Eén kopje -> (CMS-sleutel, CMS-waarde). Werpt KeyError bij een onbekend kopje."""
    if veld not in _VELD_RENDER:
        raise KeyError(f"kopje {veld!r} is niet los te renderen; kies uit "
                       f"{sorted(_VELD_RENDER)}")
    return VELD_NAAR_CMS[veld], _VELD_RENDER[veld](waarde, ctx or {})


# ---------------------------------------------------------------------------
# Vaste teksten verversen in bestaande content
# ---------------------------------------------------------------------------

_H3_BLOK_RE = re.compile(r"\s*<h3>.*", re.S | re.I)
_EERSTE_P_RE = re.compile(r"<p>.*?</p>", re.S | re.I)
# De invulling loopt tot de eerste punt: "... ervaar je hoe |je XML in de praktijk toepast|."
# Niet tot het eind van de alinea -- daar staat alinea 2 achteraan geplakt.
_INVULLING_RE = re.compile(r"ervaar je hoe\s+([^.]+)\.", re.I)

# Vaste alinea's van het kopje Vervolgstappen, huidig én vervallen. Herkend op hun eerste
# woorden, want de staart van zo'n alinea is vaker met de hand bijgeschaafd dan de kop.
_VERVOLG_VASTE_OPENINGEN = (
    "Binnen dit expertisegebied beschikken wij",
    "Binnen dit vakgebied beschikken wij",
    "Er zijn verschillende vervolgtrainingen",
    "Zo kies je een vervolgstap",
)


def _tekst_uit(html_fragment: str) -> str:
    """Ruwe HTML -> platte tekst, voor het herkennen van vaste alinea's."""
    plat = re.sub(r"<[^>]+>", "", html_fragment or "")
    return re.sub(r"\s+", " ", html.unescape(plat)).strip()


def ververs_vaste_teksten(content: dict, titel: str,
                          modules_nb: str = sjabloon.MODULES_NB_DEFAULT) -> tuple[dict, list[str]]:
    """Zet de vaste sjabloonteksten in bestaande content terug op de actuele versie.

    Nodig voor het `overnemen`-pad. Dat pad laat `content` bewust byte-voor-byte staan, zodat
    een goede tekst niet slechter wordt van een herschrijfronde -- maar vaste teksten zijn
    geen tekst van de schrijver. Verandert het template, dan horen ze mee te veranderen, ook
    in een training die verder niemand aanraakt. Zonder deze stap belandt een training met de
    vorige generatie boilerplate ongewijzigd in het CMS.

    Alleen de deterministische delen worden vervangen; de geschreven tekst eromheen blijft
    staan. Geeft (nieuwe content, lijst met wat er is ververst) terug.
    """
    nieuw = dict(content or {})
    gewijzigd: list[str] = []

    def _zet(sleutel: str, waarde: str, label: str):
        if nieuw.get(sleutel) != waarde and waarde:
            nieuw[sleutel] = waarde
            gewijzigd.append(label)

    # Deelnamecertificaat: volledig vast, geen variabelen.
    _zet("certification", f"<p>{_esc(sjabloon.CERTIFICATIE)}</p>", "Deelnamecertificaat")

    # Inleiding: alles vanaf de <h3> is het bedrijfstrainingblok en wordt vervangen. De
    # geschreven inleiding erboven blijft ongemoeid.
    intro = nieuw.get("intro") or ""
    if "<h3" in intro.lower():
        geschreven = _H3_BLOK_RE.sub("", intro).strip()
        _zet("intro", "\n".join([
            geschreven, "",
            f"<h3>{_esc(sjabloon.BEDRIJFSTRAINING_KOP)}</h3>",
            f"<p>{_esc(sjabloon.BEDRIJFSTRAINING_TEKST)}</p>",
        ]).strip(), "bedrijfstrainingblok")

    # Modules: alleen de openingszin (de eerste <p>), niet de modulelijst eronder.
    modules = nieuw.get("modules") or ""
    if _EERSTE_P_RE.search(modules):
        opening = sjabloon.modules_opening(titel, modules_nb)
        _zet("modules", _EERSTE_P_RE.sub(f"<p>{_esc(opening)}</p>", modules, count=1),
             "Modules-openingszin")

    # Aanpak: twee vaste alinea's rond één geschreven invulling. Die invulling zit achter
    # "ervaar je hoe ..." en is het enige wat we willen behouden.
    setup = nieuw.get("setup") or ""
    if setup:
        m = _INVULLING_RE.search(_tekst_uit(setup))
        invulling = (m.group(1).strip() if m else "") or sjabloon.AANPAK_FALLBACK
        if not m:
            gewijzigd.append("LET OP: Aanpak-invulling niet teruggevonden, fallback gebruikt")
        _zet("setup", _paragrafen(
            sjabloon.AANPAK_ALINEA_1.format(invulling=invulling) + "\n\n"
            + sjabloon.AANPAK_ALINEA_2, scheiding="\n\n"), "Aanpak")

    # Vervolgstappen: de vaste alinea's ervoor en de vervallen afsluiter erachter. De
    # catalogustitels en hun groep-intro's blijven precies zoals ze staan.
    follow = nieuw.get("follow_up") or ""
    if follow and "<ul" in follow.lower():
        # Op inhoud filteren, niet op positie: een groep-intro zit soms in hetzelfde blok
        # als zijn <ul> en soms erboven, dus tellen levert de verkeerde grens op. Wat blijft
        # staan is alles wat geen vaste alinea is -- dus de titels en hun eigen intro's.
        blokken = [b for b in re.split(r"\n\s*\n", follow) if b.strip()]
        staart = [b for b in blokken
                  if not _tekst_uit(b).startswith(_VERVOLG_VASTE_OPENINGEN)]
        kop = [f"<p>{_esc(sjabloon.VERVOLG_ALINEA_1)}</p>",
               f"<p>{_esc(sjabloon.VERVOLG_ALINEA_2)}</p>"]
        _zet("follow_up", "\n\n".join(kop + staart), "Vervolgstappen-boilerplate")

    return nieuw, gewijzigd


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
                                           vervolg.get("afsluiter", ""),
                                           vervolg.get("groepen") or []),
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
    groepen = [g for g in (vervolg.get("groepen") or [])
               if [t for t in (g.get("titels") or []) if str(t or "").strip()]]
    if groepen:
        for groep in groepen:
            vervolg_regels.append(str(groep.get("intro") or "").strip()
                                  or sjabloon.VERVOLG_LIJST_INTRO)
            vervolg_regels.append("\n".join(f"* {t}" for t in groep["titels"]))
    elif vervolg.get("titels"):
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
