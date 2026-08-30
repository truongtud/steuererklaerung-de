#!/usr/bin/env python3
"""
krypto_fifo.py — FIFO-Berechnung für Krypto nach deutschem Steuerrecht.

Bildet ab:
  * § 23 EStG  — Private Veräußerungsgeschäfte (Veräußerung, Tausch, Bezahlung)
                 Haltefrist 1 Jahr -> danach steuerfrei. FIFO je Asset.
                 Freigrenze (Summe aller § 23-Gewinne im Jahr) ausschließlich aus
                 steuerlib.FREIGRENZE_23 — hier steht bewusst kein Ersatzwert.
                 Ist das Jahr dort nicht hinterlegt, wird das rohe Netto mit
                 'freigrenze_angewendet': false und einer Warnung ausgewiesen.
                 Freigrenze, kein Freibetrag: bei Überschreiten ist der GESAMTE
                 Gewinn steuerpflichtig.
  * § 22 Nr. 3 EStG — Staking-/Lending-Erträge als sonstige Leistungen,
                 bewertet mit EUR-Marktwert bei Zufluss. Freigrenze 256 €.
                 Die erhaltenen Coins erhalten ein neues Anschaffungsdatum
                 (Zuflusszeitpunkt) und Anschaffungskosten = Marktwert bei Zufluss.

Eingabe ist die **vollständige Historie** — FIFO braucht die Anschaffungen der
Vorjahre. Ausgewiesen wird trotzdem nur das Steuerjahr: alle Kennzahlen sind auf
`steuerjahr` gefiltert, die vollständige Liste steht unter `alle_veraeusserungen`.

Grundregeln dieser Engine (siehe auch scripts/steuerlib.py):
  * Unlesbare Pflichtfelder werfen einen Fehler — sie werden nie still zu 0.
  * Verworfene Zeilen erscheinen in `warnungen`, nie stillschweigend.
  * Summen werden ungerundet aufaddiert; gerundet wird erst bei der Ausgabe.
  * Eine 0, die niemand angegeben hat, erreicht keine Steuerzahl:
      - `_needs_fmv` ohne nachgetragenen Wert -> Abbruch (der Marktwert FEHLT,
        er ist nicht 0),
      - Erlös bzw. Kostenbasis von genau 0 ohne Beleg -> Warnung in `warnungen`
        und `nullwert_ungeklaert: true` an der betroffenen Veräußerung.
    Eine tatsächlich nachgewiesene Null (wertloser Airdrop, Hard Fork) wird im
    Datensatz mit `"nullwert_bestaetigt": true` festgehalten und läuft dann still
    durch.

Hinweis: Per-Asset-FIFO ist die gängige Vereinfachung. Das BMF lässt
wallet-/depotbezogenes FIFO ebenfalls zu (BMF-Schreiben vom 10.05.2022 /
06.03.2025). Diese Engine rechnet per-Asset-FIFO; der Hinweis steht im Report.

KEINE Steuerberatung — Orientierungsrechnung. Endkontrolle durch Steuerberater.
"""

from __future__ import annotations

import json
import sys
from collections import deque, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import steuerlib as sl  # noqa: E402
from steuerlib import (  # noqa: E402
    FREIGRENZE_22_3,
    ParseError,
    haltefrist_erfuellt,
    parse_datetime,
    q2,
    to_decimal,
)

getcontext().prec = 40  # hohe Präzision für Krypto-Beträge

D = Decimal

BEKANNTE_TYPEN = {"buy", "sell", "swap", "reward", "deposit", "withdrawal"}
_STAUB = D("1E-30")  # Restmengen darunter gelten als aufgebraucht


# ─────────────────────────────────────────────────────────────────────────────
# Eingabe lesen — lieber ein klarer Fehler als eine stille 0
# ─────────────────────────────────────────────────────────────────────────────


def _bezeichner(t, idx: int) -> str:
    """Benennt den Datensatz so, dass man ihn im Quell-Export wiederfindet."""
    teile = [f"Datensatz #{idx + 1}"]
    if isinstance(t, dict):
        if t.get("type"):
            teile.append(f"Typ '{t.get('type')}'")
        if t.get("asset"):
            teile.append(f"Asset {t.get('asset')}")
        if t.get("tx_id"):
            teile.append(f"tx_id {t.get('tx_id')}")
        if t.get("timestamp"):
            teile.append(f"Zeit {t.get('timestamp')}")
    return " / ".join(teile)


def _pflichtzahl(wert, feld: str, bez: str) -> Decimal:
    """Pflichtfeld: fehlend oder unlesbar -> ParseError, der den Datensatz nennt."""
    try:
        return to_decimal(wert)
    except ParseError as e:
        raise ParseError(
            f"{bez}: Pflichtfeld '{feld}' nicht lesbar ({e}). "
            f"Bitte im Quell-Export korrigieren — eine stille 0 würde die "
            f"Steuerberechnung verfälschen."
        ) from None


def _optzahl(wert, feld: str, bez: str) -> Decimal:
    """Optionales Feld: fehlt -> 0; vorhanden, aber unlesbar -> Fehler."""
    if wert is None or (isinstance(wert, str) and not wert.strip()):
        return D("0")
    return _pflichtzahl(wert, feld, bez)


def _pflichtdatum(wert, bez: str) -> datetime:
    try:
        return parse_datetime(wert)
    except ParseError as e:
        raise ParseError(
            f"{bez}: Zeitstempel nicht lesbar ({e}). Ohne Datum sind weder "
            f"FIFO-Reihenfolge noch Haltefrist bestimmbar."
        ) from None


def _pflichttext(wert, feld: str, bez: str) -> str:
    s = "" if wert is None else str(wert).strip()
    if not s:
        raise ParseError(f"{bez}: Pflichtfeld '{feld}' fehlt.")
    return s


# Eine 0 im EUR-Wert ist nur dann eine Angabe, wenn jemand sie bewusst gemacht
# hat (Airdrop ohne Marktwert, Hard Fork, Schenkung mit dokumentierten AK 0).
# Diese Felder sind die ausdrückliche Bestätigung dafür.
_NULLWERT_FELDER = ("nullwert_bestaetigt", "nullwert_dokumentiert",
                    "_nullwert_bestaetigt", "_nullwert_dokumentiert")


def _nullwert_bestaetigt(t: dict) -> bool:
    return any(bool(t.get(f)) for f in _NULLWERT_FELDER)


def _pruefe_offener_marktwert(t: dict, eur: Decimal, bez: str) -> None:
    """`_needs_fmv` + kein Wert -> Abbruch. Der Marktwert fehlt, er ist nicht 0.

    parse_inputs.py / brokerprofile.py setzen `_needs_fmv`, wenn der Export für
    einen Vorgang keinen Euro-Wert liefert (Staking-Zufluss, Krypto-Tausch,
    Ein-/Auslieferung). Läuft eine solche Zeile ungefüllt in die Berechnung, wird
    aus der fehlenden Angabe ein Erlös von 0 € bzw. eine Kostenbasis von 0 € —
    und damit ein Gewinn, den es nie gab. Wurde der Wert nachgetragen (eur > 0),
    ist die Markierung erledigt und der Datensatz in Ordnung.
    """
    if not t.get("_needs_fmv") or eur > 0 or _nullwert_bestaetigt(t):
        return
    raise ParseError(
        f"{bez}: der EUR-Marktwert fehlt (Feld '_needs_fmv' gesetzt, eur_value="
        f"{t.get('eur_value')!r}). Der Wert wird NICHT als 0 angenommen — eine 0 "
        f"wäre hier ein erfundener Erlös bzw. eine erfundene Kostenbasis und "
        f"verfälscht § 23 und § 22 Nr. 3.\n"
        f"→ Historischen Kurs zum Zeitpunkt in 'eur_value' eintragen. Ist der Wert "
        f"tatsächlich 0 (z. B. wertloser Airdrop), das im Datensatz mit "
        f"\"nullwert_bestaetigt\": true festhalten.")


def _normalisieren(transactions, warnungen: list) -> list:
    """Prüft, vereinheitlicht und zerlegt die Rohtransaktionen.

    * Mengen/Werte/Gebühren werden mit abs() normalisiert — Börsen-Exporte führen
      die abgegebene Seite häufig negativ. Bleibt danach eine Menge <= 0, wird der
      Datensatz mit Warnung übersprungen (nie stillschweigend).
    * 'swap' wird in eine Sell-Leg (abgegebenes Asset) und eine Buy-Leg
      (erhaltenes Asset) zerlegt. Die Tauschgebühr wird als
      Anschaffungsnebenkosten des erhaltenen Assets aktiviert und deshalb bei
      der Sell-Leg NICHT nochmals als Werbungskosten abgezogen (keine
      Doppelerfassung).
    """
    if isinstance(transactions, dict) and "transactions" in transactions:
        transactions = transactions["transactions"]
    if transactions is None:
        transactions = []
    if not isinstance(transactions, (list, tuple)):
        raise ParseError("Transaktionsliste erwartet (Liste von Objekten).")

    records: list[dict] = []
    transfers = 0
    unbekannt: list[str] = []

    for idx, t in enumerate(transactions):
        bez = _bezeichner(t, idx)
        if not isinstance(t, dict):
            raise ParseError(f"{bez}: Objekt erwartet, gefunden {type(t).__name__}.")

        ttype = str(t.get("type") or "").strip().lower()
        if ttype not in BEKANNTE_TYPEN:
            unbekannt.append(bez)
            continue
        if ttype in ("deposit", "withdrawal"):
            # Transfer zwischen eigenen Wallets -> nicht steuerbar, kein Los-Effekt
            # (Annahme: Bestände werden assetweise geführt, nicht walletweise.)
            transfers += 1
            continue

        dt = _pflichtdatum(t.get("timestamp"), bez)
        asset = _pflichttext(t.get("asset"), "asset", bez)
        amount = abs(_pflichtzahl(t.get("amount"), "amount", bez))
        eur = abs(_pflichtzahl(t.get("eur_value"), "eur_value", bez))
        fee = abs(_optzahl(t.get("fee_eur"), "fee_eur", bez))
        _pruefe_offener_marktwert(t, eur, bez)
        null_bestaetigt = _nullwert_bestaetigt(t)

        if amount <= 0:
            warnungen.append(
                f"{bez}: Menge ist 0 (oder nach Normalisierung nicht positiv) — "
                f"Datensatz übersprungen. Quell-Export prüfen."
            )
            continue

        # Eine glatte 0 im EUR-Wert stammt fast immer aus einem Export ohne
        # Euro-Spalte, nicht aus einem Vorgang ohne Wert. Sie darf keine Steuerzahl
        # erreichen, ohne dass ein Signal davon im Ergebnis bleibt.
        nullwert = (eur == 0 and not null_bestaetigt)

        if ttype == "buy":
            # Anschaffungskosten = EUR-Wert + Gebühr
            if nullwert and eur + fee == 0:
                warnungen.append(
                    f"{bez}: Anschaffung mit Anschaffungskosten 0,00 € übernommen "
                    f"(eur_value={t.get('eur_value')!r}). Bei der späteren "
                    f"Veräußerung dieses Bestands wird der GESAMTE Erlös als Gewinn "
                    f"versteuert. Kaufpreis nachtragen oder — wenn er wirklich 0 war "
                    f"— mit \"nullwert_bestaetigt\": true festhalten.")
            records.append({"art": "buy", "dt": dt, "asset": asset,
                            "amount": amount, "kosten": eur + fee, "bez": bez,
                            "nullkosten_ungeklaert": bool(nullwert and eur + fee == 0)})

        elif ttype == "sell":
            if nullwert:
                warnungen.append(
                    f"{bez}: Veräußerung mit einem Erlös von genau 0,00 € "
                    f"(eur_value={t.get('eur_value')!r}). Die Anschaffungskosten "
                    f"laufen dadurch in voller Höhe als § 23-VERLUST ins Ergebnis. "
                    f"Erlös nachtragen oder — wenn er wirklich 0 war — mit "
                    f"\"nullwert_bestaetigt\": true festhalten.")
            records.append({"art": "sell", "dt": dt, "asset": asset,
                            "amount": amount, "erloes": eur, "gebuehr": fee,
                            "note": "Verkauf/Ausgabe", "bez": bez,
                            "nullwert_ungeklaert": bool(nullwert)})

        elif ttype == "swap":
            # Sell-Leg: Veräußerung des abgegebenen Coins zum EUR-Marktwert.
            # Gebühr = 0, weil sie unten in die Anschaffungskosten des erhaltenen
            # Assets wandert (sonst würde dieselbe Gebühr zweimal abgezogen).
            if nullwert:
                warnungen.append(
                    f"{bez}: Tausch ohne EUR-Marktwert (eur_value="
                    f"{t.get('eur_value')!r}). So gerechnet wäre der Tausch eine "
                    f"Veräußerung zum Erlös 0 € — ein Scheinverlust in Höhe der "
                    f"Anschaffungskosten — und das erhaltene Asset bekäme eine "
                    f"Kostenbasis von 0 €, sodass bei dessen Verkauf der volle Erlös "
                    f"als Gewinn erscheint. Marktwert zum Tauschzeitpunkt nachtragen.")
            records.append({"art": "sell", "dt": dt, "asset": asset,
                            "amount": amount, "erloes": eur, "gebuehr": D("0"),
                            "note": "Tausch (Gebühr in Anschaffungskosten des "
                                    "erhaltenen Assets aktiviert)", "bez": bez,
                            "nullwert_ungeklaert": bool(nullwert)})
            gegen_asset = str(t.get("counter_asset") or "").strip()
            try:
                gegen_menge = abs(_pflichtzahl(t.get("counter_amount"),
                                               "counter_amount", bez))
            except ParseError:
                gegen_menge = D("0")
            if not gegen_asset or gegen_menge <= 0:
                warnungen.append(
                    f"{bez}: Tausch ohne verwertbares 'counter_asset'/'counter_amount' — "
                    f"das erhaltene Asset wurde NICHT eingebucht. Bei dessen späterem "
                    f"Verkauf fehlt die Anschaffung (Cost Basis 0)."
                )
            else:
                records.append({"art": "buy", "dt": dt, "asset": gegen_asset,
                                "amount": gegen_menge, "kosten": eur + fee,
                                "bez": bez,
                                "nullkosten_ungeklaert": bool(nullwert and eur + fee == 0)})

        elif ttype == "reward":
            kind = str(t.get("reward_kind") or "staking").strip() or "staking"
            if nullwert:
                warnungen.append(
                    f"{bez}: Zufluss ({kind}) ohne EUR-Marktwert (eur_value="
                    f"{t.get('eur_value')!r}). Er geht mit 0,00 € in § 22 Nr. 3 ein "
                    f"und das zugeflossene Asset bekommt eine Kostenbasis von 0 €, "
                    f"sodass bei dessen Verkauf der volle Erlös als § 23-Gewinn "
                    f"erscheint. Marktwert bei Zufluss nachtragen.")
            records.append({"art": "reward", "dt": dt, "asset": asset,
                            "amount": amount, "eur": eur, "kind": kind, "bez": bez,
                            "nullkosten_ungeklaert": bool(nullwert)})

    if transfers:
        warnungen.append(
            f"{transfers} Transfer(s) vom Typ 'deposit'/'withdrawal' wurden ignoriert "
            f"(Eigenübertrag ist nicht steuerbar). Stammt ein Zugang aus einer nicht "
            f"erfassten Wallet, fehlt die Anschaffung und die Cost Basis wird 0 — "
            f"in diesem Fall die Anschaffungsdaten ergänzen."
        )
    if unbekannt:
        warnungen.append(
            "Unbekannter Transaktionstyp, nicht verarbeitet: " + "; ".join(unbekannt)
            + f". Erlaubt sind: {', '.join(sorted(BEKANNTE_TYPEN))}."
        )
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Datenstrukturen
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Lot:
    """Ein Anschaffungs-Los eines Assets (FIFO-Queue-Element)."""
    date: datetime
    amount: Decimal           # noch verfügbare Menge
    cost_per_unit: Decimal    # Anschaffungskosten pro Einheit in EUR (inkl. anteiliger Gebühr)
    # Kostenbasis 0, ohne dass sie jemand so angegeben hat (fehlender Marktwert):
    # der Verbrauch dieses Loses muss beim Verkauf gemeldet werden.
    nullkosten_ungeklaert: bool = False
    herkunft: str = ""        # Bezeichner des Datensatzes, aus dem das Los stammt


@dataclass
class Disposal:
    """Eine Teil-Veräußerung (ein Eintrag je verbrauchtem FIFO-Los).

    Beträge bleiben hier **ungerundet** — gerundet wird erst in `as_dict()`.
    Sonst summieren sich bei vielen Teil-Losen die Rundungsreste auf (1.000
    Mikro-Lose: echte 1.005,00 € wurden früher als 1.010,00 € ausgewiesen).
    """
    asset: str
    disposal_dt: datetime
    acquisition_dt: Optional[datetime]
    amount: Decimal
    proceeds: Decimal
    cost_basis: Decimal
    fee: Decimal
    gain: Decimal
    held_days: int
    holding_period_met: bool   # Jahresfrist abgelaufen -> steuerfrei
    note: str = ""
    # True, wenn Erlös oder Kostenbasis nur deshalb 0 sind, weil im Quell-Export
    # kein EUR-Wert stand. Die Kennzeichnung wandert mit in die Ausgabe, damit die
    # Zahl nicht als bestätigt gelesen wird.
    nullwert_ungeklaert: bool = False

    @property
    def taxable(self) -> bool:
        return not self.holding_period_met

    def as_dict(self, steuerjahr: int) -> dict:
        return {
            "asset": self.asset,
            "disposal_date": self.disposal_dt.date().isoformat(),
            "acquisition_date": (self.acquisition_dt.date().isoformat()
                                 if self.acquisition_dt else "UNBEKANNT"),
            "amount": str(self.amount),
            "proceeds_eur": str(q2(self.proceeds)),
            "cost_basis_eur": str(q2(self.cost_basis)),
            "fee_eur": str(q2(self.fee)),
            "gain_eur": str(q2(self.gain)),
            "held_days": self.held_days,
            "holding_period_met": bool(self.holding_period_met),
            "taxable": bool(self.taxable),
            "steuerjahr": self.disposal_dt.year,
            "im_steuerjahr": self.disposal_dt.year == steuerjahr,
            "note": self.note,
            "nullwert_ungeklaert": bool(self.nullwert_ungeklaert),
        }


@dataclass
class StakingIncome:
    asset: str
    dt: datetime
    amount: Decimal
    eur: Decimal
    kind: str   # 'staking' | 'lending' | ...

    def as_dict(self, steuerjahr: int) -> dict:
        return {
            "asset": self.asset,
            "date": self.dt.date().isoformat(),
            "amount": str(self.amount),
            "eur_value": str(q2(self.eur)),
            "kind": self.kind,
            "steuerjahr": self.dt.year,
            "im_steuerjahr": self.dt.year == steuerjahr,
        }


def _freigrenze_23(jahr: int, warnungen: list) -> Optional[Decimal]:
    """Freigrenze § 23 aus steuerlib — oder None, wenn das Jahr nicht hinterlegt ist.

    Hier steht bewusst KEIN Ersatzwert mehr: Steuerkonstanten gehören ausschließlich
    nach scripts/steuerlib.py, und eine hier hartkodierte Freigrenze wäre beim
    nächsten Gesetzgeberwechsel still falsch. Statt abzubrechen wird — wie
    build_taxreport.py bei einem nicht hinterlegten § 32a-Tarif — weitergerechnet
    und das ROHE Nettoergebnis mit 'freigrenze_angewendet': false ausgewiesen.
    build_taxreport.py behandelt eine solche Quelle wie jede andere Rohquelle und
    wendet die Freigrenze selbst an; der eigenständige Lauf bleibt auswertbar.
    """
    try:
        return sl.freigrenze_23(jahr)
    except KeyError as e:
        warnungen.append(
            f"Freigrenze § 23 für {jahr} ist in scripts/steuerlib.py nicht hinterlegt "
            f"({e.args[0]}). Es wurde KEINE Freigrenze angewendet: ausgewiesen wird das rohe "
            f"Nettoergebnis mit 'freigrenze_angewendet': false, "
            f"'steuerpflichtiger_betrag_eur' ist damit eher zu hoch. Bitte den Wert für "
            f"{jahr} prüfen und in scripts/steuerlib.py (FREIGRENZE_23) ergänzen."
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Berechnung
# ─────────────────────────────────────────────────────────────────────────────


def compute_crypto_tax(transactions, tax_year: int) -> dict:
    """
    transactions: Liste von Dicts mit Feldern:
      timestamp, type ('buy'|'sell'|'swap'|'reward'|'deposit'|'withdrawal'),
      asset, amount, eur_value, fee_eur, reward_kind, counter_asset, counter_amount
      -> **vollständige Historie**, nicht nur das Steuerjahr (FIFO braucht die
         Anschaffungen der Vorjahre).
    Rückgabe: dict mit Veräußerungen, Staking-Einkommen, Aggregaten (nur
      `tax_year`), Freigrenz-Prüfung und Warnungen.
    """
    tax_year = int(tax_year)
    warnungen: list[str] = []

    records = _normalisieren(transactions, warnungen)
    # nach Zeit sortieren — FIFO braucht chronologische Reihenfolge.
    # Stabile Sortierung: bei gleichem Zeitstempel bleibt die Eingabereihenfolge
    # (und damit Sell-Leg vor Buy-Leg eines Tauschs) erhalten.
    records.sort(key=lambda r: r["dt"])

    lots: dict[str, deque] = defaultdict(deque)
    disposals: list[Disposal] = []
    staking: list[StakingIncome] = []

    gemeldete_nulllose: set = set()

    def add_lot(asset, dt, amount, total_cost_eur, *, ungeklaert=False, herkunft=""):
        if amount <= 0:
            return
        lots[asset].append(Lot(date=dt, amount=amount,
                               cost_per_unit=total_cost_eur / amount,
                               nullkosten_ungeklaert=bool(ungeklaert and
                                                          total_cost_eur == 0),
                               herkunft=herkunft))

    def dispose(asset, dt, amount, proceeds_eur, fee_eur, note, bez,
                nullwert_ungeklaert=False):
        """Verbraucht FIFO-Lose und erzeugt Disposal-Einträge (ein Eintrag je Teil-Los)."""
        remaining = amount
        queue = lots[asset]
        if nullwert_ungeklaert:
            note = (note + " | " if note else "") + (
                "WARNUNG: Erlös 0,00 € — kein EUR-Wert im Quell-Export")
        while remaining > 0 and queue:
            lot = queue[0]
            take = min(lot.amount, remaining)
            frac = take / amount
            part_proceeds = proceeds_eur * frac
            part_fee = fee_eur * frac
            part_cost = lot.cost_per_unit * take
            met = haltefrist_erfuellt(lot.date, dt)
            los_note = note
            if lot.nullkosten_ungeklaert:
                los_note = (los_note + " | " if los_note else "") + (
                    "WARNUNG: Anschaffungskosten 0,00 € — im Quell-Export war für die "
                    "Anschaffung kein EUR-Wert angegeben")
                schluessel = (asset, lot.date, lot.herkunft)
                if schluessel not in gemeldete_nulllose:
                    gemeldete_nulllose.add(schluessel)
                    warnungen.append(
                        f"{bez}: die Veräußerung am {dt.date().isoformat()} verbraucht "
                        f"ein {asset}-Los vom {lot.date.date().isoformat()} mit einer "
                        f"Kostenbasis von genau 0,00 € — diese Null stammt aus einem "
                        f"Zugang ohne EUR-Wert ({lot.herkunft or 'Herkunft unbekannt'}), "
                        f"nicht aus einer Angabe. Der ausgewiesene Gewinn ist damit zu "
                        f"hoch. Anschaffungskosten nachtragen oder die Null mit "
                        f"\"nullwert_bestaetigt\": true belegen.")
            disposals.append(Disposal(
                asset=asset,
                disposal_dt=dt,
                acquisition_dt=lot.date,
                amount=take,
                proceeds=part_proceeds,
                cost_basis=part_cost,
                fee=part_fee,
                gain=part_proceeds - part_cost - part_fee,
                held_days=(dt.date() - lot.date.date()).days,
                holding_period_met=met,
                note=los_note,
                nullwert_ungeklaert=bool(nullwert_ungeklaert
                                         or lot.nullkosten_ungeklaert),
            ))
            lot.amount -= take
            remaining -= take
            if lot.amount <= _STAUB:
                queue.popleft()

        if remaining > 0:
            # Mehr veräußert als angeschafft bekannt -> Cost Basis 0 (fehlende Historie!)
            frac = remaining / amount
            part_proceeds = proceeds_eur * frac
            part_fee = fee_eur * frac   # Gebühr anteilig mitnehmen, nicht verlieren
            disposals.append(Disposal(
                asset=asset, disposal_dt=dt, acquisition_dt=None,
                amount=remaining, proceeds=part_proceeds, cost_basis=D("0"),
                fee=part_fee, gain=part_proceeds - part_fee,
                held_days=-1, holding_period_met=False,
                note="WARNUNG: keine Anschaffungshistorie gefunden, Cost Basis = 0",
                nullwert_ungeklaert=True,
            ))
            warnungen.append(
                f"{bez}: für {remaining} {asset} (Veräußerung am "
                f"{dt.date().isoformat()}) fehlt die Anschaffungshistorie — "
                f"Cost Basis 0 angesetzt, der ausgewiesene Gewinn ist damit zu hoch. "
                f"Anschaffung nachtragen."
            )

    for r in records:
        if r["art"] == "buy":
            add_lot(r["asset"], r["dt"], r["amount"], r["kosten"],
                    ungeklaert=r.get("nullkosten_ungeklaert"), herkunft=r["bez"])
        elif r["art"] == "sell":
            dispose(r["asset"], r["dt"], r["amount"], r["erloes"], r["gebuehr"],
                    r["note"], r["bez"],
                    nullwert_ungeklaert=bool(r.get("nullwert_ungeklaert")))
        elif r["art"] == "reward":
            # § 22 Nr. 3: Einkommen bei Zufluss; neues Los mit Cost Basis = Marktwert
            staking.append(StakingIncome(asset=r["asset"], dt=r["dt"],
                                         amount=r["amount"], eur=r["eur"],
                                         kind=r["kind"]))
            add_lot(r["asset"], r["dt"], r["amount"], r["eur"],
                    ungeklaert=r.get("nullkosten_ungeklaert"), herkunft=r["bez"])

    # ---- Aggregation § 23 — ausschließlich Veräußerungen des Steuerjahres ----
    jahr_disposals = [d for d in disposals if d.disposal_dt.year == tax_year]
    pflichtige = [d for d in jahr_disposals if d.taxable]
    freie = [d for d in jahr_disposals if not d.taxable]

    # ungerundet aufaddieren, erst am Ende runden
    gewinne = sum((d.gain for d in pflichtige if d.gain > 0), D("0"))
    verluste = sum((d.gain for d in pflichtige if d.gain < 0), D("0"))
    netto_23 = gewinne + verluste   # Verluste nur mit § 23-Gewinnen verrechenbar
    steuerfrei = sum((d.gain for d in freie), D("0"))

    fg23 = _freigrenze_23(tax_year, warnungen)
    if fg23 is None:
        # Ohne hinterlegte Freigrenze wird sie nicht geraten, sondern übersprungen.
        freigrenze_angewendet = False
        freigrenze_ueberschritten = None
        steuerpflichtiger_betrag_23 = netto_23 if netto_23 > 0 else D("0")
    else:
        freigrenze_angewendet = True
        freigrenze_ueberschritten = netto_23 >= fg23
        steuerpflichtiger_betrag_23 = (netto_23 if (netto_23 > 0 and freigrenze_ueberschritten)
                                       else D("0"))
    verlustvortrag_23 = -netto_23 if netto_23 < 0 else D("0")

    # ---- Aggregation § 22 Nr. 3 (Staking/Lending) — ebenfalls nur Steuerjahr ----
    jahr_staking = [s for s in staking if s.dt.year == tax_year]
    summe_staking = sum((s.eur for s in jahr_staking), D("0"))
    staking_ueberschritten = summe_staking >= FREIGRENZE_22_3
    staking_steuerpflichtig = summe_staking if staking_ueberschritten else D("0")

    if verlustvortrag_23 > 0:
        warnungen.append(
            f"§ 23-Verlust {sl.fmt_eur(verlustvortrag_23)} im Jahr {tax_year}: "
            f"gesonderte Verlustfeststellung beantragen, sonst verfällt der Vortrag."
        )

    jahr_rows = [d.as_dict(tax_year) for d in jahr_disposals]

    paragraph_22 = {
        "freigrenze_eur": str(FREIGRENZE_22_3),
        "freigrenze_angewendet": True,
        "freigrenze_ueberschritten": bool(staking_ueberschritten),
        "summe_eur": str(q2(summe_staking)),
        "summe_zufluesse_eur": str(q2(summe_staking)),
        "steuerpflichtig_eur": str(q2(staking_steuerpflichtig)),
        "ertraege": [s.as_dict(tax_year) for s in jahr_staking],
        "alle_ertraege": [s.as_dict(tax_year) for s in staking],
    }

    result = {
        "steuerjahr": tax_year,
        "tax_year": tax_year,   # Altname, für bestehende Konsumenten
        "quelle": "krypto_fifo.py — FIFO-Neuberechnung aus der Transaktionshistorie",
        "methode": "FIFO je Asset (per-asset). BMF lässt auch wallet-bezogenes FIFO zu.",
        "paragraph_23": {
            "freigrenze_eur": (None if fg23 is None else str(fg23)),
            "freigrenze_angewendet": freigrenze_angewendet,
            "freigrenze_ueberschritten": (None if freigrenze_ueberschritten is None
                                          else bool(freigrenze_ueberschritten)),
            "anzahl_veraeusserungen": len(jahr_disposals),
            "gewinn_eur": str(q2(gewinne)),
            "verlust_eur": str(q2(verluste)),
            "summe_steuerpflichtige_gewinne_eur": str(q2(gewinne)),
            "summe_verluste_eur": str(q2(verluste)),
            "netto_ergebnis_eur": str(q2(netto_23)),
            "steuerfrei_langfristig_eur": str(q2(steuerfrei)),
            "summe_steuerfrei_gt_1_jahr_eur": str(q2(steuerfrei)),
            "steuerpflichtiger_betrag_eur": str(q2(steuerpflichtiger_betrag_23)),
            "verlustvortrag_eur": str(q2(verlustvortrag_23)),
            "veraeusserungen": jahr_rows,
            "disposals": jahr_rows,   # Altname, gleiche Liste
        },
        "paragraph_22_nr_3": paragraph_22,
        "paragraph_22_nr3": paragraph_22,   # Schreibweise des Ausgabe-Kontrakts
        "steuerfrei_langfristig_eur": str(q2(steuerfrei)),
        "warnungen": warnungen,
        "elster_extra": [],
        "alle_veraeusserungen": [d.as_dict(tax_year) for d in disposals],
        "offene_bestaende": {
            asset: str(sum((l.amount for l in q), D("0")))
            for asset, q in lots.items() if sum((l.amount for l in q), D("0")) > 0
        },
        "hinweise": [
            "Alle Kennzahlen betreffen ausschließlich das Steuerjahr; die "
            "Vorjahres-Historie geht nur in das FIFO ein (siehe alle_veraeusserungen).",
            "Freigrenze (kein Freibetrag): bei Überschreiten ist der GESAMTE § 23-Gewinn steuerpflichtig.",
            "Coins > 1 Jahr gehalten sind steuerfrei (§ 23 Abs. 1 Nr. 2 EStG); die "
            "Jahresfrist endet taggenau nach § 108 AO / § 188 BGB.",
            "Staking/Lending: Zufluss als sonstige Leistung § 22 Nr. 3, Freigrenze 256 €.",
            "Krypto-zu-Krypto-Tausch = Veräußerung des abgegebenen Coins zum EUR-Marktwert; "
            "die Tauschgebühr wird als Anschaffungsnebenkosten des erhaltenen Coins aktiviert.",
            "Endkontrolle durch Steuerberater — dies ist keine Steuerberatung.",
        ],
    }
    return result


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: krypto_fifo.py <transactions.json> <tax_year> [out.json]")
        sys.exit(1)
    txs = _load(sys.argv[1])
    if isinstance(txs, dict) and "transactions" in txs:
        txs = txs["transactions"]
    year = int(sys.argv[2])
    try:
        res = compute_crypto_tax(txs, year)
    except ParseError as e:
        print(f"FEHLER in den Transaktionsdaten: {e}", file=sys.stderr)
        sys.exit(2)
    out = sys.argv[3] if len(sys.argv) > 3 else None
    text = json.dumps(res, indent=2, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"geschrieben: {out}")
        for w in res["warnungen"]:
            print(f"  WARNUNG: {w}")
    else:
        print(text)
