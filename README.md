# steuer-de

Ein Claude-Plugin für die **deutsche Einkommensteuererklärung**.

> Das Repository heißt `steuererklaerung-de`, das Plugin darin `steuer-de` — der kürzere
> Name lässt später Platz für weitere Steuer-Plugins daneben. Zum Hinzufügen zählt der
> Repository-Name, zum Installieren der Plugin-Name; beide Kommandos stehen unten.

[![tests](https://github.com/DEIN-GITHUB-USER/steuererklaerung-de/actions/workflows/tests.yml/badge.svg)](https://github.com/DEIN-GITHUB-USER/steuererklaerung-de/actions/workflows/tests.yml)

## Worum es geht

Wer Krypto handelt oder ein Depot bei einem ausländischen Broker hat, sitzt vor der
Steuererklärung mit einem Stapel PDFs und CSVs, aus denen am Ende ein paar Dutzend Zahlen
in ELSTER-Felder wandern müssen. Dazwischen liegt die eigentliche Arbeit: FIFO über die
gesamte Anschaffungshistorie, taggenaue Haltefristen, Freigrenzen, die pro Person und nicht
pro Broker gelten, Verlusttöpfe mit unterschiedlichen Regeln, Verlustvorträge aus Vorjahren.

Genau das macht dieses Plugin. Es liest die Broker-Reports, rechnet Krypto nach
FIFO/§ 23 EStG und die Kapitalerträge nach § 20/§ 32d, setzt daraus einen TaxReport über
alle Anlagen zusammen, schätzt Einkommensteuer, Solidaritätszuschlag, Kirchensteuer und
Abgeltungsteuer samt Nachzahlung oder Erstattung — und gibt am Ende ein **Feld-für-Feld-
Mapping** aus, das man in „Mein ELSTER" abtippt.

Was es **nicht** tut: bei ELSTER einreichen, und deine Daten irgendwohin schicken. Alles
läuft lokal, Ausgabe sind Dateien auf deiner Platte.

Veranlagungszeiträume **2022 bis 2026**. Nur deutsches Steuerrecht.

## Was hineingeht

| Format | Woher typischerweise | Verarbeitung |
|---|---|---|
| **PDF-Steuerreport** | Koinly, eToro | Profil erkennt den Anbieter automatisch, liest Tabellen und Summenausweis |
| **PDF, unbekannter Anbieter** | jeder Broker, auch **gescannt** | generische Tabellenerkennung mit OCR (Tesseract), Ergebnis wird zur Sichtprüfung markiert |
| **Exchange-CSV** | Kraken (`ledgers.csv`), Coinbase, Bitpanda, Binance | Profil bildet die Spalten auf das kanonische Transaktionsschema ab |
| **CSV, beliebige Spalten** | jede Börse | freies Spalten-Mapping über eine `mapping.json` |
| **`transactions.json`** | selbst gepflegt oder aus einem der Wege oben | kanonisches Schema, geht direkt in die FIFO-Engine |
| **`steuerdaten.json`** | von Hand, Vorlage liegt bei | Lohn, Werbungskosten, Vorsorge, Kapitalerträge, Kinder, Verlustvorträge |
| **Lohnsteuerbescheinigung** | Arbeitgeber (PDF) | wird gelesen, die vier Kennziffern werden nach `steuerdaten.json` übertragen |

Neue Broker sind **eine JSON-Datei**, kein neues Skript — siehe „Wie es funktioniert".

## Was herauskommt

| Datei | Wofür |
|---|---|
| `elster_mapping_<jahr>.csv` | **das Arbeitsergebnis**: Anlage, Zeile, Bezeichnung, Wert — von oben nach unten in ELSTER abtippen. Semikolon, Dezimalkomma, BOM, also direkt Excel-tauglich |
| `elster_mapping_<jahr>.json` | dasselbe maschinenlesbar, mit Quellenangabe je Zeile und dem vollständigen Protokoll |
| `taxreport_<jahr>.html` | Dashboard zum Draufschauen: Kennzahlen, Einkünfte je Anlage, alle Krypto-Veräußerungen, Hinweise. Self-contained, mit Druck-Stylesheet |
| `taxreport_<jahr>.pdf` | druckfertige Fassung für die Ablage oder den Steuerberater |
| `taxreport.json` | der vollständige Report als Struktur — Grundlage für alles andere, gut für eigene Auswertungen |

Zwischenergebnisse, die man ansehen oder korrigieren kann: `<name>.krypto_result.json`,
`<name>.kap_result.json`, `<name>.transactions.json`, sowie `<name>.extracted.json` und
`<name>.tables.csv` beim generischen PDF-Weg.

In der ELSTER-CSV steht **pro Formularzeile genau eine einzutragende Zahl**. Darunter
trennt eine Zeile die Belege je Quelle ab, die ausdrücklich *nicht* eingetragen werden —
sonst tippt man denselben Betrag zweimal.

## Wie es funktioniert

```
   Broker-PDF ─┐
   Exchange-CSV ├─▶ parse_broker.py ─┐
   (Profil)     │   Summenabgleich   │
                │                    ├─▶ build_taxreport.py ─▶ export_report.py
   fremdes PDF ─┴─▶ parse_pdf.py ────┤   Freigrenzen einmal     HTML · PDF · ELSTER
   CSV ────────────▶ parse_inputs.py │   Verlusttöpfe
                                     │   Tarif § 32a
   steuerdaten.json ─────────────────┘   Nachzahlung
```

**Schritt 1 — einlesen.** `parse_broker.py` erkennt anhand der Profile in
`scripts/profiles/`, welcher Report vorliegt, und wendet das passende an. Jeder Lauf
vergleicht das Geparste mit den Summen, die der Report **selbst ausweist**, und bricht bei
Abweichung ab.

**Schritt 2 — rechnen.** `krypto_fifo.py` rechnet FIFO per Asset über die *gesamte*
Historie, weist aber nur das Steuerjahr aus. Vorberechnete Reports (Koinly & Co.) gehen
diesen Schritt nicht noch einmal — deren FIFO ist bereits wallet-übergreifend gerechnet.

**Schritt 3 — zusammensetzen.** `build_taxreport.py` führt alle Quellen zusammen. Hier —
und nur hier — werden die Freigrenzen angewandt und die Verlusttöpfe verrechnet, weil § 23,
§ 22 Nr. 3 und § 20 Abs. 6 **personenbezogen über alle Broker** gelten. Zwei Reports mit je
800 € sind zusammen 1.600 € und damit voll steuerpflichtig; würde jeder Parser für sich
prüfen, bliebe beides „steuerfrei".

**Schritt 4 — ausgeben.** `export_report.py` schreibt HTML, PDF und das ELSTER-Mapping.

## So benutzt du es

Die Kommandos oben und unten tippst du **nicht** selbst — das macht Claude. Du hängst deine
Dateien an und sagst, was du willst. Voraussetzung ist nur, dass die Code-Ausführung
aktiviert ist; das Plugin arbeitet dann in Claudes Arbeitsumgebung und gibt die fertigen
Dateien zurück (bzw. legt sie in einen verbundenen Ordner, wenn du einen freigegeben hast).

### Slash-Befehle

Vier Befehle stehen nach der Installation im `/`-Menü. Sie sind der direkte Weg, wenn du
weißt, was du willst — sonst reicht es, dein Anliegen normal zu beschreiben, dann meldet
sich das Skill von selbst.

| Befehl | Wofür |
|---|---|
| `/steuererklaerung [jahr]` | der ganze Durchlauf: Reports einlesen, fehlende Angaben erfragen, rechnen, HTML + PDF + ELSTER-CSV |
| `/krypto-check [frage]` | eine einzelne Frage — Haltefrist, Freigrenze, Tausch, Staking — ohne kompletten Report |
| `/broker-profil [id]` | einen neuen Broker anbinden: Entwurf aus einem echten Report, TODOs auflösen, Fixture anlegen |
| `/steuer-pruefen [datei]` | einen fertigen TaxReport gegenprüfen, bevor die Zahlen nach ELSTER wandern |

Falls ein Name schon belegt ist, funktioniert immer die lange Form, etwa
`/steuer-de:steuererklaerung`. Die Befehle starten nur, wenn **du** sie aufrufst
— Claude löst sie nicht von sich aus aus.

**Ein Steuerjahr durchrechnen**

> 📎 *koinly-2024.pdf, etoro-taxreport-2024.pdf*
>
> „Mach mir daraus die Steuererklärung 2024. Ledig, 9 % Kirchensteuer, keine Kinder.
> Bruttoarbeitslohn 78.500 €, Lohnsteuer 18.420 €, Kirchensteuer 1.658 €. Aus 2023 habe ich
> noch 900 € § 23-Verlustvortrag."

Claude liest beide Reports, zeigt dir den Summenabgleich gegen die im Report ausgewiesenen
Beträge, **fragt nach dem, was noch fehlt** — Vorsorgeaufwendungen, Werbungskosten,
anrechenbare Kapitalertragsteuer — und liefert am Ende HTML, PDF und die ELSTER-CSV. Du
musst keine `steuerdaten.json` schreiben; Claude füllt die Vorlage aus dem Gespräch.

**Eine einzelne Frage klären**

> „Ich habe am 10.01.2023 BTC gekauft und am 10.01.2024 verkauft. Steuerfrei?"

Nein — die Jahresfrist endet mit Ablauf des 10.01.2024, steuerfrei wäre erst der 11.01.
Dafür braucht es keinen ganzen Report; das Plugin kennt die Regel.

**Einen Broker anbinden, für den es noch kein Profil gibt**

> 📎 *ertraegnisaufstellung-2025.pdf*
>
> „Baue mir ein Profil für diesen Broker."

Claude erzeugt mit dem Wizard einen Entwurf, prüft ihn gegen den Report, löst die
`TODO`-Stellen mit dir auf und legt ein Fixture für die Tests an.

**Das Folgejahr**

> 📎 *koinly-2025.pdf*
>
> „Wie letztes Jahr, aber für 2025 — den Verlustvortrag aus dem alten Report bitte
> übernehmen."

**Was du zurückbekommst.** Die Dateien aus dem Abschnitt „Was herauskommt", dazu die
Kernzahlen im Klartext und — wichtiger — die **Warnungen**: knapp verfehlte Freigrenzen,
fehlende Anschaffungshistorie, Haltefrist-Konflikte, ungeprüfte Profile. Wenn ein
Summenabgleich scheitert, bricht der Lauf ab und Claude sagt es dir, statt eine plausible
Zahl zu liefern. Diese Meldungen sind der eigentliche Wert des Plugins — nicht wegklicken.

**Wo deine Daten liegen.** Die angehängten Dateien landen in Claudes Arbeitsumgebung und
werden dort verarbeitet; das Plugin selbst schickt nichts an Dritte und reicht nichts bei
ELSTER ein. Wenn dir das für echte Steuerunterlagen zu weit geht, ist der lokale Weg
derselbe: Repository klonen, `python3 scripts/…` selbst ausführen — die Kommandos in diesem
README sind vollständig.

### Einen neuen Broker anbinden

Ein **Profil** beschreibt deklarativ, wie ein Report gelesen wird — Erkennungsmuster,
Tabellen, Spaltenzuordnung und der Abgleich gegen die ausgewiesene Summe:

```bash
python3 scripts/parse_broker.py --list                        # vorhandene Profile
python3 scripts/profile_wizard.py neu.pdf --id mein-broker    # Entwurf aus einem echten Report
```

Die Engine **lehnt** ein Profil ab, das keine Erkennung, keine Pflichtfelder oder keinen
funktionierenden Summenabgleich hat — und ebenso eines, in dem noch ein `TODO` steht. Der
Wizard erzeugt bewusst nur einen Entwurf voller `TODO`s: er darf raten, er soll aber nicht
so tun, als sei die Anbindung fertig. Details: `references/broker-profile.md`.

---

## Bevor du es benutzt — bitte einmal lesen

**Das ist keine Steuerberatung und keine verbindliche Berechnung.** Verbindlich rechnet
ELSTER; die Endkontrolle gehört zu einem Steuerberater. Darüber hinaus vier konkrete
Punkte, die du kennen solltest, bevor du einer Zahl aus diesem Plugin glaubst:

1. **Die Steuerschätzung fällt systematisch zu niedrig aus.** Vorsorgeaufwendungen werden
   in voller Höhe abgezogen — die Höchstbetragsberechnung nach § 10 Abs. 3/4 EStG ist
   *nicht* umgesetzt. Tatsächlich ist weniger abziehbar, das zvE also höher und die echte
   Steuer höher als hier geschätzt. Das ist die größte verbliebene Vereinfachung.
2. **Drei der sechs Broker-Profile sind ungeprüft.** Coinbase, Bitpanda und Binance wurden
   gegen die *dokumentierten* Spaltenüberschriften gebaut, nie gegen einen echten Export.
   Sie laufen mit einer deutlichen Warnung; der Binance-Export enthält überhaupt keine
   EUR-Spalte. Geprüft sind Koinly, eToro und Kraken. Wenn du wegen Binance installierst:
   erwarte einen Startpunkt, keine fertige Anbindung.
3. **Bei Kapitalerträgen gilt eine Annahme, die du prüfen musst.** Das Plugin liest
   `kapitalertraege` als den **Saldo**, der die Verluste der Anlage-KAP-Zeilen 22–25 bereits
   enthält — so, wie die Zeilenüberschrift „In den Zeilen 18 und 19 enthaltene …" es nahelegt.
   Weist deine Bescheinigung stattdessen einen Bruttobetrag aus, musst du vorher saldieren,
   sonst wird zu wenig Steuer ausgewiesen. Der Report stellt diesen Hinweis an den Anfang.
4. **Die Krypto-Berechnung ist nur so gut wie die Eingabedaten.** Fehlt eine Anschaffung in
   der Historie, rechnet die FIFO-Engine mit Kostenbasis 0 und weist einen zu hohen Gewinn
   aus. Sie warnt dabei — die Warnungen sind nicht dekorativ.

Ebenfalls nicht gerechnet: zumutbare Belastung bei außergewöhnlichen Belastungen,
Günstigerprüfung (KAP/Kind), Progressionsvorbehalt, Gewerbesteueranrechnung,
Vorauszahlungen, wallet-bezogenes FIFO. Eine automatische ELSTER-Einreichung findet
**nicht** statt.

**Deine Daten bleiben, wo sie sind.** Das Plugin liest lokale Dateien und schreibt lokale
Dateien; es lädt nichts hoch und ruft keinen Steuerdienst auf. Die einzige Ausnahme ist
optional und sichtbar: das PDF-Backend `docling` lädt beim ersten Lauf seine Modelle aus dem
Netz. Ohne `docling` braucht das Plugin überhaupt keine Netzverbindung.

## Installation

```
/plugin marketplace add DEIN-GITHUB-USER/steuererklaerung-de
/plugin install steuer-de@steuer-de
```

Danach löst das Skill automatisch aus, sobald es um Steuererklärung, Einkommensteuer,
Krypto-Steuer, ELSTER, Anlage N/KAP/SO/V, Freigrenze, Termingeschäfte oder Verlustvortrag
geht.

Voraussetzung ist eine Python-3.10-Umgebung mit aktivierter Code-Ausführung. Für den
PDF-Export zusätzlich `fpdf2`, für PDF-Import `pdfplumber`/`pymupdf`, für gescannte PDFs
Tesseract mit deutschem Sprachpaket — die SKILL.md nennt die genauen Kommandos.

## Was es rechnet

| | |
|---|---|
| **Anlagen** | N, KAP, SO, V, S, G, Vorsorgeaufwand, Sonderausgaben, außergewöhnliche Belastungen, Kind |
| **Krypto § 23** | FIFO per Asset über die volle Historie, **taggenaue** Jahresfrist nach § 108 AO / § 188 BGB, Freigrenze über alle Broker hinweg, Verlustvortrag über Jahre |
| **Krypto § 22 Nr. 3** | Staking und Lending zum Zuflusswert, 256-€-Freigrenze auf die Gesamtsumme |
| **Kapitalerträge** | Abgeltungsteuer inkl. Soli und Kirchensteuer (`(e−4q)/(4+k)`), Aktien-Verlusttopf, Termingeschäfte nach dem JStG 2024, Verlustvorträge, ausländische und fiktive Quellensteuer |
| **Tarif** | § 32a für 2022–2026, Soli mit Milderungszone, Grund- und Splittingtarif, Nachzahlung/Erstattung |

## Wie es aufgebaut ist

```
skills/steuererklaerung/
├── SKILL.md                    Ablauf und Regeln
├── assets/                     Vorlage für steuerdaten.json
├── references/                 Steuerwerte, Krypto-Recht, Anlagen-Schema,
│                               Broker-Profile, PDF-Ingestion
├── scripts/
│   ├── steuerlib.py            einzige Quelle für Zahlenlogik und Steuerwerte
│   ├── brokerprofile.py        Profil-Engine: Erkennung, Anwendung, Summenabgleich
│   ├── profiles/*.json         ein Broker = eine Profildatei
│   ├── parse_broker.py         ein Einstiegspunkt für alle Broker
│   ├── profile_wizard.py       Profil-Entwurf aus einem echten Report
│   ├── parse_koinly.py         Kurzbefehle auf dieselbe Engine
│   ├── parse_etoro.py
│   ├── parse_pdf.py            generischer PDF-/OCR-Import
│   ├── parse_inputs.py         CSV → kanonisches Transaktionsschema
│   ├── krypto_fifo.py          FIFO-Engine § 23 / § 22 Nr. 3
│   ├── build_taxreport.py      Anlagen, Tarif, Verlusttöpfe, ELSTER-Mapping
│   └── export_report.py        HTML / PDF / ELSTER
└── tests/                      368 Fälle, 10 Dateien
```

Zwei Konstruktionsprinzipien, die den Unterschied machen:

**Bei unlesbarer Eingabe wird abgebrochen, nie still 0 angenommen.** In einer
Steuerberechnung ist ein stiller Nullwert die teuerste Fehlerart — nichts danach fällt noch
auf. Deshalb wirft der gemeinsame Zahlenparser, statt zu raten; ein Tausch ohne
EUR-Marktwert bricht ab, statt als Null durchzulaufen; und unbekannte Feldnamen in den
Eingabedaten werden gemeldet („meintest du …?") statt ignoriert.

**Jeder profilgesteuerte Import wird gegen die Summe geprüft, die der Report selbst
ausweist.** Weicht sie ab — oder findet das Summenmuster gar nichts — bricht der Lauf ab
und schreibt keine Datei. Ein Parser, der die Hälfte einer Tabelle verliert, fällt dadurch
sofort auf statt erst im Steuerbescheid. Für den generischen PDF- und CSV-Weg gilt das
nicht: dort gibt es keine Summe zum Vergleichen, nur `confidence` und `_needs_review`.

## Tests

```bash
cd plugins/steuer-de/skills/steuererklaerung
python3 tests/run_tests.py
```

368 Fälle in 10 Dateien: Tarif-Stützpunkte und Zonenstetigkeit (findet falsch
abgeschriebene Konstanten), Zahlenparser in deutscher und englischer Notation,
Fristen-Grenzfälle inklusive Schaltjahr, FIFO mit Teillosen und Gebühren, Freigrenzen- und
Verlusttopf-Aggregation über mehrere Quellen, KAP-Quellen, Profil-Validierung, den Wizard
samt Anonymisierung, ELSTER-Export und die Schnittstellen zwischen den Skripten. CI läuft
auf Python 3.10–3.12.

## Ein neues Steuerjahr ergänzen

Alle jahresabhängigen Werte stehen an genau einer Stelle: `scripts/steuerlib.py`, gespiegelt
in `references/steuerwerte.md`. Neue Werte aus § 32a EStG und § 3 Abs. 3 SolZG eintragen,
dann `python3 tests/run_tests.py` — der Stetigkeitstest über die Tarifzonen prüft sie
automatisch mit. Fehlt ein Jahr, wird der Report weiterhin gebaut, aber die ESt-Schätzung
entfällt und Pauschbeträge greifen ersatzweise auf das nächstgelegene hinterlegte Jahr
zurück, mit Warnung — statt eine Zahl zu erfinden.

## Rechtsstand

Geprüft am **30.08.2026** für die Veranlagungszeiträume 2022–2026. Enthalten sind unter
anderem der rückwirkend erhöhte Grundfreibetrag 2024 (11.784 €), die Aufhebung von
§ 20 Abs. 6 Sätze 5 und 6 EStG durch das Jahressteuergesetz 2024 (Termingeschäfte, alle
offenen Fälle) und die Behandlung der Anlage-KAP-Zeilen 20–25 als davon-Zeilen.
Steuerrecht ändert sich laufend — vor jeder Einreichung selbst verifizieren.

Nur deutsches Steuerrecht. Für andere Länder ist dieses Plugin nicht gedacht und die
Ergebnisse wären falsch.

## Beiträge

Fehlerberichte und Korrekturen sind willkommen, besonders zu Rechtsständen und
Broker-Report-Layouts. Ein echter Export, der ein `ungeprueft`-Profil bestätigt oder
widerlegt, ist der wertvollste Beitrag überhaupt — bitte anonymisiert, der Wizard hilft
dabei. Für Codeänderungen einen Testfall mitliefern; bei diesem Thema ist ein Fix ohne Test
schwer zu bewerten. Keine echten Steuerdaten in Issues, Fixtures oder Tests.

## Lizenz

MIT — siehe [LICENSE](LICENSE). Ohne jede Gewährleistung; die Haftungsfreistellung im
Lizenztext ist hier der Teil, der zählt.
