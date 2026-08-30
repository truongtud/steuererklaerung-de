---
name: broker-profil
description: Bindet einen neuen Broker oder eine neue Börse an — erzeugt aus einem echten Report einen Profil-Entwurf, löst die TODO-Stellen auf und legt ein Test-Fixture an.
argument-hint: "[profil-id, z. B. trade-republic-de]"
disable-model-invocation: true
license: MIT — NUR Orientierung, KEINE Steuerberatung.
---

# Neues Broker-Profil anlegen

Gewünschte Profil-ID (falls angegeben): **$ARGUMENTS**

**Zuerst `references/broker-profile.md` im Skill `steuererklaerung` lesen** — dort stehen
Schema, Ausgabeschemata und Vorzeichenregeln. Alle Skripte unten liegen in dessen `scripts/`.

Ablauf:

1. **Report besorgen.** Ohne echten Report kein Profil — das ist keine Formalität, sondern
   die Lehre aus den bisherigen Fehlern dieses Skills. Ist keine Datei angehängt, danach
   fragen und auf die Anonymisierung hinweisen (Kopfzeile, zwei bis drei Datenzeilen und
   die Summenzeile genügen; Beträge dürfen verfälscht sein, solange sie zur Summenzeile
   passen).
2. **Zuerst prüfen, ob schon ein Profil passt**: `python3 scripts/parse_broker.py --list`
   und einen Erkennungsversuch mit der Datei. Ein bestehendes Profil zu erweitern ist
   besser als ein zweites danebenzustellen — außer die Layouts unterscheiden sich wirklich,
   dann ein eigenes Profil mit eigener Erkennung.
3. **Entwurf erzeugen**: `python3 scripts/profile_wizard.py <report> --id <id>`. Der Wizard
   schreibt zwei Dateien und überschreibt ohne Rückfrage — bei einer ID, die es schon gibt,
   erst `--dry-run`.
4. **TODO-Stellen auflösen.** Der Wizard markiert alles, was er nicht sicher zuordnen kann;
   ein Profil mit `TODO` wird von der Engine abgelehnt. Erfahrungsgemäß braucht der
   **Summenabgleich** die meiste Arbeit — und ein zirkulärer Abgleich (das Muster liest
   dieselbe Zeile, die es prüfen soll) ist keiner. Er muss eine geparste *Summe* gegen einen
   im Report unabhängig ausgewiesenen Gesamtwert vergleichen.
5. **Gegen den echten Report laufen lassen**: `python3 scripts/parse_broker.py <report>`.
   Ergebnis Zeile für Zeile gegen das Original prüfen, nicht nur den grünen Haken.
6. **Fixture prüfen und Test ergänzen**, dann `python3 tests/run_tests.py`. Das Fixture
   landet im Repository — die vom Wizard gemeldeten Restrisiken der Anonymisierung
   durchgehen, bevor es committet wird.
7. **`geprueft_am` setzen** — erst jetzt, nach Schritt 5. Vorher bleibt `status`
   auf `ungeprueft`.

Wenn eine Spalte mehrdeutig ist, sie **nicht** abbilden. Die Pflichtfeldprüfung schlägt dann
an, und das ist das gewünschte Verhalten: ein zu tolerantes Profil liest falsche Spalten und
meldet Erfolg — das ist schlimmer als ein sauberer Abbruch.
