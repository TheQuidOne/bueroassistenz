# Entscheidungen und Stand

Interne Notizen für Benjamin und die Agenten. Was der Kunde braucht, steht im README.

## Stand

- **2026-10-04 – Baustein 1:** Rechnungen, Bank-CSV, Abgleich, Kalender, Jahresübersicht. Noch ohne KI.
- **2026-10-09 – Neue Oberfläche:**
  - Seitenleiste mit Zählern, Startseite als Aufgabenliste mit Einrichtungsschritten.
  - Konto-Seite mit einem Auswahlfeld je Buchung und dem Angebot „Immer so einordnen“.
  - Rechnung als Dokumentansicht, Hilfe-Seite mit Video und häufigen Fragen.
  - Installation direkt aus dem GitHub-Repo.
- **2026-10-09 – Video-Tutorial** (`video/`, HyperFrames 0.8.81):
  - 2:51 Minuten, ohne Ton, mit Texteinblendungen (barrierefrei), 9 Kapitel. Seit 2026-10-09 nachmittags mit Kapitel 7 „Die KI liest Ihre Mails mit“; die KI ist im Vorspann, im Datenschutz-Teil und im Abspann genannt (Benjamin: „merkt man das nicht so wirklich?“).
  - Die Webfassung (11 MB) liegt in `arbeitsassistent/static/hilfe/`, die hohe Qualität
    als Anhang am GitHub-Release.
  - Nach Änderungen an der Oberfläche neu erzeugen:
    `cd video && node aufnahme.mjs && node rendern.mjs --hilfe`.
- **2026-10-09 – Baustein 2: Mails lesen lassen** (siehe unten).
- **Offen:** Test auf dem MacBook mit seinem echten Postfach.

## Baustein 2: Mails lesen, KI informiert (2026-10-09)

Benjamins Vorgaben:
- „Er soll seine Mails lesen lassen können, und die sollen das Rechnungsschreiben
  nicht beeinflussen, aber informieren, wenn es nötig ist.“
- Der Kalender wird erstmal nicht synchronisiert.

Umsetzung:
- **Postfach:** IMAP nur lesend.
  - Voreinstellungen für die gängigen deutschen Anbieter.
  - Outlook/Hotmail ist nicht möglich, weil Microsoft dafür OAuth verlangt.
  - Das Passwort liegt im Schlüsselbund, abgerufen wird alle 15 Minuten im Hintergrund.
- **KI:** `gemma4:12b` über Ollama auf `127.0.0.1`. Ollama wird über den eigenen
  LaunchAgent `de.arbeitsassistent.ollama` gestartet, als Kommandozeilen-Fassung aus
  `ollama-darwin.tgz`, ohne App.
  - Die KI liefert je Mail eine `Auswertung` (Art, Einsatztag, Dauer, Aktenzeichen,
    Rechnungsnummer, Frist).
- **Hinweise:** `hinweise.py` macht daraus nach festen Regeln Hinweise:
  - Einsatz abrechnen: erst ab dem Einsatztag, nicht wenn schon abgerechnet, Anfrage
    und Bestätigung ergeben nur einen.
  - Absage mit Hinweis auf ein mögliches Ausfallhonorar.
  - Rückfrage bei der Rechnung.
  - Zahlungsankündigung, steht nur bei der Rechnung.
  - Frist.
- **Kunde finden:** zuerst über die Absenderadresse, dann über einen eindeutig passenden
  Namen.
- **Rechnung finden:** über den Nummern-Erkenner aus dem Bankabgleich.
- **„Rechnung vorbereiten“:** füllt einen Entwurf vor (Kunde, Datum, Dauer, Aktenzeichen,
  passende Leistungsvorlage). Er prüft und speichert selbst.
- **Im Kalender** erscheinen Einsätze aus Mails, die noch nicht abgerechnet sind, auch
  künftige. Das ist nur eine Anzeige, keine Synchronisierung.
- **TLS:** `imaplib` prüft ohne eigenen Kontext weder Zertifikat noch Servernamen. `mail.py`
  übergibt deshalb immer `truststore.SSLContext`, also den Vertrauensspeicher des Systems.
  Das ist mit einem Test abgesichert.
- **KI-Prompt** (`ki.py`):
  - Die Mail-Arten sind dort genau definiert, z. B. Terminverschiebung = `auftrag` mit
    neuem Datum, Bewilligung für einen Zeitraum = `sonstiges` mit Aktenzeichen,
    Newsletter des Berufsverbands = keine Arbeit.
  - Der Prompt enthält eine Wochentabelle von −30 bis +90 Tagen. Die Sendezeit steht bewusst
    nicht im Prompt, sonst hält das Modell sie für den Einsatzbeginn.
- **Schutzregeln nach der KI:**
  - Rechnungsnummern, Aktenzeichen, Daten und Uhrzeiten bleiben nur, wenn sie wirklich
    in der Mail stehen.
  - Bei Rückfrage und Zahlung gibt es keine Einsatzangaben.
  - Verdächtiges gilt nie als Arbeit.
- **Messung:** `scripts/ki_pruefen.py` mit 47 erfundenen Mails in `evals/mails.json`.
  - `gemma4:12b`: 316/316 Punkte, 2,7 s pro Mail auf der RTX 4080. Auf dem M1 Pro
    geschätzt 15–25 s.
  - `qwen3.5:9b`: 289/316, hat u. a. eine Phishing-Mail als Rückfrage eingestuft.
  - Echtlauf am 2026-10-09 mit den 7 Demo-Mails: alle richtig, 5 erwartete Hinweise,
    21 s.
- **Ollama 0.34:** Muster mit `\d` im JSON-Schema werden ignoriert, deshalb überall
  `[0-9]`. Am 2026-10-09 nachgeprüft, als Wissens-Vorschlag eingereicht.
- **Wünsche für später:**
  - mehrere Rechnungsnummern je Mail,
  - Terminreihen (mehrere Einsatztage),
  - `einsatz_ende` zusätzlich zur Dauer,
  - Zuordnung über In-Reply-To.
- **Installer:**
  - Erst ist das Programm nutzbar, dann lädt die KI (etwa 8 GB). `--ohne-ki` überspringt sie.
  - `uv` nutzt nur sein eigenes Python (`UV_PYTHON_PREFERENCE=only-managed`), damit der
    macOS-Platzhalter `python3` nicht den Download der Apple-Entwicklerwerkzeuge auslöst.
    Das ist am 2026-10-09 beim ersten Installieren aufgefallen, die Ursache ist vermutet,
    nicht belegt.
  - Nach `launchctl bootout` braucht macOS einen Moment. Sofortiges `bootstrap` scheitert mit
    „Bootstrap failed: 5: Input/output error“. Das traf jedes Update, bei dem der Dienst schon
    lief (zuerst gesehen am 2026-10-09 auf dem MacBook). `dienst_laden` im Installer wartet
    deshalb, bis der Dienst weg ist, und versucht es bis zu dreimal.

## Rechner: Benjamins MacBook Pro 2021 (M1 Pro, 16 GB) geht an den Kunden

- Seine Daten bleiben bei ihm, ein Server bei Benjamin kommt nicht in Frage.
- Verworfen:
  - Sein alter Huawei-Laptop (Radeon Vega 8, 8 GB RAM): Dort passt nur Gemma 4 E2B,
    geschätzt 30–60 s pro Mail.
  - Ein neuer PC mit 16-GB-Grafikkarte: 1.200–1.700 € (Oktober 2026, Speicherkrise).
- **Vor der Übergabe den Mac komplett löschen** („Alle Inhalte und Einstellungen
  löschen“). Darauf liegen Benjamins Daten, der SSH-Schlüssel von MeinPC und Tailscale
  (`meinmac`). Danach richtet der Kunde sein eigenes Konto mit FileVault ein, und
  Time Machine sichert auf eine verschlüsselte USB-Platte.
- **Seit 2026-10-10 wird auf dem Mac mit echten Daten getestet.** Agenten lesen von dort
  nichts mehr aus (Regel in `AGENTS.md`). Vor dem Löschen deshalb erst den Ordner
  `~/Arbeits-Assistent` sichern (USB-Platte, nicht über MeinPC) und im neuen Konto
  zurückspielen, dann den Installer erneut ausführen. Bis zur Übergabe hat MeinPC noch
  SSH-Zugang; wer ihn vorher schließen will: Systemeinstellungen → Allgemein → Teilen →
  „Entfernte Anmeldung“ aus.

## Verteilung über GitHub

- Bis 2026-10-17 privates Repo `TheQuidOne/arbeits-assistent`, seitdem öffentlich als
  `TheQuidOne/bueroassistenz` (siehe „Name und Veröffentlichung“).
- **Installation auf dem Mac:**
  1. Bei GitHub anmelden und das ZIP laden.
  2. `bash ~/Downloads/arbeits-assistent-main/installieren.sh` ausführen.
  3. Das Skript entfernt die Quarantäne-Markierung, holt `uv` per `curl` (curl setzt keine
     Quarantäne) und installiert mit `uv tool install` samt Python 3.13.
  4. Danach folgen LaunchAgent `de.arbeitsassistent` (Port 8770) und ein Programmsymbol
     per `osacompile`. Das liegt unter „Programme“ (`/Applications`, für Administratoren ohne
     Passwort beschreibbar), sonst in `~/Applications`. Bis 2026-10-09 lag es immer in
     `~/Applications`, Benjamin wollte es aber im Hauptordner „Programme“.
- Bei einem öffentlichen Repo ginge es ohne ZIP, mit dem Einzeiler
  `curl -fsSL https://raw.githubusercontent.com/TheQuidOne/arbeits-assistent/main/installieren.sh | bash`.
  Das Skript lädt sich den Quellstand dann selbst.
- `Installieren.command` (Doppelklick) kann an Gatekeeper scheitern. Unter macOS 15 hilft
  dann nur Systemeinstellungen → Datenschutz & Sicherheit → „Dennoch öffnen“. Deshalb ist
  der Terminal-Befehl der Hauptweg.

## Daten und Sicherheit

- Datenordner `~/Arbeits-Assistent`: `daten.db`, `rechnungen/JJJJ/*.pdf`, `importe/`
  (Original-CSVs als Beleg). Bewusst nicht `~/Documents`, weil macOS den Ordner auf Wunsch
  mit iCloud synchronisiert.
- Die Seite läuft nur auf `127.0.0.1`. Andere Hostnamen werden abgewiesen (Schutz gegen
  DNS-Rebinding), und jedes Formular braucht einen Sitzungsschlüssel (CSRF).
- Ausgestellte Rechnungen und ihre PDFs werden nie verändert oder gelöscht (SHA-256 in
  der Datenbank). Korrekturen per Rechnungskorrektur oder Storno, siehe unten.
- Beträge sind überall ganze Cent, Mengen sind `Decimal`.
- Die Rechnungsschrift ist Noto Sans (SIL Open Font License 1.1, siehe
  `arbeitsassistent/schriften/OFL.txt`).
- Der Girocode auf Rechnungen nutzt EPC Version 002 in UTF-8 und erscheint nur bei
  gültiger IBAN. Geprüft wurde er durch Zurücklesen mit `zbarimg`.

## E-Rechnung und Rechnungskorrektur (2026-10-09)

**E-Rechnung.** Jedes ausgestellte PDF (Rechnung, Storno, Korrektur) ist ZUGFeRD 2 /
Factur-X im Profil EN 16931: PDF/A-3 mit eingebetteter `factur-x.xml` (CII). Entwürfe und
Erinnerungen bleiben einfache PDFs.

- Pflicht zum Ausstellen gilt ab 2027 (Vorjahresumsatz über 800.000 €) bzw. 2028 für alle
  B2B-Rechnungen. Ausgenommen sind u. a. steuerfreie Leistungen nach § 4 Nr. 8–29 UStG
  (dazu § 4 Nr. 16), Kleinunternehmer, Beträge bis 250 € und Privatkunden. Für ihn also
  meist nicht nötig, schadet aber nicht und ist für 19-%-Aufträge an Firmen vorbereitet.
- Umsetzung: `zugferd.py` mit `drafthorse` (XML gegen das Schema geprüft, Einbetten in
  PDF/A-3). fpdf2 setzt dafür ein sRGB-Farbprofil (OutputIntent) in jedes PDF.
- Steuerkategorie `S` (19 %) oder `E` (steuerfrei/§ 19, Begründung = Steuerhinweis).
  Ohne USt-IdNr. dient die Steuernummer als Verkäufer-Kennung (Regel BR-CO-26).
- Einheiten nach UN/ECE Rec. 20: Std. → HUR, km → KMT, Tag → DAY, sonst C62.
- Storno: Mengen negativ, Preise positiv (EN 16931 verbietet negative Preise).
  Storno und Korrektur tragen Typ 384 und den Bezug auf die Vorgängerrechnung.
- Geprüft am 2026-10-09 mit Mustang 2.26 (Schematron EN 16931 + veraPDF PDF/A-3) für
  steuerfrei, Kleinunternehmer, 19 %, Storno und Korrektur mit Vorauszahlung: alle gültig.
  Nicht im Repo, weil Java nötig ist. `tests/test_zugferd.py` prüft die Inhalte.

**Rechnungskorrektur** (§ 31 Abs. 5 UStDV). Eine Rechnung lässt sich durch ein Dokument
berichtigen, das spezifisch und eindeutig auf sie verweist.

- „Korrigieren → Rechnung berichtigen“ öffnet das Formular mit allen Angaben der alten
  Rechnung. Ausstellen erzeugt eine **Rechnungskorrektur** mit neuer Nummer und dem Satz
  „Diese Rechnungskorrektur berichtigt und ersetzt meine Rechnung Nr. … vom …“.
- Die alte Rechnung und ihr PDF bleiben unverändert (GoBD), Status `berichtigt`. Spalte
  `rechnungen.ersetzt` zeigt auf sie. Ältere Datenbanken bekommen die Spalte beim Start
  (`db.NACHTRAEGE`).
- Zahlungen auf die alte Rechnung wandern auf die Korrektur. Die Korrektur zeigt sie als
  „bereits gezahlt“, Zahlungstext und QR-Code gelten nur dem Rest. Offene Hinweise zur
  alten Rechnung werden erledigt.
- Storno bleibt für „Rechnung entfällt ganz“, nur solange nichts gezahlt ist.
- Bewusst keine Korrektur einer Korrektur in einem Schritt nötig: Eine Korrektur ist eine
  normale ausgestellte Rechnung und lässt sich ihrerseits wieder berichtigen.

## Rechnungsnummer, IK und Zusatztext (2026-10-10)

Wunsch des Kunden: sein bisheriges Format `JJJJ-MM-NNN` (z. B. `2026-09-147`) mit einer
einmal frei gewählten Startzahl, sein IK auf der Rechnung und ein fester Satz zur
Grundlage des Honorars (JVEG).

- Format in den Einstellungen mit Platzhaltern: `JJJJ` Jahr, `MM` Monat, `NNN` laufende
  Nummer (Anzahl N = Mindeststellen). Dazu „Laufende Nummer beginnt neu“: jedes Jahr,
  jeden Monat oder nie. Standard bleibt `JJJJ-NNN`, jedes Jahr neu.
- Gezählt wird ab der höchsten vorhandenen Nummer desselben Formats im Zeitraum, nicht
  über einen gespeicherten Zähler. Die Startnummer gilt nur, solange es noch keine
  Rechnung in diesem Format gibt. Sonst würde sie bei jährlichem Neubeginn jedes Jahr
  wieder greifen. Ein Formatwechsel fängt also wieder bei der Startnummer an.
- Formate, die doppelte Nummern erlauben würden, werden abgelehnt (z. B. jährlicher
  Neubeginn ohne `JJJJ`). Erlaubt sind Buchstaben, Ziffern und `- _ .`, weil die Nummer
  auch Dateiname ist. Der Jahresordner der PDFs kommt aus dem Rechnungsdatum.
- Bankabgleich und Mail-Hinweise erkennen jedes Format (`abgleich.nummer_genannt`,
  Trenner egal).
- IK: 9 Ziffern, Leerzeichen werden entfernt. Steht im Infoblock und in der Fußzeile, in
  der E-Rechnung als Verkäufer-Kennung (BT-29). Keine Prüfziffernkontrolle, damit eine
  echte Nummer nie abgewiesen wird.
- Zusatztext: Vorgabe in den Einstellungen, wird bei jeder neuen Rechnung ins Feld
  „Zusatztext unter dem Betrag“ übernommen und mit der Rechnung gespeichert (Spalte
  `zusatztext`). So behält eine spätere Korrektur den damals gültigen Text, und er lässt
  sich pro Rechnung ändern oder entfernen. Nicht auf Stornos und Erinnerungen. In der
  E-Rechnung als Hinweis (IncludedNote).
- Geprüft mit Mustang: Rechnung mit IK und Zusatztext gültig (PDF/A-3, EN 16931).

## Zahlungsziel in Werktagen, Dateiname der PDFs (2026-10-18)

- **Zahlungsziel** zählt standardmäßig in Werktagen: Montag bis Freitag, ohne die
  Feiertage des gewählten Bundeslands (Einstellungen, Standard: nur bundesweite). Umstellbar
  auf Kalendertage. In beiden Fällen rückt eine Fälligkeit am Wochenende oder Feiertag auf
  den nächsten Werktag (wie § 193 BGB). Gilt für Ausstellen, Nachtrag, Vorschau und die
  neue Frist in der Zahlungserinnerung. Schon ausgestellte Rechnungen behalten ihr Datum.
- Feiertage aus dem Paket `holidays` (offline, je Bundesland), Code in `fristen.py`.
  „Werktage“ meint hier Montag bis Freitag, wie es im Alltag verstanden wird; rechtlich
  zählt auch der Samstag als Werktag.
- **Dateiname** der gespeicherten PDFs in den Einstellungen (`dateiname`, Standard
  `{nummer}`). Platzhalter `{nummer}`, `{kunde}`, `{datum}`, `{jahr}`, `{monat}`, `{art}`.
  `{nummer}` ist Pflicht, damit jede Rechnung eine eigene Datei bekommt. Unsichere
  Zeichen werden ersetzt. Ein schon vorhandener Name bekommt „ (2)“ angehängt, es wird nie
  überschrieben. Erinnerungen und Originale von Nachträgen folgen demselben Muster. Beim
  Herunterladen heißt die Datei wie gespeichert.

## Sortieren, QR-Code abschaltbar, Rechnungsnummer frei wählbar (2026-10-18)

- **Rechnungsliste** nach Nummer, Kunde, Datum, Fällig, Status oder Betrag sortierbar
  (Spaltenkopf anklicken, erneut für die andere Richtung). Nummern sortieren nach Zahlen,
  also „2026-9-…“ vor „2026-10-…“. Ohne Auswahl bleibt die alte Reihenfolge.
- **QR-Code** auf Rechnungen und Erinnerungen in den Einstellungen abschaltbar
  (`girocode`, Standard an). Der Zahlungshinweis mit IBAN bleibt.
- **Rechnungsnummer:** Gewünscht war, ausgestellte Rechnungen löschen und ihre Nummer wieder
  freigeben zu können, oder die Nummer selbst einzutragen, mit einem Vorschlag des Programms.
  Umgesetzt ist der zweite Weg: Beim Ausstellen und bei der Rechnungskorrektur steht der
  Vorschlag (`naechste_nummer`) in einem Feld und lässt sich überschreiben. Geprüft wird nur,
  dass die Nummer nicht leer, höchstens 40 Zeichen lang und noch nicht vergeben ist
  (`rechnungen.nummer_pruefen`). Das Storno nimmt weiter den Vorschlag.
- **Kunden löschen** (auf der Seite des Kunden): mit seinen Entwürfen, solange es keine
  ausgestellte Rechnung für ihn gibt. Mail-Hinweise verlieren nur die Zuordnung. Das stand
  in der Sprachnachricht vom 2026-10-17 und war zuerst übersehen worden.
- **Ausgestellte Rechnung löschen** (Benjamins Entscheidung am 2026-10-18, auf Wunsch des
  Kunden): für Rechnungen, die nie verschickt wurden, vor allem Testrechnungen, deren Nummern
  sonst für echte Rechnungen fehlen. Die freie Nummernwahl allein löst das nicht.
  - Nur ohne Zahlungen, ohne Storno und ohne Korrektur, bestätigt durch Eintippen der Nummer
    (`rechnungen.rechnung_loeschen`). Ein Storno oder eine Korrektur lässt sich ebenso löschen,
    die Vorgängerin gilt dann wieder. So verschwindet eine Testrechnung samt Storno ganz.
  - Danach ist die Nummer wieder frei. Das PDF wird nicht vernichtet, sondern nach
    `rechnungen/geloescht/` verschoben, und die Tabelle `loeschungen` vermerkt Nummer,
    Kunde, Betrag und Zeitpunkt. Das schützt vor Versehen und erklärt Lücken.
  - Bankvorschläge zu der Rechnung werden verworfen, Mail-Hinweise verlieren die Zuordnung.
  - Der Hinweis im Programm sagt deutlich: Verschickte Rechnungen sind Belege und werden
    storniert oder berichtigt. Für diese Unterscheidung ist der Kunde selbst verantwortlich.

## Name und Veröffentlichung (2026-10-17)

- **Name:** „Büroassistenz“ mit der Unterzeile „Rechnungen, Zahlungen, Mails · lokal mit
  eigener KI“. Benjamin fand „Arbeits-Assistent“ unpassend. „Büroassistenz“ ist deutsch und
  geschlechtsneutral und sagt, dass das Programm assistiert, nicht entscheidet. Statt „LLM“
  steht „KI“, weil der Kunde kein Fachpublikum ist.
- Umbenannt ist nur, was man sieht: App und Programmsymbol (`Büroassistenz.app`), Seitentitel,
  PDF-Metadaten, Doku. Datenordner, LaunchAgents, Schlüsselbund und Paketname bleiben, sonst
  müsste ein Update aus der Ferne echte Daten und das Mail-Passwort umziehen. Der Installer
  entfernt das alte Symbol „Arbeits-Assistent“.
- **Veröffentlichung:** Ein Update aus der Ferne braucht einen Download ohne Anmeldung.
  Benjamin hat entschieden, das Programm öffentlich zu machen, aber ohne seine private
  Mail-Adresse, die in allen bisherigen Commits stand. Deshalb wurde nichts umgeschrieben,
  sondern neu begonnen: Das alte Repo `TheQuidOne/arbeits-assistent` bleibt privat als Archiv
  mit voller Historie, das neue öffentliche Repo `TheQuidOne/bueroassistenz` beginnt mit dem
  Stand vom 2026-10-17 und Benjamins GitHub-noreply-Adresse.
- Vor dem Umschalten wurde die ganze Historie geprüft: keine Zugangsdaten, keine
  IP-Adressen, keine echten Kundendaten. Die Bilder zeigen nur Demodaten.
- Installieren und Aktualisieren: `curl -fsSL https://raw.githubusercontent.com/TheQuidOne/bueroassistenz/main/installieren.sh | bash`.
- Das Video-Tutorial zeigt noch den alten Namen. Bei der nächsten Aufnahme erneuern.

## Schluss, Grußformel, alte Rechnungen, Update aus der Ferne (2026-10-17)

**Schlusstext und Grußformel** stehen in den Einstellungen (`schluss`, `grussformel`,
Vorgaben „Vielen Dank für die Zusammenarbeit.“ und „Mit freundlichen Grüßen“). Ein eigener
Schlusstext einer Rechnung geht vor, eine leere Grußformel heißt: keine. Die
Zahlungserinnerung nutzt dieselbe Grußformel.

**Alte Rechnungen nachtragen** (Rechnungen → „Alte Rechnung nachtragen“): Rechnungen aus
der Zeit vor dem Programm mit ihrer echten Nummer.

- Gespeichert als ausgestellte Rechnung mit `nachgetragen = 1` und einer Position über den
  Nettobetrag (bei 19 % aus dem Brutto zurückgerechnet). Sie zählen damit bei offenen
  Posten, Bankabgleich, Mail-Hinweisen, Einnahmen und Jahresübersicht. „Schon bezahlt am“
  bucht die Zahlung gleich mit.
- Ein PDF erzeugt das Programm für sie nie. „Original öffnen“ zeigt nur ein hochgeladenes
  Original (`rechnungen/JJJJ/<nummer>_original.pdf`).
- Nachträge im eigenen Nummernformat lassen die nächste Nummer danach weiterzählen.
- Ein Nachtrag lässt sich wieder entfernen (Tippfehler), solange es keine Zahlungen,
  kein Storno und keine Korrektur dazu gibt. Er ist eine Abschrift, nicht der Beleg selbst.
- **Auslesen aus dem PDF** (`nachtrag.py`): erst eine eingebettete E-Rechnung (exakt, ohne
  KI), sonst der Text (PDF aus Word) mit Gemma, sonst die eingescannte Seite als Bild mit
  Gemma (Vision). Werte aus dem Text gelten nur, wenn sie im Dokument stehen (Nummer,
  Betrag, Daten). Den Steuerfall erkennen feste Regeln am Steuerhinweis. Kunde: gleicher
  Name oder genau ein bekannter Kundenname im Dokument. Die KI füllt nur das Formular vor.
- Probe am 2026-10-17 auf MeinPC mit einer erfundenen Rechnung: E-Rechnung sofort,
  Text-PDF und Scan je etwa 3 s, alle Felder richtig. Auf dem M1 Pro deutlich langsamer.

**Update aus der Ferne.** Seit 2026-10-17 steht der Mac woanders, ohne LAN und ohne
Tailscale. Aktualisiert wird am Mac mit einem Befehl, der `installieren.sh` von GitHub holt
(README, „Aktualisieren“). Ist das Repo privat, liest der Installer einen Lese-Schlüssel aus
dem Schlüsselbund (Dienst „Arbeits-Assistent GitHub“) und lädt über die GitHub-API. Ohne
Schlüssel lädt er das öffentliche Archiv. Getestet am 2026-10-17: mit Schlüssel geht der
Download, ohne Schlüssel antwortet GitHub bei privatem Repo mit 404.

## Schrift der Rechnungen und Einnahmen-Übersicht (2026-10-14)

**Schrift.** Wunsch: „so wie sie im Moment ist, oder Arial“. Mehr Auswahl war ausdrücklich
nicht gewünscht.

- Noto Sans liegt bei (OFL). Arial darf nicht mitgeliefert werden (Monotype-Lizenz). Sie
  kommt deshalb vom System (`pdf._ARIAL`): auf dem Mac
  `/System/Library/Fonts/Supplemental/Arial.ttf` und `Arial Bold.ttf`, die es dort immer
  gibt. Eingebettet wird sie als Teilmenge wie bei Word.
- Fehlt Arial, nehmen die PDFs Noto Sans, und die Einstellungen sagen das. Auf dem
  Linux-Entwicklungsrechner steht Liberation Sans für Arial (gleiche Maße). Damit laufen
  Tests und Mustang-Prüfung, am 2026-10-14 gültig.
- „Probe ansehen“ erzeugt eine Musterrechnung mit erfundenem Kunden in der gerade
  ausgewählten Schrift, ohne zu speichern.

**Einnahmen im Reiter Konto.** Wunsch: „wie viel ich das Jahr über eingenommen habe,
insgesamt“, und differenzierter.

- Gezählt wird wie in der Auswertung (`auswertung.jahr`): Zahlungen auf Rechnungen und als
  „Einnahme“ eingeordnete Buchungen, am Tag des Zahlungseingangs. Privates zählt nicht.
- Im laufenden Jahr vergleicht der Vorjahreswert den gleichen Zeitraum (1. Januar bis
  heute vor einem Jahr), sonst wäre der Vergleich immer schlecht.
- Noch nicht eingeordnete Zahlungseingänge des Jahres werden genannt, weil sie in der
  Summe fehlen.
- Monatssäulen: eine Reihe in der Akzentfarbe, Werte im Tooltip, am stärksten Monat direkt
  beschriftet, dazu die Tabelle zum Aufklappen.

## Mac wach halten (2026-10-10)

Auf Benjamins Wunsch („stell ein, dass er nicht ausgeht“), damit die KI auch liest, wenn
niemand am Mac sitzt. Ab Werk schlief der Mac nach 1 Minute ein (`pmset`: sleep 1 auf Akku
und am Netzteil), damit stand jeder Abruf still.

- Eigener Hintergrunddienst `de.arbeitsassistent.wachhalten` (LaunchAgent, `caffeinate -i -s
  -m`), von Hand per SSH angelegt, startet nach jedem Anmelden wieder. Kein
  Administrator-Passwort nötig, keine Systemeinstellung geändert. `pmset` hätte `sudo`
  gebraucht.
- Grenzen: Zugeklappt schläft ein MacBook trotzdem. Auf Akku leert sich der Akku, deshalb
  am Netzteil lassen. Der Bildschirm geht weiter nach 2 bzw. 10 Minuten aus, der Mac
  arbeitet trotzdem.
- Entfernen: `launchctl bootout gui/$(id -u)/de.arbeitsassistent.wachhalten` und
  `~/Library/LaunchAgents/de.arbeitsassistent.wachhalten.plist` löschen.
  `installieren.sh --entfernen` räumt ihn mit auf.
- Nicht im Installer: Ob der Kunde das auf seinem Laptop will, entscheidet er. Idee für
  später: Schalter in den Einstellungen, oder nur während eines Lesedurchgangs wach halten.

## Sichtbarer KI-Fortschritt (2026-10-10)

Rückmeldung vom ersten echten Postfach (Posteo): Nach „Verbindung klappt“ sah die
Post-Seite leer aus, ein Fortschritt war nicht zu erkennen.

- Ursachen: Mails, die die KI noch liest, standen nur unter „Alle“, angezeigt wird aber
  „Arbeit“. Der Stand war eine kleine graue Zeile. Und ein Lauf las höchstens 40 Mails,
  der Rest wartete 15 Minuten auf den nächsten.
- Jetzt liest ein Lauf alle wartenden Mails am Stück, in zeitlicher Reihenfolge. Der
  Stand liegt im Speicher (`postfach.fortschritt()`), `/post/stand` liefert ihn als JSON.
- Post-Seite: Karte mit „Die KI liest Mail x von y“, Balken und Restzeit. Bis die erste
  Mail fertig ist, gilt eine Schätzung von 20 s pro Mail (M1 Pro, `SEKUNDEN_JE_MAIL`),
  danach das gemessene Tempo. Abfrage alle 2 s, im Ruhezustand alle 10 s. Am Ende lädt die
  Seite neu und zeigt die Ergebnisse. Wartende Mails stehen mit „wird gelesen“ unter
  „Arbeit“. Pulsierender Punkt bei „Post“ in der Navigation, Zeile auf der Übersicht.
- Echtlauf am 2026-10-10 auf MeinPC (RTX 4080) mit 12 erfundenen Mails aus
  `evals/mails.json`: etwa 5 s pro Mail, Neuladen am Ende geprüft. Auf dem M1 Pro ist mit
  15–25 s pro Mail zu rechnen.
- Ein Update startet das Programm neu. Ein laufender Durchgang bricht ab, abgerufene und
  gelesene Mails bleiben gespeichert, und die KI macht nach dem Start mit den restlichen
  weiter. Nur die Mail, die gerade in Arbeit war, wird noch einmal gelesen.

## Unterschrift und Leistungsauswahl (2026-10-10)

**Unterschrift.** In den Einstellungen ein Foto oder einen Scan hochladen (PNG, JPG,
TIFF, PDF mit eingescanntem Bild; HEIC vom iPhone auf dem Mac über das eingebaute
`sips`).

- `unterschrift.py` bereitet auf: Die Papierhelligkeit wird örtlich geschätzt (Maximum
  der Umgebung auf verkleinertem Bild), weil Handyfotos ungleichmäßig ausgeleuchtet sind.
  Ein fester Schwellwert ließ Schatten als graue Fläche stehen. Tinte behält ihre Farbe,
  das Papier wird rein weiß, das Bild wird eng zugeschnitten und als PNG ohne Transparenz
  gespeichert. Transparenz wäre in PDF/A-3 zwar erlaubt, ist aber unnötig.
- Die Datei liegt nur im Datenordner (`unterschrift.png`), nie in der Datenbank oder im
  Repo. Sie erscheint auf Rechnung, Korrektur, Storno und Erinnerung, etwa 15 mm hoch,
  über dem Namen. Ausgestellte PDFs bleiben, wie sie sind.
- Geprüft mit Mustang: Rechnung mit Unterschrift gültig.

**Leistungsauswahl.** Die Vorschlagsliste (`datalist`) am Textfeld war zu unauffällig,
gerade in Safari. Jetzt hat jede Positionszeile eine Auswahl „Leistung wählen …“ mit den
Leistungen aus den Einstellungen. Sie füllt Text, Einheit und Preis. „Andere Leistung
(selbst eintragen)“ zeigt das freie Textfeld. Darunter steht ein optionales Feld
„Erläuterung“ (Spalte `positionen.erlaeuterung`), etwa der Ausschuss. Im PDF steht sie
klein und grau unter der Leistung, in der E-Rechnung als Artikelbeschreibung (BT-154).
Das Storno übernimmt sie.

- Das Video-Tutorial zeigt noch das alte Formular (freies Textfeld). Bei der nächsten
  Aufnahme: `data-tour="position-vorlage"` ist die neue Auswahl.

## Gestaltung (2026-10-09)

Benjamins Auftrag: „so professionell und einfach wie möglich zu bedienen“, für einen
gut zahlenden Kunden.

- Eine Akzentfarbe, Systemschrift (SF Pro auf dem Mac), heller und dunkler Modus nach
  Systemeinstellung.
- Symbole liegen als SVG in `templates/_symbole.html`, alles lokal, keine CDN.
- Die Startseite sagt, was zu tun ist. Jede Aufgabe hat genau einen Knopf.
- Beim ersten Start führt sie in fünf Schritten durch die Einrichtung.
- Seltenes ist eingeklappt (Umsatzsteuer, Zahlungsziel, Texte, Hinweistexte).
- `data-tour="…"`-Attribute markieren die Elemente, die das Video-Tutorial ansteuert.
  Nicht entfernen.

## Bank: nur CSV

Benjamin hat entschieden: Bank egal, nur CSV. FinTS und Open-Banking-Dienste sind damit
vom Tisch. Der Import erkennt die Formate automatisch, Kodierung und Trennzeichen
ebenfalls. Vorgemerkte Umsätze werden übersprungen, Duplikate über einen Fingerabdruck
erkannt (siehe `arbeitsassistent/bank.py`).

## Steuerliche Einordnung (bitte einmal mit Steuerberatung oder Finanzamt bestätigen)

- Dolmetscher sind **Freiberufler** (§ 18 EStG): kein Gewerbe, keine Gewerbeanmeldung,
  keine Gewerbesteuer, Gewinn per Einnahmen-Überschuss-Rechnung.
- Umsatzsteuer gibt es in drei Fällen, wählbar pro Kunde oder Rechnung:
  - **steuerfrei nach § 4 Nr. 16 Buchst. l UStG:** Leistungen von
    Gebärdensprachdolmetschern, z. B. in der Eingliederungshilfe oder für das
    Integrationsamt (BMF, siehe Haufe „Leistungen von Gebärdensprachdolmetschern“).
  - **Kleinunternehmer (§ 19 UStG):** Vorjahresumsatz bis 25.000 €, laufendes Jahr bis
    100.000 €.
  - **19 %**, z. B. für Firmen oder Gerichte, wenn keine Befreiung greift.
- Standard ist „steuerfrei“, die Hinweistexte sind in den Einstellungen änderbar.

## KI

`gemma4:12b` über Ollama, wie in `~/Projects/assistant` (57/59 Punkte, 7,8 GB). Sie passt
auf den M1 Pro und kommt erst mit Baustein 2. **Was zuverlässig sein muss (Zahlungen,
Fristen, Zuordnung), läuft ohne KI.**

## Offene Fragen

- Steuerfälle einmal bestätigen lassen: Welche Aufträge sind steuerfrei, ist er
  Kleinunternehmer?
- Verlangt ein Kostenträger ausdrücklich XRechnung (reines XML, manche Behörden)? Das
  Programm erzeugt ZUGFeRD (EN 16931). XRechnung bräuchte u. a. eine Leitweg-ID je Kunde.
- Baustein 2:
  - Welcher Mail-Anbieter, gibt es eine eigene Arbeitsadresse?
  - Welcher Kalender, und sollen Termine auch aufs Handy (dort nur neutrale Titel)?
- Seine bisherige Word-Rechnung als Vorlage ansehen, damit das PDF vertraut aussieht
  (Logo?).
- Bisher nur mit erfundenen Daten getestet, noch nicht auf dem Mac selbst.

## Ideen für danach

- Zahler-IBAN je Kunde merken, sobald eine Zahlung bestätigt ist. Behörden zahlen oft
  unter anderem Namen („Landeskasse“), dann passt der Namensvergleich nicht.
- Mehrere Konten: ein Kontofeld im Umsatz, damit gleiche Zeilen verschiedener Konten nicht
  zusammenfallen.
- Bankformate:
  - Volksbank und Commerzbank haben keine Statusspalte. Falls sie vorgemerkte Umsätze
    exportieren, würden diese mit eingelesen.
  - Nicht unterstützt: Targobank (ohne Kopfzeile), Kontist alt (Cent-Beträge), Holvi
    (nur XLSX), Deutsche Bank englisch.
- Bei der Sparkasse (MT940) im Verwendungszweck nur den Teil hinter `SVWZ+` zeigen.
- Aktualisieren aus dem Programm heraus (heute: `installieren.sh` erneut ausführen).
