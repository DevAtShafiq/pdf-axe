#!/bin/sh
# Copy the accounts database and every uploaded file into a dated .tar.gz in
# ./backups. The server keeps running; SQLite's own backup makes a consistent
# copy of the database. Old backups are never removed by this script.
#
#   sh backup.sh            (from the deploy folder)
#   crontab: 0 3 * * * cd /opt/office-axe/deploy && sh backup.sh
set -eu
cd "$(dirname "$0")"
mkdir -p backups
stamp=$(date +%Y-%m-%d_%H%M)
docker compose exec -T server python -c "import sqlite3; s=sqlite3.connect('/data/pdfaxe.sqlite3'); d=sqlite3.connect('/data/backup.sqlite3'); s.backup(d); d.close()"
docker compose exec -T server tar -czf - -C /data backup.sqlite3 storage > "backups/office-axe_$stamp.tar.gz"
echo "Saved backups/office-axe_$stamp.tar.gz"
