#!/usr/bin/env python3
"""
build_taxreport.py — Setzt aus Steuerdaten + Krypto-Ergebnis(sen) einen vollständigen
TaxReport (alle Anlagen) zusammen, schätzt Einkommensteuer (§ 32a EStG), Soli,
Kirchensteuer und die Abgeltungsteuer (§ 32d EStG), rechnet die einbehaltenen
Steuern an (Nachzahlung/Erstattung) und erzeugt das ELSTER-Feld-Mapping.

Eingabe: steuerdaten.json  (Schema siehe references/anlagen-referenz.md)
         transactions.json (kanonisch) ODER ein/mehrere krypto-Ergebnisse.
         zusätzlich beliebig viele KAP-Ergebnisse (Steuerbescheinigungen,
         Erträgnisaufstellungen, Auslandsbroker — Schema `kap` in
         references/broker-profile.md).

Aufruf:
    python scripts/build_taxreport.py steuerdaten.json \
        [--transactions t.json] [--krypto-result k1.json k2.json ...] \
        [--kap-result tr.json comdirect.json ...] \
        [--strict] -o taxreport.json

Unbekannte Schlüssel in den Steuerdaten (Tippfehler) werden gemeldet — auf stderr
und im Report unter 'warnungen'/'eingabepruefung'; mit --strict endet der Lauf
zusätzlich mit Rückgabecode 3. Ein festgestellter § 23-Verlustvortrag der Vorjahre
kommt über 'anlage_so.verlustvortrag_23_vorjahr' herein, der Aktien-Verlustvortrag
über 'anlage_kap.verlustvortrag_aktien_vorjahr'.

Die Verlusttöpfe des § 20 Abs. 6 EStG rechnet AUSSCHLIESSLICH dieses Skript — und
zwar einmal auf der Summe aller Depots, weil § 20 Abs. 6 personenbezogen gilt. Die
Parser liefern nur die Kennzahlen je Quelle; Werte aus 'anlage_kap' kommen hinzu,
sie werden nicht ersetzt.

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

# ── KAP-Quellen (Schema `kap`, references/broker-profile.md) ─────────────────
# Die normierten Kennzahlen, die eine KAP-Quelle liefern darf. Was hier fehlt,
# wird nicht verrechnet — deshalb wird jeder unbekannte Schlüssel gemeldet statt
# still verworfen (gleiche Logik wie pruefe_unbekannte_felder für die Steuerdaten).
KAP_KENNZAHLEN = ("kapitalertraege", "gewinn_aktien", "gewinn_termingeschaefte",
                  "verlust_aktien", "verlust_termingeschaefte", "verluste_ohne_aktien",
                  "verluste_ausfall", "anrechenbare_kest", "einbehaltener_soli",
                  "einbehaltene_kirchensteuer", "auslaendische_quellensteuer",
                  "fiktive_quellensteuer")

# Kennzahlen, die laut Contract ein NEGATIVES Vorzeichen tragen. Sie werden je
# Quelle auf -abs() normiert, damit sich zwei Quellen mit unterschiedlicher
# Vorzeichenkonvention nicht gegenseitig wegkürzen.
KAP_VERLUST_KENNZAHLEN = ("verlust_aktien", "verlust_termingeschaefte",
                          "verluste_ohne_aktien", "verluste_ausfall")

# Kennzahlen, die laut Contract POSITIV sind (Gewinne).
KAP_GEWINN_KENNZAHLEN = ("gewinn_aktien", "gewinn_termingeschaefte")

# Einbehaltene/anrechenbare Steuern — negativ ist hier immer verdächtig.
KAP_STEUER_KENNZAHLEN = ("anrechenbare_kest", "einbehaltener_soli",
                         "einbehaltene_kirchensteuer", "auslaendische_quellensteuer",
                         "fiktive_quellensteuer")

# Anlage-KAP-Zeilen, für die dieses Skript SELBST eine abgeleitete Zeile erzeugt.
# Nur für diese kann sich eine Rohzeile mit einer abgeleiteten Zeile doppeln;
# alle anderen Rohzeilen werden unkommentiert durchgereicht (ihre Bedeutung
# steht in der Bescheinigung, nicht hier — ELSTER ändert die Layouts jährlich).
KAP_ZEILEN_LABEL = {
    "7": "Kapitalerträge (ohne Steuerabzug bzw. lt. Steuerbescheinigung)",
    "19": "Ausländische Kapitalerträge ohne inländischen Steuerabzug",
    "20": "Gewinne aus Aktienveräußerungen (§ 20 Abs. 2 Satz 1 Nr. 1 EStG)",
    "21": "Gewinne aus Termingeschäften und Stillhalterprämien",
    "22": "Verluste aus Kapitalvermögen ohne Verluste aus Aktienveräußerungen",
    "23": "Verluste aus Aktienveräußerungen (§ 20 Abs. 6 Satz 4 EStG)",
    "24": "Verluste aus Termingeschäften",
    "25": "Verluste aus wertlosem Ausfall / Ausbuchung von Kapitalanlagen",
    "37": "Anrechenbare Kapitalertragsteuer",
    "41": "Anrechenbare ausländische Quellensteuer",
    "42": "Fiktive ausländische Quellensteuer (Anrechnung nach DBA)",
}

# Anlage-KAP-Zeilen, die den BETRAG eines Verlustes aufnehmen. ELSTER erwartet dort
# eine positive Zahl (die Zeilenbeschriftung lautet „Verluste …", das Minus steckt
# schon im Wort). Eine Quelle, die ihre Verluste negativ druckt, darf deshalb nicht
# unverändert ins Mapping durchgereicht werden: der Steuerpflichtige tippt sonst
# „−450,00" in ein Feld, das die 450 € als Betrag will.
KAP_ZEILEN_VERLUST = {"22", "23", "24", "25"}

KAP_VERLUST_POSITIV_HINWEIS = (
    "Anlage KAP: In die Verlustzeilen 22–25 gehört der BETRAG des Verlustes — ELSTER "
    "erwartet dort eine positive Zahl ohne Minuszeichen (die Zeile heißt bereits "
    "„Verluste …“). Rohzeilen aus Bescheinigungen, die den Verlust mit negativem "
    "Vorzeichen ausweisen, sind für das ELSTER-Mapping auf den positiven Betrag "
    "umgestellt worden. Die wörtliche Abschrift der Bescheinigung steht unverändert "
    "unter anlagen.KAP.kap_zeilen.")

# Die zentrale, bewusst getroffene Auslegung dieses Skripts. Sie steht im Report,
# nicht nur im Code: sie entscheidet bei nennenswerten Verlusten über tausende Euro
# Abgeltungsteuer, und nur der Steuerpflichtige kann sie an seiner Bescheinigung
# verifizieren.
KAP_DAVON_ANNAHME_HINWEIS = (
    "ANNAHME zur Anlage KAP — bitte gegen die eigene Steuerbescheinigung prüfen: Die "
    "Zeilen 20 bis 25 stehen im Formular unter EINER gemeinsamen Überschrift — "
    "„In den Zeilen 18 und 19 enthaltene …“ (bzw. „In Zeile 7 enthaltene …“). "
    "Dieser Report behandelt deshalb alle sechs Zeilen gleich: 'kapitalertraege' "
    "(Z. 7 bzw. Z. 18/19) wird als der SALDO genommen, der die Verluste der Zeilen "
    "22–25 BEREITS ENTHÄLT. Die Verlustzeilen mindern die Bemessungsgrundlage daher "
    "KEIN ZWEITES MAL; sie dienen allein der Zuordnung zu den Verrechnungskreisen. "
    "Einzige Ausnahme ist der Aktienverlust (Z. 23): soweit er die Aktiengewinne "
    "(Z. 20) übersteigt, darf er nach § 20 Abs. 6 Satz 4 EStG nicht mit anderen "
    "Kapitalerträgen verrechnet werden — dieser Überhang wird der "
    "Bemessungsgrundlage wieder HINZUGERECHNET und in den Aktien-Verlustvortrag "
    "gestellt. WENN Ihre Bescheinigung ihre „Höhe der Kapitalerträge“ dagegen "
    "BRUTTO ausweist, also OHNE die Verluste der Zeilen 22–25, ist die hier "
    "ausgewiesene Bemessungsgrundlage zu hoch: dann sind die Verluste von "
    "'kapitalertraege' abzuziehen, bevor der Report gebaut wird. Diese Auslegung ist "
    "die folgenreichste Annahme der gesamten KAP-Rechnung.")

AUSFALLVERLUST_HINWEIS = (
    "Verluste aus dem wertlosen Ausfall bzw. der Ausbuchung von Kapitalanlagen "
    "(Anlage KAP Z. 25) unterlagen bis zum Jahressteuergesetz 2024 dem eigenen "
    "Verrechnungskreis und dem 20.000-€-Deckel des § 20 Abs. 6 Satz 6 EStG. Die "
    "Vorschrift ist aufgehoben — anwendbar in allen offenen Fällen —, weshalb diese "
    "Verluste hier ohne Deckel mit sämtlichen Kapitalerträgen verrechnet werden.")


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
    "anlage_kap": {"kapitalertraege", "gewinn_aktien", "gewinn_termingeschaefte",
                   "verlust_aktien", "verlust_termingeschaefte", "verluste_ohne_aktien",
                   "verluste_ausfall", "anrechenbare_kest", "einbehaltener_soli",
                   "einbehaltene_kirchensteuer", "auslaendische_quellensteuer",
                   "fiktive_quellensteuer", "verlustvortrag_aktien_vorjahr",
                   "verlustvortrag_allgemein_vorjahr",
                   "verlustvortrag_termingeschaefte_vorjahr"},
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


# Alle Schlüssel, die eine Krypto-Quelle auf oberster Ebene führen darf. 'kennzahlen'
# und 'kap_zeilen' gehören der KAP-Hälfte und werden gesondert gemeldet (s. build).
#
# Die Liste unten ist nur die feste Basis: was krypto_fifo.py schreibt und was
# brokerprofile.wende_an für JEDES Profil erzeugt. Alles, was ein einzelnes Profil
# zusätzlich produziert ('etoro_extra', 'so_zeilen', 'abgleich' …), kommt aus
# brokerprofile.erzeugbare_pfade() dazu — von Hand gepflegt driftet diese Liste mit
# jedem neuen Profil auseinander, und eine Falschwarnung auf der eigenen Ausgabe
# begräbt genau die Meldung, für die die Prüfung da ist.
KRYPTO_FELDER_BASIS = (
    "steuerjahr", "tax_year", "quelle", "quelle_beschreibung", "source", "profil",
    "profil_status", "profil_geprueft_am", "zahlennotation", "summen_basis",
    "abgleich", "paragraph_23", "paragraph_22_nr3", "paragraph_22_nr_3",
    "steuerfrei_langfristig_eur", "warnungen", "elster_extra", "hinweise",
    "steuerpflichtiger_aus_report", "transactions", "anzahl_transaktionen",
    "kennzahlen", "kap_zeilen", "so_zeilen", "koinly_extra", "etoro_extra",
    "etoro_kap",
    # Felder, die krypto_fifo.py selbst schreibt (Belegteil, keine Steuerwerte)
    "methode", "alle_veraeusserungen", "offene_bestaende", "veraeusserungen",
    "alle_ertraege",
)

_krypto_felder_cache: set | None = None


def krypto_felder() -> set:
    """Bekannte Top-Level-Schlüssel einer Krypto-Quelle, inkl. aller Profil-Extras.

    Fällt auf die feste Basis zurück, wenn brokerprofile/die Profile nicht lesbar
    sind — die Prüfung ist eine Hilfe, kein Muss, und darf den Report nie stoppen.
    """
    global _krypto_felder_cache
    if _krypto_felder_cache is None:
        felder = set(KRYPTO_FELDER_BASIS)
        try:
            import brokerprofile as bp  # lokal: nur für die Namensliste
            for profil in bp.lade_profile():
                felder |= {p.split(".")[0] for p in bp.erzeugbare_pfade(profil)}
        except Exception:
            pass
        _krypto_felder_cache = felder
    return _krypto_felder_cache


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

    # Unbekannte Schlüssel melden — sonst verschwindet ein Tippfehler wie
    # 'paragraph_22_nr_33' spurlos und der Betrag wird still zu 0,00 €. Dieselbe
    # Logik wie normiere_kap_quelle und pruefe_unbekannte_felder.
    bekannt = krypto_felder()
    for key in roh:
        if key in bekannt:
            continue
        vorschlag = _aehnlichstes(key, bekannt)
        wink = (f" — meintest du '{vorschlag}'?" if vorschlag else
                " — dieser Schlüssel wird von build_taxreport.py nicht ausgewertet.")
        warn.append(
            f"Krypto-Quelle '{herkunft}': unbekannter Schlüssel '{key}'{wink} "
            f"Der Wert wurde NICHT verrechnet.")

    return {
        "quelle": roh.get("quelle") or roh.get("source") or herkunft,
        "datei": herkunft,
        # Trägt diese Datei außerdem eine KAP-Hälfte? Dann würde --krypto-result
        # allein sämtliche Kennzahlen (KESt, Verluste …) verschlucken.
        "hat_kap_teil": bool(roh.get("kennzahlen") or roh.get("kap_zeilen")),
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


def _pruefe_doppelte_pfade(pfade, art: str) -> None:
    """Dieselbe Datei zweimal in EINER Liste zählt jeden Betrag doppelt."""
    gesehen, doppelt = set(), []
    for p in pfade or []:
        schluessel = str(Path(p).resolve()) if Path(p).exists() else str(p)
        if schluessel in gesehen and str(p) not in doppelt:
            doppelt.append(str(p))
        gesehen.add(schluessel)
    if doppelt:
        raise EingabeFehler(
            f"{art} mehrfach angegeben: {', '.join(doppelt)}. Jede Datei würde ihre "
            f"Beträge doppelt in den Report bringen — jede Quelle genau einmal "
            f"übergeben. (Dieselbe Datei zusätzlich in der jeweils anderen Liste "
            f"anzugeben ist dagegen erlaubt und richtig, wenn sie beide Hälften "
            f"trägt: jede Hälfte wird dann genau einmal verbraucht.)")


def lade_krypto_quellen(pfade) -> list:
    _pruefe_doppelte_pfade(pfade, "Krypto-Quelle")
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
        for row in q["elster_extra"]:
            elster_extra.append(dict(row, _quelle_id=q["quelle"])
                                if isinstance(row, dict) else row)

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
# KAP-Quellen: Steuerbescheinigungen, Erträgnisaufstellungen, Auslandsbroker
# ─────────────────────────────────────────────────────────────────────────────


def normiere_kap_quelle(roh, herkunft: str = "kap-result") -> dict:
    """Prüft eine KAP-Quell-JSON gegen das Schema `kap` und normiert sie.

    Contract: references/broker-profile.md → „Ausgabeschemata → `kap`".

    VORZEICHENREGEL — die beiden Blöcke folgen bewusst verschiedenen Konventionen:

    * `kap_zeilen` ist die **wörtliche Abschrift** dessen, was der Report druckt.
      Deutsche Steuerbescheinigungen drucken Verluste als positive Beträge (und
      genau so will ELSTER sie in den „Verluste"-Zeilen haben). Diese Werte werden
      deshalb UNVERÄNDERT durchgereicht — kein Vorzeichenwechsel, keine
      Vorzeichenwarnung, keine Plausibilisierung.
    * `kennzahlen` ist die **normierte, vorzeichenbehaftete** Fassung: Verluste
      negativ, Gewinne und einbehaltene Steuern positiv. Nur hier wird das
      Vorzeichen geprüft und auf -abs() bzw. +… gezwungen, damit eine Quelle mit
      umgekehrter Konvention nicht die Verluste einer anderen aufhebt.

    Der Abgleich Rohzeile ↔ abgeleiteter Wert vergleicht daher Beträge, nicht
    Vorzeichen (siehe _kap_zeilen_abgleich). Verrechnet wird hier NICHTS — die
    Verlusttöpfe des § 20 Abs. 6 EStG rechnet build() einmal auf der Summe.
    """
    if not isinstance(roh, dict):
        raise EingabeFehler(
            f"KAP-Quelle '{herkunft}': erwartet wurde ein JSON-Objekt, gefunden wurde "
            f"{type(roh).__name__}.")

    kennz = roh.get("kennzahlen")
    if kennz is None:
        raise EingabeFehler(
            f"KAP-Quelle '{herkunft}': Pflichtfeld 'kennzahlen' fehlt. Erwartet wird das "
            f"Ausgabeschema 'kap' mit 'steuerjahr', 'kap_zeilen' und 'kennzahlen' "
            f"({', '.join(KAP_KENNZAHLEN)}) — siehe references/broker-profile.md.")
    if not isinstance(kennz, dict):
        raise EingabeFehler(
            f"KAP-Quelle '{herkunft}': Feld 'kennzahlen' muss ein Objekt aus Kennzahl → "
            f"Betrag sein, gefunden wurde {type(kennz).__name__}.")

    zeilen_roh = roh.get("kap_zeilen")
    if zeilen_roh in (None, ""):
        zeilen_roh = {}
    if not isinstance(zeilen_roh, dict):
        raise EingabeFehler(
            f"KAP-Quelle '{herkunft}': Feld 'kap_zeilen' muss ein Objekt aus "
            f"Anlage-KAP-Zeile → Betrag sein (z. B. {{\"7\": \"1234.56\"}}), gefunden "
            f"wurde {type(zeilen_roh).__name__}.")

    jahr_roh = _erst(roh, "steuerjahr", "tax_year")
    jahr = None
    if jahr_roh is not None:
        try:
            jahr = int(str(jahr_roh).strip())
        except (TypeError, ValueError):
            raise EingabeFehler(
                f"KAP-Quelle '{herkunft}': 'steuerjahr' ist keine Jahreszahl ({jahr_roh!r}).")
        if not (1990 <= jahr <= 2100):
            raise EingabeFehler(
                f"KAP-Quelle '{herkunft}': 'steuerjahr' {jahr} liegt außerhalb des "
                f"plausiblen Bereichs.")

    warnungen: list = []

    werte: dict = {}
    for name in KAP_KENNZAHLEN:
        betrag = _betrag(kennz.get(name), f"{herkunft}.kennzahlen.{name}")
        if name in KAP_VERLUST_KENNZAHLEN:
            if betrag > 0:
                warnungen.append(
                    f"KAP-Quelle '{herkunft}': Kennzahl '{name}' ist positiv "
                    f"({fmt_eur(betrag)}), laut Contract tragen Verluste ein negatives "
                    f"Vorzeichen. Der Betrag wurde als Verlust in Höhe von "
                    f"{fmt_eur(betrag)} angesetzt — Vorzeichen in der Quelle prüfen.")
            betrag = -abs(betrag)
        elif betrag < 0 and name in KAP_STEUER_KENNZAHLEN:
            warnungen.append(
                f"KAP-Quelle '{herkunft}': einbehaltene Steuer '{name}' ist negativ "
                f"({fmt_eur(betrag)}). Der Wert wurde unverändert übernommen und mindert "
                f"damit die Anrechnung — bitte in der Bescheinigung gegenprüfen.")
        elif betrag < 0 and name in KAP_GEWINN_KENNZAHLEN:
            warnungen.append(
                f"KAP-Quelle '{herkunft}': Kennzahl '{name}' ist negativ "
                f"({fmt_eur(betrag)}), laut Contract tragen Gewinne ein positives "
                f"Vorzeichen. Der Wert wurde unverändert übernommen — gehört hier ein "
                f"Verlust hin, dann in die passende 'verlust…'-Kennzahl eintragen, "
                f"sonst läuft er im falschen Verrechnungskreis.")
        werte[name] = betrag

    for key in kennz:
        if key in KAP_KENNZAHLEN:
            continue
        vorschlag = _aehnlichstes(key, KAP_KENNZAHLEN)
        wink = (f" — meintest du '{vorschlag}'?" if vorschlag else
                f" — bekannt sind: {', '.join(KAP_KENNZAHLEN)}.")
        warnungen.append(
            f"KAP-Quelle '{herkunft}': unbekannte Kennzahl 'kennzahlen.{key}'{wink} "
            f"Der Wert wurde NICHT verrechnet.")

    # Rohzeilen: wörtliche Abschrift, KEINE Vorzeichenkorrektur und keine
    # Vorzeichenwarnung (siehe Vorzeichenregel im Docstring). Geprüft wird nur,
    # dass überhaupt ein lesbarer Betrag mit Zeilennummer dasteht.
    zeilen: list = []
    for nr, wert in zeilen_roh.items():
        nummer = str(nr).strip()
        if not nummer:
            raise EingabeFehler(
                f"KAP-Quelle '{herkunft}': 'kap_zeilen' enthält eine Zeile ohne "
                f"Zeilennummer. Erwartet wird Zeilennummer → Betrag, z. B. "
                f"{{\"7\": \"1234.56\"}}.")
        zeilen.append({"zeile": nummer,
                       "wert": _betrag(wert, f"{herkunft}.kap_zeilen.{nummer}")})
    zeilen.sort(key=lambda z: (0, int(z["zeile"])) if z["zeile"].isdigit() else (1, 0))

    warn_quelle = roh.get("warnungen")
    if warn_quelle not in (None, "") and not isinstance(warn_quelle, list):
        raise EingabeFehler(
            f"KAP-Quelle '{herkunft}': Feld 'warnungen' muss eine Liste sein, gefunden "
            f"wurde {type(warn_quelle).__name__}.")
    extra = roh.get("elster_extra")
    if extra not in (None, "") and not isinstance(extra, list):
        raise EingabeFehler(
            f"KAP-Quelle '{herkunft}': Feld 'elster_extra' muss eine Liste sein, gefunden "
            f"wurde {type(extra).__name__}.")

    # Trägt diese Datei außerdem eine Krypto-Hälfte (§ 23 / § 22 Nr. 3)? Dann würde
    # --kap-result allein die privaten Veräußerungsgeschäfte verschlucken.
    hat_krypto = bool(roh.get("paragraph_23") or roh.get("paragraph_22_nr3")
                      or roh.get("paragraph_22_nr_3") or roh.get("transactions"))

    return {
        "_kap_normiert": True,
        "quelle": roh.get("quelle") or roh.get("source") or herkunft,
        "datei": herkunft,
        "hat_krypto_teil": hat_krypto,
        "profil": roh.get("profil"),
        "steuerjahr": jahr,
        "kennzahlen": werte,
        "kap_zeilen": zeilen,
        "warnungen": warnungen + [str(w) for w in (warn_quelle or [])],
        "elster_extra": list(extra or []),
        "hinweise": [str(h) for h in (roh.get("hinweise") or [])],
    }


def lade_kap_quellen(pfade) -> list:
    _pruefe_doppelte_pfade(pfade, "KAP-Quelle")
    quellen = []
    for p in pfade or []:
        try:
            with open(p, encoding="utf-8") as f:
                roh = json.load(f)
        except FileNotFoundError:
            raise EingabeFehler(f"KAP-Quelle nicht gefunden: {p}")
        except json.JSONDecodeError as e:
            raise EingabeFehler(f"KAP-Quelle '{p}' ist kein gültiges JSON: {e}")
        quellen.append(normiere_kap_quelle(roh, herkunft=str(p)))
    return quellen


def _als_kap_quellenliste(kap) -> list:
    """Akzeptiert eine Liste normierter Quellen, eine Liste roher Dicts oder ein Dict."""
    if kap is None:
        return []
    if isinstance(kap, dict):
        kap = [kap]
    quellen = []
    for i, q in enumerate(kap):
        if isinstance(q, dict) and q.get("_kap_normiert"):
            quellen.append(q)
        else:
            quellen.append(normiere_kap_quelle(q, herkunft=f"kap-quelle[{i}]"))
    return quellen


def aggregiere_kap(quellen: list, jahr: int, handeingabe: dict) -> dict:
    """Addiert alle KAP-Quellen und die handgepflegten 'anlage_kap'-Werte.

    Ergebnis ist NUR die Summe je Kennzahl plus die Aufschlüsselung je Quelle —
    die Verlustverrechnung (§ 20 Abs. 6 EStG) rechnet build() danach EINMAL auf
    dieser Summe. Anders herum (je Quelle verrechnen und dann addieren) wäre
    derselbe Fehler wie eine je Report geprüfte § 23-Freigrenze: ein
    Aktienverlust in Depot A würde nicht gegen den Aktiengewinn in Depot B laufen.
    """
    warnungen: list = []
    hinweise: list = []

    jahre = sorted({q["steuerjahr"] for q in quellen if q["steuerjahr"] is not None})
    if len(jahre) > 1:
        details = "; ".join(f"{q['datei']}: {q['steuerjahr']}" for q in quellen
                            if q["steuerjahr"] is not None)
        raise EingabeFehler(
            "Die angegebenen KAP-Quellen gehören zu verschiedenen Steuerjahren "
            f"({details}). Ein Report kann nur ein Steuerjahr abbilden — die Quellen "
            "nach Jahren trennen und je Jahr einen Report bauen.")
    if jahre and jahre[0] != jahr:
        warnungen.append(
            f"KAP-Quellen weisen das Steuerjahr {jahre[0]} aus, die Steuerdaten jedoch "
            f"{jahr} — bitte prüfen, ob die richtigen Dateien übergeben wurden.")

    hand = {name: handeingabe.get(name, NULL) for name in KAP_KENNZAHLEN}
    summen = {name: hand[name] + sum((q["kennzahlen"][name] for q in quellen), NULL)
              for name in KAP_KENNZAHLEN}
    aus_dateien = {name: sum((q["kennzahlen"][name] for q in quellen), NULL)
                   for name in KAP_KENNZAHLEN}

    def eintrag(quelle, datei, profil, steuerjahr, art, kennzahlen, zeilen):
        return {"quelle": quelle, "datei": datei, "profil": profil,
                "steuerjahr": steuerjahr, "art": art,
                "kennzahlen": {n: str(q2(kennzahlen[n])) for n in KAP_KENNZAHLEN},
                "kap_zeilen": {z["zeile"]: str(q2(z["wert"])) for z in zeilen}}

    aufschluesselung: list = []
    hand_belegt = any(hand[n] != 0 for n in KAP_KENNZAHLEN)
    if quellen or hand_belegt:
        aufschluesselung.append(eintrag(
            "steuerdaten.json (anlage_kap)", None, None, jahr, "handeingabe", hand, []))
    for q in quellen:
        aufschluesselung.append(eintrag(q["quelle"], q["datei"], q["profil"],
                                        q["steuerjahr"], "datei", q["kennzahlen"],
                                        q["kap_zeilen"]))

    # Doppelerfassung sichtbar machen: derselbe Betrag einmal in der Datei und
    # einmal von Hand ergibt hier die Summe — das ist gewollt, aber nur, wenn es
    # wirklich zwei verschiedene Beträge sind.
    if quellen and hand_belegt:
        doppelt = [n for n in KAP_KENNZAHLEN if hand[n] != 0 and aus_dateien[n] != 0]
        if doppelt:
            hinweise.append(
                "Anlage KAP: für "
                + ", ".join(f"'{n}' (Handeingabe {fmt_eur(hand[n])} + Dateien "
                            f"{fmt_eur(aus_dateien[n])} = {fmt_eur(summen[n])})"
                            for n in doppelt)
                + " wurden Handeingabe und Datei-Quellen ADDIERT. Steckt die "
                  "Steuerbescheinigung bereits in einer der Dateien, den Wert in "
                  "'anlage_kap' entfernen — sonst wird derselbe Ertrag doppelt erklärt. "
                  "Die Aufschlüsselung je Quelle steht unter anlagen.KAP.quellen.")

    zeilen: list = []
    for q in quellen:
        for z in q["kap_zeilen"]:
            zeilen.append({"zeile": z["zeile"], "wert": str(q2(z["wert"])),
                           "quelle": q["quelle"], "datei": q["datei"]})

    # Herkunft mitführen: das ELSTER-Mapping entdoppelt je Quelle, und die Zeilen
    # zweier Depots dürfen sich nicht gegenseitig verdrängen.
    elster_extra: list = []
    for q in quellen:
        for row in q["elster_extra"]:
            elster_extra.append(dict(row, _quelle_id=q["quelle"])
                                if isinstance(row, dict) else row)
    for q in quellen:
        warnungen.extend(q["warnungen"])
    for q in quellen:
        for h in q["hinweise"]:
            if h not in hinweise:
                hinweise.append(h)

    return {
        "quellen": aufschluesselung,
        "steuerjahr": jahre[0] if jahre else jahr,
        "summen": summen,
        "aus_dateien": aus_dateien,
        "handeingabe": hand,
        "kap_zeilen": zeilen,
        "warnungen": warnungen,
        "hinweise": hinweise,
        "elster_extra": elster_extra,
    }


def _kap_zeilen_abgleich(roh_zeilen: list, abgeleitet: dict):
    """Entscheidet je Anlage-KAP-Zeile, ob die abgeleitete Zeile eine Dopplung ist.

    Die Rohzeile ist das, was der Steuerpflichtige in ELSTER eintippt; deckt sie
    den abgeleiteten Wert vollständig ab, hat die abgeleitete Zeile keinen
    eigenen Informationswert und entfällt. Summiert der Report dagegen über
    mehrere Quellen (oder kommt Handgetipptes hinzu), bleiben beide stehen —
    dann ist die abgeleitete Zeile die Summe, die ELSTER sehen will.

    Rückgabe: (Menge der unterdrückten Zeilennummern, Hinweise).
    """
    unterdrueckt, hinweise = set(), []
    nach_nummer: dict = {}
    for z in roh_zeilen:
        nach_nummer.setdefault(z["zeile"], []).append(z)
    for nummer, wert in abgeleitet.items():
        eintraege = nach_nummer.get(nummer)
        if not eintraege:
            continue
        summe_roh = sum((abs(to_decimal(e["wert"])) for e in eintraege), NULL)
        quellen = ", ".join(sorted({e["quelle"] for e in eintraege}))
        if len(eintraege) == 1 and summe_roh == abs(wert):
            unterdrueckt.add(nummer)
            hinweise.append(
                f"Anlage-KAP-Zeile {nummer}: Rohzeile aus '{quellen}' und abgeleiteter "
                f"Wert sind identisch ({fmt_eur(wert)}). Es gilt die Rohzeile — die "
                f"abgeleitete Zeile wurde aus dem ELSTER-Mapping entfernt, damit der "
                f"Betrag nicht doppelt eingetragen wird.")
        else:
            hinweise.append(
                f"Anlage-KAP-Zeile {nummer}: Es liegen Rohzeilen aus {quellen} vor "
                f"(zusammen {fmt_eur(summe_roh)}); der Report weist zusätzlich einen "
                f"abgeleiteten Gesamtwert von {fmt_eur(wert)} aus. In ELSTER gehört in "
                f"Zeile {nummer} EIN Betrag — die Summe über alle Bescheinigungen und "
                f"die handgepflegten Angaben ({fmt_eur(wert)}). Die Rohzeilen bleiben "
                f"als Beleg im Mapping stehen.")
    return unterdrueckt, hinweise


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


def build(steuerdaten: dict, krypto=None, kap_quellen=None):
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

    # --- Krypto-Quellen zuerst zusammenführen ---
    # Muss VOR der Anlage KAP stehen: Koinly weist Futures/Derivate gesondert aus
    # (koinly_extra), und die sind in Deutschland Termingeschäfte nach § 20 Abs. 2
    # EStG — sie gehören damit in die KAP-Verrechnung, nicht nur in einen Hinweis.
    quellen = _als_quellenliste(krypto)
    kd = aggregiere_krypto(quellen, jahr, werte_jahr)
    for w in kd["warnungen"]:
        if w not in warnungen:
            warnungen.append(w)
    for h in kd["hinweise"]:
        if h not in hinweise:
            hinweise.append(h)

    # --- Anlage KAP (Kapitalerträge, § 20 / § 32d EStG) ---
    # Handgetippte Werte und Datei-Quellen werden ADDIERT (nicht ersetzt) und erst
    # danach verrechnet: § 20 Abs. 6 EStG gilt personenbezogen über alle Depots.
    hand_kap = {}
    for name in KAP_KENNZAHLEN:
        wert = _betrag(kap.get(name), f"anlage_kap.{name}")
        hand_kap[name] = -abs(wert) if name in KAP_VERLUST_KENNZAHLEN else wert
    kapq = _als_kap_quellenliste(kap_quellen)
    kapd = aggregiere_kap(kapq, jahr, hand_kap)
    kap_summen = kapd["summen"]

    # Eine Datei kann BEIDE Hälften tragen (die Broker-Profile schreiben je nach
    # Report Kennzahlen und § 23 in dieselbe JSON). Wird sie nur einem Leser
    # übergeben, verschwindet die andere Hälfte lautlos — genau der stille
    # Nullwert, den dieses Skill an keiner Stelle zulassen darf. Wird sie beiden
    # übergeben, verbraucht jeder Leser seine Hälfte genau einmal; dann ist alles
    # gut und es gibt keine Meldung.
    kap_dateien = {q["datei"] for q in kapq}
    krypto_dateien = {q["datei"] for q in quellen}
    for q in kapq:
        if q.get("hat_krypto_teil") and q["datei"] not in krypto_dateien:
            warnungen.append(
                f"Die Datei '{q['datei']}' enthält außer den KAP-Kennzahlen auch ein "
                f"Krypto-Ergebnis (§ 23 / § 22 Nr. 3). Über --kap-result wird dieser "
                f"Teil NICHT gelesen und bliebe unversteuert. Dieselbe Datei "
                f"zusätzlich mit --krypto-result übergeben — jede Hälfte wird dann "
                f"genau einmal verbraucht.")
    for q in quellen:
        if q.get("hat_kap_teil") and q["datei"] not in kap_dateien:
            warnungen.append(
                f"Die Datei '{q['datei']}' enthält außer dem Krypto-Ergebnis auch "
                f"KAP-Kennzahlen (Kapitalerträge, anrechenbare Kapitalertragsteuer, "
                f"Verluste). Über --krypto-result werden diese NICHT gelesen und "
                f"fehlen in der Anlage KAP. Dieselbe Datei zusätzlich mit "
                f"--kap-result übergeben — jede Hälfte wird dann genau einmal "
                f"verbraucht.")

    kap_ertraege = kap_summen["kapitalertraege"]
    gewinn_aktien = kap_summen["gewinn_aktien"]
    gewinn_termin = kap_summen["gewinn_termingeschaefte"]
    verlust_aktien = abs(kap_summen["verlust_aktien"])
    verlust_termin = abs(kap_summen["verlust_termingeschaefte"])
    verluste_ohne_aktien = abs(kap_summen["verluste_ohne_aktien"])
    verluste_ausfall = abs(kap_summen["verluste_ausfall"])
    anrechenbare_kest = kap_summen["anrechenbare_kest"]
    kap_soli_einbehalten = kap_summen["einbehaltener_soli"]
    kap_kist_einbehalten = kap_summen["einbehaltene_kirchensteuer"]
    auslaendische_quellensteuer = kap_summen["auslaendische_quellensteuer"]
    fiktive_quellensteuer = kap_summen["fiktive_quellensteuer"]
    # § 32d Abs. 5 EStG: tatsächliche und fiktive (DBA-)Quellensteuer werden
    # gleich angerechnet, bleiben aber getrennte ELSTER-Zeilen (Z. 41 / Z. 42).
    quellensteuer_gesamt = auslaendische_quellensteuer + fiktive_quellensteuer

    # Koinly-Futures: Ergebnis aus Derivaten, das KEINE Steuerbescheinigung ausweist
    # und deshalb auch in keiner Davon-Zeile stecken kann. Positiv ist es zu
    # erklärender Kapitalertrag (und zugleich Termingeschäftsgewinn), negativ ein
    # Termingeschäftsverlust. Nur als Hinweis auszuweisen hieße: geparst, angezeigt,
    # nicht gerechnet — die Abgeltungsteuer bliebe falsch.
    futures_netto = _betrag((kd.get("koinly_extra") or {}).get("futures_nettoergebnis_eur"),
                            "koinly_extra.futures_nettoergebnis_eur")
    # Beide Vorzeichen wandern in 'kap_ertraege': die Verlustzeilen 22–25 sind
    # davon-Zeilen zum Saldo (siehe KAP_DAVON_ANNAHME_HINWEIS) und mindern die
    # Bemessungsgrundlage nicht mehr selbst. Ein Futures-Verlust nur in
    # 'verlust_termin' zu buchen hieße deshalb: erklärt, ausgewiesen — und nirgends
    # abgezogen.
    if futures_netto > 0:
        kap_ertraege += futures_netto
        gewinn_termin += futures_netto
    elif futures_netto < 0:
        kap_ertraege += futures_netto          # negativ: mindert den Saldo
        verlust_termin += abs(futures_netto)
    if futures_netto != 0:
        hinweise.append(
            f"Futures/Derivate aus der Krypto-Quelle ({fmt_eur(futures_netto)}, "
            f"koinly_extra.futures_nettoergebnis_eur) wurden als Termingeschäft nach "
            f"§ 20 Abs. 2 EStG in die Anlage KAP übernommen — "
            + ("als Kapitalertrag und Termingeschäftsgewinn (Z. 7 und Z. 21)."
               if futures_netto > 0 else
               "als Termingeschäftsverlust (Z. 24), seit dem JStG 2024 unbeschränkt "
               "verrechenbar.")
            + " Die Einordnung als Termingeschäft ist steuerlich nicht abschließend "
              "geklärt — vom Steuerberater prüfen lassen.")

    vv_aktien_vorjahr = abs(_betrag(kap.get("verlustvortrag_aktien_vorjahr"),
                                    "anlage_kap.verlustvortrag_aktien_vorjahr"))
    # Festgestellte Verlustvorträge der Vorjahre aus Kapitalvermögen außerhalb des
    # Aktientopfs. Der Termingeschäfte-Vortrag hatte bis zum JStG 2024 einen eigenen
    # Verrechnungskreis; mit dessen Aufhebung (anwendbar in allen offenen Fällen)
    # gibt es keinen Grund mehr, ihn getrennt zu führen — beide Eingaben fließen
    # deshalb in EINEN allgemeinen Topf. Das ist im Report ausgewiesen.
    vv_allg_vorjahr = abs(_betrag(kap.get("verlustvortrag_allgemein_vorjahr"),
                                  "anlage_kap.verlustvortrag_allgemein_vorjahr"))
    vv_termin_vorjahr = abs(_betrag(kap.get("verlustvortrag_termingeschaefte_vorjahr"),
                                    "anlage_kap.verlustvortrag_termingeschaefte_vorjahr"))
    vv_allg_gesamt = vv_allg_vorjahr + vv_termin_vorjahr
    if vv_termin_vorjahr > 0:
        hinweise.append(
            f"Der festgestellte Termingeschäfte-Verlustvortrag aus Vorjahren "
            f"({fmt_eur(vv_termin_vorjahr)}) wurde dem allgemeinen Verlusttopf "
            f"zugeschlagen: der eigene Verrechnungskreis des § 20 Abs. 6 Satz 5 EStG "
            f"ist durch das JStG 2024 aufgehoben (anwendbar in allen offenen Fällen), "
            f"der Vortrag ist damit mit sämtlichen Kapitalerträgen verrechenbar.")
    for w in kapd["warnungen"]:
        print(f"WARNUNG: {w}", file=sys.stderr)
        warnungen.append(w)
    for h in kapd["hinweise"]:
        if h not in hinweise:
            hinweise.append(h)
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

    # 'gewinn_termingeschaefte' ist wie 'gewinn_aktien' eine DAVON-Zeile: Anlage KAP
    # Z. 21 steht unter der Überschrift "In Zeile 7 enthaltene …" und weist die
    # Termingeschäftsgewinne nur ihrem Verrechnungskreis zu. Sie erhöht die
    # Bemessungsgrundlage deshalb NICHT — täte sie es, würde jeder Gewinn, den eine
    # Steuerbescheinigung ohnehin schon in Zeile 7 ausweist, doppelt versteuert.
    # Meldet eine Quelle einen Gewinn, der NICHT in 'kapitalertraege' steckt, gehört
    # er ins Profil der Quelle, nicht in eine Sonderbehandlung hier.
    if gewinn_termin > kap_ertraege:
        warnungen.append(
            f"'gewinn_termingeschaefte' ({fmt_eur(gewinn_termin)}) ist größer als "
            f"'kapitalertraege' ({fmt_eur(kap_ertraege)}). Anlage KAP Z. 21 ist eine "
            f"Davon-Zeile zu Zeile 7 ('In Zeile 7 enthaltene Einkünfte aus "
            f"Stillhalterprämien und Gewinne aus Termingeschäften') und erhöht die "
            f"Bemessungsgrundlage NICHT — die Gewinne müssen bereits in "
            f"'kapitalertraege' enthalten sein. Sonst bleiben sie unversteuert. "
            f"Bitte 'kapitalertraege' der Quelle prüfen.")
    if verluste_ausfall > 0:
        hinweise.append(AUSFALLVERLUST_HINWEIS)

    # Die Auslegung der Zeilen 20–25 entscheidet über die Bemessungsgrundlage und
    # damit über tausende Euro Abgeltungsteuer. Sie gehört deshalb an den ANFANG der
    # Hinweise, nicht ans Ende — und in den Disclaimer, den der Export prominent
    # rendert. Sobald eine Verlustzeile belegt ist, ist sie entscheidungsrelevant.
    if (verlust_aktien > 0 or verlust_termin > 0 or verluste_ohne_aktien > 0
            or verluste_ausfall > 0):
        hinweise.insert(0, KAP_DAVON_ANNAHME_HINWEIS)

    # ── § 20 Abs. 6 EStG ─────────────────────────────────────────────────────
    # GRUNDANNAHME (siehe KAP_DAVON_ANNAHME_HINWEIS, steht auch im Report):
    # 'kapitalertraege' ist der SALDO der Anlage-KAP-Zeile 7 bzw. 18/19 und enthält
    # die Verluste der Zeilen 22–25 BEREITS. Die Zeilen 20–25 stehen im Formular
    # unter derselben Überschrift „In den Zeilen 18 und 19 enthaltene …"; sie werden
    # deshalb ALLE gleich behandelt — als davon-Zeilen, die nur den Verrechnungskreis
    # bestimmen und die Bemessungsgrundlage nicht ein zweites Mal mindern.
    #
    # Folge: ein Verlust wird hier nicht ABGEZOGEN, sondern höchstens wieder
    # HINZUGERECHNET — nämlich dann, wenn der Saldo ihn verrechnet hat, obwohl er
    # das nicht durfte. Das trifft allein den Aktienverlust (§ 20 Abs. 6 Satz 4:
    # nur gegen Aktienveräußerungsgewinne). Für die Zeilen 22, 24 und 25 gibt es
    # seit dem JStG 2024 keinen eigenen Verrechnungskreis mehr — was der Saldo
    # verrechnet hat, durfte er verrechnen.
    #
    # Verrechnet werden darf der Aktienverlust genau bis zur Höhe der
    # Aktienveräußerungsgewinne — mehr sagt § 20 Abs. 6 Satz 4 nicht, und mehr lässt
    # sich hier auch nicht prüfen. Ein zusätzlicher Deckel auf 'kapitalertraege'
    # (frühere Fassung) wäre unter der davon-Zeilen-Lesart schlicht falsch: der
    # Saldo ist bereits um sämtliche Verluste gemindert, Aktiengewinne dürfen ihn
    # deshalb legitim übersteigen (Saldo 5.000 = 30.000 Gewinne − 25.000 Verluste).
    # Gegen einen frei erfundenen 'gewinn_aktien' schützt die Warnung oben, nicht
    # eine Deckelung, die bei ehrlichen Zahlen falsch rechnet.
    aktien_verrechnet = min(verlust_aktien, max(gewinn_aktien, NULL))
    # Der Teil des Aktienverlusts, den der Saldo nicht verrechnen durfte: er wird der
    # Bemessungsgrundlage wieder zugeschlagen und in den Aktien-Verlustvortrag
    # gestellt. Genau hier — und nur hier — bewegen die Verlustzeilen die Steuer.
    aktien_ueberhang = verlust_aktien - aktien_verrechnet
    kap_basis = kap_ertraege + aktien_ueberhang
    # Festgestellter Aktien-Verlustvortrag der Vorjahre: erst nach den laufenden
    # Verlusten des Jahres und ebenfalls nur gegen Aktiengewinne. Er steckt — anders
    # als die Verluste dieses Jahres — NICHT im Saldo, ist also ein echter Abzug.
    # Gedeckelt auf die noch freien Aktiengewinne und auf die Bemessungsgrundlage:
    # ein Altvortrag darf keine negativen Kapitalerträge erzeugen.
    aktien_gewinn_rest = max(gewinn_aktien, NULL) - aktien_verrechnet
    vv_aktien_verbraucht = min(vv_aktien_vorjahr, aktien_gewinn_rest,
                               max(kap_basis, NULL))
    vv_aktien_rest = vv_aktien_vorjahr - vv_aktien_verbraucht
    vortrag_aktien = aktien_ueberhang + vv_aktien_rest
    if aktien_ueberhang > 0:
        hinweise.append(
            f"Aktienverluste (Anlage KAP Z. 23, {fmt_eur(verlust_aktien)}) übersteigen "
            f"die Aktienveräußerungsgewinne (Z. 20, {fmt_eur(max(gewinn_aktien, NULL))}) "
            f"um {fmt_eur(aktien_ueberhang)}. Dieser Überhang darf nach § 20 Abs. 6 "
            f"Satz 4 EStG nicht mit den übrigen Kapitalerträgen verrechnet werden: er "
            f"wurde den Kapitalerträgen wieder hinzugerechnet "
            f"({fmt_eur(kap_ertraege)} + {fmt_eur(aktien_ueberhang)} = "
            f"{fmt_eur(kap_basis)}) und in den Aktien-Verlustvortrag gestellt. "
            f"Grundlage ist die Annahme, dass die Verluste im Saldo der "
            f"Kapitalerträge bereits enthalten sind — siehe den ANNAHME-Hinweis.")
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
    # Die Bemessungsgrundlage steht damit fest: Saldo + ringfenced Aktienüberhang
    # − Aktien-Altvortrag. Die Verluste der Zeilen 22, 24 und 25 kommen hier NICHT
    # noch einmal ab — sie stecken im Saldo (siehe KAP_DAVON_ANNAHME_HINWEIS).
    netto_kap = kap_basis - vv_aktien_verbraucht        # vorzeichenbehaftet

    # Ausweis der Verrechnung für die Zeilen 24, 22 und 25 (§ 20 Abs. 6 Sätze 5/6
    # sind seit dem JStG 2024 aufgehoben: kein eigener Verrechnungskreis, kein
    # Deckel). Das ist eine DARSTELLUNG, keine zweite Rechnung: gefragt ist, wie
    # viel dieser Verluste der Saldo tatsächlich aufgezehrt hat. Bezugsgröße ist
    # deshalb der rechnerische Betrag vor diesen Verlusten; die Reihenfolge
    # (Termin → übrige → Ausfall) bestimmt nur, welchem Topf ein nicht verrechneter
    # Rest zugeordnet wird — vorgetragen werden sie ohnehin gemeinsam.
    verluste_uebrige = verlust_termin + verluste_ohne_aktien + verluste_ausfall
    vor_uebrigen_verlusten = netto_kap + verluste_uebrige
    termin_verrechnet = min(verlust_termin, max(vor_uebrigen_verlusten, NULL))
    rest_uebrige = vor_uebrigen_verlusten - termin_verrechnet
    vortrag_termin = verlust_termin - termin_verrechnet
    ohne_aktien_verrechnet = min(verluste_ohne_aktien, max(rest_uebrige, NULL))
    rest_uebrige -= ohne_aktien_verrechnet
    ausfall_verrechnet = min(verluste_ausfall, max(rest_uebrige, NULL))
    vortrag_uebrige = (verluste_uebrige - termin_verrechnet - ohne_aktien_verrechnet
                       - ausfall_verrechnet)

    # Ein negativer Saldo, der über die erklärten Verlustzeilen hinausgeht (etwa ein
    # negatives 'kapitalertraege' ohne Verlustangabe), ist ebenfalls vortragsfähig.
    # 'vortrag_uebrige' deckt bereits den durch die Zeilen 22/24/25 erklärten Teil
    # ab — sonst stünde derselbe Verlust zweimal im Vortrag.
    vortrag_allgemein_laufend = (vortrag_uebrige
                                 + max(-vor_uebrigen_verlusten, NULL))

    # Festgestellter allgemeiner Verlustvortrag der Vorjahre: NACH dem
    # Sparer-Pauschbetrag. Das ist die Reihenfolge, die ELSTER anwendet, und sie
    # ist die richtige — es geht dabei nichts verloren: verbraucht wird nur, was
    # ohne den Pauschbetrag zu versteuern wäre, und der dadurch geschonte Teil des
    # Vortrags bleibt festgestellt und steht im Folgejahr wieder zur Verfügung.
    # Umgekehrt gerechnet würde der Sparer-Pauschbetrag von Altverlusten
    # aufgezehrt und wäre für dieses Jahr ersatzlos weg.
    bemessung_vor_vortrag = max(netto_kap - sparer_pb, NULL)
    vv_allg_verbraucht = min(vv_allg_gesamt, bemessung_vor_vortrag)
    vv_allg_rest = vv_allg_gesamt - vv_allg_verbraucht
    vortrag_allgemein = vortrag_allgemein_laufend + vv_allg_rest
    if vv_allg_gesamt > 0:
        hinweise.append(
            f"Allgemeiner Verlustvortrag aus Vorjahren ({fmt_eur(vv_allg_gesamt)}): "
            f"{fmt_eur(vv_allg_verbraucht)} wurden mit den Kapitalerträgen dieses "
            f"Jahres verrechnet, {fmt_eur(vv_allg_rest)} bleiben festgestellt "
            f"(§ 20 Abs. 6 Satz 3 EStG i. V. m. § 10d EStG). Der Abzug erfolgt "
            f"bewusst NACH dem Sparer-Pauschbetrag: so bleibt der Pauschbetrag für "
            f"dieses Jahr wirksam, und der dadurch nicht verbrauchte Teil des "
            f"Vortrags geht nicht verloren, sondern bleibt festgestellt.")
    bemessung_kap = bemessung_vor_vortrag - vv_allg_verbraucht

    # § 32d Abs. 1 Sätze 4/5: bei Kirchensteuerpflicht ESt = (e − 4q) / (4 + k).
    # q ist die anrechenbare ausländische Steuer nach § 32d Abs. 5 — tatsächliche
    # und fiktive Quellensteuer zusammen.
    if kist_satz is not None:
        kap_est = q2(max((bemessung_kap - 4 * quellensteuer_gesamt)
                         / (D("4") + kist_satz), NULL))
    else:
        kap_est = q2(max(bemessung_kap * D("0.25") - quellensteuer_gesamt, NULL))
    kap_soli = q2(kap_est * D("0.055"))
    kap_kist = q2(kap_est * kist_satz) if kist_satz is not None else None

    # --- Anlage SO: Krypto § 23 + § 22 Nr. 3 (Staking) + sonstige Leistungen ---
    # (die Quellen sind oben bereits zusammengeführt — kd)
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
    # Die von den Depots einbehaltenen Beträge (KESt, Soli, KiSt) sind Vorauszahlungen
    # auf die Jahressteuer und werden angerechnet. Die ausländische Quellensteuer steht
    # bewusst NICHT hier: sie ist bereits nach § 32d Abs. 5 EStG in der Abgeltungsteuer
    # abgezogen worden und würde sonst doppelt gutgeschrieben.
    soli_einbehalten_gesamt = soli_einbehalten + kap_soli_einbehalten
    kist_einbehalten_gesamt = kist_einbehalten + kap_kist_einbehalten
    anrechnung = (lohnsteuer + soli_einbehalten_gesamt + kist_einbehalten_gesamt
                  + anrechenbare_kest)
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
                "solidaritaetszuschlag_einbehalten": str(q2(soli_einbehalten_gesamt)),
                "kirchensteuer_einbehalten": str(q2(kist_einbehalten_gesamt)),
                "anrechenbare_kapitalertragsteuer": str(q2(anrechenbare_kest)),
                "soli_auf_kapitalertraege_einbehalten": str(q2(kap_soli_einbehalten)),
                "kirchensteuer_auf_kapitalertraege_einbehalten": str(q2(kap_kist_einbehalten)),
                "auslaendische_quellensteuer_nach_32d_abs5": str(q2(quellensteuer_gesamt)),
                "davon_fiktive_quellensteuer": str(q2(fiktive_quellensteuer)),
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
    # Rohzeilen aus den Bescheinigungen sind das, was der Steuerpflichtige eintippt.
    # Wo eine Rohzeile den abgeleiteten Wert vollständig abdeckt, gilt die Rohzeile.
    # Zeile 7 nimmt Kapitalerträge MIT inländischem Steuerabzug auf, Zeile 19 die
    # ausländischen ohne Steuerabzug. Meldet keine Quelle eine Zeile 7, wohl aber
    # eine Zeile 19 (typisch für einen Auslandsbroker), gehört der abgeleitete
    # Gesamtwert dorthin — sonst schickt der Report ausländische Erträge in eine
    # Zeile, die es in dieser Bescheinigung gar nicht gibt.
    # Eine mit 0,00 € ausgewiesene Zeile ist KEINE gemeldete Zeile: Profile füllen
    # ihr Zeilengerüst gern mit Nullen vor, und ein leeres Feld 7 darf die Erträge
    # nicht aus Zeile 19 herausziehen. Sonst stünden 5.000 € in beiden Zeilen.
    roh_nummern = {z["zeile"] for z in kapd["kap_zeilen"]
                   if _betrag(z["wert"], "kap_zeilen") != 0}
    # Verlustzeilen im Mapping tragen immer den Betrag (ELSTER-Konvention). Wo eine
    # Quelle das Vorzeichen anders setzt, wird das ausdrücklich gesagt — der Nutzer
    # sieht sonst im Mapping eine andere Zahl als in seiner Bescheinigung und hält
    # das für einen Fehler des Reports.
    negative_verlust_zeilen = sorted(
        {z["zeile"] for z in kapd["kap_zeilen"]
         if z["zeile"] in KAP_ZEILEN_VERLUST
         and _betrag(z["wert"], "kap_zeilen") < 0},
        key=lambda nr: (int(nr) if nr.isdigit() else 99))
    if negative_verlust_zeilen or any(
            nr in roh_nummern for nr in KAP_ZEILEN_VERLUST) or (
            verlust_aktien > 0 or verlust_termin > 0 or verluste_ohne_aktien > 0
            or verluste_ausfall > 0):
        if KAP_VERLUST_POSITIV_HINWEIS not in hinweise:
            hinweise.append(KAP_VERLUST_POSITIV_HINWEIS)
    if negative_verlust_zeilen:
        quellen_neg = sorted({z["quelle"] for z in kapd["kap_zeilen"]
                              if z["zeile"] in negative_verlust_zeilen
                              and _betrag(z["wert"], "kap_zeilen") < 0})
        hinweise.append(
            "Anlage-KAP-Zeile(n) " + ", ".join(negative_verlust_zeilen)
            + " werden von der Quelle " + ", ".join(quellen_neg)
            + " mit negativem Vorzeichen ausgewiesen. Im ELSTER-Mapping stehen sie "
              "als positiver Betrag — das ist die Zahl, die einzutippen ist. Die "
              "Abschrift der Bescheinigung bleibt in anlagen.KAP.kap_zeilen "
              "vorzeichengetreu erhalten.")
    ertraege_zeile = "19" if ("19" in roh_nummern and "7" not in roh_nummern) else "7"
    abgeleitete_kap_zeilen = {ertraege_zeile: kap_ertraege}
    if gewinn_termin != 0:
        abgeleitete_kap_zeilen["21"] = gewinn_termin
    if verluste_ohne_aktien > 0:
        abgeleitete_kap_zeilen["22"] = verluste_ohne_aktien
    if verlust_aktien > 0:
        abgeleitete_kap_zeilen["23"] = verlust_aktien
    if verlust_termin > 0:
        abgeleitete_kap_zeilen["24"] = verlust_termin
    if verluste_ausfall > 0:
        abgeleitete_kap_zeilen["25"] = verluste_ausfall
    if anrechenbare_kest != 0:
        abgeleitete_kap_zeilen["37"] = anrechenbare_kest
    if auslaendische_quellensteuer != 0:
        abgeleitete_kap_zeilen["41"] = auslaendische_quellensteuer
    if fiktive_quellensteuer != 0:
        abgeleitete_kap_zeilen["42"] = fiktive_quellensteuer
    kap_unterdrueckt, kap_zeilen_hinweise = _kap_zeilen_abgleich(
        kapd["kap_zeilen"], abgeleitete_kap_zeilen)
    for h in kap_zeilen_hinweise:
        if h not in hinweise:
            hinweise.append(h)
    # Wird von build_elster_mapping gefüllt: 'elster_extra'-Zeilen, die eine bereits
    # vorhandene Zeile (Rohzeile oder abgeleitete Zeile) betragsgleich wiederholen.
    doppelte_extra: list = []

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
        vv_aktien_rest=vv_aktien_rest, vv_allg_vorjahr=vv_allg_gesamt,
        vv_allg_verbraucht=vv_allg_verbraucht, vv_allg_rest=vv_allg_rest,
        gewinn_termin=gewinn_termin, verluste_ohne_aktien=verluste_ohne_aktien,
        verluste_ausfall=verluste_ausfall, fiktive_quellensteuer=fiktive_quellensteuer,
        auslaendische_quellensteuer=auslaendische_quellensteuer,
        kap_soli_einbehalten=kap_soli_einbehalten,
        kap_kist_einbehalten=kap_kist_einbehalten,
        kap_roh_zeilen=kapd["kap_zeilen"], kap_extra=kapd["elster_extra"],
        kap_unterdrueckt=kap_unterdrueckt, ertraege_zeile=ertraege_zeile,
        doppelte_extra_zeilen=doppelte_extra)

    # '_quelle_id' war nur für die Entdopplung nötig und gehört nicht in den Report.
    for liste in (kapd["elster_extra"], kd.get("elster_extra") or []):
        for row in liste:
            if isinstance(row, dict):
                row.pop("_quelle_id", None)

    for d in doppelte_extra:
        h = (f"{d['anlage']} {d['zeile']}: die Quelle '{d['quelle']}' liefert denselben "
             f"Betrag ({d['wert']}) sowohl als Rohzeile/abgeleiteten Wert als auch über "
             f"'elster_extra'. Die Wiederholung wurde aus dem ELSTER-Mapping entfernt — "
             f"zweimal eingetragen wäre es eine doppelte Erklärung.")
        if h not in hinweise:
            hinweise.append(h)

    report = {
        "meta": {
            "steuerjahr": jahr,
            "erstellt": datetime.now(timezone.utc).isoformat(),
            "waehrung": "EUR",
            "veranlagung": "Zusammenveranlagung" if verheiratet else "Einzelveranlagung",
            "steuerpflichtiger": tp,
            "krypto_quellen": kd.get("quellen", []),
            "kap_quellen": [{"quelle": q["quelle"], "datei": q["datei"],
                             "profil": q["profil"], "steuerjahr": q["steuerjahr"],
                             "art": q["art"]} for q in kapd["quellen"]],
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
                    "gewinn_termingeschaefte": str(q2(gewinn_termin)),
                    "verlust_aktien": str(q2(verlust_aktien)),
                    "verlust_termingeschaefte": str(q2(verlust_termin)),
                    "verluste_ohne_aktien": str(q2(verluste_ohne_aktien)),
                    "verluste_ausfall": str(q2(verluste_ausfall)),
                    "verlust_aktien_verrechnet": str(q2(aktien_verrechnet)),
                    # Der nach § 20 Abs. 6 Satz 4 nicht verrechenbare Aktienverlust,
                    # der der Bemessungsgrundlage wieder hinzugerechnet wurde, und
                    # das Ergebnis dieser Hinzurechnung. Wer gegen einen
                    # Steuerbescheid abgleicht, sieht hier den einzigen Punkt, an
                    # dem die davon-Verlustzeilen die Bemessungsgrundlage bewegen.
                    "verlust_aktien_ueberhang_hinzugerechnet": str(q2(aktien_ueberhang)),
                    "kapitalertraege_nach_aktien_hinzurechnung": str(q2(kap_basis)),
                    "verlust_termingeschaefte_verrechnet": str(q2(termin_verrechnet)),
                    "verluste_ohne_aktien_verrechnet": str(q2(ohne_aktien_verrechnet)),
                    "verluste_ausfall_verrechnet": str(q2(ausfall_verrechnet)),
                    "netto_kapitalertraege": str(q2(netto_kap)),
                    "sparer_pauschbetrag": str(sparer_pb),
                    # Drei verschiedene Zwischengrößen, die sich nur um den
                    # Verlustvortrag unterscheiden — deshalb sprechende Namen und
                    # kein bloßes 'nach_pauschbetrag'. Wer gegen einen
                    # Steuerbescheid abgleicht, braucht beide Stufen.
                    "nach_pauschbetrag_vor_verlustvortrag": str(q2(bemessung_vor_vortrag)),
                    "nach_pauschbetrag_und_verlustvortrag": str(q2(bemessung_kap)),
                    # Altname, unverändert = Bemessungsgrundlage der Abgeltungsteuer
                    # (also NACH Pauschbetrag UND Verlustvortrag).
                    "nach_pauschbetrag": str(q2(bemessung_kap)),
                    "bemessungsgrundlage_abgeltungsteuer": str(q2(bemessung_kap)),
                    "abgeltungsteuer": str(kap_est),
                    "abgeltungsteuer_soli": str(kap_soli),
                    "abgeltungsteuer_kirchensteuer": (None if kap_kist is None else str(kap_kist)),
                    "anrechenbare_kest": str(q2(anrechenbare_kest)),
                    "einbehaltener_soli": str(q2(kap_soli_einbehalten)),
                    "einbehaltene_kirchensteuer": str(q2(kap_kist_einbehalten)),
                    "auslaendische_quellensteuer": str(q2(auslaendische_quellensteuer)),
                    "fiktive_quellensteuer": str(q2(fiktive_quellensteuer)),
                    "anrechenbare_quellensteuer_gesamt": str(q2(quellensteuer_gesamt)),
                    "quellen": kapd["quellen"],
                    "aus_dateien": {n: str(q2(kapd["aus_dateien"][n]))
                                    for n in KAP_KENNZAHLEN},
                    "aus_handeingabe": {n: str(q2(kapd["handeingabe"][n]))
                                        for n in KAP_KENNZAHLEN},
                    "kap_zeilen": kapd["kap_zeilen"],
                    "verlustvortrag_aktien_vorjahr": str(q2(vv_aktien_vorjahr)),
                    "verlustvortrag_aktien_verbraucht": str(q2(vv_aktien_verbraucht)),
                    "verlustvortrag_aktien_rest": str(q2(vv_aktien_rest)),
                    "verlustvortrag_allgemein_vorjahr": str(q2(vv_allg_vorjahr)),
                    "verlustvortrag_termingeschaefte_vorjahr": str(q2(vv_termin_vorjahr)),
                    "verlustvortrag_allgemein_vorjahr_gesamt": str(q2(vv_allg_gesamt)),
                    "verlustvortrag_allgemein_verbraucht": str(q2(vv_allg_verbraucht)),
                    "verlustvortrag_allgemein_rest": str(q2(vv_allg_rest)),
                    "bemessung_vor_verlustvortrag": str(q2(bemessung_vor_vortrag)),
                    "verlustvortraege": {
                        "aktien": str(q2(vortrag_aktien)),
                        "termingeschaefte": str(q2(vortrag_termin)),
                        "allgemein": str(q2(vortrag_allgemein)),
                        "allgemein_davon_laufendes_jahr": str(q2(vortrag_allgemein_laufend)),
                        "allgemein_davon_rest_vorjahre": str(q2(vv_allg_rest)),
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

    # Die davon-Zeilen-Auslegung ist die folgenreichste Annahme der KAP-Rechnung —
    # sie steht deshalb nicht nur unter 'hinweise', sondern auch im Disclaimer.
    if KAP_DAVON_ANNAHME_HINWEIS in hinweise:
        report["disclaimer"].append(KAP_DAVON_ANNAHME_HINWEIS)
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
                         vv_aktien_verbraucht=NULL, vv_aktien_rest=NULL,
                         auslaendische_quellensteuer=None, kap_soli_einbehalten=NULL,
                         kap_kist_einbehalten=NULL, kap_roh_zeilen=None,
                         kap_extra=None, kap_unterdrueckt=None, gewinn_termin=NULL,
                         verluste_ohne_aktien=NULL, verluste_ausfall=NULL,
                         fiktive_quellensteuer=NULL, ertraege_zeile="7",
                         doppelte_extra_zeilen=None, vv_allg_vorjahr=NULL,
                         vv_allg_verbraucht=NULL, vv_allg_rest=NULL):
    """Zuordnung der Werte zu ELSTER-Eingabezeilen (manuelle Eingabe).

    Jede Zeile führt die Quelle mit, aus der ihr Wert stammt — sonst ist im Export nicht
    nachvollziehbar, welches Eingabefeld einen Betrag erzeugt hat.
    """
    m = []

    def add(anlage, zeile, bezeichnung, wert, quelle):
        m.append({"anlage": anlage, "zeile": zeile, "bezeichnung": bezeichnung,
                  "wert": str(wert), "quelle": quelle})

    # 'elster_extra' aus den Parsern wiederholt regelmäßig eine Zeile, die schon als
    # Rohzeile im Mapping steht — und dieselbe Datei kann zudem beiden Lesern
    # übergeben worden sein, dann käme die Zeile zweimal an. Wer beide abtippt,
    # erklärt den Betrag doppelt.
    #
    # Entdoppelt wird deshalb NUR INNERHALB EINER QUELLE, auf
    # (Quelle, Anlage, Zeile, Betrag). Zwei Depots, die zufällig denselben Betrag in
    # derselben Zeile melden, sind keine Wiederholung, sondern zwei Belege — ihre
    # Zeilen bleiben beide stehen, sonst verschwindet der Nachweis des zweiten
    # Depots aus dem Mapping. Ein abweichender Betrag bleibt ohnehin stehen.
    def _quellen_id(quelle) -> str:
        """'eToro (kap_zeilen)' und 'eToro (elster_extra)' sind dieselbe Quelle."""
        s = str(quelle)
        if s.endswith(")") and " (" in s:
            s = s[:s.rindex(" (")]
        return s

    def _schluessel(quelle, anlage, zeile, wert):
        try:
            betrag = str(q2(to_decimal(wert)))
        except ParseError:
            betrag = str(wert)
        return (_quellen_id(quelle), str(anlage), str(zeile), betrag)

    def add_extra(row, standard_anlage, standard_quelle):
        anlage = row.get("anlage", standard_anlage)
        zeile = row.get("zeile", "—")
        wert = row.get("wert", "")
        quelle = row.get("quelle", standard_quelle)
        # '_quelle_id' setzen die Aggregatoren, damit die Herkunft auch dann
        # feststeht, wenn das Profil in 'quelle' etwas anderes geschrieben hat.
        ident = row.get("_quelle_id", quelle)
        schluessel = _schluessel(ident, anlage, zeile, wert)
        if schluessel in {_schluessel(r["quelle"], r["anlage"], r["zeile"], r["wert"])
                          for r in m}:
            if doppelte_extra_zeilen is not None:
                doppelte_extra_zeilen.append({"anlage": anlage, "zeile": zeile,
                                              "wert": str(wert), "quelle": quelle})
            return
        add(anlage, zeile, row.get("bezeichnung", ""), wert, quelle)

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
    # Die abgeleiteten Zeilen führen die Summe über Handeingabe UND alle KAP-Quellen —
    # nur so steht in ELSTER je Zeile ein Betrag. Deckt eine einzelne Rohzeile den
    # Wert vollständig ab, wurde sie in build() als maßgeblich markiert und die
    # abgeleitete Zeile hier unterdrückt (kap_unterdrueckt).
    roh_zeilen = list(kap_roh_zeilen or [])
    unterdrueckt = set(kap_unterdrueckt or ())
    kap_herkunft = ("anlage_kap + KAP-Quellen (Summe)" if roh_zeilen or kap_extra
                    else "anlage_kap")

    def add_kap(nummer, zeile, bezeichnung, wert, quelle):
        if nummer in unterdrueckt:
            return
        add("Anlage KAP", zeile, bezeichnung, wert, quelle)

    add_kap(ertraege_zeile, f"Z. {ertraege_zeile}",
            KAP_ZEILEN_LABEL.get(ertraege_zeile, "Kapitalerträge"),
            q2(kap_ertraege), f"{kap_herkunft}.kapitalertraege")
    if gewinn_termin != 0:
        add_kap("21", "Z. 21", "Gewinne aus Termingeschäften und Stillhalterprämien "
                "(§ 20 Abs. 2 Satz 1 Nr. 3 / Abs. 1 Nr. 11 EStG)",
                q2(gewinn_termin), f"{kap_herkunft}.gewinn_termingeschaefte")
    add("Anlage KAP", "Z. 16/17", "Sparer-Pauschbetrag (Antrag)", sparer_pb,
        "steuerlib.sparer_pauschbetrag")
    if verluste_ohne_aktien > 0:
        add_kap("22", "Z. 22", "Verluste aus Kapitalvermögen ohne Verluste aus "
                "Aktienveräußerungen (unbeschränkt verrechenbar; als positiven Betrag eintragen)",
                q2(verluste_ohne_aktien), f"{kap_herkunft}.verluste_ohne_aktien")
    if verlust_aktien > 0:
        add_kap("23", "Z. 23", "Verluste aus Aktienveräußerungen (§ 20 Abs. 6 Satz 4; als "
                "positiven Betrag eintragen)",
                q2(verlust_aktien), f"{kap_herkunft}.verlust_aktien")
    if verlust_termin > 0:
        add_kap("24", "Z. 24", "Verluste aus Termingeschäften (§ 20 Abs. 6 Satz 5 a. F. — "
                "durch JStG 2024 aufgehoben, unbeschränkt verrechenbar; als positiven Betrag "
                "eintragen)",
                q2(verlust_termin), f"{kap_herkunft}.verlust_termingeschaefte")
    if verluste_ausfall > 0:
        add_kap("25", "Z. 25", "Verluste aus wertlosem Ausfall / Ausbuchung von "
                "Kapitalanlagen (§ 20 Abs. 6 Satz 6 a. F. — durch JStG 2024 aufgehoben, "
                "unbeschränkt verrechenbar; als positiven Betrag eintragen)",
                q2(verluste_ausfall), f"{kap_herkunft}.verluste_ausfall")
    # Festgestellte Verlustvorträge aus Vorjahren gehören NICHT in die Verlustzeilen
    # 22–25: die nehmen ausschließlich die Verluste DIESES Jahres auf. Der Vortrag
    # kommt aus dem Verlustfeststellungsbescheid und wird vom Finanzamt von Amts
    # wegen berücksichtigt. Diese Zeilen sind deshalb nachrichtlich (Zeile "—") —
    # wer sie in Z. 23 abtippt, erklärt den Altverlust ein zweites Mal.
    if vv_aktien_vorjahr > 0:
        add("Anlage KAP", "—",
            "nachrichtlich: festgestellter Aktien-Verlustvortrag aus Vorjahren "
            "(§ 20 Abs. 6 Satz 4 EStG, aus dem Feststellungsbescheid — nicht in "
            "Zeile 23 eintragen)",
            q2(vv_aktien_vorjahr), "anlage_kap.verlustvortrag_aktien_vorjahr")
        add("Anlage KAP", "—",
            "nachrichtlich: davon mit Aktiengewinnen dieses Jahres verrechnet",
            q2(vv_aktien_verbraucht), "anlage_kap.verlustvortrag_aktien_vorjahr")
        add("Anlage KAP", "—", "nachrichtlich: verbleibender Aktien-Verlustvortrag",
            q2(vv_aktien_rest), "anlage_kap.verlustvortrag_aktien_vorjahr")
    if vv_allg_vorjahr > 0 or vv_allg_verbraucht > 0 or vv_allg_rest > 0:
        add("Anlage KAP", "—",
            "nachrichtlich: festgestellter allgemeiner Verlustvortrag Kapitalvermögen "
            "aus Vorjahren (§ 20 Abs. 6 Satz 3 EStG, aus dem Feststellungsbescheid)",
            q2(vv_allg_vorjahr), "anlage_kap.verlustvortrag_allgemein_vorjahr")
        add("Anlage KAP", "—",
            "nachrichtlich: davon mit Kapitalerträgen dieses Jahres verrechnet",
            q2(vv_allg_verbraucht), "anlage_kap.verlustvortrag_allgemein_vorjahr")
        add("Anlage KAP", "—",
            "nachrichtlich: verbleibender allgemeiner Verlustvortrag",
            q2(vv_allg_rest), "anlage_kap.verlustvortrag_allgemein_vorjahr")
    if anrechenbare_kest != 0:
        add_kap("37", "Z. 37", "Anrechenbare Kapitalertragsteuer",
                q2(anrechenbare_kest), f"{kap_herkunft}.anrechenbare_kest")
    if kap_soli_einbehalten != 0:
        add("Anlage KAP", "Z. 38", "Einbehaltener Solidaritätszuschlag (Kapitalerträge)",
            q2(kap_soli_einbehalten), f"{kap_herkunft}.einbehaltener_soli")
    if kap_kist_einbehalten != 0:
        add("Anlage KAP", "Z. 39", "Einbehaltene Kirchensteuer (Kapitalerträge)",
            q2(kap_kist_einbehalten), f"{kap_herkunft}.einbehaltene_kirchensteuer")
    if auslaendische_quellensteuer is None:
        auslaendische_quellensteuer = _betrag(kap.get("auslaendische_quellensteuer"),
                                              "anlage_kap.auslaendische_quellensteuer")
    if auslaendische_quellensteuer != 0:
        add_kap("41", "Z. 41", "Anrechenbare ausländische Quellensteuer (§ 32d Abs. 5 EStG)",
                q2(auslaendische_quellensteuer),
                f"{kap_herkunft}.auslaendische_quellensteuer")
    if fiktive_quellensteuer != 0:
        add_kap("42", "Z. 42", "Fiktive ausländische Quellensteuer nach DBA "
                "(Anrechnung wie Z. 41, aber eigene Zeile)",
                q2(fiktive_quellensteuer), f"{kap_herkunft}.fiktive_quellensteuer")

    # Rohzeilen der Bescheinigungen: genau das, was in ELSTER eingetippt wird.
    #
    # Einzige Ausnahme vom „wörtlich durchreichen": die Verlustzeilen 22–25. Sie
    # nehmen den BETRAG des Verlustes auf, ELSTER erwartet dort eine positive Zahl.
    # Quellen drucken Verluste aber mal positiv (deutsche Steuerbescheinigung), mal
    # negativ (eToro: „−450,00"). Unverändert durchgereicht stünde im Mapping die
    # Anweisung, ein Minus in ein Betragsfeld zu tippen — je nachdem, ob ELSTER das
    # Zeichen verwirft oder den Verlust umdreht, kostet das den vollen
    # Verlustabzug. Die wörtliche Abschrift bleibt in anlagen.KAP.kap_zeilen stehen.
    for z in roh_zeilen:
        label = KAP_ZEILEN_LABEL.get(z["zeile"], "Betrag laut Bescheinigung")
        wert = z["wert"]
        if z["zeile"] in KAP_ZEILEN_VERLUST:
            wert = q2(abs(to_decimal(wert)))
            label = f"{label} (als positiven Betrag eintragen)"
        add("Anlage KAP", f"Z. {z['zeile']}",
            f"{label} — Rohzeile aus der Bescheinigung", wert,
            f"{z['quelle']} (kap_zeilen)")
    for row in kap_extra or []:
        add_extra(row, "Anlage KAP", "kap-quelle (elster_extra)")

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

    # Durchgereichte ELSTER-Zeilen aus Parsern (z. B. eToro-Summenausweis).
    # Ebenfalls entdoppelt: dieselbe Datei kann beiden Lesern übergeben worden sein,
    # dann liefert sie ihre elster_extra-Zeilen zweimal.
    for row in krypto.get("elster_extra", []) or []:
        add_extra(row, "—", "krypto-quelle (elster_extra)")
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
    ap.add_argument("--kap-result", nargs="+", metavar="DATEI",
                    help="ein oder mehrere KAP-Ergebnisse (Steuerbescheinigung / "
                         "Erträgnisaufstellung / Auslandsbroker, Schema 'kap' aus "
                         "references/broker-profile.md). Mehrere Quellen werden addiert; "
                         "Werte aus 'anlage_kap' kommen hinzu, sie werden nicht ersetzt.")
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
        kap_quellen = lade_kap_quellen(args.kap_result)
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
        report = build(sd, quellen, kap_quellen)
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
    kap_quellen_meta = [q for q in report["meta"].get("kap_quellen", [])
                        if q.get("art") == "datei"]
    if kap_quellen_meta:
        print(f"  KAP-Quellen ({len(kap_quellen_meta)}): "
              + ", ".join(q["quelle"] for q in kap_quellen_meta))
        print(f"  Kapitalerträge gesamt (Dateien + anlage_kap): "
              f"{report['anlagen']['KAP']['kapitalertraege']} €")
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
