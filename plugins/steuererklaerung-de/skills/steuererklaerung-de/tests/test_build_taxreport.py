#!/usr/bin/env python3
"""Goldwert-Tests für scripts/build_taxreport.py. Ausführen: python3 tests/test_build_taxreport.py"""
import json
import os
import sys
import tempfile
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_taxreport as bt  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def dec(x):
    return D(str(x))


# ── Testdaten-Hilfen ─────────────────────────────────────────────────────────

def steuerdaten(**over):
    """Minimaler, valider Steuerdaten-Satz; einzelne Blöcke per kwargs überschreibbar."""
    sd = {
        "steuerjahr": 2024,
        "zusammenveranlagung": False,
        "steuerpflichtiger": {"name": "Test", "verheiratet": False},
        "anlage_n": {"bruttoarbeitslohn": "0", "lohnsteuer": "0", "soli": "0",
                     "kirchensteuer": "0", "werbungskosten": {}},
        "anlage_kap": {"kapitalertraege": "0", "anrechenbare_kest": "0"},
        "anlage_so": {},
        "anlage_v": {}, "anlage_s": {}, "anlage_g": {},
        "vorsorge": {}, "sonderausgaben": {},
        "aussergewoehnliche_belastungen": {}, "kinder": [],
    }
    sd.update(over)
    return sd


def krypto_quelle(*, quelle="test-broker", jahr=2024, netto="0.00",
                  freigrenze_angewendet=False, steuerpflichtig="0.00",
                  vortrag="0.00", p22="0.00", warnungen=None, elster_extra=None):
    """Quell-JSON nach dem Quellen-Contract (wie ihn die Parser schreiben)."""
    return {
        "steuerjahr": jahr,
        "quelle": quelle,
        "paragraph_23": {
            "gewinn_eur": netto if dec(netto) > 0 else "0.00",
            "verlust_eur": "0.00" if dec(netto) > 0 else str(abs(dec(netto))),
            "netto_ergebnis_eur": netto,
            "steuerpflichtiger_betrag_eur": steuerpflichtig,
            "verlustvortrag_eur": vortrag,
            "freigrenze_angewendet": freigrenze_angewendet,
        },
        "paragraph_22_nr3": {"summe_eur": p22, "steuerpflichtig_eur": "0.00",
                             "freigrenze_angewendet": False},
        "steuerfrei_langfristig_eur": "0.00",
        "warnungen": warnungen or [],
        "elster_extra": elster_extra or [],
    }


def bau(sd, quellen=None):
    return bt.build(sd, quellen if quellen is not None else [])


def zeilen(report, anlage):
    return [r for r in report["elster_mapping"] if r["anlage"] == anlage]


# ── ESt-Goldwerte ────────────────────────────────────────────────────────────
@case
def test_est_goldwerte_grundtarif():
    # 51.266 € Brutto − 1.230 € AN-Pauschbetrag − 36 € Sonderausgaben-PB = 50.000 € zvE.
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "51266"})
    r = bau(sd)
    b = r["berechnung"]
    eq(b["zu_versteuerndes_einkommen"], "50000.00", "zvE 2024")
    eq(b["einkommensteuer_schaetzung"], "10872.00", "ESt 2024 bei 50.000 € zvE")
    eq(b["tarif"], "Grundtarif")

    # zweiter Stützpunkt, oberste Progressionszone (42 %)
    sd2 = steuerdaten(anlage_n={"bruttoarbeitslohn": "121266"})
    eq(bau(sd2)["berechnung"]["einkommensteuer_schaetzung"], "39763.00",
       "ESt 2024 bei 120.000 € zvE")


@case
def test_est_nutzt_den_geaenderten_2024er_tarif():
    """Der alte 2024er Tarif (GFB 11.604) hätte hier eine höhere ESt geliefert."""
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "13000"})
    r = bau(sd)
    eq(r["berechnung"]["zu_versteuerndes_einkommen"], "11734.00")
    eq(r["berechnung"]["einkommensteuer_schaetzung"], "0.00",
       "11.734 € sind 2024 nach dem geänderten GFB von 11.784 € steuerfrei")


@case
def test_pauschbetraege_2022_sind_jahresrichtig():
    sd = steuerdaten(steuerjahr=2022, anlage_n={"bruttoarbeitslohn": "40000"})
    r = bau(sd)
    eq(r["anlagen"]["N"]["arbeitnehmer_pauschbetrag"], "1200", "AN-Pauschbetrag 2022")
    eq(r["anlagen"]["KAP"]["sparer_pauschbetrag"], "801", "Sparer-Pauschbetrag 2022")


# ── Soli ─────────────────────────────────────────────────────────────────────
@case
def test_soli_milderungszone_und_voller_satz():
    # zvE 80.000 → ESt 22.963 €: Milderungszone, 11,9 % des Überhangs über 18.130 €.
    r = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "81266"}))
    eq(r["berechnung"]["einkommensteuer_schaetzung"], "22963.00")
    eq(r["berechnung"]["soli_schaetzung"], "575.13", "Milderungszone, nicht 5,5 %")
    assert dec(r["berechnung"]["soli_schaetzung"]) < dec("22963") * D("0.055")

    # zvE 120.000 → ESt 39.763 €: oberhalb der Milderungszone, voller Satz.
    r2 = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "121266"}))
    eq(r2["berechnung"]["soli_schaetzung"], "2186.97", "voller Satz 5,5 %")


@case
def test_soli_freigrenze_unterschritten():
    r = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "51266"}))
    eq(r["berechnung"]["soli_schaetzung"], "0.00", "ESt 10.872 € < Freigrenze 18.130 €")


# ── Zusammenveranlagung ──────────────────────────────────────────────────────
@case
def test_zusammenveranlagung_splitting_und_doppelte_soli_freigrenze():
    # 101.302 € − 1.230 € AN-Pauschbetrag − 72 € Sonderausgaben-PB (verdoppelt) = 100.000 €
    sd = steuerdaten(zusammenveranlagung=True, anlage_n={"bruttoarbeitslohn": "101302"})
    r = bau(sd)
    eq(r["berechnung"]["zu_versteuerndes_einkommen"], "100000.00")
    eq(r["anlagen"]["Sonderausgaben"]["pauschbetrag"], "72", "§ 10c: 72 € bei Zusammenveranlagung")
    eq(r["berechnung"]["tarif"], "Splitting")
    eq(r["berechnung"]["einkommensteuer_schaetzung"], "21744.00", "2 × ESt(50.000)")
    eq(r["meta"]["veranlagung"], "Zusammenveranlagung")
    eq(r["berechnung"]["soli_schaetzung"], "0.00",
       "Freigrenze verdoppelt sich (36.260 €) — 21.744 € liegen darunter")
    eq(r["anlagen"]["KAP"]["sparer_pauschbetrag"], "2000", "Sparer-PB verdoppelt")


# ── Kirchensteuer ────────────────────────────────────────────────────────────
@case
def test_kirchensteuersatz_9_ist_neun_prozent():
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "51266"})
    sd["steuerpflichtiger"] = {"name": "Test", "kirchensteuersatz": 9}
    r = bau(sd)
    est = dec(r["berechnung"]["einkommensteuer_schaetzung"])
    kist = dec(r["berechnung"]["kirchensteuer_schaetzung"])
    eq(kist, D("978.48"), "9 → 9 % von 10.872 €")
    assert kist < est, "KiSt darf nie größer als die ESt sein (alter Bug: ESt × 9)"
    eq(r["berechnung"]["kirchensteuersatz"], "0.09")

    # 0.09 muss identisch behandelt werden
    sd["steuerpflichtiger"]["kirchensteuersatz"] = "0.09"
    eq(dec(bau(sd)["berechnung"]["kirchensteuer_schaetzung"]), kist, "0.09 == 9")


@case
def test_kirchensteuersatz_null_und_unsinn():
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "51266"})
    sd["steuerpflichtiger"] = {"name": "Test", "kirchensteuersatz": 0}
    r = bau(sd)
    eq(r["berechnung"]["kirchensteuer_schaetzung"], None, "0 → keine Kirchensteuer")
    eq(r["berechnung"]["kirchensteuersatz"], None)

    sd["steuerpflichtiger"]["kirchensteuersatz"] = "90"
    try:
        bau(sd)
    except bt.EingabeFehler as e:
        assert "kirchensteuersatz" in str(e), e
        return
    raise AssertionError("unplausibler Kirchensteuersatz hätte auffallen müssen")


# ── Negative Einkünfte § 19 ──────────────────────────────────────────────────
@case
def test_werbungskostenueberhang_mindert_die_einkuenfte():
    sd = steuerdaten(
        anlage_n={"bruttoarbeitslohn": "30000",
                  "werbungskosten": {"fortbildung": "40000"}},
        anlage_g={"gewinn": "50000"})
    r = bau(sd)
    eq(r["anlagen"]["N"]["einkuenfte"], "-10000.00",
       "Werbungskostenüberhang wird NICHT auf 0 geklemmt")
    eq(r["berechnung"]["summe_der_einkuenfte"], "40000.00",
       "der Überhang mindert die Summe der Einkünfte")
    # zvE bleibt geklemmt
    sd2 = steuerdaten(anlage_n={"bruttoarbeitslohn": "10000",
                                "werbungskosten": {"fortbildung": "40000"}})
    eq(bau(sd2)["berechnung"]["zu_versteuerndes_einkommen"], "0.00", "zvE bleibt ≥ 0")
    eq(bau(sd2)["berechnung"]["einkommensteuer_schaetzung"], "0.00")


# ── Anlage KAP ───────────────────────────────────────────────────────────────
@case
def test_kap_abgeltungsteuer_landet_in_der_gesamtsteuer():
    sd = steuerdaten(anlage_kap={"kapitalertraege": "50000", "anrechenbare_kest": "0"})
    r = bau(sd)
    b = r["berechnung"]
    eq(b["einkommensteuer_schaetzung"], "0.00", "KAP gehört nicht in den Tarif")
    eq(r["anlagen"]["KAP"]["nach_pauschbetrag"], "49000.00", "50.000 − 1.000 Sparer-PB")
    eq(b["abgeltungsteuer_kap"], "12250.00", "25 % von 49.000 €")
    eq(b["abgeltungsteuer_kap_soli"], "673.75", "5,5 % Soli auf die Abgeltungsteuer")
    eq(b["steuer_gesamt_est"], "12250.00",
       "die Abgeltungsteuer muss in der Gesamtsteuer auftauchen (alter Bug: 0)")
    eq(r["ergebnis"]["steuer_festsetzung_gesamt"], "12923.75")


@case
def test_kap_kirchensteuer_mit_reduktionsformel():
    sd = steuerdaten(anlage_kap={"kapitalertraege": "50000"})
    sd["steuerpflichtiger"] = {"name": "Test", "kirchensteuersatz": "0.09"}
    r = bau(sd)
    # § 32d Abs. 1 Satz 4/5: (e − 4q) / (4 + k) = 49.000 / 4,09
    eq(r["berechnung"]["abgeltungsteuer_kap"], "11980.44", "ermäßigte Abgeltungsteuer")
    eq(r["berechnung"]["abgeltungsteuer_kap_kirchensteuer"], "1078.24", "9 % darauf")
    assert dec(r["berechnung"]["abgeltungsteuer_kap"]) < D("12250"), \
        "die Kirchensteuer mindert die Abgeltungsteuer (§ 32d Abs. 1 Satz 3)"


@case
def test_kap_verlusttoepfe_nach_jstg_2024():
    # Termingeschäftsverluste sind seit dem JStG 2024 unbeschränkt verrechenbar …
    # kapitalertraege ist der Saldo, der die Verluste der Zeilen 22–25 bereits
    # enthält (davon-Zeilen): 30.000 brutto − 25.000 Terminverlust = 5.000.
    sd = steuerdaten(anlage_kap={"kapitalertraege": "5000",
                                 "verlust_termingeschaefte": "25000"})
    r = bau(sd)
    k = r["anlagen"]["KAP"]
    eq(k["verlust_termingeschaefte_verrechnet"], "25000.00",
       "kein 20.000-€-Deckel und kein eigener Verrechnungskreis mehr")
    eq(k["netto_kapitalertraege"], "5000.00")
    eq(r["berechnung"]["abgeltungsteuer_kap"], "1000.00", "25 % von (5.000 − 1.000)")
    assert any("Jahressteuergesetz 2024" in h for h in r["hinweise"]), \
        "die Rechtsänderung muss als Hinweis ausgewiesen werden"

    # … Aktienverluste dagegen nur gegen Aktiengewinne (§ 20 Abs. 6 Satz 4).
    sd2 = steuerdaten(anlage_kap={"kapitalertraege": "5000", "verlust_aktien": "25000"})
    r2 = bau(sd2)
    eq(r2["anlagen"]["KAP"]["verlust_aktien_verrechnet"], "0.00", "eigener Topf bleibt")
    eq(r2["anlagen"]["KAP"]["verlustvortraege"]["aktien"], "25000.00")
    eq(r2["anlagen"]["KAP"]["netto_kapitalertraege"], "30000.00")

    sd3 = steuerdaten(anlage_kap={"kapitalertraege": "30000", "gewinn_aktien": "10000",
                                  "verlust_aktien": "25000"})
    r3 = bau(sd3)
    eq(r3["anlagen"]["KAP"]["verlust_aktien_verrechnet"], "10000.00")
    eq(r3["anlagen"]["KAP"]["verlustvortraege"]["aktien"], "15000.00")


@case
def test_kap_nettoverlust_bleibt_vorzeichenbehaftet():
    # Saldo-Lesart: 5.000 brutto − 12.000 Terminverlust = −7.000.
    sd = steuerdaten(anlage_kap={"kapitalertraege": "-7000",
                                 "verlust_termingeschaefte": "12000"})
    r = bau(sd)
    k = r["anlagen"]["KAP"]
    eq(k["verlust_termingeschaefte_verrechnet"], "5000.00")
    eq(k["verlustvortraege"]["termingeschaefte"], "7000.00",
       "der nicht verrechnete Verlust wird vorgetragen statt auf 0 geklemmt")
    eq(r["berechnung"]["abgeltungsteuer_kap"], "0.00")


# ── Nachzahlung / Erstattung ─────────────────────────────────────────────────
@case
def test_ergebnis_nachzahlung_und_erstattung():
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "51266", "lohnsteuer": "8000",
                               "soli": "0", "kirchensteuer": "0"})
    r = bau(sd)
    e = r["ergebnis"]
    eq(e["steuer_festsetzung_gesamt"], "10872.00")
    eq(e["anrechenbare_betraege"]["lohnsteuer"], "8000.00")
    eq(e["anrechenbare_betraege"]["summe"], "8000.00")
    eq(e["saldo"], "2872.00")
    eq(e["art"], "Nachzahlung")
    assert "SCHÄTZUNG" in e["hinweis"], "das Ergebnis muss klar als Schätzung markiert sein"

    # Überzahlung inkl. anrechenbarer KESt → Erstattung
    sd2 = steuerdaten(anlage_n={"bruttoarbeitslohn": "51266", "lohnsteuer": "12000"},
                      anlage_kap={"kapitalertraege": "0", "anrechenbare_kest": "500"})
    e2 = bau(sd2)["ergebnis"]
    eq(e2["anrechenbare_betraege"]["anrechenbare_kapitalertragsteuer"], "500.00")
    eq(e2["saldo"], "-1628.00")
    eq(e2["art"], "Erstattung")
    eq(e2["betrag_absolut"], "1628.00")


@case
def test_ergebnis_enthaelt_soli_und_kirchensteuer():
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "121266", "lohnsteuer": "40000",
                               "soli": "2000", "kirchensteuer": "3000"})
    sd["steuerpflichtiger"] = {"name": "Test", "kirchensteuersatz": "9"}
    r = bau(sd)
    e = r["ergebnis"]
    # 39.763 ESt + 2.186,97 Soli + 3.578,67 KiSt
    eq(e["davon_einkommensteuer"], "39763.00")
    eq(e["davon_solidaritaetszuschlag"], "2186.97")
    eq(e["davon_kirchensteuer"], "3578.67")
    eq(e["steuer_festsetzung_gesamt"], "45528.64")
    eq(e["anrechenbare_betraege"]["summe"], "45000.00")
    eq(e["saldo"], "528.64")


# ── Krypto: Aggregation mehrerer Quellen ─────────────────────────────────────
@case
def test_freigrenze_23_einmalig_ueber_alle_quellen():
    a = krypto_quelle(quelle="broker-a", netto="700.00")
    b = krypto_quelle(quelle="broker-b", netto="700.00")
    r = bau(steuerdaten(), [a, b])
    p23 = r["krypto_detail"]["paragraph_23"]
    eq(p23["netto_ergebnis_eur"], "1400.00", "Rohnettos werden summiert")
    eq(p23["steuerpflichtiger_betrag_eur"], "1400.00",
       "Freigrenze 1.000 € einmal auf die Summe — nicht je Quelle (sonst 0 €)")
    eq(r["anlagen"]["SO"]["krypto_23_steuerpflichtig"], "1400.00")
    eq(r["berechnung"]["einkuenfte_so"], "1400.00")
    eq(len(r["meta"]["krypto_quellen"]), 2)


@case
def test_freigrenze_23_unterschritten_bleibt_steuerfrei():
    a = krypto_quelle(quelle="a", netto="400.00")
    b = krypto_quelle(quelle="b", netto="300.00")
    r = bau(steuerdaten(), [a, b])
    p23 = r["krypto_detail"]["paragraph_23"]
    eq(p23["netto_ergebnis_eur"], "700.00")
    eq(p23["steuerpflichtiger_betrag_eur"], "0.00", "700 € < 1.000 € Freigrenze")


@case
def test_verlust_23_wird_zum_vortrag():
    r = bau(steuerdaten(), [krypto_quelle(netto="-2500.00")])
    p23 = r["krypto_detail"]["paragraph_23"]
    eq(p23["netto_ergebnis_eur"], "-2500.00")
    eq(p23["steuerpflichtiger_betrag_eur"], "0.00")
    eq(p23["verlustvortrag_eur"], "2500.00")
    assert any("Verlustfeststellung" in d for d in r["disclaimer"])


@case
def test_vorberechnete_quelle_wird_uebernommen_und_gewarnt():
    a = krypto_quelle(quelle="fifo-a", netto="5000.00", freigrenze_angewendet=True,
                      steuerpflichtig="5000.00")
    r1 = bau(steuerdaten(), [a])
    eq(r1["krypto_detail"]["paragraph_23"]["steuerpflichtiger_betrag_eur"], "5000.00")
    assert not r1["warnungen"], f"eine einzelne vorberechnete Quelle ist unkritisch: {r1['warnungen']}"

    b = krypto_quelle(quelle="fifo-b", netto="800.00", freigrenze_angewendet=True,
                      steuerpflichtig="0.00")
    r2 = bau(steuerdaten(), [a, b])
    assert any("Freigrenze bereits selbst angewendet" in w for w in r2["warnungen"]), \
        f"zwei vorberechnete Quellen müssen eine Warnung erzeugen: {r2['warnungen']}"


@case
def test_quellen_mit_verschiedenen_steuerjahren_werden_abgelehnt():
    a = krypto_quelle(quelle="a", jahr=2023, netto="100.00")
    b = krypto_quelle(quelle="b", jahr=2024, netto="100.00")
    try:
        bau(steuerdaten(), [a, b])
    except bt.EingabeFehler as e:
        assert "Steuerjahr" in str(e), e
        return
    raise AssertionError("widersprüchliche Steuerjahre hätten auffallen müssen")


@case
def test_warnungen_und_elster_extra_werden_zusammengefuehrt():
    a = krypto_quelle(quelle="a", netto="0.00", warnungen=["Warnung A"],
                      elster_extra=[{"anlage": "Anlage KAP", "zeile": "Z. 19",
                                     "bezeichnung": "eToro Summe", "wert": "123.45"}])
    b = krypto_quelle(quelle="b", netto="0.00", warnungen=["Warnung B"])
    r = bau(steuerdaten(), [a, b])
    assert "Warnung A" in r["warnungen"] and "Warnung B" in r["warnungen"], r["warnungen"]
    treffer = [z for z in r["elster_mapping"] if z["bezeichnung"] == "eToro Summe"]
    eq(len(treffer), 1, "durchgereichte ELSTER-Zeile fehlt")
    assert treffer[0]["quelle"], "auch durchgereichte Zeilen brauchen eine Quelle"


# ── § 22 Nr. 3: Freigrenze über Krypto UND sonstige Leistungen ───────────────
@case
def test_freigrenze_22_nr3_auf_dem_aggregat():
    sd = steuerdaten(anlage_so={"sonstige_einkuenfte": "100"})
    r = bau(sd, [krypto_quelle(p22="200.00")])
    so = r["anlagen"]["SO"]
    eq(so["leistungen_22_3_gesamt"], "300.00", "Staking 200 € + sonstige 100 €")
    eq(so["leistungen_22_3_steuerpflichtig"], "300.00",
       "256-€-Freigrenze am Aggregat geprüft — Krypto allein läge darunter")
    eq(r["berechnung"]["einkuenfte_so"], "300.00")

    # unter der Freigrenze bleibt alles steuerfrei
    r2 = bau(steuerdaten(anlage_so={"sonstige_einkuenfte": "50"}),
             [krypto_quelle(p22="100.00")])
    eq(r2["anlagen"]["SO"]["leistungen_22_3_steuerpflichtig"], "0.00")


# ── ELSTER-Mapping ───────────────────────────────────────────────────────────
@case
def test_elster_zeilen_fuer_s_und_g():
    sd = steuerdaten(anlage_s={"gewinn": "24000"}, anlage_g={"gewinn": "12000"})
    r = bau(sd)
    s = zeilen(r, "Anlage S")
    g = zeilen(r, "Anlage G")
    eq(len(s), 1, "Anlage S fehlte bisher komplett im Mapping")
    eq(len(g), 1, "Anlage G fehlte bisher komplett im Mapping")
    eq(s[0]["wert"], "24000.00")
    eq(g[0]["wert"], "12000.00")
    eq(s[0]["quelle"], "anlage_s.gewinn")
    eq(g[0]["quelle"], "anlage_g.gewinn")
    eq(r["berechnung"]["summe_der_einkuenfte"], "36000.00",
       "S und G werden auch besteuert")


@case
def test_elster_zeilen_fuer_kest_sonstige_sonderausgaben_agb_und_steuer_id():
    sd = steuerdaten(
        anlage_kap={"kapitalertraege": "5000", "anrechenbare_kest": "1200"},
        anlage_so={"sonstige_einkuenfte": "900"},
        sonderausgaben={"spenden": "500"},
        aussergewoehnliche_belastungen={"anzusetzen": "2400"})
    sd["steuerpflichtiger"] = {"name": "Test", "steuer_id": "12345678901"}
    r = bau(sd)
    bez = {z["bezeichnung"]: z for z in r["elster_mapping"]}
    assert any("Kapitalertragsteuer" in k for k in bez), "anrechenbare KESt fehlt"
    assert any("sonstige Leistungen" in k for k in bez), "sonstige Einkünfte fehlen"
    assert any("Identifikationsnummer" in k for k in bez), "Steuer-ID fehlt"
    eq(len(zeilen(r, "Anlage Sonderausgaben")), 1, "Sonderausgaben fehlen")
    eq(len(zeilen(r, "Anlage Außergewöhnliche Belastungen")), 1, "agB fehlen")
    # jede Zeile führt ihre Quelle mit
    for z in r["elster_mapping"]:
        assert set(z) >= {"anlage", "zeile", "bezeichnung", "wert", "quelle"}, z
        assert z["quelle"], f"Zeile ohne Quelle: {z}"


@case
def test_elster_caveat_steht_im_report():
    r = bau(steuerdaten())
    assert any("ELSTER ändert" in h for h in r["hinweise"]), \
        "der Zeilennummern-Vorbehalt muss im Report stehen, nicht nur im Docstring"


# ── Unbekanntes Jahr ─────────────────────────────────────────────────────────
@case
def test_unbekanntes_jahr_liefert_none_statt_falscher_zahl():
    sd = steuerdaten(steuerjahr=2019, anlage_n={"bruttoarbeitslohn": "51266"})
    r = bau(sd)
    b = r["berechnung"]
    eq(b["einkommensteuer_schaetzung"], None, "kein Tarif → None statt Fantasiewert")
    eq(b["soli_schaetzung"], None)
    eq(b["tarif_jahr_hinterlegt"], False)
    assert "hinweis_tarif" in b and "2019" in b["hinweis_tarif"], b
    eq(r["ergebnis"]["status"], "nicht berechenbar")
    assert r["warnungen"], "der Jahres-Ersatz muss als Warnung erscheinen"


# ── Eingabe-Validierung ──────────────────────────────────────────────────────
@case
def test_verschachtelte_werbungskosten_melden_das_feld():
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "40000",
                               "werbungskosten": {"fahrtkosten": {"januar": "100"}}})
    try:
        bau(sd)
    except bt.EingabeFehler as e:
        assert "anlage_n.werbungskosten.fahrtkosten" in str(e), e
        return
    raise AssertionError("verschachtelte Werbungskosten hätten auffallen müssen")


@case
def test_fehlendes_steuerjahr():
    sd = steuerdaten()
    del sd["steuerjahr"]
    try:
        bau(sd)
    except bt.EingabeFehler as e:
        assert "steuerjahr" in str(e), e
        return
    raise AssertionError("fehlendes Steuerjahr hätte auffallen müssen")


@case
def test_kinder_als_strings():
    sd = steuerdaten(kinder=["Anna", "Ben"])
    try:
        bau(sd)
    except bt.EingabeFehler as e:
        assert "kinder[0]" in str(e), e
        return
    raise AssertionError("Kinder als Strings hätten auffallen müssen")


@case
def test_krypto_quelle_ohne_pflichtfelder():
    try:
        bt.normiere_krypto_quelle({"foo": "bar"}, herkunft="kaputt.json")
    except bt.EingabeFehler as e:
        assert "paragraph_23" in str(e) and "kaputt.json" in str(e), e
    else:
        raise AssertionError("fehlendes paragraph_23 hätte auffallen müssen")

    try:
        bt.normiere_krypto_quelle([1, 2, 3], herkunft="liste.json")
    except bt.EingabeFehler as e:
        assert "liste.json" in str(e), e
        return
    raise AssertionError("Liste statt Objekt hätte auffallen müssen")


@case
def test_altformat_paragraph_22_nr_3_wird_akzeptiert():
    """Die FIFO-Engine schreibt (noch) 'paragraph_22_nr_3' und 'tax_year'."""
    alt = {"tax_year": 2024,
           "paragraph_23": {"netto_ergebnis_eur": "1500.00",
                            "steuerpflichtiger_betrag_eur": "1500.00",
                            "freigrenze_ueberschritten": True},
           "paragraph_22_nr_3": {"summe_zufluesse_eur": "300.00",
                                 "steuerpflichtig_eur": "300.00"}}
    r = bau(steuerdaten(), [alt])
    eq(r["krypto_detail"]["paragraph_23"]["steuerpflichtiger_betrag_eur"], "1500.00")
    eq(r["anlagen"]["SO"]["leistungen_22_3_gesamt"], "300.00")
    # export_report.py liest 'paragraph_22_nr_3' — der Alias muss erhalten bleiben
    assert "paragraph_22_nr_3" in r["krypto_detail"]
    assert "paragraph_22_nr3" in r["krypto_detail"]


# ── CLI ──────────────────────────────────────────────────────────────────────
@case
def test_cli_mit_mehreren_krypto_quellen():
    with tempfile.TemporaryDirectory() as tmp:
        p_sd = os.path.join(tmp, "steuerdaten.json")
        p_k1 = os.path.join(tmp, "k1.json")
        p_k2 = os.path.join(tmp, "k2.json")
        p_out = os.path.join(tmp, "taxreport.json")
        with open(p_sd, "w", encoding="utf-8") as f:
            json.dump(steuerdaten(anlage_n={"bruttoarbeitslohn": "51266",
                                            "lohnsteuer": "8000"}), f)
        with open(p_k1, "w", encoding="utf-8") as f:
            json.dump(krypto_quelle(quelle="a", netto="700.00"), f)
        with open(p_k2, "w", encoding="utf-8") as f:
            json.dump(krypto_quelle(quelle="b", netto="700.00"), f)

        rc = bt.main([p_sd, "--krypto-result", p_k1, p_k2, "-o", p_out])
        eq(rc, 0, "CLI-Rückgabecode")
        with open(p_out, encoding="utf-8") as f:
            r = json.load(f)
        eq(r["krypto_detail"]["paragraph_23"]["steuerpflichtiger_betrag_eur"], "1400.00")
        assert r["ergebnis"]["art"] in ("Nachzahlung", "Erstattung", "ausgeglichen")
        json.dumps(r)  # der Report muss vollständig serialisierbar bleiben


@case
def test_cli_meldet_fehler_statt_traceback():
    with tempfile.TemporaryDirectory() as tmp:
        p_sd = os.path.join(tmp, "steuerdaten.json")
        p_k = os.path.join(tmp, "k.json")
        with open(p_sd, "w", encoding="utf-8") as f:
            json.dump(steuerdaten(), f)
        with open(p_k, "w", encoding="utf-8") as f:
            json.dump({"foo": "bar"}, f)
        eq(bt.main([p_sd, "--krypto-result", p_k, "-o", os.path.join(tmp, "out.json")]), 2,
           "unbrauchbare Krypto-Quelle → Rückgabecode 2, kein Traceback")


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
