"""Einnahmen-Übersicht im Reiter Konto und wählbare Schrift der Rechnungen."""
from datetime import date

import pytest

from arbeitsassistent import auswertung, pdf, rechnungen
from arbeitsassistent import db as dbmod
from tests.test_web import app, client, schluessel  # noqa: F401 (Fixtures der Weboberfläche)


def bezahlte_rechnung(db, einst, kunde, ordner, ausgestellt: date, bezahlt: date, preis: str, betrag: int) -> int:
    rid = rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
        {"datum": ausgestellt.isoformat(), "beschreibung": "Dolmetscheinsatz DGS", "menge": "1",
         "einzelpreis": preis}]}, einst)
    rechnungen.ausstellen(db, rid, einst, ausgestellt, ordner)
    if betrag:
        rechnungen.zahlung_erfassen(db, rid, betrag, bezahlt)
    return rid


def test_einnahmen_im_jahr(db, einst, kunde, ordner):
    zweiter = rechnungen.kunde_speichern(db, {"name": "Klinikum Musterstadt", "anschrift": "Weg 1\n12345 Ort"})
    bezahlte_rechnung(db, einst, kunde, ordner, date(2025, 3, 2), date(2025, 3, 20), "100,00", 10000)
    bezahlte_rechnung(db, einst, kunde, ordner, date(2025, 11, 2), date(2025, 11, 20), "900,00", 90000)
    bezahlte_rechnung(db, einst, kunde, ordner, date(2026, 1, 5), date(2026, 1, 25), "300,00", 30000)
    bezahlte_rechnung(db, einst, zweiter, ordner, date(2026, 3, 1), date(2026, 3, 10), "100,00", 10000)
    bezahlte_rechnung(db, einst, zweiter, ordner, date(2026, 4, 1), date(2026, 4, 2), "50,00", 0)  # offen
    db.execute("INSERT INTO umsaetze (fingerabdruck, datum, betrag, gegenpartei, zweck, kategorie, import_id) "
               "VALUES ('a', '2026-02-14', 5000, 'Verlag', 'Honorar Artikel', 'einnahme', NULL)")
    db.execute("INSERT INTO umsaetze (fingerabdruck, datum, betrag, gegenpartei, zweck, kategorie, import_id) "
               "VALUES ('b', '2026-04-03', 2500, 'Unbekannt', 'Überweisung', '', NULL)")

    e = auswertung.einnahmen(db, 2026, heute=date(2026, 4, 15))
    assert e["gesamt"] == 30000 + 10000 + 5000 and e["aus_rechnungen"] == 40000 and e["sonstige"] == 5000
    assert e["monate"][:4] == [30000, 5000, 10000, 0] and e["bester_monat"] == 0
    assert (e["monate_bisher"], e["durchschnitt"]) == (4, 45000 // 4)
    # Vorjahr nur bis zum 15. April 2025: 100 €, nicht die 900 € aus November
    assert (e["vorjahr"], e["vergleich"]) == (10000, 350)
    assert [(k["name"], k["betrag"], k["anteil"]) for k in e["kunden"]] == [
        ("Landschaftsverband Musterland", 30000, 67), ("Klinikum Musterstadt", 10000, 22),
        ("Sonstige betriebliche Einnahmen", 5000, 11)]
    assert (e["offen"], e["offen_anzahl"]) == (5000, 1)
    assert (e["nicht_eingeordnet"], e["nicht_eingeordnet_betrag"]) == (1, 2500)

    abgeschlossen = auswertung.einnahmen(db, 2025, heute=date(2026, 4, 15))
    assert (abgeschlossen["gesamt"], abgeschlossen["monate_bisher"], abgeschlossen["vergleich"]) == (100000, 12, None)


def test_konto_und_detailseite(client):
    schluessel(client)
    konto = client.get("/bank").get_data(as_text=True)
    assert f"Eingenommen {date.today().year}" in konto and "Einnahmen im Detail" in konto
    assert 'class="balken-monate' in konto
    seite = client.get("/bank/einnahmen").get_data(as_text=True)
    assert "Je Kunde" in seite and "Durchschnitt pro Monat" in seite and "Noch offen" in seite
    vorjahr = client.get(f"/bank/einnahmen?jahr={date.today().year - 1}").get_data(as_text=True)
    assert f"Einnahmen {date.today().year - 1}" in vorjahr


# --- Schrift ---------------------------------------------------------------------


def test_arial_fehlt_dann_noto(monkeypatch, db, einst, kunde, ordner):
    monkeypatch.setattr(pdf, "_ARIAL", [])
    assert pdf.schriftdateien("arial") is None
    rid = bezahlte_rechnung(db, einst, kunde, ordner, date(2026, 10, 1), date(2026, 10, 2), "85,00", 0)
    daten = rechnungen.vorschau_pdf(db, rid, {**einst, "schrift": "arial"}, ordner)
    assert b"NotoSans" in daten


@pytest.mark.skipif(pdf.schriftdateien("arial") is None, reason="weder Arial noch Liberation Sans installiert")
def test_arial_im_pdf(db, einst, kunde, ordner):
    rid = bezahlte_rechnung(db, einst, kunde, ordner, date(2026, 10, 1), date(2026, 10, 2), "85,00", 0)
    daten = rechnungen.vorschau_pdf(db, rid, {**einst, "schrift": "arial"}, ordner)
    assert b"Arial" in daten or b"LiberationSans" in daten
    assert b"NotoSans-Regular" not in daten


def test_schrift_in_einstellungen(app, client):
    csrf = schluessel(client)
    seite = client.get("/einstellungen").get_data(as_text=True)
    assert "Schrift auf Rechnungen" in seite and "Probe ansehen" in seite
    formular = {"csrf": csrf, "startnummer": "1", "zahlungsziel": "14", "nummer_format": "JJJJ-NNN",
                "nummer_neu_ab": "jahr", "schrift": "arial"}
    assert "Einstellungen gespeichert" in client.post("/einstellungen", data=formular,
                                                       follow_redirects=True).get_data(as_text=True)
    assert dbmod.einstellungen(dbmod.verbinden(app.config["ORDNER"]))["schrift"] == "arial"
    falsch = client.post("/einstellungen", data={**formular, "schrift": "comic"}, follow_redirects=True)
    assert "Unbekannte Schrift" in falsch.get_data(as_text=True)
    probe = client.get("/einstellungen/schriftprobe.pdf?schrift=noto")
    assert probe.status_code == 200 and probe.data.startswith(b"%PDF")
    assert client.get("/einstellungen/schriftprobe.pdf?schrift=comic").status_code == 404
