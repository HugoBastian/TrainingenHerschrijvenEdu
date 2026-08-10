# Correcties: echte fout/goed-paren uit de review-rondes

`humanisering_nl.md` zegt wat je niet schrijft, `stijlregister_nl.md` geeft het register waaruit
je put, `schrijfspec_herschrijven_v1.md` geeft de regels. Dit bestand laat zien **hoe die regels
uitpakken op echte tekst**: elk paar hieronder komt uit een gegenereerde training die door de
schrijfstijl-eigenaar is nagelezen, met de motivering erbij.

Lees het als kalibratie, niet als een lijst met zoek-en-vervang-opdrachten. De ❌-zin is
meestal niet fóut; hij is net niet raak, en dat verschil is precies wat hier te leren valt.

> Onderhoud: dit bestand groeit per review-ronde. Voeg een paar toe zodra een correctie
> **twee keer** terugkomt; een eenmalige opmerking hoort in de training zelf thuis, niet hier.
> Ronde 1 (4 trainingen, 45 comments) staat hieronder, ronde 2 (3 trainingen, 45 comments)
> daaronder vanaf §14.

---

## 1. Het lerende aspect: "kunnen" (schrijfspec §0.15)

Dit was de meest voorkomende correctie: in **alle vier** de trainingen ontbrak "kunnen" in het
Overzicht. Wij trainen; de deelnemer levert het resultaat. Zonder dat woord belooft de zin dat
wij het werk doen.

❌ Wil je datamodellen opzetten die ontwerpkeuzes onderbouwen?
✅ Wil je datamodellen **kunnen** opzetten die ontwerpkeuzes onderbouwen?

❌ Wil je grip krijgen op grote datasets en analyses opzetten die tot bruikbare conclusies leiden?
✅ Wil je grip krijgen op grote datasets en analyses **kunnen** opzetten die tot bruikbare conclusies leiden?

❌ Wil je professioneel leren programmeren in PHP en zelf een webapplicatie bouwen?
✅ Wil je professioneel leren programmeren in PHP en zelf een webapplicatie **kunnen** bouwen?

> Waarom dit zo zwaar telt: we willen het lerende aspect benadrukken, en juist in het Overzicht
> moeten we in weinig woorden veel zeggen, expliciet én impliciet tegelijk.

**Zelfde regel, andere vorm.** Een platte bewering over wat de deelnemer straks doet, wordt een
constructie met "kunnen" of "in staat zijn":

❌ Zo werk je gestructureerder aan data-analyse.
✅ Zo ben je in staat om gestructureerder te werken aan data-analyse.

❌ … zodat je sneller het passende type kiest.
✅ … zodat je sneller het best passende type **kunt** kiezen.

> Die tweede laat meteen zien dat een overtreffende trap over het oordeel van de deelnemer
> juist scherper is (schrijfspec §0.2). Verboden zijn superlatieven die óns aanprijzen.

**Waar het níét hoort: de Doelen-bullets.** Die lopen door op "Na deze training ben je in staat
om:", dus daar dubbelt het:

❌ Een dynamische webapplicatie te **kunnen** bouwen op basis van PHP en MySQL
✅ Een dynamische webapplicatie te bouwen op basis van PHP en MySQL

---

## 2. De leer-constructie bij een reeks vaardigheden

Een rij handelingen in de kale tegenwoordige tijd leest als een opsomming van wat er gebeurt.
Met "leert … te …" wordt het een opsomming van wat de deelnemer verwerft. Let op het herhaalde
"te" bij elk element (schrijfspec §0.18).

❌ Je beoordeelt datakwaliteit, kiest passende algoritmen en tools, en vertaalt uitkomsten naar visualisaties en rapportages.
✅ Je leert datakwaliteit **te** beoordelen, passende algoritmen en tools **te** kiezen en uitkomsten **te** vertalen naar visualisaties en rapportages.

---

## 3. Het waarom in de openingsvraag (schrijfspec §2)

De "Wil je …"-vraag noemt vaak alleen de handeling. Zonder het doel is hij een echo van de
titel.

❌ Wil je design patterns in JavaScript gericht kunnen inzetten?
✅ Wil je design patterns in JavaScript gericht kunnen inzetten, zodat je code overzichtelijk blijft naarmate je applicatie groeit?

---

## 4. De belofte hangt aan het zwaartepunt van de training

Een opbrengst die klopt maar niet de kern is, verkoopt de verkeerde training.

❌ Wil je datamodellen kunnen opzetten die ontwerpkeuzes onderbouwen en discussies over structuur verkorten?
✅ Wil je zelf datamodellen kunnen opzetten en bestaande modellen kunnen beoordelen?

> "Onderbouwen" en "discussies verkorten" stonden er te nadrukkelijk in. Het gaat in deze
> training vooral om zélf modelleren. Het zwaartepunt staat in de kern die je meekrijgt; lees
> het daar af en laat het niet verschuiven.

---

## 5. Het causale verband (schrijfspec §0.12)

Twee zinnen naast elkaar zetten is geen verband leggen. Er hoort een echt verbindingswoord
tussen: **Hierdoor · Waardoor · Doordat · Zo**.

❌ Je leert veilig programmeren en bouwt een eigen webapplicatie, zoals een webwinkel. Zo lever je aan het eind werkende, onderhoudbare code op.
✅ Je leert veilig programmeren en bouwt een eigen webapplicatie, zoals een webwinkel. **Hierdoor** ben je in staat om na afloop van de training werkende, goed onderhoudbare code op te leveren.

❌ De training wordt verzorgd door trainers uit de praktijk, die ervaring hebben in verschillende organisatiecontexten.
✅ Onze trainers zijn, naast trainer, dagelijks werkzaam op dit expertisegebied, **waardoor** ze zowel actuele theoretische kennis als praktische ervaring hebben.

---

## 6. Geen loze verwijzingen (schrijfspec §0.16)

❌ … en houd je overzicht over het hele proces.
✅ … en houd je overzicht over het hele analysetraject, van businessvraag tot ingebruikname.

> "Welk proces?": als de lezer dat moet raden, doet de verwijzing geen werk.

---

## 7. Vaktermen die de lezer niet kent (schrijfspec §0.17)

❌ Je start bij de vraag wanneer een oplossing een pattern is en wanneer het een **proto-pattern** blijft.
✅ Je start bij de vraag wanneer een oplossing zich genoeg bewezen heeft om een pattern te heten, en wanneer het nog een veelbelovend idee is.

> De term zegt vrijwel niemand iets. Omschrijven in gewone woorden kost twee woorden meer en
> levert een lezer op die niet afhaakt.

---

## 8. Nederlands idioom (schrijfspec §0.18)

**Overgankelijke werkwoorden krijgen een lijdend voorwerp.**

❌ Door te oefenen, bespreken en reflecteren ervaar je hoe …
✅ Door actief te oefenen, te analyseren en te evalueren, maak je je de materie stap voor stap eigen …

> "Bespreken" hangt in de lucht: bespreken wát? "Te overleggen" had wel gekund. En het
> voorzetsel "te" hoort bij élk element herhaald.

**Bijvoeglijke naamwoorden op -baar krijgen een kwalificatie.**

❌ … zodat je code onderhoudbaar blijft.
✅ … zodat je code **goed** onderhoudbaar blijft.

> In het Engels kan "maintainable code" los staan; in het Nederlands mist er een kwalificatie:
> goed, makkelijk, door jezelf.

**Lidwoord en verbuiging bij leenwoorden.**

❌ We nemen **moderne** JavaScript als uitgangspunt.
✅ We nemen **modern** JavaScript als uitgangspunt.

**Geen opvulconnectieven als structuur.**

❌ **Tot slot** bouw je een eigen applicatie waarin je patterns onderbouwd kiest en toepast.
✅ **Aan het eind van de training** bouw je een eigen applicatie waarin je patterns onderbouwd kiest en toepast.

> Alleen als het letterlijk het einde van de training is. Betekent het dat niet, dan hoort het
> connectief er helemaal niet te staan.

---

## 9. Kies het woord op ware grootte (schrijfspec §0.19)

❌ … en zet een MySQL-database op die je in je **scripts** gebruikt.
✅ … en zet een MySQL-database op die je in je **applicatie** gebruikt.

> "Scripts" is wat mager voor wat de deelnemer daadwerkelijk bouwt. Onderschatten is net zo
> onnauwkeurig als overdrijven.

---

## 10. Vaste woordenschat (schrijfspec §0.20)

❌ Deze training **is voor** iedereen die vanaf de basis wil leren programmeren in PHP.
✅ Deze training **is bedoeld voor** iedereen die vanaf de basis wil leren programmeren in PHP.

❌ Tijdens de **T**raining PHP Professional komen onderstaande onderwerpen aan bod.
✅ Tijdens de **t**raining PHP Professional komen onderstaande onderwerpen aan bod.

❌ De training wordt verzorgd door trainers **uit de praktijk**.
✅ Onze trainers zijn, naast trainer, **dagelijks werkzaam op dit expertisegebied**.

❌ Binnen dit **vakgebied** beschikken wij over ruime praktijkervaring.
✅ Binnen dit **expertisegebied** beschikken wij over ruime kennis en praktijkervaring.

---

## 11. Afstemming op de eigen context

De woorden "werksituatie" en "werkpraktijk" horen hier standaard thuis; ze zeggen concreet
waar het over gaat.

❌ Waar het past, stemmen we voorbeelden en accenten af op de context waarin jij of je team werkt.
✅ Waar gewenst en mogelijk, laten we onze voorbeelden en suggesties aansluiten bij de context waarin jij of je team werken.

❌ Onze trainers programmeren zelf in de praktijk en delen keuzes die je op je werk herkent.
✅ Onze trainers zijn dagelijks werkzaam op dit expertisegebied en geven tips en inzichten die van toepassing zijn op je eigen werkpraktijk.

---

## 12. Alinea-indeling van de Inleiding (schrijfspec §3)

De Inleiding staat in meerdere alinea's, met een knip bij elke onderwerpwisseling. De passage
over de trainers begint **altijd** een nieuwe alinea; die stapt over van de stof naar de
mensen.

❌ … Zo lees je legacy-code zonder die stijl over te nemen. Onze trainers werken zelf dagelijks aan JavaScript-applicaties. Je mag rekenen op een training waarin we theorie en code afwisselen.

✅ … Zo lees je legacy-code zonder die stijl over te nemen.
>
> Onze trainers werken zelf dagelijks aan JavaScript-applicaties. Je mag rekenen op een training waarin we theorie en code afwisselen.

---

## 13. Vervolgstappen: noodzaak boven interesse (schrijfspec §9)

Iemand kiest zelden een vervolgtraining uit interesse; er moet iets, er knelt iets, of er moet
iets bereikt worden.

❌ Zo kies je een vervolgstap die past bij jouw rol, interesses en werksituatie.
✅ Wil je je PHP-vaardigheden verdiepen, dan sluiten deze trainingen aan:

❌ Wil je **verder** verdiepen, verbreden of juist werken aan een specifieke vraag …
✅ Wil je verdiepen, verbreden of juist werken aan een specifieke vraag …

> "Verdiepen" en "verbreden" houden dat "verder" al in.

---
---

# Ronde 2: 3 trainingen, 45 comments

De tweede lezing, op tekst die al met alle regels hierboven is geschreven. Dat zie je terug:
de fouten uit ronde 1 komen niet meer voor. Wat er nu ligt gaat één laag dieper: over
werkwoordkeuze, over idioom en over de vraag of de belofte groot genoeg is opgeschreven.

---

## 14. Aan de onderkant schrijven (schrijfspec §0.19): de grootste groep

Tien van de 45 comments. Het patroon is telkens hetzelfde: de zin klopt, maar hij is zo mager
geformuleerd dat er geen training uit spreekt. **Het niveau verandert hierbij niet**: §1a
blijft gelden. Wat verandert is het werkwoord waarmee je dat niveau opschrijft.

❌ Wil je de begrippen, methoden en tools rond Big Data kunnen **plaatsen**?
✅ Wil je de opbouw van een data-analysetraject kunnen **doorgronden**?

> "In de eerste zinnen van elke trainingbeschrijving moeten we asap ons visitekaartje afgeven.
> Dat betekent dat we spot-on werkwoorden moeten gebruiken: 'plaatsen' is een nietszeggend
> werkwoord."

❌ Hierdoor kun je **gerichter meepraten** over data-analyse.
✅ Hierdoor leg je een **stevige basis** als data-analist.

> "'Gerichter meepraten' is, ook als dat is wat het oplevert, waarschijnlijk niet een belofte
> waar iemand veel geld voor gaat neertellen."

❌ … en ervaar je hoe een data-analysetraject **in elkaar zit**.
✅ … en ervaar je hoe een data-analysetraject **is opgebouwd**.

> "'Weten/doorgronden hoe iets is opgebouwd' is ook aan de onderkant, maar is in ieder geval
> een respectabele constructie en kan ook ge-upgrade worden met bijvoeglijke naamwoorden."

❌ In deze training **werk je aan** datamodellen die houvast geven.
✅ In deze training **verdiep je je in** datamodellen die houvast geven.

> "Je bent niet op je werk, dus er moet iets bovenop. Het is niet iets vrijblijvends tenslotte."

❌ … en weet daardoor welk model in welke ontwerpfase **iets oplevert**.
✅ … en weet daardoor welk model in welke ontwerpfase **waardevol is**.

❌ … kun je een analysetraject in je eigen organisatie **beter volgen** en beoordelen.
✅ … kun je een analysetraject in je eigen organisatie **doorgronden** en beoordelen.

❌ **Zelfstandig** met design patterns te werken bij het ontwerpen van je JavaScript-code
✅ Design patterns onderbouwd toe te passen bij het ontwerpen van je JavaScript-code

> "'Zelfstandig' lijkt me toch het minste. Meestal wordt dit ingezet als waardetoevoegend,
> terwijl dat alleen zo is als je geen derde partijen meer nodig hebt. Is dat het geval, noem
> het dan zo: 'zonder tussenkomst van derde partijen'."

**Wat wél werkt bij een foundation- of basistraining**, zonder boven de scope te komen: "de
opbouw van X doorgronden", "een stevige basis leggen in X", "je begrijpt de structuur van X
volledig", "je beheerst de basis van X".

---

## 15. De slotzin staat in de in-staat-vorm (schrijfspec §2, §3)

Twee comments, allebei op dezelfde plek: de zin die de opbrengst aan de training koppelt.

❌ **Hierdoor kun je** na afloop een pattern kiezen en verdedigen, in plaats van het alleen te herkennen.
✅ **Hierdoor ben je in staat om** een pattern te kiezen en die keuze te verdedigen, in plaats van het alleen te herkennen.

> "'Kunnen' alleen mist de kracht; het kan ook bedoeld zijn als suggestie. Door eindeloos te
> herhalen dat 'je in staat bent om', dan wel 'weet hoe je iets kunt bewerkstelligen', maak je
> keer op keer spot-on duidelijk wat de toegevoegde waarde van de training is."

❌ Hierdoor **kun** je in je eigen applicatie het best passende ontwerppatroon kiezen.
✅ Hierdoor **stelt de training je in staat om** in je eigen applicatie het best passende ontwerppatroon te kiezen.

---

## 16. Anglicismen (schrijfspec §0.18, `humanisering_nl.md` §G)

> "Dit is een anglicisme. Er moet in de reviewrondes een anglicismecheck komen."

❌ Daarna **werk je door** de categorieën waarin patterns worden ingedeeld.
✅ Daarna **neem je** de categorieën **door** waarin je patronen indeelt.

❌ Je leert het onderscheid tussen een conceptueel, logisch en fysiek model **kennen**.
✅ Je leert **onderscheid te maken** tussen een conceptueel, logisch en fysiek model.

> "Je leert geen onderscheid kennen: je leert onderscheid maken tussen … en …, of: je leert wat
> het verschil is tussen … en …"

**Het Engels als hele term, het Nederlands als los woord:**

❌ Je leert de belangrijkste **patterns** te benoemen en te doorgronden hoe zo'n **pattern** is opgebouwd.
✅ Je leert de belangrijkste **design patterns** te benoemen en te doorgronden hoe zo'n **patroon** is opgebouwd.

> "Wel design pattern als volledige term, maar patroon als los woord."

**En lange samenstellingen krijgen een streepje op de naad:**

❌ Kennismaken met **datamodelleringssoftware**
✅ Kennismaken met **datamodellerings-software**

---

## 17. De openingsvraag dekt de kern, niet één deelaspect (schrijfspec §2)

Ronde 1 leerde dat de openingsvraag het *doel* moet noemen en niet alleen de handeling. Ronde 2
gaat een stap verder: dat doel moet ook het zwaartepunt van de héle training zijn.

❌ Wil je design patterns in JavaScript gericht kunnen inzetten, zodat je code overzichtelijk blijft naarmate je applicatie groeit?
✅ Wil je design patterns in JavaScript gericht kunnen inzetten, zodat je applicaties professioneel opgezet, goed onderhoudbaar en makkelijk te integreren blijven?

> "Ik vind de openingsvragen nog niet heel scherp. Dit heeft niet alleen met groeien te maken,
> maar met een professioneel app-ontwerp, onderhoudbaarheid, integratie."

**En het Overzicht mag langer worden om de kern compleet te maken.** Bij Data Modeling ontbrak
de introductie in stermodelleren volledig, en dat is een onderwerp waar een hele module over gaat.

> "Lengtebeperking is wat mij betreft geen doel op zich. Liever wat langer, maar een complete
> intro in de materie, met logische doorvertalingen, dan korter door de bocht en 'lekker kort'."

---

## 18. Maak "je" het onderwerp (schrijfspec §0.3, §3)

> "Het gebruik van actieve constructies is wat mij betreft wel een doel op zich. Gebruik van
> 'je' maakt het altijd persoonlijker en aansprekender."

❌ … maar de waarde komt pas vrij als je weet **welke techniek bij welk vraagstuk hoort**.
✅ … maar de waarde komt pas vrij als je weet **met welke technieken je die data omzet in bruikbare informatie**.

❌ … en zie je per fase **welke technieken en tools in beeld komen**.
✅ … en zie je per fase **van welke technieken en tools je je kunt bedienen**.

❌ Het ontwerp van een informatiesysteem begint bij de vraag **hoe je data structureert** voordat er iets gebouwd wordt.
✅ Het ontwerp van een informatiesysteem begint bij de vraag **hoe je je data kunt structureren, nog voor je begint te bouwen**.

---

## 19. De Kortste omschrijving noemt het moment (schrijfspec §10)

Drie keer, één per training, met "structureel" erbij.

❌ Wil je design patterns in JavaScript gericht kunnen inzetten? **Je leert** de belangrijkste patterns te benoemen en toe te passen.
✅ Wil je design patterns in JavaScript gericht kunnen inzetten? **Na deze training weet je hoe je** ze benoemt, kiest en toepast.

❌ Wil je zelf datamodellen kunnen opzetten en beoordelen? **Je werkt met** conceptuele, logische en fysieke modellen.
✅ Wil je zelf datamodellen kunnen opzetten en beoordelen? **Na deze training beheers je** conceptuele, logische en fysieke modellen.

---

## 20. Doelgroep en Voorkennis dubbelen niet (schrijfspec §6, §7)

> "Hoe verhoudt zich dit tot hetgeen erboven staat: 'deze training is bedoeld voor iedereen die
> al in JS ontwikkelt…'?"

❌ Doelgroep: "… voor iedereen die al in JavaScript ontwikkelt en zijn code beter wil structureren."
   Voorkennis: "Enige ervaring in het werken met JavaScript is vereist."

✅ Doelgroep: "… voor iedereen die zijn JavaScript-code beter wil structureren met bewezen ontwerpoplossingen."
   Voorkennis: "Ervaring met ontwikkelen in JavaScript is vereist, waaronder werken met functies, objecten en modules. Mocht je hier vragen over hebben, neem dan gerust contact met ons op."

---

## 21. Losse woordkeuzes

❌ Mocht je hier vragen over hebben, neem gerust contact met ons op.
✅ Mocht je hier vragen over hebben, neem **dan** gerust contact met ons op.

> "Altijd 'dan' toevoegen in deze context."

❌ Je kijkt **ook** naar datakwaliteit: welke eisen stel je aan je data?
✅ **Daarnaast** kijk je naar datakwaliteit: welke eisen stel je aan je data?

> "In opsommingen liever standaard 'daarnaast' gebruiken dan 'ook'."

❌ … een training waarin we theorie en **eigen code** afwisselen.
✅ … een training waarin we theorie en **praktijk** afwisselen.

> "'Eigen code' afwisselen met theorie impliceert naar mijn idee te weinig. Dan zou ik hier
> liever standaard 'praktijk' zien."

❌ **In deze training van twee dagen** krijg je een overzicht van de methoden en modellen.
✅ **Tijdens deze training** krijg je een overzicht van de methoden en modellen.

> Twee correcties in één: geen duur in de tekst (§0.21), en de zin die de openingsvraag
> beantwoordt noemt de training.

❌ Je kijkt naar datakwaliteit: welke eisen stel je aan je data, en hoe raken governance en privacywetgeving daaraan.
✅ Je kijkt naar datakwaliteit: welke eisen stel je aan je data, en hoe raken governance en privacywetgeving daaraan**?**

---

## 22. Een mogelijkheid formuleer je als mogelijkheid (schrijfspec §8)

❌ Anti-patterns in bestaande code op te sporen en te onderbouwen waarom ze **problemen geven**
✅ Anti-patterns in bestaande code op te sporen en te onderbouwen waarom deze **problemen zouden kunnen veroorzaken**

> "Het is een eventualiteit (eerste deel van de zin), die vervolgens te direct wordt getackeld."

---

## 23. Twee dingen die de code niet ziet

**Een causaal verband moet ook kloppen.** Niet elke "Hierdoor" is een echt verband:

❌ … ervaar je hoe een zorgvuldig opgezet datamodel je ontwerpkeuzes onderbouwt en fouten voorkomt.

> "Waarom moeten je ontwerpkeuzes nog onderbouwd worden als je al een zorgvuldig opgezet
> datamodel hebt?"

**En een claim moet grijpbaar zijn** (§0.16 gaat over verwijzingen; dit gaat over de claim zelf):

❌ … beoordeel je bestaande modellen scherper en **leg je nieuwe structuren reproduceerbaar vast**.

> "In je hoofd? Op papier? Deze vind ik ongrijpbaar en misschien wel hoogdravend."

---

# Ronde 3: de eerste batch met deze regels

Niet uit een reviewronde maar uit een meting over de eigen catalogus: twee patronen waarop de
output systematisch afweek van de 78 bestaande trainingen.

## 24. De tweede zin van het Overzicht noemt de training (schrijfspec §2)

73 van de 78 bestaande trainingen beginnen de zin die op de "Wil je"-vraag volgt met "In deze
training" of "Tijdens deze training". Zonder die opening hangt het antwoord los van de vraag.

❌ Wil je AI-taalmodellen zo kunnen aansturen dat de output aansluit op jouw werk? **Je leert** je eigen documenten, processen en doelen als context aan te leveren.
✅ Wil je AI-taalmodellen zo kunnen aansturen dat de output aansluit op jouw werk? **In deze training leer je** je eigen documenten, processen en doelen als context aan te leveren.

❌ Wil je zelf datamodellen kunnen opzetten …? **Je werkt met** conceptuele, logische en fysieke modellen.
✅ Wil je zelf datamodellen kunnen opzetten …? **In deze training werk je met** conceptuele, logische en fysieke modellen.

❌ Wil je de opbouw van een data-analysetraject kunnen doorgronden …? **Je maakt kennis met** de terminologie rond Big Data.
✅ Wil je de opbouw van een data-analysetraject kunnen doorgronden …? **Tijdens deze training maak je kennis met** de terminologie rond Big Data.

## 25. Het programma is geen inhoudsopgave (schrijfspec §4)

Het oude goud heeft mediaan zes modules met samen ~20 sub-bullets. Zes of zeven modules met
overal vier tot vijf sub-bullets is bijna 30 regels: dat leest als een inventarisatie van het
vakgebied en niet als een programma van twee dagen.

❌ Zeven modules bij een tweedaagse, met sub-bullets zoals "Aandachtspunten rond privacy, vertrouwelijkheid en datasensitiviteit afwegen" én, drie modules verderop, "Richtlijnen voor verantwoord gebruik en blijvende verbetering opstellen".
✅ Vijf modules, waarbij die twee onderwerpen in dezelfde module staan.

> Kies het richtgetal (4 bij één dag, 5 bij twee tot drie, 6 vanaf vier) en voeg verwante
> onderwerpen samen. Niet: de stof over meer modules uitsmeren zodat elke module "vol" oogt.

---

# Ronde 4: de eerste batch die zelf goud werd

Vier trainingen, gegenereerd met de regels uit ronde 3. Ze haalden alle checks; wat de mens
er nog uithaalde zit hieronder. Twee ervan zijn sindsdien een harde code-check.

## 26. Een opsomming die breedte toont, blijft breed (schrijfspec §0.24, §12)

De bron somt vijf werkvelden op om te laten zien hoe bréed de training van pas komt. Het
concept maakte er een voorwaarde van, en daarmee zegt de zin iets anders: hij sluit iedereen
buiten die opsomming uit. De woorden zijn bijna gelijk, de belofte niet.

❌ **Werk je in** communicatie, beleid, HR, klantcontact of projectmanagement, **dan** ben je na deze masterclass in staat om AI als vast verlengstuk van je denken en handelen in te zetten.
✅ **Of je nu in** communicatie, beleid, HR, klantcontact of projectmanagement **werkt**, na deze masterclass kun je AI inzetten als verlengstuk van je denken en handelen.

> "of het nu gaat om" staat op de verbodslijst, "Of je nu in X werkt" niet. Wie de eerste
> vermijdt door er een voorwaarde van te maken, ruilt een stijlprobleem in voor een
> inhoudelijk probleem. De code flagt deze constructie, maar alleen jij ziet de bron ernaast.

## 27. Het liggende streepje (schrijfspec §0.23, `humanisering_nl.md` §D)

Stond in twee van de vier kopjes van één training, terwijl het verbod al in de spec stond.
Sindsdien vuurt `check_em_dash` er hard op, en staat er in geen van de spec-bestanden nog een
streepje dat als voorbeeld kan dienen.

❌ Je voedt de AI met informatie uit je eigen organisatie **[liggend streepje]** beleidsdocumenten, werkinstructies, klantprocessen of communicatieformats **[liggend streepje]** en schrijft daarop prompts.
✅ Je voedt de AI met informatie uit je eigen organisatie **(**beleidsdocumenten, werkinstructies, klantprocessen of communicatieformats**)** en schrijft daarop prompts.

> Het foute voorbeeld staat hier bewust met het teken uitgeschreven: in geen van de
> spec-bestanden komt een liggend streepje nog letterlijk voor, zodat de schrijver het nergens
> in zijn context ziet staan. `test_rewrite.py` bewaakt dat.

## 28. Een introzin bij Vervolgstappen kondigt minstens twee trainingen aan (schrijfspec §9)

Een categorie-intro belooft een richting. Staat er één bullet onder, dan leest de lezer een
fout in plaats van een keuze. De code snoeit zo'n groep nu weg, introzin en al.

❌ "Wil je de stap zetten naar machine learning en AI-gedreven data-analyse, dan biedt deze training een visuele introductie:" met daaronder één titel.
✅ Eén groep met vier titels, en de vijfde training valt weg; of twee groepen die allebei minstens twee titels dragen.

---

## 29. De deelnemer brengt geen eigen case mee (schrijfspec §0.25)

Training 3036 (Change Management voor DAMA-DMBOK) beloofde het twee keer: in het Overzicht en
in het programma. Werken aan materiaal dat de deelnemer zelf aanlevert kan alleen bij een
bedrijfstraining, en dáárvoor staat het aparte blok onder de Inleiding. In de standaard
beschrijving is het een belofte die we niet nakomen.

❌ Je werkt met stakeholderbetrokkenheid, communicatie en adoptie, en past alles toe op **je eigen praktijkcase**.
✅ Je werkt met stakeholderbetrokkenheid, communicatie en adoptie, en past alles toe op **een praktijkcase**.

❌ Een **eigen** veranderopgave rond datamanagement **inbrengen**
✅ Een veranderopgave rond datamanagement **uitwerken**

❌ De training biedt ruimte om **eigen vraagstukken mee te nemen**.
✅ De training **sluit aan op** de vraagstukken die in jouw werkpraktijk **spelen**.

❌ **Je eigen** document, proces of werkvraag **inbrengen** en uitwerken tot een toepasbare oplossing
✅ **Een** document, proces of werkvraag **uitwerken** tot een toepasbare oplossing

❌ Je werkt aan casussen **uit je eigen praktijk**.
✅ Je werkt aan **herkenbare** casussen **uit de praktijk**.

> De bron verleidt hiertoe, en dat is precies waarom de fout ontstond: daar staat "jouw
> praktijkcase", maar dat betekent dat je een praktijkcase *krijgt* om aan te werken. Neem die
> formulering niet over. Let ook op het bezittelijke woord zónder werkwoord: "een praktijkcase"
> mag, "je eigen praktijkcase" en "jullie eigen casussen" niet. En het telt ook als het bezit
> een zelfstandig naamwoord verderop staat ("casussen uit je eigen praktijk", "materiaal uit je
> eigen werk"). `check_eigen_case` vuurt hard op alle drie de vormen.
>
> Twee dingen vallen er niet onder. Ten eerste, zie sectie 11: dat de training aansluit op je
> werksituatie, dat er ruimte is voor jouw vragen en eigen situaties, en dat je het geleerde
> vertaalt naar je eigen organisatie.
>
> Ten tweede, en dat is de belangrijkste: **wat je tijdens de training zelf máákt.** Wij leveren
> de praktijkcase, en wat je daar vervolgens mee bouwt is de oefening waarvoor je komt. Die
> belofte hoort er juist wél te staan; schrijfspec Sectie 0.15 noemt dat eindproduct met zoveel
> woorden.

✅ Je leert de belangrijkste patronen te benoemen, toe te passen en **in een praktijkcase te verwerken tot een eigen applicatie**.
✅ **Een roadmap opstellen** voor een SIEM-oplossing binnen je eigen organisatie

> **De richting beslist, niet het woord "eigen".** Naar binnen is verboden, naar buiten is de
> opbrengst. Twee zinnen die bijna gelijk klinken en tegengesteld zijn: "je werkt **je eigen**
> praktijkcase uit" (jij levert de case, fout) tegenover "je werkt **een** praktijkcase uit tot
> een eigen applicatie" (wij leveren de case, jij bouwt het resultaat, goed).

---

## 30. Een "benoem"-actie levert een vermelding, geen belofte (schrijfspec §12)

Training 27 (SQL) kreeg één goedgekeurde actualisering mee: "refresh: benoem concrete
SQL-platformen (bv. PostgreSQL, SQL Server, cloud data warehouses) als context bij de training",
met de reviewer-voorwaarde "in inleiding is dat prima". Die voorwaarde is netjes gevolgd, de
zin staat in de Inleiding. Het werkwoord is dat niet: de actie vroeg om **benoemen**, de tekst
belooft **toepassen**. Wij gebruiken die platformen in de training helemaal niet.

❌ De SQL die je leert, **pas je direct toe op** verschillende platformen, van PostgreSQL en SQL Server tot cloud data warehouses.
✅ De SQL die je leert, **werkt op** de platformen die je in de praktijk tegenkomt, zoals PostgreSQL, SQL Server en cloud data warehouses.

> De twee zinnen noemen precies dezelfde drie platformen; alleen de tweede belooft er niets
> mee. Dat is het hele verschil tussen een vermelding en een leerdoel.
>
> Het werkwoord van de actie is de bovengrens, niet een startpunt. "Benoem", "noem" en
> "vermeld" leveren een zin in de lopende tekst en verder niets: geen module, geen
> bullet-onderwerp, geen doel. Wil de reviewer meer, dan staat er "behandel", "voeg toe" of
> "neem op"; moet er iets voor wijken, dan staat er "vervang" of "update". Twijfel je tussen
> twee lezingen, kies dan de lichtste.
>
> De judge keurde deze zin goed met nul problemen, en dat was geen slordigheid: zijn eigen spec
> verbood hem om een passage die uit een goedgekeurde actie voortkomt af te rekenen als "te
> hoge belofte". Die vrijstelling dekt sinds deze ronde het onderwerp en niet het niveau.
> `check_actie_escalatie` flagt de constructie nu ook in code, maar alleen jij ziet de actie
> en de zin naast elkaar.
