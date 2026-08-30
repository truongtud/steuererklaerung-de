#!/usr/bin/env python3
"""Tests für den --kap-result-Pfad von scripts/build_taxreport.py.

Ausführen: python3 tests/test_kap.py

Die Fixtures werden hier inline gebaut (Schema `kap` aus references/broker-profile.md)
— bewusst OHNE parse_broker.py: geprüft wird der Report-Bauer, nicht der Parser.
"""
import contextlib
import io
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
    sd = {
        "steuerjahr": 2024,
        "zusammenveranlagung": False,
        "steuerpflichtiger": {"name": "Test", "verheiratet": False},
        "anlage_n": {"bruttoarbeitslohn": "0", "lohnsteuer": "0", "soli": "0",
                     "kirchensteuer": "0", "werbungskosten": {}},
        "anlage_kap": {},
        "anlage_so": {},
        "anlage_v": {}, "anlage_s": {}, "anlage_g": {},
        "vorsorge": {}, "sonderausgaben": {},
        "aussergewoehnliche_belastungen": {}, "kinder": [],
    }
    sd.update(over)
    return sd


def kap_quelle(*, quelle="Musterbank Steuerbescheinigung", jahr=2024,
               profil="musterbank-de", kap_zeilen=None, warnungen=None,
               elster_extra=None, **kennzahlen):
    """Quell-JSON nach dem Ausgabeschema `kap` (references/broker-profile.md).

    Verluste tragen laut Contract ein negatives Vorzeichen — die Aufrufer unten
    schreiben sie auch so.
    """
    kz = {"kapitalertraege": "0.00", "gewinn_aktien": "0.00",
          "gewinn_termingeschaefte": "0.00", "verlust_aktien": "0.00",
          "verlust_termingeschaefte": "0.00", "verluste_ohne_aktien": "0.00",
          "verluste_ausfall": "0.00", "anrechenbare_kest": "0.00",
          "einbehaltener_soli": "0.00", "einbehaltene_kirchensteuer": "0.00",
          "auslaendische_quellensteuer": "0.00", "fiktive_quellensteuer": "0.00"}
    kz.update({k: str(v) for k, v in kennzahlen.items()})
    return {
        "steuerjahr": jahr,
        "quelle": quelle,
        "profil": profil,
        "kap_zeilen": dict(kap_zeilen or {}),
        "kennzahlen": kz,
        "warnungen": list(warnungen or []),
        "elster_extra": list(elster_extra or []),
    }


def bau(sd, kap=None, krypto=None):
    return bt.build(sd, krypto if krypto is not None else [], kap)


def kap_block(report):
    return report["anlagen"]["KAP"]


def zeilen(report, anlage="Anlage KAP"):
    return [r for r in report["elster_mapping"] if r["anlage"] == anlage]


def k_zeilen(report):
    """Die wörtliche Abschrift der Bescheinigung (anlagen.KAP.kap_zeilen)."""
    return report["anlagen"]["KAP"]["kap_zeilen"]


def schreibe(tmp, name, obj):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return p


# ── Eine Quelle fließt in die Steuerwerte ────────────────────────────────────
@case
def test_eine_quelle_fliesst_in_die_steuerwerte():
    q = kap_quelle(kapitalertraege="4000.00", anrechenbare_kest="750.00",
                   einbehaltener_soli="41.25")
    r = bau(steuerdaten(), [q])
    k = kap_block(r)
    eq(k["kapitalertraege"], "4000.00", "Kapitalerträge aus der Datei")
    eq(k["anrechenbare_kest"], "750.00")
    eq(k["einbehaltener_soli"], "41.25")
    # 4.000 − 1.000 Sparer-Pauschbetrag = 3.000 × 25 % = 750,00 €
    eq(k["nach_pauschbetrag"], "3000.00")
    eq(k["abgeltungsteuer"], "750.00", "Abgeltungsteuer auf die Datei-Erträge")
    eq(r["berechnung"]["abgeltungsteuer_kap"], "750.00")


@case
def test_quelle_erscheint_in_der_aufschluesselung_und_in_meta():
    q = kap_quelle(quelle="Trade Republic", profil="trade-republic-de",
                   kapitalertraege="1234.56")
    r = bau(steuerdaten(), [q])
    quellen = kap_block(r)["quellen"]
    arten = [x["art"] for x in quellen]
    assert "datei" in arten, f"keine Datei-Quelle in der Aufschlüsselung: {quellen}"
    datei = [x for x in quellen if x["art"] == "datei"][0]
    eq(datei["quelle"], "Trade Republic")
    eq(datei["profil"], "trade-republic-de")
    eq(datei["kennzahlen"]["kapitalertraege"], "1234.56")
    meta = r["meta"]["kap_quellen"]
    assert any(m["quelle"] == "Trade Republic" for m in meta), \
        f"KAP-Quelle fehlt in meta.kap_quellen: {meta}"


# ── Zwei Quellen werden addiert ──────────────────────────────────────────────
@case
def test_zwei_quellen_werden_addiert():
    a = kap_quelle(quelle="Depot A", kapitalertraege="3000.00", anrechenbare_kest="500.00")
    b = kap_quelle(quelle="Depot B", kapitalertraege="2500.50", anrechenbare_kest="250.25")
    r = bau(steuerdaten(), [a, b])
    k = kap_block(r)
    eq(k["kapitalertraege"], "5500.50", "Summe beider Depots")
    eq(k["anrechenbare_kest"], "750.25")
    eq(k["aus_dateien"]["kapitalertraege"], "5500.50")
    eq(k["aus_handeingabe"]["kapitalertraege"], "0.00")
    dateien = [x for x in k["quellen"] if x["art"] == "datei"]
    eq(len(dateien), 2, "beide Quellen müssen einzeln ausgewiesen sein")
    eq(sorted(x["quelle"] for x in dateien), ["Depot A", "Depot B"])


# ── Verlusttöpfe: EINMAL auf der Summe, nicht je Depot ───────────────────────
@case
def test_aktienverlust_der_einen_quelle_netzt_gegen_den_gewinn_der_anderen():
    """Der Kernfall: Aktiengewinn in Depot A, Aktienverlust in Depot B.

    Je Quelle gerechnet bliebe der Verlust aus B im Verlustvortrag hängen und
    A würde voll versteuert. § 20 Abs. 6 Satz 4 EStG gilt aber personenbezogen
    über alle Depots — verrechnet wird deshalb einmal auf der Summe.
    """
    a = kap_quelle(quelle="Depot A", kapitalertraege="5000.00", gewinn_aktien="5000.00")
    # Depot B weist seinen Saldo aus, in dem der Aktienverlust bereits steckt
    # (Z. 23 ist eine Davon-Zeile zu Z. 7/18/19) — deshalb kapitalertraege = -3.000.
    b = kap_quelle(quelle="Depot B", kapitalertraege="-3000.00", verlust_aktien="-3000.00")
    r = bau(steuerdaten(), [a, b])
    k = kap_block(r)
    eq(k["gewinn_aktien"], "5000.00")
    eq(k["verlust_aktien"], "3000.00")
    eq(k["verlust_aktien_verrechnet"], "3000.00",
       "Aktienverlust aus Depot B muss gegen den Gewinn aus Depot A laufen")
    eq(k["verlustvortraege"]["aktien"], "0.00",
       "nichts darf im Aktien-Verlusttopf hängenbleiben")
    eq(k["netto_kapitalertraege"], "2000.00")
    eq(k["abgeltungsteuer"], "250.00", "(5000 − 3000 − 1000 Sparer-PB) × 25 %")


@case
def test_termingeschaeftsverlust_netzt_ueber_depots_hinweg():
    """Seit JStG 2024 unbeschränkt verrechenbar — aber ebenfalls erst auf der Summe."""
    a = kap_quelle(quelle="Depot A", kapitalertraege="10000.00")
    b = kap_quelle(quelle="Depot B", kapitalertraege="-4000.00",
                   verlust_termingeschaefte="-4000.00")
    k = kap_block(bau(steuerdaten(), [a, b]))
    eq(k["verlust_termingeschaefte"], "4000.00")
    eq(k["verlust_termingeschaefte_verrechnet"], "4000.00")
    eq(k["verlustvortraege"]["termingeschaefte"], "0.00")
    eq(k["netto_kapitalertraege"], "6000.00")


@case
def test_verlust_ohne_gegengewinn_bleibt_vortragsfaehig():
    """Ohne Aktiengewinn darf der Aktienverlust NICHT verrechnet werden.

    Der Saldo der Bescheinigung (2.000 €) hat den Aktienverlust bereits abgezogen —
    5.000 € übrige Erträge − 3.000 € Aktienverlust. § 20 Abs. 6 Satz 4 EStG erlaubt
    das nicht: der Verlust wird der Bemessungsgrundlage wieder HINZUGERECHNET und
    in den Aktien-Verlusttopf gestellt. Die Verlustzeile mindert die Steuer damit
    kein zweites Mal — sie ordnet nur zu.
    """
    b = kap_quelle(quelle="Depot B", kapitalertraege="2000.00", verlust_aktien="-3000.00")
    r = bau(steuerdaten(), [b])
    k = kap_block(r)
    eq(k["verlust_aktien_verrechnet"], "0.00")
    eq(k["verlustvortraege"]["aktien"], "3000.00",
       "Aktienverluste nur gegen Aktiengewinne (§ 20 Abs. 6 Satz 4 EStG)")
    eq(k["verlust_aktien_ueberhang_hinzugerechnet"], "3000.00",
       "der ringfenced Verlust wird dem Saldo wieder zugeschlagen")
    eq(k["kapitalertraege_nach_aktien_hinzurechnung"], "5000.00")
    eq(k["netto_kapitalertraege"], "5000.00")
    assert any("ANNAHME zur Anlage KAP" in h for h in r["hinweise"]), \
        f"die Davon-Zeilen-Annahme muss im Report stehen: {r['hinweise']}"


@case
def test_positives_vorzeichen_bei_verlusten_wird_gemeldet_aber_richtig_gerechnet():
    a = kap_quelle(quelle="Depot A", kapitalertraege="5000.00", gewinn_aktien="5000.00")
    b = kap_quelle(quelle="Depot B", verlust_aktien="3000.00")  # Contract: negativ
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), [a, b])
    eq(kap_block(r)["verlust_aktien"], "3000.00",
       "ein positiv notierter Verlust darf den Verlust der anderen Quelle nicht aufheben")
    eq(kap_block(r)["verlust_aktien_verrechnet"], "3000.00")
    assert any("negatives" in w and "verlust_aktien" in w for w in r["warnungen"]), \
        f"Vorzeichen-Warnung fehlt: {r['warnungen']}"


# ── Gewinne aus Termingeschäften (Z. 21) — Davon-Zeile ──────────────────────
@case
def test_gewinn_termingeschaefte_ist_eine_davon_zeile():
    """Anlage KAP Z. 20–25 stehen unter der Überschrift „In den Zeilen 18 und 19
    enthaltene …" bzw. „In Zeile 7 enthaltene …". Zeile 21 weist die Termin-
    geschäftsgewinne also nur ihrem Verrechnungskreis zu; sie sind im Gesamtbetrag
    bereits enthalten. Die Regel, die hier gilt: die Davon-Zeile ändert die
    Bemessungsgrundlage NICHT — sonst würde jeder Gewinn, den eine Steuer-
    bescheinigung ohnehin schon in Zeile 7 ausweist, ein zweites Mal versteuert.
    """
    ohne = kap_block(bau(steuerdaten(), [kap_quelle(kapitalertraege="5000.00")]))
    mit = kap_block(bau(steuerdaten(), [kap_quelle(
        kapitalertraege="5000.00", gewinn_termingeschaefte="4000.00")]))
    eq(mit["gewinn_termingeschaefte"], "4000.00", "die Davon-Zeile wird ausgewiesen")
    for feld in ("netto_kapitalertraege", "nach_pauschbetrag", "abgeltungsteuer"):
        eq(mit[feld], ohne[feld],
           f"'{feld}' darf sich durch die Davon-Zeile nicht ändern")
    eq(mit["abgeltungsteuer"], "1000.00", "(5000 − 1000 Sparer-PB) × 25 %")


@case
def test_gewinn_termingeschaefte_ueber_den_kapitalertraegen_wird_gemeldet():
    """Eine Davon-Zeile kann ihren Gesamtbetrag nicht übersteigen. Wenn doch,
    fehlt der Gewinn in 'kapitalertraege' und bliebe unversteuert — das muss die
    Quelle bzw. ihr Profil korrigieren, nicht der Report-Bauer erraten."""
    q = kap_quelle(kapitalertraege="1000.00", gewinn_termingeschaefte="4000.00")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), [q])
    eq(kap_block(r)["netto_kapitalertraege"], "1000.00",
       "die Bemessungsgrundlage bleibt trotzdem bei den erklärten Kapitalerträgen")
    treffer = [w for w in r["warnungen"] if "gewinn_termingeschaefte" in w]
    assert treffer, f"unstimmige Davon-Zeile nicht gemeldet: {r['warnungen']}"
    assert "Davon-Zeile" in treffer[0] and "unversteuert" in treffer[0], treffer[0]


@case
def test_termingeschaeftsgewinn_und_verlust_aus_zwei_quellen_netten():
    """Gewinn in Depot A, Verlust in Depot B — verrechnet wird einmal auf der Summe.

    Depot A weist den Gewinn so aus, wie eine Steuerbescheinigung es tut: im
    Gesamtbetrag (Z. 7) UND in der Davon-Zeile (Z. 21).
    """
    a = kap_quelle(quelle="Depot A", kapitalertraege="8000.00",
                   gewinn_termingeschaefte="8000.00")
    b = kap_quelle(quelle="Depot B", kapitalertraege="-3000.00",
                   verlust_termingeschaefte="-3000.00")
    k = kap_block(bau(steuerdaten(), [a, b]))
    eq(k["gewinn_termingeschaefte"], "8000.00")
    eq(k["verlust_termingeschaefte"], "3000.00")
    eq(k["verlust_termingeschaefte_verrechnet"], "3000.00",
       "der Verlust aus Depot B muss gegen den Gewinn aus Depot A laufen")
    eq(k["verlustvortraege"]["termingeschaefte"], "0.00")
    eq(k["netto_kapitalertraege"], "5000.00")
    eq(k["abgeltungsteuer"], "1000.00")


@case
def test_gewinn_termingeschaefte_erscheint_als_elster_zeile_21():
    q = kap_quelle(kapitalertraege="4000.00", gewinn_termingeschaefte="4000.00")
    z21 = [z for z in zeilen(bau(steuerdaten(), [q])) if z["zeile"] == "Z. 21"]
    eq(len(z21), 1)
    eq(z21[0]["wert"], "4000.00")


# ── Übrige Verluste (Z. 22) und Ausfallverluste (Z. 25) ──────────────────────
@case
def test_verluste_ohne_aktien_verrechnen_sich_unbeschraenkt():
    a = kap_quelle(quelle="Depot A", kapitalertraege="5000.00")
    b = kap_quelle(quelle="Depot B", kapitalertraege="-2000.00",
                   verluste_ohne_aktien="-2000.00")
    k = kap_block(bau(steuerdaten(), [a, b]))
    eq(k["verluste_ohne_aktien"], "2000.00")
    eq(k["verluste_ohne_aktien_verrechnet"], "2000.00",
       "Z. 22 hat keinen eigenen Verrechnungskreis")
    eq(k["netto_kapitalertraege"], "3000.00")
    eq(k["verlustvortraege"]["allgemein"], "0.00")


@case
def test_ausfallverluste_verrechnen_sich_und_werden_erlaeutert():
    # 5.000 € Bruttoerträge abzüglich 1.500 € Ausfallverlust = 3.500 € Saldo;
    # Z. 25 ist eine Davon-Zeile dazu und wird nicht ein zweites Mal abgezogen.
    q = kap_quelle(kapitalertraege="3500.00", verluste_ausfall="-1500.00")
    r = bau(steuerdaten(), [q])
    k = kap_block(r)
    eq(k["verluste_ausfall"], "1500.00")
    eq(k["verluste_ausfall_verrechnet"], "1500.00")
    eq(k["netto_kapitalertraege"], "3500.00")
    eq(k["verlustvortraege"]["allgemein"], "0.00")
    treffer = [h for h in r["hinweise"] if "Satz 6" in h and "Z. 25" in h]
    assert treffer, f"Hinweis zum aufgehobenen § 20 Abs. 6 Satz 6 fehlt: {r['hinweise']}"
    assert "Jahressteuergesetz 2024" in treffer[0], treffer[0]


@case
def test_uebersteigende_verluste_gehen_in_den_allgemeinen_verlustvortrag():
    # 1.000 € Bruttoerträge, 2.500 € (Z. 22) + 500 € (Z. 25) Verluste → Saldo −2.000 €.
    q = kap_quelle(kapitalertraege="-2000.00", verluste_ohne_aktien="-2500.00",
                   verluste_ausfall="-500.00")
    k = kap_block(bau(steuerdaten(), [q]))
    eq(k["verluste_ohne_aktien_verrechnet"], "1000.00",
       "mehr als die 1.000 € Bruttoerträge konnte der Saldo nicht aufzehren")
    eq(k["verluste_ausfall_verrechnet"], "0.00", "nach Z. 22 ist nichts mehr übrig")
    eq(k["netto_kapitalertraege"], "-2000.00",
       "der Saldo bleibt vorzeichenbehaftet — er wird nicht ein zweites Mal gemindert")
    eq(k["verlustvortraege"]["allgemein"], "2000.00", "1.500 (Z. 22) + 500 (Z. 25)")
    eq(k["abgeltungsteuer"], "0.00")


@case
def test_verluste_z22_und_z25_erscheinen_als_elster_zeilen():
    q = kap_quelle(kapitalertraege="9000.00", verluste_ohne_aktien="-1000.00",
                   verluste_ausfall="-500.00")
    zs = {z["zeile"]: z for z in zeilen(bau(steuerdaten(), [q]))}
    eq(zs["Z. 22"]["wert"], "1000.00")
    eq(zs["Z. 25"]["wert"], "500.00")


# ── Fiktive Quellensteuer (Z. 42) ────────────────────────────────────────────
@case
def test_fiktive_quellensteuer_wird_wie_die_tatsaechliche_angerechnet():
    q = kap_quelle(kapitalertraege="5000.00", auslaendische_quellensteuer="100.00",
                   fiktive_quellensteuer="100.00")
    r = bau(steuerdaten(), [q])
    k = kap_block(r)
    eq(k["auslaendische_quellensteuer"], "100.00")
    eq(k["fiktive_quellensteuer"], "100.00", "eigener Schlüssel, nicht eingemischt")
    eq(k["anrechenbare_quellensteuer_gesamt"], "200.00")
    # (5000 − 1000) × 25 % = 1000; − 200 angerechnete Quellensteuer = 800
    eq(k["abgeltungsteuer"], "800.00")
    anr = r["ergebnis"]["anrechenbare_betraege"]
    eq(anr["auslaendische_quellensteuer_nach_32d_abs5"], "200.00")
    eq(anr["davon_fiktive_quellensteuer"], "100.00")
    eq(dec(anr["summe"]), dec("0"),
       "die Quellensteuer darf NICHT zusätzlich in der Anrechnungssumme stehen")
    zs = {z["zeile"]: z for z in zeilen(r)}
    eq(zs["Z. 41"]["wert"], "100.00")
    eq(zs["Z. 42"]["wert"], "100.00", "Z. 41 und Z. 42 sind im Formular getrennt")


# ── Vorzeichenregel: kap_zeilen wörtlich, kennzahlen normiert ────────────────
@case
def test_rohzeilen_werden_nie_vorzeichenkorrigiert_und_nie_bemaengelt():
    """Deutsche Bescheinigungen drucken Verluste positiv — genau so will ELSTER sie.

    Die Vorzeichenprüfung gilt deshalb nur für 'kennzahlen', nie für 'kap_zeilen'.
    """
    q = kap_quelle(quelle="Musterbank", kapitalertraege="1234.56",
                   verlust_aktien="-500.00", verlust_termingeschaefte="-2626.00",
                   kap_zeilen={"7": "1234.56", "23": "500.00", "24": "2626.00"})
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), [q])
    assert not [w for w in r["warnungen"] if "Vorzeichen" in w], \
        f"positive Rohzeilen dürfen keine Vorzeichenwarnung auslösen: {r['warnungen']}"
    roh = {z["zeile"]: z for z in zeilen(r) if "kap_zeilen" in z["quelle"]}
    eq(roh["Z. 23"]["wert"], "500.00", "Rohzeile bleibt wörtlich stehen")
    eq(roh["Z. 24"]["wert"], "2626.00")
    # trotz umgekehrter Vorzeichen erkennt der Abgleich die Dopplung (Betragsvergleich)
    eq(len([z for z in zeilen(r) if z["zeile"] == "Z. 23"]), 1,
       "abgeleitete Zeile 23 muss als Dopplung entfallen")
    eq(len([z for z in zeilen(r) if z["zeile"] == "Z. 24"]), 1)


@case
def test_negativer_gewinn_wird_gemeldet():
    q = kap_quelle(kapitalertraege="1000.00", gewinn_termingeschaefte="-400.00")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), [q])
    assert any("gewinn_termingeschaefte" in w and "positives" in w
               for w in r["warnungen"]), f"Vorzeichen-Warnung fehlt: {r['warnungen']}"


# ── Handeingabe + Datei addieren sich ────────────────────────────────────────
@case
def test_handeingabe_und_datei_addieren_sich_statt_zu_ueberschreiben():
    sd = steuerdaten(anlage_kap={"kapitalertraege": "1000.00",
                                 "anrechenbare_kest": "100.00"})
    q = kap_quelle(quelle="Depot A", kapitalertraege="500.00", anrechenbare_kest="50.00")
    r = bau(sd, [q])
    k = kap_block(r)
    eq(k["kapitalertraege"], "1500.00", "Handeingabe darf nicht überschrieben werden")
    eq(k["anrechenbare_kest"], "150.00")
    eq(k["aus_handeingabe"]["kapitalertraege"], "1000.00")
    eq(k["aus_dateien"]["kapitalertraege"], "500.00")
    hand = [x for x in k["quellen"] if x["art"] == "handeingabe"]
    eq(len(hand), 1, "die Handeingabe muss als eigene Quelle sichtbar sein")
    eq(hand[0]["kennzahlen"]["kapitalertraege"], "1000.00")
    assert any("ADDIERT" in h for h in r["hinweise"]), \
        f"Hinweis auf mögliche Doppelerfassung fehlt: {r['hinweise']}"


@case
def test_neue_kennzahlen_lassen_sich_auch_von_hand_tippen():
    """Die vier neuen Schlüssel müssen in der 'anlage_kap'-Whitelist stehen —
    sonst meldet die Eingabeprüfung sie als unbekannt und ignoriert den Betrag."""
    sd = steuerdaten(anlage_kap={
        "kapitalertraege": "3000.00", "gewinn_termingeschaefte": "2000.00",
        "verluste_ohne_aktien": "-300.00", "verluste_ausfall": "-200.00",
        "fiktive_quellensteuer": "50.00"})
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(sd)
    eq(r["eingabepruefung"]["unbekannte_felder"], [],
       "die neuen Kennzahlen dürfen nicht als unbekanntes Feld gelten")
    k = kap_block(r)
    eq(k["gewinn_termingeschaefte"], "2000.00")
    eq(k["verluste_ohne_aktien"], "300.00", "Handeingabe wird ebenfalls auf -abs() normiert")
    eq(k["verluste_ausfall"], "200.00")
    eq(k["fiktive_quellensteuer"], "50.00")
    # DIE REGEL, nicht die Arithmetik: Z. 21 (Gewinn), Z. 22 und Z. 25 (Verluste)
    # stehen im Formular unter EINER Überschrift („In den Zeilen 18 und 19
    # enthaltene …"). Sie werden deshalb alle gleich behandelt — keine von ihnen
    # verschiebt die Bemessungsgrundlage. 'kapitalertraege' ist der Saldo, der die
    # 300 € und die 200 € bereits enthält; ein zweiter Abzug wäre eine doppelte
    # Berücksichtigung desselben Verlustes.
    ohne = kap_block(bau(steuerdaten(anlage_kap={"kapitalertraege": "3000.00"})))
    eq(k["netto_kapitalertraege"], ohne["netto_kapitalertraege"],
       "Z. 22 und Z. 25 sind Davon-Zeilen wie Z. 21 — sie mindern den Saldo nicht erneut")
    eq(k["netto_kapitalertraege"], "3000.00")
    eq(k["verluste_ohne_aktien_verrechnet"], "300.00",
       "verrechnet wurden sie trotzdem — nämlich bereits im Saldo")
    eq(k["verluste_ausfall_verrechnet"], "200.00")
    eq(k["verlustvortraege"]["allgemein"], "0.00")


@case
def test_neue_kennzahl_aus_datei_und_handeingabe_addiert_sich():
    sd = steuerdaten(anlage_kap={"kapitalertraege": "5000.00",
                                 "gewinn_termingeschaefte": "1000.00"})
    q = kap_quelle(gewinn_termingeschaefte="1500.00")
    k = kap_block(bau(sd, [q]))
    eq(k["gewinn_termingeschaefte"], "2500.00")
    eq(k["aus_handeingabe"]["gewinn_termingeschaefte"], "1000.00")
    eq(k["aus_dateien"]["gewinn_termingeschaefte"], "1500.00")


@case
def test_ohne_kap_quellen_bleibt_alles_beim_alten():
    sd = steuerdaten(anlage_kap={"kapitalertraege": "2000.00", "verlust_aktien": "-500.00",
                                 "gewinn_aktien": "800.00"})
    k = kap_block(bau(sd))
    eq(k["kapitalertraege"], "2000.00")
    eq(k["verlust_aktien"], "500.00")
    eq(k["verlust_aktien_verrechnet"], "500.00")
    eq(k["aus_dateien"]["kapitalertraege"], "0.00")


# ── Einbehaltene Steuern erreichen die Abrechnung ────────────────────────────
@case
def test_einbehaltene_steuern_erreichen_nachzahlung_und_erstattung():
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "40000", "lohnsteuer": "6000",
                               "soli": "0", "kirchensteuer": "0"})
    q = kap_quelle(kapitalertraege="10000.00", anrechenbare_kest="2250.00",
                   einbehaltener_soli="123.75", einbehaltene_kirchensteuer="45.00")
    r = bau(sd, [q])
    anr = r["ergebnis"]["anrechenbare_betraege"]
    eq(anr["anrechenbare_kapitalertragsteuer"], "2250.00")
    eq(anr["soli_auf_kapitalertraege_einbehalten"], "123.75")
    eq(anr["kirchensteuer_auf_kapitalertraege_einbehalten"], "45.00")
    eq(anr["solidaritaetszuschlag_einbehalten"], "123.75",
       "Lohn-Soli 0 + KAP-Soli 123,75")
    eq(anr["kirchensteuer_einbehalten"], "45.00")
    eq(dec(anr["summe"]),
       dec(anr["lohnsteuer"]) + dec(anr["solidaritaetszuschlag_einbehalten"])
       + dec(anr["kirchensteuer_einbehalten"])
       + dec(anr["anrechenbare_kapitalertragsteuer"]),
       "Anrechnungssumme muss die einbehaltenen KAP-Steuern enthalten")
    eq(dec(anr["summe"]), dec("6000") + dec("123.75") + dec("45.00") + dec("2250.00"))
    eq(dec(r["ergebnis"]["saldo"]),
       dec(r["ergebnis"]["steuer_festsetzung_gesamt"]) - dec(anr["summe"]),
       "Saldo ≠ Festsetzung − Anrechnung")


@case
def test_einbehaltene_kest_aus_zwei_quellen_summiert_sich_in_der_anrechnung():
    sd = steuerdaten(anlage_n={"bruttoarbeitslohn": "40000", "lohnsteuer": "6000"})
    a = kap_quelle(quelle="Depot A", kapitalertraege="4000.00", anrechenbare_kest="900.00")
    b = kap_quelle(quelle="Depot B", kapitalertraege="4000.00", anrechenbare_kest="900.00")
    r = bau(sd, [a, b])
    eq(r["ergebnis"]["anrechenbare_betraege"]["anrechenbare_kapitalertragsteuer"],
       "1800.00")


@case
def test_auslaendische_quellensteuer_mindert_die_abgeltungsteuer():
    """§ 32d Abs. 5 EStG: Anrechnung IN der Abgeltungsteuer, nicht zusätzlich daneben."""
    q = kap_quelle(kapitalertraege="5000.00", auslaendische_quellensteuer="200.00")
    r = bau(steuerdaten(), [q])
    k = kap_block(r)
    eq(k["auslaendische_quellensteuer"], "200.00")
    # (5000 − 1000) × 25 % = 1000; − 200 Quellensteuer = 800
    eq(k["abgeltungsteuer"], "800.00")
    anr = r["ergebnis"]["anrechenbare_betraege"]
    eq(anr["auslaendische_quellensteuer_nach_32d_abs5"], "200.00")
    eq(dec(anr["summe"]), dec("0"),
       "die Quellensteuer darf NICHT zusätzlich in der Anrechnungssumme stehen")


# ── ELSTER-Zeilen ────────────────────────────────────────────────────────────
@case
def test_kap_zeilen_erscheinen_als_elster_zeilen_mit_quelle():
    q = kap_quelle(quelle="Musterbank", kapitalertraege="1234.56",
                   anrechenbare_kest="300.00",
                   kap_zeilen={"7": "1234.56", "37": "300.00", "19": "820.00"})
    r = bau(steuerdaten(), [q])
    roh = [z for z in zeilen(r) if "kap_zeilen" in z["quelle"]]
    eq(sorted(z["zeile"] for z in roh), ["Z. 19", "Z. 37", "Z. 7"])
    for z in roh:
        eq(z["quelle"], "Musterbank (kap_zeilen)", f"Quelle an Zeile {z['zeile']}")
    z7 = [z for z in roh if z["zeile"] == "Z. 7"][0]
    eq(z7["wert"], "1234.56", "Rohzeile muss wörtlich durchgereicht werden")
    z19 = [z for z in roh if z["zeile"] == "Z. 19"][0]
    eq(z19["wert"], "820.00",
       "auch eine Zeile ohne abgeleitetes Gegenstück gehört ins Mapping")


@case
def test_rohzeile_verdraengt_die_deckungsgleiche_abgeleitete_zeile():
    q = kap_quelle(quelle="Musterbank", kapitalertraege="1234.56",
                   kap_zeilen={"7": "1234.56"})
    r = bau(steuerdaten(), [q])
    z7 = [z for z in zeilen(r) if z["zeile"] == "Z. 7"]
    eq(len(z7), 1, "Zeile 7 darf nicht doppelt im Mapping stehen")
    eq(z7[0]["quelle"], "Musterbank (kap_zeilen)", "es gilt die Rohzeile")
    assert any("Rohzeile" in h and "Zeile 7" in h for h in r["hinweise"]), \
        f"Hinweis auf den Vorrang der Rohzeile fehlt: {r['hinweise']}"


@case
def test_bei_zwei_quellen_bleibt_die_summenzeile_stehen():
    a = kap_quelle(quelle="Depot A", kapitalertraege="1000.00", kap_zeilen={"7": "1000.00"})
    b = kap_quelle(quelle="Depot B", kapitalertraege="500.00", kap_zeilen={"7": "500.00"})
    r = bau(steuerdaten(), [a, b])
    z7 = [z for z in zeilen(r) if z["zeile"] == "Z. 7"]
    eq(len(z7), 3, "zwei Rohzeilen plus die Summe, die ELSTER tatsächlich sehen will")
    summen = [z for z in z7 if "kap_zeilen" not in z["quelle"]]
    eq(len(summen), 1)
    eq(summen[0]["wert"], "1500.00")
    assert any("Zeile 7" in h and "Summe" in h for h in r["hinweise"]), \
        f"Hinweis zur Summenbildung fehlt: {r['hinweise']}"


@case
def test_handeingabe_neben_rohzeile_laesst_die_summenzeile_stehen():
    sd = steuerdaten(anlage_kap={"kapitalertraege": "100.00"})
    q = kap_quelle(quelle="Musterbank", kapitalertraege="1000.00",
                   kap_zeilen={"7": "1000.00"})
    r = bau(sd, [q])
    z7 = [z for z in zeilen(r) if z["zeile"] == "Z. 7"]
    eq(len(z7), 2, "Rohzeile und abweichende Summe müssen beide sichtbar bleiben")
    summe = [z for z in z7 if "kap_zeilen" not in z["quelle"]][0]
    eq(summe["wert"], "1100.00")


@case
def test_elster_extra_der_kap_quellen_wird_durchgereicht():
    q = kap_quelle(quelle="Musterbank", kapitalertraege="100.00", elster_extra=[
        {"anlage": "Anlage KAP-INV", "zeile": "Z. 4", "bezeichnung": "Investmenterträge",
         "wert": "42.00", "quelle": "Musterbank (elster_extra)"}])
    r = bau(steuerdaten(), [q])
    inv = zeilen(r, "Anlage KAP-INV")
    eq(len(inv), 1)
    eq(inv[0]["wert"], "42.00")
    eq(inv[0]["quelle"], "Musterbank (elster_extra)")


@case
def test_warnungen_der_kap_quellen_landen_im_report():
    q = kap_quelle(quelle="Musterbank", kapitalertraege="100.00",
                   warnungen=["Zeile 12 konnte nicht zugeordnet werden."])
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), [q])
    assert "Zeile 12 konnte nicht zugeordnet werden." in r["warnungen"], r["warnungen"]


@case
def test_unbekannte_kennzahl_wird_gemeldet_statt_still_verworfen():
    q = kap_quelle(quelle="Musterbank", kapitalertraege="100.00")
    q["kennzahlen"]["kapitalertraeger"] = "9999.00"   # Tippfehler
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), [q])
    eq(kap_block(r)["kapitalertraege"], "100.00", "der Tippfehler darf nicht mitgerechnet werden")
    treffer = [w for w in r["warnungen"] if "kapitalertraeger" in w]
    assert treffer, f"unbekannte Kennzahl wurde nicht gemeldet: {r['warnungen']}"
    assert "kapitalertraege" in treffer[0], f"kein Korrekturvorschlag: {treffer[0]}"


@case
def test_beinahetreffer_auf_eine_neue_kennzahl_bekommt_einen_vorschlag():
    q = kap_quelle(quelle="Musterbank", kapitalertraege="100.00")
    q["kennzahlen"]["gewinn_termingeschaeft"] = "5000.00"   # Singular statt Plural
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), [q])
    eq(kap_block(r)["gewinn_termingeschaefte"], "0.00",
       "der Tippfehler darf nicht mitgerechnet werden")
    treffer = [w for w in r["warnungen"] if "gewinn_termingeschaeft'" in w]
    assert treffer, f"Beinahetreffer nicht gemeldet: {r['warnungen']}"
    assert "gewinn_termingeschaefte" in treffer[0], f"kein Vorschlag: {treffer[0]}"


# ── Dateien, die beide Hälften tragen ────────────────────────────────────────

def krypto_haelfte(*, jahr=2024, netto="1500.00", p22="0.00"):
    """Die § 23/§ 22-Hälfte, wie die Broker-Profile sie in dieselbe JSON schreiben."""
    return {
        "paragraph_23": {
            "gewinn_eur": netto, "verlust_eur": "0.00", "netto_ergebnis_eur": netto,
            "steuerpflichtiger_betrag_eur": "0.00", "verlustvortrag_eur": "0.00",
            "freigrenze_angewendet": False},
        "paragraph_22_nr3": {"summe_eur": p22, "steuerpflichtig_eur": "0.00",
                             "freigrenze_angewendet": False},
        "steuerfrei_langfristig_eur": "0.00",
    }


def beide_haelften(**over):
    q = kap_quelle(quelle="eToro", kapitalertraege="1000.00",
                   anrechenbare_kest="700.00", **over)
    q.update(krypto_haelfte())
    return q


@case
def test_kap_datei_mit_krypto_teil_meldet_die_ignorierte_haelfte():
    """Nur --kap-result: die § 23-Hälfte wird nicht gelesen — das MUSS auffallen."""
    with tempfile.TemporaryDirectory() as tmp:
        p = schreibe(tmp, "etoro.kap_result.json", beide_haelften())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = bau(steuerdaten(), bt.lade_kap_quellen([p]))
        eq(kap_block(r)["anrechenbare_kest"], "700.00", "die KAP-Hälfte wird gelesen")
        eq(r["anlagen"]["SO"]["krypto_23_steuerpflichtig"], "0.00")
        treffer = [w for w in r["warnungen"] if "etoro.kap_result.json" in w]
        assert treffer, f"ignorierte Krypto-Hälfte nicht gemeldet: {r['warnungen']}"
        assert "§ 23" in treffer[0] and "--krypto-result" in treffer[0], treffer[0]


@case
def test_krypto_datei_mit_kap_teil_meldet_die_ignorierte_haelfte():
    with tempfile.TemporaryDirectory() as tmp:
        p = schreibe(tmp, "etoro.kap_result.json", beide_haelften())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = bau(steuerdaten(), krypto=bt.lade_krypto_quellen([p]))
        eq(kap_block(r)["anrechenbare_kest"], "0.00", "die KAP-Hälfte fehlt hier")
        treffer = [w for w in r["warnungen"] if "etoro.kap_result.json" in w]
        assert treffer, f"ignorierte KAP-Hälfte nicht gemeldet: {r['warnungen']}"
        assert "Kapitalertragsteuer" in treffer[0] and "--kap-result" in treffer[0], \
            treffer[0]


@case
def test_dieselbe_datei_in_beiden_listen_verbraucht_jede_haelfte_genau_einmal():
    """Der saubere Weg: beide Leser bekommen die Datei, jeder nimmt seine Hälfte.

    Idempotenz-Nachweis: die KAP-Zahlen sind identisch mit dem reinen
    --kap-result-Lauf, die § 23-Zahlen identisch mit dem reinen --krypto-result-Lauf,
    und die Warnung über eine ignorierte Hälfte entfällt.
    """
    with tempfile.TemporaryDirectory() as tmp:
        p = schreibe(tmp, "etoro.kap_result.json", beide_haelften())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            nur_kap = bau(steuerdaten(), bt.lade_kap_quellen([p]))
            nur_krypto = bau(steuerdaten(), krypto=bt.lade_krypto_quellen([p]))
            beide = bau(steuerdaten(), bt.lade_kap_quellen([p]),
                        krypto=bt.lade_krypto_quellen([p]))
        eq(kap_block(beide)["kapitalertraege"], kap_block(nur_kap)["kapitalertraege"],
           "die KAP-Hälfte darf durch das zweite Einlesen nicht doppelt zählen")
        eq(kap_block(beide)["anrechenbare_kest"], "700.00")
        eq(beide["krypto_detail"]["paragraph_23"]["netto_ergebnis_eur"],
           nur_krypto["krypto_detail"]["paragraph_23"]["netto_ergebnis_eur"],
           "die § 23-Hälfte darf durch das zweite Einlesen nicht doppelt zählen")
        eq(beide["krypto_detail"]["paragraph_23"]["netto_ergebnis_eur"], "1500.00")
        assert not [w for w in beide["warnungen"] if "zusätzlich mit" in w], \
            f"keine Hälfte wird ignoriert, also keine Warnung: {beide['warnungen']}"


@case
def test_dieselbe_datei_zweimal_in_einer_liste_wird_abgelehnt():
    with tempfile.TemporaryDirectory() as tmp:
        p = schreibe(tmp, "depot.kap_result.json", kap_quelle(kapitalertraege="100.00"))
        for lader, art in ((bt.lade_kap_quellen, "KAP-Quelle"),
                           (bt.lade_krypto_quellen, "Krypto-Quelle")):
            try:
                lader([p, p])
            except bt.EingabeFehler as e:
                assert art in str(e) and "doppelt" in str(e), str(e)
                assert "depot.kap_result.json" in str(e), str(e)
            else:
                raise AssertionError(f"{art} zweimal wurde nicht abgelehnt")


@case
def test_profil_eigene_schluessel_loesen_keine_falschwarnung_aus():
    """Die Schlüssel, die brokerprofile/die Profile planmäßig schreiben, dürfen
    keine 'unbekannter Schlüssel'-Warnung erzeugen: fünf Falschmeldungen begraben
    die eine Meldung, für die die Prüfung überhaupt da ist (hier: die nicht
    gelesene KAP-Hälfte). 'normiere_kap_quelle' hat auf derselben Datei null."""
    roh = krypto_haelfte()
    roh.update({
        "steuerjahr": 2024, "tax_year": 2024, "quelle": "eToro",
        "quelle_beschreibung": "eToro Jahresübersicht (englisch)",
        "profil": "etoro-de", "profil_status": "ok", "profil_geprueft_am": "2026-08-30",
        "zahlennotation": "en", "summen_basis": {"anzahl_veraeusserungen": 3},
        "abgleich": ["§ 23 Nettoergebnis: geparst 1.500,00 €"],
        "etoro_extra": {"gebuehren_eur": "12.00"},
        "so_zeilen": {"41": "1500.00"},
        "steuerpflichtiger_aus_report": {"name": "—"},
        "kennzahlen": {"kapitalertraege": "1000.00"},
        "kap_zeilen": {"19": "1000.00"},
        "hinweise": [], "elster_extra": [],
    })
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), krypto=[roh])
    falsch = [w for w in r["warnungen"] if "unbekannter Schlüssel" in w]
    eq(falsch, [], "keine Falschwarnung auf der eigenen Ausgabe des Skills")
    echte = [w for w in r["warnungen"] if "--kap-result" in w]
    assert echte, f"die Meldung, die zählt, fehlt: {r['warnungen']}"
    eq(r["warnungen"][0], echte[0],
       "die nicht gelesene KAP-Hälfte muss die erste Warnung sein, nicht die sechste")


@case
def test_die_feldliste_wird_aus_den_profilen_abgeleitet():
    """Die Allowlist darf nicht von Hand nachgepflegt werden müssen — sonst driftet
    sie mit jedem neuen Profil und erzeugt wieder Falschwarnungen."""
    felder = bt.krypto_felder()
    for pflicht in ("paragraph_23", "kennzahlen", "abgleich", "so_zeilen",
                    "quelle_beschreibung", "steuerpflichtiger_aus_report"):
        assert pflicht in felder, f"'{pflicht}' fehlt in der Feldliste"
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import brokerprofile as bp
        profile = bp.lade_profile()
    except Exception:
        return  # ohne brokerprofile greift die feste Basis — das ist der Fallback
    for profil in profile:
        offen = {p.split(".")[0] for p in bp.erzeugbare_pfade(profil)} - felder
        assert not offen, (f"Profil {getattr(profil, 'id', '?')} erzeugt Schlüssel, "
                           f"die build_taxreport.py als unbekannt melden würde: {offen}")


@case
def test_unbekannter_schluessel_in_der_krypto_quelle_wird_gemeldet():
    roh = krypto_haelfte()
    roh["steuerjahr"] = 2024
    roh["paragraph_22_nr_33"] = {"summe_eur": "900.00"}   # Tippfehler
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), krypto=[roh])
    treffer = [w for w in r["warnungen"] if "paragraph_22_nr_33" in w]
    assert treffer, f"unbekannter Schlüssel nicht gemeldet: {r['warnungen']}"
    assert "paragraph_22_nr_3" in treffer[0], f"kein Vorschlag: {treffer[0]}"


# ── Koinly-Futures erreichen die Verlusttöpfe ────────────────────────────────
@case
def test_koinly_futures_gewinn_wird_versteuert():
    roh = krypto_haelfte(netto="0.00")
    roh.update({"steuerjahr": 2024,
                "koinly_extra": {"futures_nettoergebnis_eur": "4000.00"}})
    r = bau(steuerdaten(), krypto=[roh])
    k = kap_block(r)
    eq(k["kapitalertraege"], "4000.00",
       "Futures-Gewinn ist zu erklärender Kapitalertrag, kein bloßer Hinweis")
    eq(k["gewinn_termingeschaefte"], "4000.00", "zugleich Davon-Zeile Z. 21")
    eq(k["abgeltungsteuer"], "750.00", "(4000 − 1000 Sparer-PB) × 25 %")
    assert any("Futures" in h and "§ 20 Abs. 2" in h for h in r["hinweise"]), \
        f"Herkunftshinweis fehlt: {r['hinweise']}"


@case
def test_koinly_futures_verlust_verrechnet_sich_gegen_kap_ertraege():
    roh = krypto_haelfte(netto="0.00")
    roh.update({"steuerjahr": 2024,
                "koinly_extra": {"futures_nettoergebnis_eur": "-2000.00"}})
    q = kap_quelle(quelle="Depot A", kapitalertraege="5000.00")
    k = kap_block(bau(steuerdaten(), [q], krypto=[roh]))
    eq(k["verlust_termingeschaefte"], "2000.00")
    eq(k["verlust_termingeschaefte_verrechnet"], "2000.00")
    eq(k["netto_kapitalertraege"], "3000.00")
    eq(k["abgeltungsteuer"], "500.00", "(5000 − 2000 − 1000) × 25 %")


# ── ELSTER: keine doppelten Zeilen ───────────────────────────────────────────
@case
def test_elster_extra_das_eine_rohzeile_wiederholt_wird_entdoppelt():
    """Rohzeile und elster_extra tragen denselben Betrag in dieselbe Zeile —
    zweimal abgetippt ist das eine doppelte Erklärung."""
    q = kap_quelle(quelle="eToro", kapitalertraege="5000.00",
                   kap_zeilen={"19": "5000.00"},
                   elster_extra=[{"anlage": "Anlage KAP", "zeile": "Z. 19",
                                  "bezeichnung": "Ausländische Kapitalerträge",
                                  "wert": "5000.00", "quelle": "eToro (elster_extra)"}])
    r = bau(steuerdaten(), [q])
    z19 = [z for z in zeilen(r) if z["zeile"] == "Z. 19"]
    eq(len(z19), 1, f"Zeile 19 darf nur einmal im Mapping stehen: {z19}")
    assert "kap_zeilen" in z19[0]["quelle"], "die Rohzeile hat Vorrang"
    assert any("doppelte Erklärung" in h for h in r["hinweise"]), \
        f"Hinweis auf die entfernte Wiederholung fehlt: {r['hinweise']}"


@case
def test_datei_in_beiden_listen_liefert_ihre_elster_zeile_nur_einmal():
    """Wird dieselbe Datei beiden Lesern übergeben, kommt ihr 'elster_extra'
    zweimal an — einmal über die KAP-, einmal über die Krypto-Hälfte."""
    extra = [{"anlage": "Anlage KAP", "zeile": "Z. 19",
              "bezeichnung": "Ausländische Kapitalerträge", "wert": "5000.00",
              "quelle": "eToro (elster_extra)"}]
    q = kap_quelle(quelle="eToro", kapitalertraege="5000.00",
                   kap_zeilen={"19": "5000.00"}, elster_extra=extra)
    q.update(krypto_haelfte())
    r = bau(steuerdaten(), [dict(q)], krypto=[dict(q)])
    z19 = [z for z in zeilen(r) if z["zeile"] == "Z. 19"]
    eq(len(z19), 1, f"Zeile 19 darf nur einmal im Mapping stehen: {z19}")
    assert "kap_zeilen" in z19[0]["quelle"], "die Rohzeile hat Vorrang"


@case
def test_zwei_depots_mit_gleichem_betrag_behalten_beide_ihre_zeile():
    """Zwei Depots, die zufällig denselben Betrag in derselben Zeile melden, sind
    keine Wiederholung, sondern zwei Belege. Entdoppelt wird nur je Quelle —
    sonst verschwindet der Nachweis des zweiten Depots aus dem Mapping."""
    def mit_extra(name):
        return kap_quelle(
            quelle=name, auslaendische_quellensteuer="250.00",
            kapitalertraege="4000.00",
            elster_extra=[{"anlage": "Anlage KAP", "zeile": "Z. 41",
                           "bezeichnung": "Anrechenbare ausländische Quellensteuer",
                           "wert": "250.00", "quelle": f"{name} (elster_extra)"}])
    r = bau(steuerdaten(), [mit_extra("Depot A"), mit_extra("Depot B")])
    z41 = [z for z in zeilen(r) if z["zeile"] == "Z. 41"]
    quellen = sorted(z["quelle"] for z in z41)
    assert "Depot A (elster_extra)" in quellen and "Depot B (elster_extra)" in quellen, \
        f"beide Belegzeilen müssen erhalten bleiben: {quellen}"
    eq(kap_block(r)["auslaendische_quellensteuer"], "500.00", "Summe stimmt weiterhin")
    assert not [h for h in r["hinweise"] if "doppelte Erklärung" in h], \
        f"hier wurde nichts wiederholt, also kein Doppelungs-Hinweis: {r['hinweise']}"


@case
def test_abweichender_betrag_in_derselben_zeile_bleibt_stehen():
    q = kap_quelle(quelle="eToro", kapitalertraege="5000.00",
                   kap_zeilen={"19": "5000.00"},
                   elster_extra=[{"anlage": "Anlage KAP", "zeile": "Z. 19",
                                  "bezeichnung": "korrigierter Wert",
                                  "wert": "5100.00", "quelle": "eToro (elster_extra)"}])
    z19 = [z for z in zeilen(bau(steuerdaten(), [q])) if z["zeile"] == "Z. 19"]
    eq(len(z19), 2, "ein abweichender Betrag ist keine Wiederholung und muss auffallen")


@case
def test_auslaendische_ertraege_landen_in_zeile_19_statt_zeile_7():
    """Ein Auslandsbroker ohne inländischen Steuerabzug meldet Zeile 19.
    Der abgeleitete Gesamtwert darf dann nicht in Zeile 7 wandern."""
    q = kap_quelle(quelle="eToro", kapitalertraege="5000.00",
                   kap_zeilen={"19": "5000.00"})
    r = bau(steuerdaten(), [q])
    eq([z["zeile"] for z in zeilen(r) if z["zeile"] == "Z. 7"], [],
       "keine Zeile 7, wenn die Quelle keine meldet")
    z19 = [z for z in zeilen(r) if z["zeile"] == "Z. 19"]
    eq(len(z19), 1, "Rohzeile deckt den abgeleiteten Wert ab")
    eq(z19[0]["wert"], "5000.00")


@case
def test_null_zeile_7_zieht_die_ertraege_nicht_aus_zeile_19():
    """Profile füllen ihr Zeilengerüst gern mit '0.00' vor. Eine so ausgewiesene
    Zeile 7 ist keine gemeldete Zeile — sonst stünden 5.000 € in Z. 7 UND in Z. 19,
    und wer das Mapping abtippt, erklärt sie zweimal."""
    q = kap_quelle(quelle="eToro", kapitalertraege="5000.00",
                   kap_zeilen={"7": "0.00", "19": "5000.00", "23": "0.00"})
    r = bau(steuerdaten(), [q])
    abgeleitet = [z for z in zeilen(r) if "kap_zeilen" not in z["quelle"]]
    eq([z["wert"] for z in abgeleitet if z["zeile"] == "Z. 7"], [],
       "keine abgeleitete Zeile 7 mit den ausländischen Erträgen")
    z19 = [z for z in zeilen(r) if z["zeile"] == "Z. 19"]
    eq(len(z19), 1, f"Zeile 19 genau einmal: {z19}")
    eq(z19[0]["wert"], "5000.00")
    # die 0,00-Rohzeilen bleiben als wörtliche Abschrift erhalten
    roh7 = [z for z in zeilen(r) if z["zeile"] == "Z. 7" and "kap_zeilen" in z["quelle"]]
    eq(len(roh7), 1)
    eq(roh7[0]["wert"], "0.00")


@case
def test_zeile_19_summe_bleibt_bei_zwei_quellen_und_geht_nicht_nach_zeile_7():
    a = kap_quelle(quelle="Broker A", kapitalertraege="3000.00",
                   kap_zeilen={"19": "3000.00"})
    b = kap_quelle(quelle="Broker B", kapitalertraege="2000.00",
                   kap_zeilen={"19": "2000.00"})
    r = bau(steuerdaten(), [a, b])
    eq([z for z in zeilen(r) if z["zeile"] == "Z. 7"], [])
    summen = [z for z in zeilen(r)
              if z["zeile"] == "Z. 19" and "kap_zeilen" not in z["quelle"]]
    eq(len(summen), 1)
    eq(summen[0]["wert"], "5000.00")


# ── Verlusttöpfe: kein Auswaschen, Vorträge aus Vorjahren ────────────────────
@case
def test_phantom_gewinn_aktien_laesst_den_verlust_im_aktientopf():
    """§ 20 Abs. 6 Satz 4 darf nicht dadurch umgangen werden, dass ein
    'gewinn_aktien' behauptet wird, dem keine Kapitalerträge gegenüberstehen —
    sonst wandert ein ringfenced Aktienverlust in den frei verrechenbaren Topf."""
    q = kap_quelle(kapitalertraege="0.00", gewinn_aktien="10000.00",
                   verlust_aktien="-10000.00")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), [q])
    k = kap_block(r)
    # Unter der davon-Zeilen-Lesart ist die Eingabe nicht auflösbar: entweder
    # enthält der Saldo tatsächlich 10.000 € Aktiengewinne und 10.000 €
    # Aktienverluste (dann ist 0,00 € richtig), oder der Gewinn ist behauptet
    # (dann müssten 10.000 € hinzugerechnet werden). Ein stiller Deckel würde bei
    # ehrlichen Zahlen falsch rechnen — deshalb WARNT der Report, statt zu raten.
    treffer = [w for w in r["warnungen"] if "gewinn_aktien" in w]
    assert treffer, f"unstimmige Davon-Zeile nicht gemeldet: {r['warnungen']}"
    assert "unversteuert" in treffer[0], treffer[0]
    eq(k["verlustvortraege"]["allgemein"], "0.00",
       "ein Aktienverlust darf NIE im allgemeinen Topf landen")
    eq(dec(k["verlust_aktien_verrechnet"]) + dec(k["verlust_aktien_ueberhang_hinzugerechnet"]),
       dec(k["verlust_aktien"]),
       "jeder Aktienverlust ist entweder verrechnet oder hinzugerechnet — nie beides, "
       "nie keines von beidem")


@case
def test_echter_aktiengewinn_wird_weiterhin_verrechnet():
    """Gegenprobe zur Deckelung: mit echten Kapitalerträgen bleibt alles wie bisher."""
    # Saldo 6.000 € = 10.000 € Aktiengewinne − 4.000 € Aktienverluste (Z. 20/Z. 23
    # sind Davon-Zeilen dazu). Die Verrechnung ist zulässig, es wird nichts
    # hinzugerechnet — und der Saldo auch nicht erneut gemindert.
    q = kap_quelle(kapitalertraege="6000.00", gewinn_aktien="10000.00",
                   verlust_aktien="-4000.00")
    k = kap_block(bau(steuerdaten(), [q]))
    eq(k["verlust_aktien_verrechnet"], "4000.00")
    eq(k["verlust_aktien_ueberhang_hinzugerechnet"], "0.00")
    eq(k["verlustvortraege"]["aktien"], "0.00")
    eq(k["netto_kapitalertraege"], "6000.00")


@case
def test_allgemeiner_verlustvortrag_aus_vorjahren_wird_verbraucht():
    sd = steuerdaten(anlage_kap={"verlustvortrag_allgemein_vorjahr": "2000.00"})
    q = kap_quelle(kapitalertraege="5000.00")
    r = bau(sd, [q])
    k = kap_block(r)
    eq(k["verlustvortrag_allgemein_vorjahr"], "2000.00")
    # 5000 − 1000 Sparer-PB = 4000; davon 2000 Altverlust → 2000 zu versteuern
    eq(k["bemessung_vor_verlustvortrag"], "4000.00")
    eq(k["verlustvortrag_allgemein_verbraucht"], "2000.00")
    eq(k["verlustvortrag_allgemein_rest"], "0.00")
    eq(k["nach_pauschbetrag"], "2000.00")
    eq(k["abgeltungsteuer"], "500.00")
    assert any("Verlustvortrag aus Vorjahren" in h and "Sparer-Pauschbetrag" in h
               for h in r["hinweise"]), f"Reihenfolge nicht dokumentiert: {r['hinweise']}"


@case
def test_beide_zwischenstufen_sind_eindeutig_benannt():
    """Wer gegen einen Steuerbescheid abgleicht, muss die Stufe vor und nach dem
    Verlustvortrag unterscheiden können — 'nach_pauschbetrag' allein ist zweideutig."""
    sd = steuerdaten(anlage_kap={"verlustvortrag_allgemein_vorjahr": "2000.00"})
    k = kap_block(bau(sd, [kap_quelle(kapitalertraege="5000.00")]))
    eq(k["nach_pauschbetrag_vor_verlustvortrag"], "4000.00", "5000 − 1000 Sparer-PB")
    eq(k["nach_pauschbetrag_und_verlustvortrag"], "2000.00", "− 2000 Altverlust")
    eq(k["bemessungsgrundlage_abgeltungsteuer"], "2000.00")
    eq(k["nach_pauschbetrag"], k["nach_pauschbetrag_und_verlustvortrag"],
       "der Altname bleibt die Bemessungsgrundlage — unverändert für bestehende Leser")
    eq(k["bemessung_vor_verlustvortrag"], k["nach_pauschbetrag_vor_verlustvortrag"])


@case
def test_der_geschonte_pauschbetrag_geht_nicht_verloren():
    """20.000 € Erträge, 20.000 € Vortrag: die 1.000 €, die stehen bleiben, sind
    genau der bewahrte Sparer-Pauschbetrag — er wird fortgeschrieben, nicht verbraucht."""
    sd = steuerdaten(anlage_kap={"verlustvortrag_allgemein_vorjahr": "20000.00"})
    k = kap_block(bau(sd, [kap_quelle(kapitalertraege="20000.00")]))
    eq(k["nach_pauschbetrag_vor_verlustvortrag"], "19000.00")
    eq(k["verlustvortrag_allgemein_verbraucht"], "19000.00")
    eq(k["verlustvortrag_allgemein_rest"], "1000.00")
    eq(k["verlustvortraege"]["allgemein"], "1000.00",
       "der geschonte Betrag bleibt festgestellt und ist nicht gestrandet")
    eq(k["abgeltungsteuer"], "0.00")


@case
def test_nicht_verbrauchter_allgemeiner_vortrag_wird_fortgeschrieben():
    sd = steuerdaten(anlage_kap={"verlustvortrag_allgemein_vorjahr": "9000.00"})
    # Saldo 4.000 € — die 1.000 € aus Z. 22 stecken bereits darin (Davon-Zeile).
    q = kap_quelle(kapitalertraege="4000.00", verluste_ohne_aktien="-1000.00")
    k = kap_block(bau(sd, [q]))
    # Saldo 4000; − 1000 Sparer-PB = 3000 verbraucht
    eq(k["verlustvortrag_allgemein_verbraucht"], "3000.00")
    eq(k["verlustvortrag_allgemein_rest"], "6000.00")
    eq(k["abgeltungsteuer"], "0.00")
    eq(k["verlustvortraege"]["allgemein"], "6000.00",
       "der Rest muss fortgeschrieben werden, sonst geht er verloren")
    eq(k["verlustvortraege"]["allgemein_davon_rest_vorjahre"], "6000.00")
    eq(k["verlustvortraege"]["allgemein_davon_laufendes_jahr"], "0.00")


@case
def test_termingeschaefte_vortrag_fliesst_in_den_allgemeinen_topf():
    """Nach der Aufhebung des § 20 Abs. 6 Satz 5 durch das JStG 2024 gibt es keinen
    eigenen Verrechnungskreis mehr — der Altvortrag wird allgemein verrechenbar."""
    sd = steuerdaten(anlage_kap={"verlustvortrag_termingeschaefte_vorjahr": "1500.00"})
    q = kap_quelle(kapitalertraege="5000.00")
    r = bau(sd, [q])
    k = kap_block(r)
    eq(k["verlustvortrag_termingeschaefte_vorjahr"], "1500.00")
    eq(k["verlustvortrag_allgemein_vorjahr_gesamt"], "1500.00")
    eq(k["verlustvortrag_allgemein_verbraucht"], "1500.00")
    eq(k["nach_pauschbetrag"], "2500.00", "5000 − 1000 Sparer-PB − 1500 Altverlust")
    assert any("Termingeschäfte-Verlustvortrag" in h and "JStG 2024" in h
               for h in r["hinweise"]), f"Einordnung nicht dokumentiert: {r['hinweise']}"


@case
def test_vortraege_aus_vorjahren_stehen_nicht_in_den_verlustzeilen():
    """Zeile 23 nimmt nur die Aktienverluste DIESES Jahres auf. Wer den Altvortrag
    von dort abtippt, erklärt ihn ein zweites Mal."""
    sd = steuerdaten(anlage_kap={"verlustvortrag_aktien_vorjahr": "5000.00",
                                 "verlustvortrag_allgemein_vorjahr": "1000.00"})
    q = kap_quelle(kapitalertraege="8000.00", gewinn_aktien="8000.00",
                   verlust_aktien="-2000.00")
    r = bau(sd, [q])
    z23 = [z for z in zeilen(r) if z["zeile"] == "Z. 23"]
    eq(len(z23), 1, f"Zeile 23 trägt nur den laufenden Aktienverlust: {z23}")
    eq(z23[0]["wert"], "2000.00")
    nachrichtlich = [z for z in zeilen(r) if z["zeile"] == "—"]
    assert len(nachrichtlich) >= 6, \
        f"Vorträge müssen nachrichtlich ausgewiesen werden: {nachrichtlich}"
    assert all("nachrichtlich" in z["bezeichnung"] for z in nachrichtlich), nachrichtlich


@case
def test_neue_vortragsfelder_sind_in_der_whitelist():
    sd = steuerdaten(anlage_kap={"kapitalertraege": "100.00",
                                 "verlustvortrag_allgemein_vorjahr": "10.00",
                                 "verlustvortrag_termingeschaefte_vorjahr": "20.00"})
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(sd)
    eq(r["eingabepruefung"]["unbekannte_felder"], [])


# ── Steuerjahre ──────────────────────────────────────────────────────────────
@case
def test_verschiedene_steuerjahre_werden_abgelehnt():
    with tempfile.TemporaryDirectory() as tmp:
        p1 = schreibe(tmp, "a.kap_result.json",
                      kap_quelle(quelle="Depot A", jahr=2024, kapitalertraege="100.00"))
        p2 = schreibe(tmp, "b.kap_result.json",
                      kap_quelle(quelle="Depot B", jahr=2023, kapitalertraege="200.00"))
        quellen = bt.lade_kap_quellen([p1, p2])
        try:
            bau(steuerdaten(), quellen)
        except bt.EingabeFehler as e:
            msg = str(e)
            assert "verschiedenen Steuerjahren" in msg, msg
            assert "a.kap_result.json" in msg and "b.kap_result.json" in msg, \
                f"die Meldung muss die Dateien benennen: {msg}"
            assert "2024" in msg and "2023" in msg, msg
        else:
            raise AssertionError("verschiedene Steuerjahre wurden NICHT abgelehnt")


@case
def test_abweichung_zum_steuerjahr_der_steuerdaten_wird_gewarnt():
    q = kap_quelle(jahr=2023, kapitalertraege="100.00")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(steuerjahr=2024), [q])
    assert any("2023" in w and "2024" in w for w in r["warnungen"]), r["warnungen"]


# ── Fehlerhafte Dateien ──────────────────────────────────────────────────────
@case
def test_fehlende_kennzahlen_werden_klar_gemeldet():
    q = kap_quelle(kapitalertraege="100.00")
    del q["kennzahlen"]
    try:
        bt.normiere_kap_quelle(q, herkunft="depot.json")
    except bt.EingabeFehler as e:
        msg = str(e)
        assert "depot.json" in msg, f"Datei nicht benannt: {msg}"
        assert "kennzahlen" in msg, msg
    else:
        raise AssertionError("fehlendes 'kennzahlen' wurde nicht gemeldet")


@case
def test_unlesbarer_betrag_benennt_datei_und_feld():
    q = kap_quelle(kapitalertraege="eintausend")
    try:
        bt.normiere_kap_quelle(q, herkunft="depot.json")
    except bt.EingabeFehler as e:
        msg = str(e)
        assert "depot.json.kennzahlen.kapitalertraege" in msg, msg
    else:
        raise AssertionError("unlesbarer Betrag wurde nicht gemeldet")


@case
def test_kap_zeilen_als_liste_werden_abgelehnt():
    q = kap_quelle(kapitalertraege="100.00")
    q["kap_zeilen"] = [["7", "100.00"]]
    try:
        bt.normiere_kap_quelle(q, herkunft="depot.json")
    except bt.EingabeFehler as e:
        assert "kap_zeilen" in str(e) and "depot.json" in str(e), str(e)
    else:
        raise AssertionError("falsch strukturierte kap_zeilen wurden nicht gemeldet")


# ── CLI ──────────────────────────────────────────────────────────────────────
@case
def test_cli_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        p_sd = schreibe(tmp, "steuerdaten.json", steuerdaten(
            anlage_n={"bruttoarbeitslohn": "40000", "lohnsteuer": "6000"},
            anlage_kap={"kapitalertraege": "100.00"}))
        p1 = schreibe(tmp, "tr.kap_result.json", kap_quelle(
            quelle="Trade Republic", kapitalertraege="3000.00",
            anrechenbare_kest="500.00", kap_zeilen={"7": "3000.00"}))
        p2 = schreibe(tmp, "cd.kap_result.json", kap_quelle(
            quelle="comdirect", gewinn_aktien="1000.00", kapitalertraege="1000.00",
            verlust_aktien="-1000.00"))
        out = os.path.join(tmp, "taxreport.json")
        rc = bt.main([p_sd, "--kap-result", p1, p2, "-o", out])
        eq(rc, 0, "CLI muss sauber durchlaufen")
        with open(out, encoding="utf-8") as f:
            r = json.load(f)
        k = r["anlagen"]["KAP"]
        eq(k["kapitalertraege"], "4100.00", "100 (Hand) + 3000 + 1000")
        eq(k["verlust_aktien_verrechnet"], "1000.00")
        eq(k["anrechenbare_kest"], "500.00")
        namen = [q["quelle"] for q in r["meta"]["kap_quellen"]]
        assert "Trade Republic" in namen and "comdirect" in namen, namen
        roh = [z for z in r["elster_mapping"] if "kap_zeilen" in z["quelle"]]
        eq(len(roh), 1)
        eq(roh[0]["quelle"], "Trade Republic (kap_zeilen)")
        json.dumps(r)  # der Report muss vollständig serialisierbar bleiben


@case
def test_cli_meldet_kaputte_datei_auf_deutsch_ohne_traceback():
    with tempfile.TemporaryDirectory() as tmp:
        p_sd = schreibe(tmp, "steuerdaten.json", steuerdaten())
        p_kap = schreibe(tmp, "kaputt.kap_result.json",
                         {"steuerjahr": 2024, "quelle": "Musterbank"})  # ohne 'kennzahlen'
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = bt.main([p_sd, "--kap-result", p_kap, "-o", os.path.join(tmp, "o.json")])
        eq(rc, 2, "unbrauchbare KAP-Quelle → Rückgabecode 2, kein Traceback")
        msg = err.getvalue()
        assert "FEHLER:" in msg, msg
        assert "kaputt.kap_result.json" in msg, f"Datei nicht benannt: {msg}"
        assert "kennzahlen" in msg, msg
        assert "Traceback" not in msg, msg


@case
def test_cli_meldet_fehlende_datei_und_kaputtes_json():
    with tempfile.TemporaryDirectory() as tmp:
        p_sd = schreibe(tmp, "steuerdaten.json", steuerdaten())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = bt.main([p_sd, "--kap-result", os.path.join(tmp, "gibtsnicht.json"),
                          "-o", os.path.join(tmp, "o.json")])
        eq(rc, 2)
        assert "nicht gefunden" in err.getvalue(), err.getvalue()

        p_bad = os.path.join(tmp, "bad.json")
        with open(p_bad, "w", encoding="utf-8") as f:
            f.write("{ das ist kein JSON")
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            rc2 = bt.main([p_sd, "--kap-result", p_bad, "-o", os.path.join(tmp, "o.json")])
        eq(rc2, 2)
        assert "kein gültiges JSON" in err2.getvalue(), err2.getvalue()


# ── Zeilen 20–25: EINE Überschrift, EINE Behandlung ──────────────────────────
# Im Formular stehen alle sechs Zeilen unter „In den Zeilen 18 und 19 enthaltene …"
# (bzw. „In Zeile 7 enthaltene …"). Der Report legt sie deshalb als davon-Zeilen
# aus: 'kapitalertraege' ist der Saldo, der sie bereits enthält. Diese Auslegung
# entscheidet über tausende Euro Abgeltungsteuer — sie wird hier als REGEL geprüft.


@case
def test_alle_sechs_davon_zeilen_werden_gleich_behandelt():
    """Keine der Zeilen 20–25 verschiebt die Bemessungsgrundlage.

    Einzige Ausnahme ist der Aktienverlust der Zeile 23, und zwar nicht als Abzug,
    sondern als HINZURECHNUNG: § 20 Abs. 6 Satz 4 EStG verbietet die Verrechnung
    mit anderen Kapitalerträgen, also wird der Überhang zurückgeholt.
    """
    basis = kap_block(bau(steuerdaten(), [kap_quelle(kapitalertraege="10000.00")]))
    for kennzahl, wert in (("gewinn_aktien", "4000.00"),
                           ("gewinn_termingeschaefte", "4000.00"),
                           ("verluste_ohne_aktien", "-4000.00"),
                           ("verlust_termingeschaefte", "-4000.00"),
                           ("verluste_ausfall", "-4000.00")):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            k = kap_block(bau(steuerdaten(), [kap_quelle(
                kapitalertraege="10000.00", **{kennzahl: wert})]))
        eq(k["netto_kapitalertraege"], basis["netto_kapitalertraege"],
           f"'{kennzahl}' ist eine Davon-Zeile und darf den Saldo nicht verändern")
        eq(k["abgeltungsteuer"], basis["abgeltungsteuer"], kennzahl)
    # Zeile 23 ohne Aktiengewinne: Hinzurechnung statt Abzug.
    k23 = kap_block(bau(steuerdaten(), [kap_quelle(
        kapitalertraege="10000.00", verlust_aktien="-4000.00")]))
    eq(k23["netto_kapitalertraege"], "14000.00",
       "der ringfenced Aktienverlust wird dem Saldo wieder hinzugerechnet, nie abgezogen")
    eq(k23["verlustvortraege"]["aktien"], "4000.00")


@case
def test_verlustzeilen_mindern_die_bemessungsgrundlage_nicht_ein_zweites_mal():
    """Der teure Fall aus der Praxis: Z. 19 = 30.000 €, darin Z. 24 = 25.000 €.

    Als davon-Zeile gelesen bleiben 30.000 € − 1.000 € Sparer-Pauschbetrag =
    29.000 € × 25 % = 7.250 € Abgeltungsteuer. Ein zweiter Abzug der 25.000 €
    ergäbe 4.000 € Bemessungsgrundlage und 1.000 € Steuer — rund 6.250 € zu wenig.
    """
    q = kap_quelle(kapitalertraege="30000.00", verlust_termingeschaefte="-25000.00",
                   kap_zeilen={"19": "30000.00", "24": "25000.00"})
    r = bau(steuerdaten(), [q])
    k = kap_block(r)
    eq(k["netto_kapitalertraege"], "30000.00", "Z. 24 steckt bereits in Z. 19")
    eq(k["bemessungsgrundlage_abgeltungsteuer"], "29000.00")
    eq(k["abgeltungsteuer"], "7250.00")
    eq(k["verlust_termingeschaefte_verrechnet"], "25000.00",
       "verrechnet ist der Verlust — aber im Saldo, nicht noch einmal hier")
    eq(k["verlustvortraege"]["termingeschaefte"], "0.00")


@case
def test_die_annahme_steht_prominent_im_report():
    """Eine Auslegung, die tausende Euro bewegt, darf nicht nur im Code stehen."""
    q = kap_quelle(kapitalertraege="30000.00", verlust_termingeschaefte="-25000.00")
    r = bau(steuerdaten(), [q])
    treffer = [h for h in r["hinweise"] if "ANNAHME zur Anlage KAP" in h]
    assert treffer, f"Annahme-Hinweis fehlt: {r['hinweise']}"
    eq(r["hinweise"][0], treffer[0],
       "die Annahme gehört an den Anfang der Hinweise, nicht ans Ende")
    for pflicht in ("Steuerbescheinigung", "BRUTTO", "22–25", "§ 20 Abs. 6 Satz 4"):
        assert pflicht in treffer[0], f"'{pflicht}' fehlt im Annahme-Hinweis: {treffer[0]}"
    assert any("ANNAHME zur Anlage KAP" in d for d in r["disclaimer"]), \
        f"die Annahme fehlt im Disclaimer: {r['disclaimer']}"


@case
def test_ohne_verlustzeilen_kein_annahme_hinweis():
    """Ohne Verlustzeile ist die Auslegung folgenlos — dann kein Rauschen."""
    r = bau(steuerdaten(), [kap_quelle(kapitalertraege="5000.00")])
    assert not [h for h in r["hinweise"] if "ANNAHME zur Anlage KAP" in h], r["hinweise"]


# ── ELSTER erwartet Verluste als BETRAG, nicht als negative Zahl ─────────────
@case
def test_negative_rohverlustzeile_wird_im_mapping_zum_betrag():
    """Die Quelle druckt −150,00; ELSTER will in Z. 23 die 150,00.

    Der Abgleich Rohzeile ↔ abgeleitete Zeile vergleicht Beträge und entfernt die
    abgeleitete (richtig signierte) Zeile als Dopplung. Bliebe die Rohzeile dann
    negativ stehen, stünde im Mapping die Anweisung, ein Minus in ein
    Betragsfeld zu tippen — je nach Verhalten von ELSTER kostet das den ganzen
    Verlustabzug.
    """
    q = kap_quelle(quelle="eToro", kapitalertraege="-600.00",
                   verlust_aktien="-150.00", verlust_termingeschaefte="-450.00",
                   kap_zeilen={"19": "-600.00", "23": "-150.00", "24": "-450.00"})
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = bau(steuerdaten(), [q])
    zs = [z for z in zeilen(r) if z["zeile"] in ("Z. 23", "Z. 24")]
    eq(len([z for z in zs if z["zeile"] == "Z. 23"]), 1, f"Z. 23 doppelt: {zs}")
    eq(len([z for z in zs if z["zeile"] == "Z. 24"]), 1, f"Z. 24 doppelt: {zs}")
    for z in zs:
        assert not str(z["wert"]).lstrip().startswith("-"), \
            f"{z['zeile']} trägt ein Minuszeichen in ein Betragsfeld: {z}"
    werte = {z["zeile"]: str(z["wert"]) for z in zs}
    eq(werte["Z. 23"], "150.00")
    eq(werte["Z. 24"], "450.00")
    # Die wörtliche Abschrift bleibt vorzeichengetreu — sie ist der Beleg.
    roh = {z["zeile"]: z["wert"] for z in k_zeilen(r)}
    eq(roh["23"], "-150.00", "die Abschrift der Bescheinigung wird nicht umgeschrieben")
    eq(roh["24"], "-450.00")
    assert any("positive Zahl ohne Minuszeichen" in h for h in r["hinweise"]), \
        f"Hinweis auf die ELSTER-Konvention fehlt: {r['hinweise']}"
    assert any("mit negativem Vorzeichen ausgewiesen" in h for h in r["hinweise"]), \
        f"Hinweis auf die umgestellten Zeilen fehlt: {r['hinweise']}"


@case
def test_positive_rohverlustzeile_bleibt_unveraendert():
    """Gegenprobe: die deutsche Bescheinigung druckt den Betrag schon positiv."""
    q = kap_quelle(quelle="Musterbank", kapitalertraege="1000.00",
                   verlust_aktien="-500.00", kap_zeilen={"7": "1000.00", "23": "500.00"})
    r = bau(steuerdaten(), [q])
    z23 = [z for z in zeilen(r) if z["zeile"] == "Z. 23"]
    eq(len(z23), 1)
    eq(str(z23[0]["wert"]), "500.00")
    assert not [h for h in r["hinweise"]
                if "mit negativem Vorzeichen ausgewiesen" in h], r["hinweise"]


@case
def test_abgeleitete_verlustzeile_ist_immer_positiv():
    """Auch ohne Rohzeile: Z. 22–25 tragen den Betrag, nie das Vorzeichen."""
    q = kap_quelle(kapitalertraege="-1200.00", verluste_ohne_aktien="-200.00",
                   verlust_aktien="-300.00", verlust_termingeschaefte="-400.00",
                   verluste_ausfall="-500.00")
    r = bau(steuerdaten(), [q])
    zs = {z["zeile"]: z for z in zeilen(r)}
    for nr, betrag in (("Z. 22", "200.00"), ("Z. 23", "300.00"),
                       ("Z. 24", "400.00"), ("Z. 25", "500.00")):
        eq(str(zs[nr]["wert"]), betrag, f"{nr} muss den Betrag tragen")
        assert "positiven Betrag" in zs[nr]["bezeichnung"], \
            f"{nr} sagt dem Nutzer nicht, dass ein positiver Betrag verlangt ist: {zs[nr]}"


@case
def test_zeile_20_hat_eine_eigene_bezeichnung():
    """Ohne Label rendert Z. 20 als generisches „Betrag laut Bescheinigung"."""
    q = kap_quelle(quelle="eToro", kapitalertraege="2000.00", gewinn_aktien="800.00",
                   kap_zeilen={"19": "2000.00", "20": "800.00"})
    r = bau(steuerdaten(), [q])
    z20 = [z for z in zeilen(r) if z["zeile"] == "Z. 20"]
    eq(len(z20), 1, f"Rohzeile 20 fehlt im Mapping: {zeilen(r)}")
    assert "Aktienveräußerungen" in z20[0]["bezeichnung"], z20[0]
    assert "Betrag laut Bescheinigung" not in z20[0]["bezeichnung"], z20[0]


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
