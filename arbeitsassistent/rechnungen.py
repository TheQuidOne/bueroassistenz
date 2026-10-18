"""Kunden, Rechnungen und Zahlungen.

Ein Entwurf darf beliebig geändert und gelöscht werden. Beim Ausstellen bekommt die
Rechnung ihre fortlaufende Nummer, ihr PDF wird abgelegt und danach nie mehr verändert.
Korrigiert wird über eine Rechnungskorrektur (ersetzt die Rechnung) oder eine Stornorechnung.
"""

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from . import db as dbmod
from . import fristen, pdf, unterschrift, zugferd
from .geld import anteil, cent_aus_text, mal, menge_aus_text
from .modell import STEUERFAELLE, Absender, Fehler, OffeneRechnung, Position, RechnungsDruck


def datum_aus_text(text: str | None) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for format_ in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, format_).date()
        except ValueError:
            pass
    raise Fehler(f"„{text}“ ist kein Datum.")


def iso(tag: date | None) -> str | None:
    return tag.isoformat() if tag else None


def tag(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


# --- Kunden ---------------------------------------------------------------


def kunden(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM kunden ORDER BY name COLLATE NOCASE").fetchall()


def kunde(db: sqlite3.Connection, kunde_id: int) -> sqlite3.Row:
    zeile = db.execute("SELECT * FROM kunden WHERE id = ?", (kunde_id,)).fetchone()
    if not zeile:
        raise Fehler("Diesen Kunden gibt es nicht.")
    return zeile


def kunde_speichern(db: sqlite3.Connection, daten: dict, kunde_id: int | None = None) -> int:
    name = (daten.get("name") or "").strip()
    if not name:
        raise Fehler("Bitte einen Namen eingeben.")
    steuerfall = daten.get("steuerfall") or ""
    if steuerfall and steuerfall not in STEUERFAELLE:
        raise Fehler("Unbekannter Steuerfall.")
    ziel = (daten.get("zahlungsziel") or "").strip()
    if ziel and not ziel.isdigit():
        raise Fehler("Das Zahlungsziel muss eine Zahl von Tagen sein.")
    werte = (
        name,
        (daten.get("anschrift") or "").strip(),
        (daten.get("email") or "").strip(),
        steuerfall,
        int(ziel) if ziel else None,
        (daten.get("notiz") or "").strip(),
    )
    if kunde_id is None:
        cur = db.execute(
            "INSERT INTO kunden (name, anschrift, email, steuerfall, zahlungsziel, notiz) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            werte,
        )
        return cur.lastrowid
    kunde(db, kunde_id)
    db.execute(
        "UPDATE kunden SET name = ?, anschrift = ?, email = ?, steuerfall = ?, "
        "zahlungsziel = ?, notiz = ? WHERE id = ?",
        (*werte, kunde_id),
    )
    return kunde_id


def empfaenger_text(k: sqlite3.Row) -> str:
    return "\n".join(z for z in [k["name"], *k["anschrift"].splitlines()] if z.strip())


# --- Entwürfe -------------------------------------------------------------


def absender(einst: dict, ordner: Path | None = None) -> Absender:
    """Eigene Angaben aus den Einstellungen, dazu die Unterschrift aus dem Datenordner."""
    felder = [k for k in Absender.__dataclass_fields__ if k != "unterschrift"]
    a = Absender(**{k: einst.get(k, "") for k in felder})
    if ordner:
        a.unterschrift = unterschrift.laden(ordner)
    return a


def steuerhinweis(steuerfall: str, einst: dict) -> str:
    return einst.get(f"hinweis_{steuerfall}", "") if steuerfall != "ust19" else ""


def summen(gesamtbetraege: list[int], steuerfall: str) -> tuple[int, int, int, int]:
    """(netto, ust_satz, ust, brutto). Umsatzsteuer wird auf die Nettosumme gerechnet."""
    netto = sum(gesamtbetraege)
    satz = 19 if steuerfall == "ust19" else 0
    ust = anteil(netto, satz)
    return netto, satz, ust, netto + ust


def _positionen_lesen(roh: list[dict]) -> list[Position]:
    positionen = []
    for nr, p in enumerate(roh, 1):
        beschreibung = (p.get("beschreibung") or "").strip()
        menge_roh = (p.get("menge") or "").strip()
        preis_roh = (p.get("einzelpreis") or "").strip()
        if not (beschreibung or menge_roh or preis_roh):
            continue  # leere Zeile im Formular
        if not beschreibung:
            raise Fehler(f"Position {nr}: Bitte die Leistung beschreiben.")
        try:
            menge = menge_aus_text(menge_roh or "1")
        except ValueError:
            raise Fehler(f"Position {nr}: „{menge_roh}“ ist keine Menge.") from None
        try:
            preis = cent_aus_text(preis_roh)
        except ValueError:
            raise Fehler(f"Position {nr}: „{preis_roh}“ ist kein Preis.") from None
        positionen.append(
            Position(
                beschreibung=beschreibung,
                menge=menge,
                einheit=(p.get("einheit") or "").strip(),
                einzelpreis=preis,
                gesamt=mal(menge, preis),
                datum=datum_aus_text(p.get("datum")),
                erlaeuterung=(p.get("erlaeuterung") or "").strip(),
            )
        )
    if not positionen:
        raise Fehler("Die Rechnung braucht mindestens eine Position.")
    return positionen


def entwurf_speichern(db: sqlite3.Connection, daten: dict, einst: dict, rechnung_id: int | None = None) -> int:
    """Legt einen Entwurf an oder ändert ihn. `daten` kommt aus dem Formular."""
    try:
        k = kunde(db, int(daten.get("kunde_id") or 0))
    except ValueError:
        raise Fehler("Bitte einen Kunden auswählen.") from None
    steuerfall = daten.get("steuerfall") or k["steuerfall"] or einst["steuerfall"]
    if steuerfall not in STEUERFAELLE:
        raise Fehler("Unbekannter Steuerfall.")
    ziel_roh = str(daten.get("zahlungsziel") or k["zahlungsziel"] or einst["zahlungsziel"]).strip()
    if not ziel_roh.isdigit():
        raise Fehler("Das Zahlungsziel muss eine Zahl von Tagen sein.")
    positionen = _positionen_lesen(daten.get("positionen") or [])
    von = datum_aus_text(daten.get("leistung_von"))
    bis = datum_aus_text(daten.get("leistung_bis"))
    if bis and von and bis < von:
        raise Fehler("Das Ende des Leistungszeitraums liegt vor dem Anfang.")
    netto, satz, ust, brutto = summen([p.gesamt for p in positionen], steuerfall)
    werte = {
        "kunde_id": k["id"],
        "empfaenger": empfaenger_text(k),
        "leistung_von": iso(von),
        "leistung_bis": iso(bis),
        "zahlungsziel": int(ziel_roh),
        "steuerfall": steuerfall,
        "ust_satz": satz,
        "netto": netto,
        "ust": ust,
        "brutto": brutto,
        "steuerhinweis": steuerhinweis(steuerfall, einst),
        "bezug": (daten.get("bezug") or "").strip(),
        "einleitung": (daten.get("einleitung") or "").strip(),
        "schluss": (daten.get("schluss") or "").strip(),
        "zusatztext": (daten.get("zusatztext") or "").strip(),
    }
    with dbmod.Transaktion(db):
        if rechnung_id is None:
            werte["erstellt"] = datetime.now().isoformat(timespec="seconds")
            spalten = ", ".join(werte)
            cur = db.execute(
                f"INSERT INTO rechnungen ({spalten}) VALUES ({', '.join('?' * len(werte))})",
                tuple(werte.values()),
            )
            rechnung_id = cur.lastrowid
        else:
            if rechnung(db, rechnung_id)["status"] != "entwurf":
                raise Fehler("Eine ausgestellte Rechnung lässt sich nicht mehr ändern. Bitte stornieren.")
            setzen = ", ".join(f"{s} = ?" for s in werte)
            db.execute(f"UPDATE rechnungen SET {setzen} WHERE id = ?", (*werte.values(), rechnung_id))
            db.execute("DELETE FROM positionen WHERE rechnung_id = ?", (rechnung_id,))
        _positionen_schreiben(db, rechnung_id, positionen)
    return rechnung_id


def _positionen_schreiben(db: sqlite3.Connection, rechnung_id: int, positionen: list[Position]) -> None:
    for reihe, p in enumerate(positionen, 1):
        db.execute(
            "INSERT INTO positionen (rechnung_id, reihe, datum, beschreibung, menge, einheit, "
            "einzelpreis, gesamt, erlaeuterung) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rechnung_id, reihe, iso(p.datum), p.beschreibung, str(p.menge), p.einheit,
             p.einzelpreis, p.gesamt, p.erlaeuterung),
        )


def kunde_loeschen(db: sqlite3.Connection, kunde_id: int) -> int:
    """Löscht einen Kunden samt seiner Entwürfe. Gibt die Zahl der gelöschten Entwürfe zurück.

    Hat er ausgestellte Rechnungen, bleibt er erhalten: Sie sind Belege und brauchen ihn.
    """
    k = kunde(db, kunde_id)
    belege = [z["nummer"] for z in db.execute(
        "SELECT nummer FROM rechnungen WHERE kunde_id = ? AND status != 'entwurf' ORDER BY nummer", (kunde_id,))]
    if belege:
        liste = ", ".join(belege[:5]) + (" …" if len(belege) > 5 else "")
        raise Fehler(f"{k['name']} hat ausgestellte Rechnungen ({liste}) und bleibt deshalb erhalten.")
    with dbmod.Transaktion(db):
        entwuerfe = db.execute("DELETE FROM rechnungen WHERE kunde_id = ? AND status = 'entwurf'", (kunde_id,)).rowcount
        db.execute("UPDATE hinweise SET kunde_id = NULL WHERE kunde_id = ?", (kunde_id,))
        db.execute("DELETE FROM kunden WHERE id = ?", (kunde_id,))
    return entwuerfe


def entwurf_loeschen(db: sqlite3.Connection, rechnung_id: int) -> None:
    if rechnung(db, rechnung_id)["status"] != "entwurf":
        raise Fehler("Nur Entwürfe lassen sich löschen. Ausgestellte Rechnungen werden storniert.")
    db.execute("DELETE FROM rechnungen WHERE id = ?", (rechnung_id,))


# --- Lesen ----------------------------------------------------------------


def rechnung(db: sqlite3.Connection, rechnung_id: int) -> sqlite3.Row:
    zeile = db.execute("SELECT * FROM rechnungen WHERE id = ?", (rechnung_id,)).fetchone()
    if not zeile:
        raise Fehler("Diese Rechnung gibt es nicht.")
    return zeile


def positionen(db: sqlite3.Connection, rechnung_id: int) -> list[Position]:
    zeilen = db.execute(
        "SELECT * FROM positionen WHERE rechnung_id = ? ORDER BY reihe", (rechnung_id,)
    ).fetchall()
    return [
        Position(
            beschreibung=z["beschreibung"],
            menge=menge_aus_text(z["menge"]),
            einheit=z["einheit"],
            einzelpreis=z["einzelpreis"],
            gesamt=z["gesamt"],
            datum=tag(z["datum"]),
            erlaeuterung=z["erlaeuterung"],
        )
        for z in zeilen
    ]


def leistungszeitraum(r: sqlite3.Row, pos: list[Position]) -> tuple[date | None, date | None]:
    """Angegebener Zeitraum, sonst aus den Einsatztagen der Positionen. bis=None heißt ein Tag."""
    von, bis = tag(r["leistung_von"]), tag(r["leistung_bis"])
    if not von:
        tage = sorted(p.datum for p in pos if p.datum)
        if tage:
            von, bis = tage[0], tage[-1]
    if bis == von:
        bis = None
    return von, bis


def druckdaten(db: sqlite3.Connection, rechnung_id: int, heute: date | None = None) -> RechnungsDruck:
    r = rechnung(db, rechnung_id)
    pos = positionen(db, rechnung_id)
    heute = heute or date.today()
    datum = tag(r["datum"]) or heute
    von, bis = leistungszeitraum(r, pos)
    storno_zu = korrektur_zu = ""
    vorgaenger_datum = None
    if r["storno_zu"] or r["ersetzt"]:
        v = rechnung(db, r["storno_zu"] or r["ersetzt"])
        vorgaenger_datum = tag(v["datum"])
        if r["storno_zu"]:
            storno_zu = v["nummer"]
        else:
            korrektur_zu = v["nummer"]
    # Bei einer Korrektur zählt, was schon auf die ersetzte Rechnung gezahlt wurde.
    bereits = bezahlt(db, rechnung_id) + bezahlt(db, r["ersetzt"]) if r["ersetzt"] else 0
    return RechnungsDruck(
        nummer=r["nummer"] or "ENTWURF",
        datum=datum,
        leistung_von=von or datum,
        leistung_bis=bis,
        faellig=tag(r["faellig"]) or fristen.faellig(datum, r["zahlungsziel"], dbmod.einstellungen(db)),
        empfaenger=r["empfaenger"],
        positionen=pos,
        netto=r["netto"],
        ust_satz=r["ust_satz"],
        ust=r["ust"],
        brutto=r["brutto"],
        steuerhinweis=r["steuerhinweis"],
        art=r["art"],
        storno_zu=storno_zu,
        korrektur_zu=korrektur_zu,
        vorgaenger_datum=vorgaenger_datum,
        bereits_bezahlt=bereits,
        bezug=r["bezug"],
        einleitung=r["einleitung"],
        schluss=r["schluss"],
        zusatztext=r["zusatztext"],
    )


def vorschau_pdf(db: sqlite3.Connection, rechnung_id: int, einst: dict, ordner: Path | None = None) -> bytes:
    return pdf.rechnung_pdf(druckdaten(db, rechnung_id), absender(einst, ordner))


def _e_rechnung(db: sqlite3.Connection, rechnung_id: int, einst: dict, heute: date, ordner: Path) -> bytes:
    """Das endgültige PDF: Rechnung als PDF/A-3 mit eingebetteter ZUGFeRD-XML (EN 16931)."""
    druck = druckdaten(db, rechnung_id, heute)
    a = absender(einst, ordner)
    return zugferd.einbetten(pdf.rechnung_pdf(druck, a), druck, a, rechnung(db, rechnung_id)["steuerfall"])


# --- Ausstellen und Stornieren -------------------------------------------


# Platzhalter im Nummernformat: JJJJ = Jahr, MM = Monat, NNN = laufende Nummer
# (so viele Stellen wie N, mit führenden Nullen). Beispiel: JJJJ-MM-NNN → 2026-10-148.
_PLATZHALTER = re.compile(r"JJJJ|MM|N{2,}")
NUMMER_NEU_AB = {"jahr": "jedes Jahr", "monat": "jeden Monat", "nie": "nie, sie zählt immer weiter"}


def nummer_format_pruefen(muster: str, neu_ab: str) -> None:
    """Wirft Fehler, wenn mit diesem Format Nummern doppelt vorkommen könnten."""
    teile = _PLATZHALTER.findall(muster)
    if sum(t.startswith("N") for t in teile) != 1:
        raise Fehler("Das Format der Rechnungsnummer braucht genau eine laufende Nummer, z. B. NNN.")
    if teile.count("JJJJ") > 1 or teile.count("MM") > 1:
        raise Fehler("JJJJ und MM dürfen im Format der Rechnungsnummer nur einmal vorkommen.")
    if not re.fullmatch(r"[A-Za-z0-9._-]*", _PLATZHALTER.sub("", muster)) or len(muster) > 30:
        raise Fehler("Im Format der Rechnungsnummer sind nur Buchstaben, Ziffern und - _ . erlaubt.")
    if neu_ab not in NUMMER_NEU_AB:
        raise Fehler("Unbekannte Angabe, wann die laufende Nummer neu beginnt.")
    if neu_ab == "jahr" and "JJJJ" not in teile:
        raise Fehler("Beginnt die laufende Nummer jedes Jahr neu, muss das Jahr (JJJJ) im Format stehen. "
                     "Sonst gäbe es Nummern doppelt.")
    if neu_ab == "monat" and not {"JJJJ", "MM"} <= set(teile):
        raise Fehler("Beginnt die laufende Nummer jeden Monat neu, müssen Jahr und Monat (JJJJ, MM) im "
                     "Format stehen. Sonst gäbe es Nummern doppelt.")


def _nummer_bauen(muster: str, datum: date, n: int) -> str:
    def einsetzen(t: re.Match) -> str:
        if t[0] == "JJJJ":
            return f"{datum.year}"
        if t[0] == "MM":
            return f"{datum.month:02d}"
        return f"{n:0{len(t[0])}d}"
    return _PLATZHALTER.sub(einsetzen, muster)


def _nummer_erkenner(muster: str) -> re.Pattern:
    """Regulärer Ausdruck, der Nummern dieses Formats erkennt (Gruppen j, m, n)."""
    teile, pos = [], 0
    for t in _PLATZHALTER.finditer(muster):
        teile.append(re.escape(muster[pos:t.start()]))
        gruppe = {"JJJJ": r"(?P<j>\d{4})", "MM": r"(?P<m>\d{2})"}.get(t[0], rf"(?P<n>\d{{{len(t[0])},}})")
        teile.append(gruppe)
        pos = t.end()
    return re.compile("".join(teile) + re.escape(muster[pos:]))


def naechste_nummer(db: sqlite3.Connection, datum: date, einst: dict) -> str:
    """Nächste Rechnungsnummer im Format aus den Einstellungen (Standard JJJJ-NNN, jedes Jahr ab 1).

    Gezählt wird ab der höchsten Nummer dieses Formats im Zeitraum (Jahr, Monat oder immer).
    Die Startnummer gilt nur, solange es noch keine Rechnung in diesem Format gibt, etwa um
    nach den bisherigen Word-Rechnungen weiterzuzählen.
    """
    muster = einst.get("nummer_format") or "JJJJ-NNN"
    neu_ab = einst.get("nummer_neu_ab") or "jahr"
    erkenner = _nummer_erkenner(muster)
    vergeben = {z[0] for z in db.execute("SELECT nummer FROM rechnungen WHERE nummer IS NOT NULL")}
    im_format, hoechste = False, 0
    for nummer in vergeben:
        treffer = erkenner.fullmatch(nummer)
        if not treffer:
            continue
        im_format = True
        t = treffer.groupdict()
        if neu_ab in ("jahr", "monat") and t.get("j") and int(t["j"]) != datum.year:
            continue
        if neu_ab == "monat" and t.get("m") and int(t["m"]) != datum.month:
            continue
        hoechste = max(hoechste, int(t["n"]))
    start = einst.get("startnummer", "1").strip()
    n = hoechste + 1 if im_format else (int(start) if start.isdigit() and int(start) > 0 else 1)
    while (nummer := _nummer_bauen(muster, datum, n)) in vergeben:
        n += 1
    return nummer


def _pruefe_absender(einst: dict) -> None:
    fehlt = [text for schluessel, text in [
        ("name", "Name"), ("strasse", "Straße"), ("plz_ort", "PLZ und Ort"),
        ("steuernummer", "Steuernummer oder USt-IdNr."), ("iban", "IBAN"),
    ] if not einst.get(schluessel) and not (schluessel == "steuernummer" and einst.get("ust_id"))]
    if fehlt:
        raise Fehler("Vor der ersten Rechnung bitte in den Einstellungen ergänzen: " + ", ".join(fehlt) + ".")


def _ablegen(ordner: Path, nummer: str, jahr: int, daten: bytes, name: str | None = None,
             eindeutig: bool = False) -> tuple[str, str]:
    """Schreibt das PDF nach rechnungen/JJJJ/ und gibt (relativer Pfad, sha256) zurück.

    Nie überschreiben: Mit `eindeutig` bekommt ein schon vorhandener Name „ (2)“, „ (3)“ … angehängt.
    """
    ziel = ordner / "rechnungen" / str(jahr) / (name or f"{_dateiname(nummer)}.pdf")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    stamm, zaehler = ziel.stem, 2
    while eindeutig and ziel.exists():
        ziel = ziel.with_name(f"{stamm} ({zaehler}).pdf")
        zaehler += 1
    with open(ziel, "xb") as datei:  # niemals überschreiben
        datei.write(daten)
    return str(ziel.relative_to(ordner)), hashlib.sha256(daten).hexdigest()


def nummer_pruefen(db: sqlite3.Connection, nummer: str) -> str:
    """Eine von Hand eingetragene Rechnungsnummer: nicht leer, nicht zu lang, noch nicht vergeben."""
    nummer = " ".join((nummer or "").split())
    if not nummer:
        raise Fehler("Bitte eine Rechnungsnummer eintragen oder den Vorschlag übernehmen.")
    if len(nummer) > 40:
        raise Fehler("Die Rechnungsnummer ist zu lang (höchstens 40 Zeichen).")
    if db.execute("SELECT 1 FROM rechnungen WHERE nummer = ?", (nummer,)).fetchone():
        raise Fehler(f"Die Rechnungsnummer {nummer} ist schon vergeben. Jede Nummer gibt es nur einmal.")
    return nummer


def ausstellen(db: sqlite3.Connection, rechnung_id: int, einst: dict, heute: date | None = None,
               ordner: Path | None = None, nummer: str | None = None) -> str:
    """Vergibt die Nummer, legt das PDF ab und sperrt die Rechnung. Gibt die Nummer zurück.

    Ohne `nummer` nimmt sie den Vorschlag (naechste_nummer), sonst die eingetragene, sofern frei.
    """
    heute = heute or date.today()
    ordner = ordner or dbmod.datenordner()
    _pruefe_absender(einst)
    with dbmod.Transaktion(db):
        r = rechnung(db, rechnung_id)
        if r["status"] != "entwurf":
            raise Fehler("Diese Rechnung ist schon ausgestellt.")
        pos = positionen(db, rechnung_id)
        von, bis = leistungszeitraum(r, pos)
        if not von:
            raise Fehler("Bitte das Leistungsdatum angeben (oben oder bei den Positionen).")
        k = kunde(db, r["kunde_id"])
        nummer = nummer_pruefen(db, nummer) if nummer is not None else naechste_nummer(db, heute, einst)
        db.execute(
            "UPDATE rechnungen SET nummer = ?, status = 'ausgestellt', datum = ?, faellig = ?, "
            "leistung_von = ?, leistung_bis = ?, empfaenger = ?, steuerhinweis = ?, ausgestellt = ? "
            "WHERE id = ?",
            (nummer, iso(heute), iso(fristen.faellig(heute, r["zahlungsziel"], einst)), iso(von), iso(bis),
             empfaenger_text(k), steuerhinweis(r["steuerfall"], einst),
             datetime.now().isoformat(timespec="seconds"), rechnung_id),
        )
        daten = _e_rechnung(db, rechnung_id, einst, heute, ordner)
        name = dateiname(einst, nummer, k["name"], heute, "korrektur" if r["ersetzt"] else "rechnung")
        pfad, sha = _ablegen(ordner, nummer, heute.year, daten, f"{name}.pdf", eindeutig=True)
        db.execute("UPDATE rechnungen SET pdf_pfad = ?, pdf_sha256 = ? WHERE id = ?", (pfad, sha, rechnung_id))
    return nummer


def stornieren(db: sqlite3.Connection, rechnung_id: int, einst: dict, heute: date | None = None,
               ordner: Path | None = None) -> int:
    """Stellt eine Stornorechnung mit negativen Beträgen aus. Gibt deren ID zurück."""
    heute = heute or date.today()
    ordner = ordner or dbmod.datenordner()
    r = rechnung(db, rechnung_id)
    if r["status"] != "ausgestellt" or r["art"] != "rechnung":
        raise Fehler("Nur ausgestellte Rechnungen lassen sich stornieren.")
    if bezahlt(db, rechnung_id):
        raise Fehler("Zu dieser Rechnung sind schon Zahlungen gebucht. Bitte diese zuerst lösen.")
    pos = [
        Position(p.beschreibung, p.menge, p.einheit, -p.einzelpreis, -p.gesamt, p.datum, p.erlaeuterung)
        for p in positionen(db, rechnung_id)
    ]
    with dbmod.Transaktion(db):
        cur = db.execute(
            "INSERT INTO rechnungen (art, storno_zu, kunde_id, empfaenger, leistung_von, leistung_bis, "
            "zahlungsziel, steuerfall, ust_satz, netto, ust, brutto, steuerhinweis, bezug, erstellt) "
            "VALUES ('storno', ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rechnung_id, r["kunde_id"], r["empfaenger"], r["leistung_von"], r["leistung_bis"],
             r["steuerfall"], r["ust_satz"], -r["netto"], -r["ust"], -r["brutto"], r["steuerhinweis"],
             r["bezug"], datetime.now().isoformat(timespec="seconds")),
        )
        storno_id = cur.lastrowid
        _positionen_schreiben(db, storno_id, pos)
        nummer = naechste_nummer(db, heute, einst)
        db.execute(
            "UPDATE rechnungen SET nummer = ?, status = 'ausgestellt', datum = ?, faellig = ?, "
            "ausgestellt = ?, einleitung = ? WHERE id = ?",
            (nummer, iso(heute), iso(heute), datetime.now().isoformat(timespec="seconds"),
             f"Hiermit storniere ich meine Rechnung Nr. {r['nummer']} vom "
             f"{tag(r['datum']).strftime('%d.%m.%Y')} in voller Höhe.", storno_id),
        )
        db.execute("UPDATE rechnungen SET status = 'storniert' WHERE id = ?", (rechnung_id,))
        daten = _e_rechnung(db, storno_id, einst, heute, ordner)
        name = dateiname(einst, nummer, kunde(db, r["kunde_id"])["name"], heute, "storno")
        pfad, sha = _ablegen(ordner, nummer, heute.year, daten, f"{name}.pdf", eindeutig=True)
        db.execute("UPDATE rechnungen SET pdf_pfad = ?, pdf_sha256 = ? WHERE id = ?", (pfad, sha, storno_id))
    return storno_id


def berichtigen(db: sqlite3.Connection, rechnung_id: int, daten: dict, einst: dict, heute: date | None = None,
                ordner: Path | None = None, nummer: str | None = None) -> int:
    """Stellt eine Rechnungskorrektur aus, die die Rechnung ersetzt (§ 31 Abs. 5 UStDV).

    Die alte Rechnung und ihr PDF bleiben unverändert, sie gilt danach als „berichtigt“.
    Zahlungen auf sie gehen auf die Korrektur über, offene Rückfragen zu ihr gelten als
    erledigt. Gibt die ID der Korrektur zurück.
    """
    alt = rechnung(db, rechnung_id)
    if alt["status"] != "ausgestellt" or alt["art"] != "rechnung":
        raise Fehler("Nur ausgestellte Rechnungen lassen sich berichtigen.")
    neu_id = entwurf_speichern(db, daten, einst)
    db.execute("UPDATE rechnungen SET ersetzt = ? WHERE id = ?", (rechnung_id, neu_id))
    try:
        ausstellen(db, neu_id, einst, heute, ordner, nummer)
    except Exception:
        db.execute("DELETE FROM rechnungen WHERE id = ?", (neu_id,))  # kein halber Stand
        raise
    with dbmod.Transaktion(db):
        db.execute("UPDATE rechnungen SET status = 'berichtigt' WHERE id = ?", (rechnung_id,))
        herkunft = f"übernommen von Rechnung {alt['nummer']}"
        db.execute(
            "UPDATE zahlungen SET rechnung_id = ?, "
            "notiz = CASE WHEN COALESCE(notiz, '') = '' THEN ? ELSE notiz || ' · ' || ? END WHERE rechnung_id = ?",
            (neu_id, herkunft, herkunft, rechnung_id),
        )
        db.execute("UPDATE hinweise SET status = 'erledigt' WHERE rechnung_id = ? AND status = 'offen'",
                   (rechnung_id,))
    return neu_id


# --- Alte Rechnungen nachtragen ---------------------------------------------------


# Platzhalter für den Dateinamen der Rechnungs-PDFs (Einstellungen → „dateiname“)
DATEINAME_PLATZHALTER = ("nummer", "kunde", "datum", "jahr", "monat", "art")
_ARTEN = {"rechnung": "Rechnung", "storno": "Stornorechnung", "korrektur": "Rechnungskorrektur"}


def dateiname_pruefen(muster: str) -> None:
    """Das Format braucht {nummer} (damit jede Datei eindeutig ist) und nur bekannte Platzhalter."""
    unbekannt = [p for p in re.findall(r"\{([^}]*)\}", muster) if p not in DATEINAME_PLATZHALTER]
    if unbekannt:
        erlaubt = ", ".join("{" + p + "}" for p in DATEINAME_PLATZHALTER)
        raise Fehler(f"Unbekannter Platzhalter im Dateinamen: {{{unbekannt[0]}}}. Möglich sind {erlaubt}.")
    if "{nummer}" not in muster:
        raise Fehler("Der Dateiname braucht {nummer}, damit jede Rechnung eine eigene Datei bekommt.")


def dateiname(einst: dict, nummer: str, kunde_name: str, datum: date, art: str = "rechnung") -> str:
    """Dateiname ohne „.pdf“ nach dem Format aus den Einstellungen, sicher für jedes Dateisystem."""
    werte = {"nummer": nummer, "kunde": kunde_name, "datum": datum.isoformat(), "jahr": str(datum.year),
             "monat": f"{datum.month:02d}", "art": _ARTEN.get(art, "Rechnung")}
    text = re.sub(r"\{(\w+)\}", lambda t: werte.get(t[1], ""), einst.get("dateiname") or "{nummer}")
    text = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', "_", text)
    text = re.sub(r"_{2,}", "_", " ".join(text.split())).strip(" ._")[:120]
    return text or _dateiname(nummer)


def _flach(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def kunde_nach_name(db: sqlite3.Connection, name: str) -> int | None:
    """Kunde mit genau diesem Namen (ohne Rücksicht auf Groß-/Kleinschreibung und Satzzeichen)."""
    for k in db.execute("SELECT id, name FROM kunden"):
        if _flach(k["name"]) == _flach(name):
            return k["id"]
    return None


def _dateiname(nummer: str) -> str:
    """Rechnungsnummer als sicherer Dateiname (alte Nummern können „/“ enthalten)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nummer).strip("._") or "rechnung"


def nachtragen(db: sqlite3.Connection, daten: dict, einst: dict, ordner: Path | None = None,
               original: bytes | None = None) -> int:
    """Erfasst eine Rechnung, die vor dem Programm geschrieben wurde. Gibt ihre ID zurück.

    Sie zählt danach wie eine ausgestellte Rechnung (offene Posten, Bankabgleich, Einnahmen),
    behält aber ihre eigene Nummer. Ein PDF erzeugt das Programm für sie nicht; das Original
    kann mitgegeben werden und liegt dann neben den anderen Rechnungen. „bezahlt_am“ bucht
    die Zahlung gleich mit.
    """
    ordner = ordner or dbmod.datenordner()
    anlegen = None  # Name eines Kunden, den es noch nicht gibt; angelegt wird er erst mit der Rechnung
    if daten.get("kunde_id") == "neu":
        name = " ".join((daten.get("neuer_kunde_name") or "").split())
        if not name:
            raise Fehler("Bitte den Namen des neuen Kunden eintragen.")
        vorhanden = kunde_nach_name(db, name)
        k = kunde(db, vorhanden) if vorhanden else {"steuerfall": "", "zahlungsziel": None}
        anlegen = None if vorhanden else name
    else:
        try:
            k = kunde(db, int(daten.get("kunde_id") or 0))
        except ValueError:
            raise Fehler("Bitte einen Kunden auswählen.") from None
    nummer = (daten.get("nummer") or "").strip()
    if not nummer:
        raise Fehler("Bitte die Rechnungsnummer angeben, wie sie auf der Rechnung steht.")
    if db.execute("SELECT 1 FROM rechnungen WHERE nummer = ?", (nummer,)).fetchone():
        raise Fehler(f"Die Rechnungsnummer {nummer} gibt es schon.")
    datum = datum_aus_text(daten.get("datum"))
    if not datum:
        raise Fehler("Bitte das Rechnungsdatum angeben.")
    try:
        brutto = cent_aus_text(daten.get("betrag") or "")
    except ValueError:
        raise Fehler("Bitte den Gesamtbetrag der Rechnung angeben, z. B. 212,50.") from None
    if brutto <= 0:
        raise Fehler("Der Gesamtbetrag muss größer als 0 sein.")
    steuerfall = daten.get("steuerfall") or k["steuerfall"] or einst["steuerfall"]
    if steuerfall not in STEUERFAELLE:
        raise Fehler("Unbekannter Steuerfall.")
    if steuerfall == "ust19":
        netto = round(brutto * 100 / 119)
        satz, ust = 19, brutto - netto
    else:
        netto, satz, ust = brutto, 0, 0
    von = datum_aus_text(daten.get("leistung_von")) or datum
    bis = datum_aus_text(daten.get("leistung_bis"))
    if bis and bis < von:
        raise Fehler("Das Ende des Leistungszeitraums liegt vor dem Anfang.")
    ziel = int(k["zahlungsziel"] or einst.get("zahlungsziel") or 14)
    faellig = datum_aus_text(daten.get("faellig")) or fristen.faellig(datum, ziel, einst)
    bezahlt_am = datum_aus_text(daten.get("bezahlt_am"))
    if original is not None and not original.startswith(b"%PDF"):
        raise Fehler("Das Original muss eine PDF-Datei sein.")
    beschreibung = (daten.get("beschreibung") or "").strip() or f"Leistungen laut Rechnung {nummer}"
    jetzt = datetime.now().isoformat(timespec="seconds")
    with dbmod.Transaktion(db):
        if anlegen:
            k = kunde(db, kunde_speichern(db, {"name": anlegen, "anschrift": daten.get("neuer_kunde_anschrift", "")}))
        rechnung_id = db.execute(
            "INSERT INTO rechnungen (nummer, status, art, kunde_id, empfaenger, datum, leistung_von, leistung_bis, "
            "zahlungsziel, faellig, steuerfall, ust_satz, netto, ust, brutto, steuerhinweis, bezug, erstellt, "
            "ausgestellt, nachgetragen) VALUES (?, 'ausgestellt', 'rechnung', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, 1)",
            (nummer, k["id"], empfaenger_text(k), iso(datum), iso(von), iso(bis), ziel, iso(faellig), steuerfall,
             satz, netto, ust, brutto, steuerhinweis(steuerfall, einst), (daten.get("bezug") or "").strip(),
             jetzt, jetzt),
        ).lastrowid
        _positionen_schreiben(db, rechnung_id, [Position(beschreibung, Decimal(1), "", netto, netto, von)])
        if bezahlt_am:
            db.execute("INSERT INTO zahlungen (rechnung_id, datum, betrag, notiz) VALUES (?, ?, ?, ?)",
                       (rechnung_id, iso(bezahlt_am), brutto, "nachgetragen"))
        if original is not None:
            name = dateiname(einst, nummer, k["name"], datum)
            pfad, sha = _ablegen(ordner, nummer, datum.year, original, f"{name}_original.pdf", eindeutig=True)
            db.execute("UPDATE rechnungen SET pdf_pfad = ?, pdf_sha256 = ? WHERE id = ?", (pfad, sha, rechnung_id))
    return rechnung_id


def original_hinzufuegen(db: sqlite3.Connection, rechnung_id: int, original: bytes, ordner: Path | None = None) -> None:
    """Hängt an eine nachgetragene Rechnung das Original-PDF an (nur solange keins da ist)."""
    ordner = ordner or dbmod.datenordner()
    r = rechnung(db, rechnung_id)
    if not r["nachgetragen"] or r["pdf_pfad"]:
        raise Fehler("Zu dieser Rechnung gibt es schon ein PDF.")
    if not original.startswith(b"%PDF"):
        raise Fehler("Das Original muss eine PDF-Datei sein.")
    pfad, sha = _ablegen(ordner, r["nummer"], tag(r["datum"]).year, original, f"{_dateiname(r['nummer'])}_original.pdf")
    db.execute("UPDATE rechnungen SET pdf_pfad = ?, pdf_sha256 = ? WHERE id = ?", (pfad, sha, rechnung_id))


def nachtrag_loeschen(db: sqlite3.Connection, rechnung_id: int, ordner: Path | None = None) -> None:
    """Entfernt einen Nachtrag, etwa nach einem Tippfehler. Nur ohne Zahlungen, Storno oder Korrektur."""
    ordner = ordner or dbmod.datenordner()
    r = rechnung(db, rechnung_id)
    if not r["nachgetragen"]:
        raise Fehler("Nur nachgetragene Rechnungen lassen sich löschen.")
    if bezahlt(db, rechnung_id):
        raise Fehler("Zu dieser Rechnung sind Zahlungen gebucht. Bitte diese zuerst lösen.")
    if db.execute("SELECT 1 FROM rechnungen WHERE storno_zu = ? OR ersetzt = ?", (rechnung_id, rechnung_id)).fetchone():
        raise Fehler("Diese Rechnung wurde storniert oder berichtigt und bleibt deshalb erhalten.")
    with dbmod.Transaktion(db):
        db.execute("UPDATE hinweise SET rechnung_id = NULL WHERE rechnung_id = ?", (rechnung_id,))
        _vorschlaege_entfernen(db, rechnung_id)
        db.execute("DELETE FROM rechnungen WHERE id = ?", (rechnung_id,))
    if r["pdf_pfad"]:
        (ordner / r["pdf_pfad"]).unlink(missing_ok=True)


# --- Ausgestellte Rechnung löschen ----------------------------------------------------


def _vorschlaege_entfernen(db: sqlite3.Connection, rechnung_id: int) -> None:
    """Abgleich-Vorschläge, die auf diese Rechnung zeigen, verwerfen (der Umsatz ist danach wieder offen)."""
    for v in db.execute("SELECT id, zahlungen FROM vorschlaege").fetchall():
        if any(rid == rechnung_id for rid, _ in json.loads(v["zahlungen"])):
            db.execute("DELETE FROM vorschlaege WHERE id = ?", (v["id"],))


def loeschen_moeglich(db: sqlite3.Connection, rechnung_id: int) -> str:
    """Leer, wenn die ausgestellte Rechnung gelöscht werden darf, sonst der Grund."""
    r = rechnung(db, rechnung_id)
    if r["status"] == "entwurf":
        return "Entwürfe werden über „Entwurf löschen“ entfernt."
    if r["nachgetragen"]:
        return "Nachgetragene Rechnungen werden über „Nachtrag entfernen“ gelöscht."
    if r["status"] != "ausgestellt":
        return "Diese Rechnung wurde storniert oder berichtigt. Bitte zuerst Storno bzw. Korrektur löschen."
    if bezahlt(db, rechnung_id):
        return "Zu dieser Rechnung sind Zahlungen gebucht. Bitte diese zuerst lösen."
    if db.execute("SELECT 1 FROM rechnungen WHERE storno_zu = ? OR ersetzt = ?", (rechnung_id, rechnung_id)).fetchone():
        return "Zu dieser Rechnung gibt es ein Storno oder eine Korrektur. Bitte zuerst diese löschen."
    return ""


def rechnung_loeschen(db: sqlite3.Connection, rechnung_id: int, bestaetigung: str, ordner: Path | None = None) -> str:
    """Löscht eine ausgestellte Rechnung, die nie verschickt wurde (z. B. einen Test). Gibt ihre Nummer zurück.

    Nur ohne Zahlungen, Storno und Korrektur, bestätigt durch Eintippen der Nummer. Die Nummer
    ist danach wieder frei. Das PDF wandert nach rechnungen/geloescht, die Löschung wird in
    `loeschungen` vermerkt. War sie selbst ein Storno oder eine Korrektur, gilt die Vorgängerin
    wieder als ausgestellt. Verschickte Rechnungen werden storniert oder berichtigt, nicht gelöscht.
    """
    ordner = ordner or dbmod.datenordner()
    grund = loeschen_moeglich(db, rechnung_id)
    if grund:
        raise Fehler(grund)
    r = rechnung(db, rechnung_id)
    if " ".join((bestaetigung or "").split()) != r["nummer"]:
        raise Fehler(f"Zum Löschen bitte die Rechnungsnummer {r['nummer']} genau eintippen.")
    jetzt = datetime.now()
    neuer_pfad = None
    if r["pdf_pfad"] and (ordner / r["pdf_pfad"]).is_file():
        ziel = ordner / "rechnungen" / "geloescht" / f"{_dateiname(r['nummer'])}_geloescht_{jetzt:%Y%m%d-%H%M%S}.pdf"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        (ordner / r["pdf_pfad"]).rename(ziel)
        neuer_pfad = str(ziel.relative_to(ordner))
    vorgaenger = r["storno_zu"] or r["ersetzt"]
    with dbmod.Transaktion(db):
        db.execute(
            "INSERT INTO loeschungen (nummer, art, kunde, datum, brutto, pdf_pfad, geloescht) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r["nummer"], "korrektur" if r["ersetzt"] else r["art"], kunde(db, r["kunde_id"])["name"], r["datum"],
             r["brutto"], neuer_pfad, jetzt.isoformat(timespec="seconds")),
        )
        db.execute("UPDATE hinweise SET rechnung_id = NULL WHERE rechnung_id = ?", (rechnung_id,))
        _vorschlaege_entfernen(db, rechnung_id)
        db.execute("DELETE FROM rechnungen WHERE id = ?", (rechnung_id,))
        if vorgaenger:
            db.execute("UPDATE rechnungen SET status = 'ausgestellt' WHERE id = ?", (vorgaenger,))
    return r["nummer"]


def erinnerung(db: sqlite3.Connection, rechnung_id: int, einst: dict, heute: date | None = None,
               ordner: Path | None = None, frist_tage: int = 10) -> tuple[str, bytes]:
    """Zahlungserinnerung als PDF. Eine Kopie wird neben der Rechnung abgelegt."""
    heute = heute or date.today()
    ordner = ordner or dbmod.datenordner()
    r = rechnung(db, rechnung_id)
    rest = offen(db, rechnung_id)
    if r["status"] != "ausgestellt" or r["art"] != "rechnung" or rest <= 0:
        raise Fehler("Für diese Rechnung ist nichts mehr offen.")
    daten = pdf.erinnerung_pdf(druckdaten(db, rechnung_id), absender(einst, ordner), rest, heute,
                               fristen.faellig(heute, frist_tage, einst))
    basis = dateiname(einst, r["nummer"], kunde(db, r["kunde_id"])["name"], tag(r["datum"]) or heute)
    name = f"{basis}_Zahlungserinnerung_{heute.isoformat()}.pdf"
    try:
        _ablegen(ordner, r["nummer"], tag(r["datum"]).year, daten, name)
    except FileExistsError:
        pass  # am selben Tag schon einmal erzeugt
    return name, daten


# --- Zahlungen ------------------------------------------------------------


def bezahlt(db: sqlite3.Connection, rechnung_id: int) -> int:
    return db.execute(
        "SELECT COALESCE(SUM(betrag), 0) FROM zahlungen WHERE rechnung_id = ?", (rechnung_id,)
    ).fetchone()[0]


def offen(db: sqlite3.Connection, rechnung_id: int) -> int:
    r = rechnung(db, rechnung_id)
    if r["status"] != "ausgestellt" or r["art"] != "rechnung":
        return 0
    return r["brutto"] - bezahlt(db, rechnung_id)


def zahlung_erfassen(db: sqlite3.Connection, rechnung_id: int, betrag: int, datum: date,
                     umsatz_id: int | None = None, notiz: str = "") -> int:
    r = rechnung(db, rechnung_id)
    if r["status"] != "ausgestellt" or r["art"] != "rechnung":
        raise Fehler("Zahlungen gehen nur auf ausgestellte Rechnungen.")
    if betrag <= 0:
        raise Fehler("Der Betrag muss größer als 0 sein.")
    cur = db.execute(
        "INSERT INTO zahlungen (rechnung_id, umsatz_id, datum, betrag, notiz) VALUES (?, ?, ?, ?, ?)",
        (rechnung_id, umsatz_id, iso(datum), betrag, notiz.strip()),
    )
    return cur.lastrowid


def zahlungen(db: sqlite3.Connection, rechnung_id: int) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT z.*, u.gegenpartei, u.zweck FROM zahlungen z LEFT JOIN umsaetze u ON u.id = z.umsatz_id "
        "WHERE z.rechnung_id = ? ORDER BY z.datum, z.id",
        (rechnung_id,),
    ).fetchall()


def zahlung_loeschen(db: sqlite3.Connection, zahlung_id: int) -> None:
    """Löst eine Zuordnung. Der Bankumsatz wird wieder offen."""
    with dbmod.Transaktion(db):
        z = db.execute("SELECT * FROM zahlungen WHERE id = ?", (zahlung_id,)).fetchone()
        if not z:
            raise Fehler("Diese Zahlung gibt es nicht.")
        db.execute("DELETE FROM zahlungen WHERE id = ?", (zahlung_id,))
        if z["umsatz_id"]:
            uebrig = db.execute(
                "SELECT COUNT(*) FROM zahlungen WHERE umsatz_id = ?", (z["umsatz_id"],)
            ).fetchone()[0]
            if not uebrig:
                db.execute("UPDATE umsaetze SET kategorie = '' WHERE id = ?", (z["umsatz_id"],))


def offene_rechnungen(db: sqlite3.Connection) -> list[OffeneRechnung]:
    zeilen = db.execute(
        "SELECT r.id, r.nummer, r.brutto, r.faellig, k.name, "
        "  r.brutto - COALESCE((SELECT SUM(betrag) FROM zahlungen WHERE rechnung_id = r.id), 0) AS rest "
        "FROM rechnungen r JOIN kunden k ON k.id = r.kunde_id "
        "WHERE r.status = 'ausgestellt' AND r.art = 'rechnung' ORDER BY r.faellig, r.nummer"
    ).fetchall()
    return [
        OffeneRechnung(id=z["id"], nummer=z["nummer"], offen=z["rest"], kunde=z["name"], faellig=tag(z["faellig"]))
        for z in zeilen
        if z["rest"] > 0
    ]


def zustand(r: sqlite3.Row, rest: int, heute: date) -> tuple[str, str]:
    """(Schlüssel für die Farbe, Text) für Listen."""
    if r["status"] == "entwurf":
        return "entwurf", "Entwurf"
    if r["status"] == "storniert":
        return "storniert", "Storniert"
    if r["status"] == "berichtigt":
        return "storniert", "Berichtigt"
    if r["art"] == "storno":
        return "storno", "Storno"
    if rest <= 0:
        return "bezahlt", "Bezahlt"
    faellig = tag(r["faellig"])
    teil = " (teilweise bezahlt)" if rest < r["brutto"] else ""
    if faellig < heute:
        tage = (heute - faellig).days
        return "ueberfaellig", f"Überfällig seit {tage} {'Tag' if tage == 1 else 'Tagen'}{teil}"
    return "offen", f"Offen{teil}"


# Spalten der Rechnungsliste, nach denen sortiert werden kann, mit der Richtung beim ersten Klick.
SORTIERUNGEN = {"nummer": "auf", "kunde": "auf", "datum": "ab", "faellig": "auf", "status": "auf", "betrag": "ab"}
_STATUS_REIHE = {"ueberfaellig": 0, "offen": 1, "entwurf": 2, "bezahlt": 3, "storniert": 4}


def _natuerlich(text: str | None) -> tuple:
    """„2026-10-147“ nach Zahlen sortieren, nicht nach Zeichen („2026-9-…“ vor „2026-10-…“)."""
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t.casefold()) for t in re.split(r"(\d+)", text or "") if t)


def _schluessel(spalte: str):
    return {
        "nummer": lambda e: (e["r"]["nummer"] is None, _natuerlich(e["r"]["nummer"])),
        "kunde": lambda e: e["r"]["kunde_name"].casefold(),
        "datum": lambda e: e["r"]["datum"] or e["r"]["erstellt"][:10],
        "faellig": lambda e: (not e["r"]["faellig"], e["r"]["faellig"] or ""),
        "status": lambda e: (_STATUS_REIHE.get(e["zustand"], 9), e["r"]["faellig"] or ""),
        "betrag": lambda e: e["r"]["brutto"],
    }[spalte]


def liste(db: sqlite3.Connection, filter_: str = "alle", jahr: int | None = None,
          heute: date | None = None, sortierung: str = "", absteigend: bool = False) -> list[dict]:
    """Rechnungen mit Zustand. Ohne `sortierung`: Entwürfe zuerst, dann die neuesten."""
    heute = heute or date.today()
    sql = (
        "SELECT r.*, k.name AS kunde_name, "
        "  COALESCE((SELECT SUM(betrag) FROM zahlungen WHERE rechnung_id = r.id), 0) AS gezahlt "
        "FROM rechnungen r JOIN kunden k ON k.id = r.kunde_id"
    )
    werte: list = []
    if jahr:
        sql += " WHERE COALESCE(r.datum, substr(r.erstellt, 1, 10)) LIKE ?"
        werte.append(f"{jahr}-%")
    sql += " ORDER BY r.status = 'entwurf' DESC, r.datum DESC, r.nummer DESC"
    ergebnis = []
    for r in db.execute(sql, werte):
        rest = r["brutto"] - r["gezahlt"] if r["status"] == "ausgestellt" and r["art"] == "rechnung" else 0
        schluessel, text = zustand(r, rest, heute)
        if filter_ == "offen" and schluessel not in ("offen", "ueberfaellig"):
            continue
        if filter_ in ("ueberfaellig", "entwurf", "bezahlt") and schluessel != filter_:
            continue
        ergebnis.append({"r": r, "offen": rest, "zustand": schluessel, "zustand_text": text})
    if sortierung in SORTIERUNGEN:
        ergebnis.sort(key=_schluessel(sortierung), reverse=absteigend)
    return ergebnis


# --- Leistungsvorlagen ----------------------------------------------------


def vorlagen(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM vorlagen ORDER BY beschreibung COLLATE NOCASE").fetchall()


def vorlage_speichern(db: sqlite3.Connection, beschreibung: str, einheit: str, preis: str) -> int:
    beschreibung = (beschreibung or "").strip()
    if not beschreibung:
        raise Fehler("Bitte die Leistung beschreiben.")
    try:
        cent = cent_aus_text(preis or "0")
    except ValueError:
        raise Fehler(f"„{preis}“ ist kein Preis.") from None
    return db.execute(
        "INSERT INTO vorlagen (beschreibung, einheit, einzelpreis) VALUES (?, ?, ?)",
        (beschreibung, (einheit or "").strip(), cent),
    ).lastrowid


def vorlage_loeschen(db: sqlite3.Connection, vorlage_id: int) -> None:
    db.execute("DELETE FROM vorlagen WHERE id = ?", (vorlage_id,))
