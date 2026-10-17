/* Bausteine für das Video-Tutorial „Arbeits-Assistent“.
 *
 * Baut aus dem Drehbuch (szenen.js, window.DREHBUCH) die Szenen als HyperFrames-Clips und eine
 * einzige GSAP-Zeitleiste. Die Boxen der Zielelemente kommen aus window.ZIELE (aufnahme.mjs).
 *
 * Szenenarten: titel, app, kapitel, ablauf, ordner, abspann.
 * Schritte einer App-Szene (Zeiten relativ zum Szenenbeginn, Sekunden):
 *   { t, bild: "<aufnahme>" }                         Bildwechsel (Überblendung)
 *   { t, bild: "<aufnahme>", scrollen: true }         dieselbe Seite, nur gescrollt: Inhalt gleitet
 *   { t, kamera: "ganz" | { ziel, zoom, dauer } }      Kamerafahrt; ziel = Name oder Liste von Namen
 *   { t, licht: "<ziel>" | { ziel, pad } | null }      Hervorhebung (Ring, Rest abgedunkelt)
 *   { t, maus: { ziel, punkt:[fx,fy], klick, dauer } | "aus" }   Mauszeiger, Klick mit Puls
 *   { t, tippen: { ziel, bild, dauer } }               Text erscheint Zeichen für Zeichen
 *   { t, ziehen: { ziel, datei, von:[x,y], dauer } }   Datei mit dem Zeiger hineinziehen
 *   { t, blatt: { bild, zoom:"qr" } | { zoom:"qr" } | null }  PDF als Blatt, Zoom auf ein Ziel
 *   { t, text: "Zeile" | ["Zeile 1", "Zeile 2"] | null }      Textleiste
 * Ziele heißen wie die data-tour-Attribute der App; „adresse“ ist die Adressleiste des Fensters.
 */
(function () {
  "use strict";

  const W = 1920;
  const H = 1080;
  const LEISTE = 56; // Höhe der Fensterleiste (in Screenshot-Pixeln)
  const FH = H + LEISTE;
  const UNTEN = 150; // von der Textleiste belegt
  const SICHT = { cx: W / 2, cy: (H - UNTEN) / 2 + 12 }; // Bildmitte über der Textleiste
  const RUHE = { s: 0.74, y: 92 }; // ganzes Fenster sichtbar
  RUHE.x = (W - RUHE.s * W) / 2;
  const RAND = 40;
  const ZOOM_MAX = 2; // Screenshots haben doppelte Pixeldichte
  const fehler = [];

  function warnen(text) {
    fehler.push(text);
    console.error("[Tutorial] " + text);
  }

  function el(tag, klasse, eltern, text) {
    const e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (text != null) e.textContent = text;
    if (eltern) eltern.appendChild(e);
    return e;
  }

  // GSAP: alles mit fromTo und ohne sofortiges Rendern, damit jeder Zeitpunkt aus der Zeit allein folgt.
  function tw(tl, ziel, von, nach, t) {
    tl.fromTo(ziel, von, Object.assign({ immediateRender: false }, nach), t);
  }

  function aufnahme(id) {
    const a = window.ZIELE && window.ZIELE.aufnahmen[id];
    if (!a) warnen(`Aufnahme „${id}“ fehlt. Erst „node aufnahme.mjs“ laufen lassen.`);
    return a;
  }

  // Box eines Ziels in Fenster-Koordinaten (Screenshot + Fensterleiste).
  function zielBox(bild, ziel, pad) {
    pad = pad || 0;
    const namen = Array.isArray(ziel) ? ziel : [ziel];
    let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    for (const name of namen) {
      let b;
      if (name === "adresse") b = { x: 660, y: 12 - LEISTE, w: 600, h: 32 };
      else {
        const a = aufnahme(bild);
        b = a && a.ziele[name];
      }
      if (!b) {
        warnen(`Ziel „${name}“ fehlt in Aufnahme „${bild}“.`);
        continue;
      }
      x1 = Math.min(x1, b.x);
      y1 = Math.min(y1, b.y);
      x2 = Math.max(x2, b.x + b.w);
      y2 = Math.max(y2, b.y + b.h);
    }
    if (x1 === Infinity) return null;
    return { x: x1 - pad, y: y1 + LEISTE - pad, w: x2 - x1 + 2 * pad, h: y2 - y1 + 2 * pad };
  }

  function begrenzen(v, min, max) {
    return Math.max(min, Math.min(max, v));
  }

  // Kameraeinstellung, die eine Box (Fenster-Koordinaten) in die Bildmitte über der Textleiste holt.
  function rahmen(box, zoom) {
    const s = begrenzen(zoom || Math.min(0.62 * W / box.w, 0.55 * (H - UNTEN) / box.h, 1.6), RUHE.s, ZOOM_MAX);
    const fw = s * W;
    const fh = s * FH;
    let x = SICHT.cx - s * (box.x + box.w / 2);
    let y = SICHT.cy - s * (box.y + box.h / 2);
    x = fw >= W ? begrenzen(x, W - fw, 0) : begrenzen(x, Math.min(RAND, (W - fw) / 2), Math.max(W - fw - RAND, (W - fw) / 2));
    y = fh >= H - UNTEN ? begrenzen(y, H - UNTEN - fh, 0) : begrenzen(y, RAND, H - UNTEN - fh);
    return { x, y, s };
  }

  function fensterBauen(eltern, adresse) {
    const f = el("div", "fenster", eltern);
    f.setAttribute("data-layout-allow-overflow", "");
    const leiste = el("div", "leiste", f);
    ["#ff5f57", "#febc2e", "#28c840"].forEach((farbe, i) => {
      const p = el("div", "punkt", leiste);
      p.style.left = 22 + i * 26 + "px";
      p.style.background = farbe;
    });
    el("div", "adresse", leiste, adresse);
    const ansicht = el("div", "ansicht", f);
    return { f, ansicht };
  }

  const ZEIGER_SVG =
    '<svg viewBox="0 0 28 40" aria-hidden="true"><path d="M3 3 L3 31.5 L9.6 25.3 L14.2 35.6 L18.9 33.6 L14.4 23.5 L23.3 23.5 Z" ' +
    'fill="#111" stroke="#fff" stroke-width="2.4" stroke-linejoin="round"/></svg>';

  // ---------------------------------------------------------------------------------------
  // Textleiste (für jede Szene eigene Instanz)

  function textleiste(sec, tl) {
    let aktiv = null;
    return {
      setzen(text, t) {
        if (aktiv) {
          tw(tl, aktiv, { opacity: 1 }, { opacity: 0, duration: 0.25, ease: "power1.in" }, t);
          aktiv = null;
        }
        if (!text) return;
        const platz = el("div", "textplatz", sec);
        const karte = el("div", "karte", platz);
        (Array.isArray(text) ? text : [text]).forEach((z) => el("span", "z", karte, z));
        gsap.set(karte, { opacity: 0 });
        tw(tl, karte, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.45, ease: "power2.out" }, t + 0.18);
        aktiv = karte;
      },
      ende(t) {
        if (aktiv) tw(tl, aktiv, { opacity: 1 }, { opacity: 0, duration: 0.3, ease: "power1.in" }, t);
        aktiv = null;
      },
    };
  }

  function kapitelmarke(sec, tl, t0, kapitel) {
    const m = el("div", "marke", sec);
    el("div", "nr", m, String(kapitel.nummer));
    el("div", "", m, kapitel.titel);
    gsap.set(m, { opacity: 0 });
    tw(tl, m, { opacity: 0, x: -10 }, { opacity: 1, x: 0, duration: 0.45, ease: "power2.out" }, t0 + 0.35);
    return m;
  }

  // ---------------------------------------------------------------------------------------
  // Szenenarten

  const BAU = {};

  // App-Szene: Screenshots im Fenster mit Kamera, Zeiger, Hervorhebung, Tippen, Blatt, Textleiste.
  BAU.app = function (sec, s, t0, tl, film) {
    const { f, ansicht } = fensterBauen(sec, film.adresse);
    const bilder = {};
    let z = 1;
    const bildEl = (id) => {
      if (bilder[id]) return bilder[id];
      const a = aufnahme(id);
      const img = el("img", "", ansicht);
      img.src = "assets/aufnahmen/" + (a ? a.datei : id + ".png");
      img.alt = "";
      gsap.set(img, { opacity: 0 });
      bilder[id] = img;
      return img;
    };
    (s.bilder || []).forEach(bildEl);
    let aktuell = s.start || (s.bilder && s.bilder[0]);
    gsap.set(bildEl(aktuell), { opacity: 1, zIndex: z++ });

    let kam = { x: RUHE.x, y: RUHE.y, s: RUHE.s };
    if (s.kamera && s.kamera !== "ganz") kam = rahmen(zielBox(aktuell, s.kamera.ziel, s.kamera.pad || 0), s.kamera.zoom);
    gsap.set(f, { x: kam.x, y: kam.y, scale: kam.s });

    const zeiger = el("div", "zeiger", f);
    zeiger.innerHTML = ZEIGER_SVG;
    let maus = { x: W * 0.6, y: H * 0.72, an: false };
    gsap.set(zeiger, { x: maus.x, y: maus.y, opacity: 0 });

    const licht = el("div", "licht", f);
    let lichtBox = null;
    gsap.set(licht, { opacity: 0, left: 0, top: 0, width: 10, height: 10 });

    const text = textleiste(sec, tl);
    const marke = s.kapitel ? kapitelmarke(sec, tl, t0, s.kapitel) : null;

    if (s.ein !== false) tw(tl, f, { opacity: 0 }, { opacity: 1, duration: 0.5, ease: "power1.out" }, t0);

    function wechsel(id, t, dauer) {
      if (id === aktuell) return;
      const img = bildEl(id);
      tl.set(img, { zIndex: z++, y: 0 }, t);
      tw(tl, img, { opacity: 0 }, { opacity: 1, duration: dauer, ease: "power1.inOut" }, t);
      aktuell = id;
    }

    // Scroll-Übergang zwischen zwei Aufnahmen derselben Seite: Der Inhalt gleitet, feste und
    // klebende Bereiche (Kopfzeile, Seitenleiste) bleiben stehen. Keine Doppelbilder.
    function scrollen(id, t, dauer) {
      if (id === aktuell) return;
      const alt = aufnahme(aktuell);
      const neu = aufnahme(id);
      const d = alt && neu ? neu.scroll.y - alt.scroll.y : 0;
      if (!d) return wechsel(id, t, 0.35);
      const altEl = bildEl(aktuell);
      const neuEl = bildEl(id);
      const art = { duration: dauer, ease: "power2.inOut" };
      // Das hereinkommende Bild ohne seine festen Ränder (Kopfzeile oben, Seitenleiste links/rechts),
      // sonst wandert deren Kopie mit durchs Bild.
      const rand = { o: 0, r: 0, u: 0, l: 0 };
      for (const r of neu.fest || []) {
        if (r.y <= 1 && r.w >= W * 0.9) rand.o = Math.max(rand.o, r.y + r.h);
        else if (r.y + r.h >= H - 1 && r.w >= W * 0.9) rand.u = Math.max(rand.u, H - r.y);
        else if (r.x <= 1 && r.h >= H * 0.9) rand.l = Math.max(rand.l, r.x + r.w);
        else if (r.x + r.w >= W - 1 && r.h >= H * 0.9) rand.r = Math.max(rand.r, W - r.x);
      }
      tl.set(neuEl, { zIndex: z++, opacity: 1, y: d, clipPath: `inset(${rand.o}px ${rand.r}px ${rand.u}px ${rand.l}px)` }, t);
      tw(tl, neuEl, { y: d }, Object.assign({ y: 0 }, art), t);
      tl.set(neuEl, { clipPath: "inset(0px 0px 0px 0px)" }, t + dauer);
      tw(tl, altEl, { y: 0 }, Object.assign({ y: -d }, art), t);
      tl.set(altEl, { opacity: 0, y: 0 }, t + dauer);
      for (const r of neu.fest || []) {
        const flicken = el("img", "", ansicht);
        flicken.src = "assets/aufnahmen/" + neu.datei;
        flicken.alt = "";
        gsap.set(flicken, { opacity: 0, clipPath: `inset(${r.y}px ${W - r.x - r.w}px ${H - r.y - r.h}px ${r.x}px)` });
        tl.set(flicken, { zIndex: z++, opacity: 1 }, t);
        tl.set(flicken, { opacity: 0 }, t + dauer);
      }
      aktuell = id;
    }

    function kamera(k, t) {
      let neu;
      if (k === "ganz") neu = { x: RUHE.x, y: RUHE.y, s: RUHE.s };
      else {
        const box = zielBox(k.bild || aktuell, k.ziel, k.pad || 0);
        if (!box) return;
        neu = rahmen(box, k.zoom);
      }
      const dauer = (k && k.dauer) || 1.2;
      tw(tl, f, { x: kam.x, y: kam.y, scale: kam.s }, { x: neu.x, y: neu.y, scale: neu.s, duration: dauer, ease: "power2.inOut" }, t);
      kam = neu;
    }

    function lichtSetzen(l, t) {
      if (!l) {
        if (lichtBox) tw(tl, licht, { opacity: 1 }, { opacity: 0, duration: 0.35, ease: "power1.inOut" }, t);
        lichtBox = null;
        return;
      }
      const spec = typeof l === "string" || Array.isArray(l) ? { ziel: l } : l;
      const box = zielBox(spec.bild || aktuell, spec.ziel, spec.pad == null ? 8 : spec.pad);
      if (!box) return;
      const neu = { left: box.x, top: box.y, width: box.w, height: box.h };
      if (lichtBox) tw(tl, licht, lichtBox, Object.assign({ duration: 0.6, ease: "power2.inOut" }, neu), t);
      else {
        tl.set(licht, neu, t);
        tw(tl, licht, { opacity: 0 }, { opacity: 1, duration: 0.4, ease: "power1.inOut" }, t);
      }
      lichtBox = neu;
    }

    function punkt(m) {
      const box = zielBox(m.bild || aktuell, m.ziel, 0);
      if (!box) return null;
      const p = m.punkt || [0.5, 0.55];
      return { x: box.x + box.w * p[0], y: box.y + box.h * p[1] };
    }

    function puls(p, t) {
      const r = el("div", "puls", f);
      gsap.set(r, { x: p.x, y: p.y, opacity: 0, scale: 0.3 });
      tw(tl, r, { opacity: 0.95, scale: 0.3 }, { opacity: 0, scale: 1.5, duration: 0.6, ease: "power2.out" }, t);
      tw(tl, zeiger, { scale: 1 }, { scale: 0.86, duration: 0.09, ease: "power1.out" }, t);
      tw(tl, zeiger, { scale: 0.86 }, { scale: 1, duration: 0.16, ease: "power1.out" }, t + 0.09);
    }

    function zeigerZeigen(t, von) {
      if (maus.an) return;
      if (von) maus = { x: von[0], y: von[1], an: false };
      tl.set(zeiger, { x: maus.x, y: maus.y }, t);
      tw(tl, zeiger, { opacity: 0 }, { opacity: 1, duration: 0.3, ease: "power1.out" }, t);
      maus.an = true;
    }

    function mausBewegen(m, t) {
      if (m === "aus") {
        if (maus.an) tw(tl, zeiger, { opacity: 1 }, { opacity: 0, duration: 0.3 }, t);
        maus.an = false;
        return;
      }
      const ziel = punkt(m);
      if (!ziel) return;
      const neu = !maus.an;
      zeigerZeigen(t, m.von);
      const start = t + (neu ? 0.25 : 0);
      const dauer = m.dauer || 0.9;
      tw(tl, zeiger, { x: maus.x, y: maus.y }, { x: ziel.x, y: ziel.y, duration: dauer, ease: "power2.inOut" }, start);
      maus.x = ziel.x;
      maus.y = ziel.y;
      if (m.klick) puls(ziel, start + dauer + 0.08);
    }

    function tippen(tp, t) {
      const a = aufnahme(tp.bild);
      const b = a && a.ziele[tp.ziel];
      if (!b) return warnen(`Tippen: Ziel „${tp.ziel}“ fehlt in „${tp.bild}“.`);
      const dauer = tp.dauer || 1.2;
      if (!b.text || b.text.zeilen > 1) {
        wechsel(tp.bild, t, dauer);
        return;
      }
      // Leeres Feld: deckt den Platzhalter ab, sobald das Tippen beginnt (Pfeile am rechten Rand bleiben).
      const innen = b.text.innen;
      if (innen && b.text.grund) {
        const deckel = el("div", "", ansicht);
        Object.assign(deckel.style, {
          position: "absolute", background: b.text.grund, left: b.text.x - 2 + "px", top: innen.y + 1 + "px",
          width: Math.max(0, innen.x + innen.w - 30 - (b.text.x - 2)) + "px", height: Math.max(0, innen.h - 2) + "px",
        });
        gsap.set(deckel, { opacity: 0 });
        tl.set(deckel, { zIndex: z++, opacity: 1 }, t);
      }
      const img = el("img", "", ansicht);
      img.src = "assets/aufnahmen/" + a.datei;
      img.alt = "";
      const oben = b.y - 3;
      const unten = H - (b.y + b.h) - 3;
      const links = b.text.x - 2;
      const rechtsVon = W - links;
      const rechtsBis = W - (b.text.x + b.text.w + 3);
      const clip = (rechts) => `inset(${oben}px ${rechts}px ${unten}px ${links}px)`;
      gsap.set(img, { opacity: 0, clipPath: clip(rechtsVon) });
      tl.set(img, { zIndex: z++, opacity: 1, clipPath: clip(rechtsVon) }, t);
      if (b.text.stellen && b.text.stellen.length) {
        // Zeichen für Zeichen, genau an den Zeichengrenzen
        const n = b.text.stellen.length;
        b.text.stellen.forEach((breite, i) => tl.set(img, { clipPath: clip(W - (b.text.x + breite + (i === n - 1 ? 3 : 0.5))) }, t + ((i + 1) * dauer) / n));
      } else {
        tw(tl, img, { clipPath: clip(rechtsVon) }, { clipPath: clip(rechtsBis), duration: dauer, ease: `steps(${Math.max(1, b.text.zeichen)})` }, t);
      }
      // Danach das ganze neue Bild: Was sich sonst noch ändert (Preis, Summe), erscheint sanft.
      wechsel(tp.bild, t + dauer + 0.15, 0.4);
    }

    function ziehen(zh, t) {
      const ziel = punkt({ ziel: zh.ziel, punkt: zh.punkt });
      if (!ziel) return;
      const von = zh.von || [W - 260, H - 160];
      const chip = el("div", "datei", f);
      el("div", "symbol", chip, "CSV");
      el("div", "", chip, zh.datei || "Umsaetze.csv");
      const versatz = { x: 22, y: 30 };
      gsap.set(chip, { x: von[0] + versatz.x, y: von[1] + versatz.y, opacity: 0 });
      if (maus.an) {
        tw(tl, zeiger, { x: maus.x, y: maus.y }, { x: von[0], y: von[1], duration: 0.5, ease: "power2.inOut" }, t - 0.6);
        maus.x = von[0];
        maus.y = von[1];
      } else zeigerZeigen(t - 0.4, von);
      tw(tl, chip, { opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: 0.35, ease: "power2.out" }, t);
      const dauer = zh.dauer || 1.5;
      tw(tl, zeiger, { x: von[0], y: von[1] }, { x: ziel.x, y: ziel.y, duration: dauer, ease: "power2.inOut" }, t + 0.35);
      tw(tl, chip, { x: von[0] + versatz.x, y: von[1] + versatz.y }, { x: ziel.x + versatz.x, y: ziel.y + versatz.y, duration: dauer, ease: "power2.inOut" }, t + 0.35);
      tw(tl, chip, { opacity: 1, scale: 1 }, { opacity: 0, scale: 0.6, duration: 0.3, ease: "power2.in" }, t + 0.35 + dauer + 0.1);
      puls(ziel, t + 0.35 + dauer + 0.1);
      maus.x = ziel.x;
      maus.y = ziel.y;
    }

    // Blatt: ein PDF fährt über das abgedunkelte Fenster hoch; Zoom auf ein Ziel (z. B. QR-Code).
    let blatt = null;
    const schleier = el("div", "schleier", sec);
    gsap.set(schleier, { opacity: 0 });

    function blattRahmen(b, ziel) {
      if (!ziel) {
        const s = 880 / b.a.hoehe;
        return { x: (W - b.a.breite * s) / 2, y: 46, s };
      }
      const box = b.a.ziele[ziel];
      if (!box) {
        warnen(`Blatt: Ziel „${ziel}“ fehlt in „${b.id}“.`);
        return b.rahmen;
      }
      const s = Math.min(330 / box.w, 1.2);
      // Ziel in die Mitte, aber das Blatt füllt das Bild, wo es breit genug ist (keine Kante mitten im Bild).
      const bw = b.a.breite * s;
      const bh = b.a.hoehe * s;
      let x = SICHT.cx - s * (box.x + box.w / 2);
      let y = SICHT.cy - s * (box.y + box.h / 2) + 20;
      if (bw >= W) x = begrenzen(x, W - bw, 0);
      if (bh >= H) y = begrenzen(y, H - bh, 0);
      return { x, y, s };
    }

    function blattSetzen(bl, t) {
      if (!bl) {
        if (!blatt) return;
        const r = blatt.rahmen;
        tw(tl, blatt.e, { y: r.y, opacity: 1 }, { y: r.y + 160, opacity: 0, duration: 0.5, ease: "power2.in" }, t);
        tw(tl, schleier, { opacity: 1 }, { opacity: 0, duration: 0.5, ease: "power1.inOut" }, t + 0.1);
        blatt = null;
        return;
      }
      if (bl.bild) {
        const a = aufnahme(bl.bild);
        if (!a) return;
        if (blatt) blattSetzen(null, t - 0.55);
        const e = el("div", "blatt", sec);
        e.setAttribute("data-layout-allow-overflow", "");
        e.style.width = a.breite + "px";
        e.style.height = a.hoehe + "px";
        const img = el("img", "", e);
        img.src = "assets/aufnahmen/" + a.datei;
        img.alt = "";
        blatt = { id: bl.bild, a, e, rahmen: null };
        const r = blattRahmen(blatt, null);
        blatt.rahmen = r;
        gsap.set(e, { x: r.x, y: r.y + 160, scale: r.s, opacity: 0 });
        tw(tl, schleier, { opacity: 0 }, { opacity: 1, duration: 0.5, ease: "power1.inOut" }, t);
        tw(tl, e, { y: r.y + 160, opacity: 0 }, { y: r.y, opacity: 1, duration: 0.7, ease: "power3.out" }, t);
      }
      if (bl.zoom && blatt) {
        const zt = t + (bl.bild ? 1.0 : 0);
        const box = blatt.a.ziele[bl.zoom];
        if (box) {
          const ring = el("div", "ring", blatt.e);
          // Abstand oben, rechts, unten, links (Bildpixel); unten Platz für eine Beschriftung unter dem Ziel.
          const seite = Math.round(box.w * 0.27);
          const [po, pr, pu, pl] = bl.ringPad || [44, seite, Math.round(box.h * 0.42), seite];
          Object.assign(ring.style, { left: box.x - pl + "px", top: box.y - po + "px", width: box.w + pl + pr + "px", height: box.h + po + pu + "px" });
          gsap.set(ring, { opacity: 0 });
          tw(tl, ring, { opacity: 0 }, { opacity: 1, duration: 0.5, ease: "power1.inOut" }, zt + 1.0);
        }
        const r0 = blatt.rahmen;
        const r1 = blattRahmen(blatt, bl.zoom);
        tw(tl, blatt.e, { x: r0.x, y: r0.y, scale: r0.s }, { x: r1.x, y: r1.y, scale: r1.s, duration: 1.4, ease: "power2.inOut" }, zt);
        blatt.rahmen = r1;
      }
    }

    // Schritte in zeitlicher Reihenfolge
    const schritte = (s.schritte || []).slice().sort((a, b) => a.t - b.t);
    for (const st of schritte) {
      const t = t0 + st.t;
      if (st.bild && st.scrollen) scrollen(st.bild, t, st.dauer || 0.9);
      else if (st.bild) wechsel(st.bild, t, st.blende || 0.35);
      if ("kamera" in st) kamera(st.kamera, t);
      if ("licht" in st) lichtSetzen(st.licht, t);
      if (st.maus) mausBewegen(st.maus, t);
      if (st.tippen) tippen(st.tippen, t);
      if (st.ziehen) ziehen(st.ziehen, t);
      if ("blatt" in st) blattSetzen(st.blatt, t);
      if ("text" in st) text.setzen(st.text, t);
    }

    const ende = t0 + s.dauer;
    if (s.aus !== false) {
      text.ende(ende - 0.45);
      tw(tl, f, { opacity: 1 }, { opacity: 0, duration: 0.4, ease: "power1.in" }, ende - 0.45);
      if (marke) tw(tl, marke, { opacity: 1 }, { opacity: 0, duration: 0.3 }, ende - 0.45);
      if (blatt) tw(tl, [blatt.e, schleier], { opacity: 1 }, { opacity: 0, duration: 0.4 }, ende - 0.45);
    }
  };

  // Titel: Symbol, Titel, Unterzeile; das App-Fenster steigt auf und fährt am Ende in die Ruhelage.
  BAU.titel = function (sec, s, t0, tl, film) {
    const kopf = el("div", "titel-kopf", sec);
    kopf.setAttribute("data-layout-allow-overlap", "");
    const symbol = el("div", "symbol-app", kopf, "A");
    const titel = el("div", "t-titel", kopf, s.titel);
    const zeilen = (s.zeilen || []).map((z) => el("div", "t-zeile", kopf, z));
    const { f, ansicht } = fensterBauen(sec, film.adresse);
    const a = aufnahme(s.bild);
    const img = el("img", "", ansicht);
    img.src = "assets/aufnahmen/" + (a ? a.datei : "");
    img.alt = "";
    const tief = { x: RUHE.x, y: 640, s: RUHE.s };
    gsap.set(f, { x: tief.x, y: H + 40, scale: tief.s });
    const teile = [symbol, titel, ...zeilen];
    gsap.set(teile, { opacity: 0 });
    teile.forEach((e, i) => tw(tl, e, { opacity: 0, y: 26 }, { opacity: 1, y: 0, duration: 0.8, ease: "power3.out" }, t0 + 0.25 + i * 0.18));
    tw(tl, f, { y: H + 40 }, { y: tief.y, duration: 1.8, ease: "power3.out" }, t0 + 0.6);
    const raus = t0 + s.dauer - 1.25;
    tw(tl, teile, { opacity: 1, y: 0 }, { opacity: 0, y: -30, duration: 0.6, ease: "power2.in", stagger: 0.05 }, raus);
    tw(tl, f, { x: tief.x, y: tief.y, scale: tief.s }, { x: RUHE.x, y: RUHE.y, scale: RUHE.s, duration: 1.1, ease: "power2.inOut" }, raus + 0.1);
  };

  // Kapitelkarte: Nummer, Titel, Fortschrittspunkte.
  BAU.kapitel = function (sec, s, t0, tl, film) {
    const m = el("div", "mitte", sec);
    const nr = el("div", "k-nr", m, "Kapitel " + s.nummer);
    const titel = el("div", "k-titel", m, s.titel);
    const punkte = el("div", "k-punkte", m);
    for (let i = 1; i <= film.kapitelAnzahl; i++) el("span", i === s.nummer ? "an" : "", punkte);
    const teile = [nr, titel, punkte];
    gsap.set(teile, { opacity: 0 });
    teile.forEach((e, i) => tw(tl, e, { opacity: 0, y: 18 }, { opacity: 1, y: 0, duration: 0.55, ease: "power3.out" }, t0 + 0.08 + i * 0.1));
    tw(tl, teile, { opacity: 1 }, { opacity: 0, duration: 0.35, ease: "power1.in" }, t0 + s.dauer - 0.4);
  };

  // Ordner und Sicherung: Finder-ähnliche Karte mit dem Datenordner, Pfeil zu Time Machine.
  const SYMBOL = {
    ordner:
      '<svg viewBox="0 0 64 52" aria-hidden="true"><path d="M4 9a5 5 0 0 1 5-5h15l6 6h25a5 5 0 0 1 5 5v28a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z" fill="#4f95e8"/>' +
      '<path d="M4 17a3 3 0 0 1 3-3h50a3 3 0 0 1 3 3v26a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z" fill="#74b0f4"/></svg>',
    datei:
      '<svg viewBox="0 0 52 64" aria-hidden="true"><path d="M6 4h28l14 14v40a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3z" fill="#fff" stroke="#c7cbd3" stroke-width="2.5"/>' +
      '<path d="M34 4v11a3 3 0 0 0 3 3h11" fill="#eef0f3" stroke="#c7cbd3" stroke-width="2.5"/>' +
      '<rect x="11" y="30" width="30" height="4" rx="2" fill="#c7cbd3"/><rect x="11" y="39" width="30" height="4" rx="2" fill="#c7cbd3"/><rect x="11" y="48" width="20" height="4" rx="2" fill="#c7cbd3"/></svg>',
    sicherung:
      '<svg viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="38" fill="none" stroke="#111827" stroke-width="7"/>' +
      '<path d="M60 38v23l15 10" fill="none" stroke="#111827" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<path d="M22 60a38 38 0 1 0 11-27" fill="none" stroke="var(--akzent)" stroke-width="8" stroke-linecap="round"/>' +
      '<path d="M20 22l13 11-16 6z" fill="var(--akzent)"/></svg>',
  };

  BAU.ordner = function (sec, s, t0, tl) {
    const buehne = el("div", "ordner-buehne", sec);
    const karte = el("div", "finder", buehne);
    const kopf = el("div", "finder-kopf", karte);
    ["#ff5f57", "#febc2e", "#28c840"].forEach((farbe) => (el("span", "punkt", kopf).style.background = farbe));
    const titel = el("div", "finder-titel", kopf);
    el("span", "symbol-klein", titel).innerHTML = SYMBOL.ordner;
    el("span", "", titel, s.ordner || "Arbeits-Assistent");
    const zeilen = (s.eintraege || []).map((e) => {
      const z = el("div", "finder-zeile", karte);
      el("span", "symbol-zeile", z).innerHTML = SYMBOL[e.symbol] || SYMBOL.datei;
      const txt = el("div", "finder-text", z);
      el("div", "finder-name", txt, e.name);
      if (e.text) el("div", "finder-info", txt, e.text);
      return z;
    });
    const pfeil = el("div", "ordner-pfeil", buehne);
    pfeil.innerHTML = '<svg viewBox="0 0 220 40" aria-hidden="true"><path class="linie" d="M6 20H196" fill="none" stroke="var(--akzent)" stroke-width="5" stroke-linecap="round" stroke-dasharray="1 15"/>' +
      '<path d="M190 8l18 12-18 12" fill="none" stroke="var(--akzent)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    const ziel = el("div", "sicherung", buehne);
    el("div", "sicherung-symbol", ziel).innerHTML = SYMBOL.sicherung;
    el("div", "sicherung-name", ziel, s.ziel || "Time Machine");
    if (s.zielText) el("div", "sicherung-info", ziel, s.zielText);

    const marke = s.kapitel ? kapitelmarke(sec, tl, t0, s.kapitel) : null;
    const text = textleiste(sec, tl);
    gsap.set([karte, pfeil, ziel, ...zeilen], { opacity: 0 });
    tw(tl, karte, { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.7, ease: "power3.out" }, t0 + 0.15);
    zeilen.forEach((z, i) => tw(tl, z, { opacity: 0, x: -12 }, { opacity: 1, x: 0, duration: 0.45, ease: "power2.out" }, t0 + 0.55 + i * 0.15));
    tw(tl, pfeil, { opacity: 0, x: -24 }, { opacity: 1, x: 0, duration: 0.6, ease: "power2.out" }, t0 + 1.3);
    tw(tl, ziel, { opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: 0.6, ease: "power3.out" }, t0 + 1.6);
    (s.schritte || []).forEach((st) => "text" in st && text.setzen(st.text, t0 + st.t));
    const ende = t0 + s.dauer;
    text.ende(ende - 0.45);
    tw(tl, buehne, { opacity: 1 }, { opacity: 0, duration: 0.4, ease: "power1.in" }, ende - 0.45);
    if (marke) tw(tl, marke, { opacity: 1 }, { opacity: 0, duration: 0.3 }, ende - 0.45);
  };

  // Ablauf: Stationen in einer Reihe mit Pfeilen (z. B. Mail → KI → Hinweis → Entwurf).
  // Symbole wie in der App (Strichsymbole aus arbeitsassistent/templates/_symbole.html).
  const STRICH = {
    post: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 7 8.5 6 8.5-6"/>',
    funken: '<path d="M11 3.5 12.9 9l5.6 2-5.6 2-1.9 5.5L9.1 13 3.5 11l5.6-2z"/><path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
    hinweis: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>',
    rechnung: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
    schloss: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  };
  const strich = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${STRICH[name] || ""}</svg>`;

  BAU.ablauf = function (sec, s, t0, tl) {
    const buehne = el("div", "ablauf-buehne", sec);
    const teile = [];
    (s.stationen || []).forEach((st, i) => {
      if (i) {
        const p = el("div", "ablauf-pfeil", buehne);
        p.innerHTML = '<svg viewBox="0 0 90 30" aria-hidden="true"><path d="M4 15H74" fill="none" stroke="var(--akzent)" stroke-width="4" stroke-linecap="round" stroke-dasharray="1 12"/>' +
          '<path d="M70 6l12 9-12 9" fill="none" stroke="var(--akzent)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        teile.push(p);
      }
      const k = el("div", "station" + (st.betont ? " betont" : ""), buehne);
      el("div", "station-symbol", k).innerHTML = strich(st.symbol);
      el("div", "station-titel", k, st.titel);
      if (st.text) {
        const z = el("div", "station-text", k);
        if (st.schloss) el("span", "station-schloss", z).innerHTML = strich("schloss");
        el("span", "", z, st.text);
      }
      if (st.schild) {
        const sch = el("span", "station-schild", k);
        sch.innerHTML = strich("funken");
        el("span", "", sch, st.schild);
      }
      teile.push(k);
    });
    const marke = s.kapitel ? kapitelmarke(sec, tl, t0, s.kapitel) : null;
    const text = textleiste(sec, tl);
    gsap.set(teile, { opacity: 0 });
    teile.forEach((e, i) => tw(tl, e, { opacity: 0, y: 18 }, { opacity: 1, y: 0, duration: 0.5, ease: "power2.out" }, t0 + 0.25 + i * 0.32));
    (s.schritte || []).forEach((st) => "text" in st && text.setzen(st.text, t0 + st.t));
    const ende = t0 + s.dauer;
    text.ende(ende - 0.45);
    tw(tl, buehne, { opacity: 1 }, { opacity: 0, duration: 0.4, ease: "power1.in" }, ende - 0.45);
    if (marke) tw(tl, marke, { opacity: 1 }, { opacity: 0, duration: 0.3 }, ende - 0.45);
  };

  // Abspann: Symbol, Name, Zeilen, Hinweis.
  BAU.abspann = function (sec, s, t0, tl) {
    const m = el("div", "mitte", sec);
    const teile = [el("div", "symbol-app", m, "A"), el("div", "t-titel", m, s.titel)];
    (s.zeilen || []).forEach((z) => teile.push(el("div", "a-zeile", m, z)));
    if (s.hinweis) teile.push(el("div", "a-hinweis", m, s.hinweis));
    gsap.set(teile, { opacity: 0 });
    teile.forEach((e, i) => tw(tl, e, { opacity: 0, y: 22 }, { opacity: 1, y: 0, duration: 0.7, ease: "power3.out" }, t0 + 0.2 + i * 0.16));
  };

  // ---------------------------------------------------------------------------------------

  function bauen(drehbuch, root) {
    const tl = gsap.timeline({ paused: true });
    const film = {
      adresse: drehbuch.adresse || "127.0.0.1:8770",
      kapitelAnzahl: drehbuch.kapitelAnzahl || drehbuch.szenen.filter((s) => s.art === "kapitel").length,
    };
    let t0 = 0;
    drehbuch.szenen.forEach((s, i) => {
      const sec = el("section", "clip", root);
      sec.id = "szene-" + (i + 1) + "-" + s.art;
      sec.setAttribute("data-start", String(Math.round(t0 * 1000) / 1000));
      sec.setAttribute("data-duration", String(s.dauer));
      sec.setAttribute("data-track-index", "1");
      if (!BAU[s.art]) warnen(`Unbekannte Szenenart „${s.art}“.`);
      else BAU[s.art](sec, s, t0, tl, film);
      t0 += s.dauer;
    });
    const soll = Number(root.getAttribute("data-duration"));
    if (Math.abs(soll - t0) > 0.01) warnen(`data-duration ist ${soll}, das Drehbuch dauert ${Math.round(t0 * 100) / 100} s. „node rendern.mjs“ gleicht das an.`);
    window.__tutorial = { dauer: t0, fehler };
    return tl;
  }

  window.Tutorial = { bauen };
})();
