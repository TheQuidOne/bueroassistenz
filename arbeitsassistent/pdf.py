"""Rechnungen und Zahlungserinnerungen als PDF, A4 angelehnt an DIN 5008 Form B.

Maße in Millimetern. Das Anschriftfeld sitzt im Fenster eines DIN-lang-Umschlags.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from functools import cache
from io import BytesIO
from pathlib import Path

import segno.helpers
from fpdf import FPDF
from fpdf.enums import OutputIntentSubType
from fpdf.output import PDFICCProfile
from PIL import Image, ImageCms

from .geld import euro, menge_text
from .modell import Absender, Position, RechnungsDruck

SCHRIFTEN = Path(__file__).parent / "schriften"
SCHRIFT = "NotoSans"  # Familienname im PDF-Code, gilt für jede gewählte Schrift

# Wählbare Schriften. Noto Sans liegt bei (OFL). Arial darf nicht mitgeliefert werden; sie kommt
# vom System (auf dem Mac immer vorhanden) und wird wie bei Word ins PDF eingebettet.
SCHRIFTARTEN = {"noto": "Noto Sans (Standard)", "arial": "Arial"}
_ARIAL = [
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("~/Library/Fonts/Arial.ttf", "~/Library/Fonts/Arial Bold.ttf"),
    ("/usr/share/fonts/TTF/arial.ttf", "/usr/share/fonts/TTF/arialbd.ttf"),
    ("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf"),
    # Ersatz mit genau gleichen Maßen, wo Arial fehlt (z. B. auf dem Linux-Entwicklungsrechner)
    ("/usr/share/fonts/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]


def schriftdateien(wahl: str) -> tuple[Path, Path] | None:
    """(normal, fett) der gewählten Schrift, None wenn sie auf diesem Rechner fehlt."""
    if wahl != "arial":
        return SCHRIFTEN / "NotoSans-Regular.ttf", SCHRIFTEN / "NotoSans-Bold.ttf"
    for normal, fett in _ARIAL:
        normal, fett = Path(normal).expanduser(), Path(fett).expanduser()
        if normal.is_file() and fett.is_file():
            return normal, fett
    return None

LINKS = 25
RECHTS = 190
BREITE = RECHTS - LINKS
INFO_X = 125  # Informationsblock rechts neben dem Anschriftfeld
FUSS_LINIE = 269
INHALT_UNTEN = 262  # bis hier darf Inhalt stehen
FOLGESEITE_OBEN = 26

SCHWARZ = 20
GRAU = 105
LINIE = 175

EINLEITUNG = "Für meine Leistungen erlaube ich mir, wie folgt abzurechnen:"
SCHLUSS = "Vielen Dank für die Zusammenarbeit."
GRUSS = "Mit freundlichen Grüßen"  # Vorgabe der Grußformel (Einstellungen)
GEGENSTANDSLOS = (
    "Sollten Sie die Zahlung inzwischen veranlasst haben, betrachten Sie dieses "
    "Schreiben bitte als gegenstandslos."
)


@cache
def _srgb_profil() -> bytes:
    """sRGB-Farbprofil für PDF/A (von Pillow erzeugt, keine fremde Datei nötig)."""
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _datum(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _zeitraum(von: date, bis: date | None) -> str:
    if bis is None or bis == von:
        return _datum(von)
    if von.year == bis.year:
        return f"{von:%d.%m.}–{_datum(bis)}"
    return f"{_datum(von)} – {_datum(bis)}"


def _iban_text(iban: str) -> str:
    iban = iban.replace(" ", "").upper()
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def _iban_gueltig(iban: str) -> bool:
    if not 15 <= len(iban) <= 34 or not iban.isascii() or not iban.isalnum():
        return False
    if not iban[:2].isalpha() or not iban[2:4].isdigit():
        return False
    ziffern = "".join(str(int(z, 36)) for z in iban[4:] + iban[:4])
    return int(ziffern) % 97 == 1


def girocode(absender: Absender, cent: int, nummer: str) -> segno.QRCode | None:
    """EPC-QR-Code (Version 002) für eine SEPA-Überweisung, None wenn keiner möglich ist."""
    iban = absender.iban.replace(" ", "").upper()
    if cent <= 0 or not _iban_gueltig(iban):
        return None
    bic = absender.bic.replace(" ", "").upper()
    if len(bic) not in (8, 11):
        bic = ""  # in Version 002 innerhalb des EWR nicht nötig
    try:
        return segno.helpers.make_epc_qr(
            name=(absender.kontoinhaber.strip() or absender.name.strip())[:70],
            iban=iban,
            amount=Decimal(cent) / 100,
            text=f"Rechnung {nummer}"[:140],
            bic=bic or None,
            encoding="utf-8",  # Zeichensatz 1, wie im Beispiel des EPC; Umlaute gehen damit
        )
    except ValueError:
        return None


class _Brief(FPDF):
    def __init__(self, absender: Absender, titel: str, folgekopf: str, seiten: int | None):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.absender = absender
        self.folgekopf = folgekopf
        self.seiten = seiten  # Gesamtzahl, im ersten Durchgang noch unbekannt
        self.tabellenkopf: Callable[[], None] | None = None
        self.c_margin = 0
        self.set_margins(LINKS, 20, 210 - RECHTS)
        self.set_auto_page_break(False)
        self.alias_nb_pages(None)
        normal, fett = schriftdateien(absender.schrift) or schriftdateien("noto")
        self.add_font(SCHRIFT, "", normal)
        self.add_font(SCHRIFT, "B", fett)
        self.set_title(titel)
        self.set_author(absender.name)
        self.set_creator("Büroassistenz")
        self.set_lang("de-DE")
        # PDF/A verlangt ein Farbprofil. Mit der eingebetteten XML (zugferd.py) wird daraus PDF/A-3.
        self.add_output_intent(
            OutputIntentSubType.PDFA, "sRGB", registry_name="http://www.color.org",
            dest_output_profile=PDFICCProfile(contents=_srgb_profil(), n=3, alternate="DeviceRGB"),
            info="sRGB IEC61966-2.1",
        )
        self.schrift(10)

    def schrift(self, groesse: float, fett: bool = False, farbe: int = SCHWARZ):
        self.set_font(SCHRIFT, "B" if fett else "", groesse)
        self.set_text_color(farbe)

    def linie(self, x1: float, y: float, x2: float, staerke=0.2, farbe=LINIE):
        self.set_draw_color(farbe)
        self.set_line_width(staerke)
        self.line(x1, y, x2, y)

    def schreibe(self, x: float, y: float, breite: float, hoehe: float, inhalt: str, align="L"):
        self.set_xy(x, y)
        self.cell(breite, hoehe, inhalt, align=align)

    def header(self):
        if self.page_no() == 1:
            return
        self.schrift(8, farbe=GRAU)
        self.schreibe(LINKS, 12, BREITE, 4, self.folgekopf)
        self.schreibe(LINKS, 12, BREITE, 4, self.absender.name, align="R")
        self.linie(LINKS, 17.5, RECHTS)
        self.set_y(FOLGESEITE_OBEN)
        if self.tabellenkopf:
            self.tabellenkopf()

    def footer(self):
        spalten = _fussspalten(self.absender)
        if spalten:
            groesse = 7.0
            while True:
                self.schrift(groesse, farbe=GRAU)
                breiten = [max(self.get_string_width(z) for z in s) for s in spalten]
                if sum(breiten) + 5 * (len(spalten) - 1) <= BREITE or groesse <= 5.5:
                    break
                groesse -= 0.5
            luecke = (BREITE - sum(breiten)) / (len(spalten) - 1) if len(spalten) > 1 else 0
            self.linie(LINKS, FUSS_LINIE, RECHTS)
            x = LINKS
            for spalte, breite in zip(spalten, breiten):
                for i, zeile in enumerate(spalte):
                    self.schreibe(x, FUSS_LINIE + 2 + i * 3.2, breite, 3.2, zeile)
                x += breite + luecke
        self.schrift(7, farbe=GRAU)
        self.schreibe(LINKS, 287, BREITE, 3.2, f"Seite {self.page_no()} von {self.seiten}", "R")

    def platz(self, hoehe: float) -> bool:
        """Beginnt eine neue Seite, wenn hoehe nicht mehr passt."""
        if self.get_y() + hoehe <= INHALT_UNTEN:
            return False
        self.add_page()
        return True

    def zeilen(self, inhalt: str, breite: float) -> list[str]:
        """Bricht inhalt in der aktuellen Schrift auf breite um."""
        if not inhalt:
            return []
        return self.multi_cell(breite, 5, inhalt, dry_run=True, output="LINES")

    def absatz(self, inhalt: str, groesse=10.0, hoehe=4.8, abstand=4.0):
        """Fließtext ab der aktuellen Position, bricht bei Bedarf auf die nächste Seite um."""
        self.schrift(groesse)
        for zeile in self.zeilen(inhalt, BREITE):
            self.platz(hoehe)
            self.schreibe(LINKS, self.get_y(), BREITE, hoehe, zeile)
            self.set_y(self.get_y() + hoehe)
        self.set_y(self.get_y() + abstand)


def _zweimal(zeichnen: Callable[[int | None], _Brief]) -> bytes:
    """Erst zählen, dann mit bekannter Seitenzahl („Seite x von y“) erzeugen."""
    probe = zeichnen(None)
    _schliessen(probe)
    pdf = zeichnen(probe.pages_count)
    try:
        return bytes(pdf.output())
    finally:
        _schliessen(pdf)


def _schliessen(pdf: _Brief):
    # fpdf2 lässt die Schriftdateien offen
    for schrift in pdf.fonts.values():
        if hasattr(schrift, "ttfont"):
            schrift.ttfont.close()


def _fussspalten(a: Absender) -> list[list[str]]:
    inhaber = a.kontoinhaber.strip()
    spalten = [
        [a.name, a.strasse, a.plz_ort],
        [f"Tel. {a.telefon}" if a.telefon.strip() else "", a.email],
        [
            f"Steuernummer {a.steuernummer}" if a.steuernummer.strip() else "",
            f"USt-IdNr. {a.ust_id}" if a.ust_id.strip() else "",
            f"IK {a.ik}" if a.ik.strip() else "",
        ],
        [
            f"Kontoinhaber: {inhaber}" if inhaber and inhaber != a.name.strip() else "",
            a.bank,
            f"IBAN {_iban_text(a.iban)}" if a.iban.strip() else "",
            f"BIC {a.bic}" if a.bic.strip() else "",
        ],
    ]
    spalten = [[z.strip() for z in s if z.strip()] for s in spalten]
    return [s for s in spalten if s]


def _briefkopf(pdf: _Brief, a: Absender, empfaenger: str):
    pdf.add_page()
    pdf.schrift(15, fett=True)
    pdf.schreibe(LINKS, 18, BREITE, 7, a.name, "R")
    if a.zusatz.strip():
        pdf.schrift(9.5, farbe=GRAU)
        pdf.schreibe(LINKS, 25.5, BREITE, 5, a.zusatz.strip(), "R")

    # Falzmarken bei 105 und 210 mm, Lochmarke bei 148,5 mm
    for y, laenge in ((105, 5), (148.5, 7), (210, 5)):
        pdf.linie(4, y, 4 + laenge, staerke=0.15)

    # Rücksendeangabe unten in der Zusatz- und Vermerkzone (45 bis 62,7 mm)
    ruecksendung = " · ".join(t.strip() for t in (a.name, a.strasse, a.plz_ort) if t.strip())
    groesse = 7.0
    pdf.schrift(groesse, farbe=GRAU)
    while pdf.get_string_width(ruecksendung) > 80 and groesse > 5:
        groesse -= 0.5
        pdf.schrift(groesse, farbe=GRAU)
    pdf.schreibe(LINKS, 57.2, 80, 3.4, ruecksendung)
    if ruecksendung:
        pdf.linie(LINKS, 61.0, LINKS + min(80, pdf.get_string_width(ruecksendung)), 0.1)

    # Anschriftzone: 62,7 bis 90 mm
    zeilen = [z.strip() for z in empfaenger.splitlines() if z.strip()]
    groesse, hoehe = 10.0, 4.5
    if len(zeilen) > 6:
        hoehe = 27 / len(zeilen)
        groesse = max(7.0, min(10.0, hoehe / 0.45))
    pdf.schrift(groesse)
    while zeilen and max(pdf.get_string_width(z) for z in zeilen) > 80 and groesse > 7:
        groesse -= 0.5
        pdf.schrift(groesse)
    for i, zeile in enumerate(zeilen):
        pdf.schreibe(LINKS, 63.5 + i * hoehe, 80, hoehe, zeile)


def _infoblock(pdf: _Brief, angaben: list[tuple[str, str]]) -> float:
    """Angaben rechts neben dem Anschriftfeld, gibt das untere Ende zurück."""
    angaben = [(k, w.strip()) for k, w in angaben if w.strip()]
    pdf.schrift(9, farbe=GRAU)
    etikett = max(pdf.get_string_width(k) for k, _ in angaben) + 4
    wert_x, wert_breite = INFO_X + etikett, RECHTS - INFO_X - etikett
    y = 50.0
    for k, w in angaben:
        pdf.schrift(9, farbe=GRAU)
        pdf.schreibe(INFO_X, y, etikett, 4.4, k)
        pdf.schrift(9)
        for zeile in pdf.zeilen(w, wert_breite):
            pdf.schreibe(wert_x, y, wert_breite, 4.4, zeile)
            y += 4.4
    return y


def _titel(pdf: _Brief, titel: str, info_ende: float):
    pdf.schrift(12.5, fett=True)
    pdf.set_y(max(100.0, info_ende + 8))
    for zeile in pdf.zeilen(titel, BREITE):
        pdf.schreibe(LINKS, pdf.get_y(), BREITE, 6, zeile)
        pdf.set_y(pdf.get_y() + 6)
    pdf.set_y(pdf.get_y() + 4)


def _steuerangabe(a: Absender) -> tuple[str, str]:
    if a.steuernummer.strip():
        return "Steuernummer", a.steuernummer
    return "USt-IdNr.", a.ust_id


def _tabelle(pdf: _Brief, positionen: list[Position], danach: float = 0):
    """Positionstabelle; danach ist die Höhe des Summenblocks, der an der letzten Zeile hängt."""
    mit_datum = any(p.datum for p in positionen)
    titel = ["Pos.", "Datum", "Leistung", "Menge", "Einheit", "Einzelpreis", "Betrag"]
    ausrichtung = ["L", "L", "L", "R", "L", "R", "R"]
    werte = [
        [
            str(nr),
            _datum(p.datum) if p.datum else "",
            p.beschreibung,
            menge_text(p.menge),
            p.einheit,
            euro(p.einzelpreis),
            euro(p.gesamt),
        ]
        for nr, p in enumerate(positionen, start=1)
    ]
    if not mit_datum:
        for liste in (titel, ausrichtung, *werte):
            del liste[1]
    leistung, einheit = titel.index("Leistung"), titel.index("Einheit")

    # Spaltenbreiten nach Inhalt mit Mindestbreiten, „Leistung“ bekommt den Rest
    mindest = {"Menge": 10, "Einheit": 11, "Einzelpreis": 17, "Betrag": 18}
    luft = 1.6
    breiten = []
    for i, t in enumerate(titel):
        pdf.schrift(8.5, fett=True)
        b = pdf.get_string_width(t)
        pdf.schrift(9)
        b = max([b, mindest.get(t, 0)] + [pdf.get_string_width(w[i]) for w in werte])
        breiten.append(min(b, 22) if i == einheit else b)
    # Innenabstand nur zwischen den Spalten, außen schließt die Tabelle bündig ab
    innen = [(0 if i == 0 else luft, 0 if i == len(titel) - 1 else luft) for i in range(len(titel))]
    breiten = [b + li + re for b, (li, re) in zip(breiten, innen)]
    breiten[leistung] = max(40, BREITE - sum(breiten) + breiten[leistung])
    lage = []  # (x, Breite) des Textes je Spalte
    x = LINKS
    for b, (li, re) in zip(breiten, innen):
        lage.append((x + li, b - li - re))
        x += b

    hoehe, polster = 4.2, 1.25

    def kopf():
        pdf.schrift(8.5, fett=True)
        y = pdf.get_y()
        for t, (x, b), a in zip(titel, lage, ausrichtung):
            pdf.schreibe(x, y, b, 5, t, a)
        pdf.linie(LINKS, y + 6, RECHTS, staerke=0.3, farbe=GRAU)
        pdf.set_y(y + 6)

    pdf.schrift(9)
    umbrechen = (leistung, einheit)
    zellen = [
        [pdf.zeilen(w, lage[i][1]) if i in umbrechen else [w] for i, w in enumerate(zeile)]
        for zeile in werte
    ]
    # Die Erläuterung steht klein und grau unter der Leistung.
    haupt = [len(z[leistung]) for z in zellen]
    pdf.schrift(8)
    for zeile, p in zip(zellen, positionen):
        zeile[leistung] = zeile[leistung] + pdf.zeilen(p.erlaeuterung.strip(), lage[leistung][1])
    if zellen:
        erste = max(len(z) for z in zellen[0])
        pdf.platz(6 + min(erste, 3) * hoehe + 2 * polster)
    kopf()
    pdf.tabellenkopf = kopf

    for nr, zeile in enumerate(zellen, start=1):
        n = max(len(z) for z in zeile)
        frei = int((INHALT_UNTEN - pdf.get_y() - 2 * polster) // hoehe)
        # Kurze Zeilen wandern ganz auf die nächste Seite, sehr lange werden geteilt.
        # Die letzte Zeile bleibt mit den Summen zusammen.
        if n > frei and (n * hoehe <= 50 or frei < 3):
            pdf.add_page()
        elif nr == len(zellen) and n * hoehe <= 50:
            pdf.platz(n * hoehe + 2 * polster + 3 + danach)
        a = 0
        while a < n:
            frei = int((INHALT_UNTEN - pdf.get_y() - 2 * polster) // hoehe)
            e = min(n, a + max(frei, 1))
            if n - e == 1 and e - a > 2:
                e -= 1  # keine einzelne Zeile allein auf der nächsten Seite
            y = pdf.get_y() + polster
            for i, (zelle, (x, b), ausr) in enumerate(zip(zeile, lage, ausrichtung)):
                for k, text in enumerate(zelle[a:e]):
                    if i == leistung and a + k >= haupt[nr - 1]:
                        pdf.schrift(8, farbe=GRAU)
                    else:
                        pdf.schrift(9)
                    pdf.schreibe(x, y + k * hoehe, b, hoehe, text, ausr)
            pdf.set_y(y + (e - a) * hoehe + polster)
            a = e
            if a < n:
                pdf.add_page()
        pdf.linie(LINKS, pdf.get_y(), RECHTS, staerke=0.1)
    pdf.tabellenkopf = None
    pdf.set_y(pdf.get_y() + 3)


def _summen_hoehe(zeilen: list[tuple[str, str, bool]]) -> float:
    return len(zeilen) * 5.2 + 1.6


def _summen(pdf: _Brief, zeilen: list[tuple[str, str, bool]]):
    """Rechtsbündiger Summenblock; fette Zeilen werden durch eine Linie abgesetzt."""
    hoehe = 5.2
    pdf.platz(_summen_hoehe(zeilen))
    x, etikett = 112, 50
    wert = RECHTS - x - etikett
    for i, (k, w, fett) in enumerate(zeilen):
        if fett and i > 0:
            y = pdf.get_y() + 0.8
            pdf.linie(x, y, RECHTS, staerke=0.3, farbe=GRAU)
            pdf.set_y(y + 0.8)
        pdf.schrift(10, fett=fett)
        y = pdf.get_y()
        pdf.schreibe(x, y, etikett, hoehe, k)
        pdf.schreibe(x + etikett, y, wert, hoehe, w, "R")
        pdf.set_y(y + hoehe)
    pdf.set_y(pdf.get_y() + 5)


def _zahlung(pdf: _Brief, inhalt: str, qr: segno.QRCode | None, danach: float = 0):
    """Zahlungstext, rechts daneben der Girocode. danach: Höhe des Schlusses, der mitwandert."""
    hoehe = 4.8
    pdf.schrift(10)
    if qr is None:
        pdf.platz(len(pdf.zeilen(inhalt, BREITE)) * hoehe + 4 + danach)
        pdf.absatz(inhalt)
        return
    seite = 27.0
    breite = BREITE - seite - 10
    zeilen = pdf.zeilen(inhalt, breite)
    pdf.platz(max(len(zeilen) * hoehe, seite + 5) + 6 + danach)
    y0 = pdf.get_y()
    for i, zeile in enumerate(zeilen):
        pdf.schreibe(LINKS, y0 + i * hoehe, breite, hoehe, zeile)

    module = qr.symbol_size(border=0)[0]
    m = seite / module
    x0 = RECHTS - seite
    pdf.set_fill_color(0)
    for r, reihe in enumerate(qr.matrix):
        c = 0
        while c < module:
            if not reihe[c]:
                c += 1
                continue
            start = c
            while c < module and reihe[c]:
                c += 1
            pdf.rect(x0 + start * m, y0 + r * m, (c - start) * m, m, style="F")
    pdf.schrift(6.5, farbe=GRAU)
    unterschrift = "Mit der Banking-App scannen"
    w = pdf.get_string_width(unterschrift)
    pdf.schreibe(min(RECHTS - w, x0 + (seite - w) / 2), y0 + seite + 1.2, w, 3, unterschrift)
    pdf.set_y(y0 + max(len(zeilen) * hoehe, seite + 4.5) + 6)


def _absatz_hoehe(pdf: _Brief, inhalt: str, abstand: float = 4) -> float:
    pdf.schrift(10)
    return len(pdf.zeilen(inhalt, BREITE)) * 4.8 + abstand


def _unterschrift_mass(bild: bytes) -> tuple[float, float]:
    """(Breite, Höhe) in mm: etwa 15 mm hoch, höchstens 60 mm breit."""
    with Image.open(BytesIO(bild)) as b:
        verhaeltnis = b.width / b.height
    hoehe = 15.0
    breite = min(60.0, hoehe * verhaeltnis)
    return breite, breite / verhaeltnis


def _gruss_abstaende(absender: Absender) -> tuple[float, float]:
    """Abstand nach dem Schlusstext und nach der Grußformel (vor Unterschrift bzw. Name)."""
    vor_name = 2 if absender.unterschrift else 6
    return (4 if absender.grussformel.strip() else vor_name), vor_name


def _gruss_hoehe(pdf: _Brief, schluss: str, absender: Absender) -> float:
    nach_schluss, nach_formel = _gruss_abstaende(absender)
    hoehe = _absatz_hoehe(pdf, schluss, nach_schluss) if schluss.strip() else 0
    if absender.grussformel.strip():
        hoehe += _absatz_hoehe(pdf, absender.grussformel.strip(), nach_formel)
    if absender.unterschrift:
        hoehe += _unterschrift_mass(absender.unterschrift)[1] + 1
    return hoehe + 4.8


def _gruss(pdf: _Brief, schluss: str, absender: Absender):
    """Schlusstext, Grußformel, Unterschrift und Name."""
    pdf.platz(_gruss_hoehe(pdf, schluss, absender))
    nach_schluss, nach_formel = _gruss_abstaende(absender)
    if schluss.strip():
        pdf.absatz(schluss.strip(), abstand=nach_schluss)
    if absender.grussformel.strip():
        pdf.absatz(absender.grussformel.strip(), abstand=nach_formel)
    if absender.unterschrift:
        breite, hoehe = _unterschrift_mass(absender.unterschrift)
        pdf.image(BytesIO(absender.unterschrift), x=LINKS, y=pdf.get_y(), w=breite, h=hoehe)
        pdf.set_y(pdf.get_y() + hoehe + 1)
    pdf.schreibe(LINKS, pdf.get_y(), BREITE, 4.8, absender.name)


def _zahlungstext(betrag: str, frist: date, nummer: str, absender: Absender) -> str:
    konto = " auf das unten genannte Konto" if absender.iban.strip() else ""
    return (
        f"Bitte überweisen Sie den Betrag von {betrag} bis zum {_datum(frist)} "
        f"unter Angabe der Rechnungsnummer {nummer}{konto}."
    )


def _vorgaenger(nummer: str, datum: date | None) -> str:
    """„2026-005 vom 14.09.2026“"""
    return nummer + (f" vom {_datum(datum)}" if datum else "")


def rechnung_pdf(r: RechnungsDruck, absender: Absender) -> bytes:
    storno = r.art == "storno"
    korrektur = bool(r.korrektur_zu) and not storno
    if storno:
        titel = f"Stornorechnung Nr. {r.nummer}"
        if r.storno_zu:
            titel += f" zu Rechnung Nr. {r.storno_zu}"
    elif korrektur:
        titel = f"Rechnungskorrektur Nr. {r.nummer}"
    else:
        titel = f"Rechnung Nr. {r.nummer}"

    einleitung = r.einleitung.strip()
    if not einleitung:
        if storno and r.storno_zu:
            einleitung = f"Hiermit storniere ich meine Rechnung Nr. {r.storno_zu} wie folgt:"
        else:
            einleitung = EINLEITUNG
    # Berichtigung nach § 31 Abs. 5 UStDV: ausdrücklicher Bezug auf die ersetzte Rechnung.
    bezugssatz = (
        f"Diese Rechnungskorrektur berichtigt und ersetzt meine Rechnung "
        f"Nr. {_vorgaenger(r.korrektur_zu, r.vorgaenger_datum)}. Die ursprüngliche Rechnung ist damit "
        "gegenstandslos." if korrektur else ""
    )
    if r.ust_satz > 0:
        summen = [
            ("Nettobetrag", euro(r.netto), False),
            (f"zzgl. {r.ust_satz} % Umsatzsteuer", euro(r.ust), False),
            ("Gesamtbetrag", euro(r.brutto), True),
        ]
    else:
        summen = [("Gesamtbetrag", euro(r.brutto), True)]
    zu_zahlen = r.brutto - r.bereits_bezahlt
    if r.bereits_bezahlt and not storno:
        summen += [("bereits gezahlt", euro(-r.bereits_bezahlt), False),
                   ("Noch zu zahlen", euro(max(zu_zahlen, 0)), True)]
    schluss = r.schluss.strip() or absender.schluss.strip() or SCHLUSS

    def zeichnen(seiten: int | None) -> _Brief:
        pdf = _Brief(absender, titel, f"{titel} vom {_datum(r.datum)}", seiten)
        _briefkopf(pdf, absender, r.empfaenger)
        ein_tag = r.leistung_bis in (None, r.leistung_von)
        leistung = "Leistungsdatum" if ein_tag else "Leistungszeitraum"
        angaben = [
            ("Rechnungsnummer", r.nummer),
            ("Rechnungsdatum", _datum(r.datum)),
            (leistung, _zeitraum(r.leistung_von, r.leistung_bis)),
            ("Zahlbar bis", "" if storno or zu_zahlen <= 0 else _datum(r.faellig)),
            ("Ersetzt Rechnung", _vorgaenger(r.korrektur_zu, r.vorgaenger_datum) if korrektur else ""),
            ("Ihr Zeichen", r.bezug),
            _steuerangabe(absender),
            ("IK", absender.ik),
        ]
        _titel(pdf, titel, _infoblock(pdf, angaben))
        if bezugssatz:
            pdf.absatz(bezugssatz)
        pdf.absatz(einleitung)
        if r.positionen:
            _tabelle(pdf, r.positionen, _summen_hoehe(summen))
        _summen(pdf, summen)
        if r.steuerhinweis.strip():
            pdf.absatz(r.steuerhinweis.strip(), abstand=6)
        if r.zusatztext.strip() and not storno:
            pdf.absatz(r.zusatztext.strip(), abstand=6)
        if not storno and zu_zahlen > 0:
            text = _zahlungstext(euro(zu_zahlen), r.faellig, r.nummer, absender)
            danach = _gruss_hoehe(pdf, schluss, absender)
            _zahlung(pdf, text, girocode(absender, zu_zahlen, r.nummer), danach)
        _gruss(pdf, schluss, absender)
        return pdf

    return _zweimal(zeichnen)


def erinnerung_pdf(
    r: RechnungsDruck, absender: Absender, offen: int, heute: date, neue_frist: date
) -> bytes:
    titel = f"Zahlungserinnerung zu Rechnung Nr. {r.nummer}"
    gezahlt = r.brutto - offen
    summen = [("Rechnungsbetrag", euro(r.brutto), False)]
    if gezahlt > 0:
        summen.append(("bereits gezahlt", euro(-gezahlt), False))
    summen.append(("Offener Betrag", euro(offen), True))

    def zeichnen(seiten: int | None) -> _Brief:
        pdf = _Brief(absender, titel, f"{titel} vom {_datum(heute)}", seiten)
        _briefkopf(pdf, absender, r.empfaenger)
        angaben = [
            ("Datum", _datum(heute)),
            ("Rechnungsnummer", r.nummer),
            ("Rechnungsdatum", _datum(r.datum)),
            ("Fällig seit", _datum(r.faellig)),
            ("Ihr Zeichen", r.bezug),
            _steuerangabe(absender),
            ("IK", absender.ik),
        ]
        _titel(pdf, titel, _infoblock(pdf, angaben))
        pdf.absatz("Sehr geehrte Damen und Herren,")
        pdf.absatz(
            f"bei der Durchsicht meiner Unterlagen habe ich festgestellt, dass meine Rechnung "
            f"Nr. {r.nummer} vom {_datum(r.datum)}, fällig am {_datum(r.faellig)}, "
            f"{'noch nicht vollständig' if gezahlt > 0 else 'noch nicht'} beglichen ist."
        )
        _summen(pdf, summen)
        danach = _gruss_hoehe(pdf, GEGENSTANDSLOS, absender)
        if offen > 0:
            text = _zahlungstext(euro(offen), neue_frist, r.nummer, absender)
            text = text.replace("den Betrag", "den offenen Betrag", 1)
            _zahlung(pdf, text, girocode(absender, offen, r.nummer), danach)
        pdf.platz(danach)
        _gruss(pdf, GEGENSTANDSLOS, absender)
        return pdf

    return _zweimal(zeichnen)
