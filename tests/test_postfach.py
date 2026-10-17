"""Hintergrundlauf: alle wartenden Mails am Stück lesen, sichtbarer Fortschritt."""
from datetime import datetime

from arbeitsassistent import ki as kimod
from arbeitsassistent import postfach
from arbeitsassistent.modell import Auswertung


class BereiteKi:
    def status(self):
        return True, "bereit"


def wartende_mails(db, anzahl: int) -> None:
    for nr in range(anzahl):
        db.execute(
            "INSERT INTO mails (message_id, datum, absender_name, absender_adresse, betreff, text, abgerufen) "
            "VALUES (?, ?, 'Erika Beispiel', 'erika@beispiel.example', ?, 'Hallo', ?)",
            (f"<test-{nr}@beispiel.example>", f"2026-10-{1 + nr % 9:02d}T09:00:00", f"Mail {nr}",
             datetime.now().isoformat(timespec="seconds")),
        )


def test_liest_alle_wartenden_mails_am_stueck(db, ordner, monkeypatch):
    wartende_mails(db, 45)  # früher las ein Lauf höchstens 40, der Rest wartete 15 Minuten
    beobachtet = []

    def lesen(ki, mail, heute, kunden, liste):
        beobachtet.append(postfach.fortschritt())
        return Auswertung(arbeit=False, art="sonstiges", zusammenfassung="Privat")

    monkeypatch.setattr(postfach, "ki_fuer", lambda einst: BereiteKi())
    monkeypatch.setattr(kimod, "auswerten", lesen)
    postfach.lauf(ordner)

    assert db.execute("SELECT COUNT(*) FROM mails WHERE status = 'ausgewertet'").fetchone()[0] == 45
    erste, zweite, letzte = beobachtet[0], beobachtet[1], beobachtet[-1]
    assert erste["laeuft"] and erste["phase"] == "lesen" and (erste["nr"], erste["von"]) == (1, 45)
    assert erste["geschaetzt"] and erste["rest_sekunden"] == 45 * postfach.SEKUNDEN_JE_MAIL
    assert (zweite["nr"], zweite["geschaetzt"]) == (2, False)  # ab jetzt aus dem echten Tempo
    assert (letzte["nr"], letzte["von"]) == (45, 45)
    assert postfach.fortschritt() == {"laeuft": False}  # danach aufgeräumt
    einst = dict(db.execute("SELECT schluessel, wert FROM einstellungen").fetchall())
    assert einst["mail_stand"] == "0 neue Mails, 45 ausgewertet, 0 Hinweise"


def test_fortschritt_auch_nach_fehler_aufgeraeumt(db, ordner, monkeypatch):
    wartende_mails(db, 2)

    def kaputt(ki, mail, heute, kunden, liste):
        raise kimod.KiFehler("Die KI antwortet nicht.")

    monkeypatch.setattr(postfach, "ki_fuer", lambda einst: BereiteKi())
    monkeypatch.setattr(kimod, "auswerten", kaputt)
    postfach.lauf(ordner)
    assert postfach.fortschritt() == {"laeuft": False}
    fehler = db.execute("SELECT wert FROM einstellungen WHERE schluessel = 'mail_letzter_fehler'").fetchone()[0]
    assert "antwortet nicht" in fehler


def test_jetzt_abrufen_auch_ohne_automatik(db, ordner, monkeypatch):
    from arbeitsassistent import db as dbmod
    dbmod.einstellungen_setzen(db, {"mail_host": "posteo.de", "mail_benutzer": "max@beispiel.example",
                                    "mail_aktiv": "0"})
    abgerufen = []
    monkeypatch.setattr(postfach, "abrufen", lambda db, einst: abgerufen.append(1) or 0)
    postfach.lauf(ordner)  # im Hintergrund: Automatik aus, also kein Abruf
    assert abgerufen == []
    postfach.lauf(ordner, von_hand=True)  # Knopf „Jetzt abrufen“
    assert abgerufen == [1]
    stand = db.execute("SELECT wert FROM einstellungen WHERE schluessel = 'mail_stand'").fetchone()[0]
    assert stand == "0 neue Mails"
