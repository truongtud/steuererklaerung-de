#!/usr/bin/env python3
"""
pruefe_bescheid.py — den Steuerbescheid gegen den eigenen Report halten.

Der Schritt, an dem sonst still Geld verloren geht: Der Bescheid kommt, sieht
plausibel aus, und niemand vergleicht ihn Zeile für Zeile mit der eigenen
Rechnung. Dieses Skript liest den Bescheid, stellt ihn dem TaxReport gegenüber,
nennt jede Abweichung und rechnet die Einspruchsfrist aus.

    python3 scripts/pruefe_bescheid.py bescheid.pdf --report taxreport.json \\
            -o bescheidpruefung.json
    python3 scripts/pruefe_bescheid.py --interaktiv --report taxreport.json

**Geraten wird nichts.** Was der Parser nicht eindeutig findet, wird gefragt oder
bleibt leer; geht die Festsetzung nicht auf, bricht der Lauf ab. An diesem
Dokument hängt eine Monatsfrist — ein still falsch gelesener Betrag wäre hier
teurer als überall sonst im Skill.

Das Ergebnis ist eine Arbeitsgrundlage, keine Rechtsberatung: ob eine Abweichung
einen Einspruch trägt, entscheidet nicht dieses Skript.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal as D
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steuerlib as sl  # noqa: E402


class BescheidFehler(RuntimeError):
    """Der Bescheid konnte nicht sicher gelesen werden — es wird nichts geraten."""


# Ein Bescheid nennt manche Bezeichnung zweimal, „Kirchensteuer“ etwa in der
# Festsetzung und noch einmal bei den Anrechnungsbeträgen. Ohne den Abschnitt zu
# kennen liest man den falschen Betrag — deshalb wird zuerst geschnitten.
ABSCHNITTE = {
    "grundlagen": r"Besteuerungsgrundlagen",
    "festsetzung": r"Festsetzung",
    "anrechnung": r"Anrechnung von Steuerabzugsbetr(?:ä|ae)gen",
    "schluss": r"Rechtsbehelfsbelehrung",
}

FELDER = {
    "grundlagen": {
        "gesamtbetrag_der_einkuenfte": r"Gesamtbetrag der Eink(?:ü|ue)nfte",
        "zu_versteuerndes_einkommen": r"Zu versteuerndes Einkommen",
    },
    "festsetzung": {
        "einkommensteuer": r"Festgesetzte Einkommensteuer",
        "solidaritaetszuschlag": r"Solidarit(?:ä|ae)tszuschlag",
        "kirchensteuer": r"Kirchensteuer",
    },
    "anrechnung": {
        "lohnsteuer": r"Lohnsteuer",
        "kapitalertragsteuer": r"Kapitalertragsteuer",
        "kirchensteuer": r"Kirchensteuer",
    },
}

_BETRAG = r"(-?[\d.]+,\d{2})"


def _abschnitte(text: str) -> dict:
    """Den Bescheidtext in seine Abschnitte schneiden."""
    marken = []
    for name, muster in ABSCHNITTE.items():
        m = re.search(muster, text)
        if m:
            marken.append((m.start(), name))
    marken.sort()
    if not marken:
        raise BescheidFehler(
            "Der Text enthält keinen der erwarteten Abschnitte "
            "(Besteuerungsgrundlagen, Festsetzung, Anrechnung). Ist das ein "
            "Einkommensteuerbescheid?")
    teile = {}
    for i, (pos, name) in enumerate(marken):
        ende = marken[i + 1][0] if i + 1 < len(marken) else len(text)
        teile[name] = text[pos:ende]
    return teile


def _betrag_in(abschnitt: str, muster: str) -> Optional[D]:
    """Der Betrag am Ende der Zeile, die auf das Label passt. None, wenn die
    Zeile fehlt oder mehrdeutig ist."""
    treffer = re.findall(rf"{muster}[^\n]*?{_BETRAG}", abschnitt)
    if len(treffer) != 1:
        return None
    return sl.to_decimal(treffer[0], locale_hint="de")


def bescheid_aus_text(text: str) -> dict:
    """Einkommensteuerbescheid → Kennzahlen.

    Der Summenabgleich am Ende ist die eigentliche Absicherung: Festsetzung
    minus Anrechnung muss den ausgewiesenen Saldo ergeben. Stimmt das nicht,
    wurde mindestens eine Zahl falsch gelesen — dann wird abgebrochen.
    """
    teile = _abschnitte(text)

    jahr = re.search(r"Bescheid f(?:ü|ue)r (\d{4})", text)
    if not jahr:
        raise BescheidFehler("Kein Steuerjahr gefunden ('Bescheid für JJJJ').")
    datum = re.search(r"Datum:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text)
    if not datum:
        raise BescheidFehler("Kein Bescheiddatum gefunden ('Datum: TT.MM.JJJJ').")

    werte, fehlend = {}, []
    for abschnitt, felder in FELDER.items():
        werte[abschnitt] = {}
        for feld, muster in felder.items():
            wert = _betrag_in(teile.get(abschnitt, ""), muster)
            if wert is None:
                fehlend.append(f"{abschnitt}.{feld}")
            werte[abschnitt][feld] = wert

    saldo = _betrag_in(teile.get("anrechnung", ""), r"(?:Erstattung|Nachzahlung)")
    erstattung = "Erstattung" in teile.get("anrechnung", "")

    bescheid = {
        "steuerjahr": int(jahr.group(1)),
        "bescheiddatum": sl.parse_datetime(datum.group(1)).date(),
        "grundlagen": werte["grundlagen"],
        "festsetzung": werte["festsetzung"],
        "anrechnung": werte["anrechnung"],
        "saldo": saldo,
        "saldo_art": "Erstattung" if erstattung else "Nachzahlung",
        "fehlend": fehlend,
    }
    _pruefe_summen(bescheid)
    return bescheid


def _pruefe_summen(bescheid: dict) -> None:
    """Festsetzung − Anrechnung muss den ausgewiesenen Saldo ergeben."""
    fest = [v for v in bescheid["festsetzung"].values() if v is not None]
    anger = [v for v in bescheid["anrechnung"].values() if v is not None]
    saldo = bescheid["saldo"]
    if saldo is None or len(fest) < len(bescheid["festsetzung"]) \
            or len(anger) < len(bescheid["anrechnung"]):
        return  # unvollständig gelesen — der Aufrufer fragt nach
    erwartet = sum(anger) - sum(fest)
    tatsaechlich = saldo if bescheid["saldo_art"] == "Erstattung" else -saldo
    if erwartet != tatsaechlich:
        raise BescheidFehler(
            f"Die Festsetzung geht nicht auf: Anrechnung {sum(anger)} minus "
            f"Festsetzung {sum(fest)} ergibt {erwartet}, ausgewiesen ist aber "
            f"{tatsaechlich}. Mindestens ein Betrag wurde falsch gelesen — hier "
            f"wird nichts geraten.")


# ─────────────────────────────────────────────────────────────────────────────
# Vergleich
# ─────────────────────────────────────────────────────────────────────────────

def _num(wert) -> Optional[D]:
    if wert in (None, ""):
        return None
    return D(str(wert))


def vergleiche(report: dict, bescheid: dict) -> list:
    """Report gegen Bescheid, Position für Position.

    Die Einordnung ist ein **Hinweis, keine rechtliche Bewertung**: „möglicherweise
    erklärbar“ heißt nur, dass der Report für diese Richtung selbst eine Lücke
    ausweist. Ob eine Abweichung einen Einspruch trägt, entscheidet dieses Skript
    nicht.
    """
    b, erg = report.get("berechnung", {}), report.get("ergebnis", {})
    luecken = (report.get("unsicherheit") or {}).get("posten") or []

    positionen = [
        ("Zu versteuerndes Einkommen", _num(b.get("zu_versteuerndes_einkommen")),
         bescheid["grundlagen"].get("zu_versteuerndes_einkommen")),
        ("Festgesetzte Einkommensteuer", _num(erg.get("davon_einkommensteuer")),
         bescheid["festsetzung"].get("einkommensteuer")),
        ("Solidaritätszuschlag", _num(erg.get("davon_solidaritaetszuschlag")),
         bescheid["festsetzung"].get("solidaritaetszuschlag")),
        ("Kirchensteuer", _num(erg.get("davon_kirchensteuer")),
         bescheid["festsetzung"].get("kirchensteuer")),
        # anrechenbare_betraege ist im Report eine Aufschlüsselung mit 'summe'.
        ("Anrechenbare Beträge", _num((erg.get("anrechenbare_betraege") or {}).get("summe")),
         sum((v for v in bescheid["anrechnung"].values() if v is not None), D("0"))
         if any(v is not None for v in bescheid["anrechnung"].values()) else None),
    ]

    ergebnisse = []
    for name, aus_report, aus_bescheid in positionen:
        if aus_report is None or aus_bescheid is None:
            ergebnisse.append({"position": name, "report": aus_report,
                               "bescheid": aus_bescheid, "differenz": None,
                               "einordnung": "nicht vergleichbar",
                               "moegliche_ursachen": []})
            continue
        differenz = sl.q2(aus_bescheid - aus_report)
        # Der Bescheid setzt mehr an als der Report → der Report lag zu niedrig.
        richtung = "zu niedrig" if differenz > 0 else "zu hoch"
        ursachen = [p["posten"] for p in luecken
                    if differenz != 0 and p.get("richtung") == richtung]
        if differenz == 0:
            einordnung = "stimmt überein"
        elif ursachen:
            einordnung = "möglicherweise erklärbar"
        else:
            einordnung = "unerklärt"
        ergebnisse.append({"position": name, "report": aus_report,
                           "bescheid": aus_bescheid, "differenz": differenz,
                           "einordnung": einordnung, "moegliche_ursachen": ursachen})
    return ergebnisse


def fristen(bescheiddatum: date) -> dict:
    """Bekanntgabe und Einspruchsfrist, mit den Fundstellen."""
    bekannt = sl.bekanntgabe(bescheiddatum)
    ende = sl.einspruchsfrist_ende(bekannt)
    return {
        "bescheiddatum": bescheiddatum.isoformat(),
        "bekanntgabe": bekannt.isoformat(),
        "einspruchsfrist_ende": ende.isoformat(),
        "grundlage": "§ 122 Abs. 2 Nr. 1 AO (vierter Tag nach Aufgabe zur Post), "
                     "§ 355 Abs. 1 AO (ein Monat), § 108 Abs. 3 AO (Werktag)",
        "vorbehalt": "Gesetzliche Feiertage einzelner Länder sind nicht "
                     "berücksichtigt. Das errechnete Fristende kann dadurch einen "
                     "Tag zu früh liegen, nie zu spät — wer sich danach richtet, "
                     "ist auf der sicheren Seite.",
    }


def einspruchsentwurf(bescheid: dict, unerklaert: list, frist: dict) -> str:
    """Textentwurf zum Prüfen und Unterschreiben — kein fertiger Schriftsatz."""
    punkte = "\n".join(
        f"  {i}. {p['position']}: laut Bescheid {sl.fmt_eur(p['bescheid'])}, "
        f"nach eigener Berechnung {sl.fmt_eur(p['report'])} "
        f"(Abweichung {sl.fmt_eur(p['differenz'])})."
        for i, p in enumerate(unerklaert, 1)) or "  (keine unerklärten Abweichungen)"
    return f"""ENTWURF — vor dem Absenden prüfen und unterschreiben

An das Finanzamt
Betreff: Einspruch gegen den Einkommensteuerbescheid {bescheid['steuerjahr']}
         vom {bescheid['bescheiddatum'].strftime('%d.%m.%Y')}

hiermit lege ich gegen den oben genannten Bescheid Einspruch ein.

Begründung — folgende Positionen weichen von meiner Berechnung ab:

{punkte}

Ich bitte um Überprüfung und um einen geänderten Bescheid.

Mit freundlichen Grüßen


Hinweis zur Frist: Der Einspruch ist innerhalb eines Monats nach Bekanntgabe
einzulegen (§ 355 Abs. 1 AO). Bekanntgabe am {frist['bekanntgabe']}, Fristende
am {frist['einspruchsfrist_ende']}. {frist['vorbehalt']}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Eingabe und CLI
# ─────────────────────────────────────────────────────────────────────────────

def text_aus_pdf(pfad: str) -> str:
    try:
        import fitz  # PyMuPDF, dieselbe Bibliothek wie in parse_pdf.py
    except ImportError as e:
        raise BescheidFehler("Zum Lesen des PDF fehlt PyMuPDF — "
                             "`pip install pymupdf`, oder --interaktiv benutzen.") from e
    with fitz.open(pfad) as doc:
        return "\n".join(seite.get_text() for seite in doc)


def nachfragen(bescheid: dict, report: dict) -> None:
    """Was der Parser offengelassen hat, wird gefragt — nicht geraten."""
    for pfad in list(bescheid["fehlend"]):
        abschnitt, feld = pfad.split(".")
        antwort = input(f"  {abschnitt}.{feld} laut Bescheid (leer = unbekannt): ").strip()
        if antwort:
            bescheid[abschnitt][feld] = sl.to_decimal(antwort, locale_hint="de")
            bescheid["fehlend"].remove(pfad)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Steuerbescheid gegen den TaxReport prüfen und die "
                    "Einspruchsfrist ausrechnen")
    ap.add_argument("bescheid", nargs="?", help="bescheid.pdf oder .txt")
    ap.add_argument("--report", required=True, help="taxreport.json")
    ap.add_argument("--interaktiv", action="store_true",
                    help="Kennzahlen abfragen statt aus dem PDF lesen")
    ap.add_argument("-o", "--out", help="Ergebnis als JSON schreiben")
    args = ap.parse_args(argv)

    with open(args.report, encoding="utf-8") as f:
        report = json.load(f)

    try:
        if args.bescheid:
            roh = (text_aus_pdf(args.bescheid) if args.bescheid.lower().endswith(".pdf")
                   else open(args.bescheid, encoding="utf-8").read())
            bescheid = bescheid_aus_text(roh)
        elif args.interaktiv:
            bescheid = _leerer_bescheid(report)
        else:
            ap.error("entweder eine Bescheiddatei oder --interaktiv angeben")
        if bescheid["fehlend"] and (args.interaktiv or sys.stdin.isatty()):
            print("Nicht eindeutig gelesen — bitte nachtragen:")
            nachfragen(bescheid, report)
    except BescheidFehler as e:
        print(f"\nFEHLER: {e}", file=sys.stderr)
        return 1

    frist = fristen(bescheid["bescheiddatum"])
    zeilen = vergleiche(report, bescheid)
    unerklaert = [z for z in zeilen if z["einordnung"] == "unerklärt"]

    print(f"\nBescheid {bescheid['steuerjahr']} vom "
          f"{bescheid['bescheiddatum'].strftime('%d.%m.%Y')}")
    print(f"Bekanntgabe {frist['bekanntgabe']}, Einspruchsfrist bis "
          f"{frist['einspruchsfrist_ende']}\n")
    for z in zeilen:
        pfeil = "=" if z["differenz"] == 0 else "≠"
        print(f"  {pfeil} {z['position']:32} Report {sl.fmt_eur(z['report']):>14}   "
              f"Bescheid {sl.fmt_eur(z['bescheid']):>14}   {z['einordnung']}")
    if unerklaert:
        print(f"\n{len(unerklaert)} unerklärte Abweichung(en) — das ist das Ergebnis "
              f"dieser Prüfung.")
    else:
        print("\nKeine unerklärte Abweichung.")

    if args.out:
        ergebnis = {
            "steuerjahr": bescheid["steuerjahr"],
            "fristen": frist,
            "vergleich": [dict(z, report=str(z["report"]) if z["report"] is not None else None,
                               bescheid=str(z["bescheid"]) if z["bescheid"] is not None else None,
                               differenz=str(z["differenz"]) if z["differenz"] is not None else None)
                          for z in zeilen],
            "einspruchsentwurf": einspruchsentwurf(bescheid, unerklaert, frist),
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(ergebnis, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\n{args.out} geschrieben (enthält Steuerdaten — nicht committen).")
    return 0


def _leerer_bescheid(report: dict) -> dict:
    jahr = (report.get("meta") or {}).get("steuerjahr") or report.get("steuerjahr")
    heute = date.today()
    return {"steuerjahr": int(jahr) if jahr else heute.year - 1,
            "bescheiddatum": heute,
            "grundlagen": {k: None for k in FELDER["grundlagen"]},
            "festsetzung": {k: None for k in FELDER["festsetzung"]},
            "anrechnung": {k: None for k in FELDER["anrechnung"]},
            "saldo": None, "saldo_art": "Erstattung",
            "fehlend": [f"{a}.{f}" for a, fs in FELDER.items() for f in fs]}


if __name__ == "__main__":
    sys.exit(main())
