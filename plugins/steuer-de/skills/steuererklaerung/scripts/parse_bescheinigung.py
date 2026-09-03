#!/usr/bin/env python3
"""
parse_bescheinigung.py — Bescheinigungen lesen und die Steuerdaten füllen.

`assets/steuerdaten_vorlage.json` ist die Vorlage, die am Ende erfüllt sein soll.
Dieses Skript füllt sie aus den Dokumenten, statt den Nutzer abtippen zu lassen:
Lohnsteuerbescheinigung, Steuerbescheinigung der Depots, Beitragsbescheinigungen
der Versicherungen.

    python3 scripts/parse_bescheinigung.py lohnsteuerbescheinigung.pdf \\
            steuerbescheinigung.pdf --steuerdaten steuerdaten.json

**Die tragende Regel:** Ein Feld wird nur übernommen, wenn **Nummer und
Beschriftung zusammenpassen**. Die Feldnummern der Lohnsteuerbescheinigung
stammen aus einer Musterbekanntmachung des BMF, nicht aus dem Gesetz — § 41b EStG
bestätigt Inhalt und Reihenfolge, aber nicht die Nummerierung. Ändert das BMF sie
oder druckt ein Arbeitgeber ein abweichendes Formular, fällt das damit auf,
statt einen falschen Betrag in die Steuererklärung zu tragen. Ein still
vertauschter Bruttoarbeitslohn wäre der teuerste Fehler dieses Werkzeugs.

Weiter gilt die Hausregel: **nie still eine 0**. Was nicht sicher gefunden wird,
bleibt leer und wird gemeldet. Und **nie still überschreiben**: ein schon
belegtes Feld kann von Hand geprüft worden sein.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from decimal import Decimal as D
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steuerlib as sl  # noqa: E402

HIER = os.path.dirname(os.path.abspath(__file__))
PROFILVERZEICHNIS = os.path.join(HIER, "profiles", "bescheinigungen")
VORLAGE = os.path.join(HIER, "..", "assets", "steuerdaten_vorlage.json")

# Der Beitragssatz zur allgemeinen Rentenversicherung liegt seit Jahren bei
# 18,6 %. Er dient hier nur der Plausibilitätsprüfung, nicht der Berechnung —
# deshalb eine großzügige Spanne statt eines exakten Werts.
RV_SATZ = D("0.186")
RV_TOLERANZ = D("0.35")   # ±35 %: fängt Größenordnungen, nicht Feinheiten


class BescheinigungFehler(RuntimeError):
    """Das Dokument konnte nicht sicher gelesen werden."""


def lade_profile() -> list:
    profile = []
    if not os.path.isdir(PROFILVERZEICHNIS):
        return profile
    for name in sorted(os.listdir(PROFILVERZEICHNIS)):
        if name.endswith(".json"):
            with open(os.path.join(PROFILVERZEICHNIS, name), encoding="utf-8") as f:
                profile.append(json.load(f))
    return profile


def erkenne(text: str, profile: list) -> Optional[dict]:
    """Das Profil, dessen Erkennungsmerkmale passen — und nur dieses eine.

    `erkennung_nicht` schließt aus. Das ist hier nötig, nicht schmückend:
    „Lohnsteuerbescheinigung“ enthält „Steuerbescheinigung“ als Teilstring, und
    die Lohnsteuerbescheinigung nennt Kranken- und Pflegeversicherung. Ohne
    Ausschluss griffe das falsche Profil, und der Bruttoarbeitslohn landete in
    den Kapitalerträgen.

    Passen mehrere Profile gleich gut, wird keines genommen — lieber nachfragen
    als das falsche Dokument auswerten.
    """
    klein = text.lower()
    passend = []
    for profil in profile:
        marker = profil.get("erkennung", [])
        if not all(m.lower() in klein for m in marker):
            continue
        if any(n.lower() in klein for n in profil.get("erkennung_nicht", [])):
            continue
        passend.append((len(marker), profil))
    if not passend:
        return None
    passend.sort(key=lambda p: p[0], reverse=True)
    if len(passend) > 1 and passend[0][0] == passend[1][0]:
        return None
    return passend[0][1]


_BETRAG_MUSTER = re.compile(r"-?\d{1,3}(?:[.\s]\d{3})*,\d{2}")


def _betrag_der_zeile(zeile: str) -> Optional[D]:
    """Der eine Betrag einer Zeile — oder None, wenn es keinen eindeutigen gibt.

    Aussortiert werden zwei Sorten Zahlen, die keine Beträge sind:

      * **Prozentsätze.** Bescheinigungen nennen den Beitragssatz gern neben dem
        Betrag („14,6 % 3.200,00“).
      * **Werte in Klammern.** Das sind Vergleichs- oder Vorjahresangaben
        („18.420,00 EUR (Vorjahr 17.900,00)“). Die letzte Zahl der Zeile zu
        nehmen hieße hier, den Vorjahreswert in die Steuererklärung zu tragen —
        und das sähe im Ergebnis völlig plausibel aus.

    Bleiben danach mehrere Kandidaten, wird **nicht** geraten: der Aufrufer
    meldet die Zeile als mehrdeutig.
    """
    kandidaten = []
    for m in _BETRAG_MUSTER.finditer(zeile):
        rest = zeile[m.end():]
        if re.match(r"\s*%", rest):
            continue
        davor, danach = zeile[:m.start()], zeile[m.end():]
        if davor.count("(") > davor.count(")") and ")" in danach:
            continue  # steht innerhalb einer Klammer
        kandidaten.append(m.group(0))
    if len(kandidaten) != 1:
        return None
    try:
        return sl.to_decimal(kandidaten[0], locale_hint="de")
    except sl.ParseError:
        return None


def _zeile_zur_nummer(text: str, nummer: str) -> Optional[str]:
    """Die Zeile, die mit dieser Feldnummer beginnt."""
    muster = re.compile(rf"^\s*{re.escape(nummer)}\s*[.)]\s+(.*)$", re.M)
    treffer = muster.findall(text)
    return treffer[0] if len(treffer) == 1 else None


def _zeile_zur_beschriftung(text: str, worte: list, nicht: list) -> Optional[str]:
    """Die eine Zeile, die alle Worte enthält und keines der Ausschlusswörter.

    Für Bescheinigungen ohne Feldnummern — Banken und Versicherungen
    nummerieren nicht. Trifft mehr als eine Zeile zu, ist es nicht eindeutig,
    und es wird nichts übernommen: „Kapitalertragsteuer“ steht auch in
    „Kirchensteuer zur Kapitalertragsteuer“.

    Beträge stehen manchmal in der Folgezeile, wenn die Beschriftung umbricht —
    deshalb wird die nächste Zeile mitgegeben, falls die eigene keinen enthält.
    """
    zeilen = text.splitlines()
    treffer = []
    for i, zeile in enumerate(zeilen):
        klein = zeile.lower()
        if not all(w.lower() in klein for w in worte):
            continue
        if any(n.lower() in klein for n in nicht):
            continue
        kandidat = zeile
        if _betrag_der_zeile(zeile) is None and i + 1 < len(zeilen):
            kandidat = zeile + " " + zeilen[i + 1]
        # Nur Zeilen mit Betrag zählen als Treffer. Überschriften enthalten die
        # Beschriftung ebenfalls ("Bescheinigung über Beiträge zur Kranken- und
        # Pflegeversicherung") und machten die Suche sonst mehrdeutig.
        if _betrag_der_zeile(kandidat) is not None:
            treffer.append(kandidat)
    return treffer[0] if len(treffer) == 1 else None


def extrahiere(text: str, profil: dict) -> tuple:
    """Dokument → (Werte je Zielpfad, Meldungen).

    Übernommen wird nur, was eindeutig ist: die Feldnummer muss genau einmal
    vorkommen, und die Beschriftung dieser Zeile muss zum Profil passen.
    """
    if profil is None:
        raise BescheinigungFehler(
            "Kein Profil passt auf dieses Dokument. Bekannt sind: "
            + ", ".join(p["id"] for p in lade_profile()))
    werte: dict = {}
    meldungen: list = []

    for feld in profil["felder"]:
        ziel = feld["ziel"]
        nummer = feld.get("nummer")
        if nummer:
            zeile = _zeile_zur_nummer(text, nummer)
        else:
            # Ohne Feldnummer trägt allein die Beschriftung; sie muss dann
            # eindeutig sein.
            nummer = "„" + feld["beschriftung"][0] + "“"
            zeile = _zeile_zur_beschriftung(text, feld["beschriftung"],
                                            feld.get("nicht", []))
        if zeile is None:
            meldungen.append(f"Feld {nummer} ({ziel}): nicht gefunden oder mehrdeutig — "
                             f"nichts übernommen.")
            continue
        fehlend = [w for w in feld["beschriftung"] if w.lower() not in zeile.lower()]
        if fehlend:
            meldungen.append(
                f"Feld {nummer}: die Beschriftung passt nicht (erwartet "
                f"{', '.join(feld['beschriftung'])}; gelesen „{zeile.strip()[:60]}“). "
                f"Nichts übernommen — die Feldnummern stammen aus einer "
                f"BMF-Bekanntmachung und können sich ändern.")
            continue
        betrag = _betrag_der_zeile(zeile)
        if betrag is None:
            meldungen.append(f"Feld {nummer} ({ziel}): kein Betrag in der Zeile lesbar.")
            continue
        if feld.get("summieren"):
            werte[ziel] = werte.get(ziel, D("0")) + betrag
        else:
            werte[ziel] = betrag

    meldungen += _pruefe(profil, werte)
    return werte, meldungen


def _pruefe(profil: dict, werte: dict) -> list:
    """Plausibilitätsprüfungen des Profils. Sie melden, sie verwerfen nicht."""
    meldungen = []
    for pruefung in profil.get("pruefungen", []):
        if pruefung.get("art") == "hinweis":
            meldungen.append(pruefung["text"])
            continue
        if pruefung.get("art") == "anteil_plausibel":
            meldungen += _pruefe_anteil(pruefung, werte)
            continue
        if pruefung.get("art") != "rentenbeitrag_plausibel":
            continue
        brutto, beitrag = werte.get(pruefung["brutto"]), werte.get(pruefung["beitrag"])
        if brutto is None or beitrag is None or brutto <= 0:
            continue
        jahr = max(sl.TARIF) if sl.TARIF else None
        bbg = (sl._WERTE.get(jahr) or {}).get("bbg_allgemein") if jahr else None
        grundlage = min(brutto, bbg) if bbg else brutto
        erwartet = grundlage * RV_SATZ
        if erwartet > 0 and abs(beitrag - erwartet) / erwartet > RV_TOLERANZ:
            meldungen.append(
                f"Der Beitrag zur Rentenversicherung ({sl.fmt_eur(beitrag)}) passt nicht "
                f"zum Bruttoarbeitslohn: erwartet wären rund {sl.fmt_eur(erwartet)} "
                f"({RV_SATZ * 100:.1f} % auf {sl.fmt_eur(grundlage)}). Bitte die Nummern "
                f"22a und 23a im Dokument nachsehen — ein Zahlendreher wäre hier teuer.")
    return meldungen


def _pruefe_anteil(pruefung: dict, werte: dict) -> list:
    """Ein Betrag muss ein bekannter Anteil eines anderen sein.

    Für die Kirchensteuer: 8 oder 9 Prozent der Lohnsteuer. Solche festen
    Verhältnisse sind die schärfsten Prüfungen, die ohne Zusatzwissen möglich
    sind — sie fangen eine verlesene Zeile, deren Betrag für sich genommen
    plausibel aussieht.
    """
    wert, basis = werte.get(pruefung["wert"]), werte.get(pruefung["basis"])
    if wert is None or basis is None or basis <= 0 or wert == 0:
        return []
    toleranz = D(pruefung.get("toleranz", "0.02"))
    anteil = wert / basis
    if any(abs(anteil - D(satz)) <= toleranz for satz in pruefung["saetze"]):
        return []
    erwartet = " oder ".join(f"{D(s) * 100:.0f} %" for s in pruefung["saetze"])
    return [f"{pruefung['bezeichnung']}: {sl.fmt_eur(wert)} sind {anteil * 100:.1f} % "
            f"von {sl.fmt_eur(basis)} — erwartet wären {erwartet}. "
            f"{pruefung.get('warum', '')}".strip()]


def _setze(daten: dict, pfad: str, wert: str) -> None:
    teile = pfad.split(".")
    ziel = daten
    for t in teile[:-1]:
        ziel = ziel.setdefault(t, {})
    ziel[teile[-1]] = wert


def _hole(daten: dict, pfad: str):
    ziel = daten
    for t in pfad.split("."):
        if not isinstance(ziel, dict) or t not in ziel:
            return None
        ziel = ziel[t]
    return ziel


def _ist_leer(wert) -> bool:
    if wert in (None, ""):
        return True
    try:
        return sl.to_decimal(wert) == 0
    except sl.ParseError:
        return False


def fuelle(steuerdaten: dict, werte: dict, ueberschreiben: bool = False) -> list:
    """Werte in die Steuerdaten eintragen. Gibt die Änderungen und Konflikte zurück.

    Ein leeres Feld wird gefüllt. Ein belegtes Feld mit **gleichem** Wert ist
    keine Änderung. Ein belegtes Feld mit abweichendem Wert ist ein Konflikt: er
    wird gemeldet, aber nicht überschrieben — der vorhandene Wert kann von Hand
    geprüft worden sein.
    """
    aenderungen = []
    for pfad, betrag in sorted(werte.items()):
        neu = format(sl.q2(betrag), "f")
        alt = _hole(steuerdaten, pfad)
        if alt is not None and not _ist_leer(alt):
            try:
                gleich = sl.to_decimal(alt) == sl.q2(betrag)
            except sl.ParseError:
                gleich = False
            if gleich:
                continue
            if not ueberschreiben:
                aenderungen.append(
                    f"Konflikt bei {pfad}: eingetragen {alt}, im Dokument {neu}. "
                    f"Nicht überschrieben — mit --ueberschreiben erzwingen.")
                continue
        _setze(steuerdaten, pfad, neu)
        aenderungen.append(f"{pfad} = {neu}")
    return aenderungen


def fehlende_felder(steuerdaten: dict, beantwortet=None, prefix: str = "") -> list:
    """Was in den Steuerdaten noch offen ist — die Verbindung zurück zu /einstieg.

    `beantwortet` sind die Pfade, die in diesem Lauf aus einem Dokument belegt
    wurden. Sie zählen als beantwortet, **auch wenn der Betrag 0,00 € ist**: eine
    Bescheinigung, die für den Solidaritätszuschlag null ausweist, hat die Frage
    beantwortet. Sie danach als Lücke zu führen schickte den Nutzer eine
    Bescheinigung suchen, die er gerade eingelesen hat.
    """
    beantwortet = beantwortet or set()
    offen = []
    for k, v in steuerdaten.items():
        pfad = f"{prefix}{k}"
        if isinstance(v, dict):
            offen += fehlende_felder(v, beantwortet, f"{pfad}.")
        elif isinstance(v, str) and _ist_leer(v) and pfad not in beantwortet:
            offen.append(pfad)
    return offen


# Ein Scan liefert über die reine Textebene fast nichts. Unterhalb dieser Grenze
# lohnt der Umweg über die Tabellenerkennung samt OCR aus parse_pdf.py.
MINDESTZEICHEN = 200


def text_aus_datei(pfad: str) -> str:
    """Text eines Belegs — mit OCR-Rückfall für gescannte Dokumente.

    Wirft bei einem Lesefehler, statt leeren Text zu liefern: eine unlesbare
    Datei ist etwas anderes als eine leere, und der Unterschied muss beim
    Aufrufer ankommen.
    """
    if not pfad.lower().endswith(".pdf"):
        with open(pfad, encoding="utf-8") as f:
            return f.read()
    try:
        import fitz  # PyMuPDF, wie in parse_pdf.py
    except ImportError as e:
        raise BescheinigungFehler(
            "Zum Lesen des PDF fehlt PyMuPDF — `pip install pymupdf`.") from e
    try:
        with fitz.open(pfad) as doc:
            text = "\n".join(seite.get_text() for seite in doc)
    except Exception as e:
        raise BescheinigungFehler(f"{os.path.basename(pfad)} lässt sich nicht "
                                  f"lesen: {e}") from e

    if len(text.strip()) >= MINDESTZEICHEN:
        return text
    # Zu wenig Text für ein Formular: vermutlich ein Scan. parse_pdf.py bringt
    # die Backends samt OCR mit — dieselbe Bibliothek, dieselben Optionen.
    try:
        import parse_pdf
        auszug = parse_pdf.extract(pfad, backend="auto")
        seiten = auszug.get("pages") or []
        ocr = "\n".join(s.get("text", "") for s in seiten)
        if len(ocr.strip()) > len(text.strip()):
            return ocr
    except Exception:
        # OCR ist eine Zugabe. Schlägt sie fehl, gilt der Text, den es gibt —
        # und der Aufrufer meldet ihn als zu dünn.
        pass
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Bescheinigungen lesen und steuerdaten.json daraus füllen")
    ap.add_argument("dokumente", nargs="+", help="PDF oder Text")
    ap.add_argument("--steuerdaten", default="steuerdaten.json",
                    help="wird angelegt, wenn sie fehlt")
    ap.add_argument("--ueberschreiben", action="store_true",
                    help="belegte Felder ersetzen statt Konflikt melden")
    args = ap.parse_args(argv)

    if os.path.exists(args.steuerdaten):
        with open(args.steuerdaten, encoding="utf-8") as f:
            sd = json.load(f)
    else:
        with open(os.path.normpath(VORLAGE), encoding="utf-8") as f:
            sd = json.load(f)
        print(f"{args.steuerdaten} gibt es noch nicht — aus der Vorlage angelegt.")

    profile = lade_profile()
    beantwortet: set = set()
    for pfad in args.dokumente:
        try:
            text = text_aus_datei(pfad)
            profil = erkenne(text, profile)
            werte, meldungen = extrahiere(text, profil)
        except (BescheinigungFehler, OSError) as e:
            print(f"\n{os.path.basename(pfad)}: FEHLER — {e}", file=sys.stderr)
            continue

        print(f"\n{os.path.basename(pfad)} → {profil['titel']}")
        beantwortet |= set(werte)
        for a in fuelle(sd, werte, args.ueberschreiben):
            print(f"  {a}")
        for m in meldungen:
            print(f"  ! {m}")

    with open(args.steuerdaten, "w", encoding="utf-8") as f:
        json.dump(sd, f, ensure_ascii=False, indent=2)
        f.write("\n")

    offen = fehlende_felder(sd, beantwortet)
    print(f"\n{args.steuerdaten} geschrieben.")
    if offen:
        print(f"\nNoch offen ({len(offen)} Felder) — von Hand ergänzen oder die "
              f"passende Bescheinigung nachreichen:")
        for p in offen:
            print(f"  · {p}")
    else:
        print("\nAlle Felder der Vorlage sind belegt.")
    print("\nDie Datei enthält echte Steuerdaten — sie gehört in kein Repository "
          "und in keine Cloud-Freigabe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
