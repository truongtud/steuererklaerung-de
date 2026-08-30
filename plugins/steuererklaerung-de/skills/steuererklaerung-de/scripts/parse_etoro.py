#!/usr/bin/env python3
"""
parse_etoro.py — Liest einen eToro-Steuerbericht (PDF) und extrahiert den
Summenausweis (i. d. R. Seite 2), der bereits nach deutschem Steuerrecht
klassifiziert ist und direkt auf ELSTER-Zeilen verweist.

eToro rechnet FIFO und die deutsche Einordnung selbst. Maßgeblich ist der
Summenausweis — die Einzeltransaktionen (oft viele Seiten) müssen dafür nicht
zerlegt werden. Extrahiert werden die Werte je Anlage/Zeile:
  Anlage KAP:  Z7, Z18, Z19, Z20, Z21, Z22, Z23, Z24, Z25, Z37, Z38, Z41, Z42
  Anlage SO :  Z47 (private Veräußerungsgeschäfte, § 23 — i. d. R. Krypto-Spot),
               Z10 (Wertpapierleihe), Z11 (Staking)

Sicherheitsnetze (ohne sie ist ein kaputter Parse von einem echten Null-Report
nicht zu unterscheiden):
  * Beträge laufen über steuerlib.to_decimal — Unicode-Minus, nachgestelltes Minus
    und Klammer-Notation behalten ihr Vorzeichen; ein Verlust wird nicht zu 0,00.
  * Der gefundene Betrag muss wie ein Betrag aussehen (Nachkommastellen), damit
    keine Seitenzahl aus derselben Zeile als Wert übernommen wird.
  * Wurde KEINE einzige 'Anlage … Zeile N'-Zuordnung gefunden, bricht das Skript
    mit Exit-Code 1 ab, statt lauter Nullen zu melden.
  * Im Report ausgewiesene Gesamtsummen werden gegen die geparsten Summen
    abgeglichen (steuerlib.pruefe_summen).

Ausgabe: <pdf-name>.krypto_result.json (paragraph_23 / paragraph_22_nr3 +
'etoro_kap' und 'elster_extra' zur Übernahme in build_taxreport.py).

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
import steuerlib as sl  # noqa: E402

D = Decimal

EPILOG = """\
WICHTIG — Freigrenzen werden hier NICHT angewendet:
Die Freigrenzen nach § 23 Abs. 3 Satz 5 EStG (1.000 € ab 2024, davor 600 €) und
§ 22 Nr. 3 Satz 2 EStG (256 €) gelten pro Person und Kalenderjahr über ALLE Broker
und Tools hinweg. Würde jeder Report sie für sich anwenden, blieben zwei Ergebnisse
von je 800 € steuerfrei, obwohl ihre Summe (1.600 €) steuerpflichtig ist.
Dieses Skript liefert deshalb nur die Roh-Nettobeträge ("freigrenze_angewendet": false).
build_taxreport.py wendet die Freigrenze einmal auf die Summe an und akzeptiert dafür
mehrere --krypto-result-Dateien.
"""


# ───────────────────────────────────────────────────────── Textgewinnung ──────
def _pdf_text(path: str) -> str:
    """Volltext aller Seiten. Fallback nur bei fehlendem pdfplumber — echte
    PDF-Fehler sollen sichtbar bleiben."""
    try:
        import pdfplumber
    except ImportError:
        import fitz
        doc = fitz.open(path)
        return "\n".join((p.get_text() or "") for p in doc)
    with pdfplumber.open(path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def is_etoro(text: str) -> bool:
    return "eToro" in text


def detect_year(text, override=None):
    if override:
        return int(override)
    m = (re.search(r"Berichtszeitraum:?\s*\d{1,2}\.\d{1,2}\.(\d{4})", text)
         or re.search(r"Steuerjahr:?\s*(\d{4})", text, re.I)
         or re.search(r"Tax\s+year:?\s*(\d{4})", text, re.I))
    return int(m.group(1)) if m else None


# ──────────────────────────────────────────────────── Zeilen-Zuordnungen ──────
# Ein Betrag hat im eToro-Summenausweis immer zwei Nachkommastellen. Diese
# Formprüfung verhindert, dass eine Seitenzahl aus derselben Zeile als Wert
# eingesammelt wird ("… Zeile 20) 2" -> 2,00 €).
_BETRAG_KERN = re.compile(r"^\d[\d.\s]*[.,]\d{2}$")
_ROH_BETRAG = r"(?:\(\s*)?[-−–+]?\s*\d[\d.,\s]*\d(?:\s*\))?-?"

_ZUORDNUNG_RE = re.compile(
    r"Anlage\s+(KAP-INV|KAP|SO)\s*(" + _ROH_BETRAG + r")?\s*Zeile\s*(\d+)\)?\s*("
    + _ROH_BETRAG + r")?")
# Zählt, wie viele Zuordnungen im Text überhaupt stehen (auch ohne Klammer),
# damit ein Layoutwechsel als Zeilenverlust auffällt statt als 0,00 €.
_MARKER_RE = re.compile(r"Anlage\s+(?:KAP-INV|KAP|SO)\b[^\n]{0,80}?Zeile\s*\d+")


def _entklammert(tok: str) -> tuple[str, bool]:
    """Vorzeichen aus Klammer-/Trailing-Notation herausziehen, Rest zurückgeben."""
    s = str(tok).strip()
    neg = False
    for uni in ("−", "–", "—"):
        s = s.replace(uni, "-")
    s = re.sub(r"\s+", "", s)
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.endswith("-"):
        neg, s = not neg, s[:-1]
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    return s, neg


def sieht_aus_wie_betrag(tok) -> bool:
    """True nur bei einem echten Betrag mit zwei Nachkommastellen."""
    if tok is None:
        return False
    kern, _neg = _entklammert(tok)
    return bool(_BETRAG_KERN.match(kern))


def extract_lines(text, *, locale_hint: str | None = None):
    """Findet alle '... Anlage <KAP|KAP-INV|SO> Zeile <N>) <Betrag>' Zuordnungen.

    Bei umbrochenen Beschriftungen steht der Betrag NICHT nach 'Zeile N)', sondern
    davor (z. B. 'Anlage KAP 0,00 Zeile 20)'). Es gilt: der Wert nach der
    Zeilennummer hat Vorrang — aber nur, wenn er wie ein Betrag aussieht; sonst
    der Wert davor. Rückgabe: (werte, warnungen)."""
    hint = locale_hint or sl.detect_locale(text)
    out: dict[tuple[str, int], Decimal] = {}
    warnungen: list[str] = []
    norm = re.sub(r"[ \t]+", " ", text).replace("\n", " \n ")
    flach = re.sub(r"\s+", " ", text)
    for m in _ZUORDNUNG_RE.finditer(flach):
        anlage, before, zeile, after = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        raw = None
        for kandidat in (after, before):
            if sieht_aus_wie_betrag(kandidat):
                raw = kandidat
                break
        if raw is None:
            warnungen.append(
                f"Anlage {anlage} Zeile {zeile}: kein plausibler Betrag gefunden "
                f"(gelesen: vor='{before}', nach='{after}') — Wert im Original prüfen.")
            continue
        try:
            out[(anlage, zeile)] = sl.to_decimal(raw, locale_hint=hint)
        except sl.ParseError as e:
            warnungen.append(f"Anlage {anlage} Zeile {zeile}: {e}")
    marker = len(_MARKER_RE.findall(norm))
    if marker > len(out) + len(warnungen):
        warnungen.append(
            f"{marker} 'Anlage … Zeile N'-Marker im Report, aber nur {len(out)} "
            f"Werte gelesen — Report-Layout prüfen.")
    return out, warnungen


def detect_person(text):
    name = None
    m = re.search(r"Guten Tag\s+(.+?),", text)
    if m:
        name = m.group(1).strip()
    md = re.search(r"Depot:\s*(\d+)", text)
    return name, (md.group(1) if md else None)


# ──────────────────────────────────────────────── Summen aus dem Report ───────
_KAP_GESAMT = [r"Summe\s+(?:der\s+)?Kapitalertr(?:ä|ae)ge",
               r"Gesamtsumme\s+Kapitalertr(?:ä|ae)ge",
               r"Kapitalertr(?:ä|ae)ge\s+gesamt",
               r"Gesamtergebnis\s+Anlage\s+KAP"]
_SO_GESAMT = [r"Summe\s+(?:der\s+)?privaten\s+Ver(?:ä|ae)u(?:ß|ss)erungsgesch(?:ä|ae)fte",
              r"Private\s+Ver(?:ä|ae)u(?:ß|ss)erungsgesch(?:ä|ae)fte\s+gesamt",
              r"Gesamtergebnis\s+Anlage\s+SO"]


def _gesamt(text: str, muster: list[str], hint: str) -> Decimal | None:
    for pat in muster:
        m = re.search(r"^\s*" + pat + r"[^\d\-−–(+\n]*(" + _ROH_BETRAG + r")",
                      text, re.I | re.M)
        if m and sieht_aus_wie_betrag(m.group(1)):
            try:
                return sl.to_decimal(m.group(1), locale_hint=hint)
            except sl.ParseError:
                continue
    return None


def report_summen(text: str, hint: str) -> dict:
    return {"kap": _gesamt(text, _KAP_GESAMT, hint),
            "so": _gesamt(text, _SO_GESAMT, hint)}


# ─────────────────────────────────────────────────────────────── Ergebnis ─────
def build_result(text: str, year, *, quelle: str = "etoro", strikt: bool = True) -> dict:
    hint = sl.detect_locale(text)
    lines, warnungen = extract_lines(text, locale_hint=hint)
    if not lines:
        raise sl.ParseError(
            "Keine 'Anlage … Zeile N'-Zuordnungen gefunden — Report-Layout geändert? "
            "Ein Ergebnis aus lauter Nullen wäre von einem echten Null-Report nicht "
            "zu unterscheiden; deshalb Abbruch. Report manuell prüfen.")

    def g(a, z):
        return lines.get((a, z), D("0"))

    # --- § 23 private Veräußerungsgeschäfte (Anlage SO Zeile 47) ---
    so47 = g("SO", 47)
    # --- § 22 Nr. 3 sonstige Leistungen (Staking SO Z11, Wertpapierleihe SO Z10) ---
    staking, wpleihe = g("SO", 11), g("SO", 10)
    sonst_leistungen = staking + wpleihe

    kap = {
        "z7_inlaend_mit_steuerabzug": str(sl.q2(g("KAP", 7))),
        "z18_inlaend_ohne_steuerabzug": str(sl.q2(g("KAP", 18))),
        "z19_auslaend_kapitalertraege": str(sl.q2(g("KAP", 19))),
        "z20_aktien_veraeusserung_gewinn": str(sl.q2(g("KAP", 20))),
        "z21_termingeschaefte_stillhalter_gewinne": str(sl.q2(g("KAP", 21))),
        "z22_verluste_ohne_aktien": str(sl.q2(g("KAP", 22))),
        "z23_verluste_aktien": str(sl.q2(g("KAP", 23))),
        "z24_verluste_termingeschaefte": str(sl.q2(g("KAP", 24))),
        "z25_verluste_ausfall": str(sl.q2(g("KAP", 25))),
        "z37_kapitalertragsteuer": str(sl.q2(g("KAP", 37))),
        "z38_soli": str(sl.q2(g("KAP", 38))),
        "z41_anrechenbare_auslaend_steuer": str(sl.q2(g("KAP", 41))),
        "z42_fiktive_quellensteuer": str(sl.q2(g("KAP", 42))),
    }

    elster_extra = []
    kap_rows = [
        (19, "Ausländische Kapitalerträge", g("KAP", 19)),
        (20, "Gewinne aus Aktienveräußerungen § 20 Abs. 2 Nr. 1", g("KAP", 20)),
        (21, "Gewinne aus Termingeschäften/Stillhalterprämien", g("KAP", 21)),
        (22, "Verluste (ohne Aktien)", g("KAP", 22)),
        (23, "Verluste aus Aktienveräußerungen", g("KAP", 23)),
        (24, "Verluste aus Termingeschäften", g("KAP", 24)),
        (25, "Verluste Ausfall/Ausbuchung", g("KAP", 25)),
        (41, "Anrechenbare ausländische Steuer", g("KAP", 41)),
    ]
    for z, bez, val in kap_rows:
        if val:
            elster_extra.append({"anlage": "Anlage KAP", "zeile": f"Z. {z}",
                                 "bezeichnung": f"{bez} (eToro)", "wert": str(sl.q2(val))})

    # --- Abgleich gegen die im Report ausgewiesenen Gesamtsummen ---------------
    kap_netto = sum((g("KAP", z) for z in (19, 20, 21, 22, 23, 24, 25)), D("0"))
    aus = report_summen(text, hint)
    abgleiche = [
        sl.Abgleich("Anlage KAP netto (Z.19–25)", sl.q2(kap_netto),
                    None if aus["kap"] is None else sl.q2(aus["kap"])),
        sl.Abgleich("Anlage SO Z. 47 (§ 23)", sl.q2(so47),
                    None if aus["so"] is None else sl.q2(aus["so"])),
    ]
    bericht = [str(a) for a in abgleiche]
    bericht.append(f"Gefundene Zeilen-Zuordnungen: {len(lines)}")

    name, depot = detect_person(text)

    result = {
        "tax_year": year,
        "quelle": quelle,
        "quelle_beschreibung": f"eToro-Steuerbericht (vorberechnet, FIFO; Depot {depot or '—'})",
        "methode": "Summenausweis übernommen (eToro hat deutsche Klassifizierung gerechnet).",
        "zahlennotation": hint,
        "abgleich": bericht,
        "paragraph_23": {
            "freigrenze_angewendet": False,
            "anzahl_veraeusserungen": None,
            "gewinn_eur": str(sl.q2(so47 if so47 > 0 else D("0"))),
            "verlust_eur": str(sl.q2(so47 if so47 < 0 else D("0"))),
            "netto_ergebnis_eur": str(sl.q2(so47)),
            "verlustvortrag_eur": str(sl.q2(-so47 if so47 < 0 else D("0"))),
            "steuerfrei_langfristig_eur": "0.00",
            "disposals": [],
            "warnungen": warnungen,
            "hinweis": ("Wert = Anlage SO Zeile 47 (eToro, bereits FIFO/§ 23-saldiert). "
                        "Rohwert ohne Freigrenze — build_taxreport.py wendet sie einmal "
                        "auf die Summe aller Broker an."),
        },
        "paragraph_22_nr3": {
            "freigrenze_angewendet": False,
            "gewinn_eur": str(sl.q2(sonst_leistungen if sonst_leistungen > 0 else D("0"))),
            "verlust_eur": str(sl.q2(sonst_leistungen if sonst_leistungen < 0 else D("0"))),
            "netto_ergebnis_eur": str(sl.q2(sonst_leistungen)),
            "verlustvortrag_eur": "0.00",
            "steuerfrei_langfristig_eur": "0.00",
            "summe_zufluesse_eur": str(sl.q2(sonst_leistungen)),
            "ertraege": [],
            "detail": {"staking_so_z11": str(sl.q2(staking)),
                       "wertpapierleihe_so_z10": str(sl.q2(wpleihe))},
            "warnungen": [],
            "hinweis": ("Rohwert ohne Freigrenze (§ 22 Nr. 3 Satz 2: 256 € pro Person "
                        "und Jahr, über alle Quellen)."),
        },
        "etoro_kap": kap,
        "elster_extra": elster_extra,
        "steuerpflichtiger_aus_report": name,
        "hinweise": [
            "Werte aus eToro-Summenausweis übernommen — gegen Steuerbescheinigung/Original prüfen.",
            "Freigrenzen (§ 23: 1.000/600 €, § 22 Nr. 3: 256 €) sind hier bewusst NICHT "
            "angewendet — sie gelten pro Person und Jahr über alle Broker; "
            "build_taxreport.py rechnet sie einmal auf die Summe.",
            "§ 23 (Anlage SO Z. 47): Krypto-Spot, bereits FIFO-saldiert. Verlust -> "
            "Verlustfeststellung; nur mit § 23-Gewinnen verrechenbar.",
            "Termingeschäfte (CFDs, § 20 Abs. 2): Verluste (KAP Z. 24) nur mit "
            "Termingeschäfts-Gewinnen/Stillhalterprämien verrechenbar; Sonderverlusttopf, "
            "Verrechnung pro Jahr betraglich begrenzt.",
            "Aktienverluste (KAP Z. 23) nur mit Aktiengewinnen verrechenbar (eigener Topf).",
            "Bei mehreren Brokern/Tools: § 23-Ergebnisse addieren, KAP-Töpfe getrennt führen.",
            "Endkontrolle durch Steuerberater — keine Steuerberatung.",
        ],
    }
    result["paragraph_22_nr_3"] = result["paragraph_22_nr3"]   # alte Schreibweise

    sl.pruefe_summen(abgleiche, strikt=strikt)
    return result


def main():
    ap = argparse.ArgumentParser(
        description="eToro-Steuerbericht (PDF) -> krypto_result.json",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf_path")
    ap.add_argument("--year")
    ap.add_argument("-o", "--out",
                    help="Ausgabedatei (Standard: <pdf-name>.krypto_result.json — "
                         "bewusst kein fester Name, damit ein zweiter Broker den "
                         "ersten nicht überschreibt)")
    args = ap.parse_args()

    text = _pdf_text(args.pdf_path)
    if not is_etoro(text):
        print("WARNUNG: sieht nicht nach eToro aus. Trotzdem versuchen ...", file=sys.stderr)
    year = detect_year(text, args.year)
    if not year:
        print("Steuerjahr nicht erkannt — bitte --year setzen.", file=sys.stderr)
        sys.exit(1)
    out = args.out or str(Path(args.pdf_path).with_suffix("").name + ".krypto_result.json")

    try:
        result = build_result(text, year, quelle=Path(args.pdf_path).name)
    except (sl.ParseError, sl.PlausibilityError) as e:
        print(f"ABBRUCH: {e}", file=sys.stderr)
        sys.exit(1)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    p = result["paragraph_23"]
    k = result["etoro_kap"]
    print(f"eToro-Report {year} geparst -> {out}")
    if result.get("steuerpflichtiger_aus_report"):
        print(f"  Steuerpflichtiger: {result['steuerpflichtiger_aus_report']}")
    print("  Abgleich:")
    for zeile in result["abgleich"]:
        print(f"    {zeile}")
    print(f"  § 23 (Anlage SO Z.47, Krypto-Spot), roh ohne Freigrenze: "
          f"{p['netto_ergebnis_eur']} €")
    if sl.to_decimal(p["verlustvortrag_eur"]) > 0:
        print(f"  Verlustvortrag § 23: {p['verlustvortrag_eur']} €")
    print(f"  Anlage KAP — ausländ. Kapitalerträge (Z.19): {k['z19_auslaend_kapitalertraege']} €")
    print(f"               Termingeschäfte-Gewinne (Z.21): {k['z21_termingeschaefte_stillhalter_gewinne']} €")
    print(f"               Aktienverluste (Z.23): {k['z23_verluste_aktien']} €")
    print(f"               Termingeschäfte-Verluste (Z.24): {k['z24_verluste_termingeschaefte']} €")
    print(f"  § 22 Nr.3 (Staking/Wertpapierleihe), roh: "
          f"{result['paragraph_22_nr3']['netto_ergebnis_eur']} €")
    for w in p["warnungen"]:
        print(f"  WARNUNG: {w}", file=sys.stderr)
    print("  Freigrenzen NICHT angewendet — build_taxreport.py rechnet sie einmal "
          "auf die Summe aller Reports.")


if __name__ == "__main__":
    main()
