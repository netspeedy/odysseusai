#!/usr/bin/env bash

# Copy a synchronized bundle, produced by describe-bundle-sync.sh in a job with
# no write credentials, into this checkout. Only the paths in lib/bundle.sh are
# accepted, so the job that pushes the pull request can't be handed anything
# else.

set -euo pipefail

bundle_dir="${1:?bundle directory is required}"

# shellcheck source=SCRIPTDIR/lib/bundle.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/bundle.sh"

files=()
while IFS= read -r -d '' file; do
  files+=("${file}")
done < <(find "${bundle_dir}/files" -mindepth 1 \( -type f -o -type l \) -print0)

# Check every path before copying anything, so a bad bundle changes nothing.
for file in "${files[@]}"; do
  path="${file#"${bundle_dir}/files/"}"
  allowed=false
  for bundle_file in "${bundle_files[@]}"; do
    if [ "${path}" = "${bundle_file}" ]; then
      allowed=true
      break
    fi
  done
  if [ "${allowed}" != "true" ] || [ -L "${file}" ]; then
    echo "Refusing to apply unexpected bundle file: ${path}" >&2
    exit 1
  fi
done

for file in "${files[@]}"; do
  path="${file#"${bundle_dir}/files/"}"
  mkdir -p "$(dirname "${path}")"
  cp "${file}" "${path}"
  echo "Applied ${path}"
done
