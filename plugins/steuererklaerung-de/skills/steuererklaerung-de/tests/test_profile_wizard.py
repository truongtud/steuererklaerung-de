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
