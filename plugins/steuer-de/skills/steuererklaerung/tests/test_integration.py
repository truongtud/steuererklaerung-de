#!/usr/bin/env python3
"""End-to-End-Integrationstests: die echten CLIs, hintereinander, in einem Temp-Verzeichnis.

Die Unit-Tests prüfen jedes Skript für sich. Diese Datei prüft, was nur beim
Zusammenstecken auffällt:

  1. Die volle Kette steuerdaten.json (+ transactions) → build_taxreport.py →
     export_report.py, inklusive der Frage, ob der Report *in sich* stimmt
     (Summe der Einkünfte, zvE, § 32a-Tarif, Nachzahlung).
  2. Der Schlüssel-Kontrakt zwischen den Modulen: liest der Konsument genau die
     Schlüssel, die der Produzent schreibt? Die Doppelnamen
     (`paragraph_22_nr3`/`paragraph_22_nr_3`, `veraeusserungen`/`disposals`,
     `steuerjahr`/`tax_year`) müssen synchron bleiben — sonst liest ein späterer
     Konsument still eine 0.
  3. Die § 23-Freigrenze über *mehrere* Quellen: zwei knapp unterschwellige
     Broker-Ergebnisse zusammen sind steuerpflichtig.
  4. Die ursprünglichen Defekte (Vorjahresgewinn, Jahrestag, negative Menge,
     CSV-Format) — hier end-to-end, nicht je Modul.
  5. Robustheit: kaputte Eingaben brechen mit deutscher Meldung ab, nicht mit
     einem Traceback.

Ausführen: python3 tests/test_integration.py   (oder tests/run_tests.py)
"""
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import steuerlib as sl  # noqa: E402

BUILD = os.path.join(SCRIPTS, "build_taxreport.py")
EXPORT = os.path.join(SCRIPTS, "export_report.py")
FIFO = os.path.join(SCRIPTS, "krypto_fifo.py")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def dec(x):
    return sl.to_decimal(x)


def run(*args, erwarte_rc=0):
    """Ruft ein Skript als echtes Kommando auf (kein Import — das ist der Sinn)."""
    p = subprocess.run([sys.executable, *[str(a) for a in args]],
                       capture_output=True, text=True, cwd=ROOT)
    if erwarte_rc is not None:
        assert p.returncode == erwarte_rc, (
            f"{args[0]} → rc={p.returncode} (erwartet {erwarte_rc})\n"
            f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}")
    return p


def schreibe(pfad, obj):
    with open(pfad, "w", encoding="utf-8") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    return pfad


def lies(pfad):
    with open(pfad, encoding="utf-8") as f:
        return json.load(f)


def hat_fpdf() -> bool:
    try:
        import fpdf  # noqa: F401
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Testdaten — ein Fall, der jede Anlage und jeden Krypto-Sonderfall berührt
# ─────────────────────────────────────────────────────────────────────────────

STEUERDATEN = {
    "steuerjahr": 2024,
    "steuerpflichtiger": {"name": "Ayşe Öztürk", "verheiratet": False,
                          "steuer_id": "12345678901", "kirchensteuersatz": "9"},
    "anlage_n": {"bruttoarbeitslohn": "72000.00", "lohnsteuer": "12000.00",
                 "soli": "300.00", "kirchensteuer": "900.00",
                 "werbungskosten": {"fahrtkosten": "2000.00"}},
    "anlage_kap": {"kapitalertraege": "5000.00", "anrechenbare_kest": "800.00"},
    "anlage_so": {"sonstige_einkuenfte": "100.00"},
    "anlage_v": {"einkuenfte": "3000.00"},
    "anlage_s": {"gewinn": "5000.00"},
    "anlage_g": {"gewinn": "2000.00"},
    "vorsorge": {"krankenversicherung": "4000.00", "rentenversicherung": "3000.00"},
    "sonderausgaben": {"spenden": "500.00"},
    "aussergewoehnliche_belastungen": {},
    "kinder": [{"name": "Kind Eins", "geburtsdatum": "2015-04-02"}],
}

TRANSAKTIONEN = [
    # BTC: ein Los aus 2022 (steuerfrei) und eines aus 2024 (steuerpflichtig),
    # ein Verkauf verbraucht beide → Teil-Lose mit anteiliger Gebühr.
    {"timestamp": "2022-06-01", "type": "buy", "asset": "BTC",
     "amount": "1.0", "eur_value": "20000.00", "fee_eur": "100.00"},
    {"timestamp": "2024-01-10", "type": "buy", "asset": "BTC",
     "amount": "1.0", "eur_value": "30000.00", "fee_eur": "0"},
    {"timestamp": "2024-06-01", "type": "sell", "asset": "BTC",
     "amount": "1.5", "eur_value": "60000.00", "fee_eur": "150.00"},
    # ETH: Gewinn im VORJAHR — darf 2024 nicht auftauchen.
    {"timestamp": "2023-01-05", "type": "buy", "asset": "ETH",
     "amount": "10", "eur_value": "10000.00"},
    {"timestamp": "2023-06-05", "type": "sell", "asset": "ETH",
     "amount": "5", "eur_value": "9000.00"},
    # LTC: Verkauf exakt am Jahrestag → noch steuerpflichtig (§ 188 Abs. 2 BGB).
    {"timestamp": "2023-05-20", "type": "buy", "asset": "LTC",
     "amount": "100", "eur_value": "1000.00"},
    {"timestamp": "2024-05-20", "type": "sell", "asset": "LTC",
     "amount": "100", "eur_value": "1500.00"},
    # XRP: negative Menge im Export (abgegebene Seite) → muss trotzdem zählen.
    {"timestamp": "2024-02-01", "type": "buy", "asset": "XRP",
     "amount": "100", "eur_value": "100.00"},
    {"timestamp": "2024-03-01", "type": "sell", "asset": "XRP",
     "amount": "-100", "eur_value": "150.00"},
    # Staking → § 22 Nr. 3
    {"timestamp": "2024-04-01", "type": "reward", "asset": "SOL",
     "amount": "1", "eur_value": "200.00", "reward_kind": "staking"},
]


def pipeline(tmp, steuerdaten=None, transaktionen=None, formats=("html", "elster")):
    """Baut den Report über die echten CLIs und exportiert ihn. Gibt (report, outdir)."""
    sd = schreibe(os.path.join(tmp, "steuerdaten.json"),
                  STEUERDATEN if steuerdaten is None else steuerdaten)
    tx = schreibe(os.path.join(tmp, "t.json"),
                  TRANSAKTIONEN if transaktionen is None else transaktionen)
    out = os.path.join(tmp, "r.json")
    run(BUILD, sd, "--transactions", tx, "-o", out)
    outdir = os.path.join(tmp, "out")
    fmts = list(formats)
    if "pdf" in fmts and not hat_fpdf():
        fmts.remove("pdf")
    run(EXPORT, out, "--outdir", outdir, "--formats", *fmts)
    return lies(out), outdir


# ─────────────────────────────────────────────────────────────────────────────
# 1 — Volle Kette und innere Widerspruchsfreiheit des Reports
# ─────────────────────────────────────────────────────────────────────────────


@case
def test_pipeline_transactions_bis_export():
    with tempfile.TemporaryDirectory() as tmp:
        formats = ["html", "elster"] + (["pdf"] if hat_fpdf() else [])
        r, outdir = pipeline(tmp, formats=formats)

        dateien = sorted(os.listdir(outdir))
        assert "taxreport_2024.html" in dateien, dateien
        assert "elster_mapping_2024.csv" in dateien, dateien
        assert "elster_mapping_2024.json" in dateien, dateien
        if hat_fpdf():
            assert "taxreport_2024.pdf" in dateien, dateien
            assert os.path.getsize(os.path.join(outdir, "taxreport_2024.pdf")) > 1000
        else:
            print("    (fpdf2 nicht installiert — PDF-Format übersprungen)")

        html = open(os.path.join(outdir, "taxreport_2024.html"), encoding="utf-8").read()
        assert "KEINE Steuerberatung" in html, "Disclaimer fehlt im HTML"
        assert "Ayşe Öztürk" in html, "Name (Unicode) fehlt im HTML"
        # Der HTML-Export liest paragraph_22_nr_3 — der Wert muss dort ankommen.
        assert "Staking § 22 Nr. 3" in html


@case
def test_report_ist_in_sich_konsistent():
    """Der Report muss zu seinen eigenen Zahlen passen — sonst ist irgendwo
    zwischen den Modulen eine Größe verloren gegangen oder doppelt gezählt."""
    with tempfile.TemporaryDirectory() as tmp:
        r, _ = pipeline(tmp)
        a, b, erg = r["anlagen"], r["berechnung"], r["ergebnis"]

        # (a) Summe der Einkünfte = Summe der Anlagen OHNE KAP (§ 32d, eigener Tarif)
        teil = (dec(a["N"]["einkuenfte"]) + dec(a["SO"]["einkuenfte_gesamt"])
                + dec(a["V"]["einkuenfte"]) + dec(a["S"]["gewinn"]) + dec(a["G"]["gewinn"]))
        eq(dec(b["summe_der_einkuenfte"]), teil, "Summe der Einkünfte ≠ Summe der Anlagen")
        assert dec(a["KAP"]["kapitalertraege"]) != 0, "Testfall ohne KAP prüft die Regel nicht"

        # (b) zvE = Summe der Einkünfte − die im Report ausgewiesenen Abzüge
        eq(dec(b["zu_versteuerndes_einkommen"]),
           dec(b["summe_der_einkuenfte"]) - dec(b["abzug_vorsorge"])
           - dec(b["abzug_sonderausgaben"]) - dec(b["abzug_agb"]),
           "zvE ≠ Summe der Einkünfte − Abzüge")

        # (c) ESt = § 32a-Tarif auf genau dieses zvE
        zve = dec(b["zu_versteuerndes_einkommen"])
        eq(dec(b["einkommensteuer_schaetzung"]),
           sl.q2(sl.est_tarif(zve, 2024, r["meta"]["veranlagung"] == "Zusammenveranlagung")),
           "ESt passt nicht zum Tarif")
        eq(dec(b["soli_schaetzung"]), sl.soli(dec(b["einkommensteuer_schaetzung"]), 2024),
           "Soli passt nicht zu § 4 SolZG")
        eq(dec(b["kirchensteuer_schaetzung"]),
           sl.q2(dec(b["einkommensteuer_schaetzung"]) * dec(b["kirchensteuersatz"])),
           "KiSt passt nicht zum Satz")
        eq(dec(b["kirchensteuersatz"]), D("0.09"),
           "'9' muss als 9 % gelesen werden, nicht als Faktor 9")

        # (d) Gesamtsteuer = Tarif + Abgeltungsteuer
        eq(dec(b["steuer_gesamt_est"]),
           dec(b["einkommensteuer_schaetzung"]) + dec(b["abgeltungsteuer_kap"]))
        eq(dec(b["steuer_gesamt_soli"]),
           dec(b["soli_schaetzung"]) + dec(b["abgeltungsteuer_kap_soli"]))
        eq(dec(b["steuer_gesamt_kirchensteuer"]),
           dec(b["kirchensteuer_schaetzung"]) + dec(b["abgeltungsteuer_kap_kirchensteuer"]))

        # (e) Festsetzung und Saldo
        eq(dec(erg["steuer_festsetzung_gesamt"]),
           dec(b["steuer_gesamt_est"]) + dec(b["steuer_gesamt_soli"])
           + dec(b["steuer_gesamt_kirchensteuer"]), "Festsetzung ≠ Summe der Steuerarten")
        anr = erg["anrechenbare_betraege"]
        eq(dec(anr["summe"]),
           dec(anr["lohnsteuer"]) + dec(anr["solidaritaetszuschlag_einbehalten"])
           + dec(anr["kirchensteuer_einbehalten"]) + dec(anr["anrechenbare_kapitalertragsteuer"]),
           "Anrechnungssumme stimmt nicht")
        eq(dec(erg["saldo"]),
           dec(erg["steuer_festsetzung_gesamt"]) - dec(anr["summe"]),
           "Saldo ≠ Festsetzung − Anrechnung")
        eq(erg["art"], "Nachzahlung" if dec(erg["saldo"]) > 0 else "Erstattung")
        eq(dec(erg["betrag_absolut"]), abs(dec(erg["saldo"])))

        # (f) Anlage SO = § 23 steuerpflichtig + § 22 Nr. 3 steuerpflichtig
        eq(dec(a["SO"]["einkuenfte_gesamt"]),
           dec(a["SO"]["krypto_23_steuerpflichtig"])
           + dec(a["SO"]["leistungen_22_3_steuerpflichtig"]),
           "Anlage SO ist nicht die Summe ihrer Teile")

        # (g) Anlage N: Werbungskosten mindern, Pauschbetrag greift als Untergrenze
        eq(dec(a["N"]["einkuenfte"]),
           dec(a["N"]["bruttoarbeitslohn"]) - dec(a["N"]["werbungskosten_angesetzt"]))
        eq(dec(a["N"]["werbungskosten_angesetzt"]),
           max(dec(a["N"]["werbungskosten_geltend"]), dec(a["N"]["arbeitnehmer_pauschbetrag"])))


@case
def test_goldwerte_der_kette():
    """Feste Beträge — damit ein Vorzeichen-/Reihenfolgefehler nicht nur
    'konsistent falsch' durchrutscht. Herleitung siehe Kommentare."""
    with tempfile.TemporaryDirectory() as tmp:
        r, _ = pipeline(tmp)
        a, b = r["anlagen"], r["berechnung"]
        p23 = r["krypto_detail"]["paragraph_23"]

        # BTC-Los 2024: 20.000 Erlös − 15.000 AK − 50 Gebühr = 4.950
        # LTC am Jahrestag: 1.500 − 1.000 = 500 | XRP: 150 − 100 = 50
        eq(p23["netto_ergebnis_eur"], "5500.00", "§ 23 netto 2024")
        eq(p23["steuerpflichtiger_betrag_eur"], "5500.00")
        # BTC-Los 2022: 40.000 − 20.100 − 100 = 19.800, > 1 Jahr gehalten
        eq(p23["summe_steuerfrei_gt_1_jahr_eur"], "19800.00", "steuerfreier Teil")
        # 200 € Staking + 100 € sonstige Leistungen = 300 ≥ 256 → voll steuerpflichtig
        eq(a["SO"]["leistungen_22_3_gesamt"], "300.00")
        eq(a["SO"]["leistungen_22_3_steuerpflichtig"], "300.00")
        eq(a["SO"]["einkuenfte_gesamt"], "5800.00")
        # 72.000 − 2.000 WK = 70.000 | + 5.800 SO + 3.000 V + 5.000 S + 2.000 G
        eq(b["summe_der_einkuenfte"], "85800.00")
        # − 7.000 Vorsorge − 500 Spenden
        eq(b["zu_versteuerndes_einkommen"], "78300.00")
        # § 32a Abs. 1 Nr. 4: 0,42 × 78.300 − 10.636,31 = 22.249,69 → abgerundet
        eq(b["einkommensteuer_schaetzung"], "22249.00")
        # § 4 SolZG Milderungszone: 11,9 % × (22.249 − 18.130) = 490,161
        eq(b["soli_schaetzung"], "490.16")


# ─────────────────────────────────────────────────────────────────────────────
# 2 — Schlüssel-Kontrakt zwischen den Modulen
# ─────────────────────────────────────────────────────────────────────────────

def _pfad(obj, pfad):
    """('a','b') → obj['a']['b']; (fehlt, Fehler) → sentinel FEHLT."""
    cur = obj
    for k in pfad:
        if not isinstance(cur, dict) or k not in cur:
            return FEHLT
        cur = cur[k]
    return cur


FEHLT = object()


def pruefe_kontrakt(obj, erwartungen, wer):
    """erwartungen: Liste von (Beschreibung, [Pfad, ...]) — mindestens einer muss da sein."""
    fehlend = []
    for beschreibung, pfade in erwartungen:
        if not any(_pfad(obj, p) is not FEHLT for p in pfade):
            fehlend.append(f"{beschreibung} (gesucht: "
                           + " | ".join(".".join(p) for p in pfade) + ")")
    assert not fehlend, (
        f"{wer} liest Schlüssel, die der Produzent nicht (mehr) schreibt:\n  "
        + "\n  ".join(fehlend))


# Was build_taxreport.normiere_krypto_quelle() aus einem Krypto-Ergebnis liest.
BUILD_LIEST_AUS_KRYPTO = [
    ("Steuerjahr", [("steuerjahr",), ("tax_year",)]),
    ("Quellenname", [("quelle",), ("source",)]),
    ("§ 23-Block", [("paragraph_23",)]),
    ("§ 22 Nr. 3-Block", [("paragraph_22_nr3",), ("paragraph_22_nr_3",)]),
    ("§ 23 Netto", [("paragraph_23", "netto_ergebnis_eur")]),
    ("§ 23 Gewinne", [("paragraph_23", "gewinn_eur"),
                      ("paragraph_23", "summe_steuerpflichtige_gewinne_eur")]),
    ("§ 23 Verluste", [("paragraph_23", "verlust_eur"),
                       ("paragraph_23", "summe_verluste_eur")]),
    ("§ 23 steuerpflichtiger Betrag", [("paragraph_23", "steuerpflichtiger_betrag_eur")]),
    ("§ 23 Verlustvortrag", [("paragraph_23", "verlustvortrag_eur")]),
    ("§ 23 Freigrenze-Flag", [("paragraph_23", "freigrenze_angewendet")]),
    ("§ 23 Einzelveräußerungen", [("paragraph_23", "disposals")]),
    ("steuerfrei > 1 Jahr", [("steuerfrei_langfristig_eur",),
                             ("paragraph_23", "summe_steuerfrei_gt_1_jahr_eur"),
                             ("paragraph_23", "steuerfrei_langfristig_eur")]),
    ("§ 22 Summe", [("paragraph_22_nr3", "summe_eur"),
                    ("paragraph_22_nr3", "summe_zufluesse_eur")]),
    ("§ 22 steuerpflichtig", [("paragraph_22_nr3", "steuerpflichtig_eur")]),
    ("§ 22 Freigrenze-Flag", [("paragraph_22_nr3", "freigrenze_angewendet")]),
    ("§ 22 Erträge", [("paragraph_22_nr3", "ertraege")]),
    ("Warnungen", [("warnungen",)]),
    ("ELSTER-Zusatzzeilen", [("elster_extra",)]),
    ("Hinweise", [("hinweise",)]),
]

# Was export_report.py aus einem TaxReport liest.
EXPORT_LIEST_AUS_REPORT = [
    ("Steuerjahr", [("meta", "steuerjahr")]),
    ("Veranlagungsart", [("meta", "veranlagung")]),
    ("Erstellzeitpunkt", [("meta", "erstellt")]),
    ("Name", [("meta", "steuerpflichtiger", "name")]),
    ("Anlage N Einkünfte", [("anlagen", "N", "einkuenfte")]),
    ("Anlage KAP Kapitalerträge", [("anlagen", "KAP", "kapitalertraege")]),
    ("Anlage SO Einkünfte", [("anlagen", "SO", "einkuenfte_gesamt")]),
    ("Anlage SO § 23 steuerpflichtig", [("anlagen", "SO", "krypto_23_steuerpflichtig")]),
    ("Anlage V", [("anlagen", "V", "einkuenfte")]),
    ("Anlage S", [("anlagen", "S", "gewinn")]),
    ("Anlage G", [("anlagen", "G", "gewinn")]),
    ("zvE", [("berechnung", "zu_versteuerndes_einkommen")]),
    ("ESt-Schätzung", [("berechnung", "einkommensteuer_schaetzung")]),
    ("Tarifbezeichnung", [("berechnung", "tarif")]),
    ("Soli-Schätzung", [("berechnung", "soli_schaetzung")]),
    ("KiSt-Schätzung", [("berechnung", "kirchensteuer_schaetzung")]),
    ("Summe der Einkünfte", [("berechnung", "summe_der_einkuenfte")]),
    ("§ 23-Block (Unterstrich-Schreibweise!)", [("krypto_detail", "paragraph_23")]),
    ("§ 23 Netto", [("krypto_detail", "paragraph_23", "netto_ergebnis_eur")]),
    ("§ 23 Freigrenze", [("krypto_detail", "paragraph_23", "freigrenze_eur")]),
    ("§ 23 Freigrenze überschritten",
     [("krypto_detail", "paragraph_23", "freigrenze_ueberschritten")]),
    ("§ 23 steuerpflichtig",
     [("krypto_detail", "paragraph_23", "steuerpflichtiger_betrag_eur")]),
    ("§ 23 steuerfrei",
     [("krypto_detail", "paragraph_23", "summe_steuerfrei_gt_1_jahr_eur")]),
    ("§ 23 Verlustvortrag", [("krypto_detail", "paragraph_23", "verlustvortrag_eur")]),
    ("§ 23 Einzelveräußerungen", [("krypto_detail", "paragraph_23", "disposals")]),
    ("§ 22-Block unter dem Namen paragraph_22_nr_3",
     [("krypto_detail", "paragraph_22_nr_3",)]),
    ("§ 22 steuerpflichtig",
     [("krypto_detail", "paragraph_22_nr_3", "steuerpflichtig_eur")]),
    ("§ 22 Zuflüsse",
     [("krypto_detail", "paragraph_22_nr_3", "summe_zufluesse_eur")]),
    ("ELSTER-Mapping", [("elster_mapping",)]),
    ("Disclaimer", [("disclaimer",)]),
    ("Hinweise", [("hinweise",)]),
]

# Felder je Veräußerungszeile, die HTML- und PDF-Renderer anfassen.
EXPORT_LIEST_JE_DISPOSAL = ("asset", "amount", "acquisition_date", "disposal_date",
                            "held_days", "gain_eur", "taxable")


@case
def test_mitgelieferte_vorlage_laeuft_durch():
    """assets/steuerdaten_vorlage.json ist das, was Nutzer kopieren — sie muss zum
    Schema von build_taxreport.py passen, sonst scheitert der erste Lauf."""
    vorlage = os.path.join(ROOT, "assets", "steuerdaten_vorlage.json")
    assert os.path.isfile(vorlage), vorlage
    with open(vorlage, encoding="utf-8") as fh:
        vorlage_jahr = json.load(fh)["steuerjahr"]
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "r.json")
        run(BUILD, vorlage, "-o", out)          # Transaktionen kommen aus der Vorlage
        r = lies(out)
        # Das Jahr der Vorlage darf sich ändern; es muss nur durchgereicht werden
        # und einen hinterlegten Tarif haben.
        eq(r["meta"]["steuerjahr"], vorlage_jahr)
        assert r["berechnung"]["einkommensteuer_schaetzung"] is not None, (
            f"Vorlagen-Steuerjahr {vorlage_jahr} hat keinen Tarif in steuerlib.py")
        eq(r["berechnung"]["zu_versteuerndes_einkommen"], "0.00")
        run(EXPORT, out, "--outdir", os.path.join(tmp, "out"),
            "--formats", "html", "elster")


@case
def test_arbeitnehmer_pauschbetrag_erzeugt_keinen_verlust():
    """§ 9a Satz 2 EStG: der Pauschbetrag ist nur bis zur Höhe der Einnahmen
    abziehbar. Ein echter Werbungskostenüberhang mindert die Einkünfte dagegen."""
    with tempfile.TemporaryDirectory() as tmp:
        sd = schreibe(os.path.join(tmp, "sd.json"), dict(
            STEUERDATEN,
            anlage_n={"bruttoarbeitslohn": "500.00", "lohnsteuer": "0",
                      "soli": "0", "kirchensteuer": "0", "werbungskosten": {}}))
        tx = schreibe(os.path.join(tmp, "t.json"), [])
        out = os.path.join(tmp, "r.json")
        run(BUILD, sd, "--transactions", tx, "-o", out)
        n = lies(out)["anlagen"]["N"]
        eq(n["arbeitnehmer_pauschbetrag"], "1230", "Pauschbetrag 2024")
        eq(n["werbungskosten_angesetzt"], "500.00",
           "der Pauschbetrag ist auf die Einnahmen gedeckelt")
        eq(n["einkuenfte"], "0.00", "der Pauschbetrag allein darf keinen Verlust erzeugen")

        # Gegenprobe: tatsächliche Werbungskosten über dem Lohn wirken weiterhin
        sd2 = schreibe(os.path.join(tmp, "sd2.json"), dict(
            STEUERDATEN,
            anlage_n={"bruttoarbeitslohn": "500.00", "lohnsteuer": "0", "soli": "0",
                      "kirchensteuer": "0", "werbungskosten": {"fortbildung": "3000.00"}}))
        out2 = os.path.join(tmp, "r2.json")
        run(BUILD, sd2, "--transactions", tx, "-o", out2)
        eq(lies(out2)["anlagen"]["N"]["einkuenfte"], "-2500.00",
           "echter Werbungskostenüberhang bleibt erhalten")


@case
def test_kontrakt_krypto_fifo_zu_build_taxreport():
    with tempfile.TemporaryDirectory() as tmp:
        tx = schreibe(os.path.join(tmp, "t.json"), TRANSAKTIONEN)
        k = os.path.join(tmp, "k.json")
        run(FIFO, tx, 2024, k)
        res = lies(k)
        pruefe_kontrakt(res, BUILD_LIEST_AUS_KRYPTO, "build_taxreport.py")

        # Die Doppelnamen müssen denselben Inhalt tragen — sonst liest ein Konsument
        # still eine 0, wenn ein späterer Umbau einen der beiden Namen entfernt.
        eq(res["paragraph_22_nr3"], res["paragraph_22_nr_3"],
           "paragraph_22_nr3 / paragraph_22_nr_3 auseinandergelaufen")
        eq(res["paragraph_23"]["veraeusserungen"], res["paragraph_23"]["disposals"],
           "veraeusserungen / disposals auseinandergelaufen")
        eq(res["steuerjahr"], res["tax_year"], "steuerjahr / tax_year auseinandergelaufen")
        eq(res["steuerfrei_langfristig_eur"],
           res["paragraph_23"]["summe_steuerfrei_gt_1_jahr_eur"],
           "steuerfrei_langfristig_eur auf beiden Ebenen muss übereinstimmen")

        # und der Konsument muss die Werte auch tatsächlich übernehmen
        sd = schreibe(os.path.join(tmp, "sd.json"), STEUERDATEN)
        out = os.path.join(tmp, "r.json")
        run(BUILD, sd, "--krypto-result", k, "-o", out)
        r = lies(out)
        eq(r["krypto_detail"]["paragraph_23"]["netto_ergebnis_eur"],
           res["paragraph_23"]["netto_ergebnis_eur"], "§ 23-Netto ging beim Übergang verloren")
        eq(r["krypto_detail"]["paragraph_23"]["summe_steuerfrei_gt_1_jahr_eur"],
           res["paragraph_23"]["summe_steuerfrei_gt_1_jahr_eur"],
           "steuerfreier Betrag ging beim Übergang verloren")
        eq(len(r["krypto_detail"]["paragraph_23"]["disposals"]),
           len(res["paragraph_23"]["disposals"]), "Veräußerungszeilen gingen verloren")


@case
def test_kontrakt_parser_schreibweise_zu_build_taxreport():
    """Die Broker-Parser schreiben andere (erlaubte) Schreibweisen als die FIFO-Engine.
    Auch diese Variante muss vollständig ankommen — insbesondere der steuerfreie Teil."""
    parser_stil = {
        "tax_year": 2024,                       # nicht 'steuerjahr'
        "quelle": "Koinly-Steuerbericht",
        "paragraph_23": {
            "freigrenze_angewendet": False,
            "gewinn_eur": "2000.00", "verlust_eur": "-100.00",
            "netto_ergebnis_eur": "1900.00", "verlustvortrag_eur": "0.00",
            "steuerfrei_langfristig_eur": "5000.00",   # nicht summe_steuerfrei_gt_1_jahr_eur
            "disposals": [],
        },
        "paragraph_22_nr3": {"freigrenze_angewendet": False,
                             "summe_zufluesse_eur": "300.00"},  # nicht summe_eur
        "hinweise": [], "warnungen": [], "elster_extra": [],
    }
    with tempfile.TemporaryDirectory() as tmp:
        k = schreibe(os.path.join(tmp, "k.json"), parser_stil)
        sd = schreibe(os.path.join(tmp, "sd.json"), STEUERDATEN)
        out = os.path.join(tmp, "r.json")
        run(BUILD, sd, "--krypto-result", k, "-o", out)
        r = lies(out)
        p23 = r["krypto_detail"]["paragraph_23"]
        eq(p23["netto_ergebnis_eur"], "1900.00", "tax_year/Parser-Schreibweise")
        eq(p23["steuerpflichtiger_betrag_eur"], "1900.00", "Freigrenze auf die Rohquelle")
        eq(p23["summe_steuerfrei_gt_1_jahr_eur"], "5000.00",
           "steuerfreier Betrag der Parser-Schreibweise ging verloren")
        eq(r["anlagen"]["SO"]["leistungen_22_3_gesamt"], "400.00",
           "summe_zufluesse_eur (+ 100 sonstige) kam nicht an")


@case
def test_kontrakt_build_taxreport_zu_export_report():
    with tempfile.TemporaryDirectory() as tmp:
        r, outdir = pipeline(tmp)
        pruefe_kontrakt(r, EXPORT_LIEST_AUS_REPORT, "export_report.py")

        # Die beiden Schreibweisen im Report müssen dasselbe Objekt beschreiben.
        kd = r["krypto_detail"]
        eq(kd["paragraph_22_nr3"], kd["paragraph_22_nr_3"],
           "im TaxReport sind die § 22-Schreibweisen auseinandergelaufen")

        disposals = kd["paragraph_23"]["disposals"]
        assert disposals, "Testfall ohne Veräußerungen prüft den Zeilen-Kontrakt nicht"
        for d in disposals:
            fehlend = [f for f in EXPORT_LIEST_JE_DISPOSAL if f not in d]
            assert not fehlend, f"Veräußerungszeile ohne {fehlend}: {d}"

        for zeile in r["elster_mapping"]:
            for f in ("anlage", "zeile", "bezeichnung", "wert"):
                assert f in zeile, f"ELSTER-Zeile ohne '{f}': {zeile}"

        # Der Export darf die Summe nicht selbst anders rechnen als der Report.
        sys.path.insert(0, SCRIPTS)
        import export_report as er
        eq(er.summe_einkuenfte(r), dec(r["berechnung"]["summe_der_einkuenfte"]))
        a = r["anlagen"]
        eq(er.summe_einkuenfte(r),
           sum((dec(a[k][er.ANLAGEN_FELD[k]]) for k in er.EINKUNFTS_ANLAGEN), D("0")),
           "Export-Summe ≠ Summe der von ihm gelesenen Anlagenfelder")


# ─────────────────────────────────────────────────────────────────────────────
# 3 — Mehrere Quellen: die Freigrenzen gelten je Person und Jahr, nicht je Datei
# ─────────────────────────────────────────────────────────────────────────────


def rohquelle(netto, p22="0.00", quelle="broker"):
    """Quelle im Parser-Contract: Rohwerte, Freigrenze ausdrücklich NICHT angewendet."""
    n = dec(netto)
    return {
        "steuerjahr": 2024, "quelle": quelle,
        "paragraph_23": {
            "freigrenze_angewendet": False,
            "gewinn_eur": str(n if n > 0 else D("0.00")),
            "verlust_eur": str(n if n < 0 else D("0.00")),
            "netto_ergebnis_eur": str(n),
            "verlustvortrag_eur": str(-n if n < 0 else D("0.00")),
            "steuerfrei_langfristig_eur": "0.00",
            "disposals": [],
        },
        "paragraph_22_nr3": {"freigrenze_angewendet": False, "summe_zufluesse_eur": p22},
        "warnungen": [], "elster_extra": [], "hinweise": [],
    }


@case
def test_freigrenze_23_gilt_fuer_die_summe_aller_quellen():
    """Der eigentliche Grund für den Quellen-Contract: zwei Broker mit je 600 €
    sind zusammen 1.200 € — und damit VOLL steuerpflichtig (Freigrenze, kein Freibetrag)."""
    sd = dict(STEUERDATEN, anlage_so={})
    with tempfile.TemporaryDirectory() as tmp:
        a = schreibe(os.path.join(tmp, "a.json"), rohquelle("600.00", quelle="broker-a"))
        b = schreibe(os.path.join(tmp, "b.json"), rohquelle("600.00", quelle="broker-b"))
        p_sd = schreibe(os.path.join(tmp, "sd.json"), sd)

        einzeln = os.path.join(tmp, "einzeln.json")
        run(BUILD, p_sd, "--krypto-result", a, "-o", einzeln)
        r1 = lies(einzeln)
        eq(r1["krypto_detail"]["paragraph_23"]["steuerpflichtiger_betrag_eur"], "0.00",
           "600 € allein bleiben unter der Freigrenze von 1.000 €")
        eq(r1["krypto_detail"]["paragraph_23"]["freigrenze_ueberschritten"], False)

        beide = os.path.join(tmp, "beide.json")
        run(BUILD, p_sd, "--krypto-result", a, b, "-o", beide)
        r2 = lies(beide)
        p23 = r2["krypto_detail"]["paragraph_23"]
        eq(p23["netto_ergebnis_eur"], "1200.00", "Nettos beider Quellen müssen addiert werden")
        eq(p23["freigrenze_ueberschritten"], True)
        eq(p23["steuerpflichtiger_betrag_eur"], "1200.00",
           "über der Freigrenze ist der GESAMTE Gewinn steuerpflichtig")
        eq(r2["anlagen"]["SO"]["krypto_23_steuerpflichtig"], "1200.00")
        eq(p23["freigrenze_eur"], "1000", "Freigrenze 2024")
        eq(len(r2["meta"]["krypto_quellen"]), 2, "beide Quellen müssen im Report stehen")

        # --transactions zusätzlich zu --krypto-result wird nicht verwendet —
        # das muss gesagt werden, nicht stillschweigend geschehen.
        tx = schreibe(os.path.join(tmp, "t.json"), TRANSAKTIONEN)
        p = run(BUILD, p_sd, "--transactions", tx, "--krypto-result", a,
                "-o", os.path.join(tmp, "beides.json"))
        assert "--transactions wird NICHT verwendet" in p.stderr, p.stderr


def vorberechnete_quelle(netto, steuerpflichtig, vortrag="0.00", quelle="fifo"):
    """Quelle, die die § 23-Freigrenze bereits selbst angewendet hat — so, wie
    krypto_fifo.py es tut, wenn man es je Börse einmal laufen lässt."""
    q = rohquelle(netto, quelle=quelle)
    q["paragraph_23"]["freigrenze_angewendet"] = True
    q["paragraph_23"]["steuerpflichtiger_betrag_eur"] = steuerpflichtig
    q["paragraph_23"]["verlustvortrag_eur"] = vortrag
    return q


@case
def test_vorberechnete_quellen_werden_beim_zusammenfuehren_neu_gerechnet():
    """Zwei je Börse gerechnete FIFO-Läufe mit je 600 € sind zusammen 1.200 € und
    damit voll steuerpflichtig. Würden ihre vorberechneten Nullen addiert, wiese der
    Report zugleich „Freigrenze überschritten: ja“ und „steuerpflichtig 0,00 €“ aus."""
    p_sd_daten = dict(STEUERDATEN, anlage_so={})
    with tempfile.TemporaryDirectory() as tmp:
        p_sd = schreibe(os.path.join(tmp, "sd.json"), p_sd_daten)
        a = schreibe(os.path.join(tmp, "a.json"),
                     vorberechnete_quelle("600.00", "0.00", quelle="boerse-a"))
        b = schreibe(os.path.join(tmp, "b.json"),
                     vorberechnete_quelle("600.00", "0.00", quelle="boerse-b"))

        # Einzelne vorberechnete Quelle: Wert wird unverändert übernommen.
        einzeln = os.path.join(tmp, "einzeln.json")
        run(BUILD, p_sd, "--krypto-result", a, "-o", einzeln)
        r1 = lies(einzeln)
        eq(r1["krypto_detail"]["paragraph_23"]["steuerpflichtiger_betrag_eur"], "0.00")
        assert not r1["warnungen"], r1["warnungen"]

        beide = os.path.join(tmp, "beide.json")
        run(BUILD, p_sd, "--krypto-result", a, b, "-o", beide)
        r2 = lies(beide)
        p23 = r2["krypto_detail"]["paragraph_23"]
        eq(p23["netto_ergebnis_eur"], "1200.00")
        eq(p23["freigrenze_ueberschritten"], True)
        eq(p23["steuerpflichtiger_betrag_eur"], "1200.00",
           "vorberechnete Nullen dürfen nicht einfach addiert werden")
        assert p23["freigrenze_ueberschritten"] == (
            dec(p23["steuerpflichtiger_betrag_eur"]) > 0), \
            "Report widerspricht sich: Freigrenze überschritten, aber nichts steuerpflichtig"
        assert any("neu bestimmt" in w for w in r2["warnungen"]), r2["warnungen"]

        # Gewinn- und Verlustquelle saldieren sich (§ 23 ist EIN Topf je Jahr).
        g = schreibe(os.path.join(tmp, "g.json"),
                     vorberechnete_quelle("5000.00", "5000.00", quelle="gewinn"))
        v = schreibe(os.path.join(tmp, "v.json"),
                     vorberechnete_quelle("-2000.00", "0.00", vortrag="2000.00",
                                          quelle="verlust"))
        saldo = os.path.join(tmp, "saldo.json")
        run(BUILD, p_sd, "--krypto-result", g, v, "-o", saldo)
        p = lies(saldo)["krypto_detail"]["paragraph_23"]
        eq(p["netto_ergebnis_eur"], "3000.00")
        eq(p["steuerpflichtiger_betrag_eur"], "3000.00",
           "der Verlust muss den Gewinn mindern, nicht als Vortrag danebenstehen")
        eq(p["verlustvortrag_eur"], "0.00", "kein Vortrag neben einem Gewinn desselben Jahres")


@case
def test_freigrenze_22_3_gilt_fuer_staking_plus_sonstige_leistungen():
    """§ 22 Nr. 3 Satz 2: die 256 € gelten für alle Leistungen des Jahres zusammen —
    Staking aus allen Quellen PLUS anlage_so.sonstige_einkuenfte."""
    with tempfile.TemporaryDirectory() as tmp:
        a = schreibe(os.path.join(tmp, "a.json"),
                     rohquelle("0.00", p22="100.00", quelle="broker-a"))
        b = schreibe(os.path.join(tmp, "b.json"),
                     rohquelle("0.00", p22="100.00", quelle="broker-b"))

        # 100 + 100 + 100 = 300 ≥ 256 → alles steuerpflichtig
        sd_ueber = schreibe(os.path.join(tmp, "sd1.json"),
                            dict(STEUERDATEN, anlage_so={"sonstige_einkuenfte": "100.00"}))
        out1 = os.path.join(tmp, "r1.json")
        run(BUILD, sd_ueber, "--krypto-result", a, b, "-o", out1)
        so = lies(out1)["anlagen"]["SO"]
        eq(so["leistungen_22_3_gesamt"], "300.00")
        eq(so["krypto_22_3_staking"], "200.00", "Staking beider Quellen addiert")
        eq(so["sonstige_einkuenfte"], "100.00")
        eq(so["leistungen_22_3_steuerpflichtig"], "300.00",
           "Freigrenze überschritten → gesamte Summe steuerpflichtig")

        # 100 + 100 + 50 = 250 < 256 → nichts steuerpflichtig
        sd_unter = schreibe(os.path.join(tmp, "sd2.json"),
                            dict(STEUERDATEN, anlage_so={"sonstige_einkuenfte": "50.00"}))
        out2 = os.path.join(tmp, "r2.json")
        run(BUILD, sd_unter, "--krypto-result", a, b, "-o", out2)
        so2 = lies(out2)["anlagen"]["SO"]
        eq(so2["leistungen_22_3_gesamt"], "250.00")
        eq(so2["leistungen_22_3_steuerpflichtig"], "0.00", "unter 256 € steuerfrei")


@case
def test_vorzeichenkonvention_von_verlust_eur_ueberlebt_die_aggregation():
    """`verlust_eur` ist bei allen Produzenten die Summe der NEGATIVEN Ergebnisse
    (negatives Vorzeichen). Dreht der Aggregator das Vorzeichen um, bedeutet
    derselbe Schlüsselname plötzlich etwas anderes — ein stiller Vorzeichenfehler
    für jeden Konsumenten, der nicht zufällig abs() rechnet."""
    with tempfile.TemporaryDirectory() as tmp:
        tx = schreibe(os.path.join(tmp, "t.json"), [
            {"timestamp": "2024-01-05", "type": "buy", "asset": "BTC",
             "amount": "1", "eur_value": "30000.00"},
            {"timestamp": "2024-09-05", "type": "sell", "asset": "BTC",
             "amount": "1", "eur_value": "27500.00"}])
        k = os.path.join(tmp, "k.json")
        run(FIFO, tx, 2024, k)
        p_fifo = lies(k)["paragraph_23"]
        eq(p_fifo["verlust_eur"], "-2500.00", "Produzent: negatives Vorzeichen")
        eq(p_fifo["verlust_eur"], p_fifo["summe_verluste_eur"], "Altname synchron")

        sd = schreibe(os.path.join(tmp, "sd.json"), dict(STEUERDATEN, anlage_so={}))
        out = os.path.join(tmp, "r.json")
        run(BUILD, sd, "--krypto-result", k, "-o", out)
        p_agg = lies(out)["krypto_detail"]["paragraph_23"]
        eq(p_agg["verlust_eur"], p_fifo["verlust_eur"],
           "der Aggregator dreht das Vorzeichen von verlust_eur um")
        eq(p_agg["summe_verluste_eur"], p_fifo["summe_verluste_eur"])
        eq(p_agg["verlustvortrag_eur"], "2500.00", "Vortrag ist positiv ausgewiesen")


@case
def test_verlust_einer_quelle_verrechnet_sich_mit_gewinn_der_anderen():
    """§ 23-Verluste sind mit § 23-Gewinnen verrechenbar — auch über Broker hinweg."""
    with tempfile.TemporaryDirectory() as tmp:
        a = schreibe(os.path.join(tmp, "a.json"), rohquelle("3000.00", quelle="a"))
        b = schreibe(os.path.join(tmp, "b.json"), rohquelle("-2500.00", quelle="b"))
        p_sd = schreibe(os.path.join(tmp, "sd.json"), dict(STEUERDATEN, anlage_so={}))
        out = os.path.join(tmp, "r.json")
        run(BUILD, p_sd, "--krypto-result", a, b, "-o", out)
        p23 = lies(out)["krypto_detail"]["paragraph_23"]
        eq(p23["netto_ergebnis_eur"], "500.00")
        eq(p23["steuerpflichtiger_betrag_eur"], "0.00",
           "500 € Restgewinn liegen unter der Freigrenze")
        eq(p23["verlustvortrag_eur"], "0.00", "kein Vortrag, solange netto positiv")


# ─────────────────────────────────────────────────────────────────────────────
# 4 — Regressionsschutz für die ursprünglichen Defekte, end-to-end
# ─────────────────────────────────────────────────────────────────────────────


@case
def test_vorjahresgewinn_faellt_nicht_in_dieses_jahr():
    with tempfile.TemporaryDirectory() as tmp:
        r, _ = pipeline(tmp)
        p23 = r["krypto_detail"]["paragraph_23"]
        jahre = {d["disposal_date"][:4] for d in p23["disposals"]}
        eq(jahre, {"2024"}, "im Report stehen Veräußerungen fremder Jahre")
        # Der ETH-Gewinn von 2023 (9.000 − 5.000 = 4.000) darf nirgends auftauchen.
        eq(p23["netto_ergebnis_eur"], "5500.00", "Vorjahresgewinn ist mitgezählt worden")
        assert not any(d["asset"] == "ETH" for d in p23["disposals"])


@case
def test_verkauf_am_jahrestag_ist_steuerpflichtig():
    with tempfile.TemporaryDirectory() as tmp:
        r, _ = pipeline(tmp)
        ltc = [d for d in r["krypto_detail"]["paragraph_23"]["disposals"]
               if d["asset"] == "LTC"]
        eq(len(ltc), 1, "LTC-Veräußerung fehlt")
        eq(ltc[0]["acquisition_date"], "2023-05-20")
        eq(ltc[0]["disposal_date"], "2024-05-20")
        eq(ltc[0]["held_days"], 366, "2024 ist ein Schaltjahr")
        eq(ltc[0]["taxable"], True,
           "§ 188 Abs. 2 BGB: am Jahrestag ist die Frist noch nicht abgelaufen")
        eq(ltc[0]["gain_eur"], "500.00")


@case
def test_negative_menge_erzeugt_trotzdem_eine_veraeusserung():
    with tempfile.TemporaryDirectory() as tmp:
        r, _ = pipeline(tmp)
        xrp = [d for d in r["krypto_detail"]["paragraph_23"]["disposals"]
               if d["asset"] == "XRP"]
        eq(len(xrp), 1, "Verkauf mit negativer Menge wurde verschluckt")
        eq(xrp[0]["amount"], "100", "Menge muss normalisiert werden, nicht verworfen")
        eq(xrp[0]["gain_eur"], "50.00")
        eq(xrp[0]["taxable"], True)


@case
def test_teillos_mit_gebuehr_wird_anteilig_gerechnet():
    """Ein Verkauf über zwei Lose: Gebühr anteilig, altes Los steuerfrei, neues nicht."""
    with tempfile.TemporaryDirectory() as tmp:
        r, _ = pipeline(tmp)
        btc = sorted([d for d in r["krypto_detail"]["paragraph_23"]["disposals"]
                      if d["asset"] == "BTC"], key=lambda d: d["acquisition_date"])
        eq(len(btc), 2, "der Verkauf muss in zwei Teil-Lose zerfallen")
        alt, neu = btc
        eq(alt["acquisition_date"], "2022-06-01")
        eq(alt["taxable"], False, "> 1 Jahr gehalten")
        eq(alt["fee_eur"], "100.00", "Gebühr anteilig 1,0/1,5 von 150 €")
        eq(alt["gain_eur"], "19800.00")
        eq(neu["acquisition_date"], "2024-01-10")
        eq(neu["taxable"], True)
        eq(neu["fee_eur"], "50.00", "Gebühr anteilig 0,5/1,5 von 150 €")
        eq(neu["cost_basis_eur"], "15000.00")
        eq(neu["gain_eur"], "4950.00")
        # Gebühr genau einmal verteilt, nicht doppelt
        eq(sum((dec(d["fee_eur"]) for d in btc), D("0")), D("150.00"))


@case
def test_elster_csv_format_und_pflichthinweis():
    with tempfile.TemporaryDirectory() as tmp:
        r, outdir = pipeline(tmp)
        pfad = os.path.join(outdir, "elster_mapping_2024.csv")
        with open(pfad, encoding="utf-8-sig", newline="") as f:
            zeilen = list(csv.reader(f, delimiter=";"))

        kommentare = [z[0] for z in zeilen if z and z[0].startswith("#")]
        assert any("Steuerberatung" in k for k in kommentare), \
            "Der Pflichthinweis fehlt in der ELSTER-CSV — genau die Datei wird abgetippt"
        assert any("ELSTER ändert die Layouts" in k for k in kommentare), \
            "Zeilennummern-Caveat fehlt"

        kopf = ["Anlage", "Zeile", "Bezeichnung", "Wert"]
        assert kopf in zeilen, f"Kopfzeile fehlt: {zeilen[:8]}"
        daten = zeilen[zeilen.index(kopf) + 1:]
        anlagen = {z[0] for z in daten if z}
        assert "Anlage S" in anlagen, f"Anlage-S-Zeile fehlt: {sorted(anlagen)}"
        assert "Anlage G" in anlagen, f"Anlage-G-Zeile fehlt: {sorted(anlagen)}"
        assert "Anlage SO" in anlagen and "Anlage KAP" in anlagen

        werte = {(z[0], z[2]): z[3] for z in daten if len(z) >= 4}
        eq(werte[("Anlage S", "Gewinn aus selbständiger Arbeit (§ 18 EStG)")], "5000,00",
           "Beträge müssen für deutsches Excel ein Dezimalkomma haben")
        eq(werte[("Anlage G", "Gewinn aus Gewerbebetrieb (§ 15 EStG)")], "2000,00")
        eq(werte[("Hauptvordruck", "Steuerjahr")], "2024",
           "die Jahreszahl darf NICHT umformatiert werden")
        eq(werte[("Anlage Kind", "Kind Eins")], "2015-04-02",
           "Datumsangaben bleiben unangetastet")

        for z in daten:
            if len(z) >= 4:
                assert not re.fullmatch(r"-?\d+\.\d+", z[3]), \
                    f"Punkt-Dezimaltrenner in der CSV: {z}"


@case
def test_elster_json_und_csv_zeigen_dieselben_zeilen():
    with tempfile.TemporaryDirectory() as tmp:
        r, outdir = pipeline(tmp)
        j = lies(os.path.join(outdir, "elster_mapping_2024.json"))
        eq(j["steuerjahr"], 2024)
        eq(len(j["elster_mapping"]), len(r["elster_mapping"]),
           "der Export lässt ELSTER-Zeilen weg")
        assert j["disclaimer"], "Disclaimer fehlt im ELSTER-JSON"


# ─────────────────────────────────────────────────────────────────────────────
# 5 — Robustheit: klare deutsche Meldung statt Traceback
# ─────────────────────────────────────────────────────────────────────────────


def pruefe_saubere_fehlermeldung(p, *erwartete_worte):
    assert p.returncode != 0, f"Abbruch erwartet, rc=0\nstdout:\n{p.stdout}"
    text = p.stdout + p.stderr
    assert "Traceback" not in text, f"roher Traceback statt Meldung:\n{text}"
    assert "FEHLER" in text or "ABBRUCH" in text, f"keine erkennbare Fehlermeldung:\n{text}"
    for w in erwartete_worte:
        assert w in text, f"Meldung nennt '{w}' nicht:\n{text}"


@case
def test_kaputte_steuerdaten_brechen_sauber_ab():
    with tempfile.TemporaryDirectory() as tmp:
        tx = schreibe(os.path.join(tmp, "t.json"), [])
        out = os.path.join(tmp, "r.json")

        kaputt = schreibe(os.path.join(tmp, "kaputt.json"),
                          '{"steuerjahr": 2024, "anlage_n": {,}')
        pruefe_saubere_fehlermeldung(
            run(BUILD, kaputt, "--transactions", tx, "-o", out, erwarte_rc=None),
            "kein gültiges JSON")

        ohne_jahr = schreibe(os.path.join(tmp, "ohne_jahr.json"), {"anlage_n": {}})
        pruefe_saubere_fehlermeldung(
            run(BUILD, ohne_jahr, "--transactions", tx, "-o", out, erwarte_rc=None),
            "steuerjahr")

        falscher_typ = schreibe(os.path.join(tmp, "typ.json"),
                                {"steuerjahr": 2024,
                                 "anlage_n": {"bruttoarbeitslohn": {"jan": "1000"}}})
        pruefe_saubere_fehlermeldung(
            run(BUILD, falscher_typ, "--transactions", tx, "-o", out, erwarte_rc=None),
            "anlage_n.bruttoarbeitslohn")

        fehlt = os.path.join(tmp, "gibt-es-nicht.json")
        pruefe_saubere_fehlermeldung(
            run(BUILD, fehlt, "--transactions", tx, "-o", out, erwarte_rc=None),
            "nicht gefunden")


@case
def test_unvollstaendiges_krypto_ergebnis_bricht_sauber_ab():
    with tempfile.TemporaryDirectory() as tmp:
        sd = schreibe(os.path.join(tmp, "sd.json"), STEUERDATEN)
        out = os.path.join(tmp, "r.json")

        ohne_p23 = schreibe(os.path.join(tmp, "k1.json"), {"steuerjahr": 2024})
        pruefe_saubere_fehlermeldung(
            run(BUILD, sd, "--krypto-result", ohne_p23, "-o", out, erwarte_rc=None),
            "paragraph_23", "fehlt")

        falscher_typ = schreibe(os.path.join(tmp, "k2.json"),
                                {"steuerjahr": 2024, "paragraph_23": []})
        pruefe_saubere_fehlermeldung(
            run(BUILD, sd, "--krypto-result", falscher_typ, "-o", out, erwarte_rc=None),
            "paragraph_23")

        kein_objekt = schreibe(os.path.join(tmp, "k3.json"), [1, 2, 3])
        pruefe_saubere_fehlermeldung(
            run(BUILD, sd, "--krypto-result", kein_objekt, "-o", out, erwarte_rc=None),
            "JSON-Objekt")

        unlesbarer_betrag = schreibe(os.path.join(tmp, "k4.json"), {
            "steuerjahr": 2024,
            "paragraph_23": {"netto_ergebnis_eur": "k. A.", "freigrenze_angewendet": False},
            "paragraph_22_nr3": {},
        })
        pruefe_saubere_fehlermeldung(
            run(BUILD, sd, "--krypto-result", unlesbarer_betrag, "-o", out, erwarte_rc=None),
            "netto_ergebnis_eur")


@case
def test_kaputte_transaktionen_brechen_sauber_ab():
    with tempfile.TemporaryDirectory() as tmp:
        # Pflichtfeld unlesbar → Fehler, niemals stille 0
        tx = schreibe(os.path.join(tmp, "t.json"), [
            {"timestamp": "2024-03-01", "type": "sell", "asset": "BTC",
             "amount": "1", "eur_value": "n/a"}])
        pruefe_saubere_fehlermeldung(run(FIFO, tx, 2024, erwarte_rc=None), "eur_value")

        ohne_datum = schreibe(os.path.join(tmp, "t2.json"), [
            {"type": "buy", "asset": "BTC", "amount": "1", "eur_value": "100"}])
        p = run(FIFO, ohne_datum, 2024, erwarte_rc=None)
        pruefe_saubere_fehlermeldung(p, "Zeitstempel")

        sd = schreibe(os.path.join(tmp, "sd.json"), STEUERDATEN)
        out = os.path.join(tmp, "r.json")
        pruefe_saubere_fehlermeldung(
            run(BUILD, sd, "--transactions", tx, "-o", out, erwarte_rc=None), "eur_value")


@case
def test_export_mit_kaputtem_report_bricht_sauber_ab():
    with tempfile.TemporaryDirectory() as tmp:
        kaputt = schreibe(os.path.join(tmp, "r.json"), "{nicht: json}")
        p = run(EXPORT, kaputt, "--outdir", os.path.join(tmp, "out"), erwarte_rc=None)
        assert p.returncode != 0
        assert "Traceback" not in (p.stdout + p.stderr)
        assert "kein gültiges JSON" in (p.stdout + p.stderr), p.stderr

        liste = schreibe(os.path.join(tmp, "r2.json"), [1, 2])
        p2 = run(EXPORT, liste, "--outdir", os.path.join(tmp, "out"), erwarte_rc=None)
        assert p2.returncode != 0 and "TaxReport" in (p2.stdout + p2.stderr)


@case
def test_export_eines_luecken_reports_stuerzt_nicht_ab():
    """Ein Report ohne Krypto-Block/Berechnung muss trotzdem exportierbar sein —
    der Export darf keine stillen Auslassungen und keinen Absturz produzieren."""
    with tempfile.TemporaryDirectory() as tmp:
        duenn = schreibe(os.path.join(tmp, "r.json"), {
            "meta": {"steuerjahr": 2024},
            "anlagen": {}, "berechnung": {}, "elster_mapping": [],
            "disclaimer": ["Dies ist KEINE Steuerberatung."],
        })
        outdir = os.path.join(tmp, "out")
        fmts = ["html", "elster"] + (["pdf"] if hat_fpdf() else [])
        run(EXPORT, duenn, "--outdir", outdir, "--formats", *fmts)
        html = open(os.path.join(outdir, "taxreport_2024.html"), encoding="utf-8").read()
        assert "KEINE Steuerberatung" in html
        assert "keine Veräußerungen" in html


@case
def test_unbekanntes_steuerjahr_liefert_report_ohne_est():
    """Kein hinterlegter Tarif → kein Fantasiewert, aber trotzdem ein Report."""
    with tempfile.TemporaryDirectory() as tmp:
        sd = schreibe(os.path.join(tmp, "sd.json"), dict(STEUERDATEN, steuerjahr=2019))
        tx = schreibe(os.path.join(tmp, "t.json"), [])
        out = os.path.join(tmp, "r.json")
        p = run(BUILD, sd, "--transactions", tx, "-o", out)
        r = lies(out)
        eq(r["berechnung"]["einkommensteuer_schaetzung"], None,
           "ohne Tarif darf keine Zahl erfunden werden")
        eq(r["ergebnis"]["status"], "nicht berechenbar")
        assert "hinweis_tarif" in r["berechnung"]
        assert any("2019" in w for w in r["warnungen"]), r["warnungen"]
        # und der Export muss auch damit umgehen
        outdir = os.path.join(tmp, "out")
        run(EXPORT, out, "--outdir", outdir, "--formats", "html", "elster")
        html = open(os.path.join(outdir, "taxreport_2019.html"), encoding="utf-8").read()
        assert "Tarif nicht hinterlegt" in html


# ─────────────────────────────────────────────────────────────────────────────
# 6 — Mehrjahresfall: der § 23-Verlustvortrag überlebt den Jahreswechsel
# ─────────────────────────────────────────────────────────────────────────────


@case
def test_verlustvortrag_23_ueber_zwei_jahre():
    """Der Report sagt dem Nutzer, er solle eine Verlustfeststellung beantragen —
    also muss der festgestellte Betrag im Folgejahr auch wieder eingespeist
    werden können. Genau diese Schleife wird hier über die echten CLIs geschlossen.
    """
    minimal = {"steuerjahr": 2022, "steuerpflichtiger": {"name": "Test"},
               "anlage_n": {"bruttoarbeitslohn": "40000.00"}}
    with tempfile.TemporaryDirectory() as tmp:
        # --- 2022: 5.000 € Verlust, Verlustfeststellung ---
        tx22 = schreibe(os.path.join(tmp, "t22.json"), [
            {"timestamp": "2022-01-01", "type": "buy", "asset": "BTC",
             "amount": "1", "eur_value": "20000.00"},
            {"timestamp": "2022-06-01", "type": "sell", "asset": "BTC",
             "amount": "1", "eur_value": "15000.00"}])
        sd22 = schreibe(os.path.join(tmp, "sd22.json"), minimal)
        out22 = os.path.join(tmp, "r22.json")
        run(BUILD, sd22, "--transactions", tx22, "-o", out22)
        so22 = lies(out22)["anlagen"]["SO"]
        eq(so22["krypto_23_verlustvortrag"], "5000.00")
        eq(so22["verlustvortrag_23_neu_gesamt"], "5000.00",
           "der Report muss den ins Folgejahr zu übernehmenden Betrag ausweisen")

        # --- 2024: 8.000 € Gewinn, der Vortrag aus 2022 wird verbraucht ---
        tx24 = schreibe(os.path.join(tmp, "t24.json"), [
            {"timestamp": "2024-01-01", "type": "buy", "asset": "BTC",
             "amount": "1", "eur_value": "10000.00"},
            {"timestamp": "2024-06-01", "type": "sell", "asset": "BTC",
             "amount": "1", "eur_value": "18000.00"}])
        sd24 = schreibe(os.path.join(tmp, "sd24.json"), dict(
            minimal, steuerjahr=2024,
            anlage_so={"verlustvortrag_23_vorjahr": so22["verlustvortrag_23_neu_gesamt"]}))
        out24 = os.path.join(tmp, "r24.json")
        run(BUILD, sd24, "--transactions", tx24, "-o", out24)
        r24 = lies(out24)
        so24 = r24["anlagen"]["SO"]
        eq(so24["krypto_23_vor_verlustvortrag"], "8000.00")
        eq(so24["verlustvortrag_23_verbraucht"], "5000.00")
        eq(so24["verlustvortrag_23_rest"], "0.00")
        eq(so24["krypto_23_steuerpflichtig"], "3000.00", "8.000 − 5.000 aus 2022")
        eq(so24["einkuenfte_gesamt"], "3000.00")
        # der Report bleibt in sich stimmig: SO = § 23 + § 22 Nr. 3
        eq(dec(r24["berechnung"]["summe_der_einkuenfte"]),
           dec(r24["anlagen"]["N"]["einkuenfte"]) + dec(so24["einkuenfte_gesamt"]))
        # und der Export verkraftet die neuen Zeilen
        outdir = os.path.join(tmp, "out")
        run(EXPORT, out24, "--outdir", outdir, "--formats", "html", "elster")
        with open(os.path.join(outdir, "elster_mapping_2024.csv"), encoding="utf-8") as f:
            csv_text = f.read()
        assert "Verlustvortrag" in csv_text, "der Vortrag fehlt im ELSTER-Export"


@case
def test_cli_strict_bricht_bei_unbekanntem_feld_ab():
    with tempfile.TemporaryDirectory() as tmp:
        tx = schreibe(os.path.join(tmp, "t.json"), [])
        out = os.path.join(tmp, "r.json")
        sd = schreibe(os.path.join(tmp, "sd.json"), {
            "steuerjahr": 2024, "steuerpflichtiger": {"name": "Test"},
            "anlage_n": {"brutto_arbeitslohn": "72000.00"}})

        p = run(BUILD, sd, "--transactions", tx, "-o", out, erwarte_rc=None)
        eq(p.returncode, 0, "ohne --strict nur eine Warnung")
        assert "brutto_arbeitslohn" in p.stderr, p.stderr
        assert "Traceback" not in p.stderr, p.stderr
        eq(lies(out)["anlagen"]["N"]["bruttoarbeitslohn"], "0.00",
           "der Wert des Tippfehlers darf nicht heimlich gelesen werden")

        p2 = run(BUILD, sd, "--transactions", tx, "-o", out, "--strict", erwarte_rc=None)
        assert p2.returncode != 0, "--strict muss abbrechen"
        assert "anlage_n.brutto_arbeitslohn" in p2.stderr, p2.stderr
        assert "Traceback" not in p2.stderr, p2.stderr
        # die Warnung muss auch im HTML ankommen
        outdir = os.path.join(tmp, "out")
        run(EXPORT, out, "--outdir", outdir, "--formats", "html")
        html = open(os.path.join(outdir, "taxreport_2024.html"), encoding="utf-8").read()
        assert "brutto_arbeitslohn" in html, "unbekanntes Feld fehlt im HTML-Report"


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
