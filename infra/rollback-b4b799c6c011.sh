set -euo pipefail
cd /opt/erji-releases/release-6e6fa4ecbcd0
cat > /opt/erji-shared/rollback-b4b799c6c011.yml <<'YML'
services:
  api:
    image: erji-api:before-b4b799c6c011
  worker-live:
    image: erji-api:before-b4b799c6c011
  worker-jobs:
    image: erji-api:before-b4b799c6c011
  worker-streams:
    image: erji-api:before-b4b799c6c011
  web:
    image: erji-web:before-b4b799c6c011
YML
docker compose -p erji -f compose.yml -f infra/compose.limits.yml -f /opt/erji-shared/rollback-b4b799c6c011.yml up -d --no-deps --no-build api web worker-live worker-jobs worker-streams
ln -sfn /opt/erji-releases/release-6e6fa4ecbcd0 /opt/erji-current
