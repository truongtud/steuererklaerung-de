#!/usr/bin/env python3
"""neue_steuerdaten.py — der Einstieg erzeugt eine passende steuerdaten.json.

Der wichtigste Test steht unten: was hier herauskommt, muss ohne Fehler und ohne
gemeldetes Feld durch build_taxreport laufen. Eine Startdatei, die der eigene
Report als fehlerhaft meldet, wäre schlimmer als gar keine.

Ausführen: python3 tests/test_einstieg.py   (oder tests/run_tests.py)
"""
import io
import json
import os
import sys
from contextlib import redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_taxreport as bt  # noqa: E402
import neue_steuerdaten as ns  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


@case
def test_angestellter_bekommt_nur_die_passenden_bloecke():
    """Wer nur Arbeitslohn hat, soll keine Anlage G und keine Krypto-Liste in
    seiner Startdatei finden — leere Blöcke laden zum Ausfüllen ein, wo nichts
    auszufüllen ist."""
    sd = ns.steuerdaten(jahr=2026, taetigkeiten=["angestellt"])
    assert "anlage_n" in sd
    for weg in ("anlage_g", "anlage_s", "anlage_v", "krypto_transaktionen", "kinder"):
        assert weg not in sd, f"{weg} gehört hier nicht hinein"


@case
def test_bloecke_folgen_den_angaben():
    sd = ns.steuerdaten(jahr=2026, taetigkeiten=["angestellt", "vermietung"],
                        kinder=2, kapital=True, krypto=True, handwerker=True,
                        lohnersatz=True, agb=True)
    for erwartet in ("anlage_n", "anlage_v", "anlage_kap", "anlage_so",
                     "krypto_transaktionen", "kinder", "steuerermaessigungen",
                     "lohnersatzleistungen", "aussergewoehnliche_belastungen"):
        assert erwartet in sd, f"{erwartet} fehlt"
    eq(len(sd["kinder"]), 2, "je Kind ein Eintrag")


@case
def test_stammdaten_werden_uebernommen():
    sd = ns.steuerdaten(jahr=2024, taetigkeiten=["angestellt"], verheiratet=True,
                        kirchensteuersatz="9")
    eq(sd["steuerjahr"], 2024)
    eq(sd["zusammenveranlagung"], True)
    eq(sd["steuerpflichtiger"]["verheiratet"], True)
    eq(sd["steuerpflichtiger"]["kirchensteuersatz"], "9")


@case
def test_vorsorge_ist_gegliedert():
    """Ohne Gliederung greift die Höchstbetragsberechnung nach § 10 Abs. 3/4
    nicht — eine Startdatei darf niemanden in diese Lücke laufen lassen."""
    sd = ns.steuerdaten(jahr=2026, taetigkeiten=["angestellt"])
    for topf in ("basisversorgung", "kranken_pflege_basis", "sonstige"):
        assert topf in sd["vorsorge"], f"{topf} fehlt in der Gliederung"


@case
def test_erzeugte_datei_laeuft_fehlerfrei_durch_den_report():
    """Der eigentliche Punkt: keine unbekannten Felder, kein Absturz."""
    sd = ns.steuerdaten(jahr=2026, taetigkeiten=["angestellt", "selbstaendig"],
                        kinder=1, kapital=True, krypto=True, handwerker=True,
                        lohnersatz=True, agb=True, verheiratet=True,
                        kirchensteuersatz="9")
    befunde = bt.pruefe_unbekannte_felder(sd)
    eq(befunde, [], f"die Startdatei meldet eigene Felder als unbekannt: {befunde}")
    with redirect_stderr(io.StringIO()):
        report = bt.build(sd, [])
    eq(report["meta"]["steuerjahr"], 2026)


@case
def test_unterlagen_nennen_die_belege_zur_situation():
    """Die Checkliste ist der halbe Nutzen: wer nicht weiß, welche Papiere er
    braucht, fängt gar nicht erst an."""
    u = " ".join(ns.unterlagen(taetigkeiten=["angestellt"], kapital=True,
                               kinder=1, handwerker=True))
    for stichwort in ("Lohnsteuerbescheinigung", "Steuerbescheinigung",
                      "Kindergeld", "Handwerker"):
        assert stichwort in u, f"{stichwort} fehlt in der Unterlagenliste: {u}"


@case
def test_anlagen_werden_benannt():
    a = " | ".join(ns.anlagen(taetigkeiten=["angestellt", "vermietung"],
                              kapital=True, kinder=1))
    for erwartet in ("Anlage N", "Anlage V", "Anlage KAP", "Anlage Kind",
                     "Vorsorgeaufwand"):
        assert erwartet in a, f"{erwartet} fehlt: {a}"
    assert "Anlage G" not in a, f"kein Gewerbe angegeben: {a}"


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
