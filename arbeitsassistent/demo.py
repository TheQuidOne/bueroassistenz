"""Erfundene Beispieldaten zum Ausprobieren. Alle Namen, Nummern und Beträge sind ausgedacht."""

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from . import db as dbmod
from . import hinweise, rechnungen, umsaetze
from .modell import Auswertung

EINSTELLUNGEN = {
    "name": "Max Beispiel",
    "zusatz": "Gebärdensprachdolmetscher (DGS)",
    "strasse": "Musterweg 12",
    "plz_ort": "12345 Musterstadt",
    "telefon": "0123 456789",
    "email": "max@beispiel.example",
    "steuernummer": "123/456/78901",
    "bank": "Musterbank",
    "iban": "DE02120300000000202051",
    "bic": "BYLADEM1001",
    "steuerfall": "steuerfrei",
}

VORLAGEN = [
    ("Dolmetscheinsatz DGS", "Std.", "85,00"),
    ("Fahrzeit", "Std.", "42,50"),
    ("Fahrtkosten", "km", "0,42"),
    ("Ausfallhonorar (kurzfristige Absage)", "pauschal", "170,00"),
]

KUNDEN = [
    ("Integrationsamt Musterland", "Fachbereich Begleitende Hilfe\nAmtsweg 1\n12345 Musterstadt", "steuerfrei", 30,
     "begleitende-hilfe@integrationsamt-musterland.example"),
    ("Klinikum Musterstadt gGmbH", "Sozialdienst\nGesundheitsallee 5\n12347 Musterstadt", "steuerfrei", None,
     "sozialdienst@klinikum-musterstadt.example"),
    ("Beispiel Software GmbH", "Personalabteilung\nCodeweg 9\n12349 Musterstadt", "ust19", None,
     "personal@beispiel-software.example"),
    ("Amtsgericht Musterstadt", "Justizplatz 2\n12345 Musterstadt", "ust19", 30, "verwaltung@ag-musterstadt.example"),
]

KOPF = ('"Auftragskonto";"Buchungstag";"Valutadatum";"Buchungstext";"Verwendungszweck";"Glaeubiger ID";'
        '"Mandatsreferenz";"Kundenreferenz (End-to-End)";"Sammlerreferenz";"Lastschrift Ursprungsbetrag";'
        '"Auslagenersatz Ruecklastschrift";"Beguenstigter/Zahlungspflichtiger";"Kontonummer/IBAN";'
        '"BIC (SWIFT-Code)";"Betrag";"Waehrung";"Info"')


def _zeile(tag: date, text: str, zweck: str, wer: str, iban: str, betrag: str) -> str:
    felder = ["DE02120300000000202051", tag.strftime("%d.%m.%y"), tag.strftime("%d.%m.%y"), text, zweck,
              "", "", "", "", "", "", wer, iban, "", betrag, "EUR", "Umsatz gebucht"]
    return ";".join(f'"{f}"' for f in felder)


def fuellen(ordner: Path, heute: date | None = None) -> bool:
    """Füllt einen leeren Datenordner mit Beispielen. Gibt False zurück, wenn schon Daten da sind."""
    heute = heute or date.today()
    db = dbmod.verbinden(ordner)
    if db.execute("SELECT COUNT(*) FROM kunden").fetchone()[0]:
        return False
    dbmod.einstellungen_setzen(db, EINSTELLUNGEN)
    einst = dbmod.einstellungen(db)
    for beschreibung, einheit, preis in VORLAGEN:
        rechnungen.vorlage_speichern(db, beschreibung, einheit, preis)
    kunden = [
        rechnungen.kunde_speichern(db, {"name": n, "anschrift": a, "steuerfall": s, "email": e,
                                        "zahlungsziel": str(z) if z else ""})
        for n, a, s, z, e in KUNDEN
    ]

    def rechnung(kunde: int, tage_her: int, stunden: str, km: str = "", bezug: str = "") -> int:
        einsatz = heute - timedelta(days=tage_her + 2)
        positionen = [{"datum": einsatz.isoformat(), "beschreibung": "Dolmetscheinsatz DGS",
                       "menge": stunden, "einheit": "Std.", "einzelpreis": "85,00"}]
        if km:
            positionen += [
                {"datum": einsatz.isoformat(), "beschreibung": "Fahrzeit", "menge": "1", "einheit": "Std.",
                 "einzelpreis": "42,50"},
                {"datum": einsatz.isoformat(), "beschreibung": "Fahrtkosten", "menge": km, "einheit": "km",
                 "einzelpreis": "0,42"},
            ]
        rid = rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": positionen, "bezug": bezug}, einst)
        rechnungen.ausstellen(db, rid, einst, heute - timedelta(days=tage_her), ordner)
        return rid

    r1 = rechnung(kunden[0], 50, "3", "48", "Az. 52-1234/26")
    r2 = rechnung(kunden[0], 40, "2", "", "Az. 52-1234/26")
    r3 = rechnung(kunden[0], 38, "1,5", "30", "Az. 52-1290/26")
    r4 = rechnung(kunden[1], 35, "2,5")
    r5 = rechnung(kunden[2], 20, "4", "60")
    r6 = rechnung(kunden[3], 5, "1,5")
    rechnung(kunden[1], 2, "1")
    entwurf = rechnungen.entwurf_speichern(db, {"kunde_id": kunden[2], "positionen": [
        {"datum": heute.isoformat(), "beschreibung": "Dolmetscheinsatz DGS (Teamsitzung)", "menge": "2",
         "einheit": "Std.", "einzelpreis": "85,00"}]}, einst)

    def nummer(rid: int) -> str:
        return rechnungen.rechnung(db, rid)["nummer"]

    def betrag(rid: int) -> str:
        cent = rechnungen.rechnung(db, rid)["brutto"]
        return f"{cent // 100},{cent % 100:02d}"

    sammel = rechnungen.rechnung(db, r2)["brutto"] + rechnungen.rechnung(db, r3)["brutto"]
    zeilen = [
        _zeile(heute - timedelta(days=36), "GUTSCHR. UEBERW.", f"Rechnung {nummer(r1)} Kassenzeichen 7781",
               "Landeskasse Musterland", "DE89370400440532013000", betrag(r1)),
        _zeile(heute - timedelta(days=30), "FOLGELASTSCHRIFT", "Mobilfunk Rechnung 09/2026",
               "Telefonica Beispiel GmbH", "DE44500105175407324931", "-29,99"),
        _zeile(heute - timedelta(days=24), "KARTENZAHLUNG", "Bahnticket Musterstadt-Beispielstadt",
               "Deutsche Bahn Beispiel", "", "-46,90"),
        _zeile(heute - timedelta(days=18), "GUTSCHR. UEBERW.",
               f"RE {nummer(r2)} UND {nummer(r3)} SAMMELZAHLUNG", "Landeskasse Musterland",
               "DE89370400440532013000", f"{sammel // 100},{sammel % 100:02d}"),
        _zeile(heute - timedelta(days=15), "LASTSCHRIFT", "Mitgliedsbeitrag 2026 Berufsverband",
               "Berufsverband Beispiel e.V.", "DE12500105170648489890", "-120,00"),
        _zeile(heute - timedelta(days=12), "KARTENZAHLUNG", "Einkauf", "Supermarkt Beispiel", "", "-63,17"),
        _zeile(heute - timedelta(days=6), "GUTSCHR. UEBERW.", "Dolmetscherleistung Sozialdienst Danke",
               "Klinikum Musterstadt gGmbH", "DE75512108001245126199", betrag(r4)),
        _zeile(heute - timedelta(days=3), "FOLGELASTSCHRIFT", "Mobilfunk Rechnung 10/2026",
               "Telefonica Beispiel GmbH", "DE44500105175407324931", "-29,99"),
    ]
    csv = ("\r\n".join([KOPF, *reversed(zeilen)]) + "\r\n").encode("cp1252")
    umsaetze.importieren(db, "Umsaetze_Musterbank.csv", csv, ordner)
    db.execute("UPDATE importe SET zeitpunkt = ?", ((heute - timedelta(days=2)).isoformat() + "T09:00:00",))
    _post(db, heute, nummer(r4), nummer(r5))
    del r6, entwurf  # bleiben offen bzw. Entwurf
    return True


def _post(db, heute: date, r4: str, r5: str) -> None:
    """Erfundene Mails mit fertiger Auswertung, damit „Post“ und die Hinweise auch ohne KI etwas zeigen."""
    def tag(tage: int) -> date:
        return heute + timedelta(days=tage)

    def d(t: date) -> str:
        return t.strftime("%d.%m.%Y")

    mails = [
        (-4, "Sozialdienst Klinikum Musterstadt", "sozialdienst@klinikum-musterstadt.example",
         f"Bestätigung Dolmetscheinsatz am {d(tag(-2))}",
         f"Guten Tag Herr Beispiel,\n\nhiermit bestätigen wir den Einsatz am {d(tag(-2))} von 10:00 bis 11:30 Uhr "
         "auf Station 4 (Aufnahmegespräch).\n\nMit freundlichen Grüßen\nSozialdienst",
         Auswertung(arbeit=True, art="auftrag", zusammenfassung="Einsatz auf Station 4 bestätigt (Aufnahmegespräch).",
                    einsatz_datum=tag(-2), einsatz_beginn="10:00", dauer_stunden=Decimal("1.5"),
                    auftraggeber="Klinikum Musterstadt gGmbH")),
        (-1, "Integrationsamt Musterland", "begleitende-hilfe@integrationsamt-musterland.example",
         "Kostenübernahme – Teamgespräch",
         f"Sehr geehrter Herr Beispiel,\n\nfür das Teamgespräch am {d(tag(5))} um 9:00 Uhr (ca. 2 Stunden) "
         "übernehmen wir die Kosten. Bitte geben Sie auf der Rechnung das Aktenzeichen 52-1301/26 an."
         "\n\nMit freundlichen Grüßen",
         Auswertung(arbeit=True, art="auftrag",
                    zusammenfassung="Kostenübernahme für das Teamgespräch, Aktenzeichen auf der Rechnung angeben.",
                    einsatz_datum=tag(5), einsatz_beginn="09:00", dauer_stunden=Decimal("2"),
                    auftraggeber="Integrationsamt Musterland", aktenzeichen="52-1301/26")),
        (-2, "Personalabteilung Beispiel Software", "personal@beispiel-software.example", f"Ihre Rechnung {r5}",
         f"Hallo Herr Beispiel,\n\nunsere Buchhaltung kann die Rechnung {r5} nur mit unserer Bestellnummer "
         "BS-4471 bearbeiten. Könnten Sie die Rechnung bitte mit Bestellnummer erneut senden?\n\nViele Grüße",
         Auswertung(arbeit=True, art="rueckfrage",
                    zusammenfassung="Rechnung soll mit Bestellnummer BS-4471 erneut gesendet werden.",
                    rechnungsnummer=r5, auftraggeber="Beispiel Software GmbH",
                    hinweis="Rechnung mit Bestellnummer BS-4471 neu senden.")),
        (-1, "Amtsgericht Musterstadt", "verwaltung@ag-musterstadt.example", f"Aufhebung Termin {d(tag(3))}",
         f"Sehr geehrter Herr Beispiel,\n\nder Termin am {d(tag(3))} um 13:00 Uhr wurde aufgehoben. Ihre Ladung "
         "als Dolmetscher entfällt.\n\nMit freundlichen Grüßen\nGeschäftsstelle",
         Auswertung(arbeit=True, art="absage", zusammenfassung="Gerichtstermin aufgehoben, Ladung entfällt.",
                    einsatz_datum=tag(3), auftraggeber="Amtsgericht Musterstadt")),
        (0, "Klinikum Musterstadt Buchhaltung", "buchhaltung@klinikum-musterstadt.example", "Zahlungsavis",
         f"Sehr geehrte Damen und Herren,\n\ndie Rechnung {r4} wird mit dem nächsten Zahllauf überwiesen."
         "\n\nFreundliche Grüße\nKreditorenbuchhaltung",
         Auswertung(arbeit=True, art="zahlung", zusammenfassung="Rechnung wird mit dem nächsten Zahllauf überwiesen.",
                    rechnungsnummer=r4, auftraggeber="Klinikum Musterstadt gGmbH")),
        (-3, "Berufsverband Beispiel e.V.", "newsletter@berufsverband.example",
         "Newsletter Oktober: Fortbildungen und Termine",
         "Liebe Mitglieder,\n\nin diesem Monat gibt es neue Fortbildungen zu Fachgebärden im Gesundheitswesen.",
         Auswertung(arbeit=False, art="sonstiges", zusammenfassung="Newsletter des Berufsverbands mit Fortbildungen.")),
        (-2, "Zahlungsservice", "service@rechnung-pruefen.example", "Offene Rechnung – letzte Mahnung",
         "Ihre Rechnung ist überfällig. Klicken Sie hier, um sofort zu bezahlen, sonst wird Ihr Konto gesperrt.",
         Auswertung(arbeit=False, art="verdaechtig",
                    zusammenfassung="Vermutlich Betrug: Zeitdruck und ein Link zum Bezahlen.")),
    ]
    jetzt = datetime.now().isoformat(timespec="seconds")
    for nr, (tage, name, adresse, betreff, text, a) in enumerate(mails, 1):
        zeit = datetime.combine(tag(tage), time(8 + nr, 15)).isoformat(timespec="seconds")
        mail_id = db.execute(
            "INSERT INTO mails (message_id, datum, absender_name, absender_adresse, betreff, text, abgerufen, "
            "status, auswertung, ausgewertet, arbeit, art) VALUES (?, ?, ?, ?, ?, ?, ?, 'ausgewertet', ?, ?, ?, ?)",
            (f"<demo-{nr}@beispiel.example>", zeit, name, adresse, betreff, text, jetzt,
             hinweise.auswertung_als_json(a), jetzt, int(a.arbeit), a.art),
        ).lastrowid
        hinweise.aus_mail(db, mail_id, a, heute)
