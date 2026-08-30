#!/usr/bin/env python3
"""Tests für die Profil-Engine (brokerprofile.py) und die ausgelieferten Profile.

Ausführen: python3 tests/test_brokerprofile.py

Geprüft wird vor allem, dass die drei Sicherheitsnetze wirklich greifen:
Erkennung, Pflichtfelder und Summenabgleich. Ein Profil, das ein Layout still
falsch liest und Erfolg meldet, ist schlimmer als ein sauberer Abbruch.
"""
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal as D

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.join(HIER, "..")
sys.path.insert(0, os.path.join(WURZEL, "scripts"))
import steuerlib as sl        # noqa: E402
import brokerprofile as bp    # noqa: E402
import parse_broker as pbr    # noqa: E402
import parse_koinly as pk     # noqa: E402
import parse_etoro as pe      # noqa: E402

SCRIPTS = os.path.join(WURZEL, "scripts")
FIXTURES = os.path.join(HIER, "fixtures")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def wirft(fehler, fn, *a, label="", **kw):
    try:
        fn(*a, **kw)
    except fehler:
        return
    raise AssertionError(f"{label}: {fehler.__name__} hätte geworfen werden müssen")


def fixture(name):
    return bp.text_aus_datei(os.path.join(FIXTURES, name + ".txt"))


# ── Minimalprofile für die Negativtests ──────────────────────────────────────
def mini_profil(**anders):
    p = {
        "id": "mini", "label": "Mini", "quelle": "Mini", "eingabe": "pdf",
        "ergebnis": "krypto_vorberechnet",
        "erkennung": {"muss": ["MiniBroker"], "punkte": 5},
        "notation": "de", "datum": "de",
        "tabellen": [{
            "name": "veraeusserungen", "rolle": "veraeusserungen",
            "start": r"Verkauf\s+Kauf", "ende": r"^Summe",
            "zeile": (r"^(?P<v>{DT})\s+(?P<k>{DT})\s+(?P<a>\S+)\s+(?P<m>{NUM})\s+"
                      r"(?P<g>{NUM})$"),
            "felder": {"disposal_date": "v", "acquisition_date": "k", "asset": "a",
                       "amount": "m", "gain_eur": "g"},
            "pflicht": ["disposal_date", "acquisition_date", "gain_eur"],
        }],
        "summen": [{"label": "Summe", "muster": r"(?im)^Summe{VOR}({NUM})",
                    "vergleich": "summen_basis.veraeusserungen_gewinn_gesamt",
                    "toleranz": "0.01"}],
        "status": "geprueft", "geprueft_am": "2026-08-30",
        "fixture": "tests/fixtures/mini.txt",
    }
    p.update(anders)
    return bp.Profil(p)


MINI_TEXT = """MiniBroker Report 2024
Verkauf Kauf Asset Menge Gewinn
01.12.2024 01.02.2024 BTC 0,5 1.000,00
02.12.2024 01.02.2024 ETH 2 -200,00
Summe 800,00
"""


# ── Profile und Fixtures ─────────────────────────────────────────────────────
@case
def test_alle_profile_sind_strukturell_gueltig():
    profile = bp.lade_profile()
    assert profile, "keine Profile in scripts/profiles gefunden"
    for p in profile:
        eq(bp.pruefe_profil(p), [], f"Profil {p.id}")
        pfad = os.path.join(WURZEL, p.fixture)
        assert os.path.exists(pfad), f"Fixture {p.fixture} fehlt (Pflicht)"


@case
def test_jedes_profil_erkennt_sein_eigenes_fixture():
    """Erkennung muss eindeutig sein — sonst liest ein Profil fremde Spalten."""
    for p in bp.lade_profile():
        text = bp.text_aus_datei(os.path.join(WURZEL, p.fixture))
        erkannt = bp.erkenne(text)
        assert erkannt is not None, f"{p.id}: eigenes Fixture wird nicht erkannt"
        eq(erkannt.id, p.id, f"Erkennung für {p.id}")


@case
def test_jedes_profil_laeuft_gegen_sein_fixture_durch():
    """Inklusive des profileigenen Summenabgleichs (sonst PlausibilityError)."""
    for p in bp.lade_profile():
        text = bp.text_aus_datei(os.path.join(WURZEL, p.fixture))
        r = bp.wende_an(p, text, quelle=p.fixture)
        assert r["abgleich"], f"{p.id}: kein Abgleichsbericht"
        assert not any("konnte NICHT gegengeprüft" in w for w in r["warnungen"]), \
            f"{p.id}: Summenmuster findet im eigenen Fixture nichts: {r['warnungen']}"


@case
def test_erkennung_ohne_treffer_und_bei_gleichstand():
    eq(bp.erkenne("völlig fremder Text"), None, "kein Treffer -> None")

    a = mini_profil(id="a", erkennung={"muss": ["MiniBroker"], "punkte": 5})
    b = mini_profil(id="b", erkennung={"muss": ["MiniBroker"], "punkte": 5})
    c = mini_profil(id="c", erkennung={"muss": ["MiniBroker"], "punkte": 9})
    try:
        bp.erkenne(MINI_TEXT, [a, b])
        raise AssertionError("Gleichstand hätte gemeldet werden müssen")
    except sl.ParseError as e:
        assert "a" in str(e) and "b" in str(e), f"Kandidaten fehlen in: {e}"
    eq(bp.erkenne(MINI_TEXT, [a, b, c]).id, "c", "höhere Punktzahl gewinnt")
    eq([p.id for p in bp.kandidaten(MINI_TEXT, [a, b, c])], ["c", "a", "b"])


@case
def test_darf_nicht_schliesst_aus():
    a = mini_profil(id="a", erkennung={"muss": ["MiniBroker"],
                                       "darf_nicht": ["Report 2024"], "punkte": 5})
    eq(bp.erkenne(MINI_TEXT, [a]), None, "darf_nicht schlägt zu")


# ── Ablehnung unfertiger Profile ─────────────────────────────────────────────
@case
def test_profil_mit_todo_wird_abgelehnt():
    p = mini_profil(label="TODO: Bezeichnung ergänzen")
    probleme = bp.pruefe_profil(p)
    assert any("TODO" in x for x in probleme), probleme
    wirft(sl.ParseError, bp.wende_an, p, MINI_TEXT, label="TODO-Profil anwenden")


@case
def test_profil_ohne_summen_wird_abgelehnt():
    p = mini_profil(summen=[])
    probleme = bp.pruefe_profil(p)
    assert any("summen" in x for x in probleme), probleme
    wirft(sl.ParseError, bp.wende_an, p, MINI_TEXT, label="Profil ohne Summenabgleich")


@case
def test_profil_ohne_pflichtfelder_wird_abgelehnt():
    tab = json.loads(json.dumps(mini_profil().tabellen))
    tab[0]["pflicht"] = []
    p = mini_profil(tabellen=tab)
    probleme = bp.pruefe_profil(p)
    assert any("pflicht" in x for x in probleme), probleme
    wirft(sl.ParseError, bp.wende_an, p, MINI_TEXT, label="Profil ohne Pflichtfelder")


@case
def test_profil_ohne_erkennung_oder_fixture_wird_abgelehnt():
    ohne_erkennung = mini_profil(erkennung={"muss": [], "punkte": 1})
    assert any("erkennung.muss" in x for x in bp.pruefe_profil(ohne_erkennung))
    ohne_fixture = mini_profil(fixture=None)
    assert any("fixture" in x for x in bp.pruefe_profil(ohne_fixture))


@case
def test_pflichtfeld_verweist_auf_unbekannte_gruppe():
    tab = json.loads(json.dumps(mini_profil().tabellen))
    tab[0]["felder"]["fee_eur"] = "gibtsnicht"
    probleme = bp.pruefe_profil(mini_profil(tabellen=tab))
    assert any("gibtsnicht" in x for x in probleme), probleme


# ── Tabellen: nicht zugeordnete Zeilen und Pflichtfelder ─────────────────────
@case
def test_nicht_zugeordnete_zeile_wird_gezaehlt_und_gemeldet():
    kaputt = MINI_TEXT.replace("02.12.2024 01.02.2024 ETH 2 -200,00",
                               "02.12.2024 01.02.2024 ETH 2 -200,00 unerwartete Spalte")
    kaputt = kaputt.replace("Summe 800,00", "Summe 1.000,00")
    r = bp.wende_an(mini_profil(), kaputt)
    p23 = r["paragraph_23"]
    eq(len(p23["nicht_zugeordnete_zeilen"]), 1, "Zeile gezählt")
    assert any("NICHT gelesen" in w for w in p23["warnungen"]), p23["warnungen"]
    assert any("Nicht zugeordnete Tabellenzeilen: 1" in z for z in r["abgleich"]), \
        r["abgleich"]


@case
def test_unlesbarer_betrag_wird_nicht_still_zu_null():
    kaputt = MINI_TEXT.replace("1.000,00", "1.0O0,00")   # OCR-Fehler: O statt 0
    r = bp.wende_an(mini_profil(), kaputt, strikt=False)
    # Die Zeile passt nicht mehr auf das Muster -> sie wird gezählt, nicht als 0 gebucht.
    eq(len(r["paragraph_23"]["nicht_zugeordnete_zeilen"]), 1)
    assert r["paragraph_23"]["netto_ergebnis_eur"] != "800.00"


@case
def test_fehlendes_pflichtfeld_wirft():
    tab = json.loads(json.dumps(mini_profil().tabellen))
    tab[0]["zeile"] = (r"^(?P<v>{DT})\s+(?P<k>{DT})\s+(?P<a>\S+)\s+(?P<m>{NUM})"
                       r"(?:\s+(?P<g>{NUM}))?$")
    ohne_gewinn = MINI_TEXT.replace("01.12.2024 01.02.2024 BTC 0,5 1.000,00",
                                    "01.12.2024 01.02.2024 BTC 0,5")
    wirft(sl.ParseError, bp.wende_an, mini_profil(tabellen=tab), ohne_gewinn,
          label="fehlendes Pflichtfeld gain_eur")


# ── Summenabgleich ───────────────────────────────────────────────────────────
@case
def test_summenabweichung_wirft():
    fehlt = "\n".join(l for l in MINI_TEXT.splitlines()
                      if not l.startswith("02.12.2024"))
    wirft(sl.PlausibilityError, bp.wende_an, mini_profil(), fehlt,
          label="verlorene Zeile")
    # Ohne strikt darf dieselbe Eingabe durchlaufen (Diagnosemodus).
    r = bp.wende_an(mini_profil(), fehlt, strikt=False)
    assert any("Abweichung" in z for z in r["abgleich"])


@case
def test_summenmuster_ohne_treffer_ist_warnung_kein_stiller_erfolg():
    ohne_summe = "\n".join(l for l in MINI_TEXT.splitlines()
                           if not l.startswith("Summe"))
    r = bp.wende_an(mini_profil(), ohne_summe)
    assert any("NICHT gegengeprüft" in w for w in r["warnungen"]), r["warnungen"]
    assert any("kein Vergleichswert" in z for z in r["abgleich"]), r["abgleich"]


# ── Notation ─────────────────────────────────────────────────────────────────
@case
def test_de_und_en_notation_liefern_dasselbe():
    en_profil = mini_profil(notation="en", datum="de")
    en_text = MINI_TEXT.replace("1.000,00", "1,000.00").replace("-200,00", "-200.00") \
                       .replace("0,5", "0.5").replace("Summe 800,00", "Summe 800.00")
    de = bp.wende_an(mini_profil(), MINI_TEXT)
    en = bp.wende_an(en_profil, en_text)
    for feld in ("netto_ergebnis_eur", "gewinn_eur", "verlust_eur"):
        eq(en["paragraph_23"][feld], de["paragraph_23"][feld], f"DE/EN {feld}")
    eq(en["paragraph_23"]["disposals"][0]["amount"],
       de["paragraph_23"]["disposals"][0]["amount"], "Menge")


@case
def test_notation_auto_erkennt_das_dokument():
    auto = mini_profil(notation="auto")
    r = bp.wende_an(auto, MINI_TEXT)
    eq(r["zahlennotation"], "de")
    eq(r["paragraph_23"]["netto_ergebnis_eur"], "800.00")


# ── Migration: gleiche Zahlen wie die früheren Handparser ────────────────────
KOINLY_ALT = """Koinly Steuerbericht 2024
Kapitalgewinne
Verkaufsdatum Kaufdatum Asset Menge Kosten Erlös Gewinn Wallet Haltedauer
31/12/2024 10:30 01/06/2024 09:00 BTC 0,5 4.000,00 7.000,00 3.000,00 Kraken Kurzfristig
30/11/2024 10:30 01/06/2020 09:00 ETH 2 1.000,00 3.000,00 2.000,00 Ledger Langfristig
29/11/2024 10:30 01/06/2024 09:00 SOL 5 1.000,00 900,00 -100,00 Ledger Kurzfristig
Zusammenfassung
Kapitalgewinne 4.900,00
Veräußerungen: 3
Zusammenfassung Einnahmen
Belohnungen 300,00
Airdrops 50,00
Sonstige Einnahmen 6,00
Ausgaben
Loan fee 20,00
Futures
Realisiertes Ergebnis -500,00
"""


@case
def test_migration_koinly_reproduziert_die_alten_zahlen():
    """Goldwerte des handgeschriebenen parse_koinly (vor der Migration)."""
    r = pk.build_result(KOINLY_ALT, 2024, quelle="koinly.pdf")
    p = r["paragraph_23"]
    eq(p["anzahl_veraeusserungen"], 3)
    eq(p["gewinn_eur"], "3000.00")
    eq(p["verlust_eur"], "-100.00")
    eq(p["netto_ergebnis_eur"], "2900.00")
    eq(p["steuerfrei_langfristig_eur"], "2000.00", "Langfristig bleibt steuerfrei")
    eq(p["freigrenze_angewendet"], False, "Freigrenze gehört in build_taxreport")
    eq(p["disposals"][0]["note"], "Kraken Quelle: Koinly")
    eq(p["disposals"][0]["cost_basis_eur"], "4000.00")
    eq(p["disposals"][0]["proceeds_eur"], "7000.00")
    eq(p["disposals"][0]["held_days"], 213)
    eq(r["paragraph_22_nr3"]["netto_ergebnis_eur"], "356.00")
    eq(r["paragraph_22_nr3"]["einnahmen_detail"]["Airdrop"], "50.00")
    eq(r["koinly_extra"]["futures_nettoergebnis_eur"], "-500.00")
    eq(r["koinly_extra"]["ausgaben_detail"]["Loan fee"], "20.00")
    eq(r["koinly_extra"]["ausgaben_total_eur"], "20.00")
    eq(r["quelle"], "koinly.pdf")
    eq(r["tax_year"], 2024)
    assert r["paragraph_22_nr_3"] is r["paragraph_22_nr3"], "alter Alias fehlt"
    assert any("Abweichung 0,00" in z for z in r["abgleich"]), r["abgleich"]


# Anlage KAP Z. 20-25 sind davon-Zeilen zu Z. 18/19 — die ausgewiesene Summe der
# Kapitalerträge ist deshalb die BRUTTO-Zeile, nicht Brutto plus Unterzeilen.
ETORO_ALT = """eToro Steuerbericht
Guten Tag Max Mustermann,
Berichtszeitraum: 01.01.2024 - 31.12.2024
Depot: 123456
Ausländische Kapitalerträge (Anlage KAP Zeile 19) 4.000,00
Summe der Kapitalerträge 4.000,00
Davon in Zeile 19 enthalten:
Gewinne aus Aktienveräußerungen (Anlage KAP Zeile 20) 2.646,52
Verluste aus Termingeschäften (Anlage KAP Zeile 24) −5.334,40
Private Veräußerungsgeschäfte (Anlage SO Zeile 47) −1.234,56
Staking (Anlage SO Zeile 11) 300,00
"""


@case
def test_migration_etoro_reproduziert_die_alten_zahlen():
    r = pe.build_result(ETORO_ALT, 2024, quelle="etoro.pdf")
    k = r["etoro_kap"]
    eq(k["z19_auslaend_kapitalertraege"], "4000.00")
    eq(k["z20_aktien_veraeusserung_gewinn"], "2646.52")
    eq(k["z24_verluste_termingeschaefte"], "-5334.40", "Unicode-Minus bleibt negativ")
    assert "z23_verluste_aktien" not in k, \
        "nicht ausgewiesene Zeile fehlt (0,00 wäre eine Angabe, die der Report nie macht)"
    eq(r["paragraph_23"]["netto_ergebnis_eur"], "-1234.56")
    eq(r["paragraph_23"]["verlustvortrag_eur"], "1234.56")
    eq(r["paragraph_22_nr3"]["netto_ergebnis_eur"], "300.00")
    eq(r["paragraph_22_nr3"]["detail"]["staking_so_z11"], "300.00")
    eq(r["steuerpflichtiger_aus_report"], "Max Mustermann")
    eq(pe.detect_person(ETORO_ALT), ("Max Mustermann", "123456"))
    eq(pe.detect_year(ETORO_ALT), 2024)
    assert any("Abweichung 0,00" in z for z in r["abgleich"]), r["abgleich"]
    # neu: KAP-Ausgabeschema nach references/broker-profile.md
    eq(r["kap_zeilen"]["24"], "-5334.40")
    eq(r["kennzahlen"]["verlust_termingeschaefte"], "-5334.40")
    eq(r["kennzahlen"]["kapitalertraege"], "4000.00", "Brutto = Z. 7 + 18 + 19")
    zeilen = {(e["anlage"], e["zeile"]) for e in r["elster_extra"]}
    assert ("Anlage KAP", "Z. 24") in zeilen, r["elster_extra"]


@case
def test_migration_etoro_extract_lines_bleibt_nutzbar():
    lines, warnungen = pe.extract_lines(ETORO_ALT)
    eq(lines[("KAP", 20)], D("2646.52"))
    eq(lines[("SO", 47)], D("-1234.56"))
    eq(lines[("SO", 11)], D("300.00"))
    wirft(sl.ParseError, pe.build_result,
          "eToro Anlage SO Zeile 47 ohne Betrag\n", 2024,
          label="keine Zuordnung gefunden")


@case
def test_etoro_belegt_alle_kennzahlen_des_contracts():
    """Z. 21/22/25/42 wurden gelesen, geprüft — und fielen dann aus der Rechnung."""
    import build_taxreport as bt
    text = ETORO_ALT + (
        "Gewinne aus Termingeschäften (Anlage KAP Zeile 21) 900,00\n"
        "Verluste (ohne Aktien) (Anlage KAP Zeile 22) −80,00\n"
        "Verluste Ausfall (Anlage KAP Zeile 25) −40,00\n"
        "Fiktive Quellensteuer (Anlage KAP Zeile 42) 12,00\n")
    r = pe.build_result(text, 2024, quelle="etoro.pdf", strikt=False)
    k = r["kennzahlen"]
    eq(set(k), set(bt.KAP_KENNZAHLEN),
       "kennzahlen muss exakt den Contract von build_taxreport bedienen")
    eq(k["gewinn_termingeschaefte"], "900.00")
    eq(k["verluste_ohne_aktien"], "-80.00")
    eq(k["verluste_ausfall"], "-40.00")
    eq(k["fiktive_quellensteuer"], "12.00")
    # und build_taxreport nimmt sie ohne "unbekannte Kennzahl"-Warnung an
    q = bt.normiere_kap_quelle(r, "etoro.pdf")
    assert not any("unbekannte Kennzahl" in w for w in q["warnungen"]), q["warnungen"]
    eq(str(q["kennzahlen"]["gewinn_termingeschaefte"]), "900.00")
    eq(str(q["kennzahlen"]["verluste_ausfall"]), "-40.00")


@case
def test_kap_brutto_und_davon_zeilen_landen_nie_in_einem_topf():
    """Z. 20-25 sind 'In den Zeilen 18 und 19 enthaltene …' — Teilmengen.

    Wer sie zur Bruttozeile addiert, vergleicht am Ende eine Zahl, die keine
    Steuerberechnung verwendet: der Haken ist grün, die Erträge fehlen trotzdem.
    """
    for p in bp.lade_profile():
        if p.ergebnis != "kap":
            continue
        for ziel, zeilen in bp._kap_aggregate(p).items():
            brutto = zeilen & bp.KAP_BRUTTO_ZEILEN
            davon = zeilen & bp.KAP_DAVON_ZEILEN
            assert not (brutto and davon), \
                f"{p.id}: {ziel} mischt Brutto {sorted(brutto)} mit davon {sorted(davon)}"
        # und der Summenabgleich hängt an denselben Zeilen wie die Kennzahl
        aggregate = bp._kap_aggregate(p)
        for s in p.summen:
            zeilen = aggregate.get(s["vergleich"])
            if zeilen is None:
                continue
            assert not (zeilen & bp.KAP_DAVON_ZEILEN and zeilen & bp.KAP_BRUTTO_ZEILEN), \
                f"{p.id}: summen[{s['label']}] vergleicht eine gemischte Summe"

    # Ein Profil, das beides addiert, wird abgelehnt.
    gemischt = bp.Profil({
        **json.loads(json.dumps(bp.profil_nach_id("etoro-de").roh)),
        "kennzahlen": {"kapitalertraege": ["kap_zeilen.19", "kap_zeilen.20"]},
    })
    probleme = bp.pruefe_profil(gemischt)
    assert any("davon" in x for x in probleme), probleme
    wirft(sl.ParseError, bp.wende_an, gemischt, fixture("etoro-de"),
          label="gemischte Brutto-/davon-Summe")


@case
def test_etoro_summenabgleich_prueft_die_verwendete_zahl():
    """Der Abgleich muss an dem hängen, was build_taxreport auch rechnet."""
    p = bp.profil_nach_id("etoro-de")
    vergleiche = {s["vergleich"] for s in p.summen}
    assert "kennzahlen.kapitalertraege" in vergleiche, vergleiche
    assert not any(v.startswith("summen_basis.kap") for v in vergleiche), \
        "additive KAP-Summe darf nicht wieder eingeführt werden"
    r = bp.wende_an(p, fixture("etoro-de"), quelle="etoro-de.txt")
    eq(r["kennzahlen"]["kapitalertraege"], "2000.00")
    eq(r["kap_zeilen"]["20"], "800.00", "davon-Zeile bleibt Teilmenge")
    assert any("Abweichung 0,00" in z and "Kapitalerträge" in z
               for z in r["abgleich"]), r["abgleich"]


@case
def test_etoro_fixture_rechnet_bis_zum_taxreport_durch():
    """Ende zu Ende: eToros eigenes Netto muss aus den Kennzahlen herauskommen."""
    import build_taxreport as bt
    r = bp.wende_an(bp.profil_nach_id("etoro-de"), fixture("etoro-de"),
                    quelle="etoro-de.txt")
    q = bt.normiere_kap_quelle(r, "etoro-de.txt")
    k = q["kennzahlen"]
    aktien_verrechnet = min(abs(k["verlust_aktien"]), k["gewinn_aktien"],
                            k["kapitalertraege"])
    netto = k["kapitalertraege"] - aktien_verrechnet - abs(k["verlust_termingeschaefte"])
    eq(str(sl.q2(netto)), r["etoro_extra"]["netto_nach_verlustverrechnung"],
       "Rechnung und ausgewiesenes Netto des Reports müssen sich treffen")
    assert not q["warnungen"], q["warnungen"]


@case
def test_nicht_ausgewiesene_kap_zeile_fehlt_statt_null():
    """0,00 wäre eine Angabe, die der Report nie gemacht hat."""
    r = bp.wende_an(bp.profil_nach_id("etoro-de"), fixture("etoro-de"))
    eq(set(r["kap_zeilen"]), {"19", "20", "23", "24"},
       "nur die tatsächlich ausgewiesenen Zeilen")
    assert all(w is not None for w in r["kap_zeilen"].values())
    # kennzahlen bleiben vollständig: dort ist 0 das Rechenergebnis, keine Angabe.
    import build_taxreport as bt
    eq(set(r["kennzahlen"]), set(bt.KAP_KENNZAHLEN))
    eq(r["kennzahlen"]["anrechenbare_kest"], "0.00")


@case
def test_kennzahlen_vorzeichen_wird_normiert_und_gemeldet():
    """kennzahlen ist die normierte Fassung; kap_zeilen bleibt wörtlich."""
    text = ETORO_ALT + "Verluste (ohne Aktien) (Anlage KAP Zeile 22) 250,00\n"
    r = pe.build_result(text, 2024, quelle="etoro.pdf", strikt=False)
    eq(r["kap_zeilen"]["22"], "250.00", "Rohabschrift bleibt unverändert")
    eq(r["kennzahlen"]["verluste_ohne_aktien"], "-250.00", "Verlust wird negativ")
    assert any("verluste_ohne_aktien" in w and "positiv" in w for w in r["warnungen"]), \
        r["warnungen"]


# ── Haltefrist ───────────────────────────────────────────────────────────────
def koinly_zeile(verkauf, kauf, halte, gewinn="10.000,00"):
    return (f"Koinly Steuerbericht 2024\nKapitalgewinne\n"
            f"Verkaufsdatum Kaufdatum Asset Menge Kosten Erlös Gewinn Wallet Haltedauer\n"
            f"{verkauf} {kauf} BTC 1,0 20.000,00 30.000,00 {gewinn} Wallet {halte}\n"
            f"Zusammenfassung\nKapitalgewinne {gewinn}\nVeräußerungen: 1\n")


@case
def test_haltefrist_gesetzliche_frist_schlaegt_das_label_des_reports():
    """Der Jahrestag selbst ist noch steuerpflichtig (§ 108 AO / § 188 Abs. 2 BGB).

    Ein falsches 'Langfristig' verkürzt die Steuer — deshalb entscheidet die aus
    zwei Pflichtfeldern eindeutig berechenbare Frist, nicht die Meinung des Tools.
    """
    text = koinly_zeile("05/05/2024 10:00", "05/05/2023 10:00", "Langfristig")
    r = pk.build_result(text, 2024, quelle="koinly.pdf", dateformat="de")
    p = r["paragraph_23"]
    d = p["disposals"][0]
    eq(d["holding_period_met"], False, "gesetzliche Frist entscheidet")
    eq(d["taxable"], True, "10.000 € bleiben steuerpflichtig")
    eq(d["holding_period_laut_report"], True, "Angabe des Reports als Prüfspur")
    eq(d["haltefrist_konflikt"], True)
    assert "_needs_review" not in d, "Flag, das niemand liest, gehört nicht ins Ergebnis"
    eq(p["netto_ergebnis_eur"], "10000.00", "Gewinn landet in der Bemessung")
    eq(p["steuerfrei_langfristig_eur"], "0.00")

    konflikte = [w for w in p["warnungen"] if "HALTEFRIST-KONFLIKT" in w]
    assert konflikte, f"kein Konflikt gemeldet: {p['warnungen']}"
    w = konflikte[0]
    for teil in ("2023-05-05", "2024-05-05", "BTC", "ANGEWENDET", "GESAMTE Report"):
        assert teil in w, f"{teil!r} fehlt in der Meldung: {w}"
    assert any("HALTEFRIST-KONFLIKT" in x for x in r["warnungen"]), \
        "Konflikt muss auch in den Top-Level-Warnungen stehen"
    assert any("ALLE Zeilen" in x for x in r["warnungen"]), \
        "Sammelwarnung fehlt: ein Widerspruch stellt den ganzen Report in Frage"


@case
def test_haltefrist_warnung_erreicht_den_taxreport():
    """Die Warnung ersetzt das frühere _needs_review — sie muss ankommen."""
    import build_taxreport as bt
    text = koinly_zeile("05/05/2024 10:00", "05/05/2023 10:00", "Langfristig")
    r = pk.build_result(text, 2024, quelle="koinly.pdf", dateformat="de")
    q = bt.normiere_krypto_quelle(r, "koinly.pdf")
    assert any("HALTEFRIST-KONFLIKT" in w for w in q["warnungen"]), \
        f"Warnung überlebt den Import nach build_taxreport nicht: {q['warnungen']}"


@case
def test_haltefrist_ohne_konflikt_meldet_nichts():
    for verkauf, kauf, halte, steuerfrei in (
            ("06/05/2024 10:00", "05/05/2023 10:00", "Langfristig", True),
            ("04/05/2024 10:00", "05/05/2023 10:00", "Kurzfristig", False)):
        r = pk.build_result(koinly_zeile(verkauf, kauf, halte), 2024,
                            quelle="x", dateformat="de")
        assert not any("HALTEFRIST" in w for w in r["warnungen"]), \
            f"{verkauf}/{halte}: unnötige Konfliktmeldung"
        d = r["paragraph_23"]["disposals"][0]
        assert "haltefrist_konflikt" not in d
        eq(d["holding_period_met"], steuerfrei, f"{verkauf}: Frist")


@case
def test_haltefrist_ohne_label_wird_aus_den_daten_bestimmt():
    """Ohne Haltedauer-Spalte im Profil zählt allein die Jahresfrist."""
    tab = json.loads(json.dumps(mini_profil().tabellen))   # kein 'langfristig'
    text = MINI_TEXT.replace("01.12.2024 01.02.2024 BTC 0,5 1.000,00",
                             "01.12.2024 01.02.2023 BTC 0,5 1.000,00")
    r = bp.wende_an(mini_profil(tabellen=tab), text, strikt=False)
    d = r["paragraph_23"]["disposals"][0]
    eq(d["holding_period_met"], True, "> 1 Jahr gehalten")
    assert "holding_period_laut_report" not in d, "es gibt keine Angabe des Reports"
    eq(r["paragraph_23"]["steuerfrei_langfristig_eur"], "1000.00")


# ── Vergleichspfade ──────────────────────────────────────────────────────────
@case
def test_unerreichbarer_vergleich_pfad_wird_beim_laden_abgelehnt():
    """Ein Tippfehler ergäbe sonst 'geparst 0,00 vs. Report 0,00 — Abweichung 0,00'."""
    p = mini_profil(summen=[{"label": "Summe",
                             "muster": r"(?im)^Summe{VOR}({NUM})",
                             "vergleich": "summen_basis.gibt_es_nicht",
                             "toleranz": "0.01"}])
    probleme = bp.pruefe_profil(p)
    assert any("gibt_es_nicht" in x for x in probleme), probleme
    wirft(sl.ParseError, bp.wende_an, p, MINI_TEXT, label="unerreichbarer Pfad")

    tippfehler = mini_profil(summen=[{"label": "Summe",
                                      "muster": r"(?im)^Summe{VOR}({NUM})",
                                      "vergleich": "paragraph_23.netto_ergebnis",
                                      "toleranz": "0.01"}])
    assert any("netto_ergebnis" in x for x in bp.pruefe_profil(tippfehler))


@case
def test_fehlender_vergleich_pfad_wirft_auch_zur_laufzeit():
    """Zweite Verteidigungslinie, falls ein Pfad erst zur Laufzeit fehlt."""
    warnungen = []
    wirft(sl.ParseError, bp._vergleichswert, {"summen_basis": {}},
          "summen_basis.fehlt", "Test", warnungen, label="fehlender Pfad")
    eq(bp._vergleichswert({"a": {"b": "12.00"}}, "a.b", "Test", warnungen), "12.00")
    eq(bp._vergleichswert({"a": {"b": None}}, "a.b", "Test", warnungen), 0)
    assert warnungen and "leer" in warnungen[0], warnungen


@case
def test_alle_profile_haben_erreichbare_vergleichspfade():
    for p in bp.lade_profile():
        moeglich = bp.erzeugbare_pfade(p)
        for s in p.summen:
            assert s["vergleich"] in moeglich, f"{p.id}: {s['vergleich']}"


# ── ungeprüfte Profile ───────────────────────────────────────────────────────
@case
def test_ungepruefte_profile_warnen_deutlich():
    p = mini_profil(status="ungeprueft", geprueft_am=None)
    eq(bp.pruefe_profil(p), [], "ungeprüft ist gültig, nur eben ungeprüft")
    r = bp.wende_an(p, MINI_TEXT)
    assert any("UNGEPRÜFT" in w for w in r["warnungen"]), r["warnungen"]
    eq(r["profil_status"], "ungeprueft")
    for pid in ("binance", "coinbase", "bitpanda"):
        prof = bp.profil_nach_id(pid)
        eq(prof.status, "ungeprueft", f"{pid} darf nicht als geprüft gelten")
        eq(prof.geprueft_am, None, f"{pid}: geprueft_am muss null sein")


# ── CSV-Profile ──────────────────────────────────────────────────────────────
@case
def test_csv_profile_bilden_die_kanonischen_felder_ab():
    erwartet = {
        "binance": [("2024-02-05 09:12:31", "deposit", "BTC", "0.01500000", None)],
        "coinbase": [("2024-01-15 10:22:31", "buy", "BTC", "0.02000000", "800.00")],
        "bitpanda": [("2024-02-12 09:30:00", "buy", "BTC", "0.00800000", "400.00")],
    }
    for pid, erste in erwartet.items():
        r = bp.wende_an(bp.profil_nach_id(pid), fixture(pid), quelle=pid + ".csv")
        tx = r["transactions"][0]
        ts, typ, asset, menge, eur = erste[0]
        eq(tx["timestamp"], ts, f"{pid} timestamp")
        eq(tx["type"], typ, f"{pid} type")
        eq(tx["asset"], asset, f"{pid} asset")
        eq(tx["amount"], menge, f"{pid} amount")
        eq(tx["eur_value"], eur, f"{pid} eur_value")


@case
def test_binance_hat_keinen_eurwert_und_sagt_es():
    r = bp.wende_an(bp.profil_nach_id("binance"), fixture("binance"))
    assert all(t.get("_needs_fmv") for t in r["transactions"]), \
        "ohne EUR-Spalte muss jede Zeile als ergänzungsbedürftig markiert sein"
    assert any("ohne EUR-Wert" in w for w in r["warnungen"]), r["warnungen"]


@case
def test_bitpanda_ueberspringt_fiat_und_zaehlt_es():
    r = bp.wende_an(bp.profil_nach_id("bitpanda"), fixture("bitpanda"))
    sb = r["summen_basis"]
    eq(sb["uebersprungene_zeilen"], 1, "EUR-Einzahlung ist kein Krypto-Vorgang")
    eq(sb["verarbeitete_zeilen"], sb["csv_datenzeilen"], "Zeilenabgleich geht auf")
    eq(len(r["transactions"]), 2)


@case
def test_csv_unbekannter_typ_bricht_den_zeilenabgleich_ab():
    """Eine nicht zugeordnete Zeile darf nicht stillschweigend verschwinden."""
    text = fixture("coinbase") + (
        "2024-09-01T09:00:00Z,Convert,BTC,0.00100000,EUR,55000.00,55.00,55.00,0.00,"
        "Converted to ETH\n")
    wirft(sl.PlausibilityError, bp.wende_an, bp.profil_nach_id("coinbase"), text,
          label="Convert ist bewusst nicht zugeordnet")
    r = bp.wende_an(bp.profil_nach_id("coinbase"), text, strikt=False)
    eq(r["summen_basis"]["nicht_zugeordnete_zeilen"], 1)
    assert any("Convert" in w for w in r["warnungen"]), r["warnungen"]


@case
def test_coinbase_transfer_und_ertragstypen_bleiben_unzugeordnet():
    """krypto_fifo verwirft deposit/withdrawal — jede Zuordnung dorthin ist eine
    Aussage über Steuerbarkeit, die aus der Typspalte allein nicht folgt."""
    zusatz = (
        "2024-03-01T09:00:00Z,Send,BTC,0.00100000,EUR,55000.00,,,0.00,Sent to wallet\n"
        "2024-03-02T09:00:00Z,Receive,BTC,0.00200000,EUR,55000.00,,,0.00,Received\n"
        "2024-03-03T09:00:00Z,Rewards Income,USDC,5.00000000,EUR,1.00,5.00,5.00,0.00,"
        "USDC rewards\n")
    text = fixture("coinbase") + zusatz
    profil = bp.profil_nach_id("coinbase")
    for typ in ("Send", "Receive", "Rewards Income"):
        assert typ not in (profil.csv["typ_werte"] or {}), \
            f"{typ} darf nicht blind zugeordnet werden"
    wirft(sl.PlausibilityError, bp.wende_an, profil, text,
          label="unzugeordnete Transfer-/Ertragszeilen")
    r = bp.wende_an(profil, text, strikt=False)
    eq(r["summen_basis"]["nicht_zugeordnete_zeilen"], 3)
    eq(len(r["transactions"]), 3, "nur Kauf, Verkauf und Staking Income")
    meldung = " ".join(r["warnungen"])
    for typ in ("Send", "Receive", "Rewards Income"):
        assert typ in meldung, f"{typ} wird nicht gemeldet: {meldung}"
    assert not any(t["type"] in ("deposit", "withdrawal") for t in r["transactions"]), \
        "kein stiller Nicht-Vorgang"


@case
def test_bitpanda_gebuehr_enthaelt_den_spread():
    """Nur 'Fee' anzusetzen ergäbe eine zu niedrige Kostenbasis."""
    r = bp.wende_an(bp.profil_nach_id("bitpanda"), fixture("bitpanda"))
    kauf, verkauf = r["transactions"][0], r["transactions"][1]
    eq(kauf["fee_eur"], "1.99", "Fee 1,49 + Spread 0,50")
    eq(verkauf["fee_eur"], "1.89", "Fee 1,49 + Spread 0,40")
    spalten = bp.profil_nach_id("bitpanda").csv["spalten"]
    eq(spalten["fee_eur"], ["Fee", "Spread"])


@case
def test_bitpanda_prueft_alle_waehrungsspalten():
    text = fixture("bitpanda").replace("Cryptocurrency,1,1.49,EUR,0.50,EUR",
                                       "Cryptocurrency,1,1.49,EUR,0.50,USD")
    r = bp.wende_an(bp.profil_nach_id("bitpanda"), text)
    assert any("Spread Currency" in w and "NICHT in Euro" in w
               for w in r["warnungen"]), r["warnungen"]


@case
def test_coinbase_meldet_fremdwaehrung():
    text = fixture("coinbase").replace(
        "2024-05-03T16:41:02Z,Sell,BTC,0.01000000,EUR",
        "2024-05-03T16:41:02Z,Sell,BTC,0.01000000,USD")
    r = bp.wende_an(bp.profil_nach_id("coinbase"), text)
    assert any("NICHT in Euro" in w for w in r["warnungen"]), r["warnungen"]


@case
def test_kraken_profil_entspricht_parse_inputs():
    import parse_inputs as pi
    text = fixture("kraken-ledger")
    rows, _delim = pi.parse_csv_text(text)
    erwartet, _w, _s = pi.from_kraken_ledger(rows)
    r = bp.wende_an(bp.profil_nach_id("kraken-ledger"), text)
    eq(r["transactions"], erwartet, "Profil und parse_inputs --format kraken müssen "
                                    "dasselbe liefern")


@case
def test_fehlende_csv_spalte_wirft():
    text = fixture("binance").replace("Coin", "Muenze")
    wirft(sl.ParseError, bp.wende_an, bp.profil_nach_id("binance"), text,
          label="Spalte umbenannt")


# ── CLI ──────────────────────────────────────────────────────────────────────
def _cli(*args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "parse_broker.py"),
                           *args], capture_output=True, text=True, cwd=WURZEL)


@case
def test_cli_list_zeigt_status_und_pruefdatum():
    p = _cli("--list")
    eq(p.returncode, 0, p.stderr)
    for pid in ("koinly-de", "etoro-de", "binance"):
        assert pid in p.stdout, p.stdout
    assert "UNGEPRÜFT" in p.stdout, p.stdout
    assert "2026-08-30" in p.stdout, p.stdout


@case
def test_cli_schreibt_ergebnisdatei_und_druckt_abgleich():
    with tempfile.TemporaryDirectory() as tmp:
        ziel = os.path.join(tmp, "koinly.krypto_result.json")
        p = _cli(os.path.join(FIXTURES, "koinly-de.txt"), "-o", ziel, "--year", "2024")
        eq(p.returncode, 0, p.stderr)
        assert "Abgleich" in p.stdout, p.stdout
        with open(ziel, encoding="utf-8") as f:
            r = json.load(f)
        eq(r["profil"], "koinly-de")
        eq(r["steuerjahr"], 2024)
        eq(r["paragraph_23"]["anzahl_veraeusserungen"], 3)


@case
def test_cli_standardname_haengt_am_ergebnisschema():
    eq(pbr.standard_ausgabe("a/b/koinly.pdf", bp.profil_nach_id("koinly-de")),
       "koinly.krypto_result.json")
    eq(pbr.standard_ausgabe("etoro.pdf", bp.profil_nach_id("etoro-de")),
       "etoro.kap_result.json")
    eq(pbr.standard_ausgabe("export.csv", bp.profil_nach_id("binance")),
       "export.transactions.json")


@case
def test_cli_bricht_bei_unbekanntem_report_ab():
    with tempfile.TemporaryDirectory() as tmp:
        pfad = os.path.join(tmp, "fremd.txt")
        with open(pfad, "w", encoding="utf-8") as f:
            f.write("Ein Kontoauszug ohne jedes bekannte Merkmal.\n")
        p = _cli(pfad)
        assert p.returncode != 0, "unerkannter Report muss Exit-Code != 0 liefern"
        assert "Kein Profil passt" in p.stderr, p.stderr
        assert "koinly-de" in p.stderr, "Kandidatenliste fehlt"


@case
def test_cli_bricht_bei_summenabweichung_ab():
    with tempfile.TemporaryDirectory() as tmp:
        pfad = os.path.join(tmp, "koinly.txt")
        text = fixture("koinly-de").replace("Kapitalgewinne 3.550,00",
                                            "Kapitalgewinne 9.999,00")
        with open(pfad, "w", encoding="utf-8") as f:
            f.write(text)
        p = _cli(pfad, "-o", os.path.join(tmp, "out.json"))
        assert p.returncode != 0, "Summenabweichung muss abbrechen"
        assert "ABBRUCH" in p.stderr, p.stderr


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
