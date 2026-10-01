#!/bin/sh
# Run SQL against the live database. Read-only habits recommended: start with SELECT.
#   scripts/db.sh "SELECT username, created_at FROM users"
#   scripts/db.sh tables
cd "$(dirname "$0")/.." || exit 1
case "$1" in
  ""|-h|--help) echo 'Usage: scripts/db.sh "SELECT ..." | tables'; exit 0 ;;
  tables) set -- "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name" ;;
esac
exec npx wrangler d1 execute amul-watch --remote --command "$1"
