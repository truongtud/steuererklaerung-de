#!/usr/bin/env python3
"""Goldwert-Tests für die Parser (koinly/etoro/inputs/pdf).

Ausführen: python3 tests/test_parser.py

Es wird kein echtes PDF gebraucht — alle Parser sind so geschnitten, dass der
Schritt "Text/Tabelle -> Ergebnis" mit einem String aufrufbar ist.
"""
import io
import os
import subprocess
import sys
import tempfile
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import steuerlib as sl          # noqa: E402
import parse_koinly as pk       # noqa: E402
import parse_etoro as pe        # noqa: E402
import parse_inputs as pi       # noqa: E402
import parse_pdf as pp          # noqa: E402

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")

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


# ── Fixtures ─────────────────────────────────────────────────────────────────
def koinly_report(zeilen, gesamt, *, anzahl=None, halte="Kurzfristig",
                  einnahmen=None, kopf=True):
    """Baut einen synthetischen Koinly-Steuerbericht aus Rohzeilen."""
    teile = ["Koinly Steuerbericht 2024", "Kapitalgewinne"]
    if kopf:
        teile.append("Verkaufsdatum Kaufdatum Asset Menge Kosten Erlös Gewinn "
                     "Wallet Haltedauer")
    teile += list(zeilen)
    teile += ["Zusammenfassung", f"Kapitalgewinne {gesamt}"]
    if anzahl is not None:
        teile.append(f"Veräußerungen: {anzahl}")
    if einnahmen:
        teile.append("Zusammenfassung Einnahmen")
        teile += list(einnahmen)
    return "\n".join(teile) + "\n"


DE_ZEILEN = [
    "31/12/2024 10:30 01/06/2024 09:00 BTC 0,5 4.000,00 7.000,00 3.000,00 Kraken Kurzfristig",
    "30/11/2024 10:30 01/06/2024 09:00 ETH 2 1.000,00 900,00 -100,00 Ledger Kurzfristig",
]
EN_ZEILEN = [
    "31/12/2024 10:30 01/06/2024 09:00 BTC 0.5 4,000.00 7,000.00 3,000.00 Kraken Short-term",
    "30/11/2024 10:30 01/06/2024 09:00 ETH 2 1,000.00 900.00 -100.00 Ledger Short-term",
]


def etoro_report(zeilen, kopf=True):
    teile = ["eToro Steuerbericht", "Guten Tag Max Mustermann,",
             "Berichtszeitraum: 01.01.2024 - 31.12.2024", "Depot: 123456"] if kopf else []
    return "\n".join(teile + list(zeilen)) + "\n"


# ── Koinly: Veräußerungstabelle ──────────────────────────────────────────────
@case
def test_koinly_de_und_en_gleiches_ergebnis():
    """Ein deutsch formatierter Report ergab früher 0 Zeilen und 0 € Ergebnis."""
    de = pk.build_result(koinly_report(DE_ZEILEN, "2.900,00", anzahl=2), 2024, quelle="de")
    en = pk.build_result(koinly_report(EN_ZEILEN, "2,900.00", anzahl=2), 2024, quelle="en")
    eq(de["zahlennotation"], "de", "DE-Notation erkannt")
    eq(en["zahlennotation"], "en", "EN-Notation erkannt")
    for r, name in ((de, "DE"), (en, "EN")):
        p = r["paragraph_23"]
        eq(p["anzahl_veraeusserungen"], 2, f"{name}: beide Zeilen gelesen")
        eq(p["netto_ergebnis_eur"], "2900.00", f"{name}: Netto")
        eq(p["gewinn_eur"], "3000.00", f"{name}: Gewinne")
        eq(p["verlust_eur"], "-100.00", f"{name}: Verluste")
    eq(de["paragraph_23"]["disposals"][0]["cost_basis_eur"],
       en["paragraph_23"]["disposals"][0]["cost_basis_eur"], "identische Kostenbasis")


@case
def test_koinly_grosser_gewinn_mit_tausendertrenner():
    """Genau die größten Gewinne (>= 1.000 €) fielen früher aus dem Regex."""
    zeilen = ["31/12/2024 10:30 01/06/2024 09:00 BTC 0.5 2,000.00 14,000.00 "
              "12,000.00 Kraken Short-term"]
    r = pk.build_result(koinly_report(zeilen, "12,000.00", anzahl=1), 2024, quelle="x")
    eq(r["paragraph_23"]["anzahl_veraeusserungen"], 1)
    eq(r["paragraph_23"]["gewinn_eur"], "12000.00", "12.000 € statt 0 €")

    zeilen_de = ["31/12/2024 10:30 01/06/2024 09:00 BTC 0,5 2.000,00 14.000,00 "
                 "12.000,00 Kraken Kurzfristig"]
    r2 = pk.build_result(koinly_report(zeilen_de, "12.000,00", anzahl=1), 2024, quelle="x")
    eq(r2["paragraph_23"]["gewinn_eur"], "12000.00", "DE-Notation, gleicher Wert")


@case
def test_koinly_negativer_gewinn_und_unicode_minus():
    zeilen = [
        "31/12/2024 10:30 01/06/2024 09:00 BTC 0,5 7.000,00 4.000,00 −3.000,00 Kraken Kurzfristig",
        "30/11/2024 10:30 01/06/2024 09:00 ETH 2 1.000,00 900,00 (100,00) Ledger Kurzfristig",
    ]
    r = pk.build_result(koinly_report(zeilen, "-3.100,00", anzahl=2), 2024, quelle="x")
    p = r["paragraph_23"]
    eq(p["netto_ergebnis_eur"], "-3100.00", "Unicode-Minus und Klammern bleiben negativ")
    eq(p["gewinn_eur"], "0.00")
    eq(p["verlustvortrag_eur"], "3100.00", "Verlustvortrag = |Netto|")


@case
def test_koinly_fehlende_wallet_spalte_und_asset_mit_leerzeichen():
    zeilen = [
        "5/3/2024 9:05:00 1/2/2023 08:00 USD Coin 100 1.000,00 1.200,00 200,00 Kurzfristig",
    ]
    r = pk.build_result(koinly_report(zeilen, "200,00", anzahl=1), 2024,
                        quelle="x", dateformat="de")
    d = r["paragraph_23"]["disposals"][0]
    eq(d["asset"], "USD Coin", "Assetname mit Leerzeichen")
    eq(d["disposal_date"], "2024-03-05", "1-stelliger Tag + Sekunden")
    eq(d["note"], "Quelle: Koinly", "keine Wallet-Zelle -> keine Notiz")


@case
def test_koinly_langfristig_ist_steuerfrei():
    zeilen = [
        "31/12/2024 10:30 01/06/2020 09:00 BTC 0,5 4.000,00 9.000,00 5.000,00 Kraken Langfristig",
        "30/11/2024 10:30 01/06/2024 09:00 ETH 2 1.000,00 1.100,00 100,00 Ledger Kurzfristig",
    ]
    r = pk.build_result(koinly_report(zeilen, "5.100,00", anzahl=2), 2024, quelle="x")
    p = r["paragraph_23"]
    eq(p["netto_ergebnis_eur"], "100.00", "nur die kurzfristige Zeile ist steuerbar")
    eq(p["steuerfrei_langfristig_eur"], "5000.00")


@case
def test_koinly_abgleich_schlaegt_bei_fehlender_zeile_an():
    """Das eigentliche Sicherheitsnetz: verlorene Zeile -> Abbruch statt Erfolg."""
    text = koinly_report(DE_ZEILEN, "2.900,00", anzahl=2)
    ohne = "\n".join(l for l in text.splitlines() if not l.startswith("30/11"))
    wirft(sl.PlausibilityError, pk.build_result, ohne, 2024, quelle="x",
          label="fehlende Zeile")

    # Auch ohne Anzahl-Angabe muss die Summe auffallen.
    text2 = koinly_report(DE_ZEILEN, "2.900,00")
    ohne2 = "\n".join(l for l in text2.splitlines() if not l.startswith("30/11"))
    wirft(sl.PlausibilityError, pk.build_result, ohne2, 2024, quelle="x",
          label="fehlende Zeile ohne Anzahl")


@case
def test_koinly_unlesbare_zeile_wird_gezaehlt():
    kaputt = ("31/12/2024 10:30 01/06/2024 09:00 BTC 0,5 4.000,00 7.000,00 "
              "3.000,00 Kraken ???")
    text = koinly_report(DE_ZEILEN + [kaputt], "2.900,00")
    r = pk.build_result(text, 2024, quelle="x")
    p = r["paragraph_23"]
    eq(len(p["nicht_zugeordnete_zeilen"]), 1, "unlesbare Tabellenzeile gezählt")
    assert any("NICHT" in w for w in p["warnungen"]), "Warnung fehlt"
    assert any("Nicht zugeordnete Tabellenzeilen: 1" in z for z in r["abgleich"])


@case
def test_koinly_mehrdeutiges_datum_bricht_ab():
    """US-Export: Verkauf vor Kauf unter TT/MM -> Nachfrage statt stiller Verschiebung."""
    zeilen = ["05/01/2024 10:30 01/06/2024 09:00 BTC 0,5 4.000,00 7.000,00 "
              "3.000,00 Kraken Kurzfristig"]
    text = koinly_report(zeilen, "3.000,00", anzahl=1)
    wirft(sl.ParseError, pk.build_result, text, 2024, quelle="x", label="mehrdeutig")
    # Mit ausdrücklichem Format läuft derselbe Report durch.
    r = pk.build_result(text, 2024, quelle="x", dateformat="en")
    eq(r["paragraph_23"]["disposals"][0]["disposal_date"], "2024-05-01")


# ── Koinly: Einnahmen / Freigrenze-Vertrag ───────────────────────────────────
@case
def test_koinly_einnahmen_de_und_en():
    de = pk.build_result(koinly_report(
        DE_ZEILEN, "2.900,00", anzahl=2,
        einnahmen=["Belohnungen 300,00", "Airdrops 50,00", "Sonstige Einnahmen 6,00"]),
        2024, quelle="x")
    eq(de["paragraph_22_nr3"]["netto_ergebnis_eur"], "356.00",
       "deutsche Labels werden erkannt (früher alles 0)")
    en = pk.build_result(koinly_report(
        EN_ZEILEN, "2,900.00", anzahl=2,
        einnahmen=["Rewards 300.00", "Airdrops 50.00", "Other income 6.00"]),
        2024, quelle="x")
    eq(en["paragraph_22_nr3"]["netto_ergebnis_eur"], "356.00", "englische Labels")
    eq(en["paragraph_22_nr3"]["einnahmen_detail"]["Airdrop"], "50.00",
       "Airdrop zählt zu § 22 Nr. 3")


@case
def test_koinly_cost_matcht_nicht_cost_basis():
    text = koinly_report(EN_ZEILEN, "2,900.00", anzahl=2)
    text += "Expenses\nCost basis 9,999.00\nCost 12.00\n"
    r = pk.build_result(text, 2024, quelle="x")
    eq(r["koinly_extra"]["ausgaben_detail"]["Cost"], "12.00",
       "'Cost' darf nicht an 'Cost basis' hängenbleiben")


@case
def test_koinly_wendet_keine_freigrenze_an():
    """Vertrag mit build_taxreport.py: Freigrenze gilt pro Person, nicht pro Report."""
    zeilen = ["31/12/2024 10:30 01/06/2024 09:00 BTC 0,5 4.000,00 4.800,00 "
              "800,00 Kraken Kurzfristig"]
    r = pk.build_result(koinly_report(zeilen, "800,00", anzahl=1), 2024, quelle="a.pdf")
    p = r["paragraph_23"]
    eq(p["freigrenze_angewendet"], False)
    eq(p["netto_ergebnis_eur"], "800.00", "800 € bleiben stehen (nicht auf 0 gesetzt)")
    eq(r["paragraph_22_nr3"]["freigrenze_angewendet"], False)
    eq(r["quelle"], "a.pdf", "Quelle = Eingabedatei")
    for key in ("gewinn_eur", "verlust_eur", "netto_ergebnis_eur", "verlustvortrag_eur",
                "steuerfrei_langfristig_eur", "warnungen"):
        assert key in p, f"Vertragsschlüssel {key} fehlt"
    assert "elster_extra" in r, "elster_extra fehlt"
    assert "steuerpflichtiger_betrag_eur" not in p, \
        "kein per-Report versteuerter Betrag mehr"


# ── eToro ────────────────────────────────────────────────────────────────────
@case
def test_etoro_unicode_minus_behaelt_vorzeichen():
    text = etoro_report([
        "Gewinne aus Aktienveräußerungen (Anlage KAP Zeile 20) 2.646,52",
        "Verluste aus Termingeschäften (Anlage KAP Zeile 24) −5.334,40",
        "Private Veräußerungsgeschäfte (Anlage SO Zeile 47) −1.234,56",
        "Staking (Anlage SO Zeile 11) 300,00",
    ])
    r = pe.build_result(text, 2024, quelle="etoro.pdf")
    eq(r["etoro_kap"]["z24_verluste_termingeschaefte"], "-5334.40",
       "KAP Z.24 Verlust bleibt negativ (war 0,00)")
    eq(r["paragraph_23"]["netto_ergebnis_eur"], "-1234.56", "SO Z.47 Verlust")
    eq(r["paragraph_23"]["verlustvortrag_eur"], "1234.56")
    eq(r["paragraph_22_nr3"]["netto_ergebnis_eur"], "300.00")
    eq(r["paragraph_23"]["freigrenze_angewendet"], False)
    eq(r["quelle"], "etoro.pdf")


@case
def test_etoro_betragsform_verhindert_seitenzahl():
    text = etoro_report([
        "Private Veräußerungsgeschäfte (Anlage SO Zeile 47) 1.000,00",
        "Erträge (Anlage KAP-INV Zeile 4) 2",          # Seitenzahl, kein Betrag
        "Umbrochene Beschriftung Anlage KAP 0,00 Zeile 22)",
    ])
    lines, warnungen = pe.extract_lines(text)
    eq(lines.get(("SO", 47)), D("1000.00"))
    eq(lines.get(("KAP", 22)), D("0.00"), "Betrag VOR der Zeilennummer")
    assert ("KAP-INV", 4) not in lines, "Seitenzahl darf kein Wert werden"
    assert warnungen, "fehlender Betrag muss gemeldet werden"


@case
def test_etoro_ohne_zuordnungen_bricht_ab():
    """Layoutwechsel ('Zeile 47' ohne Klammer) ergab früher {} und Erfolgsmeldung."""
    text = etoro_report(["Private Veräußerungsgeschäfte Anlage SO Zeile 47 fehlt hier"])
    wirft(sl.ParseError, pe.build_result, text, 2024, label="keine Zuordnungen")


@case
def test_etoro_abgleich_gegen_ausgewiesene_summe():
    zeilen = [
        "Ausländische Kapitalerträge (Anlage KAP Zeile 19) 1.000,00",
        "Gewinne aus Aktienveräußerungen (Anlage KAP Zeile 20) 500,00",
        "Summe der Kapitalerträge 1.500,00",
    ]
    r = pe.build_result(etoro_report(zeilen), 2024, quelle="x")
    assert any("Abweichung 0,00" in z for z in r["abgleich"]), r["abgleich"]
    # Fehlt eine Position, muss der Abgleich anschlagen.
    kaputt = [z for z in zeilen if "Zeile 20" not in z]
    wirft(sl.PlausibilityError, pe.build_result, etoro_report(kaputt), 2024,
          label="fehlende KAP-Position")


@case
def test_etoro_jahr_und_person():
    text = etoro_report(["Private Veräußerungsgeschäfte (Anlage SO Zeile 47) 10,00"])
    eq(pe.detect_year(text), 2024)
    eq(pe.detect_person(text), ("Max Mustermann", "123456"))


# ── parse_inputs ─────────────────────────────────────────────────────────────
@case
def test_semikolon_csv_mit_deutschen_zahlen():
    csv_text = ("timestamp;type;asset;amount;eur_value;fee_eur\n"
                "2024-03-01 10:00:00;buy;BTC;0,5;12.345,67;1,50\n"
                "2024-06-02 11:00:00;sell;BTC;0,5;15.000,00;2,00\n")
    rows, delim = pi.parse_csv_text(csv_text)
    eq(delim, ";", "Semikolon erkannt (sonst 1 Spalte, 0 Werte)")
    txs, warnungen = pi.from_canonical(rows)
    eq(len(txs), 2)
    eq(txs[0]["eur_value"], "12345.67", "1.234,56 wird nicht zu 1234567")
    eq(txs[0]["amount"], "0.5")
    eq(txs[0]["fee_eur"], "1.50")
    eq(pi.pruefe_typen(txs), [], "alle Typen gültig")


@case
def test_leere_typen_brechen_ab():
    csv_text = ("timestamp;type;asset;amount;eur_value\n"
                "2024-03-01;;BTC;1;100,00\n"
                "2024-03-02;;BTC;1;100,00\n"
                "2024-03-03;buy;BTC;1;100,00\n")
    rows, _ = pi.parse_csv_text(csv_text)
    txs, _ = pi.from_canonical(rows)
    wirft(sl.ParseError, pi.pruefe_typen, txs, label="mehrheitlich leerer type")


@case
def test_unbekannter_typ_wird_markiert():
    csv_text = ("timestamp;type;asset;amount;eur_value\n"
                "2024-03-01;buy;BTC;1;100,00\n"
                "2024-03-02;margin;BTC;1;100,00\n")
    rows, _ = pi.parse_csv_text(csv_text)
    txs, _ = pi.from_canonical(rows)
    warnungen = pi.pruefe_typen(txs)
    assert warnungen and "margin" in warnungen[0], warnungen
    eq(txs[1]["_needs_review"], True)


@case
def test_kraken_ledger_deutsche_zahlen():
    ledger = ("txid;refid;time;type;subtype;aclass;asset;amount;fee;balance\n"
              "L1;R1;2024-03-01 10:00:00;trade;;currency;ZEUR;-1.234,56;1,50;0\n"
              "L2;R1;2024-03-01 10:00:00;trade;;currency;XXBT;0,05;0;0\n"
              "L3;R2;2024-04-01 10:00:00;staking;;currency;ETH.S;0,25;0;0\n"
              "L4;;2024-05-01 10:00:00;deposit;;currency;XETC;10,0;0;0\n"
              "L5;;2024-05-02 10:00:00;deposit;;currency;XXLM;5,0;0;0\n"
              "L6;R9;2024-06-01 10:00:00;margin;;currency;XXBT;0,01;0;0\n")
    rows, delim = pi.parse_csv_text(ledger)
    eq(delim, ";")
    txs, warnungen, stats = pi.from_kraken_ledger(rows)
    nach_id = {t["tx_id"]: t for t in txs}
    eq(nach_id["L2"]["type"], "buy")
    eq(nach_id["L2"]["eur_value"], "1234.56", "DE-Notation stürzt nicht mehr ab")
    eq(nach_id["L2"]["amount"], "0.05")
    eq(nach_id["L3"]["type"], "reward")
    eq(nach_id["L3"]["asset"], "ETH", "ETH.S gehört in denselben FIFO-Topf wie ETH")
    eq(nach_id["L4"]["type"], "deposit", "Einlieferung wird ausgegeben, nicht verworfen")
    eq(nach_id["L4"]["asset"], "ETC")
    eq(nach_id["L5"]["asset"], "XLM", "4-stellige Altcodes werden normalisiert")
    eq(stats["nicht_zugeordnet"], 1, "margin-Zeile wird als nicht zugeordnet gemeldet")
    eq(stats["zeilen"], 6)
    assert any("L6" in w for w in warnungen), warnungen


@case
def test_kraken_ohne_refid_bildet_keine_sammelgruppe():
    ledger = ("txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"
              "L1,,2024-03-01 10:00:00,deposit,,currency,XETH,1.0,0,0\n"
              "L2,,2024-03-02 10:00:00,deposit,,currency,XXBT,2.0,0,0\n")
    rows, _ = pi.parse_csv_text(ledger)
    txs, _w, stats = pi.from_kraken_ledger(rows)
    eq(len(txs), 2, "zwei unabhängige Buchungen bleiben zwei Buchungen")
    eq({t["asset"] for t in txs}, {"ETH", "BTC"})
    eq(stats["nicht_zugeordnet"], 0)


@case
def test_norm_asset():
    for roh, want in [("ETH.S", "ETH"), ("ETH2.S", "ETH"), ("XETC", "ETC"),
                      ("XXLM", "XLM"), ("ZEUR", "EUR"), ("ZUSD", "USD"),
                      ("XXBT", "BTC"), ("XTZ", "XTZ"), ("USDT.M", "USDT"),
                      ("DOT.S", "DOT"), ("usdt", "USDT")]:
        eq(pi.norm_asset(roh), want, f"norm_asset({roh})")


@case
def test_latin1_fallback():
    with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as f:
        f.write("timestamp;type;asset;amount;eur_value\n".encode("latin-1"))
        f.write("2024-03-01;buy;BTC;1;100,00\n".encode("latin-1"))
        f.write("2024-03-02;buy;Gebühren-Test;1;100,00\n".encode("latin-1"))
        pfad = f.name
    try:
        rows, delim, enc = pi.read_csv(pfad)
        eq(enc, "latin-1", "kein UnicodeDecodeError, sondern latin-1")
        eq(len(rows), 2)
    finally:
        os.unlink(pfad)


@case
def test_cli_meldet_nicht_implementierte_formate():
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write("timestamp;type;asset;amount;eur_value\n2024-03-01;buy;BTC;1;100,00\n")
        pfad = f.name
    try:
        for argv, erwartet in [(["--format", "coinbase"], "nicht implementiert"),
                               (["--format", "map"], "--map")]:
            p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "parse_inputs.py"),
                                pfad] + argv, capture_output=True, text=True)
            assert p.returncode != 0, f"{argv} hätte fehlschlagen müssen"
            assert erwartet in p.stderr, f"{argv}: {p.stderr!r}"
    finally:
        os.unlink(pfad)


# ── parse_pdf ────────────────────────────────────────────────────────────────
@case
def test_pdf_iso_datum_bleibt_iso():
    eq(pp._norm_date("2024-01-02"), "2024-01-02", "ISO wurde zu 2002-01-24 verdreht")
    eq(pp._norm_date("2024-12-31 23:59:59"), "2024-12-31 23:59:59")
    eq(pp._norm_date("02.01.2024"), "2024-01-02")
    eq(pp._norm_date(""), None)


@case
def test_pdf_spaltenzuordnung():
    header = ["Datum", "Typ", "Währung", "Menge", "Betrag", "Gebühr",
              "Gebührenwährung", "Konto"]
    colmap = pp.mappe_spalten(header)
    eq(colmap["asset"], 2, "Währung ist das Asset, nicht die Gebührenwährung")
    eq(colmap["fee"], 5)
    eq(colmap["date"], 0)
    eq(colmap["amount"], 3)
    eq(colmap["eur_value"], 4)
    assert "counter_asset" not in colmap, "'Konto' ist keine Gegenwährung"


@case
def test_pdf_typ_aus_spalte_statt_aus_ganzer_zeile():
    eq(pp._classify_type("Verkauf", "31.12.2024 Verkauf BTC/EUR Wallet Kraken Earn")[0],
       "sell", "Wallet 'Kraken Earn' macht daraus keinen reward")
    eq(pp._classify_type("Kauf", "Trade BTC/EUR")[0], "buy",
       "'Trade' in der Zeile macht daraus keinen swap")
    typ, conf = pp._classify_type("", "31.12.2024 Trade BTC/EUR")
    eq(typ, "swap", "ohne type-Spalte wird geraten …")
    assert conf < 0.9, "… aber mit niedrigerer confidence"


@case
def test_pdf_summenzeilen_und_uebersprungene_tabellen():
    header = ["Datum", "Typ", "Währung", "Menge", "Betrag", "Gebühr"]
    extraction = {"_src": "report.pdf", "pages": [{"page": 1, "text": "x", "tables": [
        [header,
         ["31.12.2024", "Verkauf", "BTC", "0,5", "15.000,00", "2,00"],
         ["Summe", "", "", "", "17.000,00", ""],
         ["Zwischensumme", "", "", "", "1,00", ""]],
        [["irgendwas", "ohne"], ["header", "zeile"]],
    ]}]}
    txs, stat = pp.map_tables_to_transactions(extraction)
    eq(len(txs), 1, "Summenzeilen sind keine Transaktionen")
    eq(stat["summenzeilen"], 2)
    eq(stat["ohne_header"], 1, "übersprungene Tabelle wird gezählt, nicht verschluckt")
    eq(txs[0]["type"], "sell")
    eq(txs[0]["eur_value"], "15000.00")


@case
def test_pdf_mehrdeutige_zahl_wird_markiert():
    header = ["Datum", "Typ", "Währung", "Menge", "Betrag"]
    extraction = {"_src": "r.pdf", "pages": [{"page": 1, "text": "", "tables": [
        [header, ["01.02.2024", "Kauf", "ETH", "1.234", "2.000,00"]]]}]}
    txs, _stat = pp.map_tables_to_transactions(extraction)
    eq(txs[0]["_needs_review"], True, "1.234 ist ohne Kontext nicht auflösbar")
    eq(txs[0]["_ambig_spalten"], ["amount"])


if __name__ == "__main__":
    fails = []
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:  # unerwartete Ausnahme = ebenfalls Fehlschlag
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(CASES) - len(fails)}/{len(CASES)} bestanden")
    sys.exit(1 if fails else 0)
