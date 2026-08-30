# steuererklaerung-de

Ein Claude-Plugin für die **deutsche Einkommensteuererklärung**: es baut aus Einkommens-
und Krypto-Daten einen strukturierten TaxReport über alle Anlagen, rechnet Krypto nach
FIFO/§ 23 EStG, schätzt Einkommensteuer, Soli, Kirchensteuer und Abgeltungsteuer und
exportiert HTML, PDF und ein **ELSTER-Feld-Mapping** zum manuellen Abtippen.

[![tests](https://github.com/DEIN-GITHUB-USER/steuererklaerung-de/actions/workflows/tests.yml/badge.svg)](https://github.com/DEIN-GITHUB-USER/steuererklaerung-de/actions/workflows/tests.yml)

---

## Bevor du es benutzt — bitte einmal lesen

**Das ist keine Steuerberatung und keine verbindliche Berechnung.** Verbindlich rechnet
ELSTER; die Endkontrolle gehört zu einem Steuerberater. Darüber hinaus zwei konkrete
Punkte, die du kennen solltest, bevor du einer Zahl aus diesem Plugin glaubst:

1. **Die Steuerschätzung fällt systematisch zu niedrig aus.** Vorsorgeaufwendungen werden
   in voller Höhe abgezogen — die Höchstbetragsberechnung nach § 10 Abs. 3/4 EStG ist
   *nicht* umgesetzt. Tatsächlich ist weniger abziehbar, das zvE also höher und die echte
   Steuer höher als hier geschätzt. Das ist die größte verbliebene Vereinfachung.
2. **Die Krypto-Berechnung ist nur so gut wie die Eingabedaten.** Fehlt eine
   Anschaffung in der Historie, rechnet die FIFO-Engine mit Kostenbasis 0 und weist einen
   zu hohen Gewinn aus. Sie warnt dabei — die Warnungen sind nicht dekorativ.

Ebenfalls nicht gerechnet: zumutbare Belastung bei außergewöhnlichen Belastungen,
Günstigerprüfung (KAP/Kind), Progressionsvorbehalt, Gewerbesteueranrechnung,
Vorauszahlungen, wallet-bezogenes FIFO. Eine automatische ELSTER-Einreichung findet
**nicht** statt — ausgegeben wird ein Feld-Mapping zur manuellen Eingabe.

## Installation

```
/plugin marketplace add DEIN-GITHUB-USER/steuererklaerung-de
/plugin install steuererklaerung-de@steuererklaerung-de
```

Danach löst das Skill automatisch aus, sobald es um Steuererklärung, Einkommensteuer,
Krypto-Steuer, ELSTER, Anlage N/KAP/SO/V, Freigrenze oder Verlustvortrag geht.

Voraussetzung ist eine Python-3.10-Umgebung mit aktivierter Code-Ausführung. Für den
PDF-Export zusätzlich `fpdf2`, für PDF-Import `pdfplumber`/`pymupdf`, für gescannte PDFs
Tesseract mit deutschem Sprachpaket — die SKILL.md nennt die genauen Kommandos.

## Was es kann

| | |
|---|---|
| **Anlagen** | N, KAP, SO, V, S, G, Vorsorgeaufwand, Sonderausgaben, außergewöhnliche Belastungen, Kind |
| **Krypto § 23** | FIFO per Asset über die volle Historie, **taggenaue** Jahresfrist nach § 108 AO / § 188 BGB, Freigrenze über alle Broker hinweg, Verlustvortrag über Jahre |
| **Krypto § 22 Nr. 3** | Staking und Lending zum Zuflusswert, 256-€-Freigrenze auf die Gesamtsumme |
| **Kapitalerträge** | Abgeltungsteuer inkl. Soli und Kirchensteuer (`(e−4q)/(4+k)`), Aktien-Verlusttopf, Termingeschäfte nach dem JStG 2024 |
| **Tarif** | § 32a für 2022–2026, Soli mit Milderungszone, Grund- und Splittingtarif |
| **Import** | Koinly- und eToro-Steuerreports als PDF, generische Broker-PDFs mit OCR, Exchange-CSV (Kraken, freies Spalten-Mapping) |
| **Export** | HTML-Dashboard (mit Druck-Stylesheet), PDF, ELSTER-CSV/JSON |

## Wie es aufgebaut ist

```
skills/steuererklaerung-de/
├── SKILL.md                    Ablauf und Regeln
├── references/                 Steuerwerte, Krypto-Recht, Anlagen-Schema, PDF-Ingestion
├── scripts/
│   ├── steuerlib.py            einzige Quelle für Zahlenlogik und Steuerwerte
│   ├── parse_koinly.py         Presets für vorberechnete Steuerreports
│   ├── parse_etoro.py
│   ├── parse_pdf.py            generischer PDF-/OCR-Import
│   ├── parse_inputs.py         CSV → kanonisches Transaktionsschema
│   ├── krypto_fifo.py          FIFO-Engine § 23 / § 22 Nr. 3
│   ├── build_taxreport.py      Anlagen, Tarif, ELSTER-Mapping
│   └── export_report.py        HTML / PDF / ELSTER
└── tests/                      186 Fälle, 7 Dateien
```

Zwei Konstruktionsprinzipien, die den Unterschied machen:

**Bei unlesbarer Eingabe wird abgebrochen, nie still 0 angenommen.** In einer
Steuerberechnung ist ein stiller Nullwert die teuerste Fehlerart — nichts danach fällt
noch auf. Deshalb wirft der gemeinsame Zahlenparser, statt zu raten, und unbekannte
Feldnamen in den Eingabedaten werden gemeldet („meintest du …?") statt ignoriert.

**Jede geparste Summe wird gegen die Summe geprüft, die der Report selbst ausweist.**
Ein Parser, der die Hälfte einer Tabelle verliert, fällt dadurch sofort auf statt erst im
Steuerbescheid.

## Tests

```bash
cd plugins/steuererklaerung-de/skills/steuererklaerung-de
python3 tests/run_tests.py
```

186 Fälle: Tarif-Stützpunkte und Zonenstetigkeit (findet falsch abgeschriebene
Konstanten), Zahlenparser in deutscher und englischer Notation, Fristen-Grenzfälle
inklusive Schaltjahr, FIFO mit Teillosen und Gebühren, Freigrenzen über mehrere Quellen,
ELSTER-Export und die Schnittstellen zwischen den Skripten. CI läuft auf Python 3.10–3.12.

## Ein neues Steuerjahr ergänzen

Alle jahresabhängigen Werte stehen an genau einer Stelle: `scripts/steuerlib.py`, gespiegelt
in `references/steuerwerte.md`. Neue Werte aus § 32a EStG und § 3 Abs. 3 SolZG eintragen,
dann `python3 tests/run_tests.py` — der Stetigkeitstest über die Tarifzonen prüft sie
automatisch mit. Fehlt ein Jahr, überspringt das Plugin die Schätzung und sagt das, statt
eine Zahl zu erfinden.

## Rechtsstand

Geprüft am **30.08.2026** für die Veranlagungszeiträume 2022–2026. Enthalten sind unter
anderem der rückwirkend erhöhte Grundfreibetrag 2024 (11.784 €) und die Aufhebung von
§ 20 Abs. 6 Sätze 5 und 6 EStG durch das Jahressteuergesetz 2024 (Termingeschäfte, alle
offenen Fälle). Steuerrecht ändert sich laufend — vor jeder Einreichung selbst
verifizieren.

Nur deutsches Steuerrecht. Für andere Länder ist dieses Plugin nicht gedacht und die
Ergebnisse wären falsch.

## Beiträge

Fehlerberichte und Korrekturen sind willkommen, besonders zu Rechtsständen und
Broker-Report-Layouts. Bitte einen Testfall mitliefern — bei diesem Thema ist ein Fix ohne
Test schwer zu bewerten. Keine echten Steuerdaten in Issues oder Tests.

## Lizenz

MIT — siehe [LICENSE](LICENSE). Ohne jede Gewährleistung; die Haftungsfreistellung im
Lizenztext ist hier der Teil, der zählt.
