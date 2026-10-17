"""Startet die Büroassistenz und öffnet sie im Browser.

    python -m arbeitsassistent            # echte Daten in ~/Arbeits-Assistent
    python -m arbeitsassistent --demo     # erfundene Daten in ~/Arbeits-Assistent-Demo
"""

import argparse
import os
import threading
import urllib.request
import webbrowser
from pathlib import Path

STANDARD_PORT = 8770


def laeuft_schon(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/gesund", timeout=1) as antwort:
            return antwort.read() == b"arbeitsassistent"
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(prog="arbeitsassistent", description="Büroassistenz starten")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ARBEITSASSISTENT_PORT", STANDARD_PORT)))
    parser.add_argument("--daten", help="Datenordner (Standard: ~/Arbeits-Assistent)")
    parser.add_argument("--demo", action="store_true", help="mit erfundenen Beispieldaten starten")
    parser.add_argument("--ohne-browser", action="store_true", help="keinen Browser öffnen")
    args = parser.parse_args()

    if args.demo:
        args.daten = args.daten or str(Path.home() / "Arbeits-Assistent-Demo")
        if args.port == STANDARD_PORT:
            args.port += 1  # Demo und echte Daten können gleichzeitig laufen
    if args.daten:
        os.environ["ARBEITSASSISTENT_DATEN"] = args.daten
    adresse = f"http://127.0.0.1:{args.port}/"

    if laeuft_schon(args.port):
        if not args.ohne_browser:
            webbrowser.open(adresse)
        return

    from . import db, demo, postfach
    from .web import erstellen

    if args.demo:
        demo.fuellen(db.datenordner())
    app = erstellen()
    postfach.hintergrund(db.datenordner())  # Mails alle 15 Minuten abrufen und auswerten
    if not args.ohne_browser:
        threading.Timer(1.0, webbrowser.open, (adresse,)).start()
    print(f"Büroassistenz läuft auf {adresse} (Daten: {db.datenordner()})")
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
