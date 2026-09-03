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
import subprocess
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


def bestimme_art_ausfuehrlich(pfad: str) -> tuple:
    """Was ist das für ein Dokument? → (art, kennung, meldungen).

    `art` ist einer von `bescheid`, `bescheinigung`, `broker`, `unlesbar` oder
    None. Die Meldungen sagen, **warum** — bei einem nicht erkannten Dokument
    hilft nur das weiter.

    Die Reihenfolge ist Absicht: der Steuerbescheid wird zuerst geprüft, weil er
    die Wörter der Bescheinigungen enthält und sonst als eine solche durchginge.
    """
    meldungen: list = []
    try:
        text = pbesch.text_aus_datei(pfad)
    except pbesch.BescheinigungFehler as e:
        return "unlesbar", None, [str(e)]
    except OSError as e:
        return "unlesbar", None, [f"Datei nicht lesbar: {e}"]

    if not text.strip():
        return "unlesbar", None, [
            "Kein Text gefunden. Bei einem Scan hilft OCR — dafür braucht es "
            "Tesseract mit deutschem Sprachpaket; siehe references/pdf-ingestion.md."]
    if len(text.strip()) < pbesch.MINDESTZEICHEN:
        meldungen.append(
            f"Nur {len(text.strip())} Zeichen erkannt — für ein Formular wenig. "
            f"Falls das ein Scan ist: mit OCR erneut versuchen, sonst können "
            f"Zeilen fehlen.")

    treffer = sum(1 for m in BESCHEID_MERKMALE if m.lower() in text.lower())
    if treffer >= 2:
        return "bescheid", "steuerbescheid", meldungen

    profil = pbesch.erkenne(text, pbesch.lade_profile())
    if profil is not None:
        return "bescheinigung", profil["id"], meldungen

    # Kein Treffer: sagen, welches Profil am nächsten dran war und was fehlte.
    meldungen += _knapp_verfehlt(text)

    broker = bp.erkenne(text)
    if broker is not None:
        return "broker", getattr(broker, "id", None) or str(broker), meldungen

    return None, None, meldungen


def _knapp_verfehlt(text: str) -> list:
    """Welches Bescheinigungsprofil war am nächsten — und welches Merkmal fehlte?"""
    klein = text.lower()
    beste = None
    for profil in pbesch.lade_profile():
        marker = profil.get("erkennung", [])
        gefunden = [m for m in marker if m.lower() in klein]
        fehlend = [m for m in marker if m.lower() not in klein]
        if gefunden and (beste is None or len(gefunden) > beste[0]):
            beste = (len(gefunden), profil["id"], fehlend)
    if beste and beste[2]:
        return [f"Am nächsten lag das Profil „{beste[1]}“; es fehlte: "
                f"{', '.join(beste[2])}."]
    return []


def bestimme_art(pfad: str) -> tuple:
    """Kurzform von bestimme_art_ausfuehrlich — (art, kennung)."""
    art, kennung, _ = bestimme_art_ausfuehrlich(pfad)
    return (None, None) if art == "unlesbar" else (art, kennung)


def _broker_einlesen(pfad: str) -> tuple:
    """parse_broker.py auf den Report loslassen. → (erfolg, meldungen).

    Als Unterprozess und nicht als Import: das ist genau der Aufruf, den der
    Nutzer sonst selbst tippen müsste, mitsamt seinem Summenabgleich und seinem
    Rückgabecode. Bricht er ab — etwa weil die geparsten Summen nicht zu den im
    Report ausgewiesenen passen —, wird das durchgereicht, nicht geglättet.
    """
    skript = os.path.join(HIER, "parse_broker.py")
    lauf = subprocess.run([sys.executable, skript, pfad],
                          capture_output=True, text=True)
    ausgabe = (lauf.stdout or "") + (lauf.stderr or "")
    zeilen = [z.strip() for z in ausgabe.splitlines() if z.strip()]
    if lauf.returncode != 0:
        return False, ["parse_broker.py ist abgebrochen — nichts übernommen:"] + zeilen[-6:]
    return True, zeilen[-6:]


def importiere(dateien, steuerdaten: dict, ueberschreiben: bool = False,
               broker_einlesen: bool = False) -> list:
    """Jede Datei einsortieren und verarbeiten. Gibt je Datei einen Bericht."""
    bericht = []
    for pfad in dateien:
        art, kennung, meldungen = bestimme_art_ausfuehrlich(pfad)
        eintrag = {"datei": os.path.basename(pfad), "art": art, "kennung": kennung,
                   "aenderungen": [], "meldungen": list(meldungen)}
        if art == "bescheinigung":
            text = pbesch.text_aus_datei(pfad)
            profil = pbesch.erkenne(text, pbesch.lade_profile())
            werte, weitere = pbesch.extrahiere(text, profil)
            eintrag["aenderungen"] = pbesch.fuelle(steuerdaten, werte, ueberschreiben)
            eintrag["meldungen"] += weitere
        elif art == "bescheid":
            eintrag["meldungen"].append(
                "Das ist ein Steuerbescheid, keine Eingabe für die Erklärung. Er "
                "gehört zu /bescheid-pruefen — dort wird er gegen den fertigen "
                "Report gehalten.")
        elif art == "broker":
            if broker_einlesen:
                erfolg, zeilen = _broker_einlesen(pfad)
                eintrag["meldungen"] += zeilen
                eintrag["broker_gelesen"] = erfolg
            else:
                eintrag["meldungen"].append(
                    f"Broker-/Börsenreport ({kennung}). Einlesen mit: "
                    f"python3 scripts/parse_broker.py {os.path.basename(pfad)}")
        elif art == "unlesbar":
            pass  # der Grund steht schon in den Meldungen
        else:
            eintrag["meldungen"].append(
                "Keinem Profil eindeutig zuzuordnen — nichts übernommen. Wenn es "
                "eine Bescheinigung ist, kann ein Profil in "
                "scripts/profiles/bescheinigungen/ ergänzt werden; für einen "
                "Broker hilft scripts/profile_wizard.py.")
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
    ap.add_argument("--ohne-broker", action="store_true",
                    help="Broker-Reports nur melden, nicht gleich einlesen")
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

    bericht = importiere(dateien, sd, args.ueberschreiben,
                         broker_einlesen=not args.ohne_broker)
    beantwortet: set = set()

    for e in bericht:
        kopf = {"bescheinigung": "Bescheinigung", "broker": "Broker-Report",
                "bescheid": "Steuerbescheid",
                "unlesbar": "nicht lesbar"}.get(e["art"], "nicht erkannt")
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
        print(f"\nIn keinem Dokument gefunden ({len(offen)} Felder) — danach fragen, "
              f"nicht raten:")
        for p in offen:
            print(f"  · {p}")
    else:
        print("\nAlle Felder der Vorlage sind belegt.")
    print("\nDie Datei enthält echte Steuerdaten — sie gehört in kein Repository "
          "und in keine Cloud-Freigabe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
