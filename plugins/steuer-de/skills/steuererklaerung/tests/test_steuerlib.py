#!/usr/bin/env python3
"""Goldwert-Tests für scripts/steuerlib.py. Ausführen: python3 tests/run_tests.py"""
import sys, os
from datetime import date, datetime
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import steuerlib as sl  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


# ── Zahlenparser ─────────────────────────────────────────────────────────────
@case
def test_zahlen_de_en():
    eq(sl.to_decimal("1.234,56"), D("1234.56"), "DE mit Tausender")
    eq(sl.to_decimal("1,234.56"), D("1234.56"), "EN mit Tausender")
    eq(sl.to_decimal("1234,56"), D("1234.56"), "DE ohne Tausender")
    eq(sl.to_decimal("1234.56"), D("1234.56"), "EN ohne Tausender")
    eq(sl.to_decimal("12.000,00"), D("12000.00"), "DE 5-stellig")
    eq(sl.to_decimal("12,000.00"), D("12000.00"), "EN 5-stellig")
    eq(sl.to_decimal("1.234.567,89"), D("1234567.89"), "DE zwei Tausender")


@case
def test_zahlen_vorzeichen():
    eq(sl.to_decimal("-1.234,56"), D("-1234.56"), "ASCII-Minus")
    eq(sl.to_decimal("−5.334,40"), D("-5334.40"), "Unicode-Minus U+2212")
    eq(sl.to_decimal("–5.334,40"), D("-5334.40"), "En-Dash als Minus")
    eq(sl.to_decimal("(1.234,56)"), D("-1234.56"), "Klammer-Notation")
    eq(sl.to_decimal("1.234,56-"), D("-1234.56"), "nachgestelltes Minus")


@case
def test_zahlen_waehrung_und_hint():
    eq(sl.to_decimal("1.234,56 EUR"), D("1234.56"), "Währungssuffix")
    eq(sl.to_decimal("€ 12,34"), D("12.34"), "Währungspräfix")
    eq(sl.to_decimal("1.000 EUR"), D("1000"), "Tausender ohne Nachkomma")
    eq(sl.to_decimal("1.234", locale_hint="de"), D("1234"), "Hint de")
    eq(sl.to_decimal("1.234", locale_hint="en"), D("1.234"), "Hint en")
    eq(sl.to_decimal("0,5"), D("0.5"), "führende Null bleibt Dezimal")


@case
def test_zahlen_werfen_statt_nullen():
    for bad in ["", "n/a", None, "abc", "--5"]:
        try:
            sl.to_decimal(bad)
        except sl.ParseError:
            continue
        raise AssertionError(f"{bad!r} hätte ParseError werfen müssen")


@case
def test_locale_erkennung():
    eq(sl.detect_locale("Gewinn 1.234,56 Kosten 4.000,00"), "de", "DE-Text")
    eq(sl.detect_locale("Gain 1,234.56 Cost 4,000.00"), "en", "EN-Text")


@case
def test_formatierung():
    eq(sl.fmt_eur("1234.5"), "1.234,50 €")
    eq(sl.fmt_eur("-1234.5"), "-1.234,50 €")
    eq(sl.fmt_eur(None), "—", "unlesbar → Gedankenstrich, nicht 'None'")
    eq(sl.de_dezimal("62000.00"), "62000,00")
    eq(sl.de_dezimal("2024"), "2024", "Jahreszahl bleibt unangetastet")
    eq(sl.de_dezimal("2015-04-02"), "2015-04-02", "Datum bleibt unangetastet")
    eq(sl.csv_safe('=HYPERLINK("x")'), "'=HYPERLINK(\"x\")", "Formel entschärft")


# ── Datum und Fristen ────────────────────────────────────────────────────────
@case
def test_datum():
    eq(sl.parse_datetime("2024-01-02"), datetime(2024, 1, 2), "ISO bleibt ISO")
    eq(sl.parse_datetime("2024-12-31 23:59:59"), datetime(2024, 12, 31, 23, 59, 59))
    eq(sl.parse_datetime("2024-01-02T10:00:00Z"), datetime(2024, 1, 2, 10, 0, 0))
    eq(sl.parse_datetime("02.01.2024"), datetime(2024, 1, 2), "DE-Datum")
    eq(sl.parse_datetime("15/03/2024"), datetime(2024, 3, 15), "dayfirst")
    eq(sl.parse_datetime("03/15/2024"), datetime(2024, 3, 15), "Monat > 12 korrigiert")
    assert sl.date_ambiguous("03/07/2024") and not sl.date_ambiguous("15/03/2024")


@case
def test_haltefrist():
    # § 108 AO / § 188 Abs. 2 BGB: Frist endet mit Ablauf des Jahrestages.
    assert not sl.haltefrist_erfuellt(date(2023, 1, 10), date(2024, 1, 10)), \
        "Verkauf am Jahrestag ist noch steuerpflichtig"
    assert sl.haltefrist_erfuellt(date(2023, 1, 10), date(2024, 1, 11)), \
        "ab dem Folgetag steuerfrei"
    assert not sl.haltefrist_erfuellt(date(2023, 3, 1), date(2024, 2, 29)), \
        "Schaltjahr: 365 Tage reichen nicht"
    assert sl.haltefrist_erfuellt(date(2023, 3, 1), date(2024, 3, 2))
    # 29.02. als Anschaffungstag → § 188 Abs. 3 BGB
    eq(sl.jahresfrist_ende(date(2024, 2, 29)), date(2025, 2, 28))
    assert sl.haltefrist_erfuellt(date(2024, 2, 29), date(2025, 3, 1))
    # Uhrzeit darf nichts ändern
    assert not sl.haltefrist_erfuellt(datetime(2023, 1, 10, 23, 0), datetime(2024, 1, 10, 1, 0))


# ── Tarif ────────────────────────────────────────────────────────────────────
@case
def test_tarif_stuetzpunkte():
    # Grundfreibetrag: exakt darauf → 0, ein Euro darüber → > 0
    for jahr, gfb in [(2023, 10908), (2024, 11784), (2025, 12096), (2026, 12348)]:
        eq(sl.est_grundtarif(D(gfb), jahr), D("0"), f"GFB {jahr}")
        assert sl.est_grundtarif(D(gfb + 100), jahr) > 0, f"über GFB {jahr}"


@case
def test_tarif_zonenuebergaenge_stetig():
    """Der Tarif muss an den Zonengrenzen stetig sein — das findet falsche Konstanten."""
    for jahr, t in sl.TARIF.items():
        for grenze in (t["z2"], t["z3"], t["z4"]):
            a = sl.est_grundtarif(grenze, jahr)
            b = sl.est_grundtarif(grenze + 1, jahr)
            assert abs(b - a) < 2, f"Sprung bei {jahr} an Grenze {grenze}: {a} → {b}"


@case
def test_tarif_2024_ist_die_geaenderte_fassung():
    # Nach dem Gesetz zur steuerlichen Freistellung des Existenzminimums 2024
    # (GFB 11.784) liegt die ESt unter der Fassung mit GFB 11.604.
    eq(sl.TARIF[2024]["gfb"], D("11784"), "GFB 2024 rückwirkend erhöht")
    eq(sl.est_grundtarif(D("11700"), 2024), D("0"), "11.700 € sind 2024 steuerfrei")


@case
def test_splitting():
    est_einzel = sl.est_grundtarif(D("30000"), 2025)
    eq(sl.est_tarif(D("60000"), 2025, True), est_einzel * 2, "Splitting = 2 × ESt(zvE/2)")


@case
def test_abrundung():
    for jahr in sl.TARIF:
        v = sl.est_grundtarif(D("54321"), jahr)
        eq(v, v.to_integral_value(), f"{jahr}: ESt auf volle Euro abgerundet")


@case
def test_unbekanntes_jahr():
    eq(sl.est_grundtarif(D("50000"), 2019), None, "kein Tarif → None statt Fantasiewert")


# ── Soli ─────────────────────────────────────────────────────────────────────
@case
def test_soli_milderungszone():
    eq(sl.soli(D("18130"), 2024), D("0.00"), "auf der Freigrenze")
    eq(sl.soli(D("18200"), 2024), D("8.33"), "Milderungszone 11,9 % des Überhangs")
    eq(sl.soli(D("25000"), 2024), D("817.53"), "noch Milderungszone")
    eq(sl.soli(D("100000"), 2024), D("5500.00"), "voller Satz 5,5 %")


@case
def test_soli_splitting_und_jahre():
    eq(sl.soli(D("30000"), 2024, True), D("0.00"), "Freigrenze verdoppelt sich")
    eq(sl.soli(D("19950"), 2025), D("0.00"), "Freigrenze 2025")
    eq(sl.soli(D("20350"), 2026), D("0.00"), "Freigrenze 2026")
    assert sl.soli(D("20000"), 2025) > 0


# ── Kirchensteuersatz ────────────────────────────────────────────────────────
@case
def test_kirchensteuersatz():
    eq(sl.normiere_kirchensteuersatz("0.09"), D("0.09"))
    eq(sl.normiere_kirchensteuersatz("9"), D("9") / D("100"), "9 → 9 %")
    eq(sl.normiere_kirchensteuersatz(0), None)
    for bad in ["0.5", "90"]:
        try:
            sl.normiere_kirchensteuersatz(bad)
        except sl.ParseError:
            continue
        raise AssertionError(f"{bad} hätte abgelehnt werden müssen")


# ── Jahreswerte ──────────────────────────────────────────────────────────────
@case
def test_jahreswerte():
    eq(sl.freigrenze_23(2023), D("600"))
    eq(sl.freigrenze_23(2024), D("1000"))
    eq(sl.sparer_pauschbetrag(2022), D("801"), "2022 noch 801 €")
    eq(sl.sparer_pauschbetrag(2025, True), D("2000"))
    eq(sl.an_pauschbetrag(2022), D("1200"), "2022 noch 1.200 €")
    try:
        sl.freigrenze_23(2035)
    except KeyError:
        pass
    else:
        raise AssertionError("unbekanntes Jahr muss auffallen")


# ── Summenabgleich ───────────────────────────────────────────────────────────
@case
def test_summenabgleich():
    ok = [sl.Abgleich("§ 23", D("100.00"), D("100.00"))]
    sl.pruefe_summen(ok)
    schlecht = [sl.Abgleich("§ 23", D("100.00"), D("3500.00"))]
    try:
        sl.pruefe_summen(schlecht)
    except sl.PlausibilityError:
        return
    raise AssertionError("Abweichung hätte auffallen müssen")


if __name__ == "__main__":
    fails = []
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(CASES) - len(fails)}/{len(CASES)} bestanden")
    sys.exit(1 if fails else 0)
