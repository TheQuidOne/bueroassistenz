import hashlib
from datetime import date

import pytest

from arbeitsassistent import rechnungen, umsaetze
from arbeitsassistent.rechnungen import Fehler

TAG = date(2026, 10, 1)


def entwurf(db, einst, kunde, **extra):
    daten = {
        "kunde_id": kunde,
        "positionen": [
            {"datum": "2026-09-28", "beschreibung": "Dolmetscheinsatz DGS", "menge": "1,5", "einheit": "Std.",
             "einzelpreis": "85,00"},
            {"datum": "2026-09-28", "beschreibung": "Fahrtkosten", "menge": "48", "einheit": "km",
             "einzelpreis": "0,42"},
            {"beschreibung": "", "menge": "", "einzelpreis": ""},  # leere Formularzeile
        ],
        **extra,
    }
    return rechnungen.entwurf_speichern(db, daten, einst)


def test_summen_steuerfrei_und_ust(db, einst, kunde):
    r = rechnungen.rechnung(db, entwurf(db, einst, kunde))
    assert (r["netto"], r["ust"], r["brutto"]) == (12750 + 2016, 0, 14766)
    assert "§ 4 Nr. 16" in r["steuerhinweis"]
    r = rechnungen.rechnung(db, entwurf(db, einst, kunde, steuerfall="ust19"))
    assert (r["netto"], r["ust_satz"], r["ust"], r["brutto"]) == (14766, 19, 2806, 17572)
    assert r["steuerhinweis"] == ""


def test_steuerfall_vom_kunden(db, einst):
    k = rechnungen.kunde_speichern(db, {"name": "Beispiel GmbH", "steuerfall": "kleinunternehmer", "zahlungsziel": "30"})
    r = rechnungen.rechnung(db, entwurf(db, einst, k))
    assert r["steuerfall"] == "kleinunternehmer" and r["zahlungsziel"] == 30
    assert "§ 19" in r["steuerhinweis"]


@pytest.mark.parametrize("positionen, meldung", [
    ([], "mindestens eine Position"),
    ([{"beschreibung": "", "menge": "1", "einzelpreis": "5"}], "beschreiben"),
    ([{"beschreibung": "X", "menge": "viel", "einzelpreis": "5"}], "keine Menge"),
    ([{"beschreibung": "X", "menge": "1", "einzelpreis": "teuer"}], "kein Preis"),
])
def test_entwurf_pruefungen(db, einst, kunde, positionen, meldung):
    with pytest.raises(Fehler, match=meldung):
        rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": positionen}, einst)


def test_ausstellen_nummern_und_pdf(db, einst, kunde, ordner):
    a = entwurf(db, einst, kunde)
    b = entwurf(db, einst, kunde)
    assert rechnungen.ausstellen(db, a, einst, TAG, ordner) == "2026-001"
    assert rechnungen.ausstellen(db, b, einst, TAG, ordner) == "2026-002"
    r = rechnungen.rechnung(db, a)
    assert r["status"] == "ausgestellt" and r["faellig"] == "2026-10-15"
    assert (r["leistung_von"], r["leistung_bis"]) == ("2026-09-28", None)  # aus den Positionen
    pdf = (ordner / r["pdf_pfad"]).read_bytes()
    assert pdf.startswith(b"%PDF") and hashlib.sha256(pdf).hexdigest() == r["pdf_sha256"]
    with pytest.raises(Fehler, match="schon ausgestellt"):
        rechnungen.ausstellen(db, a, einst, TAG, ordner)
    with pytest.raises(Fehler, match="nicht mehr ändern"):
        rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
            {"beschreibung": "X", "menge": "1", "einzelpreis": "1"}]}, einst, a)
    with pytest.raises(Fehler, match="Nur Entwürfe"):
        rechnungen.entwurf_loeschen(db, a)


def test_startnummer_und_neues_jahr(db, einst, kunde, ordner):
    einst = {**einst, "startnummer": "42"}
    assert rechnungen.ausstellen(db, entwurf(db, einst, kunde), einst, TAG, ordner) == "2026-042"
    assert rechnungen.ausstellen(db, entwurf(db, einst, kunde), einst, TAG, ordner) == "2026-043"
    einst["startnummer"] = "1"
    assert rechnungen.ausstellen(db, entwurf(db, einst, kunde), einst, date(2027, 1, 2), ordner) == "2027-001"


def test_ausstellen_braucht_absender_und_leistungsdatum(db, einst, kunde, ordner):
    with pytest.raises(Fehler, match="IBAN"):
        rechnungen.ausstellen(db, entwurf(db, einst, kunde), {**einst, "iban": ""}, TAG, ordner)
    ohne_datum = rechnungen.entwurf_speichern(db, {"kunde_id": kunde, "positionen": [
        {"beschreibung": "Einsatz", "menge": "1", "einzelpreis": "85"}]}, einst)
    with pytest.raises(Fehler, match="Leistungsdatum"):
        rechnungen.ausstellen(db, ohne_datum, einst, TAG, ordner)
    assert rechnungen.rechnung(db, ohne_datum)["status"] == "entwurf"  # nichts halb gespeichert
    assert not (ordner / "rechnungen").exists() or not list((ordner / "rechnungen").rglob("*.pdf"))


def test_zahlungen_offen_und_zustand(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    assert rechnungen.offen(db, rid) == 14766
    assert rechnungen.zustand(rechnungen.rechnung(db, rid), 14766, date(2026, 10, 20))[0] == "ueberfaellig"
    rechnungen.zahlung_erfassen(db, rid, 10000, TAG)
    assert rechnungen.offen(db, rid) == 4766
    assert [o.offen for o in rechnungen.offene_rechnungen(db)] == [4766]
    rechnungen.zahlung_erfassen(db, rid, 4766, TAG)
    assert rechnungen.offene_rechnungen(db) == []
    assert rechnungen.liste(db, "bezahlt")[0]["r"]["id"] == rid


def test_storno(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    storno = rechnungen.stornieren(db, rid, einst, TAG, ordner)
    s = rechnungen.rechnung(db, storno)
    assert s["nummer"] == "2026-002" and s["art"] == "storno" and s["brutto"] == -14766
    assert rechnungen.rechnung(db, rid)["status"] == "storniert"
    assert rechnungen.offene_rechnungen(db) == []
    assert (ordner / s["pdf_pfad"]).exists()
    with pytest.raises(Fehler):
        rechnungen.stornieren(db, rid, einst, TAG, ordner)


def test_storno_nur_ohne_zahlungen(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    rechnungen.zahlung_erfassen(db, rid, 100, TAG)
    with pytest.raises(Fehler, match="Zahlungen"):
        rechnungen.stornieren(db, rid, einst, TAG, ordner)


def test_erinnerung_wird_abgelegt(db, einst, kunde, ordner):
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    name, daten = rechnungen.erinnerung(db, rid, einst, date(2026, 11, 1), ordner)
    assert daten.startswith(b"%PDF") and (ordner / "rechnungen" / "2026" / name).exists()
    rechnungen.erinnerung(db, rid, einst, date(2026, 11, 1), ordner)  # zweimal am Tag geht


def test_kategorie_regel_und_zahlung_loesen(db, einst, kunde, ordner):
    db.execute("INSERT INTO umsaetze (fingerabdruck, datum, betrag, gegenpartei) VALUES "
               "('a', '2026-10-01', -2999, 'Telefonica Beispiel'), ('b', '2026-11-01', -2999, 'Telefonica  beispiel'), "
               "('c', '2026-10-02', 14766, 'Landschaftsverband')")
    weitere = umsaetze.kategorie_setzen(db, 1, "telefon", merken=True)
    assert weitere == 1
    assert {z["kategorie"] for z in db.execute("SELECT kategorie FROM umsaetze WHERE betrag < 0")} == {"telefon"}
    rid = entwurf(db, einst, kunde)
    rechnungen.ausstellen(db, rid, einst, TAG, ordner)
    umsaetze.zuordnen(db, 3, rid)
    assert rechnungen.offen(db, rid) == 0
    with pytest.raises(Fehler, match="bezahlt eine Rechnung"):
        umsaetze.kategorie_setzen(db, 3, "privat")
    rechnungen.zahlung_loeschen(db, rechnungen.zahlungen(db, rid)[0]["id"])
    assert rechnungen.offen(db, rid) == 14766
    assert db.execute("SELECT kategorie FROM umsaetze WHERE id = 3").fetchone()[0] == ""
