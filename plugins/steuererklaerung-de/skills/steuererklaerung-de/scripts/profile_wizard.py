#!/usr/bin/env python3
"""
profile_wizard.py — schlägt aus einem echten Broker-Report ein **Profil-Gerüst** vor.

    python scripts/profile_wizard.py <report.pdf|csv|txt> --id <profil-id>
           [--out scripts/profiles/<id>.json] [--fixture tests/fixtures/<id>.txt]
           [--kind auto|krypto_vorberechnet|krypto_transaktionen|kap]

Der Wizard liefert einen **Entwurf**, keine fertige Anbindung (siehe
`references/broker-profile.md`). Er

  1. extrahiert den Text (PDF über die Backends aus `parse_pdf.py`, CSV direkt),
  2. schlägt `erkennung`-Muster vor (Marke, Dokumenttitel, charakteristische Köpfe)
     und filtert dabei Datum, Kontonummer und Namen aktiv heraus,
  3. sucht Tabellen: wiederkehrende Zeilenstrukturen, `start`/`ende`-Anker und eine
     `zeile`-Regex mit benannten Gruppen; die Spaltenzuordnung nutzt die
     Synonymtabelle aus `parse_pdf.py`,
  4. sucht `summen` — ausgewiesene Gesamtwerte; das ist der wertvollste Teil, weil ein
     Profil ohne funktionierenden Abgleich stromabwärts abgelehnt wird,
  5. rät `notation`/`datum` über `steuerlib.detect_locale` und die tatsächlich
     vorkommenden Datumsformen,
  6. **validiert den eigenen Vorschlag** gegen den Report, aus dem er stammt, und
  7. schreibt ein anonymisiertes Fixture-Gerüst und meldet jede Redaktion einzeln.

Was er nicht sicher weiß, schreibt er als `"TODO"`. `parse_broker.py` lehnt Profile mit
TODO ab — genau so ist es gemeint: der Wizard ist ein Startpunkt, kein Ergebnis.

Keine Steuerberatung.
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
import steuerlib as sl      # noqa: E402
import parse_pdf as pp      # noqa: E402

TODO = "TODO"
D = Decimal

ERGEBNIS_ARTEN = ("krypto_vorberechnet", "krypto_transaktionen", "kap")

# Kanonische Transaktionsfelder (Spiegel von brokerprofile.KANONISCHE_TX_FELDER —
# der Wizard darf nicht am Import scheitern, wenn es die Datei noch nicht gibt).
KANONISCHE_TX_FELDER = ["timestamp", "type", "asset", "amount", "eur_value", "fee_eur",
                        "reward_kind", "counter_asset", "counter_amount", "tx_id", "source"]

# Regex-Makros des Profil-Formats. Ein Entwurf voller ausgeschriebener Betragswüsten
# liest sich niemand durch — {NUM}/{DT}/{VOR} sind genau dafür da.
MAKROS = {
    "NUM": r"(?:\(\s*)?[-−–+]?\s*\d[\d.,]*(?:\s*\))?-?",
    "DT": r"\d{1,4}[./-]\d{1,2}[./-]\d{2,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?",
    "VOR": r"[^\d\-−–(+\n]*",
}
_MAKRO_RE = re.compile(r"\{([A-Z_]+)\}")


def entfalte(muster: str) -> str:
    """Makros einsetzen — bevorzugt mit `brokerprofile.entfalte`, damit Entwurf und
    Laufzeit dieselbe Auflösung sehen."""
    bp = _lade_brokerprofile()
    if bp is not None and hasattr(bp, "entfalte"):
        try:
            return bp.entfalte(muster)
        except Exception:
            pass
    return _MAKRO_RE.sub(lambda m: MAKROS.get(m.group(1), m.group(0)), muster)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# 0. Text besorgen
# ─────────────────────────────────────────────────────────────────────────────

def _lade_brokerprofile():
    """`brokerprofile.py` wird parallel entwickelt — Import bewusst spät und weich."""
    try:
        import brokerprofile  # noqa: F401
        return brokerprofile
    except Exception:
        return None


def _lies_textdatei(pfad: Path) -> str:
    roh = pfad.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return roh.decode(enc)
        except UnicodeDecodeError:
            continue
    return roh.decode("utf-8", errors="replace")


def text_aus_datei(pfad, *, backend: str = "auto", ocr_lang: str = "deu+eng") -> tuple[str, str]:
    """(Text, eingabeart). Nutzt `brokerprofile.text_aus_datei`, wenn vorhanden,
    sonst die Backends aus `parse_pdf.py` — nichts davon wird hier nachgebaut."""
    p = Path(pfad)
    art = "pdf" if p.suffix.lower() == ".pdf" else "csv"
    if p.suffix.lower() in (".txt", ".text"):
        art = "txt"

    # Zuerst der Weg, den auch die Laufzeit geht: sonst passen die Muster später
    # auf einen Text, den parse_broker.py so nie sieht.
    bp = _lade_brokerprofile()
    if bp is not None and hasattr(bp, "text_aus_datei"):
        try:
            txt = str(bp.text_aus_datei(str(p)))
            if art != "pdf" or len(txt.strip()) >= 200:
                return txt, art
            print("  brokerprofile.text_aus_datei liefert fast keinen Text — "
                  "gescanntes PDF? Versuche parse_pdf inkl. OCR.", file=sys.stderr)
        except Exception as e:      # eigener Weg statt Abbruch
            print(f"  brokerprofile.text_aus_datei fehlgeschlagen ({e}) — nutze parse_pdf.",
                  file=sys.stderr)

    if p.suffix.lower() != ".pdf":
        return _lies_textdatei(p), art

    extraktion = pp.extract(str(p), backend=backend, ocr_lang=ocr_lang)
    teile = []
    for seite in extraktion.get("pages", []):
        txt = seite.get("text") or ""
        teile.append(txt)
        # Bei reinen Tabellen-Backends (docling/OCR) steckt der Inhalt in `tables`;
        # ohne diese Zeilen sähe der Wizard eine leere Seite.
        if len(txt.strip()) < 40:
            for tab in seite.get("tables", []) or []:
                for zeile in tab:
                    teile.append("   ".join(str(c or "").strip() for c in zeile))
    print(f"  Backend: {extraktion.get('backend')} | OCR: {extraktion.get('ocr')} | "
          f"Seiten: {len(extraktion.get('pages', []))}", file=sys.stderr)
    return "\n".join(teile), art


# ─────────────────────────────────────────────────────────────────────────────
# 1. Anonymisierung (Fixture + Filter für `erkennung`)
# ─────────────────────────────────────────────────────────────────────────────

# (art, muster, gruppe, ersatz) — gruppe 0 heißt: ganzer Treffer wird ersetzt.
_PII = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}"), 0, "[EMAIL]"),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,4})?\b"),
     0, "[IBAN]"),
    ("steuer_id", re.compile(
        r"(?:Steuer-?(?:ID|IdNr\.?|Identifikationsnummer)|St(?:euer)?-?Nr\.?|Tax\s*ID)"
        r"\s*:?\s*([\d][\d /.-]{8,20})", re.I), 1, "[STEUER-ID]"),
    # Freistehende 11-stellige Zahl: die deutsche IdNr. Beträge haben Trennzeichen.
    ("steuer_id", re.compile(r"(?<![\d.,/-])\d{11}(?![\d.,/-])"), 0, "[STEUER-ID]"),
    # Nummer nur mit ausdrücklichem Label *und* Ziffer im Wert: sonst schluckt
    # "Kontoinhaber" sein eigenes Wortende und der Name dahinter bleibt stehen.
    ("konto", re.compile(
        r"(?:Konto|Depot|Kunden|Vertrag|Account|Portfolio|Referenz)"
        r"(?:nummer|-?\s?Nr\.?|nr\.?|-?ID)\s*[:.#]?\s*"
        r"((?=[A-Z0-9./-]{5,})[A-Z0-9./-]*\d[A-Z0-9./-]*)", re.I), 1, "[KONTO]"),
    ("konto", re.compile(
        r"(?:Konto|Depot|Kunden|Vertrag|Account|Portfolio)\s*[:#]\s*"
        r"((?=[A-Z0-9./-]{5,})[A-Z0-9./-]*\d[A-Z0-9./-]*)", re.I), 1, "[KONTO]"),
    ("adresse", re.compile(
        r"\b[A-ZÄÖÜ][\wäöüß.-]*(?:stra(?:ß|ss)e|str\.|weg|allee|platz|gasse|ring|damm)"
        r"\s+\d+\s*[a-zA-Z]?\b"), 0, "[ADRESSE]"),
    ("adresse", re.compile(r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß.-]+(?:\s+[A-ZÄÖÜ][a-zäöüß.-]+)?\b"),
     0, "[ADRESSE]"),
    ("name", re.compile(
        r"(?:Herr|Frau|Name|Kunde|Kundin|Kontoinhaber(?:in)?|Depotinhaber(?:in)?|"
        r"Steuerpflichtige[rn]?|Mr\.?|Mrs\.?|Ms\.?)\s*:?\s+"
        r"((?:[A-ZÄÖÜ][\wäöüß'’-]+\s+){0,2}[A-ZÄÖÜ][\wäöüß'’-]+)"), 1, "[NAME]"),
]

# Zeile, die *nur* aus zwei bis drei großgeschriebenen Wörtern besteht — der klassische
# Adresskopf. Ohne die Fachwortliste unten würde sie auch "Zusammenfassung Kapitalgewinne"
# schwärzen, und das Fixture wäre wertlos.
_NAME_ZEILE = re.compile(
    r"^(?:[A-ZÄÖÜ][a-zäöüß'’-]{1,20}\.?\s+){1,2}"
    r"(?:(?:von|van|de|der|zu)\s+)?[A-ZÄÖÜ][a-zäöüß'’-]{1,20}$")

_FACHWORTE = {
    # Dokument-/Tabellenvokabular. Alles hier gilt nie als Personenname.
    "steuerbericht", "steuerreport", "bericht", "report", "tax", "kapitalgewinne",
    "kapitalgewinn", "zusammenfassung", "summary", "summe", "gesamt", "gesamtergebnis",
    "total", "einnahmen", "income", "ausgaben", "expenses", "verkaufsdatum",
    "erwerbsdatum", "kaufdatum", "asset", "menge", "erlös", "erloes", "kostenbasis",
    "gewinn", "verlust", "haltedauer", "seite", "page", "datum", "date", "konto",
    "depot", "wallet", "transaktion", "transaktionen", "übersicht", "uebersicht",
    "anlage", "kap", "steuerbescheinigung", "erträgnisaufstellung",
    "ertraegnisaufstellung", "jahressteuerbescheinigung", "kapitalertragsteuer",
    "solidaritätszuschlag", "kirchensteuer", "quellensteuer", "dividende", "dividenden",
    "zinsen", "wertpapier", "wertpapiere", "aktien", "termingeschäfte", "veräußerung",
    "veraeusserung", "veräußerungen", "ergebnis", "netto", "brutto", "betrag", "währung",
    "waehrung", "kurs", "preis", "stück", "stueck", "gebühr", "gebühren", "art", "typ",
    "kurzfristig", "langfristig", "offene", "bestände", "bestaende", "hinweis",
    "hinweise", "disclaimer", "abrechnung", "depotauszug", "umsatzübersicht",
    # Firmenbestandteile: sonst wird "Musterbroker Bank" zum "Namen".
    "bank", "broker", "brokerage", "ag", "gmbh", "se", "kg", "ltd", "limited", "inc",
    "llc", "plc", "capital", "invest", "investments", "securities", "markets", "market",
    "trading", "trade", "exchange", "group", "holding", "finance", "financial",
    "europe", "deutschland", "germany", "global",
}


def _ist_pii(text: str) -> str | None:
    """Art der PII, wenn der String wie PII aussieht — sonst None."""
    for art, muster, gruppe, _ersatz in _PII:
        m = muster.search(text)
        if m:
            return art
    if re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", text):
        return "datum"
    return None


def anonymisiere(text: str, *, schuetze: tuple[str, ...] = ()) -> tuple[str, list[dict]]:
    """Schwärzt Namen, IBAN, Konto-/Steuernummern, Adressen und E-Mails.

    Rückgabe: (anonymisierter Text, Liste der Redaktionen). Die Liste enthält den
    Originalwert — sie ist zum Nachsehen gedacht, nicht zum Weitergeben.
    """
    geschuetzt = {s.strip().lower() for s in schuetze if s and s.strip()}
    redaktionen: list[dict] = []
    zeilen = text.split("\n")
    haeufigkeit: dict[str, int] = {}
    for z in zeilen:
        haeufigkeit[z.strip()] = haeufigkeit.get(z.strip(), 0) + 1

    for idx, zeile in enumerate(zeilen):
        neu = zeile
        for art, muster, gruppe, ersatz in _PII:
            def _ers(m, art=art, gruppe=gruppe, ersatz=ersatz, idx=idx):
                treffer = m.group(gruppe) if gruppe else m.group(0)
                if not treffer or treffer.strip().lower() in geschuetzt:
                    return m.group(0)
                redaktionen.append({"art": art, "original": treffer.strip(),
                                    "ersatz": ersatz, "zeile": idx + 1})
                if gruppe:
                    return m.group(0).replace(treffer, ersatz)
                return ersatz
            neu = muster.sub(_ers, neu)

        # Freistehende Namenszeile (Adresskopf).
        kandidat = neu.strip()
        if (kandidat and kandidat == zeile.strip()
                and _NAME_ZEILE.match(kandidat)
                and haeufigkeit.get(kandidat, 0) <= 2
                and kandidat.lower() not in geschuetzt
                and not any(w.strip(".,;:").lower() in _FACHWORTE for w in kandidat.split())):
            redaktionen.append({"art": "name?", "original": kandidat,
                                "ersatz": "[NAME]", "zeile": idx + 1})
            neu = neu.replace(kandidat, "[NAME]")

        zeilen[idx] = neu
    return "\n".join(zeilen), redaktionen


# ─────────────────────────────────────────────────────────────────────────────
# 2. Erkennung
# ─────────────────────────────────────────────────────────────────────────────

_TITEL = re.compile(
    r"(Steuerbericht|Steuerreport|Steuerbescheinigung|Jahressteuerbescheinigung|"
    r"Ertr(?:ä|ae)gnisaufstellung|Tax\s+Report|Capital\s+Gains?\s+Report|"
    r"Kapitalgewinne|Transaktions(?:bericht|übersicht)|Umsatz(?:übersicht|liste)|"
    r"Gewinn-?\s*und\s*Verlust|Annual\s+Statement|Depotauszug|Abrechnung|"
    r"Erträgnis(?:aufstellung|übersicht))", re.I)

_ZU_GENERISCH = {
    "seite", "page", "summe", "gesamt", "total", "datum", "date", "eur", "usd",
    "konto", "depot", "name", "adresse", "kunde", "betrag", "wert", "anlage",
    "zusammenfassung", "summary", "übersicht", "uebersicht", "hinweis", "hinweise",
    "von", "bis", "für", "fuer", "und", "der", "die", "das",
    # Platzhalter der Anonymisierung — die dürfen nie in `erkennung` landen.
    "iban", "email", "steuer-id", "kontonummer", "depotnummer",
}

# Firmenkennzeichen: die Markenzeile ist das stabilste Erkennungsmerkmal, viel
# stabiler als ein Dokumenttitel, den drei Anbieter gleich schreiben.
_FIRMA = re.compile(r"(?:\b(?:AG|SE|GmbH|KG|B\.?V\.?|N\.?V\.?|Ltd\.?|Limited|Inc\.?|LLC|"
                    r"PLC|S\.?A\.?|Bank|Broker|Brokerage|Capital|Securities|Exchange|"
                    r"Markets?|Trading|Invest(?:ments?)?|Group|Holding)\b)", re.I)


def _erkennungs_kandidaten(text: str, trenner: str | None = None) -> list[tuple[int, str]]:
    # Kandidaten aus dem *anonymisierten* Text: so kann ein Name, eine Adresse oder
    # eine Kontonummer gar nicht erst als Erkennungsmuster vorgeschlagen werden.
    text, _ = anonymisiere(text)
    zeilen = [z.strip() for z in text.split("\n")]
    # Datenzeilen liefern nur Zufallstreffer ("Zauberei", ein Assetname) — sie
    # beschreiben den Reporttyp nicht und ändern sich mit jedem Export.
    daten = {s["i"] for s in _struktur(zeilen, trenner) if _ist_datenzeile(s)}
    nichtleer = [(i, z) for i, z in enumerate(zeilen) if z and i not in daten]
    haeufig: dict[str, int] = {}
    for _i, z in nichtleer:
        haeufig[z] = haeufig.get(z, 0) + 1

    kandidaten: dict[str, int] = {}

    def _anbieten(tok: str, punkte: int):
        tok = re.sub(r"\s+", " ", tok).strip(" .:,;–—-")
        if len(tok) < 4 or len(tok) > 50:
            return
        if re.search(r"\d", tok) or "@" in tok or "[" in tok or "]" in tok:
            return                                  # Datum, Kontonummer, Betrag, Platzhalter
        if _ist_pii(tok):
            return
        if all(w.lower().strip(".,") in _ZU_GENERISCH for w in tok.split()):
            return
        if not re.search(r"[A-Za-zÄÖÜäöüß]{4,}", tok):
            return
        kandidaten[tok] = max(kandidaten.get(tok, 0), punkte)

    kopf = nichtleer[:30]
    for rang, (_i, z) in enumerate(kopf):
        punkte = 6 if rang < 5 else 3               # Markenzeile steht oben
        if haeufig.get(z, 0) > 1:
            punkte += 4                             # wiederholt sich = stabil (Kopf/Fuß)
        if _TITEL.search(z):
            punkte += 6
        if _FIRMA.search(z) and len(z) <= 50:
            punkte += 6
        if len(z) <= 50 and not re.search(r"\d", z):
            _anbieten(z, punkte)
        for stueck in re.findall(r"[A-ZÄÖÜ][\wäöüß&.\-]{3,}(?:\s+[A-ZÄÖÜ][\wäöüß&.\-]{2,}){0,2}",
                                 z):
            _anbieten(stueck, punkte - 1)

    for i, z in nichtleer:
        m = _TITEL.search(z)
        if m:
            _anbieten(m.group(0), 8 + (3 if haeufig.get(z, 0) > 1 else 0))

    rang = sorted(kandidaten.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [(p, t) for t, p in rang]


def schlage_erkennung_vor(text: str, trenner: str | None = None) -> tuple[dict, list[str], list[str]]:
    """(erkennung, marken, hinweise). `marken` sind die Rohtoken (für Label/Fixture)."""
    rang = _erkennungs_kandidaten(text, trenner)
    hinweise: list[str] = []
    gewaehlt: list[str] = []
    for _p, tok in rang:
        # Teilstrings bereits gewählter Muster bringen nichts dazu.
        if any(tok.lower() in g.lower() or g.lower() in tok.lower() for g in gewaehlt):
            continue
        gewaehlt.append(tok)
        if len(gewaehlt) == 2:
            break

    if not gewaehlt:
        return ({"muss": [TODO], "darf_nicht": [], "punkte": 10}, [],
                ["Keine tragfähige Erkennung gefunden — zwei charakteristische, "
                 "unveränderliche Textstellen von Hand eintragen."])

    muss = [re.escape(t).replace("\\ ", r"\s+") for t in gewaehlt]
    weitere = [t for _p, t in rang[:8] if t not in gewaehlt][:4]
    if weitere:
        hinweise.append("weitere Erkennungs-Kandidaten: " + ", ".join(repr(w) for w in weitere))
    return ({"muss": muss, "darf_nicht": [], "punkte": 10}, gewaehlt, hinweise)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Ergebnisart raten
# ─────────────────────────────────────────────────────────────────────────────

_KIND_SIGNALE = {
    "kap": [r"Anlage\s+KAP", r"Steuerbescheinigung", r"Ertr(?:ä|ae)gnisaufstellung",
            r"Kapitalertrag(?:s?steuer)?", r"Zeile\s+\d{1,2}\b", r"Solidarit(?:ä|ae)tszuschlag",
            r"Kirchensteuer", r"Verlustverrechnungstopf", r"Termingesch(?:ä|ae)fte",
            r"Sparer-?Pauschbetrag", r"Steuerabzug"],
    "krypto_vorberechnet": [r"Kapitalgewinn", r"Capital\s+gains?", r"Kostenbasis",
                            r"Cost\s+basis", r"Erl(?:ö|oe)s\b", r"Proceeds", r"Haltedauer",
                            r"Holding\s+period", r"Ver(?:ä|ae)u(?:ß|ss)erungsgewinn",
                            r"Gewinn/Verlust", r"realisierte[rn]?\s+Gewinn"],
    "krypto_transaktionen": [r"Transaktion(?:en|sliste)?\b", r"\bTrades?\b", r"\bDeposit\b",
                             r"Einzahlung", r"Auszahlung", r"Withdraw", r"\bWallet\b",
                             r"\bSwap\b", r"Staking", r"\bReward", r"Handelspaar"],
}


def rate_ergebnisart(text: str) -> tuple[str, dict]:
    punkte = {k: sum(1 for m in muster if re.search(m, text, re.I))
              for k, muster in _KIND_SIGNALE.items()}
    reihe = sorted(punkte.items(), key=lambda kv: -kv[1])
    bester, best_p = reihe[0]
    zweit_p = reihe[1][1] if len(reihe) > 1 else 0
    # Nur ein *deutlicher* Vorsprung zählt; sonst lieber TODO als ein falsches Schema.
    if best_p >= 2 and best_p - zweit_p >= 2:
        return bester, punkte
    return TODO, punkte


# ─────────────────────────────────────────────────────────────────────────────
# 4. Notation und Datumsformat
# ─────────────────────────────────────────────────────────────────────────────

_DT_TOKEN = re.compile(r"\b(\d{1,4})[./-](\d{1,2})[./-](\d{2,4})\b")


def rate_notation(text: str) -> tuple[str, str]:
    de = len(re.findall(r"\d{1,3}(?:\.\d{3})+,\d", text)) + len(re.findall(r"\d+,\d{2}\b", text))
    en = len(re.findall(r"\d{1,3}(?:,\d{3})+\.\d", text)) + len(re.findall(r"\d+\.\d{2}\b", text))
    if max(de, en) < 3 or abs(de - en) < max(2, 0.2 * max(de, en)):
        return "auto", (f"Notation nicht eindeutig (de-Muster {de}, en-Muster {en}) — "
                        f"'auto' lässt das Dokument zur Laufzeit vermessen.")
    return sl.detect_locale(text), f"de-Muster {de}, en-Muster {en}"


def rate_datumsformat(text: str) -> tuple[str, str]:
    iso = dmy = mdy = mehrdeutig = 0
    for a, b, c in _DT_TOKEN.findall(text):
        if len(a) == 4:
            iso += 1
            continue
        ai, bi = int(a), int(b)
        if ai > 12 and bi <= 12:
            dmy += 1
        elif bi > 12 and ai <= 12:
            mdy += 1
        elif ai <= 12 and bi <= 12:
            mehrdeutig += 1
    if iso and iso >= dmy + mdy + mehrdeutig:
        return "iso", f"{iso} ISO-Datumsangaben"
    if dmy and not mdy:
        return "de", f"{dmy} eindeutige TT.MM-Angaben (Tag > 12)"
    if mdy and not dmy:
        return "en", f"{mdy} eindeutige MM/TT-Angaben"
    if dmy and mdy:
        return TODO, (f"widersprüchlich: {dmy} Datumsangaben lesen sich als TT.MM, "
                      f"{mdy} als MM/TT — Report enthält vermutlich zwei Formate.")
    if mehrdeutig:
        return TODO, (f"alle {mehrdeutig} Datumsangaben sind in beiden Lesarten gültig "
                      f"(Tag und Monat ≤ 12) — Format im Original nachsehen.")
    return TODO, "keine Datumsangaben gefunden."


# ─────────────────────────────────────────────────────────────────────────────
# 5. Tabellen: Struktur, Anker, Zeilenregex, Spaltenzuordnung
# ─────────────────────────────────────────────────────────────────────────────

_WAEHRUNG = re.compile(r"(?:EUR|€|USD|\$|CHF|£|%)", re.I)
_DT_ZELLE = re.compile(r"^\d{1,4}[./-]\d{1,2}[./-]\d{2,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$")
_NUM_ZELLE = re.compile(r"^[-−–+(]?\d[\d.,]*\)?-?$")

_MUSTER = {"DATE": "{DT}", "NUM": "{NUM}"}


def _tokenklasse(tok: str) -> str:
    t = _WAEHRUNG.sub("", str(tok)).strip()
    if not t:
        return "LEER"
    if _DT_ZELLE.match(t):
        return "DATE"
    if _NUM_ZELLE.match(t):
        return "NUM"
    return "WORD"


def csv_trenner(text: str) -> str | None:
    """Erkennt den Spaltentrenner einer CSV. None = Whitespace-Layout (PDF-Text)."""
    zeilen = [z for z in text.split("\n") if z.strip()][:40]
    if not zeilen:
        return None
    for t in (";", "\t", ","):
        zaehler = [z.count(t) for z in zeilen]
        treffer = [c for c in zaehler if c >= 2]
        if len(treffer) >= max(3, len(zeilen) // 3) and len(set(treffer)) <= 3:
            return t
    return None


def _tokens(zeile: str, trenner: str | None) -> list[str]:
    if trenner:
        return [z.strip().strip('"').strip() for z in zeile.split(trenner)]
    return zeile.split()


def _struktur(zeilen: list[str], trenner: str | None) -> list[dict]:
    aus = []
    for i, z in enumerate(zeilen):
        toks = _tokens(z, trenner)
        klassen = tuple(_tokenklasse(t) for t in toks)
        aus.append({"i": i, "text": z, "tokens": toks, "klassen": klassen,
                    "num": klassen.count("NUM"), "dat": klassen.count("DATE")})
    return aus


def _ist_datenzeile(s: dict) -> bool:
    return len(s["tokens"]) >= 3 and (s["num"] >= 2 or (s["dat"] >= 1 and s["num"] >= 1))


def finde_bloecke(struktur: list[dict], min_zeilen: int = 3) -> list[dict]:
    """Läufe gleichförmiger Datenzeilen. Bis zu zwei nicht passende Zeilen (Seitenkopf,
    Umbruch) unterbrechen einen Block nicht — sie tauchen später als 'nicht zugeordnet'
    in der Selbstprüfung auf, und genau das ist das gewünschte Frühwarnsignal."""
    bloecke: list[dict] = []
    i = 0
    n = len(struktur)
    while i < n:
        s = struktur[i]
        if not _ist_datenzeile(s):
            i += 1
            continue
        schluessel = (s["klassen"][0], s["num"], s["dat"])
        zeilen = [s]
        j = i + 1
        luecke = 0
        letzte = i
        while j < n and luecke <= 2:
            t = struktur[j]
            if _ist_datenzeile(t) and (t["klassen"][0], t["num"], t["dat"]) == schluessel:
                zeilen.append(t)
                letzte = j
                luecke = 0
            elif t["text"].strip() == "":
                pass                      # Leerzeilen zählen nicht als Lücke
            else:
                luecke += 1
            j += 1
        if len(zeilen) >= min_zeilen:
            bloecke.append({"start": i, "ende": letzte, "zeilen": zeilen,
                            "schluessel": schluessel})
            i = letzte + 1
        else:
            i += 1
    bloecke.sort(key=lambda b: -len(b["zeilen"]))
    return bloecke


# --- Spaltenzuordnung: Synonyme aus parse_pdf plus report-spezifische Ergänzungen ---

# `parse_pdf.SYN` deckt Transaktionslisten ab. Veräußerungs- und KAP-Tabellen haben
# eigene Kopfzeilen; diese Tabelle ergänzt sie, ohne parse_pdf anzufassen.
ZUSATZ_SYN = {
    "krypto_vorberechnet": {
        "disposal_date": ["verkaufsdatum", "veräußerungsdatum", "veraeusserungsdatum",
                          "verkauft am", "date sold", "disposal date", "sell date",
                          "datum verkauf", "abgangsdatum"],
        "acquisition_date": ["erwerbsdatum", "kaufdatum", "anschaffungsdatum", "gekauft am",
                             "date acquired", "acquisition date", "buy date",
                             "datum kauf", "zugangsdatum"],
        "asset": ["asset", "coin", "währung", "waehrung", "symbol", "token", "wertpapier",
                  "bezeichnung"],
        "amount": ["menge", "anzahl", "amount", "quantity", "stück", "stueck", "nominal"],
        "proceeds_eur": ["erlös", "erloes", "proceeds", "verkaufserlös", "verkaufserloes",
                         "veräußerungserlös", "verkaufswert", "erlöse"],
        # Der Feldname heißt im Ausgabeschema cost_basis_eur, nicht cost_eur.
        "cost_basis_eur": ["kostenbasis", "cost basis", "anschaffungskosten",
                           "einstandswert", "kaufwert", "einstandspreis", "kosten basis"],
        "gain_eur": ["gewinn", "kapitalgewinn", "gain", "profit", "ergebnis", "p&l",
                     "gewinn/verlust", "gewinn verlust", "realisierter gewinn",
                     "veräußerungsgewinn", "gain/loss", "net gain"],
        "fee_eur": ["gebühr", "gebühren", "fee", "fees", "kosten", "spesen", "provision"],
        "holding": ["haltedauer", "holding period", "holding", "frist", "haltefrist"],
    },
    "krypto_transaktionen": {
        "timestamp": ["datum", "date", "zeitpunkt", "zeit", "timestamp", "buchungstag",
                      "valuta", "ausführung", "trade date"],
        "type": ["typ", "type", "art", "vorgang", "geschäftsart", "transaktionsart",
                 "action", "side", "richtung"],
        "asset": ["asset", "coin", "währung", "waehrung", "symbol", "token", "instrument"],
        "amount": ["menge", "anzahl", "amount", "quantity", "stück", "stueck"],
        "eur_value": ["wert", "value", "betrag", "gegenwert", "eur-wert", "gesamtwert",
                      "umsatz", "total"],
        "fee_eur": ["gebühr", "gebühren", "fee", "fees", "spesen", "provision"],
        "counter_asset": ["gegenwährung", "zielwährung", "counter", "quote", "erhalten"],
        "counter_amount": ["gegenmenge", "counter amount", "erhaltene menge",
                           "menge erhalten"],
        "tx_id": ["tx-id", "tx id", "txid", "transaktions-id", "referenz", "reference",
                  "hash", "id"],
    },
    "kap": {
        "zeile": ["zeile", "kap-zeile", "line", "nr", "nummer"],
        "bezeichnung": ["bezeichnung", "art", "position", "sachverhalt", "beschreibung"],
        "betrag": ["betrag", "wert", "summe", "eur", "amount"],
    },
}

# Brücke: Kanonname aus parse_pdf.SYN -> Kanonname des jeweiligen Ausgabeschemas.
SYN_BRUECKE = {
    "krypto_vorberechnet": {"date": "disposal_date", "asset": "asset", "amount": "amount",
                            "eur_value": "proceeds_eur", "fee": "fee_eur"},
    "krypto_transaktionen": {"date": "timestamp", "type": "type", "asset": "asset",
                             "amount": "amount", "eur_value": "eur_value",
                             "fee": "fee_eur", "counter_asset": "counter_asset"},
    "kap": {"eur_value": "betrag"},
}

PFLICHT = {
    "krypto_vorberechnet": ["disposal_date", "gain_eur"],
    "krypto_transaktionen": ["timestamp", "type", "asset", "amount"],
    "kap": [],
}

# Ab hier gilt ein Kopfzellen-Treffer als belastbar: exakt, Präfix oder Suffix.
# Reine Teilstring-Treffer (20 + Länge) machten aus "Vorgangsnummer" ein `type` —
# der Wizard darf raten, aber nicht falsch zuordnen.
SCHWELLE = 40


def _kanon_fuer_kopf(zelle: str, ergebnis: str) -> tuple[str | None, int]:
    """Bester Kanonname für eine Kopfzelle plus Score (reuse: parse_pdf._score)."""
    h = pp._norm(zelle)
    if not h:
        return None, 0
    treffer: dict[str, int] = {}
    for canon, syns in ZUSATZ_SYN.get(ergebnis, {}).items():
        best = max((pp._score(h, s) for s in syns), default=0)
        if best:
            # +1: bei Gleichstand gewinnt die schema-spezifische Tabelle.
            treffer[canon] = max(treffer.get(canon, 0), best + 1)
    bruecke = SYN_BRUECKE.get(ergebnis, {})
    for canon, syns in pp.SYN.items():
        ziel = bruecke.get(canon)
        if not ziel:
            continue
        best = max((pp._score(h, s) for s in syns), default=0)
        if best:
            treffer[ziel] = max(treffer.get(ziel, 0), best)
    if not treffer:
        return None, 0
    canon = max(treffer, key=treffer.get)
    return canon, treffer[canon]


def _ist_kopfzeile(s: dict, ergebnis: str) -> bool:
    if s["num"] or s["dat"] or len(s["tokens"]) < 2:
        return False
    treffer = sum(1 for t in s["tokens"] if _kanon_fuer_kopf(t, ergebnis)[0]
                  or pp._match_col(t))
    return treffer >= 2


_ENDE_WORTE = re.compile(
    r"^(Zusammenfassung|Summary|Summe|Gesamt\w*|Total|Insgesamt|Ergebnis|Saldo|"
    r"Einnahmen|Income|Ausgaben|Expenses|Futures|Derivate|Offene|Open|Hinweis\w*|"
    r"Disclaimer|Anhang|Erl(?:ä|ae)uterung\w*)\b", re.I)


def _gruppenname(zelle: str, index: int, vergeben: set[str]) -> str:
    roh = pp._norm(zelle)
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        roh = roh.replace(a, b)
    roh = re.sub(r"[^a-z0-9]+", "_", roh).strip("_")[:20]
    if not roh or not re.match(r"^[a-z]", roh):
        roh = f"spalte{index + 1}"
    name = roh
    k = 2
    while name in vergeben:
        name = f"{roh}{k}"
        k += 1
    vergeben.add(name)
    return name


def _spaltenklassen(rows: list[dict]) -> tuple[list[str], int | None, list[bool]]:
    """(Klassen je Spalte, Index einer Freitext-Spalte oder None, 'kann leer sein')."""
    laengen = {len(r["klassen"]) for r in rows}

    def _vote(werte: set[str]) -> str:
        echte = werte - {"LEER"}
        if len(echte) == 1:
            return echte.pop()
        if echte == {"NUM", "DATE"}:
            return "NUM"
        return "WORD"

    if len(laengen) == 1:
        n = laengen.pop()
        klassen, leer = [], []
        for c in range(n):
            werte = {r["klassen"][c] for r in rows}
            klassen.append(_vote(werte))
            leer.append("LEER" in werte)
        return klassen, None, leer

    kurz = min(laengen)
    praefix: list[str] = []
    for c in range(kurz):
        werte = {r["klassen"][c] for r in rows}
        if len(werte - {"LEER"}) != 1:
            break
        praefix.append(_vote(werte))
    suffix: list[str] = []
    for c in range(1, kurz - len(praefix) + 1):
        werte = {r["klassen"][-c] for r in rows}
        if len(werte - {"LEER"}) != 1:
            break
        suffix.insert(0, _vote(werte))
    klassen = praefix + ["TEXT"] + suffix
    return klassen, len(praefix), [False] * len(klassen)


def _kopfzellen(kopf: dict | None, klassen: list[str], textpos: int | None,
                trenner: str | None) -> list[str]:
    """Kopfzellen so ausrichten, dass Zelle i zur Spalte i gehört."""
    n = len(klassen)
    if kopf is None:
        return [""] * n
    zellen = list(kopf["tokens"])
    if len(zellen) != n and not trenner:
        weit = [z.strip() for z in re.split(r"\s{2,}", kopf["text"].strip()) if z.strip()]
        if len(weit) == n:
            zellen = weit
    if len(zellen) == n:
        return zellen
    if textpos is not None and len(zellen) > n:
        # Freitextspalte bekommt die überzähligen Kopfzellen der Mitte.
        rechts = n - textpos - 1
        mitte = " ".join(zellen[textpos:len(zellen) - rechts])
        return zellen[:textpos] + [mitte] + (zellen[len(zellen) - rechts:] if rechts else [])
    return [""] * n


def baue_zeilenregex(klassen: list[str], namen: list[str], trenner: str | None,
                     kann_leer: list[bool]) -> str:
    if trenner:
        sep = r"\s*" + re.escape(trenner) + r"\s*"
        wort = r"[^" + re.escape(trenner) + r"]*"
    else:
        sep = r"\s+"
        wort = r"\S+"
    teile = []
    for i, k in enumerate(klassen):
        muster = _MUSTER.get(k) or (wort if k != "TEXT" else (r".+?" if not trenner else wort))
        if kann_leer[i] and trenner:
            muster = wort
        teile.append(f"(?P<{namen[i]}>{muster})")
    return "^" + sep.join(teile) + r"\s*$"


def schlage_tabelle_vor(struktur: list[dict], block: dict, ergebnis: str,
                        trenner: str | None, name: str) -> dict:
    rows = block["zeilen"]
    klassen, textpos, kann_leer = _spaltenklassen(rows)

    kopf = None
    for j in range(block["start"] - 1, max(-1, block["start"] - 7), -1):
        if j < 0:
            break
        if struktur[j]["text"].strip() and _ist_kopfzeile(struktur[j], ergebnis):
            kopf = struktur[j]
            break

    zellen = _kopfzellen(kopf, klassen, textpos, trenner)
    vergeben: set[str] = set()
    namen = [_gruppenname(zellen[i], i, vergeben) for i in range(len(klassen))]

    felder: dict[str, str] = {}
    offen: list[str] = []
    schwach: list[str] = []
    for i, zelle in enumerate(zellen):
        canon, score = _kanon_fuer_kopf(zelle, ergebnis) if zelle else (None, 0)
        if canon and score >= SCHWELLE and canon not in felder:
            felder[canon] = namen[i]
        else:
            offen.append(namen[i])
            if canon:
                schwach.append(f"{namen[i]!r} (Kopf {zelle!r}: schwacher Treffer "
                               f"{canon!r}, Score {score})")
    for k, gname in enumerate(offen):
        schluessel = TODO if k == 0 else f"{TODO}_{k + 1}"
        felder[schluessel] = gname

    if kopf is not None:
        worte = [t for t in kopf["tokens"] if _tokenklasse(t) == "WORD"][:2]
        start = r"\s+".join(re.escape(w) for w in worte) if worte else TODO
    else:
        start = TODO

    ende = TODO
    for j in range(block["ende"] + 1, min(len(struktur), block["ende"] + 25)):
        txt = struktur[j]["text"].strip()
        if not txt:
            continue
        m = _ENDE_WORTE.match(txt)
        if m:
            ende = re.escape(m.group(1))
            break

    zeile = baue_zeilenregex(klassen, namen, trenner, kann_leer)
    try:
        re.compile(entfalte(zeile))
    except re.error:
        zeile = TODO

    pflicht = [f for f in PFLICHT.get(ergebnis, []) if f in felder]
    fehlend = [f for f in PFLICHT.get(ergebnis, []) if f not in felder]
    if fehlend:
        pflicht = pflicht + [TODO]

    p_tab = {
        "name": name,
        "start": start,
        "ende": ende,
        "zeile": zeile,
        "felder": felder,
        "pflicht": pflicht or [TODO],
        "melde_nicht_zugeordnet": True,
    }
    # Ohne Anschaffungsdatum lässt sich die Jahresfrist nicht rechnen — dann *muss*
    # die Haltedauer-Spalte ausgewertet werden, und zwar mit dem Muster, das
    # "langfristig" bedeutet (nicht mit dem, das irgendetwas trifft).
    braucht_haltedauer = ("holding" in felder and "acquisition_date" not in felder
                          and ergebnis == "krypto_vorberechnet")
    if braucht_haltedauer:
        p_tab["langfristig"] = {"feld": "holding", "muster": TODO}

    return {
        "profil": p_tab,
        "meta": {
            "block": block, "klassen": klassen, "namen": namen,
            "kopfzeile": kopf["text"].strip() if kopf else None,
            "kopf_index": kopf["i"] if kopf else None,
            "offen": offen, "schwach": schwach, "fehlende_pflichtfelder": fehlend,
            "braucht_haltedauer": braucht_haltedauer,
            "hat_haltedauer_spalte": "holding" in felder,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5b. CSV: der Motor liest CSVs über einen `csv`-Block, nicht über Zeilenregexe
# ─────────────────────────────────────────────────────────────────────────────

def schlage_csv_vor(struktur: list[dict], trenner: str | None) -> tuple[dict, dict]:
    """(csv-Block, meta). Spaltenüberschriften -> kanonische Transaktionsfelder."""
    kopf = None
    for s in struktur[:20]:
        if not s["text"].strip():
            continue
        treffer = sum(1 for z in s["tokens"]
                      if _kanon_fuer_kopf(z, "krypto_transaktionen")[0])
        if treffer >= 2 and not s["num"] and not s["dat"]:
            kopf = s
            break
    if kopf is None:
        return ({"trennzeichen": trenner or ";", "spalten": {TODO: TODO},
                 "pflicht": [TODO], "typ_werte": {TODO: TODO}},
                {"kopfzeile": None, "kopf_index": None, "offen": [], "schwach": [],
                 "unbekannte_typen": [],
                 "fehlende_pflichtfelder": ["timestamp", "type", "asset", "amount"]})

    spalten: dict[str, str] = {}
    offen: list[str] = []
    schwach: list[str] = []
    typ_spalte = None
    for zelle in kopf["tokens"]:
        if not zelle.strip():
            continue
        canon, score = _kanon_fuer_kopf(zelle, "krypto_transaktionen")
        if (canon and score >= SCHWELLE and canon in KANONISCHE_TX_FELDER
                and canon not in spalten):
            spalten[canon] = zelle.strip()
            if canon == "type":
                typ_spalte = kopf["tokens"].index(zelle)
        else:
            offen.append(zelle.strip())
            if canon:
                schwach.append(f"{zelle.strip()!r} (schwacher Treffer {canon!r}, "
                               f"Score {score})")

    # Rohwerte der Typspalte einsammeln und über die Schlüsselwörter aus parse_pdf
    # zuordnen. Was dort nicht sicher zuzuordnen ist, bleibt TODO — ein falsch
    # geratener Typ macht aus einem Verkauf einen Reward.
    typ_werte: dict[str, str] = {}
    unbekannt: list[str] = []
    if typ_spalte is not None:
        roh: list[str] = []
        for s in struktur[kopf["i"] + 1:]:
            if typ_spalte < len(s["tokens"]):
                v = s["tokens"][typ_spalte].strip()
                if v and v not in roh:
                    roh.append(v)
        for v in roh[:25]:
            t = pp._match_type(pp._norm(v))
            typ_werte[v] = t or TODO
            if not t:
                unbekannt.append(v)
    else:
        typ_werte[TODO] = TODO

    pflicht = [f for f in ("timestamp", "type", "asset", "amount") if f in spalten]
    fehlend = [f for f in ("timestamp", "type", "asset", "amount") if f not in spalten]
    if fehlend:
        pflicht = pflicht + [TODO]

    block = {"trennzeichen": trenner or ";", "spalten": spalten or {TODO: TODO},
             "pflicht": pflicht or [TODO], "typ_werte": typ_werte}
    return block, {"kopfzeile": kopf["text"].strip(), "kopf_index": kopf["i"],
                   "offen": offen, "schwach": schwach,
                   "unbekannte_typen": unbekannt, "fehlende_pflichtfelder": fehlend}


# ─────────────────────────────────────────────────────────────────────────────
# 6. Summen
# ─────────────────────────────────────────────────────────────────────────────

_SUMMEN_WORT = re.compile(
    r"(Zwischensumme|Gesamtergebnis|Gesamtgewinn|Gesamtsumme|Nettoergebnis|"
    r"Kapitalgewinne?|Capital\s+gains?|Ver(?:ä|ae)u(?:ß|ss)erungsgewinn\w*|"
    r"Summe|Gesamt|Total|Insgesamt|Saldo|Netto\w*|Ergebnis|Subtotal|"
    r"Gewinn/Verlust|Steuerpflichtige[rs]?\s+\w+|"
    # KAP: hier sind die ausgewiesenen Kennzahlen selbst der Abgleich.
    r"Kapitalertr(?:ä|ae)ge|Kapitalertragsteuer|Solidarit(?:ä|ae)tszuschlag|"
    r"Kirchensteuer|Quellensteuer|Verluste?\s+aus\s+\w+)", re.I)

_ZAHL_IM_TEXT = re.compile(r"[-−–(+]?\d[\d.,]*\)?-?")
_ZAHL_GRUPPE = "({NUM})"
# "Steuerbericht 2024" ist keine Summenzeile. Blanke Jahreszahlen fliegen raus.
_JAHRESZAHL = re.compile(r"^(?:19|20)\d{2}$")

# KAP-Kennzahlen: das normierte Gegenstück zur Rohabschrift in `kap_zeilen`.
_KAP_KENNZAHLEN = [
    (r"anrechenbare\s+Kapitalertragsteuer|Kapitalertragsteuer|\bKESt\b",
     "kennzahlen.anrechenbare_kest"),
    (r"Solidarit(?:ä|ae)tszuschlag|\bSolZ\b", "kennzahlen.einbehaltener_soli"),
    (r"Kirchensteuer", "kennzahlen.einbehaltene_kirchensteuer"),
    (r"Quellensteuer", "kennzahlen.auslaendische_quellensteuer"),
    (r"H(?:ö|oe)he\s+der\s+Kapitalertr(?:ä|ae)ge|Kapitalertr(?:ä|ae)ge",
     "kennzahlen.kapitalertraege"),
]


def _liste_pfade(w: dict) -> list[str]:
    p = w.get("pfad")
    return list(p) if isinstance(p, (list, tuple)) else [p]


_VERGLEICH_HINWEISE = [
    # Achtung: die Spaltensumme umfasst ALLE Veräußerungen, `paragraph_23` nur die
    # steuerpflichtigen. Solange langfristige Positionen möglich sind, ist
    # summen_basis.veraeusserungen_gewinn_gesamt der richtige Gegenwert.
    (r"kapitalgewinn|capital\s+gain|ver(?:ä|ae)u(?:ß|ss)erungsgewinn|netto|"
     r"gewinn/verlust|gesamtgewinn", {
         "krypto_vorberechnet": "summen_basis.veraeusserungen_gewinn_gesamt",
         "krypto_transaktionen": "paragraph_23.netto_ergebnis_eur"}),
    (r"einnahmen|income|sonstige\s+leistung|reward|staking|lending", {
        "krypto_vorberechnet": "paragraph_22_nr3.summe_eur",
        "krypto_transaktionen": "paragraph_22_nr3.summe_eur"}),
    (r"kapitalertragsteuer|kest", {"kap": "kennzahlen.anrechenbare_kest"}),
    (r"solidarit(?:ä|ae)tszuschlag|soli\b", {"kap": "kennzahlen.einbehaltener_soli"}),
    (r"kirchensteuer", {"kap": "kennzahlen.einbehaltene_kirchensteuer"}),
    (r"quellensteuer", {"kap": "kennzahlen.auslaendische_quellensteuer"}),
    (r"kapitalertr(?:ä|ae)ge", {"kap": "kennzahlen.kapitalertraege"}),
]


def _vergleichspfad(label: str, ergebnis: str) -> str:
    m = re.search(r"Zeile\s+(\d{1,2})", label, re.I)
    if m and ergebnis == "kap":
        return f"kap_zeilen.{m.group(1)}"
    for muster, ziele in _VERGLEICH_HINWEISE:
        if re.search(muster, label, re.I):
            ziel = ziele.get(ergebnis)
            if ziel:
                return ziel
    return TODO


def schlage_summen_vor(struktur: list[dict], tabellen_bereiche: list[tuple[int, int]],
                       ergebnis: str, notation: str) -> list[dict]:
    """Zeilen, die wie ausgewiesene Gesamtwerte aussehen. Zeilen innerhalb einer
    erkannten Tabelle scheiden aus — sonst wird eine Datenzeile zur 'Summe'."""
    hint = notation if notation in ("de", "en") else None
    in_tabelle = set()
    for a, b in tabellen_bereiche:
        in_tabelle.update(range(a, b + 1))

    kandidaten: list[dict] = []
    gesehen: set[str] = set()
    for s in struktur:
        if s["i"] in in_tabelle:
            continue
        txt = s["text"].strip()
        if not txt or not _SUMMEN_WORT.search(txt):
            continue
        zahlen = list(_ZAHL_IM_TEXT.finditer(txt))
        zahlen = [z for z in zahlen if not _DT_ZELLE.match(z.group(0))]
        if not zahlen:
            continue
        letzte = zahlen[-1]
        if _JAHRESZAHL.match(letzte.group(0).strip()):
            continue
        label = txt[:letzte.start()].strip(" .:…-")
        if not label or not re.search(r"[A-Za-zÄÖÜäöüß]{3,}", label):
            continue
        worte = label.split()
        if len(label) > 60 and len(worte) > 6:
            label = " ".join(worte[-6:])
        try:
            wert = sl.to_decimal(letzte.group(0), locale_hint=hint)
        except sl.ParseError:
            continue
        schluessel = pp._norm(label)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        muster = re.escape(label).replace("\\ ", r"\s+") + "{VOR}" + _ZAHL_GRUPPE
        kandidaten.append({
            "label": label,
            "muster": muster,
            "vergleich": _vergleichspfad(label, ergebnis),
            "toleranz": "0.01",
            "_wert": str(wert),
            "_zeile": s["i"],
        })
    # Starke Schlüsselwörter und späte Zeilen zuerst — dort steht die echte Endsumme.
    def _rang(k):
        stark = bool(re.search(r"kapitalgewinn|capital\s+gain|gesamt|summe|netto|total",
                               k["label"], re.I))
        return (0 if stark else 1, -k["_zeile"])
    kandidaten.sort(key=_rang)
    return kandidaten[:8]


# ─────────────────────────────────────────────────────────────────────────────
# 7. Selbstprüfung
# ─────────────────────────────────────────────────────────────────────────────

def _bereich(struktur: list[dict], start: str, ende: str) -> tuple[int, int]:
    a, b = 0, len(struktur) - 1
    if start != TODO:
        for s in struktur:
            if re.search(entfalte(start), s["text"]):
                a = s["i"] + 1
                break
    if ende != TODO:
        for s in struktur[a:]:
            if re.search(entfalte(ende), s["text"]):
                b = s["i"] - 1
                break
    return a, max(a, b)


def pruefe_entwurf(struktur: list[dict], tabellen: list[dict], summen: list[dict],
                   notation: str, werte: list[dict] | None = None) -> dict:
    """Wendet den Entwurf auf den Report an, aus dem er stammt.

    Meldet: getroffene Zeilen, Zeilen ohne Treffer im Tabellenbereich und ob eine
    Spaltensumme mit einer ausgewiesenen Summe zusammenpasst.
    """
    hint = notation if notation in ("de", "en") else None
    bericht = {"tabellen": [], "summen": [], "abgleich_ok": False, "warnungen": []}

    alle_summen: dict[str, dict] = {}

    for t in tabellen:
        p = t["profil"]
        eintrag = {"name": p["name"], "gematcht": 0, "ohne_treffer": 0,
                   "beispiele": [], "spaltensummen": {}}
        if p["zeile"] == TODO:
            eintrag["fehler"] = "keine Zeilenregex vorgeschlagen"
            bericht["tabellen"].append(eintrag)
            continue
        rx = re.compile(entfalte(p["zeile"]))
        a, b = _bereich(struktur, p["start"], p["ende"])
        eintrag["bereich"] = [a + 1, b + 1]
        summen_je_gruppe: dict[str, Decimal] = {}
        unlesbar: set[str] = set()
        for s in struktur[a:b + 1]:
            txt = s["text"]
            if not txt.strip():
                continue
            m = rx.match(txt.strip()) or rx.match(txt)
            if not m:
                eintrag["ohne_treffer"] += 1
                if len(eintrag["beispiele"]) < 3:
                    eintrag["beispiele"].append(txt.strip()[:100])
                continue
            eintrag["gematcht"] += 1
            for gname, roh in (m.groupdict() or {}).items():
                if roh is None or _tokenklasse(roh) != "NUM":
                    continue
                try:
                    wert = sl.to_decimal(roh, locale_hint=hint)
                except sl.ParseError:
                    unlesbar.add(gname)
                    continue
                summen_je_gruppe[gname] = summen_je_gruppe.get(gname, D("0")) + wert
        eintrag["spaltensummen"] = {k: str(sl.q2(v)) for k, v in summen_je_gruppe.items()}
        if unlesbar:
            eintrag["unlesbare_spalten"] = sorted(unlesbar)
        for k, v in summen_je_gruppe.items():
            alle_summen[f"{p['name']}.{k}"] = {"wert": sl.q2(v), "tabelle": p["name"],
                                               "spalte": k}
        bericht["tabellen"].append(eintrag)

    wert_pfade = {p for w in (werte or []) for p in _liste_pfade(w)}
    zirkulaer = False
    for s in summen:
        wert = sl.to_decimal(s["_wert"])
        treffer = [k for k, v in alle_summen.items() if abs(v["wert"] - wert) <= D("0.01")]
        if treffer:
            abgleich = "ok"
        elif s["vergleich"] in wert_pfade:
            # Werte-Profile (KAP) haben keine Spaltensumme: der Abgleich prüft den
            # Wert gegen die Zeile, aus der er stammt. Das hält das Muster stabil,
            # ersetzt aber keinen echten Summenabgleich — und wird so benannt.
            abgleich = "ok (Einzelwert, zirkulär)"
            zirkulaer = True
        else:
            abgleich = "keine Spalte passt"
        eintrag = {"label": s["label"], "wert": s["_wert"], "abgleich": abgleich,
                   "spalten": treffer}
        if abgleich.startswith("ok"):
            bericht["abgleich_ok"] = True
        bericht["summen"].append(eintrag)
    if zirkulaer:
        bericht["warnungen"].append(
            "Der Abgleich prüft Einzelwerte gegen dieselbe Zeile, aus der sie stammen. "
            "Er meldet ein geändertes Layout, aber keinen Zeilenverlust — wenn der "
            "Report eine echte Gesamtsumme ausweist, diese zusätzlich eintragen.")

    if not summen:
        bericht["warnungen"].append(
            "Keine Summenzeile gefunden — ohne Summenabgleich gilt das Profil als "
            "unfertig. Ausgewiesene Gesamtwerte von Hand als `summen` eintragen.")
    elif not bericht["abgleich_ok"]:
        bericht["warnungen"].append(
            "Keine geparste Spaltensumme stimmt mit einer ausgewiesenen Summe überein — "
            "Spaltenzuordnung und Tabellenbereich stimmen vermutlich noch nicht.")
    for t in bericht["tabellen"]:
        if t.get("gematcht", 0) == 0:
            bericht["warnungen"].append(
                f"Tabelle {t['name']!r}: die vorgeschlagene Zeilenregex trifft keine "
                f"einzige Zeile.")
        elif t.get("ohne_treffer", 0):
            bericht["warnungen"].append(
                f"Tabelle {t['name']!r}: {t['ohne_treffer']} Zeile(n) im Bereich ohne "
                f"Treffer — Beispiele im Bericht prüfen.")
    return bericht


def pruefe_mit_brokerprofile(profil: dict, text: str) -> list[str]:
    """Zusatzprüfung durch `brokerprofile.py`, wenn es schon existiert."""
    bp = _lade_brokerprofile()
    if bp is None:
        return ["brokerprofile.py noch nicht verfügbar — Zusatzprüfung übersprungen."]
    meldungen: list[str] = []
    if hasattr(bp, "pruefe_profil"):
        try:
            for m in bp.pruefe_profil(profil) or []:
                meldungen.append(f"pruefe_profil: {m}")
        except Exception as e:
            meldungen.append(f"pruefe_profil fehlgeschlagen: {e}")
    # `erkenne`/`wende_an` erwarten geladene Profile, keine rohen dicts.
    objekt = profil
    if hasattr(bp, "Profil"):
        try:
            objekt = bp.Profil(profil)
        except Exception as e:
            meldungen.append(f"Profil-Objekt nicht baubar: {e}")
            return meldungen
    if hasattr(bp, "erkenne"):
        try:
            got = bp.erkenne(text, profile=[objekt])
            meldungen.append("erkenne: Profil greift auf dem Report."
                             if got else "erkenne: Profil greift NICHT — `erkennung` prüfen.")
        except Exception as e:
            meldungen.append(f"erkenne fehlgeschlagen: {e}")
    if hasattr(bp, "wende_an") and not finde_todos(profil):
        try:
            bp.wende_an(objekt, text, quelle="profile_wizard")
            meldungen.append("wende_an: läuft ohne Fehler durch.")
        except Exception as e:
            meldungen.append(f"wende_an fehlgeschlagen: {e}")
    return meldungen


# ─────────────────────────────────────────────────────────────────────────────
# 8. TODOs finden
# ─────────────────────────────────────────────────────────────────────────────

def finde_todos(obj, pfad: str = "") -> list[str]:
    """Alle Stellen, an denen der Entwurf noch eine Entscheidung braucht."""
    aus: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "kommentare":
                continue
            p = f"{pfad}.{k}" if pfad else str(k)
            if isinstance(k, str) and k.startswith(TODO):
                aus.append(p)
            aus.extend(finde_todos(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            aus.extend(finde_todos(v, f"{pfad}[{i}]"))
    elif isinstance(obj, str) and obj == TODO:
        aus.append(pfad)
    return aus


# ─────────────────────────────────────────────────────────────────────────────
# 9. Fixture
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_zahl(wert: Decimal, notation: str) -> str:
    s = f"{abs(sl.q2(wert)):,.2f}"
    if notation != "en":
        s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return ("-" if wert < 0 else "") + s


def _ersetze_letzte_zahl(zeile: str, neu: str) -> str:
    treffer = [z for z in _ZAHL_IM_TEXT.finditer(zeile) if not _DT_ZELLE.match(z.group(0))]
    if not treffer:
        return zeile
    m = treffer[-1]
    return zeile[:m.start()] + neu + zeile[m.end():]


def baue_fixture(struktur: list[dict], tabellen: list[dict], summen: list[dict],
                 marken: list[str], bericht: dict, notation: str,
                 max_zeilen: int = 3,
                 csv_kopf: int | None = None) -> tuple[str, list[dict], list[str]]:
    """Kopfzeile + 2–3 Datenzeilen + Summenzeile, anonymisiert.

    Wird die Datenmenge gekürzt, wird die Summenzeile passend nachgerechnet — sonst
    scheitert der Abgleich, den das Fixture gerade beweisen soll.
    """
    hint = notation if notation in ("de", "en") else None
    indizes: set[int] = set()
    hinweise: list[str] = []

    # Kontext für die Erkennung (Markenzeile).
    for s in struktur[:25]:
        if any(m.lower() in s["text"].lower() for m in marken):
            indizes.add(s["i"])

    ersetzungen: dict[int, str] = {}
    for t in tabellen:
        meta = t["meta"]
        if meta.get("kopf_index") is not None:
            indizes.add(meta["kopf_index"])
        p = t["profil"]
        rx = None
        if p["zeile"] != TODO:
            try:
                rx = re.compile(entfalte(p["zeile"]))
            except re.error:
                rx = None
        genommen = []
        for s in meta["block"]["zeilen"]:
            if rx is None or rx.match(s["text"].strip()) or rx.match(s["text"]):
                genommen.append(s)
            if len(genommen) >= max_zeilen:
                break
        for s in genommen:
            indizes.add(s["i"])

        # Summenzeile an die übernommenen Zeilen anpassen.
        gesamt = len(meta["block"]["zeilen"])
        if rx is not None and genommen and len(genommen) < gesamt:
            teil: dict[str, Decimal] = {}
            for s in genommen:
                m = rx.match(s["text"].strip()) or rx.match(s["text"])
                if not m:
                    continue
                for g, roh in (m.groupdict() or {}).items():
                    if roh is None or _tokenklasse(roh) != "NUM":
                        continue
                    try:
                        teil[g] = teil.get(g, D("0")) + sl.to_decimal(roh, locale_hint=hint)
                    except sl.ParseError:
                        pass
            for b_summe in bericht.get("summen", []):
                for voll in b_summe.get("spalten", []):
                    tab, _, spalte = voll.partition(".")
                    if tab != p["name"] or spalte not in teil:
                        continue
                    quelle = next((x for x in summen if x["label"] == b_summe["label"]), None)
                    if quelle is None:
                        continue
                    neu = _fmt_zahl(teil[spalte], notation)
                    ersetzungen[quelle["_zeile"]] = _ersetze_letzte_zahl(
                        struktur[quelle["_zeile"]]["text"], neu)
                    hinweise.append(
                        f"Summenzeile {quelle['label']!r} auf {neu} nachgerechnet "
                        f"({len(genommen)} von {gesamt} Zeilen im Fixture) — "
                        f"so bleibt der Abgleich im Fixture gültig.")

    # CSV: Kopfzeile plus die ersten Datenzeilen — hier gibt es keine `tabellen`.
    if csv_kopf is not None:
        indizes.add(csv_kopf)
        genommen = 0
        for s in struktur[csv_kopf + 1:]:
            if not s["text"].strip():
                continue
            indizes.add(s["i"])
            genommen += 1
            if genommen >= max_zeilen:
                break

    for s in summen[:3]:
        indizes.add(s["_zeile"])
        # Abschnittsüberschrift direkt darüber mitnehmen (oft der `ende`-Anker).
        for j in range(s["_zeile"] - 1, max(-1, s["_zeile"] - 3), -1):
            if j >= 0 and struktur[j]["text"].strip() and _ENDE_WORTE.match(
                    struktur[j]["text"].strip()):
                indizes.add(j)

    if not indizes:
        indizes = {s["i"] for s in struktur[:15] if s["text"].strip()}
        hinweise.append("Keine Tabelle erkannt — Fixture enthält nur die ersten Zeilen "
                        "des Reports und muss von Hand ergänzt werden.")

    geordnet = sorted(indizes)
    zeilen: list[str] = []
    vorher = None
    for i in geordnet:
        if vorher is not None and i - vorher > 1:
            zeilen.append("")
        zeilen.append(ersetzungen.get(i, struktur[i]["text"]).rstrip())
        vorher = i

    roh = "\n".join(zeilen) + "\n"
    text, redaktionen = anonymisiere(roh, schuetze=tuple(marken))
    return text, redaktionen, hinweise


# ─────────────────────────────────────────────────────────────────────────────
# 10. Entwurf zusammensetzen
# ─────────────────────────────────────────────────────────────────────────────

def entwurf_aus_text(text: str, profil_id: str, *, eingabe: str = "pdf",
                     kind: str = "auto", quelle_datei: str = "",
                     fixture_pfad: str | None = None,
                     max_fixture_zeilen: int = 3) -> dict:
    """Text -> {profil, bericht, fixture}. Der ganze Wizard ohne Datei-IO.

    Bewusst als reine Funktion: so lässt sich der Vorschlag gegen synthetischen
    Reporttext testen, ohne dass ein echtes PDF nötig wäre.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    zeilen = text.split("\n")
    trenner = csv_trenner(text) if eingabe == "csv" else None
    struktur = _struktur(zeilen, trenner)

    kommentare: dict[str, str] = {}
    warnungen: list[str] = []

    # -- Erkennung
    erkennung, marken, erk_hinweise = schlage_erkennung_vor(text, trenner)
    if erkennung["muss"] == [TODO]:
        kommentare["erkennung.muss"] = (
            "Zwei Textstellen eintragen, die in JEDEM Report dieses Typs stehen "
            "(Marke, Dokumenttitel) — kein Datum, keine Konto-/Kundennummer, kein Name.")
    else:
        kommentare["erkennung.darf_nicht"] = (
            "Leer lassen, außer es gibt eine zweite Fassung (z. B. englische), die sonst "
            "mitmatcht — dann deren Titel hier eintragen. "
            + ("; ".join(erk_hinweise) if erk_hinweise else ""))

    # -- Ergebnisart
    if kind and kind != "auto":
        ergebnis, kind_punkte = kind, {}
    else:
        ergebnis, kind_punkte = rate_ergebnisart(text)
        if ergebnis == TODO:
            kommentare["ergebnis"] = (
                "Ausgabeschema nicht eindeutig erkannt (Signale: "
                + ", ".join(f"{k}={v}" for k, v in sorted(kind_punkte.items()))
                + "). Eines von " + " | ".join(ERGEBNIS_ARTEN) + " wählen.")
    schema = ergebnis if ergebnis in ERGEBNIS_ARTEN else "krypto_vorberechnet"

    # -- Notation und Datum
    notation, notiz_notation = rate_notation(text)
    if notation == "auto":
        kommentare["notation"] = notiz_notation
    datum, notiz_datum = rate_datumsformat(text)
    if datum == TODO:
        kommentare["datum"] = notiz_datum + " Danach 'de', 'en' oder 'iso' eintragen."

    # -- CSV: der Motor liest CSVs über `csv.spalten`, nicht über Zeilenregexe.
    csv_block: dict | None = None
    if eingabe == "csv":
        if ergebnis != "krypto_transaktionen":
            kommentare["ergebnis"] = (
                f"CSV-Eingaben liest der Motor als Transaktionsliste; erkannt war "
                f"{ergebnis!r}. Auf 'krypto_transaktionen' gesetzt — falls die CSV bereits "
                f"fertige Gewinne enthält, ist eine `tabellen`-Beschreibung nötig.")
            ergebnis = schema = "krypto_transaktionen"
        csv_block, csv_meta = schlage_csv_vor(struktur, trenner)
        if csv_meta["kopfzeile"] is None:
            kommentare["csv.spalten"] = (
                "Keine Kopfzeile mit erkennbaren Spalten gefunden — Trennzeichen und "
                "Kodierung prüfen und die Zuordnung kanonisches Feld -> Spaltenname "
                "von Hand eintragen.")
        else:
            kommentare["csv.spalten"] = f"erkannte Kopfzeile: {csv_meta['kopfzeile']!r}"
            if csv_meta["offen"]:
                kommentare["csv.spalten"] += (
                    " | nicht zugeordnete Spalten: " + ", ".join(csv_meta["offen"])
                    + (" | schwache Treffer (bewusst NICHT übernommen): "
                       + "; ".join(csv_meta["schwach"]) if csv_meta["schwach"] else ""))
        if csv_meta["fehlende_pflichtfelder"]:
            kommentare["csv.pflicht"] = (
                "Pflichtfeld(er) " + ", ".join(csv_meta["fehlende_pflichtfelder"])
                + " nicht zugeordnet — ohne sie wird eine leere Zeile zur Transaktion.")
        if csv_meta["unbekannte_typen"] or TODO in csv_block["typ_werte"]:
            kommentare["csv.typ_werte"] = (
                "Rohwerte der Typspalte auf buy|sell|swap|reward|deposit|withdrawal "
                "abbilden. Offen: "
                + (", ".join(repr(v) for v in csv_meta["unbekannte_typen"]) or "alle"))

    # -- Tabellen
    bloecke = [] if csv_block else finde_bloecke(struktur)[:2]
    tabellen: list[dict] = []
    for n, block in enumerate(bloecke):
        name = "veraeusserungen" if schema == "krypto_vorberechnet" and n == 0 else (
            "transaktionen" if schema == "krypto_transaktionen" and n == 0 else f"tabelle{n + 1}")
        vorschlag = schlage_tabelle_vor(struktur, block, schema, trenner, name)
        # Keine einzige zuzuordnende Spalte: das ist eine Label-Wert-Liste oder ein
        # Fließtextblock, keine Tabelle. Ein Gerüst aus lauter TODO-Spalten wäre
        # schlechter als der ehrliche Hinweis.
        if not [k for k in vorschlag["profil"]["felder"] if not k.startswith(TODO)]:
            kommentare[f"struktur{n + 1}"] = (
                f"Wiederkehrende Zeilenstruktur ab Zeile {block['start'] + 1} gefunden, "
                f"aber keine Spalte zuzuordnen — als Tabelle NICHT übernommen. "
                f"Falls es doch eine ist, hier ansetzen: "
                f"zeile={vorschlag['profil']['zeile']!r}")
            continue
        tabellen.append(vorschlag)

    keine_tabelle = not tabellen and not csv_block

    for t in tabellen:
        p, meta = t["profil"], t["meta"]
        basis = f"tabellen[{tabellen.index(t)}]"
        if p["start"] == TODO:
            kommentare[f"{basis}.start"] = (
                "Keine Kopfzeile über der Tabelle erkannt — Anker eintragen, ab dem die "
                "Datenzeilen beginnen.")
        if p["ende"] == TODO:
            kommentare[f"{basis}.ende"] = (
                "Kein Abschnittsende erkannt — Anker eintragen, an dem die Tabelle endet "
                "(z. B. 'Zusammenfassung'), sonst läuft der Parser bis zum Dateiende.")
        if meta["offen"]:
            kommentare[f"{basis}.felder"] = (
                "Nicht zugeordnete Spalten: " + ", ".join(meta["offen"])
                + ". TODO-Schlüssel durch den kanonischen Feldnamen ersetzen oder die "
                  "Gruppe ganz entfernen."
                + (" Schwache Treffer (bewusst NICHT übernommen): "
                   + "; ".join(meta["schwach"]) if meta["schwach"] else ""))
        if meta["fehlende_pflichtfelder"]:
            kommentare[f"{basis}.pflicht"] = (
                "Pflichtfeld(er) " + ", ".join(meta["fehlende_pflichtfelder"])
                + " nicht zugeordnet — ohne sie kann das Ergebnis nicht gebaut werden.")
        if meta["kopfzeile"]:
            kommentare[f"{basis}.zeile"] = f"erkannte Kopfzeile: {meta['kopfzeile']!r}"
        if meta.get("braucht_haltedauer"):
            kommentare[f"{basis}.langfristig"] = (
                "Ohne Anschaffungsdatum entscheidet die Haltedauer-Spalte über die "
                "Jahresfrist. Muster eintragen, das NUR die langfristigen Zeilen trifft "
                "(z. B. 'Langfristig|Long[- ]?term') — ein zu weites Muster macht jede "
                "Position steuerfrei.")
        elif meta.get("hat_haltedauer_spalte"):
            kommentare[f"{basis}.felder"] = (
                kommentare.get(f"{basis}.felder", "")
                + " Haltedauer-Spalte erkannt; die Frist wird aus den beiden Daten "
                  "gerechnet (§ 108 AO). Soll stattdessen die Spalte gelten, "
                  "`langfristig: {feld, muster}` ergänzen.").strip()

    # -- werte (nur KAP: 'Zeile NN ... Betrag' und die Kennzahlen)
    werte: list[dict] = []
    if schema == "kap":
        gesehen_kennzahl: set[str] = set()
        for s in struktur:
            txt = s["text"].strip()
            zahlen = [z for z in _ZAHL_IM_TEXT.finditer(txt)
                      if not _DT_ZELLE.match(z.group(0))
                      and not _JAHRESZAHL.match(z.group(0).strip())]
            if not zahlen:
                continue
            m = re.search(r"Zeile\s+(\d{1,2})", txt, re.I)
            kennzahl = next((z for muster, z in _KAP_KENNZAHLEN
                             if re.search(muster, txt, re.I)
                             and z not in gesehen_kennzahl), None)
            if not m and not kennzahl:
                continue
            pfade = []
            if m:
                pfade.append(f"kap_zeilen.{m.group(1)}")
            if kennzahl:
                pfade.append(kennzahl)
                gesehen_kennzahl.add(kennzahl)
            if any(set(_liste_pfade(w)) & set(pfade) for w in werte):
                continue
            if m:
                muster = rf"Zeile\s+{m.group(1)}\)?{{VOR}}{_ZAHL_GRUPPE}"
            else:
                label = txt[:zahlen[-1].start()].strip(" .:…-")
                muster = re.escape(label).replace("\\ ", r"\s+") + "{VOR}" + _ZAHL_GRUPPE
            werte.append({"pfad": pfade[0] if len(pfade) == 1 else pfade,
                          "muster": muster})
            if re.search(r"Verlust", txt, re.I):
                kommentare[f"werte[{len(werte) - 1}]"] = (
                    "Verlustzeile: im Ergebnisschema tragen Verluste ein NEGATIVES "
                    "Vorzeichen. Der Report weist sie hier positiv aus — Muster so "
                    "schärfen, dass das Vorzeichen mitkommt, sonst wird aus einem "
                    "Verlust ein Gewinn.")
        if werte and not tabellen:
            # Ohne Mindestzahl wäre ein Ergebnis aus lauter Nullen von einem echten
            # Null-Report nicht zu unterscheiden — der Motor verlangt sie deshalb.
            profil_werte_regeln = {"mindestens": len(werte)}
            kommentare["werte_regeln.mindestens"] = (
                f"{len(werte)} Einzelwerte wurden im Report gefunden. Zahl auf das "
                f"Minimum senken, das ein gültiger Report immer enthält.")
        else:
            profil_werte_regeln = None
    else:
        profil_werte_regeln = None

    if keine_tabelle and not werte:
        warnungen.append("Keine wiederkehrende Zeilenstruktur gefunden — entweder ist der "
                         "Report kein Tabellendokument (dann `werte` statt `tabellen` "
                         "nutzen) oder die Textextraktion hat die Spalten zerlegt.")
        kommentare["tabellen"] = ("Keine Tabelle erkannt. Entweder `tabellen` von Hand "
                                  "beschreiben oder die Werte einzeln über `werte` lesen.")

    # -- Summen
    bereiche = [(t["meta"]["block"]["start"], t["meta"]["block"]["ende"]) for t in tabellen]
    summen = schlage_summen_vor(struktur, bereiche, schema, notation)

    # -- Selbstprüfung
    bericht = pruefe_entwurf(struktur, tabellen, summen, notation, werte)
    bericht["warnungen"] = warnungen + bericht["warnungen"]
    bericht["ergebnis"] = ergebnis
    bericht["notation"] = notation
    bericht["datum"] = datum
    bericht["kind_punkte"] = kind_punkte
    bericht["marken"] = marken

    for i, tb in enumerate(bericht["tabellen"]):
        if tb.get("ohne_treffer"):
            beispiele = "; ".join(repr(b) for b in tb.get("beispiele", [])[:2])
            kommentare[f"tabellen[{i}].zeile"] = (
                (kommentare.get(f"tabellen[{i}].zeile", "") + " | ").lstrip(" |")
                + f"{tb['ohne_treffer']} Zeile(n) im Bereich ohne Treffer: {beispiele}. "
                  f"Seitenköpfe/-füße gehören in `ignoriere`; echte Datenzeilen in die "
                  f"Regex. Die Regex NICHT aufweichen, bis alles matcht — ein zu "
                  f"tolerantes Muster liest falsche Spalten und meldet Erfolg.")

    # Was sich nicht abgleichen lässt, bleibt TODO statt fertig auszusehen.
    summen_profil = []
    for s in summen:
        b = next((x for x in bericht["summen"] if x["label"] == s["label"]), None)
        eintrag = {"label": s["label"], "muster": s["muster"],
                   "vergleich": s["vergleich"], "toleranz": s["toleranz"]}
        idx = len(summen_profil)
        if b and b["abgleich"].startswith("ok (Einzelwert"):
            kommentare[f"summen[{idx}].vergleich"] = (
                f"Ausgewiesener Wert {s['_wert']} wird gegen denselben Wert geprüft, den "
                f"`werte` aus dieser Zeile liest — das hält das Muster stabil, ersetzt "
                f"aber keinen Summenabgleich über mehrere Zeilen.")
        elif b and b["abgleich"] != "ok":
            eintrag["vergleich"] = TODO
            kommentare[f"summen[{idx}].vergleich"] = (
                f"Ausgewiesener Wert {s['_wert']} — keine geparste Spaltensumme passt dazu. "
                f"Erst Spaltenzuordnung/Tabellenbereich klären, dann den Zielpfad eintragen.")
        elif eintrag["vergleich"] == TODO:
            kommentare[f"summen[{idx}].vergleich"] = (
                f"Ausgewiesener Wert {s['_wert']} passt zur Spaltensumme "
                f"{', '.join(b['spalten']) if b else '?'} — Zielpfad im Ergebnisschema "
                f"eintragen (z. B. paragraph_23.netto_ergebnis_eur).")
        elif eintrag["vergleich"] == "summen_basis.veraeusserungen_gewinn_gesamt":
            kommentare[f"summen[{idx}].vergleich"] = (
                f"Ausgewiesener Wert {s['_wert']} = Summe ALLER Zeilen "
                f"({', '.join(b['spalten']) if b else '?'}). Weist der Report nur die "
                f"steuerpflichtigen Gewinne aus, stattdessen "
                f"'paragraph_23.netto_ergebnis_eur' eintragen — die beiden "
                f"unterscheiden sich um die langfristigen Positionen.")
        summen_profil.append(eintrag)

    if not summen_profil:
        summen_profil = [{"label": TODO, "muster": TODO, "vergleich": TODO,
                          "toleranz": "0.01"}]
        kommentare["summen[0]"] = (
            "Keine Summenzeile gefunden. Ein Profil ohne funktionierenden Summenabgleich "
            "gilt als unfertig — ausgewiesenen Gesamtwert im Report suchen und eintragen.")

    marke = marken[0] if marken else TODO
    profil = {
        "id": profil_id,
        "label": (f"{marke} (Entwurf — bitte prüfen)" if marke != TODO else TODO),
        "quelle": marke,
        "eingabe": eingabe if eingabe in ("pdf", "csv") else "pdf",
        "ergebnis": ergebnis,
        "erkennung": erkennung,
        "notation": notation,
        "datum": datum,
        "tabellen": [t["profil"] for t in tabellen],
        "werte": werte,
        "summen": summen_profil,
        "elster": [],
        # Ein Entwurf ist per Definition ungeprüft — 'geprueft' verlangt ein Datum,
        # an dem das Profil tatsächlich gegen einen echten Report gelaufen ist.
        "status": "ungeprueft",
        "geprueft_am": TODO,
        "fixture": fixture_pfad or f"tests/fixtures/{profil_id}.txt",
        "kommentare": kommentare,
    }
    if csv_block is not None:
        profil["csv"] = csv_block
    if profil_werte_regeln is not None:
        profil["werte_regeln"] = profil_werte_regeln
    if marke == TODO:
        kommentare["quelle"] = "Anzeigename der Quelle eintragen (erscheint im Ergebnis)."
    kommentare["geprueft_am"] = ("Erst setzen (JJJJ-MM-TT) und `status` auf 'geprueft', "
                                 "wenn das Profil gegen den echten Report gelaufen ist.")

    bericht["todos"] = finde_todos(profil)
    bericht["brokerprofile"] = pruefe_mit_brokerprofile(profil, text)

    fixture_text, redaktionen, fixture_hinweise = baue_fixture(
        struktur, tabellen, summen, marken, bericht, notation, max_fixture_zeilen,
        csv_kopf=(csv_meta.get("kopf_index") if csv_block is not None else None))

    return {
        "profil": profil,
        "bericht": bericht,
        "fixture": {"text": fixture_text, "redaktionen": redaktionen,
                    "hinweise": fixture_hinweise, "quelle": quelle_datei},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 11. CLI
# ─────────────────────────────────────────────────────────────────────────────

def _drucke_bericht(erg: dict, quelle: str, out_pfad: Path, fix_pfad: Path) -> None:
    profil, bericht, fixture = erg["profil"], erg["bericht"], erg["fixture"]
    print(f"\nProfil-Entwurf '{profil['id']}' aus {quelle}")
    print(f"  Erkennung  : {', '.join(profil['erkennung']['muss'])}")
    print(f"  Ergebnis   : {profil['ergebnis']}"
          + ("" if profil["ergebnis"] != TODO else "   (nicht eindeutig — siehe Kommentar)"))
    print(f"  Notation   : {profil['notation']}   Datum: {profil['datum']}")

    print("\nTabellen:")
    if not bericht["tabellen"]:
        print("  keine erkannt.")
    for t in bericht["tabellen"]:
        print(f"  {t['name']}: {t.get('gematcht', 0)} Zeile(n) getroffen, "
              f"{t.get('ohne_treffer', 0)} ohne Treffer"
              + (f", Bereich Zeile {t['bereich'][0]}–{t['bereich'][1]}"
                 if t.get("bereich") else ""))
        for b in t.get("beispiele", []):
            print(f"      ohne Treffer: {b}")
        if t.get("spaltensummen"):
            print("      Spaltensummen: "
                  + ", ".join(f"{k}={v}" for k, v in t["spaltensummen"].items()))
    for p in profil["tabellen"]:
        print(f"  Felder {p['name']}: "
              + ", ".join(f"{k} <- {v}" for k, v in p["felder"].items()))
    if profil.get("csv"):
        print("  CSV-Spalten: "
              + ", ".join(f"{k} <- {v!r}" for k, v in profil["csv"]["spalten"].items()))
        print("  CSV-Typen  : "
              + ", ".join(f"{k!r} -> {v}" for k, v in profil["csv"]["typ_werte"].items()))
    if profil.get("werte"):
        print("  Einzelwerte: " + ", ".join(
            str(w["pfad"]) for w in profil["werte"]))

    print("\nSummen:")
    if not bericht["summen"]:
        print("  keine gefunden — das ist der wichtigste offene Punkt.")
    for s in bericht["summen"]:
        zusatz = (" (" + ", ".join(s["spalten"]) + ")") if s["spalten"] else ""
        print(f"  {s['label']!r} = {s['wert']}  ->  {s['abgleich']}{zusatz}")

    print("\nSelbstprüfung: " + ("Abgleich gefunden."
                                 if bericht["abgleich_ok"]
                                 else "KEIN belastbarer Summenabgleich."))
    for w in bericht["warnungen"]:
        print(f"  ! {w}")
    for m in bericht["brokerprofile"]:
        print(f"  brokerprofile: {m}")

    print(f"\nFixture: {fix_pfad}")
    if fixture["redaktionen"]:
        print(f"  {len(fixture['redaktionen'])} Redaktion(en) — bitte einzeln nachsehen:")
        for r in fixture["redaktionen"]:
            print(f"    Zeile {r['zeile']}: {r['original']!r} -> {r['ersatz']}  [{r['art']}]")
    else:
        print("  KEINE Redaktion nötig gewesen — das ist ungewöhnlich. Fixture vor dem "
              "Commit selbst durchlesen: der Wizard erkennt nicht jede Personenangabe.")
    for h in fixture["hinweise"]:
        print(f"  {h}")
    print("  ACHTUNG: Fixtures landen im Repository. Vor dem Commit lesen. Beträge dürfen "
          "verfälscht werden, müssen aber zur Summenzeile passen — sonst schlägt der "
          "Abgleich des Profils fehl.")

    todos = bericht["todos"]
    print(f"\nOffene TODOs ({len(todos)}):")
    for p in todos:
        k = profil["kommentare"].get(p) or profil["kommentare"].get(p.rsplit(".", 1)[0])
        print(f"  - {p}" + (f"\n      {k}" if k else ""))

    print("\nWas als Nächstes zu tun ist:")
    print(f"  1. {out_pfad} öffnen und jedes TODO ersetzen ({len(todos)} Stück).")
    if profil.get("csv"):
        print("  2. `csv.spalten` gegen die echte Kopfzeile prüfen und `csv.typ_werte` "
              "vervollständigen — ein nicht zugeordneter Typ verwirft die Zeile.")
    else:
        print("  2. `zeile`-Regex gegen weitere Seiten prüfen: jede Zeile ohne Treffer "
              "im Tabellenbereich ist eine potenziell verlorene Position.")
    print("  3. Mindestens einen `summen`-Eintrag zum Greifen bringen — ein Profil ohne "
          "Summenabgleich wird beim Laden als unfertig markiert.")
    print(f"  4. {fix_pfad} durchlesen, Redaktionen prüfen, Beträge ggf. konsistent "
          "verfälschen.")
    print("  5. `python scripts/parse_broker.py <report> -o test.json` laufen lassen.")
    print("  6. `geprueft_am` auf das heutige Datum setzen und "
          "`python3 tests/run_tests.py` fahren.")
    print("\nDer Entwurf ist ein Startpunkt, keine fertige Anbindung. "
          "Solange TODO drinsteht, lehnt parse_broker.py das Profil ab — so gewollt.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Schlägt aus einem Broker-Report ein Profil-Gerüst plus Fixture vor.")
    ap.add_argument("report", help="Report als PDF, CSV oder Textdatei")
    ap.add_argument("--id", required=True, help="Profil-ID (kebab-case)")
    ap.add_argument("--out", default=None, help="Zielpfad des Profils")
    ap.add_argument("--fixture", default=None, help="Zielpfad des Fixture-Gerüsts")
    ap.add_argument("--kind", default="auto",
                    choices=("auto",) + ERGEBNIS_ARTEN, help="Ausgabeschema erzwingen")
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "docling", "pdfplumber", "pymupdf"])
    ap.add_argument("--ocr-lang", default="deu+eng")
    ap.add_argument("--dry-run", action="store_true",
                    help="nichts schreiben, nur den Vorschlag zeigen")
    args = ap.parse_args(argv)

    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", args.id):
        print(f"--id {args.id!r}: bitte kebab-case (a-z, 0-9, '-').", file=sys.stderr)
        return 2

    quelle = Path(args.report)
    if not quelle.exists():
        print(f"Datei nicht gefunden: {quelle}", file=sys.stderr)
        return 2

    root = _repo_root()
    out_pfad = Path(args.out) if args.out else root / "scripts" / "profiles" / f"{args.id}.json"
    fix_pfad = Path(args.fixture) if args.fixture else root / "tests" / "fixtures" / f"{args.id}.txt"
    try:
        fix_rel = fix_pfad.resolve().relative_to(root).as_posix()
    except ValueError:
        fix_rel = fix_pfad.as_posix()

    print(f"Lese {quelle} ...")
    text, art = text_aus_datei(quelle, backend=args.backend, ocr_lang=args.ocr_lang)
    if not text.strip():
        print("Kein Text extrahiert — bei gescannten PDFs OCR prüfen "
              "(tesseract-ocr-deu, pdf2image).", file=sys.stderr)
        return 1
    eingabe = "csv" if art == "csv" else "pdf"
    if art == "txt":
        print("  Hinweis: Textdatei gelesen — `eingabe` im Profil auf 'pdf' gesetzt, "
              "bei Bedarf anpassen.", file=sys.stderr)

    erg = entwurf_aus_text(text, args.id, eingabe=eingabe, kind=args.kind,
                           quelle_datei=quelle.name, fixture_pfad=fix_rel)

    if not args.dry_run:
        out_pfad.parent.mkdir(parents=True, exist_ok=True)
        out_pfad.write_text(json.dumps(erg["profil"], indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        fix_pfad.parent.mkdir(parents=True, exist_ok=True)
        fix_pfad.write_text(erg["fixture"]["text"], encoding="utf-8")
        print(f"  geschrieben: {out_pfad}")
        print(f"  geschrieben: {fix_pfad}")
    else:
        print("  (--dry-run: nichts geschrieben)")

    _drucke_bericht(erg, quelle.name, out_pfad, fix_pfad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
