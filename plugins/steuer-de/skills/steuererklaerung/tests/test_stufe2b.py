#!/usr/bin/env python3
"""Kinder: Günstigerprüfung nach § 31 EStG und die Bemessung der Zuschlagsteuern.

Der Fallstrick steckt nicht in der Günstigerprüfung selbst, sondern daneben:
§ 3 Abs. 2 SolZG und § 51a Abs. 2 Satz 1 EStG verlangen beide, die Zuschlagsteuern
nach einer Einkommensteuer zu bemessen, die „unter Berücksichtigung von
Freibeträgen nach § 32 Absatz 6 in allen Fällen“ festzusetzen wäre — auch dann,
wenn das Kindergeld gewonnen hat und die Freibeträge gar nicht abgezogen wurden.

Ausführen: python3 tests/test_stufe2b.py   (oder tests/run_tests.py)
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


KIND = {"name": "Anna", "geburtsdatum": "2015-04-02"}


def steuerdaten(**over):
    sd = {
        "steuerjahr": 2026,
        "steuerpflichtiger": {"name": "Test", "verheiratet": False},
        "anlage_n": {"bruttoarbeitslohn": "50000", "lohnsteuer": "8000"},
    }
    sd.update(over)
    return sd


def bau(sd):
    with redirect_stderr(io.StringIO()):
        return bt.build(sd, [])


@case
def test_kinderfreibetrag_gewinnt_bei_hohem_einkommen():
    """Ab einem gewissen Einkommen ist der Freibetrag mehr wert als das
    Kindergeld — dann wird er abgezogen und das Kindergeld hinzugerechnet
    (§ 31 Satz 5)."""
    r = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "150000", "lohnsteuer": "50000"},
                        kinder=[KIND]))
    k = r["berechnung"]["kinder"]
    eq(k["gerechnet"], True)
    eq(k["freibetrag_guenstiger"], True, "bei 150.000 € schlägt der Freibetrag")
    eq(k["freibetraege"], "4878.00", "ledig: einfacher Satz je Kind")
    eq(k["kindergeld_anspruch"], "1554.00", "ledig: halber Anspruch, wie der halbe Freibetrag")


@case
def test_kindergeld_gewinnt_bei_kleinem_einkommen():
    """Der Umschlagpunkt liegt beim Grenzsteuersatz, ab dem die Entlastung durch
    den Freibetrag das Kindergeld übersteigt — hier 1.554 / 4.878 = 31,9 %. Bei
    35.000 € Bruttolohn ist er noch nicht erreicht."""
    k = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "35000", "lohnsteuer": "5000"},
                        kinder=[KIND]))["berechnung"]["kinder"]
    eq(k["gerechnet"], True)
    eq(k["freibetrag_guenstiger"], False, "bei 35.000 € gewinnt das Kindergeld")


@case
def test_umschlagpunkt_liegt_zwischen_40000_und_50000():
    """Hält den Umschlag fest: er hängt am Grenzsteuersatz, nicht an einer
    gerundeten Faustregel. Verschiebt sich der Tarif, muss dieser Test es sagen."""
    def guenstiger(brutto):
        return bau(steuerdaten(
            anlage_n={"bruttoarbeitslohn": brutto, "lohnsteuer": "0"}, kinder=[KIND]
        ))["berechnung"]["kinder"]["freibetrag_guenstiger"]
    eq(guenstiger("40000"), False, "bei 40.000 € noch Kindergeld")
    eq(guenstiger("50000"), True, "bei 50.000 € schon der Freibetrag")


@case
def test_zuschlagsteuern_immer_mit_kinderfreibetrag():
    """§ 3 Abs. 2 SolZG und § 51a Abs. 2 Satz 1 EStG: die Bemessungsgrundlage ist
    die Einkommensteuer unter Abzug der Kinderfreibeträge — 'in allen Fällen'.
    Die festgesetzte Steuer bleibt unverändert, die Kirchensteuer sinkt trotzdem.
    Wer das übersieht, setzt sie bei Familien zu hoch an."""
    basis = {"name": "T", "verheiratet": False, "kirchensteuersatz": "9"}
    lohn = {"bruttoarbeitslohn": "35000", "lohnsteuer": "5000"}
    ohne = bau(steuerdaten(steuerpflichtiger=basis, anlage_n=lohn))
    mit = bau(steuerdaten(steuerpflichtiger=basis, anlage_n=lohn, kinder=[KIND]))

    eq(mit["berechnung"]["kinder"]["freibetrag_guenstiger"], False,
       "Voraussetzung dieses Tests: das Kindergeld gewinnt")
    eq(mit["berechnung"]["einkommensteuer_schaetzung"],
       ohne["berechnung"]["einkommensteuer_schaetzung"],
       "die festgesetzte Einkommensteuer ändert sich dadurch nicht")
    assert (D(mit["berechnung"]["kirchensteuer_schaetzung"])
            < D(ohne["berechnung"]["kirchensteuer_schaetzung"])), \
        (f"die Kirchensteuer muss trotzdem sinken: "
         f"{mit['berechnung']['kirchensteuer_schaetzung']} vs. "
         f"{ohne['berechnung']['kirchensteuer_schaetzung']}")


@case
def test_bemessungsgrundlage_der_zuschlagsteuern_wird_ausgewiesen():
    """Die fiktive Steuer ist eine zweite Zahl neben der festgesetzten — sie
    gehört ausgewiesen, sonst wirkt die Kirchensteuer willkürlich."""
    r = bau(steuerdaten(steuerpflichtiger={"name": "T", "verheiratet": False,
                                           "kirchensteuersatz": "9"},
                        anlage_n={"bruttoarbeitslohn": "35000", "lohnsteuer": "5000"},
                        kinder=[KIND]))
    k = r["berechnung"]["kinder"]
    assert D(k["est_fuer_zuschlagsteuern"]) < D(r["berechnung"]["einkommensteuer_schaetzung"]), \
        "die fiktive Steuer liegt unter der festgesetzten"


@case
def test_ohne_hinterlegte_kinderwerte_wird_nicht_gerechnet():
    """Für 2024 sind keine Kinderwerte hinterlegt. Dann wird die Prüfung NICHT
    mit dem Wert eines Nachbarjahres gerechnet, sondern gar nicht — und der
    Report sagt es."""
    r = bau(steuerdaten(steuerjahr=2024, kinder=[KIND]))
    eq(r["berechnung"]["kinder"]["gerechnet"], False)
    # Gemeldet wird das in der Unsicherheitsbilanz, nicht als Warnung: Warnungen
    # sind für Eingabeprobleme da, die Bilanz für Nichtgerechnetes.
    posten = [p_ for p_ in r["unsicherheit"]["posten"] if "Kinderfreibetrag" in p_["posten"]]
    assert posten, r["unsicherheit"]["posten"]
    assert "2024" in posten[0]["posten"], posten[0]
    eq(posten[0]["richtung"], "zu hoch")


@case
def test_gerechnete_kinder_stehen_nicht_in_der_bilanz():
    r = bau(steuerdaten(kinder=[KIND]))
    namen = " ".join(p_["posten"] for p_ in r["unsicherheit"]["posten"])
    assert "Kinderfreibetrag" not in namen, namen


@case
def test_ohne_kinder_kein_abschnitt():
    eq(bau(steuerdaten())["berechnung"]["kinder"], None)


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
