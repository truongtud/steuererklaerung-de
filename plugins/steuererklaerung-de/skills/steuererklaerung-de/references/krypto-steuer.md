# Krypto-Besteuerung in Deutschland — Referenz

Maßgeblich: EStG sowie die BMF-Schreiben vom 10.05.2022 und 06.03.2025 zu „Einzelfragen
zur ertragsteuerrechtlichen Behandlung bestimmter Kryptowerte". Diese Datei fasst die
Regeln zusammen, die `krypto_fifo.py` umsetzt. **Keine Steuerberatung.** Rechtslage vor
jeder Einreichung verifizieren.

## 1. Private Veräußerungsgeschäfte — § 23 EStG

Kryptowerte sind „andere Wirtschaftsgüter". Gewinne aus einer Veräußerung **innerhalb der
Jahresfrist** sind steuerpflichtig (§ 23 Abs. 1 Nr. 2 EStG); danach steuerfrei.

Als Veräußerung gilt:
- Verkauf gegen EUR/Fiat
- **Tausch Krypto-zu-Krypto** (BTC→ETH): Veräußerung des abgegebenen Coins zum
  EUR-Marktwert im Zeitpunkt des Tauschs, zugleich Anschaffung des erhaltenen Coins
- Bezahlen mit Krypto (Waren, Dienstleistungen)

### Die Jahresfrist ist taggenau — und endet später, als „365 Tage" nahelegt

Die Frist berechnet sich nach § 108 Abs. 1 AO i. V. m. § 188 Abs. 2 BGB: Sie endet **mit
Ablauf des Tages im Folgejahr, der dem Anschaffungstag entspricht**.

- Kauf 10.01.2023 → Verkauf **10.01.2024 ist noch steuerpflichtig**. Steuerfrei erst ab
  dem 11.01.2024.
- Kauf 01.03.2023 → Verkauf 29.02.2024 sind zwar 365 Tage, aber der Jahrestag ist der
  01.03.2024: **steuerpflichtig**.
- Kauf am 29.02. eines Schaltjahres → die Frist endet am 28.02. des Folgejahres
  (§ 188 Abs. 3 BGB).
- Die **Uhrzeit spielt keine Rolle** — Fristen laufen tageweise.

Ein `>= 365 Tage`-Vergleich ist an all diesen Stellen falsch; `steuerlib.haltefrist_erfuellt`
bildet die Regel korrekt ab, `tests/test_steuerlib.py` prüft die Grenzfälle.

### Gewinnermittlung und FIFO

```
Gewinn = Veräußerungserlös (EUR) − Anschaffungskosten (EUR) − Veräußerungskosten/Gebühren
```

**FIFO**: die zuerst angeschafften Coins gelten als zuerst veräußert. `krypto_fifo.py`
rechnet **per Asset**; das BMF lässt auch eine wallet-/depotbezogene Betrachtung zu — bei
mehreren Wallets kann das Ergebnis abweichen. Die verwendete Methode steht im Report.

Anschaffungsnebenkosten (Kauf-Gebühren) erhöhen die Anschaffungskosten. Bei einem **Tausch**
wird die Gebühr als Anschaffungsnebenkosten des **erhaltenen** Coins aktiviert und deshalb
nicht zusätzlich beim abgegebenen Coin abgezogen — sonst würde dieselbe Gebühr zweimal
wirken. Bei einem gewöhnlichen Verkauf bleibt sie Veräußerungskosten.

Die Engine rechnet mit der **vollständigen Historie**, weist aber nur das angefragte
Steuerjahr aus. Beides ist nötig: FIFO braucht die Vorjahre, die Kennzahlen dürfen sie
nicht enthalten.

### Freigrenze — kein Freibetrag

- ab 2024: **1.000 €** pro Jahr · bis 2023: **600 €**
- Sie gilt für die **Summe aller** privaten Veräußerungsgewinne einer Person im
  Kalenderjahr — über alle Börsen, Broker und Tools hinweg, und einschließlich
  nicht-Krypto-Veräußerungsgeschäften.
- Unter der Freigrenze: komplett steuerfrei. Erreicht oder überschritten: der **gesamte**
  Gewinn ist steuerpflichtig, nicht nur der übersteigende Teil.

Deshalb wendet die **Profil-Engine** (`brokerprofile.py`, für jedes Profil gleichermaßen)
die Freigrenze **nicht** an, sondern liefert Roh-Nettobeträge mit
`"freigrenze_angewendet": false`; `build_taxreport.py` wendet sie **einmal auf die Summe
aller Quellen** an. `parse_koinly.py` und `parse_etoro.py` sind nur noch dünne Aufsätze auf
dieselbe Engine und haben dazu keine eigene Logik mehr — die Regel gilt damit auch für
jedes neu geschriebene Profil, ohne dass jemand daran denken muss. Zwei Reports mit je
800 € sind zusammen 1.600 € und damit voll steuerpflichtig — würde jede Quelle für sich
prüfen, bliebe beides „steuerfrei".

### Verlustverrechnung und Verlustvortrag

Verluste aus § 23 sind nur mit Gewinnen aus § 23 verrechenbar (§ 23 Abs. 3 Satz 7/8 EStG),
innerhalb des Jahres und darüber hinaus per Rück- oder Vortrag. Ein verbleibender Verlust
wird auf Antrag **gesondert festgestellt** — der Report weist ihn dafür aus.

Reihenfolge im Folgejahr: **erst die Freigrenze auf das Jahresergebnis selbst**, dann der
Vortrag. Ein Jahr, das für sich unter der Freigrenze liegt, ist ohnehin steuerfrei und
verbraucht keinen Vortrag. Eingabe: `anlage_so.verlustvortrag_23_vorjahr`.

## 2. Staking, Lending, Rewards — § 22 Nr. 3 EStG

Laufende Erträge sind **sonstige Leistungen**, bewertet mit dem EUR-Marktwert **bei
Zufluss**.
- **Freigrenze 256 €** pro Jahr für alle § 22-Nr.-3-Leistungen zusammen — also auch für
  nicht-Krypto-Einkünfte aus `anlage_so.sonstige_einkuenfte`.
- Die erhaltenen Coins bekommen ein **neues Anschaffungsdatum** (Zufluss) und
  **Anschaffungskosten = Marktwert bei Zufluss**. Eine spätere Veräußerung läuft wieder
  über § 23, mit einer neuen Jahresfrist ab Zufluss.
- Die früher diskutierte **Verlängerung der Haltefrist auf 10 Jahre** bei Staking ist
  **nicht** anzuwenden (BMF) — es bleibt bei einem Jahr.

## 3. Weitere Fälle (kurz)

- **Termingeschäfte / CFDs** (typisch eToro): keine § 23-Fälle, sondern Kapitalerträge
  nach § 20 Abs. 2 EStG → Anlage KAP. Der eigene Verrechnungskreis und der
  20.000-€-Deckel (§ 20 Abs. 6 Sätze 5, 6) sind durch das **Jahressteuergesetz 2024
  aufgehoben, anwendbar in allen offenen Fällen** — Verluste sind mit sämtlichen
  Kapitalerträgen verrechenbar. Siehe `anlagen-referenz.md`.
- **Mining**: gewerblich oder § 22 Nr. 3, je nach Umfang → Einzelfallprüfung.
- **Airdrops**: steuerpflichtig bei Gegenleistung (§ 22 Nr. 3), sonst unentgeltlicher
  Erwerb mit Anschaffungskosten 0.
- **Hard Forks**: Anschaffungskosten werden aufgeteilt oder sind 0 — Einzelfall.
- **Transfers zwischen eigenen Wallets**: nicht steuerbar, kein FIFO-Effekt. Die Engine
  ignoriert `deposit`/`withdrawal`, warnt aber, wenn welche vorkommen — denn eine fehlende
  Anschaffung bedeutet später Kostenbasis 0.
- **NFTs**: meist wie andere Wirtschaftsgüter (§ 23), Einzelfall.

## 4. Eingabe für die Engine (kanonisches Schema)

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `timestamp` | ja | ISO-8601 oder `TT.MM.JJJJ [hh:mm:ss]` |
| `type` | ja | `buy`/`sell`/`swap`/`reward`/`deposit`/`withdrawal` |
| `asset` | ja | Ticker (bei `swap`: das **abgegebene** Asset) |
| `amount` | ja | Menge (negative Werte werden als Betrag gelesen) |
| `eur_value` | ja* | EUR-Wert: Kosten bei `buy`, Erlös bei `sell`, FMV bei `swap`/`reward` |
| `fee_eur` | nein | Gebühr in EUR |
| `reward_kind` | bei reward | `staking`/`lending` |
| `counter_asset` | bei swap | erhaltenes Asset |
| `counter_amount` | bei swap | erhaltene Menge |

\* Für `reward` und `swap` muss der **historische EUR-Marktwert** ergänzt werden, wenn die
Börse ihn nicht liefert. Unlesbare Beträge und Daten führen zu einem Abbruch mit Nennung
des Datensatzes — nicht zu einer stillen 0.

### Ein fehlender Marktwert ist keine 0 — er bricht ab

Liefert ein Export für einen `swap`, einen `reward` oder eine Ein-/Auslieferung keinen
Euro-Wert, schreiben `parse_inputs.py` und die Profil-Engine `"eur_value": null` und setzen
zusätzlich das Feld `_needs_fmv`. **`krypto_fifo.py` verweigert dann die Rechnung**, nennt
den Datensatz und sagt, was zu tun ist.

Früher lief so eine Zeile mit 0 € durch. Das ist die teuerste Sorte Fehler, die dieses
Skill kennt, weil sie in beide Richtungen wirkt: als Erlös eines Tauschs erzeugt die 0 ein
Ergebnis, das es nie gab; als Anschaffungskosten des erhaltenen Coins macht sie später den
**gesamten** Verkaufserlös zum Gewinn. Eine 0 ist eine Aussage über den Markt — die hat
hier niemand getroffen.

Also den historischen Kurs zum Zeitpunkt in `eur_value` eintragen. `_needs_fmv` darf
stehenbleiben oder entfernt werden, beides ist zulässig; sobald ein Wert > 0 dasteht, ist
die Markierung erledigt.

War der Wert **tatsächlich** null — wertloser Airdrop, Hard Fork, dokumentierte
Anschaffungskosten 0 —, wird das im Datensatz festgehalten:

```json
{ "timestamp": "2024-03-01", "type": "reward", "asset": "XYZ", "amount": "1000",
  "eur_value": "0", "nullwert_bestaetigt": true }
```

Damit läuft die Zeile still durch. Der Unterschied zwischen „ich habe nachgesehen, es war
nichts wert" und „ich habe nicht nachgesehen" bleibt so im Datensatz erhalten, statt im
Ergebnis zu verschwinden. Ohne diese Bestätigung erzeugt eine glatte 0 bei Erlös oder
Kostenbasis eine Warnung und ein `nullwert_ungeklaert` bzw. `nullkosten_ungeklaert` an der
betroffenen Position — auch dann, wenn die Zeile gar nicht `_needs_fmv` trug und deshalb
nicht abgebrochen wurde.

## 5. Typische Fehler, auf die zu achten ist

- Fehlende **Anschaffungshistorie** → Kostenbasis 0 → zu hoher Gewinn (die Engine warnt
  und setzt `acquisition_date: "UNBEKANNT"`, `held_days: -1`).
- Tausch nicht als Veräußerung erfasst → zu niedriger Gewinn.
- Freigrenze als Freibetrag missverstanden — oder je Broker getrennt geprüft.
- Haltefrist mit „365 Tagen" statt taggenau gerechnet (Schaltjahr, Jahrestag).
- Staking-Zufluss vergessen ODER doppelt gewertet: einmal als Ertrag (§ 22 Nr. 3) **und**
  als Anschaffung mit Kostenbasis — beides ist korrekt und gewollt.
- Gebühren nicht berücksichtigt oder beim Tausch doppelt abgezogen.
- Vorjahresverluste nie festgestellt und deshalb im Folgejahr nicht verrechenbar.
