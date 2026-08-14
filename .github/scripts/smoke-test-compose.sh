#!/usr/bin/env bash

set -euo pipefail

image_ref="${1:?image reference is required}"
compose_file="${2:-docker-compose.yml}"
override_file="${3:-.github/compose/smoke-test.yml}"
pull_policy="${ODYSSEUS_COMPOSE_PULL_POLICY:-always}"
project_name="odysseus-compose-smoke-${GITHUB_RUN_ID:-local}-${RANDOM}"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/odysseus-compose-smoke.XXXXXX")"
logs_file="${work_dir}/odysseus.log"
searxng_logs_file="${work_dir}/searxng.log"

export APP_DATA_DIR="${work_dir}/data"
export APP_LOGS_DIR="${work_dir}/logs"
export ODYSSEUS_CANDIDATE_IMAGE="${image_ref}"
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
    "${compose[@]}" logs --no-color --tail 300 >&2 || true
  fi
  "${compose[@]}" down --volumes --remove-orphans --timeout 10 >/dev/null 2>&1 || true
  rm -rf "${work_dir}"
  exit "${status}"
}
trap cleanup EXIT

case "${pull_policy}" in
  always | missing | never | build) ;;
  *)
    echo "Invalid Compose pull policy: ${pull_policy}" >&2
    exit 1
    ;;
esac

mkdir -p "${APP_DATA_DIR}" "${APP_LOGS_DIR}"

resolved_images="$("${compose[@]}" config --images)"
if ! grep -Fxq "${image_ref}" <<< "${resolved_images}"; then
  echo "Compose did not resolve the requested Odysseus image: ${image_ref}" >&2
  exit 1
fi

"${compose[@]}" run --rm --no-deps --pull "${pull_policy}" --user root \
  --entrypoint /bin/sh searxng -c '
cat > /etc/searxng/settings.yml <<"EOF"
use_default_settings: true
server:
  limiter: true
  secret_key: "odysseus-upgrade-test-secret"
EOF
rm -f /etc/searxng/limiter.toml
'
"${compose[@]}" up --detach --pull "${pull_policy}" --no-build

healthy=false
for _ in $(seq 1 60); do
  odysseus_container="$("${compose[@]}" ps --quiet odysseus)"
  if [ -z "${odysseus_container}" ]; then
    sleep 2
    continue
  fi

  if [ "$(docker inspect --format '{{.State.Running}}' "${odysseus_container}")" != "true" ]; then
    echo "Odysseus exited before the Compose stack became healthy." >&2
    exit 1
  fi

  if "${compose[@]}" exec -T odysseus python -c '
import json
import urllib.request

payload = json.load(urllib.request.urlopen("http://127.0.0.1:7000/api/health", timeout=3))
assert payload.get("status") == "healthy", payload
' >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep 2
done

if [ "${healthy}" != "true" ]; then
  echo "Odysseus did not become healthy within 120 seconds." >&2
  exit 1
fi

for service in odysseus chromadb searxng ntfy; do
  container_id="$("${compose[@]}" ps --quiet "${service}")"
  if [ -z "${container_id}" ] || [ "$(docker inspect --format '{{.State.Running}}' "${container_id}")" != "true" ]; then
    echo "Compose service is not running: ${service}" >&2
    exit 1
  fi
done

searxng_container="$("${compose[@]}" ps --quiet searxng)"
if [ "$(docker inspect --format '{{.State.Health.Status}}' "${searxng_container}")" != "healthy" ]; then
  echo "SearXNG did not pass its Compose health check." >&2
  exit 1
fi

"${compose[@]}" exec -T searxng sh -eu -c '
grep -Fqx "# odysseusai-searxng-profile-v2" /etc/searxng/settings.yml
grep -Fqx "  secret_key: \"odysseus-upgrade-test-secret\"" /etc/searxng/settings.yml
test -s /etc/searxng/limiter.toml
'

# Wait for every bundled MCP subprocess, including the NPX browser server.
mcp_ready=false
for _ in $(seq 1 30); do
  "${compose[@]}" logs --no-color odysseus > "${logs_file}" 2>&1
  if grep -Eq \
    'Traceback \(most recent call last\):|Application startup failed|Built-in MCP server failed to connect:' \
    "${logs_file}"; then
    echo "Odysseus reported a critical startup or bundled MCP failure." >&2
    exit 1
  fi

  if grep -Fq 'MCP server connected: Built-in: Memory' "${logs_file}" \
    && grep -Fq 'MCP server connected: Built-in: RAG' "${logs_file}" \
    && grep -Fq 'MCP server connected: Built-in: Image Generation' "${logs_file}" \
    && grep -Fq 'MCP server connected: Built-in: Email' "${logs_file}" \
    && grep -Fq 'MCP server connected: Built-in: Browser' "${logs_file}"; then
    mcp_ready=true
    break
  fi
  sleep 2
done

if [ "${mcp_ready}" != "true" ]; then
  echo "Not all bundled MCP servers connected within 60 seconds." >&2
  exit 1
fi

"${compose[@]}" logs --no-color searxng > "${searxng_logs_file}" 2>&1
if grep -Eq \
  'Traceback \(most recent call last\):|sqlite3\.OperationalError|ERROR:searx\.engines:|ERROR:searx\.searx\.search\.processor:|ERROR:searx\.search\.processors:|missing config file: /etc/searxng/limiter\.toml|X-Forwarded-For nor X-Real-IP' \
  "${searxng_logs_file}"; then
  echo "SearXNG reported a critical startup or configuration error." >&2
  exit 1
fi

"${compose[@]}" exec -T odysseus python - <<'PY'
import json
import time
import urllib.request


def get_json(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


health = get_json("http://127.0.0.1:7000/api/health")
assert health.get("status") == "healthy", health

chroma = get_json("http://chromadb:8000/api/v2/heartbeat")
assert "nanosecond heartbeat" in chroma, chroma

ntfy = get_json("http://ntfy/v1/health")
assert ntfy.get("healthy") is True, ntfy

search_payload = None
for query in ("Odysseus AI GitHub", "Python programming language"):
    request = urllib.request.Request(
        "http://127.0.0.1:7000/api/search/query",
        data=json.dumps(
            {"query": query, "provider": "searxng", "count": 3}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            search_payload = json.load(response)
    except Exception:
        time.sleep(2)
        continue
    if search_payload.get("results"):
        break

assert search_payload is not None, "Odysseus search endpoint did not respond"
assert not search_payload.get("error"), search_payload
assert search_payload.get("provider") == "searxng", search_payload
assert search_payload.get("results"), search_payload

print(
    "Compose integration checks passed: "
    f"health={health['status']}, search_results={len(search_payload['results'])}"
)
PY

echo "Compose stack passed Odysseus, ChromaDB, SearXNG, ntfy, and bundled MCP checks."
