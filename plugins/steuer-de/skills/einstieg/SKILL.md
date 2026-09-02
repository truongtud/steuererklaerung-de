---
name: einstieg
description: Führt Schritt für Schritt in die Einkommensteuererklärung — klärt die Lebenssituation, sagt welche Anlagen betroffen sind und welche Unterlagen gebraucht werden, und legt eine passende steuerdaten.json an.
argument-hint: "[steuerjahr, z. B. 2024]"
disable-model-invocation: true
license: MIT — NUR Orientierung, KEINE Steuerberatung.
---

# Einstieg in die Einkommensteuererklärung

Steuerjahr (falls angegeben): **$ARGUMENTS** — sonst danach fragen.

Wer zum ersten Mal eine Steuererklärung macht, scheitert selten am Rechnen. Er scheitert
daran, nicht zu wissen, **welche Papiere** er braucht und **welche Anlagen** ihn
überhaupt betreffen. Genau das klärt dieser Befehl — und legt am Ende eine Startdatei an,
in der nur die Blöcke stehen, die zu diesem Menschen gehören.

**Haltung:** Das ist ein Gespräch, kein Formular. Wenige Fragen, in normaler Sprache,
eine nach der anderen. Wer nicht weiß, was gemeint ist, bekommt ein Beispiel statt einer
Paragraphenkette. Wo eine Antwort unsicher ist, lieber den Block aufnehmen — ein
überflüssiger leerer Block kostet nichts, ein fehlender kostet später eine ganze Runde.

## Schritt 1 — Steuerjahr und ob es sich lohnt

Zuerst das Jahr klären. Dann sagen, ob es noch offen ist:

```bash
S=plugins/steuer-de/skills/steuererklaerung/scripts
python3 -c "import sys; sys.path.insert(0,'$S'); import steuerlib as sl
print(sl.offene_veranlagungszeitraeume())"
```

Vier Jahre rückwirkend darf freiwillig abgegeben werden (§ 46 Abs. 2 Nr. 8 EStG,
Festsetzungsfrist § 169 Abs. 2 Nr. 2 i.V.m. § 170 Abs. 1 AO). **Wenn ein zurückliegendes
Jahr noch offen ist und dort Lohnsteuer einbehalten wurde, ausdrücklich darauf
hinweisen** — das ist für viele der greifbarste Grund anzufangen.

Auch kurz einordnen, ob überhaupt eine **Pflicht** zur Abgabe besteht (z. B. mehrere
Arbeitgeber gleichzeitig, Steuerklasse IV mit Faktor oder V/VI, Lohnersatzleistungen über
410 €, Nebeneinkünfte über 410 €) oder ob es eine freiwillige Abgabe wäre. Das ändert die
Frist, nicht das Vorgehen.

## Schritt 2 — Die Lebenssituation

Diese Fragen reichen. Einzeln stellen, nicht als Liste abfragen:

1. **Womit verdienst du dein Geld?** angestellt · selbständig · Gewerbe · Vermietung ·
   Rente — Mehrfachnennung ist normal.
2. **Verheiratet oder verpartnert?** Wenn ja: zusammen veranlagen? (In aller Regel
   günstiger — der Splittingtarif.)
3. **Kinder im Haushalt?** Wie viele?
4. **Kirchensteuerpflichtig?** Wenn ja: 8 % in Bayern und Baden-Württemberg, sonst 9 %.
5. **Depot, Bank, Kapitalerträge?** Auch wenn nichts einbehalten wurde.
6. **Krypto?** Wenn ja, den Hinweis geben, dass die **vollständige Historie über alle
   Jahre** gebraucht wird — FIFO braucht die Anschaffungen der Vorjahre.
7. **Eltern-, Arbeitslosen-, Kranken- oder Kurzarbeitergeld bezogen?**
8. **Handwerker im Haus, Putzhilfe, oder Schornsteinfeger und Treppenhausreinigung in der
   Nebenkostenabrechnung?** Danach wird fast nie von selbst gedacht, und es ist bares
   Geld: 20 % direkt von der Steuer (§ 35a).
9. **Größere Krankheits-, Pflege- oder Bestattungskosten?**

## Schritt 3 — Startdatei anlegen

```bash
S=plugins/steuer-de/skills/steuererklaerung/scripts
python3 $S/neue_steuerdaten.py --jahr 2024 \
    --taetigkeit angestellt --kinder 1 --kirchensteuer 9 \
    --kapital --handwerker -o steuerdaten.json
```

Flags nur setzen, was auch zutrifft. Das Skript nennt die betroffenen Anlagen, die
Unterlagen und die Frist — **diese Ausgabe vollständig weitergeben**, nicht
zusammenfassen. Die Unterlagenliste ist der halbe Nutzen dieses Befehls.

## Schritt 4 — Was jetzt zu tun ist

Klar sagen, was als Nächstes passiert:

1. Die genannten Unterlagen zusammensuchen.
2. Die Beträge in `steuerdaten.json` eintragen. Bei der **Lohnsteuerbescheinigung** die
   Nummern nennen: 3 Bruttoarbeitslohn, 4 Lohnsteuer, 5 Soli, 6 Kirchensteuer, sowie
   22a und 23a für die Rentenversicherungsanteile.
3. Dann `/steuererklaerung` für den ganzen Durchlauf.
4. Kommt später der Bescheid: `/bescheid-pruefen`.

## Zwei Fallen, die hier schon dazugehören

- **Vorsorgeaufwendungen:** Unter `basisversorgung` gehört der **Gesamtbeitrag** zur
  Rentenversicherung — Arbeitnehmer- *und* Arbeitgeberanteil zusammen (Nummern 22a und
  23a der Lohnsteuerbescheinigung) —, und der Arbeitgeberanteil zusätzlich in das eigene
  Feld. Wer dort nur seinen eigenen Anteil einträgt, bekommt am Ende null Abzug, und das
  sieht im Ergebnis nicht nach einem Fehler aus.
- **§ 35a:** Einzutragen ist der **Lohnanteil** der Rechnung, kein Material, und die
  Rechnung muss unbar bezahlt sein. Barzahlung erkennt das Finanzamt selbst mit Quittung
  nicht an.

## Datenschutz

`steuerdaten.json` enthält gleich echte Steuerdaten. Sie ist per `.gitignore` gesperrt und
gehört in kein Repository. Darauf einmal hinweisen, wenn die Datei angelegt ist.

## Keine Steuerberatung

Dieser Befehl ordnet ein und bereitet vor. Er ersetzt keine Beratung, und die verbindliche
Berechnung liefert ELSTER.
