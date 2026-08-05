# NL-humanisering — LLM-taal weren en herschrijven

LLM-tekst is herkenbaar: vaste openingsformules, holle intensiveerders, drieslagen,
"niet alleen … maar ook", em-dash-verslaving en gladde-maar-lege verbindingswoorden.
Deze regels dienen twee doelen:

1. **Vooraf** — ingebakken in `schrijfspec_herschrijven_v1.md`, zodat de schrijver ze niet produceert (goedkoper dan achteraf poetsen).
2. **Achteraf** — de `BANNED_PATTERNS`-lijst hieronder wordt door `rewrite_checks.py` deterministisch geflagd, en een lichte opschoon-pass ruimt residu op.

De harde regels van de stijlgids gelden onverkort: geen marketingtaal, geen superlatieven,
"je"-vorm. Zinslengte hoort daar níét bij: ±20 woorden is een gemiddelde om op te mikken,
geen plafond (schrijfspec §0.4). Afwisseling telt hier zwaarder dan het getal — een rij
zinnen van gelijke lengte is zélf een LLM-signaal, net als de patronen hieronder.

**Alles hier geldt voor tekst die de schrijver zélf produceert.** De vaste sjabloonteksten in
`sjabloon.py` vallen erbuiten: die zijn letterlijk aangeleverd door de schrijfstijl-eigenaar en
worden niet gecontroleerd en niet beoordeeld. Een paar ervan overtreden §B en §C — `AANPAK_ALINEA_2`
bevat "niet alleen … maar ook", "essentiële" en "waardevolle", en `VERVOLG_ALINEA_1` eindigt op
een uitroepteken. Dat is een bewuste keuze voor onze eigen boilerplate en **verruimt niets** aan
wat de schrijver mag schrijven. Kopieer die constructies dus niet naar gegenereerde tekst.

---

## A. Verboden openings- en vulzinnen (flag → herschrijven)

- "In de (snel veranderende / hedendaagse / moderne) wereld van …"
- "In het huidige (digitale) landschap …"
- "Of het nu gaat om X of Y …"
- "In deze training duiken we in …" / "duiken we dieper in …"
- "Ontdek de kracht van …" / "Ontgrendel het potentieel van …"
- "Neem je kennis naar een hoger niveau."
- "Of je nu een beginner bent of een ervaren …"

## B. Holle intensiveerders / modewoorden (flag)

cruciaal, essentieel, naadloos, moeiteloos, uiterst, ongekend, baanbrekend, revolutionair,
game-changer, next-level, in een handomdraai, in no-time, robuust (als buzzword),
krachtig (als buzzword), waardevol (zonder inhoud), een schat aan, een breed scala aan,
talloze, diverse (zonder specificatie).

## C. Structurele LLM-tics (flag)

- **"Niet alleen … maar ook …"** — herschrijf tot een directe zin.
- **Drieslag-opsommingen als opvulling** (adjectief, adjectief, adjectief) zonder inhoud.
- **Em-dash-verslaving** — nooit gebruiken; vervang door punt of komma.
- **"Dit betekent dat …" / "Dat wil zeggen …"** als vulzin.
- **Retorische dubbelvraag** aan het begin van meerdere kopjes (buiten de bewuste "Wil je …"-openers).
- **Slot-aanmoediging** ("Zet vandaag nog de eerste stap!", "Waar wacht je nog op?").
- **Overmatig bijwoord-gebruik** ("echt", "gewoon", "simpelweg", "daadwerkelijk").

## D. Woorden die we niet gebruiken (hard, behalve waar vermeld)

Aangeleverd door de schrijfstijl-eigenaar. Anders dan §B is dit geen smaakkwestie: deze
woorden gaan er altijd uit.

- **"professional(s)"** — schrijf waar iemand naartoe wil, niet wat iemand ís. [hard]
  Uitzondering: staat het woord in de trainingstitel zelf ("Training PHP Professional"), dan
  mag de tekst die titel gewoon noemen; dat wordt een flag, geen fout.
- **"je houdt je bezig met"** — vult een zin zonder iets te zeggen; noem de handeling. [hard]
- **"meeting"** — gebruik "overleg", "sessie" of "bijeenkomst". [flag] Het blijft een flag
  omdat het in Scrum-, Agile- en Teams-trainingen een vakterm kan zijn.
- **"Deze training is voor …"** — de doelgroep opent met "Deze training is **bedoeld** voor …".
  [hard] Zie schrijfspec §6; hier staat hij omdat het een woordkeuze is, geen structuurregel.
- **"de Training X" midden in een zin** — het soortwoord is daar een gewoon zelfstandig
  naamwoord: "de training PHP Professional", "de masterclass PHP". [flag] Als kop 1 blijft het
  uiteraard "Training PHP Professional".
- **"vakgebied"** — wij schrijven "expertisegebied". [flag]
- **"plaatsen"** als werkwoord voor wat de deelnemer leert — "begrippen kunnen plaatsen", "het
  model plaatsen binnen …". [flag] Het zegt niets: plaatsen wáár, en wat kun je er dan mee?
  Schrijf wat de deelnemer werkelijk doet: "de opbouw van het model doorgronden", "het verschil
  tussen X en Y benoemen", "weten wanneer je X inzet". Zie schrijfspec §0.19.
- **"in elkaar zit"** — "ervaar je hoe een analysetraject in elkaar zit". [flag] Ook een
  onderkant-formulering. "hoe een analysetraject is opgebouwd" zegt hetzelfde en is wél een
  respectabele constructie, die je bovendien kunt versterken met een bijwoord.
- **"(gerichter) meepraten"** als de belofte van een training. [flag] Ook als het waar is, is
  het geen belofte waarvoor iemand betaalt. Zeg binnen dezelfde scope wat de deelnemer écht
  overhoudt: "een stevige basis leggen in …", "de structuur van X volledig begrijpen".

### Woorden die we juist wél gebruiken

Geen verbod maar een voorkeur, en vaak genoeg van toepassing om hem hier vast te leggen:
**werksituatie** en **werkpraktijk**. Waar je een abstractie zou schrijven over de omgeving van
de deelnemer ("realistische situaties", "de dagelijkse context"), is een van deze twee bijna
altijd concreter en beter.

## E. Toon-correcties (herschrijven, geen harde flag)

- Vervang abstracties door concrete werksituaties (stijlgids: vermijd "realistische werksituaties").
- Actief boven passief; werkwoord vooraan waar mogelijk. Maak "we" of "je" het onderwerp,
  nooit een passieve constructie zonder onderwerp ("er wordt aandacht besteed aan …").
- Één idee per zin; knip lange samengestelde zinnen.
- Nederlands, geen onnodige Engelse buzzwords (tenzij vakterm).

---

## F. `BANNED_PATTERNS` (machine-leesbaar, voor `rewrite_checks.py`)

Regex-fragmenten, hoofdletter-ongevoelig, als **flag** (niet hard-fail). De lijst is
bewust conservatief: liever een terechte flag dan een valse hard-fail. De harde verboden uit
§D staan hier bewust niet in; die hebben hun eigen check (`check_verboden_woorden`) omdat ze
een uitzondering op de trainingstitel nodig hebben.

```
in de (snel veranderende|hedendaagse|moderne|dynamische) wereld van
in het (huidige|digitale) landschap
of het nu gaat om
duiken we (dieper )?in
ontdek de kracht van
ontgrendel het potentieel
naar een hoger niveau
niet alleen .{0,60} maar ook
een (breed|ruim) scala aan
een schat aan
in een handomdraai
in no[- ]?time
waar wacht je nog op
zet (vandaag )?(nog )?de eerste stap
\b(naadloos|moeiteloos|cruciaal|essentieel|baanbrekend|revolutionair|ongekend)\b
\b(simpelweg|daadwerkelijk|gewoonweg)\b
```

> Onderhoud: breid uit op basis van wat je in de eerste batch-output terugziet. Houd
> vakspecifieke uitzonderingen (bijv. "cruciaal pad" in projectmanagement) in gedachten —
> daarom flag, geen hard-fail.

---

## G. Anglicismen en onnodige leenwoorden (flag)

Schrijf in natuurlijk, idiomatisch Nederlands. Twee dingen gaan mis, en ze vragen een ander
oordeel:

**G1. Structurele anglicismen** — een Engelse constructie letterlijk vertaald. Die klinkt
Nederlands maar is het niet, en dat is precies waarom een LLM ze produceert. Voorbeelden uit
reviewronde 2:

❌ "Daarna **werk je door** de categorieën waarin patterns worden ingedeeld"
✅ "Daarna **neem je** de categorieën **door** waarin je patronen indeelt"

❌ "Je leert het onderscheid tussen een conceptueel en een logisch model **kennen**"
✅ "Je leert **onderscheid te maken** tussen een conceptueel en een logisch model"

**G2. Leenwoorden waar een gewoon Nederlands woord staat.** Hier telt of het een echte vakterm
is. `Skills`, `stakeholders` en `mindset` hebben een Nederlandse tegenhanger die niets verliest;
`governance`, `compliance`, `performance` en `deployment` zijn in onze catalogus gewoon de term.
Bij twijfel: staat het woord in de brontekst of de trainingstitel, dan is het een vakterm.

**De middenweg bij een ingeburgerde Engelse term.** Gebruik het Engels als **hele term** en het
Nederlands als **los woord**. Uit de review op JavaScript Design Patterns:

> "Ik zou in een lopende zin, zoals 'hoe zo'n pattern is opgebouwd', wel patroon gebruiken. Dus
> wel design pattern als volledige term, maar patroon als los woord."

✅ "Je leert de belangrijkste **design patterns** te benoemen en te doorgronden hoe zo'n
**patroon** is opgebouwd."

### `ANGLICISMEN` (machine-leesbaar, voor `rewrite_checks.py`)

Regex → Nederlandse tegenhanger. Hoofdletter-ongevoelig, **flag**. Bewust conservatief: alleen
gevallen met een schoon alternatief. Wat een echte vakterm kán zijn (`best practices`,
`governance`, `performance`, `deployment`) staat er niet in.

```
werk(?:t|en)?\s+(?:je|we)\s+door\s+de  = neem je de … door
onderscheid\b[^.]{0,30}\bkennen        = onderscheid maken tussen
\bin lijn met\b                        = volgens / passend bij
op (?:een )?(?:dagelijkse|wekelijkse|regelmatige) basis = dagelijks / wekelijks / regelmatig
\badresseer(?:t|en|d|de)?\b            = aanpakken / behandelen
\bimpacteer(?:t|en|d|de)?\b            = raken / beïnvloeden
\bcontrole (?:te )?nemen over\b        = de regie nemen over
\bzo snel als mogelijk\b               = zo snel mogelijk
\b(?:support|deliver|challeng|shar|align|committ)(?:en|t|de)\b = ondersteunen / opleveren / bevragen / delen / afstemmen / vastleggen
\bskills\b                             = vaardigheden
\bstakeholders?\b                      = belanghebbenden / betrokkenen
\bmindset\b                            = houding / denkwijze
\binsights?\b                          = inzichten
\bchallenges\b                         = uitdagingen / knelpunten
\btooling\b                            = gereedschap / hulpmiddelen
\bhands[- ]on\b                        = praktisch
\bissues\b                             = knelpunten
\bawareness\b                          = bewustzijn
\bownership\b                          = eigenaarschap
\blearnings\b                          = lessen / inzichten
\balignment\b                          = afstemming
\bdeep[- ]dive\b                       = verdieping
\bend[- ]to[- ]end\b                   = van begin tot eind
```

> Onderhoud: net als §F groeit deze lijst per review-ronde. Vuurt een patroon op de eigen
> catalogus vaker dan ongeveer één op de vijf trainingen, kijk dan eerst of het geen vakterm is
> — meet met een scriptje over `herschreven/goud/` voordat je hem toevoegt.
