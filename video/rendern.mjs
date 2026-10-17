#!/usr/bin/env node
// Rendert das Tutorial mit HyperFrames.
//
//   node rendern.mjs              check, Render in hoher Qualität (export/tutorial.mp4),
//                                 Webfassung ≤ 12 MB (export/tutorial-web.mp4), Vorschaubild
//                                 (export/tutorial.jpg) und Prüfbilder (export/pruefbilder/)
//   node rendern.mjs --hilfe      dasselbe und danach Webfassung + Vorschaubild in die Hilfe der App:
//                                 ../arbeitsassistent/static/hilfe/tutorial.mp4 und tutorial.jpg
//   node rendern.mjs --entwurf    schneller Entwurf (export/tutorial-entwurf.mp4) und Prüfbilder
//   node rendern.mjs --nur-check  nur Länge abgleichen und `hyperframes check`
//
// HyperFrames läuft immer ohne Telemetrie und ohne Skills (Regel dieses Rechners).

import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import vm from "node:vm";

const VIDEO = dirname(fileURLToPath(import.meta.url));
const TUTORIAL = join(VIDEO, "tutorial");
const EXPORT = join(VIDEO, "export");
const HILFE = join(VIDEO, "..", "arbeitsassistent", "static", "hilfe");
const HF = "hyperframes@0.8.81";
const WEB_MB = 11.6; // Ziel ≤ 12 MB (dezimal, wie der Finder zählt), höchstens 15 MB
const BILD_KB = 250; // Vorschaubild (dezimal)
const BILD_ZEIT = 2.8; // Titel steht, Fenster ist aufgestiegen
const UMGEBUNG = { ...process.env, HYPERFRAMES_NO_TELEMETRY: "1", DO_NOT_TRACK: "1", HYPERFRAMES_SKIP_SKILLS: "1" };

const { values: arg } = parseArgs({
  options: {
    entwurf: { type: "boolean", default: false },
    hilfe: { type: "boolean", default: false },
    "nur-check": { type: "boolean", default: false },
    "ohne-check": { type: "boolean", default: false },
  },
});

function laufen(befehl, argumente, cwd = VIDEO) {
  const r = spawnSync(befehl, argumente, { cwd, env: UMGEBUNG, stdio: "inherit" });
  if (r.status !== 0) throw new Error(`${befehl} ${argumente.join(" ")} ist fehlgeschlagen (${r.status}).`);
}

const mb = (datei) => statSync(datei).size / 1e6;

// Länge aus dem Drehbuch und als data-duration ins Wurzelelement schreiben.
function dauerAbgleichen() {
  const kontext = { window: {} };
  vm.runInNewContext(readFileSync(join(TUTORIAL, "szenen.js"), "utf8"), kontext);
  const dauer = Math.round(kontext.window.DREHBUCH.szenen.reduce((s, z) => s + z.dauer, 0) * 1000) / 1000;
  const datei = join(TUTORIAL, "index.html");
  const html = readFileSync(datei, "utf8");
  const neu = html.replace(/(data-composition-id="main"[^>]*?data-duration=")[\d.]+(")/, `$1${dauer}$2`);
  if (neu === html && !html.includes(`data-duration="${dauer}"`)) throw new Error("data-duration im Wurzelelement nicht gefunden.");
  if (neu !== html) writeFileSync(datei, neu);
  console.log(`Länge laut Drehbuch: ${dauer} s`);
  return dauer;
}

function pruefbilder(video, dauer, ordner) {
  rmSync(ordner, { recursive: true, force: true });
  mkdirSync(ordner, { recursive: true });
  // Ein Bild alle 2 Sekunden, halbe Größe, als Kontaktbogen und einzeln.
  laufen("ffmpeg", ["-v", "error", "-i", video, "-vf", "fps=1/2,scale=960:-1", join(ordner, "bild-%03d.png")]);
  laufen("ffmpeg", ["-v", "error", "-i", video, "-vf", "fps=1/2,scale=480:-1,tile=4x" + Math.ceil(dauer / 2 / 4), "-frames:v", "1", join(ordner, "kontaktbogen.png")]);
  console.log(`Prüfbilder: ${ordner}`);
}

// H.264 (High, yuv420p, faststart) in zwei Durchgängen mit Zielbitrate: spielt in Safari und Chromium.
// qcomp 0.8 und aq-mode 3 geben Kamerafahrten und Scrollen mehr Bits; ruhige Bilder haben Reserve.
function webfassung(quelle, ziel, dauer) {
  const log = join(EXPORT, "ffmpeg2pass");
  let kbit = Math.floor((WEB_MB * 8e6) / dauer / 1000);
  for (let versuch = 0; versuch < 3; versuch++) {
    const gemeinsam = ["-y", "-v", "error", "-i", quelle, "-an", "-c:v", "libx264", "-preset", "slow", "-tune", "animation",
      "-profile:v", "high", "-level", "4.0", "-b:v", `${kbit}k`, "-maxrate", `${kbit * 3}k`, "-bufsize", `${kbit * 6}k`,
      "-pix_fmt", "yuv420p", "-x264-params", "qcomp=0.8:aq-mode=3", "-passlogfile", log];
    laufen("ffmpeg", [...gemeinsam, "-pass", "1", "-f", "mp4", "/dev/null"]);
    laufen("ffmpeg", [...gemeinsam, "-pass", "2", "-movflags", "+faststart", ziel]);
    if (mb(ziel) <= 12) break;
    kbit = Math.floor(kbit * 0.9);
  }
  for (const endung of ["-0.log", "-0.log.mbtree"]) rmSync(log + endung, { force: true });
  console.log(`Webfassung: ${ziel} (${mb(ziel).toFixed(1)} MB, ${kbit} kbit/s)`);
}

// Vorschaubild aus dem Titel, JPEG so gut wie möglich unter BILD_KB.
function vorschaubild(quelle, ziel) {
  const png = join(EXPORT, "vorschau.png");
  laufen("ffmpeg", ["-y", "-v", "error", "-ss", String(BILD_ZEIT), "-i", quelle, "-frames:v", "1", png]);
  for (const q of [90, 86, 82, 78, 74, 70, 64, 58]) {
    laufen("magick", [png, "-strip", "-sampling-factor", "4:2:0", "-interlace", "Plane", "-quality", String(q), ziel]);
    if (statSync(ziel).size <= BILD_KB * 1000) break;
  }
  rmSync(png, { force: true });
  console.log(`Vorschaubild: ${ziel} (${Math.round(statSync(ziel).size / 1000)} KB)`);
}

function main() {
  if (!existsSync(join(VIDEO, "aufnahmen", "ziele.js"))) throw new Error("Keine Aufnahmen. Erst: node aufnahme.mjs");
  const dauer = dauerAbgleichen();
  if (!arg["ohne-check"]) laufen("npx", ["--yes", HF, "check"], TUTORIAL);
  if (arg["nur-check"]) return;
  mkdirSync(EXPORT, { recursive: true });
  const ziel = join(EXPORT, arg.entwurf ? "tutorial-entwurf.mp4" : "tutorial.mp4");
  laufen("npx", ["--yes", HF, "render", "--output", ziel, "--fps", "30", "--workers", "4", "--quality", arg.entwurf ? "standard" : "high"], TUTORIAL);
  console.log(`Video: ${ziel} (${mb(ziel).toFixed(1)} MB)`);
  if (!arg.entwurf) {
    const web = join(EXPORT, "tutorial-web.mp4");
    const bild = join(EXPORT, "tutorial.jpg");
    webfassung(ziel, web, dauer);
    vorschaubild(ziel, bild);
    if (arg.hilfe) {
      mkdirSync(HILFE, { recursive: true });
      copyFileSync(web, join(HILFE, "tutorial.mp4"));
      copyFileSync(bild, join(HILFE, "tutorial.jpg"));
      console.log(`In die Hilfe kopiert: ${HILFE}`);
    }
  }
  pruefbilder(ziel, dauer, join(EXPORT, arg.entwurf ? "pruefbilder-entwurf" : "pruefbilder"));
}

try {
  main();
} catch (fehler) {
  console.error(`\n${fehler.message}`);
  process.exitCode = 1;
}
