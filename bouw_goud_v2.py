"""Bouwt herschreven/goud_v2/<id>.json uit de vier nagelezen trainingen.

Elke opmerking uit de reviewronde is verwerkt, plus de nieuwe regels uit de schrijfspec.
De vaste teksten komen uit `sjabloon` via de echte renderers, zodat er geen enkele kans is
dat het voorbeeldmateriaal verouderde boilerplate demonstreert.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rewrite_output as uit          # noqa: E402
import sjabloon                        # noqa: E402
import rewrite_checks as checks        # noqa: E402

UIT_DIR = os.path.join(HERE, "herschreven", "goud_v2")


TRAININGEN = [
    # -------------------------------------------------------------------
    {
        "training_id": "v2_php",
        "titel": "Training PHP Professional",
        "dagen": 5,
        "modules_nb": "stabiel",
        "overzicht": (
            "Wil je professioneel leren programmeren in PHP en zelf een webapplicatie "
            "kunnen bouwen die je daarna goed kunt onderhouden? In deze training werk je van "
            "functioneel naar objectgeoriënteerd programmeren en zet je een MySQL-database op "
            "die je in je applicatie gebruikt. Je leert veilig te programmeren, beheert je "
            "afhankelijkheden met Composer en bouwt een eigen webwinkel. Hierdoor ben je in "
            "staat om werkende, goed onderhoudbare code op te leveren."
        ),
        "inleiding": (
            "PHP draait achter een groot deel van het web, van kleine sites tot complexe "
            "applicaties. In deze training bouw je een stevige basis om er professioneel mee te "
            "kunnen werken. Je begint met functioneel programmeren en de kernconstructies van "
            "de taal. Daarna stap je over naar objectgeoriënteerd programmeren: je ontwerpt "
            "eigen classes en zet bestaande libraries in voor taken als PDF-generatie en "
            "e-mail.\n\n"
            "Je leert een MySQL-database op te zetten, te bevragen en te koppelen aan je code. "
            "Veiligheid behandelen we niet als los onderwerp, maar als vast onderdeel van "
            "alles wat je schrijft. Je valideert invoer, versleutelt wachtwoorden en werkt met "
            "prepared statements. Ook moderne werkwijzen komen aan bod, zoals "
            "afhankelijkheden beheren met Composer en het toepassen van PSR-standaarden. Je "
            "werkt toe naar een eigen webapplicatie die je in overleg met de trainer afbakent, "
            "waardoor je elke techniek direct in een concreet project toepast.\n\n"
            "Onze trainers zijn, naast trainer, dagelijks werkzaam op dit expertisegebied. "
            "Hierdoor beschikken ze over actuele kennis én over de praktijkervaring om keuzes "
            "te bespreken die je op je eigen werk herkent. Waar gewenst en mogelijk laten we "
            "de voorbeelden aansluiten bij de context waarin jij of je team werken."
        ),
        "modules": [
            {"titel": "Programmeren in PHP", "bullets": [
                "Werken met variabelen, condities, loops en functies",
                "Omgaan met arrays en reguliere expressies",
                "Cookies en sessies inzetten voor het beheren van state",
                "Exception handling en debugging toepassen",
                "Moderne taalfeatures van PHP benutten",
            ]},
            {"titel": "Databases met MySQL", "bullets": [
                "Zelf een relationele database opzetten in MySQL",
                "Data bevragen en manipuleren vanuit je applicatie",
                "Beheren en inspecteren via phpMyAdmin",
                "Werken met migraties en object-relational mapping (ORM)",
            ]},
            {"titel": "Objectgeoriënteerd programmeren", "bullets": [
                "Classes en objects ontwerpen voor eigen software",
                "Externe libraries gebruiken voor template-rendering, PDF-generatie en e-mail",
                "Basis design patterns toepassen",
                "Afhankelijkheden beheren met Composer",
                "Code structureren volgens PSR-standaarden",
            ]},
            {"titel": "Veilig programmeren", "bullets": [
                "Gebruikersinvoer valideren en filteren",
                "Wachtwoorden versleutelen met password_hash",
                "SQL-injectie voorkomen met prepared statements",
                "Je database afschermen tegen ongeautoriseerde toegang",
            ]},
            {"titel": "Eindopdracht: webapplicatie bouwen", "bullets": [
                "Een eigen webapplicatie ontwerpen, zoals een webwinkel",
                "PHP en MySQL combineren tot een werkend geheel",
                "Een templating systeem inzetten voor de presentatielaag",
                "Je project in overleg met de trainer afbakenen",
                "Functionaliteit testen en stapsgewijs verbeteren",
            ]},
        ],
        "doelgroep": (
            "Deze training is bedoeld voor iedereen die vanaf de basis wil leren programmeren "
            "in PHP en zelf webapplicaties wil kunnen bouwen."
        ),
        "voorkennis": sjabloon.VOORKENNIS_FALLBACK,
        "aanpak_invulling": "je losse PHP-scripts uitbouwt tot een werkende, veilige webapplicatie",
        "doelen": [
            "Werkende PHP-code te ontwerpen, te schrijven en te debuggen",
            "Een relationele database in MySQL op te zetten en te bevragen",
            "Objectgeoriënteerd te programmeren met eigen en externe classes",
            "Veilig te programmeren met inputvalidatie en prepared statements",
            "Een dynamische webapplicatie te bouwen op basis van PHP en MySQL",
        ],
        "groepen": [
            {"intro": "Loop je in je werk tegen complexere PHP-vraagstukken aan, dan sluiten "
                      "deze trainingen aan:",
             "titels": ["Masterclass PHP", "Secure Coding PHP"]},
            {"intro": "Moet je je kennis verbreden naar aangrenzende gebieden, dan zijn deze "
                      "trainingen interessant:",
             "titels": ["Database Design", "Angular", "Python"]},
        ],
        "kortste_omschrijving": (
            "Wil je professioneel leren programmeren in PHP en zelf een webapplicatie kunnen "
            "bouwen? Na deze training weet je hoe je objectgeoriënteerd en veilig werkende "
            "code oplevert."
        ),
    },
    # -------------------------------------------------------------------
    {
        "training_id": "v2_datamodeling",
        "titel": "Training Data Modeling",
        "dagen": 2,
        "modules_nb": "stabiel",
        "overzicht": (
            "Wil je zelf datamodellen kunnen opzetten en bestaande modellen kunnen "
            "beoordelen, zodat je datastructuren altijd betrouwbaar blijven, ook als het "
            "systeem groeit? In deze training werk je met conceptuele, logische en fysieke "
            "modellen, met ER-diagrammen en met normalisatie tot 3NF, en maak je kennis met "
            "stermodelleren volgens Kimball. Je leert een model te toetsen aan concrete eisen "
            "en testcases. Hierdoor ben je in staat om je ontwerpkeuzes te onderbouwen en de "
            "kwaliteit van je datastructuren te bewaken."
        ),
        "inleiding": (
            "Data modeling begint bij de vraag hoe je je data kunt structureren, nog voor je "
            "begint te bouwen. In deze training verdiep je je in drie modelniveaus. Het "
            "conceptuele model bakent "
            "de scope af, het logische model verfijnt de structuur en het fysieke model "
            "vertaalt naar datastores. Je legt entiteiten, attributen en sleutels vast en "
            "kiest de juiste cardinaliteit bij 1:1, 1:N en N:N.\n\n"
            "Relaties werk je uit met joins en foreign keys. Normalisatie tot en met de derde "
            "normaalvorm gebruik je om redundantie en inconsistenties op te sporen. Daarnaast "
            "krijg je een introductie in sterschema's volgens Kimball en leer je het verschil "
            "met relationeel modelleren te herkennen. Als contrast bekijk je kort Data Vault "
            "en document-datamodellen. Je maakt kennis met modelleringssoftware en zet daarin "
            "je eerste model op.\n\n"
            "Onze trainers ontwerpen dagelijks databases en informatiesystemen. Hierdoor "
            "koppelen ze de theorie aan ontwerpbeslissingen die je in je eigen werkpraktijk "
            "herkent. We nemen jouw systemen en modelleervraagstukken als uitgangspunt en "
            "toetsen modellen aan concrete eisen en testcases. Zo weet je na afloop hoe je "
            "een model opzet, toetst en verdedigt tegenover ontwikkelaars en architecten."
        ),
        "modules": [
            {"titel": "Data modeling in ontwerp en analyse", "bullets": [
                "Begrippen en definities rond datamodellering doorgronden",
                "De rol van datamodellen binnen informatiesystemen bepalen",
                "De waarde van modelleren voor ontwerp, communicatie en besluitvorming benoemen",
                "Valkuilen en aandachtspunten bij het modelleren herkennen",
            ]},
            {"titel": "Conceptueel, logisch en fysiek modelleren", "bullets": [
                "Scope afbakenen in een conceptueel model",
                "Structuur verfijnen in een logisch model",
                "Vertalen naar datastores en implementatie in een fysiek model",
                "Schemaregels toepassen zodat modellen onderling consistent blijven",
                "Kwaliteitscriteria per modelniveau vaststellen",
            ]},
            {"titel": "Relationele modellering en ER-diagrammen", "bullets": [
                "Entiteiten, attributen en sleutelbegrippen vastleggen",
                "ER-diagrammen lezen en zelf opstellen op basis van concrete eisen",
                "Relatietypen en cardinaliteit bepalen bij 1:1, 1:N en N:N",
                "Associaties uitwerken met joins en foreign keys",
                "Bestaande modellen beoordelen op relaties en sleutelkeuzes",
                "Kennismaken met datamodellerings-software en daarin een eerste model opzetten",
            ]},
            {"titel": "Normalisatie en modelkwaliteit", "bullets": [
                "Normaliseren tot en met de derde normaalvorm",
                "Redundantie en inconsistenties in datastructuren opsporen",
                "Modellen verifiëren en valideren tegen de gestelde eisen",
                "Testcases opstellen en het model daarmee toetsen",
                "Integrity constraints bepalen en vastleggen",
            ]},
            {"titel": "Multidimensionaal modelleren met sterschema's (Kimball)", "bullets": [
                "De opbouw van een sterschema volgens Kimball doorgronden",
                "Multidimensionaal en relationeel modelleren vergelijken",
                "De rol van sterschema's binnen cloud-datawarehouse- en lakehouse-omgevingen bepalen",
                "Data Vault verkennen als aanvulling naast Kimball",
                "Document-datamodellen bekijken als contrast met het relationele model",
            ]},
        ],
        # Doelgroep en Voorkennis staan pal onder elkaar en mogen elkaar niet herhalen
        # (schrijfspec Sectie 6/7): de Doelgroep zegt wat je wilt bereiken, de Voorkennis noemt
        # de concrete voorwaarde.
        "doelgroep": (
            "Deze training is bedoeld voor iedereen die datamodellen wil kunnen lezen, "
            "beoordelen en zelf opzetten bij het ontwerpen van informatiesystemen en databases."
        ),
        "voorkennis": (
            "Enige ervaring met informatiesystemen en databases en een analytische manier van "
            "werken is vereist. Mocht je hier vragen over hebben, neem dan gerust contact met "
            "ons op."
        ),
        "aanpak_invulling": "ontwerpkeuzes in een datamodel doorwerken in de uiteindelijke database",
        "doelen": [
            "De rol van data modeling binnen het ontwerp van informatiesystemen te doorgronden",
            "Conceptuele, logische en fysieke datamodellen te onderscheiden en passend in te zetten",
            "Datamodellen te lezen, te bespreken en op te zetten aan de hand van eisen en relaties",
            "Datastructuren te verduidelijken met ER-diagrammen, entiteiten en normalisatie tot 3NF",
            "Ontwerpkeuzes te onderbouwen en datamodellen te verifiëren en te valideren",
        ],
        "groepen": [
            {"intro": "Moet je geavanceerdere modelleringstechnieken voor datawarehouses "
                      "beheersen, dan sluiten deze trainingen aan:",
             "titels": ["Dimensioneel Modelleren - Stermodelleren - Kimball", "Data Vault",
                        "Anchor Modelling"]},
            {"intro": "Wil je je datamodellen praktisch toepassen in databases en "
                      "BI-omgevingen, dan zijn deze trainingen geschikt:",
             "titels": ["Database Design", "SQL",
                        "Master Power BI - Ontwerpintelligentie met Data Modeling & Stermodelleren"]},
        ],
        "kortste_omschrijving": (
            "Wil je zelf datamodellen kunnen opzetten en beoordelen? Na deze training beheers "
            "je conceptuele, logische en fysieke modellen, ER-diagrammen en normalisatie tot 3NF."
        ),
    },
    # -------------------------------------------------------------------
    {
        "training_id": "v2_bigdata",
        "titel": "Training Big Data Foundation",
        "dagen": 3,
        "modules_nb": "actueel",
        "overzicht": (
            "Wil je grip krijgen op grote datasets en analyses kunnen opzetten die tot "
            "bruikbare conclusies leiden? In deze training leer je het CRISP-DM-model toe te "
            "passen als vaste route door elk analysevraagstuk. Je leert datakwaliteit te "
            "beoordelen, passende "
            "algoritmen en tools te kiezen en uitkomsten te vertalen naar rapportages. "
            "Hierdoor ben je in staat om een analysetraject gestructureerd te doorlopen, van "
            "businessvraag tot ingebruikname, en om de keuzes daarin te onderbouwen."
        ),
        "inleiding": (
            "Zonder vaste methode blijft data-analyse een reeks losse acties. CRISP-DM geeft "
            "je een werkwijze die je op ieder vraagstuk kunt leggen, van een eerste "
            "verkenning tot een model dat in productie draait. We werken die werkwijze stap "
            "voor stap uit in opdrachten waarin je zelf keuzes maakt. Welke bronnen ontsluit "
            "je, hoe stel je kwaliteit vast, welk algoritme past bij welk type vraag? Ook "
            "leer je waarop je een model beoordeelt voordat het in gebruik gaat.\n\n"
            "Daarnaast krijg je overzicht van het speelveld eromheen. Je verkent begrippen als "
            "data mining, machine learning en predictive analytics, en je kijkt naar de "
            "infrastructuur waarop analyses draaien. We introduceren daarbij kort wat "
            "generatieve AI betekent voor het analyseproces. We staan stil bij governance en "
            "bij de eisen die de AVG stelt aan het gebruik van persoonsgegevens. Aan het eind "
            "van de training behandelen we datavisualisatie en rapportage, zodat je "
            "bevindingen landen bij wie beslissingen neemt.\n\n"
            "Onze trainers zijn dagelijks werkzaam op dit expertisegebied. Hierdoor combineren "
            "ze de theorie met voorbeelden uit hun eigen projecten en herken je de afwegingen "
            "die ze bespreken. We laten de training aansluiten bij de onderwerpen en datasets "
            "uit jouw werkpraktijk, zodat je de aanpak direct in je eigen analyses inzet."
        ),
        "modules": [
            {"titel": "Big data en data-analyse in kaart brengen", "bullets": [
                "Doorgronden wat big data betekent voor sturing op strategie en bedrijfsprocessen",
                "Data mining, machine learning en predictive analytics van elkaar onderscheiden",
                "Infrastructuur en tools vergelijken, met cloud dataplatformen als voorbeeld",
                "Kort verkennen wat generatieve AI toevoegt aan het analyseproces",
                "Vooruitkijken naar de ontwikkeling van data science en machine learning",
            ]},
            {"titel": "Datakwaliteit, governance en bronontsluiting", "bullets": [
                "Datakwaliteit definiëren en meetbaar maken op volledigheid en actualiteit",
                "Bronnen ontsluiten met ETL-processen en de herkomst van data vastleggen",
                "Afspraken over eigenaarschap en definities verankeren in data governance",
                "Verwerking van persoonsgegevens toetsen aan de AVG en aan interne kaders",
            ]},
            {"titel": "CRISP-DM: Business Understanding en Data Understanding", "bullets": [
                "De opbouw en de zes fasen van het CRISP-DM-model doorlopen",
                "Een businessvraag omzetten in een scherpe, toetsbare analysevraag",
                "Succescriteria en randvoorwaarden vooraf vastleggen met belanghebbenden",
                "Beschikbare datasets verkennen op structuur, verdeling en afwijkingen",
            ]},
            {"titel": "Data Preparation en Modeling", "bullets": [
                "Data selecteren, opschonen, transformeren en samenvoegen tot een analyseset",
                "Ontbrekende waarden en uitschieters behandelen zonder de analyse te vertekenen",
                "Features construeren die aansluiten op de gekozen analysevraag",
                "Algoritmen kiezen op basis van vraagtype, datavolume en interpreteerbaarheid",
                "Modellen trainen en parameters aanscherpen",
                "Stappen en keuzes documenteren zodat resultaten herleidbaar blijven",
            ]},
            {"titel": "Evaluation en Deployment: van model naar rapportage", "bullets": [
                "Modelresultaten beoordelen op prestatie en op waarde voor de businessvraag",
                "Uitkomsten valideren tegen de vooraf opgestelde succescriteria",
                "Een model in gebruik nemen en het beheer en de monitoring inrichten",
                "Visualisaties kiezen die passen bij de informatievraag van de ontvanger",
                "Rapportages opzetten die conclusies en onzekerheden zichtbaar maken",
                "Analyseresultaten vertalen naar vervolgstappen voor besluitvorming",
            ]},
        ],
        "doelgroep": (
            "Deze training is bedoeld voor iedereen die data-analyses gestructureerd wil "
            "kunnen opzetten en de uitkomsten wil onderbouwen, in sectoren als overheid, zorg, "
            "retail en het bank- en verzekeringswezen."
        ),
        "voorkennis": sjabloon.VOORKENNIS_FALLBACK,
        "aanpak_invulling": (
            "een gestructureerde analyseaanpak je van ruwe data naar een onderbouwd besluit brengt"
        ),
        "doelen": [
            "Het CRISP-DM-model toe te passen als structuur voor een compleet analysetraject",
            "Een businessvraag te vertalen naar een concrete data- en modelleringsaanpak",
            "Datakwaliteit te beoordelen en bronnen te ontsluiten binnen governancekaders",
            "Analysetechnieken en tools te kiezen die passen bij het vraagstuk en de data",
            "Analyseresultaten scherper te evalueren, te visualiseren en te rapporteren",
        ],
        "groepen": [
            {"intro": "Moet je datamodellering, datakwaliteit en implementatie dieper "
                      "beheersen, dan sluiten deze trainingen aan:",
             "titels": ["Big Data Practitioner", "Advanced SQL voor Data Science",
                        "Data Modeling"]},
            {"intro": "Speelt de Big Data-aanpak in een specifieke sector waar jouw "
                      "organisatie actief is, dan bieden deze trainingen inzicht:",
             "titels": ["Big Data voor de Overheid", "Big Data in de Zorg"]},
        ],
        "kortste_omschrijving": (
            "Wil je grip krijgen op grote datasets en analyses kunnen opzetten die tot "
            "bruikbare conclusies leiden? Na deze training pas je het CRISP-DM-model toe, van "
            "businessvraag tot rapportage."
        ),
    },
    # -------------------------------------------------------------------
    {
        "training_id": "v2_jsdesignpatterns",
        "titel": "Training JavaScript Design Patterns",
        "dagen": 3,
        "modules_nb": "stabiel",
        "overzicht": (
            "Wil je design patterns in JavaScript gericht kunnen inzetten, zodat je "
            "applicaties professioneel opgezet, goed onderhoudbaar en makkelijk uit te breiden "
            "blijven? In deze training leer je de structuur achter zo'n patroon te doorgronden "
            "en het toe te passen in moderne JavaScript-code. Je werkt met ES6-modules, React- en "
            "Vue-componenten en TypeScript. Hierdoor ben je in staat om onderbouwd het best "
            "passende patroon te kiezen en die keuze te verdedigen."
        ),
        "inleiding": (
            "In deze training werk je van de structuur van een patroon naar de toepassing "
            "ervan in code. Je start bij de vraag wanneer een oplossing zich genoeg bewezen "
            "heeft om een design pattern te heten, en wanneer het nog een veelbelovend idee "
            "is. Oplossingen die juist averechts werken leer je herkennen aan hun symptomen in "
            "bestaande code. Daarna neem je de categorieën door waarin je die patronen "
            "indeelt, zodat je sneller het best passende type kunt kiezen.\n\n"
            "We nemen modern JavaScript als uitgangspunt: ES6-modules in plaats van losse "
            "namespacing-constructies. Daarnaast kijk je naar de patronen zoals je die in "
            "React- en Vue-componenten terugziet. Ook TypeScript komt aan bod, met interfaces, "
            "generics en decorators als aanvulling op de klassieke oplossingen. Kom je oudere "
            "codebases tegen, dan koppelen we die aan hun moderne variant. Hierdoor lees je "
            "verouderde code zonder die stijl over te nemen.\n\n"
            "Onze trainers werken zelf dagelijks aan JavaScript-applicaties. Je mag rekenen op "
            "een training waarin we theorie en praktijk afwisselen en waarin we ingaan op "
            "vraagstukken uit jouw eigen codebase. We laten de voorbeelden aansluiten bij het "
            "framework en de architectuur waarmee je werkt. Aan het eind van de training bouw "
            "je een eigen applicatie, waarin je je keuzes onderbouwt en toetst op "
            "onderhoudbaarheid."
        ),
        "modules": [
            {"titel": "Fundamenten en de keuze voor een patroon", "bullets": [
                "Onderscheiden van bewezen patronen, veelbelovende ideeën en de rule of three",
                "Ontleden van de structuur van een patroon",
                "Herkennen van oplossingen die averechts werken in bestaande code",
                "Indelen van patronen in categorieën en vergelijken van patronen die op elkaar lijken",
                "Kiezen van het best passende patroon op basis van het probleem",
                "Onderbouwen van je keuze richting je team",
            ]},
            {"titel": "Patronen in modern JavaScript", "bullets": [
                "Toepassen van patronen met ES6-modules",
                "Vervangen van namespacing-constructies door native modulescoping",
                "Opzetten van een modulaire applicatiestructuur",
                "Werken met closures, prototypes en klassen als basis voor je patronen",
                "Isoleren van afhankelijkheden tussen modules",
                "Herkennen van patronen in oudere codebases en hun moderne variant",
            ]},
            {"titel": "Patronen met TypeScript", "bullets": [
                "Combineren van typering met de klassieke design patterns",
                "Vastleggen van contracten met interfaces",
                "Inzetten van generics voor herbruikbare patronen",
                "Toepassen van decorators om gedrag toe te voegen",
            ]},
            {"titel": "MV-patronen en componentframeworks", "bullets": [
                "Scheiden van model, view en applicatielogica",
                "Toepassen van patronen in React-componenten",
                "Toepassen van patronen in Vue-componenten",
                "Beheren van state en communicatie tussen componenten",
                "Beoordelen wanneer een framework het patroon al voor je regelt",
            ]},
            {"titel": "Praktijkcase: je eigen applicatie", "bullets": [
                "Opzetten van de architectuur van je eigen applicatie",
                "Kiezen en implementeren van passende patronen",
                "Refactoren van code die averechts werkende constructies bevat",
                "Toetsen van je opzet op onderhoudbaarheid en uitbreidbaarheid",
            ]},
        ],
        # De Doelgroep noemde eerst "iedereen die al in JavaScript ontwikkelt" en herhaalde
        # daarmee de Voorkennis eronder. Nu staat hier wat iemand wil bereiken en daar de
        # concrete voorwaarde (schrijfspec Sectie 6/7).
        "doelgroep": (
            "Deze training is bedoeld voor iedereen die zijn JavaScript-code beter wil "
            "structureren met bewezen ontwerpoplossingen die goed onderhoudbaar blijven."
        ),
        "voorkennis": (
            "Ervaring met ontwikkelen in JavaScript is vereist, waaronder werken met functies, "
            "objecten en modules. Mocht je hier vragen over hebben, neem dan gerust contact "
            "met ons op."
        ),
        "aanpak_invulling": (
            "een patroon je code eenvoudiger maakt en waar het onnodige complexiteit toevoegt"
        ),
        "doelen": [
            "De belangrijkste JavaScript design patterns te benoemen en hun structuur te ontleden",
            "Constructies die je code onnodig ingewikkeld maken te herkennen en te herschrijven",
            "Patronen toe te passen in modern JavaScript met ES6-modules",
            "Patronen te implementeren in React- en Vue-componenten",
            "TypeScript in te zetten met interfaces, generics en decorators bij je ontwerpkeuzes",
        ],
        "groepen": [
            {"intro": "Moet je design patterns direct toepassen in JavaScript-applicaties, dan "
                      "sluiten deze trainingen aan:",
             "titels": ["Node.js", "TypeScript", "Secure coding Javascript voor Webapplicaties"]},
            {"intro": "Sta je voor de uitdaging om die patronen in een bredere "
                      "architectuurcontext te doorgronden, dan bieden deze trainingen inzicht:",
             "titels": ["Software Architectuur", "Design Patterns",
                        "Domain Driven Design voor DevOps Teams"]},
        ],
        "kortste_omschrijving": (
            "Wil je design patterns in JavaScript gericht kunnen inzetten? Na deze training "
            "weet je hoe je ze kiest en toepast in ES6-modules, React, Vue en TypeScript."
        ),
    },
]


def bouw(t: dict) -> tuple[dict, list]:
    document = {
        "titel": t["titel"],
        "overzicht": t["overzicht"],
        "inleiding": t["inleiding"],
        "modules": {"opening": sjabloon.modules_opening(t["titel"], t["modules_nb"]),
                    "modules": t["modules"]},
        "doelgroep": t["doelgroep"],
        "voorkennis": t["voorkennis"],
        "aanpak": (sjabloon.AANPAK_ALINEA_1.format(invulling=t["aanpak_invulling"])
                   + "\n\n" + sjabloon.AANPAK_ALINEA_2),
        "doelen": {"intro": sjabloon.DOELEN_INTRO, "bullets": t["doelen"]},
        "vervolgstappen": {
            "alineas": [sjabloon.VERVOLG_ALINEA_1, sjabloon.VERVOLG_ALINEA_2],
            "titels": [x for g in t["groepen"] for x in g["titels"]],
            "groepen": t["groepen"],
            "afsluiter": sjabloon.VERVOLG_AFSLUITER,
        },
        "kortste_omschrijving": t["kortste_omschrijving"],
        "certificatie": sjabloon.CERTIFICATIE,
    }
    content = uit.document_to_content(document, {"days": t["dagen"]})

    # Dezelfde checks als de pipeline, op de writer-vorm van deze training.
    rw = {
        "overzicht": t["overzicht"], "inleiding": t["inleiding"],
        "modules": {"modules": t["modules"]},
        "doelgroep": t["doelgroep"], "voorkennis": t["voorkennis"],
        "aanpak_invulling": t["aanpak_invulling"], "doelen": t["doelen"],
        "kortste_omschrijving": t["kortste_omschrijving"], "nieuwe_titel": t["titel"],
    }
    issues = checks.check_rewrite(rw, {"naam": t["titel"], "dagen": t["dagen"]})
    return content, issues


def vormprofiel(t: dict) -> str:
    """Eén regel per voorbeeld met de maten waarop de few-shot stil kan afdrijven.

    Waarom dit hier staat: de checks bewijzen dat een voorbeeld de regels haalt, maar niet dat
    het de goede kant van de band demonstreert. Een few-shot met zes modules van vijf bullets
    en een tweede zin die met "Je leert …" begint, haalt alles — en de output kopieert precies
    dat. Zo'n afwijking is een keer ongemerkt in de voorbeelden geslopen en pas in de batch
    erna opgevallen. Deze regel maakt hem zichtbaar zonder dat je ernaar hoeft te zoeken.
    """
    n_bullets = [len(m["bullets"]) for m in t["modules"]]
    lo, hi = checks.modulesband(t["dagen"])
    band_ok = "  " if lo <= len(n_bullets) <= hi else "!!"
    zin2 = (checks.zinnen(t["overzicht"])[1:2] or [""])[0]
    # Via de check zelf, niet via de regex erachter: dan blijft dit kloppen als de regel schuift.
    zin2_ok = "!!" if any(i.code == "tweede_zin"
                          for i in checks.check_overzicht({"overzicht": t["overzicht"]})) else "  "
    return (f"  {t['training_id']:22} {t['dagen']} dg | "
            f"{band_ok}{len(n_bullets)} modules (band {lo}-{hi}) | "
            f"{sum(n_bullets):2d} bullets ({','.join(str(n) for n in n_bullets)}) | "
            f"overzicht {checks.word_count(t['overzicht']):3d} w | "
            f"{zin2_ok}2e zin: {zin2[:34]}…")


if __name__ == "__main__":
    os.makedirs(UIT_DIR, exist_ok=True)
    # Sinds `promoveer_naar_goud()` is dit de terugvaloptie en niet meer de gewone weg: ligt
    # er een selectie, dan is die met eigen output gevuld en horen deze vier er niet naast.
    if os.path.exists(os.path.join(UIT_DIR, "selectie.json")):
        print("LET OP: er ligt al een selectie.json in de goudmap. Deze vier voorbeelden zijn\n"
              "de terugvaloptie voor een verse checkout; ze komen naast de gepromoveerde\n"
              "trainingen te staan en gaan niet vanzelf mee in de prompt. Wil je ze wél als\n"
              "few-shot, verwijder dan de gepromoveerde bestanden en selectie.json.\n",
              file=sys.stderr)
    totaal_hard = 0
    for t in TRAININGEN:
        content, issues = bouw(t)
        hard = checks.hard_fails(issues)
        vlag = checks.flags(issues)
        totaal_hard += len(hard)
        print(f"\n=== {t['titel']} ===")
        for i in hard:
            print(f"  HARD  {i.section}: {i.code} — {i.message}")
        for i in vlag:
            print(f"  flag  {i.section}: {i.code} — {i.message[:90]}")
        if not hard and not vlag:
            print("  schoon")
        pad = os.path.join(UIT_DIR, f"{t['training_id']}.json")
        with open(pad, "w", encoding="utf-8") as f:
            json.dump({"training_id": t["training_id"], "titel": t["titel"],
                       "content": content}, f, ensure_ascii=False, indent=2)

    print("\nvormprofiel (ter vergelijking: het oude goud heeft mediaan 6 modules en ~20 "
          "sub-bullets;\nde eerste twee regels hieronder gaan als voorbeeld mee in de prompt):")
    for t in TRAININGEN:
        print(vormprofiel(t))
    print(f"\n{len(TRAININGEN)} bestanden in {UIT_DIR}; {totaal_hard} harde fails totaal")
