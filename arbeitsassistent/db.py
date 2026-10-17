"""SQLite-Ablage im Datenordner. Ein Benutzer, ein Rechner, keine Netzwerkzugriffe."""

import os
import sqlite3
from pathlib import Path

# Nicht ~/Documents: macOS synchronisiert „Schreibtisch & Dokumente“ auf Wunsch mit iCloud.
STANDARD_ORDNER = Path.home() / "Arbeits-Assistent"

SCHEMA = """
CREATE TABLE IF NOT EXISTS einstellungen (
    schluessel TEXT PRIMARY KEY,
    wert TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kunden (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    anschrift TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    steuerfall TEXT NOT NULL DEFAULT '',
    zahlungsziel INTEGER,
    notiz TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rechnungen (
    id INTEGER PRIMARY KEY,
    nummer TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'entwurf',
    art TEXT NOT NULL DEFAULT 'rechnung',
    storno_zu INTEGER REFERENCES rechnungen(id),
    ersetzt INTEGER REFERENCES rechnungen(id),
    kunde_id INTEGER NOT NULL REFERENCES kunden(id),
    empfaenger TEXT NOT NULL DEFAULT '',
    datum TEXT,
    leistung_von TEXT,
    leistung_bis TEXT,
    zahlungsziel INTEGER NOT NULL DEFAULT 14,
    faellig TEXT,
    steuerfall TEXT NOT NULL,
    ust_satz INTEGER NOT NULL DEFAULT 0,
    netto INTEGER NOT NULL DEFAULT 0,
    ust INTEGER NOT NULL DEFAULT 0,
    brutto INTEGER NOT NULL DEFAULT 0,
    steuerhinweis TEXT NOT NULL DEFAULT '',
    bezug TEXT NOT NULL DEFAULT '',
    einleitung TEXT NOT NULL DEFAULT '',
    schluss TEXT NOT NULL DEFAULT '',
    zusatztext TEXT NOT NULL DEFAULT '',
    nachgetragen INTEGER NOT NULL DEFAULT 0,
    pdf_pfad TEXT,
    pdf_sha256 TEXT,
    erstellt TEXT NOT NULL,
    ausgestellt TEXT
);

CREATE TABLE IF NOT EXISTS positionen (
    id INTEGER PRIMARY KEY,
    rechnung_id INTEGER NOT NULL REFERENCES rechnungen(id) ON DELETE CASCADE,
    reihe INTEGER NOT NULL,
    datum TEXT,
    beschreibung TEXT NOT NULL,
    menge TEXT NOT NULL,
    einheit TEXT NOT NULL DEFAULT '',
    einzelpreis INTEGER NOT NULL,
    gesamt INTEGER NOT NULL,
    erlaeuterung TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS importe (
    id INTEGER PRIMARY KEY,
    zeitpunkt TEXT NOT NULL,
    dateiname TEXT NOT NULL,
    neu INTEGER NOT NULL,
    doppelt INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS umsaetze (
    id INTEGER PRIMARY KEY,
    fingerabdruck TEXT NOT NULL UNIQUE,
    datum TEXT NOT NULL,
    betrag INTEGER NOT NULL,
    gegenpartei TEXT NOT NULL DEFAULT '',
    iban TEXT NOT NULL DEFAULT '',
    zweck TEXT NOT NULL DEFAULT '',
    kategorie TEXT NOT NULL DEFAULT '',
    import_id INTEGER REFERENCES importe(id)
);

CREATE TABLE IF NOT EXISTS zahlungen (
    id INTEGER PRIMARY KEY,
    rechnung_id INTEGER NOT NULL REFERENCES rechnungen(id),
    umsatz_id INTEGER REFERENCES umsaetze(id),
    datum TEXT NOT NULL,
    betrag INTEGER NOT NULL,
    notiz TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS vorschlaege (
    id INTEGER PRIMARY KEY,
    umsatz_id INTEGER NOT NULL UNIQUE REFERENCES umsaetze(id),
    zahlungen TEXT NOT NULL,
    grund TEXT NOT NULL,
    abgelehnt INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS regeln (
    id INTEGER PRIMARY KEY,
    muster TEXT NOT NULL UNIQUE,
    kategorie TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vorlagen (
    id INTEGER PRIMARY KEY,
    beschreibung TEXT NOT NULL,
    einheit TEXT NOT NULL DEFAULT '',
    einzelpreis INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mails (
    id INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    uid INTEGER NOT NULL DEFAULT 0,
    ordner TEXT NOT NULL DEFAULT 'INBOX',
    datum TEXT,
    absender_name TEXT NOT NULL DEFAULT '',
    absender_adresse TEXT NOT NULL DEFAULT '',
    betreff TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    anhaenge TEXT NOT NULL DEFAULT '[]',
    abgerufen TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'neu',
    auswertung TEXT,
    ausgewertet TEXT,
    arbeit INTEGER,
    art TEXT
);

CREATE TABLE IF NOT EXISTS hinweise (
    id INTEGER PRIMARY KEY,
    mail_id INTEGER REFERENCES mails(id) ON DELETE CASCADE,
    art TEXT NOT NULL,
    titel TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    kunde_id INTEGER REFERENCES kunden(id),
    rechnung_id INTEGER REFERENCES rechnungen(id),
    datum TEXT,
    ab TEXT,
    daten TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'offen',
    erstellt TEXT NOT NULL
);
"""

# Startwerte der Einstellungen. Die Hinweistexte stehen so auf den Rechnungen.
STANDARDS = {
    "name": "",
    "zusatz": "Gebärdensprachdolmetscher (DGS)",
    "strasse": "",
    "plz_ort": "",
    "telefon": "",
    "email": "",
    "steuernummer": "",
    "ust_id": "",
    "ik": "",
    "bank": "",
    "iban": "",
    "bic": "",
    "kontoinhaber": "",
    "steuerfall": "steuerfrei",
    "zahlungsziel": "14",
    # Rechnungsnummer: JJJJ = Jahr, MM = Monat, NNN = laufende Nummer (siehe rechnungen.py)
    "nummer_format": "JJJJ-NNN",
    "nummer_neu_ab": "jahr",  # laufende Nummer beginnt neu: jahr, monat oder nie
    "startnummer": "1",
    "zusatztext": "",  # Vorgabe für neue Rechnungen, z. B. Grundlage des Honorars
    "schrift": "noto",  # Schrift der Rechnungs-PDFs: noto oder arial (pdf.SCHRIFTARTEN)
    "hinweis_steuerfrei": "Umsatzsteuerfreie Leistung gemäß § 4 Nr. 16 UStG.",
    "hinweis_kleinunternehmer": "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet.",
    "einleitung": "",
    "schluss": "Vielen Dank für die Zusammenarbeit.",  # Text am Ende jeder Rechnung
    "grussformel": "Mit freundlichen Grüßen",  # vor der Unterschrift, leer = keine
    # Postfach (nur lesend). Das Passwort liegt im Schlüsselbund des Systems.
    "mail_anbieter": "",
    "mail_host": "",
    "mail_port": "993",
    "mail_benutzer": "",
    "mail_ordner": "INBOX",
    "mail_aktiv": "0",
    "mail_seit_tage": "30",
    "mail_uidvalidity": "",
    "mail_letzte_uid": "0",
    "mail_letzter_abruf": "",
    "mail_letzter_fehler": "",
    "mail_stand": "",
    # Lokale KI (Ollama, nur auf diesem Rechner)
    "ki_url": "http://127.0.0.1:11434",
    "ki_modell": "gemma4:12b",
}


# Spalten, die nach der ersten Fassung dazukamen. Ältere Datenbanken bekommen sie beim Start.
NACHTRAEGE = [
    ("rechnungen", "ersetzt", "INTEGER REFERENCES rechnungen(id)"),  # Rechnungskorrektur ersetzt …
    ("rechnungen", "zusatztext", "TEXT NOT NULL DEFAULT ''"),
    ("positionen", "erlaeuterung", "TEXT NOT NULL DEFAULT ''"),
    ("rechnungen", "nachgetragen", "INTEGER NOT NULL DEFAULT 0"),  # vor dem Programm erstellt
]


def _nachziehen(db: sqlite3.Connection) -> None:
    for tabelle, spalte, typ in NACHTRAEGE:
        vorhanden = {z[1] for z in db.execute(f"PRAGMA table_info({tabelle})")}
        if spalte not in vorhanden:
            db.execute(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")


def datenordner() -> Path:
    ordner = Path(os.environ.get("ARBEITSASSISTENT_DATEN") or STANDARD_ORDNER)
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def verbinden(ordner: Path | None = None) -> sqlite3.Connection:
    ordner = ordner or datenordner()
    # Längere Wartezeit, weil der Postfach-Abruf im Hintergrund gleichzeitig schreiben kann.
    db = sqlite3.connect(ordner / "daten.db", isolation_level=None, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(SCHEMA)
    _nachziehen(db)
    return db


class Transaktion:
    """`with Transaktion(db):` – alles oder nichts."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, typ, wert, tb):
        self.db.execute("ROLLBACK" if typ else "COMMIT")
        return False


def einstellungen(db: sqlite3.Connection) -> dict[str, str]:
    werte = dict(STANDARDS)
    werte.update({z["schluessel"]: z["wert"] for z in db.execute("SELECT * FROM einstellungen")})
    return werte


def einstellungen_setzen(db: sqlite3.Connection, werte: dict[str, str]) -> None:
    with Transaktion(db):
        for schluessel, wert in werte.items():
            if schluessel in STANDARDS:
                db.execute(
                    "INSERT INTO einstellungen VALUES (?, ?) "
                    "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert",
                    (schluessel, wert.strip()),
                )
