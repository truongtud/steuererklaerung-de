#!/usr/bin/env python3
"""Zusammenspiel der Stufe-1-Posten in build_taxreport.

Die Formeln selbst prüft tests/test_steuerlib.py. Hier geht es um die
Reihenfolge und die Grenzfälle, die erst im Zusammenspiel auffallen: § 35a
mindert die tarifliche Steuer und darf sie nicht unter null drücken, der
Progressionsvorbehalt hebt den Satz ohne die Leistung zu besteuern, und die
Günstigerprüfung vergleicht einschließlich der Zuschlagsteuern.

Ausführen: python3 tests/test_stufe1.py   (oder tests/run_tests.py)
"""
import io
import os
import sys
from contextlib import redirect_stderr
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_taxreport as bt  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def steuerdaten(**over):
    sd = {
        "steuerjahr": 2024,
        "steuerpflichtiger": {"name": "Test", "verheiratet": False},
        "anlage_n": {"bruttoarbeitslohn": "50000", "lohnsteuer": "8000"},
    }
    sd.update(over)
    return sd


def bau(sd):
    """build() ohne die stderr-Ausgabe der Feldprüfung im Testprotokoll."""
    with redirect_stderr(io.StringIO()):
        return bt.build(sd, [])


# ── § 35a ────────────────────────────────────────────────────────────────────
@case
def test_35a_mindert_die_festgesetzte_steuer():
    ohne = bau(steuerdaten())
    mit = bau(steuerdaten(steuerermaessigungen={
        "paragraph_35a": {"handwerkerleistungen": "3000"}}))
    eq(mit["berechnung"]["steuerermaessigung_35a"], "600.00", "20 % von 3.000")
    eq(D(ohne["berechnung"]["einkommensteuer_schaetzung"]) - D(mit["berechnung"]["einkommensteuer_schaetzung"]),
       D("600.00"), "die Ermäßigung schlägt voll durch")
    eq(mit["berechnung"]["einkommensteuer_tariflich_schaetzung"], ohne["berechnung"]["einkommensteuer_schaetzung"],
       "die tarifliche Steuer bleibt unberührt")


@case
def test_35a_drueckt_die_steuer_nicht_unter_null():
    """Ohne diese Deckelung erfände der Report eine Erstattung, die es nicht
    gibt: ein Überhang nach § 35a verfällt, er wird weder ausgezahlt noch
    vorgetragen."""
    r = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "12000", "lohnsteuer": "0"},
                        steuerermaessigungen={"paragraph_35a": {
                            "handwerkerleistungen": "6000",
                            "haushaltsnahe_dienstleistungen": "20000"}}))
    eq(r["berechnung"]["einkommensteuer_schaetzung"], "0.00", "voll aufgezehrt, nicht negativ")


@case
def test_35a_hinweis_zu_material_und_unbarer_zahlung():
    r = bau(steuerdaten(steuerermaessigungen={
        "paragraph_35a": {"handwerkerleistungen": "3000"}}))
    text = " ".join(r["hinweise"])
    assert "Material" in text and "unbar" in text, \
        f"die beiden häufigsten Streichgründe müssen dabeistehen: {text!r}"


@case
def test_35a_tippfehler_im_topf_faellt_auf():
    """Die Topfnamen sind fest — ein vertippter Topf darf nicht still 0 ergeben."""
    r = bau(steuerdaten(steuerermaessigungen={
        "paragraph_35a": {"handwerkerkosten": "3000"}}))
    pfade = [b["pfad"] for b in r["eingabepruefung"]["unbekannte_felder"]]
    assert "steuerermaessigungen.paragraph_35a.handwerkerkosten" in pfade, \
        f"Tippfehler wurde nicht gemeldet: {pfade}"
    eq(r["berechnung"]["steuerermaessigung_35a"], "0.00", "der Wert darf nicht wirken")


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
