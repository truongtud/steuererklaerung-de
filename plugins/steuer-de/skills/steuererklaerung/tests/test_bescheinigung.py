#!/usr/bin/env python3
"""Bescheinigungen extrahieren statt abtippen.

Geprüft wird an synthetischen Fixtures — kein echtes und kein geschwärztes
Dokument. Der wichtigste Test ist test_nummer_ohne_passende_beschriftung: die
Feldnummern der Lohnsteuerbescheinigung stammen aus einer BMF-Bekanntmachung,
nicht aus dem Gesetz. Ändert sich die Nummerierung oder druckt ein Arbeitgeber
ein abweichendes Formular, darf kein falscher Betrag stillschweigend in die
Steuererklärung wandern.

Ausführen: python3 tests/test_bescheinigung.py   (oder tests/run_tests.py)
"""
import json
import os
import sys
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import parse_bescheinigung as pb  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


LSTB = "lohnsteuerbescheinigung_synthetisch.txt"


def profil():
    return pb.erkenne(fixture(LSTB), pb.lade_profile())


# ── Erkennen und Lesen ───────────────────────────────────────────────────────
@case
def test_dokument_wird_erkannt():
    p = profil()
    assert p is not None, "die Lohnsteuerbescheinigung wurde nicht erkannt"
    eq(p["id"], "lohnsteuerbescheinigung")
    eq(pb.erkenne("Werbeprospekt", pb.lade_profile()), None, "Fremdtext")


@case
def test_felder_werden_gelesen():
    werte, _ = pb.extrahiere(fixture(LSTB), profil())
    eq(werte["anlage_n.bruttoarbeitslohn"], D("78500.00"), "Nr. 3")
    eq(werte["anlage_n.lohnsteuer"], D("18420.00"), "Nr. 4")
    eq(werte["anlage_n.soli"], D("0.00"), "Nr. 5 — eine echte 0 aus dem Dokument")
    eq(werte["anlage_n.kirchensteuer"], D("1658.00"), "Nr. 6")
    eq(werte["vorsorge.kranken_pflege_basis.krankenversicherung"], D("3200.00"), "Nr. 25")


@case
def test_rentenversicherung_wird_zum_gesamtbeitrag_summiert():
    """§ 10 Abs. 3 rechnet mit dem Gesamtbeitrag. Nr. 22a und 23a zusammen
    ergeben ihn; nur der Arbeitgeberanteil geht zusätzlich in sein eigenes Feld.
    Genau hier tragen Menschen den halben Betrag ein und bekommen null Abzug —
    das ist der Hauptgrund, warum dieses Skript existiert."""
    werte, _ = pb.extrahiere(fixture(LSTB), profil())
    eq(werte["vorsorge.basisversorgung.rentenversicherung"], D("14600.00"),
       "22a + 23a")
    eq(werte["vorsorge.arbeitgeberanteil_steuerfrei"], D("7300.00"), "nur 22a")


# ── Die Sicherheitsregel ─────────────────────────────────────────────────────
@case
def test_nummer_ohne_passende_beschriftung_wird_nicht_uebernommen():
    """Die Feldnummern stammen aus einer BMF-Bekanntmachung, nicht aus dem
    Gesetz. Passt die Beschriftung nicht, wird gemeldet statt übernommen."""
    text = fixture(LSTB).replace("Bruttoarbeitslohn einschließlich Sachbezüge",
                                 "Irgendetwas ganz anderes")
    werte, meldungen = pb.extrahiere(text, profil())
    assert "anlage_n.bruttoarbeitslohn" not in werte, \
        f"der Wert hätte nicht übernommen werden dürfen: {werte}"
    assert any("3" in m for m in meldungen), f"nicht gemeldet: {meldungen}"


@case
def test_fehlendes_feld_wird_gemeldet_statt_genullt():
    text = "\n".join(z for z in fixture(LSTB).splitlines()
                     if not z.strip().startswith("25."))
    werte, meldungen = pb.extrahiere(text, profil())
    assert "vorsorge.kranken_pflege_basis.krankenversicherung" not in werte
    assert any("25" in m for m in meldungen), f"nicht gemeldet: {meldungen}"


@case
def test_unplausibler_rentenbeitrag_wird_gemeldet():
    """22a + 23a müssen ungefähr dem Beitragssatz auf den Bruttolohn entsprechen,
    gedeckelt auf die allgemeine Beitragsbemessungsgrenze. Ein Zahlendreher um
    eine Größenordnung fällt damit auf."""
    text = fixture(LSTB).replace("7.300,00", "73.000,00")
    _, meldungen = pb.extrahiere(text, profil())
    assert any("Rentenversicherung" in m or "Beitrag" in m for m in meldungen), \
        f"der unplausible Beitrag wurde nicht gemeldet: {meldungen}"


# ── Füllen der Vorlage ───────────────────────────────────────────────────────
@case
def test_leere_felder_werden_gefuellt():
    sd = {"anlage_n": {"bruttoarbeitslohn": "0.00", "lohnsteuer": ""},
          "vorsorge": {"basisversorgung": {}}}
    aenderungen = pb.fuelle(sd, {"anlage_n.bruttoarbeitslohn": D("78500.00"),
                                 "vorsorge.basisversorgung.rentenversicherung": D("14600.00")})
    eq(sd["anlage_n"]["bruttoarbeitslohn"], "78500.00")
    eq(sd["vorsorge"]["basisversorgung"]["rentenversicherung"], "14600.00", "Pfad wird angelegt")
    assert len(aenderungen) == 2, aenderungen


@case
def test_belegtes_feld_wird_nicht_still_ueberschrieben():
    """Ein vorhandener Wert kann von Hand geprüft worden sein. Ihn stillschweigend
    zu ersetzen wäre die schlechteste Variante."""
    sd = {"anlage_n": {"bruttoarbeitslohn": "70000.00"}}
    aenderungen = pb.fuelle(sd, {"anlage_n.bruttoarbeitslohn": D("78500.00")})
    eq(sd["anlage_n"]["bruttoarbeitslohn"], "70000.00", "unverändert")
    assert any("Konflikt" in a for a in aenderungen), aenderungen

    pb.fuelle(sd, {"anlage_n.bruttoarbeitslohn": D("78500.00")}, ueberschreiben=True)
    eq(sd["anlage_n"]["bruttoarbeitslohn"], "78500.00", "mit --ueberschreiben schon")


@case
def test_gleicher_wert_ist_kein_konflikt():
    """Dasselbe Dokument zweimal einzulesen darf nichts melden."""
    sd = {"anlage_n": {"bruttoarbeitslohn": "78500.00"}}
    aenderungen = pb.fuelle(sd, {"anlage_n.bruttoarbeitslohn": D("78500.00")})
    eq(aenderungen, [], f"unverändert eingelesen, trotzdem gemeldet: {aenderungen}")


@case
def test_gefuellte_datei_laeuft_durch_den_report():
    """Der eigentliche Zweck: aus Dokumenten wird eine Datei, die die Pipeline
    ohne gemeldetes Feld verarbeitet."""
    import build_taxreport as bt
    import neue_steuerdaten as ns
    sd = ns.steuerdaten(jahr=2024, taetigkeiten=["angestellt"])
    werte, _ = pb.extrahiere(fixture(LSTB), profil())
    pb.fuelle(sd, werte)
    eq(bt.pruefe_unbekannte_felder(sd), [], "die gefüllte Datei meldet eigene Felder")
    eq(sd["anlage_n"]["bruttoarbeitslohn"], "78500.00")


@case
def test_eine_echte_null_gilt_als_beantwortet():
    """Nr. 5 der Bescheinigung steht auf 0,00 € — kein Solidaritätszuschlag
    einbehalten. Das ist eine Antwort, keine Lücke. Sie danach unter „noch
    offen“ zu führen schickte den Nutzer eine Bescheinigung suchen, die er
    schon eingelesen hat."""
    sd = {"anlage_n": {"soli": "0.00", "lohnsteuer": "0.00"}}
    gefuellt = {"anlage_n.soli"}
    offen = pb.fehlende_felder(sd, beantwortet=gefuellt)
    assert "anlage_n.soli" not in offen, f"eine gelesene 0 ist beantwortet: {offen}"
    assert "anlage_n.lohnsteuer" in offen, f"das unberührte Feld fehlt weiter: {offen}"


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
