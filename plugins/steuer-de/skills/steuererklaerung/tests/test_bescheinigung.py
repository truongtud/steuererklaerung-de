#!/usr/bin/env python3
"""Bescheinigungen extrahieren statt abtippen.

Geprüft wird an synthetischen Fixtures — kein echtes und kein geschwärztes
Dokument. Der wichtigste Test ist test_nummer_ohne_passende_beschriftung: die
Feldnummern der Lohnsteuerbescheinigung stammen aus einer BMF-Bekanntmachung,
nicht aus dem Gesetz. Ändert sich die Nummerierung oder druckt ein Arbeitgeber
ein abweichendes Formular, darf kein falscher Betrag stillschweigend in die
Steuererklärung wandern.

Ausführen: python3 tests/test_bescheinigung.py   (oder tests/run_tests.py)
"""
import json
import os
import sys
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import parse_bescheinigung as pb  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


LSTB = "lohnsteuerbescheinigung_synthetisch.txt"


def profil():
    return pb.erkenne(fixture(LSTB), pb.lade_profile())


# ── Erkennen und Lesen ───────────────────────────────────────────────────────
@case
def test_dokument_wird_erkannt():
    p = profil()
    assert p is not None, "die Lohnsteuerbescheinigung wurde nicht erkannt"
    eq(p["id"], "lohnsteuerbescheinigung")
    eq(pb.erkenne("Werbeprospekt", pb.lade_profile()), None, "Fremdtext")


@case
def test_felder_werden_gelesen():
    werte, _ = pb.extrahiere(fixture(LSTB), profil())
    eq(werte["anlage_n.bruttoarbeitslohn"], D("78500.00"), "Nr. 3")
    eq(werte["anlage_n.lohnsteuer"], D("18420.00"), "Nr. 4")
    eq(werte["anlage_n.soli"], D("0.00"), "Nr. 5 — eine echte 0 aus dem Dokument")
    eq(werte["anlage_n.kirchensteuer"], D("1658.00"), "Nr. 6")
    eq(werte["vorsorge.kranken_pflege_basis.krankenversicherung"], D("3200.00"), "Nr. 25")


@case
def test_rentenversicherung_wird_zum_gesamtbeitrag_summiert():
    """§ 10 Abs. 3 rechnet mit dem Gesamtbeitrag. Nr. 22a und 23a zusammen
    ergeben ihn; nur der Arbeitgeberanteil geht zusätzlich in sein eigenes Feld.
    Genau hier tragen Menschen den halben Betrag ein und bekommen null Abzug —
    das ist der Hauptgrund, warum dieses Skript existiert."""
    werte, _ = pb.extrahiere(fixture(LSTB), profil())
    eq(werte["vorsorge.basisversorgung.rentenversicherung"], D("14600.00"),
       "22a + 23a")
    eq(werte["vorsorge.arbeitgeberanteil_steuerfrei"], D("7300.00"), "nur 22a")


# ── Betragserkennung ─────────────────────────────────────────────────────────
@case
def test_prozentsatz_ist_kein_betrag():
    """Bescheinigungen nennen oft den Beitragssatz neben dem Betrag."""
    eq(pb._betrag_der_zeile("25. Beiträge KV 14,6 % 3.200,00"), D("3200.00"))


@case
def test_vergleichswert_in_klammern_gewinnt_nicht():
    """„4. Lohnsteuer 18.420,00 EUR (Vorjahr 17.900,00)“ — die letzte Zahl der
    Zeile ist hier der Vorjahreswert. Wer sie nimmt, trägt einen falschen Betrag
    in die Steuererklärung, und die Zahl sieht plausibel aus."""
    eq(pb._betrag_der_zeile("4. Lohnsteuer 18.420,00 EUR (Vorjahr 17.900,00)"),
       D("18420.00"))


@case
def test_zwei_gleichrangige_betraege_sind_mehrdeutig():
    """Bleiben nach dem Aussortieren zwei Kandidaten übrig, wird nicht geraten —
    lieber gemeldet als der falsche von beiden."""
    eq(pb._betrag_der_zeile("Beitrag 1.000,00 Zuschuss 2.000,00"), None)


@case
def test_datum_ist_kein_betrag():
    eq(pb._betrag_der_zeile("Zeitraum 01.01.2024 bis 31.12.2024"), None)


# ── Die Sicherheitsregel ─────────────────────────────────────────────────────
@case
def test_nummer_ohne_passende_beschriftung_wird_nicht_uebernommen():
    """Die Feldnummern stammen aus einer BMF-Bekanntmachung, nicht aus dem
    Gesetz. Passt die Beschriftung nicht, wird gemeldet statt übernommen."""
    text = fixture(LSTB).replace("Bruttoarbeitslohn einschließlich Sachbezüge",
                                 "Irgendetwas ganz anderes")
    werte, meldungen = pb.extrahiere(text, profil())
    assert "anlage_n.bruttoarbeitslohn" not in werte, \
        f"der Wert hätte nicht übernommen werden dürfen: {werte}"
    assert any("3" in m for m in meldungen), f"nicht gemeldet: {meldungen}"


@case
def test_fehlendes_feld_wird_gemeldet_statt_genullt():
    text = "\n".join(z for z in fixture(LSTB).splitlines()
                     if not z.strip().startswith("25."))
    werte, meldungen = pb.extrahiere(text, profil())
    assert "vorsorge.kranken_pflege_basis.krankenversicherung" not in werte
    assert any("25" in m for m in meldungen), f"nicht gemeldet: {meldungen}"


@case
def test_unplausibler_rentenbeitrag_wird_gemeldet():
    """22a + 23a müssen ungefähr dem Beitragssatz auf den Bruttolohn entsprechen,
    gedeckelt auf die allgemeine Beitragsbemessungsgrenze. Ein Zahlendreher um
    eine Größenordnung fällt damit auf."""
    text = fixture(LSTB).replace("7.300,00", "73.000,00")
    _, meldungen = pb.extrahiere(text, profil())
    assert any("Rentenversicherung" in m or "Beitrag" in m for m in meldungen), \
        f"der unplausible Beitrag wurde nicht gemeldet: {meldungen}"


@case
def test_kirchensteuer_muss_zum_satz_passen():
    """Die Kirchensteuer ist 8 oder 9 Prozent der Lohnsteuer — ein enger,
    prüfbarer Zusammenhang. Passt sie zu keinem der beiden Sätze, wurde
    vermutlich die falsche Zeile gelesen."""
    text = fixture(LSTB)
    _, ok = pb.extrahiere(text, profil())
    assert not any("Kirchensteuer" in m for m in ok), \
        f"1.658 / 18.420 = 9 % — das darf nicht meckern: {ok}"

    verlesen = text.replace("1.658,00", "5.658,00")
    _, meldungen = pb.extrahiere(verlesen, profil())
    assert any("Kirchensteuer" in m for m in meldungen), \
        f"31 % Kirchensteuer muss auffallen: {meldungen}"


# ── Füllen der Vorlage ───────────────────────────────────────────────────────
@case
def test_leere_felder_werden_gefuellt():
    sd = {"anlage_n": {"bruttoarbeitslohn": "0.00", "lohnsteuer": ""},
          "vorsorge": {"basisversorgung": {}}}
    aenderungen = pb.fuelle(sd, {"anlage_n.bruttoarbeitslohn": D("78500.00"),
                                 "vorsorge.basisversorgung.rentenversicherung": D("14600.00")})
    eq(sd["anlage_n"]["bruttoarbeitslohn"], "78500.00")
    eq(sd["vorsorge"]["basisversorgung"]["rentenversicherung"], "14600.00", "Pfad wird angelegt")
    assert len(aenderungen) == 2, aenderungen


@case
def test_belegtes_feld_wird_nicht_still_ueberschrieben():
    """Ein vorhandener Wert kann von Hand geprüft worden sein. Ihn stillschweigend
    zu ersetzen wäre die schlechteste Variante."""
    sd = {"anlage_n": {"bruttoarbeitslohn": "70000.00"}}
    aenderungen = pb.fuelle(sd, {"anlage_n.bruttoarbeitslohn": D("78500.00")})
    eq(sd["anlage_n"]["bruttoarbeitslohn"], "70000.00", "unverändert")
    assert any("Konflikt" in a for a in aenderungen), aenderungen

    pb.fuelle(sd, {"anlage_n.bruttoarbeitslohn": D("78500.00")}, ueberschreiben=True)
    eq(sd["anlage_n"]["bruttoarbeitslohn"], "78500.00", "mit --ueberschreiben schon")


@case
def test_gleicher_wert_ist_kein_konflikt():
    """Dasselbe Dokument zweimal einzulesen darf nichts melden."""
    sd = {"anlage_n": {"bruttoarbeitslohn": "78500.00"}}
    aenderungen = pb.fuelle(sd, {"anlage_n.bruttoarbeitslohn": D("78500.00")})
    eq(aenderungen, [], f"unverändert eingelesen, trotzdem gemeldet: {aenderungen}")


@case
def test_gefuellte_datei_laeuft_durch_den_report():
    """Der eigentliche Zweck: aus Dokumenten wird eine Datei, die die Pipeline
    ohne gemeldetes Feld verarbeitet."""
    import build_taxreport as bt
    import neue_steuerdaten as ns
    sd = ns.steuerdaten(jahr=2024, taetigkeiten=["angestellt"])
    werte, _ = pb.extrahiere(fixture(LSTB), profil())
    pb.fuelle(sd, werte)
    eq(bt.pruefe_unbekannte_felder(sd), [], "die gefüllte Datei meldet eigene Felder")
    eq(sd["anlage_n"]["bruttoarbeitslohn"], "78500.00")


@case
def test_eine_echte_null_gilt_als_beantwortet():
    """Nr. 5 der Bescheinigung steht auf 0,00 € — kein Solidaritätszuschlag
    einbehalten. Das ist eine Antwort, keine Lücke. Sie danach unter „noch
    offen“ zu führen schickte den Nutzer eine Bescheinigung suchen, die er
    schon eingelesen hat."""
    sd = {"anlage_n": {"soli": "0.00", "lohnsteuer": "0.00"}}
    gefuellt = {"anlage_n.soli"}
    offen = pb.fehlende_felder(sd, beantwortet=gefuellt)
    assert "anlage_n.soli" not in offen, f"eine gelesene 0 ist beantwortet: {offen}"
    assert "anlage_n.lohnsteuer" in offen, f"das unberührte Feld fehlt weiter: {offen}"


@case
def test_jedes_dokument_wird_genau_seinem_profil_zugeordnet():
    """Zwei Fallen: „Lohnsteuerbescheinigung“ enthält „Steuerbescheinigung“ als
    Teilstring, und die Lohnsteuerbescheinigung nennt Kranken- und
    Pflegeversicherung. Ohne Ausschlussmerkmale griffe das falsche Profil — und
    dann stünde der Bruttoarbeitslohn in den Kapitalerträgen."""
    erwartet = {
        "lohnsteuerbescheinigung_synthetisch.txt": "lohnsteuerbescheinigung",
        "steuerbescheinigung_synthetisch.txt": "steuerbescheinigung",
        "beitragsbescheinigung_synthetisch.txt": "beitragsbescheinigung",
    }
    for datei, profil_id in erwartet.items():
        p = pb.erkenne(fixture(datei), pb.lade_profile())
        assert p is not None, f"{datei}: kein Profil erkannt"
        eq(p["id"], profil_id, datei)


# ── Weitere Bescheinigungen ──────────────────────────────────────────────────
@case
def test_steuerbescheinigung_ohne_feldnummern():
    """Banken nummerieren ihre Felder nicht — hier trägt allein die
    Beschriftung. Sie ist dafür amtlich vorgegeben (Muster der
    Steuerbescheinigung)."""
    text = fixture("steuerbescheinigung_synthetisch.txt")
    profil = pb.erkenne(text, pb.lade_profile())
    assert profil is not None and profil["id"] == "steuerbescheinigung", profil
    werte, _ = pb.extrahiere(text, profil)
    eq(werte["anlage_kap.kapitalertraege"], D("850.00"))
    eq(werte["anlage_kap.anrechenbare_kest"], D("212.50"))
    eq(werte["anlage_kap.einbehaltener_soli"], D("11.68"))


@case
def test_aktienverlust_und_sonstiger_verlust_bleiben_getrennt():
    """Die beiden Verlusttöpfe dürfen nicht vertauscht werden: der
    Aktien-Verlusttopf ist nach § 20 Abs. 6 Satz 4 EStG beschränkt, der andere
    nicht. Die Beschriftungen unterscheiden sich nur durch ein Wort."""
    text = fixture("steuerbescheinigung_synthetisch.txt")
    werte, _ = pb.extrahiere(text, pb.erkenne(text, pb.lade_profile()))
    assert "anlage_kap.verlust_aktien" in werte
    assert "anlage_kap.verluste_ohne_aktien" in werte


@case
def test_beitragsbescheinigung():
    text = fixture("beitragsbescheinigung_synthetisch.txt")
    profil = pb.erkenne(text, pb.lade_profile())
    assert profil is not None and profil["id"] == "beitragsbescheinigung", profil
    werte, meldungen = pb.extrahiere(text, profil)
    eq(werte["vorsorge.kranken_pflege_basis.krankenversicherung"], D("3200.00"))
    eq(werte["vorsorge.kranken_pflege_basis.pflegeversicherung"], D("550.00"))
    assert any("Basis" in m for m in meldungen), \
        f"der Hinweis zur Basisabsicherung fehlt: {meldungen}"


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
