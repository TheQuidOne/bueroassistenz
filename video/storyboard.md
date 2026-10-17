# Storyboard: Video-Tutorial „Arbeits-Assistent“

Für den Nutzer (selbstständiger DGS-Dolmetscher, keine Technikkenntnisse). Deutsch, Anrede „Sie“,
**ohne Ton**. Alles steht in kurzen Einblendungen in einfacher Sprache. 16:9, 1920 × 1080, 30 fps,
Endfassung: 171 s (2:51), neun Kapitel, jede Einblendung steht mindestens 2,5 s. Die KI ist
sichtbar gemacht (2026-10-09, Rückmeldung Benjamin):
- Der Vorspann nennt die KI.
- Der Datenschutz-Teil sagt „Auch die KI läuft auf Ihrem Mac.“ und zeigt dazu die KI-Zeile.
- Kapitel 7 heißt „Die KI liest Ihre Mails mit“ und beginnt mit einer Ablauf-Grafik
  (Mail → KI auf diesem Mac → Hinweis → Entwurf, „Die KI schlägt vor. Sie entscheiden.“).
- Kapitel 7 hebt außerdem KI-Zeile und KI-Schild hervor.
- Der Abspann nennt „Fristen und Mails mit KI im Blick“.

**Verbindlich sind die Zeiten und Schritte in `tutorial/szenen.js`.** Die Tabellen unten sind der Plan.
In der Endfassung hebt der Datenschutz den Hinweis „Alle Daten bleiben auf diesem Rechner“ in der
Seitenleiste hervor (statt der Adressleiste). Kapitel 3 startet direkt über „Neue Rechnung“ in der
Seitenleiste. In Kapitel 5 speichert die Auswahl sofort, danach kommt „Immer so einordnen“.
Kapitel 7 heißt „Kalender und Auswertung“.

## Gestaltung

- **Bild:** echte Screenshots der App mit erfundenen Demo-Daten, aufgenommen in 1920 × 1080 bei
  doppelter Pixeldichte (3840 × 2160). Dadurch bleiben Zooms bis 2× scharf. Die App steht in einem
  schlichten Browserfenster mit der Adresse `127.0.0.1:8770` auf hellgrauem Grund.
- **Kamera:** ruhige Fahrten und Zooms (1,1–1,4 s, `power2.inOut`) auf den Bereich, um den es
  gerade geht. Höchstens 2× Zoom.
- **Mauszeiger:** macOS-Pfeil, gleitet in 0,8–1,0 s zum Ziel. Ein Klick zeigt einen Puls in der
  Akzentfarbe. Die Zielpunkte stammen aus `aufnahmen/ziele.json` und sitzen dadurch genau.
- **Hervorhebung:** Ring in der Akzentfarbe um das Ziel, der Rest wird leicht abgedunkelt.
- **Textleiste:** weiße Karte unten mittig, 1–2 Zeilen, 46 px Inter Semibold. Jede Einblendung
  steht mindestens 2,5 s.
- **Kapitel:** kurze Kapitelkarte (1,6 s) mit Nummer und Titel. Danach steht oben links die
  Kapitelmarke („3 · Rechnung schreiben“).
- **Farben:** Grund `#f3f4f6`, Text `#111827`, eine Akzentfarbe `#2451c7` (Blau der neuen
  Oberfläche). Schrift Inter, lokal eingebunden.
- **Übergänge:** Bildwechsel als Überblendung (0,35 s), Kapitelwechsel als Blende (0,4 s).
  Keine Effekte.

Zeiten in den Tabellen sind Planwerte (Minute:Sekunde), die Überschriften zeigen die Endfassung. Die Aufnahmen heißen wie in `aufnahme.mjs`, die
Ziele wie die `data-tour`-Attribute.

## 0 · Titel (0:00–0:05.2)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 0:00 | Heller Grund. Titel erscheint, darunter steigt das App-Fenster (`uebersicht-start`) langsam auf. | **Arbeits-Assistent** · Rechnungen, Zahlungen und Fristen. Alles auf Ihrem Mac. | – | – |

## 1 · Datenschutz (0:05.2–0:14.2)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 0:05 | Fenster mit Übersicht (`uebersicht-start`), ganze Ansicht | Ihre Daten bleiben auf diesem Rechner. | – | – |
| 0:06.5 | Zoom auf die Adressleiste `127.0.0.1` | (bleibt) | Adressleiste; neue Oberfläche zusätzlich `lokal` („Alle Daten bleiben auf diesem Rechner“) | – |
| 0:08.4 | Kamera zurück | Keine Cloud, kein Konto. | – | – |

## 2 · Kapitel 1 – Einrichten (0:14.2–0:24.8)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 0:11 | Kapitelkarte „1 · Einrichten“ | – | – | – |
| 0:12.6 | Übersicht, dann `einstellungen-angaben` | Einmal einrichten: Ihre Angaben. | `nav-einstellungen` | Mitte → `nav-einstellungen`, Klick |
| 0:14.8 | Zoom auf die Angaben | Sie stehen auf jeder Rechnung. | `angaben` | ruht |
| 0:17.2 | `einstellungen-vorlagen` (Überblendung) | Leistungen mit festem Preis anlegen, z. B. Dolmetscheinsatz 85 € pro Stunde. | `vorlage-dolmetsch` (Zeile), dann `vorlagen` | → Zeile |

## 3 · Kapitel 2 – Kunden (0:24.8–0:38.0)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 0:21.6 | Kapitelkarte „2 · Kunden“ | – | – | – |
| 0:23.2 | `kunden-liste` | Kunden legen Sie einmal an. | Zeile Integrationsamt | → `nav-kunden`, Klick |
| 0:25.2 | `kunde-neu-leer` | (bleibt) | – | → `kunde-neu`, Klick |
| 0:26.2 | `kunde-neu-name` → `kunde-neu-fertig` (Tippen) | Name und Anschrift eintragen. | `kunde-name` | → `kunde-name`, Klick |
| 0:28.4 | Zoom auf Umsatzsteuer | Für jeden Kunden: mit oder ohne Umsatzsteuer. | `kunde-steuerfall` | → `kunde-steuerfall` |
| 0:30.2 | `kunden-gespeichert` | (bleibt) | neue Zeile | → `kunde-speichern`, Klick |

## 4 · Kapitel 3 – Rechnung schreiben (0:38.0–1:05.4)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 0:31.2 | Kapitelkarte „3 · Rechnung schreiben“ | – | – | – |
| 0:32.8 | `kunden-gespeichert` → `rechnung-neu-leer` | Neue Rechnung. | `neue-rechnung` | → `neue-rechnung`, Klick |
| 0:34.3 | `rechnung-kunde` (Zoom Kopf) | Kunde wählen. | `kunde-waehlen` | → `kunde-waehlen`, Klick |
| 0:36.3 | `rechnung-zeile` → Tippen in `rechnung-leistung` | Leistung tippen … | `position-leistung` | → `position-leistung`, Klick |
| 0:38.8 | Preis, Einheit und Betrag erscheinen | … der Preis kommt aus Ihrer Vorlage. | `position-preis` | ruht |
| 0:41.3 | `rechnung-entwurf` | Als Entwurf speichern. Er lässt sich noch ändern. | `entwurf-speichern` | → `entwurf-speichern`, Klick |
| 0:44.0 | PDF-Vorschau (`pdf-entwurf`) fährt als Blatt hoch | Vorschau ansehen. | – | → `vorschau`, Klick |
| 0:46.5 | Blatt weg, `rechnung-ausgestellt` | Ausstellen: Die Rechnung bekommt ihre Nummer. | `ausstellen`, dann Nummer | → `ausstellen`, Klick |
| 0:49.5 | Zoom Nummer und Zustand | Danach lässt sie sich nicht mehr ändern. | `rechnung-nummer` | ruht |
| 0:52.0 | `pdf-rechnung` als Blatt, Zoom auf den QR-Code | Das PDF hat einen QR-Code zum Überweisen. | `qr` | → `pdf-oeffnen`, Klick |

## 5 · Kapitel 4 – Kontoauszug einlesen (1:05.4–1:23.8)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 0:56.8 | Kapitelkarte „4 · Kontoauszug einlesen“ | – | – | – |
| 0:58.4 | `konto-vorher` | Im Online-Banking die Umsätze als CSV exportieren. | `csv-ablage` | → `nav-konto`, Klick |
| 1:01.0 | Datei-Symbol „Umsaetze.csv“ hängt am Zeiger und wird in die Ablage gezogen | Die Datei hier hineinziehen. | `csv-ablage` | von rechts außen → `csv-ablage`, loslassen |
| 1:04.0 | `konto-import` | Zahlungen mit Rechnungsnummer werden automatisch zugeordnet. | `meldung` | ruht |
| 1:07.0 | `konto-eingaenge` | Auch Sammelüberweisungen. | Zeile Sammelzahlung | → Reiter „Eingänge“, Klick |
| 1:10.0 | `konto-stimmt` | Unsichere Fälle bestätigen Sie mit „Stimmt“. | `vorschlag-stimmt` | → `vorschlag-stimmt`, Klick |

## 6 · Kapitel 5 – Ausgaben einordnen (1:23.8–1:35.6)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 1:14.4 | Kapitelkarte „5 · Ausgaben einordnen“ | – | – | – |
| 1:16.0 | `ausgaben-liste` → `ausgaben-gewaehlt` | Ausgaben einordnen: Kategorie wählen. | `kategorie-waehlen` (Zeile Deutsche Bahn) | → `kategorie-waehlen`, Klick |
| 1:19.0 | `ausgaben-merken` | „Immer so“ merkt sich Ihre Wahl. | `regel-merken` | → `regel-merken`, Klick |
| 1:21.0 | `ausgaben-fertig` | (bleibt) | `meldung` | ruht |

## 7 · Kapitel 6 – Übersicht (1:35.6–1:47.2)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 1:23.0 | Kapitelkarte „6 · Übersicht“ | – | – | – |
| 1:24.6 | `uebersicht` | Die Übersicht zeigt, was offen ist … | `kpi-offen` | → `nav-uebersicht`, Klick |
| 1:27.0 | (gleich) | … und was überfällig ist. | `kpi-ueberfaellig`, dann `aufgaben` | ruht |
| 1:29.0 | `pdf-erinnerung` als Blatt | Zahlungserinnerung mit einem Klick. | `erinnerung` | → `erinnerung`, Klick |

## 8 · Kapitel 7 – Die KI liest Ihre Mails mit (1:47.2–2:20.0)

Steht nach der Übersicht: Es erklärt, woher die Hinweise unter „Zu erledigen“ kommen, und endet dort
mit „Rechnung vorbereiten“. Der Kalender danach zeigt schon Einsätze aus Mails.

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 1:47.2 | Kapitelkarte „7 · Die KI liest Ihre Mails mit“ | – | – | – |
| 1:48.6 | Grafik: Mail → KI (auf diesem Mac, Schloss) → Hinweis „Einsatz abrechnen“ mit KI-Schild → Entwurf | Die KI schlägt vor. Sie entscheiden. | KI-Station betont | – |
| 1:45.8 | `post-liste` → „E-Mail einrichten“ → `post-einrichten-gewaehlt` (GMX, erfundene Adresse, nichts abgeschickt) | Postfach einmal verbinden. Es wird nur gelesen. | `mail-anbieter`, dann Karte „Was mit Ihren Mails passiert“ | → `nav-post`, → `post-einrichten`, → `mail-anbieter` |
| 1:54.6 | `post-liste` | Die KI auf diesem Mac liest Arbeits-Mails mit. | Mail „Bestätigung Dolmetscheinsatz“ | → `nav-post`, → Mail |
| 1:58.7 | `post-mail` | Sie erkennt Einsätze, Absagen und Rückfragen. | `ki-auswertung` | ruht |
| 2:10.9 | `post-uebersicht` | Was die KI gefunden hat, ist mit KI markiert. | `ki-zeile`, dann `ki-schild` | → `nav-uebersicht` |
| 2:14.0 | `post-uebersicht` → `post-entwurf` | Ein Klick, der Entwurf ist vorbereitet. Sie prüfen und entscheiden. | Hinweis „Einsatz … abrechnen“, dann die vorbereitete Position | → `rechnung-vorbereiten` |

## 9 · Kapitel 8 – Kalender und Auswertung (2:20.0–2:33.0)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 1:32.6 | Kapitelkarte „8 · Kalender und Auswertung“ | – | – | – |
| 1:34.2 | `kalender` | Im Kalender: Einsätze, Fristen und Zahlungseingänge. | Legende, dann heutiger Tag | → `nav-kalender`, Klick |
| 1:38.0 | `jahr` | Die Jahresübersicht ist die Grundlage für Ihre Einnahmen-Überschuss-Rechnung. | Kacheln Einnahmen/Ausgaben | → `nav-auswertung`, Klick |
| 1:41.0 | (gleich) | Als CSV für Ihre Steuerberatung. | `jahr-csv` | → `jahr-csv` (kein Download nötig) |

## 10 · Kapitel 9 – Sicherung (2:33.0–2:46.0)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 1:44.2 | Kapitelkarte „9 · Sicherung“ | – | – | – |
| 1:45.8 | `einstellungen-daten` (Pfad als `/Users/max/Arbeits-Assistent` dargestellt) | Alle Daten liegen in einem Ordner. | `datenordner`, `ordner-oeffnen` | → `ordner-oeffnen` (wird bei der Aufnahme **nicht** geklickt, sonst öffnet sich ein Dateifenster) |
| 1:48.5 | Grafik: Ordner mit `daten.db`, `rechnungen`, `importe` und Time-Machine-Symbol | Time Machine sichert ihn automatisch mit. | – | – |

## 11 · Abspann (2:46.0–2:50.6)

| Zeit | Bildschirm / Zustand | Einblendung | Hervorhebung | Maus |
|---|---|---|---|---|
| 1:51.8 | Heller Grund, App-Symbol | **Arbeits-Assistent** · Rechnungen schreiben · Zahlungen erkennen · Fristen im Blick. Dieses Video finden Sie jederzeit unter „Hilfe“. | – | – |

## Daten in der Aufnahme

Nur erfundene Daten aus `arbeitsassistent/demo.py` (Max Beispiel, Integrationsamt Musterland, …).
Dazu kommen ein neuer Kunde „Eingliederungshilfe Musterkreis“, die neue Rechnung (2 Std.
Dolmetscheinsatz DGS) und eine erfundene CSV im Sparkasse-Format mit zwei Zahlungen mit
Rechnungsnummer und einer Bahnfahrt. Die Aufnahme baut die CSV aus den offenen Demo-Rechnungen.
