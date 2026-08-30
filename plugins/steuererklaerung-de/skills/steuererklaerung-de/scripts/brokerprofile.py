#!/usr/bin/env python3
"""
brokerprofile.py — Profil-Engine für Broker- und Börsen-Reports.

Statt für jeden Broker ein eigenes Skript zu schreiben, beschreibt ein *Profil*
(scripts/profiles/<id>.json) deklarativ, wie ein Report gelesen wird. Diese Datei
lädt Profile, erkennt anhand des Reporttextes das passende, wendet es an und prüft
das Ergebnis gegen die Summen, die der Report selbst ausweist.

Verbindliche Spezifikation: references/broker-profile.md

Drei Dinge erzwingt die Engine, weil genau sie in handgeschriebenen Parsern fehlten:
  * Erkennung   — welcher Report ist das überhaupt (muss/darf_nicht/punkte)
  * Pflichtfelder — was muss jede Tabellenzeile liefern
  * Summenabgleich — stimmt das Ergebnis mit dem Report überein
Ein Profil ohne eines davon (oder mit einem literalen TODO) wird abgelehnt.

Grundregel wie in steuerlib: **unlesbare Werte werfen, sie werden nie still 0.**

KEINE Steuerberatung.
"""

from __future__ import annotations

import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steuerlib as sl  # noqa: E402

D = Decimal

PROFIL_VERZEICHNIS = Path(__file__).resolve().parent / "profiles"

ERGEBNIS_ARTEN = {"krypto_vorberechnet", "krypto_transaktionen", "kap"}
EINGABE_ARTEN = {"pdf", "csv"}
STATUS_ARTEN = {"geprueft", "ungeprueft"}

# Kanonisches Transaktionsschema (siehe parse_inputs.py)
KANONISCHE_TX_FELDER = [
    "timestamp", "type", "asset", "amount", "eur_value", "fee_eur",
    "reward_kind", "counter_asset", "counter_amount", "tx_id", "source",
]
ERLAUBTE_TX_TYPEN = {"buy", "sell", "swap", "reward", "deposit", "withdrawal"}


# ─────────────────────────────────────────────────────────────── Regex-Makros ──
# Profile sollen lesbar bleiben: {NUM} statt der immer gleichen Betragswüste.
MAKROS = {
    "NUM": r"(?:\(\s*)?[-−–+]?\s*\d[\d.,]*(?:\s*\))?-?",
    "DT": r"\d{1,4}[./-]\d{1,2}[./-]\d{2,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?",
    "VOR": r"[^\d\-−–(+\n]*",        # Füllzeichen zwischen Label und Betrag
    "HALTE": r"Kurzfristig|Langfristig|Short[- ]?term|Long[- ]?term",
}
_MAKRO_RE = re.compile(r"\{([A-Z_]+)\}")


def entfalte(muster: str) -> str:
    """{NUM} & Co. einsetzen; unbekannte {...} bleiben unangetastet (Quantoren!)."""
    return _MAKRO_RE.sub(lambda m: MAKROS.get(m.group(1), m.group(0)), muster)


def _kompiliere(muster: str, *, feld: str = "") -> re.Pattern:
    try:
        return re.compile(entfalte(muster))
    except re.error as e:
        raise sl.ParseError(f"Ungültiger regulärer Ausdruck in {feld or 'Profil'}: "
                            f"{muster!r} ({e})")


def _liste(x) -> list:
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


# ───────────────────────────────────────────────────────────────────── Profil ──
class Profil:
    """Ein geladenes Profil. Attribute heißen wie die Felder im Spec."""

    def __init__(self, daten: dict, pfad: Optional[str] = None):
        self.roh = dict(daten)
        self.pfad = pfad
        self.id = daten.get("id") or ""
        self.label = daten.get("label") or self.id
        self.quelle = daten.get("quelle") or self.label
        self.eingabe = daten.get("eingabe") or "pdf"
        self.ergebnis = daten.get("ergebnis") or ""
        self.erkennung = daten.get("erkennung") or {}
        self.notation = daten.get("notation") or "auto"
        self.datum = daten.get("datum") or "auto"
        self.tabellen = daten.get("tabellen") or []
        self.bereiche = daten.get("bereiche") or {}
        self.werte = daten.get("werte") or []
        self.werte_regeln = daten.get("werte_regeln") or {}
        self.summen = daten.get("summen") or []
        self.elster = daten.get("elster") or []
        self.kennzahlen = daten.get("kennzahlen") or {}
        self.csv = daten.get("csv") or {}
        self.jahr = daten.get("jahr") or {}
        self.zusatz = daten.get("zusatz") or {}
        self.hinweise = daten.get("hinweise") or []
        self.status = daten.get("status") or "geprueft"
        self.geprueft_am = daten.get("geprueft_am")
        self.fixture = daten.get("fixture")

    # -- Bequemlichkeiten ----------------------------------------------------
    @property
    def punkte(self) -> int:
        try:
            return int(self.erkennung.get("punkte", 0))
        except (TypeError, ValueError):
            return 0

    @property
    def ungeprueft(self) -> bool:
        return self.status == "ungeprueft"

    def __repr__(self) -> str:  # pragma: no cover - Diagnose
        return f"<Profil {self.id} ({self.ergebnis})>"

    def kurzzeile(self) -> str:
        gp = self.geprueft_am or "nie"
        st = "UNGEPRÜFT" if self.ungeprueft else "geprüft"
        return (f"{self.id:<16} {self.ergebnis:<22} {self.eingabe:<4} "
                f"{gp:<12} {st:<10} {self.label}")


def lade_profile(verzeichnis=None) -> list[Profil]:
    """Alle Profile aus scripts/profiles (oder einem anderen Verzeichnis)."""
    ordner = Path(verzeichnis or PROFIL_VERZEICHNIS)
    if not ordner.is_dir():
        return []
    profile: list[Profil] = []
    for pfad in sorted(ordner.glob("*.json")):
        try:
            with open(pfad, encoding="utf-8") as f:
                daten = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise sl.ParseError(f"Profil {pfad} nicht lesbar: {e}")
        daten.setdefault("id", pfad.stem)
        if daten["id"] != pfad.stem:
            raise sl.ParseError(
                f"Profil {pfad}: id {daten['id']!r} passt nicht zum Dateinamen "
                f"{pfad.stem!r} — die id ist der Dateiname ohne .json.")
        profile.append(Profil(daten, str(pfad)))
    return profile


def profil_nach_id(profil_id: str, verzeichnis=None) -> Profil:
    for p in lade_profile(verzeichnis):
        if p.id == profil_id:
            return p
    raise sl.ParseError(f"Kein Profil mit der id {profil_id!r} in "
                        f"{Path(verzeichnis or PROFIL_VERZEICHNIS)}.")


# ───────────────────────────────────────────────────────────── Profilprüfung ──
def _enthaelt_todo(x) -> bool:
    if isinstance(x, str):
        return "TODO" in x
    if isinstance(x, dict):
        return any(_enthaelt_todo(k) or _enthaelt_todo(v) for k, v in x.items())
    if isinstance(x, (list, tuple)):
        return any(_enthaelt_todo(v) for v in x)
    return False


def _gruppen(muster: str) -> set[str]:
    try:
        return set(re.compile(entfalte(muster)).groupindex)
    except re.error:
        return set()


# Anlage KAP: die Zeilen 20-25 sind "In den Zeilen 18 und 19 enthaltene …" —
# also Teilmengen (davon-Zeilen) der Bruttozeilen, keine eigenen Summanden. Wer
# beides in einen Topf addiert, zählt dieselben Erträge doppelt und vergleicht
# anschließend eine Zahl, die in keiner Rechnung vorkommt.
KAP_BRUTTO_ZEILEN = {"7", "18", "19"}
KAP_DAVON_ZEILEN = {"20", "21", "22", "23", "24", "25"}


def _kap_aggregate(profil) -> dict:
    """Ziel-Pfad -> Menge der Anlage-KAP-Zeilen, die dort aufsummiert werden."""
    aggregate: dict[str, set] = {}

    def zeilen(pfade):
        return {p.split(".", 1)[1] for p in pfade if str(p).startswith("kap_zeilen.")}

    for name, spec in (profil.kennzahlen or {}).items():
        quellen = spec.get("quellen") if isinstance(spec, dict) else spec
        ziel = name if "." in name else f"kennzahlen.{name}"
        aggregate.setdefault(ziel, set()).update(zeilen(_liste(quellen)))
    for w in profil.werte:
        for ziel in _liste(w.get("summiere_in")):
            aggregate.setdefault(ziel, set()).update(zeilen(_liste(w.get("pfad"))))
    return aggregate


# Felder, die die Engine für diese Ausgabeschemata immer erzeugt (_ableiten_paragraph).
_P23_FELDER = ("freigrenze_angewendet", "anzahl_veraeusserungen", "gewinn_eur",
               "verlust_eur", "netto_ergebnis_eur", "verlustvortrag_eur",
               "steuerfrei_langfristig_eur")
_P22_FELDER = _P23_FELDER + ("summe_zufluesse_eur",)
_CSV_BASIS = ("csv_datenzeilen", "zugeordnete_zeilen", "uebersprungene_zeilen",
              "verarbeitete_zeilen", "nicht_zugeordnete_zeilen", "anzahl_transaktionen")


def _flache_pfade(d, praefix: str = "") -> set[str]:
    pfade: set[str] = set()
    if not isinstance(d, dict):
        return pfade
    for k, v in d.items():
        pfad = f"{praefix}{k}"
        pfade.add(pfad)
        if isinstance(v, dict):
            pfade |= _flache_pfade(v, pfad + ".")
    return pfade


def erzeugbare_pfade(profil) -> set[str]:
    """Alle Ergebnis-Pfade, die dieses Profil überhaupt füllen kann.

    Grundlage für die Prüfung von `summen.vergleich`: ein Tippfehler dort ergäbe
    sonst einen Abgleich von 0,00 € gegen 0,00 € — eine grüne Meldung über eine
    Prüfung, die nie stattgefunden hat.
    """
    if isinstance(profil, dict):
        profil = Profil(profil)
    pfade = {"steuerjahr", "tax_year", "quelle", "profil", "zahlennotation",
             "profil_status", "profil_geprueft_am"}
    if profil.ergebnis in ("krypto_vorberechnet", "kap"):
        pfade |= {f"paragraph_23.{f}" for f in _P23_FELDER}
        pfade |= {f"paragraph_22_nr3.{f}" for f in _P22_FELDER}
        if any((t.get("rolle") or t.get("name")) == "veraeusserungen"
               for t in profil.tabellen):
            pfade |= {"summen_basis.veraeusserungen_gewinn_gesamt",
                      "summen_basis.anzahl_veraeusserungen"}
    if profil.ergebnis == "krypto_transaktionen":
        pfade |= {"anzahl_transaktionen"}
        pfade |= {f"summen_basis.{k}" for k in _CSV_BASIS}
    for w in profil.werte:
        pfade |= set(_liste(w.get("pfad")))
        pfade |= set(_liste(w.get("summiere_in")))
    for name in (profil.kennzahlen or {}):
        pfade.add(name if "." in name else f"kennzahlen.{name}")
    if profil.ergebnis == "kap":
        pfade |= {"kap_zeilen", "so_zeilen", "kennzahlen"}
    pfade |= _flache_pfade(profil.zusatz)
    return pfade


def pruefe_profil(profil) -> list[str]:
    """Strukturfehler und TODO-Marker. Leere Liste = benutzbar.

    Ein Profil ohne Erkennung, ohne Pflichtfelder oder ohne Summenabgleich ist
    unfertig — genau die drei Dinge, die ein Ad-hoc-Parser gern vergisst.
    """
    if isinstance(profil, dict):
        profil = Profil(profil)
    f: list[str] = []
    p = profil

    if not p.id:
        f.append("id fehlt.")
    if not p.label:
        f.append("label fehlt.")
    if p.eingabe not in EINGABE_ARTEN:
        f.append(f"eingabe {p.eingabe!r} unbekannt (erlaubt: {sorted(EINGABE_ARTEN)}).")
    if p.ergebnis not in ERGEBNIS_ARTEN:
        f.append(f"ergebnis {p.ergebnis!r} unbekannt (erlaubt: {sorted(ERGEBNIS_ARTEN)}).")
    if p.status not in STATUS_ARTEN:
        f.append(f"status {p.status!r} unbekannt (erlaubt: {sorted(STATUS_ARTEN)}).")
    if p.status == "geprueft" and not p.geprueft_am:
        f.append("status 'geprueft', aber geprueft_am fehlt.")
    if not p.fixture:
        f.append("fixture fehlt — jedes Profil braucht einen anonymisierten "
                 "Testausschnitt (tests/fixtures/<id>.txt).")

    # -- Erkennung -----------------------------------------------------------
    muss = _liste(p.erkennung.get("muss"))
    if not muss:
        f.append("erkennung.muss ist leer — das Profil würde auf jeden Report passen.")
    for m in muss + _liste(p.erkennung.get("darf_nicht")):
        try:
            _kompiliere(m, feld="erkennung")
        except sl.ParseError as e:
            f.append(str(e))

    # -- Tabellen ------------------------------------------------------------
    for t in p.tabellen:
        name = t.get("name") or "<ohne name>"
        for schluessel in ("start", "zeile"):
            if not t.get(schluessel):
                f.append(f"Tabelle {name}: {schluessel} fehlt.")
        felder = t.get("felder") or {}
        if not felder:
            f.append(f"Tabelle {name}: felder fehlt (Gruppen -> kanonische Namen).")
        pflicht = t.get("pflicht") or []
        if not pflicht:
            f.append(f"Tabelle {name}: pflicht fehlt — ohne Pflichtfelder kann eine "
                     f"halb gelesene Zeile unbemerkt durchrutschen.")
        for feld in pflicht:
            if feld not in felder:
                f.append(f"Tabelle {name}: Pflichtfeld {feld!r} ist in 'felder' nicht "
                         f"zugeordnet.")
        for schluessel in ("start", "ende", "zeile"):
            if t.get(schluessel):
                try:
                    _kompiliere(t[schluessel], feld=f"Tabelle {name}.{schluessel}")
                except sl.ParseError as e:
                    f.append(str(e))
        if t.get("zeile"):
            vorhanden = _gruppen(t["zeile"])
            for kanon, gruppe in felder.items():
                if gruppe not in vorhanden:
                    f.append(f"Tabelle {name}: Feld {kanon!r} verweist auf die "
                             f"benannte Gruppe {gruppe!r}, die es in 'zeile' nicht gibt.")

    # -- Bereiche ------------------------------------------------------------
    for name, spec in (p.bereiche or {}).items():
        if not spec.get("start"):
            f.append(f"Bereich {name}: start fehlt.")
        for m in [spec.get("start")] + _liste(spec.get("ende")):
            if not m:
                continue
            try:
                _kompiliere(m, feld=f"Bereich {name}")
            except sl.ParseError as e:
                f.append(str(e))

    # -- Einzelwerte ---------------------------------------------------------
    for w in p.werte:
        ref = w.get("bereich")
        if isinstance(ref, str) and ref not in (p.bereiche or {}):
            f.append(f"werte-Eintrag {w.get('pfad')!r} verweist auf den unbekannten "
                     f"Bereich {ref!r}.")
        if not w.get("pfad"):
            f.append(f"werte-Eintrag ohne pfad: {w!r}")
        if not w.get("muster"):
            f.append(f"werte-Eintrag {w.get('pfad')!r} ohne muster.")
        for m in _liste(w.get("muster")):
            try:
                _kompiliere(m, feld=f"werte {w.get('pfad')}")
            except sl.ParseError as e:
                f.append(str(e))

    # -- CSV -----------------------------------------------------------------
    if p.eingabe == "csv":
        if not p.csv:
            f.append("eingabe 'csv', aber kein csv-Block.")
        elif not p.csv.get("normalisierer"):
            spalten = p.csv.get("spalten") or {}
            if not spalten:
                f.append("csv.spalten fehlt (kanonisches Feld -> Spaltenüberschrift).")
            if not p.csv.get("pflicht"):
                f.append("csv.pflicht fehlt — ohne Pflichtfelder wird eine leere "
                         "Zeile zur Transaktion.")
            for feld in p.csv.get("pflicht") or []:
                if feld not in spalten:
                    f.append(f"csv: Pflichtfeld {feld!r} ist in csv.spalten nicht "
                             f"zugeordnet.")
            for feld, quelle in spalten.items():
                if feld not in KANONISCHE_TX_FELDER:
                    f.append(f"csv.spalten: {feld!r} ist kein kanonisches "
                             f"Transaktionsfeld ({', '.join(KANONISCHE_TX_FELDER)}).")
                if (isinstance(quelle, (list, tuple))
                        and feld not in ("amount", "counter_amount", "eur_value",
                                         "fee_eur")):
                    f.append(f"csv.spalten: {feld!r} ist kein Betragsfeld — mehrere "
                             f"Spalten können nur bei Beträgen addiert werden.")
            for check in _liste(p.csv.get("pruefe_spalte")):
                if not check.get("spalte") or check.get("erwartet") in (None, ""):
                    f.append(f"csv.pruefe_spalte braucht 'spalte' und 'erwartet': "
                             f"{check!r}")

    # -- Datenquelle überhaupt vorhanden -------------------------------------
    if not p.tabellen and not p.csv:
        mindestens = p.werte_regeln.get("mindestens", 0)
        if not p.werte:
            f.append("Profil liefert weder tabellen noch werte noch csv.")
        elif not mindestens:
            f.append("Profil ohne Tabellen: werte_regeln.mindestens muss angeben, wie "
                     "viele Einzelwerte mindestens gefunden werden müssen — sonst ist "
                     "ein Ergebnis aus lauter Nullen von einem echten Null-Report nicht "
                     "zu unterscheiden.")

    # -- Summenabgleich ------------------------------------------------------
    if not p.summen:
        f.append("summen fehlt — ohne Abgleich gegen die im Report ausgewiesenen "
                 "Summen bleibt ein Zeilenverlust unbemerkt.")
    moeglich = erzeugbare_pfade(p)
    for s in p.summen:
        if not s.get("vergleich"):
            f.append(f"summen-Eintrag ohne vergleich: {s!r}")
        for schluessel in ("vergleich", "quelle_pfad"):
            pfad = s.get(schluessel)
            if pfad and pfad not in moeglich:
                nah = sorted(x for x in moeglich
                             if x.split(".")[0] == str(pfad).split(".")[0])
                f.append(
                    f"summen-Eintrag {s.get('label') or pfad!r}: {schluessel} "
                    f"{pfad!r} kann von diesem Profil nie gefüllt werden — der "
                    f"Abgleich vergliche sonst 0,00 € gegen 0,00 € und meldete eine "
                    f"Prüfung, die nicht stattgefunden hat. Mögliche Pfade"
                    + (f" unterhalb '{str(pfad).split('.')[0]}': "
                       + ", ".join(nah) if nah
                       else ": " + ", ".join(sorted(moeglich)[:12]) + " …"))
        art = s.get("art", "betrag")
        if art not in ("betrag", "anzahl", "zeilen"):
            f.append(f"summen-Eintrag {s.get('label')!r}: art {art!r} unbekannt "
                     f"(betrag|anzahl|zeilen).")
        if art != "zeilen" and not s.get("muster"):
            f.append(f"summen-Eintrag {s.get('label')!r} ohne muster.")
        for m in _liste(s.get("muster")):
            try:
                _kompiliere(m, feld=f"summen {s.get('label')}")
            except sl.ParseError as e:
                f.append(str(e))

    # -- Kennzahlen: Quellpfade müssen füllbar sein ---------------------------
    for name, spec in (p.kennzahlen or {}).items():
        quellen = spec.get("quellen") if isinstance(spec, dict) else spec
        if isinstance(spec, dict) and spec.get("vorzeichen") not in (
                None, "positiv", "negativ"):
            f.append(f"kennzahlen.{name}: vorzeichen {spec['vorzeichen']!r} unbekannt "
                     f"(positiv|negativ).")
        if not quellen:
            f.append(f"kennzahlen.{name}: keine Quellen angegeben.")
        for q in _liste(quellen):
            if q not in moeglich:
                f.append(f"kennzahlen.{name}: Quellpfad {q!r} kann von diesem Profil "
                         f"nie gefüllt werden — die Kennzahl bliebe still 0,00.")

    # -- Anlage KAP: Brutto- und davon-Zeilen nie in einen Topf --------------
    if p.ergebnis == "kap":
        for ziel, zeilen in _kap_aggregate(p).items():
            brutto = sorted(zeilen & KAP_BRUTTO_ZEILEN)
            davon = sorted(zeilen & KAP_DAVON_ZEILEN)
            if brutto and davon:
                f.append(
                    f"{ziel!r} addiert die Bruttozeile(n) {', '.join(brutto)} mit den "
                    f"davon-Zeile(n) {', '.join(davon)}. Die Anlage-KAP-Zeilen 20-25 "
                    f"sind 'In den Zeilen 18 und 19 enthaltene …', also Teilmengen — "
                    f"ihre Summe mit der Bruttozeile zählt dieselben Erträge doppelt "
                    f"und ergibt einen Wert, den keine Steuerberechnung verwendet.")

    if _enthaelt_todo(p.roh):
        f.append("Profil enthält TODO — es ist ein Gerüst, keine fertige Anbindung. "
                 "Vor der Verwendung die markierten Stellen ausfüllen.")
    return f


def _sicherstellen_benutzbar(profil: Profil) -> None:
    probleme = pruefe_profil(profil)
    if probleme:
        raise sl.ParseError(
            f"Profil {profil.id!r} ist unfertig und wird abgelehnt:\n  "
            + "\n  ".join(probleme))


# ──────────────────────────────────────────────────────────────── Erkennung ───
def _bewerte(profil: Profil, text: str) -> tuple[bool, str]:
    """(passt, Begründung)."""
    for m in _liste(profil.erkennung.get("muss")):
        if not re.search(entfalte(m), text, re.I | re.M):
            return False, f"Muster fehlt: {m!r}"
    for m in _liste(profil.erkennung.get("darf_nicht")):
        if re.search(entfalte(m), text, re.I | re.M):
            return False, f"Ausschlussmuster gefunden: {m!r}"
    return True, "alle Erkennungsmuster gefunden"


def passt(profil, text: str) -> bool:
    """Greift dieses Profil auf diesen Reporttext?"""
    if isinstance(profil, dict):
        profil = Profil(profil)
    return _bewerte(profil, text)[0]


def kandidaten(text: str, profile=None) -> list[Profil]:
    """Alle passenden Profile, bestes zuerst."""
    profile = list(profile) if profile is not None else lade_profile()
    treffer = [p for p in profile if _bewerte(p, text)[0]]
    return sorted(treffer, key=lambda p: (-p.punkte, p.id))


def erkennungs_bericht(text: str, profile=None) -> list[str]:
    """Warum welches Profil (nicht) passt — für Fehlermeldungen."""
    profile = list(profile) if profile is not None else lade_profile()
    zeilen = []
    for p in sorted(profile, key=lambda x: x.id):
        passt, grund = _bewerte(p, text)
        zeilen.append(f"{'PASST ' if passt else 'nein  '} {p.id:<16} "
                      f"(punkte {p.punkte}) — {grund}")
    return zeilen


def erkenne(text: str, profile=None) -> Optional[Profil]:
    """Bestes Profil nach erkennung.punkte.

    None, wenn keines passt. Bei Gleichstand wird geworfen statt geraten —
    ein zufällig gewähltes Profil liest fremde Spalten und meldet Erfolg.
    """
    passende = kandidaten(text, profile)
    if not passende:
        return None
    beste = passende[0].punkte
    spitze = [p for p in passende if p.punkte == beste]
    if len(spitze) > 1:
        raise sl.ParseError(
            "Mehrere Profile passen mit derselben Punktzahl "
            f"({beste}) — Erkennung ist nicht eindeutig:\n  "
            + "\n  ".join(f"{p.id} — {p.label}" for p in spitze)
            + "\n→ Profil mit --profil ID erzwingen oder die Erkennungsmuster "
              "(erkennung.muss/darf_nicht/punkte) schärfen.")
    return spitze[0]


# ───────────────────────────────────────────────────────────── Textgewinnung ──
def text_aus_datei(pfad) -> str:
    """Volltext einer PDF (pdfplumber, Fallback PyMuPDF) oder einer Text/CSV-Datei.

    Fallback nur bei *fehlendem* pdfplumber — echte PDF-Fehler sollen sichtbar
    bleiben und nicht im nächsten Backend verschwinden.
    """
    pfad = str(pfad)
    if pfad.lower().endswith(".pdf"):
        try:
            import pdfplumber
        except ImportError:
            import fitz
            doc = fitz.open(pfad)
            return "\n".join((p.get_text() or "") for p in doc)
        with pdfplumber.open(pfad) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    try:
        with open(pfad, encoding="utf-8-sig", newline="") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(pfad, encoding="latin-1", newline="") as f:
            return f.read()


# ────────────────────────────────────────────────────────────── Werkzeuge ─────
def _setze(ziel: dict, pfad: str, wert) -> None:
    teile = str(pfad).split(".")
    d = ziel
    for t in teile[:-1]:
        naechst = d.get(t)
        if not isinstance(naechst, dict):
            naechst = {}
            d[t] = naechst
        d = naechst
    d[teile[-1]] = wert


def _hole(quelle: dict, pfad: str, default=None):
    d = quelle
    for t in str(pfad).split("."):
        if not isinstance(d, dict) or t not in d:
            return default
        d = d[t]
    return d


def _menge(tok, hint: Optional[str]) -> Decimal:
    """Coin-Menge: '0,00047383' ist auch in einem DE-Report keine Tausenderzahl."""
    t = str(tok).strip()
    if re.match(r"^-?0[.,]", t):
        return sl.to_decimal(t)
    return sl.to_decimal(t, locale_hint=hint)


_BETRAG_KERN = re.compile(r"^\d[\d.\s]*[.,]\d{2}$")


def sieht_aus_wie_betrag(tok) -> bool:
    """True nur bei einem echten Betrag mit zwei Nachkommastellen.

    Verhindert, dass eine Seitenzahl aus derselben Zeile als Wert übernommen wird.
    """
    if tok is None:
        return False
    s = str(tok).strip()
    for uni in ("−", "–", "—"):
        s = s.replace(uni, "-")
    s = re.sub(r"\s+", "", s)
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    if s.endswith("-"):
        s = s[:-1]
    if s[:1] in ("-", "+"):
        s = s[1:]
    return bool(_BETRAG_KERN.match(s))


def _feldtyp(tab: dict, kanon: str) -> str:
    typen = tab.get("typen") or {}
    if kanon in typen:
        return typen[kanon]
    if kanon in ("disposal_date", "acquisition_date", "timestamp", "datum"):
        return "datum"
    if kanon in ("amount", "counter_amount", "menge"):
        return "menge"
    if kanon.endswith("_eur"):
        return "betrag"
    return "text"


def _wandle(wert, typ: str, hint, dayfirst: bool, wo: str):
    if wert is None:
        return None
    roh = str(wert).strip()
    if roh == "":
        return None
    try:
        if typ == "betrag":
            return sl.to_decimal(roh, locale_hint=hint)
        if typ == "menge":
            return _menge(roh, hint)
        if typ == "ganzzahl":
            return int(sl.to_decimal(roh, locale_hint=hint))
        if typ == "datum":
            return sl.parse_datetime(roh, dayfirst=dayfirst)
    except sl.ParseError as e:
        raise sl.ParseError(f"{wo}: {e}")
    return roh


class _AnzahlAbgleich(sl.Abgleich):
    """Wie Abgleich, nur ohne Euro-Formatierung (Stückzahlen)."""

    def __str__(self) -> str:
        if self.ausgewiesen is None:
            return (f"{self.label}: geparst {self.geparst} "
                    f"(kein Vergleichswert im Report gefunden)")
        return (f"{self.label}: geparst {self.geparst} vs. Report "
                f"{self.ausgewiesen}")


# ────────────────────────────────────────────────────────────────── Tabellen ──
def _tabelle_lesen(tab: dict, text: str, hint, dayfirst: bool):
    """(zeilen, nicht_zugeordnete_zeilen) für eine Tabelle.

    Nicht leere Zeilen zwischen start und ende, die auf kein `zeile`-Muster
    passen, werden gezählt — sie sind das Frühwarnsignal für ein Layout, das
    sich geändert hat.
    """
    name = tab.get("name") or "?"
    start = _kompiliere(tab["start"], feld=f"{name}.start")
    ende = _kompiliere(tab["ende"], feld=f"{name}.ende") if tab.get("ende") else None
    zeile = _kompiliere(tab["zeile"], feld=f"{name}.zeile")
    ignoriere = [_kompiliere(m, feld=f"{name}.ignoriere") for m in _liste(tab.get("ignoriere"))]
    melden = tab.get("melde_nicht_zugeordnet", True)
    felder = tab.get("felder") or {}
    pflicht = tab.get("pflicht") or []

    zeilen: list[dict] = []
    unmatched: list[str] = []
    in_tab = False
    for roh in text.splitlines():
        line = roh.strip()
        if not line:
            continue
        if not in_tab:
            if start.search(line):
                in_tab = True
            continue
        if ende is not None and ende.search(line):
            in_tab = False
            continue
        if any(p.search(line) for p in ignoriere):
            continue
        m = zeile.search(line) if tab.get("suche") else zeile.match(line)
        if not m:
            if melden:
                unmatched.append(line)
            continue
        gd = m.groupdict()
        satz: dict = {"_zeile": line}
        for kanon, gruppe in felder.items():
            wo = f"Tabelle '{name}', Feld '{kanon}' in {line[:80]!r}"
            satz[kanon] = _wandle(gd.get(gruppe), _feldtyp(tab, kanon), hint, dayfirst, wo)
        fehlend = [k for k in pflicht if satz.get(k) in (None, "")]
        if fehlend:
            raise sl.ParseError(
                f"Tabelle '{name}': Pflichtfeld(er) {', '.join(fehlend)} fehlen in "
                f"der Zeile {line[:120]!r}.\n"
                "→ Report-Layout prüfen; eine halb gelesene Zeile wird NICHT als "
                "0 € übernommen.")
        zeilen.append(satz)
    return zeilen, unmatched


def _label_langfristig(tab: dict, satz: dict) -> Optional[bool]:
    """Einstufung laut Haltedauer-Spalte des Reports (None, wenn es keine gibt)."""
    spec = tab.get("langfristig")
    if not spec:
        return None
    wert = satz.get(spec.get("feld"))
    if wert is None:
        return None
    return bool(re.search(entfalte(spec.get("muster", "")), str(wert), re.I))


def _langfristig(tab: dict, satz: dict):
    """(angewandt, laut_report, gesetzlich, Konfliktmeldung).

    Maßgeblich ist die Jahresfrist nach § 23 Abs. 1 Nr. 2 EStG i. V. m. § 108 AO /
    § 188 Abs. 2 BGB, gerechnet aus Anschaffungs- und Veräußerungsdatum. Weicht die
    Einstufung des Anbieters davon ab, gilt **das Gesetz**: die Frist ist aus zwei
    Pflichtfeldern eindeutig bestimmbar, während die Meinung eines ausländischen
    Werkzeugs über eine deutsche Frist kein besserer Beleg ist als die Frist selbst.
    Die Fehlerrichtung entscheidet: ein falsches "Langfristig" verkürzt die Steuer,
    und das trägt der Steuerpflichtige. Die Angabe des Reports bleibt zur
    Nachvollziehbarkeit im Ergebnis stehen und der Widerspruch wird gemeldet.
    """
    a, v = satz.get("acquisition_date"), satz.get("disposal_date")
    gesetzlich = sl.haltefrist_erfuellt(a, v) if (a is not None and v is not None) else None
    laut_report = _label_langfristig(tab, satz)

    if gesetzlich is None:
        return laut_report, laut_report, None, None
    if laut_report is None or laut_report == gesetzlich:
        return gesetzlich, laut_report, gesetzlich, None

    def _text(lang):
        return "steuerfrei (> 1 Jahr gehalten)" if lang else "steuerpflichtig (§ 23)"

    fristende = sl.jahresfrist_ende(a.date() if hasattr(a, "date") else a)
    hinweis = (
        f"HALTEFRIST-KONFLIKT: {satz.get('asset') or '?'} — Anschaffung "
        f"{a.date().isoformat()}, Veräußerung {v.date().isoformat()}: der Report "
        f"stuft die Veräußerung als {_text(laut_report)} ein, die Jahresfrist nach "
        f"§ 23 Abs. 1 Nr. 2 EStG i. V. m. § 108 AO / § 188 Abs. 2 BGB endet am "
        f"{fristende.isoformat()} und ergibt {_text(gesetzlich)}. ANGEWENDET wurde "
        f"die gesetzliche Frist ({_text(gesetzlich)}), weil sie aus den beiden "
        f"Datumsangaben eindeutig folgt; die Angabe des Reports steht als "
        f"'holding_period_laut_report' in der Position. → Ein solcher Widerspruch "
        f"deutet darauf hin, dass der GESAMTE Report nach einer 365-Tage-Regel oder "
        f"nach ausländischem Recht gerechnet wurde: dann sind ALLE Zeilen betroffen, "
        f"nicht nur die hier gemeldeten.")
    return gesetzlich, laut_report, gesetzlich, hinweis


def _baue_disposals(tab: dict, zeilen: list[dict], quelle_notiz: str,
                    warnungen: list[str]) -> list[dict]:
    out = []
    konflikte = 0
    for satz in zeilen:
        sd, bd = satz.get("disposal_date"), satz.get("acquisition_date")
        lang, laut_report, _gesetzlich, konflikt = _langfristig(tab, satz)
        if lang is None:
            raise sl.ParseError(
                f"Tabelle '{tab.get('name')}': Haltedauer der Zeile "
                f"{satz['_zeile'][:100]!r} nicht bestimmbar (weder Haltedauer-Spalte "
                "noch Anschaffungs-/Veräußerungsdatum).")
        if konflikt:
            konflikte += 1
            warnungen.append(konflikt)
        notiz = (satz.get("note") or "").strip()
        if quelle_notiz:
            notiz = (notiz + " " if notiz else "") + quelle_notiz
        eintrag = {
            "asset": (satz.get("asset") or "").strip(),
            "disposal_date": sd.date().isoformat() if sd else None,
            "acquisition_date": bd.date().isoformat() if bd else None,
            "amount": str(satz["amount"]) if satz.get("amount") is not None else None,
            "proceeds_eur": str(sl.q2(satz["proceeds_eur"])) if satz.get("proceeds_eur") is not None else "0.00",
            "cost_basis_eur": str(sl.q2(satz["cost_basis_eur"])) if satz.get("cost_basis_eur") is not None else "0.00",
            "fee_eur": str(sl.q2(satz["fee_eur"])) if satz.get("fee_eur") is not None else "0.00",
            "gain_eur": str(sl.q2(satz["gain_eur"])),
            "held_days": (sd - bd).days if (sd and bd) else None,
            "holding_period_met": lang,       # gesetzliche Frist, s. _langfristig
            "taxable": not lang,
            "note": notiz,
        }
        if laut_report is not None:
            eintrag["holding_period_laut_report"] = laut_report
        if konflikt:
            # Prüfspur an der Position selbst — sichtbar auch dann, wenn nur die
            # Einzelveräußerungen weiterverarbeitet werden.
            eintrag["haltefrist_konflikt"] = True
        out.append(eintrag)
    if konflikte:
        warnungen.append(
            f"{konflikte} von {len(out)} Veräußerung(en): die Einstufung des Reports "
            f"widerspricht der gesetzlichen Jahresfrist. Angewendet wurde die "
            f"gesetzliche Frist (§ 23 Abs. 1 Nr. 2 EStG i. V. m. § 108 AO / § 188 "
            f"Abs. 2 BGB). Ein einziger solcher Widerspruch stellt den ganzen Report "
            f"in Frage — vermutlich wurde er nach einer 365-Tage-Regel oder nach "
            f"ausländischem Recht erzeugt; dann sind ALLE Zeilen zu prüfen.")
    return out


def _baue_transaktionen(tab: dict, zeilen: list[dict], quelle: str) -> list[dict]:
    out = []
    for satz in zeilen:
        tx = {}
        for feld in KANONISCHE_TX_FELDER:
            wert = satz.get(feld)
            if isinstance(wert, Decimal):
                wert = str(wert)
            elif hasattr(wert, "isoformat"):
                wert = wert.isoformat(sep=" ")
            tx[feld] = wert
        tx["source"] = tx.get("source") or quelle
        out.append(tx)
    return out


# ─────────────────────────────────────────────────────── Datumsformat-Prüfung ─
def _widersprueche(roh, dayfirst: bool) -> int:
    n = 0
    for verkauf, erwerb, langfristig in roh:
        try:
            sd = sl.parse_datetime(verkauf, dayfirst=dayfirst)
            bd = sl.parse_datetime(erwerb, dayfirst=dayfirst)
        except sl.ParseError:
            n += 1
            continue
        tage = (sd - bd).days
        if tage < 0 or (langfristig and tage < 364) or (not langfristig and tage > 367):
            n += 1
    return n


def _pruefe_datumsformat(roh) -> None:
    """TT/MM vs. MM/TT: ein US-Export verschiebt sonst lautlos Zeitraum und Jahr."""
    mehrdeutig = [s for s, b, _ in roh if sl.date_ambiguous(s) or sl.date_ambiguous(b)]
    if not mehrdeutig:
        return
    w_de, w_en = _widersprueche(roh, True), _widersprueche(roh, False)
    jahre_de = {sl.parse_datetime(s, dayfirst=True).year for s, _b, _l in roh}
    jahre_en = {sl.parse_datetime(s, dayfirst=False).year for s, _b, _l in roh}
    if w_de == w_en and jahre_de == jahre_en:
        return
    grund = (f"unter TT/MM/JJJJ {w_de} widersprüchliche Zeile(n), unter MM/TT/JJJJ {w_en}"
             if w_de != w_en else
             "die betroffenen Veräußerungen fallen je nach Auslegung in verschiedene "
             "Steuerjahre")
    raise sl.ParseError(
        f"Datumsformat des Reports ist nicht eindeutig (z. B. {mehrdeutig[0]}): {grund}.\n"
        "→ Format im Original prüfen und mit --dateformat de (TT/MM/JJJJ) bzw. "
        "--dateformat en (MM/TT/JJJJ) erzwingen.")


# ────────────────────────────────────────────────────────────── Einzelwerte ───
class Bereiche:
    """Benannte Textabschnitte (profil.bereiche) mit Zwischenspeicher.

    Ohne Eingrenzung matcht ein Label wie 'Cost' irgendwo im Dokument und ein
    deutschsprachiger Report liefert lautlos lauter Nullen.
    """

    def __init__(self, profil, text: str, warnungen: list[str]):
        self.defs = getattr(profil, "bereiche", {}) or {}
        self.text = text
        self.warnungen = warnungen
        self._cache: dict[str, str] = {}
        self._gemeldet: set[str] = set()

    def hole(self, ref) -> str:
        if not ref:
            return self.text
        if isinstance(ref, str):
            name, spec = ref, self.defs.get(ref)
            if spec is None:
                raise sl.ParseError(f"Unbekannter Bereich {ref!r} — in profil.bereiche "
                                    f"definieren.")
            if name in self._cache:
                return self._cache[name]
        else:
            name, spec = None, ref
        ausschnitt = self._schneide(spec, name)
        if name:
            self._cache[name] = ausschnitt
        return ausschnitt

    def _schneide(self, spec: dict, name) -> str:
        m = re.search(entfalte(spec["start"]), self.text, re.I | re.M)
        if not m:
            meldung = spec.get("warnung_wenn_fehlt")
            if meldung and name not in self._gemeldet:
                self.warnungen.append(meldung)
                self._gemeldet.add(name)
            return ""
        rest = self.text[m.end():]
        enden = []
        for pat in _liste(spec.get("ende")):
            e = re.search(entfalte(pat), rest, re.I | re.M)
            if e:
                enden.append(e.start())
        return rest[:min(enden)] if enden else rest


def _suche_wert(w: dict, text: str, hint, bereiche: Optional[Bereiche] = None):
    """(wert, gefunden). Erste Alternative gewinnt, die auch die Form erfüllt."""
    quelle = bereiche.hole(w.get("bereich")) if bereiche else text
    if w.get("flach"):
        quelle = re.sub(r"\s+", " ", quelle)
    typ = w.get("typ", "betrag")
    for muster in _liste(w.get("muster")):
        for m in re.finditer(entfalte(muster), quelle, re.I | re.M):
            roh = m.group(1) if m.lastindex else m.group(0)
            if roh is None:
                continue
            if w.get("form") == "betrag2" and not sieht_aus_wie_betrag(roh):
                continue
            if typ == "text":
                return roh.strip(), True
            try:
                if typ == "ganzzahl":
                    return int(sl.to_decimal(roh, locale_hint=hint)), True
                if typ == "menge":
                    return _menge(roh, hint), True
                return sl.to_decimal(roh, locale_hint=hint), True
            except sl.ParseError:
                continue
    return None, False


def _extrahiere_werte(profil: Profil, text: str, hint, bereiche: "Bereiche",
                      warnungen: list[str]):
    """(werte nach pfad, summen nach pfad, anzahl gefundener Zahlen)."""
    werte: dict[str, object] = {}
    summen: dict[str, Decimal] = {}
    zahlen_gefunden = 0
    for w in profil.werte:
        typ = w.get("typ", "betrag")
        wert, gefunden = _suche_wert(w, text, hint, bereiche)
        if not gefunden:
            if w.get("default") is not None:
                wert = (w["default"] if typ == "text"
                        else sl.to_decimal(w["default"]))
            elif w.get("optional", True):
                wert = None
            else:
                warnungen.append(
                    f"Pflichtwert {w['pfad']!r} nicht im Report gefunden — "
                    f"Muster prüfen: {_liste(w.get('muster'))[0]!r}")
                wert = None
        elif typ != "text":
            zahlen_gefunden += 1
        for pfad in _liste(w["pfad"]):
            werte[pfad] = wert
        if wert is not None and typ != "text":
            for ziel in _liste(w.get("summiere_in")):
                summen[ziel] = summen.get(ziel, D("0")) + D(wert)
    return werte, summen, zahlen_gefunden


def _als_ausgabe(wert, typ: str):
    if wert is None:
        return None
    if typ in ("betrag",):
        return str(sl.q2(wert))
    if typ in ("menge",):
        return str(wert)
    if typ == "ganzzahl":
        return int(wert)
    return wert


# ──────────────────────────────────────────────────────────────────── Blöcke ──
_P23_HINWEIS = ("Rohwerte ohne Freigrenze; verlust_eur ist die Summe der negativen "
                "Ergebnisse (negatives Vorzeichen). Die Freigrenze § 23 wendet "
                "build_taxreport.py einmal auf die Summe aller Quellen an.")
_P22_HINWEIS = ("Rohwert ohne Freigrenze (§ 22 Nr. 3 Satz 2: 256 € pro Person und "
                "Jahr, über alle Quellen).")


def _ableiten_paragraph(block: dict, *, hinweis: str, mit_zufluessen: bool) -> None:
    """Fehlende Kennzahlen aus dem Netto ableiten (Vorzeichenkonvention beibehalten)."""
    block.setdefault("freigrenze_angewendet", False)
    netto = sl.to_decimal(block.get("netto_ergebnis_eur") or "0")
    block["netto_ergebnis_eur"] = str(sl.q2(netto))
    block.setdefault("gewinn_eur", str(sl.q2(netto if netto > 0 else D("0"))))
    block.setdefault("verlust_eur", str(sl.q2(netto if netto < 0 else D("0"))))
    block.setdefault("verlustvortrag_eur", str(sl.q2(-netto if netto < 0 else D("0"))))
    block.setdefault("steuerfrei_langfristig_eur", "0.00")
    if mit_zufluessen:
        block.setdefault("summe_zufluesse_eur", str(sl.q2(netto)))
        block.setdefault("ertraege", [])
    block.setdefault("warnungen", [])
    block.setdefault("hinweis", hinweis)


def _tief_mischen(ziel: dict, zusatz: dict) -> None:
    for k, v in zusatz.items():
        if isinstance(v, dict) and isinstance(ziel.get(k), dict):
            _tief_mischen(ziel[k], v)
        else:
            ziel[k] = v


# ────────────────────────────────────────────────────────────────── Anwenden ──
def _jahr_aus_text(profil: Profil, text: str) -> Optional[int]:
    for muster in _liste(profil.jahr.get("muster")):
        m = re.search(entfalte(muster), text, re.I | re.M)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def jahr_aus_text(profil, text: str) -> Optional[int]:
    """Steuerjahr laut Profil (jahr.muster). None, wenn nicht erkennbar."""
    if isinstance(profil, dict):
        profil = Profil(profil)
    return _jahr_aus_text(profil, text)


def _csv_anwenden(profil: Profil, text: str, hint, warnungen: list[str]):
    """CSV-Spaltenmapping -> kanonische Transaktionen. (txs, statistik)."""
    import parse_inputs as pi  # lokal: nur der CSV-Zweig braucht das

    spec = profil.csv
    rows, delim = pi.parse_csv_text(text, spec.get("trennzeichen"))
    if not rows:
        raise sl.ParseError(
            "Keine Datenzeilen gelesen. Trennzeichen/Kodierung prüfen "
            "(csv.trennzeichen im Profil setzen).")
    if profil.notation in ("de", "en"):
        hint = profil.notation
    else:
        hint = pi.locale_hint_fuer(rows)

    if spec.get("normalisierer") == "kraken_ledger":
        txs, warn, stats = pi.from_kraken_ledger(rows, hint)
        warnungen.extend(warn)
        zugeordnet = stats["zeilen"] - stats["nicht_zugeordnet"]
        return txs, {"csv_datenzeilen": stats["zeilen"],
                     "zugeordnete_zeilen": zugeordnet,
                     "uebersprungene_zeilen": 0,
                     "verarbeitete_zeilen": zugeordnet,
                     "nicht_zugeordnete_zeilen": stats["nicht_zugeordnet"],
                     "trennzeichen": delim, "notation": hint}

    spalten = spec.get("spalten") or {}
    pflicht = spec.get("pflicht") or []
    typ_werte = {str(k).strip().lower(): v
                 for k, v in (spec.get("typ_werte") or {}).items()}
    fehlende_spalten = [s for quelle in spalten.values() for s in _liste(quelle)
                        if s not in rows[0]]
    # Auch die Kontrollspalten müssen existieren — eine fehlende Währungsspalte
    # würde sonst als "nichts zu prüfen" durchgehen.
    fehlende_spalten += [c["spalte"] for c in _liste(spec.get("pruefe_spalte"))
                         if c.get("spalte") not in rows[0]]
    if fehlende_spalten:
        raise sl.ParseError(
            f"Spalte(n) {', '.join(repr(s) for s in fehlende_spalten)} fehlen in der "
            f"CSV (vorhanden: {', '.join(repr(k) for k in rows[0])}).\n"
            "→ Falsches Profil oder geändertes Exportformat.")

    pruefe = spec.get("pruefe_spalte")
    ignoriere_typen = {str(t).strip().lower() for t in spec.get("ignoriere_typen") or []}
    ignoriere_asset = {str(a).strip().upper() for a in spec.get("ignoriere_asset") or []}
    unbekannte_typen: set[str] = set()
    ohne_wert = 0
    uebersprungen = 0
    txs, nicht_zugeordnet = [], 0
    datenzeilen = 0
    for nr, r in enumerate(rows, start=2):
        if not any((v or "").strip() for v in r.values()):
            continue
        datenzeilen += 1
        roh_typ = (r.get(spalten.get("type", "")) or "").strip()
        typ = typ_werte.get(roh_typ.lower(), "")
        tx = {"source": profil.id}
        for feld, spalte in spalten.items():
            if feld == "type":
                continue
            quellen = _liste(spalte)
            wert = r.get(quellen[0])
            if feld == "timestamp":
                tx[feld] = pi._zeit(wert, warnungen)
            elif feld in ("asset", "counter_asset"):
                tx[feld] = (wert or "").strip().upper() or None
            elif feld in ("amount", "counter_amount", "eur_value", "fee_eur"):
                # Mehrere Spalten werden addiert — z. B. Gebühr + Spread: nur die
                # Summe ist die tatsächliche Anschaffungsnebenkosten-Position.
                summe, gesehen = D("0"), False
                for q in quellen:
                    roh = r.get(q)
                    if roh is None or str(roh).strip() == "":
                        continue
                    try:
                        summe += abs(_menge(roh, hint))
                        gesehen = True
                    except sl.ParseError:
                        warnungen.append(
                            f"Zeile {nr}: Betrag in Spalte {q!r} nicht lesbar: {roh!r} "
                            f"— Zeile NICHT übernommen.")
                        gesehen = False
                        break
                tx[feld] = str(summe) if gesehen else None
            else:
                tx[feld] = (wert or "").strip() or None
        tx["type"] = typ
        for feld in KANONISCHE_TX_FELDER:
            tx.setdefault(feld, None)
        tx["fee_eur"] = tx.get("fee_eur") or "0"

        if roh_typ.lower() in ignoriere_typen:
            uebersprungen += 1
            continue
        if (tx.get("asset") or "") in ignoriere_asset:
            uebersprungen += 1
            continue
        if not typ:
            unbekannte_typen.add(roh_typ or "<leer>")
            nicht_zugeordnet += 1
            continue
        if typ not in ERLAUBTE_TX_TYPEN:
            unbekannte_typen.add(roh_typ)
            nicht_zugeordnet += 1
            continue
        fehlt = [f for f in pflicht if not tx.get(f)]
        if fehlt:
            warnungen.append(
                f"Zeile {nr}: Pflichtfeld(er) {', '.join(fehlt)} leer — Zeile NICHT "
                f"übernommen.")
            nicht_zugeordnet += 1
            continue
        for check in _liste(pruefe):
            gesehen = (r.get(check["spalte"]) or "").strip().upper()
            if gesehen and gesehen != str(check["erwartet"]).upper():
                warnungen.append(
                    f"Zeile {nr}: Spalte {check['spalte']!r} ist {gesehen!r}, erwartet "
                    f"{check['erwartet']!r} — Beträge sind NICHT in Euro. "
                    "Umrechnung ergänzen, sonst ist das Ergebnis falsch.")
        if not tx.get("eur_value"):
            tx["_needs_fmv"] = True
            ohne_wert += 1
        txs.append(tx)

    if unbekannte_typen:
        warnungen.append(
            f"{nicht_zugeordnet} Zeile(n) mit nicht zugeordnetem Typ "
            f"({', '.join(sorted(unbekannte_typen)[:8])}) — im Profil unter "
            f"csv.typ_werte ergänzen, statt sie zu raten.")
    if ohne_wert:
        warnungen.append(
            f"{ohne_wert} Transaktion(en) ohne EUR-Wert (eur_value) — der Export "
            "liefert dafür keine Euro-Spalte. Marktwert zum Zeitpunkt ergänzen, sonst "
            "rechnet FIFO mit 0 €.")
    if uebersprungen:
        warnungen.append(
            f"{uebersprungen} Zeile(n) laut Profil bewusst übersprungen "
            f"(csv.ignoriere_typen / csv.ignoriere_asset).")
    return txs, {"csv_datenzeilen": datenzeilen, "zugeordnete_zeilen": len(txs),
                 "uebersprungene_zeilen": uebersprungen,
                 "verarbeitete_zeilen": len(txs) + uebersprungen,
                 "nicht_zugeordnete_zeilen": nicht_zugeordnet,
                 "trennzeichen": delim, "notation": hint}


def wende_an(profil, text, *, jahr=None, quelle="", datum=None, strikt=True) -> dict:
    """Profil auf einen Reporttext anwenden. Ergebnis-JSON inkl. Summenabgleich.

    `datum` überschreibt profil.datum ('de'/'en'/'iso'), `strikt=False` macht aus
    dem Abbruch bei Summenabweichung eine Meldung (nur für Tests/Diagnose).
    """
    if isinstance(profil, dict):
        profil = Profil(profil)
    _sicherstellen_benutzbar(profil)

    warnungen: list[str] = []
    if profil.ungeprueft:
        warnungen.append(
            f"Profil '{profil.id}' ist als UNGEPRÜFT markiert — es wurde nie "
            "gegen einen echten Report dieses Anbieters validiert. Spaltenzuordnung "
            "und Summen vor der Verwendung Zeile für Zeile gegen das Original prüfen.")

    hint = profil.notation if profil.notation in ("de", "en") else sl.detect_locale(text)
    datumsmodus = datum or profil.datum
    dayfirst = datumsmodus != "en"
    jahr = int(jahr) if jahr else _jahr_aus_text(profil, text)

    # ── Tabellen ────────────────────────────────────────────────────────────
    tabellen_zeilen: dict[str, list[dict]] = {}
    unmatched_gesamt: list[str] = []
    tabellen_warnungen: list[str] = []
    for tab in profil.tabellen:
        zeilen, unmatched = _tabelle_lesen(tab, text, hint, dayfirst)
        tabellen_zeilen[tab.get("name") or f"tabelle{len(tabellen_zeilen)}"] = zeilen
        if unmatched:
            unmatched_gesamt.extend(unmatched)
            tabellen_warnungen.append(
                f"{len(unmatched)} Zeile(n) in Tabelle '{tab.get('name')}' konnten "
                f"NICHT gelesen werden — Beispiel: {unmatched[0][:120]!r}")
    warnungen.extend(tabellen_warnungen)

    # ── Einzelwerte ─────────────────────────────────────────────────────────
    bereiche = Bereiche(profil, text, warnungen)
    werte, teilsummen, zahlen_gefunden = _extrahiere_werte(
        profil, text, hint, bereiche, warnungen)

    mindestens = profil.werte_regeln.get("mindestens", 0)
    if mindestens and zahlen_gefunden < mindestens:
        raise sl.ParseError(
            profil.werte_regeln.get("fehlermeldung")
            or (f"Nur {zahlen_gefunden} von mindestens {mindestens} erwarteten "
                f"Einzelwerten gefunden — Report-Layout geändert? Ein Ergebnis aus "
                f"lauter Nullen wäre von einem echten Null-Report nicht zu "
                f"unterscheiden; deshalb Abbruch."))
    marker = profil.werte_regeln.get("marker")
    if marker:
        anzahl = len(re.findall(entfalte(marker), text))
        if anzahl > zahlen_gefunden:
            warnungen.append(
                f"{anzahl} Marker im Report, aber nur {zahlen_gefunden} Werte "
                f"gelesen — Report-Layout prüfen.")

    # ── Grundgerüst je Ausgabeschema ────────────────────────────────────────
    ergebnis: dict = {
        "steuerjahr": jahr,
        "tax_year": jahr,
        "quelle": quelle or profil.quelle,
        "profil": profil.id,
        "profil_status": profil.status,
        "profil_geprueft_am": profil.geprueft_am,
        "zahlennotation": hint,
        "warnungen": warnungen,
        "elster_extra": [],
    }
    summen_basis: dict = {}
    ergebnis["summen_basis"] = summen_basis

    if profil.ergebnis == "krypto_transaktionen":
        if profil.eingabe == "csv":
            txs, stats = _csv_anwenden(profil, text, hint, warnungen)
            ergebnis["zahlennotation"] = stats.get("notation", hint)
            summen_basis.update(stats)
        else:
            tab = next((t for t in profil.tabellen
                        if (t.get("rolle") or t.get("name")) == "transaktionen"), None)
            if tab is None:
                raise sl.ParseError(
                    f"Profil {profil.id!r}: ergebnis 'krypto_transaktionen' braucht "
                    "eine Tabelle mit rolle/name 'transaktionen' oder einen csv-Block.")
            zeilen = tabellen_zeilen.get(tab.get("name"), [])
            txs = _baue_transaktionen(tab, zeilen, profil.id)
            unbekannt = sorted({t["type"] for t in txs
                                if (t.get("type") or "") not in ERLAUBTE_TX_TYPEN})
            if unbekannt:
                warnungen.append(
                    f"Transaktionstyp(en) {', '.join(repr(u) for u in unbekannt)} sind "
                    f"nicht kanonisch (erlaubt: "
                    f"{'|'.join(sorted(ERLAUBTE_TX_TYPEN))}) — im Profil zuordnen.")
            summen_basis.update({"csv_datenzeilen": len(zeilen) + len(unmatched_gesamt),
                                 "zugeordnete_zeilen": len(txs),
                                 "uebersprungene_zeilen": 0,
                                 "verarbeitete_zeilen": len(txs),
                                 "nicht_zugeordnete_zeilen": len(unmatched_gesamt)})
        ergebnis["transactions"] = txs
        ergebnis["anzahl_transaktionen"] = len(txs)
        summen_basis["anzahl_transaktionen"] = len(txs)

    haltefrist_warnungen: list[str] = []
    if profil.ergebnis in ("krypto_vorberechnet", "kap"):
        p23 = ergebnis.setdefault("paragraph_23", {})
        tab = next((t for t in profil.tabellen
                    if (t.get("rolle") or t.get("name")) == "veraeusserungen"), None)
        if tab is not None:
            zeilen = tabellen_zeilen.get(tab.get("name"), [])
            if datumsmodus == "auto":
                roh = [(z["_zeile_verkauf"], z["_zeile_erwerb"], z["_lang"])
                       for z in _rohdaten(tab, zeilen, text, dayfirst)]
                _pruefe_datumsformat(roh)
            disposals = _baue_disposals(tab, zeilen, tab.get("notiz_suffix", ""),
                                        haltefrist_warnungen)
            g = lambda d: sl.to_decimal(d["gain_eur"])  # noqa: E731
            steuerbar = [d for d in disposals if d["taxable"]]
            gains = sum((g(d) for d in steuerbar if g(d) > 0), D("0"))
            losses = sum((g(d) for d in steuerbar if g(d) < 0), D("0"))
            netto = sum((g(d) for d in steuerbar), D("0"))
            steuerfrei = sum((g(d) for d in disposals if not d["taxable"]), D("0"))
            alle = sum((g(d) for d in disposals), D("0"))
            p23.update({
                "freigrenze_angewendet": False,
                "anzahl_veraeusserungen": len(disposals),
                "gewinn_eur": str(sl.q2(gains)),
                "verlust_eur": str(sl.q2(losses)),
                "netto_ergebnis_eur": str(sl.q2(netto)),
                "verlustvortrag_eur": str(sl.q2(-netto if netto < 0 else D("0"))),
                "steuerfrei_langfristig_eur": str(sl.q2(steuerfrei)),
                "disposals": disposals,
                "nicht_zugeordnete_zeilen": unmatched_gesamt,
            })
            summen_basis["veraeusserungen_gewinn_gesamt"] = str(sl.q2(alle))
            summen_basis["anzahl_veraeusserungen"] = len(disposals)
            warnungen.extend(haltefrist_warnungen)
        else:
            p23.setdefault("anzahl_veraeusserungen", None)
            p23.setdefault("disposals", [])

    if profil.ergebnis == "kap":
        ergebnis.setdefault("kap_zeilen", {})
        ergebnis.setdefault("kennzahlen", {})

    # ── statische Ergänzungen aus dem Profil (Werte dürfen sie überschreiben) ─
    if profil.zusatz:
        _tief_mischen(ergebnis, json.loads(json.dumps(profil.zusatz)))
    if profil.hinweise:
        ergebnis["hinweise"] = list(profil.hinweise)

    # ── Einzelwerte einsetzen (können Gerüstfelder füllen) ───────────────────
    # Ein Wert, den der Report NICHT ausweist, wird weggelassen statt als 0,00
    # geschrieben: sonst ist "im Report steht 0,00" nicht mehr von "steht dort
    # gar nicht" zu unterscheiden — und nachgelagerte Regeln (z. B. "leite den
    # Betrag nach Zeile 19, wenn keine Zeile 7 gemeldet wurde") greifen nie.
    for w in profil.werte:
        typ = w.get("typ", "betrag")
        for pfad in _liste(w["pfad"]):
            wert = werte.get(pfad, _FEHLT)
            if wert is None and w.get("leer") != "null":
                continue
            _setze(ergebnis, pfad,
                   _als_ausgabe(None if wert is _FEHLT else wert, typ))
    for pfad, wert in teilsummen.items():
        _setze(ergebnis, pfad, str(sl.q2(wert)))
    for pfad, spec in (profil.kennzahlen or {}).items():
        # spec: Pfad, Liste von Pfaden oder {"quellen": [...], "vorzeichen": "negativ"}
        vorzeichen = None
        if isinstance(spec, dict):
            quellen, vorzeichen = spec.get("quellen"), spec.get("vorzeichen")
        else:
            quellen = spec
        summe = D("0")
        for q in _liste(quellen):
            # Ein fehlender Pfad heißt hier "der Report weist diese Zeile nicht
            # aus" und zählt als 0. Dass der Pfad überhaupt füllbar ist, prüft
            # pruefe_profil beim Laden — ein Tippfehler fällt dort auf, nicht hier.
            v = _hole(ergebnis, q)
            summe += sl.to_decimal(v) if v not in (None, "") else D("0")
        # `kennzahlen` ist die NORMIERTE Fassung (Gewinne positiv, Verluste negativ),
        # `kap_zeilen` bleibt die wörtliche Abschrift. Muss hier ein Vorzeichen
        # gedreht werden, wird das gemeldet statt still korrigiert.
        if vorzeichen == "negativ" and summe > 0:
            warnungen.append(
                f"Kennzahl '{pfad}' war positiv ({sl.fmt_eur(summe)}), laut Contract "
                f"tragen Verluste ein negatives Vorzeichen. Der Wert wurde als Verlust "
                f"angesetzt — Vorzeichen im Report prüfen.")
            summe = -summe
        elif vorzeichen == "positiv" and summe < 0:
            warnungen.append(
                f"Kennzahl '{pfad}' ist negativ ({sl.fmt_eur(summe)}), laut Contract "
                f"tragen Gewinne ein positives Vorzeichen. Der Wert wurde unverändert "
                f"übernommen — gehört dort ein Verlust hin, muss er in die passende "
                f"'verlust…'-Kennzahl.")
        _setze(ergebnis, pfad if "." in pfad else f"kennzahlen.{pfad}", str(sl.q2(summe)))

    # ── Blöcke vervollständigen ─────────────────────────────────────────────
    if profil.ergebnis in ("krypto_vorberechnet", "kap"):
        _ableiten_paragraph(ergebnis.setdefault("paragraph_23", {}),
                            hinweis=_P23_HINWEIS, mit_zufluessen=False)
        _ableiten_paragraph(ergebnis.setdefault("paragraph_22_nr3", {}),
                            hinweis=_P22_HINWEIS, mit_zufluessen=True)
        ergebnis["paragraph_23"].setdefault("nicht_zugeordnete_zeilen", unmatched_gesamt)
        if tabellen_warnungen or haltefrist_warnungen:
            ergebnis["paragraph_23"]["warnungen"] = (
                list(ergebnis["paragraph_23"]["warnungen"])
                + tabellen_warnungen + haltefrist_warnungen)
        # Alte Schreibweise als Alias, damit ältere Konsumenten nicht still 0 lesen.
        ergebnis["paragraph_22_nr_3"] = ergebnis["paragraph_22_nr3"]

    # ── ELSTER-Zuordnungen ──────────────────────────────────────────────────
    for e in profil.elster:
        wert = _hole(ergebnis, e["pfad"])
        if wert in (None, ""):
            continue
        if e.get("nur_wenn_gesetzt", True) and sl.to_decimal(wert) == 0:
            continue
        ergebnis["elster_extra"].append({
            "anlage": e.get("anlage", ""), "zeile": e.get("zeile", ""),
            "bezeichnung": e.get("bezeichnung", ""), "wert": str(sl.q2(sl.to_decimal(wert))),
        })

    # ── Summenabgleich: das Sicherheitsnetz ─────────────────────────────────
    abgleiche, bericht = _summen_abgleich(profil, ergebnis, text, hint, warnungen,
                                          bereiche)
    if unmatched_gesamt or any(t.get("melde_nicht_zugeordnet", True)
                               for t in profil.tabellen):
        bericht.append(f"Nicht zugeordnete Tabellenzeilen: {len(unmatched_gesamt)}")
    if profil.werte:
        bericht.append(f"Gefundene Einzelwerte: {zahlen_gefunden}")
    ergebnis["abgleich"] = bericht
    try:
        sl.pruefe_summen(abgleiche, strikt=strikt)
    except sl.PlausibilityError as e:
        # Ohne die gesammelten Warnungen sieht der Abbruch nur "3 vs. 4" — welche
        # Zeilen fehlen und warum, steht genau dort.
        if warnungen:
            raise sl.PlausibilityError(
                f"{e}\nGemeldete Auffälligkeiten:\n  " + "\n  ".join(warnungen))
        raise
    return ergebnis


def _rohdaten(tab, zeilen, text, dayfirst):
    """Rohe Datumsstrings je Zeile — nur für die Datumsformat-Prüfung."""
    zeile = _kompiliere(tab["zeile"])
    felder = tab.get("felder") or {}
    gruppe_v = felder.get("disposal_date")
    gruppe_e = felder.get("acquisition_date")
    if not gruppe_v or not gruppe_e:
        return []
    out = []
    for satz in zeilen:
        m = zeile.match(satz["_zeile"]) or zeile.search(satz["_zeile"])
        if not m:
            continue
        # Bewusst die Einstufung des REPORTS: die gesetzliche Frist wird aus
        # denselben Daten gerechnet und wäre unter jeder Auslegung mit sich selbst
        # konsistent — als Gegenprobe für TT/MM vs. MM/TT taugt nur die Fremdangabe.
        laut_report = _label_langfristig(tab, satz)
        if laut_report is None:
            continue
        out.append({"_zeile_verkauf": m.groupdict().get(gruppe_v),
                    "_zeile_erwerb": m.groupdict().get(gruppe_e),
                    "_lang": laut_report})
    return out


_FEHLT = object()


def _vergleichswert(ergebnis: dict, pfad: str, label: str, warnungen: list[str]):
    """Wert hinter einem `summen`-Pfad. Fehlt der Pfad, wird geworfen.

    Ein fehlender Pfad als 0 gelesen ergäbe "geparst 0,00 € vs. Report 0,00 € —
    Abweichung 0,00 €": eine grüne Meldung über eine Prüfung, die nie stattfand.
    """
    wert = _hole(ergebnis, pfad, _FEHLT)
    if wert is _FEHLT:
        raise sl.ParseError(
            f"Summenabgleich {label!r}: der Vergleichspfad {pfad!r} existiert im "
            f"Ergebnis nicht — das Profil kann ihn nie füllen. Ein Abgleich gegen "
            f"einen nicht vorhandenen Wert würde eine Prüfung melden, die nicht "
            f"stattgefunden hat.\n→ 'vergleich' im Profil korrigieren.")
    if wert in (None, ""):
        warnungen.append(
            f"Summenabgleich {label!r}: der Vergleichspfad {pfad!r} ist leer (kein "
            f"Wert gelesen) und wurde als 0,00 verglichen — Profil prüfen.")
        return 0
    return wert


def _summen_abgleich(profil: Profil, ergebnis: dict, text: str, hint,
                     warnungen: list[str], bereiche: Optional[Bereiche] = None):
    abgleiche: list[sl.Abgleich] = []
    for s in profil.summen:
        art = s.get("art", "betrag")
        geparst_roh = _vergleichswert(ergebnis, s["vergleich"],
                                      s.get("label") or s["vergleich"], warnungen)
        geparst = sl.to_decimal(geparst_roh)
        ausgewiesen = None
        if art == "zeilen":
            quelle_pfad = s.get("quelle_pfad", "summen_basis.csv_datenzeilen")
            ausgewiesen = D(str(_vergleichswert(
                ergebnis, quelle_pfad, s.get("label") or quelle_pfad, warnungen)))
        else:
            wert, gefunden = _suche_wert(
                {"muster": s.get("muster"), "typ": "betrag" if art == "betrag" else "ganzzahl",
                 "form": s.get("form"), "bereich": s.get("bereich"), "flach": s.get("flach")},
                text, hint, bereiche)
            if gefunden:
                ausgewiesen = D(wert)
            else:
                warnungen.append(
                    f"Summenabgleich {s.get('label', s['vergleich'])!r}: das Muster "
                    f"findet im Report nichts — der Wert konnte NICHT gegengeprüft "
                    f"werden. Profil-Muster gegen das Original prüfen.")
        toleranz = sl.to_decimal(s.get("toleranz", "0.01"))
        klasse = _AnzahlAbgleich if art in ("anzahl", "zeilen") else sl.Abgleich
        if art in ("anzahl", "zeilen"):
            geparst = D(int(geparst))
            ausgewiesen = None if ausgewiesen is None else D(int(ausgewiesen))
        else:
            geparst = sl.q2(geparst)
            ausgewiesen = None if ausgewiesen is None else sl.q2(ausgewiesen)
        abgleiche.append(klasse(s.get("label") or s["vergleich"], geparst,
                                ausgewiesen, toleranz))
    return abgleiche, [str(a) for a in abgleiche]
