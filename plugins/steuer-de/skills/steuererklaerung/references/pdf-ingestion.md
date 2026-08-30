# PDF-Ingestion — Broker-/Exchange-TaxReports einlesen

`scripts/parse_pdf.py` liest TaxReports als PDF, erkennt Tabellen und mappt Transaktionen
ins kanonische Schema — **generisch, ohne jede Broker-Kenntnis**. Wo ein Anbieter bekannt
ist, führt der andere Weg schneller zum Ziel: die Profil-Engine. Für Koinly und eToro
liegen fertige **Profile** unter `scripts/profiles/` (`koinly-de.json`, `etoro-de.json`),
angewendet von `scripts/parse_broker.py` — siehe `references/broker-profile.md`.
`parse_pdf.py` ist der Weg für alles, wofür es kein Profil gibt. **Keine Steuerberatung.**
Extraktion immer gegen das Original prüfen.

## Grundregeln dieser Parser

1. **Ein Zahlenparser für alles.** `steuerlib.to_decimal` liest deutsche *und* englische
   Notation, Unicode-Minus (−), Klammer-Notation `(1.234,56)`, nachgestelltes Minus und
   Währungssuffixe. Bei Unlesbarem wirft er einen Fehler — er gibt **nie still 0 zurück**. Ein
   stiller 0-Wert ist der teuerste Fehler in einer Steuerberechnung.
2. **Summenabgleich statt Vertrauen.** Die Profil-Engine liest zusätzlich die im Report
   **selbst ausgewiesenen** Summen (und die Anzahl der Veräußerungen) und vergleicht sie mit
   dem Geparsten. Weicht es ab **oder findet das Summenmuster gar nichts**, bricht der Lauf
   ab und schreibt keine Ausgabedatei. Ohne diesen Abgleich kann ein Parser die Hälfte einer
   Tabelle verlieren und trotzdem eine plausibel aussehende Zusammenfassung drucken.
3. **Keine Freigrenzen in den Parsern** — sie gelten pro Person und Jahr über alle Broker
   und werden einmal in `build_taxreport.py` angewandt (siehe `krypto-steuer.md`).
4. **Ausgabedateien tragen den PDF-Namen** (`<pdf-name>.krypto_result.json`,
   `.kap_result.json`, `.transactions.json`), damit ein zweiter Broker den ersten nicht
   überschreibt.

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
python scripts/parse_pdf.py report.pdf --no-map     # nur extrahieren, kein Mapping
```

`--no-map` überspringt die heuristische Transaktionszuordnung und schreibt nur
`.extracted.json` und `.tables.csv`. Sinnvoll, wenn die Tabellen ohnehin von Hand ins
kanonische Schema überführt werden — dann ist eine Transaktionsdatei mit geratenen Spalten
nur eine Datei mehr, die jemand für fertig halten könnte.

Ausgaben in `--outdir`:
- `<name>.extracted.json` — je Seite `text`, `tables`, `backend`, `ocr`
- `<name>.tables.csv` — alle Tabellen zur Sichtkontrolle
- `<name>.transactions.json` — kanonisches Mapping je Zeile mit `confidence` und
  `_needs_review`

Das Skript meldet außerdem, **wie viele Tabellen es übersprungen hat** (kein Header
erkannt) und markiert Zellen, deren Zahlennotation mehrdeutig ist (`_ambig_spalten`).
Summen-/„Gesamt“-Zeilen werden erkannt und nicht als Transaktionen mitgezählt.

## Spalten- und Typ-Erkennung

Header-Synonyme werden auf kanonische Felder gemappt; dabei wird **je Spalte der beste
Treffer** gewählt statt des ersten. Das verhindert, dass „Gebührenwährung“ als `asset`
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
dem Wort „Trade“ zum `swap` und ein Verkauf aus einer Wallet namens „Kraken Earn“ zum
`reward`. Fehlt `eur_value`, aber `price` und `amount` sind da, wird multipliziert.

## Verifikations-Checkliste (vor der Steuerberechnung)

1. Alle `_needs_review: true` und niedrige `confidence` gegen das PDF prüfen.
2. EUR-**Marktwerte** für `reward`/`swap` ergänzen, falls nicht im Report enthalten. Das
   ist keine Fleißaufgabe: `krypto_fifo.py` **bricht ab**, solange ein als `_needs_fmv`
   markierter Vorgang keinen Wert hat (siehe `krypto-steuer.md`).
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
python scripts/parse_koinly.py koinly_report.pdf   # -> koinly_report.krypto_result.json
python scripts/parse_etoro.py  etoro.pdf           # -> etoro.kap_result.json
python scripts/build_taxreport.py steuerdaten.json \
    --krypto-result koinly_report.krypto_result.json etoro.kap_result.json \
    --kap-result    etoro.kap_result.json \
    -o taxreport.json
```

**Die eToro-Datei steht absichtlich in beiden Listen.** `parse_etoro.py` liefert seit dem
Umbau auf die Profil-Engine das Ausgabeschema **`kap`** (Standardname
`<pdf-name>.kap_result.json`; der eToro-Download heißt meist `taxReport.pdf` und ergäbe
entsprechend `taxReport.kap_result.json`), und diese eine Datei trägt **beide** Hälften: die
Anlage-KAP-Kennzahlen *und* das § 23-/§ 22-Ergebnis. `--krypto-result` liest nur die
Krypto-Hälfte, `--kap-result` nur die KAP-Hälfte. Wird sie nur einer Liste übergeben,
verschwindet die andere Hälfte — bei `--krypto-result` allein also sämtliche
Kapitalerträge, anrechenbare Kapitalertragsteuer und Verlustzeilen. `build_taxreport.py`
warnt in diesem Fall und nennt, was ignoriert wurde; bei Übergabe an beide Listen wird
jede Hälfte genau einmal verbraucht. Details: `references/broker-profile.md`.

**Koinly**: liest § 23-Veräußerungen mit Kostenbasis, Einnahmen (§ 22 Nr. 3, inkl. Airdrops
und Forks), das Futures-Ergebnis (→ Anlage KAP, Termingeschäfte § 20 Abs. 2) und Gebühren.
Erkennt DE- wie EN-Notation und `Kurzfristig`/`Short-term`. Zeilen der Veräußerungstabelle,
die nicht gelesen werden konnten, werden **gezählt und gemeldet**. Bei mehrdeutigem
Datumsformat (Tag ≤ 12) hilft `--dateformat de|en`.

**eToro**: liest den Summenausweis auf Seite 2, der bereits nach deutschem Recht
klassifiziert ist — Anlage SO Z. 47 (§ 23 Krypto-Spot), Anlage KAP Z. 19/21/23/24
(ausländische Kapitalerträge, Termingeschäfte, Aktienverluste), SO Z. 10/11
(Wertpapierleihe/Staking). In `kap_zeilen` bleiben die Vorzeichen des Reports erhalten
(wörtliche Abschrift), in `kennzahlen` werden sie normiert (Gewinne positiv, Verluste
negativ) — beide Blöcke stehen in derselben Datei, siehe `broker-profile.md`. Findet das
Skript **keine** Zeilenzuordnung, bricht es ab, statt lauter Nullen zu liefern. Der
Abgleich der Anlage-SO-Z.-47-Summe ist im Profil als `optional` gekennzeichnet, weil ein
reines Aktien-/CFD-Depot gar keinen Anlage-SO-Block erzeugt; fehlt die Summenzeile, druckt
der Lauf dafür eine `ACHTUNG — ohne Gegenprüfung`-Zeile auf stderr.

Für andere Tools analog ein **Profil** bauen (`references/broker-profile.md`) oder deren
CSV-Transaktionsexport über `parse_inputs.py` führen:

| `--format` | wofür |
|---|---|
| `canonical` (Standard) | die CSV liegt bereits im kanonischen Schema vor — die Spaltennamen sind dann die Feldnamen aus `krypto-steuer.md` |
| `kraken` | Kraken-`ledgers.csv`; paart die zwei Ledger-Zeilen eines Trades und normalisiert Assetcodes (`XETC` → `ETC`, `ETH.S` → `ETH`) |
| `map` | beliebige CSV über `--map mapping.json` |
| `coinbase`, `binance` | **bricht bewusst ab.** Beides ist nicht implementiert; als `canonical` gelesen ergäbe eine solche Datei lauter leere Zeilen und trotzdem eine Erfolgsmeldung. Stattdessen `--format map` mit einem Spalten-Mapping benutzen — oder die mitgelieferten (ungeprüften) Profile über `parse_broker.py` |

`--delimiter` erzwingt das Trennzeichen (deutsches Excel schreibt Semikolon), `-o` schreibt
in eine Datei statt nach stdout.
