"""Ordnet Zahlungseingänge offenen Rechnungen zu. Ohne KI: nur Rechnungsnummer, Betrag und Name.

Reihenfolge der Durchläufe, damit eine sichere Zuordnung nicht von einer unsicheren
verdrängt wird:
1. Nummer(n) im Verwendungszweck und Betrag passen genau → sicher.
2. Nummer(n) im Verwendungszweck, Betrag weicht ab → Vorschlag zum Prüfen.
3. Keine offene Nummer, aber der Betrag passt zu offenen Rechnungen → Vorschlag zum Prüfen.
Die offenen Beträge werden dabei mitgeführt, damit keine Rechnung doppelt ausgeglichen wird.
"""

import re
import unicodedata
from datetime import date
from itertools import combinations

from .geld import euro
from .modell import OffeneRechnung, Umsatz, Vorschlag


# Wörter, die in vielen Kundennamen vorkommen und nichts über den Zahler verraten.
ALLERWELTSWOERTER = {
    "und", "der", "die", "das", "des", "dem", "den", "fuer", "von", "vom", "zur", "zum",
    "gmbh", "mbh", "ggmbh", "ev", "eg", "ag", "kg", "ohg", "gbr", "ug", "co",
    "haftungsbeschraenkt",
    "gemeinnuetzige", "gemeinnuetziger", "gesellschaft", "firma", "herr", "frau", "familie",
    "stadt", "landkreis", "kreis", "gemeinde", "bezirk", "bezirksamt", "landratsamt", "rathaus",
    "verwaltung", "stadtverwaltung", "kreisverwaltung", "landesamt", "amt",
    "kasse", "stadtkasse", "kreiskasse", "landeskasse", "landeshauptkasse", "bundeskasse",
    "amtskasse", "gemeindekasse", "zahlstelle",
    "landschaftsverband", "verband", "verein", "stiftung", "zentrum", "service",
    "integrationsamt", "inklusionsamt", "versorgungsamt", "sozialamt", "jugendamt",
    "gesundheitsamt", "jobcenter", "bundesagentur", "agentur", "arbeit",
    "deutsche", "deutscher", "deutschland", "rentenversicherung", "krankenkasse",
    "unfallkasse", "berufsgenossenschaft", "schule", "hochschule", "universitaet",
    "klinik", "klinikum", "krankenhaus", "praxis",
}

_ERSATZ = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
# SEPA-Kürzel stehen bei manchen Banken ohne Leerzeichen hintereinander („…MREF+X1CRED+…“).
_SEPA_KUERZEL = re.compile(r"(EREF|KREF|MREF|CRED|DEBT|SVWZ|ABWA|ABWE|PURP|BREF|RREF)\+")
# Nach diesen Stichwörtern steht nie unsere Rechnungsnummer, sondern eine fremde Kennung.
_FREMDE_KENNUNG = re.compile(
    r"(?i)\b(?:mref|cred|mandat\w*|gl(?:ae|ä)ubiger[- ]?id|ci|kundennummer|kundennr|"
    r"kd[.-]*nr|kassenzeichen|aktenzeichen|az)\b\.?\s*[:+.]?\s*\S+"
)
_DATUM = re.compile(
    r"(?<!\d)(?:\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})|\d{4}-\d{1,2}-\d{1,2}|\d{4}/\d{1,2}/\d{1,2})"
    r"(?!\d)"
)
_STICHWORT = (
    r"(?:re|rg|rgn|rng|rech|rechn|rechnung|rechnungen|rechnungsnr|rechnungsnummer|"
    r"nr|beleg|belegnr|inv|invoice)\.?(?:\s?-?\s?nr\.?)?\s*[:#]?\s*"
)
# Vor der Jahreszahl keine Ziffer und kein „12-“ oder „15.“ (Datum, Aktenzeichen),
# danach keine weitere Ziffer und kein „-7“ oder „.5“.
_VOR = r"(?<!\d)(?<!\d[-.])"
_NACH = r"(?!\d)(?![-.]\d)"
_MIT_TRENNER = re.compile(_VOR + r"(20\d{2})(?: ?[-–/_] ?|\.(?=\d{3}))(\d{1,6})" + _NACH)
_MIT_LEERZEICHEN = re.compile(r"(?i)\b" + _STICHWORT + r"(20\d{2}) (\d{1,6})" + _NACH)
_OHNE_TRENNER = re.compile(_VOR + r"(20\d{2})(\d{3,4})" + _NACH + r"(?!,\d)")
_STICHWORT_DAVOR = re.compile(r"(?i)\b" + _STICHWORT + r"$")


def _falten(text: str) -> str:
    """Klein, Umlaute ausgeschrieben, Akzente weg."""
    text = (text or "").lower().translate(_ERSATZ)
    text = unicodedata.normalize("NFKD", text)
    return "".join(z for z in text if not unicodedata.combining(z))


def _bereinigt(zweck: str) -> str:
    """Verwendungszweck ohne IBANs, Mandats-/Kundenkennungen und Datumsangaben."""
    text = _SEPA_KUERZEL.sub(r" \1+ ", zweck or "")
    text = re.sub(r"\s+", " ", text)
    text = _IBAN.sub(" ", text)
    text = _FREMDE_KENNUNG.sub(" ", text)
    return _DATUM.sub(" ", text)


def _als_datum(ziffern: str) -> bool:
    try:
        date(int(ziffern[:4]), int(ziffern[4:6]), int(ziffern[6:]))
    except ValueError:
        return False
    return True


def nummern_im_text(zweck: str) -> set[tuple[int, int]]:
    """Alle Rechnungsnummern der Form Jahr-Zahl im Verwendungszweck, als (Jahr, Zahl)."""
    text = _bereinigt(zweck)
    gefunden = set()
    for treffer in _MIT_TRENNER.finditer(text):
        gefunden.add((int(treffer[1]), int(treffer[2])))
    for treffer in _MIT_LEERZEICHEN.finditer(text):
        gefunden.add((int(treffer[1]), int(treffer[2])))
    for treffer in _OHNE_TRENNER.finditer(text):
        ziffern = treffer[1] + treffer[2]
        # „20260115“ ist eher ein Datum, außer es steht „RE“ o. ä. davor.
        if len(ziffern) == 8 and _als_datum(ziffern):
            if not _STICHWORT_DAVOR.search(text[: treffer.start()]):
                continue
        gefunden.add((int(treffer[1]), int(treffer[2])))
    return {(j, z) for j, z in gefunden if z > 0}


def _jahr_zahl(nummer: str) -> tuple[int, int] | None:
    treffer = re.fullmatch(r"(\d{4})-(\d+)", nummer.strip())
    return (int(treffer[1]), int(treffer[2])) if treffer else None


def _freie_nummer_im_text(nummer: str, zweck: str) -> bool:
    """Nummern in anderem Format: nur Buchstaben und Ziffern vergleichen, Trenner egal."""
    zeichen = re.sub(r"[^a-z0-9]", "", _falten(nummer))
    if len(zeichen) < 3:
        return False
    vorne = r"(?<!\d)" if zeichen[0].isdigit() else r"(?<![a-z])"
    hinten = r"(?!\d)" if zeichen[-1].isdigit() else r"(?![a-z])"
    muster = vorne + r"[\W_]*".join(map(re.escape, zeichen)) + hinten
    return re.search(muster, _falten(_bereinigt(zweck))) is not None


def nummer_genannt(nummer: str, im_text: set[tuple[int, int]], zweck: str) -> bool:
    """Steht die Rechnungsnummer im Text? `im_text` = nummern_im_text(zweck). Jedes Nummernformat."""
    jahr_zahl = _jahr_zahl(nummer)
    if jahr_zahl:
        return jahr_zahl in im_text
    return _freie_nummer_im_text(nummer, zweck)


def bedeutsame_woerter(name: str) -> set[str]:
    """Wörter eines Namens, die etwas über die Person oder Stelle sagen."""
    woerter = re.findall(r"[a-z0-9]+", _falten(name))
    return {w for w in woerter if len(w) >= 4 and w not in ALLERWELTSWOERTER}


def name_passt(zahler: str, kunde: str) -> bool:
    return bool(bedeutsame_woerter(zahler) & bedeutsame_woerter(kunde))


def _liste(nummern: list[str]) -> str:
    if len(nummern) == 1:
        return nummern[0]
    return ", ".join(nummern[:-1]) + " und " + nummern[-1]


def _tag(tag: date) -> str:
    return f"{tag:%d.%m.%Y}"


def abgleichen(
    umsaetze: list[tuple[int, Umsatz]], offene: list[OffeneRechnung]
) -> list[Vorschlag]:
    """Schlägt für noch nicht zugeordnete Zahlungseingänge passende offene Rechnungen vor.

    Höchstens ein Vorschlag pro Umsatz, Reihenfolge nach Buchungsdatum.
    """
    rechnungen = sorted(offene, key=lambda r: (r.faellig, r.nummer, r.id))
    rest = {r.id: r.offen for r in rechnungen}
    eingaenge = sorted(
        ((i, u) for i, u in umsaetze if u.betrag > 0), key=lambda e: (e[1].datum, e[0])
    )

    genannt: dict[int, list[OffeneRechnung]] = {}
    fremd: dict[int, set[tuple[int, int]]] = {}
    for i, u in eingaenge:
        im_text = nummern_im_text(u.zweck)
        genannt[i] = [r for r in rechnungen if nummer_genannt(r.nummer, im_text, u.zweck)]
        fremd[i] = im_text - {_jahr_zahl(r.nummer) for r in genannt[i]}

    vorschlaege: dict[int, Vorschlag] = {}

    def buchen(i: int, zahlungen: list[tuple[OffeneRechnung, int]], sicher: bool, grund: str):
        for r, cent in zahlungen:
            rest[r.id] -= cent
        vorschlaege[i] = Vorschlag(i, [(r.id, cent) for r, cent in zahlungen], sicher, grund)

    # 1. Nummer und Betrag passen genau.
    for i, u in eingaenge:
        treffer = [r for r in genannt[i] if rest[r.id] > 0]
        if treffer and sum(rest[r.id] for r in treffer) == u.betrag:
            nummern = [r.nummer for r in treffer]
            if len(treffer) == 1:
                grund = f"Rechnungsnummer {nummern[0]} und Betrag passen."
            else:
                grund = (
                    f"Sammelüberweisung: Rechnungsnummern {_liste(nummern)} "
                    "und Gesamtbetrag passen."
                )
            buchen(i, [(r, rest[r.id]) for r in treffer], True, grund)

    # 2. Nummer gefunden, Betrag weicht ab.
    for i, u in eingaenge:
        if i in vorschlaege:
            continue
        treffer = [r for r in genannt[i] if rest[r.id] > 0]
        if treffer:
            zahlungen, grund = _verteilen(u.betrag, treffer, rest)
            buchen(i, zahlungen, False, grund)

    # 3. Keine offene Nummer: nur Betrag (und Name).
    for i, u in eingaenge:
        if i in vorschlaege:
            continue
        kandidaten = [r for r in rechnungen if rest[r.id] == u.betrag]
        if not kandidaten:
            continue
        zahler = u.gegenpartei or u.zweck
        mit_name = [r for r in kandidaten if name_passt(zahler, r.kunde)]
        if len(kandidaten) == 1:
            r = kandidaten[0]
            if mit_name:
                grund = f"Betrag passt zu Rechnung {r.nummer}, Name passt auch ({r.kunde})."
            else:
                grund = (
                    f"Betrag passt zu Rechnung {r.nummer} ({r.kunde}), "
                    "der Name des Zahlers aber nicht. Bitte prüfen."
                )
        elif len(mit_name) == 1:
            r = mit_name[0]
            grund = (
                f"Betrag passt zu {len(kandidaten)} offenen Rechnungen. "
                f"Vorgeschlagen ist {r.nummer}, weil der Name passt ({r.kunde})."
            )
        else:
            auswahl = mit_name or kandidaten
            r = auswahl[0]
            if mit_name:
                anfang = f"Betrag und Name passen zu {len(mit_name)} offenen Rechnungen."
            else:
                anfang = (
                    f"Betrag passt zu {len(kandidaten)} offenen Rechnungen, der Name zu keiner."
                )
            grund = (
                f"{anfang} Vorgeschlagen ist die älteste: {r.nummer} "
                f"(fällig {_tag(r.faellig)}). Bitte prüfen."
            )
        if fremd[i]:
            andere = _liste([f"{j}-{z:03d}" for j, z in sorted(fremd[i])])
            grund += (
                f" Achtung: Im Verwendungszweck steht {andere}, das ist keine offene Rechnung."
            )
        buchen(i, [(r, u.betrag)], False, grund)

    return [vorschlaege[i] for i, _ in eingaenge if i in vorschlaege]


def _verteilen(
    betrag: int, treffer: list[OffeneRechnung], rest: dict[int, int]
) -> tuple[list[tuple[OffeneRechnung, int]], str]:
    """Betrag auf genannte Rechnungen verteilen, deren Summe nicht genau passt.

    Passt der Betrag genau zu einem Teil der genannten Rechnungen, werden nur diese
    vorgeschlagen. Sonst wird nach Fälligkeit verteilt, älteste zuerst (wie § 366 BGB).
    """
    offen = sum(rest[r.id] for r in treffer)
    nummern = [r.nummer for r in treffer]

    if len(treffer) == 1:
        r = treffer[0]
        if betrag < offen:
            return [(r, betrag)], (
                f"Rechnung {r.nummer} steht im Verwendungszweck, gezahlt wurden aber nur "
                f"{euro(betrag)} von {euro(offen)}. Als Teilzahlung vorgeschlagen."
            )
        if betrag > offen:
            return [(r, offen)], (
                f"Rechnung {r.nummer} steht im Verwendungszweck, gezahlt wurden aber "
                f"{euro(betrag - offen)} mehr als offen (Überzahlung). "
                f"Vorgeschlagen sind die offenen {euro(offen)}."
            )
        return [(r, offen)], (
            f"Rechnung {r.nummer} steht im Verwendungszweck. Der Betrag passt zum Rest, "
            "der nach einer anderen vorgeschlagenen Zahlung offen bleibt. Bitte prüfen."
        )

    if betrag > offen:
        return [(r, rest[r.id]) for r in treffer], (
            f"Im Verwendungszweck stehen {_liste(nummern)} (zusammen {euro(offen)} offen), "
            f"gezahlt wurden {euro(betrag - offen)} mehr (Überzahlung). "
            "Alle als bezahlt vorgeschlagen."
        )

    if len(treffer) <= 12:
        for anzahl in range(1, len(treffer)):
            for teil in combinations(treffer, anzahl):
                if sum(rest[r.id] for r in teil) == betrag:
                    passend = [r.nummer for r in teil]
                    uebrig = [n for n in nummern if n not in passend]
                    return [(r, rest[r.id]) for r in teil], (
                        f"Im Verwendungszweck stehen {_liste(nummern)}, der Betrag passt "
                        f"aber nur zu {_liste(passend)}. Offen bleibt {_liste(uebrig)}."
                    )

    zahlungen, uebrig = [], betrag
    for r in treffer:
        if uebrig <= 0:
            break
        cent = min(rest[r.id], uebrig)
        zahlungen.append((r, cent))
        uebrig -= cent
    return zahlungen, (
        f"Im Verwendungszweck stehen {_liste(nummern)} (zusammen {euro(offen)} offen), "
        f"gezahlt wurden nur {euro(betrag)}. Verteilt nach Fälligkeit, älteste zuerst."
    )
