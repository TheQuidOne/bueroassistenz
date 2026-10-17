"""Grußformel und Schlusstext aus den Einstellungen; alte Rechnungen nachtragen und auslesen."""
import io
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader

from arbeitsassistent import auswertung, nachtrag, pdf, postfach, rechnungen, zugferd
from arbeitsassistent import db as dbmod
from arbeitsassistent.modell import Absender, Fehler, Position, RechnungsDruck
from tests.test_web import app, client, schluessel  # noqa: F401 (Fixtures der Weboberfläche)

TAG = date(2026, 10, 9)


def text(daten: bytes) -> str:
    return " ".join(" ".join((s.extract_text() or "").split()) for s in PdfReader(BytesIO(daten)).pages)


def entwurf(db, einst, kunde, **extra):
    return rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
        {"datum": "2026-10-07", "beschreibung": "Dolmetscheinsatz DGS", "menge": "2", "einzelpreis": "85"}],
        **extra}, einst)


# --- Grußformel und Schlusstext ----------------------------------------------------


def test_gruss_und_schluss_aus_einstellungen(db, einst, kunde, ordner):
    einst = {**einst, "schluss": "Vielen Dank für Ihr Vertrauen.", "grussformel": "Viele Grüße"}
    rid = entwurf(db, einst, kunde)
    inhalt = text(rechnungen.vorschau_pdf(db, rid, einst, ordner))
    assert f"Vielen Dank für Ihr Vertrauen. Viele Grüße {einst['name']}" in inhalt
    # Eigener Text der Rechnung geht vor, leere Grußformel heißt: keine
    eigen = entwurf(db, einst, kunde, schluss="Bis bald.")
    inhalt = text(rechnungen.vorschau_pdf(db, eigen, {**einst, "grussformel": ""}, ordner))
    assert f"Bis bald. {einst['name']}" in inhalt and "Grüße" not in inhalt


def test_erinnerung_mit_grussformel(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    _, daten = rechnungen.erinnerung(db, rid, {**einst, "grussformel": "Beste Grüße"}, date(2026, 11, 20), ordner)
    assert f"gegenstandslos. Beste Grüße {einst['name']}" in text(daten)


def test_gruss_in_den_einstellungen(app, client):
    csrf = schluessel(client)
    assert "Grußformel vor der Unterschrift" in client.get("/einstellungen").get_data(as_text=True)
    client.post("/einstellungen", data={"csrf": csrf, "startnummer": "1", "zahlungsziel": "14",
                                         "nummer_format": "JJJJ-NNN", "nummer_neu_ab": "jahr", "schrift": "noto",
                                         "schluss": "Vielen Dank für Ihr Vertrauen.", "grussformel": "Viele Grüße"})
    einst = dbmod.einstellungen(dbmod.verbinden(app.config["ORDNER"]))
    assert (einst["schluss"], einst["grussformel"]) == ("Vielen Dank für Ihr Vertrauen.", "Viele Grüße")
    assert 'placeholder="Vielen Dank für Ihr Vertrauen."' in client.get("/rechnungen/neu").get_data(as_text=True)


# --- Nachtragen ----------------------------------------------------------------------


def test_nachtragen_offen_und_bezahlt(db, einst, kunde, ordner):
    einst = {**einst, "nummer_format": "JJJJ-MM-NNN", "nummer_neu_ab": "nie", "startnummer": "1"}
    offen = rechnungen.nachtragen(db, {"kunde_id": kunde, "nummer": "2026-09-146", "datum": "2026-09-20",
                                       "betrag": "315,14", "leistung_von": "2026-09-18"}, einst, ordner,
                                  original=b"%PDF-1.4 altes Original")
    r = rechnungen.rechnung(db, offen)
    assert (r["status"], r["nachgetragen"], r["brutto"], r["faellig"]) == ("ausgestellt", 1, 31514, "2026-10-04")
    assert (ordner / r["pdf_pfad"]).read_bytes() == b"%PDF-1.4 altes Original"
    assert [o.nummer for o in rechnungen.offene_rechnungen(db)] == ["2026-09-146"]
    # Das Programm zählt nach der nachgetragenen Nummer weiter
    assert rechnungen.naechste_nummer(db, date(2026, 10, 9), einst) == "2026-10-147"

    ust = rechnungen.nachtragen(db, {"kunde_id": kunde, "nummer": "RE/2025/12", "datum": "2025-12-03",
                                     "betrag": "119,00", "steuerfall": "ust19", "bezahlt_am": "2025-12-20"},
                                einst, ordner)
    r = rechnungen.rechnung(db, ust)
    assert (r["netto"], r["ust"], rechnungen.offen(db, ust)) == (10000, 1900, 0)
    assert auswertung.jahr(db, 2025)["einnahmen"] == 11900  # zählt in der Jahresübersicht
    with pytest.raises(Fehler, match="gibt es schon"):
        rechnungen.nachtragen(db, {"kunde_id": kunde, "nummer": "RE/2025/12", "datum": "2025-12-03",
                                   "betrag": "1"}, einst, ordner)
    with pytest.raises(Fehler, match="Gesamtbetrag"):
        rechnungen.nachtragen(db, {"kunde_id": kunde, "nummer": "X-1", "datum": "2025-12-03", "betrag": ""},
                              einst, ordner)


def test_nachtrag_original_und_loeschen(db, einst, kunde, ordner):
    rid = rechnungen.nachtragen(db, {"kunde_id": kunde, "nummer": "RE/2025/13", "datum": "2025-12-10",
                                     "betrag": "85"}, einst, ordner)
    rechnungen.original_hinzufuegen(db, rid, b"%PDF-1.4 nachgereicht", ordner)
    pfad = ordner / rechnungen.rechnung(db, rid)["pdf_pfad"]
    assert pfad.name == "RE_2025_13_original.pdf" and pfad.is_file()
    with pytest.raises(Fehler, match="schon ein PDF"):
        rechnungen.original_hinzufuegen(db, rid, b"%PDF-1.4 noch eins", ordner)
    rechnungen.nachtrag_loeschen(db, rid, ordner)
    assert not pfad.exists() and not db.execute("SELECT 1 FROM rechnungen WHERE id = ?", (rid,)).fetchone()
    normal = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, normal, einst, TAG, ordner)
    with pytest.raises(Fehler, match="Nur nachgetragene"):
        rechnungen.nachtrag_loeschen(db, normal, ordner)


# --- Auslesen ------------------------------------------------------------------------


class FalscheKi:
    """Antwortet mit festen Werten, darunter erfundene, die die Prüfung verwerfen muss."""

    def __init__(self, antwort, bereit=True):
        self.antwort, self.bereit, self.bilder = antwort, bereit, None

    def status(self):
        return self.bereit, "KI nicht bereit."

    def chat_json(self, system, nutzer, schema, bilder=None):
        self.bilder = bilder
        return self.antwort


ABSENDER = Absender(name="Max Beispiel", strasse="Musterweg 12", plz_ort="12345 Musterstadt",
                    steuernummer="123/456/78901", iban="DE02120300000000202051")
ALT = RechnungsDruck(
    nummer="RE-2025/141", datum=date(2025, 12, 3), leistung_von=date(2025, 11, 27), leistung_bis=None,
    faellig=date(2025, 12, 17), empfaenger="Amtsgericht Musterstadt\nGerichtsplatz 1\n12345 Musterstadt",
    positionen=[Position("Dolmetscheinsatz DGS", Decimal("3.5"), "Std.", 8500, 29750, date(2025, 11, 27))],
    netto=29750, ust_satz=0, ust=0, brutto=29750, steuerhinweis="Umsatzsteuerfreie Leistung gemäß § 4 Nr. 16 UStG.")
KUNDEN = [{"id": 1, "name": "Amtsgericht Musterstadt"}, {"id": 2, "name": "Klinikum Musterstadt"}]


def test_auslesen_aus_e_rechnung_ohne_ki():
    roh = zugferd.einbetten(pdf.rechnung_pdf(ALT, ABSENDER), ALT, ABSENDER, "steuerfrei")
    v = nachtrag.vorschlag(roh, FalscheKi({}, bereit=False), KUNDEN)
    assert (v["quelle"], v["nummer"], v["datum"], v["betrag"], v["kunde_id"], v["steuerfall"]) == (
        "E-Rechnung", "RE-2025/141", "2025-12-03", "297,50", 1, "steuerfrei")


def test_auslesen_text_nur_was_im_dokument_steht():
    ki = FalscheKi({"rechnungsnummer": "RE-2025/141", "rechnungsdatum": "2025-12-03", "empfaenger": "Amtsgericht",
                    "gesamtbetrag": "297.50", "leistung_von": "2025-11-27", "leistung_bis": "",
                    "faellig": "2025-12-31",  # steht nicht im PDF: muss verworfen werden
                    "leistung": "Dolmetscheinsatz DGS", "ihr_zeichen": "", "steuerhinweis": ""})
    v = nachtrag.vorschlag(pdf.rechnung_pdf(ALT, ABSENDER), ki, KUNDEN, "Max Beispiel")
    assert (v["quelle"], v["nummer"], v["betrag"], v["leistung_von"], v["faellig"]) == (
        "Text", "RE-2025/141", "297,50", "2025-11-27", "")
    assert (v["kunde_id"], v["steuerfall"]) == (1, "steuerfrei")
    erfunden = FalscheKi({**ki.antwort, "rechnungsnummer": "RE-2025/999", "gesamtbetrag": "1000.00"})
    v = nachtrag.vorschlag(pdf.rechnung_pdf(ALT, ABSENDER), erfunden, KUNDEN)
    assert (v["nummer"], v["betrag"]) == ("", "")


def test_auslesen_scan_als_bild_und_ohne_ki():
    from fpdf import FPDF
    from PIL import Image
    seite = Image.new("RGB", (800, 1100), "white")
    puffer = io.BytesIO()
    seite.save(puffer, "PNG")
    scan = FPDF()
    scan.add_page()
    scan.image(io.BytesIO(puffer.getvalue()), x=0, y=0, w=210)
    roh = bytes(scan.output())
    ki = FalscheKi({"rechnungsnummer": "A-7", "rechnungsdatum": "2025-01-05", "empfaenger": "Klinikum Musterstadt",
                    "gesamtbetrag": "85.00", "leistung_von": "", "leistung_bis": "", "faellig": "",
                    "leistung": "", "ihr_zeichen": "", "steuerhinweis": "Gemäß § 19 UStG keine Umsatzsteuer"})
    v = nachtrag.vorschlag(roh, ki, KUNDEN)
    assert ki.bilder and ki.bilder[0].startswith(b"\x89PNG")
    assert (v["quelle"], v["nummer"], v["betrag"], v["kunde_id"], v["steuerfall"]) == (
        "Bild", "A-7", "85,00", 2, "kleinunternehmer")
    assert "nicht bereit" in nachtrag.vorschlag(roh, FalscheKi({}, bereit=False), KUNDEN)["hinweis"]


def test_nachtragen_ueber_oberflaeche(app, client, monkeypatch):
    csrf = schluessel(client)
    assert "Alte Rechnung nachtragen" in client.get("/rechnungen").get_data(as_text=True)
    roh = pdf.rechnung_pdf(ALT, ABSENDER)
    ki = FalscheKi({"rechnungsnummer": "RE-2025/141", "rechnungsdatum": "2025-12-03", "empfaenger": "",
                    "gesamtbetrag": "297.50", "leistung_von": "2025-11-27", "leistung_bis": "", "faellig": "",
                    "leistung": "Dolmetscheinsatz DGS", "ihr_zeichen": "", "steuerhinweis": ""})
    monkeypatch.setattr(postfach, "ki_fuer", lambda einst: ki)
    seite = client.post("/rechnungen/nachtragen/auslesen", data={"csrf": csrf, "datei": (io.BytesIO(roh), "alt.pdf")},
                        content_type="multipart/form-data").get_data(as_text=True)
    assert 'value="RE-2025/141"' in seite and 'value="297,50"' in seite and "Von der KI aus dem Text gelesen" in seite
    import re
    zwischen = re.search(r'name="schluessel" value="([0-9a-f]{32})"', seite).group(1)
    db = dbmod.verbinden(app.config["ORDNER"])
    kunde = db.execute("SELECT id FROM kunden ORDER BY id LIMIT 1").fetchone()[0]
    antwort = client.post("/rechnungen/nachtragen", data={
        "csrf": csrf, "schluessel": zwischen, "dateiname": "alt.pdf", "kunde_id": kunde, "nummer": "RE-2025/141",
        "datum": "2025-12-03", "betrag": "297,50", "steuerfall": "steuerfrei", "bezahlt": "nein"},
        content_type="multipart/form-data")
    assert antwort.status_code == 302
    rid = int(antwort.headers["Location"].rstrip("/").split("/")[-1])
    r = rechnungen.rechnung(db, rid)
    assert r["nachgetragen"] == 1 and (app.config["ORDNER"] / r["pdf_pfad"]).read_bytes() == roh
    detail = client.get(f"/rechnungen/{rid}").get_data(as_text=True)
    assert "Nachgetragen" in detail and "Original öffnen" in detail
    assert client.get(f"/rechnungen/{rid}/pdf").data == roh
    ohne = rechnungen.nachtragen(db, {"kunde_id": kunde, "nummer": "ALT-2", "datum": "2025-11-01", "betrag": "10"},
                                 dbmod.einstellungen(db), app.config["ORDNER"])
    assert client.get(f"/rechnungen/{ohne}/pdf").status_code == 302  # kein selbst erzeugtes PDF als Original
