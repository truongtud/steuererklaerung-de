# ELSTER-Zeilennummern je Steuerjahr — Referenz

**Die Zahlen stehen in `references/elster_zeilen.json`.** Diese Datei ist die
menschenlesbare Fassung: was drinsteht, woher es kommt, und wie ein neues
Steuerjahr ergänzt wird. `tests/test_elster_zeilen.py` prüft die JSON auf
Vollständigkeit und hält die Anlage-KAP-Zeilen gegen `KAP_ZEILEN_LABEL` in
`scripts/build_taxreport.py` — beide Stellen müssen dieselben Zeilennummern
nennen.

**Warum das überhaupt eine eigene Datei ist:** ELSTER ändert die Formularlayouts
jährlich, teils auch die Zeilennummern innerhalb eines Jahres (Korrektur-
vordrucke). `references/anlagen-referenz.md` und `scripts/build_taxreport.py`
nennen deshalb an vielen Stellen `Z. 7`, `Z. 22` usw. — verstreut über
Kommentare, Konstanten und f-Strings. Diese Datei bündelt dieselben Angaben an
einer Stelle, mit Quelle und Prüfdatum, statt sie nur implizit im Code stehen zu
lassen.

## Warum es **keinen** `fetch_elster_zeilen.py`-Live-Abruf gibt

`scripts/fetch_steuerwerte.py` holt Steuerwerte per HTTP aus amtlichen Quellen
(siehe `references/steuerwerte.md`). Für die Formular-Vordrucke geht das
**nicht** genauso, und das ist nachgeprüft, nicht vermutet:

- Das **Formular-Management-System des Bundes** (`formulare-bfinv.de`), über das
  alle Landesämter auf die amtlichen PDFs verlinken, liefert bei einem einfachen
  `GET` auf `?id=<formular>_<jahr>` nur die HTML-Hülle einer JavaScript-Single-
  Page-App (`Content-Type: text/html`), mit oder ohne Session-Cookie — nie das
  PDF selbst. Ein Skript ohne echten Browser (JS-Ausführung, XHR-Nachladen)
  bekommt die Datei nicht.
- **Dritt-Spiegelungen sind keine verlässliche Abkürzung.** Eine Stichprobe
  einer scheinbar aktuellen "Anlage KAP"-URL bei einem Formular-Archiv lieferte
  beim Test den Vordruck **"– Aug. 2010 –"** unter einer Adresse ohne jede
  Jahresangabe. Ein Skript, das so eine URL für das aktuelle Jahr fest
  verdrahtet, würde über Jahre hinweg denselben veralteten Stand ausliefern,
  ohne dass das auffällt — genau das Gegenteil dessen, was diese Referenz
  leisten soll.

Die einzige verlässliche Quelle ist deshalb: das PDF **von Hand** herunterladen
(Browser, `https://www.formulare-bfinv.de`, „<Anlage> zur
Einkommensteuererklärung <Jahr>" bzw. „Einkommensteuererklärung <Jahr> mit allen
Anlagen") und `scripts/fetch_elster_zeilen.py` nur noch zum **Lesen** dieses
lokalen PDFs benutzen.

**Ausnahme, gefunden bei der Suche nach 2025er-Belegen:** einzelne
Landesfinanzverwaltungen spiegeln vereinzelt eigene Vordrucke als gewöhnliche,
per `curl` abrufbare PDF-Dateien — z. B. lieferte
`https://www.finanzamt.nrw.de/dokumente/anlage-v-einkuenfte-aus-vermietungverpachtung-bebauter-grundstuecke-2025`
den echten Anlage-V-Vordruck 2025 ohne jede Session/JS. Das ist aber kein
verlässliches Muster: dieselbe Such-Systematik fand für Anlage KAP, Anlage N
oder Anlage SO auf `finanzamt.nrw.de` **nichts** — die NRW-eigene Info-Seite zur
Anlage KAP verweist selbst auf `formulare-bfinv.de`. Solche Funde sind also
Einzelfälle, die man bei jedem neuen Steuerjahr neu suchen müsste, keine
verlässliche Automatisierungsgrundlage; sie ersetzen den manuellen
Download-Schritt nicht, sparen ihn aber, wenn man Glück hat.

## `scripts/fetch_elster_zeilen.py` — Entwurf aus einem lokalen PDF

```bash
python3 scripts/fetch_elster_zeilen.py est26_alle_anlagen.pdf --jahr 2026
python3 scripts/fetch_elster_zeilen.py anlage_kap_2026.pdf --jahr 2026 \
    --anlage "Anlage KAP" --schreiben --out entwurf_kap_2026.json
```

Das Skript liest die Textebene des PDFs (PyMuPDF) und erkennt Eingabefelder an
dem Dreiklang, den die Vordrucke drucken: **Beschriftung**, dann die sichtbare
**Zeilennummer**, dann das Eingabekästchen (`,-`), dann die interne
**Kennziffer**. Bei Ja/Nein-Feldern entfällt das Kästchen (`1=Ja` statt `,-`).
Erkannt wird außerdem, welche Anlage und welches Jahr eine Seite zeigt — primär
am internen Formularkopf-Code (z. B. `2026AnlKAP…`), sonst am Fließtext
(„Anlage KAP", „Einkommensteuererklärung 2026").

**Das Ergebnis ist immer ein Entwurf, nie eine Übernahme:**

- Ohne `--schreiben` zeigt der Lauf nur, was er gefunden hat, und den Unterschied
  zum bereits hinterlegten Jahr in `references/elster_zeilen.json`.
- Mit `--schreiben` landet der Entwurf in einer separaten Datei
  (`elster_zeilen_entwurf_<jahr>.json`) — **niemals** direkt in
  `references/elster_zeilen.json`. Die Text-Heuristik ist robust genug für den
  Regelfall, aber mehrzeilige Beschriftungen, die sich über zwei Formularzeilen
  erstrecken, oder ein umgebautes Layout können falsch zugeordnete Beschriftungen
  erzeugen. Jede Zeile vor der Übernahme gegen das PDF selbst lesen.
- Findet das Skript auf keiner Seite auch nur ein Feld, bricht es mit
  Rückgabecode 1 ab, statt eine leere oder geratene Datei zu erzeugen.

## Ein neues Steuerjahr ergänzen

1. Aktuellen Vordruck herunterladen: `https://www.formulare-bfinv.de` →
   „Einkommensteuererklärung `<Jahr>` mit allen Anlagen" (ein PDF mit
   Hauptvordruck + allen Anlagen) oder einzelne Anlagen-PDFs.
2. `scripts/fetch_elster_zeilen.py <pdf> --jahr <Jahr>` laufen lassen und den
   Diff gegen das Vorjahr lesen — er zeigt, welche Zeilen sich verschoben haben.
3. Den Entwurf (`--schreiben`) gegen das PDF **visuell** prüfen: entscheidend ist
   die gedruckte Zeilennummer im Formular, nicht nur, was die Heuristik
   zusammengesetzt hat.
4. Geprüfte Zeilen unter `jahre.<Jahr>.anlagen` in
   `references/elster_zeilen.json` eintragen, `geprueft` auf das heutige Datum
   und `status` auf „gegen den amtlichen Vordruck `<Jahr>` gelesen am …" setzen.
5. Weicht eine Zeile von der Vorjahresfassung ab, die in
   `scripts/build_taxreport.py` fest verdrahtet ist (`KAP_ZEILEN_LABEL` und die
   `add(...)`-Aufrufe in `build_elster_mapping`), dort **ebenfalls** anpassen —
   sonst meldet `tests/test_elster_zeilen.py` die Abweichung, aber der Report
   selbst trägt weiter die alte Zeile ins Mapping ein.
6. `tests/run_tests.py` laufen lassen.

## Warum `geprueft: null` erlaubt ist

Wie in `references/steuerwerte.json` gilt: **kein stilles Verfallsdatum.** Ein
neues Jahr, für das noch niemand den echten Vordruck gegengelesen hat, bekommt
`geprueft: null` statt eines geratenen Datums — der `status`-Text sagt dann
ausdrücklich, dass die Zeilen aus dem Code übernommen und noch nicht gegen das
amtliche PDF verifiziert wurden. Das ist genau der Zustand von `2025` in dieser
Datei, solange niemand Schritt 1–4 oben durchlaufen hat.

**Ein einzelnes Feld kann sein eigenes `geprueft`/`quelle` tragen**, wenn nur
dieses eine Feld — nicht die ganze Jahresfassung — bereits gegen einen echten
Vordruck gelesen wurde. Genau das ist `"Anlage V"` → Zeile 85 in `2025`: gegen
den amtlichen NRW-Vordruck verifiziert (und dabei korrigiert — vorher stand hier
fälschlich Z. 21), während der Rest des Jahres `2025` weiterhin nur aus dem Code
übernommen und `geprueft: null` auf Jahresebene ist. So muss eine Teilprüfung
nicht auf alle Anlagen warten, um sichtbar zu werden, und verwässert auch nicht
den ehrlichen "ungeprüft"-Status der übrigen Felder.

Unabhängig vom Prüfstatus gilt der Disclaimer aus `references/anlagen-referenz.md`
weiter: **Zeilennummern sind Orientierung — vor jeder Eingabe in Mein ELSTER
gegen das echte Formular prüfen.** Auch ein am 2026er-Vordruck geprüfter Wert
schützt nicht vor einem Korrekturvordruck, den ELSTER mitten in der Saison
nachschiebt.
