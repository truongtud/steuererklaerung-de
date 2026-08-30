#!/usr/bin/env python3
"""
parse_etoro.py — eToro-Steuerbericht (PDF) -> Ergebnis-JSON.

Dünner Aufsatz auf die Profil-Engine: die Leseanweisung steht in
scripts/profiles/etoro-de.json, die Mechanik in scripts/brokerprofile.py.
Dieses Skript bleibt erhalten, damit bestehende Aufrufe weiterlaufen; für neue
Broker ist scripts/parse_broker.py der Einstieg.

eToro rechnet FIFO und die deutsche Einordnung selbst. Maßgeblich ist der
Summenausweis (i. d. R. Seite 2), der bereits auf ELSTER-Zeilen verweist:
  Anlage KAP:  Z7, Z18, Z19, Z20, Z21, Z22, Z23, Z24, Z25, Z37, Z38, Z39, Z41, Z42
  Anlage SO :  Z47 (private Veräußerungsgeschäfte, § 23 — i. d. R. Krypto-Spot),
               Z10 (Wertpapierleihe), Z11 (Staking)

Sicherheitsnetze (aus der Engine): Beträge über steuerlib.to_decimal (Unicode-Minus,
nachgestelltes Minus, Klammer-Notation behalten ihr Vorzeichen), Formprüfung
"zwei Nachkommastellen" gegen versehentlich eingesammelte Seitenzahlen, Abbruch
wenn KEINE 'Anlage … Zeile N'-Zuordnung gefunden wurde, und Abgleich gegen die
im Report ausgewiesenen Gesamtsummen.

Ausgabe: <pdf-name>.kap_result.json (kap_zeilen/kennzahlen + paragraph_23 /
paragraph_22_nr3 + 'etoro_kap' und 'elster_extra' für build_taxreport.py).

WICHTIG: Termingeschäfte (§ 20 Abs. 2) unterliegen einer beschränkten
Verlustverrechnung. Mehrere Broker/Tools für dasselbe Jahr zusammenführen.
KEINE Steuerberatung — gegen Original und Steuerbescheinigung prüfen.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brokerprofile as bp    # noqa: E402
import parse_broker as pbr    # noqa: E402
import steuerlib as sl        # noqa: E402

D = Decimal
PROFIL_ID = "etoro-de"
EPILOG = pbr.EPILOG


def profil():
    return bp.profil_nach_id(PROFIL_ID)


def _pdf_text(path: str) -> str:
    return bp.text_aus_datei(path)


def is_etoro(text: str) -> bool:
    return bp.passt(profil(), text)


def detect_year(text, override=None):
    if override:
        return int(override)
    return bp.jahr_aus_text(profil(), text)


def detect_person(text):
    """(Name, Depotnummer) — beides steht im Briefkopf, nicht im Summenausweis."""
    m = re.search(r"Guten Tag\s+(.+?),", text)
    md = re.search(r"Depot:\s*(\d+)", text)
    return (m.group(1).strip() if m else None), (md.group(1) if md else None)


def sieht_aus_wie_betrag(tok) -> bool:
    return bp.sieht_aus_wie_betrag(tok)


def extract_lines(text, *, locale_hint: str | None = None):
    """Alle '... Anlage <KAP|SO> Zeile <N>) <Betrag>' Zuordnungen.

    Rückgabe: ({(Anlage, Zeile): Decimal}, warnungen) — die Zeilen, die das Profil
    kennt und im Report tatsächlich gefunden hat.
    """
    result = bp.wende_an(profil(), text, quelle="etoro", strikt=False)
    out: dict[tuple[str, int], Decimal] = {}
    for anlage, schluessel in (("KAP", "kap_zeilen"), ("SO", "so_zeilen")):
        for zeile, wert in (result.get(schluessel) or {}).items():
            if wert is None:
                continue
            out[(anlage, int(zeile))] = sl.to_decimal(wert)
    return out, list(result.get("warnungen", []))


def build_result(text: str, year, *, quelle: str = "etoro", strikt: bool = True) -> dict:
    return bp.wende_an(profil(), text, jahr=year, quelle=quelle, strikt=strikt)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="eToro-Steuerbericht (PDF) -> kap_result.json",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf_path")
    ap.add_argument("--year")
    ap.add_argument("-o", "--out",
                    help="Ausgabedatei (Standard: <pdf-name>.kap_result.json — "
                         "bewusst kein fester Name, damit ein zweiter Broker den "
                         "ersten nicht überschreibt)")
    args = ap.parse_args()

    p = profil()
    text = _pdf_text(args.pdf_path)
    if not is_etoro(text):
        print("WARNUNG: sieht nicht nach eToro aus. Trotzdem versuchen ...",
              file=sys.stderr)
    year = detect_year(text, args.year)
    if not year:
        print("Steuerjahr nicht erkannt — bitte --year setzen.", file=sys.stderr)
        return 1
    out = args.out or pbr.standard_ausgabe(args.pdf_path, p)

    try:
        result = build_result(text, year, quelle=Path(args.pdf_path).name)
    except (sl.ParseError, sl.PlausibilityError) as e:
        print(f"ABBRUCH: {e}", file=sys.stderr)
        return 1

    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"eToro-Report {year} geparst -> {out}")
    if result.get("steuerpflichtiger_aus_report"):
        print(f"  Steuerpflichtiger: {result['steuerpflichtiger_aus_report']}")
    pbr.drucke_bericht(result, p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
