#!/usr/bin/env bash

set -euo pipefail

upstream_dir="${1:-../upstream}"
upstream_sha="$(git -C "${upstream_dir}" rev-parse HEAD)"
upstream_short_sha="$(git -C "${upstream_dir}" rev-parse --short=12 HEAD)"
runtime_files=(
  ".env.example"
  "LICENSE"
  "docker-compose.yml"
  "config/searxng/settings.yml"
  "docker/gpu.amd.yml"
  "docker/gpu.nvidia.yml"
  "docker/host-docker.yml"
)

echo "upstream_sha=${upstream_sha}" >> "${GITHUB_OUTPUT}"

git add -- "${runtime_files[@]}"

if git diff --cached --quiet -- "${runtime_files[@]}"; then
  echo "changed=false" >> "${GITHUB_OUTPUT}"
  echo "packaging_sha=$(git rev-parse HEAD)" >> "${GITHUB_OUTPUT}"
  echo "Deployment bundle already matches upstream main."
  exit 0
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git commit -m "chore: sync Odysseus runtime ${upstream_short_sha}" -- "${runtime_files[@]}"
git push origin HEAD:main

echo "changed=true" >> "${GITHUB_OUTPUT}"
echo "packaging_sha=$(git rev-parse HEAD)" >> "${GITHUB_OUTPUT}"
