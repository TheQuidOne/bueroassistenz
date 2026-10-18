"""Mehrere alte Rechnungen auf einmal auslesen; unbekannte Kunden aus dem PDF vorschlagen und anlegen."""
import io
import os
from dataclasses import replace

import pytest

from arbeitsassistent import nachtrag, pdf, postfach, rechnungen, zugferd
from arbeitsassistent import db as dbmod
from arbeitsassistent.modell import Fehler
from tests.test_nachtrag_gruss import ABSENDER, ALT, KUNDEN, FalscheKi
from tests.test_web import app, client, schluessel  # noqa: F401 (Fixtures der Weboberfläche)

NEU = "Landratsamt Beispielkreis\nAm Markt 3\n54321 Beispielstadt"


def e_rechnung(nummer: str, empfaenger: str = NEU) -> bytes:
    r = replace(ALT, nummer=nummer, empfaenger=empfaenger)
    return zugferd.einbetten(pdf.rechnung_pdf(r, ABSENDER), r, ABSENDER, "steuerfrei")


def test_anschrift_aus_e_rechnung():
    v = nachtrag.vorschlag(e_rechnung("RE-2025/150"), FalscheKi({}, bereit=False), KUNDEN)
    assert (v["kunde_id"], v["kunde_name"], v["kunde_anschrift"]) == (
        None, "Landratsamt Beispielkreis", "Am Markt 3\n54321 Beispielstadt")


def test_anschrift_aus_text_nur_was_dort_steht():
    ki = FalscheKi({"rechnungsnummer": "RE-2025/141", "rechnungsdatum": "2025-12-03", "gesamtbetrag": "297.50",
                    "empfaenger": "Amtsgericht Musterstadt",
                    "empfaenger_anschrift": "Gerichtsplatz 1\nErfundene Straße 9\n12345 Musterstadt"})
    v = nachtrag.vorschlag(pdf.rechnung_pdf(ALT, ABSENDER), ki, [])
    assert (v["kunde_name"], v["kunde_anschrift"]) == ("Amtsgericht Musterstadt", "Gerichtsplatz 1\n12345 Musterstadt")
    erfunden = FalscheKi({**ki.antwort, "empfaenger": "Gibt Es Nicht GmbH"})
    assert nachtrag.vorschlag(pdf.rechnung_pdf(ALT, ABSENDER), erfunden, [])["kunde_name"] == ""


def test_formularwerte_gleichen_kunden_neu_ab():
    v = {"nummer": "1", "kunde_id": 99, "kunde_name": "Klinikum Musterstadt", "kunde_anschrift": "Weg 1"}
    assert nachtrag.formularwerte(v, KUNDEN)["kunde_id"] == 2  # 99 gibt es nicht mehr, Name passt zu 2
    neu = nachtrag.formularwerte({**v, "kunde_name": "Landratsamt Beispielkreis"}, KUNDEN)
    assert (neu["kunde_id"], neu["neuer_kunde_name"], neu["neuer_kunde_anschrift"]) == (
        "neu", "Landratsamt Beispielkreis", "Weg 1")
    assert nachtrag.formularwerte({"nummer": "1"}, KUNDEN)["kunde_id"] == ""


@pytest.mark.parametrize("name, text, erwartet", [
    ("Landgericht Musterstadt", "", None),  # teilt nur die Stadt mit dem Amtsgericht: lieber neu
    ("Integrationsamt Musterland", "", None),
    ("Amtsgericht Musterstadt", "", 1),
    ("AMTSGERICHT MUSTERSTADT - Verwaltung", "", 1),  # alle Wörter des Kunden enthalten
    ("Amtsgericht", "An das Amtsgericht Musterstadt", 1),  # unvollständig gelesen, voller Name im Text
    ("Landgericht Musterstadt", "Az. beim Amtsgericht Musterstadt", None),
    ("", "An das Klinikum Musterstadt", 2),
])
def test_kunde_lieber_keiner_als_der_falsche(name, text, erwartet):
    assert nachtrag._kunde(name, text, KUNDEN) == erwartet


def test_nachtragen_legt_neuen_kunden_einmal_an(db, einst, ordner):
    neu = {"kunde_id": "neu", "neuer_kunde_name": "Landratsamt  Beispielkreis",
           "neuer_kunde_anschrift": "Am Markt 3\n54321 Beispielstadt", "datum": "2025-12-03", "betrag": "100"}
    with pytest.raises(Fehler, match="Gesamtbetrag"):
        rechnungen.nachtragen(db, {**neu, "nummer": "A-0", "betrag": ""}, einst, ordner)
    assert rechnungen.kunde_nach_name(db, "Landratsamt Beispielkreis") is None  # kein halber Kunde
    with pytest.raises(Fehler, match="Namen des neuen Kunden"):
        rechnungen.nachtragen(db, {**neu, "nummer": "A-0", "neuer_kunde_name": " "}, einst, ordner)
    a = rechnungen.nachtragen(db, {**neu, "nummer": "A-1"}, einst, ordner)
    b = rechnungen.nachtragen(db, {**neu, "nummer": "A-2", "neuer_kunde_name": "landratsamt beispielkreis."},
                              einst, ordner)
    kid = rechnungen.rechnung(db, a)["kunde_id"]
    assert rechnungen.rechnung(db, b)["kunde_id"] == kid
    k = rechnungen.kunde(db, kid)
    assert (k["name"], k["anschrift"]) == ("Landratsamt Beispielkreis", "Am Markt 3\n54321 Beispielstadt")
    assert "Am Markt 3" in rechnungen.rechnung(db, a)["empfaenger"]


def test_mehrere_pdfs_auf_einmal(app, client, monkeypatch):
    csrf = schluessel(client)
    ordner = app.config["ORDNER"]
    monkeypatch.setattr(postfach, "ki_fuer", lambda einst: FalscheKi({}, bereit=False))
    monkeypatch.setattr(nachtrag, "stapel_anstossen", nachtrag.stapel_abarbeiten)  # ohne Hintergrund-Thread
    dateien = [(io.BytesIO(e_rechnung("LRA-1")), "eins.pdf"), (io.BytesIO(e_rechnung("LRA-2")), "zwei.pdf"),
               (io.BytesIO(pdf.rechnung_pdf(replace(ALT, nummer="ALT-9"), ABSENDER)), "ohne-ki.pdf"),
               (io.BytesIO(b"kein pdf"), "notiz.txt")]
    seite = client.post("/rechnungen/nachtragen/auslesen", data={"csrf": csrf, "dateien": dateien},
                        content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "3 PDFs werden ausgelesen" in seite and "1 Datei(en) waren keine PDFs" in seite
    assert seite.count('name="auswahl"') == 2 and "Landratsamt Beispielkreis" in seite and ">neu<" in seite
    assert "Alle 3 gelesen" in seite and client.get("/rechnungen/nachtragen/stand").get_json() == {
        "offen": 0, "fertig": 3}

    db = dbmod.verbinden(ordner)
    eintraege = {e["dateiname"]: e for e in nachtrag.stapel(db)}
    eins, zwei, ohne = eintraege["eins.pdf"], eintraege["zwei.pdf"], eintraege["ohne-ki.pdf"]
    assert not nachtrag.vollstaendig(ohne) and "KI nicht bereit" in ohne["vorschlag"]["hinweis"]
    antwort = client.post("/rechnungen/nachtragen/liste/uebernehmen", data={
        "csrf": csrf, "auswahl": [eins["id"], zwei["id"], ohne["id"]], f"bezahlt_am_{eins['id']}": "2025-12-15"},
        follow_redirects=True).get_data(as_text=True)
    assert "2 Rechnungen sind nachgetragen: LRA-1, LRA-2" in antwort

    kunden = db.execute("SELECT id, anschrift FROM kunden WHERE name = 'Landratsamt Beispielkreis'").fetchall()
    assert len(kunden) == 1 and kunden[0]["anschrift"] == "Am Markt 3\n54321 Beispielstadt"
    r1 = db.execute("SELECT * FROM rechnungen WHERE nummer = 'LRA-1'").fetchone()
    r2 = db.execute("SELECT * FROM rechnungen WHERE nummer = 'LRA-2'").fetchone()
    assert r1["kunde_id"] == r2["kunde_id"] == kunden[0]["id"] and r1["nachgetragen"] == r2["nachgetragen"] == 1
    assert db.execute("SELECT datum FROM zahlungen WHERE rechnung_id = ?", (r1["id"],)).fetchone()[0] == "2025-12-15"
    assert not db.execute("SELECT 1 FROM zahlungen WHERE rechnung_id = ?", (r2["id"],)).fetchone()
    assert (ordner / r1["pdf_pfad"]).read_bytes().startswith(b"%PDF")
    assert not nachtrag.zwischen_pfad(ordner, eins["schluessel"]).exists()

    # Die übrige Rechnung einzeln prüfen: Formular mit Original, dann mit neuem Kunden speichern
    formular = client.get(f"/rechnungen/nachtragen/liste/{ohne['id']}").get_data(as_text=True)
    assert f'value="{ohne["schluessel"]}"' in formular and "ohne-ki.pdf" in formular
    gespeichert = client.post("/rechnungen/nachtragen", data={
        "csrf": csrf, "schluessel": ohne["schluessel"], "dateiname": "ohne-ki.pdf", "kunde_id": "neu",
        "neuer_kunde_name": "Sozialamt Musterstadt", "neuer_kunde_anschrift": "", "nummer": "ALT-9",
        "datum": "2025-12-03", "betrag": "297,50", "bezahlt": "nein"}, follow_redirects=True)
    assert "Der Kunde „Sozialamt Musterstadt“ steht jetzt in Ihrer Kundenliste" in gespeichert.get_data(as_text=True)
    assert nachtrag.stapel(db) == [] and not nachtrag.zwischen_pfad(ordner, ohne["schluessel"]).exists()


def test_liste_entfernen_leeren_und_aufraeumen(app, client, monkeypatch):
    csrf = schluessel(client)
    ordner = app.config["ORDNER"]
    monkeypatch.setattr(nachtrag, "stapel_anstossen", lambda *a: None)  # bleibt „wartet“
    client.post("/rechnungen/nachtragen/auslesen", content_type="multipart/form-data", data={
        "csrf": csrf, "dateien": [(io.BytesIO(e_rechnung("X-1")), "a.pdf"), (io.BytesIO(e_rechnung("X-2")), "b.pdf")]})
    db = dbmod.verbinden(ordner)
    a, b = nachtrag.stapel(db)
    assert client.get("/rechnungen/nachtragen").get_data(as_text=True).count("Wartet …") == 2
    assert client.get(f"/rechnungen/nachtragen/liste/{a['id']}").status_code == 302  # noch nicht gelesen

    # Alte, verwaiste Zwischendateien verschwinden; Dateien aus der Liste bleiben
    verwaist = ordner / nachtrag.ZWISCHENABLAGE / f"{'0' * 32}.pdf"
    verwaist.write_bytes(b"%PDF alt")
    for datei in (verwaist, nachtrag.zwischen_pfad(ordner, a["schluessel"])):
        os.utime(datei, (0, 0))
    client.get("/rechnungen/nachtragen")
    assert not verwaist.exists() and nachtrag.zwischen_pfad(ordner, a["schluessel"]).exists()

    client.post(f"/rechnungen/nachtragen/liste/{a['id']}/entfernen", data={"csrf": csrf})
    assert [e["id"] for e in nachtrag.stapel(db)] == [b["id"]]
    assert not nachtrag.zwischen_pfad(ordner, a["schluessel"]).exists()
    client.post("/rechnungen/nachtragen/liste/leeren", data={"csrf": csrf})
    assert nachtrag.stapel(db) == [] and not nachtrag.zwischen_pfad(ordner, b["schluessel"]).exists()


def test_abarbeiten_ueberlebt_kaputte_datei(app):
    ordner = app.config["ORDNER"]
    db = dbmod.verbinden(ordner)
    nachtrag.stapel_anlegen(db, ordner, [("kaputt.pdf", b"%PDF-1.7 abgeschnitten"), ("gut.pdf", e_rechnung("G-1"))])
    nachtrag.stapel_abarbeiten(ordner, lambda einst: FalscheKi({}, bereit=False))
    kaputt, gut = nachtrag.stapel(db)
    assert kaputt["status"] == "fehler" and "nicht als PDF lesen" in kaputt["vorschlag"]["hinweis"]
    assert gut["status"] == "gelesen" and nachtrag.vollstaendig(gut)
