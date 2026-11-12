#!/usr/bin/env bash
# Start Otodom Analyzer: ./start.sh  (extra arguments go to Streamlit, e.g. ./start.sh --server.port 8502)
set -e

cd "$(dirname "$(readlink -f "$0")")"

if [ ! -f .venv/bin/activate ]; then
    echo "Brak środowiska .venv – utwórz je: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

source .venv/bin/activate
exec streamlit run app.py "$@"
