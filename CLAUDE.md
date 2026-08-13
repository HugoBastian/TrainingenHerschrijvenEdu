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
python test_rewrite.py                      # 338 offline tests, geen API-key nodig
python -c "import test_rewrite as t; t.test_em_dash_is_hard_in_elk_schrijversveld()"   # één test
python bouw_goud_v2.py                      # terugval-few-shot; nodig na een verse checkout

# de pijplijn draait normaal vanuit herschrijven.ipynb; de CLI kan hetzelfde:
python rewrite_trainings.py --scored SHEET.xlsx --source BRON.xlsx --besluiten besluiten.xlsx
python rewrite_trainings.py --toon-wachtrij --scored SHEET.xlsx   # wie draait er? geen calls
python rewrite_trainings.py --scan-modus UIT.xlsx --scored SHEET.xlsx --source BRON.xlsx
python rewrite_trainings.py --scan-modus UIT.xlsx --modus-opnieuw 5 47 \
  --scored SHEET.xlsx --source BRON.xlsx   # UIT.xlsx wordt hergebruikt; deze twee niet
python rewrite_trainings.py --goud --source BRON.xlsx --out-dir herschreven

# artefacten in trainingen/batch 3/, en die submap als Google Docs naar een Drive-map
python rewrite_trainings.py --scored SHEET.xlsx --source BRON.xlsx --besluiten besluiten.xlsx \
  --batch "batch 3" --drive-map "batch 3"
python rewrite_trainings.py --alleen-uploaden --drive-map "batch 3"   # herschrijft niets
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
| `drive_upload.py` | De herschreven trainingen als Google Docs naar Drive. Kent geen pandas en geen xlsx. |
| `rewrite_trainings.py` | ~3000 regels: catalogus, briefing, prompts, writer/judge-calls, moduskeuze, goud, batch-I/O, CLI. |
| `besluiten.py` | De reviewerlaag: `actie_besluit` → doen/niet/mits per actualisering. |
| `bouw_goud_v2.py` | Bouwt de vier handmatig gerepareerde `v2_*`-voorbeelden. Terugvaloptie. |

De importrichting is eenrichtingsverkeer:
`sjabloon` ← `rewrite_output` ← `drive_upload` ← `rewrite_trainings`, met `rewrite_checks` los
ernaast. Doorbreek dat niet. Waar iets in beide werelden nodig is (zoals
`MIN_TITELS_PER_GROEP`, en `_schrijf_atomisch` voor het Drive-manifest) staat er een kopie met
een verwijzing naar het origineel; dat is goedkoper dan de richting omdraaien.

### HARD versus FLAG

Dit onderscheid stuurt de hele revisielus, dus kies bewust:

- **HARD** = terug naar de schrijver (`rewrite_one`, max `MAX_REVISIONS` = 3 rondes). Alleen
  voor wat de schrijver zelf kan repareren, en alleen op velden die hij zelf schrijft.
- **FLAG** = naar de judge en de menselijke review, nooit terug naar de schrijver.

`MAX_REVISIONS` telt **schrijverspogingen, geen judge-revisies**, en dat verschil is groter dan
het lijkt: een onvolledige `submit_rewrite` en een HARD-check verbruiken er ook een, zónder dat
de judge eraan te pas komt. Een training die één keer een harde check laat vallen houdt dus
minder judge-rondes over dan het getal suggereert. Stond op 2; over batch 1 bleven 5 van de 46
hangen op "needs-revision na max revisies", waarvan er vier nog maar één of twee concrete,
lokale correcties open hadden staan. Of 3 klopt lees je af aan `rondes` in `<id>.json`:
dezelfde klacht drie keer betekent dat een ronde erbij helpt, elke ronde een andere betekent
dat de lus niet convergeert -- en dan is het antwoord niet meer rondes maar een andere prompt.

De `ctx` van `check_rewrite` draagt sinds `check_actie_escalatie` ook briefinggegevens
(`acties`, de goedgekeurde actualiseringen kaal). Bouw hem via `build_check_ctx()`: `rewrite_one`
en `hergenereer_kopje` maakten die dict allebei zelf, en dat was meteen een plek waar de ene
aanroeper een check kon draaien die de andere niet had.

Twee vallen: een HARD-check op tekst die de schrijver niet levert (de groep-intro's van
Vervolgstappen komen uit een aparte retrieval-call) laat de lus zinloos rondgaan. En
`_all_text_fields()` levert uitsluitend schrijverstekst op: vaste sjabloonteksten komen daar
nooit langs, en juist daarom mogen de patronen hard vuren. Breid dat niet uit naar het
samengestelde document; dan flagt elke training zijn eigen boilerplate.

Daar is een derde bij gekomen, en die is duurder: **een HARD-check moet te repareren zijn.**
`check_doelen` eiste `first[0].isupper()`, en dat is ook False voor een cijfer. Training 482
(Vectorworks 2D/3D) verbruikte er zijn vier rondes aan -- elke ronde HARD op een ander doel
(3, 2, 3, 4) -- want de bron noemt "2D tekenen" en "3D volumes maken" als de onderwerpen zelf.
Een doel dat met "3D-" begint krijgt geen hoofdletter, dus de lus was per constructie niet te
winnen en de training hield niets over. `_kleine_letter_voorop` laat sindsdien twee vormen door
(eerste teken geen letter; een hoofdletter verderop in hetzelfde woord). Zie je in `rondes`
dezelfde code met een wisselende positie, kijk dan eerst of de schrijver de fout überhaupt kán
oplossen.

En een vierde, uit dezelfde batch: **een revisie is hier een hergeneratie, geen reparatie.**
`_call_tool` begint elke poging met een schone `messages`, dus de schrijver ziet zijn vorige
concept niet en leidt elke eerdere correctie opnieuw af. `notes` werd bovendien elke ronde
vervángen. Training 422 loste "professional(s)" in ronde 1 op in de Modules en zette het in
ronde 4 terug in de Inleiding -- de laatste ronde, dus het kostte het hele concept na 1280 s.
De HARD-boodschappen staan nu in `hard_gezien` en gaan elke ronde mee als een apart blok vóór
HERSTEL. Alleen HARD: judge-notities zijn positioneel ("module 4 en 5 overlappen") en slaan
nergens meer op zodra de schrijver opnieuw begint. Dat is meteen de reden om notities die naar
de schrijver gaan niet op positie te formuleren.

### De tier: HARD/FLAG zegt wie het oplost, de tier zegt wie het leest

Elke flag heeft daarnaast een **tier** (`hoog` / `mechanisch` / `laag`), afgeleid van de code via
`TIER_PER_CODE` in `rewrite_checks.py`. Het review-tabblad zet ze in drie kolommen; alleen
`flags_hoog` gaat naar de reviewers. Aanleiding: over de eerste 16 trainingen was 62% van de 34
flags `lengte_richtlijn` (13, waarvan geen enkele in de buurt van de vangrail) of
`zwakke_formulering` (8, waarvan 7 keer het woord "zelfstandig"). Uitgesplitst blijven er 10
opmerkingen over in `flags_hoog` en houdt 7 van de 16 daar een lege cel.

Drie dingen om te weten voor je iets toevoegt of verschuift:

- **een code die niet in de tabel staat is `hoog`.** Zelfde richting als bij HARD/FLAG: een
  nieuwe check komt binnen als werk voor een mens en zakt pas als de meting laat zien dat hij
  vuurt zonder dat er iets mis is. Op het `overnemen`-pad belanden ook HARD-issues in de kolom;
  die vallen daarmee vanzelf goed;
- **verruim geen band om een lage tier te vermijden.** De lengtebanden zijn op het goud
  gekalibreerd, niet op onze eigen output. Onze schrijver zit op p75 82 (Overzicht) en 213
  (Inleiding), dus de band verschuiven omdat wij er structureel boven zitten is in een kringetje
  meten. De flag klopt; hij hoort alleen niet in de kolom die om een oordeel vraagt;
- **`per_tier()` ontdubbelt.** Dezelfde boodschap in twee kopjes wordt één regel
  ("overzicht + inleiding: …"), hoofdletterongevoelig omdat de boodschap het gevonden woord
  citeert. Een check die per kopje vuurt kost een reviewer dus één regel, niet vier.

### De wachtrij: `start` telt niet over het scoresheet

`bouw_wachtrij()` is de enige plek waar wordt bepaald wélke trainingen een batch draait, en
`rewrite_file` én de preview lezen allebei dat ene frame. Zet die filters nergens anders neer:
een preview die de selectie zelf naboots, liegt zodra er een filter bijkomt.

De volgorde is de rijvolgorde van het scoresheet; er wordt nergens gesorteerd. Maar
`start`/`limit` snijden pas ná twee filters: modus `overnemen` (eigen lus, immuun voor
`start`/`limit`) en alles wat al in `herschreven.xlsx` staat -- op de `error`-rijen na, want
daar ligt geen tekst achter en die horen bij een volgende run gewoon weer mee te draaien.
Sheetrij 3 en wachtrijpositie 3 zijn dus verschillende trainingen zodra er één rij is
weggefilterd, en de wachtrij verschuift bij élke geslaagde run. Dat kostte een keer een
verkeerde training; vandaar `alleen_ids=`, dat niet meeschuift, en `toon_wachtrij()` dat
beide nummeringen naast elkaar zet.

Eén uitzondering per training is sinds batch 1 geen batchfout meer: `rewrite_one` en
`neem_over` draaien in een `try`, en wat omvalt wordt een `error`-rij (`_mislukte_training`)
met de traceback op stderr. Dat is niet de vriendelijkheid die het lijkt: `herschreven.xlsx`
wordt pas ná de lus geschreven, dus een uitzondering bij training 1 kostte alle 46 en liet
niets op schijf achter. `build_briefing` staat er bewust buiten -- deterministische assemblage
die bij één training omvalt, valt bij alle 46 om.

### Het scoresheet is een gedeelde Google Sheet

Een heel team reviewt daarin, en batches worden er met de hand in geplakt. Twee gevolgen die je
niet uit de code afleidt:

- **de kolomvolgorde ligt vast** in `score_trainings.KOLOM_VOLGORDE`, met `orden_kolommen()` als
  enige toepassing ervan (in `run_file` en in `modus_voorstellen`). `kern` t/m
  `scorer_confidence` is één blok van 28 kolommen dat in één handeling geplakt wordt; `ok`,
  `error` en het modus-blok uit sectie 3b staan er bewust achter. Verschuif daar niets zonder de
  gedeelde sheet mee te verschuiven; `test_plakblok_staat_in_de_volgorde_van_het_gedeelde_sheet`
  houdt een tweede kopie van de volgorde vast om die botsing zichtbaar te maken;
- **een gedownload blad mag rechtstreeks als scoresheet mee.** Alles matcht op kolomnaam, dus
  volgorde en extra kolommen (`Status`, `Link naar CRM` met formule) doen niets. Wat wél telt:
  de rijvolgorde (`bouw_wachtrij`) en de aanwezigheid van `modus_voorstel` /
  `modules_nb_voorstel`. `test_gedownload_reviewblad_levert_dezelfde_briefing` legt dat vast.

`rewrite_guidance` is sinds augustus 2026 één kolom voor scorer én reviewer: het enige
scorer-veld dat letterlijk in de prompt belandt, dus het enige dat een reviewer bijstelt in plaats
van naleest. Bewust géén tweede kolom zoals bij `kern`/`kern_reviewer`: de versiegeschiedenis van
de sheet is de terugval. De oude aparte kolom `guidance_reviewer` ontstond pas in sectie 3b en
kwam daardoor nooit bij het reviewteam; hij wordt nog gelezen maar niet meer aangemaakt.

### De twee voorbereidende stappen hergebruiken hun eigen uitvoer

Sectie 3 (`write_besluiten_sheet`) en sectie 3b (`modus_voorstellen`) lezen allebei het ruwe
scoresheet en schrijven een tweede bestand. Dat ruwe sheet **groeit per batch aan**, en dat is de
val: het notebook draait deze twee cellen bij elke batch opnieuw, dus tot augustus 2026 betaalde
elke ronde opnieuw voor alle rijen die er al in stonden. Bij 3b was dat één Haiku-call per
training over het hele sheet; bij 3 één per training met vrije tekst, inclusief de regels die de
reviewer al op `handmatig` had gezet en waarvan het verse label meteen weer werd weggegooid.

Beide nemen nu over wat er in hun uitvoerbestand staat, maar de sleutel verschilt bewust:

- **3b op `training_id`** (`_eerdere_modus`). De invoer is de brontekst, en die verandert
  buiten ons om; er is dus geen tekst waarop je een sleutel kunt bouwen die iets bewijst. Vandaar
  `opnieuw=True` / `opnieuw=[id, ...]` als expliciete uitgang, en `--modus-opnieuw` op de CLI;
- **3 op de teksten** (`_zelfde_tekst`): actie én annotatie moeten letterlijk gelijk zijn. Allebei
  staan ze in `_classify_user`, dus allebei kunnen ze het label kantelen. Dat houdt de belofte
  overeind die er altijd al stond -- een gewijzigde actietekst wordt netjes bijgewerkt -- terwijl
  `opnieuw=True` het geval dekt waarin de *prompt* verschoof en de teksten niet.

Het hergebruik van 3b heeft een grens die de besluitenlaag niet heeft: **het uitvoersheet is
1-op-1 met het invoersheet**, want het is verderop de wachtrij. Er mag dus geen training in
staan die niet in deze batch zit, en dat is precies waarom `_rijen_van_andere_trainingen`
(besluiten.xlsx, opgezocht op id) hier geen tegenhanger heeft. Werk je met één gedownload
bestand per batch en met dezelfde uitvoernaam, dan overschrijft batch 2 de modus én de
`modus_reviewer` van batch 1 en levert het hergebruik niets op. `modus_voorstellen` waarschuwt
daarom naar stderr zodra het uit-sheet id's bevat die niet in het scoresheet staan; het antwoord
is een eigen uitvoernaam per batch, of één scoresheet dat aangroeit.

Er zat een tweede, stillere fout in dezelfde plooi: `modus_reviewer` en `modules_nb_reviewer`
bestaan alléén in het uitvoersheet (het notebook laat je ze daar invullen), en 3b maakte ze bij
elke ronde leeg opnieuw aan. Ze komen nu terug uit dat sheet, maar alleen waar de invoer leeg is.
`kern_reviewer` en `rewrite_guidance` doen daar bewust niet aan mee: die horen in de gedeelde
sheet thuis, en terughalen zou een cel die daar net leeggemaakt is weer opvullen.

`modus_voorstellen` maakt zijn client daarom lazy: een ronde waarin niets nieuws staat doet geen
enkele call en hoort dus ook niet op een ontbrekende API-key te stranden. De poort staat nu bij
de eerste training die wél een call nodig heeft, en noemt die training bij naam.

### De prompt is vaak het probleem

De system-prefix van de schrijver is één gecachet blok: schrijfspec + humanisering +
stijlregister + correcties + de few-shot. Wijkt de output systematisch af, kijk dan **eerst**
naar wat er in dat blok staat en pas daarna naar de regels. Drie keer is dat de echte oorzaak
gebleken (de few-shot demonstreerde modules met twee sub-bullets; de spec-bestanden bevatten
173 em-dashes terwijl de spec ze verbood; en training 27 maakte van "benoem concrete
SQL-platformen" een "pas je direct toe op").

Die derde had een variant die je apart moet kennen: **de judge liet het door omdat zijn spec
hem dat opdroeg.** De vrijstelling die voorkomt dat een goedgekeurde actualisering als
"verzonnen feit" sneuvelt, verbood hem letterlijk om zo'n passage af te rekenen als te hoge
belofte. Vind je een fout die de judge had moeten zien, kijk dan of de beoordelingsspec hem
niet juist verbiedt te kijken. Die vrijstelling dekt sinds deze ronde het **onderwerp** en niet
het **niveau**; het werkwoord van de actie is de bovengrens (`ACTIE_WERKWOORD`, schrijfspec
§12, `correcties_nl.md` §30, `check_actie_escalatie`).

Dezelfde vraag heeft nog een tweede antwoord, en dat is scherper: **de judge kan iets afkeuren
dat er helemaal niet staat.** `build_judge_user` gaf `render_markdown` de mechanische titel mee
(`b.nieuwe_titel`), en die renderer gebruikte zijn argument in plaats van `document["titel"]` --
dus wat `bepaal_titel` uiteindelijk koos kwam nooit bij de judge aan. Training 279 leverde de
goedgekeurde rename ("Training HTML en CSS"), had die in zijn document staan, en kreeg drie
rondes lang de opdracht een titel te veranderen die al veranderd wás: een revisielus die per
constructie niet te winnen is en dus gegarandeerd in de menselijke wachtrij eindigt. De titel
komt sindsdien uit het document zelf, met het argument als terugval. Reproduceert een klacht van
de judge zich terwijl de tekst klopt, controleer dan eerst of hij wel leest wat er ligt.

Daaruit volgen twee regels die tests bewaken:

- in de promptbestanden staat **geen liggend streepje** (em-dash of en-dash), ook niet in foute
  voorbeelden; die staan er uitgeschreven als `[liggend streepje]`. Dit bestand houdt zich er
  ook aan;
- de few-shot moet zelf alle harde checks halen.

Die eerste regel gold lang alleen voor de vijf `.md`-bestanden en de systemprefix, en dat was
precies het gat: de laag eronder stond er 34 keer in. 9 in de beschrijvingen van
`SUBMIT_REWRITE` (de tekst die de schrijver leest op het moment dat hij een kopje schrijft),
8 in `build_writer_user`, 8 in `build_judge_user`, de rest in de modus- en actualiseerprompts.
En de HARD-boodschap van `check_em_dash` deed het zelf ook: die gaat via `notes` letterlijk terug
naar de schrijver, dus het teken stond in de zin die het verbood. Alles wat naar een model gaat is
nu schoon, en `test_geen_liggend_streepje_in_de_userberichten_en_de_tools` houdt de user-berichten,
alle tool-schema's en die correctieboodschap erbij. Wat in `rewrite_trainings.py` nog een liggend
streepje bevat is uitsluitend menselijk: sectiekoppen, `reden`-teksten voor de wachtrij en
terminaluitvoer. De regexen die het teken moeten herkennen staan er als `\u2014`/`\u2013`, zodat
een bestand dat voor de helft prompt is het teken zelf nergens toont.

Aan de invoerkant doet het scoringsproject hetzelfde: `zonder_liggend_streepje` in
`score_trainings.py` houdt de gescoorde kern schoon (95 van de eerste 316 kernen bevatten er een),
en de rubric die daar als systemprefix meegaat is ontdaan van zijn eigen 55 streepjes.

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

Spreid daarna over **dagen** en **vakgebied**, en let daarbij op het **richtgetal** en niet op
de band. De band is grover dan de instructie: 4-6 modules voor 1 t/m 3 dagen, 5-8 vanaf 4. Maar
de tool-description van `submit_rewrite` noemt per duur één getal -- 4 bij 1 dag, 5 bij 2-3, 6
bij 4 dagen of meer -- juist omdat een model dat een bereik krijgt de bovenkant kiest. Een
voorbeeld dat binnen de band valt maar naast het richtgetal, doet dus het tegenovergestelde
voor van wat de tool vraagt. De vorige few-shot had 2669 (OpenCV, 1 dag) op 5 modules staan
terwijl er 4 gevraagd worden; binnen de band, tegen de instructie in.

Selecteer daarom op richtgetal, en dek er zoveel mogelijk van de drie. De ronde van augustus
2026 verving 2407/2501/2669/2884 -- vier keer IT, drie keer 5 modules, geen enkele boven de
3 dagen -- door 161 (1 dag, 4 modules), 385 (2 dagen, 5), 125 (3 dagen, 5) en 446 (6 dagen, 6):
alle drie de richtgetallen, en marketing/soft skills/creatief naast IT. 446 is meteen het eerste
4+-daagse voorbeeld dat we ooit hebben gehad, en dus het eerste dat de band 5-8 demonstreert.

Twee dingen die die ronde opleverden en die je bij de volgende weer nodig hebt:

- **duurvariatie binnen 1-3 dagen koopt in de prefix niets.** De enige duurafhankelijke maat die
  de prefix laat zien is het aantal modules, en dat richtgetal is 5 voor zowel 2 als 3 dagen.
  De Inleiding-band schuift wél mee met de duur (170-200 / 180-210 / 190-230), maar de prefix
  toont de Inleiding niet. Een tweede voorbeeld van 2-3 dagen demonstreert dus letterlijk
  hetzelfde als het eerste;
- **kies aan de onderkant van de bulletband.** Over de hele batch kiest de schrijver de
  bovenkant (Overzicht 76/76/76/78 bij een band tot 80, sub-bullets 4-5 bij een band tot 6).
  Een voorbeeld met een compact modulepatroon is daar tegenwicht tegen; 385 staat op 3,3,3,4,3
  en dat is het smalste van het hele corpus.

Om die kandidaten te vinden: meet met `catalog=load_catalog()`, hou alles over dat `approved` is
en geen harde check laat vallen, en gooi daarna weg wat een flag heeft in `overzicht`, `modules`
of `doelen`. Over 96 kandidaten hield dat er 48 over -- ruim genoeg om daarna op richtgetal,
vakgebied en bulletpatroon te kiezen en de laatste tien met de hand te lezen.

**Draai een nieuwe check ook over de zittende few-shot.** Bij deze ronde bleek 3127 de fout te
demonstreren die de check net had moeten vangen, en dat legde drie gaten bloot (`\w` matcht geen
koppelteken; het bezit kan een naamwoord verderop staan). Zelfde les als de 173 em-dashes: wat de
schrijver in zijn context ziet, schrijft hij ook.

### De Drive-upload is een synchronisatie, geen verzendlijst

`upload_naar_drive()` leest de artefacten van één batch en zet daar Google Docs van in een map
met dezelfde naam. De invoer is dus de **map**, niet "wat deze run schreef", en dat is geen
detail: `bouw_wachtrij` slaat over wat al in `herschreven.xlsx` staat, dus een training die in
run 1 wel werd geschreven maar niet geüpload, komt in run 2 niet meer langs en zou nooit op
Drive belanden. Om dezelfde reden draait de upload ook door als de wachtrij leeg is.

### Het sublabel zit in de docnaam, en verandert buiten de code om

Een deel van de catalogus wordt onder een sublabel aangeboden; die docs heten
`{id} - SA | {titel} (automatisch herschreven)`, de rest houdt de naam zonder voorvoegsel. De 49
id's staan in `sa_products.json` (een uitdraai uit het CMS) en niet in code, om dezelfde reden
als de few-shot in `goud_v2/selectie.json`. Uit dat bestand komen **alleen de id's**: de titel
die erin staat is de oude uit het CMS en de herschrijver bepaalt de nieuwe. `SUBLABEL_IDS` wordt
bij import gevuld, dus na een wijziging is een verse import (kernelherstart) nodig; ontbreekt het
bestand, dan meldt `sublabel_ids()` dat naar stderr in plaats van stil alle labels te laten
vallen -- dat zie je anders pas in de Drive-lijst, en dan staan de docs er al.

Twee dingen die daaruit volgen:

- **een doc dat er al staat maar nog zonder label heet, wordt hernoemd**, ook op het
  `overslaan`-pad. Dat pad beschermt de *inhoud* (een reviewer verliest zijn ankers zodra de
  tekst eronder wordt vervangen); `files.update` met alleen een naam raakt de tekst niet. Bij het
  invoeren van het label stonden er 12 SA-trainingen in Batch 1 t/m 4 onder hun oude naam;
- **`zonder_sublabel` vergelijkt de twee namen kaal**, zodat alleen het label een hernoeming
  oplevert en een gewijzigde titel niet. Een nieuwe titel hoort bij nieuwe inhoud, en juist die
  wordt hier overgeslagen: hernoemen zou dan een naam op een doc zetten die de tekst erin niet
  heeft. Daarom bewaart het manifest de naam die **op Drive** staat en niet de naam die de code
  zou kiezen -- anders wist een volgende run niet meer dat er iets te hernoemen viel.

### Batches zijn submappen, en dat is wat de Drive-mappen scheidt

`herschreven/trainingen/<batch>/<id>.json`. Zonder die scheiding is er op schijf niets wat
batch 1 van batch 2 onderscheidt, en dan belandt élke training in élke Drive-map opnieuw --
gemeten: bij drie trainingen in twee batches kreeg de tweede map er drie in plaats van één.

Drie functies dragen die indeling, en ze betekenen bewust niet hetzelfde met "geen batch":

- **`artefact_dir(out_dir, batch)`** -- waar je schrijft. Zonder batch de platte map, want de
  trainingen van voor de indeling staan daar en hoeven niet te verhuizen;
- **`artefact_paden(out_dir, batch)`** -- waar je zoekt. Zonder batch juist *alles*, submappen
  incluis: `promoveer_naar_goud` kiest de few-shot uit alles wat we ooit hebben geschreven, en
  globt daarom recursief;
- **`drive_upload.verzamel_uit_map`** -- wat er geüpload wordt. Nooit recursief; zonder batch
  alleen de platte map. Recursief zou precies het probleem terugbrengen.

`zoek_artefact(out_dir, id)` vindt een training waar hij ook staat. Gebruik dat overal waar je
een `<id>.json` opent: een aanroeper weet niet in welke batch een training zit en hoeft dat ook
niet te weten. `hergenereer_kopje_op_schijf` schrijft daardoor terug naar de map waar de
training vandaan komt, en de notebookcellen in sectie 7 en 8 werken ongeacht de batch.

`kies_batch()` vult een ontbrekende batchnaam aan met de submap die net zo heet als de
Drive-map. Zonder die regel is de dure fout stil: `upload_naar_drive(OUT_DIR, "ronde 3")` zonder
`batch` zou de platte map pakken en de oude trainingen in de map van ronde 3 zetten.

Vier dingen die je niet uit de code afleidt:

- **het `overnemen`-spoor schrijft sindsdien ook zijn `<id>.json`.** Dat deed het niet, en die
  trainingen bestonden daardoor nergens op schijf. `neem_over` levert geen `document`, dus er
  komt geen `.md`; de doc-renderer draait daarom op `content` en niet op het document;
- **`bij_bestaand="overslaan"` is de default en dat is een reviewbesluit, geen voorzichtigheid.**
  `files.update` behoudt de opmerkingen van een reviewer wel, maar niet hun ankers. Een verouderd
  doc is minder erg dan commentaar dat nergens meer op slaat. Wijkt de sha256 af, dan meldt de
  lus dat in plaats van stil over te slaan;
- **scope `drive.file`, en de app maakt haar eigen rootmap.** `drive` is bij Google een
  *restricted* scope en kost bij een External app een jaarlijks betaald assessment. De prijs van
  `drive.file` is dat een bestaande map van de gebruiker als `parents` een 404 geeft -- vandaar
  `zorg_voor_rootmap`. De id gaat daarna in `.env` als `DRIVE_ROOT_ID`;
- **de google-imports staan in de functies**, zodat `test_rewrite.py` de module kan importeren
  zonder die packages. Alleen `upload_doc` heeft `googleapiclient` echt nodig; de tests raken die
  wel, want de fake-service geeft het `MediaIoBaseUpload`-object terug dat ze inspecteren.

De opmaak van het doc (`DOCS_KOPPEN`, `ALINEA_RUIMTE` in `rewrite_output.py`) zit als inline
stijl in de HTML en **nooit in de CMS-content**; die krijgt zijn opmaak van de site en zou er
een tweede, botsende laag bij krijgen. `test_docopmaak_lekt_niet_naar_de_cms_content` bewaakt
dat. Drie dingen die alleen uit een echte upload bleken:

- **ruimte tussen alinea's moet als `margin-bottom` op elke `<p>`.** Docs zet "ruimte na alinea"
  standaard op nul, dus zonder die regel plakken alle alinea's van een kopje aan elkaar. Dat was
  de enige klacht na de eerste echte batch;
- **tekengrootte en vet horen óók op een `<span>` binnen de kop.** Docs bewaart die twee als
  tekenopmaak op de tekst en niet als eigenschap van de alinea, en zo schrijft de HTML-export
  van Docs het zelf ook. Alleen op de `<h1>` kan de importer het laten vallen;
- **marges als losse eigenschappen, en alleen `margin`.** Geen `margin`-shorthand (dat is niet
  de vorm die Docs zelf schrijft) en geen `padding` ernaast: honoreert de importer ze allebei,
  dan staat er twee keer zoveel ruimte als bedoeld.

Verander je de opmaak, dan verschuift de sha256 van élk doc. De lus meldt dan per training dat
de tekst is gewijzigd; `bij_bestaand="nieuwe_versie"` werkt de bestaande docs bij.

### De opmerking bij het doc, en waar Google hem neerzet

Elk doc krijgt via `zet_comment` één opmerking met de flags én de reden voor de human-queue,
zodat een reviewer weet waar hij op moet letten voor hij begint te lezen. `comment_tekst` maakt
de tekst.

**Zonder anker, en dat kost zichtbaarheid.** Opmerkingen komen bij Google uitsluitend uit de
Drive-API; een anker kan alleen als ongedocumenteerde kix-JSON met tekstposities, en die kennen
wij niet omdat de conversie van HTML naar Doc aan de andere kant gebeurt. Gemeten gevolg bij
batch 1: alle 43 opmerkingen bestonden (`comments.list` gaf ze terug, ongeresolved, niet
verwijderd), maar Docs kan ze nergens in de tekst plaatsen en toont ze in de **geschiedenis**
onder "oorspronkelijke content verwijderd" in plaats van in de kantlijn. Wie de kantlijn wil,
moet de Docs-API aanzetten (staat uit in het Cloud-project) en het ankerformaat op de koop toe
nemen. De tussenstap -- de flags als grijs blok bovenin het document -- is geprobeerd en weer
teruggedraaid; die was zichtbaar maar zette reviewtekst in het artefact zelf.

Vier dingen om te weten:

- **de opmerking gaat er ná de inhoud op, nooit ervoor.** Een opmerking die er al stond voordat
  `files.update` de tekst verving is helemaal losgeslagen;
- **`vervang=True` op het `nieuwe_versie`-pad ruimt onze eigen vorige opmerking op**, want die
  beschrijft de flags van de vórige versie. Alleen die van ons: `ONZE_OPENERS` herkent onze
  openingsregel, en een opmerking van een reviewer blijft staan. Dat is de enige plek in de code
  die iets van een gebruiker verwijdert, dus hou het filter smal;
- **alleen de tier `hoog`**, net als de kolom `flags_hoog`. Alles tonen doet hier hetzelfde als
  de oude verzamelkolom deed. Daarom staat `flags_tier` óók in `<id>.json`: zonder die
  uitsplitsing op schijf kan de opmerking de ruis niet van het oordeel scheiden. Artefacten van
  vóór die wissel vallen terug op alle flags, dezelfde afweging als in `_review_rij`;
- **een mislukte opmerking is geen mislukte upload.** Het doc staat er en is bruikbaar, dus de
  training gaat niet op de `mislukt`-lijst.

`bij_bestaand="overslaan"` blijft de default om een andere reden, die nog steeds geldt: een
reviewer zet zélf opmerkingen in het doc, en `files.update` behoudt hun opmerkingen wel maar
niet hun ankers.

### De reden voor de human-queue is het interessantste veld

De flags zeggen wat de code zag; de reden zegt wat de judge zag, en dat is waar een reviewer
mee begint. `human_reden` vult de judge alleen als hij zélf naar de mens routeert. Blijft hij
tot het eind bij `needs-revision`, dan is dat veld leeg en stond er "judge: needs-revision na
max revisies" -- 37 tekens over precies de trainingen waar tweemaal herschrijven niet hielp
(5 van de 8 human-queue-rijen in batch 1). `_reden_uit_revisies` valt daarom terug op
`judgment.revisie_notities`, die concreet en per kopje zijn. Het oordeel stond al die tijd in
`<id>.json`; alleen `reden` pikte het niet op.

Batch 2 liet zien dat er nóg een uitgang was die dat niet deed: valt de **laatste** ronde op een
code-check, dan loopt de lus eruit en niet door een `return`. Die terugval gaf alleen `document`
en `judgment` mee plus de vaste zin "geen valide concept na max pogingen" -- geen `writer_out`
(en juist dat veld heeft `hergenereer_kopje` nodig), geen flags (dus een lege opmerking bij het
Drive-doc) en geen `_reden_uit_revisies` terwijl het oordeel er lag. `_pogingen_op` doet dat nu
in drie trappen: het laatste beoordeelde concept met het oordeel van de judge; anders de laatste
volledige schrijverspoging alsnog samengesteld, met de harde fouten als flag in de tier `hoog`;
en pas als élke poging een onvolledige `submit_rewrite` gaf is er echt niets, en zegt de reden
dat ook. Zet nieuwe velden op alle drie of op geen: dit was één plek die achterliep bij de
`return` twintig regels hoger.

De upload hangt aan het eind van `rewrite_file` in een `try/except`: de artefacten en het sheet
staan dan al op schijf, dus een kapot token kost hoogstens de upload en nooit een batch die net
een uur aan API-calls heeft gekost. Authenticeren gebeurt wél vooraf, vóór `make_client()`, om
precies die reden omgekeerd.

### Wat er per training wordt vastgelegd

`<id>.json` bewaart naast het document ook `writer_out` (nodig om één kopje te hergenereren),
`spec_versie` (hash over de vijf promptbestanden plus het template) en `goud_voorbeelden`
(welke few-shot meeging). Zonder die drie is `approved` een status zonder betekenis zodra spec
of few-shot verschuift. Verander je een promptbestand, dan verschuift `spec_versie` vanzelf;
dat is de bedoeling.

Daarnaast `rondes` en `seconden`, en die twee bestaan om aan de knoppen hierboven te kunnen
draaien. `judgment` is alleen het **laatste** oordeel; `rondes` is het verloop ernaartoe (per
poging `onvolledig` / `code-check` / het judge-verdict, met de notities die teruggingen naar de
schrijver). Zonder dat spoor was over batch 1 niet meer na te gaan waarom vijf trainingen op de
revisielimiet strandden. `seconden` staat ook in het review-tabblad, naast `n_rondes`, en is het
getal waarop `TIJDSBUDGET` hoort te worden bijgesteld -- dat staat nu op een schatting.

En sinds augustus 2026 `gestart_op`, `fout_soort` en `storingen`, die samen een andere vraag
beantwoorden: niet wat een training kostte, maar in wat voor moment hij draaide.

- **`storingen` komt uit `Storingsspoor`** (`begin_spoor()` / `huidig_spoor()`, gevuld door
  `_stream_bericht`) en telt calls, callseconden, de traagste call en de **stiltes**: elke
  gevangen netwerkfout, ook die waarna de herkansing wél lukte. Dat laatste is het hele punt.
  Een training die drie stiltes opving en toch slaagde was niet te onderscheiden van een die
  schoon doorliep, en daarmee zei het corpus niets over het moment: over vier batches staan er
  3 error-rijen tegen 198 geslaagde trainingen. De fouten zijn te zeldzaam om op te toetsen, de
  bijna-fouten niet. `traagste_call` is meteen de enige plek waar de retries van `MAX_RETRIES`
  zichtbaar worden -- die backoffen buiten ons zicht;
- **`fout_soort`** (`netwerk` / `limiet` / `overbelast` / `tijdsbudget` / `overig`) is de sleutel
  waarop je groepeert; `reden` is een zin voor een mens en per uitzondering anders geformuleerd.
  `_foutsoort` kijkt naar `status_code` en niet naar het klassetype van de SDK: 529
  (`overloaded_error`) heeft bij Anthropic geen eigen klasse.

**`verloop.jsonl` naast `herschreven.xlsx` is append-only, en dat is de kern.** Eén regel per
training per run, per training weggeschreven en niet aan het eind van de lus. `<id>.json` en de
reviewrij worden allebei overschreven zodra een gestrande training de volgende run alsnog
slaagt -- `bouw_wachtrij` draagt error-rijen bewust opnieuw aan en `drop_duplicates(keep="last")`
doet de rest. Gevolg: van vier batches waren er nog 3 error-rijen over, stond er nergens een
tijdstip, en was de runvolgorde alleen nog uit de mtimes van de artefacten te reconstrueren --
die verschuiven juist bij de trainingen waar het om gaat. De kolom `positie` staat er daarom bij:
in het sheet staat een opnieuw gedraaide training achteraan in plaats van waar hij liep.

`lees_verloop(out_dir)` leest het terug met de kolom `na_storing` erbij (viel de vorige training
in **dezelfde** run op een storing?). Die definitie hoort één keer in code te staan en niet in
een wegwerpscript naast elke analyse; `_storing_uit` is dezelfde functie die de afkoeling
gebruikt, zodat de meting niet iets anders telt dan de batch deed. Daarmee is de vraag een
`groupby("na_storing")["storing"].mean()`.

### De tijdgrenzen: één stiltelimiet en één plafond

De SDK-defaults (600 s, 2 retries) lezen als een grens per call maar zijn dat niet. Bij
`messages.stream` telt de timeout **per stukje dat over de lijn komt**, en een `ReadTimeout`
midden in een stream gaat buiten de retry-laag van de SDK om (die dekt alleen het openen van de
request). Eén training doet tot 24 modelcalls, dus zonder eigen grens is er geen bovengrens:
training 47 draaide 81 minuten voordat hij alsnog op een ReadTimeout sneuvelde.

- **`LEES_TIMEOUT` (180 s) is een stiltelimiet, geen duur.** Drie minuten zonder één byte is een
  dode verbinding; de thinking-blokken streamen mee, ook als hun tekst leeg is. Zit op de client
  via `make_client()`, dat de kale client van het scoreproject in `with_options` wikkelt -- de
  importrichting blijft eenrichtingsverkeer, en een timeout die bij ons hoort heeft daar niets
  te zoeken. De naam blijft `make_client`, zodat notebook en CLI vanzelf de ingestelde client
  krijgen;
- **`TIJDSBUDGET` (25 min) is het enige echte plafond.** `_stream_bericht` loopt de
  stream-events daarom zélf langs in plaats van `get_final_message()` aan te roepen: zo breekt
  een call af binnen één event na de deadline in plaats van pas als het model klaar is. Een
  controle tussen de calls door zou niets binden -- één call kan langer duren dan het hele
  budget. Gemeten maximum over batch 2: 1280,5 s bij een training die vier rondes deed, dus
  85% van het budget. p50 is 301 s, p90 547 s;
- **`NETWERK_HERKANSINGEN` (1) is de herkansing die `MAX_RETRIES` niet geeft.** Die zit op de
  SDK en dekt alleen het openen van de request; een `ReadTimeout` midden in een stream komt kaal
  naar boven. Training 369 sneuvelde er na 381 s op -- normale duur, nog 1119 s budget over,
  maar `_call_tool` had er niets tegenover te zetten en de hele training was weg. De herkansing
  weegt licht omdat er aan onze kant niets is gebeurd: geen document, geen bestand, alleen
  tokens. `_bewaak_tijd` staat vóór elke poging, dus dicht bij de deadline herkanst hij niet.

Daar is er een vierde bij gekomen, en die staat niet in een call maar ertussen: **`AFKOELING_START`
(60 s, verdubbelend tot `AFKOELING_MAX` = 8 min) is er tegen fouten die niet bij één training
horen.** `rewrite_file` begon de volgende training milliseconden na de vorige, dus een storing
die minuten duurt nam ze allemaal mee. Batch 4 liet zien dat die storingen zo lang leven: 2410
(909,5 s) en 2412 (429,4 s) sneuvelden allebei op een ReadTimeout, en bij allebei viel óók de
directe herkansing van `_stream_bericht` om -- die fout leefde dus aantoonbaar langer dan één
volledige call. Drie dingen om te weten:

- **`Afkoeling.wacht()` staat vóór de training en niet erna**, anders wacht de batch achter zijn
  laatste training aan. De teller loopt op `opeenvolgend`, zodat `wacht()` één keer slaapt per
  training en de verdubbeling toch klopt. Eén training die wél liep zet hem terug op nul;
- **`_is_storing` bepaalt wie afkoelt, en een `TijdOverschreden` telt alleen mee mét stiltes in
  het spoor.** Dan is het budget niet opgegaan aan werk maar aan wachten -- training 2483
  verbrandde 1571 s terwijl zijn buren 164 s en 202 s deden. Zonder stiltes is het een trage
  training en helpt wachten niets;
- **de afkoeling vertraagt de batch, ze stopt hem niet.** Een storingsvenster kost nog steeds
  trainingen; wat 60 s koopt is dat de volgende poging buiten het venster valt. Blijkt uit
  `verloop.jsonl` dat er ook ná de afkoeling nog reeksen staan, dan is het antwoord het
  requeuen van een netwerkfout achteraan in de wachtrij en niet een langere pauze.

De deadline staat als modulevariabele achter `tijdsbudget()` en niet als parameter. `_call_tool`
wordt langs vijf paden bereikt (schrijver, judge, vervolgstappen, modus, actualisering) en
vanuit twee lussen; een parameter zou bij elk van hen vergeten kunnen worden. Zelfde afweging
als bij `build_check_ctx`. Alleen de batchpaden zetten een budget (`rewrite_one`, `neem_over`);
bij een losse hergeneratie zit er een mens aan de knoppen en bewaakt `_bewaak_tijd` niets.

`TijdOverschreden` erft van `RuntimeError` en dat is functioneel: de lussen in `rewrite_file`
vangen hem als elke andere fout, maken er een `error`-rij van en gaan door naar de volgende
training. Eén vastgelopen training kost daarmee die training en nooit de batch -- en omdat
`bouw_wachtrij` error-rijen niet overslaat, draait hij de volgende run gewoon weer mee.

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
