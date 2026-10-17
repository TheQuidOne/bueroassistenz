import sqlite3
import xml.etree.ElementTree as ET
from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader

from arbeitsassistent import db as dbmod
from arbeitsassistent import hinweise, rechnungen, zugferd
from arbeitsassistent.rechnungen import Fehler

TAG = date(2026, 10, 9)
NS = {"rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
      "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
      "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"}


def entwurf(db, einst, kunde, **extra):
    return rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "bezug": "AZ-17", "positionen": [
        {"datum": "2026-10-07", "beschreibung": "Dolmetscheinsatz DGS", "menge": "1,5", "einheit": "Std.",
         "einzelpreis": "85,00"},
        {"datum": "2026-10-07", "beschreibung": "Fahrtkosten", "menge": "48", "einheit": "km", "einzelpreis": "0,42"},
    ], **extra}, einst)


def anhang(ordner, r) -> ET.Element:
    leser = PdfReader(BytesIO((ordner / r["pdf_pfad"]).read_bytes()))
    [xml] = leser.attachments["factur-x.xml"]
    return ET.fromstring(xml)


def text(baum, pfad) -> str:
    return baum.find(pfad, NS).text


def test_ausgestellte_rechnung_ist_zugferd(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    r = rechnungen.rechnung(db, rid)
    roh = (ordner / r["pdf_pfad"]).read_bytes()
    assert b"pdfaid:part" in roh  # PDF/A-Kennung in den XMP-Metadaten
    baum = anhang(ordner, r)
    assert text(baum, ".//rsm:ExchangedDocument/ram:ID") == "2026-001"
    assert text(baum, ".//rsm:ExchangedDocument/ram:TypeCode") == "380"
    assert text(baum, ".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID") == zugferd.PROFIL
    assert text(baum, ".//ram:BuyerReference") == "AZ-17"
    assert text(baum, ".//ram:BuyerTradeParty/ram:PostalTradeAddress/ram:PostcodeCode") == "12345"
    assert text(baum, ".//ram:SellerTradeParty/ram:ID") == "123/456/78901"  # BR-CO-26 ohne USt-IdNr.
    assert text(baum, ".//ram:PayeePartyCreditorFinancialAccount/ram:IBANID") == "DE02120300000000202051"
    assert text(baum, ".//ram:DuePayableAmount") == "147.66"
    assert text(baum, ".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax/ram:CategoryCode") == "E"
    einheiten = [e.get("unitCode") for e in baum.findall(".//ram:BilledQuantity", NS)]
    assert einheiten == ["HUR", "KMT"]


def test_ust19_und_kleinunternehmer(db, einst, kunde, ordner):
    a = entwurf(db, einst, kunde, steuerfall="ust19")
    b = entwurf(db, einst, kunde, steuerfall="kleinunternehmer")
    for rid in (a, b):
        rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    s = anhang(ordner, rechnungen.rechnung(db, a))
    assert text(s, ".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax/ram:CategoryCode") == "S"
    assert text(s, ".//ram:TaxTotalAmount") == "28.06"
    k = anhang(ordner, rechnungen.rechnung(db, b))
    assert "§ 19" in text(k, ".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax/ram:ExemptionReason")


def test_storno_ist_korrigierte_rechnung_mit_bezug(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    storno = rechnungen.rechnung(db, rechnungen.stornieren(db, rid, einst, TAG, ordner))
    baum = anhang(ordner, storno)
    assert text(baum, ".//rsm:ExchangedDocument/ram:TypeCode") == "384"
    assert text(baum, ".//ram:InvoiceReferencedDocument/ram:IssuerAssignedID") == "2026-001"
    mengen = [e.text for e in baum.findall(".//ram:BilledQuantity", NS)]
    preise = [e.text for e in baum.findall(".//ram:NetPriceProductTradePrice/ram:ChargeAmount", NS)]
    assert all(m.startswith("-") for m in mengen) and not any(p.startswith("-") for p in preise)


def test_berichtigung(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    alt_pdf = (ordner / rechnungen.rechnung(db, rid)["pdf_pfad"]).read_bytes()
    rechnungen.zahlung_erfassen(db, rid, 5000, TAG)
    mail_id = db.execute("INSERT INTO mails (message_id, abgerufen) VALUES ('<x@y>', '2026-10-09')").lastrowid
    db.execute("INSERT INTO hinweise (mail_id, art, titel, rechnung_id, erstellt) "
               "VALUES (?, 'rueckfrage', 'Rückfrage zu Rechnung 2026-001', ?, '2026-10-09')", (mail_id, rid))
    daten = {"kunde_id": kunde, "bezug": "AZ-17 / Bewilligung 4711", "positionen": [
        {"datum": "2026-10-07", "beschreibung": "Dolmetscheinsatz DGS", "menge": "2", "einheit": "Std.",
         "einzelpreis": "85,00"}]}
    neu_id = rechnungen.berichtigen(db, rid, daten, einst, TAG, ordner)
    alt, neu = rechnungen.rechnung(db, rid), rechnungen.rechnung(db, neu_id)
    assert (alt["status"], neu["nummer"], neu["ersetzt"]) == ("berichtigt", "2026-002", rid)
    assert (ordner / alt["pdf_pfad"]).read_bytes() == alt_pdf  # Original bleibt unverändert
    assert rechnungen.zustand(alt, 0, TAG) == ("storniert", "Berichtigt")
    assert rechnungen.bezahlt(db, rid) == 0 and rechnungen.bezahlt(db, neu_id) == 5000
    assert rechnungen.offen(db, neu_id) == 17000 - 5000
    assert [o.nummer for o in rechnungen.offene_rechnungen(db)] == ["2026-002"]
    assert hinweise.offene(db, TAG) == []  # Rückfrage zur alten Rechnung ist erledigt
    baum = anhang(ordner, neu)
    assert text(baum, ".//rsm:ExchangedDocument/ram:TypeCode") == "384"
    assert text(baum, ".//ram:InvoiceReferencedDocument/ram:IssuerAssignedID") == "2026-001"
    assert (text(baum, ".//ram:TotalPrepaidAmount"), text(baum, ".//ram:DuePayableAmount")) == ("50.00", "120.00")
    with pytest.raises(Fehler, match="Nur ausgestellte"):
        rechnungen.berichtigen(db, rid, daten, einst, TAG, ordner)


def test_berichtigung_ohne_halben_stand(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    with pytest.raises(Fehler, match="Leistungsdatum"):
        rechnungen.berichtigen(db, rid, {"kunde_id": kunde, "positionen": [
            {"beschreibung": "Einsatz", "menge": "1", "einzelpreis": "85"}]}, einst, TAG, ordner)
    assert db.execute("SELECT COUNT(*) FROM rechnungen").fetchone()[0] == 1
    assert rechnungen.rechnung(db, rid)["status"] == "ausgestellt"


def test_alte_datenbank_bekommt_neue_spalte(tmp_path):
    alt = sqlite3.connect(tmp_path / "daten.db")
    alt.execute("CREATE TABLE rechnungen (id INTEGER PRIMARY KEY, nummer TEXT)")
    alt.commit()
    alt.close()
    db = dbmod.verbinden(tmp_path)
    assert {"ersetzt", "zusatztext"} <= {z[1] for z in db.execute("PRAGMA table_info(rechnungen)")}


@pytest.mark.parametrize("anschrift, erwartet", [
    ("Klinikum\nSozialdienst\nGesundheitsallee 5\n12347 Musterstadt",
     ("Klinikum", ["Sozialdienst", "Gesundheitsallee 5"], "12347", "Musterstadt")),
    ("Erika Mustermann", ("Erika Mustermann", [], "", "")),
    ("Firma\nWeg 1\nD-12345 Ort\nDeutschland", ("Firma", ["Weg 1", "Deutschland"], "12345", "Ort")),
])
def test_anschrift_zerlegen(anschrift, erwartet):
    assert zugferd._anschrift(anschrift) == erwartet


def test_erlaeuterung_in_e_rechnung_und_storno(db, einst, kunde, ordner):
    rid = rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
        {"datum": "2026-10-07", "beschreibung": "Dolmetscheinsatz DGS", "menge": "2", "einheit": "Std.",
         "einzelpreis": "85,00", "erlaeuterung": "Sitzung des Ausschusses für Soziales"}]}, einst)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    baum = anhang(ordner, rechnungen.rechnung(db, rid))
    assert text(baum, ".//ram:SpecifiedTradeProduct/ram:Description") == "Sitzung des Ausschusses für Soziales"
    storno = rechnungen.stornieren(db, rid, einst, TAG, ordner)
    assert rechnungen.positionen(db, storno)[0].erlaeuterung == "Sitzung des Ausschusses für Soziales"
