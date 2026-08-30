# Broker-Profile — neue Broker und Börsen anbinden

Statt für jeden Broker ein eigenes Skript zu schreiben, beschreibt ein **Profil**
deklarativ, wie ein Report gelesen wird. `scripts/parse_broker.py` erkennt anhand der
Profile selbst, welcher Report vorliegt, wendet das passende an und prüft das Ergebnis
gegen die Summen, die der Report selbst ausweist.

```
report.pdf ─▶ parse_broker.py ─▶ Profil erkennen ─▶ anwenden ─▶ Summenabgleich ─▶ *.json
   .csv                                 │
                              scripts/profiles/*.json
```

Ein neuer Broker ist damit eine **JSON-Datei plus ein Test-Fixture**, kein neues Skript.

```bash
python scripts/parse_broker.py --list                 # was ist da
python scripts/parse_broker.py report.pdf             # Profil automatisch erkennen
python scripts/parse_broker.py report.csv --profil binance -o out.json
```

## Warum deklarativ

Die teuersten Fehler dieses Skills stammten aus handgeschriebenen Parsern, die bei einem
unerwarteten Layout still Zeilen verworfen haben. Ein Profil erzwingt drei Dinge, die ein
Ad-hoc-Parser gern vergisst: eine **Erkennung** (welcher Report ist das überhaupt), eine
**Pflichtfeldliste** (was muss jede Zeile liefern) und einen **Summenabgleich** (stimmt das
Ergebnis mit dem Report überein). Fehlt eines davon oder steht irgendwo ein `TODO`, lehnt
die Engine das Profil ab — sowohl in `pruefe_profil` als auch in `wende_an`, damit es sich
nicht umgehen lässt.

## Profil-Datei

`scripts/profiles/<id>.json`:

```json
{
  "id": "koinly-de",
  "label": "Koinly Steuerbericht (deutsch und englisch)",
  "quelle": "Koinly",
  "eingabe": "pdf",
  "ergebnis": "krypto_vorberechnet",
  "erkennung": { "muss": ["Koinly"], "darf_nicht": [], "punkte": 10 },
  "notation": "auto",
  "datum": "auto",
  "jahr":      { "muster": ["Steuerjahr:?\\s*(\\d{4})"] },
  "bereiche":  { … },
  "tabellen":  [ … ],
  "werte":     [ … ],
  "summen":    [ … ],
  "elster":    [ … ],
  "hinweise":  [ "…" ],
  "zusatz":    { "methode": "…" },
  "geprueft_am": "2026-08-30",
  "status": "geprueft",
  "fixture": "tests/fixtures/koinly-de.txt"
}
```

| Feld | Bedeutung |
|---|---|
| `id` | Dateiname ohne `.json`, kebab-case; erscheint im Ergebnis als `profil` |
| `eingabe` | `pdf` oder `csv` |
| `ergebnis` | `krypto_vorberechnet` · `krypto_transaktionen` · `kap` — bestimmt das Ausgabeschema |
| `erkennung.muss` | Regexe, die **alle** im Reporttext vorkommen müssen |
| `erkennung.darf_nicht` | Regexe, die den Report ausschließen (trennt z. B. DE- von EN-Fassung) |
| `erkennung.punkte` | Priorität bei mehreren Treffern; Gleichstand ist ein Fehler, kein Zufall |
| `notation` | `de`, `en` oder `auto` — steuert `steuerlib.to_decimal` |
| `datum` | `de`, `en`, `iso` oder `auto` |
| `jahr.muster` | Regexe, aus denen das Steuerjahr gelesen wird |
| `bereiche` | benannte Textabschnitte `{start, ende[], warnung_wenn_fehlt}`; ohne sie trifft ein Muster wie `Cost` auch „Cost basis" irgendwo im Dokument |
| `zusatz` | statisches JSON, das ins Ergebnis gemischt wird (z. B. `methode`) |
| `hinweise` | Texte, die als `hinweise` durchgereicht werden |
| `geprueft_am` | Datum, an dem das Profil zuletzt gegen einen **echten** Report lief |
| `status` | `geprueft` oder `ungeprueft` — letzteres funktioniert, druckt aber eine deutliche Warnung |
| `fixture` | anonymisierter Textausschnitt, gegen den der Test läuft — **Pflicht** |

### `tabellen`

```json
{
  "name": "veraeusserungen",
  "rolle": "veraeusserungen",
  "start": "Verkaufsdatum\\s+Erwerbsdatum",
  "ende":  "Zusammenfassung",
  "zeile": "^(?P<verkauf>{DT})\\s+(?P<erwerb>{DT})\\s+(?P<asset>.+?)\\s+(?P<menge>{NUM})…",
  "felder": { "disposal_date": "verkauf", "acquisition_date": "erwerb",
              "asset": "asset", "amount": "menge", "gain_eur": "gewinn" },
  "pflicht": ["disposal_date", "gain_eur"],
  "ignoriere": ["^Seite \\d+", "^Koinly"],
  "langfristig": { "feld": "frist", "muster": "Langfristig|Long[- ]?term" },
  "melde_nicht_zugeordnet": true
}
```

`zeile` nutzt **benannte Gruppen**, `felder` bildet sie auf die kanonischen Namen ab.
`rolle` ist `veraeusserungen` (fertige Veräußerungen) oder `transaktionen` (kanonisches
Transaktionsschema). `ignoriere` filtert Kopf- und Fußzeilen heraus — ohne das zählt jede
Seitenzahl als verlorene Zeile.

`langfristig` liest die Kurz-/Langfristig-Spalte des Reports. **Maßgeblich ist trotzdem das
Gesetz:** liegen beide Daten vor, rechnet die Engine die Frist nach § 108 AO / § 188 BGB
selbst und benutzt dieses Ergebnis. Weicht das Label ab, wird es als
`holding_period_laut_report` mitgeführt und eine `HALTEFRIST-KONFLIKT`-Warnung erzeugt.
Grund für diese Reihenfolge: die Frist ist aus zwei Pflichtfeldern eindeutig bestimmbar,
und ein falsches „Langfristig" senkt die Steuer — das ist die Fehlerrichtung mit
Konsequenzen. Ein einziger Konflikt heißt meist, dass der ganze Report unter einer
365-Tage- oder ausländischen Regel erzeugt wurde; dann ist jede Zeile verdächtig, nicht
nur die markierte.

Nicht zugeordnete, nicht leere und nicht ignorierte Zeilen zwischen `start` und `ende`
werden gezählt, gemeldet und landen in `warnungen` — das Frühwarnsignal für ein geändertes
Layout.

**Regex-Makros** in allen Mustern: `{NUM}` (Betrag in DE/EN mit allen Vorzeichenformen),
`{DT}` (Datum), `{VOR}` (Vorzeichen), `{HALTE}` (Kurz-/Langfristig, DE+EN).

### `werte`

Einzelwerte außerhalb von Tabellen — Summen, Kennzahlen, KAP-Zeilen:

```json
{ "pfad": ["kap_zeilen.19", "etoro_kap.z19_auslaend_kapitalertraege"],
  "muster": ["Anlage\\s+KAP\\s*Zeile\\s*19\\)?\\s*({NUM})",
             "Anlage\\s+KAP\\s*({NUM})\\s*Zeile\\s*19\\)?"],
  "typ": "betrag", "form": "betrag2", "flach": true, "default": "0.00" }
```

`pfad` und `muster` dürfen Listen sein: mehrere Ziele werden gleich befüllt, mehrere Muster
sind Alternativen — der erste Treffer gewinnt, der auch `form` erfüllt. `form: "betrag2"`
verlangt zwei Nachkommastellen und ist der Schutz davor, dass eine Seiten- oder Jahreszahl
als Betrag gelesen wird. `flach: true` sucht im umbruchbereinigten Text (nötig bei über
zwei Zeilen umbrochenen Beschriftungen), `bereich` schränkt auf einen benannten Abschnitt
ein, `summiere_in` addiert den Wert zusätzlich in einen Sammelpfad, `optional` steuert das
Verhalten bei Nichttreffer.

**Kein `default: "0.00"` auf Report-Zeilen.** Ohne Treffer wird der Pfad weggelassen, nicht
auf null gesetzt — eine nicht berichtete Zeile ist etwas anderes als eine Zeile mit dem
Wert 0,00, und nachgelagerte Logik entscheidet daran (etwa ob ein abgeleiteter Betrag nach
Z. 7 oder Z. 19 gehört). Wer eine explizite Null braucht, setzt `"leer": "null"`.
In `kennzahlen` ist 0 dagegen in Ordnung: dort ist es ein Rechenergebnis, keine Aussage
über den Report.

`werte_regeln` prüft das Ganze: `{"mindestens": 1, "marker": "…", "fehlermeldung": "…"}`
bricht ab, wenn gar kein Wert gefunden wurde — ein Ergebnis aus lauter Nullen ist von einem
echten Null-Report sonst nicht zu unterscheiden.

### `summen` — das Sicherheitsnetz

```json
{ "label": "§ 23 Ergebnis", "art": "betrag",
  "muster": "Kapitalgewinne\\s+({NUM})",
  "vergleich": "summen_basis.veraeusserungen_gewinn_gesamt", "toleranz": "0.01" }
```

`art` ist `betrag`, `anzahl` (Anzahl der Veräußerungen — wird ohne € gedruckt) oder
`zeilen` (verarbeitete gegen vorhandene Datenzeilen, für CSVs ohne Summenzeile).
Weicht der Wert ab, bricht der Lauf mit `PlausibilityError` und Exit-Code 1 ab. Findet das
Muster nichts, ist das eine Warnung. Ein Profil **ohne** `summen` wird abgelehnt.

Vergleichspfade liefert die Engine unter `summen_basis`, u. a.
`veraeusserungen_gewinn_gesamt`, `anzahl_veraeusserungen`, `csv_datenzeilen`,
`verarbeitete_zeilen`.

### `csv`

Für `eingabe: "csv"` ersetzt ein `csv`-Block die Tabellen-Regexe:

```json
{ "spalten": { "timestamp": "Timestamp", "type": "Transaction Type", "asset": "Asset" },
  "pflicht": ["timestamp", "type", "asset", "amount"],
  "typ_werte": { "Buy": "buy", "Sell": "sell", "Staking Income": "reward" },
  "ignoriere_typen": ["Convert"],
  "pruefe_spalte": { "spalte": "Spot Price Currency", "erwartet": "EUR" },
  "trennzeichen": ",",
  "normalisierer": "kraken_ledger" }
```

`pruefe_spalte` ist die Währungsabsicherung — ein USD-Export darf nicht stillschweigend als
EUR gerechnet werden. `normalisierer` ruft eine benannte eingebaute Funktion für das, was
deklarativ nicht geht (Kraken paart zwei Ledger-Zeilen zu einem Trade).

### `elster`

`{"anlage", "zeile", "bezeichnung", "pfad", "nur_wenn_gesetzt"}` — wird als `elster_extra`
durchgereicht.

## Ausgabeschemata

### `krypto_vorberechnet` und `krypto_transaktionen`

Das bestehende Krypto-Ergebnis (siehe `krypto-steuer.md`): `paragraph_23`,
`paragraph_22_nr3`, `steuerfrei_langfristig_eur`, `warnungen`, `elster_extra`, `quelle`.
**Freigrenzen werden nicht angewendet** (`freigrenze_angewendet: false`) —
`build_taxreport.py` wendet sie einmal auf die Summe aller Quellen an.
`krypto_transaktionen` liefert stattdessen eine kanonische `transactions`-Liste, die durch
`krypto_fifo.py` läuft.

### `kap`

Deutsche Steuerbescheinigungen und Erträgnisaufstellungen sind bereits nach
**Anlage-KAP-Zeilen** aufgebaut. Genau das ist das Ausgabeschema:

```json
{
  "steuerjahr": 2025,
  "quelle": "ertraegnisaufstellung-2025.pdf",
  "profil": "trade-republic-de",
  "kap_zeilen": { "7": "1234.56", "21": "6000.00", "23": "500.00" },
  "kennzahlen": {
    "kapitalertraege": "1234.56",
    "gewinn_aktien": "0.00",
    "gewinn_termingeschaefte": "6000.00",
    "verlust_aktien": "-500.00",
    "verlust_termingeschaefte": "0.00",
    "verluste_ohne_aktien": "0.00",
    "verluste_ausfall": "0.00",
    "anrechenbare_kest": "300.00",
    "einbehaltener_soli": "16.50",
    "einbehaltene_kirchensteuer": "0.00",
    "auslaendische_quellensteuer": "0.00",
    "fiktive_quellensteuer": "0.00"
  },
  "so_zeilen": { "47": "0.00" },
  "warnungen": [], "elster_extra": [ … ]
}
```

**Vorzeichenregel — wichtig, weil beide Blöcke unterschiedlich funktionieren:**

- `kap_zeilen` ist die **wörtliche Abschrift** dessen, was der Report druckt. Deutsche
  Bescheinigungen weisen Verluste als positive Beträge aus, und genau so will ELSTER sie in
  den „Verluste"-Zeilen. Keine Vorzeichenkorrektur, keine Plausibilitätsprüfung.
- `kennzahlen` ist die **normierte, vorzeichenbehaftete** Fassung, die
  `build_taxreport.py` verrechnet: Gewinne positiv, Verluste negativ.

Alle Kennzahlen sind einzeln optional (fehlend = 0); der Block selbst ist Pflicht.
Verlusttöpfe, Verrechnung und Freigrenzen rechnet der Report-Bauer, nicht der Parser —
§ 20 Abs. 6 gilt personenbezogen über alle Depots hinweg.

**Zeilen 20–25 sind „davon"-Zeilen** („In den Zeilen 18 und 19 enthaltene …"). Ein Profil
muss die Beträge daher **zusätzlich** in `kapitalertraege` enthalten haben —
`gewinn_aktien`, `gewinn_termingeschaefte`, `verluste_ohne_aktien` und `verluste_ausfall`
ordnen nur den Verrechnungskreisen zu und erhöhen die Bemessungsgrundlage nicht. Weist ein
Report einen Gewinn ausschließlich in einer davon-Zeile aus, gehört er im Profil auch nach
`kapitalertraege`; sonst warnt `build_taxreport.py`, dass die davon-Zeile ihre Summe
übersteigt, und der Betrag bliebe unversteuert.

Die Engine erzwingt das: ein Profil, das Brutto-Zeilen (7, 18, 19) und davon-Zeilen
(20–25) in **einen** Topf summiert, wird beim Laden abgelehnt. Genau diese Verwechslung
hat vorher einen grünen Haken über eine Zahl gesetzt, die die Steuerberechnung gar nicht
benutzt: der Abgleich summierte eine Gesamtzeile mit ihren eigenen Unterzeilen, während
`kapitalertraege` nur aus 7 + 18 + 19 kam — 800 € Aktiengewinn lagen damit innerhalb des
Häkchens und außerhalb der Bemessungsgrundlage. `summen[].vergleich` muss deshalb gegen
etwas prüfen, das die Berechnung wirklich verwendet, z. B. `kennzahlen.kapitalertraege`.

**Grenze, die man kennen muss:** eine verlorene davon-Zeile bewegt die Bruttosumme nicht
und kann von einem Brutto-Abgleich daher nicht gefunden werden. Wo der Report ein eigenes
Netto nach Verlustverrechnung ausweist, lohnt sich ein zweiter Abgleich dagegen — als
`zusatz`, nicht als Kennzahl, denn § 20 Abs. 6 wird personenbezogen über alle Depots
gerechnet und nicht je Report.

Trägt eine Datei **beide** Hälften (der eToro-Report enthält KAP-Werte *und* § 23/§ 22),
wird sie einfach beiden Listen übergeben — jede Hälfte wird genau einmal verbraucht:

```bash
python scripts/build_taxreport.py steuerdaten.json \
    --kap-result etoro.kap_result.json --krypto-result etoro.kap_result.json …
```

Wird sie nur einer Liste übergeben, warnt der Report und nennt, was ignoriert wurde.

Einbindung, mehrere Quellen werden addiert:

```bash
python scripts/build_taxreport.py steuerdaten.json \
    --kap-result depot.kap_result.json etoro.kap_result.json \
    --krypto-result koinly.krypto_result.json -o taxreport.json
```

Handgepflegte Werte in `steuerdaten.json` kommen **hinzu**, sie werden nicht ersetzt; ist
eine Kennzahl in beiden belegt, weist der Report das getrennt aus
(`anlagen.KAP.quellen`), damit eine Doppelerfassung auffällt.

## Ein neues Profil anlegen

```bash
# 1. Entwurf aus einem echten Report vorschlagen lassen
python scripts/profile_wizard.py neuer_report.pdf --id mein-broker

# 2. Entwurf schärfen: Regexe, Pflichtfelder, Summenabgleich, alle TODO auflösen
#    → scripts/profiles/mein-broker.json
#    → tests/fixtures/mein-broker.txt   (anonymisierter Ausschnitt)

# 3. Gegen den echten Report laufen lassen
python scripts/parse_broker.py neuer_report.pdf -o test.json

# 4. Test ergänzen und die Suite fahren
python3 tests/run_tests.py
```

Der Wizard extrahiert den Text, rät Erkennungsmuster, Tabellenbereiche, Spaltenzuordnung
und Summenmuster und **validiert seinen eigenen Vorschlag** gegen den Report, aus dem er
stammt. Was er nicht sicher zuordnen kann, schreibt er als `"TODO"` mit einem Kommentar —
und ein Profil mit `TODO` wird von der Engine abgelehnt. Das ist Absicht: der Wizard
liefert einen Startpunkt, keine fertige Anbindung.

Erfahrungsgemäß braucht **der Summenabgleich** die meiste Handarbeit. Der Wizard kann nur
eine Summe bestätigen, die der Spaltensumme aller erkannten Zeilen entspricht; weist ein
Report nur den steuerpflichtigen Teil oder einen Wert nach Gebühren aus, sagt er das und
lässt `vergleich` auf `TODO`. Ebenso bei Vorzeichen (deutsche Berichte drucken Verluste
positiv) und mehrdeutigen Datumsformaten.

## Anonymisieren von Fixtures

Fixtures gehören ins Repository, echte Steuerdaten nicht. Kopfzeile, zwei bis drei
Datenzeilen und die Summenzeile reichen. Beträge dürfen verfälscht werden — **solange
Summenzeile und Datenzeilen zueinander passen**, sonst schlägt der eigene Abgleich des
Profils fehl; der Wizard rechnet die Summenzeile deshalb neu, wenn er Zeilen weglässt.
Namen, IBANs, Kontonummern, Steuer-IDs, Adressen und E-Mail-Adressen raus. Der Wizard
redigiert automatisch und druckt, was er entfernt hat — das ersetzt aber nicht den eigenen
Blick auf die Datei vor dem Commit.

## Wartung

Broker ändern ihre Layouts ohne Ankündigung. Deshalb `geprueft_am` pflegen — und wenn ein
Report nicht mehr passt, **nicht** die Regexe aufweichen, bis irgendetwas matcht, sondern
ein zweites Profil mit eigener Erkennung anlegen (`…-2027`). Ein zu tolerantes Profil liest
falsche Spalten und meldet Erfolg; das ist schlimmer als ein sauberer Abbruch.

## Vorhandene Profile

| ID | Ergebnis | Eingabe | Status |
|---|---|---|---|
| `koinly-de` | krypto_vorberechnet | pdf | geprüft (DE und EN) |
| `etoro-de` | kap | pdf | geprüft |
| `kraken-ledger` | krypto_transaktionen | csv | geprüft |
| `coinbase` | krypto_transaktionen | csv | **ungeprüft** — `Convert` bewusst nicht abgebildet, das erhaltene Asset steht nur im Freitext |
| `bitpanda` | krypto_transaktionen | csv | **ungeprüft** — `transfer` bewusst nicht abgebildet (Verkauf, Eigentransfer oder Schenkung ist aus der Spalte nicht entscheidbar) |
| `binance` | krypto_transaktionen | csv | **ungeprüft** — der Export enthält **keine EUR-Spalte**, jede Transaktion wird `_needs_fmv` markiert; als Startpunkt brauchbar, nicht als fertige Anbindung |

Die drei ungeprüften Profile wurden gegen die **dokumentierten** Spaltenüberschriften
gebaut, nicht gegen echte Exporte. Vor dem ersten Einsatz mit einer eigenen Datei prüfen
und `geprueft_am` setzen. Spalten, deren Bedeutung nicht eindeutig dokumentiert ist, sind
absichtlich nicht abgebildet — die Pflichtfeldprüfung schlägt dann an, statt zu raten.
