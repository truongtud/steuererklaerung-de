#!/usr/bin/env python3
"""Tests für scripts/export_report.py. Ausführen: python3 tests/test_export.py

Der Report-Fixture wird hier selbst gebaut (kein Import von build_taxreport.py),
damit die Tests unabhängig vom Report-Bauer laufen. Neue Schlüssel sind optional.
"""
import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import export_report as ex  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def contains(hay, needle, label=""):
    assert needle in hay, f"{label}: {needle!r} fehlt in der Ausgabe"


def missing(hay, needle, label=""):
    assert needle not in hay, f"{label}: {needle!r} hätte nicht vorkommen dürfen"


DISCLAIMER = [
    "Dies ist KEINE Steuerberatung und keine verbindliche Steuerberechnung.",
    "Krypto-Endkontrolle durch Steuerberater und Abgleich mit der ELSTER-Berechnung.",
]
HINWEISE = [
    "ELSTER-Zeilennummern sind Orientierungswerte und aendern sich jaehrlich.",
]


def fixture(**over) -> dict:
    """Minimaler TaxReport in der Form, die der Exporter liest."""
    r = {
        "meta": {
            "steuerjahr": 2024,
            "erstellt": "2025-05-01T10:00:00+00:00",
            "waehrung": "EUR",
            "veranlagung": "Einzelveranlagung",
            "steuerpflichtiger": {"name": "Ayşe Öztürk"},
        },
        "anlagen": {
            "N": {"einkuenfte": "62000.00", "bruttoarbeitslohn": "63230.00"},
            "KAP": {"kapitalertraege": "1500.00"},
            "SO": {"krypto_23_steuerpflichtig": "2500.00", "einkuenfte_gesamt": "2756.00"},
            "V": {"einkuenfte": "0.00"},
            "S": {"gewinn": "8000.00"},
            "G": {"gewinn": "1200.00"},
        },
        "krypto_detail": {
            "paragraph_23": {
                "netto_ergebnis_eur": "2500.00",
                "freigrenze_eur": "1000.00",
                "freigrenze_ueberschritten": True,
                "steuerpflichtiger_betrag_eur": "2500.00",
                "summe_steuerfrei_gt_1_jahr_eur": "4000.00",
                "verlustvortrag_eur": "0.00",
                "disposals": [
                    {"asset": "BTC", "amount": "0.000000123456789",
                     "acquisition_date": "2015-04-02", "disposal_date": "2024-06-01",
                     "held_days": 3348, "gain_eur": "1200.00", "taxable": False},
                    {"asset": "ETH", "amount": 12345678901.5,
                     "acquisition_date": "2024-01-02", "disposal_date": "2024-03-02",
                     "held_days": 60, "gain_eur": "1300.00", "taxable": True},
                ],
            },
            "paragraph_22_nr_3": {"summe_zufluesse_eur": "300.00",
                                  "steuerpflichtig_eur": "256.00"},
        },
        "berechnung": {
            "summe_der_einkuenfte": "73956.00",
            "zu_versteuerndes_einkommen": "62000.00",
            "tarif": "Grundtarif",
            "einkommensteuer_schaetzung": "16000.00",
            "soli_schaetzung": "0.00",
            "kirchensteuer_schaetzung": None,
        },
        "elster_mapping": [
            {"anlage": "Hauptvordruck", "zeile": "—", "bezeichnung": "Steuerjahr",
             "wert": "2024"},
            {"anlage": "Anlage N", "zeile": "Z. 6", "bezeichnung": "Bruttoarbeitslohn",
             "wert": "63230.00"},
            {"anlage": "Anlage S", "zeile": "Z. 4", "bezeichnung": "Gewinn selbständige Arbeit",
             "wert": "8000.00"},
            {"anlage": "Anlage G", "zeile": "Z. 4", "bezeichnung": "Gewinn Gewerbebetrieb",
             "wert": "1200.00"},
            {"anlage": "Anlage Kind", "zeile": "Kind 1",
             "bezeichnung": '=HYPERLINK("http://evil.example/x";"Kind")',
             "wert": "2015-04-02"},
        ],
        "disclaimer": list(DISCLAIMER),
        "hinweise": list(HINWEISE),
    }
    r.update(over)
    return r


def _export(report, formats=("html", "elster"), tmp=None):
    """Exporter über die CLI laufen lassen; gibt (rc, outdir, stdout, stderr) zurück."""
    d = Path(tmp or tempfile.mkdtemp())
    src = d / "taxreport.json"
    src.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = ex.main([str(src), "--outdir", str(d / "out"), "--formats", *formats])
    return rc, d / "out", out.getvalue(), err.getvalue()


def _csv_text(outdir: Path) -> str:
    files = list(outdir.glob("elster_mapping_*.csv"))
    assert files, f"keine ELSTER-CSV in {outdir}: {list(outdir.iterdir())}"
    return files[0].read_text(encoding="utf-8-sig")


def _elster_json(outdir: Path) -> dict:
    files = list(outdir.glob("elster_mapping_*.json"))
    assert files, f"kein ELSTER-JSON in {outdir}"
    return json.loads(files[0].read_text(encoding="utf-8"))


def _html_text(outdir: Path) -> str:
    files = list(outdir.glob("taxreport_*.html"))
    assert files, f"kein HTML in {outdir}"
    return files[0].read_text(encoding="utf-8")


def _checkliste_text(outdir: Path) -> str:
    files = list(outdir.glob("elster_checkliste_*.html"))
    assert files, f"keine Checkliste in {outdir}"
    return files[0].read_text(encoding="utf-8")


# ── Disclaimer in den ELSTER-Exporten ────────────────────────────────────────
@case
def test_disclaimer_in_elster_csv_und_json():
    """Aus genau diesen Dateien wird in Mein ELSTER abgetippt — SKILL.md verlangt
    den Hinweis; er fehlte bisher in beiden."""
    rc, outdir, _o, _e = _export(fixture())
    eq(rc, 0, "Exit-Code")
    text = _csv_text(outdir)
    for satz in DISCLAIMER:
        contains(text, satz, "Disclaimer in CSV")
    contains(text, HINWEISE[0], "Hinweise in CSV")
    # Kommentarzeilen stehen *vor* der Kopfzeile
    assert text.index(DISCLAIMER[0]) < text.index("Anlage;Zeile;Bezeichnung;Wert"), \
        "Disclaimer muss vor der Tabelle stehen"

    data = _elster_json(outdir)
    eq(data.get("disclaimer"), DISCLAIMER, "Disclaimer im JSON")
    eq(data.get("hinweise"), HINWEISE, "Hinweise im JSON")
    eq(len(data.get("elster_mapping", [])), 5, "alle Mapping-Zeilen im JSON")


@case
def test_disclaimer_optional_aber_nie_leer():
    """Fehlt der Disclaimer im Report, steht trotzdem ein Hinweis in der CSV."""
    r = fixture()
    r.pop("disclaimer")
    r.pop("hinweise")
    rc, outdir, _o, _e = _export(r)
    eq(rc, 0, "Exit-Code ohne disclaimer/hinweise")
    contains(_csv_text(outdir), "Keine Steuerberatung", "Ersatz-Hinweis")
    eq(_elster_json(outdir).get("disclaimer"), [], "leerer Disclaimer-Schlüssel")


# ── ELSTER-Zeilen: kein festes Set ───────────────────────────────────────────
@case
def test_alle_mapping_zeilen_inklusive_s_und_g():
    """Anlage S/G werden besteuert — wer nach der CSV abtippt, darf sie nicht verlieren."""
    rc, outdir, _o, _e = _export(fixture())
    eq(rc, 0)
    text = _csv_text(outdir)
    contains(text, "Gewinn selbständige Arbeit", "Anlage S in CSV")
    contains(text, "Gewinn Gewerbebetrieb", "Anlage G in CSV")
    h = _html_text(outdir)
    contains(h, "Gewinn selbständige Arbeit", "Anlage S im HTML-Mapping")
    contains(h, "Anlage G — Gewerbebetrieb", "Anlage G in der HTML-Übersicht")


@case
def test_unbekannte_anlage_wird_nicht_verschluckt():
    r = fixture()
    r["elster_mapping"].append({"anlage": "Anlage AUS", "zeile": "Z. 1",
                                "bezeichnung": "Auslaendische Einkuenfte", "wert": "500.00"})
    rc, outdir, _o, _e = _export(r)
    eq(rc, 0)
    contains(_csv_text(outdir), "Auslaendische Einkuenfte", "unbekannte Anlage")
    eq(len(_elster_json(outdir)["elster_mapping"]), 6, "Zeilenzahl unverändert")


# ── CSV: deutsche Dezimaltrennung und Formelschutz ───────────────────────────
@case
def test_csv_deutsche_dezimaltrennung():
    rc, outdir, _o, _e = _export(fixture())
    eq(rc, 0)
    text = _csv_text(outdir)
    contains(text, "63230,00", "Betrag mit Komma")
    missing(text, "63230.00", "Betrag darf nicht mit Punkt stehen")
    # Jahre und Datumsangaben bleiben unangetastet
    contains(text, "2024", "Jahreszahl")
    contains(text, "2015-04-02", "ISO-Datum bleibt Datum")
    missing(text, "2015-04,02", "Datum darf nicht umgeschrieben werden")


@case
def test_csv_formel_injection_entschaerft():
    rc, outdir, _o, _e = _export(fixture())
    eq(rc, 0)
    text = _csv_text(outdir)
    assert "'=HYPERLINK" in text, "Formel muss durch führendes ' entschärft sein"
    assert not re.search(r"(^|;|\")=HYPERLINK", text), "keine ausführbare Formel in der CSV"


@case
def test_csv_behaelt_bom_und_semikolon():
    rc, outdir, _o, _e = _export(fixture())
    eq(rc, 0)
    roh = list(outdir.glob("elster_mapping_*.csv"))[0].read_bytes()
    assert roh.startswith(b"\xef\xbb\xbf"), "BOM für Excel fehlt"
    contains(roh.decode("utf-8-sig"), "Anlage;Zeile;Bezeichnung;Wert", "Semikolon-Trenner")


# ── HTML ─────────────────────────────────────────────────────────────────────
@case
def test_html_escaping_von_fremddaten():
    """asset/amount/Datumsfelder kommen aus fremden PDFs — nichts davon roh ins HTML."""
    r = fixture()
    r["krypto_detail"]["paragraph_23"]["disposals"][0].update({
        "asset": "<b>BTC</b>",
        "disposal_date": "<script>alert(1)</script>",
        "acquisition_date": "<img src=x onerror=alert(2)>",
        "held_days": "<i>7</i>",
        "amount": "<script>x</script>",
    })
    r["meta"]["steuerpflichtiger"]["name"] = "<script>name</script>"
    rc, outdir, _o, _e = _export(r, formats=("html",))
    eq(rc, 0)
    h = _html_text(outdir)
    missing(h, "<script>alert(1)</script>", "Veräußerungsdatum roh")
    missing(h, "<img src=x", "Anschaffungsdatum roh")
    missing(h, "<i>7</i>", "Haltedauer roh")
    missing(h, "<script>x</script>", "Menge roh")
    missing(h, "<script>name</script>", "Name roh")
    contains(h, "&lt;script&gt;alert(1)&lt;/script&gt;", "escapte Fassung vorhanden")


@case
def test_html_card_wert_escaped():
    r = fixture()
    r["berechnung"]["tarif"] = "Grundtarif"
    r["berechnung"]["zu_versteuerndes_einkommen"] = "<b>62000</b>"
    rc, outdir, _o, _e = _export(r, formats=("html",))
    eq(rc, 0)
    missing(_html_text(outdir), "<b>62000</b>", "card()-Wert roh interpoliert")


@case
def test_html_ist_self_contained():
    """Kein Byte darf von außerhalb der Datei nachgeladen werden.

    Geprüft wird die Eigenschaft selbst — keine externe Quelle —, nicht mehr
    das frühere Hilfsmerkmal „gar kein <script“: seit den
    „Für Claude kopieren“-Schaltflächen enthält die Datei ein INLINE-Skript.
    Das lädt nichts nach und verschickt nichts; die Zusage, dass die Datei
    ohne Netz vollständig ist, bleibt damit unberührt.
    """
    r = fixture()
    # URL aus den Nutzdaten entfernen, damit „kein http“ hier die *Datei* meint
    r["elster_mapping"][4]["bezeichnung"] = "Kind 1"
    rc, outdir, _o, _e = _export(r, formats=("html",))
    eq(rc, 0)
    h = _html_text(outdir)
    for muster in ("http://", "https://", "//cdn", "<script src", "<link",
                   " src=", " href=", "@import", "url("):
        missing(h, muster, "HTML muss ohne externe Ressourcen auskommen")


@case
def test_html_skript_verschickt_nichts():
    """Das Inline-Skript darf ausschließlich in die Zwischenablage schreiben —
    kein fetch, kein XHR, kein Navigieren, kein Formular-Post. Sonst verließen
    Steuerbeträge das Gerät, und genau das sagt dieses Werkzeug zu."""
    rc, outdir, _o, _e = _export(fixture(), formats=("html",))
    eq(rc, 0)
    h = _html_text(outdir)
    for verboten in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon",
                     "location.href", "window.open", "<form", "new Image("):
        missing(h, verboten, "das Dashboard darf nichts übertragen")


@case
def test_html_druck_stylesheet():
    rc, outdir, _o, _e = _export(fixture(), formats=("html",))
    eq(rc, 0)
    h = _html_text(outdir)
    contains(h, "@media print", "Druck-Stylesheet")
    contains(h, "color-scheme", "color-scheme gesetzt")


@case
def test_html_einkuenfte_zwischensumme():
    """KAP steht in derselben Spalte, gehört aber nicht zur Summe — ohne
    Zwischensumme geht die Spalte scheinbar nicht auf."""
    rc, outdir, _o, _e = _export(fixture(), formats=("html",))
    eq(rc, 0)
    h = _html_text(outdir)
    contains(h, "Summe der Einkünfte", "Zwischensumme fehlt")
    contains(h, "73.956,00 €", "Summe aus dem Report")
    contains(h, "nicht in der Summe", "KAP als ausgenommen gekennzeichnet")


@case
def test_html_summe_wird_notfalls_gerechnet():
    r = fixture()
    r["berechnung"].pop("summe_der_einkuenfte")
    rc, outdir, _o, _e = _export(r, formats=("html",))
    eq(rc, 0)
    # 62000 + 2756 + 0 + 8000 + 1200
    contains(_html_text(outdir), "73.956,00 €", "berechnete Summe")


# ── Robustheit gegen kaputte Felder ──────────────────────────────────────────
@case
def test_kein_absturz_bei_null_und_falschen_typen():
    r = fixture()
    r["anlagen"]["V"] = None                     # a.get("V", {}).get(...) knallte
    r["anlagen"]["S"] = {"gewinn": None}
    r["anlagen"]["G"] = {"gewinn": "k. A."}      # nicht-numerischer String
    p23 = r["krypto_detail"]["paragraph_23"]
    p23["disposals"][0]["gain_eur"] = None       # Decimal(str(None)) knallte
    p23["disposals"][0]["amount"] = 0.5          # numerisch statt String ([:10] knallte)
    p23["disposals"][1]["held_days"] = None
    p23["verlustvortrag_eur"] = None
    p23["freigrenze_eur"] = None
    r["elster_mapping"][1]["bezeichnung"] = None  # [:60] auf None knallte
    r["elster_mapping"][2]["wert"] = None
    r["berechnung"]["einkommensteuer_schaetzung"] = None
    r["meta"]["steuerpflichtiger"] = None
    r["disclaimer"] = "Ein einzelner String statt einer Liste."
    rc, outdir, _o, err = _export(r)
    eq(rc, 0, f"Export muss durchlaufen; stderr={err}")
    h = _html_text(outdir)
    missing(h, ">None<", "None darf nicht als Wert erscheinen")
    contains(h, "—", "unlesbare Beträge werden zu —")
    contains(_csv_text(outdir), "Ein einzelner String", "String-Disclaimer akzeptiert")


@case
def test_fehlende_abschnitte_komplett():
    rc, outdir, _o, err = _export({"meta": {"steuerjahr": 2024}})
    eq(rc, 0, f"leerer Report darf nicht abstürzen; stderr={err}")
    contains(_html_text(outdir), "Steuererklärung 2024")
    contains(_csv_text(outdir), "Anlage;Zeile;Bezeichnung;Wert")


@case
def test_menge_wird_nicht_falsch_abgeschnitten():
    """'12345678901.5'[:10] wäre '1234567890' — eine um Faktor 10 falsche Menge."""
    eq(ex.fmt_menge("12345678901.5"), "12345678901.5")
    eq(ex.fmt_menge(D("0.5")), "0.5")
    eq(ex.fmt_menge("2500.00"), "2500")
    eq(ex.fmt_menge(None), "")
    # sehr lange Mengen werden gerundet/wissenschaftlich, nie im Betrag verfälscht
    lang = ex.fmt_menge("0.000000123456789")
    assert lang.startswith("0.00000012") or "E-" in lang, lang
    rc, outdir, _o, _e = _export(fixture(), formats=("html",))
    eq(rc, 0)
    missing(_html_text(outdir), "<td>1234567890</td>", "abgeschnittene Menge")


@case
def test_ungueltiges_json_meldet_sich():
    d = Path(tempfile.mkdtemp())
    (d / "kaputt.json").write_text("{ das ist kein json", encoding="utf-8")
    err = io.StringIO()
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            ex.main([str(d / "kaputt.json"), "--outdir", str(d / "out")])
    except SystemExit as e:
        assert "JSON" in str(e), f"Meldung nennt das Problem nicht: {e}"
        return
    raise AssertionError("kaputtes JSON hätte gemeldet werden müssen")


# ── Dateinamen ───────────────────────────────────────────────────────────────
@case
def test_steuerjahr_wird_fuer_dateinamen_entschaerft():
    eq(ex.safe_filename("2024"), "2024")
    eq(ex.safe_filename("../../etc/passwd"), "etc_passwd")
    eq(ex.safe_filename(None), "report")
    eq(ex.safe_filename(""), "report")
    r = fixture()
    r["meta"]["steuerjahr"] = "2024/2025"
    rc, outdir, out, err = _export(r)
    eq(rc, 0, f"Slash im Steuerjahr darf den Lauf nicht abbrechen; stderr={err}")
    namen = sorted(p.name for p in outdir.iterdir())
    eq(namen, ["elster_mapping_2024_2025.csv", "elster_mapping_2024_2025.json",
               "taxreport_2024_2025.html"], "entschärfte Dateinamen")
    contains(out, "Erstellt: ")


@case
def test_erstellte_dateien_werden_sofort_gemeldet():
    """Fällt ein Format aus, dürfen die bereits geschriebenen nicht verschwiegen werden."""
    rc, outdir, out, _e = _export(fixture(), formats=("html", "elster"))
    eq(rc, 0)
    eq(out.count("Erstellt: "), 3, "HTML + CSV + JSON gemeldet")


# ── Interaktive Checkliste ───────────────────────────────────────────────────
def _mapping_mit_arten():
    """eintragen / nachrichtlich / trenner — genau wie build_taxreport.py sie
    liefert (siehe _ordne_mapping)."""
    return [
        {"anlage": "Anlage N", "zeile": "Z. 6", "bezeichnung": "Bruttoarbeitslohn",
         "wert": "63230.00", "art": "eintragen"},
        {"anlage": "Anlage KAP", "zeile": "Z. 7", "bezeichnung": "Kapitalerträge",
         "wert": "1500.00", "art": "eintragen"},
        {"anlage": "—", "zeile": "—", "bezeichnung": "— ab hier nur Belege —",
         "wert": "", "art": "trenner"},
        {"anlage": "Anlage KAP", "zeile": "Z. 7",
         "bezeichnung": "nachrichtlich: Rohzeile Musterbank", "wert": "1500.00",
         "art": "nachrichtlich"},
    ]


@case
def test_checkliste_nur_eintragen_zeilen_bekommen_eine_checkbox():
    r = fixture(elster_mapping=_mapping_mit_arten())
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    daten = json.loads(re.search(
        r'<script id="zeilen-daten"[^>]*>(.*?)</script>', h, re.S).group(1))
    eq(len(daten), 2, "nur die zwei 'eintragen'-Zeilen landen in der Checkliste")
    eq({d["bezeichnung"] for d in daten}, {"Bruttoarbeitslohn", "Kapitalerträge"})
    contains(h, "nachrichtlich: Rohzeile Musterbank", "Beleg steht im aufklappbaren Teil")
    missing(h, "ab hier nur Belege", "die Trennzeile selbst wird nicht mehr gebraucht")


@case
def test_checkliste_ist_self_contained():
    r = fixture(elster_mapping=_mapping_mit_arten())
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    for muster in ("http://", "https://", "//cdn", "<script src", "@import"):
        missing(h, muster, "Checkliste muss ohne externe Ressourcen auskommen")
    contains(h, "localStorage", "Häkchen-Speicherung ist Teil der Datei")


@case
def test_checkliste_json_bricht_nicht_aus_dem_script_tag_aus():
    """Eine Bezeichnung/ein Wert mit '</script>' (Fremddaten aus einem Broker-
    Report) darf das eingebettete <script>-Tag nicht vorzeitig schließen."""
    mapping = _mapping_mit_arten()
    mapping[0]["bezeichnung"] = "Bruttolohn</script><script>alert(1)</script>"
    r = fixture(elster_mapping=mapping)
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    missing(h, "</script><script>alert(1)", "roher Tag-Ausbruch im Markup")
    m = re.search(r'<script id="zeilen-daten"[^>]*>(.*?)</script>', h, re.S)
    assert m, "das Datenscript muss weiterhin sauber schließen"
    daten = json.loads(m.group(1))
    assert any("alert(1)" in d["bezeichnung"] for d in daten), \
        "der Wert selbst muss inhaltlich erhalten bleiben, nur sicher eingebettet"


@case
def test_checkliste_html_escaping_der_belegzeilen():
    mapping = _mapping_mit_arten()
    mapping[-1]["bezeichnung"] = "<b>Rohzeile</b>"
    r = fixture(elster_mapping=mapping)
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    missing(h, "<b>Rohzeile</b>", "Belegzeile roh im HTML")
    contains(h, "&lt;b&gt;Rohzeile&lt;/b&gt;", "escapte Fassung vorhanden")


@case
def test_checkliste_disclaimer_vorhanden():
    r = fixture(elster_mapping=_mapping_mit_arten())
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    for text in DISCLAIMER:
        contains(h, text, "Disclaimer in der Checkliste")


@case
def test_checklisten_wert_kopiert_deutsche_notation():
    """Der kopierte Betrag geht direkt in ein ELSTER-Feld: dort gilt
    Dezimalkomma. Ein kopiertes '60000.00' würde je nach Feld als
    Tausendertrennung gelesen oder abgelehnt."""
    eq(ex.checklisten_wert("60000.00"), ("60.000,00 €", "60000,00"))
    eq(ex.checklisten_wert("-450.5"), ("-450,50 €", "-450,50"))


@case
def test_checklisten_wert_zeigt_und_kopiert_dieselbe_zahl():
    """Anzeige und Kopierwert müssen denselben Betrag meinen. Vorher rundete nur
    die Anzeige auf volle Cent: '12.345' stand als '12,35 €' da und kopierte
    '12,345' — zwei verschiedene Beträge in derselben Zeile."""
    for roh in ("12.345", "0.005", "-450.5", "1000.00"):
        anzeige, kopie = ex.checklisten_wert(roh)
        eq(anzeige, f"{kopie.replace(',', '.') and ex.fmt_eur(kopie.replace(',', '.'))}",
           f"{roh}: Anzeige und Kopie beschreiben dieselbe Zahl")


@case
def test_checklisten_wert_laesst_nicht_betraege_unangetastet():
    """Steuerjahr, Name, Datum und Steuer-ID sind keine Beträge — aus '2025'
    darf nie '2.025,00 €' werden."""
    for roh in ("2025", "Max Mustermann", "2015-04-02", "00 000 000 000", ""):
        eq(ex.checklisten_wert(roh), (roh, roh), f"{roh!r} unverändert")


@case
def test_jeder_betrag_im_mapping_ist_als_betrag_erkennbar():
    """Kein Mapping-Wert darf als Betrag gemeint, aber nicht als Betrag
    geschrieben sein. Der Sparer-Pauschbetrag kam als '1000' statt '1000.00'
    aus build_taxreport — in der CSV der einzige Wert ohne Dezimalkomma, und in
    der Checkliste wurde er gar nicht erst als Betrag erkannt (also weder
    formatiert angezeigt noch formulargerecht kopiert)."""
    import build_taxreport as bt

    r = bt.build({"steuerjahr": 2025,
                  "steuerpflichtiger": {"verheiratet": False},
                  "anlage_kap": {"kapitalertraege": "4000"}}, [], None)
    pb = [z for z in r["elster_mapping"] if "Sparer-Pauschbetrag" in z["bezeichnung"]]
    eq(len(pb), 1, "genau eine Sparer-Pauschbetrag-Zeile")
    eq(pb[0]["wert"], "1000.00", "als Betrag geschrieben, nicht als blanke Ganzzahl")
    eq(ex.checklisten_wert(pb[0]["wert"]), ("1.000,00 €", "1000,00"))


@case
def test_checkliste_zeigt_formatiert_und_kopiert_formularfertig():
    mapping = _mapping_mit_arten()
    r = fixture(elster_mapping=mapping)
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    daten = json.loads(re.search(
        r'<script id="zeilen-daten"[^>]*>(.*?)</script>', h, re.S).group(1))
    lohn = next(d for d in daten if d["bezeichnung"] == "Bruttoarbeitslohn")
    eq(lohn["anzeige"], "63.230,00 €", "lesbare Anzeige")
    eq(lohn["kopie"], "63230,00", "kopiert wird die Formularfassung")
    eq(lohn["wert"], "63230.00", "der Rohwert bleibt für den localStorage-Schlüssel erhalten")
    contains(h, "z.kopie", "die Schaltfläche kopiert den Formularwert, nicht die Anzeige")


@case
def test_checkliste_kopieren_hat_fallback_und_rueckmeldung():
    """Auf einer lokal geöffneten Datei (file://) ist navigator.clipboard nicht
    garantiert — ohne Fallback und ohne Rückmeldung passiert beim Klick
    scheinbar nichts."""
    r = fixture(elster_mapping=_mapping_mit_arten())
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    contains(h, "navigator.clipboard", "bevorzugter Weg")
    contains(h, "execCommand('copy')", "Fallback für ältere/blockierte Browser")
    contains(h, "Kopiert ✓", "Erfolgsrückmeldung")
    contains(h, "Strg+C", "Hinweis, wenn beides scheitert")


@case
def test_checkliste_gruppiert_nach_anlage_und_bietet_filter():
    r = fixture(elster_mapping=_mapping_mit_arten())
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    contains(h, 'id="gruppen"', "Gruppen-Container statt einer flachen Tabelle")
    contains(h, "nur-offen-box", "Filter für offene Zeilen")
    contains(h, "prefers-color-scheme: light", "Hellmodus")
    contains(h, "@media print", "Druck-Stylesheet wie im Dashboard")


@case
def test_erklaeren_knopf_an_jedem_hinweis():
    """Je Hinweis eine Schaltfläche, die den Hinweis samt fertiger Frage in die
    Zwischenablage legt — in beiden HTML-Ausgaben."""
    r = fixture(elster_mapping=_mapping_mit_arten())
    r["hinweise"] = ["Anlage KAP: die Verlustzeilen 22–25 sind davon-Zeilen.",
                     "Freigrenze § 23 knapp überschritten."]
    rc, outdir, _o, _e = _export(r, formats=("html", "checkliste"))
    eq(rc, 0)
    for text, wo in ((_html_text(outdir), "Dashboard"),
                     (_checkliste_text(outdir), "Checkliste")):
        # je Disclaimer- UND Hinweis-Zeile ein Knopf
        knoepfe = text.count('class="erklaeren"')
        eq(knoepfe, len(DISCLAIMER) + len(r["hinweise"]), f"{wo}: Knöpfe je Hinweis")
        contains(text, "Für Claude kopieren", wo)
        contains(text, "Erkläre mir bitte diesen Hinweis", f"{wo}: fertige Frage")
        contains(text, "davon-Zeilen", f"{wo}: Hinweistext selbst steht mit drin")


@case
def test_erklaeren_frage_ist_im_attribut_escaped():
    """Der Hinweistext kommt aus dem Report und kann Anführungszeichen
    enthalten — im data-Attribut darf er das Markup nicht aufbrechen."""
    r = fixture(elster_mapping=_mapping_mit_arten())
    r["hinweise"] = ['Achtung: "Termingeschäfte" & <b>Aktien</b> getrennt führen']
    rc, outdir, _o, _e = _export(r, formats=("html",))
    eq(rc, 0)
    h = _html_text(outdir)
    missing(h, '<b>Aktien</b>', "roher Hinweistext im Markup")
    contains(h, "&quot;Termingesch", "Anführungszeichen escaped")
    contains(h, "&lt;b&gt;Aktien", "Tags escaped")


@case
def test_checkliste_skript_verschickt_nichts():
    r = fixture(elster_mapping=_mapping_mit_arten())
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    for verboten in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon",
                     "location.href", "window.open", "<form", "new Image("):
        missing(h, verboten, "die Checkliste darf nichts übertragen")


@case
def test_checkliste_ohne_eintragen_zeilen_zeigt_leere_liste_statt_absturz():
    r = fixture(elster_mapping=[
        {"anlage": "—", "zeile": "—", "bezeichnung": "x", "wert": "", "art": "trenner"},
    ])
    rc, outdir, _o, _e = _export(r, formats=("checkliste",))
    eq(rc, 0)
    h = _checkliste_text(outdir)
    contains(h, '"zeilen-daten"', "leeres Datenscript statt Absturz")


# ── PDF (optional) ───────────────────────────────────────────────────────────
def _fpdf_da() -> bool:
    try:
        ex._import_fpdf()
        return True
    except Exception:
        return False


@case
def test_pdf_wenn_fpdf2_vorhanden():
    if not _fpdf_da():
        print("       (übersprungen: fpdf2 nicht installiert)")
        return
    r = fixture()
    # viele Zeilen erzwingen den Seitenumbruch (Kopfzeile muss sich wiederholen)
    r["krypto_detail"]["paragraph_23"]["disposals"] *= 20
    r["elster_mapping"] *= 20
    rc, outdir, _o, err = _export(r, formats=("pdf",))
    eq(rc, 0, f"PDF-Export fehlgeschlagen: {err}")
    pdfs = list(outdir.glob("taxreport_*.pdf"))
    assert pdfs and pdfs[0].stat().st_size > 1000, "PDF ist leer"
    assert pdfs[0].read_bytes().startswith(b"%PDF"), "keine PDF-Signatur"


@case
def test_pdf_unicode_warnung_nur_ohne_font():
    """Ohne Unicode-Font muss gewarnt werden, welche Zeichen ersetzt wurden."""
    t = ex._Text(False)
    eq(t("Ayşe Öztürk"), "Ay?e Öztürk", "latin-1-Fallback")
    w = t.warnung()
    assert w and "U+015F" in w, f"Warnung nennt das ersetzte Zeichen nicht: {w}"
    t2 = ex._Text(True)
    eq(t2("Ayşe Öztürk"), "Ayşe Öztürk", "mit Unicode-Font bleibt der Name korrekt")
    eq(t2.warnung(), None, "keine Warnung, wenn nichts ersetzt wurde")


@case
def test_pdf_latin1_fallback_erzeugt_pdf_und_warnt():
    """Ohne Font muss der € /Umlaut-Pfad weiter funktionieren — aber laut."""
    if not _fpdf_da():
        print("       (übersprungen: fpdf2 nicht installiert)")
        return
    orig = ex._finde_unicode_font
    ex._finde_unicode_font = lambda: None
    try:
        rc, outdir, _o, err = _export(fixture(), formats=("pdf",))
    finally:
        ex._finde_unicode_font = orig
    eq(rc, 0, f"PDF-Export fehlgeschlagen: {err}")
    pdfs = list(outdir.glob("taxreport_*.pdf"))
    assert pdfs and pdfs[0].read_bytes().startswith(b"%PDF"), "kein PDF erzeugt"
    contains(err, "WARNUNG", "stille Namensverstümmelung")
    contains(err, "U+015F", "ersetztes Zeichen benannt")


@case
def test_altes_fpdf_wird_erkannt():
    """fpdf 1.7.2 importiert, stirbt aber an new_x= — das muss vorher auffallen."""
    eq(ex._version_tuple("2.8.8"), (2, 8, 8))
    eq(ex._version_tuple("1.7.2"), (1, 7, 2))
    eq(ex._version_tuple("2.7.0b1"), (2, 7, 0))
    assert ex._version_tuple("1.7.2")[0] < 2, "Alt-fpdf muss als < 2 gelten"


@case
def test_pdf_nutzt_unicode_font_wenn_vorhanden():
    if not _fpdf_da():
        print("       (übersprungen: fpdf2 nicht installiert)")
        return
    font = ex._finde_unicode_font()
    if not font:
        print("       (übersprungen: keine DejaVu-TTF auf diesem System)")
        return
    rc, outdir, _o, err = _export(fixture(), formats=("pdf",))
    eq(rc, 0, f"PDF-Export fehlgeschlagen: {err}")
    missing(err, "WARNUNG", "mit eingebettetem Font darf nicht gewarnt werden")


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
