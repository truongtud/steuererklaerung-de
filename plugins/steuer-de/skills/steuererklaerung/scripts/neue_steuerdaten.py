#!/usr/bin/env python3
"""
neue_steuerdaten.py — eine passende steuerdaten.json zum Anfangen.

Der Einstieg in eine Steuererklärung scheitert selten am Rechnen, sondern daran,
dass man nicht weiß, **welche Papiere** man braucht und **welche Anlagen** einen
überhaupt betreffen. Dieses Skript beantwortet beides aus ein paar Angaben zur
Lebenssituation und legt eine Startdatei an, die nur die Blöcke enthält, die
wirklich gebraucht werden.

    python3 scripts/neue_steuerdaten.py --jahr 2024 --taetigkeit angestellt \\
            --kinder 1 --kapital --handwerker -o steuerdaten.json

Die erzeugte `steuerdaten.json` ist ein **Nebenprodukt**: sie merkt sich, welche
Anlagen den Nutzer betreffen. Ausgefüllt wird sie nicht von Hand, sondern von
`importiere_unterlagen.py` aus den Bescheinigungen. Sie enthält nur die
passenden Blöcke — ein leerer Block lädt zum Ausfüllen ein, wo nichts
auszufüllen ist, und eine mit Nullen gefüllte Anlage G sieht im Report aus wie
eine Angabe.

Das Skript rechnet nichts und geht nicht ins Netz — es schreibt eine Vorlage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import steuerlib as sl  # noqa: E402

TAETIGKEITEN = ("angestellt", "selbstaendig", "gewerbe", "vermietung", "rente")

# Welcher Block gehört zu welcher Angabe. Die Feldnamen stammen aus
# assets/steuerdaten_vorlage.json; build_taxreport.pruefe_unbekannte_felder
# meldet jede Abweichung, und tests/test_einstieg.py prüft genau das.
_LEER = "0.00"

BLOECKE = {
    "angestellt": ("anlage_n", {
        "bruttoarbeitslohn": _LEER, "lohnsteuer": _LEER, "soli": _LEER,
        "kirchensteuer": _LEER,
        # Kontoführungsgebühren stehen hier bewusst auf 0,00 wie alles andere:
        # siehe HINWEISE["angestellt"]. Ein vorausgefüllter Betrag wäre eine
        # Angabe, die der Nutzer nie gemacht hat.
        "werbungskosten": {"entfernungspauschale": _LEER, "arbeitsmittel": _LEER,
                           "fortbildung": _LEER, "kontofuehrung": _LEER}}),
    "selbstaendig": ("anlage_s", {"gewinn": _LEER}),
    "gewerbe": ("anlage_g", {"gewinn": _LEER}),
    "vermietung": ("anlage_v", {"einkuenfte": _LEER}),
}

UNTERLAGEN = {
    "angestellt": [
        "Lohnsteuerbescheinigung des Arbeitgebers — daraus die Nummern 3 "
        "(Bruttoarbeitslohn), 4 (Lohnsteuer), 5 (Solidaritätszuschlag), "
        "6 (Kirchensteuer) sowie 22a und 23a (Arbeitgeber- und Arbeitnehmeranteil "
        "zur Rentenversicherung)",
        "Belege für Werbungskosten: Fahrten zur Arbeit, Arbeitsmittel, Fortbildung, "
        "Bewerbungen, Umzug",
    ],
    "selbstaendig": ["Einnahmenüberschussrechnung oder Bilanz"],
    "gewerbe": ["Einnahmenüberschussrechnung oder Bilanz, Gewerbesteuerbescheid"],
    "vermietung": ["Mieteinnahmen, Nebenkostenabrechnungen, Darlehenszinsen, "
                   "Abschreibung des Gebäudes, Erhaltungsaufwand"],
    "rente": ["Rentenbezugsmitteilung oder Mitteilung des Rentenversicherungsträgers"],
}

# Pauschalen, die man geltend machen KANN, aber nur mit tatsächlichem Aufwand.
# Sie werden bewusst nicht in die Startdatei geschrieben, sondern hier genannt:
# eine vorausgefüllte Zahl wäre eine Angabe gegenüber dem Finanzamt, die der
# Nutzer nie gemacht hat — und niemand prüft eine Zahl nach, die schon dasteht.
# Statutarische Pauschbeträge (Arbeitnehmer-Pauschbetrag § 9a, Sparer-Pauschbetrag
# § 20 Abs. 9) gehören NICHT hierher: die setzt das Finanzamt von Amts wegen an,
# und build_taxreport.py rechnet sie deshalb selbst.
HINWEISE = {
    "angestellt": [
        "Kontoführungsgebühren: bis zu 16 € im Jahr erkennen die Finanzämter ohne "
        "Einzelnachweis als Werbungskosten an — aber nur, wenn tatsächlich Gebühren "
        "angefallen sind. Der Betrag steht deshalb auf 0,00 und wird NICHT "
        "vorausgefüllt. Wer welche gezahlt hat, trägt sie unter "
        "anlage_n.werbungskosten.kontofuehrung selbst ein.",
        "Arbeitnehmer-Pauschbetrag (1.230 €): den setzt das Finanzamt von selbst an, "
        "er gehört nicht in die Werbungskosten. Einzutragen sind nur die "
        "tatsächlichen Kosten — sie wirken erst, soweit sie darüber liegen.",
    ],
}


def hinweise(taetigkeiten, **_ignoriert) -> list:
    """Was man eintragen KANN, aber selbst entscheiden muss."""
    h = []
    for t in taetigkeiten:
        h += HINWEISE.get(t, [])
    return h


def anlagen(taetigkeiten, kapital=False, krypto=False, kinder=0, agb=False) -> list:
    """Welche ELSTER-Anlagen die Situation betrifft."""
    a = ["Hauptvordruck ESt 1 A"]
    if "angestellt" in taetigkeiten:
        a.append("Anlage N — nichtselbständige Arbeit")
    if "selbstaendig" in taetigkeiten:
        a.append("Anlage S — selbständige Arbeit")
    if "gewerbe" in taetigkeiten:
        a.append("Anlage G — Gewerbebetrieb")
    if "vermietung" in taetigkeiten:
        a.append("Anlage V — Vermietung und Verpachtung")
    if "rente" in taetigkeiten:
        a.append("Anlage R — Renten")
    if kapital:
        a.append("Anlage KAP — Kapitalerträge")
    if krypto:
        a.append("Anlage SO — sonstige Einkünfte (Krypto nach § 23, Staking nach § 22 Nr. 3)")
    if kinder:
        a.append("Anlage Kind — je Kind eine")
    a.append("Anlage Vorsorgeaufwand — praktisch immer")
    if agb:
        a.append("Anlage Außergewöhnliche Belastungen")
    return a


def unterlagen(taetigkeiten, kapital=False, krypto=False, kinder=0,
               handwerker=False, lohnersatz=False, agb=False) -> list:
    """Die Papiere, die man zusammensuchen muss, bevor man anfängt."""
    u = []
    for t in taetigkeiten:
        u += UNTERLAGEN.get(t, [])
    u.append("Beitragsbescheinigungen der Kranken- und Pflegeversicherung sowie "
             "weiterer Versicherungen (Haftpflicht, Unfall, Arbeitslosenversicherung)")
    if kapital:
        u.append("Steuerbescheinigung jeder Bank und jedes Depots — auch wenn nichts "
                 "einbehalten wurde; ohne sie fehlen Verlusttöpfe und Quellensteuer")
    if krypto:
        u.append("Vollständige Transaktionshistorie jeder Börse und Wallet, über ALLE "
                 "Jahre — FIFO braucht die Anschaffungen der Vorjahre, nicht nur das "
                 "Steuerjahr")
    if kinder:
        u.append("Kindergeldbescheid oder Nachweis des Kindergeldanspruchs, "
                 "Geburtsdaten der Kinder, ggf. Nachweise über Betreuungskosten")
    if handwerker:
        u.append("Handwerkerrechnungen und Nebenkostenabrechnung — begünstigt ist nur "
                 "der Lohnanteil, kein Material, und die Rechnung muss unbar bezahlt "
                 "sein (§ 35a)")
    if lohnersatz:
        u.append("Leistungsbescheinigung über Eltern-, Arbeitslosen-, Kranken- oder "
                 "Kurzarbeitergeld — steuerfrei, erhöht aber den Steuersatz (§ 32b)")
    if agb:
        u.append("Belege über Krankheits-, Pflege- oder Bestattungskosten; der Report "
                 "kürzt sie selbst um die zumutbare Belastung (§ 33 Abs. 3)")
    u.append("Steuer-Identifikationsnummer, und falls vorhanden die Steuernummer des "
             "letzten Bescheids")
    return u


def steuerdaten(*, jahr: int, taetigkeiten, kinder: int = 0, kapital: bool = False,
                krypto: bool = False, handwerker: bool = False,
                lohnersatz: bool = False, agb: bool = False,
                verheiratet: bool = False, kirchensteuersatz=None) -> dict:
    """Die Startdatei — nur die Blöcke, die zur Situation gehören."""
    sd: dict = {"steuerjahr": int(jahr), "zusammenveranlagung": bool(verheiratet)}
    tp: dict = {"name": "", "verheiratet": bool(verheiratet), "steuer_id": ""}
    if kirchensteuersatz not in (None, ""):
        tp["kirchensteuersatz"] = str(kirchensteuersatz)
    sd["steuerpflichtiger"] = tp

    for t in taetigkeiten:
        if t in BLOECKE:
            name, inhalt = BLOECKE[t]
            sd[name] = json.loads(json.dumps(inhalt))

    if kapital:
        sd["anlage_kap"] = {"kapitalertraege": _LEER, "anrechenbare_kest": _LEER,
                            "einbehaltener_soli": _LEER,
                            "einbehaltene_kirchensteuer": _LEER}
    if krypto:
        sd["anlage_so"] = {"sonstige_einkuenfte": _LEER, "verlustvortrag_23_vorjahr": _LEER}
        sd["krypto_transaktionen"] = []
    if kinder:
        sd["kinder"] = [{"name": "", "geburtsdatum": "JJJJ-MM-TT"} for _ in range(kinder)]

    # Gegliedert, sonst greift die Höchstbetragsberechnung nach § 10 Abs. 3/4 nicht.
    sd["vorsorge"] = {
        "basisversorgung": {"rentenversicherung": _LEER},
        "kranken_pflege_basis": {"krankenversicherung": _LEER, "pflegeversicherung": _LEER},
        "sonstige": {"arbeitslosenversicherung": _LEER, "haftpflicht": _LEER},
        "arbeitgeberanteil_steuerfrei": _LEER,
    }
    sd["sonderausgaben"] = {"spenden": _LEER, "kirchensteuer_gezahlt": _LEER}
    if lohnersatz:
        sd["lohnersatzleistungen"] = {"elterngeld": _LEER, "arbeitslosengeld": _LEER,
                                      "krankengeld": _LEER}
    if handwerker:
        sd["steuerermaessigungen"] = {"paragraph_35a": {
            "minijob_haushalt": _LEER, "haushaltsnahe_dienstleistungen": _LEER,
            "handwerkerleistungen": _LEER}}
    if agb:
        sd["aussergewoehnliche_belastungen"] = {"aufwendungen": _LEER}
    return sd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Startdatei und Unterlagen-Checkliste für eine Einkommensteuererklärung")
    ap.add_argument("--jahr", type=int, required=True, help="Steuerjahr, z. B. 2024")
    ap.add_argument("--taetigkeit", nargs="+", default=["angestellt"],
                    choices=TAETIGKEITEN, help="eine oder mehrere")
    ap.add_argument("--kinder", type=int, default=0)
    ap.add_argument("--verheiratet", action="store_true")
    ap.add_argument("--kirchensteuer", help="Satz in Prozent, z. B. 9 (leer = keine)")
    for flag, hilfe in (("kapital", "Kapitalerträge, Depots"), ("krypto", "Krypto"),
                        ("handwerker", "Handwerker/haushaltsnahe Leistungen (§ 35a)"),
                        ("lohnersatz", "Eltern-/Arbeitslosen-/Krankengeld (§ 32b)"),
                        ("agb", "außergewöhnliche Belastungen")):
        ap.add_argument(f"--{flag}", action="store_true", help=hilfe)
    ap.add_argument("-o", "--out", default="steuerdaten.json")
    args = ap.parse_args(argv)

    gemeinsam = dict(taetigkeiten=args.taetigkeit, kapital=args.kapital,
                     krypto=args.krypto, kinder=args.kinder, agb=args.agb)
    sd = steuerdaten(jahr=args.jahr, verheiratet=args.verheiratet,
                     kirchensteuersatz=args.kirchensteuer,
                     handwerker=args.handwerker, lohnersatz=args.lohnersatz,
                     **gemeinsam)

    print(f"Steuerjahr {args.jahr}\n")
    print("Diese Anlagen betreffen dich:")
    for a in anlagen(**gemeinsam):
        print(f"  · {a}")
    print("\nDiese Unterlagen brauchst du:")
    for u in unterlagen(handwerker=args.handwerker, lohnersatz=args.lohnersatz,
                        **gemeinsam):
        print(f"  · {u}")

    offene_hinweise = hinweise(**gemeinsam)
    if offene_hinweise:
        print("\nDas trägst du selbst ein, wenn es zutrifft (nichts davon wird "
              "vorausgefüllt):")
        for h in offene_hinweise:
            print(f"  · {h}")

    offen = dict(sl.offene_veranlagungszeitraeume())
    if args.jahr in offen:
        print(f"\nFrist: für {args.jahr} läuft die Festsetzungsfrist noch bis zum "
              f"{offen[args.jahr].strftime('%d.%m.%Y')} (§ 169 Abs. 2 Nr. 2 AO). Wer "
              f"nicht abgeben muss, kann bis dahin freiwillig abgeben.")
    elif args.jahr < date.today().year:
        print(f"\nAchtung: für {args.jahr} ist die vierjährige Festsetzungsfrist "
              f"abgelaufen — eine freiwillige Abgabe ist nicht mehr möglich.")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(sd, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\nNächster Schritt: alle Papiere aus der Liste in einen Ordner legen und "
          f"/steuererklaerung aufrufen. Mehr ist nicht zu tun.")
    print(f"({args.out} ist angelegt und merkt sich, welche Anlagen dich betreffen — "
          f"du füllst darin nichts aus, das erledigt /steuererklaerung aus deinen "
          f"Unterlagen.)")
    print("Die Datei enthält später echte Steuerdaten — sie gehört in kein "
          "Repository und in keine Cloud-Freigabe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
