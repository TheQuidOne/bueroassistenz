import io
import json
from datetime import date

import pytest

from arbeitsassistent import db as dbmod
from arbeitsassistent import demo, rechnungen, web


@pytest.fixture
def app(ordner):
    demo.fuellen(ordner)
    anwendung = web.erstellen(ordner)
    anwendung.testing = True
    return anwendung


@pytest.fixture
def client(app):
    return app.test_client()


def schluessel(client) -> str:
    client.get("/")
    with client.session_transaction() as sitzung:
        return sitzung["csrf"]


def test_demo_zuordnungen(ordner):
    demo.fuellen(ordner, date(2026, 10, 4))
    db = dbmod.verbinden(ordner)
    nummern = {z["nummer"]: z["id"] for z in db.execute("SELECT id, nummer FROM rechnungen WHERE nummer IS NOT NULL")}
    bezahlt = {n for n, rid in nummern.items() if rechnungen.offen(db, rid) == 0}
    # Nummer + Betrag (einzeln und als Sammelüberweisung) werden ohne Nachfrage gebucht.
    assert bezahlt == {"2026-001", "2026-002", "2026-003"}
    # Ohne Nummer gibt es nur einen Vorschlag.
    assert db.execute("SELECT COUNT(*) FROM vorschlaege WHERE abgelehnt = 0").fetchone()[0] == 1
    assert demo.fuellen(ordner) is False  # zweiter Aufruf ändert nichts


@pytest.mark.parametrize("pfad", [
    "/", "/rechnungen", "/rechnungen?filter=offen", "/rechnungen?filter=ueberfaellig", "/rechnungen?filter=entwurf",
    "/rechnungen/1", "/rechnungen/1/pdf", "/rechnungen/neu", "/bank", "/bank?filter=alle", "/bank?filter=ausgaben",
    "/kalender", "/kalender?monat=2026-02", "/kalender?monat=quatsch", "/jahr", "/kunden", "/kunden/1",
    "/kunden/neu", "/einstellungen", "/gesund", "/hilfe",
])
def test_seiten(client, pfad):
    antwort = client.get(pfad)
    assert antwort.status_code == 200, pfad


def test_uebersicht_zeigt_aufgaben_und_tour_marken(client):
    seite = client.get("/").get_data(as_text=True)
    assert "Zahlungseingang prüfen" in seite and "überfällig" in seite
    for marke in ("neue-rechnung", "nav-konto", "aufgaben", "vorschlag-stimmt", "erinnerung", "kpi-offen"):
        assert f'data-tour="{marke}"' in seite, marke
    assert "In wenigen Schritten startklar" not in seite  # Demo ist fertig eingerichtet


def test_ki_ist_auf_der_uebersicht_zu_sehen(client):
    seite = client.get("/").get_data(as_text=True)
    assert "Lokale KI" in seite and "7 Mails gelesen, davon 5 zur Arbeit" in seite
    assert seite.count('class="pille ki"') == 3  # abrechnen, Rückfrage, Absage (Zukunft und Zahlung nicht)


def test_einrichtung_bei_leeren_daten(tmp_path):
    anwendung = web.erstellen(tmp_path)
    seite = anwendung.test_client().get("/").get_data(as_text=True)
    assert "In wenigen Schritten startklar" in seite and "0 von 5 erledigt" in seite


def test_einordnen_und_merken(client, app):
    csrf = schluessel(client)
    with app.app_context():
        db = dbmod.verbinden(app.config["ORDNER"])
        telefon = db.execute("SELECT id FROM umsaetze WHERE gegenpartei LIKE 'Telefonica%' ORDER BY datum").fetchall()
        eingang = db.execute("SELECT u.id, v.zahlungen FROM vorschlaege v JOIN umsaetze u ON u.id = v.umsatz_id").fetchone()
        rechnung = {"id": json.loads(eingang["zahlungen"])[0][0]}
    antwort = client.post(f"/bank/{telefon[0]['id']}/einordnen", data={"csrf": csrf, "wahl": "telefon"},
                          follow_redirects=True)
    assert "Immer so einordnen" in antwort.get_data(as_text=True)
    antwort = client.post(f"/bank/{telefon[0]['id']}/merken", data={"csrf": csrf}, follow_redirects=True)
    assert "1 weitere Buchungen gleich eingeordnet" in antwort.get_data(as_text=True)
    antwort = client.post(f"/bank/{eingang['id']}/einordnen", data={"csrf": csrf, "wahl": f"rechnung:{rechnung['id']}"},
                          follow_redirects=True)
    assert "Zahlung der Rechnung zugeordnet" in antwort.get_data(as_text=True)
    with app.app_context():
        db = dbmod.verbinden(app.config["ORDNER"])
        assert {z["kategorie"] for z in db.execute("SELECT kategorie FROM umsaetze WHERE gegenpartei LIKE 'Telefonica%'")} == {"telefon"}
        assert rechnungen.offen(db, rechnung["id"]) == 0
    antwort = client.post(f"/bank/{telefon[0]['id']}/einordnen", data={"csrf": csrf, "wahl": "rechnung:quatsch"},
                          follow_redirects=True)
    assert "meldung fehler" in antwort.get_data(as_text=True)


def test_entwurf_seiten(client, app):
    with app.app_context():
        db = dbmod.verbinden(app.config["ORDNER"])
        entwurf = db.execute("SELECT id FROM rechnungen WHERE status = 'entwurf'").fetchone()["id"]
        jahr = db.execute("SELECT substr(MAX(datum), 1, 4) FROM rechnungen").fetchone()[0]
    for pfad in (f"/rechnungen/{entwurf}", f"/rechnungen/{entwurf}/bearbeiten", f"/rechnungen/{entwurf}/pdf"):
        assert client.get(pfad).status_code == 200, pfad
    csv = client.get(f"/jahr/{jahr}.csv")
    assert csv.status_code == 200 and "Integrationsamt Musterland" in csv.get_data(as_text=True)


def test_fremder_host_und_ohne_schluessel(client):
    assert client.get("/", headers={"Host": "boese.example"}).status_code == 400
    assert client.post("/kunden/neu", data={"name": "X"}).status_code == 400
    assert client.post("/kunden/neu", data={"name": "X", "csrf": "falsch"}).status_code == 400


def test_rechnung_ueber_formular(client, app):
    csrf = schluessel(client)
    antwort = client.post("/kunden/neu", data={"csrf": csrf, "name": "Erika Mustermann", "anschrift": "Weg 3\n12345 Ort"})
    assert antwort.status_code == 302
    with app.app_context():
        db = dbmod.verbinden(app.config["ORDNER"])
        kunde = db.execute("SELECT id FROM kunden WHERE name = 'Erika Mustermann'").fetchone()["id"]
    antwort = client.post("/rechnungen/neu", data={
        "csrf": csrf, "kunde_id": kunde, "steuerfall": "ust19", "zahlungsziel": "7",
        "pos_datum": ["2026-10-01", ""], "pos_beschreibung": ["Dolmetscheinsatz", ""],
        "pos_menge": ["2", ""], "pos_einheit": ["Std.", ""], "pos_preis": ["85,00", ""],
    })
    assert antwort.status_code == 302
    rid = int(antwort.headers["Location"].rstrip("/").split("/")[-1])
    assert client.post(f"/rechnungen/{rid}/ausstellen", data={"csrf": csrf}).status_code == 302
    with app.app_context():
        r = rechnungen.rechnung(dbmod.verbinden(app.config["ORDNER"]), rid)
    assert r["status"] == "ausgestellt" and r["brutto"] == 20230
    pdf = client.get(f"/rechnungen/{rid}/pdf")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")


def test_formularfehler_bleiben_stehen(client):
    csrf = schluessel(client)
    antwort = client.post("/rechnungen/neu", data={
        "csrf": csrf, "kunde_id": "1", "pos_datum": [""], "pos_beschreibung": ["Einsatz"],
        "pos_menge": ["zwei"], "pos_einheit": [""], "pos_preis": ["85"],
    })
    seite = antwort.get_data(as_text=True)
    assert antwort.status_code == 200 and "keine Menge" in seite and 'value="Einsatz"' in seite


def test_csv_import_ueber_seite(client, app):
    csrf = schluessel(client)
    kaputt = client.post("/bank/import", data={"csrf": csrf, "datei": (io.BytesIO(b"nix;da\n1;2\n"), "x.csv")},
                         content_type="multipart/form-data", follow_redirects=True)
    assert kaputt.status_code == 200 and "meldung fehler" in kaputt.get_data(as_text=True)
    with app.app_context():
        db = dbmod.verbinden(app.config["ORDNER"])
        vorher = db.execute("SELECT COUNT(*) FROM umsaetze").fetchone()[0]
        datei = sorted((app.config["ORDNER"] / "importe").iterdir())[0].read_bytes()
    nochmal = client.post("/bank/import", data={"csrf": csrf, "datei": (io.BytesIO(datei), "gleich.csv")},
                          content_type="multipart/form-data", follow_redirects=True)
    assert "0 neue Umsätze" in nochmal.get_data(as_text=True)
    with app.app_context():
        assert dbmod.verbinden(app.config["ORDNER"]).execute("SELECT COUNT(*) FROM umsaetze").fetchone()[0] == vorher


def test_post_seiten_und_hinweise(client, app):
    seite = client.get("/post").get_data(as_text=True)
    assert "Bestätigung Dolmetscheinsatz" in seite and "Newsletter" not in seite  # nur Arbeit
    assert "Newsletter" in client.get("/post?filter=alle").get_data(as_text=True)
    with app.app_context():
        db = dbmod.verbinden(app.config["ORDNER"])
        mail_id = db.execute("SELECT id FROM mails WHERE betreff LIKE 'Bestätigung%'").fetchone()["id"]
        hinweis = db.execute("SELECT * FROM hinweise WHERE art = 'abrechnen' AND status = 'offen' "
                             "AND datum <= date('now')").fetchone()
    detail = client.get(f"/post/{mail_id}").get_data(as_text=True)
    assert "Was die KI erkannt hat" in detail and "Station 4" in detail
    start = client.get("/").get_data(as_text=True)
    assert "abrechnen: Klinikum" in start and 'data-tour="rechnung-vorbereiten"' in start
    assert "Rückfrage zu Rechnung" in start and "abgesagt" in start


def test_rechnung_aus_hinweis(client, app):
    csrf = schluessel(client)
    with app.app_context():
        db = dbmod.verbinden(app.config["ORDNER"])
        h = db.execute("SELECT * FROM hinweise WHERE art = 'abrechnen' AND datum <= date('now')").fetchone()
    formular = client.get(f"/rechnungen/neu?hinweis={h['id']}").get_data(as_text=True)
    assert f'value="{h["datum"]}"' in formular and 'value="1,5"' in formular and "Aus einer Mail vorbereitet" in formular
    antwort = client.post("/rechnungen/neu", data={
        "csrf": csrf, "kunde_id": h["kunde_id"], "hinweis_id": h["id"], "pos_datum": [h["datum"]],
        "pos_beschreibung": ["Dolmetscheinsatz DGS"], "pos_menge": ["1,5"], "pos_einheit": ["Std."],
        "pos_preis": ["85,00"],
    })
    assert antwort.status_code == 302
    with app.app_context():
        db = dbmod.verbinden(app.config["ORDNER"])
        assert db.execute("SELECT status FROM hinweise WHERE id = ?", (h["id"],)).fetchone()[0] == "erledigt"


def test_hinweis_ausblenden(client, app):
    csrf = schluessel(client)
    with app.app_context():
        hid = dbmod.verbinden(app.config["ORDNER"]).execute(
            "SELECT id FROM hinweise WHERE art = 'absage'").fetchone()["id"]
    assert client.post(f"/hinweise/{hid}/verworfen", data={"csrf": csrf}).status_code == 302
    assert client.post(f"/hinweise/{hid}/quatsch", data={"csrf": csrf}, follow_redirects=True).status_code == 200
    with app.app_context():
        status = dbmod.verbinden(app.config["ORDNER"]).execute(
            "SELECT status FROM hinweise WHERE id = ?", (hid,)).fetchone()[0]
    assert status == "verworfen"


def test_post_einstellungen_speichern(client, app, schluesselbund, monkeypatch):
    from arbeitsassistent import postfach
    geprueft = []
    monkeypatch.setattr(postfach, "verbindung_testen", lambda k, pw: geprueft.append((k, pw)) or ["INBOX"])
    monkeypatch.setattr(postfach, "anstossen", lambda ordner: None)
    csrf = schluessel(client)
    assert client.get("/einstellungen/post").status_code == 200
    antwort = client.post("/einstellungen/post", data={
        "csrf": csrf, "mail_anbieter": "gmx", "mail_host": "imap.gmx.net", "mail_port": "993",
        "mail_benutzer": "max@beispiel.example", "passwort": "geheim", "mail_ordner": "INBOX",
        "mail_seit_tage": "30", "mail_aktiv": "1"})
    assert antwort.status_code == 302 and geprueft[0][1] == "geheim"
    assert schluesselbund.get_password("Arbeits-Assistent", "max@beispiel.example") == "geheim"
    with app.app_context():
        einst = dbmod.einstellungen(dbmod.verbinden(app.config["ORDNER"]))
    assert einst["mail_host"] == "imap.gmx.net" and "geheim" not in json.dumps(einst)
    client.post("/einstellungen/post/abmelden", data={"csrf": csrf})
    assert schluesselbund.get_password("Arbeits-Assistent", "max@beispiel.example") is None


def test_rechnung_berichtigen_ueber_formular(client, app):
    db = dbmod.verbinden(app.config["ORDNER"])
    r = db.execute("SELECT * FROM rechnungen WHERE status = 'ausgestellt' AND art = 'rechnung' "
                   "ORDER BY id LIMIT 1").fetchone()
    assert "Rechnung berichtigen" in client.get(f"/rechnungen/{r['id']}").get_data(as_text=True)
    formular = client.get(f"/rechnungen/{r['id']}/berichtigen").get_data(as_text=True)
    assert f"Rechnung {r['nummer']} berichtigen" in formular and "Rechnungskorrektur ausstellen" in formular
    pos = rechnungen.positionen(db, r["id"])[0]
    csrf = schluessel(client)
    antwort = client.post(f"/rechnungen/{r['id']}/berichtigen", data={
        "csrf": csrf, "kunde_id": r["kunde_id"], "steuerfall": r["steuerfall"], "zahlungsziel": "14",
        "bezug": "Neues Aktenzeichen", "pos_datum": [pos.datum.isoformat() if pos.datum else "2026-10-01"],
        "pos_beschreibung": [pos.beschreibung], "pos_menge": ["1"], "pos_einheit": [pos.einheit],
        "pos_preis": ["99,00"],
    })
    assert antwort.status_code == 302
    neu_id = int(antwort.headers["Location"].rstrip("/").split("/")[-1])
    neu = rechnungen.rechnung(db, neu_id)
    assert neu["ersetzt"] == r["id"] and neu["status"] == "ausgestellt"
    assert rechnungen.rechnung(db, r["id"])["status"] == "berichtigt"
    neu_seite = client.get(f"/rechnungen/{neu_id}").get_data(as_text=True)
    assert f"Rechnungskorrektur {neu['nummer']}" in neu_seite and f"Rechnung {r['nummer']}</a>" in neu_seite
    alt_seite = client.get(f"/rechnungen/{r['id']}").get_data(as_text=True)
    assert "Berichtigt" in alt_seite and "Rechnung berichtigen" not in alt_seite
    nochmal = client.get(f"/rechnungen/{r['id']}/berichtigen", follow_redirects=True).get_data(as_text=True)
    assert "Nur ausgestellte Rechnungen" in nochmal


JVEG_TEXT = "Grundlage meines Honorars ist das JVEG in der Fassung vom 08.12.2025."


def test_einstellungen_ik_format_zusatztext(app, client):
    csrf = schluessel(client)
    seite = client.get("/einstellungen").get_data(as_text=True)
    assert "Die nächste Rechnung bekommt die Nummer" in seite
    formular = {"csrf": csrf, "startnummer": "147", "zahlungsziel": "14", "nummer_neu_ab": "nie",
                "nummer_format": "jjjj-mm-nnn", "ik": "12 345 678 9", "zusatztext": JVEG_TEXT}
    antwort = client.post("/einstellungen", data=formular, follow_redirects=True).get_data(as_text=True)
    assert "Einstellungen gespeichert" in antwort
    einst = dbmod.einstellungen(dbmod.verbinden(app.config["ORDNER"]))
    assert (einst["ik"], einst["nummer_format"], einst["zusatztext"]) == ("123456789", "JJJJ-MM-NNN", JVEG_TEXT)
    assert f"{date.today():%Y-%m}-147" in antwort  # Demo-Rechnungen haben das alte Format
    # Neue Rechnungen bekommen den Zusatztext vorausgefüllt
    assert JVEG_TEXT in client.get("/rechnungen/neu").get_data(as_text=True)
    falsch = client.post("/einstellungen", data={**formular, "ik": "1234"}, follow_redirects=True)
    assert "genau 9 Ziffern" in falsch.get_data(as_text=True)
    falsch = client.post("/einstellungen", data={**formular, "nummer_format": "NNN", "nummer_neu_ab": "jahr"},
                         follow_redirects=True)
    assert "Nummern doppelt" in falsch.get_data(as_text=True)


def test_leistung_aus_vorlage_mit_erlaeuterung(client, app):
    from io import BytesIO

    from pypdf import PdfReader
    db = dbmod.verbinden(app.config["ORDNER"])
    vorlage = rechnungen.vorlagen(db)[0]
    formular = client.get("/rechnungen/neu").get_data(as_text=True)
    assert "Leistung wählen …" in formular and "Andere Leistung (selbst eintragen)" in formular
    assert f'data-text="{vorlage["beschreibung"]}"' in formular and 'name="pos_erlaeuterung"' in formular
    csrf = schluessel(client)
    kunde = db.execute("SELECT id FROM kunden ORDER BY id LIMIT 1").fetchone()[0]
    antwort = client.post("/rechnungen/neu", data={
        "csrf": csrf, "kunde_id": kunde, "pos_datum": ["2026-10-07", "2026-10-07"],
        "pos_beschreibung": [vorlage["beschreibung"], "Vorbereitung"], "pos_menge": ["2", "1"],
        "pos_einheit": ["Std.", "pauschal"], "pos_preis": ["85,00", "40,00"],
        "pos_erlaeuterung": ["Sitzung des Ausschusses für Soziales\nRathaus, Saal 2", ""],
    })
    rid = int(antwort.headers["Location"].rstrip("/").split("/")[-1])
    pos = rechnungen.positionen(db, rid)
    assert [p.erlaeuterung for p in pos] == ["Sitzung des Ausschusses für Soziales\nRathaus, Saal 2", ""]
    # Beim Bearbeiten ist die Vorlage gewählt und das freie Textfeld versteckt
    bearbeiten = client.get(f"/rechnungen/{rid}/bearbeiten").get_data(as_text=True)
    import re
    assert re.search(rf'data-text="{re.escape(vorlage["beschreibung"])}"[^>]*selected', bearbeiten)
    assert re.search(rf'name="pos_beschreibung"[^>]*value="{re.escape(vorlage["beschreibung"])}"[^>]*hidden', bearbeiten)
    assert re.search(r'value="frei" selected', bearbeiten)  # „Vorbereitung“ ist keine Vorlage
    assert "Sitzung des Ausschusses für Soziales" in bearbeiten
    seite = client.get(f"/rechnungen/{rid}").get_data(as_text=True)
    assert "Sitzung des Ausschusses für Soziales" in seite
    text = PdfReader(BytesIO(client.get(f"/rechnungen/{rid}/pdf").data)).pages[0].extract_text()
    assert "Sitzung des Ausschusses für Soziales" in text and "Rathaus, Saal 2" in text


def test_unterschrift_hochladen_und_entfernen(client):
    from tests.test_unterschrift import foto
    csrf = schluessel(client)
    assert "Noch keine Unterschrift hinterlegt" in client.get("/einstellungen").get_data(as_text=True)
    antwort = client.post("/einstellungen/unterschrift", data={"csrf": csrf, "datei": (io.BytesIO(foto()), "scan.jpg")},
                          content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "Unterschrift gespeichert" in antwort and 'alt="Ihre hinterlegte Unterschrift"' in antwort
    assert client.get("/einstellungen/unterschrift.png").data.startswith(b"\x89PNG")
    kaputt = client.post("/einstellungen/unterschrift", data={"csrf": csrf, "datei": (io.BytesIO(b"x"), "a.jpg")},
                         content_type="multipart/form-data", follow_redirects=True)
    assert "nicht als Bild lesen" in kaputt.get_data(as_text=True)
    assert client.get("/einstellungen/unterschrift.png").status_code == 200  # die alte bleibt
    client.post("/einstellungen/unterschrift/entfernen", data={"csrf": csrf})
    assert client.get("/einstellungen/unterschrift.png").status_code == 404


def test_ki_fortschritt_sichtbar(client, app, monkeypatch):
    from arbeitsassistent import postfach
    db = dbmod.verbinden(app.config["ORDNER"])
    db.execute("INSERT INTO mails (message_id, datum, absender_name, betreff, text, abgerufen) "
               "VALUES ('<neu@beispiel.example>', '2026-10-10T08:00:00', 'Erika', 'Termin am Montag', 'x', "
               "'2026-10-10T08:01:00')")
    stand = client.get("/post/stand").get_json()
    assert stand["laeuft"] is False and stand["wartend"] == 1
    # Ruhig: keine Karte, aber die wartende Mail steht schon unter „Arbeit“
    seite = client.get("/post").get_data(as_text=True)
    assert 'id="ki-fortschritt"' in seite and 'id="ki-fortschritt" aria-live="polite" hidden' in seite
    assert "Termin am Montag" in seite and "wird gelesen" in seite
    # Während die KI liest: Karte, Puls in der Navigation, Zeile auf der Übersicht
    laufend = {"laeuft": True, "phase": "lesen", "nr": 3, "von": 12, "rest_sekunden": 180, "geschaetzt": False}
    monkeypatch.setattr(postfach, "fortschritt", lambda: laufend)
    seite = client.get("/post").get_data(as_text=True)
    assert "Die KI liest Mail 3 von 12" in seite and 'style="width: 17%"' in seite
    assert 'class="puls nav-puls"' in seite
    assert "liest gerade Mail 3 von 12" in client.get("/").get_data(as_text=True)
    assert client.get("/post/stand").get_json()["rest_sekunden"] == 180
