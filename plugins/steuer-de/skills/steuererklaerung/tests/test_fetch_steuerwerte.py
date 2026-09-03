#!/usr/bin/env python3
"""Parser-Tests für scripts/fetch_steuerwerte.py — ohne Netz.

Geprüft wird an unveränderten Ausschnitten der amtlichen Quellen
(tests/fixtures/), dass aus ihnen exakt die Zahlen fallen, die später die
Steuerberechnung tragen:

  * die Tarifhistorie des Bundesministeriums der Finanzen (bmf-steuerrechner.de),
    je Seite ein Tarifzeitraum mit der Formel nach § 32a EStG;
  * die amtliche XML-Fassung von EStG und SolZG (gesetze-im-internet.de).

Das Herunterladen selbst hat keinen Test: es ist ein Handgriff des Maintainers,
das Auswerten ist die Stelle, an der ein Fehler still in
references/steuerwerte.json landen würde.

Ausführen: python3 tests/test_fetch_steuerwerte.py   (oder tests/run_tests.py)
"""
import os
import sys
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import fetch_steuerwerte as fs  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def wirft(fehler, fn, label):
    try:
        fn()
    except fehler:
        return
    raise AssertionError(f"{label}: {fehler.__name__} erwartet, kam nicht")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def tarifhistorie_seiten():
    """Die Fixture enthält die Seitentexte, getrennt durch Seitenumbrüche.

    Erst trennen, dann den Kommentarkopf abziehen — str.splitlines() zerlegt
    auch an \\f und würde die Seitengrenzen sonst einebnen.
    """
    seiten = fixture("bmf_tarifhistorie.txt").split("\f")
    seiten[0] = "\n".join(z for z in seiten[0].split("\n") if not z.startswith("#"))
    return seiten


TARIF_2022 = {"gfb": D("10347"), "z2": D("14926"), "z3": D("58596"), "z4": D("277825"),
              "a2": D("1088.67"), "c3": D("869.32"), "a3": D("206.43"),
              "k4": D("9336.45"), "k5": D("17671.20")}
TARIF_2024 = {"gfb": D("11784"), "z2": D("17005"), "z3": D("66760"), "z4": D("277825"),
              "a2": D("954.80"), "c3": D("991.21"), "a3": D("181.19"),
              "k4": D("10636.31"), "k5": D("18971.06")}
TARIF_2026 = {"gfb": D("12348"), "z2": D("17799"), "z3": D("69878"), "z4": D("277825"),
              "a2": D("914.51"), "c3": D("1034.87"), "a3": D("173.10"),
              "k4": D("11135.63"), "k5": D("19470.38")}


# ── BMF-Tarifhistorie ────────────────────────────────────────────────────────
@case
def test_tarifhistorie_liest_alle_jahre():
    tarife = fs.tarife_aus_tarifhistorie(tarifhistorie_seiten())
    eq(sorted(tarife), [2010, 2011, 2012, 2022, 2023, 2024, 2025, 2026], "gefundene Jahre")
    eq(tarife[2022], TARIF_2022, "Tarif 2022")
    eq(tarife[2026], TARIF_2026, "Tarif 2026")


@case
def test_tarifhistorie_kennt_den_rueckwirkenden_grundfreibetrag_2024():
    """Die Tarifhistorie führt 2024 in der geltenden Fassung — 11.784 €, nicht
    die ursprünglich beschlossenen 11.604 €."""
    eq(fs.tarife_aus_tarifhistorie(tarifhistorie_seiten())[2024], TARIF_2024, "Tarif 2024")


@case
def test_alte_tarife_werden_uebergangen_statt_verbogen():
    """Die Historie reicht bis 1958 zurück, damals galten DM-Beträge und eine
    ganz andere Zonenstruktur (mit Y² und Y³). Solche Seiten dürfen nicht
    irgendwie halb gelesen werden — sie fallen einfach weg."""
    seiten = tarifhistorie_seiten()
    assert any("1958" in s and "DM" in s for s in seiten), "Fixture ohne alte Seite"
    eq(1958 in fs.tarife_aus_tarifhistorie(seiten), False, "1958 darf nicht auftauchen")


@case
def test_jahresbereich_in_der_ueberschrift_zaehlt_ganz():
    """Gilt ein Tarif mehrere Jahre, steht das in der Überschrift:
    „Einkommensteuertarif 2010 (2010 - 2012)“. Nur das erste Jahr zu nehmen
    hieße, für 2011 und 2012 zu melden, das Gesetz führe sie nicht — falsch."""
    tarife = fs.tarife_aus_tarifhistorie(tarifhistorie_seiten())
    for jahr in (2010, 2011, 2012):
        assert jahr in tarife, f"{jahr} fehlt"
    eq(tarife[2011], tarife[2010], "2011 hat denselben Tarif wie 2010")
    eq(tarife[2012], tarife[2010], "2012 hat denselben Tarif wie 2010")
    eq(tarife[2010]["gfb"], D("8004"), "Grundfreibetrag 2010")


@case
def test_tarif_mit_fremder_zonenkonstante_wird_uebergangen():
    """2007/2008 lautete die Konstante der zweiten Zone 1.500, nicht 1.400 —
    steuerlib.est_aus_tarif kennt aber nur 1.400 und 2.397. Solche Jahre dürfen
    nicht mit den falschen Konstanten weitergerechnet werden."""
    seiten = tarifhistorie_seiten()
    assert any("1.500) * Y" in s or "1.500) * y" in s for s in seiten), "Fixture ohne 2007"
    tarife = fs.tarife_aus_tarifhistorie(seiten)
    for jahr in (2007, 2008):
        eq(jahr in tarife, False, f"{jahr} darf nicht übernommen werden")


@case
def test_tarifhistorie_link_wird_gefunden():
    """Der Dateiname trägt ein Datum und ändert sich, sobald das BMF die
    Historie fortschreibt — er darf deshalb nirgends fest verdrahtet sein."""
    url = fs.tarifhistorie_link(fixture("bmf_startseite.html"))
    assert url.startswith("https://www.bmf-steuerrechner.de/"), url
    assert "Tarifhistorie" in url, url


# ── amtliche XML ─────────────────────────────────────────────────────────────
@case
def test_norm_aus_amtlicher_xml():
    text = fs.norm_aus_xml(fixture("gii_estg_32a.xml"), "§ 32a")
    jahr, tarif = fs.tarif_aus_text(text)
    eq(jahr, 2026, "Veranlagungszeitraum der geltenden Fassung")
    eq(tarif, TARIF_2026, "Tarif 2026 laut EStG")


@case
def test_norm_aus_xml_verlangt_die_richtige_norm():
    wirft(fs.FetchError,
          lambda: fs.norm_aus_xml(fixture("gii_estg_32a.xml"), "§ 99z"),
          "unbekannte Norm")


@case
def test_soli_freigrenze_nimmt_die_einzelveranlagung():
    """§ 3 Abs. 3 SolZG nennt zuerst den verdoppelten Splitting-Betrag
    (40.700 €) und erst danach den für Einzelveranlagung (20.350 €)."""
    text = fs.norm_aus_xml(fixture("gii_solzg_3.xml"), "§ 3")
    eq(fs.soli_freigrenze_aus_text(text), D("20350"), "Soli-Freigrenze")


@case
def test_beitragsbemessungsgrenzen_je_jahr():
    """Anlage 2 SGB VI führt die Grenzen je Zeitraum. Die knappschaftliche steht
    in der letzten Spalte — die allgemeine daneben ist rund ein Fünftel kleiner,
    und eine Verwechslung fiele in der fertigen Steuerzahl nicht mehr auf."""
    bbg = fs.bbg_knappschaftlich_aus_xml(fixture("gii_sgb6_anlage2.xml"))
    eq(bbg[2022], D("103800"), "BBG knappschaftlich 2022")
    eq(bbg[2024], D("111600"), "BBG knappschaftlich 2024")
    eq(bbg[2026], D("124800"), "BBG knappschaftlich 2026")
    assert 2017 not in bbg, "die Fixture reicht nur bis 2018 zurück"


@case
def test_beitrittsgebiet_wird_nicht_mitgelesen():
    """Anlage 2a führt dieselben Zeiträume für das Beitrittsgebiet. Wer über die
    ganze XML sucht statt über Anlage 2, bekommt für 2022 die Ost-Grenze
    (100.200 statt 103.800) — und damit einen um rund 900 € zu niedrigen
    Vorsorge-Höchstbetrag, der in der fertigen Steuerzahl nicht mehr auffällt."""
    bbg = fs.bbg_knappschaftlich_aus_xml(fixture("gii_sgb6_anlage2.xml"))
    eq(bbg[2022], D("103800"), "West-Grenze, nicht die des Beitrittsgebiets")
    eq(bbg[2023], D("107400"), "2023")


@case
def test_kinderwerte_aus_der_amtlichen_xml():
    """§ 32 Abs. 6 Satz 1 nennt die HALBEN Beträge — je Elternteil; bei
    Zusammenveranlagung verdoppeln sie sich (Satz 2). Wer den Satz-1-Betrag für
    den vollen Freibetrag hält, halbiert ihn."""
    w = fs.kinderwerte_aus_xml(fixture("gii_estg_32.xml") + fixture("gii_estg_66.xml"))
    eq(w["kinderfreibetrag"], D("3414"), "Kinderfreibetrag je Elternteil")
    eq(w["bea_freibetrag"], D("1464"), "BEA-Freibetrag je Elternteil")
    eq(w["kindergeld_monat"], D("259"), "§ 66 Abs. 1, monatlich")


@case
def test_kinderwerte_ohne_die_normen_wirft():
    wirft(fs.FetchError, lambda: fs.kinderwerte_aus_xml("<norm><enbez>§ 1</enbez></norm>"),
          "§ 32 fehlt")


@case
def test_allgemeine_und_knappschaftliche_grenze_aus_derselben_zeile():
    """Anlage 2 SGB VI führt beide Grenzen nebeneinander: erst die allgemeine,
    dann die knappschaftliche. Die allgemeine trägt die Plausibilitätsprüfung des
    Rentenbeitrags, die knappschaftliche den Vorsorge-Höchstbetrag — eine
    Verwechslung wäre in beiden Richtungen still falsch."""
    xml = fixture("gii_sgb6_anlage2.xml")
    allgemein = fs.bbg_allgemein_aus_xml(xml)
    knappschaft = fs.bbg_knappschaftlich_aus_xml(xml)
    eq(allgemein[2024], D("90600"), "allgemeine BBG 2024")
    eq(knappschaft[2024], D("111600"), "knappschaftliche BBG 2024")
    eq(allgemein[2026], D("101400"), "allgemeine BBG 2026")
    assert allgemein[2024] < knappschaft[2024], "die allgemeine ist die kleinere"


# ── Selbstkontrolle vor dem Schreiben ────────────────────────────────────────
@case
def test_unstetiger_tarif_faellt_auf():
    """Ein verlesener Koeffizient erzeugt fast immer einen Sprung an einer
    Zonengrenze — der Fetcher darf so etwas nicht in die JSON schreiben."""
    fs.pruefe_stetig(2026, TARIF_2026)  # der echte Tarif ist stetig
    wirft(fs.FetchError, lambda: fs.pruefe_stetig(2026, dict(TARIF_2026, a2=D("814.51"))),
          "Sprung an Zone 2")


@case
def test_text_ohne_tarif_wirft():
    for text, label in [("völlig anderer Text", "kein Paragraphentext"),
                        ("Sie beträgt ab dem Veranlagungszeitraum 2027 in Euro", "Zonen fehlen")]:
        wirft(fs.FetchError, lambda t=text: fs.tarif_aus_text(t), label)
    wirft(fs.FetchError, lambda: fs.soli_freigrenze_aus_text("kein SolZG"), "keine Freigrenze")


@case
def test_abweichung_zwischen_quellen_ist_ein_fehler():
    """BMF-Historie und EStG sind zwei getrennte amtliche Veröffentlichungen.
    Widersprechen sie sich, wird nichts geschrieben — dann stimmt eine nicht."""
    fs.pruefe_abgleich(2026, TARIF_2026, TARIF_2026)
    wirft(fs.FetchError,
          lambda: fs.pruefe_abgleich(2026, TARIF_2026, dict(TARIF_2026, k4=D("11135.64"))),
          "Abweichung BMF/EStG")


# ── Einarbeiten in die JSON ──────────────────────────────────────────────────
FUNDSTELLE = "§ 32a EStG i.d.F. Art. 2 Steuerfortentwicklungsgesetz v. 23.12.2024"


def _alt(jahr="2026"):
    return {"schema": 1, "jahre": {jahr: {
        "tarif": {k: str(v) for k, v in TARIF_2026.items()}, "soli_freigrenze": "20350",
        "freigrenze_23": "1000", "sparer_pb": "1000", "an_pauschbetrag": "1230",
        "quelle": FUNDSTELLE, "geprueft": "2026-08-31"}}}


@case
def test_fundstelle_im_gesetz_bleibt_erhalten():
    """`quelle` nennt das Änderungsgesetz mit Fundstelle im Bundesgesetzblatt —
    die Angabe, die man in einer Rückfrage ans Finanzamt zitiert. Die
    BMF-Tarifhistorie nennt kein Änderungsgesetz; sie darf die Fundstelle
    deshalb nicht überschreiben, sondern nur als `beleg` danebenschreiben."""
    neu, _ = fs.zusammenfuehren(_alt(), {2026: (TARIF_2026, "BMF-Tarifhistorie, Stand X")},
                                {2026: D("20350")})
    eintrag = neu["jahre"]["2026"]
    eq(eintrag["quelle"], FUNDSTELLE, "Fundstelle im Gesetz")
    eq(eintrag["beleg"], "BMF-Tarifhistorie, Stand X", "womit zuletzt geprüft wurde")


@case
def test_neues_jahr_bekommt_keine_null_als_pauschbetrag():
    """Für ein neues Jahr holt das Skript Tarif und Soli-Freigrenze, nicht aber
    Sparer-Pauschbetrag, AN-Pauschbetrag und Freigrenze § 23. Sie bleiben leer —
    eine 0 wäre eine stille Falschangabe: sie würde Kapitalerträge voll
    besteuern, statt aufzufallen."""
    neu, aenderungen = fs.zusammenfuehren(_alt(), {2027: (TARIF_2026, "BMF-Tarifhistorie")},
                                          {2027: D("20350")})
    eintrag = neu["jahre"]["2027"]
    for k in ("freigrenze_23", "sparer_pb", "an_pauschbetrag"):
        eq(eintrag[k], None, f"{k} für ein neues Jahr")
    eq(eintrag["tarif"]["gfb"], "12348", "Tarif wird geschrieben")
    eq(eintrag["soli_freigrenze"], "20350", "Soli-Freigrenze wird geschrieben")
    assert any("2027" in a and "von Hand" in a for a in aenderungen), \
        f"kein Hinweis auf die nachzutragenden Werte: {aenderungen}"


@case
def test_vorhandenes_jahr_behaelt_seine_handwerte():
    """Ein erneuter Lauf darf gepflegte Werte nicht überschreiben."""
    neu, _ = fs.zusammenfuehren(_alt(), {2026: (TARIF_2026, "BMF-Tarifhistorie")},
                                {2026: D("20350")})
    eq(neu["jahre"]["2026"]["sparer_pb"], "1000", "Sparer-Pauschbetrag")
    eq(neu["jahre"]["2026"]["an_pauschbetrag"], "1230", "AN-Pauschbetrag")


@case
def test_jahr_ohne_tarif_wird_nicht_angelegt():
    """Ein Eintrag ohne `tarif` macht references/steuerwerte.json unlesbar und
    damit jedes Skript des Skills unbenutzbar. Kommt für ein unbekanntes Jahr
    nur eine Soli-Freigrenze, wird das Jahr deshalb gar nicht erst angelegt."""
    neu, aenderungen = fs.zusammenfuehren(_alt(), {}, {2027: D("20350")})
    eq("2027" in neu["jahre"], False, "2027 darf nicht angelegt werden")
    assert any("2027" in a for a in aenderungen), f"nicht gemeldet: {aenderungen}"


@case
def test_jahresangabe_wird_geprueft():
    for schlecht in ("2027-2022", "", "2020-2019"):
        wirft(SystemExit, lambda s=schlecht: fs._jahre_lesen(s), f"--jahre {schlecht!r}")
    eq(fs._jahre_lesen("2024-2026"), {2024, 2025, 2026}, "Bereich")
    eq(fs._jahre_lesen("2024,2026"), {2024, 2026}, "Aufzählung")


@case
def test_nicht_geholte_soli_freigrenze_bleibt_stehen():
    """Amtlich nachprüfbar ist nur die geltende Fassung des § 3 SolZG. Für
    frühere Jahre holt das Skript nichts — und darf den bereits geprüften Wert
    dann auch nicht anfassen."""
    alt = _alt("2025")
    alt["jahre"]["2025"]["soli_freigrenze"] = "19950"
    neu, _ = fs.zusammenfuehren(alt, {2025: (TARIF_2026, "BMF-Tarifhistorie")}, {})
    eq(neu["jahre"]["2025"]["soli_freigrenze"], "19950", "Soli-Freigrenze 2025")


if __name__ == "__main__":
    fails = []
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:  # ein Parserfehler ist genauso ein Fehlschlag
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(CASES) - len(fails)}/{len(CASES)} bestanden")
    sys.exit(1 if fails else 0)
