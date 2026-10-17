"""Liest Kontoumsätze aus dem CSV-Export deutscher Banken.

Die Bank wird nicht beim Namen erkannt, sondern an ihren Spaltenköpfen. Vorspann mit
Kontoangaben, Fußzeilen mit Salden und vorgemerkte Umsätze werden übersprungen.
Welche Formate belegt sind, steht in tests/test_bank.py bei den Testdateien.
"""

import csv
import hashlib
import io
import re
from collections import Counter
from datetime import date

from .geld import cent_aus_text
from .modell import Umsatz


class CsvFehler(Exception):
    """Die Datei lässt sich nicht als Kontoauszug lesen. Die Meldung ist für Laien gedacht."""


HINWEIS = (
    "Bitte im Online-Banking die Umsätze als CSV-Datei exportieren (nicht als PDF oder "
    "Excel) und die Datei unverändert hier einlesen."
)

# Spaltenköpfe in vereinfachter Form (siehe _spaltenname), bevorzugte zuerst.
SPALTEN = {
    "datum": [
        "buchungstag", "buchungsdatum", "buchung", "bookingdate", "datum", "date",
        "completeddate", "datumdesabschlusses", "settlementdateutc", "transaktionsdatum",
        # nur wenn es kein Buchungsdatum gibt
        "valutadatum", "valuta", "wertstellung", "wertstellungvaluta", "wertstellungsdatum",
        "wert", "valuedate", "valutadate", "starteddate", "datumdesbeginns",
        "operationdateutc",
    ],
    "valuta": [
        "valutadatum", "valuta", "wertstellung", "wertstellungvaluta", "wertstellungsdatum",
        "wert", "valuedate", "valutadate",
    ],
    "betrag": [
        "betrag", "betrageur", "betragineur", "umsatz", "umsatzeur", "umsatzineur",
        "amount", "amounteur", "paymentamount", "totalamountinclvat",
        "sollhaben",  # 1822direkt: Betrag mit Vorzeichen in „Soll/Haben“
    ],
    "soll": ["soll", "solleur", "sollineur", "debit"],
    "haben": ["haben", "habeneur", "habenineur", "credit"],
    "gebuehr": ["fee", "gebuehr"],
    "kennzeichen": ["sollhaben", "sh", "sollhabenkennzeichen", "kennzeichen"],
    "gegenpartei": [
        "beguenstigterzahlungspflichtiger", "namezahlungsbeteiligter",
        "auftraggeberempfaenger", "beguenstigterauftraggeber", "auftraggeberbeguenstigter",
        "empfaengerzahlungspflichtiger", "empfaengerauftraggebername", "gegenkontoinhaber",
        "partnername", "payee", "senderempfaenger", "senderorrecipient", "counterpartyname",
        "gegenpartei", "name",
    ],
    # Zahler und Empfänger in getrennten Spalten: die Gegenpartei hängt vom Vorzeichen ab.
    "zahler": ["zahlungspflichtige", "zahlungspflichtiger", "auftraggeber"],
    "empfaenger": ["zahlungsempfaenger", "empfaenger", "beguenstigter"],
    "iban": [
        "kontonummeriban", "ibanzahlungsbeteiligter", "partneriban", "ibankontonummer",
        "empfaengerauftraggeberiban", "zahlungsempfaengeriban", "gegenkontoiban",
        "counterpartyiban", "iban", "gegenkonto", "kontonummer", "kontonr", "accountnumber",
    ],
    "zweck": [
        "verwendungszweck", "vorgangverwendungszweck", "paymentreference", "zweck",
        "reference", "description", "beschreibung", "buchungsdetails",
    ],
    "buchungstext": [
        "buchungstext", "umsatzart", "vorgang", "transaktionstyp", "transactiontype",
        "bookingtype", "type", "art",
    ],
    "status": ["status", "info", "state", "buchungsstatus", "umsatzstatus"],
}
_ALLE_SPALTEN = {name for namen in SPALTEN.values() for name in namen}
_WEITERER_ZWECK = re.compile(r"(?:verwendungszweck|vwz|zweck)\d+|weitereverwendungszwecke")

_UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NICHT_GEBUCHT = (
    "vorgemerkt", "vormerkung", "pending", "processing", "ausstehend", "nichtgebucht",
    "inbearbeitung", "declined", "reverted", "abgelehnt", "storniert", "failed",
)
_SALDOZEILE = {
    "anfangssaldo", "endsaldo", "tagessaldo", "alterkontostand", "neuerkontostand", "kontostand",
}
# Überschrift eines Abschnitts mit künftigen Umsätzen (ältere Postbank: „… nächsten 14 Tagen“)
_VORMERK_ABSCHNITT = ("naechsten", "14tagen", "zukuenftig", "geplant", "vorgemerkteumsaetze")
_ENGLISCHE_BETRAGSSPALTEN = {
    "amount", "amounteur", "paymentamount", "totalamountinclvat", "debit", "credit", "fee",
}

# comdirect: „Empfänger: Erika MustermannKto/IBAN: DE… BLZ/BIC: … Buchungstext: …“
_NAME_IM_TEXT = re.compile(
    r"(?:Auftraggeber|Empf(?:ä|ae)nger|Zahlungspflichtiger|Beg(?:ü|ue)nstigter)\s*:\s*"
    r"(.+?)\s*(?=Kto/IBAN:|BLZ/BIC:|Buchungstext:|\sRef\.\s|$)"
)
_IBAN_IM_TEXT = re.compile(
    r"IBAN\s*:?\s*([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?)\b"
)
# Commerzbank: „ERIKA MUSTERMANN BEISDEFFXXX DE02… RE 2026-001 End-to-End-Ref.: …“
_NAME_BIC_IBAN = re.compile(
    r"^(?P<name>[^:]+?)\s+"
    r"(?:[A-Z]{4}(?P<land>[A-Z]{2})[A-Z0-9]{2}(?:[A-Z0-9]{3})?\s+(?=(?P=land)))?"  # BIC
    r"(?P<iban>[A-Z]{2}\d{2}[A-Z0-9]{11,30})\b"
)


def _gueltige_iban(iban: str) -> bool:
    """Prüfziffer nach ISO 13616 (mod 97), damit z. B. eine Gläubiger-ID nicht als IBAN gilt."""
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    return int("".join(str(int(z, 36)) for z in iban[4:] + iban[:4])) % 97 == 1


def _spaltenname(text: str) -> str:
    """'Zahlungsempfänger*in' → 'zahlungsempfaenger', 'Betrag (€)' → 'betrag'."""
    text = (text or "").strip().lower().translate(_UMLAUTE)
    text = re.sub(r"[*:_/](?:innen|in|r|n)\b", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _einfach(text: str) -> str:
    return " ".join((text or "").split())


def _dekodieren(daten: bytes) -> str:
    if daten.startswith((b"\xff\xfe", b"\xfe\xff")):
        return daten.decode("utf-16")
    try:
        return daten.decode("utf-8-sig")
    except UnicodeDecodeError:
        return daten.decode("cp1252", errors="replace")


def _falsches_format(daten: bytes) -> str | None:
    anfang = daten.lstrip()[:200]
    if anfang.startswith(b"%PDF"):
        return "Das ist eine PDF-Datei, kein CSV-Export."
    if anfang.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
        return "Das ist eine Excel-Datei, kein CSV-Export."
    if anfang.startswith(b"<"):
        return "Das ist eine XML-Datei (z. B. CAMT), kein CSV-Export."
    if anfang.startswith(b":20:") or b"\n:61:" in daten[:5000]:
        return "Das ist eine MT940-Datei, kein CSV-Export."
    return None


def _zuordnen(kopf: list[str]) -> dict[str, int]:
    """Ordnet bekannte Spaltenköpfe ihren Feldern zu: {'datum': 0, 'betrag': 5, …}."""
    namen = [_spaltenname(z) for z in kopf]
    if len(_ALLE_SPALTEN.intersection(namen)) < 2:
        return {}
    zuordnung = {}
    for feld, kandidaten in SPALTEN.items():
        for kandidat in kandidaten:
            if kandidat in namen:
                zuordnung[feld] = namen.index(kandidat)
                break
    # Ältere Volksbank-Exporte: S/H-Kennzeichen in einer Spalte ohne Namen hinter dem Betrag.
    if "betrag" in zuordnung and "kennzeichen" not in zuordnung:
        danach = zuordnung["betrag"] + 1
        if danach < len(namen) and namen[danach] == "":
            zuordnung["kennzeichen"] = danach
    return zuordnung


def _ist_kopf(zuordnung: dict[str, int]) -> bool:
    return "datum" in zuordnung and bool({"betrag", "soll", "haben"} & zuordnung.keys())


def _kopf_suchen(text: str) -> tuple[list[list[str]], list[tuple[int, dict[str, int]]]]:
    """Findet Trennzeichen und alle Kopfzeilen. Bei mehreren Trennzeichen gewinnt die reichste."""
    beste = None
    teilweise: dict[str, int] = {}
    for trenner in (";", ",", "\t"):
        try:
            zeilen = list(csv.reader(io.StringIO(text), delimiter=trenner))
        except csv.Error:
            continue
        koepfe = []
        for nr, zeile in enumerate(zeilen):
            zuordnung = _zuordnen(zeile)
            if _ist_kopf(zuordnung):
                koepfe.append((nr, zuordnung))
            elif not koepfe and len(zuordnung) > len(teilweise):
                teilweise = zuordnung
        if koepfe and (beste is None or len(koepfe[0][1]) > len(beste[1][0][1])):
            beste = (zeilen, koepfe)
    if beste:
        return beste
    if len(teilweise) >= 2 and "datum" not in teilweise:
        raise CsvFehler(
            "In der Datei fehlt eine Spalte mit dem Buchungsdatum (z. B. „Buchungstag“). "
            + HINWEIS
        )
    if len(teilweise) >= 2:
        raise CsvFehler(
            "In der Datei fehlt eine Spalte mit dem Betrag (z. B. „Betrag“ oder „Umsatz“). "
            + HINWEIS
        )
    raise CsvFehler(
        "Die Datei sieht nicht wie ein Kontoauszug aus: Es gibt keine Zeile mit Spalten wie "
        "„Buchungstag“ und „Betrag“. " + HINWEIS
    )


def _abschnitt_waehlen(
    zeilen: list[list[str]], koepfe: list[tuple[int, dict[str, int]]]
) -> tuple[int, dict[str, int], int] | None:
    """Erster Abschnitt mit gebuchten Umsätzen: (Kopfzeile, Spalten, Ende).

    Manche Dateien haben mehrere Abschnitte, z. B. comdirect (Girokonto, Visa, Depot)
    oder die ältere Postbank (erst künftige, dann gebuchte Umsätze).
    """
    for i, (kopf, spalte) in enumerate(koepfe):
        ende = koepfe[i + 1][0] if i + 1 < len(koepfe) else len(zeilen)
        davor = [z for z in zeilen[:kopf] if any(zelle.strip() for zelle in z)]
        titel = _spaltenname("".join(davor[-1])) if davor else ""
        if not any(wort in titel for wort in _VORMERK_ABSCHNITT) or "nicht" in titel:
            return kopf, spalte, ende
    return None


def _datum(text: str, valuta: str = "") -> date | None:
    """Liest 'dd.mm.yyyy', 'dd.mm.yy', 'd.m.yyyy' und 'yyyy-mm-dd' (auch mit Uhrzeit dahinter).

    Fehlt das Jahr ('09.03', alte Sparkassen-Exporte), kommt es aus der Valuta.
    """
    text = text.strip()
    if treffer := re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)", text):
        tag, monat, jahr = (int(x) for x in treffer.groups())
        if jahr < 100:
            jahr += 2000
    elif treffer := re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", text):
        jahr, monat, tag = (int(x) for x in treffer.groups())
    elif (treffer := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?", text)) and (bezug := _datum(valuta)):
        tag, monat = (int(x) for x in treffer.groups())
        moeglich = []
        for jahr in (bezug.year - 1, bezug.year, bezug.year + 1):
            try:
                moeglich.append(date(jahr, monat, tag))
            except ValueError:
                pass
        return min(moeglich, key=lambda d: abs(d - bezug), default=None)
    else:
        return None
    try:
        return date(jahr, monat, tag)
    except ValueError:
        return None


def _dezimalzeichen(werte: list[str], englisch: bool) -> str:
    """Erkennt pro Datei, ob Komma (deutsch) oder Punkt (englisch) die Cents abtrennt.

    Eindeutig sind '12,5', '1.234,56', '-12.5', '1,234.56'. Zeigt keine Zeile das
    (nur '-1.080' bei der DKB oder '1500.000'), entscheidet die Sprache der Kopfzeile.
    """
    komma = punkt = 0
    for wert in werte:
        s = re.sub(r"[^\d.,]", "", wert)
        if "," in s and "." in s:
            komma += s.rfind(",") > s.rfind(".")
            punkt += s.rfind(".") > s.rfind(",")
        elif re.search(r",\d{1,2}$", s):
            komma += 1
        elif re.search(r"\.\d{1,2}$", s):
            punkt += 1
    if komma != punkt:
        return "," if komma > punkt else "."
    return "." if englisch else ","


def _cent(text: str, dezimal: str, zeile: int) -> int:
    if dezimal == ".":
        text = text.replace(",", "").replace(".", ",")
    try:
        return cent_aus_text(text)
    except ValueError:
        raise CsvFehler(
            f"In Zeile {zeile} steht beim Betrag „{text.strip()}“, das ist keine Zahl. "
            + HINWEIS
        ) from None


def _nicht_gebucht(status: str) -> bool:
    status = _spaltenname(status)
    return any(wort in status for wort in _NICHT_GEBUCHT)


def _saldozeile(zeile: list[str]) -> bool:
    return any(_spaltenname(zelle) in _SALDOZEILE for zelle in zeile)


class _Leser:
    """Liest die Zeilen eines Abschnitts mit fester Spaltenzuordnung."""

    def __init__(self, kopf: list[str], spalte: dict[str, int], zeilen: list[list[str]]):
        self.spalte = spalte
        namen = [_spaltenname(z) for z in kopf]
        self.weitere_zwecke = [i for i, n in enumerate(namen) if _WEITERER_ZWECK.fullmatch(n)]
        felder = [f for f in ("betrag", "soll", "haben", "gebuehr") if f in spalte]
        englisch = any(namen[spalte[f]] in _ENGLISCHE_BETRAGSSPALTEN for f in felder)
        werte = [self.wert(z, f) for z in zeilen for f in felder]
        self.dezimal = _dezimalzeichen([w for w in werte if w], englisch)

    def wert(self, zeile: list[str], feld: str) -> str:
        i = self.spalte.get(feld)
        return zeile[i].strip() if i is not None and i < len(zeile) else ""

    def betrag(self, zeile: list[str], nr: int) -> int | None:
        soll, haben = self.wert(zeile, "soll"), self.wert(zeile, "haben")
        if roh := self.wert(zeile, "betrag"):
            betrag = _cent(roh, self.dezimal, nr)
        elif soll or haben:
            betrag = (abs(_cent(haben, self.dezimal, nr)) if haben else 0) - (
                abs(_cent(soll, self.dezimal, nr)) if soll else 0
            )
        else:
            return None
        kennzeichen = self.wert(zeile, "kennzeichen").upper()
        if kennzeichen in ("S", "SOLL", "D", "DEBIT"):
            betrag = -abs(betrag)
        elif kennzeichen in ("H", "HABEN", "C", "CREDIT"):
            betrag = abs(betrag)
        if gebuehr := self.wert(zeile, "gebuehr"):
            betrag -= abs(_cent(gebuehr, self.dezimal, nr))  # Revolut: Gebühr extra
        return betrag

    def umsatz(self, zeile: list[str], nr: int) -> Umsatz | None:
        tag = _datum(self.wert(zeile, "datum"), self.wert(zeile, "valuta"))
        if tag is None or _nicht_gebucht(self.wert(zeile, "status")) or _saldozeile(zeile):
            return None
        betrag = self.betrag(zeile, nr)
        if not betrag:
            return None  # keine Zahl oder 0 (Infozeilen, z. B. bei der Commerzbank)

        zweck = " ".join(
            [self.wert(zeile, "zweck")] + [zeile[i] for i in self.weitere_zwecke if i < len(zeile)]
        )
        zweck = _einfach(zweck) or _einfach(self.wert(zeile, "buchungstext"))

        gegenpartei = self.wert(zeile, "gegenpartei")
        if not gegenpartei:
            if "zahler" in self.spalte and "empfaenger" in self.spalte:
                gegenpartei = self.wert(zeile, "zahler" if betrag > 0 else "empfaenger")
            else:
                gegenpartei = self.wert(zeile, "zahler") or self.wert(zeile, "empfaenger")
        iban = self.wert(zeile, "iban").replace(" ", "").upper()

        # Banken ohne eigene Spalten dafür (comdirect, Commerzbank): aus dem Text holen.
        if not gegenpartei and (treffer := _NAME_IM_TEXT.search(zweck)):
            gegenpartei = treffer[1]
        if not iban.strip("0") and (treffer := _IBAN_IM_TEXT.search(zweck)):
            iban = treffer[1].replace(" ", "")
        treffer = None if gegenpartei else _NAME_BIC_IBAN.match(zweck)
        if treffer and _gueltige_iban(treffer["iban"]):
            gegenpartei = treffer["name"]
            if not iban.strip("0"):
                iban = treffer["iban"]
        if not iban.strip("0"):
            iban = ""  # Sparkasse schreibt „0000000000“, wenn es kein Gegenkonto gibt

        return Umsatz(tag, betrag, _einfach(gegenpartei), iban, zweck)


def _fingerabdruecke(umsaetze: list[Umsatz]) -> None:
    """sha1 über Datum, Betrag, Gegenpartei, IBAN und Zweck, plus '#n' für die n-te gleiche Zeile.

    So erkennt der Import Umsätze wieder, die in sich überlappenden Exporten doppelt stehen.
    """
    gezaehlt = Counter()
    for u in umsaetze:
        basis = "|".join(
            [
                u.datum.isoformat(),
                str(u.betrag),
                _einfach(u.gegenpartei).casefold(),
                u.iban,
                _einfach(u.zweck).casefold(),
            ]
        )
        gezaehlt[basis] += 1
        u.fingerabdruck = hashlib.sha1(f"{basis}#{gezaehlt[basis]}".encode()).hexdigest()


def lese_csv(daten: bytes) -> list[Umsatz]:
    """Liest einen CSV-Export der Bank. Ergebnis nach Buchungsdatum aufsteigend."""
    if not daten.strip():
        raise CsvFehler("Die Datei ist leer. " + HINWEIS)
    if meldung := _falsches_format(daten):
        raise CsvFehler(f"{meldung} {HINWEIS}")

    zeilen, koepfe = _kopf_suchen(_dekodieren(daten))
    gewaehlt = _abschnitt_waehlen(zeilen, koepfe)
    if gewaehlt is None:
        return []
    kopf, spalte, ende = gewaehlt
    daten_zeilen = [
        (nr + 1, zeilen[nr])
        for nr in range(kopf + 1, ende)
        if any(zelle.strip() for zelle in zeilen[nr])
    ]
    leser = _Leser(zeilen[kopf], spalte, [z for _, z in daten_zeilen])
    umsaetze = [u for nr, z in daten_zeilen if (u := leser.umsatz(z, nr))]

    # Absteigend sortierte Exporte (z. B. ING) erst umdrehen, damit gleiche Tage
    # in der Reihenfolge der Bank bleiben.
    if len(umsaetze) > 1 and umsaetze[0].datum > umsaetze[-1].datum:
        umsaetze.reverse()
    umsaetze.sort(key=lambda u: u.datum)
    _fingerabdruecke(umsaetze)
    return umsaetze
