#!/bin/bash
# Installiert oder aktualisiert die Büroassistenz auf einem Mac (ohne Administratorrechte).
# Ihre Daten in ~/Arbeits-Assistent bleiben dabei immer erhalten. Der Ordner, die Hintergrunddienste
# (de.arbeitsassistent…) und der Schlüsselbund-Eintrag behalten den früheren Namen „Arbeits-Assistent“.
#
#   Aus dem von GitHub geladenen Ordner:   bash ~/Downloads/arbeits-assistent-main/installieren.sh
#   Aktualisieren ohne Download (Terminal): siehe README, Abschnitt „Aktualisieren“
#   Ohne die lokale KI (kein 8-GB-Download): … installieren.sh --ohne-ki
#   Wieder entfernen (Daten bleiben):       … installieren.sh --entfernen
set -euo pipefail

REPO="TheQuidOne/bueroassistenz"
# Lese-Schlüssel für ein privates Repo, nur im Schlüsselbund dieses Macs (nie in Dateien).
SCHLUESSEL_DIENST="Arbeits-Assistent GitHub"
PORT=8770
NAME="de.arbeitsassistent"
PLIST="$HOME/Library/LaunchAgents/$NAME.plist"
# Programmsymbol in „Programme“ unter Macintosh HD (für Administratoren ohne Passwort beschreibbar),
# sonst im persönlichen Programme-Ordner. Ältere Fassungen legten es immer dort ab.
if [ -w /Applications ]; then
    APP="/Applications/Büroassistenz.app"
else
    APP="$HOME/Applications/Büroassistenz.app"
fi
# Frühere Orte und Namen des Programmsymbols (bis 2026-10-17 hieß es „Arbeits-Assistent“)
ALTE_APPS=("$HOME/Applications/Büroassistenz.app" "$HOME/Applications/Arbeits-Assistent.app"
           "/Applications/Arbeits-Assistent.app")
VERKNUEPFUNG="$HOME/Desktop/Büroassistenz"
ALTE_VERKNUEPFUNG="$HOME/Desktop/Arbeits-Assistent"
LOG="$HOME/Library/Logs/Arbeits-Assistent.log"

# Lokale KI: Ollama nur auf diesem Rechner (127.0.0.1), mit eigenem Hintergrunddienst.
MODELL="gemma4:12b"
KI_ADRESSE="127.0.0.1:11434"
KI_ORDNER="$HOME/Library/Application Support/Arbeits-Assistent/ollama"
KI_NAME="de.arbeitsassistent.ollama"
KI_PLIST="$HOME/Library/LaunchAgents/$KI_NAME.plist"
# Wird nicht vom Installer angelegt, nur auf Wunsch von Hand (docs/entscheidungen.md, „Mac wach halten“).
WACH_NAME="de.arbeitsassistent.wachhalten"
WACH_PLIST="$HOME/Library/LaunchAgents/$WACH_NAME.plist"
KI_LOG="$HOME/Library/Logs/Arbeits-Assistent-KI.log"

schritt() { printf '\n\033[1m%s\033[0m\n' "$1"; }

uv_finden() {
    if command -v uv >/dev/null 2>&1; then command -v uv
    elif [ -x "$HOME/.local/bin/uv" ]; then echo "$HOME/.local/bin/uv"
    fi
}

ki_bereit() { curl -fs "http://$KI_ADRESSE/api/version" >/dev/null 2>&1; }

# ~/Applications nur entfernen, wenn außer Finder-Metadaten nichts mehr darin liegt.
persoenlichen_ordner_aufraeumen() {
    [ "$(ls -A "$HOME/Applications" 2>/dev/null)" = ".DS_Store" ] && rm -f "$HOME/Applications/.DS_Store"
    rmdir "$HOME/Applications" 2>/dev/null || true
}

# Hintergrunddienst (neu) anmelden. Nach dem Abmelden braucht macOS einen Moment, sonst scheitert
# das erneute Anmelden mit „Bootstrap failed: 5: Input/output error“ – das traf jedes Update.
dienst_laden() {
    local name="$1" plist="$2" bereich="gui/$(id -u)"
    launchctl bootout "$bereich/$name" 2>/dev/null || true
    for _ in $(seq 1 30); do
        launchctl print "$bereich/$name" >/dev/null 2>&1 || break
        sleep 0.5
    done
    for _ in 1 2 3; do
        launchctl bootstrap "$bereich" "$plist" && return 0
        sleep 2
    done
    echo "Der Hintergrunddienst $name ließ sich nicht starten." >&2
    return 1
}

# Ohne Pipe, damit „set -o pipefail“ nicht anschlägt, wenn find früh beendet wird.
ollama_finden() { [ -d "$KI_ORDNER" ] && find "$KI_ORDNER" -type f -name ollama -perm -u+x -print -quit 2>/dev/null; true; }

if [ "${1:-}" = "--entfernen" ]; then
    schritt "Büroassistenz wird entfernt (Ihre Daten bleiben erhalten) …"
    launchctl bootout "gui/$(id -u)/$NAME" 2>/dev/null || true
    launchctl bootout "gui/$(id -u)/$KI_NAME" 2>/dev/null || true
    launchctl bootout "gui/$(id -u)/$WACH_NAME" 2>/dev/null || true
    rm -f "$PLIST" "$KI_PLIST" "$WACH_PLIST" "$VERKNUEPFUNG" "$ALTE_VERKNUEPFUNG"
    rm -rf "$APP" "${ALTE_APPS[@]}" "$KI_ORDNER" 2>/dev/null || true
    persoenlichen_ordner_aufraeumen
    UV="$(uv_finden)"
    [ -n "$UV" ] && "$UV" tool uninstall arbeitsassistent 2>/dev/null || true
    echo "Fertig. Die Daten liegen weiter in $HOME/Arbeits-Assistent."
    echo "Das KI-Modell (etwa 8 GB) liegt in $HOME/.ollama und kann bei Bedarf gelöscht werden."
    exit 0
fi

OHNE_KI=0
[ "${1:-}" = "--ohne-ki" ] && OHNE_KI=1

if [ "$(uname)" != "Darwin" ]; then
    echo "Dieses Skript ist für macOS gedacht." >&2
    exit 1
fi

schritt "1/5  Programm finden"
QUELLE=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/pyproject.toml" ]; then
    QUELLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    # Ohne heruntergeladenen Ordner (z. B. per curl … | bash): aktuellen Stand von GitHub holen.
    QUELLE="$(mktemp -d)"
    LESESCHLUESSEL="$(security find-generic-password -s "$SCHLUESSEL_DIENST" -w 2>/dev/null || true)"
    if [ -n "$LESESCHLUESSEL" ]; then
        ADRESSE="https://api.github.com/repos/$REPO/tarball/main"
        KOPF="Authorization: Bearer $LESESCHLUESSEL"
    else
        ADRESSE="https://github.com/$REPO/archive/refs/heads/main.tar.gz"
        KOPF="Accept: */*"
    fi
    if ! curl -fsSL -H "$KOPF" "$ADRESSE" | tar -xz -C "$QUELLE" --strip-components=1; then
        echo "Der Download von GitHub hat nicht geklappt. Bitte den Ordner von GitHub herunterladen" >&2
        echo "(Code → Download ZIP) und dieses Skript von dort starten." >&2
        exit 1
    fi
fi
# macOS markiert heruntergeladene Dateien. Die Markierung würde den Start blockieren.
xattr -dr com.apple.quarantine "$QUELLE" 2>/dev/null || true
echo "$QUELLE"

schritt "2/5  Python-Umgebung einrichten (beim ersten Mal ein paar Minuten)"
UV="$(uv_finden)"
if [ -z "$UV" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
    UV="$HOME/.local/bin/uv"
fi
# Nur das von uv mitgebrachte Python nutzen. Der macOS-Platzhalter „python3“ würde sonst
# den Download der Apple-Entwicklerwerkzeuge anbieten, die hier nicht gebraucht werden.
UV_PYTHON_PREFERENCE=only-managed "$UV" tool install --force --reinstall --python 3.13 "$QUELLE"
PROGRAMM="$("$UV" tool dir --bin)/arbeitsassistent"

schritt "3/5  Im Hintergrund starten (auch nach jedem Anmelden, nur auf diesem Mac erreichbar)"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs" "$(dirname "$APP")"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$NAME</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROGRAMM</string>
    <string>--ohne-browser</string>
    <string>--port</string><string>$PORT</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
dienst_laden "$NAME" "$PLIST"

schritt "4/5  Programmsymbol anlegen"
rm -rf "$APP"
osacompile -o "$APP" -e "open location \"http://127.0.0.1:$PORT/\""
for alt in "${ALTE_APPS[@]}"; do
    if [ "$alt" != "$APP" ] && [ -e "$alt" ]; then
        rm -rf "$alt" 2>/dev/null || true
    fi
done
persoenlichen_ordner_aufraeumen
rm -f "$ALTE_VERKNUEPFUNG"
ln -sfn "$APP" "$VERKNUEPFUNG"
for _ in $(seq 1 30); do
    curl -fs "http://127.0.0.1:$PORT/gesund" >/dev/null 2>&1 && break
    sleep 1
done
open "http://127.0.0.1:$PORT/"

if [ "$OHNE_KI" = "1" ]; then
    schritt "Fertig (ohne KI)."
    echo "Die KI lässt sich später einrichten: dasselbe Skript ohne --ohne-ki ausführen."
    exit 0
fi

schritt "5/5  Lokale KI einrichten (das Programm läuft schon, Sie können es nebenher benutzen)"
OLLAMA=""
if ki_bereit; then
    echo "Ollama läuft bereits auf diesem Mac und wird mitbenutzt."
    OLLAMA="$(command -v ollama || true)"
else
    if [ -z "$(ollama_finden)" ]; then
        echo "Lade Ollama (etwa 160 MB) …"
        TMP="$(mktemp -d)"
        curl -fL --progress-bar "https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz" -o "$TMP/ollama.tgz"
        rm -rf "$KI_ORDNER"
        mkdir -p "$KI_ORDNER"
        tar -xzf "$TMP/ollama.tgz" -C "$KI_ORDNER"
        rm -rf "$TMP"
    fi
    # Je nach Version liegt das Programm in einem Unterordner des Archivs.
    OLLAMA="$(ollama_finden)"
    if [ -z "$OLLAMA" ]; then
        echo "Ollama ließ sich nicht entpacken. Das Programm läuft trotzdem, nur ohne KI." >&2
        exit 1
    fi
    cat > "$KI_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$KI_NAME</string>
  <key>ProgramArguments</key>
  <array><string>$OLLAMA</string><string>serve</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OLLAMA_HOST</key><string>$KI_ADRESSE</string>
    <key>OLLAMA_KEEP_ALIVE</key><string>5m</string>
    <key>OLLAMA_NO_CLOUD</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$KI_LOG</string>
  <key>StandardErrorPath</key><string>$KI_LOG</string>
</dict>
</plist>
PLIST
    dienst_laden "$KI_NAME" "$KI_PLIST"
    for _ in $(seq 1 30); do
        ki_bereit && break
        sleep 1
    done
fi

MODELLE="$(curl -fs "http://$KI_ADRESSE/api/tags" || true)"
if [[ "$MODELLE" == *"\"$MODELL\""* ]]; then
    echo "Das KI-Modell $MODELL ist schon da."
else
    echo "Lade das KI-Modell $MODELL (etwa 8 GB, nur einmal). Das kann je nach Leitung 10 bis 40 Minuten dauern …"
    if [ -n "$OLLAMA" ]; then
        OLLAMA_HOST="$KI_ADRESSE" "$OLLAMA" pull "$MODELL"
    else
        curl -fsS "http://$KI_ADRESSE/api/pull" -d "{\"model\": \"$MODELL\", \"stream\": false}" >/dev/null
    fi
fi

schritt "Fertig."
echo "Die Büroassistenz liegt unter „Programme“ und auf dem Schreibtisch."
echo "Die KI läuft auf diesem Mac. Unter „Post“ das Postfach verbinden, dann liest sie neue Mails mit."
echo "Ihre Daten: $HOME/Arbeits-Assistent (nur auf diesem Mac)."
echo "Den heruntergeladenen Ordner können Sie jetzt löschen."
