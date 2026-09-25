#!/bin/sh
set -e
if [ "$#" -gt 0 ]; then exec "$@"; fi   # docker compose run ... python manage.py <команда>
python manage.py migrate --noinput -v0
python manage.py ensure_admin
exec gunicorn gluckstal.wsgi -b 0.0.0.0:${PORT} -w 1 --threads 4 --timeout 120 \
     --access-logfile - --forwarded-allow-ips='*'
