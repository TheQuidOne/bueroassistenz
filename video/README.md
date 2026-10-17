# Video-Tutorial

Stummes Erklärvideo (Deutsch, „Sie“, nur Texteinblendungen) für den Nutzer des
Arbeits-Assistenten. 1920 × 1080, 30 fps, 2:51 min. Die App zeigt es unter „Hilfe“. Inhalt und Zeiten: `storyboard.md`.
Gebaut aus echten Screenshots der App mit erfundenen Demo-Daten und mit HyperFrames gerendert.

## Befehle

```bash
cd video
npm install                 # einmalig: puppeteer-core lokal (kein Browser-Download nötig)
node aufnahme.mjs           # neu aufnehmen: frische Demo starten, App bedienen, Screenshots + Ziele
node rendern.mjs --hilfe    # check, Render, Webfassung und Vorschaubild, dann in die Hilfe der App kopieren
```

- `node rendern.mjs` (ohne `--hilfe`) erzeugt nur `export/`: `tutorial.mp4` (hohe Qualität),
  `tutorial-web.mp4` (H.264, ≤ 12 MB) und `tutorial.jpg` (Vorschaubild ≤ 250 KB). `--hilfe` kopiert
  Webfassung und Vorschaubild nach `arbeitsassistent/static/hilfe/tutorial.mp4` und `tutorial.jpg`.

- `node rendern.mjs --entwurf` rendert schnell nach `export/tutorial-entwurf.mp4`.
  `--nur-check` gleicht nur die Länge ab und ruft `hyperframes check` auf.
- Beide Läufe legen Prüfbilder ab (`export/pruefbilder*/`, ein Bild alle 2 s plus Kontaktbogen).
- Vorschau im Browser: `cd tutorial && npm run dev`.
- `aufnahme.mjs` startet die Demo selbst (`.venv/bin/python -m arbeitsassistent --demo`) mit
  eigenem Datenordner (`--tmp`, Standard: Temp-Ordner des Systems) und freiem Port ab 8790. Es prüft
  mit `ss`, dass genau dieser Prozess lauscht, und stoppt am Ende nur diese PID.
  Gegen eine laufende Instanz: `node aufnahme.mjs --port 8791 --daten <Datenordner>`.
  Einen anderen Programmstand aufnehmen: `--app <Ordner mit dem Paket arbeitsassistent>`.

## Aufbau

| Datei | Aufgabe |
|---|---|
| `storyboard.md` | Kapitel, Zeiten, Bildschirm, Einblendung, Hervorhebung, Mausweg |
| `aufnahme.mjs` | bedient die App mit Chromium, Screenshots 1920 × 1080 @2x nach `aufnahmen/`, Boxen der Ziele in `aufnahmen/ziele.json` (+ `ziele.js`), PDFs als PNG mit Lage des QR-Codes |
| `rendern.mjs` | Länge aus dem Drehbuch übernehmen, `check`, `render`, Webfassung, Prüfbilder |
| `tutorial/szenen.js` | Drehbuch: Szenen und Schritte (Bild, Kamera, Licht, Maus, Tippen, Ziehen, Blatt, Text) |
| `tutorial/assets/bausteine.js` | Bausteine: Titel, Kapitelkarte, App-Szene mit Kamerafahrt, Mauszeiger mit Klick-Puls, Hervorhebung, Tipp-Effekt, Scroll-Übergang, Datei ziehen, PDF-Blatt mit Zoom, Textleiste, Kapitelmarke, Ablauf-Grafik, Ordner-Grafik, Abspann |
| `tutorial/index.html` | Aussehen (Farben, Schrift, Fenster, Textleiste) |

## Hinweise

- **Ziele** sucht `aufnahme.mjs` zuerst über `data-tour="<name>"`, danach über Rückfall-Selektoren
  (Tabelle `ZIELE` oben im Skript). Zusätzlich zu den abgesprochenen Namen nutzt es: `lokal`,
  `meldung`, `kunde-neu`, `kunde-zeile`, `kunde-name`, `kunde-anschrift`, `kunde-steuerfall`,
  `kunde-speichern`, `position-datum`, `position-menge`, `position-einheit`, `position-betrag`,
  `summe`, `vorschau`, `rechnung-nummer`, `csv-datei`, `reiter-eingaenge`, `umsatz-zeile`,
  `einordnen`, `vorlage-zeile`, `datenordner`, `kalender-legende`, `kalender-heute`, `jahr-kacheln`.
  Am Ende meldet das Skript, welche optionalen Ziele fehlen.
- **Darstellung:** Der Pfad des Demo-Ordners erscheint im Bild als `/Users/max/Arbeits-Assistent`
  (wie auf dem Mac). Für die Systemschrift setzt die Aufnahme Inter ein, weil es SF unter Linux
  nicht gibt. Hell, ohne Übergänge und ohne Textcursor. Dunkelmodus ist aus.
- „Ordner öffnen“ wird bei der Aufnahme nie geklickt: Es würde ein Dateifenster öffnen.
- HyperFrames immer als `hyperframes@0.8.81` mit `HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1
  HYPERFRAMES_SKIP_SKILLS=1` (die Skripte tun das). `snapshot` nur mit `--describe false`, sonst
  schickt es Bilder an Gemini, sobald `GEMINI_API_KEY` gesetzt ist.
- Fremddateien mit Lizenz daneben: Inter in `tutorial/assets/fonts/` (`OFL.txt`, SIL Open Font
  License 1.1), GSAP 3.15 in `tutorial/assets/vendor/` (`LICENSE-gsap.txt`, GSAP Standard License).
- `aufnahmen/` und `export/` sind nicht im Git und werden jederzeit neu erzeugt.

## Stand

**Aktualisiert (2026-10-09, KI sichtbar):** neu aufgenommen mit Commit df6a22e.
- Die KI kommt im Vorspann, im Datenschutz-Teil und im Abspann vor.
- Kapitel 7 heißt „Die KI liest Ihre Mails mit“: Ablauf-Grafik (Baustein `ablauf`), KI-Zeile und
  KI-Schild auf der Übersicht.
- 9 Kapitel, 171 s, jede Einblendung mindestens 2,5 s.

- `export/tutorial.mp4`: hohe Qualität
- `export/tutorial-web.mp4`: Webfassung, liegt auch in `arbeitsassistent/static/hilfe/tutorial.mp4`
- `export/tutorial.jpg`: Vorschaubild, liegt auch in `arbeitsassistent/static/hilfe/tutorial.jpg`

Geprüft: `check` ohne Fehler und Warnungen, Kontaktbogen, Wiedergabe auf `/hilfe` im Demo-Server.

Ändert sich die Oberfläche, reichen `node aufnahme.mjs` und `node rendern.mjs --hilfe`. Ziele, die es
nicht mehr gibt, meldet die Aufnahme. Danach die Prüfbilder ansehen.

Das Postfach-Formular füllt die Aufnahme nur fürs Bild aus (GMX, `max@beispiel.example`, erfundenes
Passwort) und schickt es nie ab.
