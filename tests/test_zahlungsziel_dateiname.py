"""Zahlungsziel in Kalendertagen (ohne Verschieben) und frei wählbarer Dateiname der Rechnungen."""
from datetime import date

import pytest

from arbeitsassistent import rechnungen
from arbeitsassistent import db as dbmod
from arbeitsassistent.modell import Fehler
from tests.test_web import app, client, schluessel  # noqa: F401 (Fixtures der Weboberfläche)


def entwurf(db, einst, kunde):
    return rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
        {"datum": "2026-10-07", "beschreibung": "Einsatz", "menge": "1", "einzelpreis": "85"}]}, einst)


@pytest.mark.parametrize("ausgestellt, faellig", [
    (date(2026, 10, 9), "2026-10-23"),  # Fr + 14 Tage
    (date(2026, 10, 3), "2026-10-17"),  # endet an einem Samstag: bleibt Samstag
    (date(2026, 12, 18), "2027-01-01"),  # endet an Neujahr: bleibt Neujahr
])
def test_zahlungsziel_in_kalendertagen(db, einst, kunde, ordner, ausgestellt, faellig):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, ausgestellt, ordner)
    assert rechnungen.rechnung(db, rid)["faellig"] == faellig


def test_dateiname_nach_format(db, einst, kunde, ordner):
    einst = {**einst, "dateiname": "Rechnung_{nummer}_{kunde}"}
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, date(2026, 10, 9), ordner)
    assert rechnungen.rechnung(db, rid)["pdf_pfad"] == "rechnungen/2026/Rechnung_2026-001_Landschaftsverband Musterland.pdf"
    storno = rechnungen.stornieren(db, rid, {**einst, "dateiname": "{art} {nummer}"}, date(2026, 10, 9), ordner)
    assert rechnungen.rechnung(db, storno)["pdf_pfad"] == "rechnungen/2026/Stornorechnung 2026-002.pdf"
    # Unsichere Zeichen werden ersetzt
    assert rechnungen.dateiname({"dateiname": "{kunde}_{nummer}"}, "A/1", 'X: "Y"', date(2026, 1, 1)) == "X_ _Y_A_1"


def test_gleicher_name_wird_nie_ueberschrieben(ordner):
    erster, _ = rechnungen._ablegen(ordner, "1", 2026, b"%PDF eins", "Rechnung.pdf", eindeutig=True)
    zweiter, _ = rechnungen._ablegen(ordner, "1", 2026, b"%PDF zwei", "Rechnung.pdf", eindeutig=True)
    assert (erster, zweiter) == ("rechnungen/2026/Rechnung.pdf", "rechnungen/2026/Rechnung (2).pdf")
    assert (ordner / erster).read_bytes() == b"%PDF eins"


@pytest.mark.parametrize("muster, meldung", [("Rechnung_{kunde}", "braucht \\{nummer\\}"),
                                             ("{nummer}_{betrag}", "Unbekannter Platzhalter")])
def test_dateiname_pruefen(muster, meldung):
    with pytest.raises(Fehler, match=meldung):
        rechnungen.dateiname_pruefen(muster)


def test_einstellungen_und_download(app, client):
    csrf = schluessel(client)
    seite = client.get("/einstellungen").get_data(as_text=True)
    assert "Zahlungsziel (Tage)" in seite and "Dateiname der gespeicherten Rechnungen" in seite
    assert "Bundesland" not in seite and "Werktag" not in seite
    formular = {"csrf": csrf, "startnummer": "1", "zahlungsziel": "14", "nummer_format": "JJJJ-NNN",
                "nummer_neu_ab": "jahr", "schrift": "noto", "dateiname": "Rechnung_{nummer}"}
    antwort = client.post("/einstellungen", data=formular, follow_redirects=True).get_data(as_text=True)
    assert "Einstellungen gespeichert" in antwort and "Zahlungsziel (Tage)" in antwort
    einst = dbmod.einstellungen(dbmod.verbinden(app.config["ORDNER"]))
    assert einst["dateiname"] == "Rechnung_{nummer}"
    assert "Rechnung_" in antwort and ".pdf</strong>" in antwort  # Beispiel-Dateiname
    falsch = client.post("/einstellungen", data={**formular, "dateiname": "{kunde}"}, follow_redirects=True)
    assert "braucht {nummer}" in falsch.get_data(as_text=True)
    # Beim Herunterladen heißt die Datei so wie gespeichert
    db = dbmod.verbinden(app.config["ORDNER"])
    r = db.execute("SELECT id, pdf_pfad FROM rechnungen WHERE pdf_pfad IS NOT NULL LIMIT 1").fetchone()
    kopf = client.get(f"/rechnungen/{r['id']}/pdf").headers["Content-Disposition"]
    assert r["pdf_pfad"].rsplit("/", 1)[-1] in kopf
