# Steuererklärung Deutschland

Claude-Plugin für die deutsche Einkommensteuererklärung.

## Worum es geht

Wer Krypto handelt oder ein Depot bei einem ausländischen Broker hat, sitzt vor der
Steuererklärung mit einem Stapel PDFs und CSVs, aus denen am Ende ein paar Dutzend Zahlen
in ELSTER-Felder wandern müssen. Dazwischen liegt die Arbeit: FIFO über die gesamte
Anschaffungshistorie, taggenaue Haltefristen, Freigrenzen, die pro Person und nicht pro
Broker gelten, Verlusttöpfe mit unterschiedlichen Regeln, Verlustvorträge aus Vorjahren.

Das Plugin liest die Broker-Reports, rechnet Krypto nach FIFO/§ 23 EStG und die
Kapitalerträge nach § 20/§ 32d, setzt daraus einen TaxReport über alle Anlagen zusammen
(N, KAP, SO, V, S, G, Vorsorgeaufwand, Sonderausgaben, außergewöhnliche Belastungen, Kind),
schätzt Einkommensteuer, Solidaritätszuschlag, Kirchensteuer und Abgeltungsteuer samt
Nachzahlung oder Erstattung — und gibt ein Feld-für-Feld-Mapping aus, das in „Mein ELSTER“
abgetippt wird.

Es reicht **nicht** bei ELSTER ein und schickt keine Daten fort: alles läuft lokal, die
Ausgabe sind Dateien. Veranlagungszeiträume 2022–2026, nur deutsches Steuerrecht.

## Eingabeformate

| Format | Woher | Verarbeitung |
|---|---|---|
| PDF-Steuerreport | Koinly, eToro | Profil erkennt den Anbieter, liest Tabellen und Summenausweis |
| PDF, unbekannter Anbieter | jeder Broker, auch **gescannt** | generische Tabellenerkennung mit OCR, Ergebnis zur Sichtprüfung markiert |
| Exchange-CSV | Kraken, Coinbase, Bitpanda, Binance | Profil bildet Spalten auf das kanonische Transaktionsschema ab |
| CSV, beliebige Spalten | jede Börse | freies Spalten-Mapping über `mapping.json` |
| `transactions.json` | selbst gepflegt | kanonisches Schema, direkt in die FIFO-Engine |
| `steuerdaten.json` | von Hand, Vorlage liegt bei | Lohn, Werbungskosten, Vorsorge, Kapitalerträge, Kinder, Verlustvorträge |

Ein neuer Broker ist **eine JSON-Profildatei**, kein neues Skript; `profile_wizard.py`
erzeugt den Entwurf aus einem echten Report.

## Ausgabeformate

| Datei | Wofür |
|---|---|
| `elster_mapping_<jahr>.csv` | das Arbeitsergebnis: Anlage, Zeile, Bezeichnung, Wert — von oben nach unten abtippen. Semikolon, Dezimalkomma, BOM |
| `elster_mapping_<jahr>.json` | dasselbe maschinenlesbar, mit Quellenangabe je Zeile |
| `taxreport_<jahr>.html` | Dashboard: Kennzahlen, Einkünfte je Anlage, alle Veräußerungen, Hinweise; self-contained, mit Druck-Stylesheet |
| `taxreport_<jahr>.pdf` | druckfertig für Ablage oder Steuerberater |
| `taxreport.json` | vollständiger Report als Struktur |

In der ELSTER-CSV steht pro Formularzeile **genau eine** einzutragende Zahl; Belege je
Quelle stehen unterhalb einer Trennzeile und werden ausdrücklich nicht eingetragen.

## Wie es funktioniert

```
Broker-PDF / CSV ─▶ parse_broker.py ─┐
(Profil, Summenabgleich)             ├─▶ build_taxreport.py ─▶ export_report.py
fremdes PDF ─▶ parse_pdf.py ─────────┤   Freigrenzen einmal    HTML · PDF · ELSTER
steuerdaten.json ────────────────────┘   Verlusttöpfe, § 32a
```

Freigrenzen und Verlusttöpfe werden **einmal auf die Summe aller Quellen** angewandt, nicht
je Report — § 23, § 22 Nr. 3 und § 20 Abs. 6 gelten personenbezogen über alle Broker.

## So benutzt du es

Vier Slash-Befehle stehen nach der Installation bereit:

| Befehl | Wofür |
|---|---|
| `/steuererklaerung [jahr]` | der ganze Durchlauf bis zur ELSTER-CSV |
| `/krypto-check [frage]` | eine einzelne Frage zu Haltefrist, Freigrenze, Tausch, Staking |
| `/broker-profil [id]` | einen neuen Broker anbinden |
| `/steuer-pruefen [datei]` | einen fertigen TaxReport gegenprüfen |

Bei Namenskollision greift die lange Form (`/steuer-de:steuererklaerung`). Die
Befehle löst nur der Nutzer aus, nicht Claude.

Die Skripte rufst du nicht selbst auf — das macht Claude. Du hängst deine Reports an und
sagst, was du brauchst:

> 📎 *koinly-2024.pdf, etoro-taxreport-2024.pdf*
>
> „Mach mir daraus die Steuererklärung 2024. Ledig, 9 % Kirchensteuer, Bruttoarbeitslohn
> 78.500 €, Lohnsteuer 18.420 €. Aus 2023 habe ich noch 900 € § 23-Verlustvortrag.“

Claude liest die Reports, zeigt den Summenabgleich, fragt nach dem, was fehlt
(Vorsorgeaufwendungen, Werbungskosten, anrechenbare KESt), und liefert HTML, PDF und die
ELSTER-CSV. Eine `steuerdaten.json` musst du nicht schreiben — Claude füllt die Vorlage aus
dem Gespräch. Voraussetzung ist aktivierte Code-Ausführung.

Genauso gehen kleine Fragen („BTC am 10.01.2023 gekauft, am 10.01.2024 verkauft —
steuerfrei?“) und das Anbinden eines neuen Brokers („Baue mir ein Profil für diese
Erträgnisaufstellung“).

Mit den Ergebnissen kommen die **Warnungen** — knapp verfehlte Freigrenzen, fehlende
Anschaffungshistorie, Haltefrist-Konflikte, ungeprüfte Profile. Scheitert ein
Summenabgleich, bricht der Lauf ab, statt eine plausible Zahl zu liefern.

## Wichtig

**Keine Steuerberatung, keine verbindliche Berechnung.** Verbindlich rechnet ELSTER; die
Endkontrolle gehört in die Hände eines Steuerberaters.

- **Die Schätzung fällt systematisch zu niedrig aus:** Vorsorgeaufwendungen werden in
  voller Höhe abgezogen, die Höchstbetragsberechnung nach § 10 Abs. 3/4 EStG ist nicht
  umgesetzt.
- **Drei der sechs Broker-Profile sind ungeprüft** (Coinbase, Bitpanda, Binance) — gegen
  dokumentierte Spaltenüberschriften gebaut, nie gegen einen echten Export. Sie laufen mit
  einer deutlichen Warnung. Geprüft sind Koinly, eToro und Kraken.
- **Kapitalerträge werden als Saldo gelesen**, der die Verluste der Anlage-KAP-Zeilen 22–25
  bereits enthält. Ist die eigene Bescheinigung brutto ausgewiesen, muss vorher saldiert
  werden. Der Report weist die Annahme aus.

Ebenfalls nicht gerechnet: zumutbare Belastung bei agB, Günstigerprüfung,
Progressionsvorbehalt, Gewerbesteueranrechnung, Vorauszahlungen.

## Voraussetzungen

Python 3.10+ mit aktivierter Code-Ausführung. Optional `fpdf2` (PDF-Export),
`pdfplumber`/`pymupdf` (PDF-Import), Tesseract mit deutschem Sprachpaket (gescannte PDFs).
Die genauen Installationskommandos stehen in `skills/steuererklaerung/SKILL.md`.

## Tests

```bash
cd skills/steuererklaerung && python3 tests/run_tests.py
```

368 Fälle in 10 Dateien; CI auf Python 3.10 bis 3.14, plus Vorschau auf die 3.15-Beta.

## Lizenz

MIT, ohne Gewährleistung.
