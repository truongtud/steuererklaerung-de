#!/usr/bin/env python3
"""Tests für scripts/profile_wizard.py. Ausführen: python3 tests/run_tests.py

Getestet wird gegen synthetischen Report*text* — der Schritt Text -> Entwurf ist
bewusst eine eigene Funktion (`entwurf_aus_text`), damit kein echtes PDF und kein
PDF-Backend nötig ist.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import profile_wizard as pw  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: erwartet {want!r}, bekommen {got!r}"


def wahr(bedingung, label=""):
    assert bedingung, label


# ── synthetische Reports ─────────────────────────────────────────────────────

VERAEUSSERUNGEN = """Musterbroker AG
Steuerbericht 2024 — Kapitalgewinne
Kontoinhaber: Max Mustermann
Kontonummer: 4711-0815
Steuer-ID: 12 345 678 901
IBAN: DE89 3704 0044 0532 0130 00

Verkaufsdatum   Erwerbsdatum   Asset   Menge   Erlös   Kostenbasis   Gewinn   Haltedauer
02.03.2024      15.01.2023     BTC     0,50    12.000,00   9.000,00   3.000,00   Langfristig
14.05.2024      20.11.2023     ETH     2,00    4.000,00    3.500,00     500,00   Kurzfristig
30.06.2024      01.02.2024     SOL     10,00   1.500,00    1.200,00     300,00   Kurzfristig
05.08.2024      02.02.2024     ADA     100,00  900,00      800,00       100,00   Kurzfristig
Seite 1 von 1
19.09.2024      02.02.2024     DOT     50,00   700,00      600,00       100,00   Kurzfristig

Zusammenfassung
Kapitalgewinne   4.000,00
"""

# Wie oben, aber mit einer Spalte, die keinem kanonischen Feld entspricht, und
# einer Summe, die zu keiner Spaltensumme passt.
UNKLAR = """Beispiel Bank AG
Kapitalgewinne 2024

Verkaufsdatum Erwerbsdatum Tranche Menge Gewinn
02.03.2024 15.01.2023 X-1 0,50 3.000,00
14.05.2024 20.11.2023 X-2 2,00 500,00
30.06.2024 01.02.2024 X-3 10,00 300,00

Zusammenfassung
Kapitalgewinne 9.999,00
"""

KAP = """Musterbank AG
Steuerbescheinigung für das Kalenderjahr 2024

Höhe der Kapitalerträge (Anlage KAP, Zeile 7) 1.234,56
Verluste aus Aktienveräußerungen (Anlage KAP, Zeile 23) 500,00
Kapitalertragsteuer 300,00
Solidaritätszuschlag 16,50
"""

# Derselbe Berichtstyp mit den beiden Fallen, an denen der Wizard sich früher selbst
# betrogen hat: einer Anrede mit Klarnamen (so druckt eToro den Namen) und einer
# "davon"-Zeile, die eine FREMDE Zeilennummer nennt. 400 € Aktienverlust gegen
# 5.000 € Kapitalerträge zu halten und das "ok" zu nennen war der Kern des Fehlers.
KAP_ZIRKULAER = """Musterbank AG
Steuerbescheinigung für das Kalenderjahr 2024
Guten Tag Max Mustermann,
Depot 12345678

Höhe der Kapitalerträge (Anlage KAP, Zeile 7) 5.000,00
In Zeile 7 enthaltene Verluste aus Aktienveräußerungen 400,00
Verluste aus Aktienveräußerungen (Anlage KAP, Zeile 23) 500,00
Kapitalertragsteuer 300,00
Solidaritätszuschlag 16,50
"""

CSV_TEXT = """Datum;Typ;Währung;Menge;Wert in EUR;Gebühr;Notiz
2024-03-02;Kauf;BTC;0.5;12000.00;1.50;
2024-05-14;Verkauf;ETH;2.0;4000.00;0.90;
2024-06-30;Zauberei;SOL;10.0;1500.00;0.10;
2024-07-01;Kauf;BTC;1.0;2000.00;0.10;
"""

# Alle Datumsangaben sind in beiden Lesarten gültig (Tag und Monat <= 12).
MEHRDEUTIG = """Sample Exchange Ltd
Capital Gains Report

Date sold   Date acquired   Asset   Quantity   Proceeds   Cost basis   Gain
03/02/2024  01/05/2023      BTC     0.50       12,000.00  9,000.00     3,000.00
05/11/2024  02/06/2023      ETH     2.00       4,000.00   3,500.00     500.00
06/07/2024  01/02/2024      SOL     10.00      1,500.00   1,200.00     300.00

Summary
Total capital gains   3,800.00
"""


def _entwurf(text=VERAEUSSERUNGEN, pid="musterbroker-de", **kw):
    return pw.entwurf_aus_text(text, pid, **kw)


# ── 1. Grundvorschlag ────────────────────────────────────────────────────────
@case
def test_entwurf_erkennt_veraeusserungsbericht():
    e = _entwurf()
    p = e["profil"]
    eq(p["id"], "musterbroker-de", "id")
    eq(p["ergebnis"], "krypto_vorberechnet", "Ausgabeschema")
    eq(p["eingabe"], "pdf", "Eingabeart")
    eq(p["status"], "ungeprueft", "Ein Entwurf ist nie 'geprueft'")
    eq(p["notation"], "de", "Zahlennotation")
    eq(p["datum"], "de", "Datumsformat (Tag > 12 vorhanden)")
    wahr(p["erkennung"]["muss"], "erkennung.muss darf nicht leer sein")
    for muster in p["erkennung"]["muss"]:
        wahr(re.search(pw.entfalte(muster), VERAEUSSERUNGEN),
             f"Erkennungsmuster {muster!r} greift auf dem eigenen Report nicht")
    eq(len(p["tabellen"]), 1, "genau eine Tabelle")
    eq(p["tabellen"][0]["name"], "veraeusserungen", "Tabellenname")


@case
def test_zeilenregex_ist_plausibel():
    p = _entwurf()["profil"]
    tab = p["tabellen"][0]
    rx = re.compile(pw.entfalte(tab["zeile"]))
    treffer = [z for z in VERAEUSSERUNGEN.split("\n") if rx.match(z.strip())]
    eq(len(treffer), 5, "alle fünf Datenzeilen müssen matchen")
    wahr(not rx.match("Seite 1 von 1"), "Seitenfuß darf NICHT als Datenzeile matchen")
    wahr(not rx.match("Kapitalgewinne   4.000,00"),
         "Summenzeile darf NICHT als Datenzeile matchen")
    m = rx.match("02.03.2024      15.01.2023     BTC     0,50    12.000,00   "
                 "9.000,00   3.000,00   Langfristig")
    gd = m.groupdict()
    felder = tab["felder"]
    eq(gd[felder["disposal_date"]], "02.03.2024", "Verkaufsdatum")
    eq(gd[felder["acquisition_date"]], "15.01.2023", "Erwerbsdatum")
    eq(gd[felder["gain_eur"]].strip(), "3.000,00", "Gewinn")
    # Anker: start/ende müssen im Report vorkommen.
    for schluessel in ("start", "ende"):
        wahr(re.search(pw.entfalte(tab[schluessel]), VERAEUSSERUNGEN),
             f"{schluessel}-Anker {tab[schluessel]!r} kommt im Report nicht vor")


@case
def test_spalten_werden_kanonisch_zugeordnet():
    felder = _entwurf()["profil"]["tabellen"][0]["felder"]
    for kanon in ("disposal_date", "acquisition_date", "asset", "amount",
                  "proceeds_eur", "cost_basis_eur", "gain_eur"):
        wahr(kanon in felder, f"{kanon} nicht zugeordnet: {felder}")
    eq(felder["gain_eur"], "gewinn", "Gewinnspalte")
    # Pflichtfelder des Schemas müssen zugeordnet sein.
    for pf in _entwurf()["profil"]["tabellen"][0]["pflicht"]:
        wahr(pf in felder, f"Pflichtfeld {pf!r} fehlt in felder")


# ── 2. Summen und Selbstprüfung ──────────────────────────────────────────────
@case
def test_summenmuster_greift_im_report():
    p = _entwurf()["profil"]
    wahr(p["summen"], "ohne summen ist ein Profil unfertig")
    s = p["summen"][0]
    eq(s["label"], "Kapitalgewinne", "Summenlabel")
    m = re.search(pw.entfalte(s["muster"]), VERAEUSSERUNGEN, re.I | re.M)
    wahr(m, f"Summenmuster {s['muster']!r} findet nichts")
    eq(str(pw.sl.to_decimal(m.group(1), locale_hint="de")), "4000.00", "Summenwert")
    wahr(s["vergleich"] != pw.TODO, "abgeglichene Summe braucht einen Zielpfad")
    eq(s["toleranz"], "0.01", "Toleranz")


@case
def test_selbstpruefung_meldet_abgleich():
    b = _entwurf()["bericht"]
    eq(b["tabellen"][0]["gematcht"], 5, "getroffene Zeilen")
    eq(b["tabellen"][0]["ohne_treffer"], 1, "der Seitenfuß muss auffallen")
    wahr("Seite 1 von 1" in b["tabellen"][0]["beispiele"],
         "nicht zugeordnete Zeile muss im Bericht auftauchen")
    eq(b["tabellen"][0]["spaltensummen"]["gewinn"], "4000.00", "Spaltensumme")
    eq(b["summen"][0]["abgleich"], "ok", "Summe und Spaltensumme müssen passen")
    eq(b["summen"][0]["spalten"], ["veraeusserungen.gewinn"], "abgleichende Spalte")
    wahr(b["abgleich_ok"], "Abgleich muss als gelungen gemeldet werden")
    wahr(any("ohne Treffer" in w for w in b["warnungen"]),
         "nicht zugeordnete Zeilen müssen als Warnung erscheinen")


@case
def test_ohne_abgleich_bleibt_vergleich_todo():
    e = _entwurf(UNKLAR, "beispiel-de")
    b, p = e["bericht"], e["profil"]
    wahr(not b["abgleich_ok"], "9.999,00 passt zu keiner Spaltensumme")
    eq(p["summen"][0]["vergleich"], pw.TODO,
       "ohne belastbaren Abgleich darf kein Zielpfad behauptet werden")
    wahr(any("Spaltensumme" in w for w in b["warnungen"]),
         "fehlender Abgleich muss gemeldet werden")
    wahr("summen[0].vergleich" in p["kommentare"], "TODO braucht einen Kommentar")


@case
def test_ohne_summenzeile_wird_gewarnt():
    ohne = "\n".join(VERAEUSSERUNGEN.split("\n")[:-3])
    e = _entwurf(ohne, "ohne-summe")
    eq(e["profil"]["summen"][0]["muster"], pw.TODO, "Platzhalter statt erfundener Summe")
    wahr(any("Summenzeile" in w for w in e["bericht"]["warnungen"]),
         "fehlende Summe ist der wichtigste offene Punkt")


# ── 2b. Zirkuläre Abgleiche sind ein Fehler, kein "ok" ───────────────────────
@case
def test_zeilennummer_im_label_ist_keine_zeilenbezeichnung():
    # "In Zeile 7 enthaltene Verluste" handelt VON Zeile 7 und ist selbst eine
    # andere Zeile — die davon-Zeilen 20–25 tragen ihre eigene Nummer nie im Text.
    for label in ("In Zeile 7 enthaltene Verluste aus Aktienveräußerungen",
                  "In den Zeilen 18 und 19 enthaltene Gewinne aus Aktienveräußerungen",
                  "davon Zeile 7", "siehe Zeile 12"):
        pfad = pw._vergleichspfad(label, "kap")
        wahr(not str(pfad).startswith("kap_zeilen."),
             f"{label!r} ergibt fälschlich den Pfad {pfad!r}")
    # Die echte Zeilenbezeichnung muss weiter funktionieren.
    eq(pw._vergleichspfad("Höhe der Kapitalerträge (Anlage KAP, Zeile 7)", "kap"),
       "kap_zeilen.7", "echte Zeilenbezeichnung")
    eq(pw._vergleichspfad("Anlage KAP Zeile 19", "kap"), "kap_zeilen.19", "Zeile 19")


@case
def test_zirkulaerer_summenabgleich_wird_todo_statt_ok():
    e = _entwurf(KAP_ZIRKULAER, "musterbank-kap")
    p, b = e["profil"], e["bericht"]
    eq(p["ergebnis"], "kap", "KAP-Schema")
    wahr(b["summen"], "es müssen Summenkandidaten gefunden worden sein")
    # Kein einziger Eintrag darf als gelungener Abgleich durchgehen …
    for s in b["summen"]:
        wahr(not s["abgleich"].startswith("ok"),
             f"{s['label']!r} wird als {s['abgleich']!r} gemeldet, obwohl der "
             f"Vergleichswert aus derselben Zeile stammt")
    wahr(b["zirkulaer"], "die zirkulären Einträge müssen benannt werden")
    wahr(not b["abgleich_ok"],
         "ein zirkulärer Selbstvergleich ist kein belastbarer Abgleich")
    # … und keiner darf im Profil als fertiger Zielpfad landen.
    for s in p["summen"]:
        eq(s["vergleich"], pw.TODO, f"{s['label']!r} behauptet einen Zielpfad")
    todos = pw.finde_todos(p)
    wahr([t for t in todos if t.startswith("summen[")],
         f"die offenen Abgleiche müssen als TODO auftauchen: {todos}")
    wahr(todos != ["datum", "geprueft_am"],
         "ein Profil, dem nur noch das Datum fehlt, wird arglos übernommen")


@case
def test_zirkularitaet_wird_als_fehler_gemeldet_und_erklaert():
    b = _entwurf(KAP_ZIRKULAER, "musterbank-kap")["bericht"]
    warnung = " ".join(b["warnungen"])
    wahr("zirkul" in warnung.lower() or "ZIRKUL" in warnung,
         f"der Selbstvergleich muss in den Warnungen stehen: {b['warnungen']}")
    kommentare = _entwurf(KAP_ZIRKULAER, "musterbank-kap")["profil"]["kommentare"]
    erklaerungen = [v for k, v in kommentare.items()
                    if k.startswith("summen[") and k.endswith(".vergleich")]
    wahr(erklaerungen, "jedes zirkuläre TODO braucht eine Begründung")
    wahr(any("Aggregation" in v or "aggregat" in v.lower() for v in erklaerungen),
         f"der Kommentar muss sagen, was ein echter Abgleich ist: {erklaerungen[:1]}")


@case
def test_davon_zeile_wird_nicht_als_zeilenwert_gelesen():
    # 400 € Aktienverlust dürfen weder als Wert der Zeile 7 gelesen noch gegen die
    # 5.000 € Kapitalerträge gehalten werden.
    p = _entwurf(KAP_ZIRKULAER, "musterbank-kap")["profil"]
    davon = [w for w in p["werte"]
             if "Verluste" in w["muster"] and "Aktien" in w["muster"]]
    wahr(davon, f"die davon-Zeile fehlt ganz in `werte`: {p['werte']}")
    eq(davon[0]["pfad"], pw.TODO,
       "die davon-Zeile darf keinen geratenen kap_zeilen-Pfad bekommen")
    # Zeile 7 selbst bleibt der Zeile 7 vorbehalten.
    zeile7 = [w for w in p["werte"] if "kap_zeilen.7" in pw._liste_pfade(w)]
    eq(len(zeile7), 1, f"kap_zeilen.7 mehrfach oder gar nicht belegt: {p['werte']}")
    wahr(r"Zeile\s+7" in zeile7[0]["muster"], f"falsches Muster: {zeile7[0]}")


@case
def test_pruefe_entwurf_meldet_zirkulaer_als_fehler():
    # Direkt auf der Selbstprüfung: derselbe Wert, dieselbe Zeile.
    struktur = pw._struktur(["Kapitalertragsteuer 300,00"], None)
    werte = [{"pfad": "kennzahlen.anrechenbare_kest",
              "muster": "Kapitalertragsteuer{VOR}({NUM})", "_zeile": 0}]
    summen = [{"label": "Kapitalertragsteuer",
               "muster": "Kapitalertragsteuer{VOR}({NUM})",
               "vergleich": "kennzahlen.anrechenbare_kest", "toleranz": "0.01",
               "_wert": "300.00", "_zeile": 0}]
    b = pw.pruefe_entwurf(struktur, [], summen, "de", werte)
    eq(b["summen"][0]["zirkulaer"], True, "Selbstvergleich nicht erkannt")
    wahr(not b["summen"][0]["abgleich"].startswith("ok"),
         f"als {b['summen'][0]['abgleich']!r} gemeldet")
    wahr(not b["abgleich_ok"], "abgleich_ok darf davon nicht gesetzt werden")
    wahr(any("ZIRKUL" in w.upper() for w in b["warnungen"]),
         f"Warnung fehlt: {b['warnungen']}")


@case
def test_unabhaengiger_zweiter_ausweis_bleibt_ein_echter_abgleich():
    # Gegenprobe: steht der Gegenwert in einer ANDEREN Zeile, ist es ein echter
    # zweiter Ausweis — die Prüfung darf nicht pauschal alles verwerfen.
    struktur = pw._struktur(["Kapitalertragsteuer 300,00",
                             "Summe Kapitalertragsteuer 300,00"], None)
    werte = [{"pfad": "kennzahlen.anrechenbare_kest",
              "muster": "Kapitalertragsteuer{VOR}({NUM})", "_zeile": 0}]
    summen = [{"label": "Summe Kapitalertragsteuer",
               "muster": "Summe\\s+Kapitalertragsteuer{VOR}({NUM})",
               "vergleich": "kennzahlen.anrechenbare_kest", "toleranz": "0.01",
               "_wert": "300.00", "_zeile": 1}]
    b = pw.pruefe_entwurf(struktur, [], summen, "de", werte)
    eq(b["summen"][0]["zirkulaer"], False,
       "ein zweiter, unabhängiger Ausweis ist kein Selbstvergleich")


# ── 3. Unklares wird TODO, nicht geraten ─────────────────────────────────────
@case
def test_unklare_spalte_wird_todo_statt_falsch_zugeordnet():
    p = _entwurf(UNKLAR, "beispiel-de")["profil"]
    felder = p["tabellen"][0]["felder"]
    todo_keys = [k for k in felder if k.startswith(pw.TODO)]
    eq(len(todo_keys), 1, f"'Tranche' muss als TODO auftauchen: {felder}")
    eq(felder[todo_keys[0]], "tranche", "die Gruppe bleibt erhalten")
    for falsch in ("type", "asset", "holding", "note"):
        wahr(felder.get(falsch) != "tranche",
             f"'Tranche' wurde fälschlich auf {falsch} gemappt")
    wahr("tabellen[0].felder" in p["kommentare"],
         "offene Spalte braucht einen Kommentar")


@case
def test_schwacher_treffer_wird_nicht_uebernommen():
    # "Assetklasse" enthält "asset" — als reiner Teilstring-Treffer darf daraus
    # keine Zuordnung werden: eine Assetklasse ist kein Asset.
    text = UNKLAR.replace("Tranche", "Assetklasse")
    p = pw.entwurf_aus_text(text, "beispiel-de")["profil"]
    felder = p["tabellen"][0]["felder"]
    wahr("asset" not in felder, f"schwacher Treffer wurde übernommen: {felder}")
    wahr(any(k.startswith(pw.TODO) for k in felder), "Spalte muss TODO werden")
    kommentar = p["kommentare"].get("tabellen[0].felder", "")
    wahr("schwacher Treffer" in kommentar,
         f"der verworfene Kandidat gehört in den Kommentar: {kommentar!r}")
    wahr("asset" in kommentar, "der verworfene Kandidat muss benannt sein")


@case
def test_mehrdeutiges_datum_wird_todo():
    e = _entwurf(MEHRDEUTIG, "sample-en")
    p = e["profil"]
    eq(p["datum"], pw.TODO, "DD/MM und MM/DD beide plausibel")
    wahr("beiden Lesarten" in p["kommentare"]["datum"], "Begründung fehlt")
    eq(p["notation"], "en", "englische Notation")
    # Spaltenzuordnung muss trotzdem klappen (mehrwortige Kopfzellen).
    felder = p["tabellen"][0]["felder"]
    eq(felder.get("disposal_date"), "date_sold", f"Kopfzeile falsch geschnitten: {felder}")
    eq(felder.get("cost_basis_eur"), "cost_basis", "mehrwortige Kopfzelle")


@case
def test_todos_werden_gefunden_und_kommentiert():
    p = _entwurf(UNKLAR, "beispiel-de")["profil"]
    todos = pw.finde_todos(p)
    wahr("geprueft_am" in todos, "geprueft_am muss offen bleiben")
    wahr(any(t.startswith("tabellen[0].felder") for t in todos),
         f"offene Spalte fehlt in den TODOs: {todos}")
    # Jedes TODO braucht einen Kommentar (auf seiner Ebene oder der darüber).
    for t in todos:
        kommentar = (p["kommentare"].get(t)
                     or p["kommentare"].get(t.rsplit(".", 1)[0])
                     or p["kommentare"].get(re.sub(r"\[\d+\]$", "", t)))
        wahr(kommentar, f"TODO {t!r} ohne Kommentar")


# ── 4. Erkennung ohne personenbezogene Daten ─────────────────────────────────
@case
def test_erkennung_enthaelt_keine_personendaten():
    p = _entwurf()["profil"]
    alles = " ".join(p["erkennung"]["muss"] + p["erkennung"]["darf_nicht"])
    for verboten in ("Mustermann", "Max", "4711", "DE89", "12 345", "2024",
                     "02.03.2024"):
        wahr(verboten not in alles,
             f"{verboten!r} darf nicht in der Erkennung stehen: {alles!r}")
    wahr(any("Musterbroker" in m for m in p["erkennung"]["muss"]),
         f"die Marke ist das stabilste Merkmal: {p['erkennung']['muss']}")
    # Auch der Kommentar mit weiteren Kandidaten darf keine Namen vorschlagen.
    wahr("Mustermann" not in p["kommentare"].get("erkennung.darf_nicht", ""),
         "Namen dürfen auch nicht als Alternative vorgeschlagen werden")


# ── 5. Anonymisierung ────────────────────────────────────────────────────────
@case
def test_anonymisierung_entfernt_und_meldet():
    text = ("Musterbroker AG\n"
            "Kontoinhaber: Max Mustermann\n"
            "Kontonummer: DE-4711-0815\n"
            "Musterstraße 12\n"
            "10115 Berlin\n"
            "Steuer-ID: 12 345 678 901\n"
            "IBAN: DE89 3704 0044 0532 0130 00\n"
            "E-Mail: max.mustermann@example.com\n"
            "Anna Beispiel\n"
            "Zusammenfassung Kapitalgewinne\n")
    sauber, redaktionen = pw.anonymisiere(text, schuetze=("Musterbroker AG",))
    for weg in ("Max Mustermann", "DE-4711-0815", "Musterstraße 12", "10115 Berlin",
                "12 345 678 901", "DE89 3704 0044 0532 0130 00",
                "max.mustermann@example.com", "Anna Beispiel"):
        wahr(weg not in sauber, f"{weg!r} steht noch im Fixture")
    arten = {r["art"] for r in redaktionen}
    for art in ("name", "konto", "adresse", "steuer_id", "iban", "email"):
        wahr(art in arten, f"Redaktionsart {art!r} nicht gemeldet: {arten}")
    originale = {r["original"] for r in redaktionen}
    wahr("Max Mustermann" in originale, "Redaktion muss den Originalwert melden")
    wahr(all(r.get("zeile") for r in redaktionen), "Zeilennummer fehlt")
    # Was kein Personenbezug ist, bleibt stehen — sonst ist das Fixture wertlos.
    wahr("Musterbroker AG" in sauber, "geschützte Marke wurde geschwärzt")
    wahr("Zusammenfassung Kapitalgewinne" in sauber, "Fachbegriffe wurden geschwärzt")


# Jede Zeile hier ist ein Leck, das die Anonymisierung vorher unverändert
# durchgelassen hat. Geprüft wird das Verhalten (der Wert ist weg und die Redaktion
# ist gemeldet), nicht der Wortlaut des Ersatzes.
LECKS = [
    # (Zeile, was verschwinden muss, erwartete Redaktionsart)
    ("Guten Tag Max Mustermann,", "Max Mustermann", "name"),
    ("Depot 12345678", "12345678", "konto"),
    ("Account 987654321", "987654321", "konto"),
    ("Portfolio 4711556", "4711556", "konto"),
    ("MAX MUSTERMANN", "MAX MUSTERMANN", "name"),
    ("Dr. Anna Maria Schmidt", "Anna Maria Schmidt", "name"),
    ("IBAN: de89 3704 0044 0532 0130 00", "de89 3704 0044 0532 0130 00", "iban"),
    ("Inhaber: Max Mustermann", "Max Mustermann", "name"),
    ("Für Max Mustermann erstellt am 01.02.2025", "Max Mustermann", "name"),
    ("Empfänger Max Mustermann, Musterweg 3, 10115 Berlin", "Max Mustermann", "name"),
]


@case
def test_bekannte_lecks_werden_geschwaerzt():
    for zeile, geheim, art in LECKS:
        sauber, redaktionen = pw.anonymisiere(zeile)
        wahr(geheim not in sauber,
             f"{geheim!r} steht nach der Anonymisierung noch in {sauber!r}")
        wahr(redaktionen, f"{zeile!r}: Redaktion wurde nicht gemeldet")
        arten = {r["art"] for r in redaktionen}
        wahr(any(a.startswith(art) for a in arten),
             f"{zeile!r}: erwartete Redaktionsart {art!r}, gemeldet wurde {arten}")


@case
def test_empfaengerzeile_verliert_namen_nicht_nur_die_adresse():
    # Der gefährlichste Teiltreffer: die Adresse wird geschwärzt, der Name bleibt
    # stehen und das Fixture sieht trotzdem redigiert aus.
    zeile = "Empfänger Max Mustermann, Musterweg 3, 10115 Berlin"
    sauber, redaktionen = pw.anonymisiere(zeile)
    for weg in ("Max Mustermann", "Musterweg 3", "10115 Berlin"):
        wahr(weg not in sauber, f"{weg!r} überlebt in {sauber!r}")
    arten = {r["art"] for r in redaktionen}
    wahr({"adresse"} < arten, f"nur die Adresse wurde erkannt: {arten}")


@case
def test_anrede_mit_klarnamen_taucht_nirgends_im_ergebnis_auf():
    # `Guten Tag <Name>,` ist die eToro-Anrede — genau die Stelle, aus der
    # scripts/profiles/etoro-de.json den Namen des Steuerpflichtigen liest.
    e = _entwurf(KAP_ZIRKULAER, "musterbank-kap")
    roh = json.dumps(e, ensure_ascii=False, default=str)
    for geheim in ("Mustermann", "12345678"):
        wahr(geheim not in roh,
             f"{geheim!r} steht im Wizard-Ergebnis (Fixture, Profil oder Kommentar)")


@case
def test_versalien_und_titel_sind_keine_ausnahme():
    # Deutsche Auszüge drucken Adressköpfe regelmäßig in Versalien; ein Titel davor
    # hat die Zeile vorher ebenfalls durchrutschen lassen.
    for zeile in ("MAX MUSTERMANN", "Dr. Anna Maria Schmidt",
                  "Max Peter Klaus Mustermann", "KONTOINHABER: ERIKA MUSTERFRAU"):
        sauber, redaktionen = pw.anonymisiere(zeile)
        wahr(redaktionen, f"{zeile!r} wurde gar nicht als Personenangabe erkannt")
        wahr("[NAME]" in sauber, f"{zeile!r} -> {sauber!r}")


@case
def test_anonymisierung_ist_case_insensitiv():
    klein, _ = pw.anonymisiere("iban: de89 3704 0044 0532 0130 00")
    gross, _ = pw.anonymisiere("IBAN: DE89 3704 0044 0532 0130 00")
    wahr("de89" not in klein.lower(), f"kleingeschriebene IBAN überlebt: {klein!r}")
    wahr("3704" not in gross, f"großgeschriebene IBAN überlebt: {gross!r}")
    for zeile in ("kontoinhaber: Max Mustermann", "KONTOINHABER: Max Mustermann",
                  "Steuer-id: 12 345 678 901", "depotnummer: DE-4711-0815"):
        sauber, _ = pw.anonymisiere(zeile)
        wahr(sauber != zeile, f"Beschriftung nur in einer Schreibweise erkannt: {zeile!r}")


@case
def test_anonymisierung_frisst_keine_betraege_und_keine_kopfzeilen():
    # Gegenprobe zur schärferen Nummernregel: ein Betrag hinter "Konto:" ist keine
    # Kontonummer, und ohne diese Grenze wäre das Fixture um einen Betrag ärmer —
    # womit der Summenabgleich scheiterte, den das Fixture belegen soll.
    unveraendert = [
        "Konto: 1.234,56",
        "Kontostand 1.234,56",
        "Depotauszug 2024",
        "Sehr geehrte Damen und Herren",
        "Name Betrag",
        "Höhe der Kapitalerträge (Anlage KAP, Zeile 7) 1.234,56",
        "Kapitalgewinne   4.000,00",
        "02.03.2024      15.01.2023     BTC     0,50    12.000,00   9.000,00",
    ]
    for zeile in unveraendert:
        sauber, redaktionen = pw.anonymisiere(zeile)
        eq(sauber, zeile, f"fälschlich geschwärzt (Redaktionen: {redaktionen})")


@case
def test_restrisiko_meldet_was_stehen_geblieben_ist():
    # Nach der Redaktion bleibt Personenbezug übrig, den keine Regel kennt. Das
    # muss der Wizard sagen, statt Vollständigkeit zu behaupten.
    text = ("Musterbroker AG\n"
            "Bearbeitet von Anna Beispielfrau\n"
            "Order 998877665544\n"
            "Auftrag AB12CD34XY\n"
            "Kapitalgewinne 4.000,00\n")
    sauber, _ = pw.anonymisiere(text, schuetze=("Musterbroker AG",))
    funde = pw.restrisiko(sauber, schuetze=("Musterbroker AG",))
    texte = " | ".join(f["text"] for f in funde)
    wahr("Anna Beispielfrau" in texte,
         f"stehengebliebener Name wird nicht gemeldet: {texte!r}")
    wahr(any("998877665544" in f["text"] for f in funde),
         f"lange Ziffernfolge wird nicht gemeldet: {texte!r}")
    wahr(all(f.get("zeile") and f.get("hinweis") for f in funde),
         "jeder Fund braucht Zeile und Hinweis, sonst ist er nicht nachprüfbar")


@case
def test_restrisiko_schweigt_ueber_fach_und_markenvokabular():
    # Ein Melder, der bei jedem Tabellenkopf anschlägt, wird überlesen.
    text = ("Musterbroker AG\n"
            "Verkaufsdatum   Erwerbsdatum   Asset   Menge   Gewinn\n"
            "Zusammenfassung Kapitalgewinne\n"
            "Kapitalgewinne   4.000,00\n")
    funde = pw.restrisiko(text, schuetze=("Musterbroker AG",))
    eq(funde, [], "Fachvokabular darf kein Restrisiko sein")


@case
def test_entwurf_liefert_restrisiken_und_die_laute_warnung():
    e = _entwurf()
    wahr("restrisiken" in e["fixture"],
         "der Entwurf muss die Restrisiko-Liste mitliefern")
    wahr(isinstance(e["fixture"]["restrisiken"], list), "Restrisiken als Liste")


@case
def test_kopfzeile_wird_nicht_faelschlich_geschwaerzt():
    kopf = ("Verkaufsdatum Erwerbsdatum Asset\n"
            "Kapitalgewinne Zusammenfassung\n"
            "Musterbroker Bank\n")
    sauber, _ = pw.anonymisiere(kopf, schuetze=("Musterbroker Bank",))
    eq(sauber.strip(), kopf.strip(), "Fachvokabular darf nicht als Name gelten")


# ── 6. Fixture ───────────────────────────────────────────────────────────────
@case
def test_fixture_ist_kurz_und_konsistent():
    e = _entwurf()
    fixture = e["fixture"]["text"]
    zeilen = [z for z in fixture.split("\n") if z.strip()]
    datenzeilen = [z for z in zeilen if re.match(r"^\d{2}\.\d{2}\.\d{4}", z.strip())]
    eq(len(datenzeilen), 3, "zwei bis drei Datenzeilen genügen")
    wahr(any("Verkaufsdatum" in z for z in zeilen), "Kopfzeile fehlt im Fixture")
    wahr(any("Kapitalgewinne" in z for z in zeilen), "Summenzeile fehlt im Fixture")
    # Die Summenzeile muss zu den übernommenen Zeilen passen (3.000+500+300).
    summenzeile = [z for z in zeilen if z.strip().startswith("Kapitalgewinne")][0]
    wert = pw.sl.to_decimal(re.findall(r"[\d.,]+", summenzeile)[-1], locale_hint="de")
    eq(str(wert), "3800.00", "Summenzeile wurde nicht nachgerechnet")
    wahr(any("nachgerechnet" in h for h in e["fixture"]["hinweise"]),
         "die Anpassung muss gemeldet werden")
    # Personenbezogenes darf gar nicht erst im Fixture landen.
    for weg in ("Mustermann", "4711", "DE89 3704", "12 345 678 901"):
        wahr(weg not in fixture, f"{weg!r} steht im Fixture")


@case
def test_fixture_wird_anonymisiert_und_gemeldet():
    # Personendaten mitten im Tabellenkopf: hier muss die Redaktion greifen.
    text = VERAEUSSERUNGEN.replace(
        "Verkaufsdatum   Erwerbsdatum",
        "Depotinhaber: Erika Musterfrau\nVerkaufsdatum   Erwerbsdatum")
    e = pw.entwurf_aus_text(text, "musterbroker-de")
    fixture = e["fixture"]
    # Die Zeile landet nur dann im Fixture, wenn sie zum Kontext gehört; falls ja,
    # darf der Name nicht mehr lesbar sein.
    wahr("Erika Musterfrau" not in fixture["text"], "Name im Fixture")
    sauber, redaktionen = pw.anonymisiere("Depotinhaber: Erika Musterfrau")
    eq(len(redaktionen), 1, "genau eine Redaktion")
    eq(redaktionen[0]["original"], "Erika Musterfrau", "gemeldeter Originalwert")
    eq(sauber, "Depotinhaber: [NAME]", "Ersetzung")


# ── 7. Schema und JSON ───────────────────────────────────────────────────────
@case
def test_entwurf_ist_gueltiges_json_im_dokumentierten_schema():
    p = _entwurf()["profil"]
    roh = json.dumps(p, indent=2, ensure_ascii=False)
    wieder = json.loads(roh)
    eq(wieder, p, "Profil muss verlustfrei durch JSON gehen")
    for feld in ("id", "label", "quelle", "eingabe", "ergebnis", "erkennung",
                 "notation", "datum", "tabellen", "werte", "summen", "elster",
                 "geprueft_am", "fixture"):
        wahr(feld in wieder, f"Feld {feld!r} fehlt (siehe references/broker-profile.md)")
    wahr(wieder["eingabe"] in ("pdf", "csv"), "eingabe")
    wahr(wieder["ergebnis"] in pw.ERGEBNIS_ARTEN + (pw.TODO,), "ergebnis")
    wahr(wieder["notation"] in ("de", "en", "auto"), "notation")
    wahr(wieder["datum"] in ("de", "en", "iso", "auto", pw.TODO), "datum")
    eq(wieder["fixture"], "tests/fixtures/musterbroker-de.txt", "Fixture-Pfad")
    for schluessel in ("muss", "darf_nicht", "punkte"):
        wahr(schluessel in wieder["erkennung"], f"erkennung.{schluessel} fehlt")
    for t in wieder["tabellen"]:
        for schluessel in ("name", "start", "ende", "zeile", "felder", "pflicht"):
            wahr(schluessel in t, f"Tabelle ohne {schluessel!r}")
        gruppen = set(re.compile(pw.entfalte(t["zeile"])).groupindex)
        for kanon, gruppe in t["felder"].items():
            wahr(gruppe in gruppen,
                 f"Feld {kanon!r} verweist auf die Gruppe {gruppe!r}, die es in "
                 f"'zeile' nicht gibt")
    for s in wieder["summen"]:
        for schluessel in ("label", "muster", "vergleich", "toleranz"):
            wahr(schluessel in s, f"summen-Eintrag ohne {schluessel!r}")


@case
def test_alle_regexe_kompilieren():
    for text, pid, kw in ((VERAEUSSERUNGEN, "a", {}), (UNKLAR, "b", {}),
                          (KAP, "c", {}), (MEHRDEUTIG, "d", {}),
                          (CSV_TEXT, "e", {"eingabe": "csv"})):
        p = pw.entwurf_aus_text(text, pid, **kw)["profil"]
        muster = list(p["erkennung"]["muss"]) + list(p["erkennung"]["darf_nicht"])
        for t in p["tabellen"]:
            muster += [t["start"], t["ende"], t["zeile"]]
        for s in p["summen"] + p["werte"]:
            muster.append(s["muster"])
        for m in muster:
            if m == pw.TODO:
                continue
            try:
                re.compile(pw.entfalte(m))
            except re.error as e:
                raise AssertionError(f"{pid}: Muster {m!r} kompiliert nicht ({e})")


# ── 8. Weitere Reportarten ───────────────────────────────────────────────────
@case
def test_kap_bescheinigung():
    e = _entwurf(KAP, "musterbank-kap")
    p = e["profil"]
    eq(p["ergebnis"], "kap", "KAP-Schema erkannt")
    pfade = [pf for w in p["werte"] for pf in pw._liste_pfade(w)]
    wahr("kap_zeilen.7" in pfade, f"Zeile 7 nicht erkannt: {pfade}")
    wahr("kennzahlen.anrechenbare_kest" in pfade, "Kapitalertragsteuer nicht erkannt")
    wahr(p.get("werte_regeln", {}).get("mindestens"),
         "ohne Mindestzahl ist ein Null-Ergebnis nicht von einem Null-Report zu "
         "unterscheiden")
    # Verlustzeilen brauchen den Vorzeichen-Hinweis.
    verlust = [k for k, v in p["kommentare"].items()
               if k.startswith("werte[") and "NEGATIVES" in v]
    wahr(verlust, f"Vorzeichen-Hinweis für Verluste fehlt: {p['kommentare']}")


@case
def test_csv_entwurf_nutzt_csv_block():
    e = _entwurf(CSV_TEXT, "beispiel-csv", eingabe="csv")
    p = e["profil"]
    eq(p["eingabe"], "csv", "Eingabeart")
    eq(p["ergebnis"], "krypto_transaktionen", "CSV wird als Transaktionsliste gelesen")
    eq(p["tabellen"], [], "für CSV beschreibt der csv-Block die Spalten")
    csv = p["csv"]
    eq(csv["trennzeichen"], ";", "Trennzeichen")
    eq(csv["spalten"]["timestamp"], "Datum", "Zeitspalte")
    eq(csv["spalten"]["amount"], "Menge", "Mengenspalte")
    eq(csv["spalten"]["eur_value"], "Wert in EUR", "Wertspalte")
    eq(csv["typ_werte"]["Kauf"], "buy", "Kauf -> buy")
    eq(csv["typ_werte"]["Verkauf"], "sell", "Verkauf -> sell")
    eq(csv["typ_werte"]["Zauberei"], pw.TODO, "unbekannter Typ darf nicht geraten werden")
    wahr("csv.typ_werte" in p["kommentare"], "offener Typ braucht einen Kommentar")


# ── 9. Robustheit ────────────────────────────────────────────────────────────
@case
def test_ohne_brokerprofile_laeuft_der_wizard_weiter():
    original = pw._lade_brokerprofile
    pw._lade_brokerprofile = lambda: None
    try:
        e = _entwurf()
        wahr(e["profil"]["tabellen"], "Entwurf muss auch ohne brokerprofile entstehen")
        meldungen = e["bericht"]["brokerprofile"]
        wahr(any("noch nicht verfügbar" in m for m in meldungen),
             f"die übersprungene Zusatzprüfung muss benannt werden: {meldungen}")
        wahr(e["bericht"]["abgleich_ok"], "eigener Abgleich läuft unabhängig davon")
    finally:
        pw._lade_brokerprofile = original


@case
def test_leerer_und_wirrer_text_stuerzt_nicht_ab():
    for text in ("", "\n\n\n", "kein Report, nur Prosa ohne Zahlen und Struktur."):
        e = pw.entwurf_aus_text(text, "leer")
        p = e["profil"]
        wahr(pw.finde_todos(p), "ein leerer Report muss lauter TODOs liefern")
        json.dumps(p)          # muss serialisierbar bleiben
        wahr(e["bericht"]["warnungen"], "und deutliche Warnungen enthalten")


@case
def test_gleiche_eingabe_gleicher_entwurf():
    a = json.dumps(_entwurf()["profil"], sort_keys=True)
    b = json.dumps(_entwurf()["profil"], sort_keys=True)
    eq(a, b, "der Vorschlag muss reproduzierbar sein")


if __name__ == "__main__":
    fails = []
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as e:
            fails.append((fn.__name__, e))
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(CASES) - len(fails)}/{len(CASES)} bestanden")
    sys.exit(1 if fails else 0)
