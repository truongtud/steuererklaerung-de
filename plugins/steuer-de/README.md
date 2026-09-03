# Steuererklärung Deutschland

Claude-Plugin für die deutsche Einkommensteuererklärung.

## Worum es geht

Vor der Steuererklärung sitzt man mit einem Stapel Papier: Lohnsteuerbescheinigung,
Bescheinigungen von Bank und Krankenkasse, bei Krypto oder ausländischem Depot dazu
Broker-Reports als PDF und CSV.

**Der Ablauf hier ist: alles in einen Ordner legen, `/steuererklaerung` aufrufen, fertig.**
Das Plugin sortiert jedes Dokument selbst ein, liest die Beträge heraus, fragt nur noch
nach dem, was in keinem Papier stand, rechnet — und führt am Ende Anlage für Anlage durch
das ELSTER-Formular.

Gerechnet wird der Weg vom Bruttolohn bis zur Abschlusszahlung: Einkünfte über alle Anlagen
(N, KAP, SO, V, S, G, Vorsorgeaufwand, Sonderausgaben, außergewöhnliche Belastungen, Kind),
Vorsorgeaufwendungen mit Höchstbetragsberechnung nach § 10 Abs. 3/4, außergewöhnliche
Belastungen nach zumutbarer Belastung, Kinderfreibetrag gegen Kindergeld, Tarif nach § 32a
mit Progressionsvorbehalt, Steuerermäßigung nach § 35a, Solidaritätszuschlag, Kirchensteuer
und Abgeltungsteuer. Krypto kommt exakt dazu: FIFO über die gesamte Anschaffungshistorie,
taggenaue Haltefristen, Freigrenzen **pro Person** statt pro Broker.

Kommt später der Bescheid, hält `/bescheid-pruefen` ihn Position für Position gegen die
eigene Rechnung und nennt die Einspruchsfrist.

Es reicht **nicht** bei ELSTER ein und schickt keine Daten fort: alles läuft lokal, die
Ausgabe sind Dateien. Veranlagungszeiträume 2022–2026, nur deutsches Steuerrecht.

## Eingabeformate

| Format | Woher | Verarbeitung |
|---|---|---|
| **ein ganzer Ordner** | alle Papiere zusammen | `importiere_unterlagen.py` sortiert jede Datei selbst ein |
| Lohnsteuerbescheinigung | Arbeitgeber (PDF) | füllt Anlage N und die Vorsorgeanteile, inkl. Gesamtbeitrag zur RV aus Nr. 22a + 23a |
| Steuerbescheinigung | Bank, Depot (PDF) | füllt Anlage KAP samt beider Verlusttöpfe und Quellensteuer |
| Beitragsbescheinigung | Kranken-/Pflegekasse (PDF) | füllt die Basisabsicherung |
| PDF-Steuerreport | Koinly, eToro | Profil erkennt den Anbieter, liest Tabellen und Summenausweis |
| PDF, unbekannter Anbieter | jeder Broker, auch **gescannt** | generische Tabellenerkennung mit OCR, Ergebnis zur Sichtprüfung markiert |
| Exchange-CSV | Kraken, Coinbase, Bitpanda, Binance | Profil bildet Spalten auf das kanonische Transaktionsschema ab |
| CSV, beliebige Spalten | jede Börse | freies Spalten-Mapping über `mapping.json` |
| `transactions.json` | selbst gepflegt | kanonisches Schema, direkt in die FIFO-Engine |
| `steuerdaten.json` | wird gefüllt, Vorlage liegt bei | von Hand bleiben Stammdaten, Werbungskosten, § 35a und Spenden |

Ein neuer Broker ist **eine JSON-Profildatei**, kein neues Skript; `profile_wizard.py`
erzeugt den Entwurf aus einem echten Report. Für Bescheinigungen gilt dasselbe:
`scripts/profiles/bescheinigungen/`.

## Ausgabeformate

| Datei | Wofür |
|---|---|
| `elster_mapping_<jahr>.csv` | das Arbeitsergebnis: Anlage, Zeile, Bezeichnung, Wert — von oben nach unten abtippen. Semikolon, Dezimalkomma, BOM |
| `elster_mapping_<jahr>.json` | dasselbe maschinenlesbar, mit Quellenangabe je Zeile |
| `taxreport_<jahr>.html` | Dashboard: Kennzahlen, Einkünfte je Anlage, alle Veräußerungen, Unsicherheitsbilanz; self-contained |
| `taxreport_<jahr>.pdf` | druckfertig für Ablage oder Steuerberater |
| `taxreport.json` | vollständiger Report als Struktur |

In der ELSTER-CSV steht pro Formularzeile **genau eine** einzutragende Zahl; Belege je
Quelle stehen unterhalb einer Trennzeile und werden ausdrücklich nicht eingetragen.

## Wie es funktioniert

```
                  ┌─ Bescheinigung ──▶ parse_bescheinigung.py ─▶ steuerdaten.json ─┐
ein Ordner mit    │                     (Nummer + Beschriftung)                    │
allen Papieren ──▶┤                                                                │
importiere_       ├─ Broker/Börse ───▶ parse_broker.py ────────┐                   │
unterlagen.py     │                    Summenabgleich          ▼                   ▼
                  └─ Bescheid ───────▶ /bescheid-pruefen   build_taxreport.py ◀─────┘
                                                           Freigrenzen einmal
                                                           § 32a, § 35a, § 32b, § 31
                                                                   │
                                                                   ▼
                                                           export_report.py
                                                       HTML · PDF · ELSTER-Mapping
```

Freigrenzen und Verlusttöpfe werden **einmal auf die Summe aller Quellen** angewandt, nicht
je Report — § 23, § 22 Nr. 3 und § 20 Abs. 6 gelten personenbezogen über alle Broker.

## So verwendest du es

Sechs Slash-Befehle stehen nach der Installation bereit:

| Befehl | Wofür |
|---|---|
| `/einstieg [jahr]` | Vorbereitung: welche Anlagen betreffen mich, welche Unterlagen brauche ich, Startdatei anlegen |
| `/steuererklaerung [jahr]` | der ganze Durchlauf — einsortieren, extrahieren, rechnen, exportieren, durch ELSTER führen |
| `/krypto-check [frage]` | eine einzelne Frage zu Haltefrist, Freigrenze, Tausch, Staking |
| `/broker-profil [id]` | einen neuen Broker anbinden |
| `/steuer-pruefen [datei]` | einen fertigen TaxReport gegenprüfen |
| `/bescheid-pruefen [datei]` | den Steuerbescheid gegen den Report halten, Einspruchsfrist rechnen |

Bei Namenskollision greift die lange Form (`/steuer-de:steuererklaerung`). Die Befehle löst
nur der Nutzer aus, nicht Claude.

Die Skripte rufst du nicht selbst auf — das macht Claude. Du hängst deine Papiere an und
sagst, was du brauchst:

> 📎 *lohnsteuerbescheinigung-2024.pdf, steuerbescheinigung-bank.pdf, koinly-2024.pdf*
>
> „Mach mir daraus die Steuererklärung 2024. Ledig, 9 % Kirchensteuer. Aus 2023 habe ich
> noch 900 € § 23-Verlustvortrag.“

Die Beträge musst du **nicht** diktieren. Claude sortiert die Dokumente ein, liest die
Bescheinigungen aus, gleicht die Broker-Reports gegen ihre eigenen Summenausweise ab und
fragt nur noch nach dem, was in keinem Papier stand. Voraussetzung ist aktivierte
Code-Ausführung.

Mit den Ergebnissen kommen die **Warnungen** — knapp verfehlte Freigrenzen, fehlende
Anschaffungshistorie, Haltefrist-Konflikte, ungeprüfte Profile. Scheitert ein
Summenabgleich, bricht der Lauf ab, statt eine plausible Zahl zu liefern.

## Wichtig

**Keine Steuerberatung, keine verbindliche Berechnung.** Verbindlich rechnet ELSTER; die
Endkontrolle gehört in die Hände eines Steuerberaters.

- **Die Schätzung sagt dir, wie sie danebenliegt.** Jeder Report führt eine
  *Unsicherheitsbilanz*: je verbliebener Lücke die Wirkungsrichtung auf die Steuer, eine
  Größenordnung wo ableitbar, und die Fundstelle.
- **Drei der sechs Broker-Profile sind ungeprüft** (Coinbase, Bitpanda, Binance) — gegen
  dokumentierte Spaltenüberschriften gebaut, nie gegen einen echten Export. Sie laufen mit
  einer deutlichen Warnung. Geprüft sind Koinly, eToro und Kraken.
- **Die Bescheinigungsprofile sind gegen synthetische Belege gebaut.** An einem echten
  Dokument kann eine Layoutvariante fehlen. Übernommen wird nur, was eindeutig ist: bei der
  Lohnsteuerbescheinigung müssen Feldnummer **und** Beschriftung zusammenpassen, sonst wird
  gemeldet statt eingetragen.
- **Kapitalerträge werden als Saldo gelesen**, der die Verluste der Anlage-KAP-Zeilen 22–25
  bereits enthält. Ist die eigene Bescheinigung brutto ausgewiesen, muss vorher saldiert
  werden. Der Report weist die Annahme aus.

Nicht gerechnet: Kinderbetreuungskosten, Entlastungsbetrag für Alleinerziehende,
Ausbildungsfreibetrag, Gewerbesteueranrechnung, geleistete Vorauszahlungen, die Kürzung des
Vorsorge-Höchstbetrags für Beamte (§ 10 Abs. 3 Satz 3), wallet-bezogenes FIFO. Für Jahre
vor dem laufenden fehlen die Kinderfreibeträge — dort unterbleibt die Günstigerprüfung nach
§ 31, statt mit dem Wert eines Nachbarjahres zu rechnen.

## Voraussetzungen

Python 3.10+ mit aktivierter Code-Ausführung. Optional `fpdf2` (PDF-Export),
`pdfplumber`/`pymupdf` (PDF-Import), Tesseract mit deutschem Sprachpaket (gescannte PDFs).
Die genauen Installationskommandos stehen in `skills/steuererklaerung/SKILL.md`.

## Tests

```bash
cd skills/steuererklaerung && python3 tests/run_tests.py
```

504 Fälle in 20 Dateien; CI auf Python 3.10 bis 3.14, plus Vorschau auf die 3.15-Beta.

## Lizenz

MIT, ohne Gewährleistung.
