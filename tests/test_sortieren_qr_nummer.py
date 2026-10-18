"""Rechnungsliste sortieren, QR-Code an/aus, Rechnungsnummer beim Ausstellen frei wählen."""
import re
from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader

from arbeitsassistent import rechnungen
from arbeitsassistent import db as dbmod
from arbeitsassistent.modell import Fehler
from tests.test_web import app, client, schluessel  # noqa: F401 (Fixtures der Weboberfläche)

TAG = date(2026, 10, 9)


def text(daten: bytes) -> str:
    return " ".join(" ".join((s.extract_text() or "").split()) for s in PdfReader(BytesIO(daten)).pages)


def entwurf(db, einst, kunde, preis="85"):
    return rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
        {"datum": "2026-10-07", "beschreibung": "Dolmetscheinsatz DGS", "menge": "1", "einzelpreis": preis}]}, einst)


# --- Rechnungsnummer frei wählen -------------------------------------------------------


def test_nummer_beim_ausstellen_frei_waehlen(db, einst, kunde, ordner):
    a = entwurf(db, einst, kunde)
    assert rechnungen.ausstellen(db, a, einst, TAG, ordner, nummer="  RE 2026/77 ") == "RE 2026/77"
    r = rechnungen.rechnung(db, a)
    assert r["pdf_pfad"] == "rechnungen/2026/RE 2026_77.pdf" and "Rechnung Nr. RE 2026/77" in text(
        (ordner / r["pdf_pfad"]).read_bytes())
    b = entwurf(db, einst, kunde)
    with pytest.raises(Fehler, match="schon vergeben"):
        rechnungen.ausstellen(db, b, einst, TAG, ordner, nummer="RE 2026/77")
    with pytest.raises(Fehler, match="eintragen"):
        rechnungen.ausstellen(db, b, einst, TAG, ordner, nummer=" ")
    assert rechnungen.rechnung(db, b)["status"] == "entwurf"  # nichts halb ausgestellt
    # Ohne Angabe gilt weiter der Vorschlag; eine Lücke lässt sich von Hand füllen
    assert rechnungen.ausstellen(db, b, einst, TAG, ordner) == "2026-001"
    c = entwurf(db, einst, kunde)
    assert rechnungen.naechste_nummer(db, TAG, einst) == "2026-002"
    assert rechnungen.ausstellen(db, c, einst, TAG, ordner, nummer="2026-005") == "2026-005"
    assert rechnungen.naechste_nummer(db, TAG, einst) == "2026-006"


def test_nummer_in_der_oberflaeche(app, client):
    csrf = schluessel(client)
    db = dbmod.verbinden(app.config["ORDNER"])
    rid = db.execute("SELECT id FROM rechnungen WHERE status = 'entwurf' LIMIT 1").fetchone()[0]
    seite = client.get(f"/rechnungen/{rid}").get_data(as_text=True)
    vorschlag = re.search(r'id="nummer" name="nummer" value="([^"]+)"', seite).group(1)
    assert vorschlag == rechnungen.naechste_nummer(db, date.today(), dbmod.einstellungen(db))
    client.post(f"/rechnungen/{rid}/ausstellen", data={"csrf": csrf, "nummer": "2026-10-147"})
    assert rechnungen.rechnung(db, rid)["nummer"] == "2026-10-147"
    # Korrektur mit eigener Nummer
    seite = client.get(f"/rechnungen/{rid}/berichtigen").get_data(as_text=True)
    assert 'id="nummer" name="nummer"' in seite
    pos = rechnungen.positionen(db, rid)[0]
    antwort = client.post(f"/rechnungen/{rid}/berichtigen", data={
        "csrf": csrf, "kunde_id": rechnungen.rechnung(db, rid)["kunde_id"], "nummer": "2026-10-148",
        "pos_datum": [(pos.datum or date(2026, 10, 1)).isoformat()], "pos_beschreibung": [pos.beschreibung],
        "pos_menge": ["1"], "pos_einheit": [pos.einheit], "pos_preis": ["99,00"]})
    neu = int(antwort.headers["Location"].rstrip("/").split("/")[-1])
    assert rechnungen.rechnung(db, neu)["nummer"] == "2026-10-148"
    doppelt = client.post(f"/rechnungen/{neu}/berichtigen", data={
        "csrf": csrf, "kunde_id": rechnungen.rechnung(db, neu)["kunde_id"], "nummer": "2026-10-148",
        "pos_datum": ["2026-10-01"], "pos_beschreibung": ["X"], "pos_menge": ["1"], "pos_einheit": [""],
        "pos_preis": ["1"]}, follow_redirects=True).get_data(as_text=True)
    assert "schon vergeben" in doppelt


# --- Sortieren -------------------------------------------------------------------------------


def test_liste_sortieren(db, einst, kunde, ordner):
    zweiter = rechnungen.kunde_speichern(db, {"name": "Amtsgericht Musterstadt", "anschrift": "Weg 1\n12345 Ort"})
    for preis, nummer, k in (("300", "2026-10-1", kunde), ("50", "2026-9-2", zweiter), ("120", "2026-11-3", kunde)):
        rechnungen.ausstellen(db, entwurf(db, einst, k, preis), einst, TAG, ordner, nummer=nummer)
    rechnungen.zahlung_erfassen(db, db.execute("SELECT id FROM rechnungen WHERE nummer = '2026-9-2'").fetchone()[0],
                                5000, TAG)

    def reihe(spalte, ab=False):
        return [e["r"]["nummer"] for e in rechnungen.liste(db, sortierung=spalte, absteigend=ab, heute=TAG)]

    assert reihe("nummer") == ["2026-9-2", "2026-10-1", "2026-11-3"]  # nach Zahlen, nicht nach Zeichen
    assert reihe("betrag", ab=True) == ["2026-10-1", "2026-11-3", "2026-9-2"]
    assert reihe("betrag") == ["2026-9-2", "2026-11-3", "2026-10-1"]
    assert reihe("kunde")[0] == "2026-9-2"  # Amtsgericht vor Landschaftsverband
    assert reihe("status")[-1] == "2026-9-2"  # bezahlt nach offen


def test_sortieren_in_der_oberflaeche(client):
    schluessel(client)
    seite = client.get("/rechnungen?sort=betrag&richtung=auf").get_data(as_text=True)
    assert 'aria-sort="ascending"' in seite and "sort=betrag&amp;richtung=ab" in seite
    betraege = [int(b.replace(".", "").replace(",", "")) for b in
                re.findall(r"<td class=\"zahl\">([\d.]+,\d{2}) €", seite)]
    assert betraege == sorted(betraege) and len(betraege) > 3
    assert "sort=datum&amp;richtung=ab" in client.get("/rechnungen").get_data(as_text=True)


# --- QR-Code an/aus --------------------------------------------------------------------------


def test_qr_code_ausschalten(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    assert "Mit der Banking-App scannen" in text(rechnungen.vorschau_pdf(db, rid, einst, ordner))
    ohne = text(rechnungen.vorschau_pdf(db, rid, {**einst, "girocode": "0"}, ordner))
    assert "Mit der Banking-App scannen" not in ohne and "Bitte überweisen Sie" in ohne


def test_qr_code_in_den_einstellungen(app, client):
    csrf = schluessel(client)
    formular = {"csrf": csrf, "startnummer": "1", "zahlungsziel": "14", "nummer_format": "JJJJ-NNN",
                "nummer_neu_ab": "jahr", "schrift": "noto"}
    assert "Überweisungs-QR-Code auf Rechnungen drucken" in client.get("/einstellungen").get_data(as_text=True)
    client.post("/einstellungen", data={**formular, "girocode": "0"})  # Haken weg: nur das versteckte Feld
    assert dbmod.einstellungen(dbmod.verbinden(app.config["ORDNER"]))["girocode"] == "0"
    client.post("/einstellungen", data={**formular, "girocode": ["1", "0"]})  # Haken gesetzt
    assert dbmod.einstellungen(dbmod.verbinden(app.config["ORDNER"]))["girocode"] == "1"


# --- Kunden löschen ------------------------------------------------------------------------


def test_kunde_loeschen(db, einst, kunde, ordner):
    probe = rechnungen.kunde_speichern(db, {"name": "Testkunde", "anschrift": "Weg 1\n12345 Ort"})
    entwurf(db, einst, probe)
    assert rechnungen.kunde_loeschen(db, probe) == 1
    assert not db.execute("SELECT 1 FROM kunden WHERE id = ?", (probe,)).fetchone()
    rechnungen.ausstellen(db, entwurf(db, einst, kunde), einst, TAG, ordner)
    with pytest.raises(Fehler, match="ausgestellte Rechnungen"):
        rechnungen.kunde_loeschen(db, kunde)


def test_kunde_loeschen_in_der_oberflaeche(app, client):
    csrf = schluessel(client)
    client.post("/kunden/neu", data={"csrf": csrf, "name": "Nur zum Ausprobieren", "anschrift": "Weg 3\n12345 Ort"})
    db = dbmod.verbinden(app.config["ORDNER"])
    kid = db.execute("SELECT id FROM kunden WHERE name = 'Nur zum Ausprobieren'").fetchone()[0]
    assert "Kunden löschen" in client.get(f"/kunden/{kid}").get_data(as_text=True)
    antwort = client.post(f"/kunden/{kid}/loeschen", data={"csrf": csrf}, follow_redirects=True)
    assert "Nur zum Ausprobieren gelöscht" in antwort.get_data(as_text=True)
    mit_rechnungen = db.execute("SELECT kunde_id FROM rechnungen WHERE status = 'ausgestellt' LIMIT 1").fetchone()[0]
    assert "Löschen nicht möglich" in client.get(f"/kunden/{mit_rechnungen}").get_data(as_text=True)
