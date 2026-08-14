#!/usr/bin/env bash

set -euo pipefail

compose_file="${1:-docker-compose.yml}"
override_file="${2:-.github/compose/smoke-test.yml}"
project_name="odysseus-searxng-smoke-${GITHUB_RUN_ID:-local}-${RANDOM}"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/odysseus-searxng-smoke.XXXXXX")"
logs_file="${work_dir}/searxng.log"

export APP_DATA_DIR="${work_dir}/data"
export APP_LOGS_DIR="${work_dir}/logs"
export ODYSSEUS_CANDIDATE_IMAGE="ghcr.io/netspeedy/odysseusai:latest"
PGID="$(id -g)"
PUID="$(id -u)"
export PGID PUID

compose=(
  docker compose
  --project-name "${project_name}"
  --env-file .env.example
  --file "${compose_file}"
  --file "${override_file}"
)

cleanup() {
  status=$?
  if [ "${status}" -ne 0 ]; then
    "${compose[@]}" ps --all >&2 || true
    "${compose[@]}" logs --no-color --tail 300 searxng >&2 || true
  fi
  "${compose[@]}" down --volumes --remove-orphans --timeout 10 >/dev/null 2>&1 || true
  rm -rf "${work_dir}"
  exit "${status}"
}
trap cleanup EXIT

mkdir -p "${APP_DATA_DIR}" "${APP_LOGS_DIR}"
"${compose[@]}" up --detach --pull always --no-build searxng

healthy=false
for _ in $(seq 1 60); do
  container_id="$("${compose[@]}" ps --quiet searxng)"
  if [ -z "${container_id}" ]; then
    sleep 2
    continue
  fi

  if [ "$(docker inspect --format '{{.State.Running}}' "${container_id}")" != "true" ]; then
    echo "SearXNG exited before becoming healthy." >&2
    exit 1
  fi

  if [ "$(docker inspect --format '{{.State.Health.Status}}' "${container_id}")" = "healthy" ]; then
    healthy=true
    break
  fi
  sleep 2
done

if [ "${healthy}" != "true" ]; then
  echo "SearXNG did not become healthy within 120 seconds." >&2
  exit 1
fi

sleep 2
"${compose[@]}" logs --no-color searxng > "${logs_file}" 2>&1
if grep -Eq \
  'Traceback \(most recent call last\):|sqlite3\.OperationalError|ERROR:searx\.engines:|ERROR:searx\.searx\.search\.processor:|ERROR:searx\.search\.processors:|missing config file: /etc/searxng/limiter\.toml|X-Forwarded-For nor X-Real-IP' \
  "${logs_file}"; then
  echo "SearXNG reported a critical startup or configuration error." >&2
  exit 1
fi

"${compose[@]}" exec -T searxng python - <<'PY'
import json
import urllib.parse
import urllib.request


params = urllib.parse.urlencode(
    {
        "q": "Odysseus AI GitHub",
        "format": "json",
        "language": "en",
        "engines": "bing,mojeek,presearch",
    }
)
request = urllib.request.Request(
    f"http://127.0.0.1:8080/search?{params}",
    headers={"X-Real-IP": "127.0.0.1"},
)
with urllib.request.urlopen(request, timeout=45) as response:
    payload = json.load(response)

assert payload.get("results"), payload
print(f"SearXNG smoke test passed with {len(payload['results'])} search results.")
PY
