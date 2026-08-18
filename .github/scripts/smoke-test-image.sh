#!/usr/bin/env bash

set -euo pipefail

image_ref="${1:?image reference is required}"
container_name="odysseus-smoke-${GITHUB_RUN_ID:-local}-${RANDOM}"
logs_file="$(mktemp "${TMPDIR:-/tmp}/odysseus-smoke.XXXXXX")"

cleanup() {
  docker rm -f "${container_name}" >/dev/null 2>&1 || true
  rm -f "${logs_file}"
}
trap cleanup EXIT

if ! docker image inspect "${image_ref}" >/dev/null 2>&1; then
  docker pull --platform linux/amd64 "${image_ref}"
fi

docker run --rm --platform linux/amd64 --entrypoint python "${image_ref}" -c '
from mcp.server import Server
from src.chat_helpers import is_vision_model

assert callable(getattr(Server, "list_tools", None)), "mcp Server.list_tools is unavailable"
assert callable(getattr(Server, "call_tool", None)), "mcp Server.call_tool is unavailable"
assert is_vision_model("gpt-5.5"), "GPT-5 chat models should preserve image attachments"
assert is_vision_model("cx/gpt-5.5-medium"), "Provider-prefixed GPT-5 chat models should preserve image attachments"
assert not is_vision_model("gpt-5.1-codex"), "Codex models should not be treated as vision chat models"
'

docker run \
  --detach \
  --name "${container_name}" \
  --platform linux/amd64 \
  --env AUTH_ENABLED=false \
  --env ODYSSEUS_ADMIN_PASSWORD=odysseus-smoke-test-password \
  --env ODYSSEUS_STARTUP_WARMUPS=0 \
  "${image_ref}" >/dev/null

healthy=false
for _ in $(seq 1 45); do
  if [ "$(docker inspect --format '{{.State.Running}}' "${container_name}")" != "true" ]; then
    docker logs "${container_name}" >&2 || true
    echo "Candidate container exited before becoming healthy." >&2
    exit 1
  fi

  if docker exec "${container_name}" python -c '
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
  docker logs "${container_name}" >&2 || true
  echo "Candidate container did not become healthy within 90 seconds." >&2
  exit 1
fi

# Give the bundled MCP subprocesses time to connect after application startup.
sleep 8
docker logs "${container_name}" > "${logs_file}" 2>&1

if grep -Eq \
  'Traceback \(most recent call last\):|Application startup failed|Built-in MCP server failed to connect: Built-in: (Memory|RAG|Image Generation|Email)' \
  "${logs_file}"; then
  cat "${logs_file}" >&2
  echo "Candidate image reported a critical startup or bundled MCP failure." >&2
  exit 1
fi

echo "Candidate image passed SDK, startup, health, and bundled MCP checks."
