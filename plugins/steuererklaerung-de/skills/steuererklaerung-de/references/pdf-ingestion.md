# PDF-Ingestion — Broker-/Exchange-TaxReports einlesen

`scripts/parse_pdf.py` liest TaxReports als PDF, erkennt Tabellen und mappt Transaktionen
ins kanonische Schema. Für Koinly und eToro gibt es fertige Presets. **Keine
Steuerberatung.** Extraktion immer gegen das Original prüfen.

## Grundregeln dieser Parser

1. **Ein Zahlenparser für alles.** `steuerlib.to_decimal` liest deutsche *und* englische
   Notation, Unicode-Minus (−), Klammer-Notation `(1.234,56)`, nachgestelltes Minus und
   Währungssuffixe. Bei Unlesbarem wirft er — er gibt **nie still 0 zurück**. Ein
   stiller 0-Wert ist der teuerste Fehler in einer Steuerberechnung.
2. **Summenabgleich statt Vertrauen.** `parse_koinly.py` und `parse_etoro.py` lesen
   zusätzlich die im Report **selbst ausgewiesenen** Summen (und die Anzahl der
   Veräußerungen) und vergleichen sie mit dem, was sie geparst haben. Weicht es ab, brechen
   sie ab. Ohne diesen Abgleich kann ein Parser die Hälfte einer Tabelle verlieren und
   trotzdem eine plausibel aussehende Zusammenfassung drucken.
3. **Keine Freigrenzen in den Parsern** — sie gelten pro Person und Jahr über alle Broker
   und werden einmal in `build_taxreport.py` angewandt (siehe `krypto-steuer.md`).
4. **Ausgabedateien tragen den PDF-Namen** (`<pdf-name>.krypto_result.json`), damit ein
   zweiter Broker den ersten nicht überschreibt.

## Backends (automatische Wahl, beste zuerst)

| Backend | Stärke | Voraussetzung |
|---|---|---|
| **docling** | Layout- und Tabellenstruktur, gute OCR, auch komplexe/gescannte PDFs | `pip install docling` (lädt Modelle beim 1. Lauf) |
| **pdfplumber** | sehr gute Tabellen bei digitalen PDFs | `pip install pdfplumber` |
| **pymupdf** | schnelle Textebene + `find_tables()` | `pip install pymupdf` |
| **OCR-Fallback** | gescannte PDFs: Tesseract + Spalten-Rekonstruktion | `tesseract-ocr(-deu)`, `poppler-utils`, `pytesseract`, `pdf2image` |

Steuern über `--backend auto|docling|pdfplumber|pymupdf`. OCR springt an, sobald eine Seite
keine brauchbare Textebene hat. Sprachen: `--ocr-lang deu+eng`.

## Aufruf

```bash
python scripts/parse_pdf.py report.pdf --outdir arbeit --backend auto --ocr-lang deu+eng
```

Ausgaben in `--outdir`:
- `<name>.extracted.json` — je Seite `text`, `tables`, `backend`, `ocr`
- `<name>.tables.csv` — alle Tabellen zur Sichtkontrolle
- `<name>.transactions.json` — kanonisches Mapping je Zeile mit `confidence` und
  `_needs_review`

Das Skript meldet außerdem, **wie viele Tabellen es übersprungen hat** (kein Header
erkannt) und markiert Zellen, deren Zahlennotation mehrdeutig ist (`_ambig_spalten`).
Summen-/„Gesamt"-Zeilen werden erkannt und nicht als Transaktionen mitgezählt.

## Spalten- und Typ-Erkennung

Header-Synonyme werden auf kanonische Felder gemappt; dabei wird **je Spalte der beste
Treffer** gewählt statt des ersten. Das verhindert, dass „Gebührenwährung" als `asset`
gelesen wird.

| kanonisch | erkannte Header (DE/EN) |
|---|---|
| `timestamp` | Datum, Date, Zeitpunkt, Valuta, Trade Date, Buchung |
| `type` | Typ, Type, Side, Action, Vorgang, Geschäftsart |
| `asset` | Asset, Coin, Währung, Symbol, Ticker, Produkt |
| `amount` | Menge, Amount, Quantity, Anzahl, Stück, Volume |
| `price` | Kurs, Price, Preis, Rate |
| `eur_value` | Wert, Value, Betrag, Gesamt, Erlös, Gegenwert, Umsatz |
| `fee_eur` | Gebühr, Fee, Provision, Kosten, Spesen, Entgelt |
| `counter_asset` | Erhalten, Quote, Gegenwährung, Counter |

Der Typ wird aus der **Typ-Spalte** klassifiziert, wenn es eine gibt — erst ohne sie aus der
ganzen Zeile (mit niedrigerer `confidence`). Sonst wird jede Zeile eines Handelsblatts mit
dem Wort „Trade" zum `swap` und ein Verkauf aus einer Wallet namens „Kraken Earn" zum
`reward`. Fehlt `eur_value`, aber `price` und `amount` sind da, wird multipliziert.

## Verifikations-Checkliste (vor der Steuerberechnung)

1. Alle `_needs_review: true` und niedrige `confidence` gegen das PDF prüfen.
2. EUR-**Marktwerte** für `reward`/`swap` ergänzen, falls nicht im Report enthalten.
3. `counter_asset`/`counter_amount` für Tausche ergänzen (für korrekte Folge-Lose).
4. **Vollständigkeit der Anschaffungshistorie** — fehlt ein Kauf, rechnet die FIFO-Engine
   mit Kostenbasis 0 und warnt.
5. Bei mehreren Reports: Transfers zwischen eigenen Börsen sind Duplikate und nicht
   steuerbar — entfernen.
6. Die vom Skript gemeldeten übersprungenen Tabellen und nicht zugeordneten Zeilen
   ansehen, nicht überlesen.

## Wenn die Heuristik scheitert

`<name>.extracted.json` / `<name>.tables.csv` selbst lesen und die Tabellen manuell ins
kanonische Schema überführen (Abschnitt 4 in `krypto-steuer.md`). `--backend docling`
liefert bei schwierigen oder gescannten PDFs die beste Tabellenstruktur.

## Vorberechnete Steuerreports (Koinly, CoinTracking, …)

Aggregator-Tools rechnen FIFO bereits wallet-übergreifend und liefern fertige
Veräußerungen mit Kostenbasis und Kurz-/Langfristig-Einstufung. Diese **nicht** erneut
durch `krypto_fifo.py` schicken — die Kaufhistorie fehlt, die Kostenbasis würde 0.

```bash
python scripts/parse_koinly.py koinly_report.pdf -o koinly.krypto_result.json
python scripts/parse_etoro.py  taxReport.pdf     -o etoro.krypto_result.json
python scripts/build_taxreport.py steuerdaten.json \
    --krypto-result koinly.krypto_result.json etoro.krypto_result.json -o taxreport.json
```

**Koinly**: liest § 23-Veräußerungen mit Kostenbasis, Einnahmen (§ 22 Nr. 3, inkl. Airdrops
und Forks), das Futures-Ergebnis (→ Anlage KAP, Termingeschäfte § 20 Abs. 2) und Gebühren.
Erkennt DE- wie EN-Notation und `Kurzfristig`/`Short-term`. Zeilen der Veräußerungstabelle,
die nicht gelesen werden konnten, werden **gezählt und gemeldet**. Bei mehrdeutigem
Datumsformat (Tag ≤ 12) hilft `--dateformat de|en`.

**eToro**: liest den Summenausweis auf Seite 2, der bereits nach deutschem Recht
klassifiziert ist — Anlage SO Z. 47 (§ 23 Krypto-Spot), Anlage KAP Z. 19/21/23/24
(ausländische Kapitalerträge, Termingeschäfte, Aktienverluste), SO Z. 10/11
(Wertpapierleihe/Staking). Vorzeichen bleiben erhalten; findet das Skript **keine**
Zeilenzuordnung, bricht es ab, statt lauter Nullen zu liefern.

Für andere Tools analog ein Preset bauen oder deren CSV-Transaktionsexport über
`parse_inputs.py` führen (`--format kraken`, `--format map --map mapping.json`;
`--delimiter` erzwingt das Trennzeichen).
