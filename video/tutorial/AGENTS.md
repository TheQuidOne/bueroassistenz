# AGENTS.md – HyperFrames-Komposition des Tutorials

Gilt zusätzlich zu `../../AGENTS.md` und `../README.md`.

- HyperFrames immer als `hyperframes@0.8.81` und mit
  `HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1 HYPERFRAMES_SKIP_SKILLS=1` aufrufen
  (die Skripte in `package.json` tun das). Keine Skills installieren, kein `publish`, kein `cloud`.
  `snapshot` nur mit `--describe false` (sonst gehen Bilder an Gemini, wenn `GEMINI_API_KEY` gesetzt ist).
- Keine `CLAUDE.md` anlegen. Legt `init` eine an, wird sie gelöscht.
- Inhalt des Videos steht in `szenen.js` (Drehbuch). Die Bausteine (Titel, Kapitelkarte,
  App-Szene mit Kamera, Mauszeiger, Hervorhebung, Textleiste, Blatt, Abspann) stehen in
  `assets/bausteine.js`, das Aussehen in `index.html`.
- `assets/aufnahmen` ist ein Link auf `../aufnahmen` (erzeugt von `../aufnahme.mjs`, nicht im Git).
  Ziele heißen wie die `data-tour`-Attribute der App.
- Die Gesamtlänge (`data-duration` des Wurzelelements) setzt `../rendern.mjs` aus dem Drehbuch.
  Nicht von Hand pflegen.
- Nur erfundene Daten. Keine Netzwerkzugriffe zur Laufzeit: GSAP und Schriften liegen in `assets/`.
