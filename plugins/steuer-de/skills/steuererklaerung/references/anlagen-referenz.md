# Anlagen-Referenz & Eingabeschema (`steuerdaten.json`)

Beschreibt das Eingabeschema von `build_taxreport.py` und die Zuordnung zu den
ELSTER-Anlagen. **Zeilennummern sind Orientierung** — ELSTER ändert die Layouts jährlich;
vor der Eingabe in „Mein ELSTER“ prüfen. **Keine Steuerberatung.**

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
    "kapitalertraege": "3200",             // Zinsen, Dividenden, realisierte Gewinne — SALDO der Z. 7/18/19, s. u.
    "gewinn_aktien": "0",                  // Z. 20 — davon-Zeile, nur zur Größe des Aktien-Verlusttopfs, s. u.
    "gewinn_termingeschaefte": "0",        // Z. 21 — davon-Zeile zu Z. 7, erhöht die Bemessungsgrundlage NICHT
    "verlust_aktien": "0",                 // Z. 23, § 20 Abs. 6 Satz 4 — eigener Topf, s. u.
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
gebraucht bei nicht versteuerten Erträgen (typisch: ausländische Broker),
Verlustverrechnung, Günstigerprüfung oder fehlerhaftem Steuerabzug. Sparer-Pauschbetrag
1.000 € / 2.000 €.

> **Rechtsstand seit dem Jahressteuergesetz 2024:** § 20 Abs. 6 **Sätze 5 und 6** EStG —
> der eigene Verrechnungskreis für Termingeschäfte und der 20.000-€-Deckel — sind
> **aufgehoben, anwendbar in allen offenen Fällen** (Bundesrat 22.11.2024). Verluste aus
> Termingeschäften sind seither mit **sämtlichen** Kapitalerträgen verrechenbar.
> Beschränkt bleibt allein der **Aktien-Verlusttopf** (§ 20 Abs. 6 Satz 4 EStG):
> Verluste aus der Veräußerung von Aktien nur gegen Gewinne aus Aktien.
> Ältere Anleitungen, die zwei getrennte Töpfe führen, sind überholt.

#### Die Zeilen 20–25 sind „davon“-Zeilen — die folgenreichste Annahme des Reports

Im Formular stehen die Zeilen 20 bis 25 unter **einer** gemeinsamen Überschrift:
*„In den Zeilen 18 und 19 enthaltene …“* bzw. *„In Zeile 7 enthaltene …“*. Sie sind damit
Teilmengen der Summenzeilen und dienen allein der Zuordnung zu den Verrechnungskreisen.

`build_taxreport.py` liest daraus eine Konsequenz, die alle sechs Zeilen gleich behandelt:

> **`kapitalertraege` (Z. 7 bzw. Z. 18/19) wird als der SALDO genommen, der die Verluste
> der Zeilen 22–25 BEREITS ENTHÄLT.** Die Verlustzeilen mindern die Bemessungsgrundlage
> deshalb **kein zweites Mal**; sie ordnen nur zu. Ebenso erhöht keine der Gewinnzeilen
> (`gewinn_aktien` Z. 20, `gewinn_termingeschaefte` Z. 21) die Bemessungsgrundlage — die
> Gewinne müssen bereits in `kapitalertraege` stecken.

Die Verlustzeilen hier zusätzlich abzuziehen wäre ein doppelter Abzug: eine Bescheinigung,
die netto ausweist, hat sie längst verrechnet.

**Einzige Ausnahme: der Aktienverlust (Z. 23).** Er darf nach § 20 Abs. 6 Satz 4 EStG nur
gegen Aktienveräußerungsgewinne (Z. 20) laufen. Soweit er sie übersteigt, hat der Saldo
etwas verrechnet, was er nicht durfte — dieser Überhang wird den Kapitalerträgen wieder
**hinzugerechnet** und in den Aktien-Verlustvortrag gestellt (ring-fenced). Für die
Zeilen 22, 24 und 25 gibt es seit dem JStG 2024 keinen eigenen Verrechnungskreis mehr;
was der Saldo dort verrechnet hat, durfte er verrechnen.

Beispiel (`kapitalertraege` 5.000, `gewinn_aktien` 500, `verlust_aktien` 2.000, 2025):

```
verlust_aktien_verrechnet                 500,00   (= min(2.000, 500))
verlust_aktien_ueberhang_hinzugerechnet  1.500,00   ring-fenced
kapitalertraege_nach_aktien_hinzurechnung 6.500,00   (= 5.000 + 1.500)
− Sparer-Pauschbetrag                     1.000,00
bemessungsgrundlage_abgeltungsteuer       5.500,00 → Abgeltungsteuer 1.375,00 €
verlustvortraege.aktien                   1.500,00   (Feststellung beantragen)
```

**Was zu prüfen ist — und nur der Steuerpflichtige kann es:** ob die eigene
Bescheinigung ihre „Höhe der Kapitalerträge“ **netto** (nach Verlustverrechnung) oder
**brutto** ausweist. Deutsche Steuerbescheinigungen sind in aller Regel netto; ein
ausländischer Broker-Export kann brutto sein. Ist er brutto, ist die hier ausgewiesene
Bemessungsgrundlage **zu hoch** — dann sind die Verluste der Zeilen 22–25 von
`kapitalertraege` **abzuziehen, bevor** der Report gebaut wird (im Beispiel also
5.000 − 2.000 = 3.000 einzutragen). Bei nennenswerten Verlusten entscheidet das über
tausende Euro Abgeltungsteuer.

Sobald eine Verlustzeile belegt ist, stellt der Report diese Annahme selbst an den Anfang
seiner `hinweise` — sie steht damit auch im HTML/PDF und im Disclaimer des Exports.
`build_taxreport.py` warnt außerdem, wenn `gewinn_aktien` oder `gewinn_termingeschaefte`
größer sind als `kapitalertraege`: dann fehlt der Betrag in der Summe und bliebe
unversteuert.

**Vorzeichen im ELSTER-Mapping:** Die Verlustzeilen 22–25 werden als **positiver Betrag**
ausgegeben, gleich welches Vorzeichen die Quelle benutzt hat — die Formularzeile heißt
bereits „Verluste …“, und ELSTER erwartet dort eine Zahl ohne Minus. Die wörtliche
Abschrift der Bescheinigung bleibt vorzeichengetreu unter `anlagen.KAP.kap_zeilen` stehen;
weicht beides voneinander ab, sagt der Report das ausdrücklich. Andernfalls stünde im
Mapping die Anweisung, ein Minus in ein Betragsfeld zu tippen — und je nachdem, ob ELSTER
das Zeichen verwirft oder den Verlust umdreht, kostet das den vollen Verlustabzug.

#### Statt Handeingabe: Bescheinigungen einlesen

Steuerbescheinigungen und Erträgnisaufstellungen sind bereits nach Anlage-KAP-Zeilen
aufgebaut; genau dafür gibt es das Ausgabeschema `kap` (siehe
`references/broker-profile.md`). Einlesen kann es die Profil-Engine — **sofern für das
Institut ein Profil existiert.** Mitgeliefert ist bisher nur `etoro-de`; für eine
Bescheinigung der eigenen Bank ist zuerst ein Profil zu schreiben
(`scripts/profile_wizard.py` liefert den Entwurf). `parse_broker.py` rät nicht: passt
kein Profil, bricht es ab.

```bash
python scripts/parse_broker.py --list                 # welche Profile es gibt
python scripts/parse_broker.py depot.pdf              # nur mit passendem Profil
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
`verlustvortrag_23_neu_gesamt` — Letzterer ist der Wert, der im Folgejahr wieder unter
`anlage_so.verlustvortrag_23_vorjahr` eingetragen wird.

### Anlage V — Vermietung und Verpachtung
Einnahmen minus Werbungskosten (AfA, Zinsen, Instandhaltung). Hier nur die Netto-Einkünfte.

### Anlage S / Anlage G — selbständige Arbeit / Gewerbebetrieb
Gewinn aus EÜR oder Bilanz (jeweils Z. 4). Bei Gewerbe zusätzlich die
Gewerbesteueranrechnung (§ 35 EStG) — hier nicht abgebildet.

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

### Steuerermäßigungen — § 35a (Hauptvordruck)
Block `steuerermaessigungen.paragraph_35a` mit drei festen Töpfen, je 20 % der
Aufwendungen mit eigenem Höchstbetrag:

| Feld | § 35a | Höchstbetrag der Ermäßigung |
|---|---|---|
| `minijob_haushalt` | Abs. 1 | 510 € |
| `haushaltsnahe_dienstleistungen` | Abs. 2 | 4.000 € |
| `handwerkerleistungen` | Abs. 3 | 1.200 € |

Einzutragen ist der **begünstigte Rechnungsbetrag**, nicht die Ermäßigung: nur
Arbeits-, Maschinen- und Fahrtkosten, **kein Material**. Viele Rechnungen weisen den
Lohnanteil getrennt aus — genau dieser Betrag gehört hierher. Die Rechnung muss unbar
bezahlt sein; eine Barzahlung erkennt das Finanzamt selbst mit Quittung nicht an.
Häufig übersehen: Schornsteinfeger, Treppenhausreinigung und Hausmeister stehen in der
Nebenkostenabrechnung und zählen zu Abs. 2 bzw. Abs. 3.

Die Töpfe füllen einander nicht auf, und die Ermäßigung kann die Steuer nicht unter
null drücken — ein Überhang verfällt.

### Lohnersatzleistungen — Progressionsvorbehalt (Hauptvordruck)
Block `lohnersatzleistungen`, frei benennbare Positionen (`elterngeld`,
`arbeitslosengeld`, `krankengeld`, `kurzarbeitergeld`, `mutterschaftsgeld` …).
Einzutragen sind die **Bruttobeträge laut Leistungsbescheinigung**. Die Leistungen
bleiben steuerfrei, erhöhen aber nach § 32b EStG den Steuersatz auf das übrige
Einkommen.

## Was der Report NICHT automatisch rechnet
Höchstbetragsberechnung Vorsorgeaufwand, zumutbare Belastung bei agB, Günstigerprüfung
Kinderfreibetrag/Kindergeld, Gewerbesteueranrechnung, Vorauszahlungen. Diese überlässt
der Report bewusst ELSTER bzw. dem Steuerberater.

Die **Günstigerprüfung nach § 32d Abs. 6** (Kapitalerträge zum Tarif) rechnet der
Report und weist beide Varianten aus — angewandt wird sie nicht, denn sie wirkt nur
auf Antrag in der Anlage KAP.
