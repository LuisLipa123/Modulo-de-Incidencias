#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 no esta disponible en el PATH." >&2
  exit 1
fi

if [ -d "venv" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
elif [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
else
  "$PYTHON" -m venv ".venv"
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

pip install --quiet --upgrade pip
pip install --quiet -r "Incidencia/requirements.txt"

cd "$ROOT_DIR/Incidencia"

APP_URL="http://127.0.0.1:8080"
LOG_FILE="$ROOT_DIR/app.log"

python - <<'PY' >"$LOG_FILE" 2>&1 &
from app import create_app, init_db

app = create_app()
init_db(app)
app.run(debug=False, port=8080)
PY

APP_PID=$!

for _ in {1..30}; do
  if curl -fsS "$APP_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$APP_URL" >/dev/null 2>&1 || true
fi

echo "Servidor listo en $APP_URL (PID $APP_PID). Log: $LOG_FILE"
wait "$APP_PID"
