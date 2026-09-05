#!/usr/bin/env python3
"""Tests für scripts/uebertrage_verlustvortrag.py.

Ausführen: python3 tests/test_uebertrage_verlustvortrag.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import build_taxreport as bt          # noqa: E402
import uebertrage_verlustvortrag as uv  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def report_mit(aktien="0.00", allgemein="0.00", p23_neu="0.00", jahr=2025) -> dict:
    """Ein taxreport.json-Fragment mit genau den Feldern, die dieses Skript liest."""
    return {
        "meta": {"steuerjahr": jahr},
        "anlagen": {
            "KAP": {"verlustvortraege": {"aktien": aktien, "allgemein": allgemein}},
            "SO": {"verlustvortrag_23_neu_gesamt": p23_neu},
        },
    }


def schreibe(tmp, name, obj):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return p


# ── vortraege_aus_report ─────────────────────────────────────────────────────
@case
def test_liest_alle_drei_felder():
    v = uv.vortraege_aus_report(report_mit(aktien="500.00", allgemein="120.50",
                                           p23_neu="80.00"))
    eq(str(v["anlage_kap.verlustvortrag_aktien_vorjahr"]), "500.00")
    eq(str(v["anlage_kap.verlustvortrag_allgemein_vorjahr"]), "120.50")
    eq(str(v["anlage_so.verlustvortrag_23_vorjahr"]), "80.00")


@case
def test_fehlende_bloecke_werden_gemeldet_nicht_geraten():
    kaputt = {"meta": {"steuerjahr": 2025}, "anlagen": {}}
    try:
        uv.vortraege_aus_report(kaputt)
        raise AssertionError("hätte UebertragungFehler werfen müssen")
    except uv.UebertragungFehler as e:
        assert "erwarteten Felder" in str(e), str(e)


@case
def test_fehlende_einzelfelder_werden_gemeldet_nicht_geraten():
    kaputt = {"meta": {"steuerjahr": 2025},
              "anlagen": {"KAP": {"verlustvortraege": {}}, "SO": {}}}
    try:
        uv.vortraege_aus_report(kaputt)
        raise AssertionError("hätte UebertragungFehler werfen müssen")
    except uv.UebertragungFehler as e:
        assert "aktien" in str(e) and "allgemein" in str(e), str(e)


@case
def test_reales_taxreport_schema_stimmt_ueberein():
    """Integrationstest gegen den ECHTEN build_taxreport.py-Output — fängt eine
    Schema-Drift ab, die die übrigen (handgebauten) Tests hier nicht sähen."""
    sd = {
        "steuerjahr": 2025,
        "steuerpflichtiger": {"verheiratet": False},
        "anlage_kap": {"kapitalertraege": "0", "gewinn_aktien": "0",
                      "verlust_aktien": "1000"},
    }
    report = bt.build(sd, [], None)
    v = uv.vortraege_aus_report(report)
    # kein Aktiengewinn -> voller Verlust wird zum Überhang -> voller Vortrag.
    eq(str(v["anlage_kap.verlustvortrag_aktien_vorjahr"]), "1000.00")
    eq(str(v["anlage_kap.verlustvortrag_allgemein_vorjahr"]), "0.00")
    eq(str(v["anlage_so.verlustvortrag_23_vorjahr"]), "0.00")


# ── plane_uebertragung ───────────────────────────────────────────────────────
@case
def test_leeres_ziel_wird_vollstaendig_uebernommen():
    v = uv.vortraege_aus_report(report_mit(aktien="500.00"))
    aktionen, konflikte = uv.plane_uebertragung({}, v, force=False)
    eq(konflikte, [], "keine Konflikte bei leerem Ziel")
    pfade = {p for p, _, _ in aktionen}
    assert "anlage_kap.verlustvortrag_aktien_vorjahr" in pfade
    aktion = next(a for a in aktionen if a[0] == "anlage_kap.verlustvortrag_aktien_vorjahr")
    eq(aktion[2], "500.00", "neuer Wert")


@case
def test_bereits_uebernommener_wert_ist_kein_konflikt():
    v = uv.vortraege_aus_report(report_mit(aktien="500.00"))
    ziel = {"anlage_kap": {"verlustvortrag_aktien_vorjahr": "500.00"}}
    aktionen, konflikte = uv.plane_uebertragung(ziel, v, force=False)
    eq(konflikte, [], "identischer Wert ist kein Konflikt")
    eq([a for a in aktionen if a[0] == "anlage_kap.verlustvortrag_aktien_vorjahr"], [],
       "nichts zu tun, wenn der Wert schon stimmt")


@case
def test_abweichender_wert_ist_konflikt_ohne_force():
    v = uv.vortraege_aus_report(report_mit(aktien="500.00"))
    ziel = {"anlage_kap": {"verlustvortrag_aktien_vorjahr": "999.00"}}
    aktionen, konflikte = uv.plane_uebertragung(ziel, v, force=False)
    eq([p for p, _, _ in aktionen if p == "anlage_kap.verlustvortrag_aktien_vorjahr"], [],
       "kein stilles Überschreiben")
    pfade = {p for p, _, _ in konflikte}
    assert "anlage_kap.verlustvortrag_aktien_vorjahr" in pfade


@case
def test_force_ueberschreibt_konflikt():
    v = uv.vortraege_aus_report(report_mit(aktien="500.00"))
    ziel = {"anlage_kap": {"verlustvortrag_aktien_vorjahr": "999.00"}}
    aktionen, konflikte = uv.plane_uebertragung(ziel, v, force=True)
    eq(konflikte, [], "force räumt Konflikte aus")
    eq(next(a[2] for a in aktionen if a[0] == "anlage_kap.verlustvortrag_aktien_vorjahr"),
       "500.00")


@case
def test_termingeschaefte_feld_wird_bei_allgemeinem_vortrag_genullt():
    """JStG 2024: der Termingeschäfte-Topf ist im allgemeinen Vortrag bereits
    enthalten. Ein stehen gebliebener Altwert dort würde doppelt verrechnet."""
    v = uv.vortraege_aus_report(report_mit(allgemein="300.00"))
    ziel = {"anlage_kap": {"verlustvortrag_termingeschaefte_vorjahr": "150.00"}}
    aktionen, konflikte = uv.plane_uebertragung(ziel, v, force=False)
    # ohne --force: Konflikt, kein stilles Nullen
    pfade_konflikt = {p for p, _, _ in konflikte}
    assert uv._TERMINGESCHAEFTE_FELD in pfade_konflikt, konflikte
    aktionen2, konflikte2 = uv.plane_uebertragung(ziel, v, force=True)
    eq(konflikte2, [])
    genullt = next(a for a in aktionen2 if a[0] == uv._TERMINGESCHAEFTE_FELD)
    eq(genullt[2], "0.00")


@case
def test_termingeschaefte_feld_bleibt_unberuehrt_wenn_schon_null():
    v = uv.vortraege_aus_report(report_mit(allgemein="300.00"))
    ziel = {"anlage_kap": {"verlustvortrag_termingeschaefte_vorjahr": "0"}}
    aktionen, konflikte = uv.plane_uebertragung(ziel, v, force=False)
    eq(konflikte, [], "0 ist kein Konflikt")
    assert not any(p == uv._TERMINGESCHAEFTE_FELD for p, _, _ in aktionen), \
        "ein bereits genulltes Feld muss nicht erneut geschrieben werden"


# ── CLI (subprocess) ─────────────────────────────────────────────────────────
SKRIPT = os.path.join(SCRIPTS, "uebertrage_verlustvortrag.py")


def _lauf(*args):
    return subprocess.run([sys.executable, SKRIPT, *args], capture_output=True, text=True)


@case
def test_cli_ohne_schreiben_aendert_nichts():
    with tempfile.TemporaryDirectory() as tmp:
        alt = schreibe(tmp, "alt.json", report_mit(aktien="500.00", jahr=2024))
        neu_pfad = schreibe(tmp, "neu.json", {"steuerjahr": 2025})
        vorher = open(neu_pfad, encoding="utf-8").read()
        p = _lauf(alt, neu_pfad)
        eq(p.returncode, 0, p.stderr)
        eq(open(neu_pfad, encoding="utf-8").read(), vorher, "Datei unveraendert ohne --schreiben")
        assert "500.00" in p.stdout


@case
def test_cli_schreiben_uebernimmt_die_werte():
    with tempfile.TemporaryDirectory() as tmp:
        alt = schreibe(tmp, "alt.json", report_mit(aktien="500.00", p23_neu="80.00", jahr=2024))
        neu_pfad = schreibe(tmp, "neu.json", {"steuerjahr": 2025, "anlage_n": {}})
        p = _lauf(alt, neu_pfad, "--schreiben")
        eq(p.returncode, 0, p.stderr)
        with open(neu_pfad, encoding="utf-8") as f:
            neu = json.load(f)
        eq(neu["anlage_kap"]["verlustvortrag_aktien_vorjahr"], "500.00")
        eq(neu["anlage_so"]["verlustvortrag_23_vorjahr"], "80.00")
        eq(neu["anlage_n"], {}, "unbeteiligte Felder bleiben unangetastet")


@case
def test_cli_bricht_bei_konflikt_ohne_force_ab():
    with tempfile.TemporaryDirectory() as tmp:
        alt = schreibe(tmp, "alt.json", report_mit(aktien="500.00", jahr=2024))
        neu_pfad = schreibe(tmp, "neu.json",
                            {"steuerjahr": 2025,
                             "anlage_kap": {"verlustvortrag_aktien_vorjahr": "999.00"}})
        vorher = open(neu_pfad, encoding="utf-8").read()
        p = _lauf(alt, neu_pfad, "--schreiben")
        eq(p.returncode, 1, p.stdout + p.stderr)
        eq(open(neu_pfad, encoding="utf-8").read(), vorher, "bei Konflikt bleibt die Datei unveraendert")
        assert "999.00" in p.stdout and "--force" in p.stdout


@case
def test_cli_warnt_bei_nicht_aufeinanderfolgenden_jahren():
    with tempfile.TemporaryDirectory() as tmp:
        alt = schreibe(tmp, "alt.json", report_mit(aktien="500.00", jahr=2023))
        neu_pfad = schreibe(tmp, "neu.json", {"steuerjahr": 2025})
        p = _lauf(alt, neu_pfad, "--schreiben")
        eq(p.returncode, 0, p.stderr)
        assert "WARNUNG" in p.stderr and "2025" in p.stderr


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
