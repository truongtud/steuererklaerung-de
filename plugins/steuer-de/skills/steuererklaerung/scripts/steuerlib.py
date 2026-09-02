#!/usr/bin/env python3
"""Gemeinsame Basis für alle Skripte dieses Skills.

Enthält (a) *einen* toleranten Zahlenparser statt vier verschiedener, (b) taggenaue
Fristenlogik nach § 108 AO / § 188 BGB, (c) alle jahresabhängigen Steuerwerte an
einer Stelle und (d) den Summenabgleich, mit dem Parser stille Zeilenverluste melden.

Grundregel: **Bei unlesbarer Eingabe wird geworfen, nicht 0 zurückgegeben.**
Ein stiller 0-Wert ist in einer Steuerberechnung der teuerste Fehler.

Keine Steuerberatung. Werte vor Verwendung gegen `references/steuerwerte.md` prüfen.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Iterable, Optional

D = Decimal

# ─────────────────────────────────────────────────────────────────────────────
# Fehler
# ─────────────────────────────────────────────────────────────────────────────


class ParseError(ValueError):
    """Eingabe konnte nicht eindeutig gelesen werden."""


class PlausibilityError(ValueError):
    """Geparste Summe weicht von der im Report ausgewiesenen Summe ab."""


class SteuerwerteError(RuntimeError):
    """references/steuerwerte.json fehlt oder ist unlesbar."""


# ─────────────────────────────────────────────────────────────────────────────
# Zahlen
# ─────────────────────────────────────────────────────────────────────────────

_MINUS = {"−": "-", "–": "-", "—": "-", "➖": "-"}
_SPACE = {" ": "", " ": "", " ": "", " ": ""}
_CURRENCY = re.compile(r"(?:EUR|€|\$|USD|CHF|£)", re.I)
_NUM_BODY = re.compile(r"^\d[\d.,]*(?:[eE][+-]?\d+)?$")


def _clean(s: str) -> str:
    for k, v in _MINUS.items():
        s = s.replace(k, v)
    s = _CURRENCY.sub("", s)
    for k, v in _SPACE.items():
        s = s.replace(k, v)
    return s.strip()


def to_decimal(value, *, locale_hint: Optional[str] = None) -> Decimal:
    """Liest einen Betrag in deutscher **oder** englischer Notation.

    Erkannt werden: 1.234,56 · 1,234.56 · 1234.56 · 1234,56 · -1.234,56 ·
    (1.234,56) · 1.234,56- · −1.234,56 (U+2212) · "1.234,56 EUR" · 3.5e-8.

    `locale_hint` ("de"/"en") entscheidet die echten Mehrdeutigkeiten — "1.234"
    und "1,234" sind ohne Kontext nicht auflösbar. Ohne Hint gilt die Konvention:
    *ein* Trennzeichen mit **genau drei** Nachkommastellen ist ein
    Tausendertrennzeichen (1.234 → 1234), alles andere ein Dezimaltrenner.

    Wirft ParseError statt still 0 zu liefern.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return D(value)
    if isinstance(value, float):
        return D(str(value))
    if value is None:
        raise ParseError("Betrag fehlt (None)")

    s = _clean(str(value))
    if not s:
        raise ParseError("Betrag ist leer")

    neg = False
    if s.startswith("(") and s.endswith(")"):  # Buchhalter-Notation
        neg, s = True, s[1:-1].strip()
    if s.endswith("-"):  # nachgestelltes Minus (SAP/DATEV)
        neg, s = True, s[:-1].strip()
    if s.startswith("-"):
        neg, s = not neg, s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()

    if not s or not _NUM_BODY.match(s):
        raise ParseError(f"Betrag nicht lesbar: {value!r}")

    exp = ""
    m = re.search(r"[eE][+-]?\d+$", s)
    if m:
        exp, s = m.group(0), s[: m.start()]

    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        # Das *letzte* Trennzeichen ist der Dezimaltrenner.
        dec_sep = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("," if dec_sep == "." else ".", "").replace(dec_sep, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = s.split(sep)
        tail = parts[-1]
        if len(parts) > 2:
            s = s.replace(sep, "")  # 1.234.567 → sicher Tausender
        elif locale_hint == "de":
            s = s.replace(".", "") if sep == "." else s.replace(",", ".")
        elif locale_hint == "en":
            s = s.replace(",", "") if sep == "," else s
        elif len(tail) == 3 and not parts[0].startswith("0"):
            s = s.replace(sep, "")  # 1.234 / 1,234 → Tausender (Konvention)
        else:
            s = s.replace(sep, ".")

    try:
        d = D(s + exp)
    except InvalidOperation:
        raise ParseError(f"Betrag nicht lesbar: {value!r}")
    return -d if neg else d


def to_decimal_or(value, default: Decimal = D("0")) -> Decimal:
    """Wie to_decimal, aber mit Default für ausdrücklich optionale Felder."""
    try:
        return to_decimal(value)
    except ParseError:
        return default


def detect_locale(text: str) -> str:
    """Rät die Zahlennotation eines ganzen Dokuments (für locale_hint)."""
    de = len(re.findall(r"\d{1,3}(?:\.\d{3})+,\d", text)) + len(re.findall(r"\d+,\d{2}\b", text))
    en = len(re.findall(r"\d{1,3}(?:,\d{3})+\.\d", text)) + len(re.findall(r"\d+\.\d{2}\b", text))
    return "de" if de >= en else "en"


def q2(d: Decimal) -> Decimal:
    return D(d).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def euro_abrunden(d: Decimal) -> Decimal:
    """§ 32a Abs. 1: der Steuerbetrag ist auf den vollen Euro **abzurunden**."""
    return D(d).quantize(D("1"), rounding=ROUND_DOWN)


def fmt_eur(d) -> str:
    """1234.5 → '1.234,50 €' (deutsche Notation)."""
    try:
        v = q2(to_decimal(d))
    except ParseError:
        return "—"
    s = f"{abs(v):,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{'-' if v < 0 else ''}{s} €"


def de_dezimal(s) -> str:
    """'62000.00' → '62000,00' — für CSV-Import in deutsches Excel."""
    t = str(s)
    return t.replace(".", ",") if re.fullmatch(r"-?\d+\.\d+", t) else t


def csv_safe(s) -> str:
    """Formel-Injection in Tabellenkalkulationen entschärfen."""
    t = "" if s is None else str(s)
    return "'" + t if t[:1] in ("=", "+", "@", "\t", "\r") else t


# ─────────────────────────────────────────────────────────────────────────────
# Datum und Fristen
# ─────────────────────────────────────────────────────────────────────────────

_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_DMY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")


def parse_datetime(value, *, dayfirst: bool = True) -> datetime:
    """ISO zuerst (damit '2024-01-02' nicht als 02.01.2002 gelesen wird),
    danach DD.MM.YYYY / DD/MM/YYYY. Naive Zeiten gelten als UTC-neutral."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    if value is None:
        raise ParseError("Datum fehlt")
    s = str(value).strip().replace("T", " ")
    if not s:
        raise ParseError("Datum ist leer")

    time_part = "00:00:00"
    m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)", s)
    if m:
        time_part = m.group(1) if m.group(1).count(":") == 2 else m.group(1) + ":00"

    mi = _ISO.match(s)
    if mi:
        y, mo, dy = int(mi.group(1)), int(mi.group(2)), int(mi.group(3))
    else:
        md = _DMY.match(s)
        if not md:
            raise ParseError(f"Datum nicht lesbar: {value!r}")
        a, b, y = int(md.group(1)), int(md.group(2)), int(md.group(3))
        if dayfirst:
            dy, mo = a, b
        else:
            mo, dy = a, b
        if mo > 12:  # Ordnung war falsch geraten
            dy, mo = mo, dy
    try:
        h, mnt, sec = (int(x) for x in time_part.split(":"))
        return datetime(y, mo, dy, h, mnt, sec)
    except ValueError as e:
        raise ParseError(f"Datum ungültig: {value!r} ({e})")


def date_ambiguous(value) -> bool:
    """True, wenn DD/MM und MM/DD beide plausibel sind (Tag ≤ 12)."""
    md = _DMY.match(str(value).strip())
    return bool(md) and int(md.group(1)) <= 12 and int(md.group(2)) <= 12


def jahresfrist_ende(anschaffung: date) -> date:
    """Letzter Tag der einjährigen Frist nach § 108 Abs. 1 AO i. V. m.
    § 188 Abs. 2 BGB: der Tag des Folgejahres, der dem Anschaffungstag entspricht.
    Fehlt er (29.02.), endet die Frist am letzten Tag des Monats (§ 188 Abs. 3 BGB).
    """
    y, m, d = anschaffung.year + 1, anschaffung.month, anschaffung.day
    while True:
        try:
            return date(y, m, d)
        except ValueError:
            d -= 1  # 29.02. → 28.02.


def haltefrist_erfuellt(anschaffung, veraeusserung) -> bool:
    """Steuerfrei nach § 23 Abs. 1 Nr. 2 EStG erst *nach* Ablauf der Jahresfrist.

    Taggenau, ohne Uhrzeit: Veräußerung am Jahrestag selbst ist noch steuerpflichtig.
    """
    a = anschaffung.date() if isinstance(anschaffung, datetime) else anschaffung
    v = veraeusserung.date() if isinstance(veraeusserung, datetime) else veraeusserung
    return v > jahresfrist_ende(a)


# ─────────────────────────────────────────────────────────────────────────────
# Jahresabhängige Steuerwerte  (Quelle: references/steuerwerte.json)
# ─────────────────────────────────────────────────────────────────────────────

# Alle jahresabhängigen Werte stehen in references/steuerwerte.json und werden
# hier beim Import gelesen — Tarif nach § 32a Abs. 1 EStG, Soli-Freigrenze nach
# § 3 Abs. 3 SolZG, Pauschbeträge und die Freigrenze nach § 23 EStG. Gepflegt
# wird die JSON mit scripts/fetch_steuerwerte.py; references/steuerwerte.md ist
# die menschenlesbare Fassung davon.
# STEUER_DE_WERTE zeigt auf eine andere Wertedatei — gedacht für Tests und für
# einen Probelauf mit einem Entwurf, nicht für den Alltag.
STEUERWERTE_JSON = os.environ.get("STEUER_DE_WERTE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "references", "steuerwerte.json")


def _lade_steuerwerte(pfad: str = STEUERWERTE_JSON) -> dict:
    """Jahr → Werte. Fehlt oder bricht die Datei, wird geworfen: eine
    Steuerberechnung ohne hinterlegte Werte darf nicht anlaufen."""
    try:
        with open(pfad, encoding="utf-8") as f:
            daten = json.load(f)
        # `null` heißt „für dieses Jahr noch nicht ermittelt“ — der Wert fehlt
        # dann in der jeweiligen Tabelle, und der dokumentierte Ersatzwert des
        # nächstgelegenen Jahres greift (mit Warnung). Eine 0 stünde dagegen für
        # „kein Pauschbetrag“ und wäre eine stille Falschangabe.
        jahre = {
            int(jahr): {
                "tarif": {k: D(v) for k, v in e["tarif"].items()},
                **{k: (None if e[k] is None else D(e[k]))
                   for k in ("soli_freigrenze", "freigrenze_23",
                             "sparer_pb", "an_pauschbetrag")},
                # Nur für den Vorsorge-Höchstbetrag (§ 10 Abs. 3 Satz 1). Fehlen
                # sie, entfällt allein die Höchstbetragsberechnung — deshalb
                # stehen sie NICHT in JAHRESWERTE und lösen keinen Ersatzwert
                # für das ganze Jahr aus.
                **{k: (None if e.get(k) is None else D(e[k]))
                   for k in ("bbg_knappschaftlich", "beitragssatz_knappschaftlich")},
            }
            for jahr, e in daten["jahre"].items()
        }
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as e:
        raise SteuerwerteError(
            f"Steuerwerte aus {os.path.normpath(pfad)} nicht lesbar: {e}"
        ) from e
    if not jahre:
        raise SteuerwerteError(f"{os.path.normpath(pfad)} enthält kein Steuerjahr.")
    return jahre


_WERTE = _lade_steuerwerte()

JAHRESWERTE = ("soli_freigrenze", "freigrenze_23", "sparer_pb", "an_pauschbetrag")


def _tabelle(name: str) -> dict:
    """Jahre ohne hinterlegten Wert bleiben draußen, statt mit 0 dazustehen."""
    return {j: w[name] for j, w in _WERTE.items() if w[name] is not None}


# Jahre, für die *alle* Jahreswerte hinterlegt sind. Ein Jahr kann einen
# § 32a-Tarif haben und trotzdem noch keine Pauschbeträge — genau diesen Zustand
# legt fetch_steuerwerte.py für ein neues Jahr an.
_VOLLSTAENDIGE_JAHRE = sorted(
    j for j, w in _WERTE.items() if all(w[k] is not None for k in JAHRESWERTE))


def jahr_mit_werten(jahr: int) -> int:
    """Das nächstgelegene Jahr, für das Pauschbeträge, Freigrenzen und die
    Soli-Freigrenze vollständig hinterlegt sind.

    Ohne diesen Umweg liefe ein Jahr mit Tarif, aber ohne Pauschbeträge, in
    einen KeyError statt in den dokumentierten Ersatzwert mit Warnung.
    """
    if not _VOLLSTAENDIGE_JAHRE:
        raise SteuerwerteError(
            "Kein Jahr in den Steuerwerten hat vollständige Pauschbeträge und "
            "Freigrenzen — references/steuerwerte.json prüfen.")
    return min(max(jahr, _VOLLSTAENDIGE_JAHRE[0]), _VOLLSTAENDIGE_JAHRE[-1])


TARIF = {j: w["tarif"] for j, w in _WERTE.items()}
# Freigrenze tarifliche ESt für den Solidaritätszuschlag (Einzelveranlagung;
# bei Zusammenveranlagung verdoppelt). § 3 Abs. 3 SolZG.
SOLI_FREIGRENZE = _tabelle("soli_freigrenze")
FREIGRENZE_23 = _tabelle("freigrenze_23")
SPARER_PB = _tabelle("sparer_pb")
AN_PAUSCHBETRAG = _tabelle("an_pauschbetrag")

SOLI_SATZ = D("0.055")
SOLI_MILDERUNG = D("0.119")  # § 4 Satz 2 SolZG

# Jahresunabhängig im Gesetz — stehen deshalb hier und nicht in der JSON.
FREIGRENZE_22_3 = D("256")  # § 22 Nr. 3 Satz 2 EStG
SONDERAUSGABEN_PB = D("36")
KIST_SAETZE = (D("0.08"), D("0.09"))


def _jahreswert(tabelle: dict, jahr: int, name: str) -> Decimal:
    if jahr in tabelle:
        return tabelle[jahr]
    bekannt = f"bekannt bis {max(tabelle)}" if tabelle else "kein Jahr hinterlegt"
    raise KeyError(
        f"{name} für {jahr} nicht hinterlegt ({bekannt}). Aktuellen Wert prüfen "
        f"und in references/steuerwerte.json ergänzen."
    )


def freigrenze_23(jahr: int) -> Decimal:
    return _jahreswert(FREIGRENZE_23, jahr, "Freigrenze § 23")


def sparer_pauschbetrag(jahr: int, zusammen: bool = False) -> Decimal:
    v = _jahreswert(SPARER_PB, jahr, "Sparer-Pauschbetrag")
    return v * 2 if zusammen else v


def an_pauschbetrag(jahr: int) -> Decimal:
    return _jahreswert(AN_PAUSCHBETRAG, jahr, "Arbeitnehmer-Pauschbetrag")


def est_aus_tarif(zve: Decimal, t: dict) -> Decimal:
    """Die Zonenformel des § 32a Abs. 1 mit einem *übergebenen* Satz Tarifwerte.

    Getrennt von est_grundtarif, damit fetch_steuerwerte.py frisch geladene
    Werte prüfen kann, bevor sie in references/steuerwerte.json landen.
    """
    x = D(max(zve, D("0")))
    if x <= t["gfb"]:
        return D("0")
    if x <= t["z2"]:
        y = (x - t["gfb"]) / D("10000")
        est = (t["a2"] * y + D("1400")) * y
    elif x <= t["z3"]:
        z = (x - t["z2"]) / D("10000")
        est = (t["a3"] * z + D("2397")) * z + t["c3"]
    elif x <= t["z4"]:
        est = D("0.42") * x - t["k4"]
    else:
        est = D("0.45") * x - t["k5"]
    return euro_abrunden(est)


def est_grundtarif(zve: Decimal, jahr: int) -> Optional[Decimal]:
    """Tarifliche ESt nach § 32a Abs. 1 (Grundtarif), auf volle Euro abgerundet.
    None, wenn für das Jahr kein Tarif hinterlegt ist."""
    t = TARIF.get(jahr)
    return None if t is None else est_aus_tarif(zve, t)


def est_tarif(zve: Decimal, jahr: int, zusammenveranlagung: bool = False) -> Optional[Decimal]:
    """Grund- oder Splittingtarif (§ 32a Abs. 5: 2 × ESt(zvE/2))."""
    if zusammenveranlagung:
        halb = est_grundtarif(D(zve) / 2, jahr)
        return None if halb is None else halb * 2
    return est_grundtarif(D(zve), jahr)


def besonderer_steuersatz(zve: Decimal, lohnersatz: Decimal, jahr: int,
                          zusammenveranlagung: bool = False) -> Optional[Decimal]:
    """§ 32b Abs. 2: Steuersatz, der sich auf zvE **zuzüglich** der
    Lohnersatzleistungen ergibt.

    Zur Rundung: § 32b schreibt selbst keine vor. Die üblichen vier
    Nachkommastellen stammen aus der Verwaltungspraxis; hier wird abgerundet,
    also zugunsten des Steuerpflichtigen. None, wenn für das Jahr kein Tarif
    hinterlegt ist.
    """
    erhoeht = D(zve) + max(D(lohnersatz), D("0"))
    if erhoeht <= 0:
        return D("0.0000")
    est = est_tarif(erhoeht, jahr, zusammenveranlagung)
    if est is None:
        return None
    return (est / erhoeht).quantize(D("0.0001"), rounding=ROUND_DOWN)


def est_mit_progressionsvorbehalt(zve: Decimal, lohnersatz: Decimal, jahr: int,
                                  zusammenveranlagung: bool = False) -> Optional[Decimal]:
    """Tarifliche ESt unter Progressionsvorbehalt (§ 32b EStG).

    Steuerfreie Lohnersatzleistungen — Eltern-, Arbeitslosen-, Kranken-,
    Kurzarbeiter-, Mutterschaftsgeld — werden nicht besteuert, heben aber den
    Satz auf das übrige Einkommen. Ohne Leistung ist das Ergebnis identisch mit
    est_tarif.
    """
    if max(D(lohnersatz), D("0")) == 0:
        return est_tarif(zve, jahr, zusammenveranlagung)
    satz = besonderer_steuersatz(zve, lohnersatz, jahr, zusammenveranlagung)
    if satz is None:
        return None
    return euro_abrunden(D(zve) * satz)


def soli(est: Decimal, jahr: int, zusammenveranlagung: bool = False) -> Decimal:
    """Solidaritätszuschlag inkl. Milderungszone (§ 4 SolZG).

    Bis zur Freigrenze 0; darüber höchstens 11,9 % des Überhangs, gedeckelt auf 5,5 %.
    """
    est = D(max(est, D("0")))
    fg = _jahreswert(SOLI_FREIGRENZE, jahr, "Soli-Freigrenze")
    if zusammenveranlagung:
        fg *= 2
    if est <= fg:
        return D("0")
    return q2(min(est * SOLI_SATZ, SOLI_MILDERUNG * (est - fg)))


# § 33 Abs. 3 EStG — Stufengrenzen und Prozentsätze. Jahresunabhängig im Gesetz.
ZUMUTBAR_STUFEN = (D("15340"), D("51130"))
ZUMUTBAR_SAETZE = {
    "grund": (D("5"), D("6"), D("7")),      # Nr. 1 a: ohne Kinder, Grundtarif
    "splitting": (D("4"), D("5"), D("6")),  # Nr. 1 b: ohne Kinder, Splitting
    "kind_1_2": (D("2"), D("3"), D("4")),   # Nr. 2 a: ein oder zwei Kinder
    "kind_3": (D("1"), D("1"), D("2")),     # Nr. 2 b: drei oder mehr Kinder
}


def zumutbare_belastung(gesamtbetrag: Decimal, zusammenveranlagung: bool = False,
                        kinder: int = 0) -> Decimal:
    """§ 33 Abs. 3 EStG — der Teil der außergewöhnlichen Belastungen, den man
    selbst zu tragen hat.

    **Stufenweise** gerechnet: der höhere Satz gilt nur für den Teil des
    Gesamtbetrags der Einkünfte, der die jeweilige Grenze übersteigt (BFH vom
    19.01.2017, VI R 75/14). Die frühere Lesart wandte einen Satz auf den ganzen
    Betrag an; an den Stufengrenzen sprang die zumutbare Belastung dadurch um
    mehrere hundert Euro.
    """
    if kinder >= 3:
        saetze = ZUMUTBAR_SAETZE["kind_3"]
    elif kinder >= 1:
        saetze = ZUMUTBAR_SAETZE["kind_1_2"]
    else:
        saetze = ZUMUTBAR_SAETZE["splitting" if zusammenveranlagung else "grund"]

    rest = max(D(gesamtbetrag), D("0"))
    vorige_grenze = D("0")
    summe = D("0")
    for grenze, satz in zip(ZUMUTBAR_STUFEN + (None,), saetze):
        teil = rest if grenze is None else min(rest, grenze - vorige_grenze)
        if teil <= 0:
            break
        summe += teil * satz / D("100")
        rest -= teil
        if grenze is not None:
            vorige_grenze = grenze
    return q2(summe)


# § 10 Abs. 4 Sätze 1 und 2 EStG — jahresunabhängig im Gesetz.
VORSORGE_SONSTIGE = D("2800")               # Satz 1
VORSORGE_SONSTIGE_MIT_ZUSCHUSS = D("1900")  # Satz 2


def vorsorge_hoechstbetrag(jahr: int, zusammenveranlagung: bool = False) -> Optional[Decimal]:
    """§ 10 Abs. 3 Sätze 1 und 2: Höchstbeitrag zur knappschaftlichen
    Rentenversicherung, aufgerundet auf einen vollen Euro, bei
    Zusammenveranlagung verdoppelt.

    None, wenn Beitragsbemessungsgrenze oder Beitragssatz für das Jahr fehlen —
    dann entfällt allein die Höchstbetragsberechnung, nicht das ganze Jahr.
    """
    werte = _WERTE.get(jahr) or {}
    bbg, satz = werte.get("bbg_knappschaftlich"), werte.get("beitragssatz_knappschaftlich")
    if bbg is None or satz is None:
        return None
    hoechst = (bbg * satz).quantize(D("1"), rounding=ROUND_UP)
    return hoechst * 2 if zusammenveranlagung else hoechst


def vorsorge_anteil(jahr: int) -> Decimal:
    """§ 10 Abs. 3 Sätze 4 und 6: 2013 sind 76 Prozent der ermittelten
    Aufwendungen anzusetzen, je folgendem Kalenderjahr zwei Prozentpunkte mehr
    bis 2022; ab 2023 sind es 100 Prozent."""
    if jahr >= 2023:
        return D("1.00")
    if jahr < 2013:
        raise KeyError(f"Vorsorge-Anteilssatz für {jahr} nicht geregelt (Staffel ab 2013)")
    return (D("76") + D("2") * (jahr - 2013)) / D("100")


def vorsorge_abziehbar(*, basis: Decimal, kranken_pflege: Decimal, sonstige: Decimal,
                       arbeitgeberanteil: Decimal, jahr: int,
                       zusammenveranlagung: bool = False,
                       mit_zuschuss: bool = True) -> dict:
    """Abziehbare Vorsorgeaufwendungen nach § 10 Abs. 3 und 4 EStG.

    Zwei getrennte Töpfe:

      * **Basisversorgung** (Abs. 1 Nr. 2) — auf den Höchstbeitrag gedeckelt,
        anteilig nach der Staffel angesetzt, dann um den steuerfreien
        Arbeitgeberanteil vermindert (Abs. 3 Sätze 1 bis 5).
      * **Kranken-/Pflege- und sonstige Vorsorge** (Abs. 1 Nr. 3 und 3a) —
        zusammen höchstens 2.800 €, bei Anspruch auf Zuschuss 1.900 €.
        Übersteigen allein die Basiskranken- und Pflegebeiträge diesen Betrag,
        sind sie in voller Höhe abziehbar und ein Abzug der sonstigen entfällt
        (Abs. 4 Satz 4).

    Nicht abgebildet: die Kürzung des Höchstbetrags für Beamte und
    Versorgungsanwärter (Abs. 3 Satz 3). Sie verlangt den fiktiven Gesamtbeitrag
    zur allgemeinen Rentenversicherung, den die Eingabedaten nicht hergeben; für
    diesen Personenkreis fällt der Abzug damit zu hoch aus.
    """
    hoechst = vorsorge_hoechstbetrag(jahr, zusammenveranlagung)
    if hoechst is None:
        return {"basisversorgung": None, "sonstige": None, "sonstige_verfallen": None,
                "hoechstbetrag_basis": None}

    null = D("0")
    angesetzt = min(max(D(basis), null), hoechst) * vorsorge_anteil(jahr)
    basisversorgung = q2(max(angesetzt - max(D(arbeitgeberanteil), null), null))

    grundbetrag = VORSORGE_SONSTIGE_MIT_ZUSCHUSS if mit_zuschuss else VORSORGE_SONSTIGE
    deckel = grundbetrag * 2 if zusammenveranlagung else grundbetrag
    kp, so = max(D(kranken_pflege), null), max(D(sonstige), null)
    if kp > deckel:
        sonstige_abziehbar, verfallen = kp, so
    else:
        sonstige_abziehbar = min(kp + so, deckel)
        verfallen = max(kp + so - deckel, null)
    return {"basisversorgung": basisversorgung, "sonstige": q2(sonstige_abziehbar),
            "sonstige_verfallen": q2(verfallen), "hoechstbetrag_basis": hoechst}


# § 35a EStG — Steuerermäßigung für haushaltsnahe Leistungen. Die Höchstbeträge
# stehen jahresunabhängig im Gesetz und bleiben deshalb hier, nicht in
# references/steuerwerte.json.
PARAGRAF_35A_SATZ = D("0.20")
PARAGRAF_35A_HOECHSTBETRAG = {
    "minijob": D("510"),          # Abs. 1: geringfügige Beschäftigung im Haushalt
    "haushaltsnah": D("4000"),    # Abs. 2: haushaltsnahe Dienstleistungen, Pflege
    "handwerker": D("1200"),      # Abs. 3: Handwerkerleistungen
}


def steuerermaessigung_35a(minijob: Decimal, haushaltsnah: Decimal,
                           handwerker: Decimal) -> Decimal:
    """§ 35a EStG: je Topf 20 % der Aufwendungen, jeder mit eigenem Höchstbetrag.

    Die drei Höchstbeträge sind getrennt — ein Überhang im einen Topf füllt den
    anderen **nicht** auf. Begünstigt sind nur Arbeits-, Maschinen- und
    Fahrtkosten (§ 35a Abs. 5); Material zählt nicht, und die Rechnung muss
    unbar bezahlt sein. Beides steht nicht in den Zahlen und kann hier nicht
    geprüft werden — der Report weist darauf hin.
    """
    summe = D("0")
    for wert, topf in ((minijob, "minijob"), (haushaltsnah, "haushaltsnah"),
                       (handwerker, "handwerker")):
        aufwand = max(D(wert), D("0"))
        summe += min(aufwand * PARAGRAF_35A_SATZ, PARAGRAF_35A_HOECHSTBETRAG[topf])
    return q2(summe)


def normiere_kirchensteuersatz(wert) -> Optional[Decimal]:
    """Akzeptiert 0.09 wie auch 9 (Prozent) und weist Unsinn zurück.

    Ohne diese Normierung ergibt die Eingabe '9' das Neunfache der ESt als KiSt.
    """
    if wert in (None, "", 0, "0"):
        return None
    s = to_decimal(wert)
    if s > 1:
        s = s / D("100")
    if not (D("0.05") <= s <= D("0.12")):
        raise ParseError(
            f"Kirchensteuersatz {wert!r} unplausibel — erwartet 0.08 (BW/BY) oder 0.09."
        )
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Plausibilität
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Abgleich:
    """Eine geparste Summe gegen die vom Report selbst ausgewiesene.

    `ausgewiesen is None` heißt: der Report gab den Vergleichswert nicht her.
    Das ist **kein** Erfolg — die geparste Zahl ist dann durch nichts gedeckt.
    Nur ein ausdrücklich als `optional=True` gekennzeichneter Abgleich darf ohne
    Vergleichswert durchgehen (Reports, die eine solche Summe wirklich nicht
    drucken); auch der bleibt im Bericht als ungeprüft sichtbar.
    """
    label: str
    geparst: Decimal
    ausgewiesen: Optional[Decimal]
    toleranz: Decimal = D("0.01")
    optional: bool = False

    @property
    def fehlend(self) -> bool:
        """Der Report lieferte keinen Vergleichswert — nichts wurde gegengeprüft."""
        return self.ausgewiesen is None

    @property
    def ok(self) -> bool:
        if self.ausgewiesen is None:
            return bool(self.optional)
        return abs(self.geparst - self.ausgewiesen) <= self.toleranz

    def _fehlend_text(self) -> str:
        if self.optional:
            return (f"{self.label}: geparst {fmt_eur(self.geparst)} — OHNE GEGENPRÜFUNG "
                    f"(im Profil als 'optional' gekennzeichnet: dieser Report weist "
                    f"keine solche Summe aus). Zahl ist NICHT verifiziert.")
        return (f"{self.label}: geparst {fmt_eur(self.geparst)} — NICHT GEGENGEPRÜFT: "
                f"die im Report ausgewiesene Vergleichssumme wurde nicht gefunden.")

    def __str__(self) -> str:
        if self.ausgewiesen is None:
            return self._fehlend_text()
        return (f"{self.label}: geparst {fmt_eur(self.geparst)} vs. Report "
                f"{fmt_eur(self.ausgewiesen)} — Abweichung {fmt_eur(self.geparst - self.ausgewiesen)}")


_FEHLT_HINWEIS = (
    "→ Muster im Profil gegen das Original prüfen. Enthält dieser Report "
    "tatsächlich keine solche Summe, den betreffenden summen-Eintrag ausdrücklich "
    "mit \"optional\": true kennzeichnen — dann bleibt der Lauf möglich, das "
    "Ergebnis aber ausgewiesen ungeprüft.")


def pruefe_summen(abgleiche: Iterable[Abgleich], *, strikt: bool = True) -> list[str]:
    """Vergleicht geparste Summen mit den im Report selbst ausgewiesenen.

    Das ist das Sicherheitsnetz gegen stille Zeilenverluste: ein Parser, der die
    Hälfte der Tabelle verliert, fällt hier auf — nicht erst im Steuerbescheid.

    Ein **fehlender** Vergleichswert ist dabei genauso ein Abbruchgrund wie eine
    Abweichung: findet das Muster nichts, hat die Prüfung nicht stattgefunden und
    das Ergebnis ist unbestätigt. Nur `Abgleich.optional` erlaubt den Durchlauf.
    """
    probleme, fehlend, hinweise = [], [], []
    for a in abgleiche:
        if a.fehlend and not a.optional:
            fehlend.append(str(a))
        elif a.ok:
            hinweise.append(str(a))
        else:
            probleme.append(str(a))
    if strikt and (probleme or fehlend):
        teile = []
        if fehlend:
            teile.append(
                "Die im Report selbst ausgewiesene Vergleichssumme konnte NICHT "
                "gefunden werden — das Ergebnis ist damit UNGEPRÜFT und darf nicht "
                "in eine Steuererklärung übernommen werden:\n  "
                + "\n  ".join(fehlend) + "\n" + _FEHLT_HINWEIS)
        if probleme:
            teile.append(
                "Geparste Summen stimmen nicht mit dem Report überein — Zeilen gingen "
                "vermutlich verloren:\n  " + "\n  ".join(probleme)
                + "\n→ Report-Layout prüfen; NICHT ungeprüft weiterverwenden.")
        raise PlausibilityError("\n".join(teile))
    return hinweise + fehlend + probleme
