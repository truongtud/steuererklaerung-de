#!/usr/bin/env python3
"""Golden-Test: das eingecheckte beispiel/ muss dem entsprechen, was der Code erzeugt.

Das Beispiel im Marketplace-Stamm committet erzeugte Ausgaben (taxreport.json,
HTML, ELSTER-Mappings) neben den Eingaben. Ohne diesen Test veralten sie still,
sobald sich build_taxreport.py oder export_report.py ändern — und das Repo zeigt
dann einen Durchlauf vor, den der aktuelle Code gar nicht mehr produziert. Hier
wird in ein Temp-Verzeichnis regeneriert und gegen die eingecheckten Dateien
verglichen; nur der eingebettete Erstellungszeitpunkt wird normalisiert.

Schlägt der Test nach einer gewollten Änderung fehl: die Kommandos aus
beispiel/README.md („Selbst erzeugen“) laufen lassen, Ergebnis nach beispiel/
kopieren und die Kernzahlen im README mitprüfen.

Ausführen: python3 tests/test_beispiel.py   (oder tests/run_tests.py)
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
BUILD = os.path.join(SCRIPTS, "build_taxreport.py")
EXPORT = os.path.join(SCRIPTS, "export_report.py")
# beispiel/ liegt im Marketplace-Stamm, vier Ebenen über dem Skill. Ist der Skill
# ohne den Stamm installiert (claude plugin install), gibt es nichts zu prüfen.
BEISPIEL = os.path.abspath(os.path.join(ROOT, "..", "..", "..", "..", "beispiel"))

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(a, b, was):
    # bewusst ohne die Werte selbst: hier stehen ganze Dateien drin
    assert a == b, was


def run(*args):
    p = subprocess.run([sys.executable, *[str(a) for a in args]],
                       capture_output=True, text=True)
    assert p.returncode == 0, f"{os.path.basename(str(args[0]))} rc={p.returncode}\n{p.stderr}"
    return p


def lies(pfad):
    with open(pfad, encoding="utf-8") as f:
        return f.read()


@case
def test_beispiel_ist_aktuell():
    with tempfile.TemporaryDirectory() as tmp:
        report = os.path.join(tmp, "taxreport.json")
        run(BUILD, os.path.join(BEISPIEL, "steuerdaten.json"),
            "--transactions", os.path.join(BEISPIEL, "transactions.json"),
            "-o", report)
        # ohne pdf: fpdf2 ist optional, und das Beispiel committet kein PDF
        run(EXPORT, report, "--outdir", tmp, "--formats", "html", "elster")

        # Die ELSTER-Mappings tragen keinen Zeitstempel → byte-identisch.
        for name in ("elster_mapping_2024.csv", "elster_mapping_2024.json"):
            eq(lies(os.path.join(tmp, name)), lies(os.path.join(BEISPIEL, name)),
               f"{name} weicht vom eingecheckten Beispiel ab")

        # taxreport.json: nur meta.erstellt ist laufabhängig.
        neu = json.loads(lies(report))
        alt = json.loads(lies(os.path.join(BEISPIEL, "taxreport.json")))
        neu["meta"].pop("erstellt", None)
        alt["meta"].pop("erstellt", None)
        eq(neu, alt, "taxreport.json weicht vom eingecheckten Beispiel ab")

        # HTML: das Datum im Kopf normalisieren, sonst byte-identisch.
        norm = lambda s: re.sub(r"erstellt \d{4}-\d{2}-\d{2}", "erstellt …", s)
        eq(norm(lies(os.path.join(tmp, "taxreport_2024.html"))),
           norm(lies(os.path.join(BEISPIEL, "taxreport_2024.html"))),
           "taxreport_2024.html weicht vom eingecheckten Beispiel ab")


if __name__ == "__main__":
    if not os.path.isdir(BEISPIEL):
        print("  übersprungen: beispiel/ nicht vorhanden (Skill ohne Marketplace-Stamm)")
        sys.exit(0)
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
