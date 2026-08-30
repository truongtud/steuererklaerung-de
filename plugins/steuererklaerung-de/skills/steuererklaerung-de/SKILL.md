---
name: steuererklaerung-de
description: Erstellt einen TaxReport für die deutsche Einkommensteuererklärung über alle Anlagen (N, KAP, SO, V, S, G, Vorsorge, Sonderausgaben, agB, Kind), rechnet Krypto exakt nach FIFO/§ 23 EStG (taggenaue Haltefrist, Freigrenze, Staking § 22 Nr. 3), liest Broker-Reports als PDF ein (Koinly, eToro, generisch mit OCR) und exportiert HTML, PDF und ELSTER-Feld-Mapping. Use whenever the user mentions Steuererklärung, Einkommensteuer, Krypto-Steuer, crypto tax, Anlage N/KAP/SO/V, Veräußerungsgeschäfte, Staking-Steuer, ELSTER, Lohnsteuerbescheinigung, Freigrenze, FIFO, Verlustvortrag, or wants a tax report from broker PDFs, exchange CSVs or income data in Germany. Nicht für Steuerrecht anderer Länder.
license: MIT — NUR Orientierung, KEINE Steuerberatung.
---

# Steuererklärung Deutschland — TaxReport-Generator

Erstellt aus Einkommens- und Krypto-Daten einen strukturierten **TaxReport** über alle
Anlagen, rechnet Krypto nach FIFO/§ 23 EStG, schätzt Einkommensteuer (§ 32a),
Solidaritätszuschlag, Kirchensteuer und Abgeltungsteuer, rechnet einbehaltene Steuern an
und exportiert **HTML**, **PDF** und ein **ELSTER-Feld-Mapping**.

> **Keine Steuerberatung.** Das Skill erzeugt eine Arbeitsgrundlage. Die verbindliche
> Berechnung liefert ELSTER, die Endkontrolle gehört zum Steuerberater. Diesen Hinweis im
> Ergebnis **immer** mitgeben.

## Pipeline

```
PDF-Reports ─▶ 0) einlesen ──────┐
(Broker/Exchange)                │
  parse_koinly · parse_etoro     ├▶ 1) normalisieren ▶ 2) FIFO ▶ 3) Report bauen ▶ 4) exportieren
  parse_pdf (generisch)          │    parse_inputs    krypto_fifo  build_taxreport   export_report
CSV/JSON ────────────────────────┘                                                   HTML · PDF · ELSTER

scripts/steuerlib.py  ── ein Zahlenparser, eine Fristenlogik, alle Steuerwerte
```

**`scripts/steuerlib.py` ist die einzige Stelle mit Steuerkonstanten und Zahlenlogik.**
Wer einen Wert ändert, ändert ihn dort (und in `references/steuerwerte.md`). Grundregel des
Codes: **bei unlesbarer Eingabe wird abgebrochen, nie still 0 angenommen.**

Setup (einmalig):
```bash
pip install fpdf2 pdfplumber pymupdf pytesseract pdf2image --break-system-packages
# gescannte PDFs zusätzlich (System):
#   apt-get install -y tesseract-ocr tesseract-ocr-deu poppler-utils
pip install docling --break-system-packages   # optional, beste Tabellenqualität
python3 tests/run_tests.py                    # Selbsttest: muss grün sein
```

### Schritt 0 — Broker-Reports einlesen

**Vorberechnete Steuerreports** (Koinly, CoinTracking, Blockpit …) haben FIFO bereits
wallet-übergreifend gerechnet. Diese **nicht** erneut durch `krypto_fifo.py` schicken — die
Kaufhistorie fehlt, die Kostenbasis würde 0.

```bash
python scripts/parse_koinly.py koinly_report.pdf -o koinly.krypto_result.json
python scripts/parse_etoro.py  taxReport.pdf     -o etoro.krypto_result.json
```

Beide Parser gleichen ihr Ergebnis gegen die **im Report selbst ausgewiesenen Summen** ab
und brechen bei Abweichung ab. Diese Meldung ist der wichtigste Schutz gegen stille
Zeilenverluste — nicht überspringen, nicht wegkonfigurieren.

Beide liefern **Roh-Nettobeträge ohne Freigrenze** (`freigrenze_angewendet: false`), weil
die Freigrenzen pro Person und Jahr über alle Broker gelten. `build_taxreport.py` nimmt
deshalb **mehrere** `--krypto-result`-Dateien und wendet die Freigrenze einmal auf die
Summe an.

**Generische Broker-PDFs** (eigene Transaktionslisten):
```bash
python scripts/parse_pdf.py report.pdf --outdir arbeit --backend auto --ocr-lang deu+eng
```
Wählt automatisch das beste Backend (Docling → pdfplumber → PyMuPDF), erkennt gescannte
Seiten und schaltet auf Tesseract-OCR um. Schreibt `<name>.extracted.json`,
`<name>.tables.csv` und `<name>.transactions.json` mit `confidence` und `_needs_review`.

**Verifikation (nicht überspringen):** alle `_needs_review`-Zeilen gegen das PDF prüfen;
EUR-Marktwerte für `reward` und `swap` ergänzen; auf **vollständige Anschaffungshistorie**
achten; die vom Skript gemeldeten übersprungenen Tabellen ansehen. Details:
`references/pdf-ingestion.md`.

### Schritt 1 — Übrige Eingaben sammeln

- **Einkommensdaten** in `steuerdaten.json` nach `references/anlagen-referenz.md`
  eintragen; Vorlage: `assets/steuerdaten_vorlage.json`. Liegt eine
  **Lohnsteuerbescheinigung** als PDF vor: Nr. 3 → `bruttoarbeitslohn`, Nr. 4 →
  `lohnsteuer`, Nr. 5 → `soli`, Nr. 6 → `kirchensteuer`.
- Unbekannte Feldnamen werden gemeldet („meintest du …?") und **ignoriert** — die Warnung
  ernst nehmen, ein Tippfehler ist sonst stillschweigend 0 € wert. `--strict` macht daraus
  einen Abbruch.
- **Krypto aus CSV**: `python scripts/parse_inputs.py datei.csv --format kraken -o transactions.json`
- **Krypto-zu-Krypto-Tausch ist eine Veräußerung** — als `swap` mit `eur_value` erfassen.
- **Verlustvorträge aus Vorjahren** eintragen (`anlage_so.verlustvortrag_23_vorjahr`,
  `anlage_kap.verlustvortrag_aktien_vorjahr`), sonst verfallen sie faktisch.

Vor dem Rechnen kurz abgleichen: Steuerjahr, Veranlagung, Kirchensteuer, Vollständigkeit
der Anschaffungshistorie, offene Verlustfeststellungen.

### Schritt 2 — Krypto FIFO (nur bei Roh-Transaktionen)

```bash
python scripts/krypto_fifo.py transactions.json <steuerjahr> krypto_result.json
```

Rechnet per-Asset-FIFO über die **gesamte** Historie, weist aber nur das Steuerjahr aus.
Prüft die Jahresfrist taggenau nach § 108 AO / § 188 BGB (der Jahrestag selbst ist noch
steuerpflichtig — „365 Tage" ist falsch), die Freigrenze und Staking nach § 22 Nr. 3.
Details und Edge-Cases: `references/krypto-steuer.md`. `build_taxreport.py` ruft die Engine
sonst selbst auf.

### Schritt 3 — TaxReport bauen

```bash
python scripts/build_taxreport.py steuerdaten.json --transactions transactions.json -o taxreport.json
# mehrere Broker-Ergebnisse:
python scripts/build_taxreport.py steuerdaten.json \
    --krypto-result koinly.krypto_result.json etoro.krypto_result.json -o taxreport.json
```

Setzt die Anlagen zusammen, wendet die Freigrenzen **einmal auf die Summe** an, verrechnet
Verlustvorträge, schätzt zvE und ESt (§ 32a, Grund-/Splittingtarif), Soli (mit
Milderungszone) und Kirchensteuer, rechnet die Abgeltungsteuer inkl.
Verlusttöpfen, ermittelt **Nachzahlung oder Erstattung** und erzeugt das ELSTER-Mapping.
Fehlt für ein Jahr der Tarif, wird die Schätzung übersprungen und darauf hingewiesen —
dann die Werte nach `references/steuerwerte.md` in `steuerlib.py` ergänzen.

### Schritt 4 — Exportieren

```bash
python scripts/export_report.py taxreport.json --outdir out --formats html pdf elster
```

- `taxreport_<jahr>.html` — Dashboard, self-contained, mit Druck-Stylesheet
- `taxreport_<jahr>.pdf` — druckfertiger Report
- `elster_mapping_<jahr>.csv` / `.json` — Feld für Feld zur manuellen Eingabe; die CSV nutzt
  Semikolon und Dezimalkomma (deutsches Excel) und trägt Disclaimer und Hinweise als
  Kommentarzeilen

Nur die gewünschten Formate wählen, die Dateien anschließend an den Nutzer ausliefern
(SendUserFile) und die Kernzahlen nennen.

## Plausibilitätsschritt — vor dem Kommunizieren von Zahlen

1. Die vom Report gemeldeten `warnungen` lesen und weitergeben, nicht filtern.
2. Report-Summen gegen die im Broker-PDF ausgewiesenen Summen abgleichen und die
   Abweichung nennen — auch wenn sie null ist.
3. Bei mehreren Quellen prüfen, ob wirklich alle Konten erfasst sind (§ 23 und § 22 Nr. 3
   gelten personenbezogen über alle Broker).

## Ergebnis kommunizieren — immer mit dabei

1. Kernzahlen in Klartext: zvE, ESt-Schätzung, **Nachzahlung/Erstattung**, Krypto § 23
   steuerpflichtig, steuerfrei (> 1 Jahr), Staking § 22 Nr. 3, Verlustvorträge.
2. Auffälligkeiten: Freigrenze knapp über-/unterschritten, fehlende Anschaffungshistorie,
   ignorierte unbekannte Felder, Steuerjahr ohne Tarif, Bestände nahe der Jahresfrist.
3. Der **Disclaimer**: keine Steuerberatung, ELSTER ist maßgeblich, Endkontrolle durch
   Steuerberater. Nicht weglassen — er steht auch in jedem Export.

## Reference-Dateien (bei Bedarf lesen)

- `references/steuerwerte.md` — Tarif, Freigrenzen, Pauschbeträge je Jahr; wie ein neues
  Jahr ergänzt wird. Bei jedem neuen Steuerjahr zuerst hierher.
- `references/krypto-steuer.md` — § 23 / § 22 Nr. 3, FIFO, taggenaue Haltefrist,
  Freigrenze über alle Broker, Verlustvortrag, kanonisches Schema, Edge-Cases.
- `references/anlagen-referenz.md` — `steuerdaten.json`-Schema, ELSTER-Zuordnung je Anlage,
  Rechtsstand § 20 Abs. 6 nach dem JStG 2024.
- `references/pdf-ingestion.md` — Backends, Spalten-Erkennung, Summenabgleich,
  Verifikations-Checkliste, Troubleshooting.

## Tests

`python3 tests/run_tests.py` prüft Tarif-Stützpunkte und Zonenstetigkeit, Zahlenparser
(DE/EN, Vorzeichen), Fristen-Grenzfälle, FIFO mit Teillosen und Gebühren, Freigrenzen-
aggregation über mehrere Quellen, ELSTER-Export und die Schnittstellen zwischen den
Skripten. **Nach jeder Änderung laufen lassen** — die Steuerwerte-Tests fangen falsch
abgeschriebene Konstanten, die Kontrakt-Tests fangen auseinanderlaufende Schlüsselnamen.

## Grenzen (bewusst nicht automatisiert)

Höchstbetragsberechnung Vorsorgeaufwand (**größte Vereinfachung** — die Schätzung fällt
dadurch zu niedrig aus), zumutbare Belastung bei agB, Günstigerprüfung (KAP/Kind),
Progressionsvorbehalt, Gewerbesteueranrechnung, Vorauszahlungen, wallet-bezogenes FIFO.
Diese überlässt das Skill ELSTER bzw. dem Steuerberater. Eine automatische
ELSTER-Einreichung erfolgt **nicht** — ausgegeben wird ein Feld-Mapping zur manuellen
Eingabe.
