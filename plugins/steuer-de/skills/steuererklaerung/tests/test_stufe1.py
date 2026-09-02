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


# ── Progressionsvorbehalt ────────────────────────────────────────────────────
@case
def test_lohnersatz_hebt_die_steuer_und_wird_nicht_besteuert():
    ohne = bau(steuerdaten())
    mit = bau(steuerdaten(lohnersatzleistungen={"elterngeld": "12000"}))
    eq(mit["berechnung"]["zu_versteuerndes_einkommen"],
       ohne["berechnung"]["zu_versteuerndes_einkommen"],
       "die Leistung erhöht das zu versteuernde Einkommen nicht")
    assert (D(mit["berechnung"]["einkommensteuer_schaetzung"])
            > D(ohne["berechnung"]["einkommensteuer_schaetzung"])), "der Satz muss steigen"
    pv = mit["berechnung"]["progressionsvorbehalt"]
    eq(pv["lohnersatzleistungen"], "12000.00")
    assert D(pv["besonderer_steuersatz"]) > 0


@case
def test_lohnersatz_zieht_den_an_pauschbetrag_nicht_doppelt_ab():
    """§ 32b Abs. 2 Nr. 1: der Arbeitnehmer-Pauschbetrag mindert die Leistungen
    nur, soweit er nicht schon bei den Einkünften abgezogen wurde. Bei
    vorhandenem Arbeitslohn ist er dort verbraucht."""
    mit_lohn = bau(steuerdaten(lohnersatzleistungen={"elterngeld": "12000"}))
    eq(mit_lohn["berechnung"]["progressionsvorbehalt"]["lohnersatzleistungen"], "12000.00",
       "kein zweiter Abzug bei vorhandenem Arbeitslohn")

    ohne_lohn = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "0", "lohnsteuer": "0"},
                                lohnersatzleistungen={"elterngeld": "12000"}))
    eq(ohne_lohn["berechnung"]["progressionsvorbehalt"]["lohnersatzleistungen"], "10770.00",
       "ohne Arbeitslohn mindert der Pauschbetrag von 1.230 € die Leistungen")


@case
def test_ohne_lohnersatz_kein_abschnitt():
    eq(bau(steuerdaten())["berechnung"]["progressionsvorbehalt"], None)


@case
def test_lohnersatz_hinweis():
    r = bau(steuerdaten(lohnersatzleistungen={"elterngeld": "12000"}))
    text = " ".join(r["hinweise"])
    assert "§ 32b" in text and "steuerfrei" in text, f"Hinweis fehlt: {text!r}"


# ── Günstigerprüfung § 32d Abs. 6 ────────────────────────────────────────────
@case
def test_guenstigerpruefung_greift_bei_niedrigem_tarif():
    """Wer wenig verdient und Kapitalerträge hat, fährt mit dem Tarif besser als
    mit 25 % Abgeltungsteuer. Bisher empfahl der Report den höheren Betrag."""
    r = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "14000", "lohnsteuer": "0"},
                        anlage_kap={"kapitalertraege": "10000", "anrechenbare_kest": "0"}))
    g = r["berechnung"]["guenstigerpruefung"]
    eq(g["tarif_guenstiger"], True, "der Tarif muss hier günstiger sein")
    eq(g["angewendet"], False, "aber angewandt wird er nicht — § 32d Abs. 6 nur auf Antrag")
    assert D(g["vorteil"]) > 0, f"der Vorteil muss beziffert sein: {g}"
    assert D(g["mit_tarif"]["gesamt"]) < D(g["mit_abgeltungsteuer"]["gesamt"]), \
        f"sonst darf nicht umgeschaltet werden: {g}"


@case
def test_guenstigerpruefung_bleibt_bei_hohem_einkommen_aus():
    g = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "90000", "lohnsteuer": "20000"},
                        anlage_kap={"kapitalertraege": "10000", "anrechenbare_kest": "0"})
            )["berechnung"]["guenstigerpruefung"]
    eq(g["tarif_guenstiger"], False, "bei 42 % Grenzsteuersatz bleibt die Abgeltungsteuer")


@case
def test_guenstigerpruefung_vergleicht_mit_zuschlagsteuern():
    """§ 32d Abs. 6 verlangt den Vergleich der Einkommensteuer 'einschließlich
    Zuschlagsteuern' — bei Kirchensteuerpflicht kann das Ergebnis kippen."""
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "14000", "lohnsteuer": "0"},
                     anlage_kap={"kapitalertraege": "10000", "anrechenbare_kest": "0"})
    sd["steuerpflichtiger"] = {"name": "T", "verheiratet": False, "kirchensteuersatz": "9"}
    g = bau(sd)["berechnung"]["guenstigerpruefung"]
    for variante in ("mit_tarif", "mit_abgeltungsteuer"):
        teile = g[variante]
        summe = (D(teile["einkommensteuer"]) + D(teile["soli"])
                 + D(teile["kirchensteuer"]))
        eq(D(teile["gesamt"]), summe, f"{variante}: gesamt = ESt + Soli + KiSt")


@case
def test_beide_varianten_werden_ausgewiesen():
    """Ausgewiesen wird beides, nicht nur das bessere — sonst kann der Nutzer
    die Empfehlung nicht nachvollziehen."""
    g = bau(steuerdaten(anlage_kap={"kapitalertraege": "5000", "anrechenbare_kest": "0"})
            )["berechnung"]["guenstigerpruefung"]
    for variante in ("mit_tarif", "mit_abgeltungsteuer"):
        assert variante in g, f"{variante} fehlt: {g}"


@case
def test_guenstigerpruefung_hinweis_nennt_den_antrag():
    r = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "14000", "lohnsteuer": "0"},
                        anlage_kap={"kapitalertraege": "10000", "anrechenbare_kest": "0"}))
    text = " ".join(r["hinweise"])
    assert "Anlage KAP" in text and "ANTRAG" in text, \
        f"ohne Antrag bleibt es bei 25 % — das muss dastehen: {text!r}"


@case
def test_ohne_kapitalertraege_keine_umschaltung():
    g = bau(steuerdaten())["berechnung"]["guenstigerpruefung"]
    eq(g["tarif_guenstiger"], False, "ohne Kapitalerträge gibt es nichts umzuschalten")


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
