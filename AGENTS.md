# AGENTS.md – Büroassistenz

Bis 2026-10-17 hieß das Programm „Arbeits-Assistent“. Technische Namen behalten den alten
Namen, damit auf dem Mac nichts umziehen muss: Paket `arbeitsassistent`, Datenordner
`~/Arbeits-Assistent`, LaunchAgents `de.arbeitsassistent…`, Schlüsselbund-Dienst
„Arbeits-Assistent“, Ollama-Ordner. Sichtbar ist nur „Büroassistenz“ (`arbeitsassistent.NAME`).

Für Claude Code, Codex und andere Agenten. Zuerst die `README.md` (für den Kunden) und
`docs/entscheidungen.md` (Stand, Entscheidungen, offene Fragen) lesen.

## Regeln

- Das Programm ist für einen Kunden von Benjamin (Gebärdensprachdolmetscher) mit sensiblen Daten. Seine echten Daten
  (Mails, Umsätze, Rechnungen, Termine, Zugangsdaten) kommen nie in dieses Repo und nie
  auf Benjamins Rechner. Entwickelt und getestet wird nur mit erfundenen Daten
  (`arbeitsassistent/demo.py`, `tests/daten/`).
- **Seit 2026-10-10 läuft der Mac (`meinmac`) mit echten Daten. Von dort nichts mehr
  auslesen:** keine Datenbankabfragen, keine Seiten der App abrufen (außer `/gesund`),
  keine Dateien aus `~/Arbeits-Assistent` ansehen, auflisten oder kopieren, keine Mails,
  keine Logs der App. Fehler auf dem Mac stellt Benjamin nach, nicht der Agent.
- **Seit 2026-10-17 steht der Mac an einem anderen Ort** und ist weder per LAN noch per
  Tailscale erreichbar. Agenten liefern nur über GitHub (`main`) aus; aktualisiert wird
  am Mac mit dem Befehl aus der README („Aktualisieren“). Nach einem Push Benjamin sagen,
  dass ein Update bereitliegt.
- Keine Cloud-Dienste, keine Telemetrie, keine Online-KI. Die Seite lauscht nur auf
  `127.0.0.1`.
- Was zuverlässig sein muss (Zahlungen, Fristen, Zuordnung, Erkennen von Arbeits-Mails),
  läuft ohne KI. Die KI macht nur Vorschläge, der Kunde bestätigt.
- Ausgestellte Rechnungen nie ändern oder löschen, auch nicht per Migration. Korrektur nur
  per Rechnungskorrektur (`rechnungen.berichtigen`, ersetzt die alte, Status „berichtigt“)
  oder Storno; geändert wird an der alten Rechnung nur der Status. PDFs werden mit
  `open(..., "xb")` geschrieben und nie überschrieben.
- Ausgestellte PDFs sind E-Rechnungen (ZUGFeRD EN 16931, PDF/A-3). Nach Änderungen an
  `pdf.py` oder `zugferd.py` erneut mit Mustang prüfen (siehe `docs/entscheidungen.md`).
- Neue Datenbankspalten gehören ins `SCHEMA` und nach `db.NACHTRAEGE`, damit bestehende
  Datenbanken auf dem Mac sie beim Start bekommen.
- Beträge sind ganze Cent (`int`), Mengen `Decimal`. Keine Fließkommazahlen für Geld.
- Mails nur lesen, nie verändern oder als gelesen markieren (`select(readonly=True)`, `BODY.PEEK[]`).
- Die KI informiert nur: Aus ihrer Auswertung entstehen nach festen Regeln (`hinweise.py`) Hinweise.
  Rechnungen, Zahlungen und Zuordnungen ändert sie nie. Rechnungsnummern erkennt `abgleich.py`,
  nicht die KI.
- Mail-Passwort nur im Schlüsselbund (`keyring`, Dienst „Arbeits-Assistent“), nie in der Datenbank.
- Zielrechner ist macOS auf Apple Silicon (M1 Pro, 16 GB). Entwickelt wird unter Linux,
  der Code muss auf beiden laufen (Python ≥ 3.11).
- Oberfläche und Texte auf Deutsch, Anrede „Sie“, für jemanden ohne Technikkenntnisse.
  Professionell und einfach: eine Handlung pro Aufgabe, Seltenes einklappen.
- `data-tour="…"`-Attribute in den Vorlagen markieren Ziele für das Video-Tutorial
  (`video/`). Nicht entfernen; bei Umbauten mitnehmen und das Video neu aufnehmen.
- **Das Repo `TheQuidOne/bueroassistenz` ist öffentlich** (seit 2026-10-17, Benjamins
  Entscheidung). Nichts Privates hineinschreiben: keine echten Daten, keine Zugangsdaten,
  keine privaten Adressen, Namen oder IPs. Commits mit Benjamins GitHub-noreply-Adresse
  (in der Repo-Konfiguration gesetzt), nie mit seiner privaten Mail. Die
  Entwicklungsgeschichte bis 2026-10-17 liegt im privaten Archiv `TheQuidOne/arbeits-assistent`.

## Aufbau

| Datei | Aufgabe |
|---|---|
| `arbeitsassistent/geld.py` | Beträge lesen und formatieren (Cent), Mengen, Rundung |
| `arbeitsassistent/modell.py` | Datenklassen zwischen den Modulen |
| `arbeitsassistent/db.py` | SQLite-Schema, Datenordner, Einstellungen |
| `arbeitsassistent/rechnungen.py` | Kunden, Entwürfe, Nummernformat, Ausstellen, Storno, Berichtigung, Zahlungen, Vorlagen |
| `arbeitsassistent/pdf.py` | Rechnungs- und Erinnerungs-PDF (fpdf2, Girocode mit segno, sRGB für PDF/A) |
| `arbeitsassistent/zugferd.py` | E-Rechnung: CII-XML (EN 16931) erzeugen und ins PDF einbetten (drafthorse) |
| `arbeitsassistent/nachtrag.py` | Alte Rechnungen: Werte aus E-Rechnung, Text oder Scan vorschlagen (KI nur Vorschlag) |
| `arbeitsassistent/unterschrift.py` | Eingescannte Unterschrift aufbereiten; liegt nur als `unterschrift.png` im Datenordner |
| `arbeitsassistent/bank.py` | CSV-Import aller gängigen Banken |
| `arbeitsassistent/abgleich.py` | Zahlungseingänge den offenen Rechnungen zuordnen |
| `arbeitsassistent/umsaetze.py` | Import, Vorschläge, Kategorien, Regeln |
| `arbeitsassistent/auswertung.py` | Übersicht, Kalender, Jahresübersicht, Einnahmen je Jahr, CSV-Export |
| `arbeitsassistent/web.py` + `templates/` | Flask-Oberfläche (`_symbole.html`: SVG-Symbole) |
| `arbeitsassistent/static/hilfe/` | Tutorial-Video der Hilfe-Seite (Webfassung) |
| `arbeitsassistent/mail.py` | Postfach lesen (IMAP, nur lesend), Anbieter-Voreinstellungen |
| `arbeitsassistent/ki.py` | lokale KI über Ollama: Prompt, Schema, Auswertung einer Mail |
| `arbeitsassistent/hinweise.py` | feste Regeln: Auswertung → Hinweis, Kunde/Rechnung zuordnen, Entwurf vorbereiten |
| `arbeitsassistent/postfach.py` | Ablauf: abrufen, speichern, auswerten; Schlüsselbund; Hintergrund alle 15 min |
| `evals/mails.json`, `scripts/ki_pruefen.py` | erfundene Testmails mit Sollwerten, Messung der KI |
| `arbeitsassistent/demo.py` | erfundene Beispieldaten (`--demo`) |
| `installieren.sh`, `Installieren.command` | Installation, Aktualisierung, `--entfernen` auf dem Mac |
| `video/` | Video-Tutorial mit HyperFrames (Aufnahmeskript, Komposition) |

Tests: `.venv/bin/python -m pytest -q`
