#!/usr/bin/env python3
"""Bescheidprüfung: Lesen, Vergleichen, Fristen.

An diesem Dokument hängt eine Monatsfrist. Entsprechend ist hier geprüft, dass
nichts geraten wird: ein Betrag, der nicht eindeutig dasteht, bleibt leer, und
eine Festsetzung, die nicht aufgeht, bricht den Lauf ab.

Die Fixture ist synthetisch — kein Original und kein geschwärztes Original.

Ausführen: python3 tests/test_bescheid.py   (oder tests/run_tests.py)
"""
import json
import os
import sys
from datetime import date
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
BEISPIEL = os.path.abspath(os.path.join(ROOT, "..", "..", "..", "..", "beispiel"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import pruefe_bescheid as pb  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def wirft(fehler, fn, label):
    try:
        fn()
    except fehler:
        return
    raise AssertionError(f"{label}: {fehler.__name__} erwartet, kam nicht")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def report():
    with open(os.path.join(BEISPIEL, "taxreport.json"), encoding="utf-8") as f:
        return json.load(f)


# ── Bescheid lesen ───────────────────────────────────────────────────────────
@case
def test_bescheid_kennzahlen():
    b = pb.bescheid_aus_text(fixture("bescheid_synthetisch.txt"))
    eq(b["steuerjahr"], 2024)
    eq(b["bescheiddatum"], date(2026, 3, 2))
    eq(b["grundlagen"]["zu_versteuerndes_einkommen"], D("66192.00"))
    eq(b["festsetzung"]["einkommensteuer"], D("16280.00"))
    eq(b["festsetzung"]["solidaritaetszuschlag"], D("0.00"))
    eq(b["festsetzung"]["kirchensteuer"], D("1465.20"))
    eq(b["fehlend"], [], "aus dieser Fixture ist alles eindeutig lesbar")


@case
def test_kirchensteuer_wird_dem_richtigen_abschnitt_entnommen():
    """'Kirchensteuer' steht zweimal im Bescheid: in der Festsetzung und bei den
    Anrechnungsbeträgen. Ohne den Abschnitt zu schneiden liest man den falschen
    Betrag — und die Zahl sieht dabei völlig plausibel aus."""
    b = pb.bescheid_aus_text(fixture("bescheid_synthetisch.txt"))
    eq(b["festsetzung"]["kirchensteuer"], D("1465.20"), "festgesetzt")
    eq(b["anrechnung"]["kirchensteuer"], D("1530.00"), "einbehalten")


@case
def test_summenabgleich_faengt_eine_verlesene_zahl():
    """Festsetzung minus Anrechnung muss den ausgewiesenen Saldo ergeben."""
    text = fixture("bescheid_synthetisch.txt").replace("16.280,00", "16.280,99")
    wirft(pb.BescheidFehler, lambda: pb.bescheid_aus_text(text), "Summe geht nicht auf")


@case
def test_unlesbarer_text_wirft():
    wirft(pb.BescheidFehler, lambda: pb.bescheid_aus_text("Werbeprospekt"),
          "kein Bescheid")
    wirft(pb.BescheidFehler,
          lambda: pb.bescheid_aus_text(fixture("bescheid_synthetisch.txt")
                                       .replace("Bescheid für 2024", "Bescheid")),
          "kein Steuerjahr")


# ── Fristen ──────────────────────────────────────────────────────────────────
@case
def test_fristen_aus_dem_bescheiddatum():
    f = pb.fristen(date(2026, 3, 2))
    eq(f["bekanntgabe"], "2026-03-06", "§ 122 Abs. 2 Nr. 1 AO: vierter Tag")
    eq(f["einspruchsfrist_ende"], "2026-04-07", "ein Monat, 6.4. ist Ostermontag")
    assert "§ 355" in f["grundlage"], f
    assert "zu früh" in f["vorbehalt"], "die Richtung des Restfehlers muss dastehen"


# ── Vergleich ────────────────────────────────────────────────────────────────
@case
def test_vergleich_stellt_report_und_bescheid_gegenueber():
    zeilen = {z["position"]: z for z in
              pb.vergleiche(report(), pb.bescheid_aus_text(fixture("bescheid_synthetisch.txt")))}
    eq(zeilen["Zu versteuerndes Einkommen"]["differenz"], D("0.00"))
    eq(zeilen["Zu versteuerndes Einkommen"]["einordnung"], "stimmt überein")
    # Der Bescheid setzt weniger ESt an als der Report — eine echte Abweichung.
    eq(zeilen["Festgesetzte Einkommensteuer"]["differenz"], D("-428.00"))


@case
def test_unerklaerte_abweichung_wird_so_benannt():
    """Deckt die Unsicherheitsbilanz des Reports die Richtung nicht ab, bleibt
    die Abweichung unerklärt — und genau das ist das Ergebnis der Prüfung."""
    zeilen = {z["position"]: z for z in
              pb.vergleiche(report(), pb.bescheid_aus_text(fixture("bescheid_synthetisch.txt")))}
    eq(zeilen["Festgesetzte Einkommensteuer"]["einordnung"], "unerklärt")
    eq(zeilen["Festgesetzte Einkommensteuer"]["moegliche_ursachen"], [])


@case
def test_bekannte_luecke_macht_die_abweichung_erklaerbar():
    """Weist der Report für diese Richtung selbst eine Lücke aus, wird die
    Abweichung als möglicherweise erklärbar geführt — als Hinweis, nicht als
    rechtliche Bewertung."""
    r = report()
    r["unsicherheit"] = {"posten": [{"posten": "Testlücke", "richtung": "zu hoch",
                                     "groessenordnung": None, "fundstelle": "§ x"}],
                         "gesamtrichtung": "Schätzung eher zu hoch"}
    zeilen = {z["position"]: z for z in
              pb.vergleiche(r, pb.bescheid_aus_text(fixture("bescheid_synthetisch.txt")))}
    p = zeilen["Festgesetzte Einkommensteuer"]
    eq(p["einordnung"], "möglicherweise erklärbar")
    eq(p["moegliche_ursachen"], ["Testlücke"])


# ── Einspruchsentwurf ────────────────────────────────────────────────────────
@case
def test_einspruchsentwurf_nennt_frist_streitpunkte_und_ist_als_entwurf_erkennbar():
    b = pb.bescheid_aus_text(fixture("bescheid_synthetisch.txt"))
    unerklaert = [z for z in pb.vergleiche(report(), b) if z["einordnung"] == "unerklärt"]
    text = pb.einspruchsentwurf(b, unerklaert, pb.fristen(b["bescheiddatum"]))
    assert text.startswith("ENTWURF"), "er darf nicht wie ein fertiger Schriftsatz aussehen"
    assert "§ 355" in text, "die Frist gehört hinein"
    assert "2026-04-07" in text, "das konkrete Fristende gehört hinein"
    assert "Festgesetzte Einkommensteuer" in text, "die Streitpunkte gehören hinein"


if __name__ == "__main__":
    fails = []
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(CASES) - len(fails)}/{len(CASES)} bestanden")
    sys.exit(1 if fails else 0)
