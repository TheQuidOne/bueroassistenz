"""Kontoumsätze: CSV einlesen, Zahlungseingänge den Rechnungen zuordnen, Ausgaben einordnen."""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import abgleich, bank
from . import db as dbmod
from . import rechnungen
from .modell import Umsatz
from .rechnungen import Fehler, iso, tag

# Einordnung eines Umsatzes. „rechnung“ wird nur über Zahlungen gesetzt, nie von Hand.
EINNAHMEN = {
    "einnahme": "Sonstige betriebliche Einnahme",
}
AUSGABEN = {
    "fahrtkosten": "Fahrtkosten",
    "fortbildung": "Fortbildung & Supervision",
    "arbeitsmittel": "Arbeitsmittel, Software & Fachliteratur",
    "telefon": "Telefon & Internet",
    "buero": "Büro & Porto",
    "versicherung": "Berufliche Versicherungen & Beiträge",
    "beratung": "Steuerberatung & Buchhaltung",
    "umsatzsteuer": "Umsatzsteuer ans Finanzamt",
    "sonstige": "Sonstige Betriebsausgabe",
}
PRIVAT = {"privat": "Privat (zählt nicht)"}
KATEGORIEN = {**EINNAHMEN, **AUSGABEN, **PRIVAT}


def _muster(gegenpartei: str) -> str:
    return re.sub(r"\s+", " ", gegenpartei).strip().lower()


def importieren(db: sqlite3.Connection, dateiname: str, daten: bytes,
                ordner: Path | None = None) -> dict:
    """Liest einen CSV-Export ein. Bekannte Umsätze werden übersprungen."""
    ordner = ordner or dbmod.datenordner()
    try:
        umsaetze = bank.lese_csv(daten)
    except bank.CsvFehler as fehler:
        raise Fehler(str(fehler)) from None
    if not umsaetze:
        raise Fehler("In der Datei stehen keine gebuchten Umsätze.")
    jetzt = datetime.now()
    sicherer_name = re.sub(r"[^\w.-]+", "_", Path(dateiname or "umsaetze.csv").name)
    ablage = ordner / "importe" / f"{jetzt:%Y-%m-%d_%H%M%S}_{sicherer_name}"
    ablage.parent.mkdir(parents=True, exist_ok=True)
    ablage.write_bytes(daten)  # Original aufheben, als Beleg

    regeln = {z["muster"]: z["kategorie"] for z in db.execute("SELECT * FROM regeln")}
    neu = 0
    with dbmod.Transaktion(db):
        import_id = db.execute(
            "INSERT INTO importe (zeitpunkt, dateiname, neu, doppelt) VALUES (?, ?, 0, 0)",
            (jetzt.isoformat(timespec="seconds"), dateiname or ""),
        ).lastrowid
        for u in umsaetze:
            kategorie = regeln.get(_muster(u.gegenpartei), "")
            cur = db.execute(
                "INSERT OR IGNORE INTO umsaetze (fingerabdruck, datum, betrag, gegenpartei, iban, zweck, "
                "kategorie, import_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (u.fingerabdruck, iso(u.datum), u.betrag, u.gegenpartei, u.iban, u.zweck,
                 kategorie, import_id),
            )
            neu += cur.rowcount
        db.execute("UPDATE importe SET neu = ?, doppelt = ? WHERE id = ?",
                   (neu, len(umsaetze) - neu, import_id))
    gebucht, vorschlaege = abgleich_laufen(db)
    return {"neu": neu, "doppelt": len(umsaetze) - neu, "gebucht": gebucht, "vorschlaege": vorschlaege,
            "von": umsaetze[0].datum, "bis": umsaetze[-1].datum}


def _als_umsatz(z: sqlite3.Row) -> Umsatz:
    return Umsatz(datum=tag(z["datum"]), betrag=z["betrag"], gegenpartei=z["gegenpartei"],
                  iban=z["iban"], zweck=z["zweck"], fingerabdruck=z["fingerabdruck"])


def abgleich_laufen(db: sqlite3.Connection) -> tuple[int, int]:
    """Ordnet offene Eingänge zu. Sichere Treffer werden gebucht, der Rest wird vorgeschlagen.

    Gibt (gebuchte Umsätze, neue Vorschläge) zurück.
    """
    offene_umsaetze = db.execute(
        "SELECT * FROM umsaetze WHERE kategorie = '' AND betrag > 0 ORDER BY datum, id"
    ).fetchall()
    ergebnisse = abgleich.abgleichen(
        [(z["id"], _als_umsatz(z)) for z in offene_umsaetze], rechnungen.offene_rechnungen(db)
    )
    abgelehnt = {
        (z["umsatz_id"], z["zahlungen"])
        for z in db.execute("SELECT umsatz_id, zahlungen FROM vorschlaege WHERE abgelehnt = 1")
    }
    gebucht = neu = 0
    with dbmod.Transaktion(db):
        db.execute("DELETE FROM vorschlaege WHERE abgelehnt = 0")
        for v in ergebnisse:
            zahlungen = json.dumps(sorted([list(p) for p in v.zahlungen]))
            if not v.zahlungen or (v.umsatz_id, zahlungen) in abgelehnt:
                continue
            if v.sicher:
                try:
                    _buchen(db, v.umsatz_id, v.zahlungen, "automatisch: " + v.grund)
                    gebucht += 1
                    continue
                except Fehler:
                    pass  # passt doch nicht genau: dann als Vorschlag zur Bestätigung
            db.execute(
                "INSERT INTO vorschlaege (umsatz_id, zahlungen, grund) VALUES (?, ?, ?) "
                "ON CONFLICT(umsatz_id) DO UPDATE SET zahlungen = excluded.zahlungen, "
                "grund = excluded.grund, abgelehnt = 0",
                (v.umsatz_id, zahlungen, v.grund),
            )
            neu += 1
    return gebucht, neu


def _umsatz(db: sqlite3.Connection, umsatz_id: int) -> sqlite3.Row:
    z = db.execute("SELECT * FROM umsaetze WHERE id = ?", (umsatz_id,)).fetchone()
    if not z:
        raise Fehler("Diesen Umsatz gibt es nicht.")
    return z


def _buchen(db: sqlite3.Connection, umsatz_id: int, zahlungen: list, notiz: str) -> None:
    """Bucht Zahlungen aus einem Umsatz. Läuft innerhalb einer Transaktion."""
    u = _umsatz(db, umsatz_id)
    if u["kategorie"]:
        raise Fehler("Dieser Umsatz ist schon eingeordnet.")
    if sum(betrag for _, betrag in zahlungen) > u["betrag"]:
        raise Fehler("Die Zahlungen sind höher als der Umsatz.")
    for rechnung_id, betrag in zahlungen:  # erst alles prüfen, dann schreiben
        if betrag > rechnungen.offen(db, rechnung_id):
            raise Fehler(f"Bei Rechnung {rechnungen.rechnung(db, rechnung_id)['nummer']} "
                         "ist weniger offen als zugeordnet werden soll.")
    for rechnung_id, betrag in zahlungen:
        rechnungen.zahlung_erfassen(db, rechnung_id, betrag, tag(u["datum"]), umsatz_id, notiz)
    db.execute("UPDATE umsaetze SET kategorie = 'rechnung' WHERE id = ?", (umsatz_id,))
    db.execute("DELETE FROM vorschlaege WHERE umsatz_id = ?", (umsatz_id,))


def vorschlag_annehmen(db: sqlite3.Connection, umsatz_id: int) -> None:
    v = db.execute("SELECT * FROM vorschlaege WHERE umsatz_id = ?", (umsatz_id,)).fetchone()
    if not v:
        raise Fehler("Diesen Vorschlag gibt es nicht mehr.")
    with dbmod.Transaktion(db):
        _buchen(db, umsatz_id, json.loads(v["zahlungen"]), "bestätigt: " + v["grund"])


def vorschlag_ablehnen(db: sqlite3.Connection, umsatz_id: int) -> None:
    db.execute("UPDATE vorschlaege SET abgelehnt = 1 WHERE umsatz_id = ?", (umsatz_id,))


def zuordnen(db: sqlite3.Connection, umsatz_id: int, rechnung_id: int) -> None:
    """Ordnet einen Eingang von Hand einer Rechnung zu (höchstens deren offenen Betrag)."""
    u = _umsatz(db, umsatz_id)
    if u["betrag"] <= 0:
        raise Fehler("Nur Zahlungseingänge lassen sich einer Rechnung zuordnen.")
    rest = rechnungen.offen(db, rechnung_id)
    if rest <= 0:
        raise Fehler("Bei dieser Rechnung ist nichts mehr offen.")
    with dbmod.Transaktion(db):
        _buchen(db, umsatz_id, [(rechnung_id, min(rest, u["betrag"]))], "von Hand zugeordnet")


def kategorie_setzen(db: sqlite3.Connection, umsatz_id: int, kategorie: str, merken: bool = False) -> int:
    """Ordnet einen Umsatz ein. Mit `merken` gilt das auch für alle weiteren Umsätze mit
    derselben Gegenpartei. Gibt die Zahl der zusätzlich eingeordneten Umsätze zurück."""
    if kategorie and kategorie not in KATEGORIEN:
        raise Fehler("Unbekannte Kategorie.")
    u = _umsatz(db, umsatz_id)
    if u["kategorie"] == "rechnung":
        raise Fehler("Dieser Umsatz bezahlt eine Rechnung. Bitte dort die Zahlung lösen.")
    weitere = 0
    with dbmod.Transaktion(db):
        db.execute("UPDATE umsaetze SET kategorie = ? WHERE id = ?", (kategorie, umsatz_id))
        db.execute("DELETE FROM vorschlaege WHERE umsatz_id = ?", (umsatz_id,))
        muster = _muster(u["gegenpartei"])
        if merken and kategorie and muster:
            db.execute(
                "INSERT INTO regeln (muster, kategorie) VALUES (?, ?) "
                "ON CONFLICT(muster) DO UPDATE SET kategorie = excluded.kategorie",
                (muster, kategorie),
            )
            for z in db.execute("SELECT id, gegenpartei FROM umsaetze WHERE kategorie = ''").fetchall():
                if _muster(z["gegenpartei"]) == muster:
                    db.execute("UPDATE umsaetze SET kategorie = ? WHERE id = ?", (kategorie, z["id"]))
                    db.execute("DELETE FROM vorschlaege WHERE umsatz_id = ?", (z["id"],))
                    weitere += 1
    return weitere


def regeln(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM regeln ORDER BY muster").fetchall()


def regel_loeschen(db: sqlite3.Connection, regel_id: int) -> None:
    db.execute("DELETE FROM regeln WHERE id = ?", (regel_id,))


def vorschlaege(db: sqlite3.Connection) -> list[dict]:
    ergebnis = []
    for v in db.execute(
        "SELECT v.*, u.datum, u.betrag, u.gegenpartei, u.zweck FROM vorschlaege v "
        "JOIN umsaetze u ON u.id = v.umsatz_id WHERE v.abgelehnt = 0 ORDER BY u.datum"
    ):
        teile = []
        for rechnung_id, betrag in json.loads(v["zahlungen"]):
            r = rechnungen.rechnung(db, rechnung_id)
            teile.append({"id": rechnung_id, "nummer": r["nummer"], "betrag": betrag})
        ergebnis.append({"v": v, "rechnungen": teile})
    return ergebnis


def liste(db: sqlite3.Connection, filter_: str = "offen", jahr: int | None = None) -> list[dict]:
    """Umsätze für die Bank-Seite. filter_: offen, eingaenge, ausgaben, alle."""
    bedingungen, werte = [], []
    if filter_ == "offen":
        bedingungen.append("u.kategorie = ''")
    elif filter_ == "eingaenge":
        bedingungen.append("u.betrag > 0")
    elif filter_ == "ausgaben":
        bedingungen.append("u.betrag < 0")
    if jahr:
        bedingungen.append("u.datum LIKE ?")
        werte.append(f"{jahr}-%")
    sql = "SELECT u.* FROM umsaetze u"
    if bedingungen:
        sql += " WHERE " + " AND ".join(bedingungen)
    sql += " ORDER BY u.datum DESC, u.id DESC"
    ergebnis = []
    for u in db.execute(sql, werte).fetchall():
        bezahlt = db.execute(
            "SELECT z.betrag, r.id, r.nummer FROM zahlungen z JOIN rechnungen r ON r.id = z.rechnung_id "
            "WHERE z.umsatz_id = ?", (u["id"],)
        ).fetchall()
        v = db.execute("SELECT * FROM vorschlaege WHERE umsatz_id = ? AND abgelehnt = 0",
                       (u["id"],)).fetchone()
        ids = [rechnung_id for rechnung_id, _ in json.loads(v["zahlungen"])] if v else []
        ergebnis.append({"u": u, "bezahlt": bezahlt, "vorschlag": v, "vorschlag_ids": ids})
    return ergebnis


def letzter_import(db: sqlite3.Connection) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM importe ORDER BY id DESC LIMIT 1").fetchone()
