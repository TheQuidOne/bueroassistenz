"""Ausgestellte Rechnung löschen (nur nie verschickte, etwa Tests): Nummer wieder frei, PDF aufbewahrt."""
from datetime import date

import pytest

from arbeitsassistent import rechnungen
from arbeitsassistent import db as dbmod
from arbeitsassistent.modell import Fehler
from tests.test_web import app, client, schluessel  # noqa: F401 (Fixtures der Weboberfläche)

TAG = date(2026, 10, 9)


def ausgestellt(db, einst, kunde, ordner) -> int:
    rid = rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
        {"datum": "2026-10-07", "beschreibung": "Testeinsatz", "menge": "1", "einzelpreis": "85"}]}, einst)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    return rid


def test_testrechnung_loeschen_gibt_nummer_frei(db, einst, kunde, ordner):
    rid = ausgestellt(db, einst, kunde, ordner)
    pdf = ordner / rechnungen.rechnung(db, rid)["pdf_pfad"]
    with pytest.raises(Fehler, match="genau eintippen"):
        rechnungen.rechnung_loeschen(db, rid, "2026-002", ordner)
    assert rechnungen.rechnung_loeschen(db, rid, " 2026-001 ", ordner) == "2026-001"
    assert not db.execute("SELECT 1 FROM rechnungen WHERE id = ?", (rid,)).fetchone()
    assert rechnungen.naechste_nummer(db, TAG, einst) == "2026-001"  # wieder frei
    vermerk = db.execute("SELECT * FROM loeschungen").fetchone()
    assert (vermerk["nummer"], vermerk["brutto"], vermerk["art"]) == ("2026-001", 8500, "rechnung")
    assert not pdf.exists() and (ordner / vermerk["pdf_pfad"]).is_file()  # aufbewahrt, nicht vernichtet
    # Die freie Nummer lässt sich neu vergeben, ohne dass das alte PDF im Weg ist
    assert rechnungen.rechnung(db, ausgestellt(db, einst, kunde, ordner))["nummer"] == "2026-001"


def test_bankvorschlag_zur_geloeschten_rechnung_verschwindet(db, einst, kunde, ordner):
    from arbeitsassistent import umsaetze
    rid = ausgestellt(db, einst, kunde, ordner)
    uid = db.execute("INSERT INTO umsaetze (fingerabdruck, datum, betrag, gegenpartei, zweck) "
                     "VALUES ('x', '2026-10-10', 8500, 'Kunde', 'RE 2026-001')").lastrowid
    db.execute("INSERT INTO vorschlaege (umsatz_id, zahlungen, grund) VALUES (?, ?, 'Nummer passt')",
               (uid, f"[[{rid}, 8500]]"))
    assert len(umsaetze.vorschlaege(db)) == 1
    rechnungen.rechnung_loeschen(db, rid, "2026-001", ordner)
    assert umsaetze.vorschlaege(db) == [] and not db.execute("SELECT 1 FROM vorschlaege").fetchone()


def test_nicht_loeschbar_mit_zahlung(db, einst, kunde, ordner):
    rid = ausgestellt(db, einst, kunde, ordner)
    rechnungen.zahlung_erfassen(db, rid, 8500, TAG)
    with pytest.raises(Fehler, match="Zahlungen"):
        rechnungen.rechnung_loeschen(db, rid, "2026-001", ordner)


def test_storno_und_rechnung_nacheinander_loeschen(db, einst, kunde, ordner):
    rid = ausgestellt(db, einst, kunde, ordner)
    storno = rechnungen.stornieren(db, rid, einst, TAG, ordner)
    assert "storniert" in rechnungen.loeschen_moeglich(db, rid)
    rechnungen.rechnung_loeschen(db, storno, "2026-002", ordner)
    assert rechnungen.rechnung(db, rid)["status"] == "ausgestellt"  # Storno weg, Rechnung gilt wieder
    rechnungen.rechnung_loeschen(db, rid, "2026-001", ordner)
    assert rechnungen.naechste_nummer(db, TAG, einst) == "2026-001"


def test_korrektur_loeschen_macht_berichtigung_rueckgaengig(db, einst, kunde, ordner):
    rid = ausgestellt(db, einst, kunde, ordner)
    korrektur = rechnungen.berichtigen(db, rid, {"kunde_id": kunde, "positionen": [
        {"datum": "2026-10-07", "beschreibung": "Testeinsatz", "menge": "2", "einzelpreis": "85"}]}, einst, TAG, ordner)
    assert rechnungen.rechnung(db, rid)["status"] == "berichtigt"
    rechnungen.rechnung_loeschen(db, korrektur, "2026-002", ordner)
    assert rechnungen.rechnung(db, rid)["status"] == "ausgestellt"
    assert db.execute("SELECT art FROM loeschungen").fetchone()[0] == "korrektur"


def test_loeschen_in_der_oberflaeche(app, client):
    csrf = schluessel(client)
    db = dbmod.verbinden(app.config["ORDNER"])
    offen = db.execute("SELECT r.id, r.nummer FROM rechnungen r WHERE r.status = 'ausgestellt' AND r.art = 'rechnung' "
                       "AND NOT EXISTS (SELECT 1 FROM zahlungen z WHERE z.rechnung_id = r.id) LIMIT 1").fetchone()
    bezahlt = db.execute("SELECT rechnung_id FROM zahlungen LIMIT 1").fetchone()[0]
    assert "Rechnung endgültig löschen" in client.get(f"/rechnungen/{offen['id']}").get_data(as_text=True)
    assert "Rechnung endgültig löschen" not in client.get(f"/rechnungen/{bezahlt}").get_data(as_text=True)
    falsch = client.post(f"/rechnungen/{offen['id']}/endgueltig-loeschen", data={"csrf": csrf, "bestaetigung": "x"},
                         follow_redirects=True).get_data(as_text=True)
    assert "genau eintippen" in falsch
    antwort = client.post(f"/rechnungen/{offen['id']}/endgueltig-loeschen",
                          data={"csrf": csrf, "bestaetigung": offen["nummer"]}, follow_redirects=True)
    assert f"Rechnung {offen['nummer']} ist gelöscht" in antwort.get_data(as_text=True)
