#!/usr/bin/env python3
"""
parse_pdf.py — Liest Broker-/Exchange-TaxReports als PDF ein, erkennt Tabellen und
extrahiert Transaktionen ins kanonische Schema (für krypto_fifo.py / build_taxreport.py).

Mehrstufige Backends (automatische Wahl, beste zuerst):
  1) docling   — Layout-/Tabellenstruktur-Erkennung + OCR (beste Qualität).
                 pip install docling   (lädt beim 1. Lauf Modelle nach -> Internet nötig)
  2) pdfplumber— sehr gute Tabellenextraktion bei digitalen PDFs.
  3) pymupdf   — Textebene + find_tables(); bei gescannten Seiten -> OCR-Fallback.
OCR-Fallback: pytesseract + poppler/pdf2image (gescannte/bildbasierte PDFs).
  Sprachpaket Deutsch empfohlen:  apt-get install tesseract-ocr-deu

Ausgaben:
  <name>.extracted.json     — pro Seite: Text, erkannte Tabellen, Backend, OCR-Flag
  <name>.tables.csv         — alle Tabellen zur schnellen Sichtkontrolle
  <name>.transactions.json  — heuristisches kanonisches Mapping (confidence/_needs_review)
  (kein fester Dateiname: sonst überschreibt der zweite Broker den ersten)

Was hier bewusst laut ist:
  * Zahlen laufen über steuerlib.to_decimal mit einem Locale-Hint aus dem ganzen
    Dokument; mehrdeutige Zellen (1.234 — 1234 oder 1,234?) werden als
    _needs_review markiert statt geraten.
  * Übersprungene Tabellen (kein erkennbarer Header) werden gezählt und gemeldet.
  * Summen-/Zwischensummenzeilen werden übersprungen statt als Transaktion gebucht.

WICHTIG: Broker-/Exchange-Layouts variieren stark. Das heuristische Mapping ist ein
Entwurf — niedrige confidence / _needs_review-Zeilen prüfen und korrigieren, bevor sie in
die Steuerberechnung gehen. Keine Steuerberatung.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steuerlib as sl  # noqa: E402


# ----------------------------------------------------------------- Backends ---
def _try_docling(pdf_path: str):
    """Docling: liefert Markdown + Tabellen als Zeilenlisten. None, wenn nicht verfügbar."""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None
    try:
        conv = DocumentConverter()
        result = conv.convert(pdf_path)
        doc = result.document
        tables = []
        for t in getattr(doc, "tables", []) or []:
            try:
                df = t.export_to_dataframe()
                rows = [list(df.columns)] + df.astype(str).values.tolist()
                tables.append(rows)
            except Exception:
                continue
        text = ""
        try:
            text = doc.export_to_markdown()
        except Exception:
            pass
        return {"backend": "docling", "ocr": True,
                "pages": [{"page": 1, "text": text, "tables": tables, "ocr": True}]}
    except Exception as e:
        print(f"  docling fehlgeschlagen ({e}); Fallback.", file=sys.stderr)
        return None


def _pymupdf_pages(pdf_path: str):
    import fitz
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc, 1):
        text = page.get_text() or ""
        tables = []
        try:
            tf = page.find_tables()
            for t in tf.tables:
                tables.append([[("" if c is None else str(c)) for c in row]
                               for row in t.extract()])
        except Exception:
            pass
        pages.append({"page": i, "text": text, "tables": tables,
                      "scanned": len(text.strip()) < 20})
    return pages


def _pdfplumber_pages(pdf_path: str):
    import pdfplumber
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            tables = []
            try:
                for t in page.extract_tables() or []:
                    tables.append([[("" if c is None else str(c)) for c in row]
                                   for row in t])
            except Exception:
                pass
            pages.append({"page": i, "text": text, "tables": tables,
                          "scanned": len(text.strip()) < 20})
    return pages


def _ocr_page(pdf_path: str, page_index: int, lang: str):
    """OCR einer Seite via pdf2image + pytesseract. Rekonstruiert zusätzlich
    Tabellen aus Wort-Bounding-Boxes (Zeilen via tesseract-Zeilen, Spalten via
    horizontale Lücken). Rückgabe: (text, tables). page_index ist 0-basiert."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
        from pytesseract import Output
    except ImportError as e:
        print(f"  OCR nicht möglich ({e}).", file=sys.stderr)
        return "", []
    images = convert_from_path(pdf_path, dpi=300,
                               first_page=page_index + 1, last_page=page_index + 1)
    if not images:
        return "", []
    img = images[0]
    text = pytesseract.image_to_string(img, lang=lang)
    tables = []
    try:
        data = pytesseract.image_to_data(img, lang=lang, output_type=Output.DICT)
        gap_thr = max(30, int(0.018 * img.width))   # Spaltenlücke abhängig von Breite
        lines = {}
        for i in range(len(data["text"])):
            w = (data["text"][i] or "").strip()
            # float(): manche pytesseract-Builds liefern conf als '95.0' — int()
            # wirft dort ValueError und verwarf bisher ALLE OCR-Tabellen.
            if not w or float(data["conf"][i]) < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append((data["left"][i], data["width"][i], w))
        rows = []
        for key in sorted(lines.keys()):
            words = sorted(lines[key], key=lambda t: t[0])
            cells, cur = [], [words[0][2]]
            prev_right = words[0][0] + words[0][1]
            for left, width, w in words[1:]:
                if left - prev_right > gap_thr:
                    cells.append(" ".join(cur))
                    cur = [w]
                else:
                    cur.append(w)
                prev_right = left + width
            cells.append(" ".join(cur))
            rows.append(cells)
        table_rows = [r for r in rows if len(r) >= 2]
        if len(table_rows) >= 2:
            tables = [table_rows]
    except Exception as e:
        print(f"  OCR-Tabellenrekonstruktion übersprungen ({e}).", file=sys.stderr)
    return text, tables


def extract(pdf_path: str, backend: str = "auto", ocr_lang: str = "deu+eng"):
    """Wählt Backend, extrahiert Text+Tabellen, OCR-Fallback bei gescannten Seiten."""
    if backend in ("auto", "docling"):
        d = _try_docling(pdf_path)
        if d:
            return d
        if backend == "docling":
            print("  docling nicht verfügbar — nutze pdfplumber.", file=sys.stderr)

    used = "pdfplumber"
    if backend == "pymupdf":
        pages = _pymupdf_pages(pdf_path)
        used = "pymupdf"
    else:
        try:
            pages = _pdfplumber_pages(pdf_path)
        except ImportError as e:
            # Nur ein FEHLENDES pdfplumber rechtfertigt den Backendwechsel;
            # ein echter PDF-Fehler muss sichtbar bleiben.
            print(f"  pdfplumber nicht verfügbar ({e}); nutze pymupdf.", file=sys.stderr)
            pages = _pymupdf_pages(pdf_path)
            used = "pymupdf"

    any_ocr = False
    for p in pages:
        # 'scanned' heißt bereits "< 20 Zeichen Text". Die frühere Zusatzbedingung
        # "und gar kein Text" ließ den OCR-Fallback praktisch nie anspringen.
        if p.get("scanned"):
            ocr_text, ocr_tables = _ocr_page(pdf_path, p["page"] - 1, ocr_lang)
            if ocr_text.strip():
                p["text"] = ocr_text
                p["ocr"] = True
                any_ocr = True
            if ocr_tables and not p.get("tables"):
                p["tables"] = ocr_tables
    return {"backend": used, "ocr": any_ocr, "pages": pages}


# ------------------------------------------------------- Heuristik-Mapping ----
SYN = {
    "date": ["date", "datum", "time", "zeitpunkt", "valuta", "trade date",
             "executed", "buchung", "ausführung", "timestamp"],
    "type": ["type", "typ", "side", "action", "transaction", "art", "kind",
             "direction", "vorgang", "geschäftsart", "operation"],
    "asset": ["asset", "coin", "currency", "symbol", "ticker", "währung",
              "waehrung", "basis", "instrument", "pair", "paar", "produkt"],
    "amount": ["amount", "menge", "quantity", "qty", "anzahl", "volume",
               "units", "stück", "stueck", "nominal"],
    "price": ["price", "kurs", "preis", "rate", "unit price", "stückpreis"],
    "eur_value": ["wert", "value", "total", "gesamt", "betrag", "proceeds",
                  "gross", "net", "umsatz", "gegenwert", "erlös", "erloes"],
    "fee": ["fee", "gebühr", "gebuehr", "commission", "provision", "kosten",
            "entgelt", "spesen"],
    # "to" wurde entfernt: es steckt in "Konto", "Total", "Netto" … und machte aus
    # der Kontospalte eine Gegenwährung.
    "counter_asset": ["quote", "counter", "received", "erhalten",
                      "zielwährung", "gegenwährung", "counter asset"],
    # Auffangspalte: "Gebührenwährung"/"Fee currency" ist WEDER das gehandelte Asset
    # noch ein Betrag. Ohne diesen Eintrag landete sie über "währung" bei asset.
    "fee_currency": ["gebührenwährung", "gebuehrenwaehrung", "fee currency",
                     "fee ccy", "währung der gebühr"],
}

# Reihenfolge = Priorität. reward/swap/sell vor buy, damit z. B. "Verkauf" nicht an
# "kauf" hängenbleibt. Zusätzlich Wortgrenzen-Matching (siehe _match_type).
TYPE_KEYWORDS = [
    ("reward", ["reward", "rewards", "staking", "earn", "interest", "zins", "zinsen",
                "dividend", "ausschüttung", "ausschuettung", "yield", "bonus",
                "airdrop", "lending"]),
    ("swap", ["swap", "convert", "conversion", "trade", "tausch", "umtausch",
              "umwandlung", "exchange"]),
    ("sell", ["sell", "verkauf", "sale", "sold", "veräuß", "verauss", "abgang",
              "disposal", "short"]),
    ("buy", ["buy", "kauf", "purchase", "bought", "erwerb", "zugang", "long"]),
    ("deposit", ["deposit", "einzahlung", "transfer in", "received", "eingang",
                 "gutschrift"]),
    ("withdrawal", ["withdraw", "auszahlung", "transfer out", "sent", "ausgang",
                    "abhebung", "belastung"]),
]

# Summen-/Zwischensummenzeilen sind keine Transaktionen.
SUMMENZEILE = re.compile(r"^(summe|gesamt|total|zwischensumme|saldo|insgesamt)\b", re.I)
# 1.234 / 1,234: ohne weiteren Kontext nicht auflösbar (1234 oder 1,234?).
MEHRDEUTIG = re.compile(r"^[-+]?\d{1,3}[.,]\d{3}$")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _norm_date(raw):
    """Normalisiert gängige Datumsformate auf ISO. Ohne steuerlib.parse_datetime
    wurde '2024-01-02' als TT.MM.JJ gelesen und zu '2002-01-24' verdreht."""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = sl.parse_datetime(s)
    except sl.ParseError:
        return s
    if re.search(r"\d{1,2}:\d{2}", s):
        return dt.isoformat(sep=" ")
    return dt.date().isoformat()


def _score(h: str, syn: str) -> int:
    """Wie gut passt ein Synonym auf eine Kopfzelle? Exakt schlägt Anfang schlägt
    Ende schlägt Teilstring; längere Synonyme schlagen kürzere."""
    if not h or not syn:
        return 0
    if h == syn:
        return 100 + len(syn)
    if re.match(re.escape(syn) + r"(?![a-zäöüß])", h):
        return 60 + len(syn)
    if re.search(r"(?<![a-zäöüß])" + re.escape(syn) + r"$", h):
        return 40 + len(syn)
    if syn in h:
        return 20 + len(syn)
    return 0


def _spalten_scores(header_cell: str) -> dict[str, int]:
    h = _norm(header_cell)
    out = {}
    for canon, syns in SYN.items():
        best = max((_score(h, s) for s in syns), default=0)
        if best:
            out[canon] = best
    return out


def _match_col(header_cell: str):
    """Bestes Kanon-Feld für eine einzelne Kopfzelle (None, wenn nichts passt)."""
    scores = _spalten_scores(header_cell)
    return max(scores, key=scores.get) if scores else None


def mappe_spalten(header) -> dict[str, int]:
    """Bestes Gesamt-Mapping Spalte -> Kanonfeld.

    Früher gewann die erste Spalte, die *irgendein* Synonym enthielt: damit wurde
    'Gebührenwährung' zum asset und 'Konto' (über das Synonym 'to') zur
    Gegenwährung. Jetzt werden alle Kandidaten bewertet und je Feld der beste
    Treffer genommen."""
    kandidaten = []
    for ci, cell in enumerate(header):
        for canon, score in _spalten_scores(cell).items():
            kandidaten.append((score, ci, canon))
    kandidaten.sort(key=lambda t: (-t[0], t[1]))
    colmap: dict[str, int] = {}
    belegt: set[int] = set()
    for score, ci, canon in kandidaten:
        if canon in colmap or ci in belegt:
            continue
        colmap[canon] = ci
        belegt.add(ci)
    colmap.pop("fee_currency", None)   # nur zum Abfangen, nicht zum Auswerten
    return colmap


def _match_type(blob: str):
    for canon, kws in TYPE_KEYWORDS:
        for kw in kws:
            # Wortgrenzen links UND rechts: links, damit "kauf" nicht in "verkauf"
            # matcht; rechts (max. 2 weitere Buchstaben für Flexionen wie
            # "verkauft"/"kaufen"), damit "long" nicht in "longitude" matcht.
            if re.search(r"(?<![a-zäöüß])" + re.escape(kw) + r"(?![a-zäöüß]{3,})", blob):
                return canon
    return None


def _classify_type(type_cell, row_blob: str = ""):
    """Typ bevorzugt aus der gemappten type-Spalte bestimmen.

    Über die ganze Zeile geraten wurde aus einem Verkauf im Wallet 'Kraken Earn'
    ein 'reward' und aus jeder Zeile mit 'Trade' und 'BTC/EUR' ein 'swap'."""
    aus_spalte = _norm(type_cell)
    if aus_spalte:
        t = _match_type(aus_spalte)
        return (t, 0.9) if t else (None, 0.0)
    t = _match_type(_norm(row_blob))
    return (t, 0.5) if t else (None, 0.0)   # geraten -> niedrigere confidence


def _zahl(raw, hint: str | None = None):
    """Betrag als String oder None. Kein stilles 0."""
    if raw is None or str(raw).strip() == "":
        return None
    tok = str(raw).strip()
    try:
        return str(sl.to_decimal(tok, locale_hint=hint))
    except sl.ParseError:
        m = re.search(r"[-−–(]?\d[\d.,\s]*\d\)?-?|\d", tok)
        if not m:
            return None
        try:
            return str(sl.to_decimal(m.group(0).replace(" ", ""), locale_hint=hint))
        except sl.ParseError:
            return None


def _mehrdeutig(raw) -> bool:
    """Zelle, deren Notation ohne Kontext nicht entscheidbar ist."""
    if raw is None:
        return False
    tok = re.sub(r"[^\d.,+-]", "", str(raw))
    return bool(MEHRDEUTIG.match(tok))


def _looks_like_header(row) -> bool:
    hits = sum(1 for c in row if _match_col(c))
    return hits >= 2


def dokument_locale(extraction: dict) -> str:
    text = " ".join(p.get("text") or "" for p in extraction.get("pages", []))
    tabellen = " ".join(str(c) for p in extraction.get("pages", [])
                        for t in p.get("tables", []) for r in t for c in r)
    return sl.detect_locale(text + " " + tabellen)


def map_tables_to_transactions(extraction: dict, locale_hint: str | None = None):
    """Versucht, aus erkannten Tabellen kanonische Transaktionen zu bilden.

    Rückgabe: (transaktionen, statistik). Die Statistik nennt übersprungene
    Tabellen — sonst verschwinden ganze Seiten kommentarlos."""
    hint = locale_hint or dokument_locale(extraction)
    txs = []
    stat = {"tabellen": 0, "ohne_header": 0, "ohne_spalten": 0, "summenzeilen": 0,
            "zeilen": 0, "zahlennotation": hint}
    for page in extraction.get("pages", []):
        for table in page.get("tables", []):
            stat["tabellen"] += 1
            if not table or len(table) < 2:
                stat["ohne_header"] += 1
                continue
            header_idx = next((i for i, r in enumerate(table[:3]) if _looks_like_header(r)),
                              None)
            if header_idx is None:
                stat["ohne_header"] += 1
                continue
            colmap = mappe_spalten(table[header_idx])
            if "date" not in colmap and "asset" not in colmap:
                stat["ohne_spalten"] += 1
                continue
            for row in table[header_idx + 1:]:
                if not any(_norm(c) for c in row):
                    continue
                erste = next((_norm(c) for c in row if _norm(c)), "")
                if SUMMENZEILE.match(erste):
                    stat["summenzeilen"] += 1
                    continue
                stat["zeilen"] += 1

                def cell(key):
                    ci = colmap.get(key)
                    return row[ci] if ci is not None and ci < len(row) else ""

                ttype, tconf = _classify_type(cell("type"),
                                              " ".join(str(c) for c in row))
                asset_raw = _norm(cell("asset")).upper()
                asset = re.split(r"[\/\-\s]", asset_raw)[0][:10] if asset_raw else ""
                amount = _zahl(cell("amount"), hint)
                eur_value = _zahl(cell("eur_value"), hint)
                price = _zahl(cell("price"), hint)
                fee = _zahl(cell("fee"), hint) or "0"
                if eur_value is None and price and amount:
                    eur_value = str(sl.q2(sl.to_decimal(price) * sl.to_decimal(amount)))

                mehrdeutig = [k for k in ("amount", "eur_value", "price", "fee")
                              if _mehrdeutig(cell(k))]
                fields_ok = sum(x is not None and x != "" for x in
                                [ttype, asset or None, amount, eur_value])
                confidence = round(min(1.0, 0.25 * fields_ok) * (tconf if ttype else 0.6), 2)
                counter = _norm(cell("counter_asset")).upper() or None
                needs_review = bool(ttype is None or not asset or amount is None
                                    or eur_value is None
                                    or (ttype == "swap" and not counter)
                                    or mehrdeutig)

                tx = {
                    "timestamp": _norm_date(cell("date")),
                    "type": ttype,
                    "asset": asset or None,
                    "amount": amount,
                    "eur_value": eur_value,
                    "fee_eur": fee,
                    "counter_asset": counter,
                    "source": f"pdf:{Path(extraction.get('_src','pdf')).name}",
                    "confidence": confidence,
                    "_needs_review": needs_review,
                    "_raw": [str(c) for c in row],
                }
                if mehrdeutig:
                    tx["_ambig_spalten"] = mehrdeutig
                    tx["_hinweis"] = ("Zahlnotation mehrdeutig (z. B. 1.234 = 1234 oder "
                                      "1,234?) — Wert im Original prüfen.")
                txs.append(tx)
    return txs, stat


# --------------------------------------------------------------------- CLI ----
def write_tables_csv(extraction: dict, path: Path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        for page in extraction.get("pages", []):
            for ti, table in enumerate(page.get("tables", []), 1):
                w.writerow([f"# Seite {page.get('page')} Tabelle {ti}"])
                for row in table:
                    w.writerow([sl.csv_safe(c) for c in row])
                w.writerow([])


def main():
    ap = argparse.ArgumentParser(
        description="Broker-PDF -> Tabellen + kanonische Transaktionen")
    ap.add_argument("pdf_path")
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "docling", "pdfplumber", "pymupdf"])
    ap.add_argument("--ocr-lang", default="deu+eng",
                    help="Tesseract-Sprachen (z. B. 'deu+eng' oder 'eng')")
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--no-map", action="store_true",
                    help="nur extrahieren, kein Transaktions-Mapping")
    args = ap.parse_args()

    pdf_path = args.pdf_path
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = Path(pdf_path).stem

    print(f"Lese {pdf_path} (backend={args.backend}) ...")
    extraction = extract(pdf_path, backend=args.backend, ocr_lang=args.ocr_lang)
    extraction["_src"] = pdf_path
    n_tables = sum(len(p.get("tables", [])) for p in extraction["pages"])
    print(f"  Backend: {extraction['backend']} | OCR: {extraction['ocr']} | "
          f"Seiten: {len(extraction['pages'])} | Tabellen: {n_tables}")

    extracted_path = outdir / f"{stem}.extracted.json"
    with open(extracted_path, "w", encoding="utf-8") as f:
        json.dump(extraction, f, indent=2, ensure_ascii=False)
    tables_csv = outdir / f"{stem}.tables.csv"
    write_tables_csv(extraction, tables_csv)
    print(f"  geschrieben: {extracted_path}")
    print(f"  geschrieben: {tables_csv}")

    if not args.no_map:
        txs, stat = map_tables_to_transactions(extraction)
        # Dateiname trägt den PDF-Namen: sonst überschreibt der zweite Broker den ersten.
        tx_path = outdir / f"{stem}.transactions.json"
        with open(tx_path, "w", encoding="utf-8") as f:
            json.dump({"transactions": txs, "quelle": Path(pdf_path).name,
                       "statistik": stat}, f, indent=2, ensure_ascii=False)
        review = [t for t in txs if t["_needs_review"]]
        print(f"  geschrieben: {tx_path}  ({len(txs)} Transaktionen, "
              f"{len(review)} zur Prüfung markiert)")
        uebersprungen = stat["ohne_header"] + stat["ohne_spalten"]
        print(f"  Tabellen: {stat['tabellen']} gefunden, {uebersprungen} übersprungen "
              f"({stat['ohne_header']} ohne erkennbaren Header, {stat['ohne_spalten']} "
              f"ohne Datum/Asset-Spalte) | {stat['summenzeilen']} Summenzeile(n) "
              f"ignoriert | Zahlennotation: {stat['zahlennotation']}")
        if uebersprungen:
            print("  ACHTUNG: übersprungene Tabellen können Transaktionen enthalten — "
                  f"{tables_csv.name} sichten.", file=sys.stderr)
        if txs:
            avg = round(sum(t["confidence"] for t in txs) / len(txs), 2)
            print(f"  durchschnittliche confidence: {avg}")
        print("\nHINWEIS: _needs_review / niedrige confidence prüfen und EUR-Marktwerte "
              "für reward/swap ergänzen, bevor die Transaktionen in die "
              "Steuerberechnung gehen.")


if __name__ == "__main__":
    main()
