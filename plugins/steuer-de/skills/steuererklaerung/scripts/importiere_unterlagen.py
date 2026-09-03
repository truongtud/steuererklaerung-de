#!/usr/bin/env python3
"""
importiere_unterlagen.py — alle Papiere auf einmal hineinwerfen.

Der Nutzer legt seine Unterlagen in einen Ordner und ruft einen Befehl auf. Dieses
Skript entscheidet je Datei, was sie ist, und schickt sie an den passenden Leser:

  * **Bescheinigungen** (Lohnsteuer, Bank, Krankenkasse) → füllen `steuerdaten.json`
  * **Broker- und Börsenreports** → werden zu `*.transactions.json`,
    `*.krypto_result.json` oder `*.kap_result.json` für den Report
  * **Steuerbescheide** → gehören nicht hierher, sondern zu `/bescheid-pruefen`

    python3 scripts/importiere_unterlagen.py unterlagen/ --steuerdaten steuerdaten.json

**Nichts wird geraten.** Was keinem Profil eindeutig zuzuordnen ist, wird gemeldet
und liegen gelassen. Ein falsch einsortiertes Dokument wäre teurer als ein nicht
erkanntes: das nicht erkannte fällt auf, das falsch einsortierte nicht.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brokerprofile as bp  # noqa: E402
import parse_bescheinigung as pbesch  # noqa: E402

HIER = os.path.dirname(os.path.abspath(__file__))
VORLAGE = os.path.join(HIER, "..", "assets", "steuerdaten_vorlage.json")

# Ein Steuerbescheid enthält dieselben Wörter wie eine Bescheinigung, ist aber
# das Gegenstück zum fertigen Report — er darf nie in die Eingabedaten wandern.
BESCHEID_MERKMALE = ("Bescheid für", "Rechtsbehelfsbelehrung", "Festsetzung")

LESBAR = (".pdf", ".txt", ".csv")


def _text(pfad: str) -> str:
    try:
        return pbesch.text_aus_datei(pfad)
    except Exception:
        return ""


def bestimme_art(pfad: str) -> tuple:
    """Was ist das für ein Dokument? → (art, kennung) oder (None, None).

    Die Reihenfolge ist Absicht: der Steuerbescheid wird zuerst geprüft, weil er
    die Wörter der Bescheinigungen enthält und sonst als eine solche durchginge.
    """
    text = _text(pfad)
    if not text.strip():
        return None, None

    if sum(1 for m in BESCHEID_MERKMALE if m.lower() in text.lower()) >= 2:
        return "bescheid", "steuerbescheid"

    profil = pbesch.erkenne(text, pbesch.lade_profile())
    if profil is not None:
        return "bescheinigung", profil["id"]

    broker = bp.erkenne(text)
    if broker is not None:
        return "broker", getattr(broker, "id", None) or str(broker)

    return None, None


def importiere(dateien, steuerdaten: dict, ueberschreiben: bool = False) -> list:
    """Jede Datei einsortieren und verarbeiten. Gibt je Datei einen Bericht."""
    bericht = []
    for pfad in dateien:
        art, kennung = bestimme_art(pfad)
        eintrag = {"datei": os.path.basename(pfad), "art": art, "kennung": kennung,
                   "aenderungen": [], "meldungen": []}
        if art == "bescheinigung":
            text = _text(pfad)
            profil = pbesch.erkenne(text, pbesch.lade_profile())
            werte, meldungen = pbesch.extrahiere(text, profil)
            eintrag["aenderungen"] = pbesch.fuelle(steuerdaten, werte, ueberschreiben)
            eintrag["meldungen"] = meldungen
        elif art == "bescheid":
            eintrag["meldungen"] = [
                "Das ist ein Steuerbescheid, keine Eingabe für die Erklärung. Er "
                "gehört zu /bescheid-pruefen — dort wird er gegen den fertigen "
                "Report gehalten."]
        elif art == "broker":
            eintrag["meldungen"] = [
                f"Broker-/Börsenreport ({kennung}). Einlesen mit: "
                f"python3 scripts/parse_broker.py {os.path.basename(pfad)}"]
        else:
            eintrag["meldungen"] = [
                "Keinem Profil eindeutig zuzuordnen — nichts übernommen. Wenn es "
                "eine Bescheinigung ist, kann ein Profil in "
                "scripts/profiles/bescheinigungen/ ergänzt werden; für einen "
                "Broker hilft scripts/profile_wizard.py."]
        bericht.append(eintrag)
    return bericht


def _sammle(pfade) -> list:
    dateien = []
    for p in pfade:
        if os.path.isdir(p):
            dateien += [os.path.join(p, n) for n in sorted(os.listdir(p))
                        if n.lower().endswith(LESBAR)]
        else:
            dateien.append(p)
    return dateien


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Alle Unterlagen einlesen und die Steuerdaten daraus füllen")
    ap.add_argument("pfade", nargs="+", help="Dateien oder ein Ordner")
    ap.add_argument("--steuerdaten", default="steuerdaten.json")
    ap.add_argument("--ueberschreiben", action="store_true")
    args = ap.parse_args(argv)

    if os.path.exists(args.steuerdaten):
        with open(args.steuerdaten, encoding="utf-8") as f:
            sd = json.load(f)
    else:
        with open(os.path.normpath(VORLAGE), encoding="utf-8") as f:
            sd = json.load(f)
        print(f"{args.steuerdaten} gibt es noch nicht — aus der Vorlage angelegt.\n")

    dateien = _sammle(args.pfade)
    if not dateien:
        print("Keine lesbaren Dateien gefunden (.pdf, .txt, .csv).", file=sys.stderr)
        return 1

    bericht = importiere(dateien, sd, args.ueberschreiben)
    beantwortet: set = set()

    for e in bericht:
        kopf = {"bescheinigung": "Bescheinigung", "broker": "Broker-Report",
                "bescheid": "Steuerbescheid"}.get(e["art"], "nicht erkannt")
        print(f"{e['datei']} — {kopf}" + (f" ({e['kennung']})" if e["kennung"] else ""))
        for a in e["aenderungen"]:
            print(f"   {a}")
        for m in e["meldungen"]:
            print(f"   ! {m}")
        print()

    for e in bericht:
        for a in e["aenderungen"]:
            if " = " in a:
                beantwortet.add(a.split(" = ")[0])

    with open(args.steuerdaten, "w", encoding="utf-8") as f:
        json.dump(sd, f, ensure_ascii=False, indent=2)
        f.write("\n")

    offen = pbesch.fehlende_felder(sd, beantwortet)
    nicht_erkannt = [e["datei"] for e in bericht if e["art"] is None]
    broker = [e["datei"] for e in bericht if e["art"] == "broker"]

    print(f"{args.steuerdaten} geschrieben.")
    if broker:
        print(f"\nNoch einzulesen (Broker/Börse): {', '.join(broker)} — "
              f"mit scripts/parse_broker.py, das Ergebnis geht per --krypto-result "
              f"bzw. --kap-result in den Report.")
    if nicht_erkannt:
        print(f"\nNicht erkannt: {', '.join(nicht_erkannt)}")
    if offen:
        print(f"\nNoch offen ({len(offen)} Felder) — danach fragen, nicht raten:")
        for p in offen:
            print(f"  · {p}")
    else:
        print("\nAlle Felder der Vorlage sind belegt.")
    print("\nDie Datei enthält echte Steuerdaten — sie gehört in kein Repository "
          "und in keine Cloud-Freigabe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
