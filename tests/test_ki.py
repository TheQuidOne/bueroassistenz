"""KI-Auswertung ohne Ollama: gefälschter Client und ein winziger Testserver."""

import dataclasses
import json
import re
import socket
import threading
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from arbeitsassistent import ki
from arbeitsassistent.modell import MAILARTEN, Auswertung, Mail

MAILTAG = date(2026, 10, 9)


def mail(text: str = "", betreff: str = "Anfrage", **felder) -> Mail:
    return Mail(message_id="<1@test.example>", datum=felder.pop("datum", datetime(2026, 10, 8, 15, 30)),
                absender_name="Kai Beispiel", absender_adresse="kai@beispiel.example",
                betreff=betreff, text=text, **felder)


class FalscheKi:
    """Antwortet immer gleich und merkt sich, was sie bekommen hat."""

    def __init__(self, antwort):
        self.antwort = antwort
        self.aufrufe = []

    def chat_json(self, system, nutzer, schema):
        self.aufrufe.append((system, nutzer, schema))
        return self.antwort


# --- normalisieren ------------------------------------------------------------------------

@pytest.mark.parametrize("roh", [
    None, [], "Text", 42, {},
    {"art": 5, "arbeit": "vielleicht", "einsatz_datum": "gestern", "dauer_stunden": "viel"},
    {"arbeit": [1], "art": None, "einsatz_datum": {"a": 1}, "frist": 20261014, "einsatz_beginn": True,
     "dauer_stunden": [2], "aktenzeichen": {}, "rechnungsnummer": None, "hinweis": float("nan")},
])
def test_unsinn_ergibt_leere_auswertung(roh):
    a = ki.normalisieren(roh, MAILTAG)
    assert a == Auswertung(arbeit=False, art="sonstiges")


def test_unsinniges_mail_datum_wirft_nicht():
    assert ki.normalisieren({"arbeit": True, "art": "auftrag"}, None).art == "auftrag"
    assert ki.normalisieren({"einsatz_datum": "2026-10-14", "arbeit": True}, datetime(2026, 10, 9, 8)).einsatz_datum


def test_gueltige_antwort():
    a = ki.normalisieren({
        "zusammenfassung": "  Anfrage  für\nDienstag. ", "arbeit": True, "art": "auftrag",
        "einsatz_datum": "2026-10-20", "einsatz_beginn": "9:00", "dauer_stunden": 2.5,
        "auftraggeber": "Jobcenter Musterstadt", "aktenzeichen": "Az. IA-2026-1342",
        "rechnungsnummer": "", "frist": "2026-10-12", "hinweis": "Bitte zu- oder absagen.",
    }, MAILTAG)
    assert a == Auswertung(
        arbeit=True, art="auftrag", zusammenfassung="Anfrage für Dienstag.",
        einsatz_datum=date(2026, 10, 20), einsatz_beginn="09:00", dauer_stunden=Decimal("2.5"),
        auftraggeber="Jobcenter Musterstadt", aktenzeichen="IA-2026-1342", rechnungsnummer="",
        frist=date(2026, 10, 12), hinweis="Bitte zu- oder absagen.",
    )


@pytest.mark.parametrize("art, erwartet", [
    ("Rückfrage", "rueckfrage"), ("ZAHLUNG", "zahlung"), ("verdächtig", "verdaechtig"),
    ("termin", "sonstiges"), ("", "sonstiges"),
])
def test_art(art, erwartet):
    assert ki.normalisieren({"arbeit": True, "art": art}, MAILTAG).art == erwartet


def test_verdaechtig_ist_nie_arbeit():
    a = ki.normalisieren({"arbeit": True, "art": "verdaechtig", "zusammenfassung": "Angebliche Mahnung.",
                          "rechnungsnummer": "2026-0047", "frist": "2026-10-10", "auftraggeber": "Buchhaltung",
                          "hinweis": "Bitte sofort bezahlen."}, MAILTAG)
    assert (a.arbeit, a.art, a.rechnungsnummer, a.frist, a.auftraggeber) == (False, "verdaechtig", "", None, "")
    assert a.hinweis == ki.HINWEIS_VERDAECHTIG
    assert a.zusammenfassung == "Angebliche Mahnung."


def test_keine_arbeit_leert_alle_arbeitsfelder():
    a = ki.normalisieren({"arbeit": False, "art": "auftrag", "zusammenfassung": "Kaffee am Sonntag.",
                          "einsatz_datum": "2026-10-11", "einsatz_beginn": "15:00", "dauer_stunden": 2,
                          "frist": "2026-10-10", "hinweis": "Bitte zusagen."}, MAILTAG)
    assert a == Auswertung(arbeit=False, art="sonstiges", zusammenfassung="Kaffee am Sonntag.")


def test_rueckfrage_und_zahlung_ohne_einsatzangaben():
    # Das Rechnungsdatum ist kein Einsatztag; daraus würde „noch keine Rechnung geschrieben“.
    for art in ("rueckfrage", "zahlung"):
        a = ki.normalisieren({"arbeit": True, "art": art, "einsatz_datum": "2026-09-08",
                              "einsatz_beginn": "09:30", "dauer_stunden": 2, "rechnungsnummer": "2026-004"},
                             MAILTAG)
        assert (a.einsatz_datum, a.einsatz_beginn, a.dauer_stunden, a.rechnungsnummer) == (None, "", None, "2026-004")


def test_frist_am_sendetag_ist_fuellwert():
    assert ki.normalisieren({"arbeit": True, "art": "frist", "frist": "2026-10-09"}, MAILTAG).frist is None
    assert ki.normalisieren({"arbeit": True, "art": "frist", "frist": "2026-10-15"}, MAILTAG).frist == date(2026, 10, 15)


@pytest.mark.parametrize("wert, erwartet", [
    ("2026-10-14", date(2026, 10, 14)),
    ("2026-10-14 09:00", date(2026, 10, 14)),
    ("14.10.2026", date(2026, 10, 14)),
    ("14.10.26", date(2026, 10, 14)),
    ("14.10.", date(2026, 10, 14)),
    ("2310-20-26", date(2026, 10, 23)),  # vom Schema verdrehtes „23.10.2026“
    ("2026-02-30", None),
    ("2027-12-01", None),  # mehr als ein Jahr entfernt
    ("2025-09-01", None),
    ("gestern", None),
    ("", None),
])
def test_datum(wert, erwartet):
    assert ki._datum(wert, MAILTAG) == erwartet


def test_datum_ohne_jahr_ueber_den_jahreswechsel():
    assert ki._datum("05.01.", date(2026, 12, 20)) == date(2027, 1, 5)
    assert ki._datum("20.12.", date(2027, 1, 3)) == date(2026, 12, 20)


@pytest.mark.parametrize("wert, erwartet", [
    (2.5, "2.5"), ("2,5", "2.5"), ("2.5 Std.", "2.5"), ("2:30", "2.5"), (3, "3"), ("10", "10"),
    (0.75, "0.75"), (2.3333333, "2.33"), (12, "12"),
    (0, None), (-1, None), (13, None), (0.001, None), (True, None), ("viel", None),
    (float("nan"), None), (float("inf"), None), ("Infinity", None), (None, None), ([2], None),
])
def test_dauer(wert, erwartet):
    dauer = ki._dauer(wert)
    if erwartet is None:
        assert dauer is None
    else:
        assert isinstance(dauer, Decimal) and str(dauer) == erwartet


@pytest.mark.parametrize("wert, erwartet", [
    ("9:00", "09:00"), ("9 Uhr", "09:00"), ("09.30", "09:30"), ("14:30:00", "14:30"),
    ("00:00", ""), ("25:00", ""), ("12:75", ""), ("abends", ""), (None, ""),
])
def test_uhrzeit(wert, erwartet):
    assert ki._uhrzeit(wert) == erwartet


@pytest.mark.parametrize("wert", ["keine", "Keine.", "-", "n/a", "unbekannt", "nicht angegeben",
                                  "kein Aktenzeichen", "Keine Angabe", "null", "?"])
def test_platzhalter_werden_leer(wert):
    assert ki._text(wert, 100) == ""


def test_texte_werden_gekuerzt_und_echte_saetze_bleiben():
    lang = "Wort " * 100
    gekuerzt = ki._text(lang, 50)
    assert len(gekuerzt) <= 50 and gekuerzt.endswith("…")
    assert ki._text("Nicht auf Links klicken.", 100) == "Nicht auf Links klicken."
    a = ki.normalisieren({"arbeit": True, "art": "sonstiges", "hinweis": "Kein Handlungsbedarf."}, MAILTAG)
    assert a.hinweis == ""


@pytest.mark.parametrize("wert, erwartet", [
    ("Az. 3 Ds 210/26", "3 Ds 210/26"),
    ("BG-Nr. 47110815", "47110815"),
    ("Kassenzeichen: 5501.2026.004711", "5501.2026.004711"),
    ("RE 2026-003 / RE 2026-005", "2026-003, 2026-005"),
    ("Rechnung 2026-003 und 2026-005", "2026-003, 2026-005"),
    ("IA-2026-1342", "IA-2026-1342"),
    ("unbekannt", ""),
    ("siehe Anhang", ""),
])
def test_nummern_ohne_etikett(wert, erwartet):
    assert ki._nummer(wert) == erwartet


# --- Schema und Prompt --------------------------------------------------------------------

def test_schema_passt_zur_auswertung():
    felder = {f.name for f in dataclasses.fields(Auswertung)}
    eigenschaften = ki.SCHEMA["properties"]
    assert set(eigenschaften) == felder
    assert ki.SCHEMA["required"] == list(eigenschaften)
    assert eigenschaften["art"]["enum"] == list(MAILARTEN)
    assert set(eigenschaften["dauer_stunden"]["type"]) == {"number", "null"}
    for name, e in eigenschaften.items():
        if e["type"] == "string":
            # Jeder Text hat eine Grenze, sonst droht endlose Ausgabe.
            assert "enum" in e or "pattern" in e or "maxLength" in e, name
        # Ollama übergeht Muster mit \d stillschweigend.
        assert "\\d" not in e.get("pattern", ""), name


@pytest.mark.parametrize("wert, passt", [("2026-10-14", True), ("", True), ("14.10.2026", False),
                                         ("2026-10-14 09:00", False)])
def test_datumsmuster(wert, passt):
    assert bool(re.fullmatch(ki.DATUM_MUSTER, wert)) == passt


def test_datumstabelle():
    tabelle = ki.datumstabelle(MAILTAG)
    zeilen = tabelle.splitlines()
    assert zeilen[0].startswith("KW 37: Mo 07.09.2026")  # Montag vor heute − 30 Tage
    assert "Fr 09.10. (heute)" in tabelle
    assert zeilen[-1].startswith("KW 01: Mo 04.01.2027") and "Do 07.01." in zeilen[-1]  # heute + 90 Tage
    assert all(len(z.split(", ")) == 7 for z in zeilen)
    assert "Fr 01.01.2027" in ki.datumstabelle(date(2026, 12, 18))


def test_nachricht_enthaelt_kontext_und_gekuerzte_mail():
    m = mail("Hallo,\r\n\r\n\r\n\r\nbitte hier: https://beispiel.example/" + "x" * 80 + "\n" + "Zeile\n" * 3000,
             anhaenge=["Ladung.pdf"])
    text = ki.nachricht(m, MAILTAG, ["Integrationsamt Musterland", " ", "Jobcenter Musterstadt"],
                        [{"nummer": "2026-004", "kunde": "Landschaftsverband Musterland",
                          "datum": date(2026, 9, 8), "betrag": "212,50 €", "bezug": ""}])
    assert "Heute ist Freitag, 09.10.2026." in text
    assert "Gesendet: Donnerstag, 08.10.2026" in text
    assert "„morgen“ = Freitag 09.10.2026" in text and "KW 42" in text
    assert "15:30" not in text  # die Sendezeit hielt das Modell sonst für den Einsatzbeginn
    assert "Bekannte Kunden: Integrationsamt Musterland; Jobcenter Musterstadt" in text
    assert "2026-004 | Landschaftsverband Musterland | 08.09.2026 | 212,50 € | " in text
    assert "Anhänge: Ladung.pdf" in text
    assert "https://beispiel.example/…" in text and "xxxx" not in text
    assert "\n\n\n" not in text
    assert text.endswith("[… gekürzt]")
    assert len(text.split("=== Mail ===")[1]) < ki.MAX_MAILTEXT + 500


def test_nachricht_ohne_datum_und_listen():
    text = ki.nachricht(mail("Text", datum=None), MAILTAG, [], [])
    assert "Gesendet: unbekannt" in text
    assert "Bekannte Kunden: (keine)" in text and "\n(keine)\n" in text


# --- auswerten mit gefälschtem Client -----------------------------------------------------

def test_auswerten_gibt_prompt_weiter_und_normalisiert():
    falsch = FalscheKi({"arbeit": True, "art": "auftrag", "einsatz_datum": "2026-10-13",
                        "einsatz_beginn": "08:30", "dauer_stunden": 2})
    a = ki.auswerten(falsch, mail("Können Sie nächsten Dienstag ab 8:30 Uhr für 2 Stunden dolmetschen?"),
                     MAILTAG, ["Integrationsamt Musterland"], [])
    system, nutzer, schema = falsch.aufrufe[0]
    assert system == ki.SYSTEM and schema is ki.SCHEMA
    assert "nächsten Dienstag" in nutzer
    assert (a.einsatz_datum, a.einsatz_beginn, a.dauer_stunden) == (date(2026, 10, 13), "08:30", Decimal("2"))


def test_auswerten_verwirft_nummern_ohne_beleg():
    falsch = FalscheKi({"arbeit": True, "art": "rueckfrage", "rechnungsnummer": "2026-004, 2026-007",
                        "aktenzeichen": "IA-2026-1187"})
    m = mail("Ihre Rechnung 2026 - 004 ist unvollständig.", betreff="Rechnung")
    a = ki.auswerten(falsch, m, MAILTAG, [], [{"nummer": "2026-007", "kunde": "Krankenkasse", "datum": "2026-10-05",
                                               "betrag": "127,50 €", "bezug": "Az. IA-2026-1187"}])
    assert a.rechnungsnummer == "2026-004"  # 2026-007 stand nur in der Liste
    assert a.aktenzeichen == ""


def test_auswerten_findet_nummern_auch_im_anhang_und_mit_anderem_etikett():
    falsch = FalscheKi({"arbeit": True, "art": "frist", "aktenzeichen": "BG-Nr. 47110815"})
    a = ki.auswerten(falsch, mail("Bitte Nachweis senden.\nBG-Nummer 47110815"), MAILTAG, [], [])
    assert a.aktenzeichen == "47110815"
    falsch = FalscheKi({"arbeit": True, "art": "auftrag", "aktenzeichen": "IA-2026-1342"})
    a = ki.auswerten(falsch, mail("Siehe Bescheid.", anhaenge=["Bescheid_IA-2026-1342.pdf"]), MAILTAG, [], [])
    assert a.aktenzeichen == "IA-2026-1342"


def test_auswerten_verwirft_daten_und_uhrzeiten_ohne_beleg():
    text = "Bitte reichen Sie alle Rechnungen für Einsätze im September bis 15.10.2026 ein."
    falsch = FalscheKi({"arbeit": True, "art": "frist", "einsatz_datum": "2026-09-30", "frist": "2026-10-15",
                        "einsatz_beginn": "08:00"})
    a = ki.auswerten(falsch, mail(text, betreff="Abrechnung"), MAILTAG, [], [])
    assert (a.einsatz_datum, a.frist, a.einsatz_beginn) == (None, date(2026, 10, 15), "")


def test_auswerten_mail_ohne_datum_prueft_gegen_heute():
    falsch = FalscheKi({"arbeit": True, "art": "auftrag", "einsatz_datum": "2026-10-20"})
    a = ki.auswerten(falsch, mail("Einsatz am 20.10.", datum=None), MAILTAG, [], [])
    assert a.einsatz_datum == date(2026, 10, 20)


@pytest.mark.parametrize("d, text, belegt", [
    (date(2026, 10, 14), "Termin am 14.10. um 9 Uhr", True),
    (date(2026, 11, 3), "am 3. November", True),
    (date(2026, 11, 12), "on November 12, 2026", True),
    (date(2026, 11, 12), "Thursday, 12 November", True),
    (date(2026, 10, 13), "nächsten Dienstag", True),
    (date(2026, 10, 14), "nächsten Dienstag", False),  # 14.10. ist ein Mittwoch
    (date(2026, 10, 8), "Danke für gestern!", True),
    (date(2026, 9, 30), "Rechnungen für September bis 15.10.", False),
    (date(2026, 10, 1), "am 11.10.", False),
])
def test_datum_belegt(d, text, belegt):
    assert ki.datum_belegt(d, text) == belegt


@pytest.mark.parametrize("beginn, text, belegt", [
    ("14:30", "übermorgen um halb drei", True),
    ("14:00", "Time: 2 pm", True),
    ("09:30", "Hauptverhandlung 9:30 Uhr", True),
    ("08:00", "Bestellnummer 4500012399, Termin am 14.10.2026", False),
])
def test_uhrzeit_belegt(beginn, text, belegt):
    assert ki.uhrzeit_belegt(beginn, text) == belegt


# --- Ollama-Client gegen einen Testserver ------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _antworten(self):
        laenge = int(self.headers.get("Content-Length") or 0)
        self.server.anfragen.append((self.path, json.loads(self.rfile.read(laenge)) if laenge else None))
        code, daten = self.server.antworten.pop(0) if self.server.antworten else (500, {"error": "leer"})
        roh = json.dumps(daten).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    do_GET = do_POST = _antworten


@pytest.fixture
def server():
    s = HTTPServer(("127.0.0.1", 0), _Handler)
    s.anfragen, s.antworten = [], []
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def _client(server, **felder):
    return ki.Ollama(url=f"http://127.0.0.1:{server.server_port}/", timeout=5, **felder)


def _chat(inhalt: str):
    return 200, {"message": {"role": "assistant", "content": inhalt}}


def freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_nicht_erreichbar():
    k = ki.Ollama(url=f"http://127.0.0.1:{freier_port()}", timeout=2)
    bereit, text = k.status()
    assert not bereit and "läuft nicht" in text
    with pytest.raises(ki.KiFehler, match="nicht erreichbar"):
        k.chat_json("System", "Nutzer", ki.SCHEMA)


def test_status(server):
    server.antworten = [(200, {"models": [{"name": "qwen3.5:9b"}]}), (200, {"models": [{"name": "gemma4:12b"}]})]
    bereit, text = _client(server).status()
    assert not bereit and "gemma4:12b fehlt" in text and "ollama pull gemma4:12b" in text
    assert _client(server).status() == (True, "Die lokale KI ist bereit (gemma4:12b).")
    assert server.anfragen[0][0] == "/api/tags"


def test_status_ohne_markierung_sucht_latest(server):
    server.antworten = [(200, {"models": [{"name": "meinmodell:latest"}]})]
    assert _client(server, modell="meinmodell").status()[0]


def test_modell_fehlt_beim_chat(server):
    server.antworten = [(404, {"error": "model 'gemma4:12b' not found"})]
    with pytest.raises(ki.KiFehler, match="fehlt"):
        _client(server).chat_json("System", "Nutzer", ki.SCHEMA)


def test_serverfehler_wird_kifehler(server):
    server.antworten = [(500, {"error": "model requires more system memory"})]
    with pytest.raises(ki.KiFehler, match="500"):
        _client(server).chat_json("System", "Nutzer", ki.SCHEMA)


def test_chat_json_anfrage_und_antwort(server):
    server.antworten = [_chat('{"arbeit": true, "art": "auftrag"}')]
    k = _client(server, keep_alive="1m")
    assert k.chat_json("System", "Nutzer", ki.SCHEMA) == {"arbeit": True, "art": "auftrag"}
    pfad, anfrage = server.anfragen[0]
    assert pfad == "/api/chat"
    assert anfrage["model"] == "gemma4:12b" and anfrage["format"] == ki.SCHEMA
    assert anfrage["messages"] == [{"role": "system", "content": "System"}, {"role": "user", "content": "Nutzer"}]
    assert anfrage["stream"] is False and anfrage["think"] is False and anfrage["keep_alive"] == "1m"
    assert anfrage["options"]["temperature"] == 0 and anfrage["options"]["num_ctx"] == 8192


def test_chat_json_wiederholt_bei_kaputtem_json(server):
    server.antworten = [_chat('{"arbeit": tr'), _chat('{"arbeit": false}')]
    assert _client(server).chat_json("S", "N", ki.SCHEMA) == {"arbeit": False}
    assert server.anfragen[1][1]["options"]["temperature"] == 0.4


def test_chat_json_zweimal_kaputt_ist_valueerror(server):
    server.antworten = [_chat("kein json"), _chat("[1, 2]")]
    with pytest.raises(ValueError) as fehler:
        _client(server).chat_json("S", "N", ki.SCHEMA)
    assert not isinstance(fehler.value, ki.KiFehler)
