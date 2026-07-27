# Trainingen Herschrijven

Herschrijft Eduvision-trainingen naar de nieuwe stijl (9 kopjes) op basis van de
**brontekst** + het **score-oordeel** uit het scoring-project. Tweede helft van de
pijplijn; de eerste helft (scoren + informatie verzamelen) leeft in het
scoring-project (`../Trainingen Scoren/TrainingenScorenEdu`).

Pijplijn per training:

```
briefing (feiten + reviewer-besluiten)
  -> schrijver (LLM, submit_rewrite)         # alleen de generatieve kopjes
  -> code-check (deterministisch)            # lengte/format/placeholders/catalogus
  -> judge (LLM, submit_judgment)            # feitgetrouwheid, persona, per sectie
  -> revisie / route                         # approved | needs-revision | human-queue
  -> per-training JSON + samenvattings-xlsx
```

Ontwerpprincipe (zelfde DNA als de scorer): **de LLM schrijft/oordeelt, Python
assembleert en beslist.** Vaste sjabloonteksten en catalogus-titels worden door de
code ingevoegd, niet door het model.

## Bestanden

| Bestand | Rol |
| --- | --- |
| `schrijfspec_herschrijven_v1.md` | Systeem-prompt van de schrijver: imperatieve regels per kopje. **Single source of truth.** |
| `beoordelingsspec_herschrijven_v1.md` | Judge-spec, afgeleid uit dezelfde bron zodat judge en schrijver convergeren. |
| `humanisering_nl.md` | NL LLM-taal regels + machine-leesbare `BANNED_PATTERNS`. |
| `rewrite_checks.py` | Deterministische code-check: `Issue`-lijsten, hard-fail vs flag. |
| `rewrite_trainings.py` | Hybride schrijver + orchestratie + I/O. |
| `test_rewrite.py` | Offline tests van de code-check (geen API-key nodig). |
| `vervolgtraining_catalog.example.json` | Schema-voorbeeld; kopieer naar `vervolgtraining_catalog.json` en vul met echte data. |

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
(`parse_content` / `build_source_text` / `extract_days` / `make_client` / `read_input`)
zodat schrijver en scorer **exact dezelfde brontekst** zien. Standaard wordt het
scoring-project als zustermap gevonden onder `.../Eduvision/`. Ligt het ergens anders,
zet dan:

```bash
export SCORE_TRAININGEN_DIR="/pad/naar/TrainingenScorenEdu"
```

## Draaien

De scorer-output bevat geen brontekst, dus de rewriter joint op `training_id` met het
originele trainingen-bestand:

```bash
python rewrite_trainings.py \
  --scored /pad/naar/trainingen_scored_Top15_WebSearch.xlsx \
  --source /pad/naar/TrainingenLijst_50.xlsx \
  --out-dir herschreven --limit 3
```

Output: `herschreven/trainingen/<id>.json` (lossless) + `herschreven/herschreven_samenvatting.xlsx`
met een `approve_edit`-kolom voor de reviewer.

## Tests

```bash
python test_rewrite.py     # 21 offline checks, geen API-key nodig
```

## Status

Walking skeleton. Nog nodig om end-to-end te draaien:

1. **`vervolgtraining_catalog.json`** — echte Eduvision-titels (titel/categorie/populariteit/omschrijving). Zonder dit blijven kopje-8-titels leeg en geflagd.
2. **Goud-corpus** — 5–10 met de hand geschreven ideale herschrijvingen om spec ↔ judge te kalibreren en als regressietest te draaien.
