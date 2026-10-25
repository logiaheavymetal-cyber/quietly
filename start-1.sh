#!/bin/sh
# Clave secreta nueva en cada arranque (no se guarda en ningún sitio)
SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
sed -i "s/CAMBIA_ESTA_CLAVE_ANTES_DE_ARRANCAR/$SECRET/" /etc/searxng/settings.yml
# Buscar Python (SearXNG ya lo trae)
PY=""
for c in python3 python /usr/local/searxng/.venv/bin/python /usr/bin/python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -n "$PY" ]; then
  (cd /app && PORT="${PORT:-10000}" SEARX_URL=http://127.0.0.1:8080 "$PY" server.py) &
else
  echo "No se encontró Python para Quietly" >&2
fi
unset PORT
for e in /usr/local/searxng/entrypoint.sh /usr/local/searxng/dockerfiles/docker-entrypoint.sh; do
  [ -x "$e" ] && exec "$e"
done
echo "No se encontró el arranque de SearXNG:" >&2
ls -la /usr/local/searxng >&2
exit 1
