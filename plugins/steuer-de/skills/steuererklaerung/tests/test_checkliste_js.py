#!/usr/bin/env python3
"""Führt das JavaScript der ELSTER-Checkliste wirklich aus (Node + DOM-Attrappe).

Die übrigen Checklisten-Tests in test_export.py prüfen nur, dass bestimmte
Zeichenketten im erzeugten HTML stehen — ob der Code beim Klick tatsächlich das
Richtige tut, sagt das nicht. Hier läuft er: Zeilen werden nach Anlage
gruppiert, ein gesetzter Haken zieht Fortschritt und Gruppenzähler mit, und die
Kopieren-Schaltfläche meldet in allen drei Fällen das Richtige — mit
Clipboard-API, ohne sie (execCommand-Rückfall) und wenn beides scheitert.

Ohne Node auf dem Rechner gilt der Test als bestanden und sagt das (wie die
PDF-Tests ohne fpdf2). Ausführen: python3 tests/test_checkliste_js.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import export_report as ex  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def _node_da() -> bool:
    return shutil.which("node") is not None


# Nur so viel DOM, wie die Checkliste anfasst. Absichtlich klein gehalten: was
# hier fehlt, fällt beim Lauf sofort als TypeError auf, statt still zu bestehen.
DOM_STUB = r"""
const store = {};
global.localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: k => { delete store[k]; },
};
function mkEl(tag) {
  const el = {
    tagName: tag.toUpperCase(), children: [], _text: '', className: '', style: {},
    dataset: {}, _listeners: {}, title: '',
    classList: {
      add(c) { const s = new Set(el.className.split(' ').filter(Boolean)); s.add(c); el.className = [...s].join(' '); },
      remove(...cs) { const s = new Set(el.className.split(' ').filter(Boolean)); cs.forEach(c => s.delete(c)); el.className = [...s].join(' '); },
      toggle(c, on) { on ? el.classList.add(c) : el.classList.remove(c); },
      contains(c) { return el.className.split(' ').includes(c); },
    },
    appendChild(c) { el.children.push(c); return c; },
    removeChild(c) { el.children = el.children.filter(x => x !== c); return c; },
    setAttribute(k, v) { el[k] = v; },
    addEventListener(ev, fn) { (el._listeners[ev] = el._listeners[ev] || []).push(fn); },
    dispatchEvent(e) { (el._listeners[e.type] || []).forEach(fn => fn(e)); },
    closest() { return null; },
    select() { el._selected = true; },
  };
  Object.defineProperty(el, 'textContent',
    { get: () => el._text, set: v => { el._text = String(v); } });
  return el;
}
const byId = {};
['balken-fuellung', 'fortschritt-text', 'gruppen', 'nur-offen-box', 'zuruecksetzen-btn']
  .forEach(id => { byId[id] = mkEl('div'); });
byId['zeilen-daten'] = mkEl('script');
byId['zeilen-daten'].textContent = ZEILEN_JSON;
global.document = {
  getElementById: id => byId[id] || null,
  createElement: mkEl,
  body: mkEl('body'),
  createRange: () => ({ selectNodeContents() {} }),
  execCommand: () => EXEC_OK,
  // Die "Für Claude kopieren"-Knöpfe der Hinweise stehen im statischen HTML,
  // nicht im hier nachgebauten DOM — für diesen Test also leer. Dass sie
  // funktionieren, prüft test_export.py am erzeugten Markup.
  querySelectorAll: () => [],
};
global.window = { getSelection: () => ({ removeAllRanges() {}, addRange() {} }) };
global.Event = class { constructor(t) { this.type = t; } stopPropagation() {} };
// Node >= 21 hat ein eingebautes, nur lesbares `navigator` (Getter ohne Setter):
// eine einfache Zuweisung verpufft dort stillschweigend, und der Test würde
// immer den Rückfallpfad messen statt der Clipboard-API.
Object.defineProperty(globalThis, 'navigator',
  { value: NAVIGATOR, configurable: true, writable: true });
global.setTimeout = () => 0;      // Rückmeldung friert ein, damit sie prüfbar bleibt
global.clearTimeout = () => {};
"""

RUNNER = r"""
eval(SKRIPT);
const gruppen = document.getElementById('gruppen');
const ersteSection = gruppen.children[0];
const tbody = ersteSection.children[1].children[1];
const tr = tbody.children[0];
const cb = tr.children[0].children[0];
const btn = tr.children[3].children[1];

const ergebnis = { sections: gruppen.children.length,
                   fortschritt_vorher: document.getElementById('fortschritt-text').textContent };
cb.checked = true;
cb.dispatchEvent(new Event('change'));
ergebnis.fortschritt_nachher = document.getElementById('fortschritt-text').textContent;
ergebnis.gruppenzaehler = ersteSection.children[0].children[1].textContent;
ergebnis.zeilenklasse = tr.className;
ergebnis.balken = document.getElementById('balken-fuellung').style.width;
ergebnis.titel = btn.title;

btn.dispatchEvent(new Event('click'));
setImmediate(() => setImmediate(() => {
  ergebnis.kopier_text = btn.textContent;
  ergebnis.kopier_klasse = btn.className;
  console.log(JSON.stringify(ergebnis));
}));
"""


def _lauf(*, clipboard: bool, exec_ok: bool) -> dict:
    """Checkliste erzeugen, JS herausschneiden und unter Node ausführen."""
    from test_export import fixture, _mapping_mit_arten   # dieselben Fixtures

    html = ex.render_checkliste(fixture(elster_mapping=_mapping_mit_arten()))
    zeilen_json = re.search(
        r'<script id="zeilen-daten"[^>]*>(.*?)</script>', html, re.S).group(1)
    js = re.search(r"<script>\n(\(function\(\).*?)\n</script>", html, re.S).group(1)

    navigator = ("{ clipboard: { writeText: () => Promise.resolve() } }"
                 if clipboard else "{}")
    stub = (DOM_STUB
            .replace("ZEILEN_JSON", json.dumps(zeilen_json))
            .replace("EXEC_OK", "true" if exec_ok else "false")
            .replace("NAVIGATOR", navigator))

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "lauf.js").write_text(
            stub + "\nconst SKRIPT = " + json.dumps(js) + ";\n" + RUNNER,
            encoding="utf-8")
        p = subprocess.run(["node", str(d / "lauf.js")], capture_output=True,
                           text=True, encoding="utf-8")
    assert p.returncode == 0, f"Node-Lauf fehlgeschlagen:\n{p.stderr}"
    return json.loads(p.stdout.strip().splitlines()[-1])


@case
def test_js_ist_syntaktisch_gueltig():
    if not _node_da():
        print("       (übersprungen: node nicht installiert)")
        return
    from test_export import fixture, _mapping_mit_arten

    html = ex.render_checkliste(fixture(elster_mapping=_mapping_mit_arten()))
    js = re.search(r"<script>\n(\(function\(\).*?)\n</script>", html, re.S).group(1)
    with tempfile.TemporaryDirectory() as tmp:
        pfad = Path(tmp) / "checkliste.js"
        pfad.write_text(js, encoding="utf-8")
        p = subprocess.run(["node", "--check", str(pfad)], capture_output=True,
                           text=True, encoding="utf-8")
    eq(p.returncode, 0, f"node --check meldet einen Syntaxfehler:\n{p.stderr}")


@case
def test_gruppierung_und_fortschritt_ziehen_mit():
    if not _node_da():
        print("       (übersprungen: node nicht installiert)")
        return
    r = _lauf(clipboard=True, exec_ok=True)
    eq(r["sections"], 2, "je Anlage eine Section (Anlage N + Anlage KAP)")
    eq(r["fortschritt_vorher"], "0 von 2 erledigt (0 %)")
    eq(r["fortschritt_nachher"], "1 von 2 erledigt (50 %)", "Haken zieht den Fortschritt mit")
    eq(r["gruppenzaehler"], "1 / 1", "die Gruppe zählt ihren eigenen Fortschritt")
    eq(r["zeilenklasse"], "erledigt")
    eq(r["balken"], "50%", "Balken folgt dem Fortschritt")


@case
def test_kopieren_meldet_erfolg_ueber_die_clipboard_api():
    if not _node_da():
        print("       (übersprungen: node nicht installiert)")
        return
    r = _lauf(clipboard=True, exec_ok=False)
    eq(r["kopier_text"], "Kopiert ✓")
    assert "ok" in r["kopier_klasse"], r["kopier_klasse"]


@case
def test_kopieren_faellt_ohne_clipboard_api_auf_execcommand_zurueck():
    """Genau der Fall einer lokal geöffneten Datei in einem Browser ohne
    Clipboard-API — ohne Rückfall bliebe der Klick wirkungslos."""
    if not _node_da():
        print("       (übersprungen: node nicht installiert)")
        return
    r = _lauf(clipboard=False, exec_ok=True)
    eq(r["kopier_text"], "Kopiert ✓")
    assert "ok" in r["kopier_klasse"], r["kopier_klasse"]


@case
def test_kopieren_sagt_es_wenn_beide_wege_scheitern():
    if not _node_da():
        print("       (übersprungen: node nicht installiert)")
        return
    r = _lauf(clipboard=False, exec_ok=False)
    eq(r["kopier_text"], "Strg+C", "kein stiller Fehlschlag")
    assert "fehler" in r["kopier_klasse"], r["kopier_klasse"]


@case
def test_kopier_hinweis_nennt_den_formularwert():
    if not _node_da():
        print("       (übersprungen: node nicht installiert)")
        return
    r = _lauf(clipboard=True, exec_ok=True)
    assert "63230,00" in r["titel"], \
        f"der Tooltip muss die ELSTER-Fassung nennen, nicht 63230.00: {r['titel']!r}"


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
