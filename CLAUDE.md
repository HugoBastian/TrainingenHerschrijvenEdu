# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Voertaal: **Nederlands**, in code, commentaar, commits en antwoorden. De docstrings en het
commentaar leggen niet uit *wat* de code doet maar *waarom*, vaak met het cijfer erbij dat de
keuze onderbouwt ("77 van de 78 openen met …"). Schrijf in die stijl mee; het is geen
sierlaag maar de plek waar de kalibratie is vastgelegd.

## Wat dit project is

De tweede helft van een pijplijn die trainingsteksten van Eduvision herschrijft naar een nieuw
format van tien kopjes. De eerste helft (scoren) leeft in `../Trainingen Scoren/TrainingenScorenEdu`
en wordt geïmporteerd, niet gekopieerd.

Ontwerpprincipe, en de sleutel tot vrijwel elke ontwerpkeuze hier: **de LLM schrijft en
oordeelt, Python assembleert en beslist.** Vaste sjabloonteksten, catalogustitels, kopstructuur
en alle grenzen komen uit code. Een model levert alleen de generatieve tekst, via tools.

Per training: briefing → schrijver (`submit_rewrite`) → deterministische code-check → judge
(`submit_judgment`) → revisie of route (`approved` / `human-queue`) → `<id>.json` + `<id>.md` +
een rij in `herschreven.xlsx`.

## Commando's

```bash
python test_rewrite.py                      # 220 offline tests, geen API-key nodig
python -c "import test_rewrite as t; t.test_em_dash_is_hard_in_elk_schrijversveld()"   # één test
python bouw_goud_v2.py                      # terugval-few-shot; nodig na een verse checkout

# de pijplijn draait normaal vanuit herschrijven.ipynb; de CLI kan hetzelfde:
python rewrite_trainings.py --scored SHEET.xlsx --source BRON.xlsx --besluiten besluiten.xlsx
python rewrite_trainings.py --toon-wachtrij --scored SHEET.xlsx   # wie draait er? geen calls
python rewrite_trainings.py --scan-modus UIT.xlsx --scored SHEET.xlsx --source BRON.xlsx
python rewrite_trainings.py --goud --source BRON.xlsx --out-dir herschreven
```

De testrunner is met de hand geschreven (onderaan `test_rewrite.py`); pytest werkt ook maar
staat niet in `requirements.txt`. Elke test die geen API-key nodig heeft hoort erin te passen:
de LLM-lagen worden bewust niet getest, de deterministische lagen volledig.

`herschreven/` en `*.xlsx` staan in `.gitignore`. Na een verse checkout is er dus geen
voorbeeldmateriaal en geen data.

## Architectuur

| Module | Rol |
| --- | --- |
| `sjabloon.py` | Het template als code. **Enige** plek voor vaste teksten en kopstructuur. Importeert niets uit het project. |
| `rewrite_checks.py` | Deterministische checks → `Issue`-lijsten, HARD of FLAG. Importeert bewust **niets** uit het project, ook `sjabloon` niet. |
| `rewrite_output.py` | Document → CMS-`content` (HTML) en → markdown. Pure renderer; kent alleen `sjabloon`. |
| `rewrite_trainings.py` | ~3000 regels: catalogus, briefing, prompts, writer/judge-calls, moduskeuze, goud, batch-I/O, CLI. |
| `besluiten.py` | De reviewerlaag: `actie_besluit` → doen/niet/mits per actualisering. |
| `bouw_goud_v2.py` | Bouwt de vier handmatig gerepareerde `v2_*`-voorbeelden. Terugvaloptie. |

De importrichting is eenrichtingsverkeer: `sjabloon` ← `rewrite_output` ← `rewrite_trainings`,
met `rewrite_checks` los ernaast. Doorbreek dat niet. Waar een getal in beide werelden nodig is
(zoals `MIN_TITELS_PER_GROEP`) staat er een kopie in `rewrite_checks` met een verwijzing.

### HARD versus FLAG

Dit onderscheid stuurt de hele revisielus, dus kies bewust:

- **HARD** = terug naar de schrijver (`rewrite_one`, max `MAX_REVISIONS` = 2 rondes). Alleen
  voor wat de schrijver zelf kan repareren, en alleen op velden die hij zelf schrijft.
- **FLAG** = naar de judge en de menselijke review, nooit terug naar de schrijver.

Twee vallen: een HARD-check op tekst die de schrijver niet levert (de groep-intro's van
Vervolgstappen komen uit een aparte retrieval-call) laat de lus zinloos rondgaan. En
`_all_text_fields()` levert uitsluitend schrijverstekst op: vaste sjabloonteksten komen daar
nooit langs, en juist daarom mogen de patronen hard vuren. Breid dat niet uit naar het
samengestelde document; dan flagt elke training zijn eigen boilerplate.

### De wachtrij: `start` telt niet over het scoresheet

`bouw_wachtrij()` is de enige plek waar wordt bepaald wélke trainingen een batch draait, en
`rewrite_file` én de preview lezen allebei dat ene frame. Zet die filters nergens anders neer:
een preview die de selectie zelf naboots, liegt zodra er een filter bijkomt.

De volgorde is de rijvolgorde van het scoresheet; er wordt nergens gesorteerd. Maar
`start`/`limit` snijden pas ná twee filters: modus `overnemen` (eigen lus, immuun voor
`start`/`limit`) en alles wat al in `herschreven.xlsx` staat. Sheetrij 3 en wachtrijpositie 3
zijn dus verschillende trainingen zodra er één rij is weggefilterd, en de wachtrij verschuift
bij élke geslaagde run. Dat kostte een keer een verkeerde training; vandaar `alleen_ids=`,
dat niet meeschuift, en `toon_wachtrij()` dat beide nummeringen naast elkaar zet.

### De prompt is vaak het probleem

De system-prefix van de schrijver is één gecachet blok: schrijfspec + humanisering +
stijlregister + correcties + de few-shot. Wijkt de output systematisch af, kijk dan **eerst**
naar wat er in dat blok staat en pas daarna naar de regels. Twee keer is dat de echte oorzaak
gebleken (de few-shot demonstreerde modules met twee sub-bullets; de spec-bestanden bevatten
173 em-dashes terwijl de spec ze verbood).

Daaruit volgen twee regels die tests bewaken:

- in de promptbestanden staat **geen liggend streepje** (em-dash of en-dash), ook niet in foute
  voorbeelden; die staan er uitgeschreven als `[liggend streepje]`. Dit bestand houdt zich er
  ook aan;
- de few-shot moet zelf alle harde checks halen.

De selectie van de few-shot is **vast** en wisselt niet per training: een prefix die per
training verschilt maakt de prompt-cache waardeloos.

### Goud: twee corpora met verschillende taken

- `herschreven/goud/`: 78 trainingen die al in de nieuwe stijl stonden (`herschreven=1`).
  Alleen **meetlat**: `checks_over_goud()` en `lengtes_over_goud()` kalibreren regels en banden
  hierop. Valt een regel bij meer dan de helft om, dan is de regel verdacht en niet het goud.
  Sinds Templatev2 haalt er geen enkele élke harde check, dus als voorbeeld is het onbruikbaar.
- `herschreven/goud_v2/`: de **few-shot**, gevuld door `promoveer_naar_goud()` uit onze eigen
  goedgekeurde output. De selectie staat in `goud_v2/selectie.json`; `GOUD_VOORBEELDEN` leest
  dat manifest bij import en valt zonder manifest terug op de vier `v2_*` uit `bouw_goud_v2.py`.
  Zet hier nooit een lijst id's in code neer.

`promoveer_naar_goud()` rendert de content opnieuw uit het document in plaats van de opgeslagen
`content` te kopiëren, zodat een voorbeeld nooit verouderde boilerplate kan demonstreren.

### De few-shot verversen: wijs precies vier trainingen aan

Dit gebeurt telkens als er genoeg nieuwe output ligt, dus hier staat de werkwijze die zich heeft
bewezen. `promoveer_naar_goud(ids=[...], dry_run=True)` eerst, daarna dezelfde aanroep zonder
`dry_run`; `vervang=True` (de default) maakt de map gelijk aan de selectie.

**Geef altijd exact `GOUD_N` (= 4) id's mee.** Promoveer je er meer, dan schrijft de functie
`nieuwe_ids` in glob-volgorde weg en pakt `GOUD_VOORBEELDEN[:GOUD_N]` de eerste vier; de
bestandsnaam kiest dan de few-shot in plaats van jij.

**De prefix toont alleen Overzicht, Modules en Doelen** (zie `goud_voorbeelden()`). Dat is het
belangrijkste selectiecriterium en het minst zichtbare: een FLAG op Inleiding of Kortste
omschrijving ziet de schrijver nooit, een FLAG op die drie kopjes wordt gedemonstreerd. Rangschik
kandidaten dus op flags *binnen die drie secties*, niet op het totaal. Dat halveerde de
kandidatenlijst van 13 naar 6. Let ook op `catalogus_ontbreekt`: die vuurt overal zodra je zonder
catalogus meet en is dan geen kwaliteitssignaal.

**De checks zijn een ondergrens, geen kwaliteit; lees de tekst.** Twee kandidaten die alles
haalden vielen alsnog af op de vorm van hun modulebullets: 2347 mengde infinitieven met
naamwoordgroepen ("Wat Big Data is …") en had een passieve bullet, 2529 deed hetzelfde in module
1. Geen enkele check ziet dat, en juist die inconsistentie neemt de schrijver over.

Spreid daarna over **dagen** en **vakgebied**. Let op: de modulesband is voor 1, 2 en 3 dagen
identiek (4-6) en verschuift pas bij 4 dagen naar 5-8, dus duurvariatie binnen 1-3 dagen koopt
minder dan je denkt. Sinds augustus 2026 bestaat er nog geen herschreven training van 4+ dagen;
die band heeft dus geen voorbeeld. Komt er een, dan is dat de eerste kandidaat.

**Draai een nieuwe check ook over de zittende few-shot.** Bij deze ronde bleek 3127 de fout te
demonstreren die de check net had moeten vangen, en dat legde drie gaten bloot (`\w` matcht geen
koppelteken; het bezit kan een naamwoord verderop staan). Zelfde les als de 173 em-dashes: wat de
schrijver in zijn context ziet, schrijft hij ook.

### Wat er per training wordt vastgelegd

`<id>.json` bewaart naast het document ook `writer_out` (nodig om één kopje te hergenereren),
`spec_versie` (hash over de vijf promptbestanden plus het template) en `goud_voorbeelden`
(welke few-shot meeging). Zonder die drie is `approved` een status zonder betekenis zodra spec
of few-shot verschuift. Verander je een promptbestand, dan verschuift `spec_versie` vanzelf;
dat is de bedoeling.

## Werkwijze die zich hier heeft bewezen

- **Meet voordat je een regel toevoegt.** Draai een kandidaat-check over `herschreven/goud/`;
  vuurt hij vaker dan ongeveer één op de vijf, dan is het waarschijnlijk een vakterm of een
  corpuspatroon en geen fout. Zet dat cijfer in het commentaar.
- **Een reviewbevinding landt op drie plekken**: de regel in de schrijfspec, het fout/goed-paar
  in `correcties_nl.md`, en waar het kan een check. `beoordelingsspec_herschrijven_v1.md` krijgt
  hem erbij als de judge het moet zien. De vier bestanden mogen niet uit elkaar lopen;
  `test_schrijfspec_citeert_de_actuele_vaste_teksten` bewaakt de citaten uit `sjabloon.py`.
- **`Template trainingen nieuwe opbouw.md` gaat nooit naar het model.** Alleen de hash telt
  mee. De inhoud is met de hand gespiegeld in `sjabloon.py`; houd die twee synchroon.
- Het notebook is de gebruikelijke ingang. Secties 1, 2, 4, 10 en 11 doen geen API-calls.

## Onderhoud van dit bestand

Werk CLAUDE.md bij zodra iets hierboven niet meer klopt: een nieuwe module, een verschoven
importrichting, een andere goud-flow, of een werkwijze die zich opnieuw bewijst. Voeg geen
opsommingen toe die uit de bestanden zelf blijken; dit bestand bestaat om de dingen vast te
leggen waarvoor je anders vijf bestanden moet lezen. Het uitgebreide ontwerpverhaal, inclusief
de meetcijfers per reviewronde, staat in `README.md`.
