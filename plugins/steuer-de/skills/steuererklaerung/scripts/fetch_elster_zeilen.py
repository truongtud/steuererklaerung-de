#!/usr/bin/env python3
"""
fetch_elster_zeilen.py — Entwurf fuer references/elster_zeilen.json aus einem von
Hand heruntergeladenen amtlichen Vordruck-PDF erzeugen.

**Anders als fetch_steuerwerte.py holt dieses Skript NICHTS aus dem Netz.** Das
Formular-Management-System des Bundes (formulare-bfinv.de) liefert seine PDFs nur
innerhalb einer per JavaScript aufgebauten Browsersitzung aus — ein einfacher
HTTP-GET liefert die HTML-Huelle der Single-Page-App, kein PDF (nachgepruefit:
curl mit und ohne Cookie-Jar liefert beide Male `Content-Type: text/html`, nie
ein PDF). Dritt-Spiegelungen sind keine verlaessliche Alternative: eine Stich-
probe unter amtsvordrucke.de lieferte beim Test eine Anlage KAP aus dem Jahr
2010 unter einer Adresse ohne jede Jahresangabe — unbrauchbar fuer ein Skript,
das das AKTUELLE Jahr treffen soll, und gefaehrlich, wenn es das still tut.

Vorgehen deshalb wie bei jeder anderen Bescheinigung in diesem Skill: das PDF
wird von Hand besorgt (Browser, formulare-bfinv.de, "<Anlage> zur
Einkommensteuererklaerung <Jahr>" bzw. "Einkommensteuererklaerung <Jahr> mit
allen Anlagen") und hier nur noch GELESEN.

Was das Skript liest: die deutschen Steuerformulare drucken jedes Eingabefeld
als Dreiklang aus sichtbarer Zeilennummer, Eingabekaestchen ("Betrag in ganzen
Euro" / ",-") und interner Kennziffer (dem Feldcode, den ELSTER intern benutzt).
In der extrahierten Textebene steht das als

    <Beschriftung des Feldes>
    <Zeile>
    ,-
    <Kennziffer>

bzw. bei Ja/Nein-Feldern als `<Beschriftung>\\n<Zeile>\\n<Kennziffer>\\n1=Ja`.
Das ist regelmaessig genug fuer eine Regex — aber ein Formularumbau (neues Jahr,
neue Anlage) kann das Layout jederzeit aendern. Deshalb: **kein Autoschreiben in
references/elster_zeilen.json.** Das Skript erzeugt neben der Ausgabe auf stdout
nur eine Entwurfsdatei; die Uebernahme in die kuratierte Referenz bleibt
Handarbeit mit Blick auf das PDF.

Aufruf:
    python3 scripts/fetch_elster_zeilen.py vordruck.pdf --jahr 2026
    python3 scripts/fetch_elster_zeilen.py est26.pdf --jahr 2026 --schreiben \\
        --out entwurf_2026.json

Ohne --schreiben wird nur der Diff gegen references/elster_zeilen.json gezeigt.
Mit --schreiben landet der Entwurf in --out (Vorgabe:
elster_zeilen_entwurf_<jahr>.json neben dem PDF) — niemals in
references/elster_zeilen.json selbst.

Benoetigt PyMuPDF (`pip install pymupdf`), dieselbe Bibliothek wie
scripts/parse_pdf.py und scripts/fetch_steuerwerte.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

HIER = os.path.dirname(os.path.abspath(__file__))
ELSTER_ZEILEN_JSON = os.path.join(HIER, "..", "references", "elster_zeilen.json")

# Kuerzel, wie sie im internen Formularkopf stehen ("2010AnlKAP051NET" ->
# Jahr 2010, Anlage "KAP", Seite 05), auf den vollen Anlagen-Namen dieses Skills.
# Unbekannte Kuerzel werden nicht geraten — sie bleiben None und damit sichtbar.
ANLAGEN_KUERZEL = {
    "KAP": "Anlage KAP",
    "N": "Anlage N",
    "SO": "Anlage SO",
    "V": "Anlage V",
    "S": "Anlage S",
    "G": "Anlage G",
    "VORSORG": "Anlage Vorsorgeaufwand",
    "SA": "Anlage Sonderausgaben",
    "AUSSGB": "Anlage Aussergewoehnliche Belastungen",
    "K": "Anlage Kind",
    "EST1A": "Hauptvordruck",
}

# Vollname im Fliesstext ("Anlage KAP", "zur Einkommensteuererklaerung" fuer den
# Hauptvordruck) als zweiter Erkennungsweg, falls der Formularkopf-Code fehlt
# oder sein Kuerzel nicht in ANLAGEN_KUERZEL steht.
_ANLAGE_FLIESSTEXT = [
    (re.compile(r"Anlage\s+KAP\b"), "Anlage KAP"),
    (re.compile(r"Anlage\s+SO\b"), "Anlage SO"),
    (re.compile(r"Anlage\s+Vorsorgeaufwand"), "Anlage Vorsorgeaufwand"),
    (re.compile(r"Anlage\s+Sonderausgaben"), "Anlage Sonderausgaben"),
    (re.compile(r"Anlage\s+Au(?:ß|ss)ergew(?:ö|oe)hnliche Belastungen"),
     "Anlage Aussergewoehnliche Belastungen"),
    (re.compile(r"Anlage\s+Kind\b"), "Anlage Kind"),
    (re.compile(r"Anlage\s+V\b"), "Anlage V"),
    (re.compile(r"Anlage\s+S\b"), "Anlage S"),
    (re.compile(r"Anlage\s+G\b"), "Anlage G"),
    (re.compile(r"Anlage\s+N\b"), "Anlage N"),
    (re.compile(r"Einkommensteuererkl(?:ä|ae)rung"), "Hauptvordruck"),
]

_KOPFCODE = re.compile(r"(?<!\d)(\d{4})Anl([A-Za-z]+)\d")

# <Beschriftung>\n<Zeile>\n,-\n<Kennziffer> — Betragsfeld mit vorgedrucktem
# Eingabekaestchen. Die Beschriftung wird NICHT gierig ueber mehrere Felder
# hinweg gefasst: sie endet an der vorigen Zeilennummer oder am Seitenanfang.
_BETRAGSFELD = re.compile(
    r"(?P<vor>[^\n]{0,300}?)\n(?P<zeile>\d{1,3})\n,-\n(?P<kennziffer>\d{2,4})\b")
# <Beschriftung>\n<Zeile>\n<Kennziffer>\n1=Ja — Ja/Nein-Ankreuzfeld.
_JANEIN_FELD = re.compile(
    r"(?P<vor>[^\n]{0,300}?)\n(?P<zeile>\d{1,3})\n(?P<kennziffer>\d{2,4})\n1=Ja")


def anlage_und_jahr_aus_text(text: str) -> tuple[Optional[str], Optional[int]]:
    """(Anlage, Jahr) aus dem Formularkopf — oder (None, None), wenn keiner der
    beiden Erkennungswege greift. Nie geraten: eine falsch zugeordnete Anlage
    waere schlimmer als eine unzugeordnete Seite."""
    m = _KOPFCODE.search(text)
    if m:
        jahr = int(m.group(1))
        anlage = ANLAGEN_KUERZEL.get(m.group(2).upper())
        if anlage:
            return anlage, jahr
    for muster, name in _ANLAGE_FLIESSTEXT:
        if muster.search(text):
            jahr_m = re.search(r"(20\d\d)", text)
            return name, (int(jahr_m.group(1)) if jahr_m else None)
    return None, None


def _bereinige_beschriftung(roh: str) -> str:
    """Zeilenumbrueche zu Leerzeichen, Mehrfach-Leerzeichen weg, Randmuell ab."""
    s = re.sub(r"\s+", " ", roh).strip(" \t-–—:")
    return s


def felder_aus_seitentext(text: str) -> list[dict]:
    """Alle erkennbaren Eingabefelder einer Formularseite: Liste von
    {zeile, kennziffer, bezeichnung, art}. Ohne jeden Treffer eine leere Liste —
    das ist ein normales Ergebnis fuer Anleitungsseiten, kein Fehler."""
    treffer = []
    besetzt = set()  # Textspannen, die ein Ja/Nein-Treffer schon verbraucht hat

    for m in _JANEIN_FELD.finditer(text):
        treffer.append({
            "zeile": m.group("zeile"),
            "kennziffer": m.group("kennziffer"),
            "bezeichnung": _bereinige_beschriftung(m.group("vor")),
            "art": "ja_nein",
        })
        besetzt.add((m.start(), m.end()))

    for m in _BETRAGSFELD.finditer(text):
        # Ueberschneidet sich der Fund mit einem bereits erkannten Ja/Nein-Feld
        # (moeglich, weil beide Muster auf "<Zahl>\n<Zahl>" enden), zaehlt er
        # nicht doppelt.
        if any(not (m.end() <= a or m.start() >= b) for a, b in besetzt):
            continue
        bez = _bereinige_beschriftung(m.group("vor"))
        if not bez:
            continue
        treffer.append({
            "zeile": m.group("zeile"),
            "kennziffer": m.group("kennziffer"),
            "bezeichnung": bez,
            "art": "betrag",
        })

    treffer.sort(key=lambda f: int(f["zeile"]) if f["zeile"].isdigit() else 0)
    return treffer


def seiten_aus_pdf(pfad: str) -> list[str]:
    try:
        import fitz  # PyMuPDF, dieselbe Bibliothek wie in parse_pdf.py
    except ImportError as e:
        raise SystemExit("Zum Lesen des Vordrucks fehlt PyMuPDF — "
                         "`pip install pymupdf`.") from e
    with fitz.open(pfad) as doc:
        return [seite.get_text() for seite in doc]


def entwurf_aus_pdf(pfad: str, jahr_vorgabe: Optional[int] = None,
                    anlage_vorgabe: Optional[str] = None) -> tuple[dict, list[str]]:
    """PDF -> ({anlage: [felder]}, Warnungen). Seiten ohne erkennbare Anlage
    werden gemeldet, nicht stillschweigend uebersprungen."""
    anlagen: dict[str, list[dict]] = {}
    warnungen: list[str] = []
    letzte_anlage = anlage_vorgabe
    for i, text in enumerate(seiten_aus_pdf(pfad), start=1):
        anlage, jahr = anlage_und_jahr_aus_text(text)
        if jahr_vorgabe and jahr and jahr != jahr_vorgabe:
            warnungen.append(
                f"Seite {i}: Formularkopf nennt {jahr}, erwartet wurde {jahr_vorgabe} "
                f"— falsches PDF fuer dieses Jahr?")
        anlage = anlage or letzte_anlage  # eine Anlage erstreckt sich oft über mehrere Seiten
        felder = felder_aus_seitentext(text)
        if not felder:
            continue
        if not anlage:
            warnungen.append(
                f"Seite {i}: {len(felder)} Feld(er) gefunden, aber keine Anlage erkannt "
                f"— mit --anlage erzwingen oder von Hand zuordnen.")
            continue
        letzte_anlage = anlage
        anlagen.setdefault(anlage, []).extend(felder)
    return anlagen, warnungen


def lade_referenz(pfad: str) -> dict:
    if not os.path.isfile(pfad):
        return {"schema": 1, "jahre": {}}
    with open(pfad, encoding="utf-8") as f:
        return json.load(f)


def diff_gegen_referenz(anlagen: dict, referenz: dict, jahr: int) -> list[str]:
    """Menschenlesbarer Unterschied zum aktuell hinterlegten Jahr — reine
    Anzeige, keine Grundlage fuer automatisches Schreiben."""
    zeilen = []
    hinterlegt = (referenz.get("jahre", {}).get(str(jahr)) or {}).get("anlagen", {})
    for anlage, felder in sorted(anlagen.items()):
        alt = {f["zeile"]: f["bezeichnung"] for f in hinterlegt.get(anlage, [])}
        neu = {f["zeile"]: f["bezeichnung"] for f in felder}
        for z in sorted(set(neu) - set(alt), key=lambda z: (len(z), z)):
            zeilen.append(f"  + {anlage} Z. {z}: {neu[z]!r} (neu im PDF)")
        for z in sorted(set(alt) - set(neu), key=lambda z: (len(z), z)):
            zeilen.append(f"  - {anlage} Z. {z}: {alt[z]!r} (in {jahr} bisher hinterlegt, im PDF nicht gefunden)")
        for z in sorted(set(alt) & set(neu), key=lambda z: (len(z), z)):
            if alt[z] != neu[z]:
                zeilen.append(f"  ~ {anlage} Z. {z}: {alt[z]!r} -> {neu[z]!r}")
    if not hinterlegt and anlagen:
        zeilen.append(f"  (fuer {jahr} war bisher nichts in {ELSTER_ZEILEN_JSON} hinterlegt)")
    return zeilen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Entwurf fuer references/elster_zeilen.json aus einem von Hand "
                    "heruntergeladenen amtlichen Vordruck-PDF erzeugen (kein Netzzugriff).")
    ap.add_argument("pdfs", nargs="+", help="ein oder mehrere Vordruck-PDFs desselben Jahres")
    ap.add_argument("--jahr", type=int, required=True, help="Steuerjahr, z. B. 2026")
    ap.add_argument("--anlage", help="Anlage erzwingen, falls der Formularkopf sie nicht "
                                     "hergibt (z. B. bei einem Einzel-Anlage-PDF ohne Kopfcode)")
    ap.add_argument("--json", default=ELSTER_ZEILEN_JSON,
                    help="kuratierte Referenz, gegen die verglichen wird (nur gelesen)")
    ap.add_argument("--schreiben", action="store_true",
                    help="Entwurfsdatei schreiben (niemals references/elster_zeilen.json selbst)")
    ap.add_argument("--out", help="Pfad der Entwurfsdatei (Vorgabe: "
                                  "elster_zeilen_entwurf_<jahr>.json neben dem ersten PDF)")
    args = ap.parse_args(argv)

    referenz = lade_referenz(args.json)
    gesamt_anlagen: dict[str, list[dict]] = {}
    gesamt_warnungen: list[str] = []

    for pdf in args.pdfs:
        if not os.path.isfile(pdf):
            print(f"FEHLER: {pdf} nicht gefunden.", file=sys.stderr)
            return 1
        print(f"Lese {pdf} …")
        anlagen, warnungen = entwurf_aus_pdf(pdf, args.jahr, args.anlage)
        for anlage, felder in anlagen.items():
            gesamt_anlagen.setdefault(anlage, []).extend(felder)
        gesamt_warnungen.extend(f"  {pdf}: {w}" for w in warnungen)

    if not gesamt_anlagen:
        print("\nKein einziges Feld erkannt — das Layout dieses PDFs weicht vom "
              "erwarteten Muster ab (Beschriftung/Zeile/,-/Kennziffer). Von Hand "
              "gegen den Vordruck lesen; dieses Skript rät nicht.", file=sys.stderr)
        for w in gesamt_warnungen:
            print(w, file=sys.stderr)
        return 1

    print(f"\nGefundene Anlagen: {', '.join(sorted(gesamt_anlagen))}")
    for anlage, felder in sorted(gesamt_anlagen.items()):
        print(f"\n{anlage} ({len(felder)} Feld(er)):")
        for f in felder:
            print(f"  Z. {f['zeile']:>3}  (Kennziffer {f['kennziffer']})  {f['bezeichnung']}")

    if gesamt_warnungen:
        print(f"\n{len(gesamt_warnungen)} Warnung(en):")
        for w in gesamt_warnungen:
            print(w)

    diff = diff_gegen_referenz(gesamt_anlagen, referenz, args.jahr)
    print(f"\nUnterschied zu {args.json} (Jahr {args.jahr}):")
    if diff:
        for z in diff:
            print(z)
    else:
        print("  keiner")

    print("\nDies ist ein automatischer ENTWURF aus einer Text-Heuristik — jede Zeile "
          "vor der Uebernahme gegen das PDF selbst pruefen. Siehe references/elster-zeilen.md.")

    if not args.schreiben:
        print("\nNichts geschrieben (--schreiben fehlt).")
        return 0

    out = args.out or str(Path(args.pdfs[0]).with_name(f"elster_zeilen_entwurf_{args.jahr}.json"))
    entwurf = {
        "schema": 1,
        "hinweis": "AUTOMATISCH ERZEUGTER ENTWURF (fetch_elster_zeilen.py) — ungeprueft. "
                  "Nicht direkt als references/elster_zeilen.json verwenden.",
        "jahr": args.jahr,
        "quelle_pdfs": [os.path.basename(p) for p in args.pdfs],
        "anlagen": gesamt_anlagen,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(entwurf, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\nEntwurf geschrieben: {out}")
    print(f"Nach Pruefung von Hand in {args.json} unter jahre.{args.jahr}.anlagen uebernehmen "
          f"und 'geprueft' auf das heutige Datum setzen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
