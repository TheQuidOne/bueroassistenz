from datetime import date, timedelta
from decimal import Decimal

from arbeitsassistent import hinweise, rechnungen
from arbeitsassistent.modell import Auswertung

HEUTE = date(2026, 10, 9)


def mail(db, betreff="Einsatz", text="", adresse="sozialdienst@klinikum.example", name="Sozialdienst Klinikum",
         nr=[0]) -> int:
    nr[0] += 1
    return db.execute(
        "INSERT INTO mails (message_id, datum, absender_name, absender_adresse, betreff, text, abgerufen, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'ausgewertet')",
        (f"<m{nr[0]}@example>", "2026-10-08T09:00:00", name, adresse, betreff, text, "2026-10-08T10:00:00"),
    ).lastrowid


def auftrag(datum, **extra) -> Auswertung:
    return Auswertung(arbeit=True, art="auftrag", zusammenfassung="Einsatz im Sozialdienst bestätigt.",
                      einsatz_datum=datum, **extra)


def klinikum(db) -> int:
    return rechnungen.kunde_speichern(db, {"name": "Klinikum Musterstadt gGmbH",
                                           "email": "Sozialdienst@Klinikum.example"})


def test_einsatz_erscheint_erst_ab_seinem_tag(db):
    k = klinikum(db)
    tag = HEUTE + timedelta(days=5)
    [hid] = hinweise.aus_mail(db, mail(db), auftrag(tag, dauer_stunden=Decimal("2.5"), aktenzeichen="AZ-17"), HEUTE)
    h = hinweise.hinweis(db, hid)
    assert h["kunde_id"] == k and h["art"] == "abrechnen" and "Klinikum" in h["titel"]
    assert hinweise.offene(db, HEUTE) == []
    assert [z["id"] for z in hinweise.offene(db, tag)] == [hid]
    assert [z["id"] for z in hinweise.einsaetze(db, HEUTE, tag)] == [hid]


def test_schon_abgerechnet_gibt_keinen_hinweis(db, einst):
    k = klinikum(db)
    rechnungen.entwurf_speichern(db, {"kunde_id": k, "positionen": [
        {"datum": "2026-10-06", "beschreibung": "Einsatz", "menge": "1", "einzelpreis": "85"}]}, einst)
    assert hinweise.aus_mail(db, mail(db), auftrag(date(2026, 10, 6)), HEUTE) == []


def test_anfrage_und_bestaetigung_ergeben_einen_hinweis(db):
    klinikum(db)
    tag = date(2026, 10, 7)
    hinweise.aus_mail(db, mail(db, "Anfrage"), auftrag(tag), HEUTE)
    hinweise.aus_mail(db, mail(db, "Bestätigung"), auftrag(tag), HEUTE)
    assert len(hinweise.offene(db, HEUTE)) == 1


def test_absage_hebt_den_einsatz_auf(db):
    klinikum(db)
    tag = date(2026, 10, 7)
    hinweise.aus_mail(db, mail(db), auftrag(tag), HEUTE)
    hinweise.aus_mail(db, mail(db, "Absage"), Auswertung(arbeit=True, art="absage", einsatz_datum=tag), HEUTE)
    [h] = hinweise.offene(db, HEUTE)
    assert h["art"] == "absage" and "07.10.2026" in h["titel"] and "Ausfallhonorar" in h["text"]
    assert db.execute("SELECT status FROM hinweise WHERE art = 'abrechnen'").fetchone()[0] == "abgesagt"


def test_rueckfrage_und_zahlung_landen_bei_der_rechnung(db, einst, ordner):
    k = klinikum(db)
    rid = rechnungen.entwurf_speichern(db, {"kunde_id": k, "positionen": [
        {"datum": "2026-09-01", "beschreibung": "Einsatz", "menge": "1", "einzelpreis": "85"}]}, einst)
    nummer = rechnungen.ausstellen(db, rid, einst, date(2026, 9, 2), ordner)
    hinweise.aus_mail(db, mail(db, f"Ihre Rechnung {nummer}", "Bitte Bewilligungsnummer ergänzen."),
                      Auswertung(arbeit=True, art="rueckfrage", hinweis="Rechnung mit Bewilligungsnummer neu senden."),
                      HEUTE)
    hinweise.aus_mail(db, mail(db, "Zahlungsavis", f"Wir überweisen RE {nummer} am 15.10."),
                      Auswertung(arbeit=True, art="zahlung", zusammenfassung="Zahlung kommt am 15.10."), HEUTE)
    [rueck] = hinweise.offene(db, HEUTE)
    assert rueck["rechnung_id"] == rid and nummer in rueck["titel"]
    assert {h["art"] for h in hinweise.zur_rechnung(db, rid)} == {"rueckfrage", "zahlung"}


def test_verdaechtig_und_privat_ergeben_nichts(db):
    klinikum(db)
    assert hinweise.aus_mail(db, mail(db), Auswertung(arbeit=False, art="verdaechtig"), HEUTE) == []
    assert hinweise.aus_mail(db, mail(db), Auswertung(arbeit=True, art="sonstiges"), HEUTE) == []
    assert hinweise.aus_mail(db, mail(db), Auswertung(arbeit=True, art="auftrag"), HEUTE) == []  # ohne Datum


def test_neu_auswerten_ersetzt_offene_hinweise(db):
    klinikum(db)
    m = mail(db)
    hinweise.aus_mail(db, m, auftrag(date(2026, 10, 1)), HEUTE)
    hinweise.aus_mail(db, m, auftrag(date(2026, 10, 2)), HEUTE)
    assert [h["datum"] for h in hinweise.offene(db, HEUTE)] == ["2026-10-02"]


def test_spaetere_rechnung_erledigt_den_hinweis(db, einst):
    k = klinikum(db)
    hinweise.aus_mail(db, mail(db), auftrag(date(2026, 10, 5)), HEUTE)
    assert len(hinweise.offene(db, HEUTE)) == 1
    rechnungen.entwurf_speichern(db, {"kunde_id": k, "leistung_von": "2026-10-05", "positionen": [
        {"beschreibung": "Einsatz", "menge": "1", "einzelpreis": "85"}]}, einst)
    assert hinweise.offene(db, HEUTE) == []


def test_kunde_ueber_namen_und_unbekannter_kunde(db):
    k = rechnungen.kunde_speichern(db, {"name": "Integrationsamt Musterland"})
    [hid] = hinweise.aus_mail(db, mail(db, adresse="poststelle@lv.example", name="Poststelle"),
                              auftrag(date(2026, 10, 1), auftraggeber="Integrationsamt Musterland"), HEUTE)
    assert hinweise.hinweis(db, hid)["kunde_id"] == k
    [hid] = hinweise.aus_mail(db, mail(db, adresse="info@firma.example", name="Beispiel AG"),
                              auftrag(date(2026, 10, 2), auftraggeber="Beispiel AG"), HEUTE)
    h = hinweise.hinweis(db, hid)
    assert h["kunde_id"] is None and "Beispiel AG" in h["titel"]


def test_entwurf_aus_hinweis(db):
    klinikum(db)
    rechnungen.vorlage_speichern(db, "Dolmetscheinsatz DGS", "Std.", "85,00")
    rechnungen.vorlage_speichern(db, "Ausfallhonorar", "pauschal", "170,00")
    [einsatz] = hinweise.aus_mail(db, mail(db), auftrag(date(2026, 10, 1), dauer_stunden=Decimal("2.5"),
                                                         aktenzeichen="AZ-17"), HEUTE)
    daten = hinweise.entwurf_daten(db, einsatz)
    [p] = daten["positionen"]
    assert daten["bezug"] == "AZ-17" and daten["hinweis_id"] == einsatz
    assert (p["datum"], p["beschreibung"], p["menge"], p["einzelpreis"]) == ("2026-10-01", "Dolmetscheinsatz DGS",
                                                                              "2,5", "85,00")
    [absage] = hinweise.aus_mail(db, mail(db), Auswertung(arbeit=True, art="absage",
                                                          einsatz_datum=date(2026, 10, 3)), HEUTE)
    [p] = hinweise.entwurf_daten(db, absage)["positionen"]
    assert (p["beschreibung"], p["einheit"], p["einzelpreis"]) == ("Ausfallhonorar", "pauschal", "170,00")


def test_auswertung_als_json_hin_und_zurueck():
    a = Auswertung(arbeit=True, art="auftrag", einsatz_datum=date(2026, 10, 1), dauer_stunden=Decimal("1.5"),
                   frist=date(2026, 10, 15), aktenzeichen="AZ")
    assert hinweise.auswertung_aus_json(hinweise.auswertung_als_json(a)) == a
