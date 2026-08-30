---
name: steuererklaerung
description: Startet eine vollständige deutsche Einkommensteuererklärung aus angehängten Broker-Reports und Einkommensdaten — liest die Reports ein, rechnet Krypto und Kapitalerträge, erzeugt HTML, PDF und das ELSTER-Feld-Mapping.
argument-hint: "[steuerjahr]"
disable-model-invocation: true
license: MIT — NUR Orientierung, KEINE Steuerberatung.
---

# Steuererklärung erstellen

Der Nutzer hat diesen Befehl bewusst aufgerufen. Argument (falls angegeben): **$ARGUMENTS**
— in aller Regel das Steuerjahr.

**Zuerst das Skill `steuererklaerung-de` laden** und dem dort beschriebenen Ablauf folgen
(Skill-Tool, oder dessen `SKILL.md` lesen). Diese Datei ersetzt ihn nicht, sie ist nur der
Einstieg; alle Pfade unten — `scripts/`, `assets/`, `references/` — liegen in jenem Skill.

Reihenfolge für diesen Befehl:

1. **Bestandsaufnahme.** Welche Dateien liegen vor — angehängt, im Arbeitsverzeichnis oder
   in einem verbundenen Ordner? Broker-Reports (PDF/CSV), Lohnsteuerbescheinigung, eine
   `steuerdaten.json` aus dem Vorjahr? Ohne Dateien: fragen, was vorliegt, und die
   unterstützten Eingabeformate aus der SKILL.md nennen.
2. **Steuerjahr klären**, falls nicht als Argument übergeben und nicht aus den Reports
   ableitbar.
3. **Reports einlesen** über `scripts/parse_broker.py`. Den Summenabgleich jedes Laufs
   **wörtlich weitergeben** — er ist das Ergebnis, nicht Beiwerk. Bricht ein Lauf ab, nicht
   umgehen, sondern erklären, was nicht zusammenpasst.
4. **Fehlende Angaben erfragen** statt eine JSON-Datei anzufordern: Veranlagung,
   Kirchensteuer, Bruttoarbeitslohn und einbehaltene Steuern, Werbungskosten,
   Vorsorgeaufwendungen, anrechenbare Kapitalertragsteuer, Verlustvorträge aus Vorjahren.
   In einem Rutsch fragen, nicht einzeln nachhaken. Die Vorlage aus `assets/` ausfüllen.
5. **Report bauen und exportieren.** Standardmäßig alle drei Formate (HTML, PDF, ELSTER),
   sofern der Nutzer nichts anderes sagt.
6. **Ergebnis kommunizieren** nach dem Abschnitt „Ergebnis kommunizieren" der SKILL.md:
   Kernzahlen, Auffälligkeiten, Saldo-Annahme zur Anlage KAP, Disclaimer. Die ausgefüllte
   `steuerdaten.json` mitliefern, damit sie im Folgejahr wiederverwendbar ist.

Die Warnungen aus dem Report vollständig weitergeben, nicht zusammenfassen und nicht
filtern. Sie sind der Grund, warum dieses Skill existiert.
