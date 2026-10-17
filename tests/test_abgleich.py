from datetime import date
from pathlib import Path

import pytest

from arbeitsassistent.abgleich import abgleichen, name_passt, nummern_im_text
from arbeitsassistent.bank import lese_csv
from arbeitsassistent.modell import OffeneRechnung, Umsatz

BANKDATEN = Path(__file__).parent / "daten" / "bank"


def umsatz(betrag, zweck="", gegenpartei="", tag=date(2026, 3, 2)):
    return Umsatz(tag, betrag, gegenpartei, "DE02120300000000202051", zweck)


def rechnung(id, nummer, offen, kunde="Landschaftsverband Musterland", faellig=date(2026, 2, 1)):
    return OffeneRechnung(id, nummer, offen, kunde, faellig)


# Erkennen der Rechnungsnummer


@pytest.mark.parametrize(
    "zweck",
    [
        "2026-001",
        "RE 2026-001",
        "RE2026-001",
        "Rg.-Nr. 2026/001",
        "Rechnung 2026 001",
        "2026001",
        "Rechnung 2026-1",
        "Re.Nr.2026-001 Dolmetscheinsatz",
        "EREF+RE 2026-001 SVWZ+Dolmetschen",
        "Rechnungsnummer: 2026 - 001",
    ],
)
def test_schreibweisen_der_nummer(zweck):
    assert nummern_im_text(zweck) == {(2026, 1)}


def test_lange_nummern_und_mehrere():
    assert nummern_im_text("RE 2026-1042, 2026-1043 und 2026-007") == {
        (2026, 1042),
        (2026, 1043),
        (2026, 7),
    }
    assert nummern_im_text("Rechnungen 2026-001/2026-002") == {(2026, 1), (2026, 2)}


@pytest.mark.parametrize(
    "zweck",
    [
        "IBAN DE02120300000000202051",
        "IBAN DE89 2026 0010 0532 0130 00",
        "Zahlung vom 2026-01-15",
        "Leistung am 15.01.2026",
        "MREF+M-2026-001 CRED+DE98ZZZ09999999999",
        "Mandatsreferenz 2026001",
        "Kundennummer 2026001",
        "Kassenzeichen 2026-0815",
        "Az. 12-2026-0815",
        "Referenz 1234520260012345",
        "20260115",
        "2026 001",
        "Betrag 2026.50 EUR",
    ],
)
def test_keine_falschen_treffer(zweck):
    assert nummern_im_text(zweck) == set()


def test_sepa_kuerzel_ohne_leerzeichen():
    zweck = "EREF+NOTPROVIDEDMREF+M-2026-009CRED+DE98ZZZ09999999999SVWZ+RE2026-004 Dolmetschen"
    assert nummern_im_text(zweck) == {(2026, 4)}


def test_datum_in_ziffern_nur_mit_stichwort():
    assert nummern_im_text("RE20260115") == {(2026, 115)}


def test_jahr_und_zahl_werden_verglichen_nicht_der_text():
    offen = [rechnung(1, "2026-007", 15000)]
    v = abgleichen([(5, umsatz(15000, "Rechnung 2026-7"))], offen)
    assert v[0].sicher and v[0].zahlungen == [(1, 15000)]


def test_2026_1_ist_nicht_2026_10():
    offen = [rechnung(1, "2026-010", 15000)]
    v = abgleichen([(5, umsatz(15000, "RE 2026-1", "Niemand Bekanntes"))], offen)
    assert not v[0].sicher


# Regel 1: Nummer und Betrag passen


def test_nummer_und_betrag_passen_sicher():
    offen = [rechnung(1, "2026-001", 23800), rechnung(2, "2026-002", 23800)]
    v = abgleichen([(10, umsatz(23800, "RE 2026-002 Dolmetschen"))], offen)
    assert len(v) == 1
    assert v[0].umsatz_id == 10
    assert v[0].sicher
    assert v[0].zahlungen == [(2, 23800)]
    assert "2026-002" in v[0].grund


def test_sammelueberweisung_mehrerer_rechnungen():
    offen = [
        rechnung(1, "2026-001", 12000, faellig=date(2026, 1, 10)),
        rechnung(2, "2026-002", 30000, faellig=date(2026, 1, 20)),
        rechnung(3, "2026-003", 8550, faellig=date(2026, 2, 5)),
        rechnung(4, "2026-004", 5000),
    ]
    zweck = "Sammelzahlung Dolmetscherkosten RE 2026-003, 2026-001 und 2026-002"
    v = abgleichen([(7, umsatz(50550, zweck, "LANDSCHAFTSVERBAND MUSTERLAND"))], offen)
    assert v[0].sicher
    assert sorted(v[0].zahlungen) == [(1, 12000), (2, 30000), (3, 8550)]
    assert "Sammelüberweisung" in v[0].grund


def test_freie_nummer_als_teilstring():
    offen = [rechnung(1, "RG-4711", 9000)]
    v = abgleichen([(1, umsatz(9000, "Rechnung RG 4711 vom Februar"))], offen)
    assert v[0].sicher


def test_freie_nummer_nicht_in_laengerer_zahl():
    offen = [rechnung(1, "4711", 9000, kunde="Erika Mustermann")]
    v = abgleichen([(1, umsatz(9000, "Kunde 147112", "Max Beispiel"))], offen)
    assert not v[0].sicher


# Regel 2: Nummer gefunden, Betrag weicht ab


def test_teilzahlung():
    offen = [rechnung(1, "2026-001", 50000)]
    v = abgleichen([(1, umsatz(20000, "RE 2026-001 erste Rate"))], offen)
    assert not v[0].sicher
    assert v[0].zahlungen == [(1, 20000)]
    assert "Teilzahlung" in v[0].grund
    assert "200,00 €" in v[0].grund and "500,00 €" in v[0].grund


def test_ueberzahlung():
    offen = [rechnung(1, "2026-001", 50000)]
    v = abgleichen([(1, umsatz(55000, "RE 2026-001"))], offen)
    assert not v[0].sicher
    assert v[0].zahlungen == [(1, 50000)]
    assert "Überzahlung" in v[0].grund and "50,00 €" in v[0].grund


def test_mehrere_nummern_betrag_passt_zu_einem_teil():
    offen = [rechnung(1, "2026-001", 10000), rechnung(2, "2026-002", 20000)]
    v = abgleichen([(1, umsatz(20000, "RE 2026-001 2026-002"))], offen)
    assert not v[0].sicher
    assert v[0].zahlungen == [(2, 20000)]
    assert "Offen bleibt 2026-001" in v[0].grund


def test_mehrere_nummern_zu_wenig_aelteste_zuerst():
    offen = [
        rechnung(2, "2026-002", 30000, faellig=date(2026, 2, 15)),
        rechnung(1, "2026-001", 30000, faellig=date(2026, 1, 15)),
    ]
    v = abgleichen([(1, umsatz(45000, "RE 2026-002, RE 2026-001"))], offen)
    assert not v[0].sicher
    assert v[0].zahlungen == [(1, 30000), (2, 15000)]
    assert "älteste zuerst" in v[0].grund


def test_mehrere_nummern_ueberzahlung():
    offen = [rechnung(1, "2026-001", 10000), rechnung(2, "2026-002", 20000)]
    v = abgleichen([(1, umsatz(31000, "2026-001 2026-002"))], offen)
    assert not v[0].sicher
    assert sorted(v[0].zahlungen) == [(1, 10000), (2, 20000)]
    assert "Überzahlung" in v[0].grund


def test_zwei_raten_nacheinander():
    offen = [rechnung(1, "2026-001", 50000)]
    v = abgleichen(
        [
            (2, umsatz(30000, "RE 2026-001 Rest", tag=date(2026, 3, 20))),
            (1, umsatz(20000, "RE 2026-001 Rate 1", tag=date(2026, 3, 1))),
        ],
        offen,
    )
    assert [x.umsatz_id for x in v] == [1, 2]
    assert v[0].zahlungen == [(1, 20000)]
    assert v[1].zahlungen == [(1, 30000)]
    assert not v[0].sicher and not v[1].sicher
    assert offen[0].offen == 50000  # Eingabe bleibt unverändert


# Regel 3: keine Nummer, Betrag passt


def test_nur_betrag_und_name_passen():
    offen = [rechnung(1, "2026-001", 17850, kunde="Erika Mustermann")]
    v = abgleichen([(1, umsatz(17850, "Dolmetschen Februar", "MUSTERMANN, ERIKA"))], offen)
    assert not v[0].sicher
    assert v[0].zahlungen == [(1, 17850)]
    assert "Name passt auch" in v[0].grund


def test_nur_betrag_name_passt_nicht():
    offen = [rechnung(1, "2026-001", 17850, kunde="Erika Mustermann")]
    v = abgleichen([(1, umsatz(17850, "Danke", "Max Beispiel"))], offen)
    assert not v[0].sicher
    assert v[0].zahlungen == [(1, 17850)]
    assert "aber nicht" in v[0].grund


def test_gleicher_betrag_name_entscheidet():
    offen = [
        rechnung(1, "2026-001", 12000, kunde="Stadt Musterstadt", faellig=date(2026, 1, 1)),
        rechnung(2, "2026-002", 12000, kunde="Erika Mustermann", faellig=date(2026, 2, 1)),
        rechnung(3, "2026-003", 12000, kunde="Landschaftsverband Musterland"),
    ]
    v = abgleichen([(1, umsatz(12000, "Danke", "Erika Mustermann"))], offen)
    assert v[0].zahlungen == [(2, 12000)]
    assert not v[0].sicher
    assert "weil der Name passt" in v[0].grund


def test_gleicher_betrag_kein_name_aelteste():
    offen = [
        rechnung(1, "2026-001", 12000, kunde="Stadt Musterstadt", faellig=date(2026, 3, 1)),
        rechnung(2, "2026-002", 12000, kunde="Erika Mustermann", faellig=date(2026, 1, 15)),
    ]
    v = abgleichen([(1, umsatz(12000, "Überweisung", "Max Beispiel"))], offen)
    assert v[0].zahlungen == [(2, 12000)]
    assert "der Name zu keiner" in v[0].grund and "älteste" in v[0].grund
    assert "15.01.2026" in v[0].grund


def test_gleicher_betrag_mehrere_namen_passen_aelteste_davon():
    offen = [
        rechnung(1, "2026-001", 12000, kunde="Erika Mustermann", faellig=date(2026, 3, 1)),
        rechnung(2, "2026-002", 12000, kunde="Erika Mustermann", faellig=date(2026, 2, 1)),
        rechnung(3, "2026-003", 12000, kunde="Stadt Musterstadt", faellig=date(2026, 1, 1)),
    ]
    v = abgleichen([(1, umsatz(12000, "", "Erika Mustermann"))], offen)
    assert v[0].zahlungen == [(2, 12000)]
    assert "Betrag und Name passen zu 2" in v[0].grund


def test_name_im_zweck_wenn_kein_zahler():
    offen = [
        rechnung(1, "2026-001", 12000, kunde="Stadt Musterstadt", faellig=date(2026, 1, 1)),
        rechnung(2, "2026-002", 12000, kunde="Erika Mustermann", faellig=date(2026, 2, 1)),
    ]
    v = abgleichen([(1, umsatz(12000, "Auftraggeber: Erika Mustermann Buchungstext: Danke"))], offen)
    assert v[0].zahlungen == [(2, 12000)]


def test_nummer_nicht_offen_wird_genannt():
    offen = [rechnung(1, "2026-002", 12000)]
    v = abgleichen([(1, umsatz(12000, "RE 2026-005", "Landschaftsverband Musterland"))], offen)
    assert not v[0].sicher
    assert v[0].zahlungen == [(1, 12000)]
    assert "2026-005" in v[0].grund and "keine offene Rechnung" in v[0].grund


# Regel 5 und Grenzen


def test_kein_vorschlag_wenn_nichts_passt():
    offen = [rechnung(1, "2026-001", 12000)]
    assert abgleichen([(1, umsatz(9999, "Erstattung", "Max Beispiel"))], offen) == []


def test_ausgaenge_werden_ignoriert():
    offen = [rechnung(1, "2026-001", 12000)]
    assert abgleichen([(1, umsatz(-12000, "RE 2026-001"))], offen) == []


def test_iban_und_datum_fuehren_nicht_zur_sicheren_buchung():
    offen = [rechnung(1, "2026-001", 12000), rechnung(2, "2026-051", 12000)]
    zweck = "Erstattung vom 2026-01-15 an DE02120300000000202051 MREF+M-2026-001"
    v = abgleichen([(1, umsatz(12000, zweck, "Max Beispiel"))], offen)
    assert not v[0].sicher


def test_kein_doppelter_ausgleich_bei_gleicher_nummer():
    offen = [rechnung(1, "2026-001", 12000)]
    v = abgleichen(
        [
            (1, umsatz(12000, "RE 2026-001", tag=date(2026, 3, 1))),
            (2, umsatz(12000, "RE 2026-001 nochmal", tag=date(2026, 3, 5))),
        ],
        offen,
    )
    assert len(v) == 1
    assert v[0].umsatz_id == 1 and v[0].sicher


def test_kein_doppelter_ausgleich_nur_betrag():
    offen = [rechnung(1, "2026-001", 12000), rechnung(2, "2026-002", 12000)]
    eingaenge = [(i, umsatz(12000, "Danke", "Max Beispiel", date(2026, 3, i))) for i in (1, 2, 3)]
    v = abgleichen(eingaenge, offen)
    assert [x.umsatz_id for x in v] == [1, 2]
    assert {x.zahlungen[0][0] for x in v} == {1, 2}


def test_sichere_zuordnung_hat_vorrang_vor_betragstreffer():
    offen = [rechnung(1, "2026-001", 12000)]
    v = abgleichen(
        [
            (1, umsatz(12000, "Danke", "Max Beispiel", date(2026, 3, 1))),
            (2, umsatz(12000, "RE 2026-001", "Landschaftsverband Musterland", date(2026, 3, 9))),
        ],
        offen,
    )
    assert len(v) == 1
    assert v[0].umsatz_id == 2 and v[0].sicher


def test_teilzahlung_verdraengt_keine_sichere_zahlung():
    offen = [rechnung(1, "2026-001", 50000)]
    v = abgleichen(
        [
            (1, umsatz(20000, "RE 2026-001", tag=date(2026, 3, 1))),
            (2, umsatz(50000, "RE 2026-001", tag=date(2026, 3, 9))),
        ],
        offen,
    )
    sicher = [x for x in v if x.sicher]
    assert [x.umsatz_id for x in sicher] == [2]
    assert sum(c for x in v for _, c in x.zahlungen) <= 50000


def test_hoechstens_ein_vorschlag_pro_umsatz():
    offen = [rechnung(i, f"2026-00{i}", 10000) for i in range(1, 5)]
    eingaenge = [(i, umsatz(10000, f"RE 2026-00{i}")) for i in range(1, 5)]
    eingaenge += [(9, umsatz(10000, "RE 2026-001"))]
    v = abgleichen(eingaenge, offen)
    ids = [x.umsatz_id for x in v]
    assert len(ids) == len(set(ids))
    assert sum(c for x in v for _, c in x.zahlungen) == 40000


# Zusammenspiel mit dem Bankimport


@pytest.mark.parametrize("datei", ["dkb.csv", "comdirect.csv", "commerzbank.csv", "n26.csv"])
def test_sammelueberweisung_aus_bankdatei(datei):
    daten = (BANKDATEN / datei).read_bytes()
    umsaetze = list(enumerate(lese_csv(daten), start=1))
    offen = [
        rechnung(1, "2026-001", 50000, faellig=date(2026, 3, 1)),
        rechnung(2, "2026-002", 58000, faellig=date(2026, 3, 8)),
        rechnung(3, "2026-099", 2999, kunde="Mobilfunk Beispiel GmbH"),
    ]
    v = abgleichen(umsaetze, offen)
    sammel = [x for x in v if x.sicher]
    assert len(sammel) == 1
    assert sorted(sammel[0].zahlungen) == [(1, 50000), (2, 58000)]
    assert all(3 not in [r for r, _ in x.zahlungen] for x in v)  # Ausgänge nie zuordnen


def test_sepa_zweck_aus_sparkasse_mt940():
    daten = (BANKDATEN / "sparkasse_mt940.csv").read_bytes()
    v = abgleichen(list(enumerate(lese_csv(daten))), [rechnung(7, "2026-003", 23800)])
    assert len(v) == 1 and v[0].sicher and v[0].zahlungen == [(7, 23800)]


# Namensvergleich


@pytest.mark.parametrize(
    "zahler, kunde",
    [
        ("LANDSCHAFTSVERBAND MUSTERLAND", "Landschaftsverband Musterland"),
        ("Stadtkasse Musterstadt", "Stadt Musterstadt - Sozialamt"),
        ("MUELLER, HANS", "Hans Müller"),
        ("Gehoerlosenverband Beispielland e.V.", "Gehörlosenverband Beispielland"),
        ("Dolmetschzentrum Muster GmbH", "dolmetschzentrum muster gmbh"),
    ],
)
def test_namen_passen(zahler, kunde):
    assert name_passt(zahler, kunde)


@pytest.mark.parametrize(
    "zahler, kunde",
    [
        ("Landschaftsverband Beispielland", "Landschaftsverband Musterland"),
        ("Stadt Beispielhausen", "Stadt Musterstadt"),
        ("Muster GmbH", "Beispiel GmbH"),
        ("Jobcenter Beispielhausen", "Jobcenter Musterstadt"),
        ("Max Beispiel", "Erika Mustermann"),
        ("Stadt und Landkreis e.V.", "Die Stadt GmbH & Co. KG"),
    ],
)
def test_namen_passen_nicht(zahler, kunde):
    assert not name_passt(zahler, kunde)
