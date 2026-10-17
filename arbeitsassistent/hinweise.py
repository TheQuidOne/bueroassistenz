"""Hinweise aus Mails: feste Regeln, wann aus einer KI-Auswertung eine Aufgabe wird.

Die KI liest nur. Ob daraus ein Hinweis wird und zu welchem Kunden oder welcher Rechnung er
gehört, entscheiden diese Regeln. Rechnungen werden nie verändert.
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal

from . import abgleich, rechnungen
from .geld import euro, menge_text
from .modell import Auswertung
from .rechnungen import iso, tag

# Diese Arten erscheinen unter „Zu erledigen“. „zahlung“ steht nur bei der Rechnung.
AUFGABEN_ARTEN = ("abrechnen", "absage", "rueckfrage", "frist")


def auswertung_als_json(a: Auswertung) -> str:
    werte = asdict(a)
    for feld in ("einsatz_datum", "frist"):
        werte[feld] = iso(werte[feld])
    werte["dauer_stunden"] = str(a.dauer_stunden) if a.dauer_stunden is not None else None
    return json.dumps(werte, ensure_ascii=False)


def auswertung_aus_json(text: str | None) -> Auswertung | None:
    if not text:
        return None
    werte = json.loads(text)
    for feld in ("einsatz_datum", "frist"):
        werte[feld] = tag(werte.get(feld))
    dauer = werte.get("dauer_stunden")
    werte["dauer_stunden"] = Decimal(dauer) if dauer else None
    return Auswertung(**werte)


def _tag_kurz(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def kunde_finden(db: sqlite3.Connection, adresse: str, namen: list[str]) -> sqlite3.Row | None:
    """Zuerst über die E-Mail-Adresse, dann über einen eindeutig passenden Namen."""
    kunden = rechnungen.kunden(db)
    adresse = (adresse or "").strip().lower()
    if adresse:
        for k in kunden:
            if k["email"].strip().lower() == adresse:
                return k
    for name in namen:
        if not name:
            continue
        passend = [k for k in kunden if abgleich.name_passt(name, k["name"])]
        if len(passend) == 1:
            return passend[0]
    return None


def rechnungen_finden(db: sqlite3.Connection, text: str, nummer: str) -> list[sqlite3.Row]:
    """Ausgestellte Rechnungen, deren Nummer im Text steht (gleicher Erkenner wie beim Bankabgleich)."""
    text = f"{text}\n{nummer}"
    im_text = abgleich.nummern_im_text(text)
    alle = db.execute("SELECT * FROM rechnungen WHERE nummer IS NOT NULL AND art = 'rechnung' ORDER BY nummer")
    return [r for r in alle if abgleich.nummer_genannt(r["nummer"], im_text, text)]


def schon_abgerechnet(db: sqlite3.Connection, kunde_id: int | None, datum: date, aktenzeichen: str = "") -> bool:
    """Gibt es für diesen Einsatztag schon eine (nicht stornierte) Rechnung oder einen Entwurf?"""
    bedingung, werte = "", [iso(datum), iso(datum), iso(datum)]
    if kunde_id:
        bedingung = " AND r.kunde_id = ?"
        werte.append(kunde_id)
    elif aktenzeichen:
        bedingung = " AND r.bezug = ?"
        werte.append(aktenzeichen)
    else:
        return False  # ohne Kunde und Aktenzeichen lieber nachfragen
    return db.execute(
        "SELECT 1 FROM rechnungen r WHERE r.art = 'rechnung' AND r.status NOT IN ('storniert', 'berichtigt') AND ("
        "  EXISTS (SELECT 1 FROM positionen p WHERE p.rechnung_id = r.id AND p.datum = ?)"
        "  OR (r.leistung_von <= ? AND COALESCE(r.leistung_bis, r.leistung_von) >= ?))" + bedingung,
        werte,
    ).fetchone() is not None


def _neu(db: sqlite3.Connection, mail_id: int, art: str, titel: str, text: str, kunde_id: int | None = None,
         rechnung_id: int | None = None, datum: date | None = None, ab: date | None = None,
         daten: dict | None = None) -> int:
    return db.execute(
        "INSERT INTO hinweise (mail_id, art, titel, text, kunde_id, rechnung_id, datum, ab, daten, erstellt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mail_id, art, titel[:160], text[:600], kunde_id, rechnung_id, iso(datum), iso(ab),
         json.dumps(daten or {}, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
    ).lastrowid


def aus_mail(db: sqlite3.Connection, mail_id: int, a: Auswertung, heute: date | None = None) -> list[int]:
    """Legt die Hinweise zu einer ausgewerteten Mail an. Gibt ihre IDs zurück.

    Wird eine Mail neu ausgewertet, ersetzen die neuen Hinweise die noch offenen alten.
    """
    heute = heute or date.today()
    m = db.execute("SELECT * FROM mails WHERE id = ?", (mail_id,)).fetchone()
    db.execute("DELETE FROM hinweise WHERE mail_id = ? AND status = 'offen'", (mail_id,))
    if not a.arbeit or a.art in ("sonstiges", "verdaechtig"):
        return []
    k = kunde_finden(db, m["absender_adresse"], [a.auftraggeber, m["absender_name"]])
    kunde_id = k["id"] if k else None
    wer = k["name"] if k else (a.auftraggeber or m["absender_name"] or m["absender_adresse"])
    gefunden = rechnungen_finden(db, f"{m['betreff']}\n{m['text']}", a.rechnungsnummer)
    mail_tag = _tag_kurz(tag(m["datum"][:10])) if m["datum"] else "unbekannt"
    daten = {"aktenzeichen": a.aktenzeichen, "beginn": a.einsatz_beginn, "auftraggeber": a.auftraggeber,
             "dauer": str(a.dauer_stunden) if a.dauer_stunden is not None else ""}
    neu: list[int] = []

    if a.art == "auftrag" and a.einsatz_datum:
        if schon_abgerechnet(db, kunde_id, a.einsatz_datum, a.aktenzeichen):
            return []
        # Derselbe Einsatz aus einer früheren Mail (Anfrage, dann Bestätigung): nur einmal führen.
        db.execute(
            "DELETE FROM hinweise WHERE art = 'abrechnen' AND status = 'offen' AND datum = ? "
            "AND (kunde_id = ? OR (kunde_id IS NULL AND ? IS NULL))",
            (iso(a.einsatz_datum), kunde_id, kunde_id),
        )
        zusatz = f" Aktenzeichen {a.aktenzeichen}." if a.aktenzeichen else ""
        neu.append(_neu(db, mail_id, "abrechnen", f"Einsatz am {_tag_kurz(a.einsatz_datum)} abrechnen: {wer}",
                        f"{a.zusammenfassung}{zusatz}".strip(), kunde_id, None, a.einsatz_datum,
                        a.einsatz_datum, daten))
    elif a.art == "absage":
        if a.einsatz_datum:
            db.execute(
                "UPDATE hinweise SET status = 'abgesagt' WHERE art = 'abrechnen' AND status = 'offen' AND datum = ? "
                "AND (kunde_id = ? OR kunde_id IS NULL)",
                (iso(a.einsatz_datum), kunde_id),
            )
        wann = f"am {_tag_kurz(a.einsatz_datum)}" if a.einsatz_datum else ""
        neu.append(_neu(db, mail_id, "absage", f"Einsatz {wann} abgesagt: {wer}".replace("  ", " "),
                        f"Abgesagt laut Mail vom {mail_tag}. Ob ein Ausfallhonorar fällig ist, hängt von Ihrer "
                        f"Vereinbarung ab. {a.zusammenfassung}".strip(),
                        kunde_id, None, a.einsatz_datum, heute, daten))
    elif a.art == "rueckfrage":
        r = gefunden[0] if gefunden else None
        titel = f"Rückfrage zu Rechnung {r['nummer']}" if r else f"Rückfrage zu einer Rechnung: {wer}"
        neu.append(_neu(db, mail_id, "rueckfrage", titel, a.hinweis or a.zusammenfassung,
                        kunde_id or (r["kunde_id"] if r else None), r["id"] if r else None, None, heute, daten))
    elif a.art == "zahlung":
        for r in gefunden:
            if rechnungen.offen(db, r["id"]) > 0:
                neu.append(_neu(db, mail_id, "zahlung", f"Zahlung für Rechnung {r['nummer']} angekündigt",
                                a.zusammenfassung, r["kunde_id"], r["id"], a.frist, heute, daten))
    elif a.art == "frist" and a.frist:
        was = a.hinweis or a.zusammenfassung or "Frist aus einer Mail"
        neu.append(_neu(db, mail_id, "frist", f"Bis {_tag_kurz(a.frist)}: {was}", a.zusammenfassung,
                        kunde_id, gefunden[0]["id"] if gefunden else None, a.frist, heute, daten))
    return neu


def aufraeumen(db: sqlite3.Connection) -> None:
    """Einsätze, die inzwischen abgerechnet sind, gelten als erledigt."""
    for h in db.execute("SELECT * FROM hinweise WHERE art = 'abrechnen' AND status = 'offen'").fetchall():
        aktenzeichen = json.loads(h["daten"] or "{}").get("aktenzeichen", "")
        if h["datum"] and schon_abgerechnet(db, h["kunde_id"], tag(h["datum"]), aktenzeichen):
            db.execute("UPDATE hinweise SET status = 'erledigt' WHERE id = ?", (h["id"],))


def offene(db: sqlite3.Connection, heute: date | None = None) -> list[sqlite3.Row]:
    """Hinweise für „Zu erledigen“: offen und fällig, Einsätze erst ab ihrem Tag."""
    heute = heute or date.today()
    aufraeumen(db)
    platzhalter = ", ".join("?" * len(AUFGABEN_ARTEN))
    return db.execute(
        f"SELECT * FROM hinweise WHERE status = 'offen' AND art IN ({platzhalter}) "
        "AND (ab IS NULL OR ab <= ?) ORDER BY COALESCE(datum, ab), id",
        (*AUFGABEN_ARTEN, iso(heute)),
    ).fetchall()


def zur_rechnung(db: sqlite3.Connection, rechnung_id: int) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM hinweise WHERE rechnung_id = ? AND status = 'offen' ORDER BY id",
                      (rechnung_id,)).fetchall()


def zur_mail(db: sqlite3.Connection, mail_id: int) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM hinweise WHERE mail_id = ? ORDER BY id", (mail_id,)).fetchall()


def hinweis(db: sqlite3.Connection, hinweis_id: int) -> sqlite3.Row:
    h = db.execute("SELECT * FROM hinweise WHERE id = ?", (hinweis_id,)).fetchone()
    if not h:
        raise rechnungen.Fehler("Diesen Hinweis gibt es nicht mehr.")
    return h


def setzen(db: sqlite3.Connection, hinweis_id: int, status: str) -> None:
    if status not in ("offen", "erledigt", "verworfen"):
        raise rechnungen.Fehler("Unbekannter Status.")
    hinweis(db, hinweis_id)
    db.execute("UPDATE hinweise SET status = ? WHERE id = ?", (status, hinweis_id))


def einsaetze(db: sqlite3.Connection, von: date, bis: date) -> list[sqlite3.Row]:
    """Einsätze aus Mails, die noch nicht abgerechnet sind, auch zukünftige (für den Kalender)."""
    return db.execute(
        "SELECT h.*, COALESCE(k.name, json_extract(h.daten, '$.auftraggeber')) AS wer FROM hinweise h "
        "LEFT JOIN kunden k ON k.id = h.kunde_id "
        "WHERE h.art = 'abrechnen' AND h.status = 'offen' AND h.datum BETWEEN ? AND ?",
        (iso(von), iso(bis)),
    ).fetchall()


def entwurf_daten(db: sqlite3.Connection, hinweis_id: int) -> dict:
    """Formularwerte für einen Rechnungsentwurf aus einem Hinweis. Er prüft und speichert selbst."""
    h = hinweis(db, hinweis_id)
    daten = json.loads(h["daten"] or "{}")
    vorlagen = rechnungen.vorlagen(db)
    suchwort = "ausfall" if h["art"] == "absage" else "dolmetsch"
    vorlage = next((v for v in vorlagen if suchwort in v["beschreibung"].lower()), None)
    if vorlage is None and h["art"] != "absage" and vorlagen:
        vorlage = vorlagen[0]
    menge = "1"
    if h["art"] == "abrechnen" and daten.get("dauer"):
        menge = menge_text(Decimal(daten["dauer"]))
    position = {
        "datum": h["datum"] or "",
        "beschreibung": vorlage["beschreibung"] if vorlage else
        ("Ausfallhonorar" if h["art"] == "absage" else "Dolmetscheinsatz"),
        "menge": menge,
        "einheit": vorlage["einheit"] if vorlage else ("pauschal" if h["art"] == "absage" else "Std."),
        "einzelpreis": euro(vorlage["einzelpreis"], False) if vorlage else "",
        "erlaeuterung": "",
    }
    return {"kunde_id": h["kunde_id"] or "", "bezug": daten.get("aktenzeichen", ""), "leistung_von": "",
            "leistung_bis": "", "steuerfall": "", "zahlungsziel": "", "einleitung": "", "schluss": "",
            "positionen": [position], "hinweis_id": h["id"], "auftraggeber": daten.get("auftraggeber", "")}
