# Steuerwerte nach Jahr — Referenz

**Alle hier genannten Werte stehen im Code an genau einer Stelle: `scripts/steuerlib.py`.**
Diese Datei ist die menschenlesbare Fassung davon und dient der Prüfung — wer einen Wert
ändert, ändert ihn in `steuerlib.py` und hier, sonst nirgends. In den übrigen Skripten
stehen bewusst keine Steuerkonstanten mehr.

Zuletzt gegen öffentliche Quellen geprüft: **30.08.2026** (2022–2026).
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
| **Soli-Freigrenze** (tarifl. ESt, ledig) | 16.956 € | 17.543 € | 18.130 € | 19.450 € | 20.350 € |
| Kirchensteuersatz | 8 % (BW/BY), sonst 9 % | dito | dito | dito | dito |
| Abgeltungsteuer | 25 % (+ Soli + KiSt) | dito | dito | dito | dito |

Bei Zusammenveranlagung verdoppeln sich Grundfreibetrag (über den Splittingtarif),
Sparer-Pauschbetrag, Sonderausgaben-Pauschbetrag und **Soli-Freigrenze**.
Die Freigrenzen nach § 23 und § 22 Nr. 3 gelten dagegen **pro Person**.

> **Der Grundfreibetrag 2024 wurde rückwirkend erhöht** — von 11.604 € auf **11.784 €**
> durch das *Gesetz zur steuerlichen Freistellung des Existenzminimums 2024* vom
> 02.12.2024. Wer noch mit 11.604 € rechnet, setzt die ESt um rund 26–34 € zu hoch an.

## § 32a EStG — Einkommensteuertarif (Grundtarif)

In `steuerlib.py` als `TARIF`-Dict für **2022 bis 2026** hinterlegt. Zonenformel:

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

1. Grundfreibetrag, Zonengrenzen und Koeffizienten aus § 32a EStG in der geltenden Fassung
   holen (gesetze-im-internet.de), Soli-Freigrenze aus § 3 Abs. 3 SolZG.
2. In `steuerlib.py` in `TARIF`, `SOLI_FREIGRENZE`, `FREIGRENZE_23`, `SPARER_PB`,
   `AN_PAUSCHBETRAG` eintragen — und in der Tabelle oben.
3. `python3 tests/run_tests.py` — der Stetigkeitstest läuft automatisch über alle Jahre.

## Ein Jahr ohne hinterlegten Tarif

Ist für das Steuerjahr kein `TARIF` hinterlegt, wird der Report trotzdem **gebaut**. Nur
die ESt-Schätzung entfällt — und mit ihr alles, was auf ihr aufbaut:

| | Verhalten |
|---|---|
| ESt, Soli, Kirchensteuer (Tarif) | `null`; `ergebnis.status` ist `"nicht berechenbar"` mit Begründung, es gibt keinen Nachzahlung/Erstattung-Saldo |
| Arbeitnehmer-Pauschbetrag, Sparer-Pauschbetrag, Freigrenze § 23 | **Wert des nächstgelegenen hinterlegten Jahres**, plus Warnung: *„Für 2030 ist kein § 32a-Tarif hinterlegt; Pauschbeträge und Freigrenzen wurden ersatzweise mit den Werten für 2026 angesetzt.“* |
| Sonderausgaben-Pauschbetrag, Freigrenze § 22 Nr. 3 | 36 € bzw. 256 € — im Gesetz jahresunabhängig, daher unberührt |
| zvE, Einkünfte je Anlage, Abgeltungsteuer, Verlusttöpfe, ELSTER-Mapping | werden normal gerechnet |

Grund für die Zweiteilung: Der Tarif lässt sich nicht extrapolieren — jede Zahl daraus wäre
erfunden. Die Freigrenze § 23 dagegen entscheidet, ob ein Ergebnis überhaupt
steuerpflichtig ist; ohne sie stünde im Report ein deutlich **zu hoher**
steuerpflichtiger Betrag, und das ist eine schlechtere Auskunft als ein um ein Jahr
veralteter Pauschbetrag mit Warnung. Der Ausweichwert wird auf den hinterlegten Bereich
gekappt, also nach oben wie nach unten.

**Anders bei `krypto_fifo.py` im Alleinlauf:** dort steht bewusst kein Ersatzwert. Fehlt
das Jahr, wird das **rohe Netto** mit `"freigrenze_angewendet": false` und einer eigenen
Warnung ausgewiesen — `build_taxreport.py` behandelt eine solche Quelle danach wie jede
andere Rohquelle und wendet die Freigrenze selbst an. Beim Lauf über `build_taxreport.py`
erscheinen deshalb **beide** Warnungen; das ist kein Widerspruch, sondern die Reihenfolge.

In jedem Fall gilt: die Werte nach dem Abschnitt oben in `steuerlib.py` nachtragen. Eine
Schätzung mit Vorjahreswerten ist Orientierung, keine Zahl für eine Erklärung.

## Vereinfachungen der ESt-Schätzung (bewusst)

- **Vorsorgeaufwendungen werden in voller Höhe abgezogen** — die Höchstbetragsberechnung
  nach § 10 Abs. 3/4 EStG fehlt. Das ist die größte Fehlerquelle: tatsächlich ist weniger
  abziehbar, das zvE hier also zu niedrig und die geschätzte Steuer **zu niedrig**.
- Kapitalerträge bleiben in der Abgeltungsteuer, keine Günstigerprüfung (§ 32d Abs. 6).
- Keine Kinderfreibeträge im Tarif, kein Progressionsvorbehalt, keine zumutbare Belastung
  bei außergewöhnlichen Belastungen, keine Gewerbesteueranrechnung, keine Vorauszahlungen.

→ Die Schätzung dient der Orientierung. Die verbindliche Zahl liefert ELSTER.
