"""Tests für das PDF-Modul. Nur erfundene Daten.

Textprüfungen brauchen pdftotext (poppler), die Girocode-Prüfung zusätzlich pdftoppm
und zbarimg. Fehlen sie, werden diese Tests übersprungen.
"""

import re
import shutil
import subprocess
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from arbeitsassistent import pdf as pdfmodul
from arbeitsassistent.geld import anteil, mal
from arbeitsassistent.modell import Absender, Position, RechnungsDruck
from arbeitsassistent.pdf import erinnerung_pdf, girocode, rechnung_pdf

MM = 72 / 25.4  # Punkt je Millimeter

VOLL = Absender(
    name="Erika Mustermann",
    zusatz="Gebärdensprachdolmetscherin (DGS)",
    strasse="Musterstraße 12",
    plz_ort="12345 Musterstadt",
    telefon="0123 456789",
    email="kontakt@erika-mustermann.example",
    steuernummer="123/456/78901",
    ust_id="DE123456789",
    bank="Musterbank Musterstadt",
    iban="DE02 1203 0000 0000 2020 51",
    bic="BYLADEM1001",
)
LEER = Absender(name="Erika Mustermann")
EMPFAENGER = (
    "Landschaftsverband Musterland\nIntegrationsamt\nAm Beispielplatz 3\n54321 Beispielstadt"
)
HEUTE, FRIST = date(2026, 10, 25), date(2026, 11, 8)


def pos(beschreibung, menge, einheit, preis, datum=None):
    m = Decimal(menge)
    return Position(beschreibung, m, einheit, preis, mal(m, preis), datum)


def rechnung(positionen, ust_satz=0, **kw) -> RechnungsDruck:
    netto = sum(p.gesamt for p in positionen)
    ust = anteil(netto, ust_satz)
    werte = dict(
        nummer="2026-001",
        datum=date(2026, 10, 4),
        leistung_von=date(2026, 9, 22),
        leistung_bis=None,
        faellig=date(2026, 10, 18),
        empfaenger=EMPFAENGER,
        positionen=positionen,
        netto=netto,
        ust_satz=ust_satz,
        ust=ust,
        brutto=netto + ust,
        steuerhinweis="" if ust_satz else "Umsatzsteuerfrei gemäß § 4 Nr. 16 UStG.",
    )
    werte.update(kw)
    return RechnungsDruck(**werte)


def drei_positionen():
    tag = date(2026, 9, 22)
    return [
        pos("Dolmetscheinsatz DGS – Personalgespräch", "2.5", "Std.", 8500, tag),
        pos("Fahrtzeit", "1", "Std.", 4250, tag),
        pos("Fahrtkosten (Hin- und Rückfahrt)", "48", "km", 42, tag),
    ]


def seiten(daten: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page\b(?!s)", daten))


def gueltig(daten: bytes):
    assert daten.startswith(b"%PDF-")
    assert daten.rstrip().endswith(b"%%EOF")


def text(daten: bytes, tmp_path: Path, *optionen: str) -> str:
    """Text des PDFs in Leseanordnung; ohne Optionen mit -layout."""
    optionen = optionen or ("-layout",)
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext nicht installiert")
    datei = tmp_path / "probe.pdf"
    datei.write_bytes(daten)
    erg = subprocess.run(
        ["pdftotext", *optionen, str(datei), "-"], capture_output=True, text=True, check=True
    )
    return erg.stdout


def einzeilig(s: str) -> str:
    return " ".join(s.split())


def test_schrift_und_lizenz_liegen_bei():
    ordner = Path(pdfmodul.__file__).parent / "schriften"
    assert (ordner / "NotoSans-Regular.ttf").is_file()
    assert (ordner / "NotoSans-Bold.ttf").is_file()
    lizenz = (ordner / "OFL.txt").read_text()
    assert "SIL OPEN FONT LICENSE Version 1.1" in lizenz
    assert "Noto Project Authors" in lizenz


def test_rechnung_mit_umsatzsteuer(tmp_path):
    r = rechnung(drei_positionen(), 19, bezug="IFD-2026-0815-MA")
    assert (r.netto, r.ust, r.brutto) == (27516, 5228, 32744)
    daten = rechnung_pdf(r, VOLL)
    gueltig(daten)
    assert seiten(daten) == 1

    t = einzeilig(text(daten, tmp_path))
    for erwartet in [
        "Rechnung Nr. 2026-001",
        "Gebärdensprachdolmetscherin (DGS)",
        "Erika Mustermann · Musterstraße 12 · 12345 Musterstadt",
        "Leistungsdatum 22.09.2026",
        "Zahlbar bis 18.10.2026",
        "Ihr Zeichen IFD-2026-0815-MA",
        "Steuernummer 123/456/78901",
        "Für meine Leistungen erlaube ich mir, wie folgt abzurechnen:",
        "Personalgespräch",
        "212,50 €",
        "Nettobetrag 275,16 €",
        "zzgl. 19 % Umsatzsteuer 52,28 €",
        "Gesamtbetrag 327,44 €",
        "Bitte überweisen Sie den Betrag von 327,44 € bis zum 18.10.2026 unter Angabe der "
        "Rechnungsnummer 2026-001 auf das unten genannte Konto.",
        "Mit der Banking-App scannen",
        "Vielen Dank für die Zusammenarbeit.",
        "IBAN DE02 1203 0000 0000 2020 51",
        "USt-IdNr. DE123456789",
        "Seite 1 von 1",
    ]:
        assert erwartet in t, erwartet
    assert "Umsatzsteuerfrei" not in t


def test_steuerfrei_lang_und_mehrseitig(tmp_path):
    lang = (
        "Dolmetschen in Deutsche Gebärdensprache (DGS) und Lautsprache im Rahmen der "
        "Arbeitsassistenz, einschließlich Vor- und Nachbereitung, Fortbildung "
        "„Arbeitssicherheit“ sowie Abstimmung mit der Teamkollegin. "
    ) * 12
    positionen = [pos(lang, "6", "Std.", 7500, date(2026, 9, 1))]
    for i in range(39):
        tag = date(2026, 9, 1) + timedelta(days=i % 30)
        positionen.append(pos(f"Dolmetscheinsatz DGS, Einsatz {i + 2}", "1.5", "Std.", 7500, tag))
    r = rechnung(
        positionen,
        nummer="2026-002",
        leistung_von=date(2026, 9, 1),
        leistung_bis=date(2026, 9, 30),
    )
    daten = rechnung_pdf(r, VOLL)
    gueltig(daten)
    n = seiten(daten)
    assert 2 <= n <= 5

    t = text(daten, tmp_path)
    seitentexte = t.split("\f")[:n]
    for s in seitentexte:
        if "Dolmetsch" in s:
            assert "Einzelpreis" in s, "Tabellenkopf fehlt auf einer Seite"
    for i, s in enumerate(seitentexte, start=1):
        assert f"Seite {i} von {n}" in s
    assert "Rechnung Nr. 2026-002 vom 04.10.2026" in seitentexte[1]  # Kopf der Folgeseite

    e = einzeilig(t)
    assert "Leistungszeitraum 01.09.–30.09.2026" in e
    assert "„Arbeitssicherheit“" in e
    assert "Einsatz 40" in e
    assert "Gesamtbetrag 4.837,50 €" in e
    assert "Umsatzsteuerfrei gemäß § 4 Nr. 16 UStG." in e
    assert "Nettobetrag" not in e and "Umsatzsteuer 0" not in e
    assert e.index("Einsatz 40") < e.index("Gesamtbetrag") < e.index("Umsatzsteuerfrei")


def test_storno(tmp_path):
    positionen = [
        pos(p.beschreibung, -p.menge, p.einheit, p.einzelpreis, p.datum) for p in drei_positionen()
    ]
    r = rechnung(positionen, 19, nummer="2026-003", art="storno", storno_zu="2026-001")
    assert r.brutto == -32744
    daten = rechnung_pdf(r, VOLL)
    gueltig(daten)
    assert seiten(daten) == 1

    t = einzeilig(text(daten, tmp_path))
    assert "Stornorechnung Nr. 2026-003 zu Rechnung Nr. 2026-001" in t
    assert "-212,50 €" in t
    assert "Gesamtbetrag -327,44 €" in t
    assert "-2,5" in t
    assert "Bitte überweisen" not in t
    assert "Banking-App" not in t
    assert "Zahlbar bis" not in t


def test_zahlungserinnerung(tmp_path):
    r = rechnung(drei_positionen(), 19, bezug="IFD-2026-0815-MA")
    daten = erinnerung_pdf(r, VOLL, offen=22744, heute=HEUTE, neue_frist=FRIST)
    gueltig(daten)
    assert seiten(daten) == 1

    t = einzeilig(text(daten, tmp_path))
    for erwartet in [
        "Zahlungserinnerung zu Rechnung Nr. 2026-001",
        "Datum 25.10.2026",
        "Rechnung Nr. 2026-001 vom 04.10.2026, fällig am 18.10.2026, noch nicht vollständig",
        "Rechnungsbetrag 327,44 €",
        "bereits gezahlt -100,00 €",
        "Offener Betrag 227,44 €",
        "Bitte überweisen Sie den offenen Betrag von 227,44 € bis zum 08.11.2026",
        "Rechnungsnummer 2026-001 auf das unten genannte Konto.",
        "als gegenstandslos",
        "Mit der Banking-App scannen",
        "Mit freundlichen Grüßen",
    ]:
        assert erwartet in t, erwartet


def test_erinnerung_ohne_teilzahlung(tmp_path):
    r = rechnung(drei_positionen(), 19)
    daten = erinnerung_pdf(r, LEER, offen=r.brutto, heute=HEUTE, neue_frist=FRIST)
    gueltig(daten)
    t = einzeilig(text(daten, tmp_path))
    assert "fällig am 18.10.2026, noch nicht beglichen" in t
    assert "bereits gezahlt" not in t
    assert "Banking-App" not in t


def test_absender_ohne_optionale_angaben(tmp_path):
    r = rechnung(
        [pos("Dolmetscheinsatz", "2", "Std.", 8000)],
        empfaenger="AOK Musterland",
        steuerhinweis="Kleinunternehmer (§ 19 UStG)",
    )
    daten = rechnung_pdf(r, LEER)
    gueltig(daten)
    assert seiten(daten) == 1
    gueltig(erinnerung_pdf(r, LEER, 16000, HEUTE, FRIST))

    t = einzeilig(text(daten, tmp_path))
    assert "Gesamtbetrag 160,00 €" in t
    assert "Kleinunternehmer (§ 19 UStG)" in t
    assert "unter Angabe der Rechnungsnummer 2026-001." in t
    for fehlt in ["IBAN", "BIC", "Steuernummer", "USt-IdNr.", "Tel.", "Banking-App", "Ihr Zeichen"]:
        assert fehlt not in t, fehlt
    assert "Konto" not in t


def test_ohne_datum_keine_datumsspalte(tmp_path):
    r = rechnung([pos("Pauschale", "1", "pauschal", 5000)])
    t = text(rechnung_pdf(r, VOLL), tmp_path, "-layout")
    kopf = next(z for z in t.splitlines() if "Pos." in z)
    assert "Leistung" in kopf and "Datum" not in kopf


def test_anschrift_sitzt_im_fenster(tmp_path):
    xml = text(rechnung_pdf(rechnung(drei_positionen(), 19), VOLL), tmp_path, "-bbox")
    woerter = {}
    zahl = r'"([\d.]+)"'
    muster = rf"<word xMin={zahl} yMin={zahl} xMax={zahl} yMax={zahl}>([^<]*)</word>"
    for m in re.finditer(muster, xml):
        woerter.setdefault(m.group(5), tuple(float(v) / MM for v in m.groups()[:4]))
    x0, y0, _, _ = woerter["Landschaftsverband"]
    _, _, x1, y1 = woerter["Beispielstadt"]
    assert 24 <= x0 <= 26
    assert 62.7 <= y0 and y1 <= 90 and x1 <= 25 + 85
    # Rücksendeangabe in der Zusatz- und Vermerkzone
    _, ry0, _, ry1 = woerter["Musterstraße"]
    assert 45 <= ry0 and ry1 <= 62.7
    # Informationsblock rechts daneben
    ix0, iy0, _, _ = woerter["Rechnungsnummer"]
    assert ix0 >= 120 and 45 <= iy0 <= 90


def test_girocode_wird_nur_bei_gueltigen_daten_erzeugt():
    assert girocode(VOLL, 32744, "2026-001") is not None
    assert girocode(VOLL, 0, "2026-001") is None
    assert girocode(VOLL, -500, "2026-001") is None
    assert girocode(LEER, 32744, "2026-001") is None
    falsch = Absender(name="Erika Mustermann", iban="DE02120300000000202052")
    assert girocode(falsch, 32744, "2026-001") is None
    ohne_bic = Absender(name="Erika Mustermann", iban="DE02120300000000202051", bic="XYZ")
    assert girocode(ohne_bic, 32744, "2026-001") is not None


def _girocode_lesen(daten: bytes, tmp_path: Path) -> bytes:
    if not (shutil.which("pdftoppm") and shutil.which("zbarimg")):
        pytest.skip("pdftoppm oder zbarimg nicht installiert")
    datei = tmp_path / "qr.pdf"
    datei.write_bytes(daten)
    subprocess.run(
        ["pdftoppm", "-png", "-r", "200", "-f", "1", "-l", "1", str(datei), str(tmp_path / "qr")],
        check=True,
    )
    png = next(tmp_path.glob("qr*.png"))
    erg = subprocess.run(
        ["zbarimg", "--raw", "-q", "-Sbinary", str(png)], capture_output=True, check=True
    )
    return erg.stdout.rstrip(b"\n")


@pytest.mark.parametrize(
    "inhaber", ["Jürgen Müßig-Özbek", "Łucja Kowalska-Müller", "Erika Mustermann"]
)
def test_girocode_inhalt_nach_epc_standard(tmp_path, inhaber):
    absender = Absender(
        name="Erika Mustermann",
        kontoinhaber=inhaber,
        iban="DE02120300000000202051",
        bic="",
    )
    r = rechnung(drei_positionen(), 19)
    felder = _girocode_lesen(rechnung_pdf(r, absender), tmp_path).split(b"\n")
    # Kennung, Version 002, Zeichensatz 1 (UTF-8), SEPA-Überweisung
    assert felder[:4] == [b"BCD", b"002", b"1", b"SCT"]
    assert felder[4] == b""  # BIC darf in Version 002 leer sein
    assert felder[5].decode("utf-8") == inhaber
    assert felder[6] == b"DE02120300000000202051"
    assert felder[7] == b"EUR327.44"
    assert felder[8:10] == [b"", b""]  # Purpose, strukturierte Referenz
    assert felder[10] == b"Rechnung 2026-001"
    assert len(felder) == 11


def test_girocode_der_erinnerung_ueber_offenen_betrag(tmp_path):
    r = rechnung(drei_positionen(), 19)
    daten = erinnerung_pdf(r, VOLL, offen=22744, heute=HEUTE, neue_frist=FRIST)
    felder = _girocode_lesen(daten, tmp_path).split(b"\n")
    assert felder[4] == b"BYLADEM1001"
    assert felder[5] == b"Erika Mustermann"
    assert felder[7] == b"EUR227.44"
    assert felder[10] == b"Rechnung 2026-001"


def test_extreme_eingaben_stuerzen_nicht_ab():
    viele_zeilen = "\n".join(f"Zeile {i} der Anschrift mit sehr langem Text" for i in range(9))
    r = rechnung(
        [pos("x" * 3000, "1", "Stunden à 60 Minuten nach Vereinbarung", 1)],
        empfaenger=viele_zeilen,
        bezug="Az. " + "123/45 " * 20,
        einleitung="Erste Zeile\n\nDritte Zeile",
        schluss="Mit freundlichen Grüßen\nund vielen Dank",
        leistung_bis=date(2027, 1, 15),
    )
    daten = rechnung_pdf(r, VOLL)
    gueltig(daten)
    leer = rechnung([], empfaenger="")
    gueltig(rechnung_pdf(leer, LEER))
