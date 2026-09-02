#!/usr/bin/env python3
"""references/steuerwerte.json ist die Quelle der jahresabhängigen Werte.

Geprüft wird dreierlei: die JSON ist vollständig und exakt lesbar, steuerlib.py
baut seine Tabellen daraus, und die Tabellen in references/steuerwerte.md sagen
dasselbe. Der letzte Punkt ist der eigentliche Grund für diese Datei — solange
Zahl und Dokumentation getrennt gepflegt wurden, konnte eine falsche
Soli-Freigrenze jahrelang unbemerkt in beiden stehen.

Ausführen: python3 tests/test_steuerwerte_json.py   (oder tests/run_tests.py)
"""
import json
import os
import re
import sys
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import steuerlib as sl  # noqa: E402

JSON_PFAD = os.path.join(ROOT, "references", "steuerwerte.json")
MD_PFAD = os.path.join(ROOT, "references", "steuerwerte.md")

TARIF_SCHLUESSEL = ("gfb", "z2", "z3", "z4", "a2", "c3", "a3", "k4", "k5")
JAHRESWERTE = ("soli_freigrenze", "freigrenze_23", "sparer_pb", "an_pauschbetrag")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def lade():
    with open(JSON_PFAD, encoding="utf-8") as f:
        return json.load(f)


def de_zahl(s):
    """„11.784 €“ / „**1.034,87**“ → Decimal. None, wenn keine Zahl drinsteht."""
    s = s.replace("*", "").replace("€", "").strip()
    if not re.fullmatch(r"-?[\d.]+(,\d+)?", s):
        return None
    return D(s.replace(".", "").replace(",", "."))


# ── JSON selbst ──────────────────────────────────────────────────────────────
@case
def test_json_ist_vollstaendig():
    daten = lade()
    eq(daten["schema"], 1, "Schema-Version")
    jahre = daten["jahre"]
    assert jahre, "keine Jahre hinterlegt"
    for jahr, eintrag in jahre.items():
        eq(bool(re.fullmatch(r"20\d\d", jahr)), True, f"Jahresschlüssel {jahr!r}")
        for k in TARIF_SCHLUESSEL:
            assert k in eintrag["tarif"], f"{jahr}: tarif.{k} fehlt"
        for k in JAHRESWERTE:
            assert k in eintrag, f"{jahr}: {k} fehlt"
        # quelle = Änderungsgesetz mit Fundstelle im BGBl. (Handarbeit),
        # beleg = womit fetch_steuerwerte.py zuletzt nachgeprüft hat.
        for k in ("quelle", "beleg", "geprueft"):
            assert eintrag.get(k), f"{jahr}: {k} fehlt"
        assert "BGBl" in eintrag["quelle"], \
            f"{jahr}: quelle ohne Fundstelle im Bundesgesetzblatt: {eintrag['quelle']!r}"
        eq(bool(re.fullmatch(r"\d{4}-\d\d-\d\d", eintrag["geprueft"])), True,
           f"{jahr}: geprueft ist kein ISO-Datum")


@case
def test_werte_sind_strings_und_exakt():
    """Als String hinterlegt, damit Decimal exakt liest — 0.1 als float wäre es
    nicht. `null` ist erlaubt und heißt „noch nicht ermittelt“; eine 0 an dieser
    Stelle wäre eine stille Falschangabe und deshalb nie richtig."""
    for jahr, eintrag in lade()["jahre"].items():
        for k, v in eintrag["tarif"].items():
            eq(isinstance(v, str), True, f"{jahr}.tarif.{k} ist {type(v).__name__}")
            eq(bool(re.fullmatch(r"\d+(\.\d+)?", v)), True,
               f"{jahr}.tarif.{k}={v!r} ist keine Dezimalzahl in Punktschreibweise")
        for k in JAHRESWERTE:
            v = eintrag[k]
            if v is None:
                continue
            eq(isinstance(v, str), True, f"{jahr}.{k} ist {type(v).__name__}, nicht str")
            eq(bool(re.fullmatch(r"\d+(\.\d+)?", v)), True,
               f"{jahr}.{k}={v!r} ist keine Dezimalzahl in Punktschreibweise")


@case
def test_leerer_jahreswert_zaehlt_als_nicht_hinterlegt():
    """Ein Jahr darf Tarif und Soli-Freigrenze haben, die von Hand gepflegten
    Pauschbeträge aber noch nicht — dann steht dort `null`. steuerlib führt das
    Jahr in diesen Tabellen dann gar nicht, und der dokumentierte Ersatzwert des
    nächstgelegenen Jahres greift, mit Warnung. Eine 0 stünde für „kein
    Pauschbetrag“ und wäre eine stille Falschangabe."""
    import tempfile
    entwurf = {"schema": 1, "jahre": {"2099": {
        "tarif": {k: "1" for k in TARIF_SCHLUESSEL},
        "soli_freigrenze": "20350",
        "freigrenze_23": None, "sparer_pb": None, "an_pauschbetrag": None,
        "quelle": "Entwurf", "geprueft": "2026-09-02"}}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8",
                                     delete=False) as f:
        json.dump(entwurf, f)
        pfad = f.name
    try:
        werte = sl._lade_steuerwerte(pfad)
    finally:
        os.unlink(pfad)
    eq(set(werte[2099]["tarif"]), set(TARIF_SCHLUESSEL), "Tarif ist da")
    eq(werte[2099]["soli_freigrenze"], D("20350"), "Soli-Freigrenze ist da")
    for k in ("freigrenze_23", "sparer_pb", "an_pauschbetrag"):
        eq(werte[2099][k], None, k)


# ── steuerlib baut daraus ────────────────────────────────────────────────────
@case
def test_steuerlib_liest_die_json():
    jahre = lade()["jahre"]
    eq(sorted(sl.TARIF), sorted(int(j) for j in jahre), "TARIF-Jahre")
    for jahr, eintrag in jahre.items():
        j = int(jahr)
        for k in TARIF_SCHLUESSEL:
            eq(sl.TARIF[j][k], D(eintrag["tarif"][k]), f"TARIF[{j}][{k}]")
        for schluessel, tabelle, name in (
                ("soli_freigrenze", sl.SOLI_FREIGRENZE, "Soli-Freigrenze"),
                ("freigrenze_23", sl.FREIGRENZE_23, "Freigrenze § 23"),
                ("sparer_pb", sl.SPARER_PB, "Sparer-Pauschbetrag"),
                ("an_pauschbetrag", sl.AN_PAUSCHBETRAG, "AN-Pauschbetrag")):
            wert = eintrag[schluessel]
            if wert is None:
                assert j not in tabelle, f"{name} {j}: null, steht aber in steuerlib"
            else:
                eq(tabelle[j], D(wert), f"{name} {j}")


@case
def test_jahr_mit_tarif_aber_ohne_pauschbetraege_bricht_nicht_ab():
    """Genau der Zustand, den fetch_steuerwerte.py für ein neues Jahr anlegt:
    § 32a-Tarif vorhanden, Pauschbeträge noch `null`. Der Report muss trotzdem
    gebaut werden — mit dem Ersatzwert des nächstgelegenen vollständigen Jahres
    und einer Warnung. Vorher lief das in einen KeyError bis in den Traceback."""
    import subprocess
    import tempfile
    daten = lade()
    letztes = max(daten["jahre"])
    kuenftig = str(int(letztes) + 1)
    daten["jahre"][kuenftig] = dict(daten["jahre"][letztes], freigrenze_23=None,
                                    sparer_pb=None, an_pauschbetrag=None,
                                    soli_freigrenze=None)
    with tempfile.TemporaryDirectory() as tmp:
        werte = os.path.join(tmp, "steuerwerte.json")
        with open(werte, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
        eingabe = os.path.join(tmp, "steuerdaten.json")
        with open(eingabe, "w", encoding="utf-8") as f:
            json.dump({"steuerjahr": int(kuenftig),
                       "steuerpflichtiger": {"verheiratet": False},
                       "anlage_n": {"bruttoarbeitslohn": "50000", "lohnsteuer": "8000"}}, f)
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "build_taxreport.py"),
             eingabe, "-o", os.path.join(tmp, "report.json")],
            capture_output=True, text=True,
            env=dict(os.environ, STEUER_DE_WERTE=werte))
        eq(p.returncode, 0, f"build_taxreport ist gescheitert:\n{p.stderr[-600:]}")
        with open(os.path.join(tmp, "report.json"), encoding="utf-8") as f:
            report = json.load(f)
    warnungen = " ".join(report.get("warnungen", []))
    assert kuenftig in warnungen and letztes in warnungen, \
        f"keine Warnung über den Ersatzwert: {warnungen!r}"
    assert report["ergebnis"].get("davon_einkommensteuer") is not None, \
        "mit hinterlegtem Tarif muss die ESt trotzdem gerechnet werden"


# ── Dokumentation sagt dasselbe ──────────────────────────────────────────────
def md_tabellen():
    """Alle Markdown-Tabellen als Liste von Zeilen, Zeile = Liste von Zellen."""
    with open(MD_PFAD, encoding="utf-8") as f:
        zeilen = f.read().splitlines()
    tabellen, aktuell = [], []
    for z in zeilen:
        if z.startswith("|"):
            aktuell.append([c.strip() for c in z.strip("|").split("|")])
        elif aktuell:
            tabellen.append(aktuell)
            aktuell = []
    if aktuell:
        tabellen.append(aktuell)
    return tabellen


def tabelle_mit(tabellen, kopfzelle):
    for t in tabellen:
        if t and t[0][0] == kopfzelle:
            return t
    raise AssertionError(f"Tabelle mit Kopfspalte {kopfzelle!r} nicht in steuerwerte.md")


@case
def test_md_freibetragstabelle_stimmt_mit_json():
    jahre = lade()["jahre"]
    t = tabelle_mit(md_tabellen(), "Wert")
    spalten = t[0][1:]                      # Jahresüberschriften
    zeilen = {r[0].replace("*", "").strip(): r[1:] for r in t[2:]}
    zuordnung = {
        "Grundfreibetrag (ledig)": lambda e: e["tarif"]["gfb"],
        "Arbeitnehmer-Pauschbetrag": lambda e: e["an_pauschbetrag"],
        "Sparer-Pauschbetrag (ledig)": lambda e: e["sparer_pb"],
        "Freigrenze § 23 EStG": lambda e: e["freigrenze_23"],
        "Soli-Freigrenze (tarifl. ESt, ledig)": lambda e: e["soli_freigrenze"],
    }
    fehlend = [j for j in jahre if j not in spalten]
    eq(fehlend, [], "Jahre der JSON ohne Spalte in steuerwerte.md")
    for label, hol in zuordnung.items():
        assert label in zeilen, f"Zeile {label!r} fehlt in steuerwerte.md"
        eq(len(zeilen[label]), len(spalten), f"Zeile {label!r} hat zu wenige Zellen")
        for spalte, zelle in zip(spalten, zeilen[label]):
            wert = hol(jahre[spalte]) if spalte in jahre else None
            if wert is None:
                continue  # Jahr nicht hinterlegt oder Wert noch nicht ermittelt
            eq(de_zahl(zelle), D(wert), f"steuerwerte.md {label} {spalte}")


@case
def test_md_tariftabelle_stimmt_mit_json():
    jahre = lade()["jahre"]
    t = tabelle_mit(md_tabellen(), "Jahr")
    # Kopf: Jahr | GFB | Zone 2 bis | Zone 3 bis | a₂ | c₃ | a₃ | k₄ | k₅
    schluessel = ["gfb", "z2", "z3", "a2", "c3", "a3", "k4", "k5"]
    eq(len(t[0]) - 1, len(schluessel), "Spaltenzahl der Tariftabelle")
    geprueft = 0
    for zeile in t[2:]:
        jahr = zeile[0].replace("*", "").strip()
        if jahr not in jahre:
            continue
        for k, zelle in zip(schluessel, zeile[1:]):
            eq(de_zahl(zelle), D(jahre[jahr]["tarif"][k]), f"steuerwerte.md Tarif {jahr} {k}")
        geprueft += 1
    eq(geprueft, len(jahre), "Jahre in der Tariftabelle")


if __name__ == "__main__":
    fails = []
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:  # Datei fehlt, JSON kaputt — genauso ein Fehlschlag
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(CASES) - len(fails)}/{len(CASES)} bestanden")
    sys.exit(1 if fails else 0)
