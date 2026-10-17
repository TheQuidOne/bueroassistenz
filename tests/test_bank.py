from datetime import date
from pathlib import Path

import pytest

from arbeitsassistent.bank import CsvFehler, lese_csv

DATEN = Path(__file__).parent / "daten" / "bank"


def lies(name: str):
    return lese_csv((DATEN / name).read_bytes())


# Allgemeines: Kodierung, Trennzeichen, Zahlen, Daten, Fehler

EINFACH = (
    "Buchungstag;Betrag;Beguenstigter/Zahlungspflichtiger;Kontonummer/IBAN;Verwendungszweck\n"
    "02.03.2026;1.234,56;Gehörlosenverband Beispielland;DE02120300000000202051;RE 2026-001\n"
    "01.03.2026;-12,50;Bäckerei Muster;;Brötchen\n"
)


def test_utf8_und_cp1252_ergeben_dasselbe():
    aus_utf8 = lese_csv(EINFACH.encode("utf-8"))
    aus_bom = lese_csv(EINFACH.encode("utf-8-sig"))
    aus_cp1252 = lese_csv(EINFACH.encode("cp1252"))
    assert aus_utf8 == aus_bom == aus_cp1252
    assert aus_cp1252[1].gegenpartei == "Gehörlosenverband Beispielland"


def test_nach_datum_aufsteigend():
    umsaetze = lese_csv(EINFACH.encode())
    assert [u.datum for u in umsaetze] == [date(2026, 3, 1), date(2026, 3, 2)]
    assert [u.betrag for u in umsaetze] == [-1250, 123456]


@pytest.mark.parametrize("trenner", [";", ",", "\t"])
def test_trennzeichen(trenner):
    text = (
        f'"Buchungsdatum"{trenner}"Zahlungspflichtige*r"{trenner}"Betrag (€)"\n'
        f'"02.03.26"{trenner}"Erika Mustermann"{trenner}"1.500,00"\n'
    )
    [u] = lese_csv(text.encode())
    assert (u.datum, u.betrag, u.gegenpartei) == (date(2026, 3, 2), 150000, "Erika Mustermann")


@pytest.mark.parametrize(
    "datum", ["02.03.2026", "02.03.26", "2026-03-02", "2026-03-02 14:05:31", "2.3.2026"]
)
def test_datumsformate(datum):
    [u] = lese_csv(f"Datum;Betrag;Verwendungszweck\n{datum};1,00;x\n".encode())
    assert u.datum == date(2026, 3, 2)


def test_spaltennamen_gross_klein_umlaute_gendern():
    text = (
        "BUCHUNGSDATUM;ZAHLUNGSEMPFAENGER:IN;Zahlungspflichtige_r;BETRAG (EUR);VERWENDUNGSZWECK\n"
        "02.03.2026;Ich;Erika Mustermann;80,00;RE 2026-004\n"
    )
    [u] = lese_csv(text.encode())
    assert u.gegenpartei == "Erika Mustermann" and u.betrag == 8000


def test_englisches_zahlenformat_pro_datei():
    text = (
        "Date,Payee,Amount (EUR),Payment reference\n"
        "2026-03-02,Erika Mustermann,1500.000,RE 2026-001\n"
        "2026-03-03,Bäckerei Muster,-4.500,Brötchen\n"
        "2026-03-04,Max Beispiel,1500,Rest\n"
    )
    assert [u.betrag for u in lese_csv(text.encode())] == [150000, -450, 150000]


def test_deutsches_zahlenformat_tausenderpunkt():
    text = "Datum;Betrag;Verwendungszweck\n02.03.2026;1.500;a\n03.03.2026;-4,50;b\n"
    assert [u.betrag for u in lese_csv(text.encode())] == [150000, -450]


def test_englische_tausender_mit_komma():
    text = "Date,Amount,Description\n2026-03-02,\"1,234.50\",a\n2026-03-03,-0.99,b\n"
    assert [u.betrag for u in lese_csv(text.encode())] == [123450, -99]


def test_zweck_leer_dann_buchungstext():
    text = (
        "Buchungstag;Buchungstext;Verwendungszweck;Betrag\n"
        "02.03.2026;ABSCHLUSS;;-7,90\n"
        "03.03.2026;GUTSCHRIFT;RE 2026-002;100,00\n"
    )
    umsaetze = lese_csv(text.encode())
    assert [u.zweck for u in umsaetze] == ["ABSCHLUSS", "RE 2026-002"]


def test_mehrzeiliger_zweck_wird_eine_zeile():
    text = 'Datum;Betrag;Verwendungszweck\n02.03.2026;1,00;"RE 2026-001\nDolmetschen   März"\n'
    [u] = lese_csv(text.encode())
    assert u.zweck == "RE 2026-001 Dolmetschen März"


def test_leere_datei():
    with pytest.raises(CsvFehler, match="leer"):
        lese_csv(b"   \n")


def test_pdf_statt_csv():
    with pytest.raises(CsvFehler, match="PDF-Datei.*als CSV-Datei exportieren"):
        lese_csv(b"%PDF-1.7\n...")


def test_excel_statt_csv():
    with pytest.raises(CsvFehler, match="Excel-Datei"):
        lese_csv(b"PK\x03\x04\x14\x00\x06\x00")


def test_keine_kopfzeile():
    with pytest.raises(CsvFehler, match="sieht nicht wie ein Kontoauszug aus.*Online-Banking"):
        lese_csv("Liebe Grüße\nvon Erika Mustermann\n".encode())


def test_fehlende_betragsspalte():
    text = "Buchungstag;Valuta;Verwendungszweck;Beguenstigter/Zahlungspflichtiger\n02.03.2026;02.03.2026;x;y\n"
    with pytest.raises(CsvFehler, match="fehlt eine Spalte mit dem Betrag"):
        lese_csv(text.encode())


def test_fehlende_datumsspalte():
    text = "Verwendungszweck;Beguenstigter/Zahlungspflichtiger;Betrag\nx;y;1,00\n"
    with pytest.raises(CsvFehler, match="fehlt eine Spalte mit dem Buchungsdatum"):
        lese_csv(text.encode())


def test_unlesbarer_betrag_nennt_zeile():
    text = "Datum;Betrag;Verwendungszweck\n02.03.2026;1,00;a\n03.03.2026;zwölf;b\n"
    with pytest.raises(CsvFehler, match="Zeile 3.*„zwölf“"):
        lese_csv(text.encode())


def test_nur_kopfzeile_ergibt_leere_liste():
    assert lese_csv(b"Buchungstag;Betrag;Verwendungszweck\n") == []


def test_fehlermeldungen_sind_deutsch_und_ohne_technik():
    for daten in (b"", b"%PDF", "Hallo;Welt\n1;2\n".encode()):
        with pytest.raises(CsvFehler) as fehler:
            lese_csv(daten)
        meldung = str(fehler.value)
        assert "Bitte" in meldung
        assert "Traceback" not in meldung and "Error" not in meldung


# Banken (Testdateien unter tests/daten/bank, erfundene Daten im echten Format)


def kurz(umsaetze):
    return [(u.datum, u.betrag, u.gegenpartei, u.iban) for u in umsaetze]


def test_sparkasse_camt_v2():
    umsaetze = lies("sparkasse_camt_v2.csv")
    assert kurz(umsaetze) == [
        (date(2026, 2, 27), -790, "", ""),
        (date(2026, 3, 2), -450, "Bäckerei Muster", "DE02370502990000684712"),
        (date(2026, 3, 2), 123456, "Landschaftsverband Musterland", "DE02300209000106531065"),
        (date(2026, 3, 4), 23800, "Gehörlosenverband Beispielland e.V.", "DE02200505501015871393"),
    ]
    assert umsaetze[2].zweck == "Sammelzahlung RE 2026-001 RE 2026-002"
    assert all(u.betrag != -2999 for u in umsaetze)  # „Umsatz vorgemerkt“ fehlt


def test_sparkasse_mt940():
    umsaetze = lies("sparkasse_mt940.csv")
    assert kurz(umsaetze) == [
        (date(2026, 3, 3), -2999, "Mobilfunk Beispiel GmbH", "DE02100100100006820101"),
        (date(2026, 3, 4), 23800, "Gehörlosenverband Beispielland e.V.", "DE02200505501015871393"),
    ]
    assert "SVWZ+RE 2026-003" in umsaetze[1].zweck


def test_sparkasse_alt_ohne_jahr_im_buchungstag():
    umsaetze = lies("sparkasse_mt940_alt.csv")
    assert [(u.datum, u.betrag) for u in umsaetze] == [
        (date(2015, 12, 30), -6000),
        (date(2016, 1, 2), 15000),
    ]


def test_volksbank():
    umsaetze = lies("volksbank.csv")
    assert kurz(umsaetze) == [
        (date(2026, 3, 2), 178500, "Landschaftsverband Musterland", "DE02300209000106531065"),
        (date(2026, 3, 3), -2999, "Mobilfunk Beispiel GmbH", "DE02100100100006820101"),
        (date(2026, 3, 31), -1250, "", ""),
    ]
    assert umsaetze[0].zweck.startswith("Rg.-Nr. 2026/001")


def test_volksbank_alt_mit_vorspann_und_soll_haben_kennzeichen():
    umsaetze = lies("volksbank_alt.csv")
    # Gegenpartei ist „Empfänger/Zahlungspflichtiger“, nicht der eigene Name davor.
    assert kurz(umsaetze) == [
        (date(2026, 3, 1), 9520, "Erika Mustermann", "DE02200505501015871393"),
        (date(2026, 3, 12), -450, "Bäckerei Muster", "DE02370502990000684712"),
        (date(2026, 3, 20), 120210, "Stadt Musterstadt", "DE02500105170137075030"),
    ]
    assert umsaetze[0].zweck == "GUTSCHRIFT Rechnung 2026 001"
    # Anfangs- und Endsaldo sind keine Umsätze.
    assert len(umsaetze) == 3


def test_volksbank_ganz_alt_ohne_iban_saldo_verschoben():
    text = (
        '"Buchungstag";"Valuta";"Auftraggeber/Zahlungsempfänger";"Empfänger/Zahlungspflichtiger";'
        '"Konto-Nr.";"BLZ";"Vorgang/Verwendungszweck";"Währung";"Umsatz";" "\n'
        '"07.04.2015";"07.04.2015";"Max Mustermann";"Erika Mustermann";"0006820101";"10010010";'
        '"GUTSCHRIFT\nRE 2015-007";"EUR";"95,20";"H"\n'
        '"07.04.2015";;;;;;"EUR";"Anfangssaldo";"1.337,00";"H"\n'
    )
    [u] = lese_csv(text.encode("cp1252"))
    assert (u.betrag, u.gegenpartei, u.iban) == (9520, "Erika Mustermann", "0006820101")


def test_ing_mit_vorspann_absteigend():
    umsaetze = lies("ing.csv")
    assert kurz(umsaetze) == [
        (date(2026, 3, 1), -2999, "Mobilfunk Beispiel GmbH", ""),
        (date(2026, 3, 12), 9520, "Erika Mustermann", ""),
        (date(2026, 3, 12), -450, "VISA BAECKEREI MUSTER", ""),
        (date(2026, 3, 20), 107100, "Jobcenter Musterstadt", ""),
    ]
    assert umsaetze[3].zweck == "RE2026-006 Dolmetschen Beratungsgespräch"


def test_ing_aeltere_kopfzeile_mit_valuta_und_kategorie():
    text = (
        "Buchung;Valuta;Auftraggeber/Empfänger;Buchungstext;Kategorie;Verwendungszweck;"
        "Saldo;Währung;Betrag;Währung\n"
        "02.03.2026;02.03.2026;Erika Mustermann;Gutschrift;Einnahmen;RE 2026-001;"
        "1.095,20;EUR;95,20;EUR\n"
    )
    [u] = lese_csv(text.encode("cp1252"))
    assert (u.datum, u.betrag, u.zweck) == (date(2026, 3, 2), 9520, "RE 2026-001")


def test_dkb():
    umsaetze = lies("dkb.csv")
    assert kurz(umsaetze) == [
        (date(2026, 3, 12), 9520, "Erika Mustermann", "DE02200505501015871393"),
        (date(2026, 3, 12), -2999, "Mobilfunk Beispiel GmbH", "DE02100100100006820101"),
        # DKB lässt Nullen weg: „1.080“ heißt 1.080,00 €.
        (date(2026, 3, 20), 108000, "Landschaftsverband Musterland", "DE02300209000106531065"),
    ]


def test_dkb_vorgemerkt_wird_uebersprungen():
    assert all(u.betrag != -380 for u in lies("dkb.csv"))


def test_dkb_2024_mit_komma_getrennt():
    assert kurz(lies("dkb_2024_komma.csv")) == [
        (date(2024, 9, 25), -870, "Bäckerei Muster", "DE02370502990000684712"),
        (date(2024, 9, 27), 178500, "Stadt Musterstadt", "DE02500105170137075030"),
    ]


def test_dkb_nur_tausender_ohne_komma_bleibt_deutsch():
    text = (
        '"Buchungsdatum";"Status";"Zahlungspflichtige*r";"Zahlungsempfänger*in";"Betrag (€)"\n'
        '"02.03.26";"Gebucht";"Stadt Musterstadt";"Max Mustermann";"1.080"\n'
        '"03.03.26";"Gebucht";"Max Mustermann";"Bäckerei Muster";"-80"\n'
    )
    assert [u.betrag for u in lese_csv(text.encode("utf-8-sig"))] == [108000, -8000]


def test_dkb_alt():
    umsaetze = lies("dkb_alt.csv")
    assert kurz(umsaetze) == [
        (date(2023, 3, 15), -2999, "Mobilfunk Beispiel GmbH", "DE02100100100006820101"),
        (date(2023, 3, 20), 142800, "Gehörlosenverband Beispielland e.V.", "DE02200505501015871393"),
    ]  # „Tagessaldo“ ist kein Umsatz


def test_comdirect_nur_girokonto_ohne_offene():
    umsaetze = lies("comdirect.csv")
    assert kurz(umsaetze) == [
        (date(2026, 3, 2), -12000, "Berufsverband Beispiel e.V.", "DE02500105170137075030"),
        (date(2026, 3, 12), -2999, "Mobilfunk Beispiel GmbH", ""),
        (date(2026, 3, 20), 108000, "Landschaftsverband Musterland", "DE02300209000106531065"),
    ]  # „offen“ ist vorgemerkt, der Abschnitt Visa-Karte zählt nicht
    assert "Buchungstext: RE 2026-001 RE 2026-002" in umsaetze[2].zweck


def test_postbank():
    umsaetze = lies("postbank.csv")
    assert kurz(umsaetze) == [
        (date(2026, 3, 2), -10080, "BAECKEREI MUSTER", "DE02370502990000684712"),
        (date(2026, 3, 12), -2999, "Mobilfunk Beispiel GmbH", "DE02200505501015871393"),
        (date(2026, 3, 20), 75000, "Erika Mustermann", "DE02200505501015871393"),
    ]


def test_postbank_alt_ueberspringt_kuenftige_umsaetze():
    umsaetze = lies("postbank_alt.csv")
    assert kurz(umsaetze) == [
        (date(2026, 3, 12), -2999, "Mobilfunk Beispiel GmbH", ""),
        (date(2026, 3, 20), 107100, "Jobcenter Musterstadt", ""),
    ]
    assert umsaetze[1].zweck == "RE 2026-006 Dolmetschen Beratungsgespräch"


def test_deutsche_bank_alt_mit_soll_und_haben():
    assert kurz(lies("deutsche_bank_alt.csv")) == [
        (date(2026, 3, 12), -2999, "Mobilfunk Beispiel GmbH", "DE02100100100006820101"),
        (date(2026, 3, 20), 120210, "Stadt Musterstadt", "DE02500105170137075030"),
    ]


def test_commerzbank_name_und_iban_aus_dem_buchungstext():
    umsaetze = lies("commerzbank.csv")
    assert kurz(umsaetze) == [
        (date(2026, 3, 5), 9520, "ERIKA BEISPIEL", "DE02200505501015871393"),
        (date(2026, 3, 12), -2999, "", ""),  # Gläubiger-ID ist keine IBAN
        (date(2026, 3, 20), 108000, "LANDSCHAFTSVERBAND MUSTERLAND", "DE02300209000106531065"),
    ]  # Infozeile mit Betrag 0 fehlt
    assert "RE 2026-001 RE 2026-002" in umsaetze[2].zweck


def test_n26():
    assert kurz(lies("n26.csv")) == [
        (date(2026, 3, 2), 9520, "Erika Mustermann", "DE02200505501015871393"),
        (date(2026, 3, 3), -450, "BAECKEREI MUSTER", ""),
        (date(2026, 3, 5), 108000, "Landschaftsverband Musterland", "DE02300209000106531065"),
        (date(2026, 3, 7), -1777, "CAFE BEISPIEL", ""),
    ]


def test_n26_alt_deutsch():
    assert kurz(lies("n26_alt.csv")) == [
        (date(2023, 3, 2), 9520, "Erika Mustermann", "DE02200505501015871393"),
        (date(2023, 3, 3), -450, "Bäckerei Muster", ""),
        (date(2023, 3, 5), 120210, "Stadt Musterstadt", "DE02500105170137075030"),
    ]


def test_revolut_mit_gebuehr_ohne_ausstehende_und_zurueckgebuchte():
    umsaetze = lies("revolut.csv")
    assert [(u.datum, u.betrag, u.zweck) for u in umsaetze] == [
        (date(2026, 3, 2), 9520, "Payment from Erika Mustermann"),
        (date(2026, 3, 4), 10000, "Top-Up by *1234"),
        (date(2026, 3, 5), -5050, "Exchanged to SEK"),
    ]


def test_revolut_deutsch():
    text = (
        "Art,Produkt,Datum des Beginns,Datum des Abschlusses,Beschreibung,Betrag,Gebühr,"
        "Währung,Status,Kontostand\n"
        "Überweisung,Giro,2026-03-02 09:14:31,2026-03-02 09:14:35,Zahlung von Erika Mustermann,"
        "95.20,0.00,EUR,ABGESCHLOSSEN,95.20\n"
    )
    [u] = lese_csv(text.encode())
    assert (u.datum, u.betrag) == (date(2026, 3, 2), 9520)


def test_1822direkt_betrag_in_soll_haben_und_verteilter_zweck():
    umsaetze = lies("1822direkt.csv")
    assert kurz(umsaetze) == [
        (date(2026, 3, 12), -2999, "Mobilfunk Beispiel GmbH", "DE02100100100006820101"),
        (date(2026, 3, 20), 120210, "Stadt Musterstadt", "DE02500105170137075030"),
    ]
    assert umsaetze[1].zweck == "RE 2026-005 Dolmetschen Elterngespräch"


def test_volksbank_variante_mit_soll_haben_spalte():
    text = (
        "Buchungstag;Valuta;Textschlüssel;Primanota;Zahlungsempfänger;ZahlungsempfängerKto;"
        "ZahlungsempfängerIBAN;ZahlungsempfängerBLZ;ZahlungsempfängerBIC;Vorgang/Verwendungszweck;"
        "Kundenreferenz;Währung;Umsatz;Soll/Haben\n"
        "02.03.2026;02.03.2026;166;1234;Erika Mustermann;;DE02200505501015871393;;TESTDEFFXXX;"
        "RE 2026-004;;EUR;95,20;H\n"
        "03.03.2026;03.03.2026;105;1235;Mobilfunk Beispiel GmbH;;DE02100100100006820101;;;"
        "Rechnung;;EUR;29,99;S\n"
    )
    assert kurz(lese_csv(text.encode("cp1252"))) == [
        (date(2026, 3, 2), 9520, "Erika Mustermann", "DE02200505501015871393"),
        (date(2026, 3, 3), -2999, "Mobilfunk Beispiel GmbH", "DE02100100100006820101"),
    ]


def test_tomorrow():
    text = (
        "account_type,booking_date,valuta_date,sender_or_recipient,iban,booking_type,"
        "description,category,amount,currency\n"
        'Girokonto,2026-03-02,2026-03-02,Erika Mustermann,DE02200505501015871393,Gutschrift,'
        'RE 2026-004,Einnahmen,"95,20",EUR\n'
    )
    [u] = lese_csv(text.encode())
    assert (u.betrag, u.gegenpartei, u.zweck) == (9520, "Erika Mustermann", "RE 2026-004")


def test_vivid():
    text = (
        "Completed date,Counterparty name,Reference,Payment amount,Payment currency\n"
        "02.03.2026,Erika Mustermann,RE 2026-004,95.20,EUR\n"
        "03.03.2026,Musterbahn AG,Ticket,-650.00,EUR\n"
    )
    assert [u.betrag for u in lese_csv(text.encode())] == [9520, -65000]


def test_hibiscus_export_geschaetzt():
    text = (
        "Kontonummer;BLZ;Konto;Gegenkonto;Gegenkonto BLZ;Gegenkonto Inhaber;Betrag;Valuta;Datum;"
        "Verwendungszweck;Verwendungszweck 2;Saldo;Weitere Verwendungszwecke\n"
        "0000202051;12030000;Girokonto;DE02200505501015871393;TESTDEFFXXX;Erika Mustermann;"
        "95,20;02.03.2026;02.03.2026;RE 2026-004;Dolmetschen;1.095,20;Arzttermin\n"
    )
    [u] = lese_csv(text.encode("cp1252"))
    assert kurz([u]) == [(date(2026, 3, 2), 9520, "Erika Mustermann", "DE02200505501015871393")]
    assert u.zweck == "RE 2026-004 Dolmetschen Arzttermin"


def test_status_in_bearbeitung_wird_uebersprungen():
    text = (
        "Status,Settlement date (UTC),Counterparty name,Reference,Total amount (incl. VAT)\n"
        "settled,2026-03-02 10:00:00,Erika Mustermann,RE 2026-004,95.20\n"
        "processing,,Musterbahn AG,Ticket,-46.90\n"
    )
    assert [u.betrag for u in lese_csv(text.encode())] == [9520]


# Fingerabdruck


def test_fingerabdruck_stabil_bei_ueberlappenden_exporten():
    kopf = (
        "Buchung;Wertstellungsdatum;Auftraggeber/Empfänger;Buchungstext;Verwendungszweck;"
        "Saldo;Währung;Betrag;Währung\n"
    )
    zeilen = [
        "20.03.2026;20.03.2026;Jobcenter Musterstadt;Gutschrift;RE2026-006;3.030,20;EUR;1.071,00;EUR\n",
        "12.03.2026;12.03.2026;Café Muster;Lastschrift;Kaffee;1.959,20;EUR;-3,20;EUR\n",
        "12.03.2026;12.03.2026;Café Muster;Lastschrift;Kaffee;1.962,40;EUR;-3,20;EUR\n",
        "12.03.2026;12.03.2026;Erika Mustermann;Gutschrift;RE 2026-004;1.965,60;EUR;95,20;EUR\n",
        "01.03.2026;01.03.2026;Mobilfunk Beispiel GmbH;Lastschrift;Rechnung;1.870,40;EUR;-29,99;EUR\n",
    ]
    vorspann = "Umsatzanzeige;Datei erstellt am: 31.03.2026 11:13\n\n"
    erster = lese_csv((vorspann + kopf + "".join(zeilen[1:])).encode("cp1252"))
    zweiter = lese_csv((kopf + "".join(zeilen[:4])).encode("cp1252"))
    gemeinsam = {u.fingerabdruck for u in erster} & {u.fingerabdruck for u in zweiter}
    assert len(gemeinsam) == 3  # beide Kaffees und Erika
    assert len({u.fingerabdruck for u in erster + zweiter}) == 5


def test_fingerabdruck_gleich_bei_derselben_datei_in_anderer_kodierung():
    utf8 = (DATEN / "ing.csv").read_bytes().decode("cp1252").encode("utf-8-sig")
    assert [u.fingerabdruck for u in lese_csv(utf8)] == [u.fingerabdruck for u in lies("ing.csv")]


def test_fingerabdruck_eindeutig_bei_gleichen_zeilen():
    text = (
        "Datum;Betrag;Verwendungszweck;Empfänger\n"
        "02.03.2026;-3,20;Kaffee;Café Muster\n"
        "02.03.2026;-3,20;Kaffee;Café Muster\n"
        "02.03.2026;-3,20;Kaffee;Café Muster\n"
    )
    umsaetze = lese_csv(text.encode())
    assert len({u.fingerabdruck for u in umsaetze}) == 3
    assert all(len(u.fingerabdruck) == 40 for u in umsaetze)


def test_fingerabdruck_unabhaengig_von_leerzeichen_und_gross_klein():
    a = lese_csv("Datum;Betrag;Verwendungszweck\n02.03.2026;5,00;RE  2026-001\n".encode())
    b = lese_csv("Datum;Betrag;Verwendungszweck\n02.03.2026;5,00;re 2026-001 \n".encode())
    assert a[0].fingerabdruck == b[0].fingerabdruck
