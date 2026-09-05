# steuer-de

Ein Claude-Plugin für die **deutsche Einkommensteuererklärung**.

> 🇻🇳 Hướng dẫn tiếng Việt (cài đặt cho cả Claude Code lẫn Claude Desktop): [README.vi.md](README.vi.md)

> Das Repository heißt `steuererklaerung-de`, das Plugin darin `steuer-de` — der kürzere
> Name lässt später Platz für weitere Steuer-Plugins daneben. Zum Hinzufügen zählt der
> Repository-Name, zum Installieren der Plugin-Name; beide Kommandos stehen unten.

[![tests](https://github.com/truongtud/steuererklaerung-de/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/truongtud/steuererklaerung-de/actions/workflows/tests.yml?query=branch%3Amain)
[![Python](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.14-blue)](https://github.com/truongtud/steuererklaerung-de/blob/main/.github/workflows/tests.yml)

## Worum es geht

Vor der Steuererklärung sitzt man mit einem Stapel Papier: Lohnsteuerbescheinigung,
Bescheinigungen der Bank und der Krankenkasse, dazu bei Krypto oder ausländischem Depot
noch Broker-Reports als PDF und CSV. Am Ende müssen daraus ein paar Dutzend Zahlen in
ELSTER-Felder wandern — und dazwischen liegt die eigentliche Arbeit.

**Fang mit `/einstieg` an.** Der Befehl stellt dir ein paar Fragen zu deiner Lage und sagt
dir dann, **welche Papiere du zusammensuchen musst** — die Frage, an der die meisten schon
scheitern, bevor sie angefangen haben.

**Danach ist der Ablauf: alles in einen Ordner legen, `/steuererklaerung` aufrufen,
fertig.** Das Plugin sortiert jedes Dokument selbst ein, liest die Beträge heraus, fragt
nur noch nach dem, was in keinem Papier stand, rechnet — und führt dich am Ende Anlage für
Anlage durch das ELSTER-Formular. Du füllst keine Datei aus und tippst keine Beträge ab.

Gerechnet wird der ganze Weg vom Bruttolohn bis zur Abschlusszahlung: Einkünfte über alle
Anlagen, Vorsorgeaufwendungen mit Höchstbetragsberechnung, außergewöhnliche Belastungen
nach zumutbarer Belastung, Kinderfreibetrag gegen Kindergeld, Tarif nach § 32a mit
Progressionsvorbehalt, Steuerermäßigung nach § 35a, Solidaritätszuschlag, Kirchensteuer und
Abgeltungsteuer. Krypto kommt exakt dazu: FIFO über die gesamte Anschaffungshistorie,
taggenaue Haltefristen, Freigrenzen die **pro Person** und nicht pro Broker gelten.

Und wenn der Bescheid kommt, hält `/bescheid-pruefen` ihn Position für Position gegen die
eigene Rechnung und nennt die Einspruchsfrist.

Was es **nicht** tut: bei ELSTER einreichen und deine Daten irgendwohin schicken. Alles
läuft lokal, die Ausgabe sind Dateien auf deiner Platte.

Veranlagungszeiträume **2022 bis 2026**. Nur deutsches Steuerrecht.

## Was hineingeht

| Format | Woher typischerweise | Verarbeitung |
|---|---|---|
| **PDF-Steuerreport** | Koinly, eToro | Profil erkennt den Anbieter automatisch, liest Tabellen und Summenausweis |
| **PDF, unbekannter Anbieter** | jeder Broker, auch **gescannt** | generische Tabellenerkennung mit OCR (Tesseract), Ergebnis wird zur Sichtprüfung markiert |
| **Exchange-CSV** | Kraken (`ledgers.csv`), Coinbase, Bitpanda, Binance | Profil bildet die Spalten auf das kanonische Transaktionsschema ab |
| **CSV, beliebige Spalten** | jede Börse | freies Spalten-Mapping über eine `mapping.json` |
| **`transactions.json`** | selbst gepflegt oder aus einem der Wege oben | kanonisches Schema, geht direkt in die FIFO-Engine |
| **Lohnsteuerbescheinigung** | Arbeitgeber (PDF) | füllt Anlage N und die Vorsorgeanteile — inklusive des Gesamtbeitrags zur Rentenversicherung aus Nr. 22a + 23a |
| **Steuerbescheinigung** | Bank oder Depot (PDF) | füllt Anlage KAP samt beider Verlusttöpfe und Quellensteuer |
| **Beitragsbescheinigung** | Kranken-/Pflegekasse (PDF) | füllt die Basisabsicherung |
| **ein ganzer Ordner** | alles zusammen | `importiere_unterlagen.py` sortiert jede Datei selbst ein |
| **`steuerdaten.json`** | wird gefüllt, Vorlage liegt bei | von Hand bleiben nur Stammdaten, Werbungskosten, § 35a und Spenden |

Neue Broker sind **eine JSON-Datei**, kein neues Skript — siehe „Wie es funktioniert“.

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

Ein vollständiger Durchlauf mit synthetischen Daten — Eingaben, alle Ausgaben und die
Kommandos zum Selbst-Erzeugen — liegt in [`beispiel/`](beispiel/).

## Wie es funktioniert

```
   unterlagen/            importiere_unterlagen.py
   alle Papiere  ──────▶  erkennt je Datei den Typ · nichts wird geraten
                                       │
        ┌──────────────────┬───────────┴───────┬────────────────────┐
        ▼                  ▼                   ▼                    ▼
  Bescheinigung      Broker / Börse      Steuerbescheid       nicht erkannt
        │                  │                   │                    │
 parse_bescheinigung  parse_broker      /bescheid-pruefen     gemeldet und
 Nummer + Beschrif-   Summenabgleich    (eigener Befehl)      liegen gelassen
 tung müssen passen   gegen den Report
        │                  │
        ▼                  ▼
  steuerdaten.json   *.krypto_result.json · *.kap_result.json
        └─────────┬────────┘
                  ▼
          build_taxreport.py
          Freigrenzen und Verlusttöpfe EINMAL über alle Quellen
          § 32a · § 32b · § 32d · § 35a · § 31 · § 10 Abs. 3/4 · § 33 Abs. 3
                  ▼
           export_report.py            HTML · PDF · ELSTER-Mapping
                  ▼
           Schritt 5: Zeile für Zeile durch das ELSTER-Formular
```

**Schritt 0 — einsortieren.** `importiere_unterlagen.py` nimmt einen ganzen Ordner und
entscheidet je Datei, was sie ist: Bescheinigung, Broker-Report oder Steuerbescheid. Was
keinem Profil eindeutig zuzuordnen ist, bleibt liegen und wird gemeldet — ein falsch
einsortiertes Dokument wäre teurer als ein nicht erkanntes.

**Schritt 1 — einlesen.** Bescheinigungen füllen `steuerdaten.json`; übernommen wird nur,
was eindeutig ist. Broker-Reports gehen durch `parse_broker.py`, das anhand der Profile in
`scripts/profiles/` erkennt, welcher Report vorliegt. Jeder Lauf vergleicht das Geparste
mit den Summen, die der Report **selbst ausweist**, und bricht bei Abweichung ab.

**Schritt 2 — rechnen.** `krypto_fifo.py` rechnet FIFO per Asset über die *gesamte*
Historie, weist aber nur das Steuerjahr aus. Vorberechnete Reports (Koinly & Co.) gehen
diesen Schritt nicht noch einmal — deren FIFO ist bereits wallet-übergreifend gerechnet.

**Schritt 3 — zusammensetzen.** `build_taxreport.py` führt alle Quellen zusammen. Hier —
und nur hier — werden die Freigrenzen angewandt und die Verlusttöpfe verrechnet, weil § 23,
§ 22 Nr. 3 und § 20 Abs. 6 **personenbezogen über alle Broker** gelten. Zwei Reports mit je
800 € sind zusammen 1.600 € und damit voll steuerpflichtig; würde jeder Parser für sich
prüfen, bliebe beides „steuerfrei“.

**Schritt 4 — ausgeben.** `export_report.py` schreibt HTML, PDF und das ELSTER-Mapping.

**Schritt 5 — durch ELSTER führen.** Die CSV auszuliefern reicht nicht: der Skill geht
Anlage für Anlage durch, nennt je Zeile Formularzeile, Bezeichnung und Betrag, und hält an
der Trennzeile zu den Belegen an. Dazu die Angaben, die kein Betrag sind und sonst nirgends
stehen — etwa dass die Günstigerprüfung in der Anlage KAP *angekreuzt* werden muss, sonst
bleibt es bei 25 %.

## So verwendest du es

Die Kommandos oben und unten tippst du **nicht** selbst — das macht Claude. Du hängst deine
Dateien an und sagst, was du willst. Voraussetzung ist nur, dass die Code-Ausführung
aktiviert ist; das Plugin arbeitet dann in Claudes Arbeitsumgebung und gibt die fertigen
Dateien zurück (bzw. legt sie in einen verbundenen Ordner, wenn du einen freigegeben hast).

### Zuerst: `/einstieg`

```
/einstieg 2024
```

Ein paar Fragen — angestellt, verheiratet, Kinder, Depot, Handwerker im Haus — und du
bekommst **die Liste der Papiere**, die du für genau deine Lage brauchst, dazu die
betroffenen Anlagen und die Frist. Zwei, drei Minuten. Danach musst du nur noch sammeln.

Wer seine Unterlagen schon beisammen hat, kann ihn überspringen und direkt
`/steuererklaerung` aufrufen.

### Slash-Befehle

Sechs Befehle stehen nach der Installation im `/`-Menü. Sie sind der direkte Weg, wenn du
weißt, was du willst — sonst reicht es, dein Anliegen normal zu beschreiben, dann meldet
sich der Hauptskill von selbst.

| Befehl | Wofür |
|---|---|
| `/einstieg [jahr]` | **hier anfangen** — die Vorfrage: welche Papiere muss ich zusammensuchen? Danach nur noch in einen Ordner legen |
| `/steuererklaerung [jahr]` | der ganze Durchlauf: **alle Unterlagen hineinwerfen**, einsortieren, extrahieren, rechnen, exportieren — und Zeile für Zeile durch das ELSTER-Formular führen |
| `/krypto-check [frage]` | eine einzelne Frage — Haltefrist, Freigrenze, Tausch, Staking — ohne kompletten Report |
| `/broker-profil [id]` | einen neuen Broker anbinden: Entwurf aus einem echten Report, TODOs auflösen, Fixture anlegen |
| `/steuer-pruefen [datei]` | einen fertigen TaxReport gegenprüfen, bevor die Zahlen nach ELSTER wandern |
| `/bescheid-pruefen [datei]` | den Steuerbescheid gegen den eigenen Report halten, Einspruchsfrist rechnen, Einspruch entwerfen |

Falls ein Name schon belegt ist, funktioniert immer die lange Form, etwa
`/steuer-de:steuererklaerung`. Die Befehle starten nur, wenn **du** sie aufrufst
— Claude startet sie nicht von sich aus.

**Ein Steuerjahr durchrechnen**

> 📎 *lohnsteuerbescheinigung-2024.pdf, steuerbescheinigung-bank.pdf,
> beitragsbescheinigung-kv.pdf, koinly-2024.pdf, etoro-taxreport-2024.pdf*
>
> „Mach mir daraus die Steuererklärung 2024. Ledig, 9 % Kirchensteuer, keine Kinder.
> Aus 2023 habe ich noch 900 € § 23-Verlustvortrag.“

Die Beträge musst du **nicht** diktieren. Claude sortiert die Dokumente ein, liest die
Bescheinigungen aus, gleicht die Broker-Reports gegen ihre eigenen Summenausweise ab und
**fragt nur noch nach dem, was in keinem Papier stand** — Werbungskosten, Handwerker-
rechnungen nach § 35a, Spenden. Am Ende kommen HTML, PDF und die ELSTER-CSV, und Claude
geht mit dir Zeile für Zeile durch das Formular.

Was dabei besonders zählt: aus der Lohnsteuerbescheinigung wird der **Gesamtbeitrag** zur
Rentenversicherung gebildet (Nr. 22a + 23a), nicht nur dein eigener Anteil. Wer das von
Hand einträgt, erwischt regelmäßig die Hälfte — und bekommt null Abzug, ohne dass es im
Ergebnis nach einem Fehler aussieht.

**Eine einzelne Frage klären**

> „Ich habe am 10.01.2023 BTC gekauft und am 10.01.2024 verkauft. Steuerfrei?“

Nein — die Jahresfrist endet mit Ablauf des 10.01.2024, steuerfrei wäre erst der 11.01.
Dafür braucht es keinen ganzen Report; das Plugin kennt die Regel.

**Einen Broker anbinden, für den es noch kein Profil gibt**

> 📎 *ertraegnisaufstellung-2025.pdf*
>
> „Baue mir ein Profil für diesen Broker.“

Claude erzeugt mit dem Wizard einen Entwurf, prüft ihn gegen den Report, löst die
`TODO`-Stellen mit dir auf und legt ein Fixture für die Tests an.

**Das Folgejahr**

> 📎 *koinly-2025.pdf*
>
> „Wie letztes Jahr, aber für 2025 — den Verlustvortrag aus dem alten Report bitte
> übernehmen.“

**Was du zurückbekommst.** Die Dateien aus dem Abschnitt „Was herauskommt“, dazu die
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

## Was du vorher wissen solltest

**Das ist keine Steuerberatung und keine verbindliche Berechnung.** Verbindlich rechnet
ELSTER; die Endkontrolle gehört in die Hände eines Steuerberaters. Darüber hinaus vier
konkrete Punkte, die du kennen solltest, bevor du einer Zahl aus diesem Plugin glaubst:

1. **Die Steuerschätzung ist eine Schätzung — aber sie sagt dir, wie sie danebenliegt.**
   Jeder Report führt eine *Unsicherheitsbilanz*: je verbliebener Lücke die Wirkungsrichtung
   auf die Steuer, eine Größenordnung wo sie sich ableiten lässt, und die Fundstelle. Dazu
   ein Gesamtbild, ob die Zahl eher zu hoch oder zu niedrig liegt. Ohne diese Richtung
   könntest du die Zahl nicht einordnen — die Abweichungen heben sich nicht auf.
2. **Drei der sechs Broker-Profile sind ungeprüft.** Coinbase, Bitpanda und Binance wurden
   gegen die *dokumentierten* Spaltenüberschriften gebaut, nie gegen einen echten Export.
   Sie laufen mit einer deutlichen Warnung; der Binance-Export enthält überhaupt keine
   EUR-Spalte. Geprüft sind Koinly, eToro und Kraken. Wenn du wegen Binance installierst:
   erwarte einen Startpunkt, keine fertige Anbindung.
3. **Bei Kapitalerträgen gilt eine Annahme, die du prüfen musst.** Das Plugin liest
   `kapitalertraege` als den **Saldo**, der die Verluste der Anlage-KAP-Zeilen 22–25 bereits
   enthält — so, wie die Zeilenüberschrift „In den Zeilen 18 und 19 enthaltene …“ es nahelegt.
   Weist deine Bescheinigung stattdessen einen Bruttobetrag aus, musst du vorher saldieren,
   sonst wird zu wenig Steuer ausgewiesen. Der Report stellt diesen Hinweis an den Anfang.
4. **Die Krypto-Berechnung ist nur so gut wie die Eingabedaten.** Fehlt eine Anschaffung in
   der Historie, rechnet die FIFO-Engine mit Kostenbasis 0 und weist einen zu hohen Gewinn
   aus. Sie warnt dabei — die Warnungen sind nicht dekorativ.

Nicht gerechnet: Kinderbetreuungskosten, Entlastungsbetrag für Alleinerziehende,
Ausbildungsfreibetrag, Gewerbesteueranrechnung, geleistete Vorauszahlungen, die Kürzung des
Vorsorge-Höchstbetrags für Beamte (§ 10 Abs. 3 Satz 3) und wallet-bezogenes FIFO. Für
Jahre vor dem laufenden fehlen die Kinderfreibeträge — dort unterbleibt die
Günstigerprüfung nach § 31, statt mit dem Wert eines Nachbarjahres zu rechnen. Eine
automatische ELSTER-Einreichung findet **nicht** statt.

**Deine Daten bleiben, wo sie sind.** Das Plugin liest lokale Dateien und schreibt lokale
Dateien; es lädt nichts hoch und ruft keinen Steuerdienst auf. Die einzige Ausnahme ist
optional und sichtbar: das PDF-Backend `docling` lädt beim ersten Lauf seine Modelle aus dem
Netz. Ohne `docling` braucht das Plugin überhaupt keine Netzverbindung.

## Installation

```
/plugin marketplace add truongtud/steuererklaerung-de
/plugin install steuer-de@steuer-de
```

Danach löst der Skill automatisch aus, sobald es um Steuererklärung, Einkommensteuer,
Krypto-Steuer, ELSTER, Anlage N/KAP/SO/V, Freigrenze, Termingeschäfte oder Verlustvortrag
geht.

Zum Ausprobieren oder Weiterentwickeln geht es auch ohne GitHub — geklont oder entpackt:

```
/plugin marketplace add ./pfad/zu/steuererklaerung-de
/plugin install steuer-de@steuer-de
```

Voraussetzung ist eine Python-3.10-Umgebung mit aktivierter Code-Ausführung. Für den
PDF-Export zusätzlich `fpdf2`, für den PDF-Import `pdfplumber`/`pymupdf`, für gescannte PDFs
Tesseract mit deutschem Sprachpaket — die SKILL.md nennt die genauen Kommandos.

## Was es rechnet

| | |
|---|---|
| **Anlagen** | N, KAP, SO, V, S, G, Vorsorgeaufwand, Sonderausgaben, außergewöhnliche Belastungen, Kind |
| **Krypto § 23** | FIFO per Asset über die volle Historie, **taggenaue** Jahresfrist nach § 108 AO / § 188 BGB, Freigrenze über alle Broker hinweg, Verlustvortrag über Jahre |
| **Krypto § 22 Nr. 3** | Staking und Lending zum Zuflusswert, 256-€-Freigrenze auf die Gesamtsumme |
| **Kapitalerträge** | Abgeltungsteuer inkl. Soli und Kirchensteuer (`(e−4q)/(4+k)`), Aktien-Verlusttopf, Termingeschäfte nach dem JStG 2024, Verlustvorträge, ausländische und fiktive Quellensteuer |
| **Tarif** | § 32a für 2022–2026, Soli mit Milderungszone, Grund- und Splittingtarif, Nachzahlung/Erstattung |
| **Steuerermäßigung § 35a** | Minijob, haushaltsnahe Dienstleistungen und Handwerker — 20 % je Topf mit eigenem Höchstbetrag, gedeckelt auf die Steuer |
| **Progressionsvorbehalt § 32b** | Eltern-, Arbeitslosen-, Kranken- und Kurzarbeitergeld heben den Satz, ohne selbst besteuert zu werden |
| **Günstigerprüfung § 32d Abs. 6** | beide Varianten gerechnet und ausgewiesen; angewandt wird sie nicht, denn sie wirkt nur auf Antrag |
| **Kinder § 31** | Günstigerprüfung Kinderfreibetrag gegen Kindergeld; Soli und Kirchensteuer bemessen sich **immer** mit Freibetrag, auch wenn das Kindergeld gewinnt |
| **Bescheinigungen einlesen** | Lohnsteuerbescheinigung, Steuerbescheinigung der Bank und Beitragsbescheinigung füllen `steuerdaten.json` — statt abzutippen; übernommen wird nur, was eindeutig ist |
| **Bescheidprüfung** | Bescheid gegen den eigenen Report, Position für Position; Einspruchsfrist nach § 122 Abs. 2, § 355 Abs. 1 und § 108 Abs. 3 AO; Einspruchsentwurf |
| **Offene Jahre** | welche Veranlagungszeiträume noch abgegeben werden können (§ 169 Abs. 2 Nr. 2 AO) |
| **Unsicherheitsbilanz** | jeder Report sagt, welche Lücken bleiben, in welche Richtung sie wirken und wie groß sie sind |
| **Zumutbare Belastung § 33 Abs. 3** | stufenweise nach BFH VI R 75/14 — der höhere Satz gilt nur für den übersteigenden Teil |
| **Vorsorge-Höchstbetrag § 10 Abs. 3/4** | Basisversorgung auf den Höchstbeitrag zur knappschaftlichen RV gedeckelt (aus Anlage 2 SGB VI amtlich abgeleitet), Kranken-/Pflegebeiträge bleiben nach Satz 4 auch darüber abziehbar |

## Wie es aufgebaut ist

Das Repository ist ein **Marketplace** mit einem Plugin darin. Das Plugin bündelt sechs
Skills: den Hauptskill mit der ganzen Logik und fünf schlanke Einstiege, die ihn aufrufen.

```
steuererklaerung-de/                        das Repository (= der Marketplace)
├── .claude-plugin/marketplace.json         Katalog: welche Plugins liegen hier
├── .github/workflows/tests.yml             CI auf Python 3.10–3.14 (+ 3.15-Beta)
└── plugins/steuer-de/                      das Plugin
    ├── .claude-plugin/plugin.json          Name, Version, Autor, Lizenz
    └── skills/
        ├── steuererklaerung/               ← der Hauptskill, alles Weitere unten
        ├── einstieg/                       welche Unterlagen brauche ich?
        ├── bescheid-pruefen/               Bescheid gegen den Report, Einspruchsfrist
        ├── krypto-check/                   Einzelfrage ohne kompletten Report
        ├── broker-profil/                  neuen Broker anbinden
        └── steuer-pruefen/                 fertigen Report gegenprüfen
```

Die fünf Einstiege sind bewusst dünn: sie laden den Hauptskill und ergänzen nur ihre eigene
Schrittfolge. Zwei Beschreibungen desselben Ablaufs würden auseinanderlaufen.

Der Hauptskill:

```
skills/steuererklaerung/
├── SKILL.md                    Ablauf und Regeln
├── assets/                     Vorlage für steuerdaten.json
├── references/                 Steuerwerte (Tabelle + steuerwerte.json), Krypto-Recht,
│                               Anlagen-Schema, Broker-Profile, PDF-Ingestion
├── scripts/
│   ├── steuerlib.py            Zahlenlogik; liest die Steuerwerte aus der JSON
│   ├── fetch_steuerwerte.py    holt § 32a EStG / § 3 SolZG (Pflege, nicht Pipeline)
│   ├── pruefe_bescheid.py      Steuerbescheid gegen den Report, Einspruchsfrist
│   ├── neue_steuerdaten.py     Startdatei + Unterlagen-Checkliste (/einstieg)
│   ├── importiere_unterlagen.py alle Unterlagen einsortieren (ein Befehl)
│   ├── parse_bescheinigung.py  Bescheinigungen lesen, Vorlage füllen
│   ├── profiles/bescheinigungen/*.json   ein Belegtyp = eine Profildatei
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
└── tests/                      24 Dateien
```

Zwei Konstruktionsprinzipien, die den Unterschied machen:

**Bei unlesbarer Eingabe wird abgebrochen, nie still 0 angenommen.** In einer
Steuerberechnung ist ein stiller Nullwert die teuerste Fehlerart — nichts danach fällt noch
auf. Deshalb wirft der gemeinsame Zahlenparser einen Fehler, statt zu raten; ein Tausch ohne
EUR-Marktwert bricht ab, statt als Null durchzulaufen; und unbekannte Feldnamen in den
Eingabedaten werden gemeldet („meintest du …?“) statt ignoriert.

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

[576 Fälle in 24 Dateien](plugins/steuer-de/skills/steuererklaerung/tests) — jede Datei ist
einzeln lauffähig, wenn nur ein Bereich interessiert:

| Datei | prüft |
|---|---|
| [`test_steuerlib.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_steuerlib.py) | Zahlenparser (DE/EN, Vorzeichen), Fristen-Grenzfälle inkl. Schaltjahr, Tarif-Stützpunkte und **Zonenstetigkeit** — findet falsch abgeschriebene § 32a-Konstanten |
| [`test_krypto_fifo.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_krypto_fifo.py) | FIFO mit Teillosen und Gebühren, Jahresfilter, Haltefrist, Freigrenzen |
| [`test_build_taxreport.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_build_taxreport.py) | Tarif, Soli, Abgeltungsteuer, Nachzahlung/Erstattung |
| [`test_kap.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_kap.py) | KAP-Quellen, davon-Zeilen, Verlusttöpfe über mehrere Depots |
| [`test_elster_zeilen.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_elster_zeilen.py) | Zeilennummern-Referenz je Jahr deckungsgleich mit `build_taxreport.py`; Zeilenerkennung aus PDF-Text |
| [`test_checkliste_js.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_checkliste_js.py) | führt das JavaScript der ELSTER-Checkliste unter Node gegen eine DOM-Attrappe aus (Gruppierung, Fortschritt, alle drei Kopier-Pfade); ohne Node übersprungen |
| [`test_eingabepruefung.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_eingabepruefung.py) | unbekannte Felder, `--strict`, Verlustvorträge |
| [`test_uebertrage_verlustvortrag.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_uebertrage_verlustvortrag.py) | Verlustvortrag-Übernahme ins Folgejahr, Konfliktabbruch statt stillem Überschreiben |
| [`test_brokerprofile.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_brokerprofile.py) | Profil-Validierung, Erkennung, Summenabgleich |
| [`test_parser.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_parser.py) | Koinly, eToro, CSV-Import, Layout-Varianten |
| [`test_profile_wizard.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_profile_wizard.py) | Entwurfserzeugung, zirkuläre Abgleiche, Anonymisierung |
| [`test_export.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_export.py) | Disclaimer in jedem Format, CSV-Notation, Escaping |
| [`test_integration.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_integration.py) | Pipeline end-to-end, Schnittstellen zwischen den Skripten |
| [`test_stufe1.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_stufe1.py) | § 35a (Deckelung bei null), Progressionsvorbehalt, Günstigerprüfung § 32d Abs. 6 |
| [`test_stufe2.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_stufe2.py) | Vorsorge-Höchstbetrag, zumutbare Belastung, Unsicherheitsbilanz |
| [`test_stufe2b.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_stufe2b.py) | Kinder § 31 — und dass Soli und Kirchensteuer **immer** mit Freibetrag bemessen werden |
| [`test_bescheinigung.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_bescheinigung.py) | Bescheinigungen lesen; Nummer **und** Beschriftung müssen passen |
| [`test_import.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_import.py) | jedes Dokument landet beim richtigen Leser — und nichts wird geraten |
| [`test_einstieg.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_einstieg.py) | die erzeugte Startdatei läuft fehlerfrei durch den Report |
| [`test_bescheid.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_bescheid.py) | Bescheid lesen, Fristenkette, Vergleich mit dem Report |
| [`test_steuerwerte_json.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_steuerwerte_json.py) | Vollständigkeit der `steuerwerte.json` — und dass die Tabellen in `steuerwerte.md` **Zelle für Zelle dasselbe sagen** |
| [`test_fetch_steuerwerte.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_fetch_steuerwerte.py) | Gesetzestext → Zahlen, an echten Seitenausschnitten; ohne Netz |
| [`test_beispiel.py`](plugins/steuer-de/skills/steuererklaerung/tests/test_beispiel.py) | das eingecheckte `beispiel/` gegen das, was der Code heute erzeugt |

CI läuft bei jedem Push auf **Python 3.10 bis 3.14**, dazu ein Vorschau-Lauf auf der
3.15-Beta, der fehlschlagen darf ([Workflow](.github/workflows/tests.yml)).

## Ein neues Steuerjahr ergänzen

Alle jahresabhängigen Werte stehen an genau einer Stelle: `references/steuerwerte.json`,
gespiegelt in `references/steuerwerte.md`. Den Tarif nach § 32a EStG und die Freigrenze
nach § 3 Abs. 3 SolZG holt `scripts/fetch_steuerwerte.py` aus dem Gesetzestext:

```bash
S=plugins/steuer-de/skills/steuererklaerung/scripts
python3 $S/fetch_steuerwerte.py --jahre 2022-2027              # zeigt nur den Unterschied
python3 $S/fetch_steuerwerte.py --jahre 2022-2027 --schreiben  # übernimmt ihn
```

Beides kommt aus **amtlichen Quellen**, zwei unabhängigen Veröffentlichungen des Bundes:

| Quelle | liefert |
|---|---|
| [Tarifhistorie des BMF](https://www.bmf-steuerrechner.de/) (PDF) | § 32a je Tarifzeitraum, zurück bis 1958 — das Jahr steht in der Seitenüberschrift, die Zuordnung ist keine Annahme |
| [gesetze-im-internet.de](https://www.gesetze-im-internet.de/estg/__32a.html) (amtliche XML) | die geltende Fassung von § 32a EStG und § 3 SolZG |

Vor dem Schreiben prüft das Skript jeden Tarif auf Stetigkeit an den Zonengrenzen und
hält die BMF-Historie gegen das EStG; widersprechen sie sich, schreibt es nichts.
Pauschbeträge, die Freigrenze nach § 23 und die Fundstelle im Bundesgesetzblatt bleiben
Handarbeit — das Skript legt sie für ein neues Jahr als `null` an und meldet sie, setzt
aber nie eine 0. Soli-Freigrenzen **früherer** Jahre kann es nicht nachprüfen: eine
amtliche Fassungshistorie des § 3 SolZG gibt es nicht; der Lauf sagt, welche Jahre das
betrifft. Danach `python3 tests/run_tests.py`. Das Skript ist Pflegewerkzeug und **nicht**
Teil der Pipeline — kein Report hängt daran, ob ein Server erreichbar ist.

Fehlt ein Jahr, wird der Report weiterhin gebaut, aber die ESt-Schätzung entfällt und
Pauschbeträge greifen ersatzweise auf das nächstgelegene hinterlegte Jahr zurück, mit
Warnung — statt eine Zahl zu erfinden.

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
