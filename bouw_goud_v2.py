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
            "Wil je professioneel leren programmeren in PHP en zelfstandig een webapplicatie "
            "kunnen bouwen die je daarna goed kunt onderhouden? Je werkt van functioneel naar "
            "objectgeoriënteerd programmeren en zet een MySQL-database op die je in je "
            "applicatie gebruikt. Je leert veilig te programmeren en bouwt een eigen "
            "webwinkel. Hierdoor lever je na afloop werkende, goed onderhoudbare code op."
        ),
        "inleiding": (
            "PHP draait achter een groot deel van het web, van kleine sites tot complexe "
            "applicaties. In vijf dagen bouw je een stevige basis om er professioneel mee te "
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
            "in PHP en zelfstandig webapplicaties wil kunnen bouwen."
        ),
        "voorkennis": sjabloon.VOORKENNIS_FALLBACK,
        "aanpak_invulling": "je losse PHP-scripts uitbouwt tot een werkende, veilige webapplicatie",
        "doelen": [
            "Zelfstandig PHP-scripts te ontwerpen en te schrijven",
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
            "bouwen? Je werkt van functioneel naar objectgeoriënteerd en levert een werkend "
            "project op."
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
            "beoordelen, zodat je datastructuren betrouwbaar blijven als je systeem groeit? "
            "Je werkt met conceptuele, logische en fysieke modellen, met ER-diagrammen en met "
            "normalisatie tot en met 3NF. Je leert relaties correct vast te leggen en de "
            "vertaling naar een fysieke implementatie te maken. Hierdoor houd je grip op de "
            "kwaliteit van je datastructuren."
        ),
        "inleiding": (
            "Data modeling begint met de vraag welke structuur je nodig hebt voordat je gaat "
            "bouwen. In twee dagen werk je aan drie modelniveaus. Het conceptuele model bakent "
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
            "toetsen modellen aan concrete eisen en testcases. Zo weet je na twee dagen hoe je "
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
                "ER-diagrammen lezen en zelf opstellen",
                "Relatietypen en cardinaliteit bepalen bij 1:1, 1:N en N:N",
                "Associaties uitwerken met joins en foreign keys",
                "Bestaande modellen beoordelen op relaties en sleutelkeuzes",
            ]},
            {"titel": "Normalisatie en modelkwaliteit", "bullets": [
                "Normaliseren tot en met de derde normaalvorm",
                "Redundantie en inconsistenties in datastructuren opsporen",
                "Modellen verifiëren en valideren tegen de gestelde eisen",
                "Integrity constraints bepalen en vastleggen",
            ]},
            {"titel": "Multidimensionaal modelleren met sterschema's (Kimball)", "bullets": [
                "De opbouw van een sterschema volgens Kimball doorgronden",
                "Multidimensionaal en relationeel modelleren vergelijken",
                "Sterschema's plaatsen binnen cloud-datawarehouse- en lakehouse-omgevingen",
                "Data Vault verkennen als aanvulling naast Kimball",
                "Document-datamodellen bekijken als contrast met het relationele model",
            ]},
            {"titel": "Tooling en praktijk", "bullets": [
                "Kennismaken met datamodelleringssoftware",
                "Een eerste model opzetten in de tool",
                "Oefenen met ERD's op basis van concrete eisen",
                "Testcases opstellen en het model daarmee toetsen",
            ]},
        ],
        "doelgroep": (
            "Deze training is bedoeld voor iedereen met technische ervaring in "
            "informatiesystemen en databases die datamodellen wil kunnen lezen, beoordelen en "
            "zelf opzetten."
        ),
        "voorkennis": (
            "Enige ervaring met informatiesystemen en databases en een analytische manier van "
            "werken is vereist; heb je hier vragen over, neem dan gerust contact met ons op."
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
            "Wil je zelf datamodellen kunnen opzetten en beoordelen? In twee dagen werk je met "
            "conceptuele, logische en fysieke modellen, ER-diagrammen en normalisatie tot 3NF."
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
            "bruikbare conclusies leiden? Je leert het CRISP-DM-model toe te passen als vaste "
            "route door elk analysevraagstuk. Je leert datakwaliteit te beoordelen, passende "
            "algoritmen en tools te kiezen en uitkomsten te vertalen naar rapportages. "
            "Hierdoor kun je een analysetraject gestructureerder doorlopen, van businessvraag "
            "tot ingebruikname."
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
            "infrastructuur waarop analyses draaien. Ook introduceren we kort wat generatieve "
            "AI betekent voor het analyseproces. We staan stil bij governance en bij de eisen "
            "die de AVG stelt aan het gebruik van persoonsgegevens. Tot besluit behandelen we "
            "datavisualisatie en rapportage, zodat je bevindingen landen bij wie beslissingen "
            "neemt.\n\n"
            "Onze trainers zijn dagelijks werkzaam op dit expertisegebied. Hierdoor combineren "
            "ze de theorie met voorbeelden uit hun eigen projecten en herken je de afwegingen "
            "die ze bespreken. We laten de training aansluiten bij de onderwerpen en datasets "
            "uit jouw werkpraktijk, zodat je de aanpak direct in je eigen analyses inzet."
        ),
        "modules": [
            {"titel": "Big data en data-analyse in kaart brengen", "bullets": [
                "Doorgronden wat big data betekent voor sturing op strategie en bedrijfsprocessen",
                "Begrippen plaatsen: data mining, machine learning, predictive analytics",
                "Overzicht opbouwen van beschikbare methoden, modellen en algoritmen",
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
            {"titel": "Evaluation en Deployment", "bullets": [
                "Modelresultaten beoordelen op prestatie en op waarde voor de businessvraag",
                "Uitkomsten valideren tegen de vooraf opgestelde succescriteria",
                "Een model in gebruik nemen en het beheer en de monitoring inrichten",
            ]},
            {"titel": "Datavisualisatie, rapportage en best practices", "bullets": [
                "Visualisaties kiezen die passen bij de informatievraag van de ontvanger",
                "Rapportages opzetten die conclusies en onzekerheden zichtbaar maken",
                "Analyseresultaten vertalen naar vervolgstappen voor besluitvorming",
                "Veelvoorkomende valkuilen in analysetrajecten herkennen en vermijden",
                "Best practices toepassen op een eigen praktijkcase",
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
            "bruikbare conclusies leiden? Je past het CRISP-DM-model toe, van databegrip tot "
            "rapportage."
        ),
    },
    # -------------------------------------------------------------------
    {
        "training_id": "v2_jsdesignpatterns",
        "titel": "Training JavaScript Design Patterns",
        "dagen": 3,
        "modules_nb": "stabiel",
        "overzicht": (
            "Wil je design patterns in JavaScript gericht kunnen inzetten, zodat je code "
            "overzichtelijk en goed onderhoudbaar blijft naarmate je applicatie groeit? Je "
            "leert de structuur achter patterns te doorgronden en ze toe te passen in moderne "
            "JavaScript-code. Je werkt met ES6-modules, React- en Vue-componenten en "
            "TypeScript. Hierdoor kun je onderbouwd het best passende pattern kiezen."
        ),
        "inleiding": (
            "In deze training werk je van de structuur van een pattern naar de toepassing "
            "ervan in code. Je start bij de vraag wanneer een oplossing zich genoeg bewezen "
            "heeft om een pattern te heten, en wanneer het nog een veelbelovend idee is. "
            "Oplossingen die juist averechts werken leer je herkennen aan hun symptomen in "
            "bestaande code. Daarna breng je de categorieën in kaart waarin je patterns "
            "indeelt, zodat je sneller het best passende type kunt kiezen.\n\n"
            "We nemen modern JavaScript als uitgangspunt: ES6-modules in plaats van losse "
            "namespacing-constructies. Daarnaast kijk je naar patterns zoals je die in React- "
            "en Vue-componenten terugziet. Ook TypeScript komt aan bod, met interfaces, "
            "generics en decorators als aanvulling op de klassieke patterns. Kom je oudere "
            "codebases tegen, dan koppelen we die aan hun moderne variant. Hierdoor lees je "
            "verouderde code zonder die stijl over te nemen.\n\n"
            "Onze trainers werken zelf dagelijks aan JavaScript-applicaties. Je mag rekenen op "
            "een training waarin we theorie en code afwisselen en waarin we ingaan op "
            "vraagstukken uit jouw eigen codebase. We laten de voorbeelden aansluiten bij het "
            "framework en de architectuur waarmee je werkt. Aan het eind van de training bouw "
            "je een eigen applicatie, waarin je je patternkeuzes onderbouwt en toetst op "
            "onderhoudbaarheid."
        ),
        "modules": [
            {"titel": "Fundamenten van design patterns", "bullets": [
                "Onderscheiden van bewezen patterns, veelbelovende ideeën en de rule of three",
                "Ontleden van de structuur van een pattern",
                "Herkennen van oplossingen die averechts werken in bestaande code",
                "Beschrijven van een eigen pattern volgens een vaste opzet",
            ]},
            {"titel": "Categorieën en patternkeuze", "bullets": [
                "Indelen van patterns in categorieën",
                "Vergelijken van patterns die op elkaar lijken",
                "Kiezen van het best passende pattern op basis van het probleem",
                "Onderbouwen van je keuze richting je team",
            ]},
            {"titel": "Patterns in modern JavaScript", "bullets": [
                "Toepassen van patterns met ES6-modules",
                "Vervangen van namespacing-constructies door native modulescoping",
                "Opzetten van een modulaire applicatiestructuur",
                "Werken met closures, prototypes en klassen als basis voor patterns",
                "Isoleren van afhankelijkheden tussen modules",
                "Herkennen van patterns in oudere codebases en hun moderne variant",
            ]},
            {"titel": "Patterns met TypeScript", "bullets": [
                "Combineren van typering met de klassieke patterns",
                "Vastleggen van contracten met interfaces",
                "Inzetten van generics voor herbruikbare patterns",
                "Toepassen van decorators om gedrag toe te voegen",
            ]},
            {"titel": "MV-patterns en componentframeworks", "bullets": [
                "Scheiden van model, view en applicatielogica",
                "Toepassen van patterns in React-componenten",
                "Toepassen van patterns in Vue-componenten",
                "Beheren van state en communicatie tussen componenten",
                "Beoordelen wanneer een framework het pattern al voor je regelt",
            ]},
            {"titel": "Praktijkcase: je eigen applicatie", "bullets": [
                "Opzetten van de architectuur van je eigen applicatie",
                "Kiezen en implementeren van passende patterns",
                "Refactoren van code die averechts werkende constructies bevat",
                "Toetsen van je opzet op onderhoudbaarheid en uitbreidbaarheid",
            ]},
        ],
        "doelgroep": (
            "Deze training is bedoeld voor iedereen die al in JavaScript ontwikkelt en "
            "applicaties wil kunnen opzetten met herbruikbare ontwerpoplossingen die goed "
            "onderhoudbaar blijven."
        ),
        "voorkennis": (
            "Ervaring met ontwikkelen in JavaScript is vereist, waaronder werken met functies, "
            "objecten en modules; heb je hier vragen over, neem dan gerust contact met ons op."
        ),
        "aanpak_invulling": (
            "een pattern je code eenvoudiger maakt en waar het onnodige complexiteit toevoegt"
        ),
        "doelen": [
            "De belangrijkste JavaScript design patterns te benoemen en hun structuur te ontleden",
            "Constructies die je code onnodig ingewikkeld maken te herkennen en te herschrijven",
            "Patterns toe te passen in modern JavaScript met ES6-modules",
            "Patterns te implementeren in React- en Vue-componenten",
            "TypeScript in te zetten met interfaces, generics en decorators bij je patternkeuzes",
        ],
        "groepen": [
            {"intro": "Moet je design patterns direct toepassen in JavaScript-applicaties, dan "
                      "sluiten deze trainingen aan:",
             "titels": ["Node.js", "TypeScript", "Secure coding Javascript voor Webapplicaties"]},
            {"intro": "Sta je voor de uitdaging om patterns in een bredere "
                      "architectuurcontext te plaatsen, dan bieden deze trainingen inzicht:",
             "titels": ["Software Architectuur", "Design Patterns",
                        "Domain Driven Design voor DevOps Teams"]},
        ],
        "kortste_omschrijving": (
            "Wil je design patterns in JavaScript gericht kunnen inzetten? Je doorgrondt de "
            "structuur achter patterns en past ze toe in ES6-modules, React, Vue en TypeScript."
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


if __name__ == "__main__":
    os.makedirs(UIT_DIR, exist_ok=True)
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
    print(f"\n{len(TRAININGEN)} bestanden in {UIT_DIR}; {totaal_hard} harde fails totaal")
