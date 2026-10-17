"""Alte Rechnungen nachtragen: Werte aus einem vorhandenen PDF vorschlagen.

Reihenfolge: eingebettete E-Rechnung (exakt, ohne KI) → Text im PDF (etwa aus Word) mit der
lokalen KI → eingescannte Seite als Bild mit der lokalen KI. Die KI schlägt nur vor. Werte
aus dem Text werden geprüft (Nummer, Betrag und Daten müssen im Dokument stehen), sonst
bleibt das Feld leer. Gespeichert wird, was im Formular steht.
"""

import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

from PIL import Image
from pypdf import PdfReader

from . import abgleich
from .geld import euro
from .ki import DATUM_MUSTER, KiFehler

GESAMT_MUSTER = r"^([0-9]{1,7}[.,][0-9]{2})?$"
SCHEMA = {
    "type": "object",
    "properties": {
        "rechnungsnummer": {"type": "string", "maxLength": 40},
        "rechnungsdatum": {"type": "string", "pattern": DATUM_MUSTER},
        "empfaenger": {"type": "string", "maxLength": 120},
        "gesamtbetrag": {"type": "string", "pattern": GESAMT_MUSTER},
        "leistung_von": {"type": "string", "pattern": DATUM_MUSTER},
        "leistung_bis": {"type": "string", "pattern": DATUM_MUSTER},
        "faellig": {"type": "string", "pattern": DATUM_MUSTER},
        "leistung": {"type": "string", "maxLength": 80},
        "ihr_zeichen": {"type": "string", "maxLength": 60},
        "steuerhinweis": {"type": "string", "maxLength": 160},
    },
    "required": ["rechnungsnummer", "rechnungsdatum", "empfaenger", "gesamtbetrag", "leistung_von",
                 "leistung_bis", "faellig", "leistung", "ihr_zeichen", "steuerhinweis"],
}
SYSTEM = (
    "Du liest eine Rechnung, die {absender} geschrieben hat, und gibst ihre Angaben als JSON zurück. "
    "Nur was im Dokument steht, nichts ergänzen oder raten; fehlt etwas, bleibt das Feld leer.\n"
    "- rechnungsnummer: genau wie gedruckt.\n"
    "- rechnungsdatum, leistung_von, leistung_bis, faellig: als JJJJ-MM-TT. leistung_von ist der "
    "(erste) Leistungs- oder Einsatztag, leistung_bis nur bei einem Zeitraum. faellig nur, wenn ein "
    "Zahlungsdatum („zahlbar bis“) genannt ist.\n"
    "- empfaenger: Name des Rechnungsempfängers (erste Zeile der Anschrift), nie {absender}.\n"
    "- gesamtbetrag: der zu zahlende Endbetrag (brutto) mit zwei Nachkommastellen, z. B. 212.50.\n"
    "- leistung: die Leistung in wenigen Worten, z. B. „Dolmetscheinsatz DGS“.\n"
    "- ihr_zeichen: Aktenzeichen oder Zeichen des Kunden, falls angegeben.\n"
    "- steuerhinweis: der Satz zur Umsatzsteuer, wörtlich (z. B. Steuerbefreiung oder „zzgl. 19 %“)."
)
_BETRAG = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+),(\d{2})(?![\d,])")


def _wert(element) -> str:
    """Text eines drafthorse-Elements (die Bibliothek hat dafür keine öffentliche Eigenschaft)."""
    for name in ("_text", "_amount", "_value"):
        wert = getattr(element, name, None)
        if wert not in (None, ""):
            return str(wert)
    return ""


def _e_rechnung(leser: PdfReader) -> dict | None:
    from drafthorse.models.document import Document
    try:
        anhaenge = leser.attachments
    except Exception:
        return None
    for name, inhalte in anhaenge.items():
        if not name.lower().endswith(".xml") or not inhalte:
            continue
        try:
            doc = Document.parse(inhalte[0])
        except Exception:
            continue
        summe = doc.trade.settlement.monetary_summation
        zeitraum = doc.trade.settlement.period
        bedingungen = list(doc.trade.settlement.terms.children)
        steuern = list(doc.trade.settlement.trade_tax.children)
        kategorien = {_wert(t.category_code) for t in steuern}
        gruende = " ".join([_wert(t.exemption_reason) for t in steuern]
                           + [_wert(n.content) for n in doc.header.notes.children])
        positionen = list(doc.trade.items.children)
        return {
            "nummer": _wert(doc.header.id),
            "datum": _wert(doc.header.issue_date_time),
            "kunde_name": _wert(doc.trade.agreement.buyer.name),
            "betrag": _wert(summe.grand_total),
            "leistung_von": _wert(doc.trade.delivery.event.occurrence) or _wert(zeitraum.start),
            "leistung_bis": _wert(zeitraum.end),
            "faellig": _wert(bedingungen[0].due) if bedingungen else "",
            "bezug": _wert(doc.trade.agreement.buyer_reference),
            "steuerfall": "ust19" if kategorien == {"S"} else _steuerfall(gruende),
            "beschreibung": _wert(positionen[0].product.name) if positionen else "",
        }
    return None


def _text(leser: PdfReader) -> str:
    try:
        return "\n".join((seite.extract_text() or "") for seite in leser.pages[:3])
    except Exception:
        return ""


def _bild(leser: PdfReader) -> bytes | None:
    """Die eingescannte erste Seite als PNG (verkleinert, das reicht der KI)."""
    try:
        bilder = [b.image for b in leser.pages[0].images]
    except Exception:
        return None
    if not bilder:
        return None
    bild = max(bilder, key=lambda b: b.width * b.height).convert("RGB")
    bild.thumbnail((1600, 1600))
    ausgabe = BytesIO()
    bild.save(ausgabe, "PNG")
    return ausgabe.getvalue()


def _steuerfall(text: str) -> str:
    """Steuerfall aus den Pflichthinweisen im Text, ohne KI."""
    flach = re.sub(r"\s+", " ", text)
    if re.search(r"§\s?4 Nr\.? ?\d+|umsatzsteuerfrei|steuerfrei", flach, re.I):
        return "steuerfrei"
    if re.search(r"§\s?19 UStG|Kleinunternehmer", flach, re.I):
        return "kleinunternehmer"
    if re.search(r"19\s?%", flach):
        return "ust19"
    return ""


def _datum_im_text(wert: str, text: str) -> str:
    try:
        d = date.fromisoformat(wert)
    except ValueError:
        return ""
    varianten = {f"{d:%d.%m.%Y}", f"{d.day}.{d.month}.{d.year}", f"{d:%d.%m.%y}", d.isoformat()}
    return wert if any(v in text for v in varianten) else ""


def _betrag_im_text(wert: str, text: str) -> str:
    try:
        cent = int(Decimal(wert.replace(",", ".")) * 100)
    except (InvalidOperation, ValueError):
        return ""
    im_text = {int(ganz.replace(".", "")) * 100 + int(rest) for ganz, rest in _BETRAG.findall(text)}
    return euro(cent, zeichen=False) if cent in im_text else ""


def _flach(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _kunde(name: str, text: str, kunden: list[sqlite3.Row]) -> int | None:
    """Bekannter Kunde zum Empfänger: gleicher Name, sonst genau ein Kundenname im Dokument."""
    passend = [k for k in kunden if name and abgleich.name_passt(name, k["name"])]
    if len(passend) != 1 and name:
        passend = [k for k in kunden if _flach(name) in _flach(k["name"]) or _flach(k["name"]) in _flach(name)]
    if len(passend) != 1 and text:
        passend = [k for k in kunden if _flach(k["name"]) in _flach(text)]
    return passend[0]["id"] if len(passend) == 1 else None


def vorschlag(roh: bytes, ki, kunden: list[sqlite3.Row], absender: str = "") -> dict:
    """Vorschläge fürs Nachtrag-Formular aus einem PDF. Wirft nicht, außer bei unlesbarer Datei.

    Rückgabe: Formularfelder (nummer, datum, betrag, …), dazu „quelle“ (E-Rechnung, Text, Bild
    oder leer), „kunde_name“ (wie im Dokument) und „hinweis“ (was der Mensch prüfen soll).
    """
    try:
        leser = PdfReader(BytesIO(roh))
    except Exception:
        raise ValueError("Die Datei lässt sich nicht als PDF lesen.") from None
    e = _e_rechnung(leser)
    if e:
        e["kunde_id"] = _kunde(e["kunde_name"], "", kunden)
        if e["betrag"]:
            e["betrag"] = euro(int(Decimal(e["betrag"]) * 100), zeichen=False)
        return {**e, "quelle": "E-Rechnung",
                "hinweis": "Aus der eingebetteten E-Rechnung übernommen. Bitte kurz prüfen."}

    text = _text(leser)
    bild = None if len(text.strip()) >= 40 else _bild(leser)
    if len(text.strip()) < 40 and not bild:
        return {"quelle": "", "hinweis": "In diesem PDF ist kein lesbarer Text. Bitte von Hand eintragen."}
    bereit, meldung = ki.status()
    if not bereit:
        return {"quelle": "", "steuerfall": _steuerfall(text),
                "hinweis": f"{meldung} Bitte die Angaben von Hand eintragen."}
    system = SYSTEM.format(absender=absender or "der Dolmetscher")
    try:
        if bild:
            a = ki.chat_json(system, "Hier ist die eingescannte Rechnung als Bild.", SCHEMA, bilder=[bild])
        else:
            a = ki.chat_json(system, f"Text der Rechnung:\n\n{text[:6000]}", SCHEMA)
    except (KiFehler, ValueError) as fehler:
        return {"quelle": "", "steuerfall": _steuerfall(text),
                "hinweis": f"Die KI konnte das PDF nicht auslesen ({fehler}). Bitte von Hand eintragen."}

    nummer = (a.get("rechnungsnummer") or "").strip()
    werte = {
        "nummer": nummer,
        "datum": a.get("rechnungsdatum", ""),
        "leistung_von": a.get("leistung_von", ""),
        "leistung_bis": a.get("leistung_bis", ""),
        "faellig": a.get("faellig", ""),
        "betrag": a.get("gesamtbetrag", ""),
        "beschreibung": (a.get("leistung") or "").strip(),
        "bezug": (a.get("ihr_zeichen") or "").strip(),
        "kunde_name": (a.get("empfaenger") or "").strip(),
    }
    if bild:
        if werte["betrag"]:
            try:
                werte["betrag"] = euro(int(Decimal(werte["betrag"].replace(",", ".")) * 100), zeichen=False)
            except InvalidOperation:
                werte["betrag"] = ""
        hinweis = "Aus einem eingescannten Bild gelesen. Bitte jeden Wert mit dem Original vergleichen."
    else:
        # Nur übernehmen, was wirklich im Text steht.
        if len(_flach(nummer)) < 3 or _flach(nummer) not in _flach(text):
            werte["nummer"] = ""
        for feld in ("datum", "leistung_von", "leistung_bis", "faellig"):
            werte[feld] = _datum_im_text(werte[feld], text) if werte[feld] else ""
        werte["betrag"] = _betrag_im_text(werte["betrag"], text) if werte["betrag"] else ""
        hinweis = "Von der KI aus dem Text gelesen. Bitte prüfen, leere Felder ergänzen."
    werte["kunde_id"] = _kunde(werte["kunde_name"], text, kunden)
    werte["steuerfall"] = _steuerfall(text) or _steuerfall(a.get("steuerhinweis") or "")
    return {**werte, "quelle": "Bild" if bild else "Text", "hinweis": hinweis}
