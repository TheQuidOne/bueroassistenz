"""Eingescannte Unterschrift: hochladen, aufbereiten, im Datenordner ablegen.

Aus einem Foto oder Scan (PNG, JPG, TIFF, PDF; HEIC vom iPhone nur auf dem Mac) wird ein
sauberes PNG: Papier rein weiß, Tinte in ihrer Farbe, eng zugeschnitten, ohne
Transparenz (PDF/A). Die Datei liegt nur im Datenordner, nie im Programm oder Repo.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError
from pypdf import PdfReader

from .modell import Fehler

DATEI = "unterschrift.png"
GROESSTE = (1200, 500)  # Pixel, reicht für den Druck in etwa 5 cm Breite
UNLESBAR = ("Die Datei lässt sich nicht als Bild lesen. Bitte ein Foto oder einen Scan der "
            "Unterschrift als PNG, JPG oder PDF hochladen.")


def pfad(ordner: Path) -> Path:
    return ordner / DATEI


def laden(ordner: Path) -> bytes:
    """Die aufbereitete Unterschrift, oder b"", wenn keine hinterlegt ist."""
    datei = pfad(ordner)
    return datei.read_bytes() if datei.is_file() else b""


def _aus_pdf(roh: bytes) -> Image.Image:
    """Das größte Bild der ersten Seite (ein Scan ist im PDF ein Bild)."""
    try:
        seite = PdfReader(BytesIO(roh)).pages[0]
        bilder = [b.image for b in seite.images]
    except Exception:
        raise Fehler(UNLESBAR) from None
    if not bilder:
        raise Fehler("Im PDF ist kein eingescanntes Bild. Bitte die Unterschrift fotografieren "
                     "oder scannen und als Bild hochladen.")
    return max(bilder, key=lambda b: b.width * b.height)


def _ist_heic(roh: bytes) -> bool:
    return roh[4:8] == b"ftyp" and roh[8:12] in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heif")


def _mit_sips(roh: bytes) -> Image.Image:
    """HEIC vom iPhone: macOS wandelt mit dem eingebauten „sips“ in PNG um."""
    if sys.platform != "darwin" or not shutil.which("sips"):
        raise Fehler("Fotos im HEIC-Format kann das Programm hier nicht lesen. Bitte das Foto als "
                     "JPG oder PNG speichern (Vorschau: Ablage → Exportieren).")
    with tempfile.TemporaryDirectory() as tmp:
        ein, aus = os.path.join(tmp, "ein.heic"), os.path.join(tmp, "aus.png")
        Path(ein).write_bytes(roh)
        try:
            subprocess.run(["sips", "-s", "format", "png", ein, "--out", aus],
                           check=True, capture_output=True, timeout=60)
            with Image.open(aus) as bild:
                return bild.copy()
        except (subprocess.SubprocessError, OSError, UnidentifiedImageError):
            raise Fehler(UNLESBAR) from None


def _oeffnen(roh: bytes) -> Image.Image:
    if roh.startswith(b"%PDF"):
        return _aus_pdf(roh)
    if _ist_heic(roh):
        return _mit_sips(roh)
    try:
        bild = Image.open(BytesIO(roh))
        bild.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise Fehler(UNLESBAR) from None
    return ImageOps.exif_transpose(bild)  # Handyfotos richtig herum drehen


def aufbereiten(roh: bytes) -> bytes:
    """Foto oder Scan → PNG mit weißem Papier, eng um die Unterschrift zugeschnitten."""
    bild = _oeffnen(roh)
    if bild.mode in ("RGBA", "LA", "PA") or (bild.mode == "P" and "transparency" in bild.info):
        papier = Image.new("RGB", bild.size, "white")
        papier.paste(bild.convert("RGBA"), mask=bild.convert("RGBA").getchannel("A"))
        bild = papier
    bild = ImageOps.autocontrast(bild.convert("RGB"), cutoff=1)
    grau = bild.convert("L")
    # Handyfotos sind ungleichmäßig ausgeleuchtet. Darum die Papierhelligkeit örtlich schätzen
    # (das Maximum der Umgebung überdeckt die dünnen Striche) und nur, was deutlich dunkler
    # ist als das Papier ringsum, als Tinte nehmen. Weiche Kanten bleiben weich.
    faktor = max(1, max(grau.size) // 200)
    klein = grau.reduce(faktor).filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(4))
    papier = klein.resize(grau.size, Image.Resampling.BILINEAR)
    tinte = ImageChops.subtract(papier, grau).point(lambda v: 0 if v < 25 else 255 if v > 65 else (v - 25) * 255 // 40)
    bild = Image.composite(bild, Image.new("RGB", bild.size, "white"), tinte)
    # Einzelne Staubkörner zählen beim Zuschneiden nicht mit.
    rahmen = tinte.point(lambda v: 255 if v > 128 else 0).filter(ImageFilter.MedianFilter(5)).getbbox()
    if not rahmen:
        raise Fehler("Auf dem Bild ist keine Unterschrift zu erkennen. Bitte mit dunklem Stift auf "
                     "weißem Papier unterschreiben und erneut fotografieren.")
    rand = max(4, (rahmen[3] - rahmen[1]) // 12)
    links, oben, rechts, unten = rahmen
    bild = bild.crop((max(0, links - rand), max(0, oben - rand),
                      min(bild.width, rechts + rand), min(bild.height, unten + rand)))
    bild.thumbnail(GROESSTE)
    ausgabe = BytesIO()
    bild.save(ausgabe, "PNG", optimize=True)
    return ausgabe.getvalue()


def speichern(ordner: Path, roh: bytes) -> None:
    daten = aufbereiten(roh)
    ziel = pfad(ordner)
    neu = ziel.with_suffix(".neu")
    neu.write_bytes(daten)
    neu.replace(ziel)  # erst nach vollständigem Schreiben austauschen


def entfernen(ordner: Path) -> None:
    pfad(ordner).unlink(missing_ok=True)
