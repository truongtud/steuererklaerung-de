---
name: steuererklaerung
description: Erstellt einen TaxReport für die deutsche Einkommensteuererklärung über alle Anlagen (N, KAP, SO, V, S, G, Vorsorge, Sonderausgaben, agB, Kind), rechnet Krypto exakt nach FIFO/§ 23 EStG (taggenaue Haltefrist, Freigrenze, Staking § 22 Nr. 3) und Kapitalerträge inkl. Verlusttöpfen, liest Broker- und Börsen-Reports als PDF oder CSV über Profildateien ein (Koinly, eToro, Kraken, Coinbase, Bitpanda, Binance; weitere Broker und Steuerbescheinigungen über eigene Profile) und exportiert HTML, PDF und ELSTER-Feld-Mapping. Use whenever the user mentions Steuererklärung, Einkommensteuer, Krypto-Steuer, crypto tax, Anlage N/KAP/SO/V, Veräußerungsgeschäfte, Staking-Steuer, ELSTER, Lohnsteuerbescheinigung, Steuerbescheinigung, Erträgnisaufstellung, Freigrenze, FIFO, Verlustvortrag, Termingeschäfte, or wants a tax report from broker/exchange PDFs, exchange CSVs or income data in Germany. Nicht für Steuerrecht anderer Länder.
license: MIT — NUR Orientierung, KEINE Steuerberatung.
---

# Steuererklärung Deutschland — TaxReport-Generator

Erstellt aus Einkommens- und Krypto-Daten einen strukturierten **TaxReport** über alle
Anlagen, rechnet Krypto nach FIFO/§ 23 EStG, schätzt Einkommensteuer (§ 32a),
Solidaritätszuschlag, Kirchensteuer und Abgeltungsteuer, berücksichtigt die
Steuerermäßigung nach § 35a und den Progressionsvorbehalt nach § 32b, prüft die
Günstigerprüfung nach § 32d Abs. 6, rechnet einbehaltene Steuern an und exportiert
**HTML**, **PDF** und ein **ELSTER-Feld-Mapping**.

> **Keine Steuerberatung.** Der Skill erzeugt eine Arbeitsgrundlage. Die verbindliche
> Berechnung liefert ELSTER, die Endkontrolle gehört in die Hände des Steuerberaters.
> Diesen Hinweis im Ergebnis **immer** mitgeben.

## Pipeline

```
PDF/CSV-Reports ─▶ 0) einlesen ───┐
(Broker/Exchange)                 │
  parse_broker (Profile)          ├▶ 1) normalisieren ▶ 2) FIFO ▶ 3) Report bauen ▶ 4) exportieren
  parse_pdf (generisch)           │    parse_inputs    krypto_fifo  build_taxreport   export_report
JSON/manuell ─────────────────────┘                                                  HTML · PDF · ELSTER

scripts/steuerlib.py      ── ein Zahlenparser, eine Fristenlogik, alle Steuerwerte
scripts/fetch_steuerwerte.py ─ holt § 32a EStG / § 3 SolZG (Pflege, nicht Pipeline)
scripts/pruefe_bescheid.py  ── Steuerbescheid gegen den Report halten, Fristen
scripts/neue_steuerdaten.py ── Startdatei und Unterlagen-Checkliste (/einstieg)
scripts/brokerprofile.py  ── Profil-Engine: Erkennung, Anwendung, Summenabgleich
scripts/profiles/*.json   ── ein Broker = eine Profildatei
```

**`references/steuerwerte.json` ist die einzige Stelle mit jahresabhängigen Werten**,
`scripts/steuerlib.py` die einzige mit Zahlenlogik; die Tabellen in
`references/steuerwerte.md` sind dieselben Zahlen zum Nachlesen und werden von
`tests/test_steuerwerte_json.py` gegen die JSON geprüft. Gepflegt wird die JSON mit
`scripts/fetch_steuerwerte.py` aus dem Gesetzestext. Grundregel des Codes: **bei
unlesbarer Eingabe wird abgebrochen, nie still 0 angenommen.**

## Slash-Befehle

Als Plugin bringt dieser Skill sechs vom Nutzer aufrufbare Einstiege mit, die alle hierher
zurückführen: `/einstieg` (Vorbereitung: welche Anlagen, welche Unterlagen, Startdatei),
`/steuererklaerung` (ganzer Durchlauf), `/krypto-check` (Einzelfrage ohne Report),
`/broker-profil` (neuen Broker anbinden), `/steuer-pruefen` (fertigen Report gegenprüfen)
und `/bescheid-pruefen` (den Steuerbescheid gegen den Report halten, mit Einspruchsfrist).
Wird einer davon aufgerufen, gilt zusätzlich dessen eigene Schrittfolge.

## Was reingeht, was rauskommt

| Eingabe | Weg |
|---|---|
| PDF von Koinly oder eToro | `parse_broker.py` (Profil, erkennt automatisch) |
| PDF eines Brokers ohne Profil, auch gescannt | `parse_pdf.py` (Tabellenerkennung + OCR), Ergebnis prüfen |
| CSV von Kraken, Coinbase, Bitpanda, Binance | `parse_broker.py --profil <id>` |
| CSV mit beliebigen Spalten | `parse_inputs.py --format map --map mapping.json` |
| `transactions.json` (kanonisch) | direkt in `krypto_fifo.py` / `build_taxreport.py --transactions` |
| `steuerdaten.json` | Hand-Eingabe, Vorlage in `assets/` |
| Lohnsteuerbescheinigung (PDF) | lesen, Nr. 3/4/5/6 nach `steuerdaten.json` übertragen |

| Ausgabe | Inhalt |
|---|---|
| `elster_mapping_<jahr>.csv` | **das Arbeitsergebnis** — Anlage, Zeile, Bezeichnung, Wert; pro Formularzeile genau eine einzutragende Zahl, Belege unterhalb einer Trennzeile. Semikolon, Dezimalkomma, BOM |
| `elster_mapping_<jahr>.json` | dasselbe maschinenlesbar, mit `quelle` und `art` je Zeile |
| `taxreport_<jahr>.html` | Dashboard, self-contained, mit Druck-Stylesheet |
| `taxreport_<jahr>.pdf` | druckfertige Fassung |
| `taxreport.json` | vollständiger Report als Struktur, Grundlage der Exporte |

Zwischenstände zum Prüfen und Korrigieren: `<name>.krypto_result.json`,
`<name>.kap_result.json`, `<name>.transactions.json`, `<name>.extracted.json`,
`<name>.tables.csv`.

Setup (einmalig):
```bash
pip install fpdf2 pdfplumber pymupdf pytesseract pdf2image --break-system-packages
# gescannte PDFs zusätzlich (System):
#   apt-get install -y tesseract-ocr tesseract-ocr-deu poppler-utils
pip install docling --break-system-packages   # optional, beste Tabellenqualität
```

### Schritt 0 — Broker-Reports einlesen

**Ein Einstiegspunkt für alle Broker und Börsen.** `parse_broker.py` erkennt anhand der
Profile in `scripts/profiles/`, welcher Report vorliegt:

```bash
python3 scripts/parse_broker.py --list              # welche Profile gibt es
python3 scripts/parse_broker.py report.pdf          # Profil automatisch erkennen
python3 scripts/parse_broker.py export.csv --profil binance
```

Je nach Report entsteht `<name>.krypto_result.json`, `<name>.kap_result.json` oder
`<name>.transactions.json`. Vorhanden sind Profile für Koinly, eToro, Kraken sowie —
**ungeprüft, gegen die dokumentierten Spalten gebaut** — Coinbase, Bitpanda und Binance.
Ein neuer Broker ist eine Profildatei, kein neues Skript: `references/broker-profile.md`
beschreibt das Schema, `profile_wizard.py` erzeugt aus einem echten Report einen Entwurf.

```bash
python3 scripts/profile_wizard.py neuer_report.pdf --id mein-broker
```

Der Entwurf enthält bewusst `TODO`-Marker für alles, was der Wizard nicht sicher zuordnen
kann; ein Profil mit `TODO`, ohne Pflichtfelder oder ohne Summenabgleich wird von der
Engine **abgelehnt**. Erst prüfen, dann `geprueft_am` setzen.

Jeder Lauf gleicht das Ergebnis gegen die **im Report selbst ausgewiesenen Summen** ab und
bricht bei Abweichung ab. Diese Meldung ist der wichtigste Schutz gegen stille
Zeilenverluste — nicht überspringen, nicht wegkonfigurieren. Ein `ungeprueft`-Profil
warnt zusätzlich deutlich; diese Warnung gehört in die Antwort an den Nutzer.

**Vorberechnete Steuerreports** (Koinly, CoinTracking, Blockpit …) haben FIFO bereits
wallet-übergreifend gerechnet. Diese **nicht** erneut durch `krypto_fifo.py` schicken — die
Kaufhistorie fehlt, die Kostenbasis würde 0. Sie liefern **Roh-Nettobeträge ohne
Freigrenze** (`freigrenze_angewendet: false`), weil die Freigrenzen pro Person und Jahr
über alle Broker gelten; `build_taxreport.py` wendet sie einmal auf die Summe an.

`parse_koinly.py` und `parse_etoro.py` bleiben als Kurzbefehle bestehen und rufen dieselbe
Engine auf.

**Generische Broker-PDFs** (eigene Transaktionslisten):
```bash
python3 scripts/parse_pdf.py report.pdf --outdir arbeit --backend auto --ocr-lang deu+eng
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
- Unbekannte Feldnamen werden gemeldet („meintest du …?“) und **ignoriert** — die Warnung
  ernst nehmen, ein Tippfehler ist sonst stillschweigend 0 € wert. `--strict` schreibt den
  Report weiterhin, endet aber mit Rückgabecode 3, damit der Fehler nicht untergeht.
- **Krypto aus CSV**: `python3 scripts/parse_inputs.py datei.csv --format kraken -o transactions.json`
- **Krypto-zu-Krypto-Tausch ist eine Veräußerung** — als `swap` mit `eur_value` erfassen.
- **Verlustvorträge aus Vorjahren** eintragen (`anlage_so.verlustvortrag_23_vorjahr`,
  `anlage_kap.verlustvortrag_aktien_vorjahr`), sonst verfallen sie faktisch.

**Der Nutzer schreibt die `steuerdaten.json` nicht selbst.** Er hängt Reports an und
beschreibt seine Lage in Prosa. Die Vorlage aus `assets/` mit dem füllen, was er gesagt
hat, und die verbleibenden Lücken **gezielt erfragen** statt eine JSON-Datei anzufordern —
typischerweise fehlen Vorsorgeaufwendungen, Werbungskosten, anrechenbare
Kapitalertragsteuer und offene Verlustfeststellungen. Die ausgefüllte Datei am Ende
mitliefern, damit er sie im Folgejahr wiederverwenden kann.

Vor dem Rechnen kurz abgleichen: Steuerjahr, Veranlagung, Kirchensteuer, Vollständigkeit
der Anschaffungshistorie, offene Verlustfeststellungen.

### Schritt 2 — Krypto FIFO (nur bei Roh-Transaktionen)

```bash
python3 scripts/krypto_fifo.py transactions.json <steuerjahr> krypto_result.json
```

Rechnet per-Asset-FIFO über die **gesamte** Historie, weist aber nur das Steuerjahr aus.
Prüft die Jahresfrist taggenau nach § 108 AO / § 188 BGB (der Jahrestag selbst ist noch
steuerpflichtig — „365 Tage“ ist falsch), die Freigrenze und Staking nach § 22 Nr. 3.
Details und Edge-Cases: `references/krypto-steuer.md`. `build_taxreport.py` ruft die Engine
sonst selbst auf.

### Schritt 3 — TaxReport bauen

```bash
python3 scripts/build_taxreport.py steuerdaten.json --transactions transactions.json -o taxreport.json
# mehrere Quellen, Krypto und Wertpapiere gemischt:
python3 scripts/build_taxreport.py steuerdaten.json \
    --krypto-result koinly.krypto_result.json etoro.kap_result.json \
    --kap-result    etoro.kap_result.json \
    -o taxreport.json
```

**Die eToro-Datei steht absichtlich in beiden Listen.** Sie trägt beide Hälften — die
Anlage-KAP-Kennzahlen *und* das § 23-/§ 22-Ergebnis. `--krypto-result` liest nur die eine,
`--kap-result` nur die andere; in beiden Listen wird jede Hälfte genau einmal verbraucht,
in nur einer verschwindet die andere. Das Skript warnt dann und nennt, was fehlt.

Setzt die Anlagen zusammen, wendet die Freigrenzen **einmal auf die Summe** an, verrechnet
Verlustvorträge, schätzt zvE und ESt (§ 32a, Grund-/Splittingtarif), Soli (mit
Milderungszone) und Kirchensteuer, rechnet die Abgeltungsteuer inkl. Verlusttöpfen,
ermittelt **Nachzahlung oder Erstattung** und erzeugt das ELSTER-Mapping.

Freigrenzen und Verlusttöpfe gehören hierher und nicht in die Parser: § 23, § 22 Nr. 3 und
§ 20 Abs. 6 gelten **personenbezogen über alle Broker und Depots**. Werte aus Dateien und
handgepflegte Werte aus `steuerdaten.json` werden **addiert**; ist eine Kennzahl in beiden
belegt, weist der Report das getrennt aus, damit eine Doppelerfassung auffällt.
Fehlt für ein Jahr der Tarif, wird die Schätzung übersprungen und darauf hingewiesen —
dann `scripts/fetch_steuerwerte.py` laufen lassen, wie in `references/steuerwerte.md`
unter „Neues Steuerjahr ergänzen“ beschrieben.

### Schritt 4 — Exportieren

```bash
python3 scripts/export_report.py taxreport.json --outdir out --formats html pdf elster
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
   ignorierte unbekannte Felder, Steuerjahr ohne Tarif, Bestände nahe der Jahresfrist,
   Haltefrist-Konflikte zwischen Report und Gesetz, Profile ohne Gegenprüfung.
3. Bei Kapitalerträgen die **Saldo-Annahme** nennen: `kapitalertraege` wird als der Betrag
   gelesen, der die Verluste der Anlage-KAP-Zeilen 22–25 **bereits enthält**. Ist die eigene
   Bescheinigung brutto ausgewiesen, muss vorher saldiert werden. Der Report stellt diesen
   Satz an den Anfang von `hinweise`; er gehört in die Antwort.
4. Der **Disclaimer**: keine Steuerberatung, ELSTER ist maßgeblich, Endkontrolle durch
   Steuerberater. Nicht weglassen — er steht auch in jedem Export.

## Reference-Dateien (bei Bedarf lesen)

- `references/steuerwerte.md` — Tarif, Freigrenzen, Pauschbeträge je Jahr; wie ein neues
  Jahr ergänzt wird (`fetch_steuerwerte.py`). Bei jedem neuen Steuerjahr zuerst hierher.
  Die Zahlen selbst stehen in `references/steuerwerte.json`.
- `references/bescheid.md` — Steuerbescheid prüfen: Fristenkette mit Fundstellen,
  was geprüft wird, warum nichts geraten wird.
- `references/krypto-steuer.md` — § 23 / § 22 Nr. 3, FIFO, taggenaue Haltefrist,
  Freigrenze über alle Broker, Verlustvortrag, kanonisches Schema, Edge-Cases.
- `references/anlagen-referenz.md` — `steuerdaten.json`-Schema, ELSTER-Zuordnung je Anlage,
  Rechtsstand § 20 Abs. 6 nach dem JStG 2024.
- `references/broker-profile.md` — Profil-Schema, Ausgabeschemata (inkl. `kap`),
  Vorzeichenregeln, wie ein neuer Broker angebunden wird. **Bei jedem neuen Broker hierher.**
- `references/pdf-ingestion.md` — Backends, Spalten-Erkennung, Summenabgleich,
  Verifikations-Checkliste, Troubleshooting.

## Grenzen (bewusst nicht automatisiert)

Höchstbetragsberechnung Vorsorgeaufwand (**größte Vereinfachung** — die Schätzung fällt
dadurch zu niedrig aus), zumutbare Belastung bei agB, Günstigerprüfung (KAP/Kind),
Progressionsvorbehalt, Gewerbesteueranrechnung, Vorauszahlungen, wallet-bezogenes FIFO.
Diese überlässt der Skill ELSTER bzw. dem Steuerberater. Eine automatische
ELSTER-Einreichung erfolgt **nicht** — ausgegeben wird ein Feld-Mapping zur manuellen
Eingabe.
