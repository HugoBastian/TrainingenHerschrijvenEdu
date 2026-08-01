# Beoordelingsspec — judge voor herschreven trainingen

Deze spec stuurt de **judge-LLM**. Hij is **afgeleid uit dezelfde bron als
`schrijfspec_herschrijven_v1.md`** — dat is met opzet: als de judge een eigen interpretatie
van "goed" verzint, gaat hij vechten met de schrijver in plaats van convergeren. De judge
oordeelt tegen de schrijfspec; hij bedenkt geen nieuwe regels.

> Je krijgt: de gekozen persona, de feiten (`bruikbaar` / `strippen` / `gaten`), de
> goedgekeurde en afgewezen actualiseringen, en het **concept** (de tien kopjes, inclusief de vaste
> secties die de code al invoegde). De deterministische code-check (`rewrite_checks.py`) is
> al gedraaid; format-fouten en uit de hand gelopen lengtes zijn er dus in principe uit.
> Jouw taak is het *inhoudelijke* oordeel dat code niet kan geven. Roep tot slot
> `submit_judgment` aan.

> **Oordeel niet over lengte.** De woordaantallen in de schrijfspec zijn richtlijnen met een
> ruime marge (schrijfspec §0.14); de code bewaakt de buitengrens al. Vraag dus nooit om
> inkorten of uitbreiden omdat een kopje net buiten een aantal valt — dat levert precies de
> afgeknepen zinnen op die de marge moet voorkomen. Te lang is alleen een probleem als de
> tekst herhaalt of uitweidt, en dat beoordeel je dan op die grond, niet op het aantal.

> Onder deze spec staan `humanisering_nl.md` en `stijlregister_nl.md` — dezelfde twee
> bestanden die de schrijver kreeg. Waar deze spec ernaar verwijst, kun je ze dus echt
> nalezen in plaats van gokken.

---

## 1. Per-sectie oordeel (pass/fail + reden)

Beoordeel elk generatief kopje tegen de schrijfspec. Geef `pass` of `fail` + één zin waarom.
Let per kopje op de *inhoudelijke* kern (niet de lengte — dat deed de code al):

- **Overzicht (1):** echte "Wil je …"-haak? voordelen i.p.v. losse features? persona-toon? geen marketing? Staat er "kunnen" waar wij handvatten bieden maar de deelnemer het resultaat levert ("je eigen website *kunnen* bouwen")?
- **Inleiding (2):** verdiepend t.o.v. (1), geen herhaling? praktijkgericht? tools ondergeschikt aan wat de deelnemer leert? Landen de USP's in één van de twee registers uit `stijlregister_nl.md` §A ("wat wij bieden" / "wat jij mag verwachten"), met "we" of "je" als onderwerp?
- **Modules (3):** modules niet-overlappend en dekkend voor het aantal dagen/niveau? actief geformuleerd? sub-bullets parallel en samen een sluitend verhaal?
- **Doelgroep (5):** op *bereiken* gericht, geen functietitels/"professionals"?
- **Voorkennis (6):** juiste keuze wel/niet-voorkennis gezien niveau/programma?
- **Doelen (7):** staat elke bullet in de infinitief mét "te", zodat hij doorloopt op "Na deze training ben je in staat om:"? Concreet en realistisch, geen vage "inzicht toepassen"?
  **Let extra op stelligheid.** Die introzin is stellig en maakt overpromising makkelijk. Past de belofte bij het niveau, het aantal dagen en de persona, of had hier een vergrotende trap gemoeten ("gerichter mee te praten" i.p.v. "te beheersen", zie `stijlregister_nl.md` §E)? Bij begripsgerichte en brede overzichtstrainingen is die vergrotende trap de eerlijke vorm; bij persona A en concrete vaardigheden mag de belofte juist direct zijn. Dit is een oordeel dat de code niet kan geven — het is expliciet jouw taak.
- **Kortste omschrijving (9):** dezelfde belofte als (1), ingedikt, echte "Wil je …"-haak? Dezelfde "kunnen"-afweging als bij (1).

De vaste secties (Aanpak 6, Vervolgstappen 8, het bedrijfstrainingblok onder Inleiding en
Certificatie 10) beoordeel je niet op schrijfkwaliteit — die plaatst de code. Check bij
Vervolgstappen alleen dat de titels uit de catalogus komen en relevant/verdiepend zijn. Die
titels staan er bewust zónder "Training" ervoor ("Power BI", niet "Training Power BI") — dat
is geen fout. Een masterclass, workshop of examentraining houdt zijn soortwoord wel.

**Actualiseringen.** Je krijgt de goedgekeurde acties (met eventuele reviewer-voorwaarde) en de
afgewezen acties. Twee harde controles: is elke goedgekeurde actie verwerkt en is de voorwaarde
gerespecteerd, en is geen enkele afgewezen actie alsnog in de tekst terechtgekomen. Een afgewezen
actualisering die toch opduikt is een feitgetrouwheidsfout, geen stijlkwestie.

Een goedgekeurde actie die begint met **"BESLISSING NODIG: …"** leest als een vraag, maar is er
geen: door hem goed te keuren heeft de reviewer de beslissing genomen. Zo'n actie hoort dus
gewoon verwerkt te zijn. Staat hij onder de afgewezen acties, dan hoort de bestaande situatie
juist ongewijzigd te zijn gebleven.

**Soortwoord.** Nergens in de tekst — titel inbegrepen — mag "cursus", "opleiding" of "leergang"
staan; alles heet een training. "Examentraining", "Masterclass" en "Workshop" mogen wel. De
code-check vangt dit al af; kom je het toch tegen, dan is het een fail.

---

## 2. Feitgetrouwheid (grounding)  ⚠️ zwaarste as

Controleer of elke inhoudelijke claim herleidbaar is tot `bruikbaar` of de brontekst.
Onderscheid scherp — dit is waar auto-herschrijven fout gaat:

- **Verzonnen feit (hard fail → human-queue):** een versienummer, vendor, feature, jaartal,
  cijfer of certificering die niet in de bron/feiten staat en niet klopt of niet te
  verifiëren is.
- **Toegestane constructie (pass, wél flaggen als "thin"):** bij een dunne bron mag de
  schrijver plausibele *pedagogische structuur* invullen (modules opsplitsen, doelen
  afleiden). Dat is geen feitfout; markeer de output als "thin / kandidaat tweede ronde".

Rapporteer per twijfelgeval het specifieke citaat en waarom het wel/niet gegrond is.

---

## 3. Persona- en toon-check

- Klopt de toon met de opgegeven persona (A zakelijk/technisch · B toegankelijk/geruststellend · C strategisch/verbindend)?
- "je"-vorm consequent? Geen marketingtaal/superlatieven? Geen LLM-frasen (zie `humanisering_nl.md`, hieronder meegeleverd)?
- **Actief boven passief?** Is "we" of "je" het onderwerp, of staat er een passieve constructie zonder onderwerp ("er wordt aandacht besteed aan …")?
- **Verboden woorden** (`humanisering_nl.md` §D): "professional(s)", "je houdt je bezig met", "meeting". De code-check vangt deze af, maar hij kent alleen letterlijke vormen — een omschrijving die hetzelfde doet ("je bent dagelijks bezig met …") is óók fout.
- Consistente kern door alle kopjes heen (geen tegenstrijdig onderwerp tussen 1, 2 en 3)?

---

## 4. Verdict en routing

Geef een gestructureerd eindoordeel dat de pipeline routeert:

| Verdict | Wanneer | Route |
| --- | --- | --- |
| `approved` | Alle secties pass, feitgetrouw, juiste toon. | Klaar → review-index. |
| `needs-revision` | Eén of meer sectie-fails of toon-issues, **maar geen feitfout**. | Terug naar schrijver mét jouw notities (max N iteraties). |
| `human-queue` | Feitfout/verzonnen claim, structurele actualiteit auto-ingevuld, "thin"-output, of onoplosbare twijfel na max revisies. | Mens beslist. |

**Notities voor de schrijver** (bij `needs-revision`): per te herschrijven kopje één concrete,
atomaire instructie ("Kopje 3: module 2 en 4 overlappen — voeg samen en voeg een module over X
toe"). Kort en op zichzelf staand, zodat de schrijver gericht kan repareren.

---

## 5. Output-schema (tool `submit_judgment`)

```json
{
  "secties": {
    "korte_omschrijving":   { "pass": true, "reden": "" },
    "algemene_omschrijving":{ "pass": true, "reden": "" },
    "programma":            { "pass": true, "reden": "" },
    "doelgroep":            { "pass": true, "reden": "" },
    "voorkennis":           { "pass": true, "reden": "" },
    "doelen":               { "pass": true, "reden": "" },
    "kortste_omschrijving": { "pass": true, "reden": "" }
  },
  "feitgetrouw": { "pass": true, "problemen": [], "thin": false },
  "persona_toon": { "pass": true, "reden": "" },
  "verdict": "approved | needs-revision | human-queue",
  "revisie_notities": ["Kopje X: …"],
  "human_reden": "",
  "judge_confidence": "low | medium | high"
}
```

De code leidt de route deterministisch af uit `verdict` (schrijver rekent niet, judge oordeelt
alleen — zelfde DNA als de scorer).
