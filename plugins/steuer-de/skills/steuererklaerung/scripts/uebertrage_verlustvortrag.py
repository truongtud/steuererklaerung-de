#!/usr/bin/env python3
"""
uebertrage_verlustvortrag.py — Verlustvorträge aus einem fertigen taxreport.json
in die steuerdaten.json des Folgejahres übernehmen.

`build_taxreport.py` weist am Ende jedes Laufs aus, was im Folgejahr an
Verlustvortrag zur Verfügung steht (`anlagen.KAP.verlustvortraege.aktien`,
`anlagen.KAP.verlustvortraege.allgemein`, `anlagen.SO.verlustvortrag_23_neu_gesamt`)
— aber nichts schreibt diesen Wert automatisch in die steuerdaten.json des
nächsten Jahres. Bislang musste er von Hand abgetippt werden, und ein einziger
Zahlendreher dabei verliert lautlos einen echten Abzug, ohne dass irgendein
Sicherheitsnetz in `build_taxreport.py` das bemerken könnte — dort kommt der
Vortrag ja als ganz normale Eingabe an, nicht anders als ein Tippfehler.

Wichtig zum Termingeschäfte-Topf: seit dem Jahressteuergesetz 2024 gibt es
keinen eigenen Verrechnungskreis für Termingeschäfte mehr — ein etwaiger
`verlustvortrag_termingeschaefte_vorjahr` fließt beim Bauen des Reports
vollständig in denselben Topf wie `verlustvortrag_allgemein_vorjahr`
(`vv_allg_gesamt = vv_allg_vorjahr + vv_termin_vorjahr` in build_taxreport.py).
Der hier übertragene Wert `verlustvortraege.allgemein` deckt das bereits mit ab.
Bliebe in der Zieldatei zusätzlich ein alter, von Hand stehen gelassener
`verlustvortrag_termingeschaefte_vorjahr` ungleich 0 erhalten, würde derselbe
Restbetrag im Folgejahr ein zweites Mal verrechnet — das Skript setzt dieses
Feld deshalb ausdrücklich auf "0", sobald es den allgemeinen Vortrag schreibt.

Wie jedes Pflege-Werkzeug in diesem Skill: **nie still überschreiben.** Steht in
der Zieldatei bereits ein von 0 verschiedener Wert, der vom hier ermittelten
abweicht, bricht der Lauf für dieses Feld ab (Rückgabecode 1) — jemand könnte
diesen Wert schon geprüft und von Hand korrigiert haben, etwa nach einem
abweichenden Steuerbescheid. `--force` erzwingt das Überschreiben trotzdem.

Aufruf:
    python3 scripts/uebertrage_verlustvortrag.py alt_taxreport.json steuerdaten_2026.json
    python3 scripts/uebertrage_verlustvortrag.py alt_taxreport.json steuerdaten_2026.json --schreiben

Ohne --schreiben wird nur gezeigt, was sich ändern würde.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal as D
from decimal import InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from steuerlib import q2  # noqa: E402


class UebertragungFehler(RuntimeError):
    """Die Eingabedateien reichen nicht aus, um sicher zu übertragen."""


def _decimal_oder(wert, feld: str) -> D:
    try:
        return D(str(wert))
    except (InvalidOperation, TypeError):
        raise UebertragungFehler(f"{feld}: {wert!r} ist kein gültiger Betrag.") from None


def lade_json(pfad: str, was: str) -> dict:
    try:
        with open(pfad, encoding="utf-8") as f:
            daten = json.load(f)
    except FileNotFoundError:
        raise UebertragungFehler(f"{was} nicht gefunden: {pfad}") from None
    except json.JSONDecodeError as e:
        raise UebertragungFehler(
            f"{was} ({pfad}) ist kein gültiges JSON (Zeile {e.lineno}): {e.msg}") from None
    if not isinstance(daten, dict):
        raise UebertragungFehler(f"{was} ({pfad}) enthält kein Objekt.")
    return daten


def _pfad_wert(daten: dict, *schluessel: str):
    cur = daten
    for k in schluessel:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def vortraege_aus_report(report: dict) -> dict[str, D]:
    """(anlage.feld) -> Betrag, der im Folgejahr als Vorjahres-Vortrag gilt."""
    kap = _pfad_wert(report, "anlagen", "KAP", "verlustvortraege")
    so = _pfad_wert(report, "anlagen", "SO")
    if kap is None or so is None:
        raise UebertragungFehler(
            "taxreport.json enthält nicht die erwarteten Felder "
            "(anlagen.KAP.verlustvortraege / anlagen.SO) — mit einer anderen "
            "build_taxreport.py-Version erzeugt?")
    fehlend = [k for k in ("aktien", "allgemein") if k not in kap]
    if "verlustvortrag_23_neu_gesamt" not in so:
        fehlend.append("SO.verlustvortrag_23_neu_gesamt")
    if fehlend:
        raise UebertragungFehler(
            f"taxreport.json: Feld(er) fehlen: {', '.join(fehlend)}")
    return {
        "anlage_kap.verlustvortrag_aktien_vorjahr": _decimal_oder(
            kap["aktien"], "anlagen.KAP.verlustvortraege.aktien"),
        "anlage_kap.verlustvortrag_allgemein_vorjahr": _decimal_oder(
            kap["allgemein"], "anlagen.KAP.verlustvortraege.allgemein"),
        "anlage_so.verlustvortrag_23_vorjahr": _decimal_oder(
            so["verlustvortrag_23_neu_gesamt"], "anlagen.SO.verlustvortrag_23_neu_gesamt"),
    }


# Zusätzlich zu den drei Übertragungen: dieses Feld wird ausdrücklich auf "0"
# gesetzt, sobald 'anlage_kap.verlustvortrag_allgemein_vorjahr' geschrieben wird
# — sonst würde ein dort stehen gebliebener Altwert ein zweites Mal verrechnet
# (siehe Modul-Docstring, JStG 2024).
_TERMINGESCHAEFTE_FELD = "anlage_kap.verlustvortrag_termingeschaefte_vorjahr"


def _setze(steuerdaten: dict, block: str, feld: str, wert: str) -> None:
    ziel = steuerdaten.setdefault(block, {})
    if not isinstance(ziel, dict):
        raise UebertragungFehler(f"steuerdaten.{block} ist kein Objekt — kann {feld} "
                                 "nicht eintragen.")
    ziel[feld] = wert


def plane_uebertragung(steuerdaten: dict, vortraege: dict[str, D], *, force: bool):
    """Gibt (aktionen, konflikte) zurück. 'aktionen' sind (pfad, alt, neu)-Tripel,
    die tatsächlich geschrieben würden; 'konflikte' die, die es ohne --force nicht
    werden."""
    aktionen, konflikte = [], []

    def pruefe(pfad: str, neu: D):
        block, feld = pfad.split(".", 1)
        alt_roh = _pfad_wert(steuerdaten, block, feld)
        alt = None if alt_roh in (None, "") else _decimal_oder(alt_roh, pfad)
        neu_str = str(q2(neu))
        if alt is not None and alt != 0 and q2(alt) != D(neu_str):
            konflikte.append((pfad, str(q2(alt)), neu_str))
        elif alt is not None and q2(alt) == D(neu_str):
            pass  # bereits übernommen — nichts zu tun, kein Konflikt
        else:
            aktionen.append((pfad, "—" if alt is None else str(q2(alt)), neu_str))

    for pfad, betrag in vortraege.items():
        pruefe(pfad, betrag)

    # Termingeschäfte-Feld: nur anfassen, wenn der allgemeine Vortrag geschrieben
    # UND das Feld dort noch einen von 0 verschiedenen Altwert traegt.
    termin_alt_roh = _pfad_wert(steuerdaten, "anlage_kap", "verlustvortrag_termingeschaefte_vorjahr")
    if termin_alt_roh not in (None, ""):
        termin_alt = _decimal_oder(termin_alt_roh, _TERMINGESCHAEFTE_FELD)
        wird_allgemein_geschrieben = any(
            p == "anlage_kap.verlustvortrag_allgemein_vorjahr" for p, _, _ in aktionen)
        if termin_alt != 0 and wird_allgemein_geschrieben:
            if force:
                aktionen.append((_TERMINGESCHAEFTE_FELD, str(q2(termin_alt)), "0.00"))
            else:
                konflikte.append((_TERMINGESCHAEFTE_FELD, str(q2(termin_alt)),
                                  "0.00 (bereits im allgemeinen Vortrag enthalten)"))

    if force:
        for pfad, alt, neu in list(konflikte):
            aktionen.append((pfad, alt, neu.split(" ", 1)[0]))
        konflikte = []
    return aktionen, konflikte


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Verlustvorträge aus einem fertigen taxreport.json in die "
                    "steuerdaten.json des Folgejahres übernehmen.")
    ap.add_argument("alter_taxreport", help="taxreport.json des Vorjahres")
    ap.add_argument("neue_steuerdaten", help="steuerdaten.json des Jahres, in das "
                                             "übertragen werden soll")
    ap.add_argument("--schreiben", action="store_true",
                    help="neue_steuerdaten.json tatsächlich ändern")
    ap.add_argument("--force", action="store_true",
                    help="bereits gesetzte, abweichende Werte trotzdem überschreiben")
    args = ap.parse_args(argv)

    try:
        report = lade_json(args.alter_taxreport, "taxreport.json")
        steuerdaten = lade_json(args.neue_steuerdaten, "steuerdaten.json")
        vortraege = vortraege_aus_report(report)
    except UebertragungFehler as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1

    alt_jahr = _pfad_wert(report, "meta", "steuerjahr")
    neu_jahr = steuerdaten.get("steuerjahr")
    if alt_jahr is not None and neu_jahr is not None and int(neu_jahr) != int(alt_jahr) + 1:
        print(f"WARNUNG: {args.alter_taxreport} ist für Steuerjahr {alt_jahr}, "
              f"{args.neue_steuerdaten} für {neu_jahr} — erwartet wurde "
              f"{int(alt_jahr) + 1}. Trotzdem fortgesetzt.", file=sys.stderr)

    aktionen, konflikte = plane_uebertragung(steuerdaten, vortraege, force=args.force)

    print(f"Verlustvortrag aus {args.alter_taxreport} (Steuerjahr {alt_jahr}) "
          f"-> {args.neue_steuerdaten} (Steuerjahr {neu_jahr}):\n")
    if aktionen:
        for pfad, alt, neu in aktionen:
            print(f"  {pfad}: {alt} -> {neu}")
    else:
        print("  (nichts zu übertragen — alle Werte stimmen bereits überein)")

    if konflikte:
        print(f"\n{len(konflikte)} Konflikt(e) — in der Zieldatei steht bereits ein "
              f"abweichender, von 0 verschiedener Wert (nicht überschrieben, "
              f"möglicherweise bewusst von Hand korrigiert):")
        for pfad, alt, neu in konflikte:
            print(f"  {pfad}: dort {alt}, ermittelt {neu} — mit --force erzwingen")

    if not args.schreiben:
        print("\nNichts geschrieben (--schreiben fehlt).")
        return 1 if konflikte else 0

    if konflikte:
        print("\nABBRUCH: Konflikte vorhanden — nichts geschrieben. Mit --force "
              "erzwingen oder die Zieldatei von Hand bereinigen.", file=sys.stderr)
        return 1

    if not aktionen:
        print("\nNichts geschrieben (keine Änderung nötig).")
        return 0

    for pfad, _alt, neu in aktionen:
        block, feld = pfad.split(".", 1)
        _setze(steuerdaten, block, feld, neu)

    with open(args.neue_steuerdaten, "w", encoding="utf-8") as f:
        json.dump(steuerdaten, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n{args.neue_steuerdaten} geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
