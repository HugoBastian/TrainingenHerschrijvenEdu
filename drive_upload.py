"""
drive_upload.py
===============
De herschreven trainingen als opgemaakte Google Docs in Google Drive zetten, zodat het
reviewen niet meer begint met per training markdown kopiëren naar een leeg document.

Twee dingen bepalen de vorm van deze module.

**Het is een synchronisatie, geen verzendlijst.** De invoer is de map met artefacten
(`<out_dir>/trainingen/*.json`), niet "wat de batch zojuist maakte". `bouw_wachtrij` slaat een
training over die al in `herschreven.xlsx` staat; een training die in run 1 wél geschreven maar
niet geüpload werd, komt in run 2 dus nooit meer langs. Eén producent, twee aanroepers: de
batch en de losse cel in het notebook.

**Drive converteert bij upload.** De conversie zit in het verschil tussen twee mimetypes: het
doelformaat staat in de metadata (`application/vnd.google-apps.document`), het bronformaat in de
media (`text/html`). Daarom hoeft er geen regel opmaakcode in dit bestand te staan --
`rewrite_output.render_docs_html` levert de HTML en Drive maakt er koppen en bullets van.

Importrichting: deze module kent `rewrite_output` en `sjabloon`, en wordt door
`rewrite_trainings` geïmporteerd, niet andersom. Geen pandas, geen xlsx -- de kolom `drive_url`
in `herschreven.xlsx` vult de wrapper in `rewrite_trainings`, want daar zit het sheet.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os

import rewrite_checks as checks
import rewrite_output as uit

# `drive.file` geeft toegang tot uitsluitend wat deze app zelf aanmaakt. Dat is genoeg en het
# scheelt een verificatietraject: `drive` is bij Google een *restricted* scope en vereist bij
# een External app een jaarlijks (betaald) assessment. De prijs is dat we geen bestaande map van
# de gebruiker als ouder kunnen opgeven -- die geeft een 404 op de parent-id. Vandaar dat de app
# zijn eigen rootmap aanmaakt; zie `zorg_voor_rootmap`.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

DOC_MIME = "application/vnd.google-apps.document"
MAP_MIME = "application/vnd.google-apps.folder"

ROOTMAP_NAAM = "Herschreven trainingen"
MANIFEST_NAAM = "drive_uploads.json"

CLIENT_SECRET_DEFAULT = "google_client_secret.json"
TOKEN_DEFAULT = "google_token.json"

# Wat te doen met een training die al een doc heeft. `overslaan` is de default en dat is een
# bewuste keuze: een reviewer zet opmerkingen in dat doc, en `files.update` behoudt weliswaar de
# opmerkingenlijst maar niet de ankers -- de reviewer leest dan commentaar dat nergens meer op
# slaat. Een verouderd doc is minder erg dan losgeslagen commentaar.
BIJ_BESTAAND = ("overslaan", "nieuwe_versie", "tweede_doc")

_HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Authenticatie
# ---------------------------------------------------------------------------

def bouw_service(client_secrets: str | None = None, token_pad: str | None = None):
    """De Drive-client, met de OAuth-flow voor een client van het type "Desktop app".

    De google-imports staan bewust in de functie en niet bovenaan het bestand: `test_rewrite.py`
    moet deze module kunnen importeren op een machine zonder die packages, en dat is precies de
    situatie zolang niemand `pip install -r requirements.txt` opnieuw heeft gedraaid. Zelfde
    patroon als `import pandas` in `rewrite_file`.

    Het token bevat een refresh-token, dus het bestand krijgt 0600. Loopt het verversen stuk
    (ingetrokken toestemming, of de zevendaagse vervaldatum van een app die in "Testing" staat),
    dan is opnieuw inloggen het antwoord en geen foutmelding.
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    client_secrets = client_secrets or os.environ.get("GOOGLE_CLIENT_SECRET") or CLIENT_SECRET_DEFAULT
    token_pad = token_pad or os.environ.get("GOOGLE_TOKEN") or TOKEN_DEFAULT
    if not os.path.isabs(client_secrets):
        client_secrets = os.path.join(_HERE, client_secrets)
    if not os.path.isabs(token_pad):
        token_pad = os.path.join(_HERE, token_pad)

    creds = None
    if os.path.exists(token_pad):
        creds = Credentials.from_authorized_user_file(token_pad, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                creds = None
    if not creds or not creds.valid:
        if not os.path.exists(client_secrets):
            raise SystemExit(
                f"{client_secrets} ontbreekt. Maak in de Google Cloud Console een OAuth-client "
                f"van het type 'Desktop app' aan en zet de JSON daar neer (zie README).")
        creds = InstalledAppFlow.from_client_secrets_file(
            client_secrets, SCOPES).run_local_server(port=0)
        fd = os.open(token_pad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    # cache_discovery=False onderdrukt de oauth2client-waarschuwing van googleapiclient
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Namen, mappen en documenten
# ---------------------------------------------------------------------------

def docnaam(tid, titel: str) -> str:
    """`{id} - {titel} (automatisch herschreven)`, in één regel.

    Een regeleinde of tab in de titel maakt een Drive-naam die je in de lijst niet meer kunt
    lezen; Drive accepteert hem wel. Vandaar het inklappen van witruimte.
    """
    schoon = " ".join(str(titel or "").split())
    return f"{tid} - {schoon} (automatisch herschreven)"


def _q_escape(waarde: str) -> str:
    """Een naam in een Drive-query staat tussen enkele quotes; die moeten eruit ontsnappen."""
    return str(waarde).replace("\\", "\\\\").replace("'", "\\'")


def zoek_op_naam(service, naam: str, ouder_id: str | None, mime: str | None = None):
    """De eerste niet-verwijderde file met deze naam in deze map, of None.

    De terugval voor het manifest: is dat weggegooid of verplaatst, dan levert een herdraai
    zonder deze zoektocht een tweede doc naast het eerste op. Met `drive.file` ziet de query
    alleen bestanden die deze app zelf heeft gemaakt, dus hij kan niets van de gebruiker raken.
    """
    q = [f"name = '{_q_escape(naam)}'", "trashed = false"]
    if ouder_id:
        q.append(f"'{_q_escape(ouder_id)}' in parents")
    if mime:
        q.append(f"mimeType = '{_q_escape(mime)}'")
    antwoord = service.files().list(
        q=" and ".join(q), fields="files(id,name,webViewLink)", pageSize=2,
        spaces="drive").execute(num_retries=5)
    gevonden = antwoord.get("files") or []
    return gevonden[0] if gevonden else None


def zorg_voor_map(service, naam: str, ouder_id: str | None = None) -> dict:
    """De map met deze naam onder deze ouder; maakt hem aan als hij er niet is.

    Drive staat twee mappen met dezelfde naam naast elkaar toe, dus zonder deze zoektocht
    krijg je bij elke herdraai een nieuwe lege batchmap erbij.
    """
    bestaand = zoek_op_naam(service, naam, ouder_id, MAP_MIME)
    if bestaand:
        return bestaand
    body = {"name": naam, "mimeType": MAP_MIME}
    if ouder_id:
        body["parents"] = [ouder_id]
    return service.files().create(body=body, fields="id,name,webViewLink").execute(num_retries=5)


def zorg_voor_rootmap(service, verbose: bool = True) -> dict:
    """De vaste bovenliggende map, aangemaakt in de root van Mijn Drive.

    Waarom de app hem zelf maakt en je niet gewoon een bestaande map aanwijst: met de scope
    `drive.file` bestaat een map die de app niet heeft gemaakt simpelweg niet voor haar, en dan
    is `parents: [<jouw map>]` een 404. Verplaatsen of hernoemen in de Drive-UI breekt niets --
    de koppeling loopt via de file-id, niet via het pad.
    """
    map_ = zorg_voor_map(service, ROOTMAP_NAAM, None)
    if verbose:
        print(f"rootmap '{ROOTMAP_NAAM}': {map_['id']}\n"
              f"  zet dit in .env als DRIVE_ROOT_ID={map_['id']} om de zoektocht over te slaan")
    return map_


def upload_doc(service, html: str, naam: str, map_id: str) -> dict:
    """Eén HTML-pagina -> één Google Doc in deze map.

    `mimeType` in de body is het DOEL, de mimetype van de media is de BRON; dat verschil is de
    hele conversie. `fields` is nodig voor `webViewLink`: de standaardrespons geeft alleen
    id/name/mimeType/kind terug. `num_retries` laat googleapiclient zelf exponentieel
    terugvallen op 429, 5xx en `userRateLimitExceeded` -- een eigen backoff-lus zou dat
    dubbelop doen.
    """
    from googleapiclient.http import MediaIoBaseUpload
    import io

    media = MediaIoBaseUpload(io.BytesIO(html.encode("utf-8")),
                              mimetype="text/html", resumable=False)
    return service.files().create(
        body={"name": naam, "mimeType": DOC_MIME, "parents": [map_id]},
        media_body=media, fields="id,name,webViewLink").execute(num_retries=5)


def comment_tekst(training: dict) -> str:
    """De opmerking die bij een doc komt te staan: waar moet de reviewer op letten?

    Alleen de flags uit de tier `hoog`, net als de kolom `flags_hoog` in het review-tabblad.
    Alles erin zetten zou hier hetzelfde doen als de oude verzamelkolom deed: over de eerste 16
    trainingen was 62% van de flags een lengtemelding binnen de vangrail of hetzelfde woord voor
    de derde keer, en dan leest niemand het meer. Artefacten van vóór `flags_tier` hebben de
    uitsplitsing niet; die vallen terug op alle flags -- liever te veel tonen dan iets
    verstoppen, dezelfde afweging als in `_review_rij`.
    """
    tiers = training.get("flags_tier") or {}
    flags = tiers.get(checks.TIER_HOOG, []) if tiers else list(training.get("flags") or [])

    kop = "Automatisch herschreven."
    status = str(training.get("status") or "")
    if status and status != "approved":
        reden = str(training.get("reden") or "").strip()
        kop = f"Automatisch herschreven, status: {status}" + (f" ({reden})." if reden else ".")

    if not flags:
        return f"{kop} De code-check heeft geen opmerkingen die om een oordeel vragen."
    regels = "\n".join(f"- {f}" for f in flags)
    woord = "punt" if len(flags) == 1 else "punten"
    return f"{kop}\n\n{len(flags)} {woord} om op te letten:\n{regels}"


def heeft_comment(service, file_id: str) -> bool:
    """Staat er al een opmerking op dit document?

    Nodig op het `nieuwe_versie`-pad. "Het doc bestond al, dus de opmerking ook" klopt niet voor
    de documenten die zijn geüpload voordat deze functie bestond: die zouden er dan nooit een
    krijgen. Eén extra call, en alleen voor de docs die daadwerkelijk vervangen worden.
    """
    antwoord = service.comments().list(
        fileId=file_id, fields="comments(id)", pageSize=1).execute(num_retries=5)
    return bool(antwoord.get("comments"))


def plaats_comment(service, file_id: str, tekst: str) -> dict:
    """Eén opmerking op het document.

    Zonder anker, en dat is geen keuze maar een grens van de API: opmerkingen komen bij Google
    uitsluitend uit de Drive-API, en die kan een anker alleen als ondocumenteerde kix-JSON met
    tekstposities meekrijgen -- posities die wij niet kennen, want de conversie van HTML naar
    Doc gebeurt aan de andere kant. Een opmerking zonder anker hangt aan het document en staat
    in het opmerkingenpaneel, wat is wat de reviewer nodig heeft: weten waar hij op moet letten
    voor hij begint te lezen.

    De opmerking komt op naam van het account dat is ingelogd.
    """
    return service.comments().create(
        fileId=file_id, body={"content": tekst}, fields="id").execute(num_retries=5)


def vervang_doc(service, file_id: str, html: str) -> dict:
    """Nieuwe inhoud in een bestaand doc. Alleen op expliciet verzoek; zie `BIJ_BESTAAND`."""
    from googleapiclient.http import MediaIoBaseUpload
    import io

    media = MediaIoBaseUpload(io.BytesIO(html.encode("utf-8")),
                              mimetype="text/html", resumable=False)
    return service.files().update(fileId=file_id, media_body=media,
                                  fields="id,name,webViewLink").execute(num_retries=5)


# ---------------------------------------------------------------------------
# Het manifest
# ---------------------------------------------------------------------------

def manifest_pad(out_dir: str) -> str:
    return os.path.join(out_dir, MANIFEST_NAAM)


def lees_manifest(out_dir: str) -> dict:
    pad = manifest_pad(out_dir)
    if not os.path.exists(pad):
        return {"root_id": "", "mappen": {}, "docs": {}}
    with open(pad, encoding="utf-8") as f:
        data = json.load(f)
    for sleutel in ("mappen", "docs"):
        data.setdefault(sleutel, {})
    data.setdefault("root_id", "")
    return data


def schrijf_manifest(out_dir: str, manifest: dict) -> str:
    """Atomisch, om dezelfde reden als `_schrijf_atomisch` in `rewrite_trainings.py`.

    Een kopie in plaats van een import: die module ligt stroomafwaarts van deze, en
    `rewrite_output` is bewust een pure renderer waar geen schrijf-I/O in hoort. Zelfde
    afweging als bij `MIN_TITELS_PER_GROEP` in `rewrite_checks.py`. Halveert het schrijven
    hier, dan is het manifest onleesbaar en uploadt de volgende run alles opnieuw.
    """
    os.makedirs(out_dir, exist_ok=True)
    pad = manifest_pad(out_dir)
    tmp = f"{pad}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, pad)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return pad


# ---------------------------------------------------------------------------
# De artefacten op schijf
# ---------------------------------------------------------------------------

def verzamel_uit_map(out_dir: str, alleen_ids=None) -> list[dict]:
    """De trainingen in `<out_dir>/trainingen/` die een doc verdienen.

    Zonder `content` valt er niets te renderen: dat is een artefact met status `error` of een
    route die vóór het schrijven afbrak. Die overslaan is beter dan een leeg doc uploaden, want
    een leeg doc ziet een reviewer aan voor werk dat af is.

    Sorteert numeriek op training_id zodat de uitvoer van twee runs vergelijkbaar is. Niet op
    bestandsnaam: die sorteert lexicografisch en zet 27 tussen 2669 en 2725. In Drive doet de
    volgorde niets -- daar sorteert de gebruiker zelf.
    """
    gekozen = set(alleen_ids) if alleen_ids else None
    trainingen = []
    for pad in sorted(glob.glob(os.path.join(out_dir, "trainingen", "*.json"))):
        with open(pad, encoding="utf-8") as f:
            data = json.load(f)
        tid = data.get("training_id")
        if gekozen is not None and tid not in gekozen:
            continue
        if not data.get("content"):
            continue
        trainingen.append({"training_id": tid, "titel": data.get("titel") or "",
                           "status": data.get("status") or "", "content": data["content"],
                           # voor de opmerking bij het doc; zie `comment_tekst`
                           "reden": data.get("reden") or "",
                           "flags": data.get("flags") or [],
                           "flags_tier": data.get("flags_tier") or {}})

    def _volgorde(training):
        tid = training["training_id"]
        try:
            return (0, int(tid), "")
        except (TypeError, ValueError):
            return (1, 0, str(tid))

    return sorted(trainingen, key=_volgorde)


# ---------------------------------------------------------------------------
# De lus
# ---------------------------------------------------------------------------

def upload_naar_drive(out_dir: str, drive_map: str, *, service=None, root_id: str | None = None,
                      alleen_ids=None, bij_bestaand: str = "overslaan",
                      met_comment: bool = True, verbose: bool = True) -> dict:
    """Alles in `<out_dir>/trainingen/` als Google Doc in de Drive-map `drive_map`.

    Sequentieel, en dat is geen voorbehoud: het service-object uit `build()` is niet
    thread-safe en 200 docs kosten zo ongeveer drie minuten. Een mislukte upload stopt de rest
    niet -- het id gaat op de `mislukt`-lijst en de geslaagde staan in het manifest, zodat
    dezelfde aanroep opnieuw draaien alleen de rest oppakt. Dát is waarvoor deze functie los
    aanroepbaar is.

    `met_comment` zet bij elk vers doc een opmerking met de flags die om een oordeel vragen;
    zie `comment_tekst` en `plaats_comment`.

    Geeft {"map_id", "map_url", "urls", "nieuw", "overgeslagen", "mislukt"} terug.
    """
    if bij_bestaand not in BIJ_BESTAAND:
        raise ValueError(f"bij_bestaand moet een van {BIJ_BESTAAND} zijn, niet {bij_bestaand!r}")
    if not str(drive_map or "").strip():
        raise ValueError("Geef een mapnaam op voor deze batch (drive_map).")
    drive_map = str(drive_map).strip()

    trainingen = verzamel_uit_map(out_dir, alleen_ids)
    if not trainingen:
        if verbose:
            print(f"Geen trainingen met content in {os.path.join(out_dir, 'trainingen')}/")
        return {"map_id": "", "map_url": "", "urls": {}, "nieuw": [],
                "overgeslagen": [], "mislukt": []}

    service = service or bouw_service()
    manifest = lees_manifest(out_dir)
    root_id = root_id or os.environ.get("DRIVE_ROOT_ID") or manifest.get("root_id")
    if not root_id:
        root_id = zorg_voor_rootmap(service, verbose=verbose)["id"]
    manifest["root_id"] = root_id

    map_ = zorg_voor_map(service, drive_map, root_id)
    manifest["mappen"][drive_map] = map_["id"]

    urls, nieuw, overgeslagen, mislukt = {}, [], [], []
    for n, training in enumerate(trainingen, start=1):
        tid = training["training_id"]
        naam = docnaam(tid, training["titel"])
        html = uit.render_docs_html(training["content"], training["titel"])
        sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
        bekend = manifest["docs"].get(str(tid))
        if bekend and bekend.get("map") != drive_map:
            bekend = None   # zelfde training, andere batchmap: daar hoort een eigen doc

        try:
            if bekend is None:
                # terugval als het manifest weg is: anders staat er straks een tweede doc naast
                gevonden = zoek_op_naam(service, naam, map_["id"], DOC_MIME)
                if gevonden:
                    bekend = {"file_id": gevonden["id"], "url": gevonden.get("webViewLink", ""),
                              "naam": naam, "map": drive_map, "sha256": ""}

            if bekend and bij_bestaand == "overslaan":
                urls[tid] = bekend.get("url", "")
                overgeslagen.append(tid)
                # de opgeslagen sha blijft staan: hem hier bijwerken zou de melding hieronder
                # na één run wegpoetsen, terwijl het doc nog steeds de oude tekst toont
                manifest["docs"][str(tid)] = {**bekend, "naam": naam, "map": drive_map}
                if verbose and bekend.get("sha256") and bekend["sha256"] != sha:
                    # stil overslaan zou hier het verkeerde zijn: de tekst is sinds de upload
                    # veranderd, en dat is precies het geval waarin je het doc wilt bijwerken
                    print(f'  {tid}: doc bestaat al maar de tekst is gewijzigd '
                          f'-> bij_bestaand="nieuwe_versie" om het bij te werken')
                elif verbose:
                    print(f"  {tid}: staat er al")
            else:
                vervangt = bool(bekend) and bij_bestaand == "nieuwe_versie"
                if vervangt:
                    bestand = vervang_doc(service, bekend["file_id"], html)
                else:
                    bestand = upload_doc(service, html, naam, map_["id"])
                urls[tid] = bestand.get("webViewLink", "")
                nieuw.append(tid)
                manifest["docs"][str(tid)] = {"file_id": bestand["id"], "url": urls[tid],
                                              "naam": naam, "map": drive_map, "sha256": sha}
                # Een vervangen doc houdt de opmerking die erop staat -- een tweede zou de
                # reviewer twee keer hetzelfde laten lezen. Maar de docs van vóór deze functie
                # hebben er nog geen, dus daar wordt eerst gekeken. Faalt dit, dan is dat geen
                # mislukte upload: het document staat er en is bruikbaar.
                if met_comment:
                    try:
                        if not (vervangt and heeft_comment(service, bestand["id"])):
                            plaats_comment(service, bestand["id"], comment_tekst(training))
                    except Exception as fout:   # noqa: BLE001
                        print(f"  {tid}: opmerking plaatsen mislukt "
                              f"({type(fout).__name__}: {fout}); het doc staat er wel")
                if verbose:
                    # de titel en niet de docnaam: die eindigt op een vast achtervoegsel dat
                    # afkappen precies wegknipt wat een kolombreedte overhoudt
                    print(f"[{n}/{len(trainingen)}] {str(tid):>6} {training['titel'][:50]}")
        except Exception as fout:   # noqa: BLE001 -- één doc mag de rest niet meenemen
            mislukt.append(tid)
            print(f"  {tid}: upload mislukt ({type(fout).__name__}: {fout})")

    schrijf_manifest(out_dir, manifest)
    map_url = map_.get("webViewLink") or f"https://drive.google.com/drive/folders/{map_['id']}"
    if verbose:
        print(f"\nDrive-map '{drive_map}': {len(nieuw)} nieuw, {len(overgeslagen)} al aanwezig"
              + (f", {len(mislukt)} mislukt: {mislukt}" if mislukt else "")
              + f"\n{map_url}")
    return {"map_id": map_["id"], "map_url": map_url, "urls": urls,
            "nieuw": nieuw, "overgeslagen": overgeslagen, "mislukt": mislukt}
