#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/servidor"
if [[ ! -f .env ]]; then
  echo 'Configura servidor/.env copiando .env.example y colocando tus credenciales de MySQL y SECRET_KEY.'
  exit 1
fi
[[ -d .venv ]] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
