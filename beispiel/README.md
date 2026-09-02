# Beispiel: ein vollständiger Durchlauf

Ein synthetischer Fall (Steuerjahr **2024**, alle Daten erfunden), der die Pipeline von
der Eingabe bis zum ELSTER-Mapping zeigt — derselbe Fall wie im Beispiel-Prompt des
Haupt-READMEs: ledig, 9 % Kirchensteuer, 78.500 € Bruttoarbeitslohn, 900 €
§ 23-Verlustvortrag aus 2023, 850 € Kapitalerträge.

## Eingaben

| Datei | Inhalt |
|---|---|
| [`steuerdaten.json`](steuerdaten.json) | Lohn, Werbungskosten, Vorsorge, Kapitalerträge, Verlustvortrag, § 35a |
| [`transactions.json`](transactions.json) | sechs Krypto-Transaktionen — je ein Lehrbuchfall |

Die Transaktionen decken die drei wichtigsten § 23-/§ 22-Fälle ab:

- **BTC-Verkauf aus einem 2022er-Los** — Haltefrist über ein Jahr, steuerfrei;
- **kurzfristiger ETH-Gewinn** (Kauf Januar, Verkauf August 2024) — steuerpflichtig,
  wird mit dem Verlustvortrag verrechnet;
- **Staking-Reward über 256 €** — sonstige Leistung nach § 22 Nr. 3.

## Ausgaben

| Datei | Wofür |
|---|---|
| [`elster_mapping_2024.csv`](elster_mapping_2024.csv) | das Arbeitsergebnis: Zeile für Zeile in „Mein ELSTER“ abtippen |
| [`elster_mapping_2024.json`](elster_mapping_2024.json) | dasselbe maschinenlesbar, mit Quellenangabe je Zeile |
| [`taxreport_2024.html`](taxreport_2024.html) | Dashboard (self-contained, im Browser öffnen) |
| [`taxreport.json`](taxreport.json) | der vollständige Report als Struktur |

Kernzahlen: § 23-Gewinn 1.482 € (der BTC-Langfristgewinn bleibt steuerfrei), nach
Verlustvortrag 582 € steuerpflichtig; 700 € Staking nach § 22 Nr. 3; zvE 66.192 €,
tarifliche ESt 17.105 €, abzüglich 456 € Steuerermäßigung nach § 35a (20 % von 480 €
haushaltsnahen Dienstleistungen und 1.800 € Handwerkerleistungen), geschätzte
Erstattung 2.090,46 €.

Die Vorsorgeaufwendungen zeigen die Höchstbetragsberechnung nach § 10 Abs. 3 und 4: von
12.080 € eingetragenen Beiträgen sind **11.050 €** abziehbar. Die Basisversorgung steht
mit dem Gesamtbeitrag von 14.600 € drin, abzüglich des steuerfreien Arbeitgeberanteils
von 7.300 € bleiben 7.300 €. Kranken- und Pflegeversicherung übersteigen mit 3.750 €
den Höchstbetrag von 1.900 €, bleiben nach § 10 Abs. 4 Satz 4 aber voll abziehbar —
dafür entfallen die 1.030 € Arbeitslosenversicherung daneben ganz. Die Hinweise am Anfang der
CSV — insbesondere zur fehlenden Höchstbetragsberechnung der Vorsorgeaufwendungen —
gehören zum Ergebnis dazu.

## Selbst erzeugen

Vom Repository-Stamm aus, in ein Arbeitsverzeichnis statt in `beispiel/` — so bleiben
die eingecheckten Dateien als Vergleichsmaßstab unberührt (das PDF zusätzlich, wenn
`fpdf2` installiert ist):

```bash
S=plugins/steuer-de/skills/steuererklaerung/scripts
mkdir -p /tmp/beispiel-lauf
python3 $S/build_taxreport.py beispiel/steuerdaten.json \
    --transactions beispiel/transactions.json -o /tmp/beispiel-lauf/taxreport.json
python3 $S/export_report.py /tmp/beispiel-lauf/taxreport.json --outdir /tmp/beispiel-lauf
```

Die ELSTER-Mappings (CSV/JSON) müssen byte-identisch mit den eingecheckten sein;
`taxreport.json` und das HTML unterscheiden sich nur im eingebetteten
Erstellungszeitpunkt.
