"""Liest Mails per IMAP, ohne am Postfach etwas zu ändern.

Der Ordner wird nur lesend geöffnet (EXAMINE) und Nachrichten werden ausschließlich mit
BODY.PEEK[] geholt, das setzt kein \\Seen. STORE, EXPUNGE, COPY und MOVE kommen nicht vor.
Der Abruf ist inkrementell über UIDVALIDITY und die höchste bereits gesehene UID.
"""

import base64
import email
import email.header
import email.policy
import email.utils
import hashlib
import imaplib
import mimetypes
import re
import socket
import ssl
from contextlib import contextmanager
from datetime import date, datetime, timezone
from html.parser import HTMLParser

from .modell import Mail, MailKonto

ZEITLIMIT = 30  # Sekunden für Verbindung und jede Antwort des Servers
HOECHSTENS_ZEICHEN = 20_000
# Je Mail wird höchstens 1 MB geladen. Text steht vorn, große Anhänge werden so nicht ganz
# übertragen; die Namen weiterer Anhänge hinter einem sehr großen können dann fehlen.
HOECHSTENS_BYTES = 1024 * 1024
_PAKET = 50  # UIDs je FETCH
_SIGNATURZEILEN = 6  # so viele Zeilen der Signatur bleiben (Name, Stelle, Telefon …)
_MONATE = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Reihenfolge = Anzeige. Stand Oktober 2026, Server und Ports aus den Hilfeseiten der Anbieter
# (hilfe.gmx.net, hilfe.web.de, support.google.com/mail/answer/7126229, support.apple.com/102525,
# posteo.de/en/help, ionos.de/hilfe, strato.de/faq, kundenservice.freenet.de, help.yahoo.com
# SLN4075, Microsoft „POP, IMAP, and SMTP settings for Outlook.com“). Nur aus Drittquellen:
# T-Online-Server, mailbox.org-Server, imap.1und1.de, das Einschalten von IMAP bei freenet und
# das Passwort bei aktiver Zwei-Faktor-Anmeldung bei WEB.DE (wie bei GMX angenommen).
ANBIETER: dict[str, dict] = {
    "gmx": {
        "name": "GMX",
        "host": "imap.gmx.net",
        "port": 993,
        "hilfe": "Bei GMX ist der Zugriff für andere Programme zuerst ausgeschaltet. Melden "
        "Sie sich im Browser bei GMX an und setzen Sie unter Einstellungen → „POP3/IMAP "
        "Abruf“ den Haken bei „POP3 und IMAP Zugriff erlauben“. Benutzername ist Ihre "
        "E-Mail-Adresse. Haben Sie die Zwei-Faktor-Authentifizierung eingeschaltet, tragen "
        "Sie hier ein anwendungsspezifisches Passwort aus den GMX-Sicherheitseinstellungen ein.",
        "unterstuetzt": True,
    },
    "webde": {
        "name": "WEB.DE",
        "host": "imap.web.de",
        "port": 993,
        "hilfe": "Bei WEB.DE ist der Zugriff für andere Programme zuerst ausgeschaltet. "
        "Melden Sie sich im Browser bei WEB.DE an und erlauben Sie unter "
        "E-Mail-Einstellungen → „POP3/IMAP“ den Zugriff per POP3 und IMAP. Benutzername ist "
        "Ihre E-Mail-Adresse. Mit Zwei-Faktor-Authentifizierung brauchen Sie ein "
        "anwendungsspezifisches Passwort.",
        "unterstuetzt": True,
    },
    "gmail": {
        "name": "Gmail",
        "host": "imap.gmail.com",
        "port": 993,
        "hilfe": "Google verlangt ein App-Passwort, Ihr normales Google-Passwort funktioniert "
        "hier nicht. Schalten Sie im Google-Konto die „Bestätigung in zwei Schritten“ ein, "
        "erstellen Sie dann unter myaccount.google.com/apppasswords ein App-Passwort und "
        "tragen Sie die 16 Zeichen hier ein.",
        "unterstuetzt": True,
    },
    "icloud": {
        "name": "iCloud",
        "host": "imap.mail.me.com",
        "port": 993,
        "hilfe": "Apple verlangt ein app-spezifisches Passwort. Erstellen Sie es auf "
        "account.apple.com unter „Anmelden und Sicherheit“ → „App-spezifische Passwörter“. "
        "Benutzername ist Ihre iCloud-E-Mail-Adresse; klappt das nicht, nur der Teil vor "
        "dem @.",
        "unterstuetzt": True,
    },
    "tonline": {
        "name": "T-Online",
        "host": "secureimap.t-online.de",
        "port": 993,
        "hilfe": "Tragen Sie nicht das Passwort für den Telekom-Login ein, sondern das "
        "„Passwort für E-Mail-Programme“. Sie legen es im E-Mail Center unter Einstellungen "
        "→ Passwörter fest.",
        "unterstuetzt": True,
    },
    "posteo": {
        "name": "Posteo",
        "host": "posteo.de",
        "port": 993,
        "hilfe": "Benutzername ist Ihre Posteo-Adresse. Nutzen Sie bei Posteo App-Passwörter, "
        "tragen Sie hier eines ein (Einstellungen → Passwort und Sicherheit).",
        "unterstuetzt": True,
    },
    "mailboxorg": {
        "name": "mailbox.org",
        "host": "imap.mailbox.org",
        "port": 993,
        "hilfe": "Benutzername ist Ihre E-Mail-Adresse. Mit Zwei-Faktor-Anmeldung brauchen "
        "Sie ein App-Passwort für E-Mail (Einstellungen → Sicherheit → "
        "E-Mail-App-Passwörter).",
        "unterstuetzt": True,
    },
    "ionos": {
        "name": "IONOS (1&1)",
        "host": "imap.ionos.de",
        "port": 993,
        "hilfe": "Benutzername ist die vollständige E-Mail-Adresse, Passwort das des "
        "Postfachs (nicht das des IONOS-Kundenkontos). Für eine Adresse aus einem "
        "1&1-Internetvertrag (z. B. @online.de) lautet der Server imap.1und1.de.",
        "unterstuetzt": True,
    },
    "strato": {
        "name": "Strato",
        "host": "imap.strato.de",
        "port": 993,
        "hilfe": "Benutzername ist die vollständige E-Mail-Adresse, Passwort das des "
        "Postfachs. Es wird im Strato-Kundenbereich unter „E-Mail“ festgelegt.",
        "unterstuetzt": True,
    },
    "freenet": {
        "name": "freenet",
        "host": "mx.freenet.de",
        "port": 993,
        "hilfe": "Schalten Sie zuerst in freenet Mail im Browser unter Einstellungen → "
        "„POP3/IMAP“ den Zugriff per POP3, IMAP und SMTP ein. Benutzername ist Ihre "
        "E-Mail-Adresse.",
        "unterstuetzt": True,
    },
    "yahoo": {
        "name": "Yahoo",
        "host": "imap.mail.yahoo.com",
        "port": 993,
        "hilfe": "Yahoo verlangt ein App-Passwort. Erstellen Sie es in den Kontoeinstellungen "
        "unter „Kontosicherheit“ → „App-Passwort generieren“ und tragen Sie es hier ein.",
        "unterstuetzt": True,
    },
    "outlook": {
        "name": "Outlook / Hotmail",
        "host": "outlook.office365.com",
        "port": 993,
        "hilfe": "Microsoft lässt Outlook.com, Hotmail und Live seit dem 16. September 2024 "
        "nicht mehr mit Passwort lesen, sondern nur noch über die Microsoft-Anmeldung im "
        "Browser. Das kann dieses Programm nicht. Ausweg: In Outlook.com eine automatische "
        "Weiterleitung an eine Adresse bei einem anderen Anbieter einrichten und diese hier "
        "verbinden.",
        "unterstuetzt": False,
    },
    "anderer": {
        "name": "Anderer Anbieter",
        "host": "",
        "port": 993,
        "hilfe": "Serveradresse und Port finden Sie in der Hilfe Ihres Anbieters unter dem "
        "Stichwort „IMAP“. Nötig ist IMAP mit SSL/TLS, meist Port 993. Manche Anbieter "
        "verlangen ein eigenes App-Passwort oder dass IMAP erst eingeschaltet wird.",
        "unterstuetzt": True,
    },
}
_WEITERE_HOSTS = {
    "imap.gmx.de": "gmx",
    "imap-mail.outlook.com": "outlook",
    "imap.1und1.de": "ionos",
    "imap.ionos.com": "ionos",
}


class MailFehler(Exception):
    """Abruf nicht möglich. Die Meldung ist für Laien gedacht und sagt, was zu tun ist."""


def verbindung_testen(konto: MailKonto, passwort: str) -> list[str]:
    """Meldet sich an und gibt die Ordner des Postfachs zurück (INBOX zuerst)."""
    with _sitzung(konto, passwort) as imap:
        return _ordnerliste(imap)


def abrufen(
    konto: MailKonto,
    passwort: str,
    seit_datum: date,
    uidvalidity: int | None = None,
    nach_uid: int = 0,
    hoechstens: int = 300,
) -> tuple[list[Mail], int, int]:
    """Holt neue Mails aus konto.ordner: (Mails aufsteigend nach UID, UIDVALIDITY, höchste UID).

    Die beiden Zahlen beim nächsten Aufruf als uidvalidity und nach_uid übergeben. Passt
    UIDVALIDITY nicht mehr (oder erster Abruf), wird alles ab seit_datum neu gelesen; dann
    über message_id doppelte erkennen.
    Mehr als hoechstens neue Mails: Beim ersten Abruf kommen die neuesten, ältere entfallen.
    Danach geht es lückenlos weiter: die ältesten neuen zuerst, der Rest beim nächsten Abruf.
    """
    hoechstens = max(1, hoechstens)
    with _sitzung(konto, passwort) as imap:
        gueltigkeit = _ordner_oeffnen(imap, konto.ordner)
        if uidvalidity == gueltigkeit and nach_uid > 0:
            # „n:*“ liefert immer auch die höchste UID, selbst wenn sie kleiner als n ist.
            uids = [u for u in _suchen(imap, "UID", f"{nach_uid + 1}:*") if u > nach_uid]
            auswahl = uids[:hoechstens]
            hoechste = auswahl[-1] if auswahl else nach_uid
        else:
            uids = _suchen(imap, "SINCE", _imap_datum(seit_datum))
            auswahl = uids[-hoechstens:]
            hoechste = uids[-1] if uids else 0
        mails = _holen(imap, auswahl, konto.ordner)
    return mails, gueltigkeit, hoechste


def mail_aus_bytes(roh: bytes, uid: int = 0, ordner: str = "INBOX") -> Mail:
    """Macht aus einer Rohmail (RFC 5322) eine Mail mit Klartext. Wirft nicht bei Unsinn."""
    nachricht = email.message_from_bytes(roh, policy=email.policy.default)
    kopf = _kopfzeilen(nachricht)
    betreff = _kopf_dekodieren(kopf.get("subject", ""))
    name, adresse = _absender(kopf.get("from", ""))
    datum_roh = " ".join(kopf.get("date", "").split())
    message_id = _message_id(_kopf_dekodieren(kopf.get("message-id", "")))
    if not message_id:
        schluessel = f"{datum_roh}\n{adresse}\n{betreff}".encode("utf-8", "surrogatepass")
        message_id = f"<ersatz.{hashlib.sha1(schluessel).hexdigest()}@arbeitsassistent>"
    texte: list[str] = []
    anhaenge: list[str] = []
    _durchgehen(nachricht, texte, anhaenge, betreff)
    text = "\n\n".join(t for t in texte if t)
    return Mail(
        message_id=message_id,
        datum=_datum(datum_roh),
        absender_name=name,
        absender_adresse=adresse,
        betreff=betreff,
        text=text[:HOECHSTENS_ZEICHEN],
        an=_kopf_dekodieren(kopf.get("to", ""))[:500],
        anhaenge=anhaenge,
        uid=uid,
        ordner=ordner,
    )


# --- Text ---------------------------------------------------------------------------


class _HtmlText(HTMLParser):
    WEG = {"script", "style", "head", "title"}
    ABSATZ = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "blockquote", "pre", "hr"}
    ZEILE = {"div", "tr", "li", "dt", "dd", "section", "article", "header", "footer", "center", "address"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.teile: list[str] = []
        self.weg = 0
        self.pre = 0

    def _umbruch(self, anzahl: int):
        ende = "".join(self.teile[-4:])
        vorhanden = ende[len(ende.rstrip()):].count("\n")
        if vorhanden < anzahl:
            self.teile.append("\n" * (anzahl - vorhanden))

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.weg = 0  # falls </head> fehlt
        elif tag in self.WEG:
            self.weg += 1
        elif tag == "br":
            self.teile.append("\n")
        elif tag in ("td", "th"):
            self.teile.append(" ")
        elif tag in self.ABSATZ:
            self._umbruch(2)
        elif tag in self.ZEILE:
            self._umbruch(1)
        if tag == "li":
            self.teile.append("- ")
        elif tag == "pre":
            self.pre += 1

    def handle_endtag(self, tag):
        if tag in self.WEG:
            self.weg = max(0, self.weg - 1)
        elif tag in self.ABSATZ:
            self._umbruch(2)
        elif tag in self.ZEILE:
            self._umbruch(1)
        if tag == "pre":
            self.pre = max(0, self.pre - 1)

    def handle_data(self, data):
        if not self.weg:
            self.teile.append(data if self.pre else re.sub(r"\s+", " ", data))


def html_zu_text(html: str) -> str:
    leser = _HtmlText()
    try:
        leser.feed(html)
        leser.close()
    except Exception:
        pass  # kaputtes HTML: nehmen, was bis dahin gelesen wurde
    return _leerraum("".join(leser.teile))


def _leerraum(text: str) -> str:
    """Einheitliche Zeilenenden, Leerzeichen zusammengefasst, höchstens eine Leerzeile."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub("[​‌‍⁠﻿­]", "", text)
    zeilen: list[str] = []
    for zeile in text.split("\n"):
        zeile = " ".join(zeile.split())
        if zeile or (zeilen and zeilen[-1]):
            zeilen.append(zeile)
    return "\n".join(zeilen).strip()


# Weitergeleitetes bleibt immer: Darin steht oft das Wichtige (Auftrag, Aktenzeichen).
_WEITERGELEITET = re.compile(
    r"^(?:-{2,} ?(?:forwarded message|weitergeleitete nachricht|weitergeleitete e-mail) ?-{2,}"
    r"|anfang der weitergeleiteten (?:nachricht|e-mail):?|begin forwarded message:?)$",
    re.IGNORECASE,
)
_ANTWORTKOPF = re.compile(
    r"^(?:am .{4,200} schrieb .{1,200}:|on .{4,200} wrote:|.{1,150} schrieb am .{4,80}:"
    r"|-{2,} ?(?:ursprüngliche nachricht|original message|original-nachricht) ?-{2,})$",
    re.IGNORECASE,
)
_KOPFFELD = re.compile(
    r"^\*?(von|from|gesendet|sent|datum|date|an|to|betreff|subject|cc)\s*:\*?\s*\S", re.IGNORECASE
)
_FELDART = {"from": "von", "gesendet": "datum", "sent": "datum", "date": "datum", "to": "an",
            "subject": "betreff"}
_WG_BETREFF = re.compile(r"^\s*\[?(?:wg|fwd?|fw|tr|weiterleitung)\s*:", re.IGNORECASE)


def _antwortkopf(zeilen: list[str], i: int) -> int:
    """Anzahl Zeilen eines Antwortkopfs ab Zeile i, sonst 0."""
    zeile = zeilen[i].strip()
    if _ANTWORTKOPF.match(zeile):
        return 1
    # Gmail bricht „Am … schrieb Name <adresse>:“ auf zwei Zeilen um.
    if i + 1 < len(zeilen) and re.match(r"(?i)(am|on) ", zeile):
        if _ANTWORTKOPF.match(f"{zeile} {zeilen[i + 1].strip()}"):
            return 2
    # Kopfblock wie bei Outlook oder GMX: Von, Gesendet, An, Betreff
    felder, j = set(), i
    while j < min(len(zeilen), i + 8) and (treffer := _KOPFFELD.match(zeilen[j].strip())):
        art = treffer.group(1).lower()
        felder.add(_FELDART.get(art, art))
        j += 1
    if {"von", "betreff"} <= felder and len(felder) >= 3:
        return j - i
    return 0


def zitate_kuerzen(text: str, betreff: str = "") -> str:
    """Entfernt zitierte frühere Nachrichten und lange Signaturen.

    - Weitergeleitetes bleibt vollständig, ebenso bei Betreff „WG:“/„Fwd:“ ein Kopfblock wie
      bei Outlook (dort sehen Antwort und Weiterleitung gleich aus).
    - Antwortkopf mit „>“-Zitat danach: nur Kopf und „>“-Zeilen fallen weg, eigener Text
      darunter (Antwort unten oder zwischen den Zitaten) bleibt.
    - Antwortkopf ohne „>“ (Outlook): alles ab dem Kopf fällt weg.
    - Signatur nach „-- “: nur die ersten Zeilen bleiben (Name, Stelle), lange
      Rechtshinweise fallen weg.
    Bleibt nichts übrig, kommt der ganze Text zurück.
    """
    zeilen = text.split("\n")
    weiterleitung = bool(_WG_BETREFF.match(betreff or ""))
    eigen: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(zeilen):
        zeile = zeilen[i]
        if _WEITERGELEITET.match(zeile.strip()):
            rest = zeilen[i:]
            break
        kopf = _antwortkopf(zeilen, i)
        if kopf:
            if weiterleitung:
                rest = zeilen[i:]
                break
            danach = next((z for z in zeilen[i + kopf:] if z.strip()), "")
            if not danach.lstrip().startswith(">"):
                break
            i += kopf
            continue
        if not zeile.lstrip().startswith(">"):
            eigen.append(zeile)
        i += 1
    eigen = _signatur_kuerzen(eigen)
    while eigen and re.fullmatch(r"[\s_\-=*]*", eigen[-1]):
        eigen.pop()  # Trennlinie vor einem abgeschnittenen Zitat
    ergebnis = _leerraum("\n".join(eigen + ([""] if eigen and rest else []) + rest))
    return ergebnis or _leerraum(text)


def _signatur_kuerzen(zeilen: list[str]) -> list[str]:
    for k, zeile in enumerate(zeilen):
        if zeile.strip() == "--":
            signatur = [z for z in zeilen[k + 1:] if z.strip()]
            if len(signatur) <= 25:  # länger ist eher keine Signatur, dann alles behalten
                return zeilen[:k + 1] + signatur[:_SIGNATURZEILEN]
            break
    return zeilen


# --- Rohmail ------------------------------------------------------------------------

# Werden oft falsch angegeben: dann erst UTF-8, sonst Windows-1252 (Obermenge von Latin-1).
_EINFACHE_ZEICHENSAETZE = {
    "", "us-ascii", "ascii", "iso-8859-1", "iso8859-1", "latin-1", "latin1", "l1",
    "windows-1252", "cp1252", "unknown-8bit", "x-unknown",
}
_OHNE_ANHANG = {"application/pgp-signature", "application/pkcs7-signature",
                "application/x-pkcs7-signature"}


def _text_aus_bytes(daten: bytes, zeichensatz: str | None) -> str:
    zs = (zeichensatz or "").strip().strip('"').lower()
    kandidaten = ("utf-8", "cp1252") if zs in _EINFACHE_ZEICHENSAETZE else (zs, "utf-8")
    for kandidat in kandidaten:
        try:
            return daten.decode(kandidat)
        except (LookupError, UnicodeDecodeError):
            pass
    return daten.decode("cp1252", errors="replace")


def _kopf_dekodieren(wert: str) -> str:
    """RFC 2047 („=?UTF-8?Q?…?=“), auch mit unbekanntem Zeichensatz und rohen 8-Bit-Bytes."""
    wert = (wert or "").replace("\r", "").replace("\n", "")
    if re.search("[\udc80-\udcff]", wert):  # 8-Bit-Bytes ohne Kodierung
        wert = _text_aus_bytes(wert.encode("utf-8", "surrogateescape"), None)
    try:
        text = _woerter_zusammensetzen(email.header.decode_header(wert))
    except Exception:  # ein kaputtes Wort: die übrigen einzeln dekodieren
        text = re.sub(r"=\?[^?\s]+\?[bBqQ]\?[^?\s]*\?=", _einzelnes_wort, wert)
    return " ".join(text.split())


def _woerter_zusammensetzen(teile: list) -> str:
    return "".join(t if isinstance(t, str) else _text_aus_bytes(t, zs) for t, zs in teile)


def _einzelnes_wort(treffer) -> str:
    try:
        return _woerter_zusammensetzen(email.header.decode_header(treffer.group(0)))
    except Exception:
        return treffer.group(0)


def _kopfzeilen(nachricht) -> dict[str, str]:
    """Rohe Kopfzeilen, die erste gewinnt. Dekodiert wird selbst, das ist robuster."""
    kopf: dict[str, str] = {}
    for name, wert in nachricht.raw_items():
        kopf.setdefault(name.lower(), str(wert))
    return kopf


def _absender(roh: str) -> tuple[str, str]:
    roh = roh.replace("\r", "").replace("\n", "")
    name, adresse = email.utils.parseaddr(roh)
    if "@" not in adresse:  # z. B. „Mustermann, Erika <…>“ ohne Anführungszeichen
        treffer = re.search(r"<\s*([^<>\s]+@[^<>\s]+?)\s*>", roh) or re.search(
            r"[^\s<>\"',;:]+@[^\s<>\"',;:]+", roh
        )
        if not treffer:
            return _kopf_dekodieren(roh), ""
        adresse = treffer.group(treffer.lastindex or 0)
        name = roh[: treffer.start()] if treffer.lastindex else ""
    return _kopf_dekodieren(name).strip(" \"'"), _kopf_dekodieren(adresse).lower()


def _datum(wert: str) -> datetime | None:
    if not wert:
        return None
    try:
        datum = email.utils.parsedate_to_datetime(wert)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if datum.tzinfo is None:  # „-0000“: Zeitzone unbekannt, gilt als UTC
        datum = datum.replace(tzinfo=timezone.utc)
    try:
        return datum.astimezone()  # Ortszeit des Rechners, mit Zeitzone
    except (OverflowError, ValueError, OSError):
        return datum


def _message_id(wert: str) -> str:
    wert = " ".join((wert or "").split())
    treffer = re.search(r"<[^<>\s]+>", wert)
    if treffer:
        return treffer.group(0)
    return wert if wert and " " not in wert else ""


def _dateiname(teil) -> str:
    try:
        name = teil.get_filename() or ""
    except Exception:
        name = teil.get_param("filename", "", "content-disposition") or teil.get_param("name", "")
        name = name if isinstance(name, str) else ""
    name = _kopf_dekodieren(str(name))
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _teil_text(teil) -> str:
    try:
        daten = teil.get_payload(decode=True) or b""
    except Exception:
        daten = b""
    if isinstance(daten, str):
        daten = daten.encode("utf-8", "surrogateescape")
    try:
        zeichensatz = teil.get_content_charset()
    except Exception:
        zeichensatz = None
    return _text_aus_bytes(daten, zeichensatz)


def _durchgehen(teil, texte: list[str], anhaenge: list[str], betreff: str) -> None:
    """Sammelt Klartext und Anhangnamen. text/plain vor HTML, je multipart/alternative."""
    typ = teil.get_content_type()
    if typ == "message/rfc822":  # als Anhang weitergeleitete Mail
        innen = teil.get_payload()
        innen = innen[0] if isinstance(innen, list) and innen else None
        if innen is None:
            return
        kopf = _kopfzeilen(innen)
        innen_betreff = _kopf_dekodieren(kopf.get("subject", ""))
        anhaenge.append(_dateiname(teil) or f"{innen_betreff or 'Nachricht'}.eml")
        innen_texte: list[str] = []
        _durchgehen(innen, innen_texte, anhaenge, innen_betreff)
        if any(innen_texte):
            texte.append(
                "---------- Angehängte Nachricht ----------\n"
                f"Von: {_kopf_dekodieren(kopf.get('from', ''))}\n"
                f"Datum: {_kopf_dekodieren(kopf.get('date', ''))}\n"
                f"Betreff: {innen_betreff}"
            )
            texte.extend(innen_texte)
        return
    if teil.is_multipart():
        kinder = [k for k in teil.get_payload() if not isinstance(k, str)]
        if typ != "multipart/alternative":
            for kind in kinder:
                _durchgehen(kind, texte, anhaenge, betreff)
            return
        fassungen = []
        for kind in kinder:
            kind_texte: list[str] = []
            kind_anhaenge: list[str] = []
            _durchgehen(kind, kind_texte, kind_anhaenge, betreff)
            fassungen.append((kind.get_content_type(), kind_texte))
            # Apple Mail legt Anhänge in die HTML-Fassung: Namen aus allen Fassungen nehmen.
            anhaenge.extend(n for n in kind_anhaenge if n not in anhaenge)
        nur_text = [t for art, t in fassungen if art == "text/plain" and any(t)]
        sonst = [t for _, t in reversed(fassungen) if any(t)]
        texte.extend((nur_text or sonst or [[]])[0])
        return
    name = _dateiname(teil)
    ist_text = typ in ("text/plain", "text/html") or typ.startswith("multipart/")  # ohne Boundary
    if teil.get_content_disposition() != "attachment" and not name and ist_text:
        roh = _teil_text(teil)
        text = html_zu_text(roh) if typ == "text/html" else _leerraum(roh)
        if text:
            texte.append(zitate_kuerzen(text, betreff))
        return
    if typ in _OHNE_ANHANG:
        return
    eingebettet = "content-id" in teil and teil.get_content_disposition() != "attachment"
    if typ.startswith("image/") and eingebettet:
        return  # Bild in der Signatur o. Ä.
    anhaenge.append(name or "Anhang" + (mimetypes.guess_extension(typ) or ""))


# --- IMAP ---------------------------------------------------------------------------


def _anbieter(host: str) -> dict | None:
    host = (host or "").strip().lower()
    schluessel = _WEITERE_HOSTS.get(host)
    if schluessel:
        return ANBIETER[schluessel]
    return next((a for a in ANBIETER.values() if a["host"] and a["host"] == host), None)


def _imap_datum(tag: date) -> str:
    """Datum für SEARCH SINCE, mit englischem Monat unabhängig von der Systemsprache."""
    return f"{tag.day:02d}-{_MONATE[tag.month - 1]}-{tag.year}"


def _utf7_kodieren(name: str) -> str:
    """Ordnernamen für IMAP (modifiziertes UTF-7, RFC 3501): „Entwürfe“ → „Entw&APw-rfe“."""

    def kodieren(treffer):
        roh = base64.b64encode(treffer.group(0).encode("utf-16-be")).decode("ascii")
        return "&" + roh.rstrip("=").replace("/", ",") + "-"

    return re.sub(r"[^\x20-\x7e]+", kodieren, name.replace("&", "&-"))


def _utf7_dekodieren(name: str) -> str:
    def dekodieren(treffer):
        inhalt = treffer.group(1)
        if not inhalt:
            return "&"
        roh = inhalt.replace(",", "/")
        try:
            return base64.b64decode(roh + "=" * (-len(roh) % 4)).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return treffer.group(0)

    return re.sub(r"&([A-Za-z0-9+,]*)-", dekodieren, name)


def _ordner_quoten(name: str) -> str:
    kodiert = _utf7_kodieren(name)
    return '"' + kodiert.replace("\\", "\\\\").replace('"', '\\"') + '"'


_LISTENZEILE = re.compile(r'^\((?P<merkmale>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.*)$', re.I)


def _ordnerliste(imap) -> list[str]:
    typ, daten = imap.list()
    if typ != "OK":
        return []
    namen = []
    for eintrag in daten or []:
        if isinstance(eintrag, tuple):  # Name als Literal: (b'(...) "/" {12}', b'Name')
            zeile, name = eintrag[0].decode(errors="replace"), eintrag[1].decode(errors="replace")
            treffer = _LISTENZEILE.match(zeile)
        elif isinstance(eintrag, bytes):
            treffer = _LISTENZEILE.match(eintrag.decode(errors="replace"))
            name = treffer.group("name").strip() if treffer else ""
            if len(name) >= 2 and name[0] == name[-1] == '"':
                name = re.sub(r"\\(.)", r"\1", name[1:-1])
        else:
            continue
        if not treffer or "\\noselect" in treffer.group("merkmale").lower():
            continue
        namen.append(_utf7_dekodieren(name))
    return sorted(namen, key=lambda n: n.upper() != "INBOX")


def _ordner_oeffnen(imap, ordner: str) -> int:
    """Öffnet den Ordner nur lesend (EXAMINE) und gibt UIDVALIDITY zurück."""
    try:
        typ, _ = imap.select(_ordner_quoten(ordner), readonly=True)
    except imaplib.IMAP4.abort:
        raise
    except imaplib.IMAP4.error:
        typ = "NO"
    if typ != "OK":
        vorhanden = ", ".join(_ordnerliste(imap)) or "keine"
        raise MailFehler(
            f"Den Ordner „{ordner}“ gibt es in diesem Postfach nicht. Vorhandene Ordner: "
            f"{vorhanden}. Bitte in den Einstellungen einen davon auswählen."
        )
    wert = (imap.response("UIDVALIDITY")[1] or [None])[0]
    try:
        return int(wert)
    except (TypeError, ValueError):
        return 0


def _suchen(imap, *kriterien: str) -> list[int]:
    typ, daten = imap.uid("SEARCH", *kriterien)
    if typ != "OK":
        raise MailFehler("Der Mail-Server hat die Suche nach neuen Mails abgelehnt. "
                         "Bitte später noch einmal versuchen.")
    return sorted({int(x) for zeile in daten or [] if zeile for x in zeile.split() if x.isdigit()})


def _fetch_zerlegen(daten: list) -> list[tuple[int, bytes]]:
    """imaplib-FETCH-Antwort → [(uid, rohmail)]. UID kann vor oder hinter dem Literal stehen."""
    eintraege: list[list] = []
    for teil in daten or []:
        if isinstance(teil, tuple) and len(teil) >= 2:
            eintraege.append([teil[0], teil[1] or b""])
        elif isinstance(teil, bytes) and eintraege and not re.match(rb"\d+ \(", teil):
            eintraege[-1][0] += b" " + teil
    ergebnis = []
    for kopf, roh in eintraege:
        treffer = re.search(rb"UID (\d+)", kopf)
        if treffer:
            ergebnis.append((int(treffer.group(1)), roh))
    return ergebnis


def _holen(imap, uids: list[int], ordner: str) -> list[Mail]:
    gewuenscht = set(uids)
    mails: dict[int, Mail] = {}
    for start in range(0, len(uids), _PAKET):
        menge = ",".join(str(u) for u in uids[start:start + _PAKET])
        typ, daten = imap.uid("FETCH", menge, f"(UID BODY.PEEK[]<0.{HOECHSTENS_BYTES}>)")
        if typ != "OK":
            raise MailFehler("Der Mail-Server hat den Abruf der Mails abgelehnt. "
                             "Bitte später noch einmal versuchen.")
        for uid, roh in _fetch_zerlegen(daten):
            if uid in gewuenscht and uid not in mails:
                mails[uid] = mail_aus_bytes(roh, uid, ordner)
    return [mails[u] for u in sorted(mails)]


def _verbindungsfehler(host: str, port: int, fehler: Exception) -> str:
    if isinstance(fehler, TimeoutError):
        return (f"Der Mail-Server „{host}“ antwortet nicht (nach {ZEITLIMIT} Sekunden "
                "abgebrochen). Bitte die Internetverbindung prüfen und es später noch einmal "
                "versuchen.")
    if isinstance(fehler, socket.gaierror):
        return (f"Der Mail-Server „{host}“ wurde nicht gefunden. Bitte die Serveradresse "
                "prüfen und ob der Mac mit dem Internet verbunden ist.")
    if isinstance(fehler, ssl.SSLCertVerificationError):
        return (f"Das Sicherheitszertifikat von „{host}“ ist nicht gültig. Bitte die "
                "Serveradresse prüfen.")
    if isinstance(fehler, ssl.SSLError):
        return (f"Mit „{host}“ kam keine verschlüsselte Verbindung zustande. Bitte "
                "Serveradresse und Port prüfen (meist 993 mit SSL/TLS).")
    if isinstance(fehler, ConnectionRefusedError):
        return (f"„{host}“ nimmt auf Port {port} keine Verbindung an. Bitte den Port prüfen "
                "(meist 993).")
    return (f"Die Verbindung zum Mail-Server „{host}“ ist fehlgeschlagen oder wurde "
            "unterbrochen. Bitte die Internetverbindung prüfen und es später noch einmal "
            "versuchen.")


def _anmeldefehler(host: str) -> str:
    anbieter = _anbieter(host)
    if anbieter and not anbieter["unterstuetzt"]:
        return anbieter["hilfe"]
    text = ("Die Anmeldung wurde abgelehnt. Bitte Benutzername (meist die E-Mail-Adresse) "
            "und Passwort prüfen.")
    if anbieter:
        return f"{text} {anbieter['hilfe']}"
    return (f"{text} Manche Anbieter verlangen dafür ein eigenes App-Passwort oder dass der "
            "Zugriff per IMAP erst in den Einstellungen des Postfachs eingeschaltet wird.")


def tls_kontext() -> ssl.SSLContext:
    """Geprüfte Verbindung: Zertifikat und Servername werden kontrolliert.

    imaplib prüft ohne eigenen Kontext gar nichts. truststore nutzt den Vertrauensspeicher des
    Systems (auf dem Mac den Schlüsselbund), das von uv mitgebrachte Python hat keinen eigenen.
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


@contextmanager
def _sitzung(konto: MailKonto, passwort: str):
    """Angemeldete IMAP-Verbindung; Fehler werden zu MailFehler mit verständlicher Meldung."""
    host = (konto.host or "").strip()
    benutzer = (konto.benutzer or "").strip()
    if not host:
        raise MailFehler("Bitte die Adresse des Mail-Servers eintragen (z. B. imap.gmx.net).")
    if not benutzer or not passwort:
        raise MailFehler("Bitte Benutzername und Passwort des Postfachs eintragen.")
    try:
        imap = imaplib.IMAP4_SSL(host, konto.port, ssl_context=tls_kontext(), timeout=ZEITLIMIT)
    except (OSError, imaplib.IMAP4.error) as fehler:
        raise MailFehler(_verbindungsfehler(host, konto.port, fehler)) from fehler
    try:
        try:
            if (benutzer + passwort).isascii():
                imap.login(benutzer, passwort)
            else:  # LOGIN kann in imaplib nur ASCII, AUTHENTICATE PLAIN überträgt UTF-8
                imap.authenticate("PLAIN", lambda _: f"\0{benutzer}\0{passwort}".encode())
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error as fehler:
            raise MailFehler(_anmeldefehler(host)) from fehler
        yield imap
    except (OSError, imaplib.IMAP4.abort) as fehler:
        raise MailFehler(_verbindungsfehler(host, konto.port, fehler)) from fehler
    except imaplib.IMAP4.error as fehler:
        raise MailFehler("Der Mail-Server hat eine Anfrage abgelehnt. Bitte später noch "
                         "einmal versuchen.") from fehler
    finally:
        try:
            imap.logout()
        except Exception:
            pass
