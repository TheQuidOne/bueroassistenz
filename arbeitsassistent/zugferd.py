"""E-Rechnung nach ZUGFeRD 2 / Factur-X im Profil EN 16931: die Rechnungsdaten als XML im PDF.

Das PDF sieht unverändert aus. Die XML liegt als Anhang „factur-x.xml“ darin, das PDF wird
zum PDF/A-3. Rechnungsprogramme der Kunden lesen die Daten daraus, ohne abzutippen.
"""

import re
from decimal import Decimal

from drafthorse.models.accounting import ApplicableTradeTax
from drafthorse.models.document import Document
from drafthorse.models.note import IncludedNote
from drafthorse.models.party import TaxRegistration
from drafthorse.models.payment import PaymentMeans, PaymentTerms
from drafthorse.models.tradelines import LineItem
from drafthorse.pdf import attach_xml

from .modell import Absender, RechnungsDruck

PROFIL = "urn:cen.eu:en16931:2017"

# Einheiten nach UN/ECE Recommendation 20, wie sie EN 16931 verlangt.
EINHEITEN = {
    "std": "HUR", "std.": "HUR", "stunde": "HUR", "stunden": "HUR", "h": "HUR",
    "min": "MIN", "min.": "MIN", "minute": "MIN", "minuten": "MIN",
    "tag": "DAY", "tage": "DAY", "km": "KMT",
}
STUECK = "C62"  # „Einheit“, auch für pauschal

# Steuerkategorien: S = Normalsatz, E = steuerbefreit (mit Begründung).
KATEGORIEN = {"ust19": "S", "steuerfrei": "E", "kleinunternehmer": "E"}


def _einheit(text: str) -> str:
    return EINHEITEN.get(text.strip().lower(), STUECK)


def _euro(cent: int) -> Decimal:
    return (Decimal(cent) / 100).quantize(Decimal("0.01"))


def _plz_ort(zeile: str) -> tuple[str, str] | None:
    treffer = re.fullmatch(r"\s*(?:D-)?(\d{5})\s+(.+?)\s*", zeile)
    return (treffer.group(1), treffer.group(2)) if treffer else None


def _anschrift(text: str) -> tuple[str, list[str], str, str]:
    """Mehrzeilige Anschrift → (Name, Adresszeilen, PLZ, Ort). PLZ und Ort aus der letzten passenden Zeile."""
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    name = zeilen[0] if zeilen else ""
    rest = zeilen[1:]
    for i in range(len(rest) - 1, -1, -1):
        gefunden = _plz_ort(rest[i])
        if gefunden:
            return name, rest[:i] + rest[i + 1:], *gefunden
    return name, rest, "", ""


def _adresse(ziel, zeilen: list[str], plz: str, ort: str) -> None:
    for feld, zeile in zip(("line_one", "line_two", "line_three"), zeilen):
        setattr(ziel, feld, zeile)
    if plz:
        ziel.postcode = plz
    if ort:
        ziel.city_name = ort
    ziel.country_id = "DE"


def xml(r: RechnungsDruck, a: Absender, steuerfall: str) -> bytes:
    """Die Rechnung als CII-XML (EN 16931). Wird gegen das XML-Schema geprüft."""
    gegenrechnung = r.art == "storno" or bool(r.korrektur_zu)
    vorgaenger = r.storno_zu or r.korrektur_zu
    kategorie = KATEGORIEN.get(steuerfall, "S")
    satz = Decimal(r.ust_satz)

    doc = Document()
    doc.context.guideline_parameter.id = PROFIL
    doc.header.id = r.nummer
    doc.header.type_code = "384" if gegenrechnung else "380"  # 384 = korrigierte Rechnung
    doc.header.issue_date_time = r.datum
    notizen = (r.steuerhinweis, "" if r.art == "storno" else r.zusatztext,
               f"Bezieht sich auf Rechnung {vorgaenger}." if vorgaenger else "")
    for notiz in notizen:
        if notiz.strip():
            doc.header.notes.add(IncludedNote(content=notiz.strip()))

    verkaeufer = doc.trade.agreement.seller
    verkaeufer.name = a.name
    plz_ort = _plz_ort(a.plz_ort) or ("", a.plz_ort.strip())
    _adresse(verkaeufer.address, [a.strasse] if a.strasse.strip() else [], *plz_ort)
    if a.ik.strip():
        verkaeufer.id = a.ik.strip()  # Verkäufer-Kennung (BT-29): das IK, unter dem Kostenträger ihn führen
    elif a.steuernummer.strip() and not a.ust_id.strip():
        # Ohne USt-IdNr. verlangt EN 16931 eine Verkäufer-Kennung (BR-CO-26): dafür die Steuernummer.
        verkaeufer.id = a.steuernummer.strip()
    if a.ust_id.strip():
        verkaeufer.tax_registrations.add(TaxRegistration(id=("VA", a.ust_id.strip())))
    if a.steuernummer.strip():
        verkaeufer.tax_registrations.add(TaxRegistration(id=("FC", a.steuernummer.strip())))

    name, zeilen, plz, ort = _anschrift(r.empfaenger)
    kaeufer = doc.trade.agreement.buyer
    kaeufer.name = name or "Unbekannt"
    _adresse(kaeufer.address, zeilen, plz, ort)
    if r.bezug.strip():
        doc.trade.agreement.buyer_reference = r.bezug.strip()

    if r.leistung_bis and r.leistung_bis != r.leistung_von:
        doc.trade.settlement.period.start = r.leistung_von
        doc.trade.settlement.period.end = r.leistung_bis
    else:
        doc.trade.delivery.event.occurrence = r.leistung_von

    for nr, p in enumerate(r.positionen, 1):
        zeile = LineItem()
        zeile.document.line_id = str(nr)
        zeile.product.name = p.beschreibung
        if p.erlaeuterung.strip():
            zeile.product.description = p.erlaeuterung.strip()
        # Der Preis darf nicht negativ sein. Bei Storno wird stattdessen die Menge negativ.
        menge = -p.menge if p.einzelpreis < 0 else p.menge
        zeile.agreement.net.amount = _euro(abs(p.einzelpreis))
        zeile.delivery.billed_quantity = (menge, _einheit(p.einheit))
        zeile.settlement.trade_tax.type_code = "VAT"
        zeile.settlement.trade_tax.category_code = kategorie
        zeile.settlement.trade_tax.rate_applicable_percent = satz
        if p.datum:  # Einsatztag der Position
            zeile.settlement.period.start = p.datum
            zeile.settlement.period.end = p.datum
        zeile.settlement.monetary_summation.total_amount = _euro(p.gesamt)
        doc.trade.items.add(zeile)

    abrechnung = doc.trade.settlement
    abrechnung.currency_code = "EUR"
    abrechnung.payment_reference = r.nummer
    zu_zahlen = r.brutto - r.bereits_bezahlt
    if zu_zahlen > 0 and r.art != "storno" and a.iban.strip():
        mittel = PaymentMeans()
        mittel.type_code = "58"  # SEPA-Überweisung
        mittel.payee_account.iban = a.iban.replace(" ", "").upper()
        mittel.payee_account.account_name = (a.kontoinhaber or a.name).strip()
        if a.bic.strip():
            mittel.payee_institution.bic = a.bic.strip()
        abrechnung.payment_means.add(mittel)

    steuer = ApplicableTradeTax()
    steuer.calculated_amount = _euro(r.ust)
    steuer.type_code = "VAT"
    if kategorie == "E":
        steuer.exemption_reason = r.steuerhinweis.strip() or "Steuerfreie Leistung"
    steuer.basis_amount = _euro(r.netto)
    steuer.category_code = kategorie
    steuer.rate_applicable_percent = satz
    abrechnung.trade_tax.add(steuer)

    if zu_zahlen > 0 and r.art != "storno":
        bedingungen = PaymentTerms()
        bedingungen.description = f"Zahlbar bis {r.faellig.strftime('%d.%m.%Y')} ohne Abzug."
        bedingungen.due = r.faellig
        abrechnung.terms.add(bedingungen)

    if vorgaenger:
        abrechnung.invoice_referenced_document.issuer_assigned_id = vorgaenger
        if r.vorgaenger_datum:
            abrechnung.invoice_referenced_document.issue_date_time = r.vorgaenger_datum

    summe = abrechnung.monetary_summation
    summe.line_total = _euro(r.netto)
    summe.charge_total = Decimal("0.00")
    summe.allowance_total = Decimal("0.00")
    summe.tax_basis_total = _euro(r.netto)
    summe.tax_total = (_euro(r.ust), "EUR")
    summe.grand_total = _euro(r.brutto)
    summe.prepaid_total = _euro(r.bereits_bezahlt)
    summe.due_amount = _euro(zu_zahlen)

    return doc.serialize(schema="FACTUR-X_EN16931")


def einbetten(pdf: bytes, r: RechnungsDruck, a: Absender, steuerfall: str) -> bytes:
    """Hängt die XML an das PDF an und macht es zum PDF/A-3 (ZUGFeRD 2, Profil EN 16931)."""
    titel = ("Rechnungskorrektur" if r.korrektur_zu else
             "Stornorechnung" if r.art == "storno" else "Rechnung") + f" Nr. {r.nummer}"
    return attach_xml(pdf, xml(r, a, steuerfall), "EN 16931", lang="de-DE", metadata={
        "author": a.name, "title": titel, "subject": f"{titel} von {a.name}",
        "keywords": "ZUGFeRD, Factur-X, E-Rechnung", "creator": "Büroassistenz",
        "producer": "Büroassistenz",
    })
