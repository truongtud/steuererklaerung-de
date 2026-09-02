---
name: steuer-pruefen
description: Prüft einen bereits erstellten TaxReport auf Plausibilität — Warnungen, Summenabgleiche, Freigrenzen-Grenzfälle, offene Annahmen — bevor die Zahlen nach ELSTER wandern.
argument-hint: "[pfad zu taxreport.json]"
disable-model-invocation: true
license: MIT — NUR Orientierung, KEINE Steuerberatung.
---

# TaxReport gegenprüfen

Zu prüfender Report (falls angegeben): **$ARGUMENTS** — sonst den zuletzt erzeugten
`taxreport.json` im Arbeitsverzeichnis nehmen oder danach fragen.

Dieser Befehl rechnet nichts neu. Er ist der letzte Blick, bevor jemand Zahlen in ein
Formular tippt. Der Abschnitt „Plausibilitätsschritt“ in der `SKILL.md` des Skills `steuererklaerung`
beschreibt die Haltung dazu.

Durchgehen und **jeden Punkt beantworten**, auch die unauffälligen:

1. **`warnungen` vollständig vorlesen**, nicht zusammenfassen. Zu jeder Warnung sagen, was
   sie für die Zahl bedeutet und was der Nutzer tun müsste.
2. **`hinweise`** — insbesondere die Saldo-Annahme zur Anlage KAP (Zeilen 22–25 als
   davon-Zeilen). Den Nutzer ausdrücklich fragen, ob seine Bescheinigung brutto oder netto
   ausgewiesen ist; davon hängt ab, ob zu wenig Steuer ausgewiesen wird.
3. **Summenabgleiche der Quellen**: stimmen die geparsten Beträge mit den Broker-Reports
   überein, und wurde bei irgendeiner Quelle ohne Gegenprüfung gearbeitet
   (`ungeprueft`-Profil, `summen`-Eintrag mit `optional`)?
4. **Freigrenzen-Grenzfälle**: liegt § 23 knapp über oder unter 1.000 € (bis 2023: 600 €),
   § 22 Nr. 3 knapp um 256 €? Knapp darunter heißt: fehlt noch ein Konto, kippt das
   Ergebnis komplett.
5. **Vollständigkeit**: sind alle Börsen und Depots erfasst? § 23, § 22 Nr. 3 und
   § 20 Abs. 6 gelten personenbezogen — eine vergessene Quelle macht nicht nur ihren
   eigenen Betrag falsch, sondern die Freigrenzenprüfung aller anderen.
6. **Anschaffungshistorie**: gibt es Veräußerungen mit Kostenbasis 0 oder
   `acquisition_date: "UNBEKANNT"`?
7. **Haltefrist-Konflikte** zwischen Report-Label und Gesetz.
8. **Verlustvorträge**: wurden die aus Vorjahren eingetragen, und ist der neu festzustellende
   Vortrag für das Folgejahr notiert?
9. **Rechnerische Gegenprobe**: Summe der Einkünfte je Anlage gegen `summe_einkuenfte`,
   zvE gegen die Abzüge, Nachzahlung/Erstattung gegen Festsetzung minus einbehaltene
   Steuern. Nicht die Rechnung des Skripts nacherzählen, sondern selbst nachrechnen.

10. **Unsicherheitsbilanz** (`unsicherheit`): jeden Posten mit Richtung und
    Größenordnung vorlesen und die Gesamtrichtung nennen. Sie sagt, ob die Schätzung
    eher zu hoch oder zu niedrig liegt — ohne sie kann der Nutzer die Zahl nicht
    einordnen.

Am Ende eine klare Aussage: was ist belastbar, was muss der Nutzer noch klären, und was
gehört vor der Einreichung zum Steuerberater. Ist der Block `vorsorge` nicht in
`basisversorgung`, `kranken_pflege_basis` und `sonstige` gegliedert, konnte die
Höchstbetragsberechnung nach § 10 Abs. 3/4 nicht greifen — dann ist die Schätzung **zu
niedrig**, und das gehört in die Zusammenfassung.

Kommt später der Bescheid, führt `/bescheid-pruefen` diesen Report gegen ihn.
