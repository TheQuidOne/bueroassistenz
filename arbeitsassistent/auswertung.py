"""Übersicht, Kalender und Jahresübersicht (Grundlage für die Einnahmen-Überschuss-Rechnung)."""

import calendar
import csv
import io
import json
import sqlite3
from datetime import date, timedelta

from . import hinweise, rechnungen, umsaetze
from .geld import euro
from .modell import STEUERFAELLE
from .rechnungen import tag

# Nach so vielen Tagen ohne neuen Kontoauszug erinnert die Übersicht daran.
IMPORT_ERINNERUNG_TAGE = 7

# Symbol und Farbe der Hinweise aus Mails unter „Zu erledigen“.
MAIL_SYMBOLE = {"abrechnen": ("rechnung", "blau"), "absage": ("achtung", "gelb"),
                "rueckfrage": ("post", "gelb"), "frist": ("uhr", "rot")}


def uebersicht(db: sqlite3.Connection, heute: date | None = None) -> dict:
    heute = heute or date.today()
    offene = rechnungen.offene_rechnungen(db)
    ueberfaellig = [o for o in offene if o.faellig < heute]
    bald = [o for o in offene if o.faellig >= heute]
    monat = heute.strftime("%Y-%m")
    eingang_monat = db.execute(
        "SELECT COALESCE(SUM(betrag), 0) FROM zahlungen WHERE datum LIKE ?", (f"{monat}-%",)
    ).fetchone()[0]
    letzte_eingaenge = db.execute(
        "SELECT z.*, r.nummer, k.name AS kunde_name FROM zahlungen z "
        "JOIN rechnungen r ON r.id = z.rechnung_id JOIN kunden k ON k.id = r.kunde_id "
        "ORDER BY z.datum DESC, z.id DESC LIMIT 5"
    ).fetchall()
    letzter = umsaetze.letzter_import(db)
    import_alt = letzter is None or (
        heute - date.fromisoformat(letzter["zeitpunkt"][:10])
    ).days >= IMPORT_ERINNERUNG_TAGE
    return {
        "offen_summe": sum(o.offen for o in offene),
        "offen_anzahl": len(offene),
        "ueberfaellig": ueberfaellig,
        "ueberfaellig_summe": sum(o.offen for o in ueberfaellig),
        "bald": bald[:6],
        "eingang_monat": eingang_monat,
        "letzte_eingaenge": letzte_eingaenge,
        "vorschlaege": umsaetze.vorschlaege(db),
        "unklar": db.execute("SELECT COUNT(*) FROM umsaetze WHERE kategorie = ''").fetchone()[0],
        "entwuerfe": db.execute("SELECT COUNT(*) FROM rechnungen WHERE status = 'entwurf'").fetchone()[0],
        "letzter_import": letzter,
        "import_alt": import_alt,
    }


def kalender(db: sqlite3.Connection, jahr: int, monat: int, heute: date | None = None) -> list[list[dict]]:
    """Wochen (Mo–So) mit Einträgen: Fälligkeiten, Zahlungseingänge und Einsatztage."""
    heute = heute or date.today()
    wochen = calendar.Calendar().monthdatescalendar(jahr, monat)
    erster, letzter = wochen[0][0], wochen[-1][-1]
    eintraege: dict[date, list[dict]] = {}

    def neu(tag_: date, art: str, text: str, ziel: int, mail_id: int | None = None) -> None:
        eintraege.setdefault(tag_, []).append({"art": art, "text": text, "rechnung_id": ziel, "mail_id": mail_id})

    for r in db.execute(
        "SELECT r.id, r.nummer, r.faellig, r.brutto, k.name FROM rechnungen r JOIN kunden k ON k.id = r.kunde_id "
        "WHERE r.status = 'ausgestellt' AND r.art = 'rechnung' AND r.faellig BETWEEN ? AND ?",
        (erster.isoformat(), letzter.isoformat()),
    ):
        rest = rechnungen.offen(db, r["id"])
        if rest <= 0:
            continue
        faellig = tag(r["faellig"])
        art = "ueberfaellig" if faellig < heute else "faellig"
        neu(faellig, art, f"Fällig: {r['nummer']} · {r['name']} · {euro(rest)}", r["id"])

    for z in db.execute(
        "SELECT z.datum, z.betrag, r.id, r.nummer, k.name FROM zahlungen z "
        "JOIN rechnungen r ON r.id = z.rechnung_id JOIN kunden k ON k.id = r.kunde_id "
        "WHERE z.datum BETWEEN ? AND ?",
        (erster.isoformat(), letzter.isoformat()),
    ):
        neu(tag(z["datum"]), "eingang", f"Eingang: {z['nummer']} · {z['name']} · {euro(z['betrag'])}", z["id"])

    gesehen = set()
    for p in db.execute(
        "SELECT p.datum, r.id, r.nummer, r.status, k.name FROM positionen p "
        "JOIN rechnungen r ON r.id = p.rechnung_id JOIN kunden k ON k.id = r.kunde_id "
        "WHERE r.art = 'rechnung' AND r.status NOT IN ('storniert', 'berichtigt') AND p.datum BETWEEN ? AND ?",
        (erster.isoformat(), letzter.isoformat()),
    ):
        schluessel = (p["datum"], p["id"])
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        wo = p["nummer"] or "Entwurf"
        neu(tag(p["datum"]), "einsatz", f"Einsatz: {p['name']} ({wo})", p["id"])

    for h in hinweise.einsaetze(db, erster, letzter):
        beginn = json.loads(h["daten"] or "{}").get("beginn", "")
        neu(tag(h["datum"]), "einsatz", f"{beginn + ' ' if beginn else ''}Einsatz: {h['wer'] or 'aus Mail'} (Mail)",
            0, h["mail_id"])

    return [
        [{"tag": t, "im_monat": t.month == monat, "heute": t == heute, "eintraege": eintraege.get(t, [])}
         for t in woche]
        for woche in wochen
    ]


def jahr(db: sqlite3.Connection, jahr_: int) -> dict:
    """Einnahmen nach Zufluss (Zahlungsdatum), Ausgaben nach Kategorie. Nur eine Übersicht."""
    muster = f"{jahr_}-%"
    nach_fall = {fall: {"text": text, "brutto": 0, "ust": 0} for fall, text in STEUERFAELLE.items()}
    monate = [0] * 12
    for z in db.execute(
        "SELECT z.betrag, z.datum, r.steuerfall, r.ust, r.brutto FROM zahlungen z "
        "JOIN rechnungen r ON r.id = z.rechnung_id WHERE z.datum LIKE ?", (muster,)
    ):
        eintrag = nach_fall[z["steuerfall"]]
        eintrag["brutto"] += z["betrag"]
        if z["ust"] and z["brutto"]:
            eintrag["ust"] += round(z["betrag"] * z["ust"] / z["brutto"])
        monate[int(z["datum"][5:7]) - 1] += z["betrag"]

    summen = {
        z["kategorie"]: z["summe"]
        for z in db.execute(
            "SELECT kategorie, SUM(betrag) AS summe FROM umsaetze WHERE datum LIKE ? GROUP BY kategorie",
            (muster,),
        )
    }
    sonstige = summen.get("einnahme", 0)
    for z in db.execute("SELECT datum, betrag FROM umsaetze WHERE kategorie = 'einnahme' AND datum LIKE ?",
                        (muster,)):
        monate[int(z["datum"][5:7]) - 1] += z["betrag"]
    ausgaben = [
        {"schluessel": k, "text": text, "betrag": -summen.get(k, 0)}
        for k, text in umsaetze.AUSGABEN.items()
        if summen.get(k)
    ]
    einnahmen_rechnungen = sum(e["brutto"] for e in nach_fall.values())
    einnahmen = einnahmen_rechnungen + sonstige
    ausgaben_summe = sum(a["betrag"] for a in ausgaben)
    return {
        "jahr": jahr_,
        "nach_fall": [dict(e, schluessel=k) for k, e in nach_fall.items() if e["brutto"]],
        "einnahmen_rechnungen": einnahmen_rechnungen,
        "sonstige": sonstige,
        "einnahmen": einnahmen,
        "ust_eingenommen": sum(e["ust"] for e in nach_fall.values()),
        "ausgaben": ausgaben,
        "ausgaben_summe": ausgaben_summe,
        "ergebnis": einnahmen - ausgaben_summe,
        "monate": monate,
        "unklar": db.execute(
            "SELECT COUNT(*) FROM umsaetze WHERE kategorie = '' AND datum LIKE ?", (muster,)
        ).fetchone()[0],
        "privat": -summen.get("privat", 0),
    }


def _eingenommen(db: sqlite3.Connection, von: date, bis: date) -> int:
    """Einnahmen nach Zufluss zwischen von und bis (beide eingeschlossen), wie in jahr()."""
    zeitraum = (von.isoformat(), bis.isoformat())
    rechnungen_ = db.execute("SELECT COALESCE(SUM(betrag), 0) FROM zahlungen WHERE datum BETWEEN ? AND ?",
                             zeitraum).fetchone()[0]
    sonstige = db.execute("SELECT COALESCE(SUM(betrag), 0) FROM umsaetze WHERE kategorie = 'einnahme' "
                          "AND datum BETWEEN ? AND ?", zeitraum).fetchone()[0]
    return rechnungen_ + sonstige


def einnahmen(db: sqlite3.Connection, jahr_: int, heute: date | None = None) -> dict:
    """Wie viel im Jahr eingenommen wurde: gesamt, je Monat, je Kunde, Vergleich zum Vorjahr.

    Gezählt wird wie in jahr(): Zahlungen auf Rechnungen und als Einnahme eingeordnete
    Buchungen, am Tag des Zahlungseingangs. Im laufenden Jahr vergleicht der Vorjahreswert
    den gleichen Zeitraum (1. Januar bis heute vor einem Jahr).
    """
    heute = heute or date.today()
    j = jahr(db, jahr_)
    laufend = jahr_ == heute.year
    monate_bisher = heute.month if laufend else (12 if jahr_ < heute.year else 0)
    if laufend:
        try:
            stichtag = heute.replace(year=jahr_ - 1)
        except ValueError:  # 29. Februar
            stichtag = date(jahr_ - 1, 2, 28)
        vorjahr = _eingenommen(db, date(jahr_ - 1, 1, 1), stichtag)
    else:
        vorjahr = jahr(db, jahr_ - 1)["einnahmen"]
    gesamt = j["einnahmen"]

    kunden = [
        {"name": z["name"], "betrag": z["summe"], "rechnungen": z["anzahl"]}
        for z in db.execute(
            "SELECT k.name, SUM(z.betrag) AS summe, COUNT(DISTINCT r.id) AS anzahl FROM zahlungen z "
            "JOIN rechnungen r ON r.id = z.rechnung_id JOIN kunden k ON k.id = r.kunde_id "
            "WHERE z.datum LIKE ? GROUP BY k.id ORDER BY summe DESC, k.name", (f"{jahr_}-%",))
    ]
    if j["sonstige"]:
        kunden.append({"name": "Sonstige betriebliche Einnahmen", "betrag": j["sonstige"], "rechnungen": 0})
    for k in kunden:
        k["anteil"] = round(100 * k["betrag"] / gesamt) if gesamt > 0 else 0

    offen = rechnungen.offene_rechnungen(db)
    nicht_eingeordnet = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(betrag), 0) FROM umsaetze WHERE kategorie = '' AND betrag > 0 "
        "AND datum LIKE ?", (f"{jahr_}-%",)).fetchone()
    return {
        "jahr": jahr_,
        "laufend": laufend,
        "gesamt": gesamt,
        "aus_rechnungen": j["einnahmen_rechnungen"],
        "sonstige": j["sonstige"],
        "nach_fall": j["nach_fall"],
        "monate": j["monate"],
        "monate_bisher": monate_bisher,
        "bester_monat": max(range(12), key=lambda i: j["monate"][i]) if any(j["monate"]) else None,
        "durchschnitt": round(gesamt / monate_bisher) if monate_bisher else 0,
        "vorjahr": vorjahr,
        "vergleich": round(100 * (gesamt - vorjahr) / vorjahr) if vorjahr > 0 else None,
        "kunden": kunden,
        "offen": sum(o.offen for o in offen),
        "offen_anzahl": len(offen),
        "nicht_eingeordnet": nicht_eingeordnet[0],
        "nicht_eingeordnet_betrag": nicht_eingeordnet[1],
    }


def jahr_csv(db: sqlite3.Connection, jahr_: int) -> str:
    """Alle betrieblichen Buchungen des Jahres als CSV (für Steuerberatung oder ELSTER)."""
    muster = f"{jahr_}-%"
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";")
    schreiber.writerow(["Datum", "Art", "Kategorie", "Betrag", "Rechnung", "Steuerfall",
                        "Gegenpartei", "Verwendungszweck"])
    for z in db.execute(
        "SELECT z.datum, z.betrag, r.nummer, r.steuerfall, k.name, COALESCE(u.zweck, z.notiz) AS zweck "
        "FROM zahlungen z JOIN rechnungen r ON r.id = z.rechnung_id JOIN kunden k ON k.id = r.kunde_id "
        "LEFT JOIN umsaetze u ON u.id = z.umsatz_id WHERE z.datum LIKE ? ORDER BY z.datum", (muster,)
    ):
        schreiber.writerow([tag(z["datum"]).strftime("%d.%m.%Y"), "Einnahme", "Rechnung",
                            euro(z["betrag"], zeichen=False), z["nummer"],
                            STEUERFAELLE[z["steuerfall"]], z["name"], z["zweck"]])
    for u in db.execute(
        "SELECT * FROM umsaetze WHERE datum LIKE ? AND kategorie NOT IN ('', 'rechnung', 'privat') "
        "ORDER BY datum", (muster,)
    ):
        schreiber.writerow([tag(u["datum"]).strftime("%d.%m.%Y"),
                            "Einnahme" if u["betrag"] > 0 else "Ausgabe",
                            umsaetze.KATEGORIEN[u["kategorie"]], euro(u["betrag"], zeichen=False), "", "",
                            u["gegenpartei"], u["zweck"]])
    return puffer.getvalue()


def monat_verschieben(jahr_: int, monat: int, schritt: int) -> tuple[int, int]:
    erster = date(jahr_, monat, 1)
    ziel = (erster + timedelta(days=32 * schritt)) if schritt > 0 else (erster - timedelta(days=1))
    return ziel.year, ziel.month


def aufgaben(db: sqlite3.Connection, heute: date | None = None) -> list[dict]:
    """Was gerade zu tun ist, wichtigstes zuerst. Jede Aufgabe hat genau eine Handlung."""
    heute = heute or date.today()
    liste = []
    for e in umsaetze.vorschlaege(db):
        v = e["v"]
        nummern = ", ".join(r["nummer"] for r in e["rechnungen"])
        liste.append({"art": "vorschlag", "symbol": "eingang", "farbe": "blau",
                      "titel": f"Zahlungseingang prüfen: {euro(v['betrag'])} von {v['gegenpartei'] or 'unbekannt'}",
                      "text": v["grund"] or f"Passt vermutlich zu Rechnung {nummern}.",
                      "umsatz_id": v["umsatz_id"]})
    for h in hinweise.offene(db, heute):
        symbol, farbe = MAIL_SYMBOLE[h["art"]]
        liste.append({"art": "mail", "unterart": h["art"], "symbol": symbol, "farbe": farbe, "titel": h["titel"],
                      "text": h["text"], "hinweis_id": h["id"], "mail_id": h["mail_id"],
                      "rechnung_id": h["rechnung_id"]})
    for o in rechnungen.offene_rechnungen(db):
        if o.faellig < heute:
            tage = (heute - o.faellig).days
            liste.append({"art": "ueberfaellig", "symbol": "glocke", "farbe": "rot",
                          "titel": f"Rechnung {o.nummer} ist seit {tage} {'Tag' if tage == 1 else 'Tagen'} überfällig",
                          "text": f"{o.kunde} · noch offen {euro(o.offen)}", "rechnung_id": o.id})
    offen = db.execute(
        "SELECT COUNT(*) FROM umsaetze u WHERE kategorie = '' "
        "AND NOT EXISTS (SELECT 1 FROM vorschlaege v WHERE v.umsatz_id = u.id AND v.abgelehnt = 0)"
    ).fetchone()[0]
    if offen:
        liste.append({"art": "konto", "symbol": "konto", "farbe": "gelb",
                      "titel": f"{offen} {'Kontobewegung' if offen == 1 else 'Kontobewegungen'} einordnen",
                      "text": "Ein Klick je Buchung, damit die Auswertung vollständig ist."})
    letzter = umsaetze.letzter_import(db)
    hat_rechnungen = db.execute("SELECT 1 FROM rechnungen WHERE status != 'entwurf' LIMIT 1").fetchone()
    if hat_rechnungen and (letzter is None or (
            heute - date.fromisoformat(letzter["zeitpunkt"][:10])).days >= IMPORT_ERINNERUNG_TAGE):
        zuletzt = (f"Zuletzt am {date.fromisoformat(letzter['zeitpunkt'][:10]).strftime('%d.%m.%Y')}."
                   if letzter else "Noch nie eingelesen.")
        liste.append({"art": "import", "symbol": "hochladen", "farbe": "blau",
                      "titel": "Neuen Kontoauszug einlesen",
                      "text": f"{zuletzt} So werden Zahlungseingänge erkannt."})
    entwuerfe = db.execute("SELECT COUNT(*) FROM rechnungen WHERE status = 'entwurf'").fetchone()[0]
    if entwuerfe:
        liste.append({"art": "entwurf", "symbol": "stift", "farbe": "",
                      "titel": f"{entwuerfe} {'Rechnungsentwurf' if entwuerfe == 1 else 'Rechnungsentwürfe'} "
                               "noch nicht ausgestellt",
                      "text": "Prüfen und ausstellen, dann bekommt die Rechnung ihre Nummer."})
    return liste


def zaehler(db: sqlite3.Connection, heute: date | None = None) -> dict:
    """Kleine Zahlen für die Seitenleiste."""
    heute = heute or date.today()
    return {
        "ueberfaellig": sum(1 for o in rechnungen.offene_rechnungen(db) if o.faellig < heute),
        "konto": db.execute("SELECT COUNT(*) FROM umsaetze WHERE kategorie = ''").fetchone()[0],
        "aufgaben": len(aufgaben(db, heute)),
    }


def einrichtung(db: sqlite3.Connection, einst: dict) -> dict:
    """Die Schritte bis zum ersten echten Einsatz. Verschwindet, sobald alles erledigt ist."""
    def da(sql: str) -> bool:
        return db.execute(sql).fetchone() is not None

    angaben = all(einst.get(k) for k in ("name", "strasse", "plz_ort", "iban")) and bool(
        einst.get("steuernummer") or einst.get("ust_id"))
    schritte = [
        {"titel": "Ihre Angaben", "erledigt": angaben, "ziel": "einstellungen", "anker": "angaben"},
        {"titel": "Leistungen und Preise", "erledigt": da("SELECT 1 FROM vorlagen"), "ziel": "einstellungen",
         "anker": "vorlagen"},
        {"titel": "Ersten Kunden anlegen", "erledigt": da("SELECT 1 FROM kunden"), "ziel": "kunde_neu", "anker": ""},
        {"titel": "Erste Rechnung", "erledigt": da("SELECT 1 FROM rechnungen WHERE status != 'entwurf'"),
         "ziel": "rechnung_neu", "anker": ""},
        {"titel": "Kontoauszug einlesen", "erledigt": da("SELECT 1 FROM importe"), "ziel": "bank", "anker": ""},
    ]
    fertig = sum(s["erledigt"] for s in schritte)
    return {"schritte": schritte, "erledigt": fertig, "gesamt": len(schritte), "fertig": fertig == len(schritte)}
