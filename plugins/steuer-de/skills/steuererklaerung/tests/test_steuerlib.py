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


# ── Fristen nach der Abgabenordnung ──────────────────────────────────────────
@case
def test_ostersonntag():
    """Basis für Karfreitag, Ostermontag, Himmelfahrt und Pfingstmontag."""
    eq(sl.ostersonntag(2024), date(2024, 3, 31))
    eq(sl.ostersonntag(2025), date(2025, 4, 20))
    eq(sl.ostersonntag(2026), date(2026, 4, 5))


@case
def test_bekanntgabe_am_vierten_tag():
    """§ 122 Abs. 2 Nr. 1 AO — seit dem Postrechtsmodernisierungsgesetz der
    VIERTE Tag, nicht mehr der dritte. Wer mit drei rechnet, nennt eine zu kurze
    Einspruchsfrist, und das ist hier die teuerste Sorte Fehler."""
    eq(sl.bekanntgabe(date(2026, 3, 2)), date(2026, 3, 6), "Montag + 4 = Freitag")


@case
def test_bekanntgabe_verschiebt_sich_auf_den_werktag():
    eq(sl.bekanntgabe(date(2026, 3, 5)), date(2026, 3, 9), "Donnerstag + 4 = Montag")
    eq(sl.bekanntgabe(date(2026, 3, 4)), date(2026, 3, 9), "Sonntag → Montag")


@case
def test_einspruchsfrist_ein_monat():
    """§ 355 Abs. 1 AO: ein Monat nach Bekanntgabe; das Ende wird nach
    § 108 Abs. 3 AO auf den nächsten Werktag geschoben."""
    eq(sl.einspruchsfrist_ende(date(2026, 3, 6)), date(2026, 4, 7),
       "6.4.2026 ist Ostermontag")
    eq(sl.einspruchsfrist_ende(date(2026, 1, 15)), date(2026, 2, 16),
       "15.2.2026 ist ein Sonntag")


@case
def test_einspruchsfrist_am_monatsende():
    """§ 188 Abs. 3 BGB: gibt es den Tag im Folgemonat nicht, endet die Frist mit
    dessen letztem Tag."""
    eq(sl.einspruchsfrist_ende(date(2026, 1, 31)), date(2026, 3, 2),
       "28.2.2026 ist ein Samstag → Montag")


@case
def test_feiertage_sind_keine_werktage():
    assert not sl.ist_werktag(date(2026, 4, 6)), "Ostermontag"
    assert not sl.ist_werktag(date(2026, 10, 3)), "Tag der Deutschen Einheit"
    assert not sl.ist_werktag(date(2026, 12, 26)), "zweiter Weihnachtstag"
    assert sl.ist_werktag(date(2026, 4, 7)), "Dienstag nach Ostern"


@case
def test_offene_veranlagungszeitraeume():
    """§ 169 Abs. 2 Nr. 2 i.V.m. § 170 Abs. 1 AO: vier Jahre ab Ablauf des
    Kalenderjahres. Am 02.09.2026 ist 2022 noch offen — bis 31.12.2026."""
    offen = dict(sl.offene_veranlagungszeitraeume(date(2026, 9, 2)))
    assert 2022 in offen, f"2022 muss offen sein: {sorted(offen)}"
    eq(offen[2022], date(2026, 12, 31))
    assert 2021 not in offen, "2021 ist abgelaufen"


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
def test_est_aus_tarif_rechnet_mit_uebergebenen_werten():
    """Damit fetch_steuerwerte.py frisch geladene Werte prüfen kann, bevor sie
    in die JSON wandern — ohne die Zonenformel ein zweites Mal zu schreiben."""
    eq(sl.est_aus_tarif(D("50000"), sl.TARIF[2025]), sl.est_grundtarif(D("50000"), 2025),
       "gleiche Werte → gleiches Ergebnis")
    erfunden = dict(sl.TARIF[2025], gfb=D("99000"))
    eq(sl.est_aus_tarif(D("50000"), erfunden), D("0"), "unter dem übergebenen Grundfreibetrag")


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


# ── Progressionsvorbehalt ────────────────────────────────────────────────────
@case
def test_progressionsvorbehalt_hebt_den_satz():
    """§ 32b: der Satz bemisst sich nach zvE + Lohnersatz, angewandt wird er auf
    das zvE. Die Leistung selbst bleibt steuerfrei."""
    ohne = sl.est_grundtarif(D("30000"), 2024)
    mit = sl.est_mit_progressionsvorbehalt(D("30000"), D("12000"), 2024)
    assert mit > ohne, f"Elterngeld muss den Satz heben: {mit} vs. {ohne}"
    assert mit < sl.est_grundtarif(D("42000"), 2024), \
        "die Leistung darf nicht wie Einkommen besteuert werden"


@case
def test_progressionsvorbehalt_ohne_leistung_aendert_nichts():
    eq(sl.est_mit_progressionsvorbehalt(D("30000"), D("0"), 2024),
       sl.est_grundtarif(D("30000"), 2024), "ohne Lohnersatz identisch")


@case
def test_besonderer_steuersatz_vier_nachkommastellen():
    satz = sl.besonderer_steuersatz(D("30000"), D("12000"), 2024)
    eq(satz, satz.quantize(D("0.0001")), "auf vier Nachkommastellen festgelegt")
    assert D("0") < satz < D("0.45"), f"unplausibler Satz: {satz}"


@case
def test_progressionsvorbehalt_ohne_tarif_ist_none():
    eq(sl.est_mit_progressionsvorbehalt(D("30000"), D("12000"), 2019), None)
    eq(sl.besonderer_steuersatz(D("30000"), D("12000"), 2019), None)


@case
def test_progressionsvorbehalt_splitting():
    einzel = sl.est_mit_progressionsvorbehalt(D("30000"), D("12000"), 2024)
    zusammen = sl.est_mit_progressionsvorbehalt(D("60000"), D("24000"), 2024, True)
    eq(zusammen, einzel * 2, "Splitting: doppeltes zvE und doppelte Leistung")


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


# ── § 35a ────────────────────────────────────────────────────────────────────
@case
def test_35a_zwanzig_prozent_je_topf():
    # § 35a Abs. 1/2/3: je 20 %, gedeckelt auf 510 / 4.000 / 1.200 €.
    eq(sl.steuerermaessigung_35a(D("0"), D("1000"), D("0")), D("200.00"), "20 % von 1.000")
    eq(sl.steuerermaessigung_35a(D("1000"), D("0"), D("0")), D("200.00"), "Minijob unter Deckel")
    eq(sl.steuerermaessigung_35a(D("0"), D("0"), D("1000")), D("200.00"), "Handwerker")


@case
def test_35a_deckelt_je_topf_einzeln():
    """Jeder Topf hat seinen eigenen Höchstbetrag; ein Überhang im einen Topf
    darf nicht den anderen auffüllen."""
    eq(sl.steuerermaessigung_35a(D("99000"), D("0"), D("0")), D("510.00"), "Deckel Minijob")
    eq(sl.steuerermaessigung_35a(D("0"), D("99000"), D("0")), D("4000.00"), "Deckel haushaltsnah")
    eq(sl.steuerermaessigung_35a(D("0"), D("0"), D("99000")), D("1200.00"), "Deckel Handwerker")
    eq(sl.steuerermaessigung_35a(D("99000"), D("99000"), D("99000")), D("5710.00"),
       "alle drei Deckel zusammen")


@case
def test_35a_ohne_aufwand_ist_null():
    eq(sl.steuerermaessigung_35a(D("0"), D("0"), D("0")), D("0.00"))


# ── Vorsorgeaufwendungen ─────────────────────────────────────────────────────
@case
def test_vorsorge_hoechstbetrag_aus_der_bbg():
    """§ 10 Abs. 3 Satz 1: Höchstbeitrag zur knappschaftlichen RV, aufgerundet
    auf einen vollen Euro — Beitragsbemessungsgrenze mal Beitragssatz."""
    eq(sl.vorsorge_hoechstbetrag(2022), D("25639"), "2022")
    eq(sl.vorsorge_hoechstbetrag(2024), D("27566"), "2024")
    eq(sl.vorsorge_hoechstbetrag(2025), D("29344"), "2025")
    eq(sl.vorsorge_hoechstbetrag(2026), D("30826"), "2026")
    eq(sl.vorsorge_hoechstbetrag(2024, True), D("55132"), "Satz 2: verdoppelt")
    eq(sl.vorsorge_hoechstbetrag(2019), None, "ohne hinterlegte Werte kein Höchstbetrag")


@case
def test_vorsorge_anteil_staffel():
    """§ 10 Abs. 3 Sätze 4 und 6: 2013 sind 76 Prozent anzusetzen, je folgendem
    Kalenderjahr zwei Prozentpunkte mehr bis 2022; ab 2023 volle 100 Prozent."""
    eq(sl.vorsorge_anteil(2013), D("0.76"), "Ausgangswert")
    eq(sl.vorsorge_anteil(2022), D("0.94"), "2022 noch 94 %, nicht 100 %")
    eq(sl.vorsorge_anteil(2023), D("1.00"), "ab 2023 voll")
    eq(sl.vorsorge_anteil(2026), D("1.00"))


@case
def test_vorsorge_basis_gedeckelt_und_um_ag_anteil_gemindert():
    """§ 10 Abs. 3 Satz 5: erst deckeln und anteilig ansetzen, dann den
    steuerfreien Arbeitgeberanteil abziehen."""
    r = sl.vorsorge_abziehbar(basis=D("10000"), kranken_pflege=D("0"), sonstige=D("0"),
                              arbeitgeberanteil=D("4000"), jahr=2024,
                              zusammenveranlagung=False, mit_zuschuss=True)
    eq(r["basisversorgung"], D("6000.00"), "10.000 × 100 % − 4.000")

    gedeckelt = sl.vorsorge_abziehbar(basis=D("99000"), kranken_pflege=D("0"),
                                      sonstige=D("0"), arbeitgeberanteil=D("0"),
                                      jahr=2024, zusammenveranlagung=False,
                                      mit_zuschuss=True)
    eq(gedeckelt["basisversorgung"], D("27566.00"), "auf den Höchstbetrag gedeckelt")

    anteilig = sl.vorsorge_abziehbar(basis=D("10000"), kranken_pflege=D("0"),
                                     sonstige=D("0"), arbeitgeberanteil=D("0"),
                                     jahr=2022, zusammenveranlagung=False,
                                     mit_zuschuss=True)
    eq(anteilig["basisversorgung"], D("9400.00"), "2022: 94 % von 10.000")


@case
def test_vorsorge_sonstige_hoechstbetrag():
    """§ 10 Abs. 4: insgesamt 2.800 €, aber 1.900 € bei Anspruch auf Zuschuss —
    also bei praktisch jedem Arbeitnehmer."""
    an = sl.vorsorge_abziehbar(basis=D("0"), kranken_pflege=D("1000"),
                               sonstige=D("2000"), arbeitgeberanteil=D("0"),
                               jahr=2024, zusammenveranlagung=False, mit_zuschuss=True)
    eq(an["sonstige"], D("1900.00"), "Höchstbetrag 1.900 € greift")

    selbst = sl.vorsorge_abziehbar(basis=D("0"), kranken_pflege=D("1000"),
                                   sonstige=D("2000"), arbeitgeberanteil=D("0"),
                                   jahr=2024, zusammenveranlagung=False,
                                   mit_zuschuss=False)
    eq(selbst["sonstige"], D("2800.00"), "ohne Zuschuss 2.800 €")


@case
def test_vorsorge_basiskranken_bleibt_ueber_dem_hoechstbetrag_abziehbar():
    """§ 10 Abs. 4 Satz 4: übersteigen die Basiskranken- und Pflegebeiträge den
    Höchstbetrag, sind SIE abzuziehen — der Deckel gilt dann nicht, und ein
    Abzug der sonstigen Vorsorge entfällt. Wer diesen Satz übersieht, rechnet
    bei fast jedem Arbeitnehmer zu wenig ab."""
    r = sl.vorsorge_abziehbar(basis=D("0"), kranken_pflege=D("5000"),
                              sonstige=D("800"), arbeitgeberanteil=D("0"),
                              jahr=2024, zusammenveranlagung=False, mit_zuschuss=True)
    eq(r["sonstige"], D("5000.00"), "die Basisbeiträge selbst, nicht der Deckel")
    eq(r["sonstige_verfallen"], D("800.00"), "der Rest nach Nr. 3a entfällt ganz")


# ── zumutbare Belastung ──────────────────────────────────────────────────────
@case
def test_zumutbare_belastung_stufen():
    """§ 33 Abs. 3: Stufen bis 15.340 / bis 51.130 / darüber; ohne Kinder im
    Grundtarif 5, 6, 7 Prozent."""
    eq(sl.zumutbare_belastung(D("10000")), D("500.00"), "5 % innerhalb der ersten Stufe")
    eq(sl.zumutbare_belastung(D("15340")), D("767.00"), "erste Stufe voll")
    # 5 % von 15.340 + 6 % von 35.790
    eq(sl.zumutbare_belastung(D("51130")), D("2914.40"), "zweite Stufe voll")


@case
def test_zumutbare_belastung_wird_stufenweise_gerechnet():
    """Seit BFH vom 19.01.2017, VI R 75/14, gilt der höhere Satz nur für den
    ÜBERSTEIGENDEN Teil. Die frühere Lesart wandte einen Satz auf den ganzen
    Betrag an und erzeugte an den Grenzen Sprünge von mehreren hundert Euro."""
    for grenze in (D("15340"), D("51130")):
        davor = sl.zumutbare_belastung(grenze)
        danach = sl.zumutbare_belastung(grenze + 1)
        assert danach - davor < D("1"), f"Sprung an der Stufengrenze {grenze}: {davor} → {danach}"


@case
def test_zumutbare_belastung_nach_familienstand_und_kindern():
    gde = D("40000")
    ledig = sl.zumutbare_belastung(gde)
    zusammen = sl.zumutbare_belastung(gde, zusammenveranlagung=True)
    ein_kind = sl.zumutbare_belastung(gde, kinder=1)
    drei = sl.zumutbare_belastung(gde, kinder=3)
    assert zusammen < ledig, "Splitting: eine Stufe niedriger"
    assert ein_kind < zusammen and drei < ein_kind, "mehr Kinder, weniger zumutbar"
    eq(drei, D("400.00"), "1 % über beide Stufen")
    eq(sl.zumutbare_belastung(gde, kinder=2), ein_kind, "ein oder zwei Kinder: gleicher Satz")


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
