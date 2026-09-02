#!/bin/sh
set -eu

release_id="e5e205d"
release_dir="/opt/ekonex/e-voice-lab/releases/$release_id"
archive="$release_dir/ekonex-lab-backend-$release_id.zip"
lab_env="/opt/ekonex/e-voice-lab/.env"
postgres_container="e-voice-postgres-1"
lab_database="ekonex_voice_lab"
lab_role="ekonex_lab_reader"

test -f "$archive"
test -f "$lab_env"
cd "$release_dir"
unzip -q -o "$archive"
docker build -t "e-voice-lab:$release_id" .

database_exists=$(docker exec "$postgres_container" sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1 FROM pg_database WHERE datname = '\''ekonex_voice_lab'\''"')
if [ "$database_exists" != "1" ]; then
  docker exec "$postgres_container" sh -lc \
    'createdb -U "$POSTGRES_USER" -O ekonex_lab_reader ekonex_voice_lab'
  docker exec "$postgres_container" sh -lc \
    '{ printf "SET ROLE ekonex_lab_reader;\n"; pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges; } | psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d ekonex_voice_lab'
fi

sed -i 's#\(EKONEX_DATABASE_URL=.*:5432/\).*#\1ekonex_voice_lab#' "$lab_env"

docker run --rm \
  --network e-voice_default \
  --env-file "$lab_env" \
  "e-voice-lab:$release_id" \
  alembic upgrade head

if docker container inspect e-voice-lab-api >/dev/null 2>&1; then
  docker stop e-voice-lab-api >/dev/null
  docker rename e-voice-lab-api "e-voice-lab-api-before-$release_id"
fi

docker run -d \
  --name e-voice-lab-api \
  --restart unless-stopped \
  --network e-voice_default \
  --env-file "$lab_env" \
  --publish 127.0.0.1:8001:8000 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  "e-voice-lab:$release_id"

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8001/health >/dev/null; then
    exit 0
  fi
  sleep 1
done

docker logs --tail 80 e-voice-lab-api
exit 1
