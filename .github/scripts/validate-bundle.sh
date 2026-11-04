#!/usr/bin/env bash

# Render every Compose file combination that .env.example documents and check
# that each local file the bundle bind-mounts is actually shipped. A missing
# source is easy to miss otherwise: Docker creates an empty directory in its
# place and the service may still report healthy.

set -euo pipefail

bundle_dir="$(pwd -P)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/odysseus-bundle.XXXXXX")"
trap 'rm -rf "${work_dir}"' EXIT

# host-workspace.yml refuses to render without a host directory to mount.
export ODYSSEUS_HOST_WORKSPACE_DIR="${work_dir}/workspace"
mkdir -p "${ODYSSEUS_HOST_WORKSPACE_DIR}"

combinations=("docker-compose.yml")
while IFS= read -r combination; do
  combinations+=("${combination}")
done < <(
  sed -n 's/^# COMPOSE_FILE=\(docker-compose\.yml[^ ;]*\)[[:space:]]*$/\1/p' .env.example | sort -u
)

for combination in "${combinations[@]}"; do
  files=()
  IFS=: read -r -a compose_files <<< "${combination}"
  for compose_file in "${compose_files[@]}"; do
    files+=(--file "${compose_file}")
  done

  rendered="${work_dir}/rendered.json"
  docker compose --env-file .env.example "${files[@]}" config --format json \
    > "${rendered}" 2> "${work_dir}/stderr" || {
    cat "${work_dir}/stderr" >&2
    echo "Compose files do not render: ${combination}" >&2
    exit 1
  }

  python3 - "${rendered}" "${bundle_dir}" "${combination}" <<'PY'
import json
import sys
from pathlib import Path

# Compose reports logical paths; compare resolved ones so symlinks do not hide anything.
rendered, bundle_dir, combination = sys.argv[1], Path(sys.argv[2]).resolve(), sys.argv[3]
config = json.loads(Path(rendered).read_text(encoding="utf-8"))
# data/ and logs/ are runtime state that Docker creates on first start.
runtime_roots = {"data", "logs"}
missing = []
for name, service in config.get("services", {}).items():
    for volume in service.get("volumes", []):
        if volume.get("type") != "bind":
            continue
        source = Path(volume["source"]).resolve()
        try:
            relative = source.relative_to(bundle_dir)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] in runtime_roots:
            continue
        if not source.exists():
            missing.append(f"{name}: {relative}")

if missing:
    print(f"Bundle is missing bind-mounted files for {combination}:", file=sys.stderr)
    for entry in missing:
        print(f"  {entry}", file=sys.stderr)
    sys.exit(1)
PY

  echo "Rendered ${combination}"
done
