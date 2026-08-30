---
name: krypto-check
description: Beantwortet eine einzelne Frage zur deutschen Krypto-Besteuerung — Haltefrist, Freigrenze, Tausch, Staking, Verlustvortrag — ohne einen ganzen TaxReport zu bauen.
argument-hint: "[frage, z. B. BTC am 10.01.2023 gekauft, am 10.01.2024 verkauft]"
disable-model-invocation: true
license: MIT — NUR Orientierung, KEINE Steuerberatung.
---

# Krypto-Steuerfrage klären

Frage des Nutzers: **$ARGUMENTS**

Für die Regeln `references/krypto-steuer.md` im Skill `steuererklaerung` lesen. Das ist der
schnelle Weg: **keinen** vollständigen TaxReport bauen, keine `steuerdaten.json` anlegen.

Häufige Fälle und worauf es jeweils ankommt:

- **Haltefrist.** Taggenau nach § 108 AO / § 188 BGB — die Frist endet mit Ablauf des
  Jahrestages, der Verkauf am Jahrestag selbst ist noch steuerpflichtig. Schaltjahre und
  der 29.02. haben eigene Regeln. Für die Antwort `steuerlib.haltefrist_erfuellt` benutzen,
  statt Tage im Kopf zu zählen — genau dort entstehen die Fehler.
- **Freigrenze.** Kein Freibetrag: ab Erreichen ist der *gesamte* Gewinn steuerpflichtig.
  Sie gilt pro Person und Jahr über alle Broker — also nachfragen, ob es weitere Konten
  gibt, bevor „steuerfrei“ gesagt wird.
- **Tausch Krypto-zu-Krypto** ist eine Veräußerung zum EUR-Marktwert und zugleich eine
  Anschaffung.
- **Staking/Lending** ist § 22 Nr. 3 mit 256-€-Freigrenze, bewertet zum Zuflusswert; die
  Coins bekommen ein neues Anschaffungsdatum.
- **Verlustvortrag** nur gegen § 23-Gewinne, Freigrenze wird vor dem Vortrag geprüft.

Kurz und konkret antworten, mit der einschlägigen Norm. Wenn die Antwort von einer Angabe
abhängt, die der Nutzer nicht gemacht hat, genau danach fragen. Zum Schluss der Hinweis:
keine Steuerberatung, im Zweifel Steuerberater.
