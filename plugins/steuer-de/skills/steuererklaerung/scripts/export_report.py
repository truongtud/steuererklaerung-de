#!/usr/bin/env python3
"""
export_report.py — Rendert einen TaxReport (taxreport.json) in:
  * HTML-Dashboard (self-contained, keine externen Abhängigkeiten, mit Druck-Stylesheet)
  * PDF-Report (fpdf2; benötigt: pip install fpdf2 --break-system-packages)
  * ELSTER-Feld-Mapping als CSV und JSON (manuelle Eingabe in Mein ELSTER)
  * ELSTER-Checkliste als interaktives HTML (dieselben Zeilen zum Abhaken statt nur
    Abtippen — Häkchen bleiben per localStorage im Browser erhalten, self-contained
    wie das Dashboard)

Grundregeln dieses Exporters:
  * Der Disclaimer steht in **jedem** Format — auch in den ELSTER-Dateien, aus denen
    abgetippt wird (SKILL.md: „Diesen Hinweis nicht weglassen.“).
  * Es wird gerendert, was im Report steht — kein festes Zeilen-Set, keine stillen
    Auslassungen. Fehlende/kaputte Felder erzeugen „—“, keinen Absturz.
  * Beträge in der CSV in deutscher Notation (Komma), damit deutsches Excel sie als
    Zahl und nicht als Text importiert.

Aufruf:
  python export_report.py taxreport.json --outdir ./out --formats html pdf elster checkliste
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from steuerlib import csv_safe, de_dezimal, fmt_eur, to_decimal_or  # noqa: E402

D = Decimal


# ---------------------------------------------------------------- Helfer ------
def _dict(x) -> dict:
    """Alles, was kein dict ist (None, Liste, Zahl), wird zum leeren dict."""
    return x if isinstance(x, dict) else {}


def _list(x) -> list:
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def _texte(x) -> list[str]:
    """Disclaimer/Hinweise robust als Liste von Strings."""
    return [str(i).strip() for i in _list(x) if str(i).strip()]


def _s(x) -> str:
    """String für Anzeige — None wird zu '', nicht zu 'None'."""
    return "" if x is None else str(x)


def _g(d, *path, default=""):
    """Verschachtelter Zugriff, der auch über None-Zwischenwerte nicht stolpert."""
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return default if cur is None else cur


def esc(x) -> str:
    """HTML-Escaping für Fremddaten — von render_html und render_checkliste geteilt,
    damit eine künftige Korrektur nicht in nur einer der beiden Kopien landet."""
    return html.escape(_s(x))


def row(cells, header=False, cls="") -> str:
    """Eine <tr> aus Zellen, die bereits fertig formatiert (escaped) sind."""
    tag = "th" if header else "td"
    tds = "".join(f"<{tag}>{c}</{tag}>" for c in cells)
    attr = f' class="{cls}"' if cls else ""
    return f"<tr{attr}>{tds}</tr>"


# ------------------------------------------------- „Für Claude kopieren" ------
# Die Schaltfläche neben jedem Hinweis legt den Hinweis samt fertiger Frage in
# die Zwischenablage — sie SCHICKT nichts. Das ist Absicht und keine
# Einschränkung, die sich beheben ließe: diese Datei liegt lokal (meist
# file://), enthält Steuerbeträge und macht laut Test keinen einzigen
# Netzaufruf. Ein Knopf, der den Hinweis irgendwohin überträgt, würde genau die
# Zusage brechen, dass nichts das Gerät verlässt. Eingefügt wird von Hand, in
# der Sitzung, in der man ohnehin gerade sitzt.
KOPIER_JS = """
  function lsKopierFallback(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '-1000px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e) { return false; }
  }

  function kopierRueckmeldung(btn, text, klasse) {
    if (!btn._standard) btn._standard = btn.textContent;
    btn.textContent = text;
    btn.classList.remove('ok', 'fehler');
    if (klasse) btn.classList.add(klasse);
    clearTimeout(btn._timer);
    btn._timer = setTimeout(function() {
      btn.textContent = btn._standard;
      btn.classList.remove('ok', 'fehler');
    }, 1600);
  }

  function inZwischenablage(text, btn, markierEl) {
    function gelungen() { kopierRueckmeldung(btn, 'Kopiert ✓', 'ok'); }
    function gescheitert() {
      if (markierEl) {
        try {
          var b = document.createRange();
          b.selectNodeContents(markierEl);
          var s = window.getSelection();
          s.removeAllRanges();
          s.addRange(b);
        } catch (e) {}
      }
      kopierRueckmeldung(btn, 'Strg+C', 'fehler');
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(gelungen, function() {
        if (lsKopierFallback(text)) gelungen(); else gescheitert();
      });
      return;
    }
    if (lsKopierFallback(text)) gelungen(); else gescheitert();
  }

  Array.prototype.forEach.call(
    document.querySelectorAll('button.erklaeren'), function(btn) {
      btn.addEventListener('click', function() {
        inZwischenablage(btn.getAttribute('data-frage'), btn, btn.parentNode);
      });
    });
"""

ERKLAER_PROMPT = (
    "Erkläre mir bitte diesen Hinweis aus meiner Steuererklärung {jahr} "
    "(erstellt mit dem Skill „Steuererklärung Deutschland“) in einfachen Worten: "
    "Was bedeutet er für mich, was muss ich prüfen oder tun, und auf welche "
    "Vorschrift stützt er sich?\n\n{text}")


def hinweis_liste(texte, jahr) -> str:
    """<li>-Liste mit einer „Für Claude kopieren"-Schaltfläche je Hinweis."""
    if not texte:
        return "<li>—</li>"
    teile = []
    for t in texte:
        frage = ERKLAER_PROMPT.format(jahr=_s(jahr), text=_s(t))
        teile.append(
            f"<li>{esc(t)} "
            f'<button type="button" class="erklaeren" '
            f'data-frage="{html.escape(frage, quote=True)}" '
            f'title="Hinweis samt Frage in die Zwischenablage — zum Einfügen in '
            f'deine Claude-Sitzung. Es wird nichts verschickt.">Für Claude kopieren'
            f"</button></li>")
    return "".join(teile)


def _num(x, default=D("0")):
    """Zahl aus beliebiger Eingabe; nie ein Absturz, nie ein stiller Fantasiewert."""
    return to_decimal_or(x, default)


def fmt_menge(x, maxlen: int = 16) -> str:
    """Mengen numerisch formatieren.

    Nicht abschneiden: '12345678901.5'[:10] wäre '1234567890' — eine um den Faktor
    10 falsche Menge. Lieber runden bzw. wissenschaftlich notieren.
    """
    d = to_decimal_or(x, None)
    if d is None:
        s = _s(x)
        return s if len(s) <= maxlen else s[: maxlen - 1] + "…"
    try:
        if d.as_tuple().exponent < -8:
            d2 = d.quantize(D("0.00000001"), rounding=ROUND_HALF_UP)
        else:
            d2 = d
        s = format(d2, "f")
    except (InvalidOperation, ValueError, TypeError):
        s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("", "-"):
        s = "0"
    if len(s) > maxlen:
        try:
            s = f"{d:.6E}"
        except (InvalidOperation, ValueError):
            pass
    return s


def safe_filename(part, fallback: str = "report") -> str:
    """'2024/../etc' darf keinen Pfad verlassen und keinen Lauf abbrechen."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", _s(part)).strip("._-")
    return s or fallback


def report_texte(report: dict) -> tuple[list[str], list[str]]:
    """(disclaimer, hinweise) — beide Schlüssel sind optional.

    Hinweise, die wörtlich schon im Disclaimer stehen, werden nicht doppelt
    ausgegeben (Bauer und Krypto-Engine liefern teils denselben Satz)."""
    disclaimer = _texte(report.get("disclaimer"))
    gesehen = set(disclaimer)
    hinweise = []
    for h in _texte(report.get("hinweise")):
        if h not in gesehen:
            gesehen.add(h)
            hinweise.append(h)
    return disclaimer, hinweise


def elster_rows(report: dict) -> list[dict]:
    """Jede gelieferte Zeile wird gerendert — kein festes Anlagen-Set, kein Limit."""
    return [r for r in _list(report.get("elster_mapping")) if isinstance(r, dict)]


ANLAGEN_LABEL = {
    "N": "Anlage N — Nichtselbständige Arbeit",
    "KAP": "Anlage KAP — Kapitalerträge",
    "SO": "Anlage SO — Sonstige Einkünfte (Krypto)",
    "V": "Anlage V — Vermietung und Verpachtung",
    "S": "Anlage S — Selbständige Arbeit",
    "G": "Anlage G — Gewerbebetrieb",
}
ANLAGEN_FELD = {"N": "einkuenfte", "KAP": "kapitalertraege", "SO": "einkuenfte_gesamt",
                "V": "einkuenfte", "S": "gewinn", "G": "gewinn"}
# KAP bleibt draußen: Kapitalerträge laufen i. d. R. über die Abgeltungsteuer und
# sind in `summe_der_einkuenfte` des Reports bewusst nicht enthalten.
EINKUNFTS_ANLAGEN = ("N", "SO", "V", "S", "G")


def summe_einkuenfte(report: dict) -> Decimal:
    """Summe der Einkünfte — bevorzugt die vom Report ausgewiesene, sonst gerechnet."""
    b = _dict(report.get("berechnung"))
    ausgewiesen = to_decimal_or(_g(b, "summe_der_einkuenfte", default=None), None)
    if ausgewiesen is not None:
        return ausgewiesen
    a = _dict(report.get("anlagen"))
    return sum((_num(_g(a, k, ANLAGEN_FELD[k], default="0")) for k in EINKUNFTS_ANLAGEN), D("0"))


# ---------------------------------------------------------------- HTML --------
def render_html(report: dict) -> str:
    report = _dict(report)
    meta = _dict(report.get("meta"))
    a = _dict(report.get("anlagen"))
    b = _dict(report.get("berechnung"))
    kr = _dict(report.get("krypto_detail"))
    p23 = _dict(kr.get("paragraph_23"))
    p22 = _dict(kr.get("paragraph_22_nr_3"))
    ke = _dict(report.get("koinly_extra") or kr.get("koinly_extra"))
    year = html.escape(_s(meta.get("steuerjahr")))
    tp = _dict(meta.get("steuerpflichtiger"))
    name = html.escape(_s(tp.get("name")) or "—")

    def card(label, value, sub=""):
        sub_html = f'<div class="sub">{html.escape(_s(sub))}</div>' if sub else ""
        return (f'<div class="card"><div class="lbl">{html.escape(_s(label))}</div>'
                f'<div class="val">{html.escape(_s(value))}</div>{sub_html}</div>')

    # Disposals-Tabelle — Felder stammen aus fremden PDFs/CSVs: alles escapen.
    disp_rows = ""
    for d in _list(p23.get("disposals")):
        if not isinstance(d, dict):
            continue
        taxable = bool(d.get("taxable"))
        disp_rows += row([
            esc(d.get("asset")),
            esc(fmt_menge(d.get("amount"))),
            esc(d.get("acquisition_date")),
            esc(d.get("disposal_date")),
            esc(f'{_s(d.get("held_days"))} T' if d.get("held_days") not in (None, "") else "—"),
            esc(fmt_eur(d.get("gain_eur"))),
            ('<span class="badge tax">steuerpfl.</span>' if taxable
             else '<span class="badge free">steuerfrei</span>'),
        ])
    if not disp_rows:
        disp_rows = row(["—", "", "", "", "", "", "keine Veräußerungen"])

    # ELSTER-Mapping — alle gelieferten Zeilen, kein festes Set
    el_rows = ""
    for e in elster_rows(report):
        el_rows += row([esc(e.get("anlage")), esc(e.get("zeile")),
                        esc(e.get("bezeichnung")), esc(e.get("wert"))])
    if not el_rows:
        el_rows = row(["—", "", "keine Mapping-Zeilen im Report", ""])

    # Anlagen-Übersicht inkl. Zwischensumme (KAP ist bewusst nicht Teil der Summe)
    anlagen_rows = ""
    for k, lab in ANLAGEN_LABEL.items():
        val = _g(a, k, ANLAGEN_FELD[k], default="0")
        if _num(val) != 0 or k in ("N", "SO"):
            label = lab + (" (nicht in der Summe — Abgeltungsteuer)" if k == "KAP" else "")
            anlagen_rows += row([esc(label), esc(fmt_eur(val))])
    anlagen_rows += row([esc("Summe der Einkünfte (ohne Anlage KAP)"),
                         esc(fmt_eur(summe_einkuenfte(report)))], cls="sum")

    disclaimer, hinweise = report_texte(report)
    disc_html = hinweis_liste(disclaimer, meta.get("steuerjahr"))
    # Was die Schätzung NICHT enthält, mit Richtung — sonst kann der Leser die
    # Zahl darüber nicht einordnen.
    unsicher = report.get("unsicherheit") or {}
    unsicher_block = ""
    if unsicher.get("posten"):
        zeilen = "".join(
            f"<tr><td>{esc(p['posten'])}</td>"
            f"<td class=\"r\">{esc(p['richtung'])}</td>"
            f"<td>{esc(p.get('groessenordnung') or '—')}</td>"
            f"<td>{esc(p['fundstelle'])}</td></tr>"
            for p in unsicher["posten"])
        unsicher_block = (
            '<div class="note"><strong>Was diese Schätzung nicht enthält</strong>'
            f'<p>Gesamtbild: <strong>{esc(unsicher.get("gesamtrichtung", "—"))}</strong>. '
            'Die Richtung sagt, wie die ausgewiesene Steuer vom tatsächlichen Ergebnis '
            'abweichen dürfte.</p>'
            '<table><thead><tr><th>Posten</th><th>Wirkung auf die Steuer</th>'
            '<th>Größenordnung</th><th>Fundstelle</th></tr></thead>'
            f'<tbody>{zeilen}</tbody></table></div>')

    hinweis_block = ""
    if hinweise:
        hinweis_block = ('<div class="note"><strong>Weitere Hinweise</strong><ul>'
                         + hinweis_liste(hinweise, meta.get("steuerjahr")) + "</ul></div>")

    est = b.get("einkommensteuer_schaetzung")
    est_disp = fmt_eur(est) if est not in (None, "") else "— (Tarif nicht hinterlegt)"

    verlustvortrag = _num(p23.get("verlustvortrag_eur"))
    futures = _num(ke.get("futures_nettoergebnis_eur"))
    extra_cards = ""
    if verlustvortrag > 0:
        extra_cards += card("Verlustvortrag § 23", fmt_eur(p23.get("verlustvortrag_eur")),
                            "Verlustfeststellung beantragen")
    if futures != 0:
        extra_cards += card("Futures (separat, Anlage KAP)",
                            fmt_eur(ke.get("futures_nettoergebnis_eur")))

    return f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TaxReport {year} — {name}</title>
<style>
:root{{color-scheme:dark light;
--bg:#0b0f17;--panel:#131a26;--panel2:#1a2436;--line:#243044;
--txt:#e6edf6;--mut:#8aa0bd;--acc:#4da3ff;--good:#3fd17a;--bad:#ff6b6b;--warn:#ffb454;}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
background:var(--bg);color:var(--txt);line-height:1.5}}
.wrap{{max-width:1080px;margin:0 auto;padding:28px 20px 60px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;
border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:24px;flex-wrap:wrap;gap:12px}}
h1{{font-size:24px;margin:0}}h2{{font-size:17px;margin:28px 0 12px;color:var(--acc)}}
.muted{{color:var(--mut);font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin:18px 0}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}}
.card .lbl{{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.card .val{{font-size:22px;font-weight:600;margin-top:6px}}
.card .sub{{color:var(--mut);font-size:12px;margin-top:4px}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border-radius:12px;overflow:hidden;font-size:14px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line)}}
th{{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}}
td:last-child,th:last-child{{text-align:right}}
tr.sum td{{font-weight:700;border-top:2px solid var(--line);background:var(--panel2)}}
.badge{{padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600}}
.badge.tax{{background:rgba(255,107,107,.15);color:var(--bad)}}
.badge.free{{background:rgba(63,209,122,.15);color:var(--good)}}
.note{{background:var(--panel);border-left:3px solid var(--warn);padding:12px 16px;
border-radius:8px;margin:16px 0;font-size:13px;color:var(--mut)}}
.note ul{{margin:6px 0 0;padding-left:18px}}
.note li{{margin-bottom:6px}}
button.erklaeren{{font:inherit;font-size:11px;cursor:pointer;background:var(--panel2);
color:var(--mut);border:1px solid var(--line);border-radius:6px;padding:1px 8px;
margin-left:6px;white-space:nowrap;vertical-align:baseline}}
button.erklaeren:hover{{border-color:var(--acc);color:var(--txt)}}
button.erklaeren.ok{{border-color:var(--good);color:var(--good)}}
button.erklaeren.fehler{{border-color:var(--bad);color:var(--bad)}}
footer{{margin-top:36px;color:var(--mut);font-size:12px;text-align:center}}
@media print{{
  :root{{color-scheme:light;--bg:#fff;--panel:#fff;--panel2:#f2f4f8;--line:#9aa4b2;
  --txt:#000;--mut:#333;--acc:#000;--good:#065f36;--bad:#8a1414;--warn:#7a4b00;}}
  body{{background:#fff;color:#000;font-size:11pt}}
  .wrap{{max-width:none;padding:0}}
  h2{{color:#000;border-bottom:1px solid #000;padding-bottom:2px}}
  .muted,.card .lbl,.card .sub,.note{{color:#333}}
  .card{{border:1px solid #9aa4b2;break-inside:avoid}}
  table,.note{{border:1px solid #9aa4b2}}
  th{{color:#000;background:#f2f4f8}}
  th,td{{border-bottom:1px solid #9aa4b2}}
  tr,li{{break-inside:avoid}}
  thead{{display:table-header-group}}
  .badge{{border:1px solid #333;background:none !important}}
  .badge.tax{{color:#8a1414}}.badge.free{{color:#065f36}}
  button.erklaeren{{display:none !important}}
  footer{{color:#333}}
}}
</style></head><body><div class="wrap">
<header><div><h1>Steuererklärung {year}</h1>
<div class="muted">{name} · {html.escape(_s(meta.get("veranlagung")))} · erstellt {html.escape(_s(meta.get("erstellt"))[:10])}</div></div>
<div class="muted">TaxReport · alle Anlagen + Krypto</div></header>

<div class="cards">
{card("Zu versteuerndes Einkommen", fmt_eur(b.get("zu_versteuerndes_einkommen")))}
{card("Einkommensteuer (Schätzung)", est_disp, _s(b.get("tarif")))}
{card("Krypto § 23 steuerpflichtig", fmt_eur(_g(a, "SO", "krypto_23_steuerpflichtig", default="0")), "nach Freigrenze")}
{card("Krypto steuerfrei (> 1 J.)", fmt_eur(p23.get("summe_steuerfrei_gt_1_jahr_eur")))}
</div>

<h2>Einkünfte nach Anlagen</h2>
<table>{row(["Anlage", "Einkünfte / Betrag"], header=True)}{anlagen_rows}</table>

<h2>Krypto — Private Veräußerungsgeschäfte (§ 23 EStG)</h2>
<div class="cards">
{card("Netto-Ergebnis § 23", fmt_eur(p23.get("netto_ergebnis_eur")))}
{card("Freigrenze", fmt_eur(p23.get("freigrenze_eur")), "überschritten: " + ("ja" if p23.get("freigrenze_ueberschritten") else "nein"))}
{card("Staking § 22 Nr. 3", fmt_eur(p22.get("steuerpflichtig_eur")), "Zufluss " + fmt_eur(p22.get("summe_zufluesse_eur")))}
{extra_cards}
</div>
<table>{row(["Asset", "Menge", "Anschaffung", "Veräußerung", "Haltedauer", "Gewinn/Verlust", "Status"], header=True)}{disp_rows}</table>

<h2>ELSTER-Feld-Mapping (manuelle Eingabe)</h2>
<table>{row(["Anlage", "Zeile", "Bezeichnung", "Wert"], header=True)}{el_rows}</table>

{unsicher_block}
<div class="note"><strong>Wichtige Hinweise</strong><ul>{disc_html}</ul></div>
{hinweis_block}
<footer>Erstellt mit dem Skill „Steuererklärung Deutschland“ · keine Steuerberatung · Endkontrolle durch Steuerberater</footer>
</div>
<script>
(function() {{
{KOPIER_JS}
}})();
</script>
</body></html>"""


# ----------------------------------------------------- Interaktive Checkliste --
# Beträge kommen aus build_taxreport.py in Punktschreibweise ("60000.00") —
# dieselbe Form, die die CSV über steuerlib.de_dezimal ins deutsche Format
# bringt. Alles andere (Steuerjahr, Name, Datum, Steuer-ID) ist kein Betrag und
# bleibt unangetastet.
_BETRAG_MUSTER = re.compile(r"-?\d+\.\d+")


def checklisten_wert(wert) -> tuple[str, str]:
    """(Anzeige, Kopierwert) einer Mapping-Zeile.

    Angezeigt wird die lesbare deutsche Fassung mit Tausenderpunkten und
    Währungszeichen („60.000,00 €"), kopiert wird dagegen genau das, was in ein
    ELSTER-Eingabefeld gehört: dieselbe Zahl mit **Dezimalkomma**, ohne
    Tausenderpunkte und ohne € („60000,00").

    Das ist kein Schönheitsdetail: Ein aus dem Report kopiertes „60000.00"
    landet mit Dezimal**punkt** im Formular — ELSTER liest deutsche Notation,
    und je nach Feld wird der Punkt als Tausendertrennzeichen gedeutet oder die
    Eingabe abgelehnt. Aus 60000.00 € würden dann 6.000.000 €.
    """
    s = _s(wert)
    if _BETRAG_MUSTER.fullmatch(s):
        return fmt_eur(s), de_dezimal(s)
    return s, s


def render_checkliste(report: dict) -> str:
    """Dieselben Zeilen wie das ELSTER-Mapping, aber zum Abhaken statt Abtippen.

    Nur 'eintragen'-Zeilen bekommen eine Checkbox — das sind laut
    build_taxreport.py._ordne_mapping genau die, die tatsächlich in ein
    ELSTER-Formularfeld gehören. 'nachrichtlich'-Zeilen (Belege je Quelle) und
    die Trennzeile selbst sind reine Nachweise; sie stehen nur noch als
    aufklappbare Liste darunter, ohne Checkbox — dort gibt es nichts abzuhaken.

    Der Haken-Status lebt ausschließlich im Browser (localStorage), geschlüsselt
    über Anlage+Zeile+Bezeichnung+WERT: ändert sich der Wert einer Zeile bei
    einem erneuten Export (z. B. weil eine Eingabe korrigiert wurde), ist die
    Zeile automatisch wieder offen — ein alter Haken auf einem inzwischen
    anderen Betrag wäre die gefährlichste Art dieser Datei, falsch zu liegen.

    Angezeigt wird der Betrag lesbar deutsch („60.000,00 €"), **kopiert** wird
    dagegen die Fassung fürs Formularfeld („60000,00", siehe checklisten_wert).
    Die Zeilen sind nach Anlage gruppiert — in der Reihenfolge, in der man die
    Formulare tatsächlich ausfüllt —, jede Gruppe zählt ihren eigenen
    Fortschritt, und „nur offene anzeigen" blendet Erledigtes aus. Ein Klick
    irgendwo in die Zeile setzt den Haken; die Kopieren-Schaltfläche meldet in
    jedem Fall zurück, ob sie Erfolg hatte, und markiert den Betrag zum
    manuellen Kopieren, wenn der Browser die Zwischenablage verweigert
    (auf file:// keine Selbstverständlichkeit).
    """
    report = _dict(report)
    meta = _dict(report.get("meta"))
    year = html.escape(_s(meta.get("steuerjahr")))
    tp = _dict(meta.get("steuerpflichtiger"))
    name = html.escape(_s(tp.get("name")) or "—")
    # Der Klartext-Teil ist nur zur Lesbarkeit beim Debuggen; der Hash-Teil
    # hält zwei Steuerpflichtige mit gleich lautendem Jahr auseinander, deren
    # Namen nach dem Entfernen von Sonderzeichen zufällig gleich aussehen
    # (z. B. durch unterschiedliche Akzente/Satzzeichen) — sonst könnten sich
    # ihre Häkchen im selben Browserprofil überschreiben.
    roh = f"{_s(meta.get('steuerjahr'))}_{_s(tp.get('name'))}"
    kurzhash = hashlib.sha1(roh.encode("utf-8")).hexdigest()[:8]
    storage_suffix = html.escape(
        re.sub(r"[^A-Za-z0-9_-]+", "_", roh) + "_" + kurzhash)

    alle = elster_rows(report)
    eintragen = [r for r in alle if r.get("art") == "eintragen"]
    belege = [r for r in alle if r.get("art") not in ("eintragen", "trenner")]

    beleg_rows = "".join(
        row([esc(b.get("anlage")), esc(b.get("zeile")), esc(b.get("bezeichnung")),
             esc(b.get("wert"))])
        for b in belege) or row(["—", "", "keine Belegzeilen", ""])

    # '<' escapen: eine Bezeichnung oder ein Wert mit dem Teilstring '</script>'
    # (Fremddaten aus einer Bescheinigung oder einem Broker-Report — nichts
    # davon ist vertrauenswürdiges HTML) dürfte das eingebettete <script>-Tag
    # sonst vorzeitig schließen. < bleibt für JSON.parse ein normales "<".
    # 'wert' bleibt der ROHWERT: er trägt den localStorage-Schlüssel, und der
    # soll sich ändern, sobald sich der Betrag ändert — unabhängig davon, wie
    # er gerade formatiert angezeigt wird.
    zeilen_json = json.dumps(
        [dict(zip(("anlage", "zeile", "bezeichnung", "wert", "anzeige", "kopie"),
                  (_s(r.get("anlage")), _s(r.get("zeile")), _s(r.get("bezeichnung")),
                   _s(r.get("wert")), *checklisten_wert(r.get("wert")))))
         for r in eintragen],
        ensure_ascii=False).replace("<", "\\u003c")

    disclaimer, hinweise = report_texte(report)
    disc_html = hinweis_liste(disclaimer, meta.get("steuerjahr"))
    # Die inhaltlichen Hinweise (Saldo-Annahme der Anlage KAP, Verlustvorträge,
    # knapp verfehlte Freigrenzen) gehören gerade hier hin: Sie entscheiden, ob
    # eine Zeile überhaupt so eingetragen werden darf, wie sie dasteht.
    weitere_html = ('<div class="note"><strong>Weitere Hinweise</strong><ul>'
                    + hinweis_liste(hinweise, meta.get("steuerjahr")) + "</ul></div>"
                    ) if hinweise else ""

    return f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ELSTER-Checkliste {year} — {name}</title>
<style>
:root{{color-scheme:dark light;
--bg:#0b0f17;--panel:#131a26;--panel2:#1a2436;--line:#243044;
--txt:#e6edf6;--mut:#8aa0bd;--acc:#4da3ff;--good:#3fd17a;--bad:#ff6b6b;--warn:#ffb454;}}
@media (prefers-color-scheme: light){{:root{{
--bg:#f5f7fa;--panel:#ffffff;--panel2:#eef2f7;--line:#d5dce6;
--txt:#111823;--mut:#5c6b80;--acc:#0b62c4;--good:#137a44;--bad:#b3261e;--warn:#8a5a00;}}}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
background:var(--bg);color:var(--txt);line-height:1.5}}
.wrap{{max-width:960px;margin:0 auto;padding:28px 20px 60px}}
header{{border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:4px}}
h1{{font-size:22px;margin:0 0 4px}}
.muted{{color:var(--mut);font-size:13px}}
.fortschritt{{position:sticky;top:0;background:var(--bg);padding:14px 0 14px;
z-index:5;border-bottom:1px solid var(--line);margin-bottom:20px}}
.balken{{height:10px;border-radius:6px;background:var(--panel2);overflow:hidden;margin-bottom:10px}}
.balken-fuellung{{height:100%;background:var(--good);width:0%;transition:width .2s}}
.fortschritt-zeile{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
.werkzeuge{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.schalter{{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--mut);
cursor:pointer;user-select:none}}
button{{font:inherit;cursor:pointer;background:var(--panel2);color:var(--txt);
border:1px solid var(--line);border-radius:8px;padding:6px 12px}}
button:hover{{border-color:var(--acc)}}
button:focus-visible,input:focus-visible{{outline:2px solid var(--acc);outline-offset:2px}}
h2.gruppe{{font-size:15px;margin:22px 0 8px;display:flex;align-items:baseline;
justify-content:space-between;gap:12px;color:var(--acc)}}
h2.gruppe .zaehler{{font-size:12px;color:var(--mut);font-weight:400;white-space:nowrap}}
section.fertig h2.gruppe{{color:var(--good)}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border-radius:12px;
overflow:hidden;font-size:14px;margin-bottom:8px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}}
tbody tr{{cursor:pointer}}
tbody tr:hover{{background:var(--panel2)}}
td.haken{{width:34px}}
td.zeile{{white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--mut);width:84px}}
td.wert{{white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}
td.wert .betrag{{margin-right:8px}}
tr.erledigt td.bezeichnung,tr.erledigt td.zeile{{opacity:.5;text-decoration:line-through}}
tr.erledigt td.wert{{opacity:.5}}
input[type=checkbox]{{width:18px;height:18px;cursor:pointer}}
.kopieren{{padding:2px 10px;font-size:12px}}
.kopieren.ok{{border-color:var(--good);color:var(--good)}}
.kopieren.fehler{{border-color:var(--bad);color:var(--bad)}}
.leer{{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:20px;color:var(--mut);font-size:14px}}
details{{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:4px 16px;margin:24px 0}}
summary{{cursor:pointer;padding:10px 0;color:var(--mut);font-size:13px}}
details table{{margin:0 0 12px}}
.note{{background:var(--panel);border-left:3px solid var(--warn);padding:12px 16px;
border-radius:8px;margin:16px 0;font-size:13px;color:var(--mut)}}
.note ul{{margin:6px 0 0;padding-left:18px}}
.note li{{margin-bottom:6px}}
button.erklaeren{{font:inherit;font-size:11px;cursor:pointer;background:var(--panel2);
color:var(--mut);border:1px solid var(--line);border-radius:6px;padding:1px 8px;
margin-left:6px;white-space:nowrap;vertical-align:baseline}}
button.erklaeren:hover{{border-color:var(--acc);color:var(--txt)}}
button.erklaeren.ok{{border-color:var(--good);color:var(--good)}}
button.erklaeren.fehler{{border-color:var(--bad);color:var(--bad)}}
footer{{margin-top:36px;color:var(--mut);font-size:12px;text-align:center}}
.nur-offen tr.erledigt,.nur-offen section.fertig{{display:none}}
@media print{{
  :root{{--bg:#fff;--panel:#fff;--panel2:#f2f4f8;--line:#9aa4b2;--txt:#000;--mut:#333;
  --acc:#000;--good:#065f36;--bad:#8a1414;--warn:#7a4b00;}}
  body{{background:#fff;color:#000;font-size:11pt}}
  .wrap{{max-width:none;padding:0}}
  .fortschritt{{position:static;border-bottom:1px solid #000}}
  .werkzeuge,.kopieren,button.erklaeren{{display:none !important}}
  .nur-offen tr.erledigt{{display:table-row}}
  .nur-offen section.fertig{{display:block}}
  table,.note,details{{border:1px solid #9aa4b2}}
  tbody tr{{cursor:auto}}
  tr,li,section{{break-inside:avoid}}
  thead{{display:table-header-group}}
}}
</style></head><body><div class="wrap">
<header><h1>ELSTER-Checkliste {year}</h1>
<div class="muted">{name} · zum Abhaken beim Abtippen in Mein ELSTER. Der Status bleibt nur in
diesem Browser (localStorage), nirgends sonst. <strong>Kopieren</strong> übernimmt den Betrag
in deutscher Schreibweise mit Dezimalkomma — genau so, wie ihn das ELSTER-Feld erwartet.</div></header>

<div class="fortschritt">
  <div class="balken"><div class="balken-fuellung" id="balken-fuellung"></div></div>
  <div class="fortschritt-zeile">
    <span id="fortschritt-text" class="muted">wird geladen …</span>
    <span class="werkzeuge">
      <label class="schalter"><input type="checkbox" id="nur-offen-box"> nur offene anzeigen</label>
      <button type="button" id="zuruecksetzen-btn">Alle Haken zurücksetzen</button>
    </span>
  </div>
</div>

<div id="gruppen"></div>

<details>
<summary>Belege je Quelle ({len(belege)}) — NICHT eintragen, nur zur Prüfung</summary>
<table>{row(["Anlage", "Zeile", "Bezeichnung", "Wert"], header=True)}{beleg_rows}</table>
</details>

{weitere_html}
<div class="note"><strong>Wichtige Hinweise</strong><ul>{disc_html}</ul></div>
<footer>Erstellt mit dem Skill „Steuererklärung Deutschland“ · keine Steuerberatung · Endkontrolle durch Steuerberater</footer>
</div>
<script id="zeilen-daten" type="application/json">{zeilen_json}</script>
<script>
(function() {{
  var zeilen = JSON.parse(document.getElementById('zeilen-daten').textContent);
  var praefix = 'elster-checkliste:{storage_suffix}:';
  var gruppenBehaelter = document.getElementById('gruppen');

  function lsGet(k) {{ try {{ return localStorage.getItem(k); }} catch (e) {{ return null; }} }}
  function lsSet(k, v) {{ try {{ localStorage.setItem(k, v); }} catch (e) {{}} }}
  function lsRemove(k) {{ try {{ localStorage.removeItem(k); }} catch (e) {{}} }}

  function zelle(tag, text, klasse) {{
    var el = document.createElement(tag);
    el.textContent = text;
    if (klasse) el.className = klasse;
    return el;
  }}

  // Kopieren (Clipboard-API, execCommand-Rückfall, Rückmeldung) kommt aus
  // KOPIER_JS — dieselbe Fassung wie im Dashboard, damit eine Korrektur nicht
  // in nur einer der beiden Seiten landet. Sie verdrahtet zugleich die
  // „Für Claude kopieren"-Schaltflächen der Hinweise unten.
{KOPIER_JS}

  // ---- Zeilen nach Anlage gruppieren -----------------------------------------
  // So wird die Liste in derselben Reihenfolge abgearbeitet, in der man die
  // Formulare in ELSTER ausfüllt — und die Spalte "Anlage" muss nicht in jeder
  // Zeile wiederholt werden.
  var gruppen = [];
  var nachName = {{}};
  zeilen.forEach(function(z) {{
    z._key = praefix + JSON.stringify([z.anlage, z.zeile, z.bezeichnung, z.wert]);
    if (!nachName[z.anlage]) {{
      nachName[z.anlage] = {{ name: z.anlage, zeilen: [] }};
      gruppen.push(nachName[z.anlage]);
    }}
    nachName[z.anlage].zeilen.push(z);
  }});

  if (!zeilen.length) {{
    var leer = document.createElement('div');
    leer.className = 'leer';
    leer.textContent = 'Keine einzutragenden Zeilen in diesem Report — '
      + 'es gibt nichts abzuhaken.';
    gruppenBehaelter.appendChild(leer);
  }}

  gruppen.forEach(function(g) {{
    var section = document.createElement('section');
    var h2 = document.createElement('h2');
    h2.className = 'gruppe';
    h2.appendChild(zelle('span', g.name));
    g._zaehler = zelle('span', '', 'zaehler');
    h2.appendChild(g._zaehler);
    section.appendChild(h2);

    var table = document.createElement('table');
    var thead = document.createElement('thead');
    var kopf = document.createElement('tr');
    [['', 'haken'], ['Zeile', 'zeile'], ['Bezeichnung', 'bezeichnung'],
     ['Wert', 'wert']].forEach(function(p) {{
      kopf.appendChild(zelle('th', p[0], p[1]));
    }});
    thead.appendChild(kopf);
    table.appendChild(thead);
    var tbody = document.createElement('tbody');

    g.zeilen.forEach(function(z) {{
      var tr = document.createElement('tr');
      var tdBox = zelle('td', '', 'haken');
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = lsGet(z._key) === '1';
      cb.setAttribute('aria-label', z.zeile + ' ' + z.bezeichnung);
      if (cb.checked) tr.className = 'erledigt';
      tdBox.appendChild(cb);
      tr.appendChild(tdBox);
      tr.appendChild(zelle('td', z.zeile, 'zeile'));
      tr.appendChild(zelle('td', z.bezeichnung, 'bezeichnung'));

      var tdWert = zelle('td', '', 'wert');
      var betrag = zelle('span', z.anzeige, 'betrag');
      tdWert.appendChild(betrag);
      var kopierBtn = document.createElement('button');
      kopierBtn.type = 'button';
      kopierBtn.className = 'kopieren';
      kopierBtn.textContent = 'Kopieren';
      kopierBtn.title = 'Kopiert "' + z.kopie + '" — so, wie ELSTER es erwartet';
      kopierBtn.addEventListener('click', function(e) {{
        e.stopPropagation();
        inZwischenablage(z.kopie, kopierBtn, betrag);
      }});
      tdWert.appendChild(kopierBtn);
      tr.appendChild(tdWert);

      cb.addEventListener('change', function() {{
        if (cb.checked) {{ lsSet(z._key, '1'); tr.classList.add('erledigt'); }}
        else {{ lsRemove(z._key); tr.classList.remove('erledigt'); }}
        fortschrittAktualisieren();
      }});
      // Die ganze Zeile ist die Klickfläche — bei einer langen Liste trifft man
      // sonst ständig neben die 18-Pixel-Box.
      tr.addEventListener('click', function(e) {{
        if (e.target === cb || e.target.closest('button')) return;
        cb.checked = !cb.checked;
        cb.dispatchEvent(new Event('change'));
      }});
      tbody.appendChild(tr);
    }});

    table.appendChild(tbody);
    section.appendChild(table);
    g._section = section;
    gruppenBehaelter.appendChild(section);
  }});

  function fortschrittAktualisieren() {{
    var gesamt = zeilen.length;
    var erledigt = 0;
    gruppen.forEach(function(g) {{
      var fertig = g.zeilen.filter(function(z) {{ return lsGet(z._key) === '1'; }}).length;
      erledigt += fertig;
      g._zaehler.textContent = fertig + ' / ' + g.zeilen.length;
      g._section.classList.toggle('fertig', fertig === g.zeilen.length);
    }});
    var pct = gesamt ? Math.round(100 * erledigt / gesamt) : 100;
    document.getElementById('balken-fuellung').style.width = pct + '%';
    document.getElementById('fortschritt-text').textContent = gesamt
      ? erledigt + ' von ' + gesamt + ' erledigt (' + pct + ' %)'
      : 'nichts einzutragen';
  }}
  fortschrittAktualisieren();

  document.getElementById('nur-offen-box').addEventListener('change', function(e) {{
    gruppenBehaelter.classList.toggle('nur-offen', e.target.checked);
  }});

  document.getElementById('zuruecksetzen-btn').addEventListener('click', function() {{
    zeilen.forEach(function(z) {{ lsRemove(z._key); }});
    location.reload();
  }});
}})();
</script>
</body></html>"""


# ---------------------------------------------------------------- PDF ---------
DEJAVU_KANDIDATEN = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/local/share/fonts/DejaVuSans.ttf",
    "/Library/Fonts/DejaVuSans.ttf",
    str(Path.home() / ".fonts" / "DejaVuSans.ttf"),
)


def _finde_unicode_font() -> tuple[str, str] | None:
    """(regular, bold) — bold fällt notfalls auf regular zurück."""
    for p in DEJAVU_KANDIDATEN:
        if os.path.isfile(p):
            bold = p.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
            return p, (bold if os.path.isfile(bold) else p)
    # letzte Chance: irgendeine DejaVuSans.ttf unter /usr/share/fonts
    for wurzel in ("/usr/share/fonts", "/usr/local/share/fonts"):
        for dirpath, _dirs, files in os.walk(wurzel):
            if "DejaVuSans.ttf" in files:
                p = os.path.join(dirpath, "DejaVuSans.ttf")
                bold = os.path.join(dirpath, "DejaVuSans-Bold.ttf")
                return p, (bold if os.path.isfile(bold) else p)
    return None


class _Text:
    """Text für das PDF aufbereiten.

    Mit eingebettetem Unicode-Font bleibt „Ayşe Öztürk“ stehen. Ohne ihn muss auf
    latin-1 reduziert werden — dann wird protokolliert, *welche* Zeichen ersetzt
    wurden, statt sie stillschweigend zu verstümmeln.
    """

    def __init__(self, unicode_ok: bool):
        self.unicode_ok = unicode_ok
        self.ersetzt: set[str] = set()

    def __call__(self, s) -> str:
        s = _s(s)
        if self.unicode_ok:
            return s
        s = (s.replace("€", "EUR").replace("–", "-").replace("—", "-")
             .replace("−", "-").replace("„", '"').replace("“", '"')
             .replace("’", "'").replace("…", "..."))
        out = []
        for ch in s:
            try:
                ch.encode("latin-1")
                out.append(ch)
            except UnicodeEncodeError:
                self.ersetzt.add(ch)
                out.append("?")
        return "".join(out)

    def warnung(self) -> str | None:
        if not self.ersetzt:
            return None
        zeichen = " ".join(f"{c!r} (U+{ord(c):04X})" for c in sorted(self.ersetzt))
        return ("WARNUNG: Kein Unicode-Font gefunden — im PDF wurden folgende Zeichen "
                f"durch '?' ersetzt: {zeichen}. Namen/Bezeichnungen im PDF können damit "
                "falsch geschrieben sein. Abhilfe: DejaVuSans.ttf installieren "
                "(z. B. Paket 'fonts-dejavu-core') und Export wiederholen.")


def _version_tuple(v) -> tuple:
    teile = []
    for t in str(v).split(".")[:3]:
        m = re.match(r"\d+", t)
        teile.append(int(m.group(0)) if m else 0)
    return tuple(teile) or (0,)


def _import_fpdf():
    """fpdf2 >= 2.x — das alte fpdf 1.7.2 importiert zwar, stirbt aber später an
    `new_x=`/`new_y=`. Lieber sofort und verständlich abbrechen.

    Gibt (FPDF, Versions-Tupel) zurück."""
    try:
        import fpdf  # noqa: F401
        from fpdf import FPDF
    except Exception as e:  # ImportError, aber auch kaputte Installationen
        raise RuntimeError(
            f"fpdf2 nicht verwendbar ({e.__class__.__name__}: {e}). "
            "Bitte: pip install fpdf2 --break-system-packages") from e
    version = getattr(fpdf, "FPDF_VERSION", None) or getattr(fpdf, "__version__", "0")
    vt = _version_tuple(version)
    if vt[0] < 2:
        raise RuntimeError(
            f"Gefunden wurde das alte 'fpdf' {version}; benötigt wird fpdf2 >= 2.0. "
            "Bitte: pip uninstall fpdf && pip install fpdf2 --break-system-packages")
    return FPDF, vt


def render_pdf(report: dict, out_path: Path):
    FPDF, fpdf_version = _import_fpdf()
    report = _dict(report)
    meta = _dict(report.get("meta"))
    a = _dict(report.get("anlagen"))
    b = _dict(report.get("berechnung"))
    kr = _dict(report.get("krypto_detail"))
    p23 = _dict(kr.get("paragraph_23"))
    p22 = _dict(kr.get("paragraph_22_nr_3"))
    year = _s(meta.get("steuerjahr"))
    tp = _dict(meta.get("steuerpflichtiger"))

    class Report(FPDF):
        def footer(self):
            self.set_y(-14)
            self.set_font(self.base_font, "I", 8)
            self.set_text_color(120, 130, 150)
            self.cell(0, 8, T(f"Seite {self.page_no()} von {{nb}}"), align="C")
            self.set_text_color(20, 20, 20)

    pdf = Report()
    font = _finde_unicode_font()
    if font:
        regular, bold = font
        for style, path in (("", regular), ("B", bold), ("I", regular)):
            try:
                # Bis fpdf2 2.5.0 muss uni=True gesetzt werden, sonst wird die TTF
                # latin-1 eingebettet; ab 2.5.1 ist Unicode Standard und uni veraltet.
                if fpdf_version < (2, 5, 1):
                    pdf.add_font("DejaVu", style, path, uni=True)
                else:
                    pdf.add_font("DejaVu", style, path)
            except TypeError:
                pdf.add_font("DejaVu", style, path)
        pdf.base_font = "DejaVu"
        T = _Text(True)
    else:
        pdf.base_font = "Helvetica"
        T = _Text(False)
    F = pdf.base_font

    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font(F, "B", 20)
    pdf.cell(0, 12, T(f"Steuererklärung {year}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(F, "", 11)
    pdf.set_text_color(110, 120, 140)
    pdf.cell(0, 7, T(f"{_s(tp.get('name')) or '—'}  ·  {_s(meta.get('veranlagung'))}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, T("TaxReport - alle Anlagen + Krypto  -  keine Steuerberatung"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(20, 20, 20)
    pdf.ln(4)

    def section(title):
        pdf.ln(3)
        pdf.set_font(F, "B", 13)
        pdf.set_text_color(20, 80, 160)
        pdf.cell(0, 9, T(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(20, 20, 20)
        pdf.set_font(F, "", 10)

    def kv(label, value, bold_label=False):
        pdf.set_font(F, "B" if bold_label else "", 10)
        pdf.cell(120, 7, T(label))
        pdf.set_font(F, "B", 10)
        pdf.cell(0, 7, T(fmt_eur(value)), new_x="LMARGIN", new_y="NEXT", align="R")
        pdf.set_font(F, "", 10)

    def fit(text, w):
        """Zellinhalt auf die Spaltenbreite kürzen — sichtbar mit '…', nie überlappend."""
        s = T(text)
        if pdf.get_string_width(s) <= w - 2:
            return s
        ell = T("…")
        while s and pdf.get_string_width(s + ell) > w - 2:
            s = s[:-1]
        return s + ell

    def fit_menge(x, w):
        """Mengen dürfen nicht abgeschnitten werden — sonst steht im Steuer-PDF eine
        um Zehnerpotenzen falsche Menge. Passt die Zahl nicht, wird sie exponentiell
        notiert statt gekürzt."""
        s = T(fmt_menge(x, 24))
        if pdf.get_string_width(s) <= w - 2:
            return s
        d = to_decimal_or(x, None)
        if d is not None:
            for prec in (4, 3, 2):
                alt = T(f"{d:.{prec}E}")
                if pdf.get_string_width(alt) <= w - 2:
                    return alt
        return fit(s, w)

    def tabelle(spalten, zeilen, h=6, size=8):
        """Tabelle mit auf jeder Seite wiederholter Kopfzeile."""
        def kopf():
            pdf.set_font(F, "B", size)
            for titel, w in spalten:
                pdf.cell(w, h, fit(titel, w), border=1)
            pdf.ln(h)
            pdf.set_font(F, "", size)

        kopf()
        for zeile in zeilen:
            if pdf.get_y() + h > pdf.page_break_trigger:
                pdf.add_page()
                kopf()
            for (c, (_titel, w)) in zip(zeile, spalten):
                pdf.cell(w, h, fit(c, w), border=1)
            pdf.ln(h)

    section("Zusammenfassung")
    kv("Zu versteuerndes Einkommen", b.get("zu_versteuerndes_einkommen"))
    est = b.get("einkommensteuer_schaetzung")
    pdf.set_font(F, "", 10)
    pdf.cell(120, 7, T(f"Einkommensteuer (Schätzung, {_s(b.get('tarif'))})"))
    pdf.set_font(F, "B", 10)
    pdf.cell(0, 7, T(fmt_eur(est) if est not in (None, "") else "n/a"),
             new_x="LMARGIN", new_y="NEXT", align="R")
    pdf.set_font(F, "", 10)
    if b.get("soli_schaetzung") not in (None, ""):
        kv("Solidaritätszuschlag (Schätzung)", b.get("soli_schaetzung"))
    if b.get("kirchensteuer_schaetzung") not in (None, ""):
        kv("Kirchensteuer (Schätzung)", b.get("kirchensteuer_schaetzung"))

    section("Einkünfte nach Anlagen")
    kv("Anlage N - Nichtselbständige Arbeit", _g(a, "N", "einkuenfte", default="0"))
    kv("Anlage KAP - Kapitalerträge (nicht in der Summe)",
       _g(a, "KAP", "kapitalertraege", default="0"))
    kv("Anlage SO - Sonstige (Krypto)", _g(a, "SO", "einkuenfte_gesamt", default="0"))
    for key, label in (("V", "Anlage V - Vermietung und Verpachtung"),
                       ("S", "Anlage S - Selbständige Arbeit"),
                       ("G", "Anlage G - Gewerbebetrieb")):
        wert = _g(a, key, ANLAGEN_FELD[key], default="0")
        if _num(wert) != 0:
            kv(label, wert)
    kv("Summe der Einkünfte (ohne Anlage KAP)", summe_einkuenfte(report), bold_label=True)

    section("Krypto - Private Veräußerungsgeschäfte (§ 23 EStG)")
    kv("Netto-Ergebnis § 23", p23.get("netto_ergebnis_eur"))
    kv("Freigrenze", p23.get("freigrenze_eur"))
    pdf.set_font(F, "", 10)
    pdf.cell(0, 7, T("Freigrenze überschritten: " +
                     ("ja" if p23.get("freigrenze_ueberschritten") else "nein")),
             new_x="LMARGIN", new_y="NEXT")
    kv("Davon steuerpflichtig", p23.get("steuerpflichtiger_betrag_eur"))
    kv("Steuerfrei (> 1 Jahr gehalten)", p23.get("summe_steuerfrei_gt_1_jahr_eur"))
    kv("Staking/Lending § 22 Nr. 3 (steuerpfl.)", p22.get("steuerpflichtig_eur"))

    disposals = [d for d in _list(p23.get("disposals")) if isinstance(d, dict)]
    if disposals:
        pdf.ln(2)
        MAX = 40
        spalten = [("Asset", 18), ("Menge", 30), ("Ansch.", 22), ("Verauss.", 22),
                   ("Tage", 14), ("Gewinn EUR", 30), ("Status", 22)]
        pdf.set_font(F, "", 8)  # Breitenmessung in der Schriftgröße der Tabelle
        zeilen = [[_s(d.get("asset")), fit_menge(d.get("amount"), 30),
                   _s(d.get("acquisition_date")), _s(d.get("disposal_date")),
                   _s(d.get("held_days")), fmt_eur(d.get("gain_eur")),
                   "steuerpfl." if d.get("taxable") else "frei"]
                  for d in disposals[:MAX]]
        tabelle(spalten, zeilen)
        if len(disposals) > MAX:
            pdf.set_font(F, "I", 8)
            pdf.multi_cell(pdf.epw, 5, T(
                f"... {len(disposals) - MAX} weitere Veräußerungen: vollständig im "
                f"HTML-Report und in taxreport.json"), new_x="LMARGIN", new_y="NEXT")

    pdf.add_page()
    section("ELSTER-Feld-Mapping (manuelle Eingabe in Mein ELSTER)")
    mapping = elster_rows(report)
    if mapping:
        tabelle([("Anlage", 36), ("Zeile", 24), ("Bezeichnung", 90), ("Wert", 30)],
                [[_s(e.get("anlage")), _s(e.get("zeile")), _s(e.get("bezeichnung")),
                  _s(e.get("wert"))] for e in mapping])
    else:
        pdf.set_font(F, "", 9)
        pdf.multi_cell(pdf.epw, 5, T("Keine Mapping-Zeilen im Report."),
                       new_x="LMARGIN", new_y="NEXT")

    unsicher = report.get("unsicherheit") or {}
    if unsicher.get("posten"):
        section("Was diese Schätzung nicht enthält")
        pdf.set_font(F, "", 9)
        pdf.multi_cell(pdf.epw, 5, T(f"Gesamtbild: {unsicher.get('gesamtrichtung', '—')}"),
                       new_x="LMARGIN", new_y="NEXT")
        for p in unsicher["posten"]:
            groesse = p.get("groessenordnung") or "—"
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(pdf.epw, 5, T(f"- {p['posten']}: Steuer {p['richtung']} "
                                         f"({groesse}; {p['fundstelle']})"))

    disclaimer, hinweise = report_texte(report)
    section("Wichtige Hinweise")
    pdf.set_font(F, "", 9)
    for x in disclaimer + hinweise:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.epw, 5, T("- " + x))
    if not (disclaimer or hinweise):
        pdf.multi_cell(pdf.epw, 5, T("- Keine Steuerberatung. Endkontrolle durch "
                                     "Steuerberater; maßgeblich ist die ELSTER-Berechnung."))

    pdf.output(str(out_path))
    warnung = T.warnung()
    if warnung:
        print(warnung, file=sys.stderr)


# ---------------------------------------------------------------- ELSTER ------
def _csv_zelle(v) -> str:
    """Deutsche Dezimaltrennung (Jahre/Datumsangaben bleiben unberührt) +
    Schutz gegen Formel-Injection in Excel/LibreOffice."""
    return csv_safe(de_dezimal(_s(v)))


def render_elster(report: dict, csv_path: Path, json_path: Path):
    report = _dict(report)
    mapping = elster_rows(report)
    disclaimer, hinweise = report_texte(report)
    year = _g(report, "meta", "steuerjahr", default=None)

    # Die ELSTER-Dateien sind genau das, was in Mein ELSTER abgetippt wird —
    # der Pflichthinweis darf hier am wenigsten fehlen (SKILL.md).
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([_csv_zelle(f"# ELSTER-Feld-Mapping Steuerjahr {_s(year)} "
                               f"- manuelle Eingabe in Mein ELSTER")])
        for x in disclaimer:
            w.writerow([_csv_zelle("# Hinweis: " + x)])
        for x in hinweise:
            w.writerow([_csv_zelle("# Hinweis: " + x)])
        if not disclaimer:
            w.writerow([_csv_zelle("# Hinweis: Keine Steuerberatung. Maßgeblich ist die "
                                   "Berechnung in ELSTER; Endkontrolle durch Steuerberater.")])
        w.writerow([])
        w.writerow(["Anlage", "Zeile", "Bezeichnung", "Wert"])
        for e in mapping:
            w.writerow([_csv_zelle(e.get("anlage")), _csv_zelle(e.get("zeile")),
                        _csv_zelle(e.get("bezeichnung")), _csv_zelle(e.get("wert"))])

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"steuerjahr": year,
                   "disclaimer": disclaimer,
                   "hinweise": hinweise,
                   "elster_mapping": mapping}, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- CLI ---------
def lade_report(pfad: str) -> dict:
    try:
        with open(pfad, encoding="utf-8") as f:
            report = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"Datei nicht gefunden: {pfad}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"{pfad} ist kein gültiges JSON (Zeile {e.lineno}, Spalte {e.colno}): {e.msg}")
    except OSError as e:
        raise SystemExit(f"{pfad} nicht lesbar: {e}")
    if not isinstance(report, dict):
        raise SystemExit(f"{pfad} enthält kein TaxReport-Objekt (gefunden: {type(report).__name__}).")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TaxReport nach HTML / PDF / ELSTER exportieren")
    ap.add_argument("taxreport", help="taxreport.json")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--formats", nargs="+", default=["html", "pdf", "elster"],
                    choices=["html", "pdf", "elster", "checkliste"])
    args = ap.parse_args(argv)

    report = lade_report(args.taxreport)
    outdir = Path(args.outdir)
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SystemExit(f"Ausgabeverzeichnis {outdir} nicht anlegbar: {e}")

    year = safe_filename(_g(report, "meta", "steuerjahr", default="report"))
    fehler = []

    def melde(*pfade):
        for p in pfade:
            print(f"Erstellt: {p}")  # sofort, nicht erst am Ende

    if "html" in args.formats:
        try:
            p = outdir / f"taxreport_{year}.html"
            p.write_text(render_html(report), encoding="utf-8")
            melde(p)
        except Exception as e:
            fehler.append(f"HTML: {e.__class__.__name__}: {e}")
            print(f"FEHLER HTML: {e}", file=sys.stderr)

    if "pdf" in args.formats:
        try:
            p = outdir / f"taxreport_{year}.pdf"
            render_pdf(report, p)
            melde(p)
        except (RuntimeError, OSError, SystemExit) as e:
            fehler.append(f"PDF: {e}")
            print(f"FEHLER PDF: {e}", file=sys.stderr)
        except Exception as e:
            fehler.append(f"PDF: {e.__class__.__name__}: {e}")
            print(f"FEHLER PDF: {e.__class__.__name__}: {e}", file=sys.stderr)

    if "elster" in args.formats:
        try:
            c = outdir / f"elster_mapping_{year}.csv"
            j = outdir / f"elster_mapping_{year}.json"
            render_elster(report, c, j)
            melde(c, j)
        except Exception as e:
            fehler.append(f"ELSTER: {e.__class__.__name__}: {e}")
            print(f"FEHLER ELSTER: {e}", file=sys.stderr)

    if "checkliste" in args.formats:
        try:
            p = outdir / f"elster_checkliste_{year}.html"
            p.write_text(render_checkliste(report), encoding="utf-8")
            melde(p)
        except Exception as e:
            fehler.append(f"CHECKLISTE: {e.__class__.__name__}: {e}")
            print(f"FEHLER CHECKLISTE: {e}", file=sys.stderr)

    if fehler:
        print("\nNicht erstellt:", file=sys.stderr)
        for f_ in fehler:
            print(f"  {f_}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
