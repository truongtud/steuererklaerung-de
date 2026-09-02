# Steuerbescheid prüfen — Referenz

Der Bescheid kommt, sieht plausibel aus, und niemand vergleicht ihn Zeile für Zeile mit
der eigenen Rechnung. Genau dort geht still Geld verloren. `scripts/pruefe_bescheid.py`
nimmt diesen Vergleich ab und rechnet die Frist aus.

```bash
S=plugins/steuer-de/skills/steuererklaerung/scripts
python3 $S/pruefe_bescheid.py bescheid.pdf --report taxreport.json -o bescheidpruefung.json
python3 $S/pruefe_bescheid.py --interaktiv --report taxreport.json
```

## Die Fristenkette

| Schritt | Fundstelle | Inhalt |
|---|---|---|
| Bekanntgabe | § 122 Abs. 2 Nr. 1 AO | **vierter** Tag nach Aufgabe zur Post (Inland) |
| Einspruchsfrist | § 355 Abs. 1 AO | ein Monat nach Bekanntgabe |
| Fristende | § 108 Abs. 3 AO | fällt es auf Sonnabend, Sonntag oder Feiertag → nächster Werktag |
| Fristbeginn/-ende im Detail | § 187 Abs. 1, § 188 Abs. 2 und 3 BGB | gibt es den Tag im Folgemonat nicht, endet die Frist an dessen letztem Tag |

> **Vier Tage, nicht drei.** Die Bekanntgabefiktion wurde durch das
> Postrechtsmodernisierungsgesetz von drei auf vier Tage verlängert. Wer noch mit drei
> rechnet, nennt eine um einen Tag zu kurze Einspruchsfrist.

**Feiertage.** Gerechnet werden die bundesweit gesetzlichen Feiertage — Neujahr,
Karfreitag, Ostermontag, 1. Mai, Christi Himmelfahrt, Pfingstmontag, 3. Oktober und
beide Weihnachtstage; die osterabhängigen über den Computus. **Länderfeiertage nicht**:
dafür bräuchte es das Bundesland und eine gepflegte Tabelle. Die Folge ist kontrolliert
und steht in jedem Ergebnis: ein übersehener Länderfeiertag macht das errechnete
Fristende **einen Tag zu früh, nie zu spät**. Wer sich danach richtet, ist auf der
sicheren Seite.

## Was geprüft wird

Position für Position gegen den Report: zu versteuerndes Einkommen, festgesetzte
Einkommensteuer, Solidaritätszuschlag, Kirchensteuer und die anrechenbaren Beträge.
Jede Abweichung bekommt eine Einordnung:

| Einordnung | Bedeutung |
|---|---|
| `stimmt überein` | keine Differenz |
| `möglicherweise erklärbar` | der Report weist für diese Richtung selbst eine Lücke aus (siehe Unsicherheitsbilanz) |
| `unerklärt` | keine bekannte Lücke erklärt sie — **das ist das Ergebnis der Prüfung** |
| `nicht vergleichbar` | eine der beiden Seiten führt die Position nicht |

Die Einordnung ist ein **Hinweis, keine rechtliche Bewertung**. Ob eine Abweichung einen
Einspruch trägt, entscheidet dieses Skript nicht.

## Warum nichts geraten wird

An diesem Dokument hängt eine Monatsfrist; ein still falsch gelesener Betrag wäre hier
teurer als überall sonst im Skill. Deshalb:

- Der Bescheid wird zuerst in **Abschnitte geschnitten**. „Kirchensteuer“ steht zweimal
  darin — in der Festsetzung und bei den Anrechnungsbeträgen. Ohne den Schnitt liest man
  den falschen Betrag, und die Zahl sieht dabei völlig plausibel aus.
- Was nicht **eindeutig** dasteht, bleibt leer und wird abgefragt, statt geraten.
- **Summenabgleich:** Anrechnung minus Festsetzung muss den ausgewiesenen Saldo ergeben.
  Geht das nicht auf, wurde mindestens eine Zahl falsch gelesen — dann bricht der Lauf
  ab. Dieselbe Mechanik sichert schon die Broker-Parser ab.

## Einspruchsentwurf

Aus den unerklärten Abweichungen entsteht ein Textentwurf mit Streitpunkten, Frist und
Fundstelle. Er beginnt mit „ENTWURF“ und ist genau das: eine Vorlage zum Prüfen und
Unterschreiben, kein fertiger Schriftsatz und keine Rechtsberatung.

## Datenschutz

Ein Bescheid enthält Steuer-ID, Steuernummer und das Finanzamt. `bescheid.pdf`,
`bescheid.txt`, `bescheid.json` und `bescheidpruefung.json` gehören in kein Repository
und in keine Cloud-Freigabe.

*In diesem Repository* sperrt die `.gitignore` sie zusätzlich in der Tiefe. Wer den Skill
installiert hat, arbeitet dagegen in seinem eigenen Verzeichnis — dort greift diese
Sperre nicht, und er muss selbst darauf achten.

Die Testfixture ist **synthetisch**: kein Original und kein geschwärztes Original, denn
ein geschwärzter Bescheid bliebe ein personenbezogenes Dokument.

## Noch offene Jahre

Jeder Report nennt die Veranlagungszeiträume, die noch offen sind. Wer nicht zur Abgabe
verpflichtet ist, kann freiwillig abgeben (Antragsveranlagung, § 46 Abs. 2 Nr. 8 EStG),
solange die vierjährige Festsetzungsfrist läuft (§ 169 Abs. 2 Nr. 2 i.V.m. § 170 Abs. 1
AO). Für zurückliegende Jahre mit einbehaltener Lohnsteuer lohnt der Blick besonders.
