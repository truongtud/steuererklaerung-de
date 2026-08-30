#!/usr/bin/env python3
"""
parse_koinly.py — Liest einen Koinly-Steuerbericht (PDF) und erzeugt ein Krypto-Ergebnis
im Schema von krypto_fifo.compute_crypto_tax — OHNE erneutes FIFO.

Begründung: Koinly hat FIFO bereits wallet-übergreifend gerechnet. Der Report enthält je
Veräußerung Kostenbasis, Erlös, Gewinn/Verlust und die Kurz-/Langfristig-Einstufung. Diese
werden direkt übernommen (autoritativer als ein Neu-FIFO auf unvollständiger Historie).

Extrahiert zusätzlich: Einnahmen (§ 22 Nr. 3: Reward/Lending/Mining/Airdrop/Fork/…),
Futures-Ergebnis (separat anzugeben, i. d. R. Anlage KAP / Termingeschäfte § 20 Abs. 2)
und Ausgaben (z. B. Loan fees — für Privatanleger meist NICHT abziehbar).

Zwei Sicherheitsnetze, ohne die ein Parser still falsch rechnet:
  * Alle Beträge laufen über steuerlib.to_decimal (DE *und* EN Notation, Vorzeichen in
    allen Schreibweisen) — unlesbare Werte werfen, statt still 0 zu liefern.
  * Die geparsten Summen werden gegen die im Report selbst ausgewiesenen Summen
    (und die Anzahl der Veräußerungen) abgeglichen. Weicht etwas ab, bricht das
    Skript mit Exit-Code 1 ab, statt eine zu niedrige Zahl zu melden.
Nicht zugeordnete Zeilen innerhalb der Veräußerungstabelle werden gezählt und gemeldet.

Ausgabe: <pdf-name>.krypto_result.json  -> nutzbar mit
         build_taxreport.py steuerdaten.json --krypto-result <datei>.json

KEINE Steuerberatung. Werte gegen den Original-Report prüfen.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steuerlib as sl  # noqa: E402

D = Decimal

EPILOG = """\
WICHTIG — Freigrenzen werden hier NICHT angewendet:
Die Freigrenzen nach § 23 Abs. 3 Satz 5 EStG (1.000 € ab 2024, davor 600 €) und
§ 22 Nr. 3 Satz 2 EStG (256 €) gelten pro Person und Kalenderjahr über ALLE Broker
und Tools hinweg. Würde jeder Report sie für sich anwenden, blieben zwei Ergebnisse
von je 800 € steuerfrei, obwohl ihre Summe (1.600 €) steuerpflichtig ist.
Dieses Skript liefert deshalb nur die Roh-Nettobeträge ("freigrenze_angewendet": false).
build_taxreport.py wendet die Freigrenze einmal auf die Summe an und akzeptiert dafür
mehrere --krypto-result-Dateien.
"""


# ───────────────────────────────────────────────────────── Textgewinnung ──────
def _pdf_text(path: str) -> str:
    """Volltext aller Seiten (pdfplumber, Fallback PyMuPDF).

    Nur ein *fehlendes* pdfplumber führt zum Fallback — echte PDF-Fehler sollen
    sichtbar werden und nicht im nächsten Backend verschwinden.
    """
    try:
        import pdfplumber
    except ImportError:
        import fitz
        doc = fitz.open(path)
        return "\n".join((p.get_text() or "") for p in doc)
    with pdfplumber.open(path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def is_koinly(text: str) -> bool:
    return "Koinly" in text and bool(
        re.search(r"Steuerbericht|STEUERJAHR|Tax Report|TAX YEAR", text, re.I))


def detect_year(text: str, override=None) -> int:
    if override:
        return int(override)
    m = (re.search(r"Steuerbericht\s+(\d{4})", text)
         or re.search(r"STEUERJAHR\s+(\d{4})", text, re.I)
         or re.search(r"Tax\s+Report\s+(\d{4})", text, re.I)
         or re.search(r"TAX\s+YEAR\s+(\d{4})", text, re.I))
    if m:
        return int(m.group(1))
    from datetime import datetime as _dt
    return _dt.now().year


# ────────────────────────────────────────────────────── Veräußerungstabelle ───
# Toleranter als das Original: 1-stellige Tage, Sekunden, ISO-Datum, DE- *und*
# EN-Tausendertrennung, Vorzeichen in jeder Schreibweise, Assetnamen mit
# Leerzeichen, fehlende Wallet-/Notizspalte, deutsche *und* englische Haltedauer.
_DT = r"\d{1,4}[./-]\d{1,2}[./-]\d{2,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
_NUM = r"(?:\(\s*)?[-−–+]?\s*\d[\d.,]*(?:\s*\))?-?"
_HALTE = r"Kurzfristig|Langfristig|Short[- ]?term|Long[- ]?term"

ROW_RE = re.compile(
    rf"^({_DT})\s+({_DT})\s+(.+?)\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})"
    rf"(?:\s+(.+?))?\s+({_HALTE})\s*$",
    re.IGNORECASE)

_ZEILE_MIT_DATUM = re.compile(rf"^{_DT}\s")
_KOPFZEILE = re.compile(
    r"(Verkaufsdatum|Kaufdatum|Erwerbsdatum|Ver(?:ä|ae)u(?:ß|ss)erungsdatum|"
    r"Kostenbasis|Cost basis|Erl(?:ö|oe)s|Proceeds|Haltedauer|Holding period|"
    r"Date (?:of )?(?:sold|acquired|disposal)|"
    r"Wallet|Anmerkung|Label)", re.I)
_FUSSZEILE = re.compile(
    r"^(Seite|Page)\s*\d+|^\d+\s*/\s*\d+$|^Koinly\b|Steuerbericht|Tax Report", re.I)
_TAB_ENDE = re.compile(
    r"^(Zusammenfassung|Summary|Gesamt|Total|Einnahmen|Income|Ausgaben|Expenses|"
    r"Futures|Derivate|Offene|Open)\b", re.I)
_TAB_START = re.compile(
    r"(Kapitalgewinn|Capital gain|Ver(?:ä|ae)u(?:ß|ss)erungen|Disposals)", re.I)


def _menge(tok: str, hint: str) -> Decimal:
    """Coin-Menge. '0.00047383' ist auch in einem DE-Report keine Tausenderzahl,
    deshalb wird bei führender Null ohne Locale-Hint gelesen."""
    t = str(tok).strip()
    if re.match(r"^0[.,]", t):
        return sl.to_decimal(t)
    return sl.to_decimal(t, locale_hint=hint)


def _sieht_aus_wie_datenzeile(line: str) -> bool:
    return bool(_ZEILE_MIT_DATUM.match(line)) or len(re.findall(r"\d[\d.,]*", line)) >= 4


def _ist_langfristig(halte: str) -> bool:
    h = halte.lower()
    return h.startswith("lang") or h.startswith("long")


def parse_disposals(text: str, *, locale_hint: str | None = None,
                    dateformat: str | None = None):
    """Liest die Veräußerungstabelle.

    Rückgabe: (disposals, nicht_zugeordnete_zeilen).
    Nicht zugeordnete Zeilen *innerhalb* der Tabelle werden gesammelt statt
    stillschweigend übersprungen — genau dort verschwinden sonst die größten Gewinne.
    """
    hint = locale_hint or sl.detect_locale(text)
    dayfirst = (dateformat or "de") != "en"
    disposals: list[dict] = []
    unmatched: list[str] = []
    roh: list[tuple[str, str, bool]] = []   # für die Datumsformat-Prüfung
    in_tab = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = ROW_RE.match(line)
        if m:
            in_tab = True
            sell_dt, buy_dt, asset, amount, kosten, erloes, gewinn, anm, halte = m.groups()
            roh.append((sell_dt, buy_dt, _ist_langfristig(halte)))
            sd = sl.parse_datetime(sell_dt, dayfirst=dayfirst)
            bd = sl.parse_datetime(buy_dt, dayfirst=dayfirst)
            cost = sl.to_decimal(kosten, locale_hint=hint)
            proceeds = sl.to_decimal(erloes, locale_hint=hint)
            gain = sl.to_decimal(gewinn, locale_hint=hint)
            langfristig = _ist_langfristig(halte)
            note = (anm or "").strip()
            disposals.append({
                "asset": asset.strip(),
                "disposal_date": sd.date().isoformat(),
                "acquisition_date": bd.date().isoformat(),
                "amount": str(_menge(amount, hint)),
                "proceeds_eur": str(sl.q2(proceeds)),
                "cost_basis_eur": str(sl.q2(cost)),
                "fee_eur": "0.00",
                "gain_eur": str(sl.q2(gain)),
                "held_days": (sd - bd).days,
                "holding_period_met": langfristig,       # > 1 Jahr -> steuerfrei
                "taxable": not langfristig,
                "note": (note + " " if note else "") + "Quelle: Koinly",
            })
            continue
        if _TAB_ENDE.match(line):
            in_tab = False
            continue
        if _KOPFZEILE.search(line):
            in_tab = True
            continue
        if _TAB_START.search(line) and not re.search(r"\d", line):
            in_tab = True
            continue
        if _FUSSZEILE.search(line):
            continue
        if (in_tab and _sieht_aus_wie_datenzeile(line)) or _ZEILE_MIT_DATUM.match(line):
            unmatched.append(line)

    if dateformat is None:
        _pruefe_datumsformat(roh)
    return disposals, unmatched


def _widersprueche(roh, dayfirst: bool):
    """Zählt Zeilen, deren Datumspaar unter dieser Auslegung unmöglich ist.

    Unmöglich heißt: Verkauf vor Kauf, oder die Haltedauer widerspricht der von
    Koinly ausgewiesenen Einstufung Kurz-/Langfristig."""
    n = 0
    for sell_dt, buy_dt, langfristig in roh:
        try:
            sd = sl.parse_datetime(sell_dt, dayfirst=dayfirst)
            bd = sl.parse_datetime(buy_dt, dayfirst=dayfirst)
        except sl.ParseError:
            n += 1
            continue
        tage = (sd - bd).days
        if tage < 0 or (langfristig and tage < 364) or (not langfristig and tage > 367):
            n += 1
    return n


def _jahre(roh, dayfirst: bool):
    return {sl.parse_datetime(s, dayfirst=dayfirst).year for s, _b, _l in roh}


def _pruefe_datumsformat(roh):
    """TT/MM vs. MM/TT: ein US-Export verschiebt sonst lautlos Zeitraum und Jahr.

    Nur wenn Datumsangaben tatsächlich mehrdeutig sind (Tag ≤ 12) und die beiden
    Auslegungen zu unterschiedlichen Ergebnissen führen, wird abgebrochen."""
    mehrdeutig = [s for s, b, _ in roh if sl.date_ambiguous(s) or sl.date_ambiguous(b)]
    if not mehrdeutig:
        return
    w_de, w_en = _widersprueche(roh, True), _widersprueche(roh, False)
    jahre_gleich = _jahre(roh, True) == _jahre(roh, False)
    if w_de == w_en and jahre_gleich:
        return  # Auslegung ändert nichts Wesentliches
    grund = (f"unter TT/MM/JJJJ {w_de} widersprüchliche Zeile(n), unter MM/TT/JJJJ {w_en}"
             if w_de != w_en else
             "die betroffenen Veräußerungen fallen je nach Auslegung in verschiedene "
             "Steuerjahre")
    raise sl.ParseError(
        f"Datumsformat des Reports ist nicht eindeutig (z. B. {mehrdeutig[0]}): {grund}.\n"
        "→ Format im Original prüfen und mit --dateformat de (TT/MM/JJJJ) bzw. "
        "--dateformat en (MM/TT/JJJJ) erzwingen.")


# ──────────────────────────────────────────────── Summen aus dem Report ───────
_GESAMT_PATTERNS = [
    r"^\s*Netto-?Kapitalgewinn(?:e|s)?",
    r"^\s*Gesamt(?:e[rn]?\s+)?Kapitalgewinn(?:e|s)?",
    r"^\s*Kapitalgewinn(?:e|s)?\s+gesamt",
    r"^\s*Kapitalgewinn(?:e|s)?",
    r"^\s*Ver(?:ä|ae)u(?:ß|ss)erungsgewinn(?:e|s)?",
    r"^\s*Gesamtgewinn",
    r"^\s*Reingewinn",
    r"^\s*Net\s+capital\s+gains?",
    r"^\s*Total\s+(?:net\s+)?(?:capital\s+)?gains?",
    r"^\s*Capital\s+gains?",
]
# ae/ss-Schreibweisen mitnehmen: manche PDF-Textebenen liefern keine Umlaute.
_VERAEUSS = r"Ver(?:ä|ae)u(?:ß|ss)erungen|Verk(?:ä|ae)ufe|Disposals"
_ANZAHL_PATTERNS = [
    r"^\s*(?:Anzahl\s+(?:der\s+)?)?(?:" + _VERAEUSS + r"|Transaktionen|Transactions)"
    r"\s*[:.]?\s*(\d{1,6})\s*$",
    r"^\s*(\d{1,6})\s+(?:" + _VERAEUSS + r")\b",
]


def report_gesamtgewinn(text: str, hint: str) -> Decimal | None:
    """Die vom Report selbst ausgewiesene Kapitalgewinn-Summe (Vergleichswert)."""
    for pat in _GESAMT_PATTERNS:
        m = re.search(pat + r"[^\d\-−–(+\n]*(" + _NUM + r")", text, re.I | re.M)
        if m:
            try:
                return sl.to_decimal(m.group(1), locale_hint=hint)
            except sl.ParseError:
                continue
    return None


def report_anzahl(text: str) -> int | None:
    for pat in _ANZAHL_PATTERNS:
        m = re.search(pat, text, re.I | re.M)
        if m:
            return int(m.group(1))
    return None


# ───────────────────────────────────────────── Einnahmen / Ausgaben / Futures ─
_EINNAHMEN_LABELS = {
    "Airdrop": [r"Airdrops?"],
    "Fork": [r"(?:Hard\s+)?Forks?"],
    "Mining": [r"Mining"],
    "Reward": [r"Rewards?", r"Belohnungen?", r"Prämien?", r"Staking(?:-Erträge)?"],
    "Salary": [r"Salary", r"Gehalt", r"Lohn"],
    "Lending interest": [r"Lending\s+interest", r"Kreditzinsen", r"Lending-?Zinsen",
                         r"Zinsen"],
    "Other income": [r"Other\s+income", r"Sonstige\s+Einnahmen", r"Andere\s+Einnahmen"],
}
# "Cost" darf nicht an "Cost basis"/"Kostenbasis" hängenbleiben.
_AUSGABEN_LABELS = {
    "Margin fee": [r"Margin\s*-?\s*(?:fee|Gebühr)e?n?"],
    "Loan fee": [r"Loan\s*fees?", r"Kredit(?:gebühr|zins)en?", r"Darlehensgebühren?"],
    "Cost": [r"Costs?(?!\s*basis)", r"Kosten(?!basis)"],
    "Transfer fees": [r"Transfer\s*fees?", r"(?:Übertragungs|Transfer)gebühren?"],
}
# § 22 Nr. 3 EStG: sonstige Leistungen. Airdrop/Fork gehören dazu, wenn ihnen eine
# Leistung gegenübersteht — sie fehlten bisher und wurden schlicht nicht erfasst.
_RELEVANT_22_3 = ["Reward", "Lending interest", "Mining", "Other income",
                  "Airdrop", "Fork"]

_EIN_START = r"(?:Zusammenfassung\s+(?:der\s+)?Einnahmen|Einnahmen(?:übersicht)?|" \
             r"Einkünfte|Income\s+summary|Income)"
_AUS_START = r"(?:Zusammenfassung\s+(?:der\s+)?Ausgaben|Ausgaben(?:übersicht)?|" \
             r"Expenses\s+summary|Expenses|Kosten\s+summary)"
_ABSCHNITTE = [r"Zusammenfassung", r"Kapitalgewinn", r"Capital\s+gain", r"Einnahmen",
               r"Income", r"Ausgaben", r"Expenses", r"Futures", r"Derivate",
               r"Offene\s+Bestände", r"Open\s+positions"]


def _block(text: str, start_pat: str) -> str | None:
    """Text vom gefundenen Abschnittstitel bis zum nächsten Abschnittstitel.

    Ohne diese Eingrenzung matcht 'Cost' irgendwo im Dokument und ein
    deutschsprachiger Report liefert lautlos lauter Nullen."""
    m = re.search(r"^\s*" + start_pat + r"\b.*$", text, re.I | re.M)
    if not m:
        return None
    rest = text[m.end():]
    enden = [e.start() for e in
             (re.search(r"^\s*" + p + r"\b", rest, re.I | re.M) for p in _ABSCHNITTE)
             if e]
    return rest[:min(enden)] if enden else rest


def _label_wert(block: str, muster: list[str], hint: str) -> Decimal | None:
    for lab in muster:
        m = re.search(r"^\s*" + lab + r"\s*[:.]?[^\d\-−–(+\n]*(" + _NUM + r")",
                      block, re.I | re.M)
        if m:
            try:
                return sl.to_decimal(m.group(1), locale_hint=hint)
            except sl.ParseError:
                continue
    return None


def parse_income(text: str, hint: str):
    """§ 22 Nr. 3 relevante Einnahmen aus dem Einnahmen-Block (DE oder EN)."""
    warnungen: list[str] = []
    block = _block(text, _EIN_START)
    if block is None:
        warnungen.append(
            "WARNUNG: Einnahmen-Abschnitt im Report nicht gefunden — § 22 Nr. 3 "
            "(Staking/Lending/Mining/Airdrop) konnte NICHT ausgelesen werden. "
            "Werte manuell aus dem Report übernehmen.")
        block = ""
    detail: dict[str, str | None] = {}
    for key, muster in _EINNAHMEN_LABELS.items():
        v = _label_wert(block, muster, hint)
        detail[key] = str(sl.q2(v)) if v is not None else None
    relevant = sum((sl.to_decimal(detail[k]) for k in _RELEVANT_22_3
                    if detail.get(k) is not None), D("0"))
    return detail, relevant, warnungen


def parse_expenses(text: str, hint: str):
    warnungen: list[str] = []
    block = _block(text, _AUS_START)
    if block is None:
        warnungen.append("HINWEIS: Ausgaben-Abschnitt nicht gefunden (0 € angesetzt).")
        block = ""
    detail: dict[str, str | None] = {}
    total = D("0")
    for key, muster in _AUSGABEN_LABELS.items():
        v = _label_wert(block, muster, hint)
        detail[key] = str(sl.q2(v)) if v is not None else None
        if v is not None:
            total += v
    return detail, total, warnungen


def parse_futures(text: str, hint: str) -> Decimal:
    for lab in [r"Realisierte[rs]?\s+Gewinn\s+und\s+Verlust",
                r"Realisierte[rs]?\s+Ergebnis",
                r"Realized\s+(?:profit\s+and\s+loss|P&L|gains?)"]:
        m = re.search(r"^\s*" + lab + r"[^\d\-−–(+\n]*(" + _NUM + r")", text, re.I | re.M)
        if m:
            try:
                return sl.q2(sl.to_decimal(m.group(1), locale_hint=hint))
            except sl.ParseError:
                pass
    return sl.q2(D("0"))


# ─────────────────────────────────────────────────────────────── Ergebnis ─────
def build_result(text: str, year: int, *, quelle: str = "koinly",
                 dateformat: str | None = None, strikt: bool = True) -> dict:
    hint = sl.detect_locale(text)
    disposals, unmatched = parse_disposals(text, locale_hint=hint, dateformat=dateformat)
    income_detail, income_2233, warn_income = parse_income(text, hint)
    expenses_detail, expenses_total, warn_exp = parse_expenses(text, hint)
    futures = parse_futures(text, hint)

    g = lambda d: sl.to_decimal(d["gain_eur"])  # noqa: E731
    taxable = [d for d in disposals if d["taxable"]]
    gains = sum((g(d) for d in taxable if g(d) > 0), D("0"))
    losses = sum((g(d) for d in taxable if g(d) < 0), D("0"))
    net = sum((g(d) for d in taxable), D("0"))
    taxfree = sum((g(d) for d in disposals if not d["taxable"]), D("0"))
    alle = sum((g(d) for d in disposals), D("0"))

    warnungen = list(warn_income) + list(warn_exp)
    if unmatched:
        warnungen.append(
            f"{len(unmatched)} Zeile(n) in der Veräußerungstabelle konnten NICHT "
            f"gelesen werden — Beispiel: {unmatched[0][:120]!r}")

    # ── Abgleich gegen die im Report ausgewiesenen Summen ────────────────────
    ausgewiesen = report_gesamtgewinn(text, hint)
    anzahl_report = report_anzahl(text)
    abgleiche = [sl.Abgleich("Kapitalgewinn (alle Veräußerungen)", sl.q2(alle),
                             None if ausgewiesen is None else sl.q2(ausgewiesen))]
    bericht = [str(a) for a in abgleiche]
    if anzahl_report is None:
        bericht.append(f"Anzahl Veräußerungen: geparst {len(disposals)} "
                       f"(kein Vergleichswert im Report gefunden)")
    else:
        bericht.append(f"Anzahl Veräußerungen: geparst {len(disposals)} vs. Report "
                       f"{anzahl_report}")
    bericht.append(f"Nicht zugeordnete Tabellenzeilen: {len(unmatched)}")

    result = {
        "tax_year": year,
        "quelle": quelle,
        "quelle_beschreibung": "Koinly-Steuerbericht (vorberechnet, FIFO wallet-übergreifend)",
        "methode": "Veräußerungen direkt aus Koinly übernommen (kein Neu-FIFO).",
        "zahlennotation": hint,
        "abgleich": bericht,
        "paragraph_23": {
            "freigrenze_angewendet": False,
            "anzahl_veraeusserungen": len(disposals),
            "gewinn_eur": str(sl.q2(gains)),
            "verlust_eur": str(sl.q2(losses)),
            "netto_ergebnis_eur": str(sl.q2(net)),
            "verlustvortrag_eur": str(sl.q2(-net if net < 0 else D("0"))),
            "steuerfrei_langfristig_eur": str(sl.q2(taxfree)),
            "disposals": disposals,
            "nicht_zugeordnete_zeilen": unmatched,
            "warnungen": warnungen,
            "hinweis": ("Rohwerte ohne Freigrenze; verlust_eur ist die Summe der "
                        "negativen Ergebnisse (negatives Vorzeichen). "
                        "Freigrenze § 23 wendet build_taxreport.py einmal auf die "
                        "Summe aller Broker an."),
        },
        "paragraph_22_nr3": {
            "freigrenze_angewendet": False,
            "gewinn_eur": str(sl.q2(income_2233)),
            "verlust_eur": "0.00",
            "netto_ergebnis_eur": str(sl.q2(income_2233)),
            "verlustvortrag_eur": "0.00",
            "steuerfrei_langfristig_eur": "0.00",
            "summe_zufluesse_eur": str(sl.q2(income_2233)),
            "ertraege": [],
            "einnahmen_detail": income_detail,
            "warnungen": list(warn_income),
            "hinweis": ("Rohwert ohne Freigrenze (§ 22 Nr. 3 Satz 2: 256 € pro Person "
                        "und Jahr, über alle Quellen)."),
        },
        "elster_extra": [],
        "koinly_extra": {
            "futures_nettoergebnis_eur": str(futures),
            "futures_hinweis": ("Futures/Derivate sind NICHT in den Kapitalgewinnen "
                                "enthalten. In Deutschland i. d. R. Termingeschäfte "
                                "§ 20 Abs. 2 EStG -> Anlage KAP, gesondert angeben."),
            "ausgaben_detail": expenses_detail,
            "ausgaben_total_eur": str(sl.q2(expenses_total)),
            "ausgaben_hinweis": ("Gebühren wie 'Loan fee'/Margin sind für Privatanleger "
                                 "meist NICHT abziehbar. Steuerberater prüfen."),
        },
        "offene_bestaende": {},
        "hinweise": [
            "Werte aus Koinly-Steuerbericht übernommen — gegen Original prüfen.",
            "Freigrenzen (§ 23: 1.000/600 €, § 22 Nr. 3: 256 €) sind hier bewusst NICHT "
            "angewendet — sie gelten pro Person und Jahr über alle Broker; "
            "build_taxreport.py rechnet sie einmal auf die Summe.",
            "§ 23: Verluste nur mit § 23-Gewinnen verrechenbar; Verlustfeststellung sichert "
            "den Vortrag in Folgejahre (Anlage SO / gesonderte Feststellung).",
            "Coins > 1 Jahr gehalten sind steuerfrei (Koinly: 'Langfristig').",
            "Endkontrolle durch Steuerberater — keine Steuerberatung.",
        ],
    }
    # Alte Schreibweise als Alias, damit ältere Konsumenten nicht stumm 0 lesen.
    result["paragraph_22_nr_3"] = result["paragraph_22_nr3"]

    if anzahl_report is not None and anzahl_report != len(disposals) and strikt:
        raise sl.PlausibilityError(
            f"Der Report weist {anzahl_report} Veräußerungen aus, geparst wurden "
            f"{len(disposals)}. {len(unmatched)} Zeile(n) konnten nicht gelesen werden.\n"
            "→ Report-Layout prüfen; NICHT ungeprüft weiterverwenden.")
    sl.pruefe_summen(abgleiche, strikt=strikt)
    return result


def main():
    ap = argparse.ArgumentParser(
        description="Koinly-Steuerbericht (PDF) -> krypto_result.json",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf_path")
    ap.add_argument("--year", help="Steuerjahr überschreiben (sonst aus Report)")
    ap.add_argument("--dateformat", choices=["de", "en"],
                    help="Datumsformat des Reports erzwingen: de=TT/MM/JJJJ, "
                         "en=MM/TT/JJJJ (nötig bei mehrdeutigen Datumsangaben)")
    ap.add_argument("-o", "--out",
                    help="Ausgabedatei (Standard: <pdf-name>.krypto_result.json — "
                         "bewusst kein fester Name, damit ein zweiter Broker den "
                         "ersten nicht überschreibt)")
    args = ap.parse_args()

    text = _pdf_text(args.pdf_path)
    if not is_koinly(text):
        print("WARNUNG: sieht nicht nach Koinly aus. Trotzdem versuchen ...", file=sys.stderr)
    year = detect_year(text, args.year)
    out = args.out or str(Path(args.pdf_path).with_suffix("").name + ".krypto_result.json")

    try:
        result = build_result(text, year, quelle=Path(args.pdf_path).name,
                              dateformat=args.dateformat)
    except (sl.ParseError, sl.PlausibilityError) as e:
        print(f"ABBRUCH: {e}", file=sys.stderr)
        sys.exit(1)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    p = result["paragraph_23"]
    print(f"Koinly-Report {year} geparst -> {out}")
    print("  Abgleich:")
    for zeile in result["abgleich"]:
        print(f"    {zeile}")
    print(f"  Veräußerungen: {p['anzahl_veraeusserungen']} | Netto § 23: "
          f"{p['netto_ergebnis_eur']} € (Gewinne {p['gewinn_eur']} €, "
          f"Verluste {p['verlust_eur']} €) | steuerfrei > 1 Jahr: "
          f"{p['steuerfrei_langfristig_eur']} €")
    if sl.to_decimal(p["verlustvortrag_eur"]) > 0:
        print(f"  Verlustvortrag § 23: {p['verlustvortrag_eur']} € (Verlustfeststellung beantragen)")
    print(f"  § 22 Nr. 3 (roh, ohne Freigrenze): "
          f"{result['paragraph_22_nr3']['netto_ergebnis_eur']} €")
    ke = result["koinly_extra"]
    print(f"  Futures (separat/Anlage KAP): {ke['futures_nettoergebnis_eur']} € | "
          f"Ausgaben: {ke['ausgaben_total_eur']} €")
    for w in p["warnungen"]:
        print(f"  {w}", file=sys.stderr)
    print("  Freigrenzen NICHT angewendet — build_taxreport.py rechnet sie einmal "
          "auf die Summe aller Reports.")


if __name__ == "__main__":
    main()
