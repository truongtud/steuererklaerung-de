---
name: bescheid-pruefen
description: Prüft einen Einkommensteuerbescheid gegen den eigenen TaxReport — Position für Position —, rechnet die Einspruchsfrist aus und entwirft bei unerklärten Abweichungen einen Einspruch.
argument-hint: "[pfad zu bescheid.pdf]"
disable-model-invocation: true
license: MIT — NUR Orientierung, KEINE Steuerberatung und KEINE Rechtsberatung.
---

# Steuerbescheid gegenprüfen

Zu prüfender Bescheid (falls angegeben): **$ARGUMENTS** — sonst nach der Datei fragen
oder `--interaktiv` anbieten.

Der Schritt, an dem sonst still Geld verloren geht: Der Bescheid kommt, sieht plausibel
aus, und niemand vergleicht ihn Zeile für Zeile mit der eigenen Rechnung. Dieser Befehl
nimmt den Vergleich ab.

**Zeitkritisch.** Der Einspruch ist innerhalb **eines Monats nach Bekanntgabe**
einzulegen (§ 355 Abs. 1 AO). Deshalb steht die Frist ganz vorn, nicht am Ende.

## Ablauf

1. **Bescheid und Report bestimmen.** Gebraucht werden beide: der Bescheid (PDF oder
   Text) und der `taxreport.json` desselben Steuerjahres. Stimmen die Jahre nicht
   überein, hier abbrechen und nachfragen — ein Bescheid gegen den falschen Report
   geprüft ist wertlos.

2. **Skript laufen lassen:**

   ```bash
   S=plugins/steuer-de/skills/steuererklaerung/scripts
   python3 $S/pruefe_bescheid.py bescheid.pdf --report taxreport.json \
           -o bescheidpruefung.json
   ```

   Ohne PDF oder wenn der Parser das Layout nicht erkennt:
   `python3 $S/pruefe_bescheid.py --interaktiv --report taxreport.json`

   Bricht das Skript ab, weil die Festsetzung nicht aufgeht, **nicht** die Zahlen von
   Hand „passend“ machen: dann wurde etwas falsch gelesen, und die fehlenden Werte
   gehören einzeln abgefragt.

3. **Die Frist zuerst nennen** — Bekanntgabetag, Fristende, verbleibende Tage. Dazu den
   Vorbehalt: gesetzliche Feiertage einzelner Länder sind nicht gerechnet, das Fristende
   kann deshalb einen Tag zu früh liegen, **nie zu spät**. Wer sich danach richtet, ist
   auf der sicheren Seite. Liegt das Fristende nah, das ausdrücklich sagen.

4. **Jede Position durchgehen**, auch die übereinstimmenden — eine Übereinstimmung ist
   ein Ergebnis. Zu jeder Abweichung sagen, in welche Richtung sie wirkt und was sie in
   Euro bedeutet.

5. **Unerklärte Abweichungen sind das Ergebnis.** „Möglicherweise erklärbar“ heißt nur,
   dass der Report für diese Richtung selbst eine Lücke ausweist — das ist ein Hinweis,
   keine rechtliche Bewertung. Bei jeder unerklärten Abweichung überlegen: fehlt dem
   Finanzamt eine Angabe, oder fehlt dem Report eine? Beides kommt vor, und die Antwort
   entscheidet, ob ein Einspruch oder eine berichtigte Erklärung das Richtige ist.

6. **Einspruchsentwurf anbieten**, wenn etwas unerklärt bleibt. Er steht im JSON unter
   `einspruchsentwurf` und beginnt mit „ENTWURF“. Ausdrücklich dazusagen: das ist eine
   Vorlage zum Prüfen und Unterschreiben, kein fertiger Schriftsatz. Ob eine Abweichung
   einen Einspruch trägt, entscheidet dieser Befehl nicht.

7. **Auch bei Übereinstimmung** einen Satz zum Ergebnis: Wurde der Bescheid geprüft und
   deckt er sich mit der eigenen Rechnung, ist das die Auskunft — dann muss niemand
   fristwahrend etwas tun.

## Datenschutz

Ein Bescheid enthält Steuer-ID, Steuernummer und das Finanzamt. `bescheid.pdf`,
`bescheid.txt`, `bescheid.json` und `bescheidpruefung.json` **nie** in ein Repository
committen und ihren Inhalt nicht weitergeben.

## Wenn kein Report vorliegt

Ohne `taxreport.json` gibt es nichts zu vergleichen. Dann zuerst `/steuererklaerung`
für dasselbe Jahr durchlaufen — der Bescheid ist der Gegenpart zu diesem Report, nicht
zu einer neu geschätzten Zahl.

## Hintergrund

`references/bescheid.md` im Skill `steuererklaerung` beschreibt die Fristenkette mit
allen Fundstellen (§ 122 Abs. 2 Nr. 1, § 355 Abs. 1, § 108 Abs. 3 AO), den Aufbau eines
Bescheids und warum das Skript nichts rät.
