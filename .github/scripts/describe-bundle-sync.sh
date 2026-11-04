#!/usr/bin/env bash

# Run in the deployment repository after sync-upstream-runtime.sh. When the
# bundle changed, copy the changed files to OUTPUT_DIR/files and write the
# pull request description to OUTPUT_DIR/pull-request.md. Sets the changed and
# title step outputs.

set -euo pipefail

upstream_dir="${1:?upstream checkout is required}"
output_dir="${2:?output directory is required}"
upstream_repository="${UPSTREAM_REPOSITORY:-odysseus-dev/odysseus}"

# shellcheck source=SCRIPTDIR/lib/bundle.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/bundle.sh"

output() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "$1" >> "${GITHUB_OUTPUT}"
  fi
}

changed_files=()
while IFS= read -r path; do
  changed_files+=("${path}")
done < <(
  git status --porcelain --untracked-files=all -- "${bundle_files[@]}" | cut -c4- | sort
)

if [ "${#changed_files[@]}" -eq 0 ]; then
  output "changed=false"
  echo "Deployment bundle already matches upstream main."
  exit 0
fi

upstream_sha="$(git -C "${upstream_dir}" rev-parse HEAD)"
upstream_short="${upstream_sha:0:12}"
title="chore: sync deployment bundle with upstream ${upstream_short}"
upstream_url="https://github.com/${upstream_repository}"

# The previous sync's upstream revision, from its commit subject, for a compare
# link. Upstream has rewritten history before, so it may no longer resolve.
previous_short="$(
  git log -1 --format=%s --grep='^chore: sync deployment bundle with upstream ' \
    | sed -n 's/^chore: sync deployment bundle with upstream \([0-9a-f]\{12\}\).*/\1/p'
)"

mkdir -p "${output_dir}/files"
for path in "${changed_files[@]}"; do
  mkdir -p "${output_dir}/files/$(dirname "${path}")"
  cp "${path}" "${output_dir}/files/${path}"
done

git add --intent-to-add -- "${changed_files[@]}"
stat_table="$(
  git diff --numstat -- "${changed_files[@]}" \
    | awk -F '\t' '{ printf "| `%s` | +%s / -%s |\n", $3, $1, $2 }'
)"
full_diff="$(git diff -- "${changed_files[@]}")"
if [ "${#full_diff}" -gt 50000 ]; then
  full_diff="${full_diff:0:50000}"$'\n'"... (truncated; see the Files changed tab)"
fi

image_changes="$(
  python3 - <<'PY'
import re
import subprocess
from pathlib import Path


def images(text):
    found, service = {}, None
    for line in text.splitlines():
        match = re.match(r"^  ([A-Za-z0-9_.-]+):$", line)
        if match:
            service = match.group(1)
            continue
        match = re.match(r"^    image: (.+)$", line)
        if match and service:
            found[service] = match.group(1)
    return found


old = subprocess.run(
    ["git", "show", "HEAD:docker-compose.yml"], capture_output=True, text=True, check=False
).stdout
new = Path("docker-compose.yml").read_text(encoding="utf-8")
before, after = images(old), images(new)
for service in sorted(set(before) | set(after)):
    if before.get(service) != after.get(service):
        print(f"- `{service}`: `{before.get(service, 'none')}` to `{after.get(service, 'none')}`")
PY
)"

# The backticks below are Markdown, not command substitution.
# shellcheck disable=SC2016
{
  printf 'Upstream main is at [`%s`](%s/commit/%s)' "${upstream_short}" "${upstream_url}" "${upstream_sha}"
  if [ -n "${previous_short}" ]; then
    printf ', compared with [`%s`](%s/compare/%s...%s) at the last sync' \
      "${previous_short}" "${upstream_url}" "${previous_short}" "${upstream_sha}"
  fi
  printf '. The sync re-applied the packaging overrides, rendered every Compose file combination that `.env.example` documents, and booted the bundled SearXNG for a real query before opening this.\n\n'
  printf '| File | Lines |\n| --- | --- |\n%s\n\n' "${stat_table}"
  if [ -n "${image_changes}" ]; then
    printf 'Image changes in `docker-compose.yml`:\n\n%s\n\n' "${image_changes}"
  fi
  printf '<details>\n<summary>Full diff</summary>\n\n```diff\n%s\n```\n\n</details>\n\n' "${full_diff}"
  printf 'Build and Validate runs on this branch, and Automation Auto Merge rebases it onto main once that passes, which also starts an image run against the new bundle. Close it to skip this sync; the next run opens it again if upstream still differs.\n'
} > "${output_dir}/pull-request.md"

output "changed=true"
output "title=${title}"
echo "Deployment bundle changed: ${changed_files[*]}"
