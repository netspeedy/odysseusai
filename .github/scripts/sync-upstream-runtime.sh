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
  "docker/host-network.yml"
  "docker/host-workspace.yml"
  "scripts/migrate_searxng_settings.py"
)

for relative_path in "${required_files[@]}"; do
  if [ ! -f "${upstream_dir}/${relative_path}" ]; then
    echo "Missing upstream runtime file: ${relative_path}" >&2
    exit 1
  fi
done

mkdir -p "${target_dir}/config/searxng" "${target_dir}/docker" "${target_dir}/scripts"

if [ -n "${SEARXNG_PINNED_IMAGE:-}" ]; then
  searxng_image="${SEARXNG_PINNED_IMAGE}"
else
  # Follow upstream's own pin. Upstream tests it against this Odysseus release,
  # and the sync never has to pull a moving tag to resolve one.
  searxng_image="$(
    awk '
      /^  searxng:$/ { in_searxng = 1; next }
      in_searxng && /^  [[:alnum:]_.-]+:$/ { exit }
      in_searxng && /^    image: / { sub(/^    image: /, ""); print; exit }
    ' "${upstream_dir}/docker-compose.yml"
  )"
fi

case "${searxng_image}" in
  docker.io/searxng/searxng:*@sha256:*) ;;
  *)
    echo "SearXNG image must use a readable tag and immutable digest: ${searxng_image}" >&2
    exit 1
    ;;
esac

echo "Using SearXNG image ${searxng_image}"

cp "${upstream_dir}/LICENSE" "${target_dir}/LICENSE"
cp "${upstream_dir}/docker/gpu.amd.yml" "${target_dir}/docker/gpu.amd.yml"
cp "${upstream_dir}/docker/gpu.nvidia.yml" "${target_dir}/docker/gpu.nvidia.yml"
cp "${upstream_dir}/docker/host-docker.yml" "${target_dir}/docker/host-docker.yml"
cp "${upstream_dir}/docker/host-network.yml" "${target_dir}/docker/host-network.yml"
cp "${upstream_dir}/docker/host-workspace.yml" "${target_dir}/docker/host-workspace.yml"
cp "${upstream_dir}/scripts/migrate_searxng_settings.py" \
  "${target_dir}/scripts/migrate_searxng_settings.py"

compose_tmp="$(mktemp "${TMPDIR:-/tmp}/odysseus-compose.XXXXXX")"
env_tmp="$(mktemp "${TMPDIR:-/tmp}/odysseus-env.XXXXXX")"
settings_tmp="$(mktemp "${TMPDIR:-/tmp}/odysseus-searxng-settings.XXXXXX")"
trap 'rm -f "${compose_tmp}" "${env_tmp}" "${settings_tmp}"' EXIT

awk '
  BEGIN {
    print "# odysseusai-searxng-profile-v2"
  }

  $0 == "use_default_settings: true" {
    print "use_default_settings:"
    print "  engines:"
    print "    remove:"
    print "      - ahmia"
    print "      - radio browser"
    print "      - torch"
    print "      - wikidata"
    defaults_replacements++
    next
  }

  $0 == "server:" {
    print
    print "  limiter: false"
    server_replacements++
    next
  }

  { print }

  END {
    if (defaults_replacements != 1 || server_replacements != 1) {
      print "Unable to apply the SearXNG settings profile cleanly" > "/dev/stderr"
      exit 1
    }
  }
' "${upstream_dir}/config/searxng/settings.yml" > "${settings_tmp}"

mv "${settings_tmp}" "${target_dir}/config/searxng/settings.yml"

awk -v searxng_image="${searxng_image}" '
  function print_odysseus_image() {
    print "    # Prebuilt by netspeedy/odysseusai from upstream Odysseus and smoke-tested"
    print "    # with this Compose file before it is tagged. Choose a channel or pin a"
    print "    # CalVer build with ODYSSEUS_IMAGE_TAG in .env."
    print "    image: ghcr.io/netspeedy/odysseusai:${ODYSSEUS_IMAGE_TAG:-latest}"
    odysseus_replacements++
  }

  /^  odysseus:$/ {
    in_odysseus = 1
    print
    next
  }

  in_odysseus && /^  [[:alnum:]_.-]+:$/ {
    in_odysseus = 0
    printf "%s", odysseus_comment
    odysseus_comment = ""
  }

  # Service-level comments are held back until the next key. The block above
  # the upstream image line describes upstream images, so it goes with it.
  in_odysseus && /^    #/ {
    odysseus_comment = odysseus_comment $0 "\n"
    next
  }

  in_odysseus && /^    image: / {
    odysseus_comment = ""
    print_odysseus_image()
    next
  }

  in_odysseus && /^    build: \.$/ {
    printf "%s", odysseus_comment
    odysseus_comment = ""
    if (!odysseus_replacements) {
      print_odysseus_image()
    }
    next
  }

  in_odysseus && odysseus_comment != "" {
    printf "%s", odysseus_comment
    odysseus_comment = ""
  }

  /^  searxng:$/ {
    in_searxng = 1
    print
    next
  }

  in_searxng && /^  [[:alnum:]_.-]+:$/ {
    in_searxng = 0
  }

  in_odysseus && /^      - SEARXNG_INSTANCE=/ {
    print
    print "      - SEARXNG_GENERAL_ENGINES=${SEARXNG_GENERAL_ENGINES:-bing,duckduckgo}"
    searxng_engines_replacements++
    next
  }

  in_searxng && /^    # Pinned, not :latest/ {
    skip_upstream_searxng_comment = 1
    next
  }

  in_searxng && skip_upstream_searxng_comment && /^    #/ {
    next
  }

  in_searxng && skip_upstream_searxng_comment {
    skip_upstream_searxng_comment = 0
  }

  in_searxng && /^    image: (docker\.io\/)?searxng\/searxng:/ {
    print "    # Upstream pin, smoke-tested again by netspeedy/odysseusai before the"
    print "    # bundle is published. Override with SEARXNG_IMAGE in .env."
    print "    image: ${SEARXNG_IMAGE:-" searxng_image "}"
    searxng_image_replacements++
    next
  }

  in_searxng && /^        if \[ ! -s \/etc\/searxng\/settings\.yml \] \|\| grep -q .* \/etc\/searxng\/settings\.yml; then$/ {
    print "        profile_marker=\"# odysseusai-searxng-profile-v2\""
    print "        if [ ! -s /etc/searxng/settings.yml ] || ! grep -Fqx \"$$profile_marker\" /etc/searxng/settings.yml; then"
    settings_init_replacements++
    next
  }

  in_searxng && /^          secret="\$\$\{SEARXNG_SECRET:-\}"$/ {
    print
    print "          if [ -z \"$$secret\" ] && [ -s /etc/searxng/settings.yml ]; then"
    print "            secret=\"$$(grep -m 1 secret_key: /etc/searxng/settings.yml | cut -d\\\" -f 2)\""
    print "          fi"
    secret_preserve_replacements++
    next
  }

  in_searxng && /^        exec \/usr\/local\/searxng\/entrypoint\.sh$/ {
    print "        cp /tmp/searxng-limiter.toml.template /etc/searxng/limiter.toml"
    limiter_init_replacements++
  }

  in_searxng && /^      - \.\/config\/searxng\/settings\.yml:\/tmp\/searxng-settings\.yml\.template:ro,z$/ {
    print
    print "      - ./config/searxng/limiter.toml:/tmp/searxng-limiter.toml.template:ro,z"
    limiter_mount_replacements++
    next
  }

  in_searxng && /^      test: \["CMD-SHELL", "python -c .*localhost:8080.*"\]$/ {
    print "      test: [\"CMD-SHELL\", \"wget --quiet --spider --header=X-Real-IP:127.0.0.1 http://localhost:8080/\"]"
    healthcheck_replacements++
    next
  }

  { print }

  END {
    if (odysseus_replacements != 1 \
        || searxng_image_replacements != 1 \
        || searxng_engines_replacements != 1 \
        || settings_init_replacements != 1 \
        || secret_preserve_replacements != 1 \
        || limiter_init_replacements != 1 \
        || limiter_mount_replacements != 1 \
        || healthcheck_replacements != 1) {
      print "Unable to apply the Compose packaging overrides cleanly" > "/dev/stderr"
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
  echo "# Reliable engines for the bundled SearXNG release."
  echo "SEARXNG_GENERAL_ENGINES=bing,duckduckgo"
  echo
  cat "${upstream_dir}/.env.example"
} > "${env_tmp}"

mv "${env_tmp}" "${target_dir}/.env.example"
