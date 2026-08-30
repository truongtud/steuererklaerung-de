#!/usr/bin/env python3
"""Führt alle Testdateien dieses Skills aus. Aufruf: python3 tests/run_tests.py"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    dateien = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    if not dateien:
        print("Keine Testdateien gefunden.")
        return 1
    fehlgeschlagen = []
    for f in dateien:
        name = os.path.basename(f)
        print(f"\n─── {name} " + "─" * max(0, 60 - len(name)))
        r = subprocess.run([sys.executable, f], cwd=os.path.dirname(HERE))
        if r.returncode != 0:
            fehlgeschlagen.append(name)
    print("\n" + "=" * 68)
    if fehlgeschlagen:
        print("FEHLGESCHLAGEN: " + ", ".join(fehlgeschlagen))
        return 1
    print(f"Alle {len(dateien)} Testdateien bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
