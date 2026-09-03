#!/usr/bin/env python3
"""importiere_unterlagen.py — alle Unterlagen auf einmal hineinwerfen.

Der Nutzer soll seine Papiere in einen Ordner legen und einen Befehl tippen.
Dieses Modul entscheidet je Datei, was sie ist, und schickt sie an den
passenden Leser. Die wichtigste Eigenschaft ist dabei nicht, möglichst viel zu
erkennen, sondern **nichts zu verwechseln**: was nicht sicher zuzuordnen ist,
wird gemeldet und liegen gelassen.

Ausführen: python3 tests/test_import.py   (oder tests/run_tests.py)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import importiere_unterlagen as iu  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def pfad(name):
    return os.path.join(FIXTURES, name)


@case
def test_bescheinigungen_werden_als_solche_erkannt():
    for datei, erwartet in (
            ("lohnsteuerbescheinigung_synthetisch.txt", "lohnsteuerbescheinigung"),
            ("steuerbescheinigung_synthetisch.txt", "steuerbescheinigung"),
            ("beitragsbescheinigung_synthetisch.txt", "beitragsbescheinigung")):
        art, kennung = iu.bestimme_art(pfad(datei))
        eq(art, "bescheinigung", datei)
        eq(kennung, erwartet, datei)


@case
def test_broker_report_wird_als_solcher_erkannt():
    """Die Broker-Fixtures liegen seit jeher im selben Verzeichnis — der
    Verteiler muss sie von den Bescheinigungen unterscheiden."""
    art, kennung = iu.bestimme_art(pfad("koinly-de.txt"))
    eq(art, "broker", "Koinly-Report")
    assert kennung, "das Broker-Profil muss benannt werden"


@case
def test_unbekanntes_dokument_wird_nicht_geraten():
    """Der teuerste Fehler wäre, ein unbekanntes Papier irgendwie zuzuordnen.
    Es wird gemeldet und liegen gelassen."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8",
                                     delete=False) as f:
        f.write("Werbeprospekt eines Möbelhauses\nSofa 499,00\n")
        p = f.name
    try:
        art, kennung = iu.bestimme_art(p)
    finally:
        os.unlink(p)
    eq(art, None, "kein Ratespiel")
    eq(kennung, None)


@case
def test_bescheid_wird_erkannt_und_nicht_als_bescheinigung_verarbeitet():
    """Ein Steuerbescheid gehört zu /bescheid-pruefen, nicht in die
    Steuerdaten. Er darf hier nicht stillschweigend eingelesen werden."""
    art, _ = iu.bestimme_art(pfad("bescheid_synthetisch.txt"))
    eq(art, "bescheid", "als Bescheid erkannt, nicht als Bescheinigung")


@case
def test_import_fuellt_und_berichtet():
    """Der Lauf über mehrere Dokumente füllt die Steuerdaten und sagt je Datei,
    was passiert ist."""
    import neue_steuerdaten as ns
    sd = ns.steuerdaten(jahr=2024, taetigkeiten=["angestellt"], kapital=True)
    bericht = iu.importiere(
        [pfad("lohnsteuerbescheinigung_synthetisch.txt"),
         pfad("steuerbescheinigung_synthetisch.txt")], sd)
    eq(sd["anlage_n"]["bruttoarbeitslohn"], "78500.00")
    eq(sd["anlage_kap"]["kapitalertraege"], "850.00")
    eq(len(bericht), 2, "je Dokument ein Eintrag")
    for eintrag in bericht:
        eq(eintrag["art"], "bescheinigung")
        assert eintrag["aenderungen"], eintrag


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
