"""Postfach abrufen und neue Mails von der lokalen KI auswerten lassen. Nichts verlässt den Rechner.

Ablauf eines Laufs: Mails nur lesend abrufen → speichern → neue Mails einzeln von der KI
auswerten → daraus nach festen Regeln Hinweise anlegen (siehe hinweise.py).
"""

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db as dbmod
from . import hinweise, rechnungen
from .geld import euro
from .modell import Mail, MailKonto
from .rechnungen import Fehler

SCHLUESSELBUND = "Arbeits-Assistent"
INTERVALL = 15 * 60  # Sekunden zwischen zwei Abrufen
SEKUNDEN_JE_MAIL = 20  # Schätzung für die Restzeit, bis die erste Mail gelesen ist (M1 Pro)

_sperre = threading.Lock()
# Stand des laufenden Durchgangs für die Anzeige. Nur im Speicher, gilt für diesen Prozess.
_fortschritt: dict = {}


# --- Zugang -----------------------------------------------------------------


def konto(einst: dict) -> MailKonto | None:
    if not (einst.get("mail_host") and einst.get("mail_benutzer")):
        return None
    port = einst.get("mail_port", "993")
    return MailKonto(host=einst["mail_host"], benutzer=einst["mail_benutzer"],
                     port=int(port) if port.isdigit() else 993, ordner=einst.get("mail_ordner") or "INBOX")


def _keyring():
    import keyring  # erst bei Bedarf: auf dem Mac der Schlüsselbund des Systems
    return keyring


def passwort_speichern(benutzer: str, passwort: str) -> None:
    try:
        _keyring().set_password(SCHLUESSELBUND, benutzer, passwort)
    except Exception as fehler:
        raise Fehler(f"Das Passwort ließ sich nicht im Schlüsselbund speichern ({fehler}).") from None


def passwort_lesen(benutzer: str) -> str | None:
    try:
        return _keyring().get_password(SCHLUESSELBUND, benutzer)
    except Exception as fehler:
        raise Fehler(f"Der Schlüsselbund ist nicht erreichbar ({fehler}).") from None


def passwort_loeschen(benutzer: str) -> None:
    try:
        _keyring().delete_password(SCHLUESSELBUND, benutzer)
    except Exception:
        pass  # war nicht da


def verbindung_testen(k: MailKonto, passwort: str) -> list[str]:
    from . import mail as mailmod
    try:
        return mailmod.verbindung_testen(k, passwort)
    except mailmod.MailFehler as fehler:
        raise Fehler(str(fehler)) from None


# --- Abruf und Auswertung ----------------------------------------------------


def speichern(db: sqlite3.Connection, mails: list[Mail]) -> int:
    """Neue Mails ablegen. Bekannte (gleiche Message-ID) werden übersprungen."""
    neu = 0
    jetzt = datetime.now().isoformat(timespec="seconds")
    with dbmod.Transaktion(db):
        for m in mails:
            neu += db.execute(
                "INSERT OR IGNORE INTO mails (message_id, uid, ordner, datum, absender_name, absender_adresse, "
                "betreff, text, anhaenge, abgerufen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (m.message_id, m.uid, m.ordner, m.datum.isoformat(timespec="seconds") if m.datum else None,
                 m.absender_name, m.absender_adresse, m.betreff, m.text,
                 json.dumps(m.anhaenge, ensure_ascii=False), jetzt),
            ).rowcount
    return neu


def abrufen(db: sqlite3.Connection, einst: dict, heute: date | None = None) -> int:
    """Holt neue Mails (nur lesend). Gibt die Zahl der neu gespeicherten zurück."""
    from . import mail as mailmod
    heute = heute or date.today()
    k = konto(einst)
    if k is None:
        raise Fehler("Es ist noch kein Postfach eingerichtet.")
    passwort = passwort_lesen(k.benutzer)
    if not passwort:
        raise Fehler("Für das Postfach ist kein Passwort hinterlegt. Bitte unter Einstellungen → E-Mail eintragen.")
    tage = einst.get("mail_seit_tage", "30")
    seit = heute - timedelta(days=int(tage) if tage.isdigit() else 30)
    uidvalidity = int(einst["mail_uidvalidity"]) if einst.get("mail_uidvalidity", "").isdigit() else None
    letzte = int(einst["mail_letzte_uid"]) if einst.get("mail_letzte_uid", "").isdigit() else 0
    try:
        mails, uidvalidity, hoechste = mailmod.abrufen(k, passwort, seit, uidvalidity, letzte)
    except mailmod.MailFehler as fehler:
        raise Fehler(str(fehler)) from None
    neu = speichern(db, mails)
    dbmod.einstellungen_setzen(db, {"mail_uidvalidity": str(uidvalidity), "mail_letzte_uid": str(hoechste)})
    return neu


def ki_fuer(einst: dict):
    from . import ki
    return ki.Ollama(url=einst.get("ki_url") or "http://127.0.0.1:11434",
                     modell=einst.get("ki_modell") or "gemma4:12b")


def kontext(db: sqlite3.Connection, heute: date) -> tuple[list[str], list[dict]]:
    """Was die KI zum Einordnen wissen darf: Kundennamen und offene oder jüngere Rechnungen."""
    kunden = [k["name"] for k in rechnungen.kunden(db)]
    grenze = (heute - timedelta(days=120)).isoformat()
    liste = []
    for r in db.execute(
        "SELECT r.*, k.name AS kunde_name FROM rechnungen r JOIN kunden k ON k.id = r.kunde_id "
        "WHERE r.status = 'ausgestellt' AND r.art = 'rechnung' AND r.datum >= ? ORDER BY r.datum DESC LIMIT 40",
        (grenze,),
    ):
        liste.append({"nummer": r["nummer"], "kunde": r["kunde_name"], "datum": r["datum"],
                      "betrag": euro(r["brutto"]), "bezug": r["bezug"]})
    return kunden, liste


def als_mail(z: sqlite3.Row) -> Mail:
    return Mail(message_id=z["message_id"], datum=datetime.fromisoformat(z["datum"]) if z["datum"] else None,
                absender_name=z["absender_name"], absender_adresse=z["absender_adresse"], betreff=z["betreff"],
                text=z["text"], anhaenge=json.loads(z["anhaenge"] or "[]"), uid=z["uid"], ordner=z["ordner"])


def auswerten(db: sqlite3.Connection, ki, heute: date | None = None, hoechstens: int | None = None,
              fortschritt=None) -> dict:
    """Wertet neue Mails aus (alle, oder höchstens so viele) und legt Hinweise an.

    Ist die KI nicht bereit, passiert nichts. fortschritt(nr, von) wird vor jeder Mail gerufen.
    """
    from . import ki as kimod
    heute = heute or date.today()
    bereit, text = ki.status()
    if not bereit:
        raise Fehler(text)
    offen = db.execute("SELECT * FROM mails WHERE status = 'neu' ORDER BY datum LIMIT ?",
                       (-1 if hoechstens is None else hoechstens,)).fetchall()
    kunden, liste = kontext(db, heute)
    ausgewertet = angelegt = 0
    for nr, z in enumerate(offen, 1):
        if fortschritt:
            fortschritt(nr, len(offen))
        try:
            a = kimod.auswerten(ki, als_mail(z), heute, kunden, liste)
        except kimod.KiFehler as fehler:
            raise Fehler(str(fehler)) from None  # KI weg: später weitermachen
        except Exception:
            db.execute("UPDATE mails SET status = 'fehler' WHERE id = ?", (z["id"],))
            continue
        with dbmod.Transaktion(db):
            db.execute(
                "UPDATE mails SET status = 'ausgewertet', auswertung = ?, ausgewertet = ?, arbeit = ?, art = ? "
                "WHERE id = ?",
                (hinweise.auswertung_als_json(a), datetime.now().isoformat(timespec="seconds"), int(a.arbeit),
                 a.art, z["id"]),
            )
            angelegt += len(hinweise.aus_mail(db, z["id"], a, heute))
        ausgewertet += 1
    return {"ausgewertet": ausgewertet, "hinweise": angelegt}


def neu_auswerten(db: sqlite3.Connection, mail_id: int) -> None:
    db.execute("UPDATE mails SET status = 'neu' WHERE id = ?", (mail_id,))


# --- Lauf im Hintergrund -----------------------------------------------------


def _stand(db: sqlite3.Connection, text: str) -> None:
    dbmod.einstellungen_setzen(db, {"mail_stand": text})


def fortschritt() -> dict:
    """Für die Anzeige: läuft?, Phase („abrufen“, „lesen“), Mail nr von von, Restzeit in Sekunden."""
    f = {k: v for k, v in _fortschritt.items() if k != "lesen_seit"}
    f["laeuft"] = laeuft()
    if not f["laeuft"]:
        return {"laeuft": False}
    if f.get("phase") == "lesen" and f.get("von"):
        fertig = max(0, f["nr"] - 1)
        je_mail = (time.monotonic() - _fortschritt["lesen_seit"]) / fertig if fertig else SEKUNDEN_JE_MAIL
        f["rest_sekunden"] = round(je_mail * (f["von"] - fertig))
        f["geschaetzt"] = not fertig  # noch keine Mail fertig: nur Erfahrungswert
    return f


def lauf(ordner: Path, von_hand: bool = False) -> None:
    """Ein vollständiger Durchgang. Läuft schon einer, passiert nichts.

    Automatisch wird nur abgerufen, wenn „automatisch abrufen“ an ist. Von Hand („Jetzt
    abrufen“) immer, sobald ein Postfach eingerichtet ist.
    """
    if not _sperre.acquire(blocking=False):
        return
    db = None
    try:
        db = dbmod.verbinden(ordner)
        einst = dbmod.einstellungen(db)
        abrufen_an = bool(konto(einst)) and (von_hand or einst.get("mail_aktiv") == "1")
        if not abrufen_an and not db.execute("SELECT 1 FROM mails WHERE status = 'neu' LIMIT 1").fetchone():
            return  # nichts zu tun: kein Postfach und keine ungelesenen Mails
        neu = 0
        _fortschritt.clear()
        if abrufen_an:
            _fortschritt.update(phase="abrufen")
            _stand(db, "Mails werden abgerufen …")
            neu = abrufen(db, einst)
        ergebnis = {"ausgewertet": 0, "hinweise": 0}
        wartend = db.execute("SELECT COUNT(*) FROM mails WHERE status = 'neu'").fetchone()[0]
        if wartend:
            _fortschritt.update(phase="lesen", geholt=neu, nr=0, von=wartend, lesen_seit=time.monotonic())

            def melden(nr: int, von: int) -> None:
                _fortschritt.update(nr=nr, von=von)
                _stand(db, f"Die KI liest Mail {nr} von {von} …")

            ergebnis = auswerten(db, ki_fuer(einst), fortschritt=melden)
        teile = [f"{neu} neue Mail{'' if neu == 1 else 's'}"]
        if ergebnis["ausgewertet"]:
            teile.append(f"{ergebnis['ausgewertet']} ausgewertet, {ergebnis['hinweise']} Hinweise")
        dbmod.einstellungen_setzen(db, {"mail_letzter_abruf": datetime.now().isoformat(timespec="seconds"),
                                        "mail_letzter_fehler": "", "mail_stand": ", ".join(teile)})
    except Fehler as fehler:
        if db is not None:
            dbmod.einstellungen_setzen(db, {"mail_letzter_fehler": str(fehler), "mail_stand": ""})
    except Exception as fehler:  # der Hintergrund-Thread darf nie sterben
        if db is not None:
            dbmod.einstellungen_setzen(db, {"mail_letzter_fehler": f"Unerwarteter Fehler: {fehler}", "mail_stand": ""})
    finally:
        _fortschritt.clear()
        if db is not None:
            db.close()
        _sperre.release()


def laeuft() -> bool:
    return _sperre.locked()


def anstossen(ordner: Path) -> None:
    """Sofort einen Lauf starten (Knopf „Jetzt abrufen“), ohne die Seite warten zu lassen."""
    threading.Thread(target=lauf, args=(ordner, True), daemon=True, name="postfach-jetzt").start()


def hintergrund(ordner: Path, intervall: int = INTERVALL) -> None:
    """Ruft regelmäßig ab, solange das Programm läuft."""
    def schleife():
        while True:
            lauf(ordner)
            time.sleep(intervall)
    threading.Thread(target=schleife, daemon=True, name="postfach").start()
