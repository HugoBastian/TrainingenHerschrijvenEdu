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
| `test_rewrite.py` | 42 offline tests (geen API-key nodig). |

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
  `toegepaste_acties`, flags;
- `herschreven.xlsx`, tabblad **cms** — `id` / `name` / `content`, met dezelfde
  JSON-structuur als het bronsheet, zodat het zo terug het CMS in kan. Alleen `approved`;
- `herschreven.xlsx`, tabblad **review** — status, flags en elk kopje in platte tekst, met een
  lege `approve_edit`-kolom.

De run **hervat** standaard: trainingen die al in `herschreven.xlsx` staan worden overgeslagen,
en `herschreven=1` (al in de nieuwe stijl) wordt sowieso niet opnieuw gegenereerd. Gebruik
`--no-append` om te overschrijven.

### 3. Goud-corpus

```bash
python rewrite_trainings.py --goud --source "/pad/naar/bronsheet.xlsx" --out-dir herschreven
```

Schrijft de trainingen met `herschreven=1` weg naar `herschreven/goud/<id>.json`. Dat is
referentiemateriaal om spec en judge aan te kalibreren — géén voorschrift; het template en de
schrijfspec zijn leidend.

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
python test_rewrite.py     # 42 offline checks, geen API-key nodig
```

Getest wordt de deterministische laag: de code-check, de structurele splitsing van
`actie_besluit` (met de echte strings uit het sheet als fixtures) en de CMS-output. De
LLM-classificatie erboven blijft bewust ongetest — die vraagt een API-call.

## Status

End-to-end werkend. Nog open:

1. **`vervolgtraining_catalog.json`** — echte titels (titel/categorie/populariteit/omschrijving).
   Zonder dit blijven de Vervolgstappen-titels leeg en geflagd.
   `~/Downloads/Vervolgtrainingen (cleaned).xlsx` lijkt de bron.
2. **Goud-corpus benutten** — de 78 geëxporteerde trainingen zijn nu alleen referentie; ze
   worden nog niet als few-shot of regressietest gebruikt.
