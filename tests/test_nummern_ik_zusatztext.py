"""Rechnungsnummer im eigenen Format, Institutionskennzeichen (IK) und fester Zusatztext."""
import xml.etree.ElementTree as ET
from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader

from arbeitsassistent import abgleich, hinweise, rechnungen
from arbeitsassistent.rechnungen import Fehler

JVEG = "Grundlage meines Honorars ist das JVEG in der Fassung vom 08.12.2025."
NS = {"ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"}


def entwurf(db, einst, kunde, **extra):
    return rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
        {"datum": "2026-09-02", "beschreibung": "Dolmetscheinsatz DGS", "menge": "2", "einheit": "Std.",
         "einzelpreis": "85,00"}], **extra}, einst)


def ausstellen(db, einst, kunde, tag, ordner, **extra) -> str:
    return rechnungen.ausstellen(db, entwurf(db, einst, kunde, **extra), einst, tag, ordner)


def text_und_xml(ordner, r) -> tuple[str, ET.Element]:
    leser = PdfReader(BytesIO((ordner / r["pdf_pfad"]).read_bytes()))
    text = " ".join(" ".join(s.extract_text().split()) for s in leser.pages)
    return text, ET.fromstring(leser.attachments["factur-x.xml"][0])


# --- Rechnungsnummer ---------------------------------------------------------


def test_jahr_monat_fortlaufend_mit_startnummer(db, einst, kunde, ordner):
    einst = {**einst, "nummer_format": "JJJJ-MM-NNN", "nummer_neu_ab": "nie", "startnummer": "147"}
    assert ausstellen(db, einst, kunde, date(2026, 9, 30), ordner) == "2026-09-147"
    assert ausstellen(db, einst, kunde, date(2026, 9, 30), ordner) == "2026-09-148"
    assert ausstellen(db, einst, kunde, date(2026, 10, 1), ordner) == "2026-10-149"
    assert ausstellen(db, einst, kunde, date(2027, 1, 4), ordner) == "2027-01-150"
    r = rechnungen.rechnung(db, db.execute("SELECT id FROM rechnungen WHERE nummer = '2026-10-149'").fetchone()[0])
    assert r["pdf_pfad"] == "rechnungen/2026/2026-10-149.pdf"
    # Storno zählt in derselben Reihe weiter
    storno = rechnungen.stornieren(db, r["id"], einst, date(2027, 1, 5), ordner)
    assert rechnungen.rechnung(db, storno)["nummer"] == "2027-01-151"


def test_monatlich_und_jaehrlich_neu(db, einst, kunde, ordner):
    monat = {**einst, "nummer_format": "JJJJ-MM-NNN", "nummer_neu_ab": "monat"}
    assert ausstellen(db, monat, kunde, date(2026, 9, 30), ordner) == "2026-09-001"
    assert ausstellen(db, monat, kunde, date(2026, 9, 30), ordner) == "2026-09-002"
    assert ausstellen(db, monat, kunde, date(2026, 10, 1), ordner) == "2026-10-001"
    # Die Startnummer gilt nur für die erste Rechnung im Format, nicht in jedem neuen Jahr
    jahr = {**einst, "nummer_format": "RE-JJJJ-NNNN", "nummer_neu_ab": "jahr", "startnummer": "42"}
    assert ausstellen(db, jahr, kunde, date(2026, 12, 30), ordner) == "RE-2026-0042"
    assert ausstellen(db, jahr, kunde, date(2027, 1, 2), ordner) == "RE-2027-0001"


def test_formatwechsel_nach_bisherigen_rechnungen(db, einst, kunde, ordner):
    assert ausstellen(db, einst, kunde, date(2026, 9, 1), ordner) == "2026-001"  # Standardformat
    neu = {**einst, "nummer_format": "JJJJ-MM-NNN", "nummer_neu_ab": "nie", "startnummer": "147"}
    assert rechnungen.naechste_nummer(db, date(2026, 10, 9), neu) == "2026-10-147"


@pytest.mark.parametrize("muster, neu_ab, meldung", [
    ("JJJJ-MM", "nie", "genau eine laufende Nummer"),
    ("NNN", "jahr", "Jahr \\(JJJJ\\)"),
    ("JJJJ-NNN", "monat", "Jahr und Monat"),
    ("JJJJ/NNN", "jahr", "nur Buchstaben"),
    ("JJJJ-NNN", "woche", "Unbekannte"),
])
def test_unsichere_formate_abgelehnt(muster, neu_ab, meldung):
    with pytest.raises(Fehler, match=meldung):
        rechnungen.nummer_format_pruefen(muster, neu_ab)


@pytest.mark.parametrize("zweck, erwartet", [
    ("RE 2026-09-147 Dolmetschen", True),
    ("Rechnung 2026/09/147", True),
    ("RG202609147", True),
    ("Einsatz am 2026-09-14", False),
    ("Rechnung 2026-09-148", False),
])
def test_neues_format_im_verwendungszweck(zweck, erwartet):
    im_text = abgleich.nummern_im_text(zweck)
    assert abgleich.nummer_genannt("2026-09-147", im_text, zweck) is erwartet


def test_neues_format_in_mail_gefunden(db, einst, kunde, ordner):
    einst = {**einst, "nummer_format": "JJJJ-MM-NNN", "nummer_neu_ab": "nie", "startnummer": "147"}
    ausstellen(db, einst, kunde, date(2026, 9, 30), ordner)
    gefunden = hinweise.rechnungen_finden(db, "Rückfrage zu Ihrer Rechnung 2026-09-147", "")
    assert [r["nummer"] for r in gefunden] == ["2026-09-147"]


# --- IK und Zusatztext -----------------------------------------------------------


def test_ik_und_zusatztext_auf_rechnung(db, einst, kunde, ordner):
    einst = {**einst, "ik": "123456789"}
    nummer = ausstellen(db, einst, kunde, date(2026, 9, 30), ordner, zusatztext=JVEG)
    r = rechnungen.rechnung(db, db.execute("SELECT id FROM rechnungen WHERE nummer = ?", (nummer,)).fetchone()[0])
    text, baum = text_und_xml(ordner, r)
    assert "IK 123456789" in text and JVEG in text
    assert baum.find(".//ram:SellerTradeParty/ram:ID", NS).text == "123456789"
    assert JVEG in [n.text for n in baum.findall(".//ram:IncludedNote/ram:Content", NS)]
    # Auf der Stornorechnung steht der Zusatztext nicht
    storno = rechnungen.rechnung(db, rechnungen.stornieren(db, r["id"], einst, date(2026, 10, 1), ordner))
    assert JVEG not in text_und_xml(ordner, storno)[0]

