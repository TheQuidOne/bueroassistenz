#!/usr/bin/env python3
"""Misst die KI-Auswertung an erfundenen Test-Mails (evals/mails.json).

    .venv/bin/python scripts/ki_pruefen.py                          # gemma4:12b
    .venv/bin/python scripts/ki_pruefen.py gemma4:12b qwen3.5:9b
    .venv/bin/python scripts/ki_pruefen.py --fall privat --fall zahlungsavis -v

Braucht ein laufendes Ollama. Jedes Modell wird danach sofort entladen.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

from arbeitsassistent.ki import KiFehler, Ollama, auswerten  # noqa: E402
from arbeitsassistent.modell import Mail  # noqa: E402


def mail_aus(fall: dict) -> Mail:
    return Mail(
        message_id=f"<{fall['id']}@test.example>",
        datum=datetime.fromisoformat(fall["datum"]),
        absender_name=fall["von"],
        absender_adresse=fall["adresse"],
        betreff=fall["betreff"],
        text=fall["text"],
        an=fall.get("an", "jonas@muster-dolmetschen.example"),
        anhaenge=fall.get("anhaenge", []),
    )


def _flach(text) -> str:
    return re.sub(r"[\W_]+", "", str(text).casefold())


def vergleichen(a, soll: dict) -> list[tuple[str, bool, str]]:
    """Feld für Feld: (Feld, richtig?, Beschreibung des Fehlers)."""
    ergebnis = []
    for feld in ("arbeit", "art", "einsatz_beginn"):
        if feld in soll:
            ist, erwartet = getattr(a, feld), soll[feld]
            ok = ist in erwartet if isinstance(erwartet, list) else ist == erwartet  # Liste: mehrdeutig
            ergebnis.append((feld, ok, f"{ist!r} statt {erwartet!r}"))
    for feld in ("einsatz_datum", "frist"):
        if feld in soll:
            ist = getattr(a, feld)
            ist = ist.isoformat() if ist else ""
            ergebnis.append((feld, ist == soll[feld], f"{ist or '–'} statt {soll[feld] or '–'}"))
    if "dauer_stunden" in soll:
        erwartet = None if soll["dauer_stunden"] is None else Decimal(str(soll["dauer_stunden"]))
        ist = a.dauer_stunden
        ergebnis.append(("dauer_stunden", ist == erwartet, f"{ist} statt {erwartet}"))
    for feld in ("rechnungsnummer", "aktenzeichen"):
        if feld in soll:
            ist, erwartet = getattr(a, feld), soll[feld]
            if not erwartet:
                ok = ist == ""
            else:
                teile = [erwartet] if isinstance(erwartet, str) else erwartet
                ok = all(_flach(t) in _flach(ist) for t in teile)
            ergebnis.append((feld, ok, f"{ist or '–'!r} statt {erwartet or '–'!r}"))
    for feld in ("auftraggeber", "hinweis"):
        if feld in soll:
            ist, erwartet = getattr(a, feld), soll[feld]
            ok = ist == "" if erwartet == "" else any(w.casefold() in ist.casefold() for w in erwartet)
            ergebnis.append((feld, ok, f"{ist or '–'!r}"))
    return ergebnis


class MitRohantwort(Ollama):
    """Merkt sich die letzte Antwort des Modells vor dem Normalisieren (für -v)."""

    roh: dict = {}

    def chat_json(self, system, nutzer, schema):
        self.roh = super().chat_json(system, nutzer, schema)
        return self.roh


def ollama(pfad: str, daten: dict | None = None) -> dict:
    anfrage = urllib.request.Request("http://127.0.0.1:11434" + pfad,
                                     data=json.dumps(daten).encode() if daten is not None else None,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(anfrage, timeout=60) as antwort:
        return json.loads(antwort.read())


def pruefen(modell: str, daten: dict, faelle: list[dict], ausfuehrlich: bool) -> dict | None:
    ki = MitRohantwort(modell=modell, timeout=600)
    bereit, text = ki.status()
    if not bereit:
        print(f"{modell}: {text}")
        return None
    punkte, gesamt = Counter(), Counter()  # je Gruppe: Übung, Kontrollrunde 1, 2 …
    zeiten, fehlerfelder, grafik = [], Counter(), 0.0
    for nr, fall in enumerate(faelle):
        heute = date.fromisoformat(fall.get("heute", daten["heute"]))
        gruppe = f"Kontrollrunde {fall['kontrolle']}" if fall.get("kontrolle") else "Übung"
        beginn = time.time()
        try:
            a = auswerten(ki, mail_aus(fall), heute, daten["kunden"], daten["rechnungen"])
        except ValueError as e:  # unbrauchbare Antwort: alle Felder falsch
            a, fehler = None, str(e)
        zeiten.append(time.time() - beginn)
        if nr == 0:
            grafik = sum(m.get("size_vram", 0) for m in ollama("/api/ps")["models"]
                         if m["name"].startswith(modell)) / 2**30
        if a is None:
            gesamt[gruppe] += len(fall["soll"])
            fehlerfelder.update(fall["soll"])
            print(f"  {fall['id']:26}  0/{len(fall['soll']):<2} {zeiten[-1]:5.1f} s  FEHLER {fehler[:100]}")
            continue
        ergebnis = vergleichen(a, fall["soll"])
        richtig = sum(ok for _, ok, _ in ergebnis)
        punkte[gruppe] += richtig
        gesamt[gruppe] += len(ergebnis)
        falsch = [f"{feld}: {text}" for feld, ok, text in ergebnis if not ok]
        fehlerfelder.update(feld for feld, ok, _ in ergebnis if not ok)
        print(f"  {fall['id']:26} {richtig:>2}/{len(ergebnis):<2} {zeiten[-1]:5.1f} s  " + " | ".join(falsch),
              flush=True)
        if ausfuehrlich:
            print(f"      roh: {json.dumps(ki.roh, ensure_ascii=False)}")
    ollama("/api/generate", {"model": modell, "keep_alive": 0})
    danach = zeiten[1:] or zeiten
    return {"modell": modell, "punkte": punkte.total(), "gesamt": gesamt.total(),
            "gruppen": {g: (punkte[g], gesamt[g]) for g in sorted(gesamt)}, "erste": zeiten[0],
            "schnitt": sum(danach) / len(danach), "grafik": grafik, "fehlerfelder": fehlerfelder}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("modelle", nargs="*", default=["gemma4:12b"])
    parser.add_argument("--fall", action="append", help="nur diese Test-Mail (mehrfach möglich)")
    parser.add_argument("-v", action="store_true", help="Rohantwort des Modells zu jeder Mail zeigen")
    args = parser.parse_args()

    daten = json.loads((WURZEL / "evals" / "mails.json").read_text(encoding="utf-8"))
    faelle = [f for f in daten["mails"] if not args.fall or f["id"] in args.fall]
    ergebnisse = []
    for modell in args.modelle:
        print(f"… {modell}, {len(faelle)} Mails", flush=True)
        try:
            r = pruefen(modell, daten, faelle, args.v)
        except KiFehler as e:
            print(f"{modell}: {e}")
            continue
        if r:
            ergebnisse.append(r)
            print(f"{modell}: {r['punkte']}/{r['gesamt']} Punkte, erste Mail {r['erste']:.1f} s, "
                  f"danach Ø {r['schnitt']:.1f} s, Grafikspeicher {r['grafik']:.1f} GB")
            print("  davon " + ", ".join(f"{g} {p}/{n}" for g, (p, n) in r["gruppen"].items()))
            print("  Fehler je Feld: " + (", ".join(f"{f} {n}" for f, n in r["fehlerfelder"].most_common()) or "keine"))
    if len(ergebnisse) > 1:
        print("\nRangliste:")
        for r in sorted(ergebnisse, key=lambda r: (-r["punkte"], r["schnitt"])):
            print(f"  {r['modell']:20} {r['punkte']}/{r['gesamt']}  Ø {r['schnitt']:.1f} s/Mail")


if __name__ == "__main__":
    main()
