"""Zahlungsziel: Fälligkeit in Werktagen (Montag bis Freitag ohne Feiertage) oder Kalendertagen.

Feiertage kommen aus dem Paket `holidays` (offline), je nach Bundesland aus den Einstellungen.
Eine Fälligkeit fällt nie auf ein Wochenende oder einen Feiertag, sie rückt auf den nächsten
Werktag (wie § 193 BGB).
"""

from datetime import date, timedelta
from functools import cache

import holidays

BUNDESLAENDER = {
    "": "Nur bundesweite Feiertage",
    "BW": "Baden-Württemberg", "BY": "Bayern", "BE": "Berlin", "BB": "Brandenburg", "HB": "Bremen",
    "HH": "Hamburg", "HE": "Hessen", "MV": "Mecklenburg-Vorpommern", "NI": "Niedersachsen",
    "NW": "Nordrhein-Westfalen", "RP": "Rheinland-Pfalz", "SL": "Saarland", "SN": "Sachsen",
    "ST": "Sachsen-Anhalt", "SH": "Schleswig-Holstein", "TH": "Thüringen",
}
ZAEHLWEISEN = {"werktage": "Werktagen (Montag bis Freitag, ohne Feiertage)", "kalendertage": "Kalendertagen"}


@cache
def _feiertage(land: str, jahr: int) -> frozenset[date]:
    return frozenset(holidays.country_holidays("DE", subdiv=land or None, years=jahr))


def werktag(tag: date, land: str = "") -> bool:
    return tag.weekday() < 5 and tag not in _feiertage(land if land in BUNDESLAENDER else "", tag.year)


def einheit(einst: dict) -> str:
    """„Werktage“ oder „Tage“, für Beschriftungen."""
    return "Tage" if einst.get("zahlungsziel_zaehlung") == "kalendertage" else "Werktage"


def faellig(start: date, tage: int, einst: dict) -> date:
    """Fälligkeit nach `tage` Werk- bzw. Kalendertagen ab `start`, nie am Wochenende oder Feiertag."""
    land = einst.get("bundesland", "")
    if einst.get("zahlungsziel_zaehlung") == "kalendertage":
        ziel = start + timedelta(days=tage)
    else:
        ziel, gezaehlt = start, 0
        while gezaehlt < tage:
            ziel += timedelta(days=1)
            gezaehlt += werktag(ziel, land)
    while not werktag(ziel, land):
        ziel += timedelta(days=1)
    return ziel
