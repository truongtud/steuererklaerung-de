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

### Alle Optionen von `parse_broker.py`

| Option | Wirkung |
|---|---|
| `--list` | vorhandene Profile anzeigen; unfertige sind mit `[UNFERTIG: …]` markiert |
| `--profil ID` | Profil **wählen**. Die Erkennungsmuster werden trotzdem geprüft: passen sie nicht, wird abgebrochen — ein erzwungenes falsches Profil liest fremde Spalten und meldet Erfolg |
| `--profil-trotzdem` | hebt genau diese Prüfung auf. Notausgang für ein geändertes Layout, **nur nach Sichtprüfung des Originals**. Der Lauf schreibt dann eine Warnung an den Anfang von `warnungen` im Ergebnis-JSON, damit die Nicht-Bestätigung im Report ankommt und nicht im Terminal bleibt |
| `--year JAHR` | Steuerjahr überschreiben (sonst aus `jahr.muster`) |
| `--dateformat de\|en\|iso` | Datumsformat erzwingen: `de` = TT/MM/JJJJ, `en` = MM/TT/JJJJ. Nötig, wenn ein Report mehrdeutige Daten (Tag ≤ 12) enthält — die Engine bricht dann von selbst ab, statt ein Jahr zu verschieben |
| `-o DATEI` | Ausgabedatei; Standard ist `<name>.<krypto_result\|kap_result\|transactions>.json`, bewusst kein fester Name, damit ein zweiter Broker den ersten nicht überschreibt |
| `--profile-verzeichnis` | anderes Profilverzeichnis (Tests, Entwürfe außerhalb des Skills) |

Exit-Code 1 bei: unerkanntem Report, nicht passendem `--profil`, unfertigem Profil,
abweichendem **oder fehlendem** Summenabgleich, leerer Tabelle. In all diesen Fällen wird
**keine** Ausgabedatei geschrieben.

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
  "kennzahlen": { … },        // nur bei ergebnis "kap" — Pflicht, s. u.
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
| `kennzahlen` | nur `ergebnis: "kap"`: normierte Kennzahlen aus den Abschriftpfaden — **Pflicht**, s. u. |
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

**Null gelesene Zeilen sind ein Lesefehler, kein Ergebnis.** Extrahiert eine Tabelle keine
einzige Zeile, bricht der Lauf ab. Sonst käme ein vollständig aussehendes Ergebnis aus
lauter Nullen heraus, das von einem echten Report ohne Vorgänge nicht zu unterscheiden ist
— und ein Zeilenmuster, das nach einem Layout-Wechsel gar nicht mehr greift, sähe genauso
aus wie ein ruhiges Steuerjahr. Anbieter, die ihren Report auch leer ausliefern, bekommen
den Opt-out `"darf_leer_sein": true` an der betreffenden Tabelle (bzw. `csv.darf_leer_sein`
im CSV-Block). Kein mitgeliefertes Profil braucht ihn.

**Regex-Makros** in allen Mustern: `{NUM}` (Betrag in DE/EN mit allen Vorzeichenformen),
`{DT}` (Datum), `{VOR}` (Vorzeichen), `{HALTE}` (Kurz-/Langfristig, DE+EN).

### Weniger gebrauchte Felder auf einen Blick

| Feld | Wirkung |
|---|---|
| `tabellen[].typen` | `{kanonisches Feld: "betrag"\|"menge"\|"ganzzahl"\|"datum"\|"text"}` — überschreibt die Typableitung aus dem Feldnamen (Standard: `*_date`/`timestamp` → Datum, `amount`/`counter_amount` → Menge, `*_eur` → Betrag, sonst Text) |
| `tabellen[].suche` | `true` sucht das `zeile`-Muster irgendwo in der Zeile statt es zu verankern. Nur wenn die Zeile einen variablen Vorspann hat — ein unverankertes Muster trifft leichter das Falsche |
| `tabellen[].darf_leer_sein` | erlaubt 0 gelesene Zeilen (s. o.) |
| `tabellen[].notiz_suffix` | Text, der an das `note`-Feld jeder Veräußerung angehängt wird (`koinly-de`: „Quelle: Koinly") — macht im zusammengeführten Report sichtbar, aus welchem Tool eine Position stammt |
| `csv.ignoriere_asset` | Ticker (Großschreibung), deren Zeilen bewusst übersprungen werden; die Anzahl steht in den Warnungen. `bitpanda` schließt so EUR-Ein-/Auszahlungen aus, die steuerlich kein Vorgang sind |
| `csv.darf_leer_sein` | wie `tabellen[].darf_leer_sein`, für den CSV-Zweig |
| `werte[].typ` | `betrag` (Standard), `menge`, `ganzzahl`, `text` |
| `werte[].leer` | `"null"` schreibt den Pfad ausdrücklich als `null` ins Ergebnis, statt ihn wegzulassen |
| `summen[].quelle_pfad` | nur bei `art: "zeilen"`: woher die Soll-Zeilenzahl kommt (Standard `summen_basis.csv_datenzeilen`) |
| `summen[].bereich` | schränkt die Suche nach der Summenzeile auf einen benannten Abschnitt ein |
| `summen[].flach` | sucht im umbruchbereinigten Text — nötig bei über zwei Zeilen umbrochenen Summenbeschriftungen |
| `summen[].form` | `"betrag2"` verlangt zwei Nachkommastellen, damit keine Seiten- oder Jahreszahl als Summe gelesen wird |

Die Zahlennotation wird **nicht** je Eintrag gesetzt, sondern einmal für das ganze Profil
(`notation`); ein Report mischt DE- und EN-Notation nicht.

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

### `kennzahlen` — Pflicht für jedes `kap`-Profil

`werte` schreibt die **Abschrift** des Reports (`kap_zeilen.19`, `so_zeilen.47`, …).
Rechnen kann `build_taxreport.py` damit noch nicht: es braucht die zwölf normierten
Kennzahlen des `kap`-Schemas. Genau die baut der `kennzahlen`-Block — ohne ihn ist ein
`kap`-Profil nicht schreibbar.

Ein Eintrag ist ein Quellpfad, eine Liste von Quellpfaden (die addiert werden) oder ein
Objekt mit `vorzeichen`:

```json
"kennzahlen": {
  "kapitalertraege": ["kap_zeilen.7", "kap_zeilen.18", "kap_zeilen.19"],
  "gewinn_aktien":            { "quellen": "kap_zeilen.20", "vorzeichen": "positiv" },
  "gewinn_termingeschaefte":  { "quellen": "kap_zeilen.21", "vorzeichen": "positiv" },
  "verlust_aktien":           { "quellen": "kap_zeilen.23", "vorzeichen": "negativ" },
  "verlust_termingeschaefte": { "quellen": "kap_zeilen.24", "vorzeichen": "negativ" },
  "verluste_ohne_aktien":     { "quellen": "kap_zeilen.22", "vorzeichen": "negativ" },
  "verluste_ausfall":         { "quellen": "kap_zeilen.25", "vorzeichen": "negativ" },
  "anrechenbare_kest": "kap_zeilen.37",
  "einbehaltener_soli": "kap_zeilen.38",
  "einbehaltene_kirchensteuer": "kap_zeilen.39",
  "auslaendische_quellensteuer": "kap_zeilen.41",
  "fiktive_quellensteuer": "kap_zeilen.42"
}
```

(vollständig so in `scripts/profiles/etoro-de.json`)

- **Ein fehlender Quellpfad zählt als 0** — der Report weist diese Zeile eben nicht aus.
  Dass der Pfad überhaupt füllbar *wäre*, prüft `pruefe_profil` beim Laden: ein Tippfehler
  fällt dort auf und nicht erst als stille 0,00 € im Ergebnis.
- `vorzeichen` ist `"positiv"` oder `"negativ"` und beschreibt die Konvention des
  `kennzahlen`-Blocks (Gewinne positiv, Verluste negativ), nicht die des Reports. Trägt ein
  Verlust ein positives Vorzeichen — deutsche Bescheinigungen drucken ihn so —, wird er als
  Verlust angesetzt **und das gemeldet**; still korrigiert wird nichts. Ohne diese
  Normierung hebt eine Quelle mit umgekehrter Konvention die Verluste einer anderen auf.
- Das Ziel darf einen Punkt enthalten (`"paragraph_23.netto_ergebnis_eur"`); ohne Punkt
  landet es unter `kennzahlen.`.

**Der Brutto-/davon-Wächter schaut genau hier hin.** Aggregiert ein Eintrag — oder ein
`werte[].summiere_in` — eine Bruttozeile (7, 18, 19) zusammen mit einer davon-Zeile
(20–25) in **einen** Topf, wird das Profil beim Laden abgelehnt. Warum, steht unten unter
„Ausgabeschemata → `kap`".

### `summen` — das Sicherheitsnetz

```json
{ "label": "§ 23 Ergebnis", "art": "betrag",
  "muster": "Kapitalgewinne\\s+({NUM})",
  "vergleich": "summen_basis.veraeusserungen_gewinn_gesamt", "toleranz": "0.01" }
```

`art` ist `betrag`, `anzahl` (Anzahl der Veräußerungen — wird ohne € gedruckt) oder
`zeilen` (verarbeitete gegen vorhandene Datenzeilen, für CSVs ohne Summenzeile).

**Weicht der Wert ab, bricht der Lauf ab — und ein Muster, das nichts findet, ebenso.**
Beides endet in `PlausibilityError`, Exit-Code 1, **keine Ausgabedatei**. Der fehlende
Vergleichswert war früher nur eine Warnung; das war falsch. Ein Abgleich, der nicht
stattgefunden hat, ist kein bestandener Abgleich: die geparste Zahl ist dann durch nichts
gedeckt, und im Bericht stand trotzdem eine Zeile, die neben den grünen kaum auffiel.
Genau der Fall — Report-Layout geändert, Summenmuster greift nicht mehr — ist der, für den
das Sicherheitsnetz da ist.

Ein Profil **ohne** `summen` wird abgelehnt.

Vergleichspfade liefert die Engine unter `summen_basis`, u. a.
`veraeusserungen_gewinn_gesamt`, `anzahl_veraeusserungen`, `csv_datenzeilen`,
`verarbeitete_zeilen`.

#### Wenn der Report eine Summe wirklich nicht ausweist: `optional`

```json
{ "label": "Anlage SO Z. 47 (§ 23)", "muster": [ … ],
  "vergleich": "paragraph_23.netto_ergebnis_eur",
  "optional": true,
  "begruendung": "eToro ist in erster Linie Aktien-/CFD-Broker: enthaelt ein Depot keine privaten Veraeusserungsgeschaefte (kein Krypto-Spot), fehlt im Report der gesamte Anlage-SO-Block und damit auch jede Summenzeile dazu. …" }
```

`optional: true` lässt den Lauf ohne Vergleichswert durchgehen. Die `begruendung` ist
dabei **Pflicht** — ohne sie lehnt `pruefe_profil` das Profil ab. Grund: der Verzicht auf
eine Gegenprüfung ist eine inhaltliche Aussage über den Report („diese Summe existiert
dort nicht"), keine Bequemlichkeit beim Regex-Schreiben. Wer sie begründen muss, merkt,
wenn er sie nicht begründen kann.

Aus demselben Grund wird ein Profil abgelehnt, dessen `summen`-Einträge **alle** optional
sind: dann hat es kein Sicherheitsnetz mehr. Mindestens ein Abgleich muss verbindlich
sein.

Mitgeliefert nutzt das genau ein Eintrag — `scripts/profiles/etoro-de.json`, die
Anlage-SO-Z.-47-Summe. Ein reines Aktien-/CFD-Depot bei eToro erzeugt gar keinen
Anlage-SO-Block; der Wert wäre dann korrekt 0,00 €, ohne dass es etwas gegenzuprüfen gibt.
Verbindlich bleibt dort der Abgleich der Kapitalerträge (Anlage KAP).

Unsichtbar wird ein opt-out dabei nicht. Im Abgleichsbericht trägt die Zeile ein
`!!`-Präfix, und `parse_broker.py` wiederholt sie danach auf **stderr**:

```
  Abgleich (geparst vs. im Report ausgewiesen):
    Kapitalerträge (Anlage KAP Z. 7 + 18 + 19): geparst 2.000,00 € vs. Report 2.000,00 € …
    !!  Anlage SO Z. 47 (§ 23): geparst 640,00 € — OHNE GEGENPRÜFUNG (im Profil als 'optional' …
  ACHTUNG — ohne Gegenprüfung: Anlage SO Z. 47 (§ 23): …          ← auf stderr
```

Eine so gemeldete Zahl gehört in die Antwort an den Nutzer, nicht in die stille Ablage.

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

> **Schema und Engine tragen diese Dokumente, ein Profil dafür muss aber erst geschrieben
> werden.** Mitgeliefert ist bisher genau ein `kap`-Profil: `etoro-de`. Für die
> Erträgnisaufstellung der eigenen Bank existiert keines, und `parse_broker.py` rät
> nicht — ohne passendes Profil bricht es ab und listet auf, welche es geprüft hat.
> Der Weg dorthin steht unten unter „Ein neues Profil anlegen".

```json
{
  "steuerjahr": 2025,
  "quelle": "ertraegnisaufstellung-2025.pdf",
  "profil": "<id des selbst geschriebenen Profils>",
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

**`--transactions` nimmt genau eine Datei**, während `--krypto-result` und `--kap-result`
beliebig viele nehmen. Das ist kein Versehen: fertige Ergebnisse lassen sich addieren, rohe
Transaktionslisten nicht — FIFO braucht **eine** durchgehende Historie je Asset. Zwei
Börsen-CSVs also vorher zu einer Liste zusammenführen und die zusammengeführte übergeben.
Wer `--transactions` zusammen mit `--krypto-result` angibt, bekommt einen Hinweis, dass die
Transaktionsdatei nicht verwendet wurde.

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

**Er schreibt dabei zwei Dateien, und zwar ohne Rückfrage:**

| | Standardpfad | Option |
|---|---|---|
| Profil | `scripts/profiles/<id>.json` | `--out` |
| Fixture-Gerüst | `tests/fixtures/<id>.txt` | `--fixture` |

Diese Standardpfade werden **relativ zum Skill-Verzeichnis** aufgelöst, nicht zum aktuellen
Arbeitsverzeichnis — der Wizard schreibt also dorthin, wo die Engine später sucht, egal von
wo er aufgerufen wurde. (`--out`/`--fixture` gelten dagegen relativ zum Arbeitsverzeichnis.) Eine Existenzprüfung gibt es nicht: `--id koinly-de` **überschreibt das
mitgelieferte Koinly-Profil kommentarlos**. Also eine neue ID wählen, oder erst mit
`--dry-run` schauen, was herauskäme (druckt den Vorschlag, schreibt nichts).

Weitere Optionen: `--kind` erzwingt das Ausgabeschema (`auto` errät es), `--backend` und
`--ocr-lang` reichen an dieselbe PDF-Extraktion durch wie `parse_pdf.py`.

Erfahrungsgemäß braucht **der Summenabgleich** die meiste Handarbeit. Der Wizard kann nur
eine Summe bestätigen, die der Spaltensumme aller erkannten Zeilen entspricht; weist ein
Report nur den steuerpflichtigen Teil oder einen Wert nach Gebühren aus, sagt er das und
lässt `vergleich` auf `TODO`. Ebenso bei Vorzeichen (deutsche Berichte drucken Verluste
positiv) und mehrdeutigen Datumsformaten.

**Zirkuläre Abgleiche markiert er als `TODO` und meldet sie als Fehler.** Zirkulär heißt:
der `summen`-Eintrag liest seinen Vergleichswert aus derselben Report-Zeile, aus der
`werte` den geprüften Wert liest. Ein solcher Abgleich prüft nichts — er meldet für jeden
Report „Abweichung 0,00 €", auch für einen, dem der halbe Inhalt fehlt, und macht damit
genau die Garantie wertlos, auf der das Profil-Format ruht. Ein echter Abgleich stellt eine
über **mehrere** Zeilen geparste Aggregation einem im Report **unabhängig** ausgewiesenen
Gesamtwert gegenüber. Lässt sich die Zeilenherkunft nicht bestimmen, gilt der Eintrag
ebenfalls als zirkulär: bei einer Prüfung gegen Selbstbestätigung muss der Zweifel gegen
den Entwurf ausschlagen. Weist der Report überhaupt keinen unabhängigen Gesamtwert aus,
gehört das als `optional` samt `begruendung` ins Profil — nicht mit einem Selbstvergleich
überdeckt.

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
