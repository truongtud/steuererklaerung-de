#!/usr/bin/env python3
"""
parse_koinly.py — Koinly-Steuerbericht (PDF) -> Krypto-Ergebnis (krypto_result.json).

Dünner Aufsatz auf die Profil-Engine: die eigentliche Leseanweisung steht in
scripts/profiles/koinly-de.json, die Mechanik in scripts/brokerprofile.py.
Dieses Skript bleibt erhalten, damit bestehende Aufrufe weiterlaufen; für neue
Broker ist scripts/parse_broker.py der Einstieg.

Begründung des Ansatzes: Koinly hat FIFO bereits wallet-übergreifend gerechnet.
Der Report enthält je Veräußerung Kostenbasis, Erlös, Gewinn/Verlust und die
Kurz-/Langfristig-Einstufung. Diese werden direkt übernommen (autoritativer als
ein Neu-FIFO auf unvollständiger Historie). Zusätzlich extrahiert werden
Einnahmen (§ 22 Nr. 3), das Futures-Ergebnis (i. d. R. § 20 Abs. 2 -> Anlage KAP)
und Ausgaben.

Sicherheitsnetze (aus der Engine): alle Beträge über steuerlib.to_decimal
(DE *und* EN), Abgleich der geparsten Summen und der Anzahl Veräußerungen gegen
die im Report ausgewiesenen Werte, Zählung nicht zugeordneter Tabellenzeilen.

Ausgabe: <pdf-name>.krypto_result.json -> nutzbar mit
         build_taxreport.py steuerdaten.json --krypto-result <datei>.json

KEINE Steuerberatung. Werte gegen den Original-Report prüfen.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime as _dt
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brokerprofile as bp    # noqa: E402
import parse_broker as pbr    # noqa: E402
import steuerlib as sl        # noqa: E402

PROFIL_ID = "koinly-de"
EPILOG = pbr.EPILOG


def profil():
    return bp.profil_nach_id(PROFIL_ID)


def _pdf_text(path: str) -> str:
    """Volltext aller Seiten (pdfplumber, Fallback PyMuPDF)."""
    return bp.text_aus_datei(path)


def is_koinly(text: str) -> bool:
    return bp.passt(profil(), text)


def detect_year(text: str, override=None) -> int:
    if override:
        return int(override)
    jahr = bp.jahr_aus_text(profil(), text)
    return jahr if jahr else _dt.now().year


def build_result(text: str, year: int, *, quelle: str = "koinly",
                 dateformat: str | None = None, strikt: bool = True) -> dict:
    """Reporttext -> Ergebnis-JSON. Führt den Summenabgleich mit aus."""
    return bp.wende_an(profil(), text, jahr=year, quelle=quelle,
                       datum=dateformat, strikt=strikt)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Koinly-Steuerbericht (PDF) -> krypto_result.json",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf_path")
    ap.add_argument("--year", help="Steuerjahr überschreiben (sonst aus Report)")
    ap.add_argument("--dateformat", choices=["de", "en"],
                    help="Datumsformat des Reports erzwingen: de=TT/MM/JJJJ, "
                         "en=MM/TT/JJJJ (nötig bei mehrdeutigen Datumsangaben)")
    ap.add_argument("-o", "--out",
                    help="Ausgabedatei (Standard: <pdf-name>.krypto_result.json — "
                         "bewusst kein fester Name, damit ein zweiter Broker den "
                         "ersten nicht überschreibt)")
    args = ap.parse_args()

    p = profil()
    text = _pdf_text(args.pdf_path)
    if not is_koinly(text):
        print("WARNUNG: sieht nicht nach Koinly aus. Trotzdem versuchen ...",
              file=sys.stderr)
    year = detect_year(text, args.year)
    out = args.out or pbr.standard_ausgabe(args.pdf_path, p)

    try:
        result = build_result(text, year, quelle=Path(args.pdf_path).name,
                              dateformat=args.dateformat)
    except (sl.ParseError, sl.PlausibilityError) as e:
        print(f"ABBRUCH: {e}", file=sys.stderr)
        return 1

    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Koinly-Report {year} geparst -> {out}")
    pbr.drucke_bericht(result, p)
    ke = result.get("koinly_extra", {})
    print(f"  Futures (separat/Anlage KAP): {ke.get('futures_nettoergebnis_eur')} € | "
          f"Ausgaben: {ke.get('ausgaben_total_eur')} €")
    return 0


if __name__ == "__main__":
    sys.exit(main())
