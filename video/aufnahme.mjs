#!/usr/bin/env node
// Nimmt die App für das Video-Tutorial auf.
//
// Bedient den Arbeits-Assistenten mit Chromium (puppeteer-core), macht pro Schritt einen Screenshot
// (1920 × 1080 bei doppelter Pixeldichte) und schreibt die Boxen der Zielelemente nach
// aufnahmen/ziele.json (und ziele.js für die HyperFrames-Komposition).
//
//   node aufnahme.mjs                          startet eine frische Demo (eigener Datenordner, freier Port)
//   node aufnahme.mjs --port 8791 --daten DIR  nimmt eine laufende Demo-Instanz auf
//
// Weitere Optionen:
//   --app DIR   Ordner, der das Paket „arbeitsassistent“ enthält (Standard: dieses Projekt)
//   --tmp DIR   wohin der Datenordner der frischen Demo kommt (Standard: Temp-Ordner des Systems)
//   --dsf N     Pixeldichte der Screenshots (Standard 2)
//
// Nur erfundene Daten: die Demo aus arbeitsassistent/demo.py plus ein erfundener Kunde, eine
// Rechnung und eine erfundene CSV im Sparkasse-Format. Nichts verlässt den Rechner.

import { spawn, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync, createWriteStream } from "node:fs";
import net from "node:net";
import os from "node:os";
import { dirname, join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import puppeteer from "puppeteer-core";

const VIDEO = dirname(fileURLToPath(import.meta.url));
const PROJEKT = resolve(VIDEO, "..");
const AUS = join(VIDEO, "aufnahmen");
const SCHRIFTEN = join(VIDEO, "tutorial", "assets", "fonts");
const CHROMIUM = "/usr/bin/chromium";
const BREITE = 1920;
const HOEHE = 1080;
// So heißt der Datenordner auf dem Mac. Der Pfad des Temp-Ordners erscheint nie im Video.
const MAC_DATENORDNER = "/Users/max/Arbeits-Assistent";

// ---------------------------------------------------------------------------------------------
// ZIELE: alle Elemente, die das Skript bedient oder deren Box das Video braucht.
// Für jeden Namen wird zuerst [data-tour="<name>"] gesucht. Die Liste dahinter ist der Rückfall
// für Oberflächen ohne data-tour: CSS, Puppeteer-Textselektoren (::-p-text) oder Funktionen.
// ---------------------------------------------------------------------------------------------
const karteMitTitel = (titel) => async (page) =>
  page.evaluateHandle((t) => {
    const h = [...document.querySelectorAll("h2, h3")].find((e) => e.textContent.includes(t));
    return h ? h.closest(".karte, section") : null;
  }, titel);

const abschnitt = (titel) => async (page) =>
  page.evaluateHandle((t) => {
    const h = [...document.querySelectorAll("h2")].find((e) => e.textContent.includes(t));
    return h ? h.nextElementSibling : null;
  }, titel);

const ZIELE = {
  // Navigation und Rahmen
  "nav-uebersicht": ['nav a[href="/"]'],
  "nav-rechnungen": ['nav a[href="/rechnungen"]'],
  "nav-konto": ['nav a[href="/bank"]'],
  "nav-kalender": ['nav a[href="/kalender"]'],
  "nav-auswertung": ['nav a[href="/jahr"]'],
  "nav-kunden": ['nav a[href="/kunden"]'],
  "nav-einstellungen": ['nav a[href="/einstellungen"]'],
  lokal: [".lokal"],
  meldung: [".meldung[role=status]", ".meldung.angebot"],
  // Übersicht
  "kpi-offen": [".kacheln .kachel:nth-child(1)"],
  "kpi-ueberfaellig": [".kacheln .kachel:nth-child(2)"],
  aufgaben: [abschnitt("Überfällig")],
  erinnerung: ["a::-p-text(Zahlungserinnerung)"],
  // Einstellungen
  angaben: [abschnitt("Eigene Angaben")],
  vorlagen: ["#vorlagen + .karte"],
  "vorlage-zeile": ["#vorlagen + .karte tbody tr", "[data-tour=vorlagen] tr"],
  datenordner: [".karte code", "code"],
  "ordner-oeffnen": ["button::-p-text(Ordner öffnen)"],
  // Kunden
  "kunde-neu": ["a::-p-text(Neuer Kunde)"],
  "kunde-zeile": ["tbody tr"],
  "kunde-name": ["input#name"],
  "kunde-anschrift": ["textarea#anschrift"],
  "kunde-steuerfall": ["select#steuerfall"],
  "kunde-speichern": ["button::-p-text(Speichern)"],
  // Rechnung
  "neue-rechnung": ['main a[href="/rechnungen/neu"]', 'a[href="/rechnungen/neu"]'],
  "kunde-waehlen": ["select#kunde_id"],
  "position-datum": ["input[name=pos_datum]"],
  "position-leistung": ["input[name=pos_beschreibung]"],
  "position-menge": ["input[name=pos_menge]"],
  "position-einheit": ["input[name=pos_einheit]"],
  "position-preis": ["input[name=pos_preis]"],
  "position-betrag": ["td.p-summe"],
  summe: ["#summe"],
  "entwurf-speichern": ["button::-p-text(Entwurf speichern)"],
  vorschau: ["a::-p-text(Vorschau)"],
  ausstellen: ["button::-p-text(Ausstellen)"],
  "rechnung-nummer": ["main h1", "h1"],
  "pdf-oeffnen": ["a::-p-text(PDF öffnen)"],
  // Konto
  "csv-ablage": ["form.ablage"],
  "csv-datei": ["input[type=file]"],
  "vorschlag-stimmt": ["button::-p-text(Stimmt)"],
  "reiter-eingaenge": ['a[href*="filter=eingaenge"]', "a::-p-text(Eingänge)"],
  "umsatz-zeile": ["tbody tr"],
  "kategorie-waehlen": ["select[name=kategorie]", "select[name=wahl]"],
  "regel-merken": ["label.ankreuzen"],
  einordnen: ["button::-p-text(Einordnen)"],
  // Kalender und Jahr
  // Post (Baustein 2)
  "post-einrichten": ['a[href$="/einstellungen/post"]'],
  "mail-adresse": ["input#mail_benutzer"],
  "mail-passwort": ["input#passwort"],
  "mail-hilfe": ["#anbieter-hilfe"],
  "mail-datenschutz": [karteMitTitel("Was mit Ihren Mails passiert")],
  "mail-ki": [karteMitTitel("Lokale KI")],
  "mail-zeile": ["tbody tr"],
  "mail-link": ["tbody tr a"],
  "aufgabe-zeile": ["[data-tour=aufgaben] li", ".liste li"],
  "ki-zeile": [".ki-zeile"],
  "ki-schild": ["[data-tour=aufgaben] .pille.ki", ".pille.ki"],
  "kalender-legende": [".legende"],
  "kalender-heute": ["td.heute"],
  "jahr-kacheln": [".kacheln"],
  "jahr-csv": ["a::-p-text(CSV)"],
};

// Erfundene Daten, die das Video selbst anlegt
const NEUER_KUNDE = {
  name: "Eingliederungshilfe Musterkreis",
  anschrift: "Sozialamt\nKreishausweg 3\n12346 Musterstadt",
  steuerfall: "steuerfrei",
};
const LEISTUNG = { text: "Dolmetscheinsatz DGS", menge: "2" };
const CSV_NAME = "Umsaetze.csv";
// Postfach-Formular: nur ausgefüllt fürs Bild, nie abgeschickt. Erfundene Adresse, erfundenes Passwort.
const MAIL = { anbieter: "GMX", adresse: "max@beispiel.example", passwort: "beispiel", betreff: "Bestätigung Dolmetscheinsatz" };

// ---------------------------------------------------------------------------------------------

const { values: arg } = parseArgs({
  options: {
    port: { type: "string" },
    daten: { type: "string" },
    app: { type: "string", default: PROJEKT },
    tmp: { type: "string", default: os.tmpdir() },
    dsf: { type: "string", default: "2" },
  },
});
const DSF = Number(arg.dsf);

function meldung(text) {
  process.stdout.write(text + "\n");
}

function portFrei(port) {
  return new Promise((ok) => {
    const s = net.createServer();
    s.once("error", () => ok(false));
    s.listen(port, "127.0.0.1", () => s.close(() => ok(true)));
  });
}

async function freierPort(ab = 8790) {
  for (let p = ab; p < ab + 100; p++) if (await portFrei(p)) return p;
  throw new Error("Kein freier Port gefunden.");
}

async function warteAufApp(port, sekunden = 30) {
  for (let i = 0; i < sekunden * 4; i++) {
    try {
      const antwort = await fetch(`http://127.0.0.1:${port}/gesund`);
      if ((await antwort.text()) === "arbeitsassistent") return;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`Die App antwortet nicht auf Port ${port}.`);
}

// Startet eine frische Demo. Merkt sich die PID, prüft mit `ss`, dass genau dieser Prozess auf dem
// Port lauscht, und stoppt am Ende nur diese PID.
async function demoStarten() {
  const port = await freierPort();
  mkdirSync(arg.tmp, { recursive: true });
  const daten = mkdtempSync(join(arg.tmp, "arbeitsassistent-video-"));
  const python = join(PROJEKT, ".venv", "bin", "python");
  if (!existsSync(python)) throw new Error(`${python} fehlt. Erst: python3 -m venv .venv && .venv/bin/pip install -e '.[test]'`);
  const log = createWriteStream(join(AUS, "server.log"));
  const kind = spawn(python, ["-m", "arbeitsassistent", "--demo", "--daten", daten, "--port", String(port), "--ohne-browser"], {
    cwd: arg.app,
    env: { ...process.env, PYTHONPATH: arg.app },
    stdio: ["ignore", "pipe", "pipe"],
  });
  kind.stdout.pipe(log);
  kind.stderr.pipe(log);
  await warteAufApp(port);
  const ss = execFileSync("ss", ["-ltnp"], { encoding: "utf8" });
  const zeile = ss.split("\n").find((z) => z.includes(`127.0.0.1:${port} `));
  if (!zeile || !zeile.includes(`pid=${kind.pid},`)) {
    kind.kill("SIGTERM");
    throw new Error(`Auf Port ${port} lauscht nicht der gestartete Prozess ${kind.pid}:\n${zeile}`);
  }
  meldung(`Demo läuft: Port ${port}, PID ${kind.pid}, Daten ${daten}`);
  return {
    port,
    daten,
    async stoppen() {
      if (kind.exitCode === null) {
        const ende = new Promise((r) => kind.once("exit", r));
        process.kill(kind.pid, "SIGTERM");
        const zeit = setTimeout(() => kind.exitCode === null && process.kill(kind.pid, "SIGKILL"), 5000);
        await ende;
        clearTimeout(zeit);
      }
      meldung(`Demo gestoppt (PID ${kind.pid}), Port ${port} ${(await portFrei(port)) ? "frei" : "NOCH BELEGT"}.`);
    },
  };
}

// ---------------------------------------------------------------------------------------------

function seitenStil() {
  const schrift = (g) => readFileSync(join(SCHRIFTEN, `inter-${g}.ttf`)).toString("base64");
  // Auf dem Mac erscheint die Systemschrift (SF). Unter Linux setzen wir die ähnliche Inter dafür ein.
  const familien = ["-apple-system", "BlinkMacSystemFont", "system-ui"];
  const flaechen = [400, 500, 600, 700]
    .map((g) => familien.map((f) => `@font-face{font-family:"${f}";font-weight:${g};src:url(data:font/ttf;base64,${schrift(g)}) format("truetype");}`).join(""))
    .join("");
  // Ruhige, scharfe Bilder: keine Übergänge, kein Textcursor, kein weiches Scrollen.
  const ruhe = "*,*::before,*::after{transition:none!important;animation:none!important;caret-color:transparent!important;scroll-behavior:auto!important}";
  return flaechen + ruhe;
}

const fehlend = new Set();
const daten = { erstellt: new Date().toISOString(), viewport: { breite: BREITE, hoehe: HOEHE, dsf: DSF }, aufnahmen: {}, info: {} };

async function main() {
  rmSync(AUS, { recursive: true, force: true });
  mkdirSync(AUS, { recursive: true });
  const verweis = join(VIDEO, "tutorial", "assets", "aufnahmen");
  if (!existsSync(verweis)) symlinkSync("../../aufnahmen", verweis);

  const demo = arg.port ? { port: Number(arg.port), daten: arg.daten, stoppen: async () => {} } : await demoStarten();
  if (!demo.daten) throw new Error("Mit --port bitte auch --daten <Datenordner der Instanz> angeben.");
  const basis = `http://127.0.0.1:${demo.port}`;

  const browser = await puppeteer.launch({
    executablePath: CHROMIUM,
    headless: true,
    args: ["--lang=de-DE", "--accept-lang=de-DE,de", "--hide-scrollbars", "--force-color-profile=srgb", "--font-render-hinting=none"],
    env: { ...process.env, LANG: "de_DE.UTF-8", LANGUAGE: "de_DE:de" },
    defaultViewport: { width: BREITE, height: HOEHE, deviceScaleFactor: DSF },
  });
  try {
    const page = await browser.newPage();
    const cdp = await page.createCDPSession();
    await cdp.send("Emulation.setLocaleOverride", { locale: "de-DE" });
    await page.emulateTimezone("Europe/Berlin");
    await page.emulateMediaFeatures([
      { name: "prefers-color-scheme", value: "light" },
      { name: "prefers-reduced-motion", value: "reduce" },
    ]);
    await page.evaluateOnNewDocument((css) => {
      const setzen = () => {
        const s = document.createElement("style");
        s.textContent = css;
        document.head.prepend(s);
      };
      if (document.head) setzen();
      else document.addEventListener("DOMContentLoaded", setzen);
    }, seitenStil());
    page.on("dialog", (d) => d.accept());
    page.setDefaultTimeout(15000);

    await ablauf(new Bediener(page, basis, demo.daten));
    daten.oberflaeche = (await page.$("[data-tour]")) ? "data-tour" : "ohne data-tour (Rückfall-Selektoren)";
  } catch (fehler) {
    const seiten = await browser.pages();
    const page = seiten[seiten.length - 1];
    if (page) await page.screenshot({ path: join(AUS, "FEHLER.png") }).catch(() => {});
    throw fehler;
  } finally {
    await browser.close();
    await demo.stoppen();
  }

  writeFileSync(join(AUS, "ziele.json"), JSON.stringify(daten, null, 2));
  writeFileSync(join(AUS, "ziele.js"), `// Erzeugt von aufnahme.mjs – nicht von Hand ändern.\nwindow.ZIELE = ${JSON.stringify(daten)};\n`);
  meldung(`\n${Object.keys(daten.aufnahmen).length} Aufnahmen in ${AUS}`);
  meldung(`Oberfläche: ${daten.oberflaeche}`);
  if (fehlend.size) meldung(`Nicht gefunden (optional): ${[...fehlend].join(", ")}`);
}

// ---------------------------------------------------------------------------------------------

class Bediener {
  constructor(page, basis, datenordner) {
    this.page = page;
    this.basis = basis;
    this.datenordner = datenordner;
  }

  async geh(pfad) {
    await this.page.goto(this.basis + pfad, { waitUntil: "networkidle0" });
  }

  // Sucht ein Ziel. Optionen: zeile (Text, der in derselben Tabellenzeile/Karte stehen muss),
  // pflicht (Standard true), sichtbar (Standard true).
  async finde(name, { zeile, pflicht = true, sichtbar = true } = {}) {
    const kandidaten = [`[data-tour="${name}"]`, ...(ZIELE[name] || [])];
    for (const k of kandidaten) {
      let treffer = [];
      if (typeof k === "function") {
        const h = await k(this.page);
        if (h && (await h.evaluate((e) => e instanceof Element))) treffer = [h];
      } else {
        treffer = await this.page.$$(k);
      }
      for (const h of treffer) {
        const ok = await h.evaluate(
          (el, zeile, sichtbar) => {
            if (sichtbar) {
              const r = el.getBoundingClientRect();
              const s = getComputedStyle(el);
              if (r.width === 0 || r.height === 0 || s.visibility === "hidden" || s.display === "none") return false;
            }
            if (!zeile) return true;
            // Nur die eigene Zeile zählt. Ohne Zeilen-Element höchstens drei Ebenen nach oben.
            const reihe = el.closest("tr, li, [role=row], [data-tour-zeile], article");
            if (reihe) return reihe.textContent.includes(zeile);
            let e = el;
            for (let i = 0; e && i < 3; i++, e = e.parentElement) if (e.textContent.includes(zeile)) return true;
            return false;
          },
          zeile,
          sichtbar,
        );
        if (ok) return h;
      }
    }
    if (pflicht) throw new Error(`Ziel „${name}“${zeile ? ` (Zeile „${zeile}“)` : ""} nicht gefunden auf ${this.page.url()}`);
    fehlend.add(name + (zeile ? `[${zeile}]` : ""));
    return null;
  }

  async klick(name, opt = {}) {
    const h = await this.finde(name, opt);
    if (!h) return false;
    const navigiert = await h.evaluate((el) => {
      const a = el.closest("a[href]");
      if (a) return a.target !== "_blank" && !a.getAttribute("href").startsWith("#");
      const b = el.closest("button, input[type=submit]");
      return !!(b && b.form && b.type === "submit");
    });
    if (navigiert) await Promise.all([this.page.waitForNavigation({ waitUntil: "networkidle0" }), h.click()]);
    else await h.click();
    return true;
  }

  async tippen(name, text, opt = {}) {
    const h = await this.finde(name, opt);
    await h.click({ count: 3 });
    await h.evaluate((el) => el.select && el.select());
    await this.page.keyboard.press("Backspace");
    await h.type(text, { delay: 12 });
  }

  async wertSetzen(name, wert, opt = {}) {
    const h = await this.finde(name, opt);
    await h.evaluate((el, w) => {
      el.value = w;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }, wert);
  }

  // Wählt in einem Auswahlfeld die Option mit dem Wert `text` oder deren Text `text` enthält
  // (ohne Groß-/Kleinschreibung). Gibt true zurück, wenn
  // das Feld sich selbst abschickt (neue Oberfläche: select[data-auto]).
  async waehlen(name, text, opt = {}) {
    const h = await this.finde(name, opt);
    const wert = await h.evaluate((el, t) => {
      const klein = t.toLowerCase();
      const o = [...el.options].find((o) => o.value === t) || [...el.options].find((o) => o.textContent.toLowerCase().includes(klein));
      return o ? o.value : null;
    }, text);
    if (wert === null) throw new Error(`Option „${text}“ fehlt in „${name}“.`);
    const auto = await h.evaluate((el) => el.hasAttribute("data-auto"));
    if (auto) await Promise.all([this.page.waitForNavigation({ waitUntil: "networkidle0" }), h.select(wert)]);
    else await h.select(wert);
    return auto;
  }

  // Setzt eine Auswahl nur sichtbar, ohne sie abzuschicken (für das Bild „Auswahl getroffen“
  // bei Feldern, die sich selbst abschicken). Gibt false zurück, wenn das Feld das nicht tut.
  async vorwaehlen(name, text, opt = {}) {
    const h = await this.finde(name, opt);
    return h.evaluate((el, t) => {
      if (!el.hasAttribute("data-auto")) return false;
      const klein = t.toLowerCase();
      const o = [...el.options].find((o) => o.value === t) || [...el.options].find((o) => o.textContent.toLowerCase().includes(klein));
      if (!o) return false;
      el.value = o.value;
      return true;
    }, text);
  }

  async zeige(name, opt = {}) {
    const h = await this.finde(name, opt);
    await h.evaluate((el, block) => el.scrollIntoView({ block, inline: "nearest" }), opt.block || "center");
  }

  async nachOben() {
    await this.page.evaluate(() => window.scrollTo(0, 0));
  }

  async hochladen(datei) {
    const ablage = await this.finde("csv-ablage");
    const feld = (await ablage.$('[data-tour="csv-datei"]')) || (await ablage.$("input[type=file]")) || (await this.finde("csv-datei", { sichtbar: false }));
    const navigation = this.page.waitForNavigation({ waitUntil: "networkidle0", timeout: 8000 }).catch(() => null);
    await feld.uploadFile(datei);
    await navigation;
    await this.page.waitForNetworkIdle({ idleTime: 300 }).catch(() => {});
  }

  // Box eines Elements in CSS-Pixeln des Ansichtsfensters. Für Eingabefelder zusätzlich die Lage
  // des Textes (für den Tipp-Effekt im Video).
  async box(h) {
    return h.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const rund = (v) => Math.round(v * 10) / 10;
      const box = { x: rund(r.x), y: rund(r.y), w: rund(r.width), h: rund(r.height) };
      const feld = el.matches("input:not([type=checkbox]):not([type=radio]):not([type=file]), textarea, select") ? el : null;
      if (feld) {
        const s = getComputedStyle(feld);
        const text = feld.tagName === "SELECT" ? feld.options[feld.selectedIndex]?.textContent || "" : feld.value;
        const zeilen = text.split("\n");
        const c = document.createElement("canvas").getContext("2d");
        c.font = `${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
        const breite = Math.max(...zeilen.map((z) => c.measureText(z).width));
        const links = r.x + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft);
        // Grundfarbe und Innenfläche: Damit deckt das Video den Platzhalter ab, während getippt wird.
        let grund = "rgba(0, 0, 0, 0)";
        for (let e = feld; e && /rgba\(0, 0, 0, 0\)|transparent/.test(grund); e = e.parentElement) grund = getComputedStyle(e).backgroundColor;
        const rahmen = (seite) => parseFloat(s[`border${seite}Width`]) || 0;
        const innen = { x: rund(r.x + rahmen("Left")), y: rund(r.y + rahmen("Top")), w: rund(r.width - rahmen("Left") - rahmen("Right")), h: rund(r.height - rahmen("Top") - rahmen("Bottom")) };
        box.text = { x: rund(links), w: rund(Math.min(breite, r.width)), zeichen: text.replace(/\n/g, "").length, zeilen: zeilen.length, grund, innen };
        // Breite nach jedem Zeichen, damit der Tipp-Effekt genau an Zeichengrenzen aufdeckt.
        if (zeilen.length === 1 && text.length <= 80) box.text.stellen = [...text].map((_, i) => rund(c.measureText(text.slice(0, i + 1)).width));
      }
      return box;
    });
  }

  // Ein Schritt: Seite beruhigen, Ziele messen, Screenshot.
  async aufnahme(id, ziele = []) {
    const page = this.page;
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => document.activeElement && document.activeElement.blur && document.activeElement.blur());
    // Den echten Pfad des Demo-Ordners durch den Pfad auf dem Mac ersetzen.
    await page.evaluate(
      (alt, neu) => {
        const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        for (let n = w.nextNode(); n; n = w.nextNode()) if (n.nodeValue.includes(alt)) n.nodeValue = n.nodeValue.split(alt).join(neu);
      },
      this.datenordner,
      MAC_DATENORDNER,
    );
    const boxen = {};
    const immer = ["nav-uebersicht", "nav-rechnungen", "nav-konto", "nav-post", "nav-kalender", "nav-auswertung", "nav-kunden", "nav-einstellungen", "nav-hilfe", "neue-rechnung", "meldung", "lokal"];
    for (const z of [...immer.map((name) => ({ name, pflicht: false, still: true })), ...ziele]) {
      const spec = typeof z === "string" ? { name: z } : z;
      const h = await this.finde(spec.name, { zeile: spec.zeile, pflicht: spec.pflicht ?? false });
      if (h) boxen[spec.als || spec.name] = await this.box(h);
      else if (spec.still) fehlend.delete(spec.name);
    }
    const scroll = await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));
    // Feste und klebende Bereiche (Kopfzeile, Seitenleiste): Sie bleiben beim Scroll-Übergang im Video stehen.
    const fest = await page.evaluate(() => {
      const raus = [];
      for (const e of document.body.querySelectorAll("*")) {
        const pos = getComputedStyle(e).position;
        if (pos !== "fixed" && pos !== "sticky") continue;
        if (raus.some((r) => r.el.contains(e))) continue;
        const r = e.getBoundingClientRect();
        if (r.width < 1 || r.height < 1 || r.bottom <= 0 || r.top >= innerHeight) continue;
        raus.push({ el: e, x: Math.max(0, r.x), y: Math.max(0, r.y), w: Math.min(innerWidth, r.right) - Math.max(0, r.x), h: Math.min(innerHeight, r.bottom) - Math.max(0, r.y) });
      }
      return raus.map(({ x, y, w, h }) => ({ x, y, w, h }));
    });
    await page.screenshot({ path: join(AUS, `${id}.png`), type: "png" });
    daten.aufnahmen[id] = { art: "app", datei: `${id}.png`, breite: BREITE, hoehe: HOEHE, dsf: DSF, pfad: new URL(page.url()).pathname, scroll, fest, ziele: boxen };
    meldung(`  ${id}  (${Object.keys(boxen).length} Ziele)`);
  }

  // Lädt das PDF hinter einem Link, rendert Seite 1 als PNG und sucht auf Wunsch den QR-Code.
  async pdf(name, id, { qr = false } = {}) {
    const h = await this.finde(name);
    const url = await h.evaluate((a) => a.closest("a").href);
    const antwort = await fetch(url);
    if (!antwort.ok) throw new Error(`PDF ${url}: ${antwort.status}`);
    const pdfDatei = join(AUS, `${id}.pdf`);
    writeFileSync(pdfDatei, Buffer.from(await antwort.arrayBuffer()));
    execFileSync("pdftoppm", ["-png", "-singlefile", "-f", "1", "-l", "1", "-scale-to-y", "3600", "-scale-to-x", "-1", pdfDatei, join(AUS, id)]);
    const groesse = execFileSync("magick", ["identify", "-format", "%w %h", join(AUS, `${id}.png`)], { encoding: "utf8" }).split(" ").map(Number);
    const ziele = {};
    if (qr) {
      const xml = execFileSync("zbarimg", ["-q", "--xml", join(AUS, `${id}.png`)], { encoding: "utf8" });
      const punkte = [...(xml.match(/points='([^']+)'/)?.[1] || "").matchAll(/([+-]?\d+),([+-]?\d+)/g)].map((m) => [Number(m[1]), Number(m[2])]);
      if (punkte.length) {
        const xs = punkte.map((p) => p[0]);
        const ys = punkte.map((p) => p[1]);
        ziele.qr = { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
      } else fehlend.add(`qr in ${id}`);
    }
    daten.aufnahmen[id] = { art: "dokument", datei: `${id}.png`, breite: groesse[0], hoehe: groesse[1], ziele };
    meldung(`  ${id}  (PDF${ziele.qr ? ", QR-Code gefunden" : ""})`);
  }
}

// ---------------------------------------------------------------------------------------------
// Erfundene CSV im Sparkasse-Format (wie arbeitsassistent/demo.py): zwei Zahlungen mit
// Rechnungsnummer für noch nicht fällige Demo-Rechnungen und eine Bahnfahrt.

const KOPF =
  '"Auftragskonto";"Buchungstag";"Valutadatum";"Buchungstext";"Verwendungszweck";"Glaeubiger ID";' +
  '"Mandatsreferenz";"Kundenreferenz (End-to-End)";"Sammlerreferenz";"Lastschrift Ursprungsbetrag";' +
  '"Auslagenersatz Ruecklastschrift";"Beguenstigter/Zahlungspflichtiger";"Kontonummer/IBAN";' +
  '"BIC (SWIFT-Code)";"Betrag";"Waehrung";"Info"';

function csvBauen(datenordner) {
  const db = new DatabaseSync(join(datenordner, "daten.db"), { readOnly: true });
  const heute = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  const offen = db
    .prepare(
      `SELECT r.nummer, r.faellig, k.name AS kunde,
              r.brutto - COALESCE((SELECT SUM(z.betrag) FROM zahlungen z WHERE z.rechnung_id = r.id), 0) AS offen
         FROM rechnungen r JOIN kunden k ON k.id = r.kunde_id
        WHERE r.status = 'ausgestellt' AND r.art = 'rechnung' AND r.faellig >= ? AND k.name <> ?
        ORDER BY r.datum`,
    )
    .all(iso(heute), NEUER_KUNDE.name)
    .filter((r) => r.offen > 0);
  db.close();
  if (offen.length < 2) throw new Error("Zu wenige offene, noch nicht fällige Demo-Rechnungen für die CSV.");
  const tag = (minus) => {
    const d = new Date(heute);
    d.setDate(d.getDate() - minus);
    return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getFullYear()).slice(2)}`;
  };
  const euro = (cent) => `${cent < 0 ? "-" : ""}${Math.floor(Math.abs(cent) / 100)},${String(Math.abs(cent) % 100).padStart(2, "0")}`;
  const zeile = (minus, text, zweck, wer, iban, betrag) =>
    ["DE02120300000000202051", tag(minus), tag(minus), text, zweck, "", "", "", "", "", "", wer, iban, "", betrag, "EUR", "Umsatz gebucht"].map((f) => `"${f}"`).join(";");
  const [a, b] = offen;
  const zeilen = [
    zeile(0, "GUTSCHR. UEBERW.", `RE ${b.nummer} DOLMETSCHERLEISTUNG`, b.kunde, "DE75512108001245126199", euro(b.offen)),
    zeile(1, "KARTENZAHLUNG", "Bahnticket Musterstadt-Beispielstadt", "Deutsche Bahn Beispiel", "", "-38,40"),
    zeile(1, "GUTSCHR. UEBERW.", `Rechnung ${a.nummer} Danke`, "Landeskasse Musterland", "DE89370400440532013000", euro(a.offen)),
  ];
  const datei = join(AUS, CSV_NAME);
  writeFileSync(datei, Buffer.from([KOPF, ...zeilen].join("\r\n") + "\r\n", "latin1"));
  daten.info.csv = { datei: CSV_NAME, bezahlt: [a.nummer, b.nummer] };
  return datei;
}

// ---------------------------------------------------------------------------------------------
// Der Ablauf. Reihenfolge wie im Storyboard; jede Aufnahme baut auf der vorigen auf.

async function ablauf(b) {
  const gestern = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
  const R = ["kunde-waehlen", "position-datum", "position-leistung", "position-menge", "position-einheit", "position-preis", "position-betrag", "summe", "entwurf-speichern"];
  const kundeFelder = ["kunde-name", "kunde-anschrift", "kunde-steuerfall", "kunde-speichern"];

  meldung("Start und Datenschutz");
  await b.geh("/");
  await b.aufnahme("uebersicht-start", ["kpi-offen", "kpi-ueberfaellig", "aufgaben", "ki-zeile", "ki-schild"]);

  meldung("1 Einrichten");
  await b.klick("nav-einstellungen");
  await b.aufnahme("einstellungen-angaben", ["angaben", "nav-einstellungen"]);
  await b.zeige("vorlagen");
  await b.aufnahme("einstellungen-vorlagen", ["vorlagen", { name: "vorlage-zeile", zeile: "Dolmetscheinsatz", als: "vorlage-dolmetsch" }]);

  meldung("2 Kunden");
  await b.klick("nav-kunden");
  await b.aufnahme("kunden-liste", ["kunde-neu", { name: "kunde-zeile", zeile: "Integrationsamt", als: "zeile-integrationsamt" }]);
  await b.klick("kunde-neu");
  await b.aufnahme("kunde-neu-leer", kundeFelder);
  await b.tippen("kunde-name", NEUER_KUNDE.name);
  await b.aufnahme("kunde-neu-name", kundeFelder);
  await b.tippen("kunde-anschrift", NEUER_KUNDE.anschrift);
  await b.waehlen("kunde-steuerfall", NEUER_KUNDE.steuerfall);
  await b.aufnahme("kunde-neu-fertig", kundeFelder);
  await b.klick("kunde-speichern");
  await b.aufnahme("kunden-gespeichert", [{ name: "kunde-zeile", zeile: NEUER_KUNDE.name, als: "zeile-neuer-kunde" }]);

  meldung("3 Rechnung schreiben");
  await b.klick("nav-rechnungen");
  await b.aufnahme("rechnungen-liste", []);
  await b.klick("neue-rechnung");
  await b.aufnahme("rechnung-neu-leer", R);
  await b.waehlen("kunde-waehlen", NEUER_KUNDE.name);
  await b.aufnahme("rechnung-kunde", R);
  await b.wertSetzen("position-datum", gestern);
  await b.tippen("position-menge", LEISTUNG.menge);
  await b.aufnahme("rechnung-zeile", R);
  await b.tippen("position-leistung", LEISTUNG.text);
  await b.aufnahme("rechnung-leistung", R);
  await b.klick("entwurf-speichern");
  await b.aufnahme("rechnung-entwurf", ["vorschau", "ausstellen", "rechnung-nummer"]);
  await b.pdf("vorschau", "pdf-entwurf");
  await b.klick("ausstellen");
  await b.aufnahme("rechnung-ausgestellt", ["pdf-oeffnen", "rechnung-nummer"]);
  daten.info.rechnung = await b.page.$eval("h1", (h) => h.textContent.trim().replace(/\s+/g, " "));
  await b.pdf("pdf-oeffnen", "pdf-rechnung", { qr: true });

  meldung("4 Kontoauszug einlesen");
  await b.klick("nav-konto");
  await b.aufnahme("konto-vorher", ["csv-ablage"]);
  await b.hochladen(csvBauen(b.datenordner));
  await b.aufnahme("konto-import", ["csv-ablage", "vorschlag-stimmt", "reiter-eingaenge"]);
  const eingaenge = await b.klick("reiter-eingaenge", { pflicht: false });
  await b.aufnahme("konto-eingaenge-oben", ["vorschlag-stimmt", "reiter-eingaenge"]);
  await b.zeige("umsatz-zeile", { zeile: "SAMMEL" });
  await b.aufnahme("konto-eingaenge", [
    { name: "umsatz-zeile", zeile: "SAMMEL", als: "zeile-sammel" },
    { name: "umsatz-zeile", zeile: daten.info.csv.bezahlt[0], als: "zeile-auto-1" },
    { name: "umsatz-zeile", zeile: daten.info.csv.bezahlt[1], als: "zeile-auto-2" },
  ]);
  await b.nachOben();
  await b.klick("vorschlag-stimmt");
  await b.aufnahme("konto-stimmt", []);
  if (!eingaenge) meldung("  (kein Reiter „Eingänge“ gefunden)");

  meldung("5 Ausgaben einordnen");
  await b.klick("nav-konto");
  const bahn = { zeile: "Deutsche Bahn" };
  const A = [
    { name: "kategorie-waehlen", ...bahn },
    { name: "regel-merken", ...bahn },
    { name: "einordnen", ...bahn },
    { name: "umsatz-zeile", ...bahn, als: "zeile-bahn" },
  ];
  await b.zeige("umsatz-zeile", bahn);
  await b.aufnahme("ausgaben-liste", A);
  if (await b.vorwaehlen("kategorie-waehlen", "Fahrtkosten", bahn)) await b.aufnahme("ausgaben-auswahl", A);
  const selbst = await b.waehlen("kategorie-waehlen", "Fahrtkosten", bahn);
  if (!selbst) {
    // Bisherige Oberfläche: Häkchen „immer so“, dann „Einordnen“
    await b.aufnahme("ausgaben-gewaehlt", A);
    await b.klick("regel-merken", bahn);
    await b.aufnahme("ausgaben-merken", A);
    await b.klick("einordnen", bahn);
  } else {
    // Neue Oberfläche: Die Auswahl speichert sofort, danach kommt das Angebot „Immer so einordnen“.
    await b.aufnahme("ausgaben-gewaehlt", ["regel-merken"]);
    await b.klick("regel-merken");
  }
  await b.aufnahme("ausgaben-fertig", []);

  meldung("6 Übersicht");
  await b.klick("nav-uebersicht");
  await b.aufnahme("uebersicht", ["kpi-offen", "kpi-ueberfaellig", "aufgaben", "erinnerung", "ki-zeile", "ki-schild"]);
  await b.pdf("erinnerung", "pdf-erinnerung");

  meldung("7 Mails lesen lassen");
  // Nur ansehen und ausfüllen, nie absenden: kein Postfach wird verbunden.
  const mailZeile = { zeile: MAIL.betreff };
  await b.klick("nav-post");
  await b.aufnahme("post-liste", ["post-stand", "post-einrichten", { name: "mail-zeile", ...mailZeile, als: "zeile-mail" }]);
  await b.klick("post-einrichten");
  const P = ["mail-anbieter", "mail-adresse", "mail-passwort", "mail-hilfe"];
  await b.aufnahme("post-einrichten-leer", P);
  await b.waehlen("mail-anbieter", MAIL.anbieter);
  await b.tippen("mail-adresse", MAIL.adresse);
  await b.tippen("mail-passwort", MAIL.passwort);
  await b.aufnahme("post-einrichten-gewaehlt", P);
  await b.zeige("mail-datenschutz");
  await b.aufnahme("post-datenschutz", ["mail-datenschutz", "mail-ki"]);
  await b.klick("nav-post");
  await b.klick("mail-link", mailZeile);
  await b.aufnahme("post-mail", ["ki-auswertung"]);
  await b.klick("nav-uebersicht");
  const abrechnen = { name: "aufgabe-zeile", zeile: "abrechnen", als: "zeile-abrechnen" };
  await b.zeige("rechnung-vorbereiten");
  await b.aufnahme("post-uebersicht", ["rechnung-vorbereiten", abrechnen, "aufgaben", "ki-zeile",
    { name: "ki-schild", zeile: "abrechnen" }]);
  await b.klick("rechnung-vorbereiten");
  await b.aufnahme("post-entwurf", R);

  meldung("8 Kalender und Jahr");
  await b.klick("nav-kalender");
  await b.aufnahme("kalender", ["kalender-legende", "kalender-heute"]);
  await b.klick("nav-auswertung");
  await b.aufnahme("jahr", ["jahr-kacheln", "jahr-csv"]);

  meldung("9 Sicherung");
  await b.klick("nav-einstellungen");
  await b.zeige("ordner-oeffnen");
  await b.aufnahme("einstellungen-daten", ["datenordner", "ordner-oeffnen"]);
}

main().catch((fehler) => {
  console.error(`\nAufnahme abgebrochen: ${fehler.message}`);
  process.exitCode = 1;
});
