#!/usr/bin/env python3
"""
parse_broker.py — ein Einstieg für jeden Broker und jede Börse.

    python scripts/parse_broker.py <report.pdf|csv> [--profil ID] [--year JAHR]
                                   [-o OUT] [--list]

Das Skript erkennt anhand der Profile in scripts/profiles/ selbst, welcher Report
vorliegt, wendet das passende Profil an und prüft das Ergebnis gegen die Summen,
die der Report selbst ausweist. Ein neuer Broker ist damit eine JSON-Datei plus
ein Test-Fixture, kein neues Skript (siehe references/broker-profile.md).

Abbruch (Exit-Code 1) bei:
  * unerkanntem Report — geraten wird nicht,
  * unfertigem Profil (fehlender Summenabgleich, fehlende Pflichtfelder, TODO),
  * abweichendem Summenabgleich — dann sind vermutlich Zeilen verlorengegangen.

KEINE Steuerberatung. Werte gegen den Original-Report prüfen.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brokerprofile as bp  # noqa: E402
import steuerlib as sl      # noqa: E402

EPILOG = """\
WICHTIG — Freigrenzen werden hier NICHT angewendet:
Die Freigrenzen nach § 23 Abs. 3 Satz 5 EStG (1.000 € ab 2024, davor 600 €) und
§ 22 Nr. 3 Satz 2 EStG (256 €) gelten pro Person und Kalenderjahr über ALLE Broker
und Tools hinweg. Würde jeder Report sie für sich anwenden, blieben zwei Ergebnisse
von je 800 € steuerfrei, obwohl ihre Summe (1.600 €) steuerpflichtig ist.
Dieses Skript liefert deshalb nur die Roh-Nettobeträge ("freigrenze_angewendet": false).
build_taxreport.py wendet die Freigrenze einmal auf die Summe an und akzeptiert dafür
mehrere --krypto-result-/--kap-result-Dateien.
"""

DATEIENDUNG = {
    "krypto_vorberechnet": "krypto_result",
    "krypto_transaktionen": "transactions",
    "kap": "kap_result",
}


def standard_ausgabe(eingabe: str, profil) -> str:
    """<stem>.<krypto_result|kap_result|transactions>.json.

    Bewusst kein fester Name: sonst überschreibt der zweite Broker den ersten.
    """
    art = DATEIENDUNG.get(profil.ergebnis, "result")
    return f"{Path(eingabe).with_suffix('').name}.{art}.json"


def drucke_bericht(result: dict, profil, ziel=None) -> None:
    """Abgleich und Kernzahlen — bei JEDEM Lauf, nicht nur im Fehlerfall."""
    aus = ziel or sys.stdout
    if profil.ungeprueft:
        print("\n" + "!" * 72, file=aus)
        print(f"!! ACHTUNG: Profil '{profil.id}' ist UNGEPRÜFT.", file=aus)
        print("!! Es wurde nie gegen einen echten Report dieses Anbieters validiert.",
              file=aus)
        print("!! Spaltenzuordnung, Vorzeichen und Summen Zeile für Zeile gegen das",
              file=aus)
        print("!! Original prüfen, bevor die Zahlen in eine Steuererklärung wandern.",
              file=aus)
        print("!" * 72 + "\n", file=aus)

    print(f"Profil: {profil.id} — {profil.label} "
          f"(geprüft_am {profil.geprueft_am or '—'})", file=aus)
    print("  Abgleich (geparst vs. im Report ausgewiesen):", file=aus)
    for zeile in result.get("abgleich", []):
        print(f"    {zeile}", file=aus)

    p23 = result.get("paragraph_23") or {}
    if p23.get("netto_ergebnis_eur") is not None:
        print(f"  § 23 netto (roh, ohne Freigrenze): {p23['netto_ergebnis_eur']} € "
              f"(Gewinne {p23.get('gewinn_eur')} €, Verluste {p23.get('verlust_eur')} €, "
              f"steuerfrei > 1 Jahr: {p23.get('steuerfrei_langfristig_eur')} €)", file=aus)
        if sl.to_decimal(p23.get("verlustvortrag_eur") or "0") > 0:
            print(f"  Verlustvortrag § 23: {p23['verlustvortrag_eur']} € "
                  f"(Verlustfeststellung beantragen)", file=aus)
    p22 = result.get("paragraph_22_nr3") or {}
    if p22.get("netto_ergebnis_eur") is not None:
        print(f"  § 22 Nr. 3 (roh, ohne Freigrenze): {p22['netto_ergebnis_eur']} €",
              file=aus)
    if result.get("kap_zeilen"):
        gesetzt = {z: w for z, w in result["kap_zeilen"].items()
                   if w not in (None, "0.00")}
        print(f"  Anlage KAP — belegte Zeilen: "
              f"{', '.join(f'Z.{z}: {w} €' for z, w in gesetzt.items()) or '—'}",
              file=aus)
    if result.get("transactions") is not None:
        sb = result.get("summen_basis", {})
        print(f"  Transaktionen: {len(result['transactions'])} aus "
              f"{sb.get('csv_datenzeilen', '?')} CSV-Zeile(n) "
              f"({sb.get('uebersprungene_zeilen', 0)} übersprungen, "
              f"{sb.get('nicht_zugeordnete_zeilen', 0)} nicht zugeordnet)", file=aus)
        offen = [t for t in result["transactions"] if t.get("_needs_fmv")]
        if offen:
            print(f"  {len(offen)} Transaktion(en) ohne EUR-Wert — Marktwert zum "
                  f"Zeitpunkt ergänzen, sonst rechnet FIFO mit 0 €.", file=aus)

    for w in result.get("warnungen", []):
        print(f"  WARNUNG: {w}", file=sys.stderr)
    print("  Freigrenzen NICHT angewendet — build_taxreport.py rechnet sie einmal "
          "auf die Summe aller Reports.", file=aus)


def liste_profile(verzeichnis=None) -> int:
    profile = bp.lade_profile(verzeichnis)
    if not profile:
        print("Keine Profile gefunden.", file=sys.stderr)
        return 1
    print(f"{'ID':<16} {'Ergebnis':<22} {'Ein':<4} {'geprüft am':<12} "
          f"{'Status':<10} Bezeichnung")
    for p in profile:
        probleme = bp.pruefe_profil(p)
        print(p.kurzzeile() + ("   [UNFERTIG: " + probleme[0] + "]" if probleme else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Broker-/Börsen-Report (PDF oder CSV) -> Ergebnis-JSON, "
                    "profilgesteuert und mit Summenabgleich.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", nargs="?", help="Report als PDF oder CSV")
    ap.add_argument("--profil", help="Profil-ID erzwingen (sonst automatische Erkennung)")
    ap.add_argument("--year", help="Steuerjahr überschreiben (sonst aus dem Report)")
    ap.add_argument("--dateformat", choices=["de", "en", "iso"],
                    help="Datumsformat erzwingen: de=TT/MM/JJJJ, en=MM/TT/JJJJ")
    ap.add_argument("-o", "--out",
                    help="Ausgabedatei (Standard: <name>.<krypto_result|kap_result|"
                         "transactions>.json — bewusst kein fester Name, damit ein "
                         "zweiter Broker den ersten nicht überschreibt)")
    ap.add_argument("--profile-verzeichnis", help="anderes Profilverzeichnis")
    ap.add_argument("--list", action="store_true",
                    help="verfügbare Profile anzeigen und beenden")
    args = ap.parse_args()

    if args.list:
        return liste_profile(args.profile_verzeichnis)
    if not args.report:
        ap.error("Bitte einen Report angeben (oder --list).")

    try:
        text = bp.text_aus_datei(args.report)
    except (OSError, ImportError) as e:
        print(f"ABBRUCH: {args.report} nicht lesbar: {e}", file=sys.stderr)
        return 1

    profile = bp.lade_profile(args.profile_verzeichnis)
    try:
        if args.profil:
            profil = next((p for p in profile if p.id == args.profil), None)
            if profil is None:
                print(f"ABBRUCH: Kein Profil mit der id {args.profil!r}. Verfügbar: "
                      + ", ".join(p.id for p in profile), file=sys.stderr)
                return 1
        else:
            profil = bp.erkenne(text, profile)
    except sl.ParseError as e:
        print(f"ABBRUCH: {e}", file=sys.stderr)
        return 1

    if profil is None:
        print("ABBRUCH: Kein Profil passt auf diesen Report — es wird NICHT geraten.\n"
              "Geprüfte Profile:", file=sys.stderr)
        for zeile in bp.erkennungs_bericht(text, profile):
            print(f"  {zeile}", file=sys.stderr)
        print("→ Passendes Profil mit --profil erzwingen oder ein neues anlegen "
              "(scripts/profile_wizard.py, references/broker-profile.md).",
              file=sys.stderr)
        return 1

    probleme = bp.pruefe_profil(profil)
    if probleme:
        print(f"ABBRUCH: Profil {profil.id!r} ist unfertig und wird nicht angewendet:",
              file=sys.stderr)
        for pr in probleme:
            print(f"  - {pr}", file=sys.stderr)
        return 1

    try:
        result = bp.wende_an(profil, text, jahr=args.year,
                             quelle=os.path.basename(args.report),
                             datum=args.dateformat)
    except (sl.ParseError, sl.PlausibilityError) as e:
        print(f"ABBRUCH: {e}", file=sys.stderr)
        return 1

    out = args.out or standard_ausgabe(args.report, profil)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"{args.report} gelesen -> {out}")
    drucke_bericht(result, profil)
    return 0


if __name__ == "__main__":
    sys.exit(main())
