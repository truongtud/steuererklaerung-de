# Anlagen-Referenz & Eingabeschema (`steuerdaten.json`)

Beschreibt das Eingabeschema von `build_taxreport.py` und die Zuordnung zu den
ELSTER-Anlagen. **Zeilennummern sind Orientierung** — ELSTER ändert die Layouts jährlich;
vor der Eingabe in „Mein ELSTER" prüfen. **Keine Steuerberatung.**

## Eingabeschema `steuerdaten.json`

```json
{
  "steuerjahr": 2025,
  "zusammenveranlagung": false,
  "steuerpflichtiger": {
    "name": "Vorname Nachname",
    "verheiratet": false,
    "kirchensteuersatz": "0.09",          // 0.08 (BW/BY) oder 0.09; 9 wird als 9 % gelesen
    "steuer_id": "00 000 000 000"
  },
  "anlage_n": {
    "bruttoarbeitslohn": "85000",          // Lohnsteuerbescheinigung Nr. 3
    "lohnsteuer": "21500",                 // Nr. 4
    "soli": "0",                           // Nr. 5
    "kirchensteuer": "1935",               // Nr. 6
    "werbungskosten": {                    // frei benennbare Positionen
      "arbeitsmittel": "800",
      "fahrtkosten": "1500",
      "fortbildung": "600",
      "homeoffice": "1260"
    }
  },
  "anlage_kap": {
    "kapitalertraege": "3200",             // Zinsen, Dividenden, realisierte Gewinne — BRUTTO (Z. 7/18/19)
    "gewinn_aktien": "0",                  // nur zur Größe des Aktien-Verlusttopfs, s. u. (Z. 20)
    "gewinn_termingeschaefte": "0",        // Z. 21 — erhöht die Bemessungsgrundlage
    "verlust_aktien": "0",                 // Z. 23, § 20 Abs. 6 Satz 4 — eigener Topf
    "verlust_termingeschaefte": "0",       // Z. 24, seit JStG 2024 voll verrechenbar, s. u.
    "verluste_ohne_aktien": "0",           // Z. 22, allgemeine Verluste
    "verluste_ausfall": "0",               // Z. 25, Ausfall/Ausbuchung
    "verlustvortrag_aktien_vorjahr": "0",  // festgestellter Aktien-Verlustvortrag
    "verlustvortrag_allgemein_vorjahr": "0",       // allgemeiner Verlustvortrag
    "verlustvortrag_termingeschaefte_vorjahr": "0", // fließt in denselben Topf, s. u.
    "anrechenbare_kest": "550",            // einbehaltene Kapitalertragsteuer (Z. 37)
    "einbehaltener_soli": "0",             // Z. 38
    "einbehaltene_kirchensteuer": "0",     // Z. 39
    "auslaendische_quellensteuer": "0",    // Z. 41, § 32d Abs. 5 EStG
    "fiktive_quellensteuer": "0"           // Z. 42, fiktive Quellensteuer nach DBA
  },
  "anlage_so": {
    "sonstige_einkuenfte": "0",            // § 22 Nr. 3 außerhalb Krypto (zählt in die 256 €)
    "verlustvortrag_23_vorjahr": "0"       // festgestellter § 23-Verlustvortrag, s. u.
  },
  "anlage_v": { "einkuenfte": "4200" },    // Überschuss Vermietung/Verpachtung
  "anlage_s": { "gewinn": "0" },           // selbständige Arbeit
  "anlage_g": { "gewinn": "0" },           // Gewerbebetrieb
  "vorsorge": {                            // Anlage Vorsorgeaufwand, frei benennbar
    "rentenversicherung": "7905",
    "krankenversicherung": "4100",
    "pflegeversicherung": "900",
    "arbeitslosenversicherung": "780"
  },
  "sonderausgaben": { "spenden": "300" },  // frei benennbar
  "aussergewoehnliche_belastungen": { "anzusetzen": "0" },  // nach zumutbarer Belastung
  "kinder": [
    { "name": "Kind 1", "geburtsdatum": "2015-06-01", "kindergeld": "3000" }
  ],
  "krypto_transaktionen": [ /* kanonisches Schema, siehe krypto-steuer.md */ ]
}
```

Beträge als **String** mit Dezimalpunkt oder -komma (beides wird gelesen), damit keine
Float-Rundungsfehler entstehen. Nicht zutreffende Felder können weggelassen werden.
Vorlage: `assets/steuerdaten_vorlage.json`.

### Tippfehler werden gemeldet, nicht ignoriert

`build_taxreport.py` kennt die zulässigen Feldnamen je Block und warnt bei allem anderen:

```
WARNUNG: Unbekanntes Feld 'anlage_n.brutto_arbeitslohn' — meintest du
         'bruttoarbeitslohn'? Der Wert wurde IGNORIERT.
```

Die Warnung steht auch im Report (`warnungen`) und damit im HTML/PDF. `--strict` macht
daraus einen Abbruch (Rückgabecode 3, Report wird trotzdem geschrieben). Frei benennbar
und daher ungeprüft bleiben nur `anlage_n.werbungskosten`, `vorsorge` und `sonderausgaben`.

## Anlagen-Übersicht und ELSTER-Zuordnung

### Hauptvordruck (ESt 1 A)
Stammdaten, Veranlagungsart, Steuer-ID, Bankverbindung. In ELSTER zuerst ausfüllen.

### Anlage N — nichtselbständige Arbeit
Bruttoarbeitslohn (Z. 6), Lohnsteuer (Z. 7), Soli (Z. 8), Kirchensteuer (Z. 9);
Werbungskosten ab Z. 31 ff. Der **Arbeitnehmer-Pauschbetrag** (1.230 €, 2022: 1.200 €)
wird automatisch angesetzt, wenn die Einzelpositionen darunter liegen — höhere tatsächliche
Kosten nur mit Nachweis. Der Pauschbetrag kann den Arbeitslohn nicht übersteigen
(§ 9a Satz 2 EStG); ein echter Werbungskosten-Überhang darf dagegen negative Einkünfte
erzeugen und mindert die übrigen Einkünfte.

### Anlage KAP — Kapitalerträge
Meist über die **Abgeltungsteuer 25 %** an der Quelle erledigt; die Anlage KAP wird
gebraucht bei nicht versteuerten Erträgen (typisch: ausländische Broker), Verlust-
verrechnung, Günstigerprüfung oder fehlerhaftem Steuerabzug. Sparer-Pauschbetrag
1.000 € / 2.000 €.

> **Rechtsstand seit dem Jahressteuergesetz 2024:** § 20 Abs. 6 **Sätze 5 und 6** EStG —
> der eigene Verrechnungskreis für Termingeschäfte und der 20.000-€-Deckel — sind
> **aufgehoben, anwendbar in allen offenen Fällen** (Bundesrat 22.11.2024). Verluste aus
> Termingeschäften sind seither mit **sämtlichen** Kapitalerträgen verrechenbar.
> Beschränkt bleibt allein der **Aktien-Verlusttopf** (§ 20 Abs. 6 Satz 4 EStG):
> Verluste aus der Veräußerung von Aktien nur gegen Gewinne aus Aktien.
> Ältere Anleitungen, die zwei getrennte Töpfe führen, sind überholt.

> **Falle `gewinn_aktien`:** Dieses Feld dient **nur** dazu, den Aktien-Verlusttopf zu
> bemessen. Es erhöht die Bemessungsgrundlage **nicht** — die realisierten Aktiengewinne
> müssen bereits in `kapitalertraege` enthalten sein. `build_taxreport.py` warnt, wenn
> `gewinn_aktien` größer ist als `kapitalertraege`.
>
> **Zeilen 20–25 sind „davon"-Zeilen.** Im Formular stehen sie unter der Überschrift
> *„In den Zeilen 18 und 19 enthaltene …"* (bzw. *„In Zeile 7 enthaltene …"*). Sie sind
> also bereits in den Summen enthalten und dienen nur der Zuordnung zu den
> Verrechnungskreisen. Deshalb erhöht **keines** der Felder `gewinn_aktien`,
> `gewinn_termingeschaefte`, `verluste_ohne_aktien`, `verluste_ausfall` die
> Bemessungsgrundlage — die Beträge müssen in `kapitalertraege` enthalten sein.
> Übersteigt eine davon-Zeile die Kapitalerträge, warnt der Report: dann fehlt der Betrag
> in der Summe und bliebe unversteuert.

Statt die Werte hier von Hand einzutragen, lassen sich Steuerbescheinigungen und
Erträgnisaufstellungen auch direkt einlesen — siehe `references/broker-profile.md`:

```bash
python scripts/parse_broker.py ertraegnisaufstellung.pdf -o depot.kap_result.json
python scripts/build_taxreport.py steuerdaten.json --kap-result depot.kap_result.json …
```

Beides lässt sich mischen: Datei-Quellen und Handeingaben werden **addiert**, und wenn
eine Kennzahl in beiden belegt ist, weist der Report das getrennt aus
(`anlagen.KAP.quellen`), damit eine Doppelerfassung auffällt.

### Anlage SO — sonstige Einkünfte  ← **Krypto**
- **Private Veräußerungsgeschäfte § 23** (Krypto ≤ 1 Jahr): Z. 41–47.
- **Leistungen § 22 Nr. 3** (Staking, Lending): Z. 10–13. Die 256-€-Freigrenze gilt für
  die **Summe** aus Krypto-Erträgen und `sonstige_einkuenfte`.
- **Verlustvortrag/-feststellung § 23**: Z. 54–59.

**Verlustvortrag § 23 über Jahre** (§ 23 Abs. 3 Satz 8 EStG): Ein festgestellter Verlust
verrechnet sich nur mit § 23-Gewinnen. Reihenfolge im Report — erst die **Freigrenze auf
das Ergebnis des Jahres selbst**, dann der Vortrag; ein Jahr unterhalb der Freigrenze ist
ohnehin steuerfrei und verbraucht deshalb nichts. Der Report weist aus:
`verlustvortrag_23_verbraucht`, `verlustvortrag_23_rest` und
`verlustvortrag_23_neu_gesamt` — letzterer ist der Wert, der im Folgejahr wieder unter
`anlage_so.verlustvortrag_23_vorjahr` eingetragen wird.

### Anlage V — Vermietung und Verpachtung
Einnahmen minus Werbungskosten (AfA, Zinsen, Instandhaltung). Hier nur die Netto-Einkünfte.

### Anlage S / Anlage G — selbständige Arbeit / Gewerbebetrieb
Gewinn aus EÜR oder Bilanz (jeweils Z. 4). Bei Gewerbe zusätzlich die Gewerbesteuer-
anrechnung (§ 35 EStG) — hier nicht abgebildet.

### Anlage Vorsorgeaufwand
Renten-, Kranken-, Pflege-, Arbeitslosenversicherung. Erfasst werden die **gezahlten**
Beträge; die Höchstbetragsberechnung nach § 10 Abs. 3/4 EStG macht ELSTER. Der Report
zieht sie in voller Höhe ab und schätzt die Steuer dadurch **zu niedrig** — das ist die
größte Vereinfachung und steht so auch im Disclaimer.

### Anlage Sonderausgaben / Außergewöhnliche Belastungen
Spenden, Kirchensteuer, Ausbildungskosten; Krankheitskosten u. a. nach zumutbarer
Belastung (die der Report nicht rechnet — hier den bereits gekürzten Betrag eintragen).

### Anlage Kind
Je Kind Name, Geburtsdatum, Kindergeld. Die Günstigerprüfung Kinderfreibetrag vs.
Kindergeld macht ELSTER automatisch.

## Was der Report NICHT automatisch rechnet
Höchstbetragsberechnung Vorsorgeaufwand, zumutbare Belastung bei agB, Günstigerprüfung
(KAP/Kind), Progressionsvorbehalt, Gewerbesteueranrechnung, Vorauszahlungen. Diese
überlässt der Report bewusst ELSTER bzw. dem Steuerberater.
