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
python test_rewrite.py                      # 307 offline tests, geen API-key nodig
python -c "import test_rewrite as t; t.test_em_dash_is_hard_in_elk_schrijversveld()"   # één test
python bouw_goud_v2.py                      # terugval-few-shot; nodig na een verse checkout

# de pijplijn draait normaal vanuit herschrijven.ipynb; de CLI kan hetzelfde:
python rewrite_trainings.py --scored SHEET.xlsx --source BRON.xlsx --besluiten besluiten.xlsx
python rewrite_trainings.py --toon-wachtrij --scored SHEET.xlsx   # wie draait er? geen calls
python rewrite_trainings.py --scan-modus UIT.xlsx --scored SHEET.xlsx --source BRON.xlsx
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

Spreid daarna over **dagen** en **vakgebied**. Let op: de modulesband is voor 1, 2 en 3 dagen
identiek (4-6) en verschuift pas bij 4 dagen naar 5-8, dus duurvariatie binnen 1-3 dagen koopt
minder dan je denkt. Sinds augustus 2026 bestaat er nog geen herschreven training van 4+ dagen;
die band heeft dus geen voorbeeld. Komt er een, dan is dat de eerste kandidaat.

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
- **`TIJDSBUDGET` (25 min) is het enige echte plafond.** `_call_tool` loopt de stream-events
  daarom zélf langs in plaats van `get_final_message()` aan te roepen: zo breekt een call af
  binnen één event na de deadline in plaats van pas als het model klaar is. Een controle tussen
  de calls door zou niets binden -- één call kan langer duren dan het hele budget.

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
