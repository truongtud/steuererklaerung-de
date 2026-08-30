#!/usr/bin/env python3
"""Goldwert-Tests für scripts/krypto_fifo.py. Ausführen: python3 tests/test_krypto_fifo.py

Geprüft wird vor allem, was in einer Steuerberechnung teuer ist: Jahresabgrenzung,
Haltefrist auf den Tag genau, verlorene Zeilen, Rundungsdrift und der
Ausgabe-Kontrakt, den build_taxreport.py / export_report.py konsumieren.
"""
import sys, os
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import steuerlib as sl  # noqa: E402
import krypto_fifo as kf  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


# ── Bausteine ────────────────────────────────────────────────────────────────
def buy(ts, asset, amount, eur, fee="0"):
    return {"timestamp": ts, "type": "buy", "asset": asset,
            "amount": amount, "eur_value": eur, "fee_eur": fee}


def sell(ts, asset, amount, eur, fee="0"):
    return {"timestamp": ts, "type": "sell", "asset": asset,
            "amount": amount, "eur_value": eur, "fee_eur": fee}


def reward(ts, asset, amount, eur, kind="staking"):
    return {"timestamp": ts, "type": "reward", "asset": asset, "amount": amount,
            "eur_value": eur, "fee_eur": "0", "reward_kind": kind}


def swap(ts, asset, amount, eur, gegen_asset, gegen_menge, fee="0"):
    return {"timestamp": ts, "type": "swap", "asset": asset, "amount": amount,
            "eur_value": eur, "fee_eur": fee,
            "counter_asset": gegen_asset, "counter_amount": gegen_menge}


def p23(res):
    return res["paragraph_23"]


def hat_warnung(res, teil):
    return any(teil.lower() in w.lower() for w in res["warnungen"])


# ── Jahresabgrenzung ─────────────────────────────────────────────────────────
@case
def test_jahresfilter_vorjahresgewinn_zaehlt_nicht():
    txs = [
        buy("2022-01-01", "BTC", "1", "10000"),
        sell("2022-06-01", "BTC", "1", "12000"),      # 2.000 € Gewinn in 2022
        buy("2024-01-01", "ETH", "1", "1000"),
        sell("2024-06-01", "ETH", "1", "1500"),       # 500 € Gewinn in 2024
    ]
    r = kf.compute_crypto_tax(txs, 2024)
    eq(p23(r)["gewinn_eur"], "500.00", "nur 2024er Gewinn")
    eq(p23(r)["netto_ergebnis_eur"], "500.00")
    eq(p23(r)["anzahl_veraeusserungen"], 1, "nur die Veräußerung des Steuerjahres")
    eq(len(p23(r)["veraeusserungen"]), 1)
    eq(len(r["alle_veraeusserungen"]), 2, "Audit-Trail behält die volle Historie")
    eq([d["im_steuerjahr"] for d in r["alle_veraeusserungen"]], [False, True])
    eq(r["steuerjahr"], 2024)

    r22 = kf.compute_crypto_tax(txs, 2022)
    eq(p23(r22)["gewinn_eur"], "2000.00", "dasselbe Material, Jahr 2022")
    eq(p23(r22)["anzahl_veraeusserungen"], 1)


@case
def test_jahresfilter_vorjahresverlust_faellt_nicht_ins_steuerjahr():
    txs = [
        buy("2023-01-01", "BTC", "1", "20000"),
        sell("2023-03-01", "BTC", "1", "10000"),      # -10.000 € in 2023
        buy("2024-01-01", "ETH", "1", "1000"),
        sell("2024-03-01", "ETH", "1", "3000"),       # +2.000 € in 2024
    ]
    r = kf.compute_crypto_tax(txs, 2024)
    eq(p23(r)["verlust_eur"], "0.00", "Vorjahresverlust wird nicht mitgeschleppt")
    eq(p23(r)["netto_ergebnis_eur"], "2000.00")
    eq(p23(r)["steuerpflichtiger_betrag_eur"], "2000.00")


# ── Haltefrist ───────────────────────────────────────────────────────────────
@case
def test_haltefrist_jahrestag_ist_noch_steuerpflichtig():
    r = kf.compute_crypto_tax(
        [buy("2023-01-10", "BTC", "1", "10000"), sell("2024-01-10", "BTC", "1", "15000")],
        2024)
    d = p23(r)["veraeusserungen"][0]
    eq(d["holding_period_met"], False, "Verkauf am Jahrestag: Frist noch nicht abgelaufen")
    eq(d["taxable"], True)
    eq(p23(r)["gewinn_eur"], "5000.00")


@case
def test_haltefrist_folgetag_ist_steuerfrei():
    r = kf.compute_crypto_tax(
        [buy("2023-01-10", "BTC", "1", "10000"), sell("2024-01-11", "BTC", "1", "15000")],
        2024)
    d = p23(r)["veraeusserungen"][0]
    eq(d["holding_period_met"], True)
    eq(d["taxable"], False)
    eq(p23(r)["gewinn_eur"], "0.00", "steuerfrei zählt nicht in den § 23-Gewinn")
    eq(p23(r)["steuerfrei_langfristig_eur"], "5000.00")
    eq(r["steuerfrei_langfristig_eur"], "5000.00", "auch auf oberster Ebene")


@case
def test_haltefrist_schaltjahr():
    # 01.03.2023 -> 29.02.2024 sind 365 Tage, die Jahresfrist endet aber erst
    # am 01.03.2024 (§ 188 Abs. 2 BGB). Die alte 365-Tage-Logik lag hier falsch.
    r = kf.compute_crypto_tax(
        [buy("2023-03-01", "BTC", "1", "10000"), sell("2024-02-29", "BTC", "1", "15000")],
        2024)
    d = p23(r)["veraeusserungen"][0]
    eq(d["held_days"], 365, "365 Tage — und trotzdem steuerpflichtig")
    eq(d["holding_period_met"], False)
    eq(p23(r)["gewinn_eur"], "5000.00")

    # Anschaffung am 29.02. -> Frist endet am 28.02. des Folgejahres (§ 188 Abs. 3)
    r2 = kf.compute_crypto_tax(
        [buy("2024-02-29", "BTC", "1", "10000"), sell("2025-03-01", "BTC", "1", "15000")],
        2025)
    eq(p23(r2)["veraeusserungen"][0]["holding_period_met"], True)
    eq(p23(r2)["steuerfrei_langfristig_eur"], "5000.00")


# ── Vorzeichen und Nullmengen ────────────────────────────────────────────────
@case
def test_negative_verkaufsmenge_wird_normalisiert():
    # Börsen-Exporte führen die abgegebene Seite häufig negativ — früher fiel
    # der gesamte Gewinn still unter den Tisch.
    txs = [buy("2024-01-01", "ETH", "2", "2000"),
           sell("2024-03-01", "ETH", "-2", "3000")]
    r = kf.compute_crypto_tax(txs, 2024)
    eq(p23(r)["anzahl_veraeusserungen"], 1, "negative Menge darf nicht verschluckt werden")
    eq(p23(r)["gewinn_eur"], "1000.00")
    eq(p23(r)["veraeusserungen"][0]["amount"], "2")


@case
def test_negativer_swap_und_negative_gebuehr():
    txs = [buy("2024-01-01", "ETH", "1", "1000"),
           swap("2024-03-01", "ETH", "-1", "-2000", "SOL", "-10", fee="-20")]
    r = kf.compute_crypto_tax(txs, 2024)
    eq(p23(r)["anzahl_veraeusserungen"], 1)
    eq(p23(r)["gewinn_eur"], "1000.00", "abs() auf Menge, Wert und Gebühr")
    eq(r["offene_bestaende"]["SOL"], "10")


@case
def test_nullmenge_wird_gewarnt_nicht_verschluckt():
    txs = [buy("2024-01-01", "ETH", "1", "1000"),
           sell("2024-03-01", "ETH", "0", "3000")]
    r = kf.compute_crypto_tax(txs, 2024)
    eq(p23(r)["anzahl_veraeusserungen"], 0)
    assert hat_warnung(r, "Menge ist 0"), r["warnungen"]
    assert hat_warnung(r, "Datensatz #2"), "die Warnung muss den Datensatz benennen"


# ── FIFO über mehrere Lose, mit Gebühren ─────────────────────────────────────
@case
def test_teillos_fifo_mit_gebuehren():
    txs = [
        buy("2023-05-01", "BTC", "1", "10000", fee="100"),   # 10.100 € je BTC
        buy("2023-08-01", "BTC", "1", "20000"),              # 20.000 € je BTC
        sell("2024-01-15", "BTC", "1.5", "30000", fee="300"),
    ]
    r = kf.compute_crypto_tax(txs, 2024)
    ds = p23(r)["veraeusserungen"]
    eq(len(ds), 2, "zwei Teil-Lose")
    # Los 1: 2/3 des Erlöses (20.000) - 10.100 - 2/3 der Gebühr (200)
    eq(ds[0]["acquisition_date"], "2023-05-01")
    eq(ds[0]["proceeds_eur"], "20000.00")
    eq(ds[0]["cost_basis_eur"], "10100.00")
    eq(ds[0]["fee_eur"], "200.00")
    eq(ds[0]["gain_eur"], "9700.00")
    # Los 2: 1/3 des Erlöses (10.000) - 10.000 - 100 Gebühr = -100
    eq(ds[1]["acquisition_date"], "2023-08-01")
    eq(ds[1]["amount"], "0.5")
    eq(ds[1]["fee_eur"], "100.00")
    eq(ds[1]["gain_eur"], "-100.00")
    eq(p23(r)["gewinn_eur"], "9700.00")
    eq(p23(r)["verlust_eur"], "-100.00")
    eq(p23(r)["netto_ergebnis_eur"], "9600.00")
    eq(r["offene_bestaende"]["BTC"], "0.5")


@case
def test_keine_rundungsdrift_bei_vielen_teillosen():
    # 1.000 Mikro-Lose à 1,005 € Gewinn: echt 1.005,00 €. Wer je Teil-Los rundet,
    # landet bei 1.010,00 € — genau dieser Fehler wurde behoben.
    txs = [buy("2024-01-01", "XYZ", "1", "0") for _ in range(1000)]
    txs.append(sell("2024-06-01", "XYZ", "1000", "1005"))
    r = kf.compute_crypto_tax(txs, 2024)
    eq(p23(r)["anzahl_veraeusserungen"], 1000)
    eq(p23(r)["gewinn_eur"], "1005.00", "ungerundet summieren, erst am Ende runden")
    eq(p23(r)["netto_ergebnis_eur"], "1005.00")


# ── Fehlende Anschaffungshistorie ────────────────────────────────────────────
@case
def test_fehlende_historie_cost_basis_null_mit_warnung():
    r = kf.compute_crypto_tax([sell("2024-05-01", "BTC", "1", "5000", fee="50")], 2024)
    d = p23(r)["veraeusserungen"][0]
    eq(d["acquisition_date"], "UNBEKANNT")
    eq(d["cost_basis_eur"], "0.00")
    eq(d["fee_eur"], "50.00", "Gebühr darf auf diesem Zweig nicht verloren gehen")
    eq(d["gain_eur"], "4950.00")
    eq(d["held_days"], -1)
    eq(d["taxable"], True)
    assert hat_warnung(r, "Anschaffungshistorie"), r["warnungen"]


@case
def test_teilweise_fehlende_historie():
    txs = [buy("2024-01-01", "BTC", "0.5", "1000"),
           sell("2024-05-01", "BTC", "1", "4000", fee="100")]
    r = kf.compute_crypto_tax(txs, 2024)
    ds = p23(r)["veraeusserungen"]
    eq(len(ds), 2)
    eq(ds[0]["gain_eur"], "950.00", "2000 Erlös - 1000 Kosten - 50 Gebühr")
    eq(ds[1]["acquisition_date"], "UNBEKANNT")
    eq(ds[1]["gain_eur"], "1950.00", "2000 Erlös - 0 Kosten - 50 Gebühr")
    eq(p23(r)["netto_ergebnis_eur"], "2900.00")


@case
def test_transfers_erzeugen_warnung():
    txs = [{"timestamp": "2024-01-01", "type": "deposit", "asset": "BTC",
            "amount": "1", "eur_value": "0"},
           buy("2024-02-01", "BTC", "1", "1000"),
           sell("2024-03-01", "BTC", "1", "1500")]
    r = kf.compute_crypto_tax(txs, 2024)
    assert hat_warnung(r, "deposit"), r["warnungen"]
    assert hat_warnung(r, "Cost Basis"), "Warnung muss die Folge benennen"
    eq(p23(r)["gewinn_eur"], "500.00", "Transfer selbst ist nicht steuerbar")


# ── Freigrenze § 23 ──────────────────────────────────────────────────────────
@case
def test_freigrenze_23_genau_erreicht():
    # § 23 Abs. 3 Satz 5: steuerfrei nur, wenn der Gewinn *weniger als* 1.000 €
    # beträgt. Exakt 1.000 € sind also in voller Höhe steuerpflichtig.
    r = kf.compute_crypto_tax(
        [buy("2024-01-01", "X", "1", "1000"), sell("2024-06-01", "X", "1", "2000")], 2024)
    eq(p23(r)["netto_ergebnis_eur"], "1000.00")
    eq(p23(r)["freigrenze_eur"], "1000")
    eq(p23(r)["freigrenze_ueberschritten"], True)
    eq(p23(r)["steuerpflichtiger_betrag_eur"], "1000.00")


@case
def test_freigrenze_23_einen_cent_darunter():
    r = kf.compute_crypto_tax(
        [buy("2024-01-01", "X", "1", "1000"), sell("2024-06-01", "X", "1", "1999.99")], 2024)
    eq(p23(r)["netto_ergebnis_eur"], "999.99")
    eq(p23(r)["freigrenze_ueberschritten"], False)
    eq(p23(r)["steuerpflichtiger_betrag_eur"], "0.00")
    eq(p23(r)["verlustvortrag_eur"], "0.00")


@case
def test_freigrenze_23_600_bis_2023():
    txs = [buy("2023-01-01", "X", "1", "1000"), sell("2023-06-01", "X", "1", "1600")]
    r = kf.compute_crypto_tax(txs, 2023)
    eq(p23(r)["freigrenze_eur"], "600")
    eq(p23(r)["steuerpflichtiger_betrag_eur"], "600.00", "600 € = Freigrenze erreicht")


@case
def test_freigrenze_kommt_ausschliesslich_aus_steuerlib():
    """Keine Steuerkonstante außerhalb von steuerlib.py — auch kein Ersatzwert.

    Gegenprobe durch Veränderung der Tabelle: die Engine muss den geänderten Wert
    übernehmen, statt irgendwo 1.000 € hartzukodieren.
    """
    txs = [buy("2024-01-01", "X", "1", "1000"), sell("2024-06-01", "X", "1", "1900")]
    original = dict(sl.FREIGRENZE_23)
    try:
        sl.FREIGRENZE_23[2024] = D("2000")
        r = kf.compute_crypto_tax(txs, 2024)
        eq(p23(r)["freigrenze_eur"], "2000", "die Engine liest die Tabelle, nicht sich selbst")
        eq(p23(r)["steuerpflichtiger_betrag_eur"], "0.00", "900 € < 2.000 €")
    finally:
        sl.FREIGRENZE_23.clear()
        sl.FREIGRENZE_23.update(original)


@case
def test_nicht_hinterlegtes_jahr_erfindet_keine_freigrenze():
    """Für ein Jahr ohne hinterlegte Freigrenze wird nichts geraten: das rohe Netto
    wird mit 'freigrenze_angewendet': false ausgewiesen und gewarnt — so kann
    build_taxreport.py die Quelle wie jede andere Rohquelle weiterverarbeiten."""
    txs = [buy("2018-01-01", "X", "1", "1000"), sell("2018-06-01", "X", "1", "1700")]
    r = kf.compute_crypto_tax(txs, 2018)
    eq(p23(r)["freigrenze_angewendet"], False)
    eq(p23(r)["freigrenze_eur"], None)
    eq(p23(r)["freigrenze_ueberschritten"], None)
    eq(p23(r)["netto_ergebnis_eur"], "700.00")
    eq(p23(r)["steuerpflichtiger_betrag_eur"], "700.00", "roh, ungeprüft")
    assert hat_warnung(r, "nicht hinterlegt"), r["warnungen"]
    assert hat_warnung(r, "FREIGRENZE_23"), r["warnungen"]
    treffer = [w for w in r["warnungen"] if "Freigrenze" in w]
    assert not any("600" in w or "1.000" in w for w in treffer), \
        f"es darf kein Ersatzwert mehr genannt werden: {treffer}"


# ── Verlustjahr ──────────────────────────────────────────────────────────────
@case
def test_verlustjahr_erzeugt_verlustvortrag():
    txs = [buy("2024-01-01", "X", "1", "5000"), sell("2024-06-01", "X", "1", "3000"),
           buy("2024-02-01", "Y", "1", "1000"), sell("2024-07-01", "Y", "1", "1500")]
    r = kf.compute_crypto_tax(txs, 2024)
    eq(p23(r)["gewinn_eur"], "500.00")
    eq(p23(r)["verlust_eur"], "-2000.00")
    eq(p23(r)["netto_ergebnis_eur"], "-1500.00")
    eq(p23(r)["steuerpflichtiger_betrag_eur"], "0.00")
    eq(p23(r)["verlustvortrag_eur"], "1500.00", "Schlüssel, den build_taxreport.py liest")
    assert hat_warnung(r, "Verlustfeststellung"), r["warnungen"]


@case
def test_ohne_verlust_kein_verlustvortrag():
    r = kf.compute_crypto_tax([], 2024)
    eq(p23(r)["verlustvortrag_eur"], "0.00")
    eq(p23(r)["netto_ergebnis_eur"], "0.00")
    eq(p23(r)["anzahl_veraeusserungen"], 0)


# ── § 22 Nr. 3 Staking ───────────────────────────────────────────────────────
@case
def test_staking_freigrenze_256_knapp_darunter():
    txs = [reward("2024-03-01", "ETH", "0.1", "100"),
           reward("2024-09-01", "ETH", "0.1", "155.99")]
    r = kf.compute_crypto_tax(txs, 2024)
    p22 = r["paragraph_22_nr_3"]
    eq(p22["summe_eur"], "255.99")
    eq(p22["summe_zufluesse_eur"], "255.99", "Altname bleibt bedient")
    eq(p22["freigrenze_ueberschritten"], False)
    eq(p22["steuerpflichtig_eur"], "0.00")


@case
def test_staking_freigrenze_256_genau_erreicht():
    txs = [reward("2024-03-01", "ETH", "0.1", "100"),
           reward("2024-09-01", "ETH", "0.1", "156")]
    r = kf.compute_crypto_tax(txs, 2024)
    p22 = r["paragraph_22_nr_3"]
    eq(p22["summe_eur"], "256.00")
    eq(p22["freigrenze_ueberschritten"], True)
    eq(p22["steuerpflichtig_eur"], "256.00", "Freigrenze: dann der volle Betrag")
    eq(p22["freigrenze_eur"], "256")


@case
def test_staking_nur_das_steuerjahr():
    txs = [reward("2023-05-01", "ETH", "1", "900"),
           reward("2024-05-01", "ETH", "1", "300")]
    r = kf.compute_crypto_tax(txs, 2024)
    eq(r["paragraph_22_nr_3"]["summe_eur"], "300.00", "Vorjahres-Staking zählt nicht mit")
    eq(len(r["paragraph_22_nr_3"]["ertraege"]), 1)
    eq(len(r["paragraph_22_nr_3"]["alle_ertraege"]), 2)


@case
def test_staking_bildet_neues_los_mit_marktwert():
    # Zufluss ist zugleich Anschaffung zum Marktwert (§ 22 Nr. 3 + § 23).
    txs = [reward("2024-01-10", "ETH", "1", "2000"),
           sell("2024-06-01", "ETH", "1", "2500")]
    r = kf.compute_crypto_tax(txs, 2024)
    d = p23(r)["veraeusserungen"][0]
    eq(d["acquisition_date"], "2024-01-10")
    eq(d["cost_basis_eur"], "2000.00")
    eq(d["gain_eur"], "500.00")
    eq(r["paragraph_22_nr_3"]["steuerpflichtig_eur"], "2000.00")


# ── Tausch ───────────────────────────────────────────────────────────────────
@case
def test_swap_gebuehr_landet_in_den_anschaffungskosten():
    txs = [
        buy("2024-01-01", "ETH", "1", "1000"),
        swap("2024-03-01", "ETH", "1", "2000", "SOL", "10", fee="20"),
        sell("2024-09-01", "SOL", "10", "2020"),
    ]
    r = kf.compute_crypto_tax(txs, 2024)
    ds = p23(r)["veraeusserungen"]
    eq(len(ds), 2)
    eq(ds[0]["asset"], "ETH")
    eq(ds[0]["gain_eur"], "1000.00", "Tauscherlös 2000 - AK 1000; Gebühr hier nicht doppelt")
    eq(ds[1]["asset"], "SOL")
    eq(ds[1]["cost_basis_eur"], "2020.00", "Tauschgebühr aktiviert")
    eq(ds[1]["gain_eur"], "0.00")


@case
def test_swap_ohne_gegenwert_warnt():
    txs = [buy("2024-01-01", "ETH", "1", "1000"),
           {"timestamp": "2024-03-01", "type": "swap", "asset": "ETH", "amount": "1",
            "eur_value": "2000", "fee_eur": "0"}]
    r = kf.compute_crypto_tax(txs, 2024)
    eq(p23(r)["gewinn_eur"], "1000.00", "die Veräußerung zählt trotzdem")
    assert hat_warnung(r, "counter_asset"), r["warnungen"]


# ── Eingabeformate und Fehlerverhalten ───────────────────────────────────────
@case
def test_deutsche_notation_und_iso_datum():
    txs = [buy("01.02.2024", "BTC", "0,5", "1.234,56", fee="1,44"),
           sell("2024-09-01T12:00:00Z", "BTC", "0,5", "2.000,00")]
    r = kf.compute_crypto_tax(txs, 2024)
    d = p23(r)["veraeusserungen"][0]
    eq(d["acquisition_date"], "2024-02-01", "DD.MM.YYYY korrekt gelesen")
    eq(d["disposal_date"], "2024-09-01", "ISO mit Z korrekt gelesen")
    eq(d["cost_basis_eur"], "1236.00")
    eq(d["gain_eur"], "764.00")


@case
def test_unlesbare_pflichtfelder_werfen_klaren_fehler():
    faelle = [
        ([{"type": "sell", "asset": "BTC", "amount": "1", "eur_value": "100"}],
         "Zeitstempel"),
        ([sell("2024-01-01", "BTC", "n/a", "100")], "amount"),
        ([sell("2024-01-01", "BTC", "1", "")], "eur_value"),
        ([sell("2024-01-01", "", "1", "100")], "asset"),
        ([sell("kein datum", "BTC", "1", "100")], "Zeitstempel"),
    ]
    for txs, stichwort in faelle:
        try:
            kf.compute_crypto_tax(txs, 2024)
        except sl.ParseError as e:
            assert stichwort in str(e), f"Fehlertext nennt '{stichwort}' nicht: {e}"
            assert "Datensatz #1" in str(e), f"Fehlertext benennt den Datensatz nicht: {e}"
            continue
        raise AssertionError(f"{txs!r} hätte ParseError werfen müssen")


@case
def test_unbekannter_typ_wird_gemeldet():
    r = kf.compute_crypto_tax(
        [{"timestamp": "2024-01-01", "type": "airdrop", "asset": "X",
          "amount": "1", "eur_value": "10"}], 2024)
    assert hat_warnung(r, "Unbekannter Transaktionstyp"), r["warnungen"]


# ── Ausgabe-Kontrakt (wird von build_taxreport.py / export_report.py gelesen) ─
@case
def test_ausgabe_kontrakt():
    txs = [buy("2022-01-01", "BTC", "1", "10000"),
           sell("2024-06-01", "BTC", "1", "13000"),     # > 1 Jahr -> steuerfrei
           buy("2024-01-05", "LTC", "1", "100"),
           sell("2024-08-05", "LTC", "1", "400"),       # < 1 Jahr -> steuerpflichtig
           reward("2024-02-01", "ETH", "1", "300")]
    r = kf.compute_crypto_tax(txs, 2024)
    eq(p23(r)["steuerfrei_langfristig_eur"], "3000.00")
    eq(p23(r)["netto_ergebnis_eur"], "300.00")

    for k in ("steuerjahr", "methode", "quelle", "paragraph_23", "paragraph_22_nr3",
              "paragraph_22_nr_3", "steuerfrei_langfristig_eur", "warnungen",
              "elster_extra", "hinweise", "offene_bestaende", "tax_year"):
        assert k in r, f"Top-Level-Schlüssel fehlt: {k}"
    assert isinstance(r["steuerjahr"], int)
    assert isinstance(r["methode"], str) and isinstance(r["quelle"], str)
    assert isinstance(r["warnungen"], list) and all(isinstance(w, str) for w in r["warnungen"])
    assert isinstance(r["elster_extra"], list)

    for k in ("gewinn_eur", "verlust_eur", "netto_ergebnis_eur", "freigrenze_eur",
              "steuerpflichtiger_betrag_eur", "verlustvortrag_eur",
              "freigrenze_angewendet", "veraeusserungen", "disposals",
              "anzahl_veraeusserungen", "summe_steuerpflichtige_gewinne_eur",
              "summe_verluste_eur", "summe_steuerfrei_gt_1_jahr_eur",
              "freigrenze_ueberschritten"):
        assert k in p23(r), f"paragraph_23-Schlüssel fehlt: {k}"
    eq(p23(r)["freigrenze_angewendet"], True, "diese Engine kennt die volle Historie")
    eq(p23(r)["disposals"], p23(r)["veraeusserungen"], "Alt- und Neuname identisch")

    for k in ("summe_eur", "freigrenze_eur", "steuerpflichtig_eur",
              "freigrenze_angewendet", "summe_zufluesse_eur", "ertraege"):
        assert k in r["paragraph_22_nr3"], f"paragraph_22_nr3-Schlüssel fehlt: {k}"
    eq(r["paragraph_22_nr3"], r["paragraph_22_nr_3"], "beide Schreibweisen identisch")
    eq(r["paragraph_22_nr3"]["freigrenze_angewendet"], True)

    # alle Geldbeträge als String in Punkt-Notation mit 2 Nachkommastellen
    for k in ("gewinn_eur", "verlust_eur", "netto_ergebnis_eur",
              "steuerpflichtiger_betrag_eur", "verlustvortrag_eur"):
        v = p23(r)[k]
        assert isinstance(v, str) and D(v) == D(v), f"{k} ist kein Decimal-String: {v!r}"
        eq(v, str(sl.q2(D(v))), f"{k} auf 2 Nachkommastellen")


@case
def test_json_serialisierbar_und_cli():
    import json, subprocess, tempfile
    txs = [buy("2023-08-01", "BTC", "1", "10000"),   # < 1 Jahr -> steuerpflichtig
           sell("2024-06-01", "BTC", "1", "13000"),
           reward("2024-02-01", "ETH", "1", "300")]
    r = kf.compute_crypto_tax(txs, 2024)
    json.loads(json.dumps(r, ensure_ascii=False))   # muss ohne Custom-Encoder gehen

    skript = os.path.join(os.path.dirname(__file__), "..", "scripts", "krypto_fifo.py")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "tx.json")
        dst = os.path.join(tmp, "out.json")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(txs, f)
        p = subprocess.run([sys.executable, skript, src, "2024", dst],
                           capture_output=True, text=True)
        eq(p.returncode, 0, f"CLI fehlgeschlagen: {p.stderr}")
        with open(dst, encoding="utf-8") as f:
            aus = json.load(f)
        eq(aus["paragraph_23"]["netto_ergebnis_eur"], "3000.00")
        eq(aus["steuerjahr"], 2024)

        # unlesbare Eingabe -> klare deutsche Meldung, kein Traceback
        with open(src, "w", encoding="utf-8") as f:
            json.dump([{"type": "sell", "asset": "BTC", "amount": "1", "eur_value": "1"}], f)
        p = subprocess.run([sys.executable, skript, src, "2024", dst],
                           capture_output=True, text=True)
        eq(p.returncode, 2, "Datenfehler -> Exit 2")
        assert "FEHLER in den Transaktionsdaten" in p.stderr, p.stderr
        assert "Traceback" not in p.stderr, p.stderr


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
