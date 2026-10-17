import keyring
import pytest
from keyring.backend import KeyringBackend

from arbeitsassistent import db as dbmod
from arbeitsassistent import rechnungen


class SpeicherSchluesselbund(KeyringBackend):
    """Ersatz für den Schlüsselbund des Systems: Tests schreiben nie in den echten."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.werte = {}

    def set_password(self, service, benutzer, passwort):
        self.werte[(service, benutzer)] = passwort

    def get_password(self, service, benutzer):
        return self.werte.get((service, benutzer))

    def delete_password(self, service, benutzer):
        self.werte.pop((service, benutzer), None)


@pytest.fixture(autouse=True)
def schluesselbund():
    ersatz = SpeicherSchluesselbund()
    keyring.set_keyring(ersatz)
    return ersatz

ABSENDER = {
    "name": "Erika Mustermann",
    "strasse": "Musterweg 1",
    "plz_ort": "12345 Musterstadt",
    "steuernummer": "123/456/78901",
    "iban": "DE02120300000000202051",
}


@pytest.fixture
def ordner(tmp_path, monkeypatch):
    monkeypatch.setenv("ARBEITSASSISTENT_DATEN", str(tmp_path))
    return tmp_path


@pytest.fixture
def db(ordner):
    verbindung = dbmod.verbinden(ordner)
    dbmod.einstellungen_setzen(verbindung, ABSENDER)
    yield verbindung
    verbindung.close()


@pytest.fixture
def einst(db):
    return dbmod.einstellungen(db)


@pytest.fixture
def kunde(db):
    return rechnungen.kunde_speichern(db, {"name": "Landschaftsverband Musterland", "anschrift": "Amtsweg 1\n12345 Musterstadt"})
