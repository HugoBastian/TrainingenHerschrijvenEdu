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
| `humanisering_nl.md` | NL LLM-taal regels + machine-leesbare `BANNED_PATTERNS`. |
| `besluiten.py` | De besluitenlaag: `actie_besluit` → expliciete doen/niet/mits per actie. |
| `rewrite_checks.py` | Deterministische code-check: `Issue`-lijsten, hard-fail vs flag. |
| `rewrite_output.py` | Document → CMS-`content`-JSON (HTML) en → markdown met kop 1/2/3. |
| `rewrite_trainings.py` | Hybride schrijver + orchestratie + I/O. |
| `herschrijven.ipynb` | Notebook om de pijplijn stap voor stap te draaien en te inspecteren. |
| `test_rewrite.py` | 69 offline tests (geen API-key nodig). |

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
rw.checks_over_goud()   # hoe vaak faalt elke harde regel op de 78 trainingen?
```

Van de 78 halen er **4** élke harde check; die vier staan in `GOUD_VOORBEELDEN` en gaan als
few-shot mee in de gecachete system-prefix van de schrijver. De rest is meetlat: valt een regel
bij meer dan de helft van het corpus om, dan is de regel verdacht en niet de training (59 van
de 78 falen bijvoorbeeld de Inleiding-lengte). Verander je een check, draai dit dan opnieuw en
werk `GOUD_VOORBEELDEN` bij.

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

## Tests

```bash
python test_rewrite.py     # 69 offline checks, geen API-key nodig
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
