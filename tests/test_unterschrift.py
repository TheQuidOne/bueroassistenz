"""Eingescannte Unterschrift: Aufbereitung, Ablage und Platz auf der Rechnung."""
import subprocess
from datetime import date
from io import BytesIO

import pytest
from fpdf import FPDF
from PIL import Image, ImageDraw
from pypdf import PdfReader

from arbeitsassistent import rechnungen, unterschrift
from arbeitsassistent.modell import Fehler


def foto(format_="JPEG", papier=(214, 210, 200), tinte=(20, 40, 140)) -> bytes:
    """Wie ein Handyfoto: graues Papier, blaue Tinte, viel Rand."""
    bild = Image.new("RGB", (1600, 1000), papier)
    zeichnen = ImageDraw.Draw(bild)
    zeichnen.line([(500, 520), (620, 430), (700, 560), (820, 440), (950, 540), (1100, 470)], fill=tinte, width=9)
    zeichnen.ellipse((560, 470, 640, 560), outline=tinte, width=7)
    ausgabe = BytesIO()
    bild.save(ausgabe, format_)
    return ausgabe.getvalue()


def lesen(png: bytes) -> Image.Image:
    bild = Image.open(BytesIO(png))
    bild.load()
    return bild


def test_foto_wird_weiss_und_zugeschnitten():
    bild = lesen(unterschrift.aufbereiten(foto()))
    assert bild.format == "PNG" and bild.mode == "RGB"  # ohne Transparenz, PDF/A-tauglich
    assert bild.width < 800 and bild.height < 300  # eng um die Unterschrift
    assert bild.getpixel((0, 0)) == (255, 255, 255) and bild.getpixel((bild.width - 1, bild.height - 1)) == (255, 255, 255)
    farben = {f for _, f in bild.getcolors(1 << 20)}
    assert any(b > r + 40 for r, g, b in farben)  # blaue Tinte bleibt blau


def test_ungleichmaessiges_licht_wie_beim_handyfoto():
    bild = Image.new("RGB", (2000, 1300))
    zeichnen = ImageDraw.Draw(bild)
    for x in range(2000):  # links hell, rechts im Schatten
        zeichnen.line([(x, 0), (x, 1300)], fill=(215 - x // 25, 212 - x // 25, 205 - x // 25))
    zeichnen.line([(700, 650), (900, 560), (1100, 690), (1300, 600)], fill=(25, 40, 120), width=10)
    roh = BytesIO()
    bild.save(roh, "JPEG")
    ergebnis = lesen(unterschrift.aufbereiten(roh.getvalue()))
    assert ergebnis.width < 800 and ergebnis.height < 300  # Schatten zählt nicht als Tinte
    assert ergebnis.getpixel((ergebnis.width - 1, 0)) == (255, 255, 255)


def test_transparentes_png_auf_weiss():
    bild = Image.new("RGBA", (600, 300), (0, 0, 0, 0))
    ImageDraw.Draw(bild).line([(100, 150), (500, 120)], fill=(0, 0, 0, 255), width=8)
    roh = BytesIO()
    bild.save(roh, "PNG")
    ergebnis = lesen(unterschrift.aufbereiten(roh.getvalue()))
    assert ergebnis.mode == "RGB" and ergebnis.getpixel((0, 0)) == (255, 255, 255)


def test_scan_als_pdf():
    pdf = FPDF()
    pdf.add_page()
    pdf.image(BytesIO(foto("PNG")), x=10, y=10, w=120)
    ergebnis = lesen(unterschrift.aufbereiten(bytes(pdf.output())))
    assert ergebnis.width < 800


@pytest.mark.parametrize("roh, meldung", [
    (b"kein Bild", "nicht als Bild lesen"),
    (b"%PDF-1.4 kaputt", "nicht als Bild lesen"),
    (foto("PNG", papier=(255, 255, 255), tinte=(255, 255, 255)), "keine Unterschrift"),
])
def test_unbrauchbare_dateien(roh, meldung):
    with pytest.raises(Fehler, match=meldung):
        unterschrift.aufbereiten(roh)


HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64


def test_heic_ohne_mac(monkeypatch):
    monkeypatch.setattr(unterschrift.sys, "platform", "linux")
    with pytest.raises(Fehler, match="HEIC"):
        unterschrift.aufbereiten(HEIC)


def test_heic_auf_dem_mac_ueber_sips(monkeypatch):
    aufrufe = []

    def sips(befehl, **_):
        aufrufe.append(befehl)
        Image.open(BytesIO(foto("PNG"))).save(befehl[befehl.index("--out") + 1])
        return subprocess.CompletedProcess(befehl, 0)

    monkeypatch.setattr(unterschrift.sys, "platform", "darwin")
    monkeypatch.setattr(unterschrift.shutil, "which", lambda name: "/usr/bin/sips")
    monkeypatch.setattr(unterschrift.subprocess, "run", sips)
    assert lesen(unterschrift.aufbereiten(HEIC)).width < 800
    assert aufrufe[0][:4] == ["sips", "-s", "format", "png"]


def bilder_im_pdf(daten: bytes) -> int:
    return sum(len(seite.images) for seite in PdfReader(BytesIO(daten)).pages)


def test_unterschrift_auf_rechnung_und_erinnerung(db, einst, kunde, ordner):
    unterschrift.speichern(ordner, foto())
    assert unterschrift.pfad(ordner).is_file()
    rid = rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
        {"datum": "2026-10-07", "beschreibung": "Dolmetscheinsatz DGS", "menge": "2", "einzelpreis": "85"}]}, einst)
    assert bilder_im_pdf(rechnungen.vorschau_pdf(db, rid, einst, ordner)) == 1
    rechnungen.ausstellen(db, rid, einst, date(2026, 10, 9), ordner)
    r = rechnungen.rechnung(db, rid)
    assert bilder_im_pdf((ordner / r["pdf_pfad"]).read_bytes()) == 1
    _, erinnerung = rechnungen.erinnerung(db, rid, einst, date(2026, 11, 1), ordner)
    assert bilder_im_pdf(erinnerung) == 1
    unterschrift.entfernen(ordner)
    assert bilder_im_pdf(rechnungen.vorschau_pdf(db, rid, einst, ordner)) == 0
