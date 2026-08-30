#!/usr/bin/env python3
"""
parse_inputs.py — Normalisiert Exchange-Exporte in das kanonische Transaktionsschema,
das krypto_fifo.py erwartet.

Kanonisches Schema (eine Zeile = eine Transaktion):
  timestamp     ISO-8601 oder 'YYYY-MM-DD HH:MM:SS'
  type          buy | sell | swap | reward | deposit | withdrawal
  asset         Ticker des gehandelten Assets (bei swap: das ABGEGEBENE Asset)
  amount        Menge des Assets (positiv)
  eur_value     EUR-Wert der Transaktion (Kosten bei buy, Erlös bei sell, FMV bei swap/reward)
  fee_eur       Gebühr in EUR (optional)
  reward_kind   staking | lending  (nur bei type=reward)
  counter_asset erhaltenes Asset (nur bei swap)
  counter_amount erhaltene Menge (nur bei swap)
  tx_id, source optional

Unterstützte Eingaben:
  --format canonical : CSV liegt bereits im kanonischen Schema vor
  --format kraken    : Kraken 'ledgers.csv' Export (best effort)
  --map mapping.json : beliebige CSV via Spalten-Mapping (--format map)

Robustheit (früher stille Fehlerquellen):
  * Trennzeichen wird erkannt (Semikolon-CSV aus deutschem Excel ergab sonst
    lauter leere Zeilen und trotzdem eine Erfolgsmeldung); --delimiter erzwingt es.
  * Kodierung: UTF-8, bei Dekodierfehler automatisch latin-1.
  * Alle Zahlen laufen über steuerlib.to_decimal — deutsche Notation (1.234,56)
    lässt nichts mehr abstürzen und wird nicht um Faktor 100/1000 verfälscht.
  * Kraken: deposit/withdrawal werden ausgegeben (fehlende Anschaffung = Kostenbasis 0
    im FIFO), Zeilen ohne refid landen nicht mehr alle in einer Gruppe, Assetcodes
    wie XETC/XXLM und Staking-Suffixe (ETH.S) werden normalisiert.
  * Es wird IMMER berichtet, wie viele Zeilen nicht zugeordnet werden konnten.

Für Formate, die hier nicht abgedeckt sind, oder PDF-Reports: Claude liest die Datei,
mappt sie selbst ins kanonische Schema (oder parse_pdf.py). Werte IMMER stichprobenartig
prüfen. Keine Steuerberatung.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steuerlib as sl  # noqa: E402

D = Decimal

ERLAUBTE_TYPEN = {"buy", "sell", "swap", "reward", "deposit", "withdrawal"}
_KANDIDATEN = [";", ",", "\t", "|"]


# ───────────────────────────────────────────────────────────── Hilfsmittel ────
def _menge(v, hint: str | None) -> Decimal:
    """Zahl lesen. Bei führender '0,'/'0.' ohne Locale-Hint (0.00047383 ist auch in
    einem deutschen Export keine Tausenderzahl)."""
    t = str(v).strip()
    if re.match(r"^-?0[.,]", t):
        return sl.to_decimal(t)
    return sl.to_decimal(t, locale_hint=hint)


def _num(v, hint, *, default: str | None = "0", feld: str = "",
         warnungen: list | None = None) -> str | None:
    """Betrag als String. Unlesbar -> default; wenn kein default gesetzt ist, None
    plus Warnung — NICHT stillschweigend 0 (das wäre in einer Steuerrechnung der
    teuerste Fehler)."""
    if v is None or str(v).strip() == "":
        return default
    try:
        return str(_menge(v, hint))
    except sl.ParseError:
        if warnungen is not None:
            warnungen.append(f"Betrag in Spalte '{feld}' nicht lesbar: {v!r}")
        return default


def _zeit(v, warnungen: list | None = None):
    if v is None or str(v).strip() == "":
        return None
    try:
        dt = sl.parse_datetime(v)
    except sl.ParseError:
        if warnungen is not None:
            warnungen.append(f"Zeitstempel nicht lesbar: {v!r}")
        return str(v)
    return dt.isoformat(sep=" ")


def sniff_delimiter(sample: str, override: str | None = None) -> str:
    """Trennzeichen bestimmen. Deutsches Excel schreibt Semikolon — mit ','
    fest verdrahtet ergab das eine einzige Spalte und lauter leere Felder."""
    if override:
        return {"tab": "\t", "\\t": "\t"}.get(override, override)
    kopf = next((l for l in sample.splitlines() if l.strip()), "")
    treffer = {d: kopf.count(d) for d in _KANDIDATEN if kopf.count(d) > 0}
    if len(treffer) == 1:
        return next(iter(treffer))
    try:
        return csv.Sniffer().sniff(sample[:4096], delimiters="".join(_KANDIDATEN)).delimiter
    except csv.Error:
        return max(treffer, key=treffer.get) if treffer else ","


def parse_csv_text(text: str, delimiter: str | None = None):
    """CSV-Text -> (Zeilen als dicts, verwendetes Trennzeichen)."""
    delim = sniff_delimiter(text, delimiter)
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delim))
    rows = [{(k or "").strip(): v for k, v in r.items()} for r in rows]
    return rows, delim


def read_csv(path: str, delimiter: str | None = None):
    """Datei lesen; bei Dekodierfehler auf latin-1 zurückfallen statt abzustürzen."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            text = f.read()
        enc = "utf-8"
    except UnicodeDecodeError:
        with open(path, encoding="latin-1", newline="") as f:
            text = f.read()
        enc = "latin-1"
        print("HINWEIS: Datei ist kein UTF-8 — als latin-1 gelesen. Umlaute prüfen.",
              file=sys.stderr)
    rows, delim = parse_csv_text(text, delimiter)
    return rows, delim, enc


def locale_hint_fuer(rows) -> str:
    """Zahlennotation des ganzen Exports raten (ein Hint für die ganze Datei)."""
    probe = " ".join(str(v) for r in rows[:200] for v in r.values() if v)
    return sl.detect_locale(probe)


# ────────────────────────────────────────────────────────────── kanonisch ─────
def from_canonical(rows, hint: str | None = None):
    hint = hint or locale_hint_fuer(rows)
    warnungen: list[str] = []
    out = []
    for r in rows:
        ca = (r.get("counter_amount") or "").strip()
        out.append({
            "timestamp": _zeit(r.get("timestamp"), warnungen),
            "type": (r.get("type") or "").strip().lower(),
            "asset": (r.get("asset") or "").strip().upper(),
            "amount": _num(r.get("amount"), hint, default=None, feld="amount",
                           warnungen=warnungen),
            "eur_value": _num(r.get("eur_value"), hint, default=None, feld="eur_value",
                              warnungen=warnungen),
            "fee_eur": _num(r.get("fee_eur"), hint, default="0", feld="fee_eur"),
            "reward_kind": (r.get("reward_kind") or "").strip().lower() or None,
            "counter_asset": (r.get("counter_asset") or "").strip().upper() or None,
            "counter_amount": _num(ca, hint, default=None, feld="counter_amount") if ca else None,
            "tx_id": r.get("tx_id"),
            "source": r.get("source") or "canonical",
        })
    return out, warnungen


# ───────────────────────────────────────────────────────────────── Kraken ─────
_KRAKEN_MAP = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "ETH2": "ETH",
               "ZEUR": "EUR", "XXDG": "DOGE", "XDG": "DOGE"}
# Kraken hängt an gestakte Bestände Suffixe an (ETH.S, DOT.S, USDT.M, ETH2.S).
# Ohne Abschneiden führt ETH.S einen eigenen FIFO-Topf neben ETH — die Lots
# werden getrennt und die Haltefrist falsch berechnet.
_STAKING_SUFFIX = re.compile(r"\.(S|M|F|B|P|HOLD)\d*$", re.I)


def norm_asset(a) -> str:
    a = (a or "").strip().upper()
    if not a:
        return ""
    a = _STAKING_SUFFIX.sub("", a)
    a = _KRAKEN_MAP.get(a, a)
    # Kraken-Altcodes: 4 Zeichen mit führendem X (Krypto) bzw. Z (Fiat):
    # XETC -> ETC, XXLM -> XLM, ZUSD -> USD. 3-stellige Ticker (XTZ) bleiben.
    if len(a) == 4 and a[0] in ("X", "Z"):
        a = a[1:]
    return _KRAKEN_MAP.get(a, a)


def from_kraken_ledger(rows, hint: str | None = None):
    """Kraken ledgers.csv: txid,refid,time,type,subtype,aclass,asset,amount,fee,balance.

    'trade'-Zeilen kommen paarweise (Asset raus / EUR rein o. umgekehrt) mit gleicher
    refid. 'staking'/'reward' -> reward, 'deposit'/'withdrawal' werden ausgegeben.
    Rückgabe: (transaktionen, warnungen, statistik).

    Wo Kraken keinen Euro-Wert liefert (Staking-Zufluss, Krypto-Tausch, Ein-/
    Auslieferung), bleibt `eur_value` **None** und die Zeile trägt `_needs_fmv`.
    Bewusst keine "0": eine 0 ist eine Aussage über den Markt, die hier niemand
    getroffen hat — sie würde im FIFO als echter Erlös bzw. als Anschaffungskosten
    0 durchlaufen und einen Gewinn erzeugen, den es nicht gibt. None bricht
    stattdessen in krypto_fifo._pflichtzahl sichtbar ab (genau wie der
    Profil-CSV-Pfad in brokerprofile.py)."""
    hint = hint or locale_hint_fuer(rows)
    warnungen: list[str] = []
    benutzt = [False] * len(rows)

    def betrag(r, feld) -> Decimal:
        try:
            return _menge(r.get(feld) or "0", hint)
        except sl.ParseError:
            warnungen.append(f"Kraken: Feld '{feld}' nicht lesbar: {r.get(feld)!r} "
                             f"(txid {r.get('txid')})")
            return D("0")

    # Zeilen ohne refid dürfen NICHT alle in eine Gruppe fallen — sonst wird aus
    # vielen unabhängigen Buchungen ein einziger (falscher) Trade.
    gruppen: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        ref = (r.get("refid") or "").strip()
        key = ref if ref else f"__ohne_refid_{i}"
        gruppen.setdefault(key, []).append(i)

    out = []
    for key, idxs in gruppen.items():
        group = [rows[i] for i in idxs]
        types = {(g.get("type") or "").strip().lower() for g in group}

        if types & {"staking", "reward", "earn"}:
            for i, g in zip(idxs, group):
                asset = norm_asset(g.get("asset"))
                if asset == "EUR":
                    benutzt[i] = True
                    continue
                out.append({"timestamp": _zeit(g.get("time"), warnungen), "type": "reward",
                            "asset": asset, "amount": str(abs(betrag(g, "amount"))),
                            "eur_value": None, "fee_eur": "0", "reward_kind": "staking",
                            "source": "kraken", "tx_id": g.get("txid"),
                            "_needs_fmv": True})
                benutzt[i] = True
            continue

        if types & {"deposit", "withdrawal"}:
            for i, g in zip(idxs, group):
                t = (g.get("type") or "").strip().lower()
                asset = norm_asset(g.get("asset"))
                if t not in ("deposit", "withdrawal"):
                    continue
                benutzt[i] = True
                if asset == "EUR":
                    continue          # EUR-Ein-/Auszahlung ist steuerlich kein Vorgang
                amt = abs(betrag(g, "amount"))
                out.append({"timestamp": _zeit(g.get("time"), warnungen), "type": t,
                            "asset": asset, "amount": str(amt), "eur_value": None,
                            "fee_eur": str(abs(betrag(g, "fee"))), "source": "kraken",
                            "tx_id": g.get("txid"), "_needs_fmv": True,
                            "_hinweis": ("Ein-/Auslieferung ohne Anschaffungskosten — "
                                         "ohne Ergänzung rechnet FIFO mit Kostenbasis 0.")})
            continue

        eur_idx = [i for i in idxs if norm_asset(rows[i].get("asset")) == "EUR"]
        asset_idx = [i for i in idxs if norm_asset(rows[i].get("asset")) != "EUR"]
        if eur_idx and asset_idx:
            eur_leg = rows[eur_idx[0]]
            eur_amt = betrag(eur_leg, "amount")
            fee = abs(betrag(eur_leg, "fee"))
            for i in eur_idx:
                benutzt[i] = True
            for i in asset_idx:
                al = rows[i]
                amt = betrag(al, "amount")
                out.append({"timestamp": _zeit(al.get("time"), warnungen),
                            "type": "buy" if amt > 0 else "sell",
                            "asset": norm_asset(al.get("asset")), "amount": str(abs(amt)),
                            "eur_value": str(abs(eur_amt)), "fee_eur": str(fee),
                            "source": "kraken", "tx_id": al.get("txid")})
                benutzt[i] = True
        elif len(asset_idx) == 2:
            # Krypto-zu-Krypto-Tausch (kein EUR-Bein) -> swap; FMV muss ergänzt werden
            sell_i = next((i for i in asset_idx if betrag(rows[i], "amount") < 0), None)
            buy_i = next((i for i in asset_idx if betrag(rows[i], "amount") > 0), None)
            if sell_i is not None and buy_i is not None:
                s, b = rows[sell_i], rows[buy_i]
                out.append({"timestamp": _zeit(s.get("time"), warnungen), "type": "swap",
                            "asset": norm_asset(s.get("asset")),
                            "amount": str(abs(betrag(s, "amount"))),
                            "eur_value": None,
                            "counter_asset": norm_asset(b.get("asset")),
                            "counter_amount": str(abs(betrag(b, "amount"))),
                            "fee_eur": str(abs(betrag(s, "fee"))), "source": "kraken",
                            "tx_id": s.get("txid"), "_needs_fmv": True})
                benutzt[sell_i] = benutzt[buy_i] = True

    out.sort(key=lambda x: x.get("timestamp") or "")
    nicht = [i for i, b in enumerate(benutzt) if not b]
    if nicht:
        warnungen.append(
            "Nicht zugeordnete Kraken-Zeilen (txid): "
            + ", ".join(str(rows[i].get("txid") or f"Zeile {i + 1}") for i in nicht[:10])
            + (" …" if len(nicht) > 10 else ""))
    return out, warnungen, {"zeilen": len(rows), "nicht_zugeordnet": len(nicht)}


# ──────────────────────────────────────────────────────────────── Mapping ─────
def from_mapping(rows, mapping, hint: str | None = None):
    """mapping: {'timestamp':'Date','type':'Side','asset':'Coin',...,
                 'type_values':{'BUY':'buy','SELL':'sell'}}"""
    hint = hint or locale_hint_fuer(rows)
    tv = {str(k).strip().lower(): v for k, v in (mapping.get("type_values") or {}).items()}
    warnungen: list[str] = []
    out = []
    for r in rows:
        def col(key):
            src = mapping.get(key)
            return r.get(src) if src else None
        raw_type = (col("type") or "").strip()
        ca = (col("counter_amount") or "")
        out.append({
            "timestamp": _zeit(col("timestamp"), warnungen),
            "type": tv.get(raw_type.lower(), raw_type.lower()),
            "asset": (col("asset") or "").strip().upper(),
            "amount": _num(col("amount"), hint, default=None, feld="amount",
                           warnungen=warnungen),
            "eur_value": _num(col("eur_value"), hint, default=None, feld="eur_value",
                              warnungen=warnungen),
            "fee_eur": _num(col("fee_eur"), hint, default="0", feld="fee_eur"),
            "reward_kind": (col("reward_kind") or "").strip().lower() or None,
            "counter_asset": (col("counter_asset") or "").strip().upper() or None,
            "counter_amount": _num(ca, hint, default=None, feld="counter_amount") if ca else None,
            "source": mapping.get("source", "custom"),
        })
    return out, warnungen


# ───────────────────────────────────────────────────────────── Validierung ────
def pruefe_typen(txs) -> list[str]:
    """Bricht ab, wenn mehr als die Hälfte der Zeilen keinen gültigen 'type' hat —
    das ist das typische Bild eines falschen Trennzeichens oder Formats."""
    if not txs:
        raise sl.ParseError(
            "Keine Datenzeilen gelesen. Trennzeichen/Format prüfen (--delimiter ';').")
    schlecht = [t for t in txs if (t.get("type") or "") not in ERLAUBTE_TYPEN]
    warnungen = []
    if schlecht:
        unbekannt = sorted({(t.get("type") or "<leer>") for t in schlecht})
        for t in schlecht:
            t["_needs_review"] = True
        warnungen.append(
            f"{len(schlecht)} von {len(txs)} Zeile(n) mit unbekanntem type "
            f"({', '.join(unbekannt[:6])}) — erlaubt: {'|'.join(sorted(ERLAUBTE_TYPEN))}.")
    if len(schlecht) * 2 > len(txs):
        raise sl.ParseError(
            f"{len(schlecht)} von {len(txs)} Zeilen haben keinen gültigen 'type' "
            f"({', '.join(sorted({(t.get('type') or '<leer>') for t in schlecht})[:6])}).\n"
            "→ Falsches Trennzeichen (--delimiter ';'), falsches --format oder ein "
            "fehlerhaftes Mapping. Ergebnis wäre unbrauchbar; deshalb Abbruch.")
    return warnungen


# ───────────────────────────────────────────────────────────────────── CLI ────
EPILOG = """\
--format coinbase/binance ist derzeit NICHT implementiert und bricht bewusst ab,
statt die Datei als 'canonical' zu lesen und lauter leere Zeilen zu erzeugen.
Für solche Exporte: Spalten-Mapping benutzen (--format map --map mapping.json).
"""


def main():
    ap = argparse.ArgumentParser(
        description="Exchange-CSV -> kanonisches Transaktionsschema",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path")
    ap.add_argument("--format", default="canonical",
                    choices=["canonical", "kraken", "coinbase", "binance", "map"])
    ap.add_argument("--map", help="Pfad zu mapping.json (für --format map)")
    ap.add_argument("--delimiter", help="CSV-Trennzeichen erzwingen (z. B. ';' oder tab)")
    ap.add_argument("-o", "--out", help="Ausgabe-JSON (sonst stdout)")
    args = ap.parse_args()

    if args.format == "map" and not args.map:
        ap.error("--format map benötigt --map mapping.json")
    if args.format in ("coinbase", "binance"):
        ap.error(f"Format '{args.format}' noch nicht implementiert — "
                 "bitte --format map mit einem Spalten-Mapping verwenden.")

    rows, delim, enc = read_csv(args.csv_path, args.delimiter)
    hint = locale_hint_fuer(rows)
    print(f"{len(rows)} Zeile(n) gelesen (Trennzeichen {delim!r}, Kodierung {enc}, "
          f"Zahlennotation {hint}).", file=sys.stderr)

    stats = None
    try:
        if args.format == "canonical":
            txs, warnungen = from_canonical(rows, hint)
        elif args.format == "kraken":
            txs, warnungen, stats = from_kraken_ledger(rows, hint)
        else:
            with open(args.map, encoding="utf-8") as f:
                mapping = json.load(f)
            txs, warnungen = from_mapping(rows, mapping, hint)
        warnungen += pruefe_typen(txs)
    except sl.ParseError as e:
        print(f"ABBRUCH: {e}", file=sys.stderr)
        sys.exit(1)

    nicht_zugeordnet = stats["nicht_zugeordnet"] if stats else \
        sum(1 for t in txs if t.get("_needs_review"))
    payload = {"transactions": txs,
               "quelle": os.path.basename(args.csv_path),
               "warnungen": warnungen}
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{len(txs)} Transaktionen geschrieben: {args.out}")
    else:
        print(text)

    print(f"{nicht_zugeordnet} von {len(rows)} Zeilen nicht zugeordnet.", file=sys.stderr)
    for w in warnungen:
        print(f"WARNUNG: {w}", file=sys.stderr)
    needs_fmv = [t for t in txs if t.get("_needs_fmv")]
    if needs_fmv:
        print(f"\nACHTUNG: {len(needs_fmv)} Transaktion(en) ohne EUR-Marktwert "
              f"(eur_value ist null): Staking-Zuflüsse, Krypto-Tausche und Ein-/"
              f"Auslieferungen. Der Wert wird NICHT als 0 angenommen — krypto_fifo.py "
              f"bricht ab, solange er fehlt. Bitte den historischen Kurs zum Zeitpunkt "
              f"eintragen (und das Feld '_needs_fmv' dabei stehen lassen oder "
              f"entfernen, beides ist zulässig).", file=sys.stderr)


if __name__ == "__main__":
    main()
