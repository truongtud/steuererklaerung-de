#!/usr/bin/env python3
"""Der YAML-Kopf jeder SKILL.md muss lesbar sein.

Ein Skill, dessen Kopf YAML nicht parst, wird gar nicht erst geladen — und der
Fehler fällt nicht beim Bauen auf, sondern erst dem Nutzer, der den Befehl
aufruft. `claude plugin validate` prüft das Manifest, nicht diese Köpfe.

Der Klassiker ist ein **unquotierter Doppelpunkt im Fließtext**:

    description: Der Startpunkt: sagt, welche Unterlagen ...
                              ^ ab hier hält YAML das für ein Mapping

Genau das ist einmal passiert und hat /einstieg unbenutzbar gemacht.

Geprüft wird ohne PyYAML — die Bibliothek fehlt in vielen Umgebungen, und der
Test soll überall laufen. Ist sie da, wird zusätzlich echt geparst.

Ausführen: python3 tests/test_skill_kopf.py   (oder tests/run_tests.py)
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", ".."))

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def koepfe():
    """(pfad, kopftext) für jede SKILL.md neben diesem Skill."""
    gefunden = []
    for pfad in sorted(glob.glob(os.path.join(SKILLS, "*", "SKILL.md"))):
        text = open(pfad, encoding="utf-8").read()
        teile = text.split("---")
        assert len(teile) >= 3, f"{pfad}: kein YAML-Kopf zwischen --- gefunden"
        gefunden.append((pfad, teile[1]))
    return gefunden


@case
def test_es_gibt_ueberhaupt_skills():
    """Ohne diesen Test würde ein falscher Pfad alle anderen still bestehen
    lassen — sie liefen dann über eine leere Liste."""
    assert len(koepfe()) >= 6, f"nur {len(koepfe())} SKILL.md gefunden"


@case
def test_jeder_kopf_ist_gueltiges_yaml():
    """Der eigentliche Test: unquotierte Doppelpunkte im Wert."""
    for pfad, kopf in koepfe():
        name = os.path.basename(os.path.dirname(pfad))
        for nr, zeile in enumerate(kopf.strip().splitlines(), 1):
            if not zeile.strip() or zeile.lstrip().startswith("#"):
                continue
            m = re.match(r"^([A-Za-z][\w-]*): (.*)$", zeile)
            assert m, f"{name}, Zeile {nr}: kein 'schlüssel: wert' — {zeile!r}"
            wert = m.group(2).strip()
            if wert[:1] in ('"', "'"):
                continue        # quotiert, da darf alles drin stehen
            assert ": " not in wert, (
                f"{name}, Zeile {nr}: unquotierter Doppelpunkt im Wert — YAML "
                f"liest das als Mapping und lädt den Skill nicht. Umformulieren "
                f"oder den Wert in Anführungszeichen setzen: {zeile!r}")


@case
def test_pyyaml_bestaetigt_das_urteil():
    """Wo PyYAML da ist, gegen die echte Bibliothek prüfen. Fehlt sie, gilt
    der Test als bestanden — die Regel oben steht auf eigenen Füßen."""
    try:
        import yaml
    except ImportError:
        return
    for pfad, kopf in koepfe():
        try:
            geladen = yaml.safe_load(kopf)
        except yaml.YAMLError as e:
            raise AssertionError(
                f"{pfad}: YAML-Kopf parst nicht — {str(e).splitlines()[0]}")
        assert isinstance(geladen, dict), f"{pfad}: Kopf ist kein Mapping"


@case
def test_name_passt_zum_verzeichnis():
    """Der Befehlsname kommt aus dem Verzeichnis. Weicht `name` davon ab,
    heißt der Skill in der Liste anders als der Aufruf."""
    for pfad, kopf in koepfe():
        verzeichnis = os.path.basename(os.path.dirname(pfad))
        m = re.search(r"^name: (.+)$", kopf, re.M)
        assert m, f"{verzeichnis}: kein name im Kopf"
        eq(m.group(1).strip(), verzeichnis, f"name in {verzeichnis}")


@case
def test_jeder_skill_hat_eine_beschreibung():
    """Ohne description entscheidet Claude ins Blaue, wann ein Skill passt."""
    for pfad, kopf in koepfe():
        name = os.path.basename(os.path.dirname(pfad))
        m = re.search(r"^description: (.+)$", kopf, re.M)
        assert m and len(m.group(1).strip()) > 30, \
            f"{name}: description fehlt oder ist zu knapp"


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
