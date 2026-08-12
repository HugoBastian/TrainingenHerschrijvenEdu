"""
besluiten.py
============
De besluitenlaag: zet de handmatig ingevulde `actie_besluit`-kolom om in expliciete,
per-actie besluiten die de schrijver ondubbelzinnig meekrijgt.

Uitgangspunt: **de structuur van `actie_besluit` ligt vast, de tekst niet.**
Elk item is `<nr> <vrije tekst>`, bij voorkeur gescheiden door een komma waar een nummer
op volgt -- maar de komma is geen eis: laat een reviewer hem weg, dan leest `koppel()` de
cel alsnog, op de nummers uit `actualiteit_actie` (zie sectie 2b).
Die structuur parsen we deterministisch. De annotatie zelf is gewoon Nederlands en
laat zich niet in een woordenlijst vangen -- "geen specifieke frameworks benoemen" is
een *voorwaarde*, "nee dat is advanced" een *afwijzing*. Dus dezelfde splitsing als
overal in dit project: **Python doet de structuur, de LLM doet het oordeel.**

    A. splitsen      deterministisch, lijnt nummers uit met `actualiteit_actie`
    B. classificeren fastpath op triviale annotaties, LLM op de rest
    C. normaliseren  besluiten.xlsx, één rij per (training_id, nr), mens corrigeert

De pipeline leest daarna alleen dat sheet. Het model classificeert; het herschrijft
niet -- de voorwaarde die de schrijver ziet is altijd de letterlijke reviewer-tekst.

Gebruik:
    import besluiten
    besluiten.check_alignment("Nieuwe lijst herschreven en dagen.xlsx")      # geen API nodig
    besluiten.write_besluiten_sheet("Nieuwe lijst herschreven en dagen.xlsx",
                                    "besluiten.xlsx")                        # hier lopen calls
    per_training = besluiten.load_besluiten("besluiten.xlsx")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 1. CONFIG
# ---------------------------------------------------------------------------

MODEL = "claude-haiku-4-5"   # classificatie, geen generatie; makkelijk op te schalen
MAX_TOKENS = 2000

# de drie besluiten
DOEN = "doen"     # actie ongewijzigd uitvoeren
NIET = "niet"     # actie niet uitvoeren
MITS = "mits"     # uitvoeren met de voorwaarde van de reviewer

# herkomst van het label
BRON_REGEL = "regel"        # exact-match fastpath, geen LLM
BRON_LLM = "llm"            # door het model geclassificeerd -- dit controleer je
BRON_HANDMATIG = "handmatig"  # door een mens gezet; wordt nooit overschreven

BESLUITEN = (DOEN, NIET, MITS)

KOLOMMEN = ["training_id", "titel", "nr", "actie", "besluit_ruw",
            "besluit", "voorwaarde", "bron", "confidence"]


class BesluitFout(ValueError):
    """De structuur van actie_besluit lijnt niet uit met actualiteit_actie."""


# ---------------------------------------------------------------------------
# 2. STAP A -- STRUCTUUR (deterministisch, geen LLM)
# ---------------------------------------------------------------------------

# Komma's komen ook binnen de vrije tekst voor ("PHP versie niet benoemen, wel
# taalfeatures toevoegen"). Splits daarom alleen op een komma die door een
# actienummer wordt gevolgd.
SPLIT_RE = re.compile(r",\s*(?=\d+\s*(?:,|$|\s))")
# Achter het nummer mag leestekenafval staan: "1." en "1)" al langer, "1:" sinds batch 2
# (training 392 opende met "1: wel containers, geen serverless").
ITEM_RE = re.compile(r"^(\d+)[.):]?\s*(.*)$", re.S)

# `actualiteit_actie` wordt door de scorer als f"{i}. {a}" weggeschreven.
ACTIE_RE = re.compile(r"^\s*(\d+)[.)]\s*(.+?)\s*$", re.M)


def _leeg(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and val != val:   # NaN
        return True
    return not str(val).strip()


def parse_acties(actualiteit_actie: Any) -> dict[int, str]:
    """De genummerde actielijst uit de scorer -> {nr: actietekst}."""
    if _leeg(actualiteit_actie):
        return {}
    return {int(m.group(1)): m.group(2).strip()
            for m in ACTIE_RE.finditer(str(actualiteit_actie))}


def split_besluit(actie_besluit: Any) -> list[tuple[int, str]]:
    """De ruwe besluit-cel -> [(nr, annotatie), ...], in de volgorde van de cel.

    Alleen structuur; de annotatie blijft onaangeraakt.
    """
    if _leeg(actie_besluit):
        return []
    items: list[tuple[int, str]] = []
    for chunk in SPLIT_RE.split(str(actie_besluit).strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = ITEM_RE.match(chunk)
        if not m:
            raise BesluitFout(f"item begint niet met een nummer: {chunk!r}")
        items.append((int(m.group(1)), m.group(2).strip()))
    return items


def align(acties: dict[int, str], items: list[tuple[int, str]]) -> list[tuple[int, str, str]]:
    """Koppelt annotaties aan acties. -> [(nr, actietekst, annotatie), ...].

    Lijnt het niet uit (aantal ongelijk, dubbel nummer, of een nummer zonder actie),
    dan is dat een harde fout: die training gaat naar de human-queue, niet naar de
    schrijver. Gokken is precies wat we hier repareren.
    """
    nrs = [nr for nr, _ in items]
    if len(nrs) != len(set(nrs)):
        raise BesluitFout(f"dubbel actienummer in besluit: {sorted(nrs)}")
    if len(nrs) != len(acties):
        raise BesluitFout(
            f"{len(nrs)} besluiten tegenover {len(acties)} genummerde acties")
    onbekend = sorted(set(nrs) - set(acties))
    if onbekend:
        raise BesluitFout(f"besluit verwijst naar niet-bestaande actie(s): {onbekend}")
    return [(nr, acties[nr], ann) for nr, ann in sorted(items)]


# ---------------------------------------------------------------------------
# 2b. STAP A ZONDER KOMMA'S
# ---------------------------------------------------------------------------

# Eén reviewer laat de scheidingskomma structureel weg: "1 prima 2 niet 3 geen versies
# benoemen". Splitsen op "een los cijfer" kan niet -- "PHP 8" en "versie 3" staan gewoon in
# de vrije tekst, en dat is nu juist waarom SPLIT_RE een komma eist.
#
# Beslisbaar wordt het pas met een gegeven dat de kommalezing niet gebruikt: we weten welke
# nummers we zoeken (uit `actualiteit_actie`) en de reviewer schrijft ze oplopend, beginnend
# aan het begin van de cel. Zoek dus geen "cijfers" maar precies die nummers, in die volgorde.
# Over de 47 ingevulde cellen van batch 1 komt elk verwacht nummer, met de scheidingskomma's
# weggehaald, precies één keer los voor: 47 van de 47 eenduidig.
#
# Blijven er tóch twee lezingen over, dan is de cel echt meerduidig en is dat een harde fout,
# net als een scheve uitlijning: die training gaat naar de mens. Gokken is precies wat deze
# module repareert.
#
# Batch 2 bevestigt dat: van de 48 ingevulde cellen leest er één op komma's, lezen er 42 op
# nummer, en vallen er vijf om. Eén daarvan was van ons ("1:" liep stuk op het leesteken,
# nu toegestaan); de andere vier zijn nummerfouten van de reviewer, en die horen ook om te
# vallen -- drie keer stond het laatste item op het vorige nummer ("1 2 3 3" bij vier acties)
# en één keer bleef de laatste actie onbeantwoord. Beide zijn positioneel wel te "repareren",
# en juist dat is de gok die hier niet thuishoort: het gaat om wel of niet uitvoeren.
LOS_GETAL_RE = re.compile(r"(?:^|(?<=[\s,;]))(\d+)[.):]?(?=$|[\s,;])")

MAX_LEZINGEN = 2   # meer dan één is al fataal; verder zoeken kost alleen tijd

# hoe `koppel` de cel uiteindelijk gelezen heeft
LEZING_KOMMAS = "kommas"
LEZING_NUMMERS = "nummers"


def _opsomming(nrs: list[int]) -> str:
    if len(nrs) == 1:
        return str(nrs[0])
    return ", ".join(str(n) for n in nrs[:-1]) + f" en {nrs[-1]}"


def _zin(nrs: list[int], enkelvoud: str, meervoud: str) -> str:
    return (f"nummer {nrs[0]} {enkelvoud}" if len(nrs) == 1
            else f"de nummers {_opsomming(nrs)} {meervoud}")


def _waarom_geen_lezing(doelen: list[int],
                        kandidaten: dict[int, list[tuple[int, int]]]) -> str:
    """Zegt wát er aan de nummering mankeert, niet dát er iets aan mankeert.

    De reviewer moet zijn eigen cel kunnen repareren zonder de code te lezen. "de nummers
    [1, 2, 3, 4] staan er niet oplopend als losse getallen in" laat hem zoeken; over batch 2
    was het echte euvel drie keer "4 ontbreekt en 3 staat dubbel" -- één teken in het sheet.
    """
    redenen = []
    ontbreekt = [nr for nr in doelen if not kandidaten.get(nr)]
    dubbel = [nr for nr in doelen if len(kandidaten.get(nr, ())) > 1]
    if ontbreekt:
        redenen.append(_zin(ontbreekt, "ontbreekt", "ontbreken"))
    if dubbel:
        redenen.append(_zin(dubbel, "staat er dubbel in", "staan er dubbel in"))
    if not ontbreekt and not dubbel and all(s != 0 for s, _ in kandidaten.get(doelen[0], ())):
        redenen.append(f"de cel begint niet met nummer {doelen[0]}")
    if not redenen:   # alles staat er precies één keer, maar niet in deze volgorde
        return f"de nummers {doelen} staan er niet oplopend als losse getallen in"
    return " en ".join(redenen)


def split_zonder_kommas(actie_besluit: Any, nrs: Iterable[int]) -> list[tuple[int, str]]:
    """Dezelfde cel, maar zonder scheidingskomma's -> [(nr, annotatie), ...].

    `nrs` zijn de verwachte actienummers; alleen díe worden als scheiding herkend.
    """
    tekst = "" if _leeg(actie_besluit) else str(actie_besluit).strip()
    doelen = sorted(nrs)
    if not tekst or not doelen:
        return []

    gezocht = set(doelen)
    kandidaten: dict[int, list[tuple[int, int]]] = {}
    for m in LOS_GETAL_RE.finditer(tekst):
        waarde = int(m.group(1))
        if waarde in gezocht:
            kandidaten.setdefault(waarde, []).append((m.start(1), m.end()))

    lezingen: list[list[tuple[int, int]]] = []

    def zoek(i: int, ondergrens: int, gekozen: list[tuple[int, int]]) -> None:
        if len(lezingen) >= MAX_LEZINGEN:
            return
        if i == len(doelen):
            lezingen.append(list(gekozen))
            return
        for start, eind in kandidaten.get(doelen[i], ()):
            if start < ondergrens or (i == 0 and start != 0):
                continue   # de cel begint bij het eerste nummer, en de rest volgt oplopend
            gekozen.append((start, eind))
            zoek(i + 1, eind, gekozen)
            gekozen.pop()

    zoek(0, 0, [])
    if not lezingen:
        raise BesluitFout(_waarom_geen_lezing(doelen, kandidaten))
    if len(lezingen) > 1:
        raise BesluitFout(
            f"meerdere lezingen mogelijk voor de nummers {doelen}; zet komma's tussen de items")

    posities = lezingen[0]
    items: list[tuple[int, str]] = []
    for i, nr in enumerate(doelen):
        vanaf = posities[i][1]
        tot = posities[i + 1][0] if i + 1 < len(posities) else len(tekst)
        items.append((nr, tekst[vanaf:tot].strip().strip(",;:").strip()))
    return items


def koppel_met_lezing(acties: dict[int, str],
                      actie_besluit: Any) -> tuple[list[tuple[int, str, str]], str]:
    """Als `koppel`, maar vertelt er ook bij hoe de cel gelezen is."""
    try:
        return align(acties, split_besluit(actie_besluit)), LEZING_KOMMAS
    except BesluitFout as komma_fout:
        try:
            return align(acties, split_zonder_kommas(actie_besluit, acties)), LEZING_NUMMERS
        except BesluitFout as nummer_fout:
            # Beide meldingen, want welke van de twee de reviewer moet lezen hangt af van
            # de stijl die hij bedoelde -- en dat weten wij hier niet.
            raise BesluitFout(f"{komma_fout}; ook zonder komma's gelezen: "
                              f"{nummer_fout}") from nummer_fout


def koppel(acties: dict[int, str], actie_besluit: Any) -> list[tuple[int, str, str]]:
    """De cel -> [(nr, actietekst, annotatie), ...]. De enige ingang voor de pijplijn.

    Eerst de kommalezing, en pas als die niet uitlijnt de komma-loze: elke cel die vandaag
    werkt wordt daardoor letterlijk hetzelfde geparseerd als voorheen. Een half-gekommade cel
    ("1 prima, 2 niet 3 wel") valt vanzelf op de tweede lezing terug.
    """
    return koppel_met_lezing(acties, actie_besluit)[0]


# ---------------------------------------------------------------------------
# 3. STAP B -- CLASSIFICATIE
# ---------------------------------------------------------------------------

# Fastpath: alleen exact-match na normalisatie. Alles daarbuiten is vrije tekst en
# gaat naar het model. LET OP bij uitbreiden: "geen" hoort hier NIET bij --
# "geen specifieke frameworks benoemen" is een voorwaarde, geen afwijzing.
JA = {"", "wel", "ja", "prima", "ok", "oke", "oké", "akkoord", "goed", "doen", "klopt"}
NEE = {"niet", "nee", "nvt", "n.v.t.", "skip"}


def _normaliseer(annotatie: str) -> str:
    return annotatie.strip().lower().rstrip(".").strip()


def regel_label(annotatie: str) -> str | None:
    """Triviale annotatie -> label, buiten het model om. Anders None."""
    norm = _normaliseer(annotatie)
    if norm in JA:
        return DOEN
    if norm in NEE:
        return NIET
    return None


SUBMIT_BESLUITEN = {
    "name": "submit_besluiten",
    "description": "Lever per actienummer het besluit van de reviewer. Je classificeert "
                   "alleen; je herschrijft de reviewer-tekst niet.",
    "input_schema": {
        "type": "object",
        "properties": {
            "besluiten": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nr": {"type": "integer",
                               "description": "Het actienummer waar dit besluit bij hoort."},
                        "besluit": {"type": "string", "enum": list(BESLUITEN),
                                    "description": "doen = ongewijzigd uitvoeren; "
                                                   "niet = niet uitvoeren; "
                                                   "mits = uitvoeren met de voorwaarde."},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["nr", "besluit", "confidence"],
                },
            },
        },
        "required": ["besluiten"],
    },
}

CLASSIFY_SYSTEM = """\
Je bent een classificator. Een menselijke reviewer heeft per voorgestelde actualiteit-actie
een korte Nederlandse aantekening gemaakt. Bepaal per nummer wat de reviewer bedoelt:

- "doen"  -- de aantekening is een kale goedkeuring en voegt niets toe.
- "niet"  -- de reviewer wijst de actie af; er moet niets gebeuren.
- "mits"  -- de actie gaat door, maar de aantekening stuurt bij: ze perkt in, nuanceert,
             verlegt de reikwijdte, of vervangt de actie door iets ingrijpenders.

De kernvraag is: **moet de schrijver deze aantekening lezen?**
- Zo ja, en er moet iets gebeuren -> "mits".
- Zo ja, maar er moet juist niets gebeuren -> "niet".
- Zo nee (kale goedkeuring, niets meer) -> "doen".

Daaruit volgt:
- "geen specifieke frameworks benoemen" bij een actie over frameworks -> mits.
  De aantekening perkt in; hij wijst niet af, ook al staat er "geen" in.
- "in inleiding is dat prima" -> mits. Er staat "prima", maar er staat ook een
  beperking (alleen in de inleiding). Alleen een kaal "prima" is "doen".
- "stuk over certificatie vervalt helemaal" of "certificeringsverwijzing volledig
  verwijderen" -> mits. Dit is een OPDRACHT om te verwijderen, niet het afwijzen van
  de actie. De schrijver moet iets doen, dus nooit "niet".
- "nee dat is advanced" -> niet. De reviewer wijst af; de rest is motivering.
- "nuanceer", "algemene formulering", "beide in algemene termen" -> mits.

Bij twijfel tussen "niet" en "mits": kies "mits" als er ook maar iets moet gebeuren.
Zet confidence op "low" als de aantekening echt meerduidig is; een mens kijkt die na.
Classificeer alleen. Herschrijf of vertaal de reviewer-tekst niet.
Roep tot slot het tool `submit_besluiten` aan, met precies één regel per aangeboden nummer.
"""


def _classify_user(titel: str, te_doen: list[tuple[int, str, str]]) -> str:
    regels = [f"{nr}. ACTIE: {actie}\n   REVIEWER: {ann}" for nr, actie, ann in te_doen]
    return f"Training: {titel}\n\n" + "\n\n".join(regels)


def _extract_tool_input(response, tool_name: str) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    return None


def classify(client, titel: str, te_doen: list[tuple[int, str, str]]) -> dict[int, tuple[str, str]]:
    """Vrije-tekst annotaties -> {nr: (besluit, confidence)}. Alleen het label."""
    if not te_doen:
        return {}
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=CLASSIFY_SYSTEM,
        messages=[{"role": "user", "content": _classify_user(titel, te_doen)}],
        tools=[SUBMIT_BESLUITEN],
        tool_choice={"type": "tool", "name": "submit_besluiten"},
    )
    out = _extract_tool_input(resp, "submit_besluiten") or {}
    labels: dict[int, tuple[str, str]] = {}
    for rij in out.get("besluiten", []):
        try:
            nr = int(rij["nr"])
        except (KeyError, TypeError, ValueError):
            continue
        besluit = str(rij.get("besluit", "")).strip()
        if besluit in BESLUITEN:
            labels[nr] = (besluit, str(rij.get("confidence", "")).strip())
    return labels


# ---------------------------------------------------------------------------
# 4. HET BESLUIT-RECORD
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Besluit:
    training_id: Any
    titel: str
    nr: int
    actie: str
    besluit_ruw: str      # de originele annotatie, onaangeraakt (audit-spoor)
    besluit: str          # doen | niet | mits
    voorwaarde: str       # letterlijke reviewer-tekst; leeg bij een kaal "prima"/"niet"
    bron: str             # regel | llm | handmatig
    confidence: str = ""

    @property
    def goedgekeurd(self) -> bool:
        return self.besluit in (DOEN, MITS)

    def als_instructie(self) -> str:
        """Regel zoals de schrijver hem ziet.

        De reviewer-tekst gaat mee zodra hij er is -- ook bij `doen` en `niet`. Het label
        bepaalt of de actie wordt uitgevoerd, niet of de reviewer gehoord wordt; anders zou
        een net-verkeerd label de aantekening laten verdampen.
        """
        if not self.voorwaarde:
            return self.actie
        kop = "REDEN" if self.besluit == NIET else "VOORWAARDE"
        return f"{self.actie}\n  {kop} (reviewer): {self.voorwaarde}"


# ---------------------------------------------------------------------------
# 5. STAP C -- OPBOUWEN EN NORMALISEREN
# ---------------------------------------------------------------------------

# Kolomnamen die "het id" of "de titel" kunnen heten, in volgorde van voorkeur. Bewust een
# eigen lijstje en niet `_find_col` uit score_trainings.py: die module leeft in de zusterrepo
# en besluiten.py is verder volledig standalone (alleen re/dataclasses/typing).
_ID_KOLOMMEN = ("training_id", "id", "trainingid")
_TITEL_KOLOMMEN = ("titel", "name", "naam", "title")


def _eerste_kolom(df, kandidaten: Iterable[str]) -> str | None:
    per_kleine_letter = {str(c).lower().strip(): c for c in df.columns}
    for kandidaat in kandidaten:
        if kandidaat in per_kleine_letter:
            return per_kleine_letter[kandidaat]
    return None


def normaliseer_scored_kolommen(df):
    """Hernoemt de id- en titelkolom naar de namen die de scorer schrijft.

    De scorer levert `training_id` en `titel`. Een prioriteitslijst die met de hand uit de
    bronlijst is samengesteld houdt de bronnamen `id` en `name` -- inhoudelijk hetzelfde sheet,
    alleen anders gelabeld. Beide zijn geldig; de rest van de code rekent op de scorer-namen.

    Staat de kanonieke naam er al, dan blijft alles ongemoeid, ook als er daarnaast nog een
    `id`-kolom bestaat.
    """
    hernoem = {}
    if "training_id" not in df.columns:
        kolom = _eerste_kolom(df, _ID_KOLOMMEN)
        if kolom:
            hernoem[kolom] = "training_id"
    if "titel" not in df.columns:
        kolom = _eerste_kolom(df, _TITEL_KOLOMMEN)
        if kolom:
            hernoem[kolom] = "titel"
    return df.rename(columns=hernoem) if hernoem else df


def normaliseer_training_ids(df, bron: str = ""):
    """Maakt van een numerieke id-kolom weer gehele getallen, of weigert het sheet.

    Excel maakt van een handmatig samengestelde lijst zomaar een float-kolom: staat er in
    één cel een duizendtalscheiding (`2.347`), dan leest pandas dat als het getal 2,347 en
    wordt de héle kolom float64. Dat is dubbel schadelijk. Het id joint niet meer met de
    bronsheet -- en omdat een training zonder broncontent verderop als `volledig` wordt
    aangemerkt, ziet zo'n typefout eruit als een inhoudelijk oordeel. En de rest van de
    kolom komt als `796.0` in beeld, wat een reviewer niet kan overtypen.

    Vandaar hier de poort, in het inleespad van beide pijplijnen: hele floats worden `int`,
    en alles wat geen geheel getal is stopt de boel meteen. Bewust niet in
    `normaliseer_scored_kolommen`: die hernoemt kolommen en hoort niet te gooien.

    Tekstuele ids blijven ongemoeid -- die zijn geldig en niet aan dit euvel onderhevig.
    """
    kolom = df["training_id"] if "training_id" in df.columns else None
    if kolom is None:
        return df

    import pandas as pd
    if not pd.api.types.is_numeric_dtype(kolom):
        return df

    fout = []
    for positie, waarde in enumerate(kolom, start=2):   # +2: kopregel en 1-based tellen
        if pd.isna(waarde):
            fout.append(f"rij {positie}: leeg")
        elif float(waarde) != int(waarde):
            heel = str(waarde).replace(".", "").replace(",", "")
            fout.append(f"rij {positie}: {waarde} (duizendtalscheiding? bedoeld is {heel})")
    if fout:
        waar = f" in {bron}" if bron else ""
        raise ValueError(
            f"{len(fout)} training_id(s){waar} zijn geen geheel getal:\n  "
            + "\n  ".join(fout)
            + "\nCorrigeer de id-kolom in het scoresheet; een id met een punt erin wordt "
              "als decimaal gelezen en joint daarna nergens meer mee.")

    df = df.copy()
    df["training_id"] = kolom.astype("int64")
    return df


def _load_scored(path: str):
    import pandas as pd
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    df = normaliseer_scored_kolommen(df)
    for kolom in ("training_id", "actualiteit_actie", "actie_besluit"):
        if kolom not in df.columns:
            raise ValueError(f"kolom {kolom!r} ontbreekt in {path}")
    return normaliseer_training_ids(df, path)


def check_alignment(scored_path: str, verbose: bool = True) -> list[str]:
    """Structurele controle over het hele sheet -- geen LLM, geen API-key.

    Geeft de lijst met foutmeldingen terug (leeg = alles lijnt uit).
    """
    df = _load_scored(scored_path)
    fouten: list[str] = []
    met_acties = 0
    op_nummer = 0
    for _, rij in df.iterrows():
        acties = parse_acties(rij["actualiteit_actie"])
        if not acties:
            continue
        met_acties += 1
        tid = rij["training_id"]
        try:
            _, lezing = koppel_met_lezing(acties, rij["actie_besluit"])
            op_nummer += lezing == LEZING_NUMMERS
        except BesluitFout as e:
            if _leeg(rij["actie_besluit"]):
                continue   # geen besluit ingevuld -> niets goedgekeurd, geen structuurfout
            fouten.append(f"training_id {tid}: {e}")
    if verbose:
        print(f"{len(df)} rijen, {met_acties} met genummerde acties, {len(fouten)} uitlijnfouten")
        if op_nummer:
            # Geen fout, wel iets om te weten: een reviewer is van stijl gewisseld.
            print(f"{op_nummer} cel(len) zonder scheidingskomma's gelezen")
        for f in fouten:
            print(f"  {f}")
    return fouten


def build_besluiten(scored_path: str, client=None, verbose: bool = True,
                    eerder: dict[tuple[Any, int], Besluit] | None = None) -> list[Besluit]:
    """Bouwt alle besluit-records. Roept de LLM alleen aan voor vrije-tekst annotaties.

    `eerder` is de oogst van een vorige ronde (`_eerdere_besluiten`): staat er voor deze
    (training_id, nr) een label bij een ONGEWIJZIGDE actie én annotatie, dan blijft dat staan
    en gaat de regel niet nog een keer langs het model. Anders classificeert een tweede run
    over hetzelfde scoresheet alles opnieuw -- inclusief de regels waar de reviewer al
    `handmatig` van heeft gemaakt en waarvan `write_besluiten_sheet` het verse label meteen
    weer weggooit. Verandert een van de twee teksten wel, dan telt het oordeel niet meer en
    valt de regel gewoon terug op een verse call.
    """
    df = _load_scored(scored_path)
    eerder = eerder or {}
    records: list[Besluit] = []
    n_llm = n_hergebruikt = 0

    for _, rij in df.iterrows():
        tid = rij["training_id"]
        titel = str(rij.get("titel", "") or "")
        acties = parse_acties(rij["actualiteit_actie"])
        if not acties:
            continue   # geen acties -> niets te besluiten

        # Lege besluit-cel betekent NIETS goedgekeurd, niet alles.
        if _leeg(rij["actie_besluit"]):
            records += [Besluit(tid, titel, nr, actie, "", NIET,
                                "geen besluit ingevuld", BRON_REGEL)
                        for nr, actie in sorted(acties.items())]
            if verbose:
                print(f"[{tid}] {titel[:40]:40} geen besluit -> {len(acties)}x niet")
            continue

        try:
            gekoppeld, lezing = koppel_met_lezing(acties, rij["actie_besluit"])
        except BesluitFout as e:
            raise BesluitFout(f"training_id {tid} ({titel}): {e}") from e

        # fastpath eerst, dan het oordeel van een vorige ronde; alleen wat daarna overblijft
        # is echte vrije tekst en kost een call
        labels: dict[int, tuple[str, str, str]] = {}   # nr -> (besluit, bron, confidence)
        te_doen: list[tuple[int, str, str]] = []
        for nr, actie, ann in gekoppeld:
            label = regel_label(ann)
            if label is not None:
                labels[nr] = (label, BRON_REGEL, "")
                continue
            vorig = eerder.get((tid, nr))
            if (vorig is not None and vorig.besluit in BESLUITEN
                    and _zelfde_tekst(vorig, actie, ann)):
                labels[nr] = (vorig.besluit, vorig.bron, vorig.confidence)
                n_hergebruikt += 1
                continue
            te_doen.append((nr, actie, ann))

        if te_doen:
            if client is None:
                from score_trainings import make_client
                client = make_client()
            n_llm += len(te_doen)
            for nr, (besluit, conf) in classify(client, titel, te_doen).items():
                labels[nr] = (besluit, BRON_LLM, conf)

        for nr, actie, ann in gekoppeld:
            besluit, bron, conf = labels.get(nr, (MITS, BRON_LLM, "low"))
            # Een kaal fastpath-woord draagt geen informatie; vrije tekst altijd verbatim
            # doorgeven -- de woorden van de reviewer blijven gezaghebbend.
            voorwaarde = "" if regel_label(ann) is not None else ann
            records.append(Besluit(tid, titel, nr, actie, ann, besluit, voorwaarde, bron, conf))

        if verbose:
            samenvatting = " ".join(f"{nr}:{labels.get(nr, ('?',))[0]}"
                                    for nr, _, _ in gekoppeld)
            hoe = "  (zonder komma's gelezen)" if lezing == LEZING_NUMMERS else ""
            print(f"[{tid}] {titel[:40]:40} {samenvatting}{hoe}")

    if verbose:
        hergebruik = f", {n_hergebruikt} overgenomen uit de vorige ronde" if n_hergebruikt else ""
        print(f"\n{len(records)} besluiten, waarvan {n_llm} geclassificeerd door het "
              f"model{hergebruik}")
    return records


def _zelfde_tekst(vorig: Besluit, actie: str, ann: str) -> bool:
    """Slaat het eerdere oordeel nog op deze regel?

    Beide teksten tellen: `_classify_user` zet de actie én de annotatie in de prompt, dus een
    herschreven actie kan het label kantelen terwijl de aantekening van de reviewer letterlijk
    hetzelfde bleef. Vergelijken op `strip()`, want een rondje door Excel kan witruimte
    toevoegen -- en bij twijfel valt de regel terug op een verse call, nooit op een stil
    verouderd label.
    """
    return (str(vorig.actie).strip() == str(actie).strip()
            and str(vorig.besluit_ruw).strip() == str(ann).strip())


def _eerdere_besluiten(out_path: str) -> dict[tuple[Any, int], Besluit]:
    """Alle labels uit een eerder sheet, van het model én van een mens.

    Twee gebruikers met een verschillende reikwijdte: `write_besluiten_sheet` neemt hieruit
    alleen de `handmatig`-regels over als eindoordeel, `build_besluiten` gebruikt de hele dict
    om een tweede ronde niet opnieuw te laten classificeren. Een met de hand fout ingetikt
    label gaat wél mee maar wordt nooit hergebruikt (`build_besluiten` eist daar `in BESLUITEN`
    bovenop `_zelfde_tekst`): overschrijven met een vers modeloordeel maakt de typefout stil,
    en `load_besluiten` wijst hem verderop met rij en al aan.
    """
    import os
    import pandas as pd
    if not os.path.exists(out_path):
        return {}
    df = pd.read_excel(out_path)
    df.columns = [str(c).strip() for c in df.columns]
    if "bron" not in df.columns or "besluit" not in df.columns:
        return {}
    bewaard: dict[tuple[Any, int], Besluit] = {}
    for _, r in df.iterrows():
        try:
            nr = int(r["nr"])
        except (TypeError, ValueError):
            continue   # een regel zonder nummer koppelt nergens aan; die telt niet mee
        besluit = str(r["besluit"]).strip().lower()
        sleutel = (r["training_id"], nr)
        bewaard[sleutel] = Besluit(
            r["training_id"], str(r.get("titel", "") or ""), nr,
            str(r.get("actie", "") or ""), str(r.get("besluit_ruw", "") or ""),
            besluit,
            "" if _leeg(r.get("voorwaarde")) else str(r["voorwaarde"]),
            str(r.get("bron", "") or ""), str(r.get("confidence", "") or ""))
    return bewaard


def _rijen_van_andere_trainingen(out_path: str, eigen_ids: set):
    """Bestaande regels van trainingen die niet in dit scoresheet staan.

    Zo kun je op één besluitensheet met meerdere batches werken: een prioriteitslijst van 15
    trainingen wist de besluiten van de andere 47 niet. De pijplijn zoekt besluiten op
    training_id op (`load_besluiten` -> dict, `rewrite_file` doet `per_training.get(tid)`),
    dus regels die niet bij de batch horen worden simpelweg nooit geraadpleegd.
    """
    import os
    import pandas as pd
    if not os.path.exists(out_path):
        return None
    df = pd.read_excel(out_path)
    df.columns = [str(c).strip() for c in df.columns]
    if "training_id" not in df.columns:
        return None
    return df[~df["training_id"].isin(eigen_ids)]


def write_besluiten_sheet(scored_path: str, out_path: str, client=None,
                          verbose: bool = True, opnieuw: bool = False):
    """Genereert besluiten.xlsx. Handmatige correcties uit een bestaand sheet blijven staan.

    Trainingen die al in het sheet stonden maar niet in dit scoresheet zitten blijven ook
    staan; de trainingen die er wél in zitten worden vers weggeschreven. Opnieuw draaien op
    hetzelfde scoresheet is dus idempotent en werkt een gewijzigde actietekst netjes bij.

    Dat "idempotent" kostte tot nu toe wél elke keer opnieuw alle calls: een regel waarvan de
    actie én de annotatie onveranderd zijn, houdt sinds deze ronde zijn label (zie `eerder` in
    `build_besluiten`). Een tweede run over een sheet waarin alleen de laatste batch nieuw is,
    classificeert dus alleen die batch. `opnieuw=True` zet het hergebruik uit -- nodig na een
    wijziging in `CLASSIFY_SYSTEM` of de fastpath, want dan is het oude oordeel geveld met
    een andere maatstaf en verschuift er niets aan de teksten die het hergebruik bewaakt.
    """
    import pandas as pd
    bestaand = _eerdere_besluiten(out_path)
    handmatig = {k: v for k, v in bestaand.items() if v.bron == BRON_HANDMATIG}
    records = build_besluiten(scored_path, client=client, verbose=verbose,
                              eerder=None if opnieuw else bestaand)

    samengevoegd = []
    for r in records:
        eerder = handmatig.get((r.training_id, r.nr))
        if eerder is not None:
            # label + voorwaarde van de mens, actietekst vers uit het scoresheet
            samengevoegd.append(replace(r, besluit=eerder.besluit,
                                        voorwaarde=eerder.voorwaarde,
                                        bron=BRON_HANDMATIG, confidence=""))
        else:
            samengevoegd.append(r)

    df = pd.DataFrame([{k: getattr(r, k) for k in KOLOMMEN} for r in samengevoegd])
    # De ids uit het scoresheet, niet uit de records: een training die geen acties (meer) heeft
    # levert geen records op, maar zijn oude regels moeten wél verdwijnen.
    eigen_ids = set(_load_scored(scored_path)["training_id"])
    behouden = _rijen_van_andere_trainingen(out_path, eigen_ids)
    n_behouden = 0 if behouden is None else len(behouden)
    if n_behouden:
        df = pd.concat([behouden.reindex(columns=KOLOMMEN), df], ignore_index=True)
    df.to_excel(out_path, index=False)
    if verbose:
        n_na = sum(1 for r in samengevoegd if r.bron == BRON_LLM)
        print(f"\nGeschreven: {out_path} ({len(df)} regels). "
              f"Controleer de {n_na} regels met bron=llm.")
        if n_behouden:
            print(f"{n_behouden} regels van trainingen buiten dit scoresheet behouden.")
        if handmatig:
            print(f"{len(handmatig)} handmatige correcties behouden.")
    return df


def load_besluiten(path: str) -> dict[Any, list[Besluit]]:
    """besluiten.xlsx -> {training_id: [Besluit, ...]}, op nr gesorteerd."""
    import pandas as pd
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    ontbreekt = {"training_id", "nr", "actie", "besluit"} - set(df.columns)
    if ontbreekt:
        raise ValueError(f"{path} mist kolom(men): {sorted(ontbreekt)}")

    per_training: dict[Any, list[Besluit]] = {}
    for _, r in df.iterrows():
        besluit = str(r["besluit"]).strip().lower()
        if besluit not in BESLUITEN:
            raise ValueError(
                f"{path}: training_id {r['training_id']} nr {r['nr']} heeft besluit "
                f"{r['besluit']!r}; verwacht een van {BESLUITEN}")
        rec = Besluit(
            r["training_id"], str(r.get("titel", "") or ""), int(r["nr"]),
            str(r.get("actie", "") or ""), str(r.get("besluit_ruw", "") or ""),
            besluit, "" if _leeg(r.get("voorwaarde")) else str(r["voorwaarde"]).strip(),
            str(r.get("bron", "") or ""), str(r.get("confidence", "") or ""),
        )
        per_training.setdefault(rec.training_id, []).append(rec)
    for lijst in per_training.values():
        lijst.sort(key=lambda b: b.nr)
    return per_training


def splits(besluiten: Iterable[Besluit]) -> tuple[list[Besluit], list[Besluit]]:
    """-> (goedgekeurd, afgewezen). De afgewezen lijst gaat als NIET DOEN mee."""
    goedgekeurd, afgewezen = [], []
    for b in besluiten:
        (goedgekeurd if b.goedgekeurd else afgewezen).append(b)
    return goedgekeurd, afgewezen
