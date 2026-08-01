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
| `test_rewrite.py` | 80 offline tests (geen API-key nodig). |

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
| **scoresheet** | Alle scorer-velden + de handmatig ingevulde kolom `actie_besluit` + `herschreven`. |
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
- `herschreven.xlsx`, tabblad **review** — status, flags en elk kopje in platte tekst, met een
  lege `approve_edit`-kolom.

Trainingen met `herschreven=1` worden **niet** herschreven maar wél ongewijzigd doorgezet
(status `overgenomen`), zodat `herschreven.xlsx` één compleet CMS-document is. Het scoresheet
bepaalt wat erin hoort. De run **hervat** verder standaard: wat al in `herschreven.xlsx` staat
wordt overgeslagen. Gebruik `--no-append` om te overschrijven.

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
schrijfspec zijn leidend. Het corpus heeft twee toepassingen:

```python
rw.checks_over_goud()    # hoe vaak faalt elke harde regel op de 78 trainingen?
rw.lengtes_over_goud()   # hoe lang zijn de kopjes werkelijk? -> kalibratie van de lengtebanden
```

Van de 78 halen er **23** élke harde check; vier daarvan staan in `GOUD_VOORBEELDEN` en gaan
als few-shot mee in de gecachete system-prefix van de schrijver (een wisselende selectie zou
die cache waardeloos maken). De rest is meetlat: valt een regel bij meer dan de helft van het
corpus om, dan is de regel verdacht en niet de training. Zo is de lengte-check ontstaan — met
één hard venster van 55–65 woorden viel 65% van het goud om op het Overzicht, dus is die
lengte nu een richtlijn met een vangrail eromheen (zie hieronder). Verander je een check, draai
dit dan opnieuw en werk `GOUD_VOORBEELDEN` bij.

Het goud dateert van vóór de huidige Doelen-introzin: 47 van de 78 openen nog met "Na deze
training heb je handvatten om:". `goud_voorbeelden()` vervangt die regel bij het opbouwen van
de few-shot door `sjabloon.DOELEN_INTRO`, zodat het voorbeeld niet de zin demonstreert die de
schrijfspec verbiedt. De bullets eronder staan al in de te-infinitief en lopen ongewijzigd door.

### De stijl-lagen

Stijl zit bewust in drie soorten lagen, met een strikte werkverdeling:

| Laag | Waar | Voor |
| --- | --- | --- |
| Vaste tekst | `sjabloon.py` | Zinnen die de code invoegt; de schrijver raakt ze niet aan. |
| Prompt | `schrijfspec` (regels) · `humanisering_nl.md` (verboden) · `stijlregister_nl.md` (register) | Alles wat oordeel vraagt. Gaat naar schrijver én judge. |
| Check | `rewrite_checks.py` | Alleen wat deterministisch te betrappen is. |

Positieve stijlvoorkeur ("gebruik een vergrotende trap waar het doel begrip is") hoort per
definitie in de promptlaag: code kan niet zien of een woord raak gekozen is. Een verbodslijst
hoort in beide — de tekst in `humanisering_nl.md`, de regex in `rewrite_checks.py`. Die twee
zijn een handmatige spiegel; wijk je in de één af, dan lopen schrijver en check uiteen.

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

### Lengtes: richtlijn met vangrail

Elk lengte-kopje heeft twee banden (`checks.BANDEN`). De **doelband** is de lengte uit de
schrijfspec; erbuiten levert het een FLAG op — zichtbaar bij review, maar de schrijver gaat er
niet voor terug. De **vangrail** is de buitengrens; pas daarbuiten is het een hard fail en
schrijft hij het kopje opnieuw.

| Kopje | Doelband | Vangrail |
| --- | --- | --- |
| Overzicht | 55–65 woorden | 45–90 |
| Inleiding | 180–210 woorden (1 dag: 170–200 · 4+ dagen: 190–230) | 150–260 (4+ dagen: 150–280) |
| Kortste omschrijving | — | **max. 200 tekens, hard** |

De reden voor de marge: met één hard venster moest de schrijver op het laatste woord inkorten,
en dat kostte de zin zijn ritme en precisie — precies wat de spec elders probeert op te bouwen.
De banden liggen rond p85 van het goud (`lengtes_over_goud()`), dus ruim genoeg om een
goedgeschreven kopje niet terug te sturen en strak genoeg om een ontspoorde tekst te vangen.
Alleen de 200 tekens van de Kortste omschrijving zijn absoluut: die grens komt van Edudex, die
langere tekst afkapt. De schrijfspec (§0.14) en de judge weten dit ook — de judge oordeelt
bewust niet over lengte.

## Tests

```bash
python test_rewrite.py     # 95 offline checks, geen API-key nodig
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
