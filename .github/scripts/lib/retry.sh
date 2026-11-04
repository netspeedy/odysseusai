# Sourced by the pipeline scripts.

# retry ATTEMPTS COMMAND...
# Run COMMAND until it succeeds, waiting a little longer after each failure.
# Registry pulls fail transiently often enough to fail a nightly run on their
# own, which is what happened on 1 September 2026.
retry() {
  local attempts="$1"
  shift
  local attempt=1
  until "$@"; do
    if [ "${attempt}" -ge "${attempts}" ]; then
      echo "Giving up after ${attempt} attempts: $*" >&2
      return 1
    fi
    echo "Attempt ${attempt} failed, retrying: $*" >&2
    sleep $((attempt * 10))
    attempt=$((attempt + 1))
  done
}
