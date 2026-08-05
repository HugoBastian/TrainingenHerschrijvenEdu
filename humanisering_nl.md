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
