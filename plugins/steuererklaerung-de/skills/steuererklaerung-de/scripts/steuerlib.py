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

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Iterable, Optional

D = Decimal

# ─────────────────────────────────────────────────────────────────────────────
# Fehler
# ─────────────────────────────────────────────────────────────────────────────


class ParseError(ValueError):
    """Eingabe konnte nicht eindeutig gelesen werden."""


class PlausibilityError(ValueError):
    """Geparste Summe weicht von der im Report ausgewiesenen Summe ab."""


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
# Jahresabhängige Steuerwerte  (Quelle: references/steuerwerte.md)
# ─────────────────────────────────────────────────────────────────────────────

# § 32a Abs. 1 EStG, Grundtarif. Zonen: (obergrenze, faktor, summand, konstante)
# 2024 in der Fassung des Gesetzes zur steuerlichen Freistellung des
# Existenzminimums 2024 v. 02.12.2024 (Grundfreibetrag rückwirkend 11.784 €).
TARIF = {
    2022: {"gfb": D("10347"), "z2": D("14926"), "z3": D("58596"), "z4": D("277825"),
           "a2": D("1088.67"), "c3": D("869.32"), "a3": D("206.43"),
           "k4": D("9336.45"), "k5": D("17671.20")},
    2023: {"gfb": D("10908"), "z2": D("15999"), "z3": D("62809"), "z4": D("277825"),
           "a2": D("979.18"), "c3": D("966.53"), "a3": D("192.59"),
           "k4": D("9972.98"), "k5": D("18307.73")},
    2024: {"gfb": D("11784"), "z2": D("17005"), "z3": D("66760"), "z4": D("277825"),
           "a2": D("954.80"), "c3": D("991.21"), "a3": D("181.19"),
           "k4": D("10636.31"), "k5": D("18971.06")},
    2025: {"gfb": D("12096"), "z2": D("17443"), "z3": D("68480"), "z4": D("277825"),
           "a2": D("932.30"), "c3": D("1015.13"), "a3": D("176.64"),
           "k4": D("10911.92"), "k5": D("19246.67")},
    2026: {"gfb": D("12348"), "z2": D("17799"), "z3": D("69878"), "z4": D("277825"),
           "a2": D("914.51"), "c3": D("1034.87"), "a3": D("173.10"),
           "k4": D("11135.63"), "k5": D("19470.38")},
}

# Freigrenze tarifliche ESt für den Solidaritätszuschlag (Einzelveranlagung;
# bei Zusammenveranlagung verdoppelt). § 3 Abs. 3 SolZG.
SOLI_FREIGRENZE = {2022: D("16956"), 2023: D("17543"), 2024: D("18130"),
                   2025: D("19450"), 2026: D("20350")}
SOLI_SATZ = D("0.055")
SOLI_MILDERUNG = D("0.119")  # § 4 Satz 2 SolZG

FREIGRENZE_23 = {2022: D("600"), 2023: D("600"), 2024: D("1000"),
                 2025: D("1000"), 2026: D("1000")}
FREIGRENZE_22_3 = D("256")  # § 22 Nr. 3 Satz 2 EStG, seit Jahren unverändert
SPARER_PB = {2022: D("801"), 2023: D("1000"), 2024: D("1000"),
             2025: D("1000"), 2026: D("1000")}
AN_PAUSCHBETRAG = {2022: D("1200"), 2023: D("1230"), 2024: D("1230"),
                   2025: D("1230"), 2026: D("1230")}
SONDERAUSGABEN_PB = D("36")
KIST_SAETZE = (D("0.08"), D("0.09"))


def _jahreswert(tabelle: dict, jahr: int, name: str) -> Decimal:
    if jahr in tabelle:
        return tabelle[jahr]
    letztes = max(tabelle)
    raise KeyError(
        f"{name} für {jahr} nicht hinterlegt (bekannt bis {letztes}). "
        f"Aktuellen Wert prüfen und in scripts/steuerlib.py ergänzen."
    )


def freigrenze_23(jahr: int) -> Decimal:
    return _jahreswert(FREIGRENZE_23, jahr, "Freigrenze § 23")


def sparer_pauschbetrag(jahr: int, zusammen: bool = False) -> Decimal:
    v = _jahreswert(SPARER_PB, jahr, "Sparer-Pauschbetrag")
    return v * 2 if zusammen else v


def an_pauschbetrag(jahr: int) -> Decimal:
    return _jahreswert(AN_PAUSCHBETRAG, jahr, "Arbeitnehmer-Pauschbetrag")


def est_grundtarif(zve: Decimal, jahr: int) -> Optional[Decimal]:
    """Tarifliche ESt nach § 32a Abs. 1 (Grundtarif), auf volle Euro abgerundet.
    None, wenn für das Jahr kein Tarif hinterlegt ist."""
    t = TARIF.get(jahr)
    if t is None:
        return None
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


def est_tarif(zve: Decimal, jahr: int, zusammenveranlagung: bool = False) -> Optional[Decimal]:
    """Grund- oder Splittingtarif (§ 32a Abs. 5: 2 × ESt(zvE/2))."""
    if zusammenveranlagung:
        halb = est_grundtarif(D(zve) / 2, jahr)
        return None if halb is None else halb * 2
    return est_grundtarif(D(zve), jahr)


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
