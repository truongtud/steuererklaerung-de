#!/usr/bin/env python3
"""
build_taxreport.py — Setzt aus Steuerdaten + Krypto-Ergebnis(sen) einen vollständigen
TaxReport (alle Anlagen) zusammen, schätzt Einkommensteuer (§ 32a EStG), Soli,
Kirchensteuer und die Abgeltungsteuer (§ 32d EStG), rechnet die einbehaltenen
Steuern an (Nachzahlung/Erstattung) und erzeugt das ELSTER-Feld-Mapping.

Eingabe: steuerdaten.json  (Schema siehe references/anlagen-referenz.md)
         transactions.json (kanonisch) ODER ein/mehrere krypto-Ergebnisse.

Aufruf:
    python scripts/build_taxreport.py steuerdaten.json \
        [--transactions t.json] [--krypto-result k1.json k2.json ...] \
        [--strict] -o taxreport.json

Unbekannte Schlüssel in den Steuerdaten (Tippfehler) werden gemeldet — auf stderr
und im Report unter 'warnungen'/'eingabepruefung'; mit --strict endet der Lauf
zusätzlich mit Rückgabecode 3. Ein festgestellter § 23-Verlustvortrag der Vorjahre
kommt über 'anlage_so.verlustvortrag_23_vorjahr' herein, der Aktien-Verlustvortrag
über 'anlage_kap.verlustvortrag_aktien_vorjahr'.

Ausgabe: taxreport.json  — strukturierter Report, den export_report.py rendert.

Alle jahresabhängigen Werte und der Tarif kommen aus scripts/steuerlib.py — hier
stehen bewusst KEINE Steuerkonstanten mehr.

WICHTIG: Die Steuerschätzung ist eine grobe Orientierung, ersetzt KEINE
Steuerberatung und keine ELSTER-Berechnung.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from steuerlib import (  # noqa: E402
    FREIGRENZE_22_3,
    SONDERAUSGABEN_PB,
    TARIF,
    ParseError,
    an_pauschbetrag,
    est_tarif,
    fmt_eur,
    freigrenze_23,
    normiere_kirchensteuersatz,
    q2,
    soli,
    sparer_pauschbetrag,
    to_decimal,
)

getcontext().prec = 30
D = Decimal
NULL = D("0")

ELSTER_CAVEAT = ("Zeilennummern sind Orientierung — ELSTER ändert die Layouts jährlich. "
                 "Jede Zeile vor dem Absenden im Formular gegenprüfen.")
JSTG_2024_HINWEIS = (
    "§ 20 Abs. 6 Sätze 5 und 6 EStG (eigener Verrechnungskreis und 20.000-€-Deckel für "
    "Termingeschäfte) wurden durch das Jahressteuergesetz 2024 aufgehoben — anwendbar in "
    "allen offenen Fällen. Verluste aus Termingeschäften sind daher mit sämtlichen "
    "Kapitalerträgen verrechenbar; beschränkt bleibt nur der Aktien-Verlusttopf "
    "(§ 20 Abs. 6 Satz 4 EStG).")


class EingabeFehler(ValueError):
    """Eingabedatei ist unvollständig oder falsch strukturiert (klare Meldung statt Traceback)."""


# ─────────────────────────────────────────────────────────────────────────────
# Eingabe-Validierung
# ─────────────────────────────────────────────────────────────────────────────

# Alle Schlüssel, die dieses Skript aus den Steuerdaten überhaupt liest. Was hier
# fehlt, wird von build() nie angefasst — ein Tippfehler wie "brutto_arbeitslohn"
# würde sonst spurlos verschwinden und der Betrag still zu 0,00 € werden. Das ist
# genau der Fehler, den steuerlib.py bei unlesbaren Beträgen verhindert; für
# *unbekannte* Schlüssel übernimmt das die Prüfung unten.
FELDER_OBERSTE_EBENE = {
    "steuerjahr", "tax_year", "zusammenveranlagung", "steuerpflichtiger",
    "anlage_n", "anlage_kap", "anlage_so", "anlage_v", "anlage_s", "anlage_g",
    "vorsorge", "sonderausgaben", "aussergewoehnliche_belastungen", "kinder",
    "krypto_transaktionen",
}

FELDER_JE_BLOCK = {
    "steuerpflichtiger": {
        "name", "vorname", "verheiratet", "kirchensteuersatz", "geburtsdatum",
        "steuer_id", "steueridentifikationsnummer", "identifikationsnummer", "idnr",
        "steuernummer", "finanzamt", "adresse", "bundesland", "konfession",
    },
    "anlage_n": {"bruttoarbeitslohn", "lohnsteuer", "soli", "kirchensteuer",
                 "werbungskosten"},
    "anlage_kap": {"kapitalertraege", "gewinn_aktien", "verlust_aktien",
                   "verlust_termingeschaefte", "anrechenbare_kest",
                   "auslaendische_quellensteuer", "verlustvortrag_aktien_vorjahr"},
    "anlage_so": {"sonstige_einkuenfte", "verlustvortrag_23_vorjahr"},
    "anlage_v": {"einkuenfte"},
    "anlage_s": {"gewinn"},
    "anlage_g": {"gewinn"},
    "aussergewoehnliche_belastungen": {"anzusetzen"},
}

# Absichtlich frei benennbare Positions-Dicts — hier ist JEDER Schlüssel gültig
# (er wird summiert und ins ELSTER-Mapping übernommen), also wird nicht gewarnt.
FREIFORM_BLOECKE = ("anlage_n.werbungskosten", "vorsorge", "sonderausgaben")


def _aehnlichstes(key: str, bekannt) -> str | None:
    treffer = difflib.get_close_matches(str(key), sorted(bekannt), n=1, cutoff=0.6)
    return treffer[0] if treffer else None


def pruefe_unbekannte_felder(steuerdaten: dict) -> list:
    """Findet Schlüssel, die kein Leser dieses Skripts auswertet.

    Rückgabe: Liste von Befunden mit 'pfad', 'block', 'feld', 'vorschlag' und der
    fertigen deutschen 'meldung'. Die Freiform-Blöcke aus FREIFORM_BLOECKE werden
    bewusst NICHT geprüft.
    """
    befunde: list = []
    if not isinstance(steuerdaten, dict):
        return befunde

    def pruefe(mapping, bekannt, block: str, prefix: str):
        if not isinstance(mapping, dict):
            return
        for key in mapping:
            if key in bekannt:
                continue
            pfad = f"{prefix}{key}"
            ort = "" if prefix else " (oberste Ebene)"
            vorschlag = _aehnlichstes(key, bekannt)
            if vorschlag:
                meldung = (f"Unbekanntes Feld '{pfad}'{ort} — meintest du "
                           f"'{vorschlag}'? Der Wert wurde IGNORIERT.")
            else:
                meldung = (f"Unbekanntes Feld '{pfad}'{ort} — kein ähnlich geschriebenes "
                           f"Feld bekannt. Bekannt sind hier: "
                           f"{', '.join(sorted(bekannt))}. Der Wert wurde IGNORIERT.")
            befunde.append({"block": block, "feld": key, "pfad": pfad,
                            "vorschlag": vorschlag, "meldung": meldung})

    pruefe(steuerdaten, FELDER_OBERSTE_EBENE, "(oberste Ebene)", "")
    for block, bekannt in FELDER_JE_BLOCK.items():
        pruefe(steuerdaten.get(block), bekannt, block, f"{block}.")
    return befunde


def _betrag(wert, feld: str, default: Decimal = NULL) -> Decimal:
    """Liest einen Betrag oder wirft eine Meldung, die das Feld benennt."""
    if wert is None or wert == "":
        return default
    if isinstance(wert, (dict, list, tuple)):
        art = "Objekt" if isinstance(wert, dict) else "Liste"
        raise EingabeFehler(
            f"Feld '{feld}': erwartet wurde ein einzelner Betrag, gefunden wurde ein {art}. "
            f"Verschachtelte Angaben bitte zu einer Zahl zusammenfassen "
            f"(z. B. \"{feld}\": \"1234.56\").")
    if isinstance(wert, bool):
        raise EingabeFehler(f"Feld '{feld}': erwartet wurde ein Betrag, gefunden wurde {wert!r}.")
    try:
        return to_decimal(wert)
    except ParseError as e:
        raise EingabeFehler(f"Feld '{feld}': Betrag nicht lesbar ({wert!r}) — {e}")


def _summe_positionen(mapping, feld: str) -> Decimal:
    """Summiert ein Positionen-Dict (Werbungskosten, Vorsorge, Sonderausgaben)."""
    if mapping in (None, ""):
        return NULL
    if not isinstance(mapping, dict):
        raise EingabeFehler(
            f"Feld '{feld}': erwartet wurde ein Objekt aus Position → Betrag, "
            f"gefunden wurde {type(mapping).__name__}.")
    total = NULL
    for key, val in mapping.items():
        total += _betrag(val, f"{feld}.{key}")
    return total


def _dict_feld(daten: dict, name: str) -> dict:
    v = daten.get(name)
    if v in (None, ""):
        return {}
    if not isinstance(v, dict):
        raise EingabeFehler(
            f"Feld '{name}': erwartet wurde ein Objekt, gefunden wurde {type(v).__name__}.")
    return v


def lies_steuerjahr(steuerdaten: dict) -> int:
    roh = steuerdaten.get("steuerjahr", steuerdaten.get("tax_year"))
    if roh in (None, ""):
        raise EingabeFehler(
            "Pflichtfeld 'steuerjahr' fehlt in den Steuerdaten — ohne Jahr sind Tarif, "
            "Pauschbeträge und Freigrenzen nicht bestimmbar (z. B. \"steuerjahr\": 2024).")
    try:
        jahr = int(str(roh).strip())
    except (TypeError, ValueError):
        raise EingabeFehler(f"Feld 'steuerjahr': {roh!r} ist keine Jahreszahl (erwartet z. B. 2024).")
    if not (1990 <= jahr <= 2100):
        raise EingabeFehler(f"Feld 'steuerjahr': {jahr} liegt außerhalb des plausiblen Bereichs.")
    return jahr


def pruefe_kinder(kinder) -> list:
    if kinder in (None, ""):
        return []
    if not isinstance(kinder, list):
        raise EingabeFehler(
            f"Feld 'kinder': erwartet wurde eine Liste von Objekten, gefunden wurde "
            f"{type(kinder).__name__}.")
    for i, kind in enumerate(kinder):
        if not isinstance(kind, dict):
            raise EingabeFehler(
                f"Feld 'kinder[{i}]': erwartet wurde ein Objekt mit 'name' und "
                f"'geburtsdatum', gefunden wurde {type(kind).__name__} ({kind!r}). "
                f"Beispiel: {{\"name\": \"Anna\", \"geburtsdatum\": \"2015-04-02\"}}.")
    return kinder


# ─────────────────────────────────────────────────────────────────────────────
# Krypto-Quellen: Normierung, Zusammenführung, Freigrenzen
# ─────────────────────────────────────────────────────────────────────────────


def _wahr(wert) -> bool:
    if isinstance(wert, str):
        return wert.strip().lower() in ("true", "ja", "1", "yes")
    return bool(wert)


def _erst(mapping: dict, *namen):
    """Erster im Dict tatsächlich belegter Schlüssel (None/'' gelten als unbelegt)."""
    for name in namen:
        wert = mapping.get(name)
        if wert not in (None, ""):
            return wert
    return None


def normiere_krypto_quelle(roh, herkunft: str = "krypto-result") -> dict:
    """Prüft eine Quell-JSON gegen den Quellen-Contract und vereinheitlicht die Felder.

    Akzeptiert 'paragraph_22_nr3' (Contract) wie auch 'paragraph_22_nr_3' (Altbestand).
    """
    if not isinstance(roh, dict):
        raise EingabeFehler(
            f"Krypto-Quelle '{herkunft}': erwartet wurde ein JSON-Objekt, gefunden wurde "
            f"{type(roh).__name__}.")
    p23 = roh.get("paragraph_23")
    if p23 is None:
        raise EingabeFehler(
            f"Krypto-Quelle '{herkunft}': Pflichtfeld 'paragraph_23' fehlt. Erwartet wird das "
            f"Ergebnisformat mit 'steuerjahr', 'paragraph_23' und 'paragraph_22_nr3' "
            f"(siehe krypto_fifo.py / die Broker-Parser).")
    if not isinstance(p23, dict):
        raise EingabeFehler(
            f"Krypto-Quelle '{herkunft}': Feld 'paragraph_23' muss ein Objekt sein, gefunden "
            f"wurde {type(p23).__name__}.")
    p22 = roh.get("paragraph_22_nr3", roh.get("paragraph_22_nr_3")) or {}
    if not isinstance(p22, dict):
        raise EingabeFehler(
            f"Krypto-Quelle '{herkunft}': Feld 'paragraph_22_nr3' muss ein Objekt sein, "
            f"gefunden wurde {type(p22).__name__}.")

    jahr_roh = _erst(roh, "steuerjahr", "tax_year")
    jahr = None
    if jahr_roh is not None:
        try:
            jahr = int(str(jahr_roh).strip())
        except (TypeError, ValueError):
            raise EingabeFehler(
                f"Krypto-Quelle '{herkunft}': 'steuerjahr' ist keine Jahreszahl ({jahr_roh!r}).")

    # Netto § 23: bevorzugt ausgewiesen, sonst aus Gewinn/Verlust rekonstruiert.
    if _erst(p23, "netto_ergebnis_eur") is not None:
        netto = _betrag(p23["netto_ergebnis_eur"], f"{herkunft}.paragraph_23.netto_ergebnis_eur")
    elif _erst(p23, "gewinn_eur", "verlust_eur") is not None:
        gewinn = _betrag(p23.get("gewinn_eur"), f"{herkunft}.paragraph_23.gewinn_eur")
        verlust = _betrag(p23.get("verlust_eur"), f"{herkunft}.paragraph_23.verlust_eur")
        netto = gewinn - abs(verlust)
    else:
        netto = _betrag(p23.get("steuerpflichtiger_betrag_eur"),
                        f"{herkunft}.paragraph_23.steuerpflichtiger_betrag_eur")

    fa = p23.get("freigrenze_angewendet")
    if fa is None:
        # Altformat: wer 'freigrenze_ueberschritten' ausweist, hat sie auch angewendet.
        fa = "freigrenze_ueberschritten" in p23
    else:
        fa = _wahr(fa)

    p22_summe = _betrag(_erst(p22, "summe_eur", "summe_zufluesse_eur"),
                        f"{herkunft}.paragraph_22_nr3.summe_eur")

    # Die Schreibweise unterscheidet sich je Produzent: krypto_fifo.py schreibt den
    # Wert auf oberster Ebene und als 'summe_steuerfrei_gt_1_jahr_eur', die
    # Broker-Parser dagegen als 'paragraph_23.steuerfrei_langfristig_eur'. Fehlt der
    # letzte Name hier, wird der steuerfreie Anteil still zu 0.
    steuerfrei = _erst(roh, "steuerfrei_langfristig_eur") or _erst(
        p23, "summe_steuerfrei_gt_1_jahr_eur", "steuerfrei_langfristig_eur")

    vortrag = _betrag(p23.get("verlustvortrag_eur"),
                      f"{herkunft}.paragraph_23.verlustvortrag_eur")
    if vortrag == 0 and fa and netto < 0:
        # Vorberechnete Quelle ohne ausgewiesenen Vortrag: Verlust nicht verschlucken.
        vortrag = -netto

    warn = list(roh.get("warnungen") or [])
    extra = list(roh.get("elster_extra") or [])

    return {
        "quelle": roh.get("quelle") or roh.get("source") or herkunft,
        "datei": herkunft,
        "steuerjahr": jahr,
        "netto_23": netto,
        "steuerpflichtig_23": _betrag(p23.get("steuerpflichtiger_betrag_eur"),
                                      f"{herkunft}.paragraph_23.steuerpflichtiger_betrag_eur"),
        "verlustvortrag_23": vortrag,
        "freigrenze_angewendet": fa,
        "gewinne_23": _betrag(_erst(p23, "gewinn_eur", "summe_steuerpflichtige_gewinne_eur"),
                              f"{herkunft}.paragraph_23.gewinn_eur"),
        "verluste_23": abs(_betrag(_erst(p23, "verlust_eur", "summe_verluste_eur"),
                                   f"{herkunft}.paragraph_23.verlust_eur")),
        "steuerfrei_langfristig": _betrag(steuerfrei, f"{herkunft}.steuerfrei_langfristig_eur"),
        "disposals": list(p23.get("disposals") or []),
        "p22_summe": p22_summe,
        "p22_steuerpflichtig": _betrag(p22.get("steuerpflichtig_eur"),
                                       f"{herkunft}.paragraph_22_nr3.steuerpflichtig_eur"),
        "p22_freigrenze_angewendet": _wahr(p22.get("freigrenze_angewendet")),
        "p22_ertraege": list(p22.get("ertraege") or []),
        "warnungen": warn,
        "elster_extra": extra,
        "koinly_extra": roh.get("koinly_extra"),
        "hinweise": list(roh.get("hinweise") or []),
    }


def lade_krypto_quellen(pfade) -> list:
    quellen = []
    for p in pfade or []:
        try:
            with open(p, encoding="utf-8") as f:
                roh = json.load(f)
        except FileNotFoundError:
            raise EingabeFehler(f"Krypto-Quelle nicht gefunden: {p}")
        except json.JSONDecodeError as e:
            raise EingabeFehler(f"Krypto-Quelle '{p}' ist kein gültiges JSON: {e}")
        quellen.append(normiere_krypto_quelle(roh, herkunft=str(p)))
    return quellen


def aggregiere_krypto(quellen: list, jahr: int, werte_jahr: int) -> dict:
    """Führt beliebig viele Quellen zusammen und wendet die § 23-Freigrenze EINMAL an.

    Die Freigrenze des § 23 Abs. 3 Satz 5 EStG gilt pro Person und Jahr über *alle*
    Broker hinweg — sie je Teilreport anzuwenden erklärt zwei knapp unterschwellige
    Ergebnisse fälschlich für steuerfrei.
    """
    warnungen: list = []
    jahre = sorted({q["steuerjahr"] for q in quellen if q["steuerjahr"] is not None})
    if len(jahre) > 1:
        details = ", ".join(f"{q['datei']}: {q['steuerjahr']}" for q in quellen
                            if q["steuerjahr"] is not None)
        raise EingabeFehler(
            "Die angegebenen Krypto-Quellen gehören zu verschiedenen Steuerjahren "
            f"({details}). Ein Report kann nur ein Steuerjahr abbilden — Quellen trennen.")
    if jahre and jahre[0] != jahr:
        warnungen.append(
            f"Krypto-Quellen weisen das Steuerjahr {jahre[0]} aus, die Steuerdaten "
            f"jedoch {jahr} — bitte prüfen, ob die richtigen Dateien übergeben wurden.")

    fg = freigrenze_23(werte_jahr)
    vorberechnet = [q for q in quellen if q["freigrenze_angewendet"]]
    roh = [q for q in quellen if not q["freigrenze_angewendet"]]

    netto_roh = sum((q["netto_23"] for q in roh), NULL)
    netto_vor = sum((q["netto_23"] for q in vorberechnet), NULL)
    netto_gesamt = netto_roh + netto_vor

    pflichtig_roh = netto_roh if netto_roh >= fg else NULL
    vortrag_roh = -netto_roh if netto_roh < 0 else NULL
    pflichtig_vor = sum((q["steuerpflichtig_23"] for q in vorberechnet), NULL)
    vortrag_vor = sum((q["verlustvortrag_23"] for q in vorberechnet), NULL)

    if len(vorberechnet) > 1:
        warnungen.append(
            f"{len(vorberechnet)} Quellen haben die § 23-Freigrenze bereits selbst angewendet "
            f"({', '.join(q['quelle'] for q in vorberechnet)}). Die Freigrenze gilt aber nur "
            f"einmal je Person und Jahr — maßgeblich ist der Saldo aller Quellen; "
            f"Ergebnis prüfen.")
    if vorberechnet and roh:
        warnungen.append(
            "Es wurden vorberechnete und rohe Krypto-Quellen gemischt. Freigrenze und "
            "Verlustverrechnung wurden auf den Saldo aller Quellen angewendet — "
            "Gesamtergebnis manuell gegenprüfen.")

    steuerpflichtig = pflichtig_roh + pflichtig_vor
    verlustvortrag = vortrag_roh + vortrag_vor

    if len(quellen) > 1 and vorberechnet:
        # Sobald mehrere Quellen zusammenkommen, sind die je Quelle vorberechneten
        # Beträge nicht mehr addierbar: § 23 bildet je Person und Jahr EINEN Topf.
        # Gewinne und Verluste saldieren sich über alle Quellen, und die Freigrenze
        # entscheidet über den Saldo — sonst weist der Report zugleich
        # „Freigrenze überschritten: ja" und „steuerpflichtig 0,00 €" aus.
        neu_pflichtig = netto_gesamt if netto_gesamt >= fg else NULL
        neu_vortrag = -netto_gesamt if netto_gesamt < 0 else NULL
        if neu_pflichtig != steuerpflichtig or neu_vortrag != verlustvortrag:
            warnungen.append(
                f"Die vorberechneten § 23-Beträge wurden verworfen und aus dem Saldo aller "
                f"Quellen neu bestimmt ({fmt_eur(steuerpflichtig)} → {fmt_eur(neu_pflichtig)} "
                f"steuerpflichtig): Freigrenze und Verlustverrechnung gelten je Person und "
                f"Jahr über alle Broker, nicht je Datei.")
        steuerpflichtig, verlustvortrag = neu_pflichtig, neu_vortrag

    for q in quellen:
        warnungen.extend(q["warnungen"])

    elster_extra: list = []
    for q in quellen:
        elster_extra.extend(q["elster_extra"])

    disposals: list = []
    for q in quellen:
        disposals.extend(q["disposals"])

    p22_summe = sum((q["p22_summe"] for q in quellen), NULL)
    p22_ertraege: list = []
    for q in quellen:
        p22_ertraege.extend(q["p22_ertraege"])

    koinly_extra = None
    for q in quellen:
        if q["koinly_extra"]:
            koinly_extra = q["koinly_extra"]

    hinweise: list = []
    for q in quellen:
        for h in q["hinweise"]:
            if h not in hinweise:
                hinweise.append(h)

    summe_gewinne = sum((q["gewinne_23"] for q in quellen), NULL)
    summe_verluste = sum((q["verluste_23"] for q in quellen), NULL)
    p23 = {
        "freigrenze_eur": str(fg),
        "freigrenze_angewendet": True,
        "freigrenze_ueberschritten": bool(netto_gesamt >= fg),
        "anzahl_veraeusserungen": len(disposals),
        "summe_steuerpflichtige_gewinne_eur": str(q2(summe_gewinne)),
        "summe_verluste_eur": str(q2(-summe_verluste)),
        "gewinn_eur": str(q2(summe_gewinne)),
        # Vorzeichenkonvention aller Produzenten (krypto_fifo.py, parse_koinly.py,
        # parse_etoro.py): 'verlust_eur' ist die Summe der NEGATIVEN Ergebnisse und
        # trägt ein negatives Vorzeichen. summe_verluste ist hier positiv aufaddiert.
        "verlust_eur": str(q2(-summe_verluste)),
        "netto_ergebnis_eur": str(q2(netto_gesamt)),
        "summe_steuerfrei_gt_1_jahr_eur":
            str(q2(sum((q["steuerfrei_langfristig"] for q in quellen), NULL))),
        "steuerpflichtiger_betrag_eur": str(q2(steuerpflichtig)),
        "verlustvortrag_eur": str(q2(verlustvortrag)),
        "disposals": disposals,
    }
    return {
        "quellen": [{"quelle": q["quelle"], "datei": q["datei"], "steuerjahr": q["steuerjahr"],
                     "freigrenze_angewendet": q["freigrenze_angewendet"],
                     "netto_23_eur": str(q2(q["netto_23"]))} for q in quellen],
        "steuerjahr": jahre[0] if jahre else jahr,
        "paragraph_23": p23,
        "_p22_summe": p22_summe,
        "_p22_ertraege": p22_ertraege,
        "steuerfrei_langfristig_eur":
            str(q2(sum((q["steuerfrei_langfristig"] for q in quellen), NULL))),
        "elster_extra": elster_extra,
        "warnungen": warnungen,
        "hinweise": hinweise,
        "koinly_extra": koinly_extra,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────


def _als_quellenliste(krypto) -> list:
    """Akzeptiert eine Liste normierter Quellen, eine Liste roher Dicts oder ein Dict."""
    if krypto is None:
        return []
    if isinstance(krypto, dict):
        krypto = [krypto]
    quellen = []
    for i, q in enumerate(krypto):
        if isinstance(q, dict) and "netto_23" in q and "p22_summe" in q:
            quellen.append(q)  # bereits normiert
        else:
            quellen.append(normiere_krypto_quelle(q, herkunft=f"krypto-quelle[{i}]"))
    return quellen


def build(steuerdaten: dict, krypto=None):
    jahr = lies_steuerjahr(steuerdaten)
    tarif_hinterlegt = jahr in TARIF
    # Für Pauschbeträge/Freigrenzen auf das nächstgelegene hinterlegte Jahr ausweichen,
    # damit der Report auch für unbekannte Jahre gebaut wird — die ESt bleibt dann None.
    werte_jahr = jahr if tarif_hinterlegt else min(max(jahr, min(TARIF)), max(TARIF))

    tp = _dict_feld(steuerdaten, "steuerpflichtiger")
    verheiratet = bool(tp.get("verheiratet") or steuerdaten.get("zusammenveranlagung"))

    n = _dict_feld(steuerdaten, "anlage_n")
    kap = _dict_feld(steuerdaten, "anlage_kap")
    so_extra = _dict_feld(steuerdaten, "anlage_so")
    v = _dict_feld(steuerdaten, "anlage_v")
    s = _dict_feld(steuerdaten, "anlage_s")
    g = _dict_feld(steuerdaten, "anlage_g")
    vorsorge = _dict_feld(steuerdaten, "vorsorge")
    sonder = _dict_feld(steuerdaten, "sonderausgaben")
    agb = _dict_feld(steuerdaten, "aussergewoehnliche_belastungen")
    kinder = pruefe_kinder(steuerdaten.get("kinder"))

    hinweise = [ELSTER_CAVEAT, JSTG_2024_HINWEIS]
    warnungen: list = []

    # Unbekannte Schlüssel zuerst melden: ihr Wert geht in KEINE der folgenden
    # Rechnungen ein, und ohne Meldung sähe der Report exakt so aus, als wäre das
    # Feld nie eingegeben worden. Die Warnung geht auf stderr *und* in den Report.
    unbekannte_felder = pruefe_unbekannte_felder(steuerdaten)
    for befund in unbekannte_felder:
        print(f"WARNUNG: {befund['meldung']}", file=sys.stderr)
        warnungen.append(befund["meldung"])

    if not tarif_hinterlegt:
        warnungen.append(
            f"Für {jahr} ist kein § 32a-Tarif hinterlegt; Pauschbeträge und Freigrenzen wurden "
            f"ersatzweise mit den Werten für {werte_jahr} angesetzt.")

    # --- Kirchensteuersatz zuerst: 9 darf nicht das Neunfache der ESt ergeben ---
    try:
        kist_satz = normiere_kirchensteuersatz(tp.get("kirchensteuersatz"))
    except ParseError as e:
        raise EingabeFehler(f"Feld 'steuerpflichtiger.kirchensteuersatz': {e}")

    # --- Anlage N (nichtselbständige Arbeit) ---
    brutto = _betrag(n.get("bruttoarbeitslohn"), "anlage_n.bruttoarbeitslohn")
    wk_summe = _summe_positionen(n.get("werbungskosten"), "anlage_n.werbungskosten")
    an_pb = an_pauschbetrag(werte_jahr)
    # § 9a Satz 2 EStG: der Arbeitnehmer-Pauschbetrag darf nur bis zur Höhe der
    # Einnahmen abgezogen werden — er allein darf keinen Verlust erzeugen.
    wk_angesetzt = max(wk_summe, min(an_pb, brutto)) if brutto > 0 else wk_summe
    # KEIN max(..., 0): ein echter Werbungskostenüberhang mindert die Summe der Einkünfte.
    eink_n = brutto - wk_angesetzt
    lohnsteuer = _betrag(n.get("lohnsteuer"), "anlage_n.lohnsteuer")
    soli_einbehalten = _betrag(n.get("soli"), "anlage_n.soli")
    kist_einbehalten = _betrag(n.get("kirchensteuer"), "anlage_n.kirchensteuer")

    # --- Anlage KAP (Kapitalerträge, § 20 / § 32d EStG) ---
    kap_ertraege = _betrag(kap.get("kapitalertraege"), "anlage_kap.kapitalertraege")
    gewinn_aktien = _betrag(kap.get("gewinn_aktien"), "anlage_kap.gewinn_aktien")
    verlust_aktien = abs(_betrag(kap.get("verlust_aktien"), "anlage_kap.verlust_aktien"))
    verlust_termin = abs(_betrag(kap.get("verlust_termingeschaefte"),
                                 "anlage_kap.verlust_termingeschaefte"))
    anrechenbare_kest = _betrag(kap.get("anrechenbare_kest"), "anlage_kap.anrechenbare_kest")
    auslaendische_quellensteuer = _betrag(kap.get("auslaendische_quellensteuer"),
                                          "anlage_kap.auslaendische_quellensteuer")
    vv_aktien_vorjahr = abs(_betrag(kap.get("verlustvortrag_aktien_vorjahr"),
                                    "anlage_kap.verlustvortrag_aktien_vorjahr"))
    sparer_pb = sparer_pauschbetrag(werte_jahr, verheiratet)

    # 'gewinn_aktien' bemisst NUR den Aktien-Verlusttopf und geht selbst nicht in
    # die Bemessungsgrundlage ein. Wer den Gewinn ausschließlich hier einträgt,
    # bekommt ihn unversteuert — das darf nicht stillschweigend passieren.
    if gewinn_aktien > kap_ertraege:
        warnungen.append(
            f"'anlage_kap.gewinn_aktien' ({fmt_eur(gewinn_aktien)}) ist größer als "
            f"'anlage_kap.kapitalertraege' ({fmt_eur(kap_ertraege)}). 'gewinn_aktien' "
            f"dient AUSSCHLIESSLICH der Bemessung des Aktien-Verlusttopfs "
            f"(§ 20 Abs. 6 Satz 4 EStG) und erhöht die Bemessungsgrundlage NICHT — "
            f"die realisierten Aktienveräußerungsgewinne müssen bereits in "
            f"'anlage_kap.kapitalertraege' enthalten sein. Sonst bleiben sie "
            f"unversteuert. Bitte 'kapitalertraege' prüfen.")

    # § 20 Abs. 6 Satz 4: Aktienverluste nur gegen Aktiengewinne.
    aktien_verrechnet = min(verlust_aktien, max(gewinn_aktien, NULL))
    # Festgestellter Aktien-Verlustvortrag der Vorjahre: erst nach den laufenden
    # Verlusten des Jahres und ebenfalls nur gegen Aktiengewinne. Zusätzlich auf
    # die tatsächlich erklärten Kapitalerträge gedeckelt — sonst würde ein zu hoch
    # angegebener 'gewinn_aktien' den Aktienvortrag in einen allgemeinen Verlust
    # umwandeln (falscher Verrechnungskreis).
    aktien_gewinn_rest = max(gewinn_aktien, NULL) - aktien_verrechnet
    vv_aktien_verbraucht = min(vv_aktien_vorjahr, aktien_gewinn_rest,
                               max(kap_ertraege - aktien_verrechnet, NULL))
    vv_aktien_rest = vv_aktien_vorjahr - vv_aktien_verbraucht
    vortrag_aktien = verlust_aktien - aktien_verrechnet + vv_aktien_rest
    if vv_aktien_vorjahr > 0:
        hinweise.append(
            f"Aktien-Verlustvortrag aus Vorjahren ({fmt_eur(vv_aktien_vorjahr)}): "
            f"{fmt_eur(vv_aktien_verbraucht)} wurden mit Aktienveräußerungsgewinnen "
            f"dieses Jahres verrechnet, {fmt_eur(vv_aktien_rest)} bleiben festgestellt "
            f"(§ 20 Abs. 6 Satz 4 EStG i. V. m. § 10d EStG).")
    if verlust_aktien > 0 and gewinn_aktien <= 0:
        hinweise.append(
            "Aktienverluste können nur mit Aktienveräußerungsgewinnen verrechnet werden "
            "(§ 20 Abs. 6 Satz 4 EStG). Ohne Angabe von 'anlage_kap.gewinn_aktien' wurde der "
            "gesamte Aktienverlust in den Verlustvortrag gestellt.")
    nach_aktien = kap_ertraege - aktien_verrechnet - vv_aktien_verbraucht
    # Seit JStG 2024: Termingeschäftsverluste gegen alle Kapitalerträge, ohne 20.000-€-Deckel.
    termin_verrechnet = min(verlust_termin, max(nach_aktien, NULL))
    vortrag_termin = verlust_termin - termin_verrechnet

    netto_kap = nach_aktien - termin_verrechnet          # vorzeichenbehaftet
    vortrag_allgemein = -netto_kap if netto_kap < 0 else NULL
    bemessung_kap = max(netto_kap - sparer_pb, NULL)

    # § 32d Abs. 1 Sätze 4/5: bei Kirchensteuerpflicht ESt = (e − 4q) / (4 + k).
    if kist_satz is not None:
        kap_est = q2(max((bemessung_kap - 4 * auslaendische_quellensteuer)
                         / (D("4") + kist_satz), NULL))
    else:
        kap_est = q2(max(bemessung_kap * D("0.25") - auslaendische_quellensteuer, NULL))
    kap_soli = q2(kap_est * D("0.055"))
    kap_kist = q2(kap_est * kist_satz) if kist_satz is not None else None

    # --- Anlage SO: Krypto § 23 + § 22 Nr. 3 (Staking) + sonstige Leistungen ---
    quellen = _als_quellenliste(krypto)
    kd = aggregiere_krypto(quellen, jahr, werte_jahr)
    warnungen.extend(kd["warnungen"])
    for h in kd["hinweise"]:
        if h not in hinweise:
            hinweise.append(h)

    krypto_23_pflichtig = _betrag(kd["paragraph_23"]["steuerpflichtiger_betrag_eur"], "§23")
    krypto_23_verlustvortrag = _betrag(kd["paragraph_23"]["verlustvortrag_eur"], "§23")

    # --- § 23 Abs. 3 Satz 8 EStG: Verlustvortrag aus Vorjahren ---
    # Festgestellte Verluste aus privaten Veräußerungsgeschäften mindern AUSSCHLIESSLICH
    # Gewinne derselben Einkunftsart; sie können das Ergebnis nicht negativ machen,
    # und ein nicht verbrauchter Rest wird erneut vorgetragen.
    #
    # REIHENFOLGE (wichtig): Zuerst greift die Freigrenze des § 23 Abs. 3 Satz 5 auf
    # den EIGENEN Jahressaldo — 'krypto_23_pflichtig' ist bereits das Ergebnis dieser
    # Prüfung —, erst danach der Verlustvortrag. Begründung: Die Freigrenze bezieht
    # sich auf den „Gesamtgewinn" des Veranlagungszeitraums, also auf das Ergebnis des
    # Jahres vor dem Verlustabzug aus anderen Jahren. Ein Jahr, dessen eigener Saldo
    # unter der Freigrenze bleibt, ist ohnehin in voller Höhe steuerfrei; es darf
    # deshalb auch nichts vom Vortrag verbrauchen — sonst ginge festgestelltes
    # Verlustpotenzial ohne jede Steuerersparnis verloren.
    vv_23_vorjahr = abs(_betrag(so_extra.get("verlustvortrag_23_vorjahr"),
                                "anlage_so.verlustvortrag_23_vorjahr"))
    krypto_23_vor_vortrag = krypto_23_pflichtig
    vv_23_verbraucht = min(vv_23_vorjahr, krypto_23_vor_vortrag)
    krypto_23_pflichtig = krypto_23_vor_vortrag - vv_23_verbraucht   # nie negativ
    vv_23_rest = vv_23_vorjahr - vv_23_verbraucht
    # Was im Folgejahr zur Verfügung steht: nicht verbrauchter Vortrag + Verlust dieses Jahres.
    vv_23_neu_gesamt = vv_23_rest + krypto_23_verlustvortrag
    if vv_23_vorjahr > 0:
        if vv_23_verbraucht > 0:
            hinweise.append(
                f"§ 23-Verlustvortrag aus Vorjahren ({fmt_eur(vv_23_vorjahr)}): "
                f"{fmt_eur(vv_23_verbraucht)} wurden mit den steuerpflichtigen "
                f"§ 23-Gewinnen dieses Jahres verrechnet (§ 23 Abs. 3 Satz 8 EStG), "
                f"{fmt_eur(vv_23_rest)} bleiben vortragsfähig.")
        else:
            hinweise.append(
                f"§ 23-Verlustvortrag aus Vorjahren ({fmt_eur(vv_23_vorjahr)}) wurde "
                f"NICHT verbraucht: es gibt keinen steuerpflichtigen § 23-Gewinn "
                f"(Jahressaldo negativ oder unter der Freigrenze — die Freigrenze wird "
                f"vor dem Verlustabzug geprüft). Der Vortrag bleibt in voller Höhe "
                f"erhalten.")

    # § 22 Nr. 3 Satz 2: Freigrenze 256 € auf die SUMME aller Leistungen des Jahres —
    # Staking aus allen Quellen plus die sonstigen Leistungen aus den Steuerdaten.
    so_sonstige = _betrag(so_extra.get("sonstige_einkuenfte"), "anlage_so.sonstige_einkuenfte")
    p22_summe_gesamt = kd["_p22_summe"] + so_sonstige
    p22_pflichtig = p22_summe_gesamt if p22_summe_gesamt >= FREIGRENZE_22_3 else NULL
    p22 = {
        "freigrenze_eur": str(FREIGRENZE_22_3),
        "freigrenze_angewendet": True,
        "summe_eur": str(q2(p22_summe_gesamt)),
        "summe_zufluesse_eur": str(q2(p22_summe_gesamt)),
        "davon_krypto_eur": str(q2(kd["_p22_summe"])),
        "davon_sonstige_leistungen_eur": str(q2(so_sonstige)),
        "steuerpflichtig_eur": str(q2(p22_pflichtig)),
        "ertraege": kd.pop("_p22_ertraege"),
    }
    kd.pop("_p22_summe")
    kd["paragraph_22_nr3"] = p22
    kd["paragraph_22_nr_3"] = p22  # Alias für export_report.py
    if so_sonstige > 0:
        hinweise.append(
            "Die § 22 Nr. 3-Freigrenze von 256 € wurde auf die Summe aus Krypto-Leistungen "
            "und 'anlage_so.sonstige_einkuenfte' angewendet — sie gilt für alle Leistungen "
            "des Jahres gemeinsam, nicht je Quelle.")

    eink_so = krypto_23_pflichtig + p22_pflichtig

    # --- Anlage V / S / G ---
    eink_v = _betrag(v.get("einkuenfte"), "anlage_v.einkuenfte")
    eink_s = _betrag(s.get("gewinn"), "anlage_s.gewinn")
    eink_g = _betrag(g.get("gewinn"), "anlage_g.gewinn")

    # --- Summe der Einkünfte (KAP bleibt außen vor: gesonderter Tarif § 32d) ---
    summe_einkuenfte = eink_n + eink_so + eink_v + eink_s + eink_g

    # --- Abzüge ---
    vorsorge_summe = _summe_positionen(vorsorge, "vorsorge")
    sonder_pausch = SONDERAUSGABEN_PB * 2 if verheiratet else SONDERAUSGABEN_PB
    sonder_geltend = _summe_positionen(sonder, "sonderausgaben")
    sonder_summe = max(sonder_geltend, sonder_pausch)
    agb_summe = _betrag(agb.get("anzusetzen"), "aussergewoehnliche_belastungen.anzusetzen")

    # Clamp bleibt hier: ein negatives zvE gibt es nicht (Verlustabzug § 10d gesondert).
    zve = max(summe_einkuenfte - vorsorge_summe - sonder_summe - agb_summe, NULL)

    est = est_tarif(zve, jahr, verheiratet)
    if est is None:
        tarif_soli = None
        tarif_kist = None
        est_gesamt = soli_gesamt = kist_gesamt = None
    else:
        est = q2(est)
        tarif_soli = q2(soli(est, jahr, verheiratet))
        tarif_kist = q2(est * kist_satz) if kist_satz is not None else None
        est_gesamt = q2(est + kap_est)
        soli_gesamt = q2(tarif_soli + kap_soli)
        kist_gesamt = None
        if kist_satz is not None:
            kist_gesamt = q2((tarif_kist or NULL) + (kap_kist or NULL))

    # --- Nachzahlung / Erstattung ---
    anrechnung = lohnsteuer + soli_einbehalten + kist_einbehalten + anrechenbare_kest
    if est_gesamt is None:
        ergebnis = {
            "status": "nicht berechenbar",
            "hinweis": f"Ohne hinterlegten § 32a-Tarif für {jahr} ist keine Abschlusszahlung "
                       f"schätzbar.",
            "anrechenbare_betraege_gesamt": str(q2(anrechnung)),
        }
    else:
        festsetzung = q2(est_gesamt + soli_gesamt + (kist_gesamt or NULL))
        saldo = q2(festsetzung - anrechnung)
        ergebnis = {
            "steuer_festsetzung_gesamt": str(festsetzung),
            "davon_einkommensteuer": str(est_gesamt),
            "davon_solidaritaetszuschlag": str(soli_gesamt),
            "davon_kirchensteuer": (None if kist_gesamt is None else str(kist_gesamt)),
            "anrechenbare_betraege": {
                "lohnsteuer": str(q2(lohnsteuer)),
                "solidaritaetszuschlag_einbehalten": str(q2(soli_einbehalten)),
                "kirchensteuer_einbehalten": str(q2(kist_einbehalten)),
                "anrechenbare_kapitalertragsteuer": str(q2(anrechenbare_kest)),
                "summe": str(q2(anrechnung)),
            },
            "saldo": str(saldo),
            "art": "Nachzahlung" if saldo > 0 else ("Erstattung" if saldo < 0 else "ausgeglichen"),
            "betrag_absolut": str(q2(abs(saldo))),
            "hinweis": "SCHÄTZUNG — keine verbindliche Steuerfestsetzung. Vorauszahlungen, "
                       "Günstigerprüfung, Progressionsvorbehalt und Kinderfreibeträge sind "
                       "nicht berücksichtigt.",
        }

    # --- ELSTER-Feld-Mapping ---
    elster = build_elster_mapping(
        jahr=jahr, tp=tp, n=n, brutto=brutto, wk_summe=wk_summe,
        kap=kap, kap_ertraege=kap_ertraege, sparer_pb=sparer_pb,
        verlust_aktien=verlust_aktien, verlust_termin=verlust_termin,
        anrechenbare_kest=anrechenbare_kest, krypto=kd, so_extra=so_extra,
        so_sonstige=so_sonstige, v=v, s=s, g=g, eink_s=eink_s, eink_g=eink_g,
        vorsorge=vorsorge, sonder=sonder, agb_summe=agb_summe, kinder=kinder,
        vv_23_vorjahr=vv_23_vorjahr, vv_23_verbraucht=vv_23_verbraucht,
        vv_23_rest=vv_23_rest, vv_23_neu_gesamt=vv_23_neu_gesamt,
        vv_aktien_vorjahr=vv_aktien_vorjahr, vv_aktien_verbraucht=vv_aktien_verbraucht,
        vv_aktien_rest=vv_aktien_rest)

    report = {
        "meta": {
            "steuerjahr": jahr,
            "erstellt": datetime.now(timezone.utc).isoformat(),
            "waehrung": "EUR",
            "veranlagung": "Zusammenveranlagung" if verheiratet else "Einzelveranlagung",
            "steuerpflichtiger": tp,
            "krypto_quellen": kd.get("quellen", []),
        },
        "anlagen": {
            "N": {"bruttoarbeitslohn": str(q2(brutto)),
                  "werbungskosten_geltend": str(q2(wk_summe)),
                  "werbungskosten_angesetzt": str(q2(wk_angesetzt)),
                  "arbeitnehmer_pauschbetrag": str(an_pb),
                  "einkuenfte": str(q2(eink_n)),
                  "lohnsteuer": str(q2(lohnsteuer)),
                  "soli_einbehalten": str(q2(soli_einbehalten)),
                  "kirchensteuer_einbehalten": str(q2(kist_einbehalten))},
            "KAP": {"kapitalertraege": str(q2(kap_ertraege)),
                    "gewinn_aktien": str(q2(gewinn_aktien)),
                    "verlust_aktien": str(q2(verlust_aktien)),
                    "verlust_termingeschaefte": str(q2(verlust_termin)),
                    "verlust_aktien_verrechnet": str(q2(aktien_verrechnet)),
                    "verlust_termingeschaefte_verrechnet": str(q2(termin_verrechnet)),
                    "netto_kapitalertraege": str(q2(netto_kap)),
                    "sparer_pauschbetrag": str(sparer_pb),
                    "nach_pauschbetrag": str(q2(bemessung_kap)),
                    "abgeltungsteuer": str(kap_est),
                    "abgeltungsteuer_soli": str(kap_soli),
                    "abgeltungsteuer_kirchensteuer": (None if kap_kist is None else str(kap_kist)),
                    "anrechenbare_kest": str(q2(anrechenbare_kest)),
                    "auslaendische_quellensteuer": str(q2(auslaendische_quellensteuer)),
                    "verlustvortrag_aktien_vorjahr": str(q2(vv_aktien_vorjahr)),
                    "verlustvortrag_aktien_verbraucht": str(q2(vv_aktien_verbraucht)),
                    "verlustvortrag_aktien_rest": str(q2(vv_aktien_rest)),
                    "verlustvortraege": {
                        "aktien": str(q2(vortrag_aktien)),
                        "termingeschaefte": str(q2(vortrag_termin)),
                        "allgemein": str(q2(vortrag_allgemein)),
                    }},
            "SO": {"krypto_23_steuerpflichtig": str(q2(krypto_23_pflichtig)),
                   "krypto_23_vor_verlustvortrag": str(q2(krypto_23_vor_vortrag)),
                   "verlustvortrag_23_vorjahr": str(q2(vv_23_vorjahr)),
                   "verlustvortrag_23_verbraucht": str(q2(vv_23_verbraucht)),
                   "verlustvortrag_23_rest": str(q2(vv_23_rest)),
                   "verlustvortrag_23_neu_gesamt": str(q2(vv_23_neu_gesamt)),
                   "krypto_23_verlustvortrag": str(q2(krypto_23_verlustvortrag)),
                   "krypto_22_3_staking": str(q2(_betrag(p22["davon_krypto_eur"], "§22"))),
                   "sonstige_einkuenfte": str(q2(so_sonstige)),
                   "leistungen_22_3_gesamt": str(q2(p22_summe_gesamt)),
                   "leistungen_22_3_steuerpflichtig": str(q2(p22_pflichtig)),
                   "einkuenfte_gesamt": str(q2(eink_so))},
            "V": {"einkuenfte": str(q2(eink_v))},
            "S": {"gewinn": str(q2(eink_s))},
            "G": {"gewinn": str(q2(eink_g))},
            "Vorsorgeaufwand": {"summe": str(q2(vorsorge_summe)), "positionen": vorsorge},
            "Sonderausgaben": {"summe_geltend": str(q2(sonder_geltend)),
                               "summe_angesetzt": str(q2(sonder_summe)),
                               "pauschbetrag": str(sonder_pausch), "positionen": sonder},
            "AussergewoehnlicheBelastungen": {"anzusetzen": str(q2(agb_summe))},
            "Kind": kinder,
        },
        "krypto_detail": kd,
        "berechnung": {
            "summe_der_einkuenfte": str(q2(summe_einkuenfte)),
            "einkuenfte_n": str(q2(eink_n)),
            "einkuenfte_so": str(q2(eink_so)),
            "abzug_vorsorge": str(q2(vorsorge_summe)),
            "abzug_sonderausgaben": str(q2(sonder_summe)),
            "abzug_agb": str(q2(agb_summe)),
            "zu_versteuerndes_einkommen": str(q2(zve)),
            "tarif": "Splitting" if verheiratet else "Grundtarif",
            "einkommensteuer_schaetzung": (None if est is None else str(est)),
            "soli_schaetzung": (None if tarif_soli is None else str(tarif_soli)),
            "kirchensteuer_schaetzung": (None if tarif_kist is None else str(tarif_kist)),
            "abgeltungsteuer_kap": str(kap_est),
            "abgeltungsteuer_kap_soli": str(kap_soli),
            "abgeltungsteuer_kap_kirchensteuer": (None if kap_kist is None else str(kap_kist)),
            "steuer_gesamt_est": (None if est_gesamt is None else str(est_gesamt)),
            "steuer_gesamt_soli": (None if soli_gesamt is None else str(soli_gesamt)),
            "steuer_gesamt_kirchensteuer": (None if kist_gesamt is None else str(kist_gesamt)),
            "kirchensteuersatz": (None if kist_satz is None else str(kist_satz)),
            "tarif_jahr_hinterlegt": tarif_hinterlegt,
        },
        "ergebnis": ergebnis,
        "elster_mapping": elster,
        "eingabepruefung": {"unbekannte_felder": unbekannte_felder},
        "hinweise": hinweise,
        "warnungen": warnungen,
        "disclaimer": [
            "Dies ist KEINE Steuerberatung und keine verbindliche Steuerberechnung.",
            "Die Schätzung nutzt den § 32a-Tarif und vereinfachte Annahmen "
            "(keine Günstigerprüfung, kein Progressionsvorbehalt, keine Kinderfreibeträge "
            "im Tarif, keine Vorauszahlungen).",
            "GRÖSSTE VEREINFACHUNG: Vorsorgeaufwendungen werden hier in voller Höhe "
            "abgezogen — die Höchstbetragsberechnung nach § 10 Abs. 3 und 4 EStG "
            "(Deckelung der Altersvorsorgeaufwendungen, eigener Höchstbetrag für "
            "Kranken-/Pflege- und übrige sonstige Vorsorgeaufwendungen, Kürzung um den "
            "Arbeitgeberanteil) ist NICHT umgesetzt. Tatsächlich abziehbar ist "
            "regelmäßig deutlich weniger; das zu versteuernde Einkommen ist damit hier "
            "zu niedrig und die ausgewiesene Steuer ZU NIEDRIG (bzw. eine Erstattung zu "
            "hoch). Bei nennenswerten Vorsorgeaufwendungen ist die Abweichung die mit "
            "Abstand größte Fehlerquelle dieser Schätzung.",
            ELSTER_CAVEAT,
            "Krypto-Endkontrolle durch Steuerberater und Abgleich mit der "
            "ELSTER-Berechnung vor Einreichung.",
        ],
    }

    if vv_23_verbraucht > 0:
        report["disclaimer"].append(
            f"§ 23-Verlustvortrag aus Vorjahren: {fmt_eur(vv_23_verbraucht)} von "
            f"{fmt_eur(vv_23_vorjahr)} wurden mit den § 23-Gewinnen dieses Jahres "
            f"verrechnet (§ 23 Abs. 3 Satz 8 EStG). Der verbrauchte Betrag muss in "
            f"Anlage SO beantragt werden und steht künftig nicht mehr zur Verfügung.")
    if vv_23_rest > 0:
        report["disclaimer"].append(
            f"Verbleibender § 23-Verlustvortrag aus Vorjahren: {fmt_eur(vv_23_rest)} — "
            f"Verlustfeststellung fortschreiben lassen, sonst geht der Rest verloren.")
    if krypto_23_verlustvortrag > 0:
        report["disclaimer"].append(
            f"§ 23-Verlust {q2(krypto_23_verlustvortrag)} €: in Anlage SO erklären und "
            f"Verlustfeststellung beantragen — verrechenbar nur mit künftigen/vergangenen "
            f"§ 23-Gewinnen (Vor-/Rücktrag).")
    if vv_23_neu_gesamt > 0:
        report["disclaimer"].append(
            f"Für das Folgejahr festzustellender § 23-Verlustvortrag insgesamt: "
            f"{fmt_eur(vv_23_neu_gesamt)} (Rest aus Vorjahren {fmt_eur(vv_23_rest)} + "
            f"Verlust dieses Jahres {fmt_eur(krypto_23_verlustvortrag)}). Diesen Betrag "
            f"im nächsten Report als 'anlage_so.verlustvortrag_23_vorjahr' eintragen.")
    if vortrag_aktien > 0 or vortrag_termin > 0 or vortrag_allgemein > 0:
        report["disclaimer"].append(
            f"Verlustvortrag Kapitalvermögen: Aktien {fmt_eur(vortrag_aktien)}, "
            f"Termingeschäfte {fmt_eur(vortrag_termin)}, übrige {fmt_eur(vortrag_allgemein)} — "
            f"Verlustfeststellung nach § 10d EStG beantragen.")

    ke = kd.get("koinly_extra")
    if ke:
        fut = _betrag(ke.get("futures_nettoergebnis_eur"), "koinly_extra.futures_nettoergebnis_eur")
        if fut != 0:
            report["disclaimer"].append(
                f"Futures/Derivate {q2(fut)} € sind NICHT in den § 23-Gewinnen enthalten — "
                f"in Deutschland i. d. R. Termingeschäfte § 20 Abs. 2 EStG (Anlage KAP), "
                f"gesondert angeben (Verlustverrechnung seit JStG 2024 unbeschränkt).")
        if _betrag(ke.get("ausgaben_total_eur"), "koinly_extra.ausgaben_total_eur") > 0:
            report["disclaimer"].append(
                f"Gebühren {ke.get('ausgaben_total_eur')} € (z. B. Loan/Margin fee) sind für "
                f"Privatanleger meist NICHT abziehbar — Steuerberater prüfen.")
        report.setdefault("koinly_extra", ke)

    for h in warnungen:
        if h not in report["disclaimer"]:
            report["disclaimer"].append(h)

    if not tarif_hinterlegt:
        report["berechnung"]["hinweis_tarif"] = (
            f"§ 32a-Tarif für {jahr} nicht hinterlegt — ESt-Schätzung übersprungen. "
            f"Aktuelle Tarifkonstanten verifizieren und in scripts/steuerlib.py (TARIF) "
            f"ergänzen; bekannt sind {min(TARIF)}–{max(TARIF)}.")
    return report


# ─────────────────────────────────────────────────────────────────────────────
# ELSTER-Mapping
# ─────────────────────────────────────────────────────────────────────────────


def build_elster_mapping(*, jahr, tp, n, brutto, wk_summe, kap, kap_ertraege, sparer_pb,
                         verlust_aktien, verlust_termin, anrechenbare_kest, krypto,
                         so_extra, so_sonstige, v, s, g, eink_s, eink_g, vorsorge,
                         sonder, agb_summe, kinder,
                         vv_23_vorjahr=NULL, vv_23_verbraucht=NULL, vv_23_rest=NULL,
                         vv_23_neu_gesamt=NULL, vv_aktien_vorjahr=NULL,
                         vv_aktien_verbraucht=NULL, vv_aktien_rest=NULL):
    """Zuordnung der Werte zu ELSTER-Eingabezeilen (manuelle Eingabe).

    Jede Zeile führt die Quelle mit, aus der ihr Wert stammt — sonst ist im Export nicht
    nachvollziehbar, welches Eingabefeld einen Betrag erzeugt hat.
    """
    m = []

    def add(anlage, zeile, bezeichnung, wert, quelle):
        m.append({"anlage": anlage, "zeile": zeile, "bezeichnung": bezeichnung,
                  "wert": str(wert), "quelle": quelle})

    add("Hauptvordruck", "—", "Steuerjahr", jahr, "steuerjahr")
    steuer_id = (tp.get("steuer_id") or tp.get("steueridentifikationsnummer")
                 or tp.get("identifikationsnummer") or tp.get("idnr"))
    if steuer_id:
        add("Hauptvordruck", "Z. 7", "Identifikationsnummer (Steuer-ID)", steuer_id,
            "steuerpflichtiger.steuer_id")
    if tp.get("name"):
        add("Hauptvordruck", "Z. 8", "Name", tp.get("name"), "steuerpflichtiger.name")

    # --- Anlage N ---
    add("Anlage N", "Z. 6", "Bruttoarbeitslohn (lt. Lohnsteuerbescheinigung Nr. 3)",
        q2(brutto), "anlage_n.bruttoarbeitslohn")
    add("Anlage N", "Z. 7", "Einbehaltene Lohnsteuer",
        q2(_betrag(n.get("lohnsteuer"), "anlage_n.lohnsteuer")), "anlage_n.lohnsteuer")
    add("Anlage N", "Z. 8", "Solidaritätszuschlag",
        q2(_betrag(n.get("soli"), "anlage_n.soli")), "anlage_n.soli")
    if _betrag(n.get("kirchensteuer"), "anlage_n.kirchensteuer") != 0:
        add("Anlage N", "Z. 9", "Einbehaltene Kirchensteuer",
            q2(_betrag(n.get("kirchensteuer"), "anlage_n.kirchensteuer")),
            "anlage_n.kirchensteuer")
    if n.get("werbungskosten"):
        add("Anlage N", "Z. 31 ff.", "Werbungskosten (Summe)", q2(wk_summe),
            "anlage_n.werbungskosten")

    # --- Anlage KAP ---
    add("Anlage KAP", "Z. 7", "Kapitalerträge (ohne Steuerabzug / nachzuerklären)",
        q2(kap_ertraege), "anlage_kap.kapitalertraege")
    add("Anlage KAP", "Z. 16/17", "Sparer-Pauschbetrag (Antrag)", sparer_pb,
        "steuerlib.sparer_pauschbetrag")
    if verlust_aktien > 0:
        add("Anlage KAP", "Z. 23", "Verluste aus Aktienveräußerungen (§ 20 Abs. 6 Satz 4)",
            q2(verlust_aktien), "anlage_kap.verlust_aktien")
    if verlust_termin > 0:
        add("Anlage KAP", "Z. 24", "Verluste aus Termingeschäften (§ 20 Abs. 6 Satz 5 a. F. — "
            "durch JStG 2024 aufgehoben, unbeschränkt verrechenbar)",
            q2(verlust_termin), "anlage_kap.verlust_termingeschaefte")
    if vv_aktien_vorjahr > 0:
        add("Anlage KAP", "Z. 23",
            "Festgestellter Aktien-Verlustvortrag aus Vorjahren (§ 20 Abs. 6 Satz 4 EStG)",
            q2(vv_aktien_vorjahr), "anlage_kap.verlustvortrag_aktien_vorjahr")
        add("Anlage KAP", "Z. 23", "davon mit Aktiengewinnen dieses Jahres verrechnet",
            q2(vv_aktien_verbraucht), "anlage_kap.verlustvortrag_aktien_vorjahr")
        add("Anlage KAP", "Z. 23", "verbleibender Aktien-Verlustvortrag",
            q2(vv_aktien_rest), "anlage_kap.verlustvortrag_aktien_vorjahr")
    if anrechenbare_kest != 0:
        add("Anlage KAP", "Z. 37", "Anrechenbare Kapitalertragsteuer",
            q2(anrechenbare_kest), "anlage_kap.anrechenbare_kest")
    ausl = _betrag(kap.get("auslaendische_quellensteuer"), "anlage_kap.auslaendische_quellensteuer")
    if ausl != 0:
        add("Anlage KAP", "Z. 41", "Anrechenbare ausländische Quellensteuer", q2(ausl),
            "anlage_kap.auslaendische_quellensteuer")

    # --- Anlage SO (Krypto § 23 + Leistungen § 22 Nr. 3) ---
    p23 = krypto["paragraph_23"]
    add("Anlage SO", "Z. 41–47", "Private Veräußerungsgeschäfte § 23 — Gewinn/Verlust",
        q2(_betrag(p23["netto_ergebnis_eur"], "§23")), "krypto-quellen (aggregiert)")
    add("Anlage SO", "Z. 41–47", "davon steuerpflichtig (Freigrenze einmalig geprüft)",
        q2(_betrag(p23["steuerpflichtiger_betrag_eur"], "§23")), "krypto-quellen (aggregiert)")
    if _betrag(p23.get("verlustvortrag_eur"), "§23") > 0:
        add("Anlage SO", "Z. 41–47", "Verlust § 23 (Verlustfeststellung beantragen)",
            q2(_betrag(p23["verlustvortrag_eur"], "§23")), "krypto-quellen (aggregiert)")
    if vv_23_vorjahr > 0:
        add("Anlage SO", "Z. 54–59",
            "Festgestellter Verlustvortrag § 23 aus Vorjahren (Antrag auf Verrechnung)",
            q2(vv_23_vorjahr), "anlage_so.verlustvortrag_23_vorjahr")
        add("Anlage SO", "Z. 54–59",
            "davon mit § 23-Gewinnen dieses Jahres verrechnet (§ 23 Abs. 3 Satz 8 EStG)",
            q2(vv_23_verbraucht), "anlage_so.verlustvortrag_23_vorjahr")
        add("Anlage SO", "Z. 54–59",
            "verbleibender Verlustvortrag § 23 aus Vorjahren (Feststellung fortschreiben)",
            q2(vv_23_rest), "anlage_so.verlustvortrag_23_vorjahr")
    if vv_23_neu_gesamt > 0:
        add("Anlage SO", "Z. 54–59",
            "Für das Folgejahr festzustellender Verlustvortrag § 23 (Rest + Verlust "
            "dieses Jahres)", q2(vv_23_neu_gesamt), "berechnet")
    ke = krypto.get("koinly_extra")
    if ke and _betrag(ke.get("futures_nettoergebnis_eur"), "koinly_extra") != 0:
        add("Anlage KAP", "Z. 21 ff.", "Termingeschäfte/Futures § 20 Abs. 2 (gesondert)",
            q2(_betrag(ke["futures_nettoergebnis_eur"], "koinly_extra")), "koinly_extra")
    p22 = krypto.get("paragraph_22_nr3", {})
    if _betrag(p22.get("summe_eur"), "§22") > 0:
        add("Anlage SO", "Z. 10–13", "Leistungen § 22 Nr. 3 (Staking/Lending u. a., "
            "Freigrenze 256 € auf die Gesamtsumme)",
            q2(_betrag(p22.get("steuerpflichtig_eur"), "§22")),
            "krypto-quellen + anlage_so.sonstige_einkuenfte")
    if so_sonstige != 0:
        add("Anlage SO", "Z. 10–13", "davon sonstige Leistungen (ohne Krypto)", q2(so_sonstige),
            "anlage_so.sonstige_einkuenfte")

    # --- Anlage V / S / G ---
    if v and _betrag(v.get("einkuenfte"), "anlage_v.einkuenfte") != 0:
        add("Anlage V", "Z. 21", "Einkünfte aus Vermietung und Verpachtung",
            q2(_betrag(v.get("einkuenfte"), "anlage_v.einkuenfte")), "anlage_v.einkuenfte")
    if s and eink_s != 0:
        add("Anlage S", "Z. 4", "Gewinn aus selbständiger Arbeit (§ 18 EStG)", q2(eink_s),
            "anlage_s.gewinn")
    if g and eink_g != 0:
        add("Anlage G", "Z. 4", "Gewinn aus Gewerbebetrieb (§ 15 EStG)", q2(eink_g),
            "anlage_g.gewinn")

    # --- Vorsorge / Sonderausgaben / agB ---
    # Die Positions-Schlüssel sind frei benennbar; für das Mapping werden sie
    # lesbar gemacht, damit die CSV ohne Rückgriff auf die Eingabedatei taugt.
    def _label(key: str) -> str:
        return key.replace("_", " ").strip().capitalize() or key

    for key, val in (vorsorge or {}).items():
        add("Anlage Vorsorgeaufwand", "—", _label(key),
            q2(_betrag(val, f"vorsorge.{key}")), f"vorsorge.{key}")
    for key, val in (sonder or {}).items():
        betrag = q2(_betrag(val, f"sonderausgaben.{key}"))
        zeile = "Z. 5–12" if key.lower().startswith("spende") else "—"
        add("Anlage Sonderausgaben", zeile, _label(key), betrag, f"sonderausgaben.{key}")
    if agb_summe != 0:
        add("Anlage Außergewöhnliche Belastungen", "Z. 13 ff.",
            "Außergewöhnliche Belastungen (anzusetzen, nach zumutbarer Belastung)",
            q2(agb_summe), "aussergewoehnliche_belastungen.anzusetzen")

    for i, kind in enumerate(kinder or [], 1):
        add("Anlage Kind", f"Kind {i}", kind.get("name", f"Kind {i}"),
            kind.get("geburtsdatum", ""), f"kinder[{i - 1}]")

    # Durchgereichte ELSTER-Zeilen aus Parsern (z. B. eToro-Summenausweis)
    for row in krypto.get("elster_extra", []) or []:
        add(row.get("anlage", "—"), row.get("zeile", "—"), row.get("bezeichnung", ""),
            row.get("wert", ""), row.get("quelle", "krypto-quelle (elster_extra)"))
    return m


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main(argv=None):
    ap = argparse.ArgumentParser(description="TaxReport aus Steuerdaten und Krypto-Ergebnissen bauen")
    ap.add_argument("steuerdaten", help="steuerdaten.json")
    ap.add_argument("--transactions", help="kanonische transactions.json (Krypto)")
    ap.add_argument("--krypto-result", nargs="+", metavar="DATEI",
                    help="ein oder mehrere bereits berechnete Krypto-Ergebnisse (JSON)")
    ap.add_argument("-o", "--out", default="taxreport.json")
    ap.add_argument("--strict", action="store_true",
                    help="unbekannte Felder in den Steuerdaten als Fehler behandeln "
                         "(Report wird trotzdem geschrieben, Rückgabecode 3)")
    args = ap.parse_args(argv)

    try:
        with open(args.steuerdaten, encoding="utf-8") as f:
            sd = json.load(f)
    except FileNotFoundError:
        print(f"FEHLER: Steuerdaten nicht gefunden: {args.steuerdaten}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"FEHLER: '{args.steuerdaten}' ist kein gültiges JSON: {e}", file=sys.stderr)
        return 2

    try:
        jahr = lies_steuerjahr(sd)
        if args.krypto_result:
            if args.transactions:
                # Eine übergebene Datei stillschweigend zu ignorieren ist genau das,
                # was in einer Steuerberechnung nicht passieren darf.
                print("HINWEIS: --transactions wird NICHT verwendet, weil --krypto-result "
                      "angegeben ist. Für eine gemeinsame FIFO-Rechnung die Transaktions"
                      "listen zusammenführen und nur --transactions übergeben.",
                      file=sys.stderr)
            quellen = lade_krypto_quellen(args.krypto_result)
        else:
            from krypto_fifo import compute_crypto_tax  # lokal: nur wenn wirklich gerechnet wird
            if args.transactions:
                with open(args.transactions, encoding="utf-8") as f:
                    txs = json.load(f)
                if isinstance(txs, dict):
                    txs = txs.get("transactions", [])
            else:
                txs = sd.get("krypto_transaktionen") or []
            quellen = [normiere_krypto_quelle(compute_crypto_tax(txs, jahr),
                                              herkunft="krypto_fifo (FIFO-Engine)")]
        report = build(sd, quellen)
    except EingabeFehler as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 2
    except ParseError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 2

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    b = report["berechnung"]
    erg = report["ergebnis"]
    print(f"TaxReport geschrieben: {args.out}")
    print(f"  zu versteuerndes Einkommen: {b['zu_versteuerndes_einkommen']} €")
    print(f"  ESt-Schätzung ({b['tarif']}): {b['einkommensteuer_schaetzung']} €")
    print(f"  Abgeltungsteuer (Anlage KAP): {b['abgeltungsteuer_kap']} €")
    print(f"  Krypto § 23 steuerpflichtig: "
          f"{report['anlagen']['SO']['krypto_23_steuerpflichtig']} €")
    if "saldo" in erg:
        print(f"  {erg['art']} (Schätzung): {erg['betrag_absolut']} €")
    else:
        print(f"  {erg['status']}: {erg['hinweis']}")
    for w in report.get("warnungen", []):
        print(f"  WARNUNG: {w}")

    unbekannt = (report.get("eingabepruefung") or {}).get("unbekannte_felder") or []
    if unbekannt and args.strict:
        print(f"FEHLER (--strict): {len(unbekannt)} unbekannte(s) Feld(er) in "
              f"'{args.steuerdaten}' — die Werte wurden IGNORIERT: "
              + ", ".join(b["pfad"] for b in unbekannt)
              + f". Der Report wurde trotzdem nach {args.out} geschrieben.",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
