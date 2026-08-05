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
| `rewrite_output.py` | Document → CMS-`content`-JSON (HTML) en → markdown met kop 1/2/3. |
| `rewrite_trainings.py` | Hybride schrijver + orchestratie + I/O. |
| `herschrijven.ipynb` | Notebook om de pijplijn stap voor stap te draaien en te inspecteren. |
| `test_rewrite.py` | 110 offline tests (geen API-key nodig). |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Zet je API-sleutel in een `.env` (of de omgeving):

```
ANTHROPIC_API_KEY=...
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
| **scoresheet** | Alle scorer-velden + de handmatig ingevulde kolommen `actie_besluit`, `kern_reviewer`, `modus_reviewer` en `guidance_reviewer` + `herschreven`. |
| **bronsheet** | `id`, `name`, `herschreven`, `content` (JSON-string met de CMS-velden). |

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

### 1c. De mate van aanpassing kiezen

```bash
python rewrite_trainings.py --scan-modus scoresheet_met_voorstel.xlsx \
  --scored "scoresheet.xlsx" --source "/pad/naar/bronsheet.xlsx"
```

Vult per training `modus_voorstel` en `modus_reden` en levert lege kolommen `modus_reviewer` en
`guidance_reviewer` aan. Jij kijkt het voorstel na en vult `modus_reviewer` waar je het er niet
mee eens bent; leeg laten betekent "voorstel is goed". Zie **De mate van aanpassing** hieronder
voor wat de vier niveaus betekenen. `--geen-llm` slaat de modelcall over en laat alleen de
deterministische ondergrens staan — bruikbaar als kalibratie, niet als voorstel.

`guidance_reviewer` is vrije tekst en gaat letterlijk mee naar de schrijver, achter de
`rewrite_guidance` van de scorer en met het label dat hij vóór gaat: "modules 3 en 4 overlappen",
"de inleiding mag blijven zoals hij is".

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
- `herschreven.xlsx`, tabblad **review** — status, `modus`, `modus_voorstel`, `spec_versie`, flags
  en elk kopje in platte tekst, met een lege `approve_edit`-kolom en als laatste kolom de
  **brontekst**. Die staat er zodat je een claim of een opgeschoven niveau naast het origineel kunt
  leggen; zonder die kolom lees je alleen de nieuwe tekst en is precies de fout onzichtbaar die de
  judge net doorliet.

`spec_versie` is een korte hash over de schrijfspec, `humanisering_nl.md`, `stijlregister_nl.md` en
het template. Zodra die bestanden veranderen is "welke goedgekeurde trainingen dateren van vóór de
huidige regels" precies de vraag die bepaalt wie er een `stijl`-ronde in moet — met deze kolom is
dat een filter, zonder is het een gok op bestandsdatums.

Trainingen op modus `overnemen` worden **niet** herschreven maar wél doorgezet (status
`overgenomen`), zodat `herschreven.xlsx` één compleet CMS-document is. Het scoresheet bepaalt wat
erin hoort. De run **hervat** verder standaard: wat al in `herschreven.xlsx` staat wordt
overgeslagen. Gebruik `--no-append` om te overschrijven.

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
niet meer uit dit corpus; `GOUD_VOORBEELDEN` verwijst naar `herschreven/goud_v2/`, en die vier
gaan mee in de gecachete system-prefix van de schrijver (een wisselende selectie zou die cache
waardeloos maken). Dit corpus is nu puur meetlat: valt een regel bij meer dan de helft van het
corpus om, dan is de regel verdacht en niet de training. Zo is de lengte-check ontstaan — met
één hard venster van 55–65 woorden viel 65% van het goud om op het Overzicht, dus is die
lengte nu een richtlijn met een vangrail eromheen (zie hieronder). Verander je een check, draai
dit dan opnieuw en werk `GOUD_VOORBEELDEN` bij.

**De modules tellen sinds kort mee, en dat verschoof dit beeld.** Zolang `goud_naar_check_input`
de modulestructuur oversloeg — geneste `<ul>` betrouwbaar terugparsen leek meer valkuil dan
antwoord — waren `modules_aantal`, `bullets_aantal` en `bullets_variatie` op het goud onzichtbaar.
Met een diepteteller in plaats van een regex is die structuur wél te lezen (75 van de 78), en
zakte het aantal schone trainingen van 26 naar 7. Drie van de vier oude `GOUD_VOORBEELDEN` (2730,
3101, 3125) vielen daardoor af: die demonstreerden modules met twee sub-bullets terwijl de spec er
3–6 eist. Een few-shot die de eigen regel schendt is erger dan geen few-shot, dus de selectie is
vervangen door 3046, 3146, 2737 en 2586.

Datzelfde corpus heeft de modulesband bijgesteld. Met de oude vaste 4–6 viel `modules_aantal`
**25 keer** om; met de duurafhankelijke band (zie "Lengtes: richtlijn met vangrail") nog **14
keer**, vrijwel allemaal programma's van 8 of meer. De regel was smaller dan de eigen praktijk,
en dat trok de schrijver stelselmatig naar de bovengrens.

Wat daarbij hoort als open vraag aan de spec, niet als oordeel over het goud: `bullets_aantal`
valt **172 keer** om over 78 trainingen. Dat is precies het soort cijfer waarvoor de regel hierboven
bedoeld is. `checks_over_goud()` rapporteert daarom ook hoeveel trainingen schoon zijn als je de
modules-checks weglaat, zodat beide getallen naast elkaar staan.

Het goud dateert van vóór de huidige Doelen-introzin: 47 van de 78 openen nog met "Na deze
training heb je handvatten om:". `goud_voorbeelden()` vervangt die regel bij het opbouwen van
de few-shot door `sjabloon.DOELEN_INTRO`. Dat is sinds de overstap naar `goud_v2` een vangnet
in plaats van een noodzaak — het blijft staan voor wie `goud_dir` terugzet op het oude corpus.

### 3b. Few-shot: `goud_v2` (tijdelijk)

```bash
python bouw_goud_v2.py     # herbouwt herschreven/goud_v2/ uit de bron in het script
```

Vier trainingen die als eerste door de nieuwe pipeline gingen en daarna zijn nagelezen: PHP
Professional, Data Modeling, Big Data Foundation en JavaScript Design Patterns. Alle 45
comments uit die ronde zijn erin verwerkt, plus de regels die daaruit zijn gedistilleerd. Ze
staan in het nieuwe template, halen alle harde checks en gaan als few-shot mee in de
gecachete system-prefix van de schrijver.

**Dit is bewust tijdelijk** (`GOUD_V2_INTERIM = True`). Het is *gerepareerd* materiaal, niet
materiaal dat vanaf de eerste zin volgens deze regels is geschreven — en dat verschil zie je
terug in wat een few-shot voordoet. Het vervangingspad:

1. een batch van 10–15 trainingen draaien met de huidige spec;
2. `checks_over_goud(rw.GOUD_V2_DIR)` erover;
3. 3–4 laten aftekenen door de schrijfstijl-eigenaar;
4. die in `herschreven/goud_v2/` zetten, deze vier eruit, `GOUD_V2_INTERIM = False`.

`test_fewshot_haalt_zelf_alle_harde_checks` bewaakt intussen dat het voorbeeldmateriaal zijn
eigen regels haalt. Verander je een regel en valt die test om, dan is het voorbeeld het
probleem — niet de test.

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
1 dag → 4–6, 2–3 dagen → 4–7, 4 dagen of meer → 5–9, onbekend → 4–7. Dit is een vangrail en
geen doel; de schrijfspec en de tool-description noemen daarnaast een *typisch* aantal, want een
model dat alleen een bereik krijgt kiest stelselmatig de bovenkant. De vorige vaste band van
4–6 stond smaller dan de eigen catalogus: van de 71 bestaande nieuwe-stijl trainingen met een
genest programma viel 31% erbuiten, vrijwel allemaal erboven (7 t/m 10). De medianen lopen op
met de duur (1 dag → 5, 2–3 dagen → 6, 4+ → 7); de huidige banden dekken 85% van dat corpus.

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

## Tests

```bash
python bouw_goud_v2.py     # few-shot opbouwen; `herschreven/` is gitignored, dus dit hoort
                           # bij een verse checkout. Geen API-key nodig.
python test_rewrite.py     # 186 offline checks, geen API-key nodig
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
