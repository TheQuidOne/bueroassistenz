"""Die Oberfläche: eine lokale Webseite, nur auf diesem Rechner erreichbar."""

import json
import re
import secrets
import subprocess
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from markupsafe import Markup
from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, session,
                   url_for)

from . import UNTERZEILE, auswertung, hinweise, nachtrag, postfach, rechnungen, umsaetze, unterschrift
from . import db as dbmod
from . import pdf as pdfmod
from .geld import cent_aus_text, euro, menge_text
from .modell import MAILARTEN, STEUERFAELLE, Position, RechnungsDruck
from .rechnungen import Fehler, tag

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
          "Oktober", "November", "Dezember"]
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _geheimnis(ordner: Path) -> str:
    datei = ordner / ".geheim"
    if not datei.exists():
        datei.write_text(secrets.token_hex(32))
        datei.chmod(0o600)
    return datei.read_text().strip()


def _datum(wert) -> str:
    if not wert:
        return ""
    if isinstance(wert, str):
        wert = tag(wert[:10])
    return wert.strftime("%d.%m.%Y")


def _datum_lang(wert: date) -> str:
    return f"{WOCHENTAGE[wert.weekday()]}, {wert.day}. {MONATE[wert.month - 1]} {wert.year}"


def _symbol(name: str, klasse: str = "") -> Markup:
    return Markup(f'<svg class="ic {klasse}" aria-hidden="true"><use href="#i-{name}"/></svg>')


def erstellen(ordner: Path | None = None) -> Flask:
    app = Flask(__name__)
    ordner = ordner or dbmod.datenordner()
    app.config["ORDNER"] = ordner
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
    app.secret_key = _geheimnis(ordner)
    app.jinja_env.filters.update(euro=euro, datum=_datum, datum_lang=_datum_lang, menge=menge_text)
    app.jinja_env.globals["symbol"] = _symbol

    def verbindung():
        if "db" not in g:
            g.db = dbmod.verbinden(ordner)
        return g.db

    @app.teardown_appcontext
    def schliessen(_fehler):
        verbindung_ = g.pop("db", None)
        if verbindung_ is not None:
            verbindung_.close()

    @app.before_request
    def schutz():
        # Nur Aufrufe an 127.0.0.1/localhost (gegen DNS-Rebinding) und nur Formulare
        # mit eigenem Schlüssel (gegen fremde Webseiten, die hierher Formulare schicken).
        if request.host.rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
            abort(400)
        if request.method == "POST":
            erwartet = session.get("csrf")
            if not erwartet or not secrets.compare_digest(request.form.get("csrf", ""), erwartet):
                abort(400)

    @app.context_processor
    def ueberall():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf": session["csrf"], "heute": date.today(), "STEUERFAELLE": STEUERFAELLE,
                "KATEGORIEN": umsaetze.KATEGORIEN, "AUSGABEN": umsaetze.AUSGABEN,
                "EINNAHMEN": umsaetze.EINNAHMEN, "MONATE": MONATE, "MAILARTEN": MAILARTEN,
                "UNTERZEILE": UNTERZEILE,
                "zaehler": auswertung.zaehler(verbindung()), "ki_liest": postfach.fortschritt(),
                "merken_angebot": session.pop("merken_angebot", None)}

    def zurueck(standard: str):
        weiter = request.form.get("weiter", "")
        return redirect(weiter if weiter.startswith("/") and not weiter.startswith("//") else standard)

    @app.get("/gesund")
    def gesund():
        return "arbeitsassistent"

    # --- Übersicht ---------------------------------------------------------

    @app.get("/")
    def start():
        db = verbindung()
        einst = dbmod.einstellungen(db)
        seit = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        ki_zeile = dict(db.execute(
            "SELECT COUNT(*) AS gelesen, SUM(arbeit = 1) AS arbeit FROM mails "
            "WHERE status = 'ausgewertet' AND ausgewertet >= ?", (seit,)).fetchone())
        ki_zeile["hinweise"] = db.execute("SELECT COUNT(*) FROM hinweise WHERE status = 'offen'").fetchone()[0]
        ki_zeile["konto"] = postfach.konto(einst) is not None
        return render_template("uebersicht.html", d=auswertung.uebersicht(db), einst=einst, ki_zeile=ki_zeile,
                               aufgaben=auswertung.aufgaben(db), einrichtung=auswertung.einrichtung(db, einst))

    # --- Rechnungen --------------------------------------------------------

    @app.get("/rechnungen")
    def rechnungsliste():
        filter_ = request.args.get("filter", "alle")
        jahr = request.args.get("jahr", type=int)
        sortierung = request.args.get("sort", "")
        if sortierung not in rechnungen.SORTIERUNGEN:
            sortierung = ""
        richtung = request.args.get("richtung") or rechnungen.SORTIERUNGEN.get(sortierung, "auf")
        return render_template("rechnungen.html", filter=filter_, jahr=jahr, sortierung=sortierung,
                               richtung=richtung, standard_richtung=rechnungen.SORTIERUNGEN,
                               liste=rechnungen.liste(verbindung(), filter_, jahr, sortierung=sortierung,
                                                      absteigend=richtung == "ab"))

    def formular_daten() -> dict:
        f = request.form
        positionen = [
            {"datum": d, "beschreibung": b, "menge": m, "einheit": e, "einzelpreis": p}
            for d, b, m, e, p in zip(f.getlist("pos_datum"), f.getlist("pos_beschreibung"),
                                     f.getlist("pos_menge"), f.getlist("pos_einheit"), f.getlist("pos_preis"))
        ]
        for p, erlaeuterung in zip(positionen, f.getlist("pos_erlaeuterung")):
            p["erlaeuterung"] = erlaeuterung
        felder = ("kunde_id", "bezug", "leistung_von", "leistung_bis", "steuerfall", "zahlungsziel",
                  "einleitung", "schluss", "zusatztext", "hinweis_id")
        return {**{k: f.get(k, "") for k in felder}, "positionen": positionen}

    def formular(daten: dict, rechnung_id: int | None = None, berichtigung=None):
        db = verbindung()
        einst = dbmod.einstellungen(db)
        vorschlag = rechnungen.naechste_nummer(db, date.today(), einst) if berichtigung else ""
        return render_template("rechnung_form.html", daten=daten, rechnung_id=rechnung_id, berichtigung=berichtigung,
                               vorschlag_nummer=request.form.get("nummer") or vorschlag,
                               kunden=rechnungen.kunden(db), vorlagen=rechnungen.vorlagen(db), einst=einst)

    def daten_aus_rechnung(db, r) -> dict:
        daten = {k: r[k] or "" for k in ("kunde_id", "bezug", "leistung_von", "leistung_bis", "steuerfall",
                                          "zahlungsziel", "einleitung", "schluss", "zusatztext")}
        daten["positionen"] = [
            {"datum": p.datum.isoformat() if p.datum else "", "beschreibung": p.beschreibung,
             "menge": menge_text(p.menge), "einheit": p.einheit, "einzelpreis": euro(p.einzelpreis, False),
             "erlaeuterung": p.erlaeuterung}
            for p in rechnungen.positionen(db, r["id"])
        ]
        return daten

    @app.get("/rechnungen/neu")
    def rechnung_neu():
        db = verbindung()
        einst = dbmod.einstellungen(db)
        daten = {"kunde_id": request.args.get("kunde", ""), "steuerfall": "", "zahlungsziel": "",
                 "positionen": [{"datum": "", "beschreibung": "", "menge": "1", "einheit": "", "einzelpreis": ""}]}
        hinweis_id = request.args.get("hinweis", type=int)
        if hinweis_id:
            try:
                daten = hinweise.entwurf_daten(db, hinweis_id)
            except Fehler as fehler:
                flash(str(fehler), "fehler")
            else:
                if not daten["kunde_id"]:
                    wer = daten["auftraggeber"] or "die Mail"
                    flash(f"Den Kunden ({wer}) gibt es noch nicht. Bitte auswählen oder neu anlegen.", "hinweis")
                else:
                    flash("Aus einer Mail vorbereitet. Bitte prüfen und ergänzen.", "hinweis")
        daten.setdefault("zusatztext", einst["zusatztext"])  # Vorgabe aus den Einstellungen
        if not rechnungen.kunden(db):
            flash("Bitte zuerst einen Kunden anlegen.", "hinweis")
            return redirect(url_for("kunde_neu", weiter=url_for("rechnung_neu")))
        if not einst["name"]:
            flash("Tipp: Unter Einstellungen die eigenen Angaben eintragen, sie stehen auf jeder Rechnung.",
                  "hinweis")
        return formular(daten)

    @app.post("/rechnungen/neu")
    def rechnung_anlegen():
        db = verbindung()
        daten = formular_daten()
        try:
            rechnung_id = rechnungen.entwurf_speichern(db, daten, dbmod.einstellungen(db))
        except Fehler as fehler:
            flash(str(fehler), "fehler")
            return formular(daten)
        if str(daten.get("hinweis_id", "")).isdigit():
            try:
                hinweise.setzen(db, int(daten["hinweis_id"]), "erledigt")
            except Fehler:
                pass  # Hinweis inzwischen weg, der Entwurf zählt
        flash("Entwurf gespeichert.", "ok")
        return redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))

    @app.get("/rechnungen/<int:rechnung_id>/bearbeiten")
    def rechnung_bearbeiten(rechnung_id):
        db = verbindung()
        r = rechnungen.rechnung(db, rechnung_id)
        if r["status"] != "entwurf":
            flash("Ausgestellte Rechnungen lassen sich nicht mehr ändern.", "fehler")
            return redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))
        return formular(daten_aus_rechnung(db, r), rechnung_id)

    @app.post("/rechnungen/<int:rechnung_id>/bearbeiten")
    def rechnung_aendern(rechnung_id):
        db = verbindung()
        daten = formular_daten()
        try:
            rechnungen.entwurf_speichern(db, daten, dbmod.einstellungen(db), rechnung_id)
        except Fehler as fehler:
            flash(str(fehler), "fehler")
            return formular(daten, rechnung_id)
        flash("Entwurf gespeichert.", "ok")
        return redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))

    @app.route("/rechnungen/<int:rechnung_id>/berichtigen", methods=["GET", "POST"])
    def rechnung_berichtigen(rechnung_id):
        db = verbindung()
        r = rechnungen.rechnung(db, rechnung_id)
        if r["status"] != "ausgestellt" or r["art"] != "rechnung":
            flash("Nur ausgestellte Rechnungen lassen sich berichtigen.", "fehler")
            return redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))
        if request.method == "GET":
            return formular(daten_aus_rechnung(db, r), berichtigung=r)
        daten = formular_daten()
        try:
            neu_id = rechnungen.berichtigen(db, rechnung_id, daten, dbmod.einstellungen(db), ordner=ordner,
                                            nummer=request.form.get("nummer"))
        except Fehler as fehler:
            flash(str(fehler), "fehler")
            return formular(daten, berichtigung=r)
        flash(f"Rechnungskorrektur {rechnungen.rechnung(db, neu_id)['nummer']} ist ausgestellt und ersetzt "
              f"Rechnung {r['nummer']}. Das PDF liegt im Ordner „rechnungen“.", "ok")
        return redirect(url_for("rechnung_ansehen", rechnung_id=neu_id))

    # --- Alte Rechnungen nachtragen ----------------------------------------

    zwischenablage = ordner / "nachtrag"  # hochgeladene PDFs, bis der Nachtrag gespeichert ist

    def zwischen_aufraeumen() -> None:
        if zwischenablage.is_dir():
            grenze = datetime.now().timestamp() - 24 * 3600
            for datei in zwischenablage.glob("*.pdf"):
                if datei.stat().st_mtime < grenze:
                    datei.unlink(missing_ok=True)

    def zwischen_datei(schluessel: str) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{32}", schluessel or ""):
            return None
        datei = zwischenablage / f"{schluessel}.pdf"
        return datei if datei.is_file() else None

    def nachtrag_formular(daten: dict, vorschlag: dict | None = None, schluessel: str = "", dateiname: str = ""):
        db = verbindung()
        return render_template("nachtrag.html", daten=daten, vorschlag=vorschlag or {}, schluessel=schluessel,
                               dateiname=dateiname, kunden=rechnungen.kunden(db), einst=dbmod.einstellungen(db))

    @app.get("/rechnungen/nachtragen")
    def rechnung_nachtragen():
        zwischen_aufraeumen()
        if not rechnungen.kunden(verbindung()):
            flash("Bitte zuerst den Kunden anlegen, an den die Rechnung ging.", "hinweis")
            return redirect(url_for("kunde_neu", weiter=url_for("rechnung_nachtragen")))
        return nachtrag_formular({})

    @app.post("/rechnungen/nachtragen/auslesen")
    def nachtrag_auslesen():
        datei = request.files.get("datei")
        roh = datei.read() if datei and datei.filename else b""
        if not roh.startswith(b"%PDF"):
            flash("Bitte die alte Rechnung als PDF-Datei auswählen.", "fehler")
            return redirect(url_for("rechnung_nachtragen"))
        db = verbindung()
        einst = dbmod.einstellungen(db)
        try:
            v = nachtrag.vorschlag(roh, postfach.ki_fuer(einst), rechnungen.kunden(db), einst.get("name", ""))
        except ValueError as fehler:
            flash(str(fehler), "fehler")
            return redirect(url_for("rechnung_nachtragen"))
        zwischenablage.mkdir(exist_ok=True)
        schluessel = secrets.token_hex(16)
        (zwischenablage / f"{schluessel}.pdf").write_bytes(roh)
        felder = ("nummer", "datum", "leistung_von", "leistung_bis", "faellig", "betrag", "steuerfall",
                  "beschreibung", "bezug", "kunde_id")
        daten = {k: v.get(k) or "" for k in felder}
        return nachtrag_formular(daten, v, schluessel, datei.filename)

    @app.post("/rechnungen/nachtragen")
    def nachtrag_speichern():
        f = request.form
        daten = {k: f.get(k, "") for k in ("kunde_id", "nummer", "datum", "leistung_von", "leistung_bis", "faellig",
                                            "betrag", "steuerfall", "beschreibung", "bezug")}
        if f.get("bezahlt") == "ja":
            daten["bezahlt_am"] = f.get("bezahlt_am", "")
            if not daten["bezahlt_am"]:
                flash("Bitte angeben, wann die Rechnung bezahlt wurde.", "fehler")
                return nachtrag_formular(daten, schluessel=f.get("schluessel", ""), dateiname=f.get("dateiname", ""))
        datei = request.files.get("datei")
        zwischen = zwischen_datei(f.get("schluessel", ""))
        original = zwischen.read_bytes() if zwischen else (datei.read() if datei and datei.filename else None)
        try:
            rid = rechnungen.nachtragen(verbindung(), daten, dbmod.einstellungen(verbindung()), ordner, original)
        except Fehler as fehler:
            flash(str(fehler), "fehler")
            return nachtrag_formular(daten, schluessel=f.get("schluessel", ""), dateiname=f.get("dateiname", ""))
        if zwischen:
            zwischen.unlink(missing_ok=True)
        flash(f"Rechnung {daten['nummer'].strip()} ist nachgetragen.", "ok")
        return redirect(url_for("rechnung_ansehen", rechnung_id=rid))

    @app.post("/rechnungen/<int:rechnung_id>/original")
    def nachtrag_original(rechnung_id):
        datei = request.files.get("datei")
        try:
            rechnungen.original_hinzufuegen(verbindung(), rechnung_id, datei.read() if datei else b"", ordner)
            flash("Original-PDF gespeichert.", "ok")
        except Fehler as fehler:
            flash(str(fehler), "fehler")
        return redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))

    @app.post("/rechnungen/<int:rechnung_id>/nachtrag-loeschen")
    def nachtrag_entfernen(rechnung_id):
        try:
            rechnungen.nachtrag_loeschen(verbindung(), rechnung_id, ordner)
        except Fehler as fehler:
            flash(str(fehler), "fehler")
            return redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))
        flash("Nachgetragene Rechnung entfernt.", "ok")
        return redirect(url_for("rechnungsliste"))

    @app.get("/rechnungen/<int:rechnung_id>")
    def rechnung_ansehen(rechnung_id):
        db = verbindung()
        try:
            r = rechnungen.rechnung(db, rechnung_id)
        except Fehler:
            abort(404)
        rest = rechnungen.offen(db, rechnung_id)
        zustand, zustand_text = rechnungen.zustand(r, rest, date.today())
        storno = db.execute("SELECT id, nummer FROM rechnungen WHERE storno_zu = ?", (rechnung_id,)).fetchone()
        original = rechnungen.rechnung(db, r["storno_zu"]) if r["storno_zu"] else None
        ersetzt_rechnung = rechnungen.rechnung(db, r["ersetzt"]) if r["ersetzt"] else None
        ersetzt_durch = db.execute("SELECT id, nummer FROM rechnungen WHERE ersetzt = ?", (rechnung_id,)).fetchone()
        return render_template(
            "rechnung.html", r=r, kunde=rechnungen.kunde(db, r["kunde_id"]),
            positionen=rechnungen.positionen(db, rechnung_id), zahlungen=rechnungen.zahlungen(db, rechnung_id),
            offen=rest, zustand=zustand, zustand_text=zustand_text, storno=storno, original=original,
            ersetzt_rechnung=ersetzt_rechnung, ersetzt_durch=ersetzt_durch,
            vorschlag_nummer=rechnungen.naechste_nummer(db, date.today(), dbmod.einstellungen(db))
            if r["status"] == "entwurf" else "",
            zeitraum=rechnungen.leistungszeitraum(r, rechnungen.positionen(db, rechnung_id)),
            mail_hinweise=hinweise.zur_rechnung(db, rechnung_id),
            eingaenge=db.execute(
                "SELECT * FROM umsaetze WHERE kategorie = '' AND betrag > 0 ORDER BY datum DESC LIMIT 30"
            ).fetchall(),
        )

    def aktion(rechnung_id: int, ausfuehren, erfolg: str):
        try:
            ergebnis = ausfuehren()
        except Fehler as fehler:
            flash(str(fehler), "fehler")
            return redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))
        flash(erfolg.format(ergebnis=ergebnis), "ok")
        return None

    @app.post("/rechnungen/<int:rechnung_id>/ausstellen")
    def rechnung_ausstellen(rechnung_id):
        db = verbindung()
        antwort = aktion(rechnung_id, lambda: rechnungen.ausstellen(
            db, rechnung_id, dbmod.einstellungen(db), ordner=ordner, nummer=request.form.get("nummer")),
            "Rechnung {ergebnis} ist ausgestellt. Das PDF liegt im Ordner „rechnungen“.")
        return antwort or redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))

    @app.post("/rechnungen/<int:rechnung_id>/loeschen")
    def rechnung_loeschen(rechnung_id):
        antwort = aktion(rechnung_id, lambda: rechnungen.entwurf_loeschen(verbindung(), rechnung_id),
                         "Entwurf gelöscht.")
        return antwort or redirect(url_for("rechnungsliste"))

    @app.post("/rechnungen/<int:rechnung_id>/stornieren")
    def rechnung_stornieren(rechnung_id):
        db = verbindung()
        ergebnis = {}

        def ausfuehren():
            ergebnis["id"] = rechnungen.stornieren(db, rechnung_id, dbmod.einstellungen(db), ordner=ordner)
            return rechnungen.rechnung(db, ergebnis["id"])["nummer"]

        antwort = aktion(rechnung_id, ausfuehren, "Stornorechnung {ergebnis} ist ausgestellt.")
        return antwort or redirect(url_for("rechnung_ansehen", rechnung_id=ergebnis["id"]))

    @app.post("/rechnungen/<int:rechnung_id>/zahlung")
    def zahlung_neu(rechnung_id):
        db = verbindung()

        def ausfuehren():
            try:
                betrag = cent_aus_text(request.form.get("betrag", ""))
            except ValueError:
                raise Fehler("Bitte einen Betrag eingeben.") from None
            if betrag > rechnungen.offen(db, rechnung_id):
                raise Fehler("Der Betrag ist höher als der offene Rest.")
            datum = rechnungen.datum_aus_text(request.form.get("datum")) or date.today()
            rechnungen.zahlung_erfassen(db, rechnung_id, betrag, datum, notiz=request.form.get("notiz", ""))

        antwort = aktion(rechnung_id, ausfuehren, "Zahlung erfasst.")
        return antwort or redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))

    @app.post("/rechnungen/<int:rechnung_id>/eingang")
    def eingang_zuordnen(rechnung_id):
        antwort = aktion(rechnung_id, lambda: umsaetze.zuordnen(
            verbindung(), request.form.get("umsatz_id", type=int) or 0, rechnung_id), "Eingang zugeordnet.")
        return antwort or redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))

    @app.post("/zahlungen/<int:zahlung_id>/loeschen")
    def zahlung_loesen(zahlung_id):
        try:
            rechnungen.zahlung_loeschen(verbindung(), zahlung_id)
            flash("Zahlung gelöst.", "ok")
        except Fehler as fehler:
            flash(str(fehler), "fehler")
        return zurueck(url_for("rechnungsliste"))

    @app.get("/rechnungen/<int:rechnung_id>/pdf")
    def rechnung_pdf(rechnung_id):
        db = verbindung()
        r = rechnungen.rechnung(db, rechnung_id)
        if r["pdf_pfad"]:
            return send_file(ordner / r["pdf_pfad"], mimetype="application/pdf",
                             download_name=f"Rechnung_{rechnungen._dateiname(r['nummer'])}.pdf")
        if r["nachgetragen"]:  # nie ein selbst erzeugtes PDF als „Original“ zeigen
            flash("Zu dieser nachgetragenen Rechnung ist kein Original-PDF hinterlegt.", "hinweis")
            return redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))
        daten = rechnungen.vorschau_pdf(db, rechnung_id, dbmod.einstellungen(db), ordner)
        return Response(daten, mimetype="application/pdf",
                        headers={"Content-Disposition": "inline; filename=Entwurf.pdf"})

    @app.get("/rechnungen/<int:rechnung_id>/erinnerung")
    def rechnung_erinnerung(rechnung_id):
        db = verbindung()
        try:
            name, daten = rechnungen.erinnerung(db, rechnung_id, dbmod.einstellungen(db), ordner=ordner)
        except Fehler as fehler:
            flash(str(fehler), "fehler")
            return redirect(url_for("rechnung_ansehen", rechnung_id=rechnung_id))
        return Response(daten, mimetype="application/pdf", headers={"Content-Disposition": f"inline; filename={name}"})

    # --- Kunden ------------------------------------------------------------

    @app.get("/kunden")
    def kundenliste():
        db = verbindung()
        offen_je_kunde = {}
        for o in rechnungen.offene_rechnungen(db):
            kunde_id = rechnungen.rechnung(db, o.id)["kunde_id"]
            offen_je_kunde[kunde_id] = offen_je_kunde.get(kunde_id, 0) + o.offen
        return render_template("kunden.html", kunden=rechnungen.kunden(db), offen=offen_je_kunde)

    @app.route("/kunden/neu", methods=["GET", "POST"])
    def kunde_neu():
        return kunde_formular(None)

    @app.route("/kunden/<int:kunde_id>", methods=["GET", "POST"])
    def kunde_bearbeiten(kunde_id):
        return kunde_formular(kunde_id)

    @app.post("/kunden/<int:kunde_id>/loeschen")
    def kunde_entfernen(kunde_id):
        db = verbindung()
        try:
            name = rechnungen.kunde(db, kunde_id)["name"]
            entwuerfe = rechnungen.kunde_loeschen(db, kunde_id)
        except Fehler as fehler:
            flash(str(fehler), "fehler")
            return redirect(url_for("kunde_bearbeiten", kunde_id=kunde_id))
        flash(f"{name} gelöscht" + (f", dazu {entwuerfe} Entwurf/Entwürfe." if entwuerfe else "."), "ok")
        return redirect(url_for("kundenliste"))

    def kunde_formular(kunde_id: int | None):
        db = verbindung()
        if request.method == "POST":
            try:
                neue_id = rechnungen.kunde_speichern(db, request.form, kunde_id)
            except Fehler as fehler:
                flash(str(fehler), "fehler")
                return render_template("kunde_form.html", k=request.form, kunde_id=kunde_id)
            flash("Kunde gespeichert.", "ok")
            weiter = request.form.get("weiter")
            if weiter and weiter.startswith("/rechnungen/neu"):
                return redirect(url_for("rechnung_neu", kunde=neue_id))
            return redirect(url_for("kundenliste"))
        k = rechnungen.kunde(db, kunde_id) if kunde_id else {}
        belege = db.execute("SELECT COUNT(*) FROM rechnungen WHERE kunde_id = ? AND status != 'entwurf'",
                            (kunde_id,)).fetchone()[0] if kunde_id else 0
        return render_template("kunde_form.html", k=k, kunde_id=kunde_id, weiter=request.args.get("weiter", ""),
                               belege=belege)

    # --- Bank --------------------------------------------------------------

    @app.get("/bank")
    def bank():
        db = verbindung()
        filter_ = request.args.get("filter", "offen")
        return render_template(
            "bank.html", liste=umsaetze.liste(db, filter_), filter=filter_,
            vorschlaege=umsaetze.vorschlaege(db), offene=rechnungen.offene_rechnungen(db),
            letzter=umsaetze.letzter_import(db), e=auswertung.einnahmen(db, date.today().year),
        )

    @app.get("/bank/einnahmen")
    def einnahmen():
        jahr = request.args.get("jahr", type=int) or date.today().year
        return render_template("einnahmen.html", e=auswertung.einnahmen(verbindung(), jahr))

    @app.post("/bank/import")
    def bank_import():
        datei = request.files.get("datei")
        if not datei or not datei.filename:
            flash("Bitte eine CSV-Datei auswählen.", "fehler")
            return redirect(url_for("bank"))
        try:
            e = umsaetze.importieren(verbindung(), datei.filename, datei.read(), ordner)
        except Fehler as fehler:
            flash(str(fehler), "fehler")
            return redirect(url_for("bank"))
        teile = [f"{e['neu']} neue Umsätze vom {_datum(e['von'])} bis {_datum(e['bis'])} eingelesen"]
        if e["doppelt"]:
            teile.append(f"{e['doppelt']} waren schon bekannt")
        if e["gebucht"]:
            teile.append(f"{e['gebucht']} Zahlungseingänge automatisch Rechnungen zugeordnet")
        if e["vorschlaege"]:
            teile.append(f"{e['vorschlaege']} Vorschläge warten auf Bestätigung")
        flash(". ".join(teile) + ".", "ok")
        return redirect(url_for("bank"))

    @app.post("/bank/<int:umsatz_id>/annehmen")
    def vorschlag_annehmen(umsatz_id):
        try:
            umsaetze.vorschlag_annehmen(verbindung(), umsatz_id)
            flash("Zahlung gebucht.", "ok")
        except Fehler as fehler:
            flash(str(fehler), "fehler")
        return zurueck(url_for("bank"))

    @app.post("/bank/<int:umsatz_id>/ablehnen")
    def vorschlag_ablehnen(umsatz_id):
        umsaetze.vorschlag_ablehnen(verbindung(), umsatz_id)
        flash("Vorschlag verworfen. Der Umsatz bleibt offen.", "ok")
        return zurueck(url_for("bank"))

    @app.post("/bank/<int:umsatz_id>/einordnen")
    def umsatz_einordnen(umsatz_id):
        """Ein Auswahlfeld für alles: „rechnung:<id>“ oder eine Kategorie."""
        db = verbindung()
        wert = request.form.get("wahl", "")
        try:
            if wert.startswith("rechnung:"):
                umsaetze.zuordnen(db, umsatz_id, int(wert.split(":", 1)[1]))
                flash("Zahlung der Rechnung zugeordnet.", "ok")
            else:
                umsaetze.kategorie_setzen(db, umsatz_id, wert)
                u = db.execute("SELECT gegenpartei FROM umsaetze WHERE id = ?", (umsatz_id,)).fetchone()
                bekannt = db.execute("SELECT 1 FROM regeln WHERE muster = ?",
                                     (umsaetze._muster(u["gegenpartei"]),)).fetchone()
                if wert and u["gegenpartei"].strip() and not bekannt:
                    session["merken_angebot"] = {"umsatz_id": umsatz_id, "gegenpartei": u["gegenpartei"],
                                                 "text": umsaetze.KATEGORIEN[wert]}
                else:
                    flash("Eingeordnet.", "ok")
        except (Fehler, ValueError) as fehler:
            flash(str(fehler) if isinstance(fehler, Fehler) else "Unbekannte Auswahl.", "fehler")
        return zurueck(url_for("bank"))

    @app.post("/bank/<int:umsatz_id>/merken")
    def umsatz_merken(umsatz_id):
        db = verbindung()
        u = db.execute("SELECT kategorie FROM umsaetze WHERE id = ?", (umsatz_id,)).fetchone()
        try:
            if not u or not u["kategorie"]:
                raise Fehler("Dieser Umsatz ist nicht eingeordnet.")
            weitere = umsaetze.kategorie_setzen(db, umsatz_id, u["kategorie"], merken=True)
            flash("Gemerkt." + (f" {weitere} weitere Buchungen gleich eingeordnet." if weitere else ""), "ok")
        except Fehler as fehler:
            flash(str(fehler), "fehler")
        return zurueck(url_for("bank"))

    # --- Kalender und Jahr -------------------------------------------------

    @app.get("/kalender")
    def kalender():
        heute = date.today()
        try:
            jahr, monat = (int(t) for t in request.args.get("monat", "").split("-"))
            date(jahr, monat, 1)
        except ValueError:
            jahr, monat = heute.year, heute.month
        return render_template(
            "kalender.html", wochen=auswertung.kalender(verbindung(), jahr, monat), jahr=jahr, monat=monat,
            vorher="%d-%02d" % auswertung.monat_verschieben(jahr, monat, -1),
            nachher="%d-%02d" % auswertung.monat_verschieben(jahr, monat, 1),
        )

    @app.get("/jahr")
    def jahresuebersicht():
        jahr = request.args.get("jahr", type=int) or date.today().year
        return render_template("jahr.html", j=auswertung.jahr(verbindung(), jahr))

    @app.get("/jahr/<int:jahr>.csv")
    def jahr_csv(jahr):
        # Mit BOM, damit Excel und Numbers die Umlaute richtig lesen.
        return Response("﻿" + auswertung.jahr_csv(verbindung(), jahr), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=Buchungen_{jahr}.csv"})

    # --- Einstellungen -----------------------------------------------------

    @app.route("/einstellungen", methods=["GET", "POST"])
    def einstellungen():
        db = verbindung()
        if request.method == "POST":
            werte = request.form.to_dict()
            werte["ik"] = "".join(werte.get("ik", "").split())
            # Platzhalter dürfen klein geschrieben sein („jjjj-mm-nnn“), der Rest bleibt, wie er ist.
            werte["nummer_format"] = re.sub(r"(?i)j{4}|m{2}|n{2,}", lambda t: t[0].upper(),
                                            werte.get("nummer_format", "").strip()) or "JJJJ-NNN"
            try:
                if not werte.get("startnummer", "1").strip().isdigit():
                    raise Fehler("Die erste laufende Nummer muss eine Zahl sein.")
                if not werte.get("zahlungsziel", "14").strip().isdigit():
                    raise Fehler("Das Zahlungsziel muss eine Zahl von Tagen sein.")
                if werte["ik"] and not (werte["ik"].isdigit() and len(werte["ik"]) == 9):
                    raise Fehler("Das Institutionskennzeichen (IK) hat genau 9 Ziffern.")
                if werte.get("schrift", "noto") not in pdfmod.SCHRIFTARTEN:
                    raise Fehler("Unbekannte Schrift.")
                rechnungen.nummer_format_pruefen(werte["nummer_format"], werte.get("nummer_neu_ab", "jahr"))
            except Fehler as fehler:
                flash(str(fehler), "fehler")
            else:
                dbmod.einstellungen_setzen(db, werte)
                flash("Einstellungen gespeichert.", "ok")
                return redirect(url_for("einstellungen"))
        einst = dbmod.einstellungen(db)
        return render_template("einstellungen.html", einst=einst, vorlagen=rechnungen.vorlagen(db),
                               regeln=umsaetze.regeln(db), ordner=ordner, neu_ab=rechnungen.NUMMER_NEU_AB,
                               naechste=rechnungen.naechste_nummer(db, date.today(), einst),
                               unterschrift=unterschrift.pfad(ordner).is_file(),
                               schriftarten=pdfmod.SCHRIFTARTEN, arial=pdfmod.schriftdateien("arial"))

    @app.get("/einstellungen/schriftprobe.pdf")
    def schriftprobe():
        """Musterrechnung mit erfundenem Kunden in der gewählten Schrift, nur zum Ansehen."""
        wahl = request.args.get("schrift", "noto")
        if wahl not in pdfmod.SCHRIFTARTEN:
            abort(404)
        einst = {**dbmod.einstellungen(verbindung()), "schrift": wahl}
        heute = date.today()
        muster = RechnungsDruck(
            nummer="MUSTER", datum=heute, leistung_von=heute, leistung_bis=None, faellig=heute + timedelta(days=14),
            empfaenger="Musterkunde GmbH\nMusterstraße 1\n12345 Musterstadt",
            positionen=[Position("Dolmetscheinsatz DGS", Decimal("2.5"), "Std.", 8500, 21250, heute,
                                 "Beispiel für eine Erläuterung"),
                        Position("Fahrtkosten", Decimal("36"), "km", 42, 1512, heute)],
            netto=22762, ust_satz=0, ust=0, brutto=22762, steuerhinweis=einst.get("hinweis_steuerfrei", ""),
            zusatztext=einst.get("zusatztext", ""))
        daten = pdfmod.rechnung_pdf(muster, rechnungen.absender(einst, ordner))
        return Response(daten, mimetype="application/pdf",
                        headers={"Content-Disposition": f"inline; filename=Schriftprobe_{wahl}.pdf"})

    @app.post("/einstellungen/unterschrift")
    def unterschrift_hochladen():
        datei = request.files.get("datei")
        if not datei or not datei.filename:
            flash("Bitte ein Foto oder einen Scan der Unterschrift auswählen.", "fehler")
        else:
            try:
                unterschrift.speichern(ordner, datei.read())
                flash("Unterschrift gespeichert. Sie steht ab jetzt auf neuen Rechnungen. Bitte in der "
                      "Vorschau unten prüfen.", "ok")
            except Fehler as fehler:
                flash(str(fehler), "fehler")
        return redirect(url_for("einstellungen") + "#unterschrift")

    @app.post("/einstellungen/unterschrift/entfernen")
    def unterschrift_entfernen():
        unterschrift.entfernen(ordner)
        flash("Unterschrift entfernt. Bereits ausgestellte Rechnungen bleiben, wie sie sind.", "ok")
        return redirect(url_for("einstellungen") + "#unterschrift")

    @app.get("/einstellungen/unterschrift.png")
    def unterschrift_bild():
        if not unterschrift.pfad(ordner).is_file():
            abort(404)
        return send_file(unterschrift.pfad(ordner), mimetype="image/png", max_age=0)

    @app.post("/einstellungen/vorlage")
    def vorlage_neu():
        try:
            rechnungen.vorlage_speichern(verbindung(), request.form.get("beschreibung", ""),
                                         request.form.get("einheit", ""), request.form.get("preis", ""))
            flash("Vorlage gespeichert.", "ok")
        except Fehler as fehler:
            flash(str(fehler), "fehler")
        return redirect(url_for("einstellungen") + "#vorlagen")

    @app.post("/einstellungen/vorlage/<int:vorlage_id>/loeschen")
    def vorlage_weg(vorlage_id):
        rechnungen.vorlage_loeschen(verbindung(), vorlage_id)
        return redirect(url_for("einstellungen") + "#vorlagen")

    @app.post("/einstellungen/regel/<int:regel_id>/loeschen")
    def regel_weg(regel_id):
        umsaetze.regel_loeschen(verbindung(), regel_id)
        return redirect(url_for("einstellungen") + "#regeln")

    # --- Post und Hinweise aus Mails ----------------------------------------

    def ki_status(einst: dict) -> tuple[bool, str]:
        try:
            return postfach.ki_fuer(einst).status()
        except Exception as fehler:  # KI-Modul fehlt oder Ollama antwortet unerwartet
            return False, f"KI nicht bereit ({fehler})"

    @app.get("/post")
    def post():
        db = verbindung()
        einst = dbmod.einstellungen(db)
        filter_ = request.args.get("filter", "arbeit")
        # Mails, die die KI noch liest, stehen auch unter „Arbeit“, damit man sieht, dass etwas passiert.
        bedingung = "WHERE arbeit = 1 OR status = 'neu'" if filter_ == "arbeit" else ""
        mails = db.execute(
            f"SELECT m.*, (SELECT COUNT(*) FROM hinweise h WHERE h.mail_id = m.id AND h.status = 'offen') AS offen "
            f"FROM mails m {bedingung} ORDER BY datum DESC LIMIT 200"
        ).fetchall()
        zahlen = db.execute(
            "SELECT COUNT(*) AS alle, SUM(arbeit = 1 OR status = 'neu') AS arbeit, SUM(status = 'neu') AS neu FROM mails"
        ).fetchone()
        return render_template("post.html", mails=mails, filter=filter_, einst=einst, zahlen=zahlen,
                               auswertungen={m["id"]: hinweise.auswertung_aus_json(m["auswertung"]) for m in mails},
                               ki=ki_status(einst), laeuft=postfach.laeuft(), konto=postfach.konto(einst))

    @app.get("/post/stand")
    def post_stand():
        """Für die Live-Anzeige auf der Post-Seite (wird alle paar Sekunden abgefragt)."""
        db = verbindung()
        einst = dbmod.einstellungen(db)
        wartend = db.execute("SELECT COUNT(*) FROM mails WHERE status = 'neu'").fetchone()[0]
        return jsonify({**postfach.fortschritt(), "wartend": wartend,
                        "fehler": einst.get("mail_letzter_fehler", ""), "stand": einst.get("mail_stand", "")})

    @app.get("/post/<int:mail_id>")
    def mail_ansehen(mail_id):
        db = verbindung()
        m = db.execute("SELECT * FROM mails WHERE id = ?", (mail_id,)).fetchone()
        if not m:
            abort(404)
        liste = hinweise.zur_mail(db, mail_id)
        return render_template("mail.html", m=m, a=hinweise.auswertung_aus_json(m["auswertung"]),
                               hinweise=liste, anhaenge=json.loads(m["anhaenge"] or "[]"),
                               rechnungen_={h["rechnung_id"]: rechnungen.rechnung(db, h["rechnung_id"])
                                            for h in liste if h["rechnung_id"]})

    @app.post("/post/abrufen")
    def post_abrufen():
        if postfach.laeuft():
            flash("Der Abruf läuft bereits.", "hinweis")
        else:
            postfach.anstossen(ordner)
            flash("Abruf gestartet. Neue Mails erscheinen hier, sobald die KI sie gelesen hat.", "ok")
        return zurueck(url_for("post"))

    @app.post("/post/<int:mail_id>/neu-auswerten")
    def mail_neu_auswerten(mail_id):
        postfach.neu_auswerten(verbindung(), mail_id)
        postfach.anstossen(ordner)
        flash("Die Mail wird neu ausgewertet.", "ok")
        return redirect(url_for("mail_ansehen", mail_id=mail_id))

    @app.post("/hinweise/<int:hinweis_id>/<status>")
    def hinweis_setzen(hinweis_id, status):
        try:
            hinweise.setzen(verbindung(), hinweis_id, status)
            flash("Erledigt." if status == "erledigt" else "Ausgeblendet.", "ok")
        except Fehler as fehler:
            flash(str(fehler), "fehler")
        return zurueck(url_for("start"))

    @app.route("/einstellungen/post", methods=["GET", "POST"])
    def post_einstellungen():
        from . import mail as mailmod
        db = verbindung()
        einst = dbmod.einstellungen(db)
        if request.method == "POST":
            f = request.form
            benutzer = f.get("mail_benutzer", "").strip()
            werte = {"mail_anbieter": f.get("mail_anbieter", ""), "mail_host": f.get("mail_host", ""),
                     "mail_port": f.get("mail_port", "993") or "993", "mail_benutzer": benutzer,
                     "mail_ordner": f.get("mail_ordner", "") or "INBOX",
                     "mail_seit_tage": f.get("mail_seit_tage", "30") or "30",
                     "mail_aktiv": "1" if f.get("mail_aktiv") else "0"}
            try:
                if not werte["mail_host"] or not benutzer:
                    raise Fehler("Bitte Anbieter bzw. Server und E-Mail-Adresse angeben.")
                if not werte["mail_port"].isdigit() or not werte["mail_seit_tage"].isdigit():
                    raise Fehler("Port und Tage müssen Zahlen sein.")
                passwort = f.get("passwort", "")
                if passwort:
                    postfach.passwort_speichern(benutzer, passwort)
                else:
                    passwort = postfach.passwort_lesen(benutzer) or ""
                if not passwort:
                    raise Fehler("Bitte das Passwort (bzw. App-Passwort) eingeben.")
                if einst.get("mail_benutzer") != benutzer or einst.get("mail_host") != werte["mail_host"]:
                    werte.update({"mail_uidvalidity": "", "mail_letzte_uid": "0"})  # anderes Postfach
                dbmod.einstellungen_setzen(db, werte)
                ordner_liste = postfach.verbindung_testen(postfach.konto(dbmod.einstellungen(db)), passwort)
            except Fehler as fehler:
                flash(str(fehler), "fehler")
                return render_template("post_einstellungen.html", einst={**einst, **werte},
                                       anbieter=mailmod.ANBIETER, ki=ki_status(einst))
            flash(f"Verbindung klappt ({len(ordner_liste)} Ordner gefunden). "
                  + ("Die Mails werden jetzt abgerufen." if werte["mail_aktiv"] == "1" else ""), "ok")
            if werte["mail_aktiv"] == "1":
                postfach.anstossen(ordner)
            return redirect(url_for("post"))
        return render_template("post_einstellungen.html", einst=einst, anbieter=mailmod.ANBIETER,
                               ki=ki_status(einst))

    @app.post("/einstellungen/post/abmelden")
    def post_abmelden():
        db = verbindung()
        einst = dbmod.einstellungen(db)
        if einst.get("mail_benutzer"):
            postfach.passwort_loeschen(einst["mail_benutzer"])
        dbmod.einstellungen_setzen(db, {"mail_aktiv": "0"})
        flash("Postfach getrennt. Das Passwort wurde aus dem Schlüsselbund gelöscht. Gelesene Mails bleiben "
              "hier gespeichert.", "ok")
        return redirect(url_for("post_einstellungen"))

    @app.get("/hilfe")
    def hilfe():
        video = Path(app.static_folder) / "hilfe" / "tutorial.mp4"
        return render_template("hilfe.html", video=video.exists())

    @app.post("/ordner")
    def ordner_oeffnen():
        unterordner = request.form.get("unter", "")
        ziel = ordner / unterordner if unterordner in ("rechnungen", "importe") else ordner
        ziel.mkdir(parents=True, exist_ok=True)
        programm = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
        subprocess.Popen([programm, str(ziel)])
        return zurueck(url_for("start"))

    return app
