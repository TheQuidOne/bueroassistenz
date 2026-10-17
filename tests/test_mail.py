"""Mail-Abruf: nur lesend, inkrementell, verständliche Fehler, robuste Rohmails.

Alle Mails unter tests/daten/mail/ sind erfunden. Der IMAP-Server ist nachgebaut: einmal als
Klasse anstelle von imaplib.IMAP4_SSL, einmal als kleiner echter Server für das echte imaplib.
"""

import imaplib
import re
import socket
import socketserver
import ssl
import threading
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import pytest

from arbeitsassistent import mail
from arbeitsassistent.modell import MailKonto

DATEN = Path(__file__).parent / "daten" / "mail"
SEIT = date(2026, 10, 1)


def eml(name: str) -> bytes:
    return (DATEN / name).read_bytes()


def lesen(name: str, **kw):
    return mail.mail_aus_bytes(eml(name), **kw)


# --- Nachgebauter IMAP-Server -------------------------------------------------------


class Postfach:
    """Zustand des nachgebauten Servers, über alle Verbindungen eines Tests hinweg."""

    def __init__(self):
        self.passwort = "geheim"
        self.ordner = {
            "INBOX": {"uidvalidity": 7, "mails": {}},
            "Entw&APw-rfe": {"uidvalidity": 3, "mails": {}},  # „Entwürfe“
            'Rechnungen "A"': {"uidvalidity": 4, "mails": {}},
        }
        self.gelesen: set[tuple[str, int]] = set()  # \Seen, das wir gesetzt hätten
        self.befehle: list[tuple] = []
        self.verbindungen: list[tuple] = []
        self.fehler_beim_verbinden: Exception | None = None
        self.abbruch_bei: str | None = None

    def ablegen(self, uid: int, roh: bytes, tag: date, ordner: str = "INBOX"):
        self.ordner[ordner]["mails"][uid] = (roh, tag)


class NachgebauterServer:
    """Ersetzt imaplib.IMAP4_SSL. Antworten haben die Form, die imaplib zurückgibt."""

    postfach: Postfach

    def __init__(self, host, port=993, *, ssl_context=None, timeout=None):
        self.p = self.postfach
        self.p.verbindungen.append((host, port, timeout))
        if self.p.fehler_beim_verbinden:
            raise self.p.fehler_beim_verbinden
        self.gewaehlt = None
        self.nur_lesen = None
        self._antworten = {}

    def __getattr__(self, name):  # alles Weitere (store, expunge, copy …) ist verboten
        raise AssertionError(f"unerwarteter IMAP-Aufruf: {name}")

    def login(self, benutzer, passwort):
        self.p.befehle.append(("LOGIN", benutzer))
        if passwort != self.p.passwort:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"LOGIN completed"]

    def authenticate(self, verfahren, rueckruf):
        self.p.befehle.append(("AUTHENTICATE", verfahren))
        _, benutzer, passwort = rueckruf(b"").split(b"\0")
        if passwort.decode() != self.p.passwort:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"AUTHENTICATE completed"]

    def list(self, directory='""', pattern="*"):
        self.p.befehle.append(("LIST",))
        eintraege = []
        for name in self.p.ordner:
            if '"' in name:  # Server schicken solche Namen als Literal
                eintraege += [(f'(\\HasNoChildren) "/" {{{len(name)}}}'.encode(), name.encode()), b""]
            else:
                eintraege.append(f'(\\HasNoChildren) "/" "{name}"'.encode())
        eintraege.append(b'(\\HasChildren \\Noselect) "/" "[Gmail]"')
        return "OK", eintraege

    def select(self, mailbox="INBOX", readonly=False):
        self.p.befehle.append(("SELECT", mailbox, readonly))
        name = mailbox[1:-1].replace('\\"', '"') if mailbox.startswith('"') else mailbox
        if name not in self.p.ordner:
            return "NO", [b"[NONEXISTENT] Unknown Mailbox"]
        self.gewaehlt, self.nur_lesen = name, readonly
        ordner = self.p.ordner[name]
        self._antworten["UIDVALIDITY"] = [str(ordner["uidvalidity"]).encode()]
        return "OK", [str(len(ordner["mails"])).encode()]

    def response(self, code):
        return code, self._antworten.pop(code, [None])

    def uid(self, befehl, *args):
        befehl = befehl.upper()
        args = [a for a in args if a is not None]
        self.p.befehle.append(("UID", befehl, *args))
        if self.p.abbruch_bei == befehl:
            raise imaplib.IMAP4.abort("socket error: EOF")
        mails = self.p.ordner[self.gewaehlt]["mails"]
        if befehl == "SEARCH":
            if args[0] == "SINCE":
                tag, monat, jahr = args[1].split("-")
                seit = date(int(jahr), mail._MONATE.index(monat) + 1, int(tag))
                uids = [u for u, (_, eingang) in mails.items() if eingang >= seit]
            else:
                assert args[0] == "UID"
                unten = int(args[1].split(":")[0])
                # wie echte Server: „n:*“ trifft immer mindestens die höchste UID
                uids = [u for u in mails if u >= unten] or ([max(mails)] if mails else [])
            return "OK", [" ".join(str(u) for u in sorted(uids)).encode()]
        if befehl == "FETCH":
            menge, teile = args
            abschnitt = re.search(r"BODY(\.PEEK)?\[\](?:<0\.(\d+)>)?", teile)
            assert abschnitt, teile
            antwort = []
            for nr, u in enumerate(sorted(int(x) for x in menge.split(",")), start=1):
                if u not in mails:
                    continue
                if not abschnitt.group(1) and not self.nur_lesen:
                    self.p.gelesen.add((self.gewaehlt, u))  # BODY[] ohne PEEK setzt \Seen
                roh = mails[u][0]
                if abschnitt.group(2):
                    roh = roh[: int(abschnitt.group(2))]
                antwort.append((f"{nr} (UID {u} BODY[]<0> {{{len(roh)}}}".encode(), roh))
                antwort.append(b")")
            return "OK", antwort
        raise AssertionError(f"verbotener Befehl: UID {befehl}")

    def logout(self):
        self.p.befehle.append(("LOGOUT",))
        return "BYE", [b"LOGOUT completed"]


@pytest.fixture
def postfach(monkeypatch):
    p = Postfach()
    p.ablegen(5, eml("kaputtes_datum.eml"), date(2026, 9, 1))  # vor SEIT
    p.ablegen(10, eml("text.eml"), date(2026, 10, 5))
    p.ablegen(11, eml("nur_html.eml"), date(2026, 10, 6))
    p.ablegen(12, eml("antwort.eml"), date(2026, 10, 6))
    p.ablegen(1, eml("alternative.eml"), date(2026, 10, 7), "Entw&APw-rfe")
    server = type("Server", (NachgebauterServer,), {"postfach": p})
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", server)
    return p


def konto(**kw) -> MailKonto:
    werte = {"host": "imap.example.org", "benutzer": "max.beispiel@example.org"}
    return MailKonto(**(werte | kw))


def befehle(p: Postfach, name: str) -> list[tuple]:
    return [b for b in p.befehle if b[0] == name or (b[0] == "UID" and b[1] == name)]


def test_verbindung_prueft_zertifikat_und_servername(monkeypatch):
    # imaplib prüft ohne eigenen Kontext nichts, deshalb muss immer ein geprüfter übergeben werden.
    gesehen = {}

    def verbinden(host, port=993, *, ssl_context=None, timeout=None):
        gesehen["kontext"] = ssl_context
        raise OSError("kein Netz im Test")

    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", verbinden)
    with pytest.raises(mail.MailFehler):
        mail.verbindung_testen(konto(), "geheim")
    k = gesehen["kontext"]
    assert k is not None and k.verify_mode == ssl.CERT_REQUIRED and k.check_hostname


# --- Anbieter -----------------------------------------------------------------------


def test_anbieter_vollstaendig():
    schluessel = list(mail.ANBIETER)
    assert schluessel[:3] == ["gmx", "webde", "gmail"] and schluessel[-1] == "anderer"
    for name in ("icloud", "tonline", "posteo", "mailboxorg", "ionos", "strato", "freenet",
                 "yahoo", "outlook"):
        assert name in mail.ANBIETER
    for eintrag in mail.ANBIETER.values():
        assert {"name", "host", "port", "hilfe"} <= eintrag.keys()
        assert eintrag["port"] == 993 and len(eintrag["hilfe"]) > 40
    assert all(a["host"] for k, a in mail.ANBIETER.items() if k != "anderer")
    assert mail.ANBIETER["anderer"]["host"] == ""
    assert mail.ANBIETER["outlook"]["unterstuetzt"] is False
    assert all(a["unterstuetzt"] for k, a in mail.ANBIETER.items() if k != "outlook")
    assert "App-Passwort" in mail.ANBIETER["gmail"]["hilfe"]
    assert "POP3 und IMAP Zugriff erlauben" in mail.ANBIETER["gmx"]["hilfe"]


# --- Nur lesen ----------------------------------------------------------------------


def test_abruf_veraendert_nichts(postfach):
    mail.abrufen(konto(), "geheim", SEIT)
    mail.abrufen(konto(ordner="Entwürfe"), "geheim", SEIT)
    auswahl = befehle(postfach, "SELECT")
    assert auswahl and all(b[2] is True for b in auswahl)  # select(readonly=True) = EXAMINE
    abrufe = befehle(postfach, "FETCH")
    assert abrufe and all("BODY.PEEK[]" in b[3] and "RFC822" not in b[3] for b in abrufe)
    assert postfach.gelesen == set()
    erlaubt = {"LOGIN", "SELECT", "UID", "LOGOUT"}
    assert {b[0] for b in postfach.befehle} <= erlaubt
    assert {b[1] for b in postfach.befehle if b[0] == "UID"} <= {"SEARCH", "FETCH"}


def test_quelltext_ohne_schreibende_befehle():
    quelltext = Path(mail.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\bimap\.(store|expunge|copy|move|append|delete|rename|create)\(", quelltext)
    assert not re.search(r"[\"'](STORE|EXPUNGE|COPY|MOVE|APPEND|SELECT)[\"']", quelltext)
    assert not re.search(r"readonly\s*=\s*False", quelltext)
    assert "readonly=True" in quelltext and "BODY.PEEK[]" in quelltext


# --- Abruf --------------------------------------------------------------------------


def test_verbindung_testen_gibt_ordner(postfach):
    ordner = mail.verbindung_testen(konto(), "geheim")
    assert ordner == ["INBOX", "Entwürfe", 'Rechnungen "A"']  # ohne \Noselect
    assert postfach.verbindungen == [("imap.example.org", 993, 30)]
    assert postfach.befehle[-1] == ("LOGOUT",)


def test_erster_abruf_ab_datum(postfach):
    mails, gueltigkeit, hoechste = mail.abrufen(konto(), "geheim", SEIT)
    assert ("UID", "SEARCH", "SINCE", "01-Oct-2026") in postfach.befehle
    assert [m.uid for m in mails] == [10, 11, 12]  # 5 ist älter als SEIT
    assert (gueltigkeit, hoechste) == (7, 12)
    assert mails[0].betreff == "Auftrag: Dolmetscheinsatz am 14.10.2026"
    assert {m.ordner for m in mails} == {"INBOX"}


def test_imap_datum_englisch_unabhaengig_von_systemsprache():
    import locale

    try:
        alt = locale.setlocale(locale.LC_TIME)
        locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")
    except locale.Error:
        alt = None
    try:
        assert mail._imap_datum(date(2026, 3, 5)) == "05-Mar-2026"
        assert mail._imap_datum(date(2026, 5, 1)) == "01-May-2026"
        assert mail._imap_datum(date(2026, 12, 24)) == "24-Dec-2026"
    finally:
        if alt:
            locale.setlocale(locale.LC_TIME, alt)


def test_inkrementell_nur_neue(postfach):
    _, gueltigkeit, hoechste = mail.abrufen(konto(), "geheim", SEIT)
    postfach.befehle.clear()
    mails, gueltigkeit, hoechste = mail.abrufen(konto(), "geheim", SEIT, gueltigkeit, hoechste)
    assert mails == [] and hoechste == 12  # Server liefert für „13:*“ die 12, die zählt nicht
    assert ("UID", "SEARCH", "UID", "13:*") in postfach.befehle
    assert not befehle(postfach, "FETCH")

    postfach.ablegen(13, eml("kodiert_utf8_q.eml"), date(2026, 10, 9))
    mails, gueltigkeit, hoechste = mail.abrufen(konto(), "geheim", SEIT, gueltigkeit, hoechste)
    assert [m.uid for m in mails] == [13] and hoechste == 13
    assert not [b for b in postfach.befehle if "SINCE" in b]


def test_uidvalidity_geaendert_liest_neu(postfach):
    _, gueltigkeit, hoechste = mail.abrufen(konto(), "geheim", SEIT)
    inbox = postfach.ordner["INBOX"]
    alte = inbox["mails"]
    inbox["uidvalidity"] = 8  # Server hat neu nummeriert
    inbox["mails"] = {neu: alte[u] for neu, u in enumerate(sorted(alte), start=1)}
    postfach.befehle.clear()
    mails, gueltigkeit, hoechste = mail.abrufen(konto(), "geheim", SEIT, gueltigkeit, hoechste)
    assert ("UID", "SEARCH", "SINCE", "01-Oct-2026") in postfach.befehle
    assert [m.uid for m in mails] == [2, 3, 4] and (gueltigkeit, hoechste) == (8, 4)
    assert mails[0].message_id == "<auftrag-4711@integrationsamt-musterland.example>"


def test_limit_erster_abruf_nimmt_die_neuesten(postfach):
    mails, _, hoechste = mail.abrufen(konto(), "geheim", SEIT, hoechstens=2)
    assert [m.uid for m in mails] == [11, 12] and hoechste == 12


def test_limit_danach_lueckenlos(postfach):
    _, gueltigkeit, hoechste = mail.abrufen(konto(), "geheim", SEIT)
    for uid in range(13, 18):
        postfach.ablegen(uid, eml("ohne_message_id.eml"), date(2026, 10, 9))
    gesehen = []
    for _ in range(4):
        mails, gueltigkeit, hoechste = mail.abrufen(
            konto(), "geheim", SEIT, gueltigkeit, hoechste, hoechstens=2
        )
        gesehen.append([m.uid for m in mails])
    assert gesehen == [[13, 14], [15, 16], [17], []] and hoechste == 17


def test_viele_mails_in_paketen(postfach):
    for uid in range(100, 230):
        postfach.ablegen(uid, eml("text.eml"), date(2026, 10, 8))
    mails, _, hoechste = mail.abrufen(konto(), "geheim", SEIT)
    assert len(mails) == 133 and hoechste == 229
    assert len(befehle(postfach, "FETCH")) == 3


def test_grosse_mail_wird_nur_teilweise_geladen(postfach):
    nachricht = EmailMessage()
    nachricht["From"] = "erika.mustermann@example.org"
    nachricht["Subject"] = "Scan der Kostenzusage"
    nachricht["Message-ID"] = "<gross@example.org>"
    nachricht.set_content("Anbei der Scan.")
    nachricht.add_attachment(b"\0" * (2 * mail.HOECHSTENS_BYTES), maintype="application",
                             subtype="pdf", filename="Kostenzusage.pdf")
    postfach.ablegen(20, nachricht.as_bytes(), date(2026, 10, 9))
    mails, _, _ = mail.abrufen(konto(), "geheim", date(2026, 10, 9))
    assert mails[0].text == "Anbei der Scan."
    assert mails[0].anhaenge == ["Kostenzusage.pdf"]


def test_ordner_mit_umlaut(postfach):
    mails, gueltigkeit, _ = mail.abrufen(konto(ordner="Entwürfe"), "geheim", SEIT)
    assert ("SELECT", '"Entw&APw-rfe"', True) in postfach.befehle
    assert [m.betreff for m in mails] == ["Termin am Freitag"] and gueltigkeit == 3
    assert mails[0].ordner == "Entwürfe"


def test_passwort_mit_umlaut(postfach):
    postfach.passwort = "Grüße-2026"
    assert mail.verbindung_testen(konto(), "Grüße-2026")[0] == "INBOX"
    assert ("AUTHENTICATE", "PLAIN") in postfach.befehle


def test_utf7_hin_und_zurueck():
    for name in ["INBOX", "Entwürfe", "Gesendete Objekte", "A&B", "Rechnungen/2026 – Übersicht"]:
        assert mail._utf7_dekodieren(mail._utf7_kodieren(name)) == name
    assert mail._utf7_kodieren("Entwürfe") == "Entw&APw-rfe"
    assert mail._utf7_kodieren("A&B") == "A&-B"


# --- Fehler -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host, erwartet",
    [
        ("imap.gmx.net", "POP3 und IMAP Zugriff erlauben"),
        ("imap.web.de", "POP3/IMAP"),
        ("imap.gmail.com", "myaccount.google.com/apppasswords"),
        ("imap.mail.me.com", "app-spezifisches Passwort"),
        ("secureimap.t-online.de", "Passwort für E-Mail-Programme"),
        ("imap.example.org", "App-Passwort"),
    ],
)
def test_anmeldung_abgelehnt(postfach, host, erwartet):
    with pytest.raises(mail.MailFehler) as fehler:
        mail.abrufen(konto(host=host), "falsch", SEIT)
    assert "Anmeldung wurde abgelehnt" in str(fehler.value)
    assert erwartet in str(fehler.value)
    assert postfach.befehle[-1] == ("LOGOUT",)


def test_outlook_ehrlicher_hinweis(postfach):
    with pytest.raises(mail.MailFehler, match="16. September 2024") as fehler:
        mail.verbindung_testen(konto(host="outlook.office365.com"), "falsch")
    assert "Weiterleitung" in str(fehler.value)


@pytest.mark.parametrize(
    "ausnahme, erwartet",
    [
        (socket.gaierror(-2, "Name or service not known"), "wurde nicht gefunden"),
        (TimeoutError("timed out"), "nach 30 Sekunden"),
        (ConnectionRefusedError(111, "Connection refused"), "Port 993"),
        (ssl.SSLError(1, "WRONG_VERSION_NUMBER"), "verschlüsselte Verbindung"),
        (OSError(101, "Network is unreachable"), "Internetverbindung"),
    ],
)
def test_server_nicht_erreichbar(postfach, ausnahme, erwartet):
    postfach.fehler_beim_verbinden = ausnahme
    with pytest.raises(mail.MailFehler, match=erwartet):
        mail.verbindung_testen(konto(), "geheim")
    assert postfach.verbindungen[-1][2] == 30


def test_abbruch_waehrend_abruf(postfach):
    postfach.abbruch_bei = "FETCH"
    with pytest.raises(mail.MailFehler, match="unterbrochen"):
        mail.abrufen(konto(), "geheim", SEIT)
    assert postfach.befehle[-1] == ("LOGOUT",)


def test_unbekannter_ordner(postfach):
    with pytest.raises(mail.MailFehler) as fehler:
        mail.abrufen(konto(ordner="Aufträge"), "geheim", SEIT)
    meldung = str(fehler.value)
    assert "„Aufträge“ gibt es in diesem Postfach nicht" in meldung
    assert "INBOX, Entwürfe" in meldung


def test_fehlende_angaben(postfach):
    with pytest.raises(mail.MailFehler, match="Adresse des Mail-Servers"):
        mail.verbindung_testen(konto(host=" "), "geheim")
    with pytest.raises(mail.MailFehler, match="Passwort"):
        mail.verbindung_testen(konto(), "")
    assert postfach.verbindungen == []


# --- Echtes imaplib gegen einen kleinen Server --------------------------------------


class _MiniImap(socketserver.StreamRequestHandler):
    """Gerade genug IMAP4rev1, damit das echte imaplib Anmeldung, EXAMINE und FETCH macht."""

    def schreiben(self, *teile):
        self.wfile.write(b"".join(t if isinstance(t, bytes) else t.encode() for t in teile))

    def handle(self):
        mails = self.server.mails
        self.schreiben("* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN] Testserver bereit\r\n")
        while zeile := self.rfile.readline():
            zeile = zeile.decode().rstrip("\r\n")
            self.server.befehle.append(zeile)
            tag, befehl, rest = (zeile.split(" ", 2) + [""])[:3]
            befehl = befehl.upper()
            if befehl == "CAPABILITY":
                self.schreiben("* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n", f"{tag} OK fertig\r\n")
            elif befehl == "LOGIN":
                if rest.endswith('"geheim"'):
                    self.schreiben(f"{tag} OK angemeldet\r\n")
                else:
                    self.schreiben(f"{tag} NO [AUTHENTICATIONFAILED] nein\r\n")
            elif befehl == "LIST":
                self.schreiben('* LIST (\\HasNoChildren) "/" "INBOX"\r\n', f"{tag} OK LIST\r\n")
            elif befehl == "EXAMINE":
                self.schreiben(f"* {len(mails)} EXISTS\r\n", "* OK [UIDVALIDITY 42] gültig\r\n",
                               "* OK [UIDNEXT 3] als Nächstes\r\n",
                               f"{tag} OK [READ-ONLY] EXAMINE fertig\r\n")
            elif befehl == "UID" and rest.upper().startswith("SEARCH"):
                treffer = " ".join(str(u) for u in sorted(mails))
                self.schreiben(f"* SEARCH {treffer}\r\n", f"{tag} OK SEARCH\r\n")
            elif befehl == "UID" and rest.upper().startswith("FETCH"):
                for uid in map(int, rest.split(" ")[1].split(",")):
                    roh = mails[uid]
                    kopf = f"* {uid} FETCH (UID {uid} BODY[]<0> {{{len(roh)}}}\r\n"
                    self.schreiben(kopf, roh, ")\r\n")
                self.schreiben(f"{tag} OK FETCH\r\n")
            elif befehl == "LOGOUT":
                self.schreiben("* BYE tschüss\r\n", f"{tag} OK LOGOUT\r\n")
                return
            else:
                self.schreiben(f"{tag} BAD nicht nachgebaut\r\n")


@pytest.fixture
def mini_server(monkeypatch):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _MiniImap)
    server.daemon_threads = True
    server.mails = {1: eml("text.eml"), 2: eml("anhang.eml")}
    server.befehle = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    class Klartext(imaplib.IMAP4):  # wie IMAP4_SSL, nur ohne Verschlüsselung
        def __init__(self, host, port_ssl=993, *, ssl_context=None, timeout=None):
            super().__init__("127.0.0.1", port, timeout=timeout)

    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", Klartext)
    yield server
    server.shutdown()
    server.server_close()


def test_echtes_imaplib_nur_lesend(mini_server):
    assert mail.verbindung_testen(konto(), "geheim") == ["INBOX"]
    mails, gueltigkeit, hoechste = mail.abrufen(konto(), "geheim", SEIT)
    assert (gueltigkeit, hoechste) == (42, 2)
    assert [m.betreff for m in mails] == ["Auftrag: Dolmetscheinsatz am 14.10.2026",
                                         "Auftragsbestätigung IA-2026-0815"]
    assert mails[1].anhaenge == ["Auftragsbestätigung IA-2026-0815.pdf", "Kostenzusage Köln.pdf"]
    gesendet = [b.split(" ", 1)[1] for b in mini_server.befehle]
    assert 'EXAMINE "INBOX"' in gesendet
    assert "UID SEARCH SINCE 01-Oct-2026" in gesendet
    assert f"UID FETCH 1,2 (UID BODY.PEEK[]<0.{mail.HOECHSTENS_BYTES}>)" in gesendet
    verboten = ("SELECT", "STORE", "EXPUNGE", "COPY", "MOVE", "APPEND", "DELETE")
    assert not [b for b in gesendet if b.upper().startswith(verboten) or " STORE " in b.upper()]


def test_echtes_imaplib_anmeldung_abgelehnt(mini_server):
    with pytest.raises(mail.MailFehler, match="Anmeldung wurde abgelehnt"):
        mail.verbindung_testen(konto(), "falsch")


# --- Rohmails -----------------------------------------------------------------------


def test_reine_textmail():
    m = lesen("text.eml", uid=10)
    assert m.message_id == "<auftrag-4711@integrationsamt-musterland.example>"
    assert m.absender_name == "Integrationsamt Musterland"
    assert m.absender_adresse == "auftraege@integrationsamt-musterland.example"
    assert m.an == "Max Beispiel <max.beispiel@example.org>"
    assert m.datum == datetime(2026, 10, 5, 7, 12, tzinfo=timezone.utc)
    assert m.datum.tzinfo is not None
    assert "von 10:00 bis 12:00 Uhr" in m.text  # weicher Zeilenumbruch (quoted-printable)
    assert "Aktenzeichen: IA-2026-0815" in m.text and "Grüßen" in m.text
    assert m.anhaenge == [] and m.uid == 10 and m.ordner == "INBOX"


def test_nur_html():
    m = lesen("nur_html.eml")
    assert m.betreff == "Terminbestätigung" and m.absender_name == "Praxis Dr. Beispiel"
    assert m.text == (
        "Guten Tag Herr Beispiel,\n\n"
        "wir bestätigen den Termin am Donnerstag, 15.10.2026 um 9:30 Uhr.\n\n"
        "- Ort: Musterstraße 5\n- Dauer: ca. 1½ Stunden\n\n"
        "Rechnungsnummer 2026-012\n\n"
        "Freundliche Grüße\nIhr Praxisteam"
    )


def test_alternative_bevorzugt_klartext():
    m = lesen("alternative.eml")
    assert m.text.startswith("Klartextfassung:") and "HTML-Fassung" not in m.text


def test_alternative_mit_leerem_klartext_nimmt_html():
    nachricht = EmailMessage()
    nachricht["Subject"] = "Leer"
    nachricht.set_content("\n")
    nachricht.add_alternative("<p>Nur hier steht <i>etwas</i>.</p>", subtype="html")
    assert mail.mail_aus_bytes(nachricht.as_bytes()).text == "Nur hier steht etwas."


def test_anhaenge():
    m = lesen("anhang.eml")
    assert m.text == "Anbei erhalten Sie die Auftragsbestätigung und die Kostenzusage."
    # RFC 2231 und RFC 2047 im Dateinamen; das eingebettete Logo zählt nicht
    assert m.anhaenge == ["Auftragsbestätigung IA-2026-0815.pdf", "Kostenzusage Köln.pdf"]


def test_kopfzeilen_utf8_q():
    m = lesen("kodiert_utf8_q.eml")
    assert m.betreff == "Rückfrage zur Rechnung 2026-007"  # zwei Wörter, umbrochen
    assert m.absender_name == "Jürgen Müller"
    assert m.absender_adresse == "juergen.mueller@example.org"
    assert "Können Sie sie bitte korrigieren?" in m.text


def test_kopfzeilen_latin1_b_und_windows_1252_im_text():
    m = lesen("kodiert_latin1_b.eml")
    assert m.betreff == "Absage: Termin in Köln fällt aus"
    assert m.absender_name == "Bärbel Grün"
    assert "Der Kurs „Gebärdensprache im Alltag“ fällt aus." in m.text


def test_kaputte_zeichensaetze_ohne_absturz():
    m = lesen("kaputte_zeichensaetze.eml")
    assert m.betreff.startswith("Grüße und ")  # unbekannter Zeichensatz, kaputtes Base64
    assert m.absender_name == "Jürgen Beispiel"  # rohe UTF-8-Bytes in der Kopfzeile
    assert m.an == "Grün <gruen@example.com>"  # rohe Latin-1-Bytes
    assert "Falsch angegeben: Grüße aus Köln." in m.text
    assert "85 € pro Stunde – wie besprochen" in m.text
    assert "Unbekannter Zeichensatz: Straße." in m.text


def test_antwort_ohne_zitat_mit_kurzer_signatur():
    m = lesen("antwort.eml")
    assert m.text.startswith("Hallo Herr Beispiel,")
    assert "verschiebt sich auf 11:00 Uhr" in m.text
    assert "Sachgebiet Hörbehinderung" in m.text  # Anfang der Signatur bleibt
    assert "vertrauliche" not in m.text  # Rechtshinweis fällt weg
    assert "schrieb" not in m.text and "bestätige den Einsatz" not in m.text


def test_weitergeleitete_mail_bleibt_vollstaendig():
    m = lesen("weitergeleitet.eml")
    assert m.text.startswith("Zur Info, bitte die Rechnung an das Integrationsamt schicken.")
    assert "---------- Forwarded message ---------" in m.text
    assert "Kassenzeichen: 5500.1234.5678" in m.text
    assert "> Bitte um Kostenzusage" in m.text  # auch Zitate innerhalb der Weiterleitung


def test_fehlende_message_id_stabiler_ersatz():
    erste, zweite = lesen("ohne_message_id.eml"), lesen("ohne_message_id.eml", uid=99)
    assert re.fullmatch(r"<ersatz\.[0-9a-f]{40}@arbeitsassistent>", erste.message_id)
    assert erste.message_id == zweite.message_id
    anderer = eml("ohne_message_id.eml").replace(b"Absage Vereinsabend", b"Absage Sommerfest")
    assert mail.mail_aus_bytes(anderer).message_id != erste.message_id


def test_kaputtes_oder_fehlendes_datum():
    m = lesen("kaputtes_datum.eml")
    assert m.datum is None and m.betreff == "Zahlung angewiesen"
    assert m.absender_name == "" and m.absender_adresse == "erika.mustermann@example.org"
    ohne = eml("kaputtes_datum.eml").replace(b"Date: Donnerstag, 32. Oktember 2026 25:61\n", b"")
    assert mail.mail_aus_bytes(ohne).datum is None


def test_datum_ohne_zeitzone_gilt_als_utc():
    roh = b"From: a@example.org\nDate: Mon, 05 Oct 2026 09:00:00 -0000\n\nx\n"
    assert mail.mail_aus_bytes(roh).datum == datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


def test_absender_mit_komma_ohne_anfuehrungszeichen():
    roh = b"From: Mustermann, Erika <Erika.Mustermann@Example.org>\n\nx\n"
    m = mail.mail_aus_bytes(roh)
    assert (m.absender_name, m.absender_adresse) == ("Mustermann, Erika", "erika.mustermann@example.org")


def test_text_hoechstens_20000_zeichen():
    nachricht = EmailMessage()
    nachricht.set_content("Langer Text. " * 3000)
    assert len(mail.mail_aus_bytes(nachricht.as_bytes()).text) == 20_000


def test_als_anhang_weitergeleitete_mail():
    innen = EmailMessage()
    innen["From"] = "Integrationsamt Musterland <auftraege@integrationsamt-musterland.example>"
    innen["Subject"] = "Kostenzusage"
    innen["Date"] = "Fri, 02 Oct 2026 11:00:00 +0200"
    innen.set_content("Die Kosten werden übernommen. Kassenzeichen 5500.1234.5678")
    aussen = EmailMessage()
    aussen["Subject"] = "WG: Kostenzusage"
    aussen.set_content("Zur Info.")
    aussen.add_attachment(innen)
    m = mail.mail_aus_bytes(aussen.as_bytes())
    assert m.text.startswith("Zur Info.\n\n---------- Angehängte Nachricht ----------")
    assert "Betreff: Kostenzusage" in m.text and "Kassenzeichen 5500.1234.5678" in m.text
    assert m.anhaenge == ["Kostenzusage.eml"]


# --- Zitate -------------------------------------------------------------------------

OUTLOOK = (
    "Passt, ich komme um 10 Uhr.\n\n"
    "________________________________\n"
    "Von: Erika Mustermann <erika.mustermann@example.org>\n"
    "Gesendet: Montag, 5. Oktober 2026 09:12\n"
    "An: Max Beispiel <max.beispiel@example.org>\n"
    "Betreff: Einsatz am 14.10.\n\n"
    "Können Sie am 14.10. dolmetschen?"
)


def test_outlook_antwort_ohne_zitatzeichen():
    assert mail.zitate_kuerzen(OUTLOOK, "AW: Einsatz am 14.10.") == "Passt, ich komme um 10 Uhr."


def test_outlook_weiterleitung_bleibt():
    text = mail.zitate_kuerzen(OUTLOOK, "WG: Einsatz am 14.10.")
    assert "Gesendet: Montag" in text and "Können Sie am 14.10. dolmetschen?" in text


def test_gmx_kopfblock_und_alte_trennlinie():
    gmx = ("Ja, gern.\n\nGesendet: Montag, 05. Oktober 2026 um 09:12 Uhr\nVon: \"Erika\" "
           "<erika@example.org>\nAn: max@example.org\nBetreff: Termin\nAlter Text")
    assert mail.zitate_kuerzen(gmx) == "Ja, gern."
    alt = "Danke!\n\n-----Ursprüngliche Nachricht-----\nVon: Erika\nAlter Text"
    assert mail.zitate_kuerzen(alt, "AW: Termin") == "Danke!"


def test_antwort_unter_dem_zitat_bleibt():
    text = ("Am 05.10.2026 um 09:12 schrieb Erika Mustermann:\n"
            "> Können Sie am 14.10.?\n\nJa, das passt.\n> Und am 15.10.?\nAuch das.")
    assert mail.zitate_kuerzen(text) == "Ja, das passt.\nAuch das."


def test_nur_zitat_gibt_alles_zurueck():
    text = "> Nur zitierter Text\n> zweite Zeile"
    assert mail.zitate_kuerzen(text) == text


def test_kein_kopfblock_bei_normalen_angaben():
    text = "Einsatz:\nDatum: 14.10.2026\nVon: 10:00\nAn: Amtsgericht Musterstadt\nOrt: Saal 3"
    assert mail.zitate_kuerzen(text) == text


def test_lange_strichzeile_ist_keine_signatur():
    zeilen = "\n".join(f"Punkt {i}" for i in range(40))
    text = f"Ablauf:\n--\n{zeilen}"
    assert mail.zitate_kuerzen(text) == text
