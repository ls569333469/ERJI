#!/usr/bin/env bash
# Install as a root-owned scheduled task only after a successful restore drill.
set -euo pipefail
umask 077
cd /opt/erji-current
backup=/opt/erji-shared/backups
install -d -m 700 "$backup"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
file="$backup/erji-$stamp.dump"
trap 'rm -f -- "$file.partial"' EXIT
docker compose -f compose.yml -f infra/compose.limits.yml exec -T db pg_dump -U erji -d erji -Fc > "$file.partial"
docker compose -f compose.yml -f infra/compose.limits.yml exec -T db pg_restore --list < "$file.partial" >/dev/null
mv -- "$file.partial" "$file"
sha256sum "$file" > "$file.sha256"
cp deployment-manifest.json "$backup/erji-$stamp.release.json"
# Keep the newest seven dumps only after today's dump passes archive validation.
python3 - "$backup" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]).resolve()
for p in sorted(root.glob('erji-*.dump'), reverse=True)[7:]:
 for candidate in (p, Path(str(p)+'.sha256'), p.with_suffix('.release.json')):
  if candidate.parent.resolve()!=root: raise SystemExit('Unsafe backup path')
  candidate.unlink(missing_ok=True)
PY
printf 'ERJI backup completed: %s\n' "$stamp"
