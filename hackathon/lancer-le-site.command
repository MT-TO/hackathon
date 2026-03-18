#!/bin/zsh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "Demarrage de Photo Desk..."
echo "Projet: $SCRIPT_DIR"
echo ""

(
  sleep 2
  open "http://127.0.0.1:5001"
) &

python3 app.py
