# Trainingen Herschrijven

Herschrijft trainingen naar de nieuwe stijl (tien kopjes) op basis van de **brontekst**, het
**score-oordeel** uit het scoring-project en de **besluiten van de reviewer**. Tweede helft van
de pijplijn; de eerste helft (scoren + informatie verzamelen) leeft in
`../Trainingen Scoren/TrainingenScorenEdu`.

Pijplijn per training:

```
besluiten (reviewer)  -> besluiten.xlsx           # wat mag er doorgevoerd worden?
briefing (feiten + goedgekeurde/afgewezen acties)
  -> schrijver (LLM, submit_rewrite)              # alleen de generatieve kopjes
  -> code-check (deterministisch)                 # lengte/format/placeholders/catalogus
  -> judge (LLM, submit_judgment)                 # feitgetrouwheid, persona, per sectie
  -> revisie / route                              # approved | needs-revision | human-queue
  -> per-training JSON + herschreven.xlsx (cms + review)
```

Ontwerpprincipe (zelfde DNA als de scorer): **de LLM schrijft/oordeelt, Python assembleert en
beslist.** Vaste sjabloonteksten en catalogus-titels worden door de code ingevoegd, niet door
het model.

## Bestanden

| Bestand | Rol |
| --- | --- |
| `Template trainingen nieuwe opbouw.md` | Het bronformat: kopnamen, volgorde en alle vaste teksten. |
| `sjabloon.py` | Datzelfde template als code. **Enige plek** voor vaste teksten en kopstructuur. |
| `schrijfspec_herschrijven_v1.md` | Systeem-prompt van de schrijver: imperatieve regels per kopje. |
| `beoordelingsspec_herschrijven_v1.md` | Judge-spec, afgeleid uit dezelfde bron zodat judge en schrijver convergeren. |
| `humanisering_nl.md` | Wat je níét schrijft: NL LLM-taal, verboden woorden (§D) + machine-leesbare `BANNED_PATTERNS`. |
| `stijlregister_nl.md` | Wat je wél schrijft: registers, causale constructies, actieve werkwoorden, vergrotende trappen. Van de schrijfstijl-eigenaar. |
| `besluiten.py` | De besluitenlaag: `actie_besluit` → expliciete doen/niet/mits per actie. |
| `rewrite_checks.py` | Deterministische code-check: `Issue`-lijsten, hard-fail vs flag. |
| `rewrite_output.py` | Document → CMS-`content`-JSON (HTML), → markdown met kop 1/2/3, en → HTML voor Google Docs. |
| `drive_upload.py` | De herschreven trainingen als opgemaakte Google Docs naar een map in Google Drive. |
| `rewrite_trainings.py` | Hybride schrijver + orchestratie + I/O. |
| `herschrijven.ipynb` | Notebook om de pijplijn stap voor stap te draaien en te inspecteren. |
| `test_rewrite.py` | 287 offline tests (geen API-key nodig). |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Zet je API-sleutel in een `.env` (of de omgeving):

```
ANTHROPIC_API_KEY=...
```

Wil je de output ook als Google Docs in Drive, zet dan `DRIVE_ROOT_ID` erbij; zie
"2b. Naar Google Drive" verderop. Zonder die stap raakt de pijplijn Google niet aan.
`.env.example` staat model voor beide.

Eenmalig, per checkout: zorg dat `herschrijven.ipynb` nooit met celuitvoer wordt gecommit.
Celuitvoer bevat echte trainingsdata (id's, titels, batchstatussen) en die hoort net zomin in
git als `*.xlsx` of `herschreven/`. Git-filters staan niet in de repo zelf, dus dit moet elke
checkout opnieuw:

```bash
git config filter.strip-notebook-outputs.clean "python3 strip_notebook_outputs.py"
git config filter.strip-notebook-outputs.smudge cat
git config filter.strip-notebook-outputs.required true
```

## Afhankelijkheid: het scoring-project

`rewrite_trainings.py` hergebruikt de content-ingestie van `score_trainings.py`
(`parse_content` / `build_source_text` / `make_client` / `read_input`) zodat schrijver en
scorer **exact dezelfde brontekst** zien. Standaard wordt het scoring-project als zustermap
gevonden onder `.../Eduvision/`. Ligt het ergens anders, zet dan:

```bash
export SCORE_TRAININGEN_DIR="/pad/naar/TrainingenScorenEdu"
```

## Twee inputbestanden

De scorer-output bevat geen brontekst, dus de rewriter joint op `training_id` ↔ `id`:

| Bestand | Inhoud |
| --- | --- |
| **scoresheet** | Alle scorer-velden + de handmatig ingevulde kolommen `actie_besluit`, `kern_reviewer`, `rewrite_guidance`, `modus_reviewer` en `modules_nb_reviewer` + `herschreven`. |
| **bronsheet** | `id`, `name`, `herschreven`, `content` (JSON-string met de CMS-velden). |

Sinds augustus 2026 neemt de scorer `content` en `herschreven` uit zijn invoersheet over, dus in
de praktijk kan het scoresheet zijn eigen bronsheet zijn.

### Een blad uit de gedeelde reviewsheet meegeven

Het reviewen gebeurt met een team in één Google Sheet, en een blad daaruit mag rechtstreeks als
scoresheet mee. Er wordt op **kolomnaam** gematcht:

- de **kolomvolgorde doet niet mee**. `id`/`name` in plaats van `training_id`/`titel` werkt ook
  (`besluiten.normaliseer_scored_kolommen`);
- **extra kolommen zijn onschadelijk** en worden nooit gelezen — `Status`, `Link naar CRM` met
  formule en al;
- de **rijvolgorde doet wél mee**: `bouw_wachtrij` loopt het sheet van boven naar beneden af;
- wat er niet mag ontbreken zijn de kolommen uit stap 1d: zonder `modus_voorstel` en
  `modules_nb_voorstel` valt élke training terug op modus `volledig` en NB `stabiel`. Dat is een
  waarschuwing naar stderr en geen fout, dus lees hem.

De kolomvolgorde van de scoring-output staat vast in `score_trainings.KOLOM_VOLGORDE`, precies
zoals de gedeelde sheet hem heeft: `kern` t/m `scorer_confidence` is één blok van 28 kolommen dat
je in één handeling plakt. `ok`, `error` en het modus-blok staan er bewust áchter.

## Draaien

### 1. Besluiten normaliseren (eenmalig, met een menselijke controle)

`actie_besluit` heeft een vaste **structuur** (`<nr> <vrije tekst>`, gescheiden door een komma
waar een nummer op volgt) maar vrije **tekst**. Python splitst de structuur deterministisch;
een klein model classificeert de aantekening als `doen` / `niet` / `mits`.

```bash
# structuurcontrole -- geen API-key nodig
python -c "import besluiten; besluiten.check_alignment('scoresheet.xlsx')"

# normaliseren (hier lopen de classificatie-calls)
python -c "import besluiten; besluiten.write_besluiten_sheet('scoresheet.xlsx', 'besluiten.xlsx')"
```

Open daarna `besluiten.xlsx` en kijk **alleen de regels met `bron=llm`** na. Corrigeer wat niet
klopt en zet `bron` op `handmatig` — die worden bij hergenereren nooit overschreven. Vanaf dat
punt is er geen interpretatie meer, alleen data.

Twee dingen die het model bewust onderscheidt, en die je dus moet controleren:

- `geen specifieke frameworks benoemen` → **mits** (een voorwaarde, geen afwijzing);
- `nee dat is advanced` → **niet** (een afwijzing mét motivering).

De reviewer-tekst gaat altijd letterlijk mee naar de schrijver, ook bij `doen` — het label
bepaalt of de actie doorgaat, niet of de reviewer gehoord wordt.

### 1b. De kern controleren (zelfde ronde, zelfde reviewer)

Naast `actie_besluit` staat in het scoresheet de kolom **`kern_reviewer`**, leeg aangeleverd door
de scorer. Klopt de kern in de kolom ernaast, laat hem dan leeg. Klopt hij niet — meestal omdat het
**niveau** van de training niet klopt — plak hem over en pas hem aan. Twee kolommen omdat herscoren
een rij vervangt: zou de reviewer de scorer-kern zelf overschrijven, dan gooit de eerstvolgende
herscoring dat werk stil weg (`_behoud_kern_reviewer` in het scoring-project bewaakt dit).

Waar je op let: de kern hoort te beschrijven wat de deelnemer met het onderwerp **doet**, in de
werkwoorden van de bron. Zegt de bron "maak je kennis met" en "we introduceren", dan is de training
introducerend — staat er in de kern "leert toepassen", dan is dat de correctie die je maakt. De kern
sluit af met een afbakeningszin: wat de training expliciet níét doet.

**Gezag volgt herkomst.** Een door jou bijgestelde kern is leidend, ook waar de brontekst iets
anders suggereert — een mens heeft ervoor getekend. Een kern die alleen van de scorer komt is een
lezing: botst die met de brontekst, dan wint de brontekst en meldt de schrijver het conflict in
`notities`. Zo kost een verkeerd gelezen kern nooit meer dan een signaal.

### 1c. De guidance nakijken (zelfde ronde)

Twee kolommen verder staat **`rewrite_guidance`**, gevuld door de scorer. Dit is het enige
scorer-veld dat letterlijk in de prompt van de schrijver belandt (`guidance_definitief`), en dus
het enige dat je niet naleest maar bijstelt: schrijf in dezelfde cel wat er wél moet gebeuren.
"Modules 3 en 4 overlappen", "de inleiding mag blijven zoals hij is".

Anders dan bij de kern is er hier géén tweede kolom. Een herscoring overschrijft deze cel dus
wel; de versiegeschiedenis van de gedeelde sheet is de terugval. Sheets van vóór augustus 2026
hebben de aanwijzing in een aparte kolom `guidance_reviewer` staan — die wordt nog gelezen, met
het label erbij, maar er komt niets nieuws meer in.

### 1d. De mate van aanpassing kiezen

```bash
python rewrite_trainings.py --scan-modus scoresheet_met_voorstel.xlsx \
  --scored "scoresheet.xlsx" --source "/pad/naar/bronsheet.xlsx"
```

Vult per training `modus_voorstel` en `modus_reden` en levert een lege kolom `modus_reviewer`
aan. Jij kijkt het voorstel na en vult `modus_reviewer` waar je het er niet
mee eens bent; leeg laten betekent "voorstel is goed". Zie **De mate van aanpassing** hieronder
voor wat de vier niveaus betekenen. `--geen-llm` slaat de modelcall over en laat alleen de
deterministische ondergrens staan — bruikbaar als kalibratie, niet als voorstel.

Deze stap komt ná de scoor-review, dus zijn kolommen belanden nooit in de gedeelde sheet; ze
worden achteraan het scoresheet geschreven, buiten het blok dat je plakt. Vrije aanwijzingen
horen daarom in `rewrite_guidance` (stap 1c) en niet hier.

Dezelfde call vult ook `modules_nb_voorstel` en `modules_nb_reden`, met `modules_nb_reviewer`
ernaast — de Modules-NB, zie **De vaste NB onder kopje Modules** hieronder. Die keuze gaat over
het onderwerp en niet over de kwaliteit van de tekst; verwar hem niet met de actualiseringen uit
stap 1.

Het commando **stopt** als een `training_id` niet in de bronsheet voorkomt, en het inleespad
weigert een id dat geen geheel getal is. Dat laatste is de klassieke Excel-val: staat er in één
cel `2.347` in plaats van `2347`, dan leest pandas dat als een decimaal, wordt de hele kolom
float, joint het id nergens meer mee en zou de training als `volledig` worden voorgesteld — een
typefout die zich voordoet als een oordeel.

### 2. Herschrijven

```bash
python rewrite_trainings.py \
  --scored  "scoresheet.xlsx" \
  --source  "/pad/naar/bronsheet.xlsx" \
  --besluiten besluiten.xlsx \
  --out-dir herschreven --limit 3
```

Output in `--out-dir`:

- `trainingen/<id>.json` — lossless: `document`, `content` (CMS-JSON), `judgment`,
  `toegepaste_acties`, flags en `writer_out` (wat de schrijver letterlijk leverde);
- `trainingen/<id>.md` — hetzelfde document als leesbare markdown (de kopstructuur van het
  template), om terug te lezen zonder de JSON open te klappen. Een training zonder document
  (`error`/`rejected`) krijgt geen `.md`;
- `herschreven.xlsx`, tabblad **cms** — `id` / `name` / `content`, met dezelfde
  JSON-structuur als het bronsheet, zodat het zo terug het CMS in kan;
- `herschreven.xlsx`, tabblad **review** — status, `modus`, `modus_voorstel`, `spec_versie`, de
  flags in drie kolommen (`flags_hoog` / `flags_mechanisch` / `flags_laag`, zie
  "Drie tiers" verderop) en elk kopje in platte tekst, met een lege `approve_edit`-kolom en als
  laatste kolom de **brontekst**. Die staat er zodat je een claim of een opgeschoven niveau naast
  het origineel kunt leggen; zonder die kolom lees je alleen de nieuwe tekst en is precies de fout
  onzichtbaar die de judge net doorliet.

`spec_versie` is een korte hash over de schrijfspec, `humanisering_nl.md`, `stijlregister_nl.md` en
het template. Zodra die bestanden veranderen is "welke goedgekeurde trainingen dateren van vóór de
huidige regels" precies de vraag die bepaalt wie er een `stijl`-ronde in moet — met deze kolom is
dat een filter, zonder is het een gok op bestandsdatums.

Trainingen op modus `overnemen` worden **niet** herschreven maar wél doorgezet (status
`overgenomen`), zodat `herschreven.xlsx` één compleet CMS-document is. Ook zij krijgen hun
`trainingen/<id>.json`; alleen geen `.md`, want `neem_over` levert geen document. Het scoresheet
bepaalt wat erin hoort. De run **hervat** verder standaard: wat al in `herschreven.xlsx` staat
wordt overgeslagen. Gebruik `--no-append` om te overschrijven.

### 2b. Naar Google Drive, één doc per training

Het reviewen gebeurt in Google Docs, en de weg ernaartoe was handwerk: per training de markdown
kopiëren naar een leeg document. Met `--drive-map` (of `DRIVE_MAP` in het notebook) doet de
pijplijn dat zelf.

```bash
# als onderdeel van de batch: artefacten in trainingen/batch 2026-08-10/, docs in de Drive-map
# met dezelfde naam
python rewrite_trainings.py --scored ... --source ... --besluiten besluiten.xlsx \
  --batch "batch 2026-08-10" --drive-map "batch 2026-08-10"

# of los, over een batch die er al staat
python rewrite_trainings.py --alleen-uploaden --drive-map "batch 2026-08-10" \
  --out-dir herschreven --scored scoresheet.xlsx
```

Elk doc heet `{id} - {titel} (automatisch herschreven)`. Drive converteert de HTML uit
`render_docs_html` bij het uploaden naar een echt Google Doc, dus de koppen komen in de
documentoverzicht-zijbalk en de modules houden hun sub-bullets. De links belanden in de kolom
`drive_url` van het review-tabblad.

**Elk vers doc krijgt een opmerking met de flags**, zodat een reviewer weet waar hij op moet
letten voor hij begint te lezen. Alleen de tier `hoog`, net als de kolom `flags_hoog` — alles
tonen zou hier hetzelfde doen als de oude verzamelkolom deed. De opmerking hangt aan het
document en niet aan de titel: opmerkingen komen bij Google uitsluitend uit de Drive-API, en
die kan een anker alleen als ondocumenteerde kix-JSON met tekstposities meekrijgen, posities die
wij niet kennen omdat de conversie aan de andere kant gebeurt. Zet `met_comment=False` om het
over te slaan.

De opmaak (`DOCS_KOPPEN` en `ALINEA_RUIMTE` in `rewrite_output.py`) staat als inline stijl in de
doc-HTML en komt nooit in de CMS-content terecht — die krijgt zijn opmaak van de site. Kop 1 is
20pt, kop 2 16pt, allebei zonder vet; kop 3 is 14pt en wel vet. Elke alinea krijgt ruimte
eronder, want Docs zet "ruimte na alinea" standaard op nul en zonder die regel plakken alle
alinea's van een kopje aan elkaar tot één blok. Grootte en vet staan óók op een `<span>` binnen
de kop: Docs bewaart die als tekenopmaak op de tekst en niet als eigenschap van de alinea.

Vier dingen die de vorm bepalen:

- **het is een synchronisatie van de batchmap, geen verzendlijst van deze run.** De wachtrij
  slaat over wat al in `herschreven.xlsx` staat, dus een training die eerder wel werd geschreven
  maar niet geüpload zou anders nooit meer langskomen. Om dezelfde reden draait de upload door
  als de wachtrij leeg is;
- **`--batch NAAM` zet de artefacten in `trainingen/NAAM/`**, en de upload neemt alleen die
  submap mee. Zonder die scheiding is er op schijf niets wat batch 1 van batch 2 onderscheidt en
  belandt élke training in élke Drive-map opnieuw. Laat je `--batch` weg bij het uploaden, dan
  wordt de submap gekozen die net zo heet als de Drive-map; bestaat die niet, dan gaat de platte
  map mee (daar staan de trainingen van voor de indeling, die niet hoeven te verhuizen).
  `rw.zoek_artefact(out_dir, id)` vindt een training waar hij ook staat;
- **wat er al staat blijft staan.** Een reviewer zet opmerkingen in een doc, en die raken hun
  ankers kwijt zodra de inhoud eronder wordt vervangen. Is de tekst sinds de upload veranderd,
  dan meldt de lus dat; met `bij_bestaand="nieuwe_versie"` werk je alsnog bij;
- **één mislukte upload stopt de rest niet**, en de geslaagde staan in `drive_uploads.json`.
  Dezelfde aanroep opnieuw draaien pakt alleen op wat er nog niet is;
- **de upload hangt achter de batch, met een vangnet.** De artefacten en het sheet staan dan al
  op schijf, dus een kapot token kost hoogstens de upload. Authenticeren gebeurt juist vooraf,
  vóór de eerste Claude-call, om precies die reden omgekeerd.

**Eenmalig inrichten.** In de Google Cloud Console: project kiezen, **Google Drive API** aanzetten,
onder Google Auth Platform de scope `.../auth/drive.file` toevoegen (die staat onder
Non-sensitive) en een OAuth-client van het type **Desktop app** aanmaken. De JSON komt als
`google_client_secret.json` naast het notebook te staan. Staat het project buiten de
eduvision.nl-Workspace, publiceer de app dan (Audience → Publish app): een app die in "Testing"
blijft, laat zijn refresh-token elke 7 dagen verlopen.

Bij de eerste run opent er een browservenster; daarna staat er een `google_token.json` (0600) en
draait alles zonder tussenkomst. De code maakt zelf een map `Herschreven trainingen` in Mijn
Drive en print de id voor `DRIVE_ROOT_ID` in `.env`. Dat de app haar eigen rootmap maakt is geen
gemak maar een gevolg van de scope: `drive.file` geeft toegang tot uitsluitend wat de app zelf
aanmaakt, en een bestaande map van de gebruiker als `parents` geeft een 404. De ruimere scope
`drive` is bij Google *restricted* en kost een jaarlijks betaald assessment.

## De mate van aanpassing

Niet elke training hoeft even zwaar herschreven. Sommige zijn inhoudelijk in orde en missen alleen
gevulde velden; andere voldoen aan het format van gisteren maar niet aan dat van vandaag. Dat
onderscheid loopt langs **twee assen die los van elkaar staan**.

### As 1: het herschrijfniveau

Kolom `modus_reviewer`. Elk niveau mag alles wat het niveau eronder mag.

| Modus | Wat er mag veranderen | Wanneer |
| --- | --- | --- |
| `overnemen` | Niets, behalve de titel en de vervolgtitels | Voldoet aan het actuele format |
| `stijl` | De formulering | Alle kopjes staan er en de inhoud klopt; de schrijfregels zijn opgeschoven |
| `format` | + de structuur en de ontbrekende kopjes | Inhoud klopt, velden leeg of verkeerd ingedeeld |
| `volledig` | + de opbouw, vanaf nul uit de brontekst | Het gedrag van vóór deze schaal |

In `stijl` en `format` krijgt de schrijver **de bestaande tekst per kopje** in plaats van de
brontekst. Dat is dezelfde content: `build_source_text` bouwt zijn tekst uit precies dat
`content`-object, alleen zonder `setup`, `follow_up`, `summary_edudex` en `certification` en zonder
indeling per kopje. Voor herschrijven vanaf nul is dat prima, voor bijwerken niet — dan wil je zien
wat er in elk veld staat. Twee keer hetzelfde meesturen zou het model twee versies van de waarheid
geven, waarvan de ene net minder compleet is.

De betekenis van `stijl` is **relatief aan de actuele spec**, niet aan een moment in de tijd. Wordt
`schrijfspec_herschrijven_v1.md` strenger, dan verschuift vanzelf welke trainingen daar
thuishoren — daarom leest de modusschatting die spec letterlijk in plaats van een drempel te
hanteren.

### As 2: de actualiseringen

De goedgekeurde besluiten uit `besluiten.xlsx` worden op **elk** niveau doorgevoerd, ook bij
`overnemen`. Ze verschuiven de modus bewust niet: een goedgekeurde actie is een lokale toevoeging,
en de training daarom integraal opnieuw laten schrijven maakt de wijziging groter dan de reviewer
vroeg. De opdracht luidt overal hetzelfde — voer de actie uit, laat wat de actie niet raakt op het
niveau dat as 1 voorschrijft.

Bij `overnemen` gebeurt dat met een apart tool (`submit_actualisatie`) waarin **geen enkel kopje
verplicht is**: het model levert alleen de kopjes die de actie raakt, de code rendert precies die
velden opnieuw en laat de rest van `content` byte-voor-byte staan — inclusief `follow_up`, `setup`
en `certification`, die anders door het sjabloon zouden worden overschreven. Zonder goedgekeurde
acties kost dit pad nog steeds geen enkele API-call.

Dit repareerde ook een stille fout: `neem_over` kreeg de besluiten helemaal niet te zien, dus een
training met `herschreven=1` liet zijn goedgekeurde actualiseringen vallen terwijl een mens er wel
voor had getekend. Van de zes trainingen met `herschreven=1` in het huidige sheet gold dat er drie.

### Wie de modus bepaalt: drie lagen

Python kan de schrijfregels niet lezen. `rewrite_checks.py` vangt openingszinnen, lengtes,
aantallen en verboden woorden — daarmee is te **bewijzen dat een tekst niet voldoet, nooit dat hij
wél voldoet**. Of een zin het stijlregister volgt of het causale verband zichtbaar maakt, ziet code
niet. Vandaar dezelfde drietrap als bij `besluiten.py` en de vervolgtrainingen:

| Laag | Wat | Levert |
| --- | --- | --- |
| **Python** `scan_vorm()` | Deterministische ondergrens uit `check_rewrite` | Nooit `overnemen`; zelfs een tekst die elke check haalt levert `stijl` op |
| **Haiku** `schat_modus()` | Leest de actuele schrijfspec + stijlregister naast de bestaande tekst | Een voorstel ≥ die ondergrens, met één regel motivering |
| **Mens** `modus_reviewer` | Leest de tekst en beslist | Leidend |

De scan scheidt **structuur** van **formulering**: een ontbrekend kopje of een verkeerd aantal
modules, sub-bullets of doelen vraagt om herindelen (`format`), een missende openingszin of een
verboden woord alleen om andere zinnen (`stijl`). Die codes staan in `STRUCTUUR_CODES`; verandert
een regel in `rewrite_checks.py` van aard, dan hoort hij daar mee te verhuizen.

Een scoresheet zónder de nieuwe kolommen gedraagt zich exact als voorheen: `herschreven=1` was de
facto al een modus ("niet aanraken") en valt terug op `overnemen`, de rest op `volledig`.

Een training zonder bronrij is géén modus-oordeel maar een fout in het scoresheet, en
`modus_voorstellen()` stopt er dan ook op — vóór de eerste call. Zou hij doorlopen, dan zag
`scan_vorm()` lege content en stelde hij `volledig` voor: het duurste advies dat er is, op basis
van een typefout in de id-kolom.

### De vaste NB onder kopje Modules

Onder kopje Modules staat altijd een vaste zin, in twee varianten (`sjabloon.MODULES_NB_STABIEL`
en `MODULES_NB_ACTUEEL`). De keuze ertussen gaat **niet over de kwaliteit van de tekst maar over
het onderwerp**, en staat los van de actualiseringen uit de besluitenronde:

| Variant | Belofte | Wanneer |
| --- | --- | --- |
| `stabiel` (default) | Bel ons als je de inhoud op jouw praktijksituatie aangepast wilt zien | Verreweg de meeste trainingen |
| `actueel` | De werkelijke inhoud kan afwijken door snelle ontwikkelingen | Alleen als het vakgebied binnen een jaar aan de beschrijving voorbij loopt |

`schat_modus()` stelt de variant voor in hetzelfde tool-schema als de modus — dus zonder extra
call, want dat model leest de brontekst toch al. De gezagsvolgorde is dezelfde als overal:
`modules_nb_reviewer` → `modules_nb_voorstel` → default `stabiel` (`RewriteBriefing.modules_nb`).

De `actueel`-variant is een voorbehoud en hoort de uitzondering te zijn: zonder die noodzaak
suggereert hij dat wij zelf niet weten wat we geven. `modus_voorstellen()` waarschuwt daarom
zodra meer dan een derde van de batch hem voorgesteld krijgt — een prioriteitslijst vol cloud,
AI en security haalt die drempel makkelijk, en dan is nalezen precies wat er moet gebeuren.

### Eén kopje opnieuw

Een reviewer die alleen de Modules wil bijsturen hoeft niet de hele training opnieuw te
betalen:

```python
rw.hergenereer_kopje_op_schijf(SCORED, SOURCE, training_id=5, kopje="modules",
                               comment="module 2 en 4 overlappen; voeg ze samen",
                               besluiten_path=BESLUITEN)
```

Zonder `comment` is het een gewone retry. De JSON en de rij in `herschreven.xlsx` worden
bijgewerkt.

### 3. Goud-corpus

```bash
python rewrite_trainings.py --goud --source "/pad/naar/bronsheet.xlsx" --out-dir herschreven
```

Schrijft de trainingen met `herschreven=1` weg naar `herschreven/goud/<id>.json`. Dat is
referentiemateriaal om spec en judge aan te kalibreren — géén voorschrift; het template en de
schrijfspec zijn leidend.

> **Sinds Templatev2 is dit corpus alleen nog meetlat, geen voorbeeld.** Het dateert van vóór
> het nieuwe template én vóór de eerste stijlronde. Concreet: 77 van de 78 openen met "Deze
> training is voor" in plaats van "is bedoeld voor", geen enkele demonstreert het lerende
> aspect uit schrijfspec §0.15, en er haalt er nu **nul** élke harde check. Als few-shot zou
> het precies de vormen tonen die de spec inmiddels verbiedt. De few-shot komt daarom uit
> `herschreven/goud_v2/` — zie hieronder.
>
> Lees de nieuwe `doelgroep: opening`-fails (77×) dus niet als regressie: dat is de regel die
> verandert, niet het goud dat slechter wordt.

Het corpus heeft twee toepassingen:

```python
rw.checks_over_goud()    # hoe vaak faalt elke harde regel op de 78 trainingen?
rw.lengtes_over_goud()   # hoe lang zijn de kopjes werkelijk? -> kalibratie van de lengtebanden
```

Van de 78 haalt er sinds Templatev2 **nul** élke harde check (zie het kader hierboven: de
`doelgroep: opening`-regel alleen al velt er 77). De few-shot komt daarom uit `goud_v2/` en
niet meer uit dit corpus maar uit `goud_v2/`, gevuld door `promoveer_naar_goud()` (zie 3b).
Dit corpus is nu puur meetlat: valt een regel bij meer dan de helft van het corpus om, dan is
de regel verdacht en niet de training. Zo is de lengte-check ontstaan — met één hard venster
van 55–65 woorden viel 65% van het goud om op het Overzicht, dus is die lengte nu een richtlijn
met een vangrail eromheen (zie hieronder). Verander je een check, draai dit dan opnieuw; de
few-shot toets je in dezelfde notebook-cel met `checks_over_goud(rw.GOUD_V2_DIR)`.

**De modules tellen sinds kort mee, en dat verschoof dit beeld.** Zolang `goud_naar_check_input`
de modulestructuur oversloeg — geneste `<ul>` betrouwbaar terugparsen leek meer valkuil dan
antwoord — waren `modules_aantal`, `bullets_aantal` en `bullets_variatie` op het goud onzichtbaar.
Met een diepteteller in plaats van een regex is die structuur wél te lezen (75 van de 78), en
zakte het aantal schone trainingen van 26 naar 7. Drie van de vier toenmalige few-shots (2730,
3101, 3125) vielen daardoor af: die demonstreerden modules met twee sub-bullets terwijl de spec er
3-6 eist. Een few-shot die de eigen regel schendt is erger dan geen few-shot; kort daarna is het
oude corpus als voorbeeldmateriaal helemaal losgelaten (zie 3b).

Datzelfde corpus heeft de modulesband bijgesteld. Met een vaste 4–6 viel `modules_aantal`
**25 keer** om; met de ruime duurafhankelijke band (4–7 / 5–9) nog **14 keer**, vrijwel allemaal
programma's van 8 of meer; met de huidige band (zie "Lengtes: richtlijn met vangrail") weer
**25 keer**. Dat laatste getal is bewust opgelopen: de band is in ronde 3 teruggeschroefd omdat
het programma te lang werd, niet omdat het corpus dat vroeg. Dat het gelijk uitkomt met de oude
vaste 4–6 is logisch — alleen bij vier dagen of meer verschillen ze nog, en dat zijn er vijf.

Dat heeft één neveneffect dat de moeite waard is om te weten. `modules_aantal` staat in
`STRUCTUUR_CODES`, dus een te ruim programma legt in `scan_vorm` de ondergrens op `format` in
plaats van `stijl`. In de praktijk verschuift dat weinig: 75 van de 78 landen sowieso al op
`format` via `_verouderde_vaste_tekst`, dat eerder in de keten staat.

Wat daarbij hoort als open vraag aan de spec, niet als oordeel over het goud: `bullets_aantal`
valt **172 keer** om over 78 trainingen. Dat is precies het soort cijfer waarvoor de regel hierboven
bedoeld is. `checks_over_goud()` rapporteert daarom ook hoeveel trainingen schoon zijn als je de
modules-checks weglaat, zodat beide getallen naast elkaar staan.

Het goud dateert van vóór de huidige Doelen-introzin: 47 van de 78 openen nog met "Na deze
training heb je handvatten om:". `goud_voorbeelden()` vervangt die regel bij het opbouwen van
de few-shot door `sjabloon.DOELEN_INTRO`. Dat is sinds de overstap naar `goud_v2` een vangnet
in plaats van een noodzaak — het blijft staan voor wie `goud_dir` terugzet op het oude corpus.

### 3b. Few-shot: `goud_v2`, gevuld met eigen output

```python
rw.promoveer_naar_goud(dry_run=True)   # wat zou er goud worden, en hoe ziet het eruit?
rw.promoveer_naar_goud()               # checken, kopiëren, selectie vastleggen
```

De few-shot hoort te bestaan uit trainingen die mét de huidige spec zijn geschreven. Sinds
reviewronde 4 regelt `promoveer_naar_goud()` dat in één stap: hij draait de checks over
`herschreven/trainingen/`, kopieert wat slaagt naar `herschreven/goud_v2/<id>.json` en schrijft
de selectie naar `herschreven/goud_v2/selectie.json`. `GOUD_VOORBEELDEN` leest dat manifest bij
import, dus er blijft geen lijst met id's in `rewrite_trainings.py` achter die iemand met de
hand moet bijwerken.

Drie keuzes die erin zitten:

- **de checks gaan over de rijke vorm** (`writer_out` plus de groepen uit het document), niet
  over de CMS-HTML zoals bij `checks_over_goud()`. Daardoor tellen `aanpak_invulling`, de
  catalogustitels en de groep-intro's mee — precies de plekken waar ronde 4 fouten vond;
- **de content wordt opnieuw gerenderd** uit het document, zoals `bouw_goud_v2.py` het ook doet.
  Een voorbeeld kan zo nooit verouderde boilerplate demonstreren;
- **`vervang=True` (de default) maakt de map gelijk aan de selectie.** Wat er niet in zit gaat
  weg; de vier gerepareerde `v2_*` zijn altijd terug te bouwen met `python bouw_goud_v2.py`.

De selectie is **vast** en wisselt niet per training: de hele system-prefix gaat als één
`cache_control: ephemeral`-blok mee, dus een prefix die per training verschilt maakt de
prompt-cache waardeloos. Er gaan er `GOUD_N` (vier) mee, en welke dat waren staat per training
in `<id>.json` onder `goud_voorbeelden` — naast `spec_versie`, want de few-shot vormt de output
net zo goed als de spec.

**Kijk bij het promoveren naar de profielregels** (dagen, modules, bullets, woorden in het
Overzicht). De checks bewijzen dat een voorbeeld de regels haalt, niet dat de sélectie
gevarieerd is. De huidige vier zijn 2/2/2/3 dagen en allemaal data, AI of development; bij een
volgende ronde is een langere training of een ander vakgebied welkom.

`bouw_goud_v2.py` blijft bestaan als terugvaloptie: `herschreven/` is gitignored, dus na een
verse checkout is er geen manifest en valt `GOUD_VOORBEELDEN` terug op de vier `v2_*`.

`test_fewshot_haalt_zelf_alle_harde_checks` bewaakt dat het voorbeeldmateriaal zijn eigen
regels haalt. Verander je een regel en valt die test om, dan is het voorbeeld het probleem —
niet de test.

**Dat vermoeden over de lengtebanden is inmiddels bevestigd.** Ze waren gekalibreerd op het oude
corpus, dus op tekst zónder "kunnen"-framing en zónder causale slotzin, en dat maakte de
Overzicht-band te krap. Reviewronde 2 bevestigde het van de andere kant; de band staat nu op
55–80 (zie "Lengtes: richtlijn met vangrail"). Voor de Inleiding is dezelfde meting nog niet
gedaan — meet die op de eerste nieuwe batch met `lengtes_over_goud()` en besluit dan.

### De stijl-lagen

Stijl zit bewust in drie soorten lagen, met een strikte werkverdeling:

| Laag | Waar | Voor |
| --- | --- | --- |
| Vaste tekst | `sjabloon.py` | Zinnen die de code invoegt; de schrijver raakt ze niet aan. |
| Prompt | `schrijfspec` (regels) · `humanisering_nl.md` (verboden) · `stijlregister_nl.md` (register) · `correcties_nl.md` (fout/goed-paren) | Alles wat oordeel vraagt. Gaat naar schrijver én judge. |
| Check | `rewrite_checks.py` | Alleen wat deterministisch te betrappen is. |

Positieve stijlvoorkeur ("gebruik een vergrotende trap waar het doel begrip is") hoort per
definitie in de promptlaag: code kan niet zien of een woord raak gekozen is. Een verbodslijst
hoort in beide — de tekst in `humanisering_nl.md`, de regex in `rewrite_checks.py`. Die twee
zijn een handmatige spiegel; wijk je in de één af, dan lopen schrijver en check uiteen.

`correcties_nl.md` is de nieuwste laag en groeit per review-ronde. Het bevat echte fout/goed-
paren met de motivering erbij, want de ❌-zin is meestal niet fóut maar net niet raak — en dat
verschil is met een regel moeilijker over te brengen dan met een contrast. Zet er een paar in
zodra een correctie **twee keer** terugkomt; een eenmalige opmerking hoort in de training zelf.

### Vaste tekst valt buiten de taalregels

Een paar aangeleverde sjabloonzinnen overtreden regels die voor de schrijver wél gelden:
`AANPAK_ALINEA_2` bevat "niet alleen … maar ook" (een `BANNED_PATTERN`), plus "essentiële" en
"waardevolle"; `VERVOLG_ALINEA_1` eindigt op een uitroepteken. Het template is daarin leidend,
dus dat blijft zo. Het is op drie plekken expliciet gemaakt, en die horen bij elkaar:

- `_all_text_fields()` scant alléén velden van de schrijver, nooit vaste tekst;
- de schrijfspec (§5, §13) zegt dat de vaste tekst geen voorbeeld is voor eigen proza;
- de beoordelingsspec verbiedt de judge er revisie op te vragen.

Haal je één van de drie weg, dan flagt elke training voor altijd zijn eigen boilerplate — of
gaat de schrijver de constructie kopiëren omdat hij hem als huisstijl leest.

### Notebook

`herschrijven.ipynb` doet hetzelfde in stappen, met `importlib.reload` zodat je edits in de
`.py`-bestanden meteen meepakt. Cellen 1, 2 en 4 doen geen API-calls: je ziet zwart op wit
welke briefing het model krijgt vóórdat de dure schrijfcalls lopen.

## Het formaat

De kopstructuur komt uit `Template trainingen nieuwe opbouw.md`: de trainingstitel is kop 1,
elk kopje is kop 2, en het bedrijfstrainingblok onder Inleiding is kop 3. Elk kopje mapt op één
veld in de CMS-`content`:

| Kopje | CMS-veld | Vorm |
| --- | --- | --- |
| Overzicht | `summary` | platte tekst |
| Inleiding | `intro` | `<p>` + `<h3>`-bedrijfstrainingblok |
| Modules | `modules` | `<p>` + geneste `<ul>` |
| Doelgroep | `target_audience` | `<p>` |
| Voorkennis | `prior_knowledge` | `<p>` |
| Aanpak | `setup` | `<p>` |
| Doelen | `objectives` | `<p>` + `<ul>` |
| Vervolgstappen | `follow_up` | `<p>` + `<ul>` + `<p>` |
| Kortste omschrijving | `summary_edudex` | platte tekst |
| Certificatie | `certification` | `<p>`, vaste tekst |

`days` wordt ongewijzigd uit de bron overgenomen.

**Eén kopje draagt nadruk binnen de tekst.** In de tweede Aanpak-alinea zijn "kennis" en
"toepassing binnen jouw organisatie en werksituatie" benadrukt; zo staat het in het template.
Tot augustus 2026 gebeurde dat cursief: `*kennis*` in de markdown, `<em>kennis</em>` in de
CMS-content. Onze site en de leerportalen geven die cursivering niet goed weer, dus staan er nu
enkele aanhalingstekens: ‘kennis’. Die zitten in de tekst zelf en niet in de opmaak, dus is er
nog maar één constante (`sjabloon.AANPAK_ALINEA_2`, ook de vorm waarop `VASTE_TEKSTEN` matcht)
en geen afgeleide platte vorm meer.

De documenten die vóór die wissel zijn weggeschreven dragen de sterretjes nog in hun
`aanpak`-veld. `uit.render_aanpak()` zet die alsnog om naar dezelfde aanhalingstekens
(`sjabloon.verquote_cursief()`), zodat een herrender geen letterlijke sterretjes toont; het
escapen gaat daaraan vooraf, zodat brontekst nooit een tag kan binnensmokkelen.

### Wat de judge ziet

De judge oordeelt over wat code niet kan: feitgetrouwheid, niveau, persona en toon. Daarvoor
krijgt hij dezelfde stijlbestanden als de schrijver, plus de kern (mét wie hem schreef), de
feiten (`bruikbaar` / `strippen` / `gaten`), de besluiten van de reviewer, **de brontekst** en
het concept — in die volgorde, met het concept achteraan omdat dat is wat hij beoordeelt.

De brontekst zat er lang niet bij, terwijl de beoordelingsspec §2 wél opdroeg elke claim te
herleiden tot "`bruikbaar` of de brontekst". De judge toetste feitgetrouwheid dus tegen de
samenvatting van de scorer: een claim die de bron tegensprak maar `bruikbaar` niet, kwam er
ongehinderd doorheen. Sinds de kern het niveau draagt weegt dat zwaarder — zwijgt de kern over
een aspect, dan had hij niets om op terug te vallen.

De bron is voor de judge de maatstaf voor **claims en niveau, niet voor vorm**. Zonder die
regel gaat hij het concept afrekenen op het niet volgen van de bronstructuur, en juist daarvan
afwijken is het hele punt van herschrijven. `BRONTEKST_UITLEG_JUDGE` staat daarom bewust anders
dan `BRONTEKST_UITLEG` voor de schrijver: die moet de bron niet overtreffen, de judge moet hem
niet napluizen op vorm.

**Een goedgekeurde actualisering gaat vóór de brontekst.** Dat is de tegenhanger van de hele
regel en staat in beide prompts én in §2 van de beoordelingsspec. Een actualisering voegt per
definitie iets toe dat niet in de bron staat — dat is waaróm hij bestaat. Met de bron als enige
maatstaf zou elke actualisering een "verzonnen feit" zijn en zou de review precies het werk
terugdraaien dat de reviewer in de sessie deed. De twijfel gaat daarom bewust één kant op: valt
een passage misschien onder een goedgekeurde actie, dan valt hij eronder. De reviewer-**voorwaarde**
is de enige grens ("prima als voorbeeld" betekent: geen eigen module).

Kosten: ~3.000 tekens per training, tegen een gecachete system-prefix van ~20k. Verwaarloosbaar
naast de schrijfcall.

### Lengtes: richtlijn met vangrail

Elk lengte-kopje heeft twee banden (`checks.BANDEN`). De **doelband** is de lengte uit de
schrijfspec; erbuiten levert het een FLAG op — zichtbaar bij review, maar de schrijver gaat er
niet voor terug. De **vangrail** is de buitengrens; pas daarbuiten is het een hard fail en
schrijft hij het kopje opnieuw.

| Kopje | Doelband | Vangrail |
| --- | --- | --- |
| Overzicht | 55–80 woorden | 45–110 |
| Inleiding | 180–210 woorden (1 dag: 170–200 · 4+ dagen: 190–230) | 150–260 (4+ dagen: 150–280) |
| Zin (lopende tekst) | ±20 woorden gemiddeld | geen — alleen een FLAG boven 35 |
| Voorkennis | zo compact mogelijk, meestal één of twee zinnen | geen — alleen een FLAG boven 45 woorden |
| Kortste omschrijving | — | **max. 200 tekens, hard** |

**Het aantal modules schuift mee met de duur** (`checks.modulesband()`), langs dezelfde lijn:
1 dag → 4–6, 2–3 dagen → 4–6, 4 dagen of meer → 5–8, onbekend → 4–6. De medianen in de eigen
catalogus lopen op met de duur (1 dag → 5, 2–3 dagen → 6, 4+ → 7); deze banden dekken 71% van
dat corpus.

Die band is in ronde 3 teruggeschroefd van 4–7 / 5–9, en dat is **een redactiebesluit en geen
corpusmeting**: het programma werd in de praktijk te lang. De bovenkant van de catalogus is
bewust niet meer de bovenkant van de band. Wie dit later leest moet niet denken dat de band per
ongeluk onder het corpus is gezakt — de ondergrens is ongewijzigd.

Wat er tegelijk veranderde: de tool-description noemt nu **één richtgetal** (4 bij één dag, 5
bij 2–3, 6 vanaf vier) in plaats van een bereik plus een "typisch aantal". Een model dat een
bereik krijgt kiest stelselmatig de bovenkant, en dat is over de hele eerste batch te zien:
Overzicht 76/76/76/78 woorden bij een band tot 80, modules 6/6/6/7 bij een band tot 7,
sub-bullets 4–5 bij een band tot 6. Het bereik staat nu alleen nog in de checks, als vangrail.

**Het Overzicht ging in reviewronde 2 van 55–65 naar 55–80.** Twee signalen wezen dezelfde kant
op. De catalogus: mediaan 64 woorden, p75 77, p90 94 — maar 29 van de 78 haalden 55–65, dus de
band stond onder de eigen praktijk. En de review: *"lengtebeperking is geen doel op zich; liever
wat langer, maar een complete intro in de materie, dan korter door de bocht"* — bij één van de
drie beoordeelde trainingen ontbrak een heel onderwerp uit het programma in het Overzicht. De
ondergrens blijft staan: te kort betekent nog steeds dat er inhoud mist. Gemeten met
`lengtes_over_goud()`: de doelband gaat van 29/78 (37%) naar 44/78 (56%), de vangrail van
65/78 naar 75/78 (96%).

**Voorkennis stond eerder op "één zin, [flag]".** Die regel flagde precies het antwoord dat de
schrijfspec zelf aanbeveelt ("Enige ervaring met […] is vereist. Mocht je hier vragen over
hebben, neem gerust contact met ons op."), want dat zijn er twee. Wat bedoeld was is *compact*,
niet *grammaticaal één zin* — vandaar nu een woordsignaal in plaats van een zinsteller. Bij
**Doelgroep** blijft de zinsteller wél staan: daar is één zin het ontwerp en is het voorbeeld
in de spec ermee in lijn.

De reden voor de marge: met één hard venster moest de schrijver op het laatste woord inkorten,
en dat kostte de zin zijn ritme en precisie — precies wat de spec elders probeert op te bouwen.
De banden liggen rond p85 van het goud (`lengtes_over_goud()`), dus ruim genoeg om een
goedgeschreven kopje niet terug te sturen en strak genoeg om een ontspoorde tekst te vangen.

**Zinslengte is nóg zachter.** De ±20 woorden uit de schrijfspec zijn een gemiddelde, geen
plafond: op het goud is de mediane zin 19 woorden, maar 41% zit erboven en p90 ligt op 27. Een
grens daar zou de bijzin wegsnijden die de gedachte compleet maakt — en de causale constructie
uit §0.12 ("doordat we X doen, kun jij Y") maakt zinnen juist langer. Er is daarom geen
vangrail, alleen een FLAG boven de 35 woorden (1% van het goud), waar het meestal om twee
gedachten in één zin gaat. Bullets tellen niet mee; dat zijn geen zinnen.

Alleen de 200 tekens van de Kortste omschrijving zijn absoluut: die grens komt van Edudex, die
langere tekst afkapt. De schrijfspec (§0.4 en §0.14) en de judge weten dit ook — de judge
oordeelt bewust niet over lengte. Het onderliggende principe staat boven aan §0 van de
schrijfspec: **bij twijfel gaat functie vóór vorm**, behalve bij de harde regels, want daar zit
geen stijlafweging in.

### De taalchecks uit reviewronde 2

Vier signalen, alle vier **FLAG** — ze gaan naar de judge en de menselijke review en nooit terug
naar de schrijver. Dat is bewust: elk van de vier vraagt een oordeel dat een regex niet kan
geven. Tussen haakjes staat hoe vaak ze op de eigen 78 vuren, want dat is de maat voor of een
check ruis of signaal is.

| Check | Wat | Op het goud |
| --- | --- | --- |
| `anglicisme` | Letterlijk vertaalde constructies ("werk je door de …", "onderscheid kennen") en leenwoorden met een schone Nederlandse tegenhanger ("skills", "stakeholders"). Lijst in `humanisering_nl.md` §G. Hooguit één per veld. | 25/78 |
| `zwakke_formulering` | Beloftes "aan de onderkant": "plaatsen", "in elkaar zit", "meepraten", "zelfstandig". De boodschap noemt het sterkere alternatief. | 18/78 |
| `contactzin_zonder_dan` | "neem gerust contact" i.p.v. "neem **dan** gerust contact". | 35/78 |
| `geen_na_deze_training` | De Kortste omschrijving mist "Na deze training …" na de openingsvraag. | 77/78 |

Die laatste twee getallen zijn hoog en dat klopt: het zijn nieuwe regels, geen corpuspatronen.
Bij `anglicisme` is de lijst juist wél op het corpus gekalibreerd — wat een echte vakterm kan
zijn blijft eruit (`best practices` 13/78 en het staat in onze eigen schrijfspec, `governance`
15/78, `compliance` 7/78, `performance` 8/78). Voeg je een patroon toe, meet het dan eerst over
`herschreven/goud/`; vuurt het vaker dan ongeveer één op de vijf, dan is het waarschijnlijk een
vakterm.

**Wat bewust géén check is geworden.** De slotzin-constructie ("Hierdoor ben je in staat om …"
i.p.v. een kaal "Hierdoor kun je …") staat in 0 van de 78 slotzinnen — een check zou het hele
corpus flaggen én elke training in dezelfde zin duwen, precies wat `stijlregister_nl.md` §B
afraadt. Lange samenstellingen ("datamodellerings-software") evenmin: bij ≥22 tekens raakt dat
45 van de 78 trainingen met woorden die niemand splitst ("organisatievraagstukken"). En de
dubbeling tussen Doelgroep en Voorkennis is met een regex fragieler te vangen dan het probleem
groot is. Alle drie staan in de schrijfspec en in de beoordelingsspec, waar een oordeel wél kan.

### De check uit ronde 3: de tweede zin van het Overzicht

`tweede_zin` (FLAG) vuurt als de zin ná de "Wil je …"-vraag niet begint met "In deze training"
of "Tijdens deze training" (een bijvoeglijk naamwoord ertussen mag, en "masterclass"/"workshop"
ook). Ruis op de eigen 78: **2/78** — de laagste van alle checks tot nu toe, en dat is precies
het punt. Dit is geen nieuwe regel maar een **bestaand catalogus-patroon dat we kwijt waren**:
73 van de 78 doen het al zo, en geen van de vier few-shots deed het nog. De eerste batch nam dat
een-op-een over — vier keer "Je leert …" / "Je werkt met …" — waardoor het antwoord los kwam te
staan van de vraag erboven.

Dat is de bredere les van die batch: **de few-shot is 6% van de prompt en stuurt aantoonbaar
meer dan de 94% proza eromheen.** Wijkt de output ergens systematisch af, kijk dan eerst naar
`bouw_goud_v2.py` en pas daarna naar de spec. `bouw_goud_v2.py` print daarom sinds ronde 3 een
**vormprofiel** per voorbeeld: dagen, aantal modules met de band erbij, sub-bullets per module,
woorden in het Overzicht en de tweede zin. De checks bewijzen dat een voorbeeld de regels haalt;
dit laat zien of het ook de goede kant van de band demonstreert.

### De checks uit ronde 4: em-dash, reikwijdte en de groep-intro's

De eerste batch die zélf goud werd. Drie bevindingen, drie soorten oplossing:

| Bevinding | Wat er is gebeurd |
| --- | --- |
| Twee em-dashes in één training | `check_em_dash` (**HARD**) over alle schrijversvelden plus de titel, en de regel is verhuisd van `humanisering_nl.md` §C (flag-instructie) naar §D (hard verbod). |
| Een opsomming die breedte toonde, werd een afbakening | Regels in schrijfspec §0.24 en §12, in de beoordelingsspec §2 en als paar in `correcties_nl.md` §26, plus `check_reikwijdte` (**FLAG**) als leesbril. |
| Een groep-intro bij Vervolgstappen met één training eronder | Deterministische reparatie in `kies_vervolgtrainingen` (`snoei_groepen`), een vangnet in de renderers (`uit.bruikbare_groepen`) en `groep_te_klein` (**FLAG**) voor wat er buiten om komt. |

**De em-dash is het duidelijkste voorbeeld van "de prompt is het probleem".** Het verbod stond
er al sinds ronde 1, maar de vijf spec-bestanden bevatten er samen **173** — de schrijfspec
alleen al 86. Een model dat om een stijl vraagt kijkt naar wat het ziet, niet naar wat het
leest. Ze staan er nu uitgeschreven ("[liggend streepje]"), inclusief de foute voorbeelden in
`correcties_nl.md`, en `test_geen_liggend_streepje_in_de_promptbestanden` plus
`test_geen_liggend_streepje_in_de_systemprompts` houden dat zo — die tweede dekt ook de kop
boven de few-shot en het voorbeeldmateriaal zelf.

De reikwijdte-bevinding is de subtielste van de drie en de enige die geen betrouwbare check kan
krijgen: "Wil je …, dan …" is in §9 juist vóórgeschreven voor groep-intro's. `check_reikwijdte`
vuurt daarom alleen op een voorwaarde met een opsomming van drie of meer elementen erin, en
altijd als FLAG. Alleen wie de bron ernaast legt, kan het beslissen.

De groep-reparatie laat een eenzame titel liever vallen dan hem te verhuizen naar de andere
groep: die groep heeft zijn eigen intro, en een training die daar inhoudelijk niet onder valt
maakt die intro onwaar. Blijven er in totaal minder dan drie titels over, dan vervallen de
groepen helemaal en valt de weergave terug op één lijst onder "Zo bieden we onder andere:".

### De check uit ronde 5: de deelnemer brengt geen eigen case mee

Training 3036 (Change Management voor DAMA-DMBOK) beloofde twee keer dat je je eigen case
inbrengt: "past alles toe op je eigen praktijkcase" in het Overzicht en de modulebullet "Een
eigen veranderopgave rond datamanagement inbrengen". Dat kan alleen bij een bedrijfstraining,
en die staat al als apart blok onder de Inleiding. In de standaard beschrijving is het een
belofte die we niet nakomen.

**Dit is de eerste bevinding waarbij de bron de schrijver actief het verkeerde in duwt.** Er
stond wél iets over "jouw praktijkcase", maar dat betekende dat je een praktijkcase *krijgt*
om aan te werken, niet dat je er zelf een aanlevert. Het concept las het als bezit. Daarom is
de regel op drie plekken zo geformuleerd dat de bron hem niet kan overrulen, en staat hij in
de beoordelingsspec expliciet als uitzondering: de judge mag een concept dat er "een
praktijkcase" van maakt níét afrekenen als afwijking van de bron.

`check_eigen_case` (**HARD**) heeft daarom drie patronen, en het meten dwong de grens af:

| Patroon | Wat het vangt | Over het goud |
| --- | --- | --- |
| `_INBRENG_RE` | een inbreng-werkwoord met eigen werkmateriaal, in beide volgordes en gescheiden ("brengt … in", "levert … aan") | 1 van de 78 |
| `_EIGEN_CASE_RE` | een bezittelijk voornaamwoord op een case, ook zonder werkwoord | 7 van de 78 |
| `_CASE_HERKOMST_RE` | het bezit een zelfstandig naamwoord verderop: "casussen uit je eigen praktijk" | 0 van de 78 |

**Het derde patroon en de koppeltekens komen uit een tweede ronde, en die is leerzaam.** Bij het
kiezen van nieuw goud bleek dat een van de vier zittende few-shot-voorbeelden de fout zélf
demonstreerde terwijl de check zweeg: 3127, met "Je eigen document, proces of werkvraag
inbrengen", "casussen uit je eigen praktijk" en "je eigen AI-toepassingscasus". Drie gaten
tegelijk: "document", "proces" en "werkvraag" stonden niet in de materiaallijst, het bezit kon
een naamwoord verderop staan, en `\w` matcht geen koppelteken, waardoor "AI-toepassingscasus" en
"je use-case" erlangs glipten. De woordklassen gebruiken nu `[\w-]`. Dit is dezelfde les als bij
de em-dash: **een check die de few-shot moet bewaken, moet eerst op de few-shot zelf worden
losgelaten.**

De objectlijst van het eerste patroon is bewust beperkt tot werkmateriaal (case, opgave,
opdracht, vraagstuk, dataset, project, data, code). Neem je "situatie" en "voorbeeld" mee, dan
gaat hij van 1 naar 5 over het goud, en die vier extra zijn allemaal terecht: "ruimte voor
vragen en het inbrengen van eigen situaties" gaat over het gesprek in de zaal, en dat belooft
`sjabloon.AANPAK_ALINEA_1` zelf al ("veel ruimte voor jouw vragen en werksituatie"). Precies
die zin mag nooit vuren, want dan faalt elke training; een test bewaakt dat over alle
`VASTE_TEKSTEN`.

Het tweede patroon vraagt om het bezit en niet om het onderwerp, want alleen dát verschuift de
belofte: "een praktijkcase" leveren wij, "je eigen praktijkcase" komt van de deelnemer. Dat het
goud er zeven bevat, waaronder "jullie eigen casussen" en "jullie eigen praktijkcase", maakt het
juist een fout en geen corpusconventie — het goud is meetlat, geen norm. Bij 3036 vuurt de
check op zes plekken, vier meer dan de reviewer had aangewezen: ook de Inleiding, de
moduletitel "Jouw praktijkcase" en een bullet met "je case".

**De grens aan de andere kant is even belangrijk, en die is bij de review expliciet
scherpgesteld.** Er zijn twee dingen die op elkaar lijken en tegengesteld zijn:

| | Wie levert het? | Mag het in de tekst? |
| --- | --- | --- |
| De **case** waaraan gewerkt wordt | wij | ja, mits zonder bezittelijk woord: "een praktijkcase" |
| Wat de deelnemer daar **mee bouwt** | de deelnemer, tijdens de training | ja, en het hóórt er te staan |

796 is het schoolvoorbeeld van de tweede rij: "de belangrijkste patronen te benoemen, toe te
passen en in een praktijkcase te verwerken tot een **eigen applicatie**". Daar wordt niets
ingebracht. Wíj geven de praktijkcase, en dat de deelnemer daarna een eigen applicatie bouwt is
precies de oefening waarvoor hij komt: die belofte moeten we juist wél doen. Hetzelfde geldt voor
"een roadmap opstellen voor een SIEM-oplossing binnen je eigen organisatie" (2725) en "een
monitoring-strategie voor je eigen AWS-omgeving" (2808).

**De richting beslist, niet het woord "eigen".** Materiaal dat naar binnen komt is verboden;
materiaal dat eruit komt is de opbrengst. Dat is de reden dat `_INBRENG_RE` verplicht een
inbreng-werkwoord eist en `_EIGEN_CASE_RE` alleen op de case-familie kijkt en niet op
"applicatie", "roadmap" of "strategie". Een test legt die zinnen letterlijk vast, want dit is de
grens die het makkelijkst meeschuift zodra iemand de patronen verbreedt.

De idioomval zit bij de gescheiden werkwoordsvorm: "in kaart brengen" staat er met een
negatieve lookahead uit.

### Drie tiers: wat komt er in de kolom die een reviewer leest

HARD versus FLAG beantwoordt de vraag *wie lost dit op*. Na de eerste zestien trainingen bleek er
een tweede vraag onder te zitten die daar niet in past: *wat moet een mens hiervan lezen?* Die
zestien leverden 34 flags op, en de verdeling was scheef:

| Code | Flags | Trainingen (van 16) |
| --- | ---: | ---: |
| `lengte_richtlijn` | 13 | 11 |
| `zwakke_formulering` | 8 | 6 |
| `anglicisme` | 5 | 3 |
| `lerend_aspect` | 3 | 3 |
| `zin_lang` | 3 | 3 |
| `voorkennis_lang`, `llm_taal` | 1 + 1 | 1 + 1 |

**Twee codes waren 62% van alles wat een reviewer las.** En geen van die dertien lengtes zat in
de buurt van de vangrail: Overzicht 81 t/m 86 op een band van 55-80 met vangrail 110, Inleiding
212 t/m 231 op 180-210 met vangrail 260, twee ervan één woord over de grens. De boodschap zei het
zelf al ("alleen bijstellen als de tekst er beter van wordt"). Van de acht
`zwakke_formulering`-meldingen waren er zeven letterlijk het woord "zelfstandig". Een kolom die
voor twee derde uit zulke regels bestaat, wordt niet meer met aandacht gelezen — en dan sneuvelt
óók de opmerking die er wél toe deed.

De verleiding is dan om de lengteband te verruimen: de eigen output zit op mediaan 76 woorden
(Overzicht) en 204 (Inleiding), met p75 op 82 respectievelijk 213, dus de band snijdt
systematisch de bovenste kwart van een verder prima verdeling af. Toch is dat de verkeerde fix.
De band is gekalibreerd op het **goud**, niet op onszelf; hem verschuiven omdat onze eigen
schrijver erboven zit, is in een kringetje meten. De flag klopt — hij hoort alleen niet in de
kolom die om een oordeel vraagt.

Vandaar een derde as naast `section` en `severity`: de **tier**, afgeleid van de code via
`TIER_PER_CODE` in `rewrite_checks.py`.

| Tier | Wat erin staat | Wat een reviewer ermee moet |
| --- | --- | --- |
| `hoog` | Kan een echte fout zijn: `lerend_aspect`, `reikwijdte`, `actie_escalatie`, `zwakke_formulering`, `marketing`, `llm_taal`, `vaag`, `groep_te_klein`, `tweede_zin`, `een_zin`, `geen_na_deze_training` | Lezen en oordelen |
| `mechanisch` | Eén woord vervangen, het alternatief staat in de melding: `anglicisme`, `contactzin_zonder_dan`, `soortwoord_hoofdletter`, `meeting`, `u_vorm`, `dubbel_in_staat` | Doorvoeren, geen oordeel nodig |
| `laag` | Een meting buiten de richtlijn maar binnen de vangrail, of telemetrie: `lengte_richtlijn`, `zin_lang`, `voorkennis_lang`, `invulling_voegwoord`, `catalogus_ontbreekt` | Niets |

Drie keuzes daarin zijn niet vanzelfsprekend:

- **een onbekende code is `hoog`.** Een nieuwe check komt binnen als werk voor een mens en zakt
  pas als de meting laat zien dat hij vaak vuurt zonder dat er iets mis is. Dezelfde richting als
  bij HARD/FLAG: liever te veel laten zien dan iets verstoppen. `test_elke_tier_code_bestaat_ook_echt_als_check`
  bewaakt de andere kant — een typefout in de tabel laat een code stil op `hoog` staan, en dat
  zou nergens opvallen omdat de kolom dan gewoon weer even lang is;
- **`invulling_voegwoord` is `laag` en geen `mechanisch`**, want de code heeft het al weggehaald
  (`sjabloon.schoon_invulling`). De melding bestaat om te zien of de tool-description werkt; dat
  is telemetrie over de prompt en geen opdracht aan een mens;
- **dezelfde opmerking in twee kopjes wordt één regel.** Training 27 kreeg "zelfstandig" in het
  Overzicht én de Inleiding, 3159 in de Modules én de Doelen: één beslissing, geen twee.
  `per_tier()` vouwt ze samen tot "overzicht + inleiding: …", hoofdletterongevoelig, want de
  boodschap citeert het gevonden woord en dat staat aan het begin van een zin met een hoofdletter.

Netto over dezelfde zestien trainingen: **10 opmerkingen in `flags_hoog`** (van 34), 3 in
`flags_mechanisch`, 17 in `flags_laag` — en zeven van de zestien trainingen houdt een lege
`flags_hoog`. Dat is de kolom die naar de reviewers gaat.

Op het `overnemen`-pad gaan ook de HARD-issues de kolom in: daar komt de schrijver er niet aan te
pas, dus ze zijn signaal en geen revisie-opdracht. `per_tier()` kent geen tier voor een HARD-code
en zet ze daarmee vanzelf op `hoog`. Het wijzigingsspoor van dat pad is óók gesplitst: een
gewijzigde titel of een doorgevoerde actualisering is `hoog` (daar heeft een mens voor getekend),
bijgewerkte vaste teksten en genormaliseerde vervolgtitels zijn `laag` (die komen deterministisch
uit `sjabloon` en kunnen daar niet fout gaan).

### De scoresheet-val

De pijplijn heeft twee scoresheets: het ruwe (`SCORED`) en dat wat sectie 3b oplevert
(`scoresheet_met_modus.xlsx`). Alles ná 3b hoort het tweede te lezen. Deed het notebook dat niet,
dan draaide de hele batch stil op de defaults — `modus` viel via `herschreven=0` terug op
`volledig` en `modules_nb` op `stabiel` — terwijl 3b iets anders had voorgesteld. Dat is één keer
gebeurd en kostte een batch: drie van de vier trainingen werden volledig herschreven terwijl de
scan `format` voorstelde, en een training met `modules_nb_voorstel = actueel` kreeg toch de
stabiele NB.

`_load_scored()` waarschuwt daarom naar stderr zodra `modus_voorstel`, `modules_nb_voorstel` of
een van de vier reviewerkolommen ontbreekt, mét de gevolgen erbij. `modus_voorstellen()` zelf
roept hem aan met `waarschuw=False`: dat is de stap die die kolommen juist máákt.

## Tests

```bash
python bouw_goud_v2.py     # terugval-few-shot opbouwen; `herschreven/` is gitignored, dus dit
                           # hoort bij een verse checkout. Geen API-key nodig.
python test_rewrite.py     # 236 offline checks, geen API-key nodig
```

Getest wordt de deterministische laag: de code-check, de structurele splitsing van
`actie_besluit` (met de echte strings uit het sheet als fixtures) en de CMS-output. De
LLM-classificatie erboven blijft bewust ongetest — die vraagt een API-call.

## Vervolgstappen: twee trappen

`vervolgtraining.json` is de catalogus: 779 trainingen als `{product_id, titel, summary}`.
Samen ~89k tokens, dus die gaat **nooit** naar de API. In plaats daarvan:

1. **Python** (`shortlist_vervolgtrainingen`) — IDF-gewogen keyword-overlap over titel +
   summary, aangevuld met vakgenoten uit de taxonomieboom, levert ~30 kandidaten. De training
   zelf valt af op `product_id` en op titel; elke gescoorde training staat namelijk ook in de
   catalogus. Nul API-kosten.
2. **Haiku** (`kies_vervolgtrainingen`) — kiest uit die 30 er 3-6 en verdeelt ze over één of
   twee groepen met een eigen intro-zin, zoals in het goud. ~$0,003 per training.

De code-check blijft de poort: elke titel moet letterlijk in de catalogus staan, dus het model
kan er geen verzinnen. Levert stap 2 niets bruikbaars, dan valt het terug op de shortlist.

### De taxonomieboom

`vervolgtrainingen_tree.json` deelt het aanbod in als domein > subdomein > onderwerp
(13 domeinen, 69 subdomeinen), en hangt een training desgewenst in meerdere takken.
`load_tree()` koppelt elk blad aan een catalogusrij en gooit de rest weg — wat niet resolvet,
kan de code-check niet passeren. De koppeling gaat eerst op genormaliseerde titel en daarna op
een variant zonder spaties en interpunctie ("Claude Co-Work" ↔ "Claude CoWork",
"Timemanagement" ↔ "Time management"); levert die variant meer dan één catalogusrij op, dan
wordt hij overgeslagen — "C# Professional" en "C++ Professional" vallen anders samen.

Keyword-overlap en boom worden bewust **samen** gebruikt, want ze falen op verschillende
manieren. Keywords boden XSL "Interieurdesign met Vectorworks" en "Big Data in de Zorg" aan;
de boom levert daar Web Development. Omgekeerd hangt LDAP onder Netwerken, waardoor de boom
5G en breedband voorstelt terwijl de beste vervolgstap (Active Directory) onder Identity staat.
De shortlist reserveert daarom `N_KEYWORD_GARANTIE` plekken voor de sterkste keyword-treffers
en vult de rest op een gemengde score. Over de 51 te herschrijven trainingen zakte het aandeel
kandidaten buiten het eigen vakgebied daarmee van 55% naar 24%, en houden alle 50 trainingen
die in de boom staan minstens één vakgenoot op de shortlist (was: 6 zonder).

Het vakgebied gaat als label mee in de prompt — `Node.js [Software Development > Web
Development]` — zodat het model de twee groepen langs echte vakgrenzen legt in plaats van op
gevoel. Het label mag nooit in een titel terugkomen; doet het dat toch, dan strippen we het
vóór de controle tegen de shortlist.

Bij het laden gaat elke catalogustitel door `sjabloon.vervolgtitel()`: het voorvoegsel
"Training" (en elk verboden soortwoord) valt weg, want in een lijst onder het kopje
Vervolgstappen staat het bij elke regel. Een afwijkende vorm blijft staan — "Masterclass PHP",
"Workshop Storytelling", "Examentraining CEH". Omdat de shortlist, de Haiku-call, de code-check
én de output allemaal dezelfde genormaliseerde titel zien, kan er geen vorm tussendoor glippen.

## Status

End-to-end werkend.
