#!/usr/bin/env bash

# Run the upstream tests that cover the packaging patches, in an upstream
# checkout that image_pipeline.py prepare has already patched. The checkout is
# also the image build context, so the virtual environment lives outside it and
# pytest writes no caches into it.

set -euo pipefail

source_dir="${1:?prepared upstream checkout is required}"
patches_dir="${2:-.github/patches}"
constraints_file="${3:-.github/constraints/requirements.txt}"
scripts_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv_dir="${UPSTREAM_TEST_VENV:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/odysseus-upstream-tests}"

# shellcheck source=SCRIPTDIR/lib/retry.sh
source "${scripts_dir}/lib/retry.sh"

tests=()
while IFS= read -r path; do
  tests+=("${path}")
done < <(
  python3 "${scripts_dir}/upstream_tests.py" select \
    --source "${source_dir}" --patches "${patches_dir}"
)

if [ "${#tests[@]}" -eq 0 ]; then
  echo "No upstream tests cover the patched files."
  exit 0
fi

python_version="$(python3 "${scripts_dir}/upstream_tests.py" python --source "${source_dir}")"

retry 3 uv venv --clear --quiet --python "${python_version}" "${venv_dir}"
retry 3 uv pip install --quiet --python "${venv_dir}/bin/python" \
  --requirement "${source_dir}/requirements-dev.txt" \
  --constraint "${constraints_file}"

echo "Running ${#tests[@]} upstream test files on Python ${python_version}:"
printf '  %s\n' "${tests[@]}"

cd "${source_dir}"
PYTHONDONTWRITEBYTECODE=1 "${venv_dir}/bin/python" -m pytest \
  -q -rfE -p no:cacheprovider -n auto "${tests[@]}"
