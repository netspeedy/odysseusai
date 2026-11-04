# Sourced by the pipeline scripts.

# The deployment bundle files that sync-upstream-runtime.sh owns, relative to
# the repository root. Each one comes from upstream main, as is or with the
# packaging overrides applied. config/searxng/limiter.toml is ours and is not
# synced. The automated pull request may only touch these paths.
# shellcheck disable=SC2034
bundle_files=(
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
