# Steuerwerte nach Jahr — Referenz

**Alle hier genannten Werte stehen an genau einer Stelle: `references/steuerwerte.json`.**
`scripts/steuerlib.py` liest sie von dort, die übrigen Skripte lesen sie aus `steuerlib`;
Steuerkonstanten stehen sonst nirgends im Code. Diese Datei ist die menschenlesbare
Fassung derselben Zahlen — `tests/test_steuerwerte_json.py` vergleicht die Tabellen unten
Zelle für Zelle mit der JSON, eine der beiden allein zu ändern schlägt also fehl.

Zuletzt gegen die amtlichen Quellen geprüft: **02.09.2026** (2022–2026), mit
`scripts/fetch_steuerwerte.py` gegen die BMF-Tarifhistorie und die amtliche XML-Fassung
von § 32a EStG und § 3 SolZG.
Vor jeder Einreichung erneut verifizieren — Werte ändern sich jährlich, teils rückwirkend.

## Freibeträge / Freigrenzen / Pauschalen

| Wert | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| Grundfreibetrag (ledig) | 10.347 € | 10.908 € | **11.784 €** | 12.096 € | 12.348 € |
| Arbeitnehmer-Pauschbetrag | 1.200 € | 1.230 € | 1.230 € | 1.230 € | 1.230 € |
| Sparer-Pauschbetrag (ledig) | 801 € | 1.000 € | 1.000 € | 1.000 € | 1.000 € |
| **Freigrenze § 23 EStG** | 600 € | 600 € | **1.000 €** | 1.000 € | 1.000 € |
| **Freigrenze § 22 Nr. 3 EStG** | 256 € | 256 € | 256 € | 256 € | 256 € |
| Sonderausgaben-Pauschbetrag (ledig) | 36 € | 36 € | 36 € | 36 € | 36 € |
| **Soli-Freigrenze** (tarifl. ESt, ledig) | 16.956 € | 17.543 € | 18.130 € | 19.950 € | 20.350 € |
| Kirchensteuersatz | 8 % (BW/BY), sonst 9 % | dito | dito | dito | dito |
| Abgeltungsteuer | 25 % (+ Soli + KiSt) | dito | dito | dito | dito |

Bei Zusammenveranlagung verdoppeln sich Grundfreibetrag (über den Splittingtarif),
Sparer-Pauschbetrag, Sonderausgaben-Pauschbetrag und **Soli-Freigrenze**.
Die Freigrenzen nach § 23 und § 22 Nr. 3 gelten dagegen **pro Person**.

> **Der Grundfreibetrag 2024 wurde rückwirkend erhöht** — von 11.604 € auf **11.784 €**
> durch das *Gesetz zur steuerlichen Freistellung des Existenzminimums 2024* vom
> 02.12.2024. Wer noch mit 11.604 € rechnet, setzt die ESt um rund 26–34 € zu hoch an.

## § 32a EStG — Einkommensteuertarif (Grundtarif)

In `steuerwerte.json` unter `tarif` für **2022 bis 2026** hinterlegt, in
`steuerlib.py` als `TARIF`-Dict daraus gebaut. Zonenformel:

| Jahr | GFB | Zone 2 bis | Zone 3 bis | a₂ | c₃ | a₃ | k₄ | k₅ |
|---|---|---|---|---|---|---|---|---|
| 2022 | 10.347 | 14.926 | 58.596 | 1.088,67 | 869,32 | 206,43 | 9.336,45 | 17.671,20 |
| 2023 | 10.908 | 15.999 | 62.809 | 979,18 | 966,53 | 192,59 | 9.972,98 | 18.307,73 |
| 2024 | 11.784 | 17.005 | 66.760 | 954,80 | 991,21 | 181,19 | 10.636,31 | 18.971,06 |
| 2025 | 12.096 | 17.443 | 68.480 | 932,30 | 1.015,13 | 176,64 | 10.911,92 | 19.246,67 |
| 2026 | 12.348 | 17.799 | 69.878 | 914,51 | 1.034,87 | 173,10 | 11.135,63 | 19.470,38 |

```
zvE ≤ GFB                    → ESt = 0
GFB < zvE ≤ Zone2            → y = (zvE − GFB)/10.000 ;  ESt = (a₂·y + 1.400)·y
Zone2 < zvE ≤ Zone3          → z = (zvE − Zone2)/10.000 ; ESt = (a₃·z + 2.397)·z + c₃
Zone3 < zvE ≤ 277.825        → ESt = 0,42·zvE − k₄
zvE > 277.825                → ESt = 0,45·zvE − k₅
```

Der Steuerbetrag ist auf den **vollen Euro abzurunden** (§ 32a Abs. 1 Satz 6) — nicht
kaufmännisch runden. **Splittingtarif** (§ 32a Abs. 5): ESt = 2 · ESt_Grundtarif(zvE/2).

`tests/test_steuerlib.py` prüft die Zonenübergänge auf Stetigkeit. Das ist der schnellste
Test für falsch abgeschriebene Konstanten: ein Zahlendreher erzeugt fast immer einen Sprung
an einer Zonengrenze.

## Solidaritätszuschlag (§§ 3, 4 SolZG)

5,5 % der tariflichen ESt, aber **erst oberhalb der Freigrenze** und dort zunächst gedeckelt
auf **11,9 % des Überhangs** (Milderungszone, § 4 Satz 2 SolZG):

```
Soli = 0                                              , wenn ESt ≤ Freigrenze
Soli = min(0,055 · ESt ; 0,119 · (ESt − Freigrenze))  , sonst
```

Die Milderungszone ist kein Detail: Bei einer ESt von 18.200 € (2024) sind es 8,33 €, nicht
1.001 €. Bei Zusammenveranlagung verdoppelt sich die Freigrenze.

## Neues Steuerjahr ergänzen

```bash
S=plugins/steuer-de/skills/steuererklaerung/scripts
python3 $S/fetch_steuerwerte.py --jahre 2022-2027              # nur anzeigen
python3 $S/fetch_steuerwerte.py --jahre 2022-2027 --schreiben  # übernehmen
```

`fetch_steuerwerte.py` benutzt ausschließlich amtliche Quellen:

- **Tarifhistorie des Bundesministeriums der Finanzen** (bmf-steuerrechner.de) — je Seite
  ein Tarifzeitraum mit der „Formel nach § 32a EStG“, zurück bis 1958. Das Jahr steht in
  der Seitenüberschrift, die Zuordnung ist also keine Annahme. Der Dateiname trägt ein
  Datum und wird von der Startseite geholt, nicht fest verdrahtet.
- **Amtliche XML-Fassung von EStG und SolZG** (gesetze-im-internet.de) — daraus die
  Freigrenze des § 3 Abs. 3 SolZG; der Tarif des geltenden Jahres wird damit gegen die
  BMF-Historie gehalten.

Vor dem Schreiben prüft das Skript jeden Tarif auf Stetigkeit an den Zonengrenzen und die
beiden Quellen gegeneinander — stimmt etwas nicht, wird nichts geschrieben. Ohne
`--schreiben` zeigt es nur, was sich ändern würde. Zum Lesen des PDF wird PyMuPDF
gebraucht (`pip install pymupdf`), dieselbe Bibliothek wie in `scripts/parse_pdf.py`.

Danach von Hand:

1. **Sparer-Pauschbetrag** (§ 20 Abs. 9 EStG), **Arbeitnehmer-Pauschbetrag**
   (§ 9a Satz 1 Nr. 1a EStG) und **Freigrenze § 23** (§ 23 Abs. 3 Satz 5 EStG) für das
   neue Jahr in der JSON eintragen — die holt das Skript bewusst nicht. Es legt sie als
   `null` an: **niemals 0**. Eine 0 hieße „kein Pauschbetrag“ und ginge still in die
   Berechnung ein; `null` heißt „noch nicht ermittelt“, hält das Jahr aus der Tabelle
   und löst den Ersatzwert des nächstgelegenen Jahres samt Warnung aus (siehe unten).
2. **`quelle`** eintragen: das Änderungsgesetz mit Fundstelle im Bundesgesetzblatt. Das
   nennt die BMF-Historie nicht, und es ist die Angabe, die man in einer Rückfrage ans
   Finanzamt zitiert. Was das Skript zuletzt geprüft hat, steht daneben in `beleg`.
3. Die Tabellen oben nachziehen; `tests/test_steuerwerte_json.py` prüft sie gegen die JSON.
4. `python3 tests/run_tests.py` — der Stetigkeitstest läuft über alle Jahre.

Die **Soli-Freigrenzen früherer Jahre** prüft das Skript nicht nach: amtlich
veröffentlicht ist nur die geltende Fassung des § 3 SolZG, eine maschinenlesbare
Fassungshistorie gibt es nicht. Es lässt sie unangetastet und sagt im Lauf, welche Jahre
das betrifft.

Das Skript geht als einziges hier ins Netz und ist **nicht** Teil der Report-Pipeline:
ein Steuerreport hängt nie davon ab, ob ein Server erreichbar ist.

## Ein Jahr mit unvollständigen Werten

Der Report wird in jedem Fall **gebaut**. Zwei Lücken sind zu unterscheiden.

**Kein Tarif hinterlegt.** Dann entfällt die ESt-Schätzung — und mit ihr alles, was auf
ihr aufbaut:

| | Verhalten |
|---|---|
| ESt, Soli, Kirchensteuer (Tarif) | `null`; `ergebnis.status` ist `"nicht berechenbar"` mit Begründung, es gibt keinen Nachzahlung/Erstattung-Saldo |
| Arbeitnehmer-Pauschbetrag, Sparer-Pauschbetrag, Freigrenze § 23, Soli-Freigrenze | **Werte des nächstgelegenen vollständig hinterlegten Jahres**, plus Warnung: *„Für 2030 ist kein § 32a-Tarif hinterlegt; Pauschbeträge, Freigrenzen und die Soli-Freigrenze wurden ersatzweise mit den Werten für 2026 angesetzt.“* |
| Sonderausgaben-Pauschbetrag, Freigrenze § 22 Nr. 3 | 36 € bzw. 256 € — im Gesetz jahresunabhängig, daher unberührt |
| zvE, Einkünfte je Anlage, Abgeltungsteuer, Verlusttöpfe, ELSTER-Mapping | werden normal gerechnet |

Grund für die Zweiteilung: Der Tarif lässt sich nicht extrapolieren — jede Zahl daraus wäre
erfunden. Die Freigrenze § 23 dagegen entscheidet, ob ein Ergebnis überhaupt
steuerpflichtig ist; ohne sie stünde im Report ein deutlich **zu hoher**
steuerpflichtiger Betrag, und das ist eine schlechtere Auskunft als ein um ein Jahr
veralteter Pauschbetrag mit Warnung. Der Ausweichwert wird auf den hinterlegten Bereich
gekappt, also nach oben wie nach unten.

**Tarif hinterlegt, Pauschbeträge noch nicht.** Genau das legt `fetch_steuerwerte.py`
für ein neues Jahr an: `tarif` und `soli_freigrenze` stehen, die drei von Hand gepflegten
Werte sind `null`. Die ESt wird dann **normal gerechnet** — der Tarif ist ja da —, und für
Pauschbeträge, Freigrenzen und Soli-Freigrenze greift derselbe Ersatzwert des
nächstgelegenen vollständigen Jahres, mit der Warnung *„Für 2027 sind Pauschbeträge und
Freigrenzen noch nicht hinterlegt; … ersatzweise mit den Werten für 2026 angesetzt.“*
Maßgeblich ist dafür nicht, ob ein Tarif hinterlegt ist, sondern ob **alle** Jahreswerte
es sind (`steuerlib.jahr_mit_werten`).

**Anders bei `krypto_fifo.py` im Alleinlauf:** dort steht bewusst kein Ersatzwert. Fehlt
das Jahr, wird das **rohe Netto** mit `"freigrenze_angewendet": false` und einer eigenen
Warnung ausgewiesen — `build_taxreport.py` behandelt eine solche Quelle danach wie jede
andere Rohquelle und wendet die Freigrenze selbst an. Beim Lauf über `build_taxreport.py`
erscheinen deshalb **beide** Warnungen; das ist kein Widerspruch, sondern die Reihenfolge.

In jedem Fall gilt: die Werte nach dem Abschnitt oben in `steuerwerte.json` nachtragen. Eine
Schätzung mit Vorjahreswerten ist Orientierung, keine Zahl für eine Erklärung.

## Vereinfachungen der ESt-Schätzung (bewusst)

- **Vorsorgeaufwendungen werden in voller Höhe abgezogen** — die Höchstbetragsberechnung
  nach § 10 Abs. 3/4 EStG fehlt. Das ist die größte Fehlerquelle: tatsächlich ist weniger
  abziehbar, das zvE hier also zu niedrig und die geschätzte Steuer **zu niedrig**.
- **Die Günstigerprüfung nach § 32d Abs. 6 wird gerechnet und ausgewiesen, aber nicht
  angewandt** — sie wirkt nur auf Antrag, und das ELSTER-Mapping enthält keine
  Antragszeile. Ist der Tarif günstiger, nennt der Report den Betrag und den Antrag.
- Keine Kinderfreibeträge im Tarif, keine zumutbare Belastung bei außergewöhnlichen
  Belastungen, keine Gewerbesteueranrechnung, keine Vorauszahlungen.

→ Die Schätzung dient der Orientierung. Die verbindliche Zahl liefert ELSTER.
