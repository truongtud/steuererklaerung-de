#!/usr/bin/env python3
"""Tests für die Eingabeprüfung und die Verlustvorträge.

Geprüft wird, was in einer Steuerberechnung teuer ist:
  * ein Tippfehler im Schlüssel darf nicht still zu 0,00 € werden,
  * --strict muss so einen Lauf abbrechen lassen,
  * der § 23-Verlustvortrag aus Vorjahren muss die Freigrenze in der richtigen
    Reihenfolge respektieren,
  * 'gewinn_aktien' darf nicht unbemerkt an der Bemessungsgrundlage vorbeilaufen,
  * die FIFO-Engine darf für ein unbekanntes Jahr keine Freigrenze erfinden.

Ausführen: python3 tests/test_eingabepruefung.py
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal as D

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_taxreport as bt  # noqa: E402
import krypto_fifo as kf  # noqa: E402

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


def krypto_quelle(*, netto="0.00", p22="0.00", quelle="test-broker", jahr=2024):
    """Rohquelle im Parser-Contract: Freigrenze ausdrücklich NICHT angewendet."""
    n = dec(netto)
    return {
        "steuerjahr": jahr,
        "quelle": quelle,
        "paragraph_23": {
            "gewinn_eur": str(n if n > 0 else D("0")),
            "verlust_eur": str(n if n < 0 else D("0")),
            "netto_ergebnis_eur": netto,
            "freigrenze_angewendet": False,
            "disposals": [],
        },
        "paragraph_22_nr3": {"summe_eur": p22, "freigrenze_angewendet": False},
        "warnungen": [], "elster_extra": [], "hinweise": [],
    }


def bau(sd, quellen=None):
    """build() ohne die stderr-Ausgabe der Feldprüfung im Testprotokoll."""
    with redirect_stderr(io.StringIO()):
        return bt.build(sd, quellen if quellen is not None else [])


def warnung_mit(report, *teile):
    for w in report["warnungen"]:
        if all(t in w for t in teile):
            return w
    return None


def so_zeilen(report, *teile):
    return [z for z in report["elster_mapping"]
            if all(t in z["bezeichnung"] for t in teile)]


# ─────────────────────────────────────────────────────────────────────────────
# 1 — Unbekannte Felder
# ─────────────────────────────────────────────────────────────────────────────


@case
def test_tippfehler_wird_gemeldet_und_der_wert_ignoriert():
    sd = steuerdaten(anlage_n={"brutto_arbeitslohn": "72000"})
    r = bau(sd)

    w = warnung_mit(r, "anlage_n.brutto_arbeitslohn")
    assert w, f"der Tippfehler muss gemeldet werden: {r['warnungen']}"
    eq(w, "Unbekanntes Feld 'anlage_n.brutto_arbeitslohn' — meintest du "
          "'bruttoarbeitslohn'? Der Wert wurde IGNORIERT.",
       "Wortlaut der Warnung")

    # … und der Wert darf tatsächlich nirgends angekommen sein
    eq(r["anlagen"]["N"]["bruttoarbeitslohn"], "0.00",
       "der Tippfehler-Wert darf nicht heimlich doch gelesen werden")
    eq(r["berechnung"]["zu_versteuerndes_einkommen"], "0.00")

    # strukturierter Befund für Werkzeuge
    befunde = r["eingabepruefung"]["unbekannte_felder"]
    eq(len(befunde), 1, "genau ein Befund")
    eq(befunde[0]["pfad"], "anlage_n.brutto_arbeitslohn")
    eq(befunde[0]["block"], "anlage_n")
    eq(befunde[0]["vorschlag"], "bruttoarbeitslohn")


@case
def test_warnung_geht_auch_auf_stderr_und_in_den_disclaimer():
    sd = steuerdaten(anlage_n={"brutto_arbeitslohn": "72000"})
    buf = io.StringIO()
    with redirect_stderr(buf):
        r = bt.build(sd, [])
    assert "brutto_arbeitslohn" in buf.getvalue(), \
        f"die Warnung muss auf stderr erscheinen: {buf.getvalue()!r}"
    # Der Disclaimer landet im HTML/PDF — dort muss die Warnung ankommen.
    assert any("brutto_arbeitslohn" in d for d in r["disclaimer"]), \
        "unbekannte Felder müssen bis in den Disclaimer (HTML/PDF) durchschlagen"


@case
def test_unbekannte_felder_in_jedem_block():
    faelle = [
        ("anlage_kap", {"kapitalertraege": "0", "kapital_ertraege": "5000"},
         "anlage_kap.kapital_ertraege", "kapitalertraege"),
        ("anlage_so", {"sonstige_einkuenfe": "500"},
         "anlage_so.sonstige_einkuenfe", "sonstige_einkuenfte"),
        ("anlage_v", {"einkunfte": "3000"}, "anlage_v.einkunfte", "einkuenfte"),
        ("anlage_s", {"gewin": "3000"}, "anlage_s.gewin", "gewinn"),
        ("anlage_g", {"gewinne": "3000"}, "anlage_g.gewinne", "gewinn"),
        ("steuerpflichtiger", {"name": "T", "kirchensteuer_satz": "9"},
         "steuerpflichtiger.kirchensteuer_satz", "kirchensteuersatz"),
    ]
    for block, inhalt, pfad, vorschlag in faelle:
        r = bau(steuerdaten(**{block: inhalt}))
        w = warnung_mit(r, pfad)
        assert w, f"{pfad} wurde nicht gemeldet: {r['warnungen']}"
        assert f"meintest du '{vorschlag}'" in w, f"{pfad}: falscher Vorschlag — {w}"
        assert "IGNORIERT" in w, w

    # oberste Ebene
    sd = steuerdaten()
    sd["anlage_kapp"] = {"kapitalertraege": "5000"}
    r = bau(sd)
    w = warnung_mit(r, "anlage_kapp")
    assert w and "meintest du 'anlage_kap'" in w and "oberste Ebene" in w, w


@case
def test_unbekanntes_feld_ohne_aehnlichkeit_nennt_die_bekannten_felder():
    r = bau(steuerdaten(anlage_v={"mieteinnahmen_brutto": "12000"}))
    w = warnung_mit(r, "anlage_v.mieteinnahmen_brutto")
    assert w, r["warnungen"]
    assert "kein ähnlich geschriebenes Feld bekannt" in w, w
    assert "einkuenfte" in w, "die bekannten Felder müssen genannt werden"
    assert "IGNORIERT" in w, w
    eq(r["eingabepruefung"]["unbekannte_felder"][0]["vorschlag"], None)


@case
def test_freiform_bloecke_werden_nicht_gewarnt():
    """Werbungskosten, Vorsorge und Sonderausgaben sind absichtlich frei benennbar."""
    sd = steuerdaten(
        anlage_n={"bruttoarbeitslohn": "40000",
                  "werbungskosten": {"kontofuehrung": "16", "irgendwas_exotisches": "99"}},
        vorsorge={"basisrente_ruerup": "2400", "unfallversicherung": "120"},
        sonderausgaben={"kirchensteuer_gezahlt": "900", "spende_tierheim": "50"})
    r = bau(sd)
    eq(r["eingabepruefung"]["unbekannte_felder"], [],
       f"Freiform-Positionen dürfen nicht gemeldet werden: {r['warnungen']}")
    # und sie müssen weiterhin voll wirken
    eq(r["anlagen"]["N"]["werbungskosten_geltend"], "115.00")
    eq(r["berechnung"]["abzug_vorsorge"], "2520.00")
    eq(r["berechnung"]["abzug_sonderausgaben"], "950.00")


@case
def test_saubere_eingabe_erzeugt_keine_feldwarnung():
    r = bau(steuerdaten(anlage_n={"bruttoarbeitslohn": "51266", "lohnsteuer": "8000",
                                  "soli": "0", "kirchensteuer": "0",
                                  "werbungskosten": {"fahrtkosten": "1000"}},
                        anlage_so={"sonstige_einkuenfte": "100",
                                   "verlustvortrag_23_vorjahr": "0"},
                        anlage_kap={"kapitalertraege": "500", "gewinn_aktien": "100",
                                    "verlust_aktien": "50",
                                    "verlust_termingeschaefte": "0",
                                    "anrechenbare_kest": "0",
                                    "auslaendische_quellensteuer": "0",
                                    "verlustvortrag_aktien_vorjahr": "0"}))
    eq(r["eingabepruefung"]["unbekannte_felder"], [],
       f"die dokumentierten Felder dürfen nicht gemeldet werden: {r['warnungen']}")
    eq(r["warnungen"], [], f"unerwartete Warnungen: {r['warnungen']}")


@case
def test_krypto_transaktionen_und_zusammenveranlagung_sind_bekannt():
    """Beides steht in assets/steuerdaten_vorlage.json — es darf nicht gewarnt werden."""
    sd = steuerdaten(zusammenveranlagung=True)
    sd["krypto_transaktionen"] = []
    eq(bau(sd)["eingabepruefung"]["unbekannte_felder"], [])


# ─────────────────────────────────────────────────────────────────────────────
# 2 — --strict
# ─────────────────────────────────────────────────────────────────────────────


def _cli(argv):
    """bt.main() ohne Ausgaberauschen; gibt (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = bt.main(argv)
    return rc, out.getvalue(), err.getvalue()


@case
def test_strict_beendet_mit_fehlercode_und_schreibt_den_report_trotzdem():
    with tempfile.TemporaryDirectory() as tmp:
        p_sd = os.path.join(tmp, "sd.json")
        p_out = os.path.join(tmp, "r.json")
        with open(p_sd, "w", encoding="utf-8") as f:
            json.dump(steuerdaten(anlage_n={"brutto_arbeitslohn": "72000"}), f)

        rc, _, err = _cli([p_sd, "-o", p_out])
        eq(rc, 0, "ohne --strict bleibt es bei einer Warnung")
        assert "brutto_arbeitslohn" in err, err

        rc, _, err = _cli([p_sd, "-o", p_out, "--strict"])
        assert rc != 0, "--strict muss einen Fehlercode liefern"
        eq(rc, 3, "Rückgabecode für --strict")
        assert "--strict" in err and "anlage_n.brutto_arbeitslohn" in err, err
        assert os.path.isfile(p_out), "der Report soll trotzdem geschrieben werden"


@case
def test_strict_ist_bei_sauberer_eingabe_folgenlos():
    with tempfile.TemporaryDirectory() as tmp:
        p_sd = os.path.join(tmp, "sd.json")
        p_out = os.path.join(tmp, "r.json")
        with open(p_sd, "w", encoding="utf-8") as f:
            json.dump(steuerdaten(anlage_n={"bruttoarbeitslohn": "51266"}), f)
        rc, _, _ = _cli([p_sd, "-o", p_out, "--strict"])
        eq(rc, 0, "saubere Eingabe + --strict → 0")


# ─────────────────────────────────────────────────────────────────────────────
# 3 — § 23-Verlustvortrag aus Vorjahren (§ 23 Abs. 3 Satz 8 EStG)
# ─────────────────────────────────────────────────────────────────────────────


@case
def test_verlustvortrag_23_mindert_den_gewinn():
    sd = steuerdaten(anlage_so={"verlustvortrag_23_vorjahr": "3000"})
    r = bau(sd, [krypto_quelle(netto="5000.00")])
    so = r["anlagen"]["SO"]
    eq(so["krypto_23_vor_verlustvortrag"], "5000.00", "Jahresergebnis vor dem Vortrag")
    eq(so["verlustvortrag_23_vorjahr"], "3000.00")
    eq(so["verlustvortrag_23_verbraucht"], "3000.00")
    eq(so["verlustvortrag_23_rest"], "0.00")
    eq(so["krypto_23_steuerpflichtig"], "2000.00", "5.000 − 3.000")
    eq(so["einkuenfte_gesamt"], "2000.00", "der Vortrag muss die Einkünfte mindern")
    eq(r["berechnung"]["summe_der_einkuenfte"], "2000.00")


@case
def test_verlustvortrag_23_groesser_als_der_gewinn_wird_weitergetragen():
    sd = steuerdaten(anlage_so={"verlustvortrag_23_vorjahr": "8000"})
    r = bau(sd, [krypto_quelle(netto="5000.00")])
    so = r["anlagen"]["SO"]
    eq(so["verlustvortrag_23_verbraucht"], "5000.00", "nur bis zur Höhe des Gewinns")
    eq(so["krypto_23_steuerpflichtig"], "0.00",
       "der Vortrag darf kein negatives Ergebnis erzeugen")
    eq(so["verlustvortrag_23_rest"], "3000.00", "der Rest wird erneut vorgetragen")
    eq(so["verlustvortrag_23_neu_gesamt"], "3000.00")
    assert any("Verbleibender § 23-Verlustvortrag" in d for d in r["disclaimer"]), \
        r["disclaimer"]


@case
def test_jahr_unter_der_freigrenze_verbraucht_keinen_vortrag():
    """Die Freigrenze wird auf den EIGENEN Jahressaldo geprüft — vor dem Vortrag.

    900 € sind 2024 ohnehin steuerfrei (Freigrenze 1.000 €); der festgestellte
    Vortrag darf dort nichts verlieren.
    """
    sd = steuerdaten(anlage_so={"verlustvortrag_23_vorjahr": "3000"})
    r = bau(sd, [krypto_quelle(netto="900.00")])
    so = r["anlagen"]["SO"]
    eq(so["krypto_23_vor_verlustvortrag"], "0.00", "900 € < Freigrenze 1.000 €")
    eq(so["verlustvortrag_23_verbraucht"], "0.00",
       "ein steuerfreies Jahr darf keinen Vortrag verbrauchen")
    eq(so["verlustvortrag_23_rest"], "3000.00")
    eq(so["verlustvortrag_23_neu_gesamt"], "3000.00")
    eq(so["krypto_23_steuerpflichtig"], "0.00")
    assert any("NICHT verbraucht" in h for h in r["hinweise"]), r["hinweise"]

    # Gegenprobe: ein Euro mehr reißt die Freigrenze und verbraucht den Vortrag.
    r2 = bau(steuerdaten(anlage_so={"verlustvortrag_23_vorjahr": "3000"}),
             [krypto_quelle(netto="1000.00")])
    so2 = r2["anlagen"]["SO"]
    eq(so2["krypto_23_vor_verlustvortrag"], "1000.00")
    eq(so2["verlustvortrag_23_verbraucht"], "1000.00")
    eq(so2["verlustvortrag_23_rest"], "2000.00")


@case
def test_verlustjahr_addiert_sich_auf_den_alten_vortrag():
    sd = steuerdaten(anlage_so={"verlustvortrag_23_vorjahr": "3000"})
    r = bau(sd, [krypto_quelle(netto="-2000.00")])
    so = r["anlagen"]["SO"]
    eq(so["verlustvortrag_23_verbraucht"], "0.00")
    eq(so["krypto_23_verlustvortrag"], "2000.00", "Verlust des Jahres")
    eq(so["verlustvortrag_23_rest"], "3000.00", "unverbrauchter Altvortrag")
    eq(so["verlustvortrag_23_neu_gesamt"], "5000.00", "3.000 + 2.000 für das Folgejahr")
    assert any("Folgejahr" in d and "5.000,00" in d for d in r["disclaimer"]), \
        r["disclaimer"]


@case
def test_verlustvortrag_23_erscheint_im_elster_mapping():
    sd = steuerdaten(anlage_so={"verlustvortrag_23_vorjahr": "3000"})
    r = bau(sd, [krypto_quelle(netto="5000.00")])
    verrechnet = so_zeilen(r, "verrechnet")
    eq(len(verrechnet), 1, f"ELSTER-Zeile für den Verbrauch fehlt: {r['elster_mapping']}")
    eq(verrechnet[0]["wert"], "3000.00")
    eq(verrechnet[0]["anlage"], "Anlage SO")
    eq(verrechnet[0]["quelle"], "anlage_so.verlustvortrag_23_vorjahr")
    rest = so_zeilen(r, "verbleibender Verlustvortrag § 23")
    eq(len(rest), 1, "ELSTER-Zeile für den Rest fehlt")
    eq(rest[0]["wert"], "0.00")
    for z in r["elster_mapping"]:
        assert z["quelle"], f"Zeile ohne Quelle: {z}"


@case
def test_verlustvortrag_23_ohne_angabe_aendert_nichts():
    ohne = bau(steuerdaten(), [krypto_quelle(netto="5000.00")])
    mit_null = bau(steuerdaten(anlage_so={"verlustvortrag_23_vorjahr": "0"}),
                   [krypto_quelle(netto="5000.00")])
    eq(ohne["anlagen"]["SO"]["krypto_23_steuerpflichtig"], "5000.00")
    eq(mit_null["anlagen"]["SO"]["krypto_23_steuerpflichtig"], "5000.00")
    eq(so_zeilen(ohne, "Verlustvortrag § 23 aus Vorjahren"), [],
       "ohne Vortrag darf keine ELSTER-Zeile entstehen")


@case
def test_verlustvortrag_23_unlesbar_meldet_das_feld():
    sd = steuerdaten(anlage_so={"verlustvortrag_23_vorjahr": "dreitausend"})
    try:
        bau(sd, [krypto_quelle(netto="5000.00")])
    except bt.EingabeFehler as e:
        assert "anlage_so.verlustvortrag_23_vorjahr" in str(e), e
        return
    raise AssertionError("ein unlesbarer Vortrag hätte auffallen müssen")


# ─────────────────────────────────────────────────────────────────────────────
# 4 — anlage_kap.gewinn_aktien / Aktien-Verlustvortrag
# ─────────────────────────────────────────────────────────────────────────────


@case
def test_gewinn_aktien_groesser_als_kapitalertraege_warnt():
    sd = steuerdaten(anlage_kap={"kapitalertraege": "1000", "gewinn_aktien": "20000",
                                 "verlust_aktien": "5000"})
    r = bau(sd)
    w = warnung_mit(r, "gewinn_aktien")
    assert w, f"die Falle muss gemeldet werden: {r['warnungen']}"
    assert "kapitalertraege" in w and "enthalten sein" in w, w
    assert "unversteuert" in w, "die Folge muss benannt werden"
    assert any("gewinn_aktien" in d for d in r["disclaimer"]), \
        "die Warnung muss bis in den Disclaimer durchschlagen"


@case
def test_gewinn_aktien_innerhalb_der_kapitalertraege_warnt_nicht():
    r = bau(steuerdaten(anlage_kap={"kapitalertraege": "30000", "gewinn_aktien": "10000",
                                    "verlust_aktien": "25000"}))
    assert warnung_mit(r, "gewinn_aktien") is None, r["warnungen"]
    # unveränderte Verrechnung (Regressionsschutz)
    eq(r["anlagen"]["KAP"]["verlust_aktien_verrechnet"], "10000.00")
    eq(r["anlagen"]["KAP"]["verlustvortraege"]["aktien"], "15000.00")

    # Gleichstand ist zulässig (der Aktiengewinn ist der einzige Kapitalertrag)
    r2 = bau(steuerdaten(anlage_kap={"kapitalertraege": "10000", "gewinn_aktien": "10000"}))
    assert warnung_mit(r2, "gewinn_aktien") is None, r2["warnungen"]


@case
def test_aktien_verlustvortrag_vorjahr():
    sd = steuerdaten(anlage_kap={"kapitalertraege": "12000", "gewinn_aktien": "8000",
                                 "verlustvortrag_aktien_vorjahr": "5000"})
    r = bau(sd)
    k = r["anlagen"]["KAP"]
    eq(k["verlustvortrag_aktien_verbraucht"], "5000.00", "nur gegen Aktiengewinne")
    eq(k["verlustvortrag_aktien_rest"], "0.00")
    eq(k["netto_kapitalertraege"], "7000.00", "12.000 − 5.000")
    eq(r["berechnung"]["abgeltungsteuer_kap"], "1500.00", "25 % von (7.000 − 1.000)")

    # Vortrag größer als der Aktiengewinn: Rest bleibt im Aktientopf
    sd2 = steuerdaten(anlage_kap={"kapitalertraege": "12000", "gewinn_aktien": "3000",
                                  "verlustvortrag_aktien_vorjahr": "5000"})
    k2 = bau(sd2)["anlagen"]["KAP"]
    eq(k2["verlustvortrag_aktien_verbraucht"], "3000.00")
    eq(k2["verlustvortrag_aktien_rest"], "2000.00")
    eq(k2["verlustvortraege"]["aktien"], "2000.00",
       "der Rest bleibt Aktien-Verlustvortrag, nicht allgemeiner Vortrag")
    eq(k2["netto_kapitalertraege"], "9000.00")

    # ohne Aktiengewinn wird nichts verbraucht
    k3 = bau(steuerdaten(anlage_kap={"kapitalertraege": "12000",
                                     "verlustvortrag_aktien_vorjahr": "5000"}))["anlagen"]["KAP"]
    eq(k3["verlustvortrag_aktien_verbraucht"], "0.00")
    eq(k3["netto_kapitalertraege"], "12000.00")
    eq(k3["verlustvortraege"]["aktien"], "5000.00")


# ─────────────────────────────────────────────────────────────────────────────
# 5 — Vorsorge-Disclaimer
# ─────────────────────────────────────────────────────────────────────────────


@case
def test_disclaimer_nennt_die_fehlende_hoechstbetragsberechnung():
    r = bau(steuerdaten(vorsorge={"krankenversicherung": "4000"}))
    treffer = [d for d in r["disclaimer"] if "§ 10 Abs. 3" in d]
    eq(len(treffer), 1, f"Vorsorge-Vorbehalt fehlt im Disclaimer: {r['disclaimer']}")
    d = treffer[0]
    assert "Vorsorgeaufwendungen" in d, d
    assert "ZU NIEDRIG" in d, "die Richtung des Fehlers muss klar sein (Steuer zu niedrig)"
    # der Abzug erfolgt tatsächlich ungedeckelt — genau darauf zielt der Vorbehalt
    eq(r["berechnung"]["abzug_vorsorge"], "4000.00")


# ─────────────────────────────────────────────────────────────────────────────
# 6 — FIFO-Engine: kein hartkodierter Freigrenzen-Ersatz mehr
# ─────────────────────────────────────────────────────────────────────────────


TX_2018 = [
    {"timestamp": "2018-01-01", "type": "buy", "asset": "BTC",
     "amount": "1", "eur_value": "1000.00"},
    {"timestamp": "2018-06-01", "type": "sell", "asset": "BTC",
     "amount": "1", "eur_value": "1700.00"},
]


@case
def test_fifo_unbekanntes_jahr_wendet_keine_freigrenze_an():
    res = kf.compute_crypto_tax(TX_2018, 2018)
    p = res["paragraph_23"]
    eq(p["freigrenze_angewendet"], False,
       "für ein nicht hinterlegtes Jahr darf keine Freigrenze erfunden werden")
    eq(p["freigrenze_eur"], None)
    eq(p["freigrenze_ueberschritten"], None)
    eq(p["netto_ergebnis_eur"], "700.00", "das Rohergebnis wird trotzdem ausgewiesen")
    eq(p["steuerpflichtiger_betrag_eur"], "700.00", "roh, ohne Freigrenzenprüfung")

    w = [x for x in res["warnungen"] if "Freigrenze § 23 für 2018" in x]
    eq(len(w), 1, f"die fehlende Freigrenze muss gewarnt werden: {res['warnungen']}")
    assert "steuerwerte.json" in w[0] and "freigrenze_23" in w[0], w[0]
    assert "600" not in w[0] and "1.000" not in w[0], \
        f"es darf kein Ersatzwert mehr genannt werden: {w[0]}"


@case
def test_fifo_hinterlegtes_jahr_bleibt_unveraendert():
    res = kf.compute_crypto_tax(
        [{"timestamp": "2024-01-01", "type": "buy", "asset": "BTC",
          "amount": "1", "eur_value": "1000.00"},
         {"timestamp": "2024-06-01", "type": "sell", "asset": "BTC",
          "amount": "1", "eur_value": "1700.00"}], 2024)
    p = res["paragraph_23"]
    eq(p["freigrenze_angewendet"], True)
    eq(p["freigrenze_eur"], "1000")
    eq(p["freigrenze_ueberschritten"], False, "700 € < 1.000 €")
    eq(p["steuerpflichtiger_betrag_eur"], "0.00")
    assert not [x for x in res["warnungen"] if "Freigrenze" in x], res["warnungen"]


@case
def test_builder_kann_die_rohquelle_des_unbekannten_jahres_aggregieren():
    """Der Report muss auch dann noch gebaut werden — genau dafür bricht die
    FIFO-Engine nicht ab, sondern liefert das rohe Netto."""
    res = kf.compute_crypto_tax(TX_2018, 2018)
    r = bau(steuerdaten(steuerjahr=2018), [res])
    p23 = r["krypto_detail"]["paragraph_23"]
    eq(p23["netto_ergebnis_eur"], "700.00")
    eq(p23["freigrenze_angewendet"], True,
       "der Builder wendet die Freigrenze selbst an (Ersatzjahr)")
    eq(p23["steuerpflichtiger_betrag_eur"], "700.00",
       "Ersatzwerte 2022: Freigrenze 600 € → 700 € steuerpflichtig")
    assert any("Freigrenze § 23 für 2018" in w for w in r["warnungen"]), r["warnungen"]
    assert any("kein § 32a-Tarif" in w for w in r["warnungen"]), r["warnungen"]
    json.dumps(r)  # muss serialisierbar bleiben


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
