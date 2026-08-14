#!/usr/bin/env bash

set -euo pipefail

upstream_dir="${1:-upstream}"
target_dir="${2:-.}"

required_files=(
  ".env.example"
  "LICENSE"
  "docker-compose.yml"
  "config/searxng/settings.yml"
  "docker/gpu.amd.yml"
  "docker/gpu.nvidia.yml"
  "docker/host-docker.yml"
)

for relative_path in "${required_files[@]}"; do
  if [ ! -f "${upstream_dir}/${relative_path}" ]; then
    echo "Missing upstream runtime file: ${relative_path}" >&2
    exit 1
  fi
done

mkdir -p "${target_dir}/config/searxng" "${target_dir}/docker"

cp "${upstream_dir}/LICENSE" "${target_dir}/LICENSE"
cp "${upstream_dir}/config/searxng/settings.yml" \
  "${target_dir}/config/searxng/settings.yml"
cp "${upstream_dir}/docker/gpu.amd.yml" "${target_dir}/docker/gpu.amd.yml"
cp "${upstream_dir}/docker/gpu.nvidia.yml" "${target_dir}/docker/gpu.nvidia.yml"
cp "${upstream_dir}/docker/host-docker.yml" "${target_dir}/docker/host-docker.yml"

compose_tmp="$(mktemp "${TMPDIR:-/tmp}/odysseus-compose.XXXXXX")"
env_tmp="$(mktemp "${TMPDIR:-/tmp}/odysseus-env.XXXXXX")"
trap 'rm -f "${compose_tmp}" "${env_tmp}"' EXIT

awk '
  /^  odysseus:$/ {
    in_odysseus = 1
    print
    next
  }

  in_odysseus && /^  [[:alnum:]_.-]+:$/ {
    in_odysseus = 0
  }

  in_odysseus && /^    build: \.$/ {
    print "    image: ghcr.io/netspeedy/odysseusai:${ODYSSEUS_IMAGE_TAG:-latest}"
    replacements++
    next
  }

  { print }

  END {
    if (replacements != 1) {
      print "Expected exactly one Odysseus build directive, found " replacements > "/dev/stderr"
      exit 1
    }
  }
' "${upstream_dir}/docker-compose.yml" > "${compose_tmp}"

mv "${compose_tmp}" "${target_dir}/docker-compose.yml"

{
  echo "# Image channel from ghcr.io/netspeedy/odysseusai."
echo "# Use latest/main for stable, dev for development, or a CalVer tag to pin."
  echo "ODYSSEUS_IMAGE_TAG=latest"
  echo
  cat "${upstream_dir}/.env.example"
} > "${env_tmp}"

mv "${env_tmp}" "${target_dir}/.env.example"
