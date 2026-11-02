#!/usr/bin/env bash
# Legt .env an bzw. ergänzt fehlende Werte. Vorhandenes bleibt.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
chmod 600 .env
setzen() {
  local name="$1" wert
  wert="$(grep -E "^$name=" .env | tail -1 | cut -d= -f2- || true)"
  if [ -z "$wert" ]; then
    wert="$(head -c 32 /dev/urandom | base64 | tr -d '/+=\n' | head -c 40)"
    if grep -qE "^$name=" .env; then sed -i "s|^$name=.*|$name=$wert|" .env; else echo "$name=$wert" >> .env; fi
    echo "  $name erzeugt"
  fi
}
setzen GEDAECHTNIS_DB_PW
setzen GEDAECHTNIS_KOPPLUNGSSCHLUESSEL
if ! docker network inspect ota_erweiterungen >/dev/null 2>&1; then
  echo "Hinweis: Das Netz ota_erweiterungen fehlt. Es kommt mit OTA (ab 2026-11, 'sudo make update')."
fi
echo
echo "Kopplungsschlüssel für OTA (Einstellungen → Erweiterungen → Hinzufügen):"
echo "  Adresse:  http://ota-gedaechtnis:8080"
echo "  Schlüssel: $(grep -E '^GEDAECHTNIS_KOPPLUNGSSCHLUESSEL=' .env | cut -d= -f2-)"
