#!/bin/bash
# Doppelklick-Variante von installieren.sh.
exec bash "$(cd "$(dirname "$0")" && pwd)/installieren.sh" "$@"
