#!/bin/sh
set -eu

# Construct the selected database URL inside the container: empty DATABASE_URL
# is not a reliable override for dj-database-url, and passwords need URL encoding.
# Keep credentials out of the manager's registry and command-line arguments.
DATABASE_URL="$(python - <<'PY'
import os
from urllib.parse import quote

user = quote(os.environ['POSTGRES_USER'], safe='')
password = quote(os.environ['POSTGRES_PASSWORD'], safe='')
database = quote(os.environ['POSTGRES_DB'], safe='')
print(f'postgresql://{user}:{password}@postgresql:5432/{database}')
PY
)"
export DATABASE_URL
exec /entrypoint.sh "$@"
