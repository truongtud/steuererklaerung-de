---
name: einstieg
description: Der Startpunkt: sagt, welche Unterlagen für die Einkommensteuererklärung zusammenzusuchen sind und welche Anlagen betroffen sind — die Vorbereitung vor /steuererklaerung.
argument-hint: "[steuerjahr, z. B. 2024]"
disable-model-invocation: true
license: MIT — NUR Orientierung, KEINE Steuerberatung.
---

# Einstieg: welche Unterlagen brauche ich?

Steuerjahr (falls angegeben): **$ARGUMENTS** — sonst danach fragen.

Wer zum ersten Mal eine Steuererklärung macht, scheitert selten am Rechnen. Er scheitert
daran, nicht zu wissen, **welche Papiere** er überhaupt zusammensuchen muss. Genau das —
und nur das — klärt dieser Befehl.

**Was der Nutzer danach tut, ist eine einzige Sache: alle Papiere in einen Ordner legen
und `/steuererklaerung` aufrufen.** Er füllt keine Datei aus, er trägt keine Beträge ein,
er öffnet kein Formular. Das Einlesen, Rechnen und das Ausfüllen von ELSTER übernimmt der
Hauptskill.

## Schritt 0 — Den Nutzer einleiten

**Zuerst begrüßen und den Weg zeigen, bevor die erste Frage kommt.** Wer hier ankommt,
weiß meistens nicht, was auf ihn zukommt — und genau daran scheitern die meisten, bevor
sie angefangen haben. Also kurz, in eigenen Worten, ungefähr so:

> Wir machen das in drei Schritten. Ich stelle dir gleich ein paar Fragen zu deiner Lage
> — angestellt, verheiratet, Kinder, Depot. Daraus sage ich dir, **welche Papiere du
> zusammensuchen musst** und welche Anlagen dich betreffen. Dann legst du die Papiere in
> einen Ordner und rufst `/steuererklaerung` auf; ab da läuft es von selbst: einlesen,
> rechnen — und am Ende führe ich dich Zeile für Zeile durch ELSTER.
>
> Du füllst nichts aus, tippst keine Beträge ab und rechnest nichts.
> Die Fragen jetzt dauern zwei, drei Minuten.

Dazu gehört, was **nicht** passiert: nichts wird eingereicht, nichts verlässt den Rechner,
und zu einer Abgabe wird niemand gedrängt — bei freiwilliger Abgabe entscheidet der Nutzer
am Ende selbst, ob er sie abschickt.

Wer schon weiß, wie es läuft, sagt das — dann ohne Vorrede zu Schritt 1.

**Haltung:** Das ist ein Gespräch, kein Formular. Wenige Fragen in normaler Sprache, eine
nach der anderen. Wer nicht weiß, was gemeint ist, bekommt ein Beispiel statt einer
Paragraphenkette. Bei Unsicherheit lieber mit aufnehmen — ein Papier zu viel im Ordner
kostet nichts, ein fehlendes kostet eine ganze Runde.

## Schritt 1 — Steuerjahr, und ob es sich lohnt

Zuerst das Jahr klären, dann sagen, ob es noch offen ist:

```bash
S=plugins/steuer-de/skills/steuererklaerung/scripts
python3 -c "import sys; sys.path.insert(0,'$S'); import steuerlib as sl
print(sl.offene_veranlagungszeitraeume())"
```

Vier Jahre rückwirkend darf freiwillig abgegeben werden (Antragsveranlagung,
§ 46 Abs. 2 Nr. 8 EStG; Festsetzungsfrist § 169 Abs. 2 Nr. 2 i. V. m. § 170 Abs. 1 AO).
**Ist ein zurückliegendes Jahr noch offen und wurde dort Lohnsteuer einbehalten, ausdrücklich
darauf hinweisen** — für viele ist das der greifbarste Grund, überhaupt anzufangen.

Auch kurz einordnen, ob eine **Pflicht** zur Abgabe besteht (mehrere Arbeitgeber
gleichzeitig, Steuerklasse IV mit Faktor oder V/VI, Lohnersatzleistungen über 410 €,
Nebeneinkünfte über 410 €) oder ob es freiwillig wäre. Das ändert die Frist, nicht das
Vorgehen.

## Schritt 2 — Die Lebenssituation

Diese Fragen reichen. Einzeln stellen, nicht als Liste abfragen:

1. **Womit verdienst du dein Geld?** angestellt · selbständig · Gewerbe · Vermietung ·
   Rente — Mehrfachnennung ist normal.
2. **Verheiratet oder verpartnert?** Wenn ja: zusammen veranlagen? (In aller Regel
   günstiger — der Splittingtarif.)
3. **Kinder im Haushalt?** Wie viele?
4. **Kirchensteuerpflichtig?** 8 % in Bayern und Baden-Württemberg, sonst 9 %.
5. **Depot, Bank, Kapitalerträge?** Auch wenn nichts einbehalten wurde.
6. **Krypto?** Wenn ja: es wird die **vollständige Historie über alle Jahre** gebraucht,
   nicht nur das Steuerjahr — FIFO braucht die Anschaffungen der Vorjahre.
7. **Eltern-, Arbeitslosen-, Kranken- oder Kurzarbeitergeld bezogen?**
8. **Handwerker im Haus, Putzhilfe, oder Schornsteinfeger und Treppenhausreinigung in der
   Nebenkostenabrechnung?** Danach wird fast nie von selbst gedacht, und es ist bares
   Geld: 20 % direkt von der Steuer (§ 35a).
9. **Größere Krankheits-, Pflege- oder Bestattungskosten?**

## Schritt 3 — Die Unterlagenliste ausgeben

```bash
S=plugins/steuer-de/skills/steuererklaerung/scripts
python3 $S/neue_steuerdaten.py --jahr 2024 \
    --taetigkeit angestellt --kinder 1 --kirchensteuer 9 \
    --kapital --handwerker -o steuerdaten.json
```

Flags nur setzen, was zutrifft. Das Skript nennt die betroffenen Anlagen, **die Liste der
Unterlagen** und die Frist — diese Ausgabe vollständig weitergeben, nicht zusammenfassen.
Die Unterlagenliste ist das Ergebnis dieses Befehls.

Die dabei angelegte `steuerdaten.json` ist ein Nebenprodukt: sie merkt sich, welche
Anlagen den Nutzer betreffen. **Er füllt darin nichts aus** — das tut `/steuererklaerung`
aus seinen Unterlagen. Das gehört einmal klar gesagt, sonst fängt jemand an, darin zu
tippen.

## Schritt 4 — Was jetzt zu tun ist

Genau zwei Dinge, und so knapp sagen:

1. **Alle Papiere in einen Ordner legen** — Bescheinigungen, Broker-Reports, Rechnungen,
   alles aus der Liste. PDFs genügen; Scans gehen auch.
2. **`/steuererklaerung` aufrufen.** Der Befehl sortiert jedes Dokument selbst ein, liest
   die Beträge heraus, rechnet, erzeugt HTML, PDF und das ELSTER-Mapping — und führt
   danach Anlage für Anlage durch das Formular.

Kommt später der Bescheid: `/bescheid-pruefen`.

Zum Schluss ausdrücklich sagen, dass es hier aufhört und wo es weitergeht: sammeln, und
mit dem vollen Ordner `/steuererklaerung` aufrufen. Bleibt etwas unklar, führt der Weg
jederzeit hierher zurück.

## Wofür es kein Papier zum Einlesen gibt

Das meiste kommt aus den Bescheinigungen. Drei Dinge stehen in keinem Beleg, den ein
Profil lesen könnte — sie fragt `/steuererklaerung` am Ende ab, und es hilft, sie schon
jetzt bereitzulegen:

- **Werbungskosten**: Arbeitstage und einfache Entfernung zur Arbeit, Arbeitsmittel,
  Fortbildung, Bewerbungen, Umzug.
- **§ 35a**: die Handwerker- und Dienstleistungsrechnungen. Begünstigt ist nur der
  **Lohnanteil**, kein Material, und die Rechnung muss **unbar** bezahlt sein — Barzahlung
  erkennt das Finanzamt selbst mit Quittung nicht an.
- **Spenden** und die Stammdaten (Name, Steuer-Identifikationsnummer).

## Datenschutz

Die Unterlagen und die daraus gefüllte `steuerdaten.json` enthalten echte Steuerdaten —
Name, Steuer-Identifikationsnummer, Einkommen. Einmal darauf hinweisen: sie gehören in
kein Repository, in keine Cloud-Freigabe und in keinen fremden Chat.

## Keine Steuerberatung

Dieser Befehl ordnet ein und bereitet vor. Er ersetzt keine Beratung, und die verbindliche
Berechnung liefert ELSTER.
