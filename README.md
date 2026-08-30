# steuererklaerung-de

Ein Claude-Plugin für die **deutsche Einkommensteuererklärung**: es baut aus Einkommens-,
Krypto- und Kapitalertragsdaten einen strukturierten TaxReport über alle Anlagen, rechnet
Krypto nach FIFO/§ 23 EStG, schätzt Einkommensteuer, Soli, Kirchensteuer und
Abgeltungsteuer und exportiert HTML, PDF und ein **ELSTER-Feld-Mapping** zum manuellen
Abtippen.

Veranlagungszeiträume **2022 bis 2026**. Nur deutsches Steuerrecht.

[![tests](https://github.com/DEIN-GITHUB-USER/steuererklaerung-de/actions/workflows/tests.yml/badge.svg)](https://github.com/DEIN-GITHUB-USER/steuererklaerung-de/actions/workflows/tests.yml)

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
**nicht** statt — ausgegeben wird ein Feld-Mapping zur manuellen Eingabe.

**Deine Daten bleiben, wo sie sind.** Das Plugin liest lokale Dateien und schreibt lokale
Dateien; es lädt nichts hoch und ruft keinen Steuerdienst auf. Die einzige Ausnahme ist
optional und sichtbar: das PDF-Backend `docling` lädt beim ersten Lauf seine Modelle aus dem
Netz. Ohne `docling` braucht das Plugin überhaupt keine Netzverbindung.

## Installation

```
/plugin marketplace add DEIN-GITHUB-USER/steuererklaerung-de
/plugin install steuererklaerung-de@steuererklaerung-de
```

Danach löst das Skill automatisch aus, sobald es um Steuererklärung, Einkommensteuer,
Krypto-Steuer, ELSTER, Anlage N/KAP/SO/V, Freigrenze, Termingeschäfte oder Verlustvortrag
geht.

Voraussetzung ist eine Python-3.10-Umgebung mit aktivierter Code-Ausführung. Für den
PDF-Export zusätzlich `fpdf2`, für PDF-Import `pdfplumber`/`pymupdf`, für gescannte PDFs
Tesseract mit deutschem Sprachpaket — die SKILL.md nennt die genauen Kommandos.

## Was es kann

| | |
|---|---|
| **Anlagen** | N, KAP, SO, V, S, G, Vorsorgeaufwand, Sonderausgaben, außergewöhnliche Belastungen, Kind |
| **Krypto § 23** | FIFO per Asset über die volle Historie, **taggenaue** Jahresfrist nach § 108 AO / § 188 BGB, Freigrenze über alle Broker hinweg, Verlustvortrag über Jahre |
| **Krypto § 22 Nr. 3** | Staking und Lending zum Zuflusswert, 256-€-Freigrenze auf die Gesamtsumme |
| **Kapitalerträge** | Abgeltungsteuer inkl. Soli und Kirchensteuer (`(e−4q)/(4+k)`), Aktien-Verlusttopf, Termingeschäfte nach dem JStG 2024, Verlustvorträge, ausländische und fiktive Quellensteuer |
| **Tarif** | § 32a für 2022–2026, Soli mit Milderungszone, Grund- und Splittingtarif, Nachzahlung/Erstattung |
| **Import** | Broker- und Börsen-Reports über **Profildateien**: Koinly, eToro (PDF), Kraken, Coinbase, Bitpanda, Binance (CSV) — dazu generische Broker-PDFs mit OCR und freies Spalten-Mapping für alles andere |
| **Export** | HTML-Dashboard (mit Druck-Stylesheet), PDF, ELSTER-CSV/JSON |

## Ein neuer Broker ist eine JSON-Datei

Das ist der Kern des Plugins. Statt für jeden Anbieter ein Skript zu schreiben, beschreibt
ein **Profil** deklarativ, wie ein Report gelesen wird — Erkennungsmuster, Tabellen,
Spaltenzuordnung und, entscheidend, der Abgleich gegen die Summe, die der Report selbst
ausweist.

```bash
python3 scripts/parse_broker.py --list          # vorhandene Profile
python3 scripts/parse_broker.py report.pdf      # Profil automatisch erkennen
python3 scripts/profile_wizard.py neu.pdf --id mein-broker   # Entwurf aus einem echten Report
```

Die Engine **lehnt** ein Profil ab, das keine Erkennung, keine Pflichtfelder oder keinen
funktionierenden Summenabgleich hat — und ebenso eines, in dem noch ein `TODO` steht. Der
Wizard erzeugt bewusst nur einen Entwurf voller `TODO`s: er kann raten, er soll aber nicht
so tun, als sei die Anbindung fertig. Details: `references/broker-profile.md`.

## Wie es aufgebaut ist

```
skills/steuererklaerung-de/
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
cd plugins/steuererklaerung-de/skills/steuererklaerung-de
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
