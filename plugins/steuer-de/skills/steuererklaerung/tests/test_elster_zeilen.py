#!/usr/bin/env python3
"""
references/elster_zeilen.json ist die kuratierte Zeilennummern-Referenz je
Steuerjahr (siehe references/elster-zeilen.md). Geprueft wird dreierlei: die
JSON ist vollstaendig und exakt lesbar, scripts/fetch_elster_zeilen.py liest
aus echten Formulartexten die richtigen Felder heraus, und die Anlage-KAP-
Zeilen dort stimmen mit den in build_taxreport.py fest verdrahteten
Zeilennummern (KAP_ZEILEN_LABEL) ueberein — sonst koennte eine Aenderung an
einer Stelle unbemerkt von der anderen abweichen.

Ausfuehren: python3 tests/test_elster_zeilen.py   (oder tests/run_tests.py)
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_taxreport as bt  # noqa: E402
import fetch_elster_zeilen as fz  # noqa: E402

JSON_PFAD = os.path.join(ROOT, "references", "elster_zeilen.json")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def lade():
    with open(JSON_PFAD, encoding="utf-8") as f:
        return json.load(f)


# ── JSON selbst ──────────────────────────────────────────────────────────────
@case
def test_json_ist_vollstaendig():
    daten = lade()
    eq(daten["schema"], 1, "Schema-Version")
    jahre = daten["jahre"]
    assert jahre, "keine Jahre hinterlegt"
    for jahr, eintrag in jahre.items():
        eq(bool(re.fullmatch(r"20\d\d", jahr)), True, f"Jahresschluessel {jahr!r}")
        assert eintrag.get("quelle"), f"{jahr}: quelle fehlt"
        assert eintrag.get("status"), f"{jahr}: status fehlt"
        assert eintrag.get("anlagen"), f"{jahr}: keine Anlagen hinterlegt"
        for anlage, felder in eintrag["anlagen"].items():
            assert felder, f"{jahr}.{anlage}: leere Liste statt weggelassenem Schluessel"
            for f in felder:
                assert f.get("zeile"), f"{jahr}.{anlage}: Feld ohne 'zeile'"
                assert f.get("bezeichnung"), f"{jahr}.{anlage} Z. {f.get('zeile')}: ohne 'bezeichnung'"


@case
def test_geprueft_ist_datum_oder_null():
    """'geprueft': null heisst ausdruecklich 'noch nicht gegen den amtlichen
    Vordruck gegengelesen' (siehe 'status') — keine 0/leerer-String-Ersatzangabe,
    die sich als geprueft lesen liesse."""
    for jahr, eintrag in lade()["jahre"].items():
        g = eintrag.get("geprueft")
        if g is None:
            continue
        eq(bool(re.fullmatch(r"\d{4}-\d\d-\d\d", g)), True, f"{jahr}: geprueft ist kein ISO-Datum")


@case
def test_zeilennummern_ohne_dopplung_je_anlage():
    for jahr, eintrag in lade()["jahre"].items():
        for anlage, felder in eintrag["anlagen"].items():
            zeilen = [f["zeile"] for f in felder]
            eq(len(zeilen), len(set(zeilen)), f"{jahr}.{anlage}: doppelte Zeile in {zeilen}")


# ── Deckungsgleich mit build_taxreport.py ─────────────────────────────────────
@case
def test_kap_zeilen_stimmen_mit_build_taxreport():
    """KAP_ZEILEN_LABEL ist die Stelle, an der build_taxreport.py selbst weiss,
    was in einer Anlage-KAP-Zeile steht. Weicht references/elster_zeilen.json
    davon ab, hat jemand nur eine der beiden Stellen aktualisiert."""
    jahre = lade()["jahre"]
    # Das juengste hinterlegte Jahr ist das, gegen das build_taxreport.py
    # aktuell entwickelt wird — es gibt keinen Steuerjahr-Parameter in
    # KAP_ZEILEN_LABEL, das Dict ist jahresunabhaengig formuliert.
    jahr = max(jahre)
    hinterlegt = {f["zeile"]: f["bezeichnung"] for f in jahre[jahr]["anlagen"].get("Anlage KAP", [])}
    fehlend = sorted(set(bt.KAP_ZEILEN_LABEL) - set(hinterlegt), key=lambda z: (len(z), z))
    eq(fehlend, [], f"Zeilen aus KAP_ZEILEN_LABEL fehlen in elster_zeilen.json ({jahr})")


# ── fetch_elster_zeilen.py: Text-Extraktion ───────────────────────────────────
@case
def test_anlage_und_jahr_aus_kopfcode():
    """Formularkopf-Code wie im echten Vordruck ('2010AnlKAP051NET')."""
    anlage, jahr = fz.anlage_und_jahr_aus_text("2026AnlKAP051NET\n2026AnlKAP051NET\n")
    eq(anlage, "Anlage KAP", "Anlage aus Kopfcode")
    eq(jahr, 2026, "Jahr aus Kopfcode")


@case
def test_anlage_und_jahr_aus_fliesstext_ohne_kopfcode():
    anlage, jahr = fz.anlage_und_jahr_aus_text("Anlage SO zur Einkommensteuererklaerung 2026")
    eq(anlage, "Anlage SO", "Anlage aus Fliesstext")
    eq(jahr, 2026, "Jahr aus Fliesstext")


@case
def test_unbekannter_kopfcode_wird_nicht_geraten():
    """Ein Kuerzel ausserhalb ANLAGEN_KUERZEL (hier 'XYZ') darf keine Anlage
    erfinden — sonst landete ein Feld unter dem falschen Formular."""
    anlage, jahr = fz.anlage_und_jahr_aus_text("2026AnlXYZ011NET blabla ohne Anlage-Woerter")
    eq(anlage, None, "unbekanntes Kuerzel bleibt unzugeordnet")


@case
def test_betragsfeld_aus_seitentext():
    """Das Beschriftung/Zeile/,-/Kennziffer-Muster, wie es echte Anlage-KAP-PDFs
    drucken (gegen ein amtliches Muster nachgeprueft, siehe fetch_elster_zeilen.py
    Docstring). Die Beschriftung eines Feldes STEHT VOR seiner eigenen
    Zeilennummer, nicht danach."""
    text = (
        "Kapitalertraege\n32\n,-\n40\n"
        "In Zeile 32 enthaltene Gewinne aus Kapitalertraegen i. S. d. Par. 20 Abs. 2 EStG\n"
        "33\n,-\n41\n"
    )
    felder = fz.felder_aus_seitentext(text)
    eq([f["zeile"] for f in felder], ["32", "33"], "Reihenfolge/Anzahl der Zeilen")
    eq(felder[0]["bezeichnung"], "Kapitalertraege", "Beschriftung Z. 32")
    eq(felder[0]["kennziffer"], "40", "Kennziffer Z. 32")
    eq(felder[1]["bezeichnung"],
       "In Zeile 32 enthaltene Gewinne aus Kapitalertraegen i. S. d. Par. 20 Abs. 2 EStG",
       "Beschriftung Z. 33")
    eq(felder[1]["kennziffer"], "41", "Kennziffer Z. 33")


@case
def test_ja_nein_feld_aus_seitentext():
    text = "Ich beantrage die Guenstigerpruefung fuer saemtliche Kapitalertraege.\n4\n01\n1=Ja\n"
    felder = fz.felder_aus_seitentext(text)
    eq(len(felder), 1, "ein Ja/Nein-Feld erkannt")
    eq(felder[0]["art"], "ja_nein", "als Ja/Nein-Feld erkannt, nicht als Betragsfeld")
    eq(felder[0]["zeile"], "4", "Zeile des Ja/Nein-Felds")


@case
def test_seite_ohne_treffer_liefert_leere_liste():
    """Eine Anleitungsseite ganz ohne Formularfelder ist kein Fehler."""
    eq(fz.felder_aus_seitentext("Anleitung zur Anlage KAP. Dies ist Fliesstext ohne Formularfelder."),
       [], "keine Felder auf einer reinen Textseite")


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
