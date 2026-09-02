#!/usr/bin/env python3
"""
export_report.py — Rendert einen TaxReport (taxreport.json) in:
  * HTML-Dashboard (self-contained, keine externen Abhängigkeiten, mit Druck-Stylesheet)
  * PDF-Report (fpdf2; benötigt: pip install fpdf2 --break-system-packages)
  * ELSTER-Feld-Mapping als CSV und JSON (manuelle Eingabe in Mein ELSTER)

Grundregeln dieses Exporters:
  * Der Disclaimer steht in **jedem** Format — auch in den ELSTER-Dateien, aus denen
    abgetippt wird (SKILL.md: „Diesen Hinweis nicht weglassen.“).
  * Es wird gerendert, was im Report steht — kein festes Zeilen-Set, keine stillen
    Auslassungen. Fehlende/kaputte Felder erzeugen „—“, keinen Absturz.
  * Beträge in der CSV in deutscher Notation (Komma), damit deutsches Excel sie als
    Zahl und nicht als Text importiert.

Aufruf:
  python export_report.py taxreport.json --outdir ./out --formats html pdf elster
"""

from __future__ import annotations

import argparse
import csv
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

    def row(cells, header=False, cls=""):
        tag = "th" if header else "td"
        tds = "".join(f"<{tag}>{c}</{tag}>" for c in cells)
        attr = f' class="{cls}"' if cls else ""
        return f"<tr{attr}>{tds}</tr>"

    def esc(x):
        return html.escape(_s(x))

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
    disc_html = "".join(f"<li>{esc(x)}</li>" for x in disclaimer) or "<li>—</li>"
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
                         + "".join(f"<li>{esc(x)}</li>" for x in hinweise) + "</ul></div>")

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
</div></body></html>"""


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
                    choices=["html", "pdf", "elster"])
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

    if fehler:
        print("\nNicht erstellt:", file=sys.stderr)
        for f_ in fehler:
            print(f"  {f_}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
