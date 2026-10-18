"""Datenklassen, die zwischen Datenbank, Bankimport, Abgleich, PDF, Mail und KI ausgetauscht werden.

Alle Beträge in ganzen Cent (int), Mengen als Decimal, Daten als datetime.date.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


class Fehler(Exception):
    """Fehler mit einer Meldung, die so in der Oberfläche stehen kann."""


# Steuerfälle einer Rechnung. Der Hinweistext steht in den Einstellungen und ist änderbar.
STEUERFAELLE = {
    "steuerfrei": "Steuerfrei (§ 4 Nr. 16 UStG)",
    "kleinunternehmer": "Kleinunternehmer (§ 19 UStG)",
    "ust19": "19 % Umsatzsteuer",
}


@dataclass
class Absender:
    """Die eigenen Angaben auf jeder Rechnung."""

    name: str
    zusatz: str = ""  # z. B. „Gebärdensprachdolmetscher (DGS)“
    strasse: str = ""
    plz_ort: str = ""
    telefon: str = ""
    email: str = ""
    steuernummer: str = ""
    ust_id: str = ""
    ik: str = ""  # Institutionskennzeichen, 9 Ziffern (Abrechnung mit Sozialleistungsträgern)
    bank: str = ""
    iban: str = ""
    bic: str = ""
    kontoinhaber: str = ""  # leer = name
    unterschrift: bytes = b""  # PNG aus unterschrift.py, leer = keine
    schrift: str = "noto"  # Schlüssel aus pdf.SCHRIFTARTEN
    schluss: str = ""  # Text am Ende, wenn die Rechnung keinen eigenen hat
    grussformel: str = "Mit freundlichen Grüßen"  # vor Unterschrift und Name, leer = keine
    girocode: str = "1"  # „0“ = kein Überweisungs-QR-Code auf Rechnungen und Erinnerungen


@dataclass
class Position:
    beschreibung: str
    menge: Decimal
    einheit: str  # „Std.“, „km“, „pauschal“ …
    einzelpreis: int  # Cent
    gesamt: int  # Cent, = geld.mal(menge, einzelpreis)
    datum: date | None = None  # Einsatztag, optional
    erlaeuterung: str = ""  # optional, z. B. „Sitzung des Sozialausschusses“


@dataclass
class RechnungsDruck:
    """Alles, was auf dem PDF steht. Wird aus der Datenbank zusammengestellt."""

    nummer: str  # „2026-001“, Format aus den Einstellungen
    datum: date
    leistung_von: date
    leistung_bis: date | None  # None = ein einzelner Leistungstag
    faellig: date
    empfaenger: str  # Anschrift, mehrzeilig
    positionen: list[Position]
    netto: int
    ust_satz: int  # 0 oder 19
    ust: int
    brutto: int
    steuerhinweis: str  # leer bei ust19, sonst Pflichthinweis zur Befreiung
    art: str = "rechnung"  # „rechnung“ oder „storno“
    storno_zu: str = ""  # Nummer der stornierten Rechnung
    korrektur_zu: str = ""  # Nummer der Rechnung, die diese Rechnungskorrektur ersetzt
    vorgaenger_datum: date | None = None  # Datum der stornierten bzw. ersetzten Rechnung
    bereits_bezahlt: int = 0  # Cent, schon gezahlt (bei einer Korrektur: auf die ersetzte Rechnung)
    bezug: str = ""  # „Ihr Zeichen / Aktenzeichen“, optional
    einleitung: str = ""
    schluss: str = ""
    zusatztext: str = ""  # fester Absatz unter dem Betrag, z. B. Grundlage des Honorars


@dataclass
class Umsatz:
    """Eine Zeile aus dem Kontoauszug (CSV-Export der Bank)."""

    datum: date
    betrag: int  # Cent, positiv = Eingang, negativ = Ausgang
    gegenpartei: str  # Name von Zahler bzw. Empfänger
    iban: str
    zweck: str  # Verwendungszweck (bei manchen Banken samt Buchungstext)
    fingerabdruck: str = ""  # stabil über mehrere Importe, für Duplikate


@dataclass
class OffeneRechnung:
    id: int
    nummer: str
    offen: int  # noch offener Betrag in Cent (> 0)
    kunde: str  # Name des Kunden
    faellig: date


@dataclass
class Vorschlag:
    """Ein Umsatz soll eine oder mehrere Rechnungen bezahlen."""

    umsatz_id: int
    zahlungen: list[tuple[int, int]] = field(default_factory=list)  # (rechnung_id, Cent)
    sicher: bool = False  # True = Nummer und Betrag passen, wird ohne Nachfrage gebucht
    grund: str = ""  # kurze Begründung für die Oberfläche, auf Deutsch


@dataclass
class MailKonto:
    """Zugang zum Postfach (nur lesend). Das Passwort liegt im Schlüsselbund, nicht hier."""

    host: str
    benutzer: str  # meist die E-Mail-Adresse
    port: int = 993  # IMAP über SSL
    ordner: str = "INBOX"


@dataclass
class Mail:
    """Eine gelesene E-Mail, als Klartext."""

    message_id: str  # Message-ID-Kopfzeile, sonst ein stabiler Ersatz
    datum: datetime | None
    absender_name: str
    absender_adresse: str  # klein geschrieben
    betreff: str
    text: str  # Klartext, zitierte Antworten entfernt, höchstens 20.000 Zeichen
    an: str = ""
    anhaenge: list[str] = field(default_factory=list)  # Dateinamen
    uid: int = 0
    ordner: str = "INBOX"


# Arten, in die die KI eine Mail einordnet.
MAILARTEN = {
    "auftrag": "Auftrag oder Einsatz",
    "absage": "Absage",
    "rueckfrage": "Rückfrage zu einer Rechnung",
    "zahlung": "Zahlung angekündigt",
    "frist": "Frist",
    "sonstiges": "Sonstiges",
    "verdaechtig": "Verdächtig",
}


@dataclass
class Auswertung:
    """Was die lokale KI aus einer Mail herausliest. Nur Vorschläge, nie verbindlich."""

    arbeit: bool  # betrifft die Dolmetsch-Arbeit
    art: str  # Schlüssel aus MAILARTEN
    zusammenfassung: str = ""  # ein Satz
    einsatz_datum: date | None = None
    einsatz_beginn: str = ""  # „HH:MM“ oder leer
    dauer_stunden: Decimal | None = None
    auftraggeber: str = ""  # wer bezahlt (Kostenträger), sonst der Absender
    aktenzeichen: str = ""  # Aktenzeichen, Bewilligungs- oder Kassenzeichen
    rechnungsnummer: str = ""  # wie in der Mail geschrieben
    frist: date | None = None  # bis wann etwas zu tun ist
    hinweis: str = ""  # was zu tun ist, kurz
