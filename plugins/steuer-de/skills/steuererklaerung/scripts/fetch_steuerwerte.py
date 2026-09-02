#!/usr/bin/env python3
"""
fetch_steuerwerte.py — references/steuerwerte.json aus amtlichen Quellen füllen.

Werkzeug für die Pflege des Skills, **nicht** Teil der Report-Pipeline: kein
anderes Skript hier geht ins Netz, und ein Steuerreport darf nie davon abhängen,
ob ein Server erreichbar ist. Aufgerufen wird das hier von Hand, wenn ein neues
Steuerjahr hinzukommt oder ein Wert nachgeprüft werden soll.

Zwei amtliche Quellen, beide vom Bund, unabhängig voneinander:

  * **Tarifhistorie des Bundesministeriums der Finanzen**
    (bmf-steuerrechner.de) — je Seite ein Tarifzeitraum mit der „Formel nach
    § 32a EStG“, zurück bis 1958. Daher kommen die Tarife aller Jahre; das Jahr
    steht in der Seitenüberschrift, die Zuordnung ist also keine Annahme.
  * **Amtliche XML-Fassung von EStG und SolZG** (gesetze-im-internet.de,
    herausgegeben vom Bundesministerium der Justiz) — führt immer nur die
    *geltende* Fassung. Daraus kommt die Freigrenze des § 3 Abs. 3 SolZG, und
    der Tarif des geltenden Jahres wird damit gegen die BMF-Historie gehalten.
    Widersprechen sich die beiden, wird nichts geschrieben.

Nicht geholt werden:

  * **Soli-Freigrenzen früherer Jahre.** Amtlich veröffentlicht ist nur die
    geltende Fassung des § 3 SolZG; eine maschinenlesbare amtliche Historie gibt
    es nicht. Frühere Jahre bleiben unangetastet, und der Lauf sagt, welche er
    nicht nachprüfen konnte.
  * **Sparer-Pauschbetrag, Arbeitnehmer-Pauschbetrag, Freigrenze § 23.** Sie
    stehen an anderer Stelle im Gesetz und werden von Hand gepflegt. Für ein
    neues Jahr legt das Skript sie als `null` an — nie als 0.

    python3 scripts/fetch_steuerwerte.py                 # nur anzeigen
    python3 scripts/fetch_steuerwerte.py --jahre 2022-2027
    python3 scripts/fetch_steuerwerte.py --schreiben     # JSON aktualisieren

Ohne --schreiben wird nichts verändert; es erscheint nur, was sich ändern würde.
Zum Lesen der BMF-Historie wird PyMuPDF gebraucht (`pip install pymupdf`) —
dieselbe Bibliothek, die auch scripts/parse_pdf.py benutzt.
"""
from __future__ import annotations

import argparse
import html as html_mod
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import date
from decimal import Decimal as D
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steuerlib as sl  # noqa: E402

BMF_START = "https://www.bmf-steuerrechner.de/"
ESTG_XML = "https://www.gesetze-im-internet.de/estg/xml.zip"
SOLZG_XML = "https://www.gesetze-im-internet.de/solzg_1995/xml.zip"
SGB6_XML = "https://www.gesetze-im-internet.de/sgb_6/xml.zip"

UA = "steuer-de-marketplace/steuerwerte-pflege"

TARIF_SCHLUESSEL = ("gfb", "z2", "z3", "z4", "a2", "c3", "a3", "k4", "k5")


class FetchError(RuntimeError):
    """Abruf oder Auswertung ist gescheitert — es wird nichts geschrieben."""


# ─────────────────────────────────────────────────────────────────────────────
# Text gewinnen
# ─────────────────────────────────────────────────────────────────────────────

def text_aus_markup(h: str) -> str:
    """Tags raus, Entities auf, Zwischenraum vereinheitlichen — für HTML wie XML."""
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"<!--.*?-->", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def _norm_block(xml: str, enbez: str) -> str:
    """Das rohe `<norm>`-Element einer Vorschrift aus der amtlichen XML.

    Getrennt von norm_aus_xml, weil manche Auswertungen das Markup brauchen —
    eine Tabelle verliert beim Plätten ihre Spalten.
    """
    i = xml.find(f"<enbez>{enbez}</enbez>")
    if i < 0:
        raise FetchError(f"{enbez} nicht in der amtlichen XML-Fassung gefunden")
    anfang = xml.rfind("<norm ", 0, i)
    ende = xml.find("</norm>", i)
    if anfang < 0 or ende < 0:
        raise FetchError(f"{enbez}: XML-Aufbau unerwartet")
    return xml[anfang:ende]


def norm_aus_xml(xml: str, enbez: str) -> str:
    """Den Text *einer* Norm aus der amtlichen XML-Fassung eines Gesetzes.

    Die XML enthält das ganze Gesetz; jede Vorschrift steckt in einem `<norm>`
    mit ihrer Bezeichnung in `<enbez>`. Ohne diesen Schnitt träfe ein Suchmuster
    irgendwann auf einen ganz anderen Paragraphen.
    """
    return text_aus_markup(_norm_block(xml, enbez))


def tarifhistorie_link(startseite: str) -> str:
    """Die URL der Tarifhistorie von der Startseite des BMF-Rechners.

    Der Dateiname trägt das Datum der letzten Fortschreibung
    (`2025_10_14_Tarifhistorie_…`) und ändert sich — fest verdrahtet wäre er
    beim nächsten Steuerjahr tot.
    """
    m = re.search(r'href="([^"]*Tarifhistorie[^"]*)"', startseite)
    if not m:
        raise FetchError("Kein Link zur Tarifhistorie auf der BMF-Startseite")
    url = m.group(1)
    return url if url.startswith("http") else BMF_START.rstrip("/") + url


# ─────────────────────────────────────────────────────────────────────────────
# Text → Zahlen
# ─────────────────────────────────────────────────────────────────────────────

# Tausender trennt die amtliche XML mit geschütztem Leerzeichen (12 348), die
# BMF-Historie mit Punkt (12.348); multipliziert wird mit „•“, „·“ oder „*“.
_LEER = "     "
_MAL = r"[·•*×]"
_MINUS = r"[-−–]"
_ZAHL = rf"[\d.{_LEER}]*\d(?:,\d+)?"


def _zahl(s: str) -> D:
    for z in _LEER:
        s = s.replace(z, "")
    return D(s.replace(".", "").replace(",", "."))


def _such(muster: str, text: str, was: str) -> re.Match:
    m = re.search(muster, text)
    if not m:
        raise FetchError(f"{was} nicht im Text gefunden")
    return m


def _pruefe_zonenraender(jahr: int, tarif: dict, unten: tuple) -> None:
    """Jede Zone muss einen Euro über der vorigen beginnen. Ein verlesener
    Zonenrand fällt damit sofort auf."""
    for u, oben, was in zip(unten,
                            (tarif["gfb"], tarif["z2"], tarif["z3"], tarif["z4"]),
                            ("Zone 2", "Zone 3", "Zone 4", "Zone 5")):
        if u != oben + 1:
            raise FetchError(f"{jahr}: {was} beginnt bei {u}, erwartet {oben + 1}")


def tarif_aus_text(text: str) -> tuple[int, dict]:
    """§ 32a Abs. 1 EStG im Gesetzeswortlaut → (Veranlagungszeitraum, Tarifwerte)."""
    # „ab dem Veranlagungszeitraum 2026“, wenn der Tarif offen weitergilt,
    # „im Veranlagungszeitraum 2023“, wenn er nur für dieses eine Jahr gilt.
    jahr = int(_such(r"(?:ab dem|im) Veranlagungszeitraum (\d{4})", text,
                     "Veranlagungszeitraum").group(1))

    gfb = _such(rf"1\.\s*bis ({_ZAHL})\s*Euro \(Grundfreibetrag\)", text, "Grundfreibetrag")
    z2 = _such(rf"2\.\s*von ({_ZAHL})\s*Euro bis ({_ZAHL})\s*Euro:\s*\(({_ZAHL})\s*{_MAL}\s*y",
               text, "Zone 2")
    z3 = _such(rf"3\.\s*von ({_ZAHL})\s*Euro bis ({_ZAHL})\s*Euro:\s*\(({_ZAHL})\s*{_MAL}\s*z"
               rf"\s*\+\s*{_ZAHL}\s*\)\s*{_MAL}\s*z\s*\+\s*({_ZAHL})\s*[;.]", text, "Zone 3")
    z4 = _such(rf"4\.\s*von ({_ZAHL})\s*Euro bis ({_ZAHL})\s*Euro:\s*0,42\s*{_MAL}\s*x"
               rf"\s*{_MINUS}\s*({_ZAHL})\s*[;.]", text, "Zone 4")
    z5 = _such(rf"5\.\s*von ({_ZAHL})\s*Euro an:\s*0,45\s*{_MAL}\s*x"
               rf"\s*{_MINUS}\s*({_ZAHL})\s*[;.]", text, "Zone 5")

    tarif = {
        "gfb": _zahl(gfb.group(1)), "z2": _zahl(z2.group(2)), "z3": _zahl(z3.group(2)),
        "z4": _zahl(z4.group(2)), "a2": _zahl(z2.group(3)), "c3": _zahl(z3.group(4)),
        "a3": _zahl(z3.group(3)), "k4": _zahl(z4.group(3)), "k5": _zahl(z5.group(2)),
    }
    _pruefe_zonenraender(jahr, tarif, tuple(_zahl(m.group(1)) for m in (z2, z3, z4, z5)))
    return jahr, tarif


def jahre_aus_ueberschrift(seite: str) -> list[int]:
    """Die Jahre, für die eine Seite der BMF-Historie gilt.

    Meist eines („Einkommensteuertarif 2024“), bei mehrjähriger Geltung ein
    Bereich („Einkommensteuertarif 2010 (2010 - 2012)“). Nur das erste Jahr zu
    nehmen hieße, für die übrigen zu melden, das Gesetz führe sie nicht.
    """
    kopf = re.search(r"Einkommensteuertarif\s+(\d{4})\s*(?:\(\s*(\d{4})\s*[-–]\s*(\d{4})\s*\))?",
                     seite)
    if not kopf:
        return []
    if kopf.group(2) and kopf.group(3):
        return list(range(int(kopf.group(2)), int(kopf.group(3)) + 1))
    return [int(kopf.group(1))]


def tarif_aus_historieseite(seite: str) -> Optional[tuple[list[int], dict]]:
    """Eine Seite der BMF-Tarifhistorie → (Jahre, Tarifwerte), oder None.

    None für jede Seite, die nicht den Tarif zeigt, den steuerlib rechnen kann:
    die Historie reicht bis 1958 zurück (DM-Beträge, Formeln mit Y² und Y³), und
    2007/2008 lautete die Konstante der zweiten Zone 1.500 statt 1.400. So eine
    Seite halb zu lesen wäre schlimmer, als sie zu übergehen.
    """
    jahre = jahre_aus_ueberschrift(seite)
    if not jahre or "€" not in seite:
        return None
    jahr = jahre[0]

    muster = (
        rf"a\)\s*bis\s*({_ZAHL})\s*€\s*\(Grundfreibetrag\)",
        rf"b\)\s*({_ZAHL})\s*€\s*bis\s*({_ZAHL})\s*€\s*:\s*ESt\s*=\s*\(({_ZAHL})\s*{_MAL}\s*y"
        rf"\s*\+\s*({_ZAHL})\s*\)",
        rf"c\)\s*({_ZAHL})\s*€\s*bis\s*({_ZAHL})\s*€\s*:\s*ESt\s*=\s*\(({_ZAHL})\s*{_MAL}\s*z"
        rf"\s*\+\s*({_ZAHL})\s*\)\s*{_MAL}\s*z\s*\+\s*({_ZAHL})\s*;",
        rf"d\)\s*({_ZAHL})\s*€\s*bis\s*({_ZAHL})\s*€\s*:\s*ESt\s*=\s*0,42\s*{_MAL}\s*zvE"
        rf"\s*{_MINUS}\s*({_ZAHL})\s*;",
        rf"e\)\s*ab\s*({_ZAHL})\s*€\s*:\s*ESt\s*=\s*0,45\s*{_MAL}\s*zvE"
        rf"\s*{_MINUS}\s*({_ZAHL})\s*\.",
    )
    # Die Zeilenumbrüche der PDF-Extraktion stören sonst jedes Muster.
    # Zeilenumbrüche der PDF-Extraktion und die Schreibweise „Y“/„y“ der älteren
    # Seiten dürfen nicht darüber entscheiden, was gelesen wird.
    flach = re.sub(r"\s+", " ", seite)
    treffer = [re.search(m, flach, re.I) for m in muster]
    if not all(treffer):
        return None
    gfb, b, c, d, e = treffer

    # steuerlib.est_aus_tarif rechnet mit den Zonenkonstanten 1.400 und 2.397.
    # Ein Tarif mit anderen Konstanten ließe sich damit nicht darstellen.
    if (_zahl(b.group(4)), _zahl(c.group(4))) != (D("1400"), D("2397")):
        return None

    tarif = {
        "gfb": _zahl(gfb.group(1)), "z2": _zahl(b.group(2)), "z3": _zahl(c.group(2)),
        "z4": _zahl(d.group(2)), "a2": _zahl(b.group(3)), "c3": _zahl(c.group(5)),
        "a3": _zahl(c.group(3)), "k4": _zahl(d.group(3)), "k5": _zahl(e.group(2)),
    }
    _pruefe_zonenraender(jahr, tarif, tuple(_zahl(m.group(1)) for m in (b, c, d, e)))
    return jahre, tarif


def tarife_aus_tarifhistorie(seiten: list[str]) -> dict[int, dict]:
    """Alle lesbaren Tarifzeiträume der BMF-Historie, Jahr → Tarifwerte."""
    tarife = {}
    for seite in seiten:
        gelesen = tarif_aus_historieseite(seite)
        if gelesen:
            for jahr in gelesen[0]:
                tarife[jahr] = gelesen[1]
    if not tarife:
        raise FetchError("In der BMF-Tarifhistorie war kein einziger Tarif lesbar — "
                         "hat sich der Aufbau des Dokuments geändert?")
    return tarife


def soli_freigrenze_aus_text(text: str) -> D:
    """§ 3 Abs. 3 SolZG → Freigrenze bei Einzelveranlagung.

    Der Absatz nennt zuerst den doppelten Betrag für Zusammenveranlagung
    (§ 32a Abs. 5 und 6 EStG) und erst danach den für alle anderen Fälle.
    """
    m = _such(rf"2\.\s*in anderen F(?:ä|ae)llen\s*({_ZAHL})\s*Euro", text,
              "Freigrenze § 3 Abs. 3 SolZG")
    return _zahl(m.group(1))


def bbg_knappschaftlich_aus_xml(xml: str) -> dict:
    """Anlage 2 SGB VI → Jahr → knappschaftliche Beitragsbemessungsgrenze.

    Für § 10 Abs. 3 Satz 1 EStG ist der Höchstbeitrag zur **knappschaftlichen**
    Rentenversicherung maßgeblich. Die Tabelle führt je Zeitraum zuerst die
    allgemeine, dann die knappschaftliche Grenze; genommen wird die letzte Zahl
    der Zeile. Die allgemeine ist rund ein Fünftel kleiner — eine Verwechslung
    fiele in der fertigen Steuerzahl nicht mehr auf.
    """
    # Nur Anlage 2. Anlage 2a führt dieselben Zeiträume für das Beitrittsgebiet;
    # über die ganze XML gesucht überschriebe sie die West-Werte, und für 2022
    # stünde dann 100.200 statt 103.800 in der Steuerberechnung.
    grenzen = {}
    for zeile in re.findall(r"<row>.*?</row>", _norm_block(xml, "Anlage 2"), re.S):
        # Zellenweise lesen: über die ganze Zeile gesucht verschmölzen Datum und
        # Beträge zu einer einzigen Zahl, weil hier Leerzeichen die Tausender
        # trennen ("103 800") und Punkte im Datum stehen.
        zellen = [text_aus_markup(z) for z in re.findall(r"<entry[^>]*>.*?</entry>",
                                                         zeile, re.S)]
        if not zellen:
            continue
        jahr = re.match(r"\s*1\.\s?1\.(\d{4})", zellen[0])
        if not jahr:
            continue
        betraege = [_zahl(z) for z in zellen[1:]
                    if re.fullmatch(rf"\s*{_ZAHL}\s*", z) and _zahl(z) > 1000]
        if betraege:
            grenzen[int(jahr.group(1))] = betraege[-1]
    if not grenzen:
        raise FetchError("Anlage 2 SGB VI: keine Beitragsbemessungsgrenze gelesen — "
                         "hat sich der Aufbau der Tabelle geändert?")
    return grenzen


# ─────────────────────────────────────────────────────────────────────────────
# Selbstkontrolle
# ─────────────────────────────────────────────────────────────────────────────

def pruefe_stetig(jahr: int, tarif: dict) -> None:
    """An jeder Zonengrenze muss der Tarif stetig sein.

    Das ist die schärfste Kontrolle gegen einen verlesenen Koeffizienten: die
    Zonen sind so aufeinander abgestimmt, dass ein falscher Wert an der Grenze
    einen sichtbaren Sprung erzeugt.
    """
    for grenze in (tarif["z2"], tarif["z3"], tarif["z4"]):
        a = sl.est_aus_tarif(grenze, tarif)
        b = sl.est_aus_tarif(grenze + 1, tarif)
        if abs(b - a) >= 2:
            raise FetchError(
                f"{jahr}: Sprung von {a} auf {b} an der Zonengrenze {grenze} — "
                f"mindestens ein Tarifwert ist falsch gelesen")


def pruefe_abgleich(jahr: int, bmf: dict, estg: dict) -> None:
    """BMF-Tarifhistorie und EStG sind zwei getrennte amtliche
    Veröffentlichungen. Für das Jahr, das beide führen, müssen sie sich auf den
    Cent einig sein."""
    abweichend = [f"{k}: BMF {bmf[k]} / EStG {estg[k]}"
                  for k in TARIF_SCHLUESSEL if bmf[k] != estg[k]]
    if abweichend:
        raise FetchError(f"{jahr}: Quellen widersprechen sich — " + "; ".join(abweichend))


# ─────────────────────────────────────────────────────────────────────────────
# Abruf
# ─────────────────────────────────────────────────────────────────────────────

def hole(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise FetchError(f"{url} nicht abrufbar: {e}") from e


def gesetz_xml(url: str) -> str:
    """Die amtliche XML-Fassung eines Gesetzes; sie kommt als ZIP mit einer Datei."""
    try:
        z = zipfile.ZipFile(io.BytesIO(hole(url)))
        return z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, IndexError, KeyError) as e:
        raise FetchError(f"{url} ist kein lesbares ZIP: {e}") from e


def seiten_aus_pdf(daten: bytes) -> list[str]:
    try:
        import fitz  # PyMuPDF, dieselbe Bibliothek wie in parse_pdf.py
    except ImportError as e:
        raise FetchError("Zum Lesen der BMF-Tarifhistorie fehlt PyMuPDF — "
                         "`pip install pymupdf`.") from e
    with fitz.open(stream=daten, filetype="pdf") as doc:
        return [seite.get_text() for seite in doc]


def tarife_holen(jahre: set[int], laut: bool = True) -> dict[int, tuple[dict, str]]:
    """Jahr → (Tarifwerte, Quellenangabe) aus der BMF-Tarifhistorie."""
    url = tarifhistorie_link(hole(BMF_START).decode("utf-8", errors="replace"))
    if laut:
        print(f"  Tarifhistorie: {url}")
    beleg = ("Tarifhistorie des Bundesministeriums der Finanzen, "
             + os.path.basename(url).replace(".xhtml", ""))
    alle = tarife_aus_tarifhistorie(seiten_aus_pdf(hole(url)))
    gefunden = {}
    for jahr in sorted(jahre & set(alle)):
        pruefe_stetig(jahr, alle[jahr])
        gefunden[jahr] = (alle[jahr], beleg)
        if laut:
            print(f"  § 32a {jahr}: Grundfreibetrag {alle[jahr]['gfb']} €")
    return gefunden


def bbg_holen(jahre: set, laut: bool = True) -> dict:
    """Knappschaftliche Beitragsbemessungsgrenze je Jahr, aus Anlage 2 SGB VI.

    Daraus ergibt sich mit dem Beitragssatz der Höchstbetrag für
    Altersvorsorgeaufwendungen nach § 10 Abs. 3 Satz 1 EStG. Anders als beim
    Tarif führt die Anlage die Werte für alle Jahre, auch zurückliegende.
    """
    alle = bbg_knappschaftlich_aus_xml(gesetz_xml(SGB6_XML))
    gefunden = {j: alle[j] for j in sorted(jahre & set(alle))}
    if laut:
        for jahr, wert in gefunden.items():
            print(f"  BBG knappschaftlich {jahr}: {wert} €")
    return gefunden


def geltendes_jahr_pruefen(tarife: dict[int, tuple[dict, str]],
                           laut: bool = True) -> tuple[int, D]:
    """Den Tarif des geltenden Jahres gegen das EStG halten und die
    Soli-Freigrenze aus dem SolZG holen.

    Gibt (Kalenderjahr, Freigrenze) zurück — das Jahr, für das die geltende
    Fassung des § 3 SolZG steht, nicht das des § 32a.
    """
    jahr, tarif_estg = tarif_aus_text(norm_aus_xml(gesetz_xml(ESTG_XML), "§ 32a"))
    if jahr in tarife:
        pruefe_abgleich(jahr, tarife[jahr][0], tarif_estg)
        if laut:
            print(f"  § 32a {jahr}: BMF-Historie und EStG stimmen überein")
    elif laut:
        print(f"  EStG führt § 32a für {jahr} — nicht unter den geholten Jahren")

    # Das Jahr des § 32a taugt hier *nicht*: seine Fassung gilt offen weiter
    # („ab dem Veranlagungszeitraum 2026“), während die Soli-Freigrenze jährlich
    # angehoben wird. Die geltende Fassung des § 3 SolZG ist die des laufenden
    # Kalenderjahres — sonst schriebe ein Lauf im Januar den neuen Wert in das
    # alte Jahr und überschriebe dort einen geprüften Wert.
    freigrenze = soli_freigrenze_aus_text(norm_aus_xml(gesetz_xml(SOLZG_XML), "§ 3"))
    soli_jahr = date.today().year
    if laut:
        print(f"  § 3 Abs. 3 SolZG {soli_jahr}: Freigrenze {freigrenze} €")
    return soli_jahr, freigrenze


# ─────────────────────────────────────────────────────────────────────────────
# JSON schreiben
# ─────────────────────────────────────────────────────────────────────────────

def _s(d: D) -> str:
    return format(d, "f")


def zusammenfuehren(alt: dict, tarife: dict[int, tuple[dict, str]],
                    soli: dict[int, D], bbg: Optional[dict] = None
                    ) -> tuple[dict, list[str]]:
    """Geholte Werte in die vorhandene JSON einarbeiten. Gibt die neue Fassung
    und die Liste der Änderungen zurück; von Hand gepflegte Werte bleiben."""
    neu = json.loads(json.dumps(alt))
    jahre = neu.setdefault("jahre", {})
    heute = date.today().isoformat()
    aenderungen: list[str] = []

    bbg = bbg or {}
    for jahr in sorted(set(tarife) | set(soli) | set(bbg)):
        k = str(jahr)
        eintrag = jahre.get(k)
        if eintrag is None and jahr not in tarife:
            # Ein Eintrag ohne `tarif` macht references/steuerwerte.json unlesbar
            # und damit jedes Skript des Skills unbenutzbar. Für ein Jahr, zu dem
            # es keinen Tarif gibt, wird deshalb gar nichts angelegt.
            aenderungen.append(
                f"{k}: übersprungen — es gibt eine Soli-Freigrenze, aber keinen "
                f"§ 32a-Tarif; ohne Tarif wird kein Jahr angelegt")
            continue
        if eintrag is None:
            # Diese Werte stehen an anderer Stelle im Gesetz und werden von Hand
            # gepflegt. Sie bleiben leer statt 0: eine 0 hieße „kein
            # Pauschbetrag“ und ginge still in die Berechnung ein, während
            # `null` das Jahr aus der Tabelle hält und den dokumentierten
            # Ersatzwert samt Warnung auslöst.
            fehlend = ["freigrenze_23", "sparer_pb", "an_pauschbetrag", "quelle"]
            aenderungen.append(
                f"{k}: NEU — {', '.join(fehlend)} bleiben leer und sind von Hand "
                f"nachzutragen (§ 23 Abs. 3, § 20 Abs. 9, § 9a EStG; als quelle "
                f"das Änderungsgesetz mit Fundstelle im BGBl.)")
            eintrag = jahre[k] = dict.fromkeys(fehlend + ["soli_freigrenze"])

        if jahr in tarife:
            tarif, quelle = tarife[jahr]
            fuer_json = {s: _s(tarif[s]) for s in TARIF_SCHLUESSEL}
            for s in TARIF_SCHLUESSEL:
                vorher = (eintrag.get("tarif") or {}).get(s)
                if vorher != fuer_json[s]:
                    aenderungen.append(f"{k}: tarif.{s} {vorher} → {fuer_json[s]}")
            eintrag["tarif"] = fuer_json
            # `quelle` nennt das Änderungsgesetz mit Fundstelle im BGBl. — die
            # Angabe, die man zitiert. Die BMF-Historie nennt kein
            # Änderungsgesetz, sie darf die Fundstelle also nicht ersetzen;
            # sie steht als `beleg` daneben.
            if eintrag.get("beleg") != quelle:
                aenderungen.append(f"{k}: beleg → {quelle}")
            eintrag["beleg"] = quelle
        if jahr in bbg:
            wert = _s(bbg[jahr])
            if eintrag.get("bbg_knappschaftlich") != wert:
                aenderungen.append(
                    f"{k}: bbg_knappschaftlich {eintrag.get('bbg_knappschaftlich')} → {wert}")
            eintrag["bbg_knappschaftlich"] = wert
        if jahr in soli:
            wert = _s(soli[jahr])
            if eintrag.get("soli_freigrenze") != wert:
                aenderungen.append(
                    f"{k}: soli_freigrenze {eintrag.get('soli_freigrenze')} → {wert}")
            eintrag["soli_freigrenze"] = wert
        eintrag["geprueft"] = heute

    neu["jahre"] = {k: jahre[k] for k in sorted(jahre)}
    return neu, aenderungen


def _jahre_lesen(s: str) -> set[int]:
    """„2022-2026“ oder „2025,2026“ → Jahresmenge. Leer oder verdreht ist ein
    Tippfehler und wird gemeldet, statt später an min() zu scheitern."""
    try:
        if "-" in s:
            a, b = s.split("-", 1)
            jahre = set(range(int(a), int(b) + 1))
        else:
            jahre = {int(t) for t in s.split(",")}
    except ValueError:
        raise SystemExit(f"--jahre {s!r}: erwartet z. B. 2022-2026 oder 2025,2026")
    if not jahre:
        raise SystemExit(f"--jahre {s!r}: kein Jahr — ist der Bereich verdreht?")
    return jahre


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="§ 32a EStG und § 3 SolZG aus amtlichen Quellen holen und "
                    "references/steuerwerte.json pflegen")
    ap.add_argument("--jahre", help="z. B. 2022-2026 oder 2025,2026 "
                                    "(Vorgabe: die bereits hinterlegten Jahre)")
    ap.add_argument("--schreiben", action="store_true",
                    help="die JSON tatsächlich ändern (ohne das nur anzeigen)")
    ap.add_argument("--json", default=sl.STEUERWERTE_JSON, help="Pfad zur steuerwerte.json")
    args = ap.parse_args(argv)

    pfad = os.path.normpath(args.json)
    with open(pfad, encoding="utf-8") as f:
        alt = json.load(f)
    jahre = _jahre_lesen(args.jahre) if args.jahre else {int(j) for j in alt["jahre"]}

    print(f"Hole {min(jahre)}–{max(jahre)} aus amtlichen Quellen …")
    try:
        tarife = tarife_holen(jahre)
        soli_jahr, freigrenze = geltendes_jahr_pruefen(tarife)
        bbg = bbg_holen(jahre)
    except FetchError as e:
        print(f"\nFEHLER: {e}\nEs wurde nichts geschrieben.", file=sys.stderr)
        return 1

    # Amtlich nachprüfbar ist nur die geltende Fassung des § 3 SolZG.
    soli = {soli_jahr: freigrenze} if soli_jahr in jahre else {}
    ungeprueft = sorted(jahre - set(soli))
    if ungeprueft:
        print(f"\n  Soli-Freigrenze nicht amtlich nachprüfbar für "
              f"{', '.join(str(j) for j in ungeprueft)} — es gibt keine amtliche "
              f"Fassungshistorie des § 3 SolZG.\n  Die hinterlegten Werte bleiben stehen.")

    fehlend = sorted(jahre - set(tarife))
    if fehlend:
        print(f"\n  Kein Tarif in der BMF-Historie für "
              f"{', '.join(str(j) for j in fehlend)} — das Gesetz führt diese "
              f"Veranlagungszeiträume (noch) nicht.")

    neu, aenderungen = zusammenfuehren(alt, tarife, soli, bbg)
    if not aenderungen:
        print("\nAlle geholten Werte stimmen mit der JSON überein.")
    else:
        print(f"\n{len(aenderungen)} Abweichung(en):")
        for a in aenderungen:
            print(f"  {a}")

    if not args.schreiben:
        print("\nNichts geschrieben (--schreiben fehlt).")
        return 0

    with open(pfad, "w", encoding="utf-8") as f:
        json.dump(neu, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n{pfad} geschrieben. Jetzt references/steuerwerte.md nachziehen und "
          f"python3 tests/run_tests.py laufen lassen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
