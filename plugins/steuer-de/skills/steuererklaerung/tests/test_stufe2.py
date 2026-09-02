#!/usr/bin/env python3
"""Zusammenspiel der Stufe-2-Posten in build_taxreport.

Die Formeln selbst prüft tests/test_steuerlib.py. Hier geht es darum, dass sie
in der richtigen Reihenfolge greifen und dass alte Eingabedateien nicht brechen.

Ausführen: python3 tests/test_stufe2.py   (oder tests/run_tests.py)
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



# ── Vorsorgeaufwendungen ─────────────────────────────────────────────────────
@case
def test_vorsorge_wird_gedeckelt_statt_voll_abgezogen():
    """Bisher wurde jeder Betrag voll abgezogen — das zvE war zu niedrig und
    damit die geschätzte Steuer."""
    r = bau(steuerdaten(vorsorge={"basisversorgung": {"rentenversicherung": "99000"}}))
    eq(r["berechnung"]["abzug_vorsorge"], "27566.00", "auf den Höchstbetrag 2024 gedeckelt")


@case
def test_vorsorge_arbeitgeberanteil_mindert_den_abzug():
    r = bau(steuerdaten(vorsorge={"basisversorgung": {"rentenversicherung": "10000"},
                                  "arbeitgeberanteil_steuerfrei": "4000"}))
    eq(r["berechnung"]["abzug_vorsorge"], "6000.00", "§ 10 Abs. 3 Satz 5")


@case
def test_vorsorge_details_im_report():
    r = bau(steuerdaten(vorsorge={"basisversorgung": {"rentenversicherung": "8000"},
                                  "kranken_pflege_basis": {"kv": "4000"},
                                  "sonstige": {"haftpflicht": "300"}}))
    d = r["berechnung"]["vorsorge_details"]
    eq(d["hoechstbetrag_basisversorgung"], "27566", "Höchstbetrag wird ausgewiesen")
    eq(d["basisversorgung_abziehbar"], "8000.00")
    eq(d["sonstige_abziehbar"], "4000.00", "§ 10 Abs. 4 Satz 4: Basis-KV voll abziehbar")
    eq(d["sonstige_nicht_abziehbar"], "300.00", "die Haftpflicht entfällt daneben")


@case
def test_alter_flacher_vorsorgeblock_wird_weiter_gelesen():
    """Rückwärtskompatibilität: alte Dateien ohne Gliederung brechen nicht, der
    Report sagt aber, dass ohne sie nicht gedeckelt werden kann."""
    r = bau(steuerdaten(vorsorge={"rentenversicherung": "8000", "krankenversicherung": "3000"}))
    eq(r["berechnung"]["abzug_vorsorge"], "11000.00", "unverändert voll abgezogen")
    text = " ".join(r["disclaimer"])
    assert "Gliederung" in text and "ZU NIEDRIG" in text, \
        f"die fehlende Gliederung und ihre Richtung müssen im Disclaimer stehen: {text!r}"
    eq(r["berechnung"]["vorsorge_details"], None, "ohne Gliederung keine Detailrechnung")


@case
def test_arbeitgeberanteil_groesser_als_beitrag_faellt_auf():
    """§ 10 Abs. 3 rechnet mit dem GESAMTBEITRAG (Arbeitnehmer- und
    Arbeitgeberanteil) und zieht den Arbeitgeberanteil danach ab. Wer nur seinen
    eigenen Anteil einträgt und zusätzlich den Arbeitgeberanteil angibt, bekommt
    null Abzug — das ist die wahrscheinlichste Fehleingabe hier und darf nicht
    still passieren."""
    r = bau(steuerdaten(vorsorge={"basisversorgung": {"rentenversicherung": "7300"},
                                  "arbeitgeberanteil_steuerfrei": "7300"}))
    text = " ".join(r["warnungen"])
    assert "GESAMTBEITRAG" in text and "22a" in text, \
        f"die Falle und die Fundstelle in der Lohnsteuerbescheinigung fehlen: {text!r}"
    eq(r["berechnung"]["vorsorge_details"]["basisversorgung_abziehbar"], "0.00",
       "gerechnet wird trotzdem, was dasteht — nur eben mit Warnung")

    # Der Normalfall — Gesamtbeitrag eingetragen, Arbeitgeber trägt die Hälfte —
    # darf nicht warnen, sonst stumpft die Meldung ab.
    ok = bau(steuerdaten(vorsorge={"basisversorgung": {"rentenversicherung": "14600"},
                                   "arbeitgeberanteil_steuerfrei": "7300"}))
    assert not any("GESAMTBEITRAG" in w for w in ok["warnungen"]), \
        f"Fehlalarm im Normalfall: {ok['warnungen']}"
    eq(ok["berechnung"]["vorsorge_details"]["basisversorgung_abziehbar"], "7300.00")


# ── zumutbare Belastung ──────────────────────────────────────────────────────
@case
def test_agb_aufwendungen_werden_um_die_zumutbare_belastung_gekuerzt():
    """Neu: 'aufwendungen' ist der Bruttobetrag, den der Report selbst kürzt —
    bisher musste der Nutzer die Kürzung selbst rechnen."""
    r = bau(steuerdaten(aussergewoehnliche_belastungen={"aufwendungen": "5000"}))
    d = r["berechnung"]["agb_details"]
    eq(d["aufwendungen"], "5000.00")
    # GdE 48.770 (50.000 − 1.230 AN-Pauschbetrag): 5 % von 15.340 + 6 % vom Rest
    eq(d["zumutbare_belastung"], "2772.80", "§ 33 Abs. 3, stufenweise")
    eq(r["berechnung"]["abzug_agb"], "2227.20", "5.000 − 2.772,80")


@case
def test_agb_unter_der_zumutbaren_belastung_wirkt_gar_nicht():
    r = bau(steuerdaten(aussergewoehnliche_belastungen={"aufwendungen": "500"}))
    eq(r["berechnung"]["abzug_agb"], "0.00", "nichts übersteigt die zumutbare Belastung")
    text = " ".join(r["hinweise"])
    assert "zumutbare Belastung" in text, f"das muss erklärt werden: {text!r}"


@case
def test_altes_feld_anzusetzen_wird_unveraendert_uebernommen():
    """Rückwärtskompatibilität: wer weiterhin den selbst gekürzten Betrag
    einträgt, bekommt ihn unverändert — sonst würde doppelt gekürzt."""
    r = bau(steuerdaten(aussergewoehnliche_belastungen={"anzusetzen": "1500"}))
    eq(r["berechnung"]["abzug_agb"], "1500.00", "keine zweite Kürzung")


# ── Unsicherheitsbilanz ──────────────────────────────────────────────────────
@case
def test_unsicherheitsbilanz_nennt_richtung_und_fundstelle():
    """Der Disclaimer zählte bisher auf, was fehlt — aber nicht, in welche
    Richtung es wirkt. Weil sich die Abweichungen nicht aufheben, konnte der
    Leser die Zahl nicht einordnen."""
    u = bau(steuerdaten())["unsicherheit"]
    assert u["posten"], "die Bilanz darf nicht leer sein"
    for p_ in u["posten"]:
        assert p_["richtung"] in ("zu niedrig", "zu hoch", "offen"), p_
        assert p_["fundstelle"], f"jeder Posten braucht eine Fundstelle: {p_}"
    assert u["gesamtrichtung"] in (
        "Schätzung eher zu niedrig", "Schätzung eher zu hoch", "uneindeutig",
        "keine gerichtete Abweichung erkennbar"), u


@case
def test_posten_ohne_bekannte_wirkung_faerbt_das_gesamtbild_nicht():
    """Die Beamten-Kürzung greift nur bei einem Personenkreis, den die Daten
    nicht hergeben. Als gerichteter Posten würde sie jedem Angestellten ein
    'zu niedrig' anhängen, das für ihn nicht stimmt."""
    r = bau(steuerdaten(vorsorge={"basisversorgung": {"rentenversicherung": "8000"}}))
    beamte = [p_ for p_ in r["unsicherheit"]["posten"] if "Beamte" in p_["posten"]]
    assert beamte, r["unsicherheit"]["posten"]
    eq(beamte[0]["richtung"], "offen")
    eq(r["unsicherheit"]["gesamtrichtung"], "keine gerichtete Abweichung erkennbar")


@case
def test_gerechnete_posten_stehen_nicht_mehr_in_der_bilanz():
    """Was Stufe 1 und 2 rechnen, darf nicht weiter als Lücke geführt werden —
    sonst verlieren die übrigen Posten an Gewicht."""
    r = bau(steuerdaten(vorsorge={"basisversorgung": {"rentenversicherung": "8000"}},
                        aussergewoehnliche_belastungen={"aufwendungen": "5000"}))
    namen = " ".join(p_["posten"] for p_ in r["unsicherheit"]["posten"])
    for erledigt in ("Höchstbetragsberechnung", "Progressionsvorbehalt",
                     "zumutbare Belastung"):
        assert erledigt not in namen, f"{erledigt!r} wird gerechnet, steht aber drin: {namen}"


@case
def test_ungegliederte_vorsorge_erscheint_als_posten():
    r = bau(steuerdaten(vorsorge={"rentenversicherung": "8000"}))
    posten = {p_["posten"]: p_ for p_ in r["unsicherheit"]["posten"]}
    treffer = [p_ for name, p_ in posten.items() if "Vorsorge" in name]
    assert treffer, f"die ungedeckelte Vorsorge fehlt: {list(posten)}"
    eq(treffer[0]["richtung"], "zu niedrig", "voller Abzug → Steuer zu niedrig")


@case
def test_guenstigerpruefung_erscheint_mit_beziffertem_betrag():
    r = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "14000", "lohnsteuer": "0"},
                        anlage_kap={"kapitalertraege": "10000", "anrechenbare_kest": "0"}))
    treffer = [p_ for p_ in r["unsicherheit"]["posten"] if "nstigerpr" in p_["posten"]]
    assert treffer, r["unsicherheit"]["posten"]
    eq(treffer[0]["richtung"], "zu hoch")
    assert treffer[0]["groessenordnung"], "hier ist der Betrag bekannt und gehört genannt"


# ── Offene Veranlagungszeiträume ─────────────────────────────────────────────
@case
def test_hinweis_auf_offene_veranlagungszeitraeume():
    """Für viele der greifbarste Nutzen: dass ein zurückliegendes Jahr noch
    abgegeben werden kann. Vier Jahre rückwirkend, § 169 Abs. 2 Nr. 2 AO."""
    text = " ".join(bau(steuerdaten())["hinweise"])
    assert "Antragsveranlagung" in text and "§ 46 Abs. 2 Nr. 8" in text, \
        f"der Hinweis fehlt: {text[:400]!r}"


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
