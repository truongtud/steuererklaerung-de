# Steuererklärung Deutschland

Claude-Plugin für die deutsche Einkommensteuererklärung. Es baut aus Einkommens- und
Krypto-Daten einen TaxReport über alle Anlagen (N, KAP, SO, V, S, G, Vorsorgeaufwand,
Sonderausgaben, außergewöhnliche Belastungen, Kind), rechnet Krypto nach FIFO/§ 23 EStG,
schätzt Einkommensteuer, Solidaritätszuschlag, Kirchensteuer und Abgeltungsteuer und
exportiert HTML, PDF sowie ein ELSTER-Feld-Mapping zur manuellen Eingabe.

## Wichtig

**Keine Steuerberatung, keine verbindliche Berechnung.** Verbindlich rechnet ELSTER; die
Endkontrolle gehört zu einem Steuerberater.

**Die Schätzung fällt systematisch zu niedrig aus:** Vorsorgeaufwendungen werden in voller
Höhe abgezogen, die Höchstbetragsberechnung nach § 10 Abs. 3/4 EStG ist nicht umgesetzt.
Ebenfalls nicht gerechnet: zumutbare Belastung bei agB, Günstigerprüfung,
Progressionsvorbehalt, Gewerbesteueranrechnung, Vorauszahlungen. Eine automatische
ELSTER-Einreichung findet nicht statt.

Nur deutsches Steuerrecht.

## Enthalten

- **Krypto § 23 EStG** — FIFO per Asset über die volle Historie, taggenaue Jahresfrist nach
  § 108 AO / § 188 BGB (der Jahrestag selbst ist noch steuerpflichtig), Freigrenze einmal
  über alle Broker, Verlustvortrag über Jahre
- **Krypto § 22 Nr. 3** — Staking und Lending zum Zuflusswert, 256-€-Freigrenze auf die
  Gesamtsumme aller sonstigen Leistungen
- **Anlage KAP** — Abgeltungsteuer inkl. Soli und Kirchensteuer, Aktien-Verlusttopf,
  Termingeschäfte nach dem Stand des Jahressteuergesetzes 2024
- **Tarif § 32a** für 2022–2026, Soli mit Milderungszone, Grund- und Splittingtarif
- **Import** — Koinly- und eToro-Steuerreports als PDF, generische Broker-PDFs mit OCR,
  Exchange-CSV
- **Export** — HTML-Dashboard, PDF, ELSTER-CSV/JSON

## Voraussetzungen

Python 3.10+ mit aktivierter Code-Ausführung. Optional `fpdf2` (PDF-Export),
`pdfplumber`/`pymupdf` (PDF-Import), Tesseract mit deutschem Sprachpaket (gescannte PDFs).
Die genauen Installationskommandos stehen in `skills/steuererklaerung-de/SKILL.md`.

## Tests

```bash
cd skills/steuererklaerung-de && python3 tests/run_tests.py
```

186 Fälle in 7 Dateien; CI auf Python 3.10–3.12.

## Lizenz

MIT, ohne Gewährleistung.
