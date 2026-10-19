#!/bin/zsh
cd "$(dirname "$0")"
source "${AEGIS_VENV:-$HOME/venv}/bin/activate"
python app.py
