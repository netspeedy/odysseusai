# Odysseus AI Container Images

Public, prebuilt Docker images for
[Odysseus](https://odysseus-dev.github.io/odysseus), the self-hosted AI
workspace. Application source and development are maintained in the upstream
[`odysseus-dev/odysseus`](https://github.com/odysseus-dev/odysseus)
repository.

This project automatically turns upstream Odysseus updates into ready-to-run
container images, removing the need to clone the application source and rebuild
it locally whenever a new version is available.

Images are published through GitHub Container Registry at
[`ghcr.io/netspeedy/odysseusai`](https://github.com/netspeedy/odysseusai/pkgs/container/odysseusai).

## Deploy

No application source or local image build is required. Choose whichever setup
method is most convenient.

### Clone the deployment repository

```bash
git clone https://github.com/netspeedy/odysseusai.git
cd odysseusai
cp .env.example .env
```

Review `.env`, add any model-provider credentials or deployment settings you
need, then start Odysseus:

```bash
docker compose up -d
```

### Download the deployment package

[Download the ZIP archive](https://github.com/netspeedy/odysseusai/archive/refs/heads/main.zip),
extract it, copy `.env.example` to `.env`, adjust your settings, and run
`docker compose up -d` from the extracted directory.

### Download only the required files

A standard CPU deployment needs only the Compose file, environment template,
and SearXNG settings template:

```bash
mkdir -p odysseusai/config/searxng
cd odysseusai
repository_url=https://raw.githubusercontent.com/netspeedy/odysseusai/main
curl -fsSLO "${repository_url}/docker-compose.yml"
curl -fsSL "${repository_url}/.env.example" -o .env
curl -fsSL "${repository_url}/config/searxng/settings.yml" \
  -o config/searxng/settings.yml
docker compose up -d
```

This downloads deployment configuration only. The prebuilt Odysseus image and
supporting service images are pulled automatically from their public
registries.

Open `http://localhost:7000` once the containers are healthy. On first startup,
the generated administrator password is available in the Odysseus logs:

```bash
docker compose logs odysseus
```

Application data and logs are stored in the local `data/` and `logs/`
directories by default. Runtime settings, ports, credentials, model providers,
and optional integrations can be configured in `.env`.

## Image Channels

| Tag | Upstream source | Intended use |
| --- | --- | --- |
| `latest` | `main` | Recommended stable image |
| `main` | `main` | Explicit stable channel |
| `dev` | `dev` | Newest upstream development build |
| `YYYY.MM.DD.N` | `main` | Immutable stable build |
| `YYYY.MM.DD.N-dev` | `dev` | Immutable development build |

Immutable builds use CalVer based on the UTC publication date. The final
number starts at `1` and increments when more than one image is published on
the same day. The exact upstream Git commit is recorded in the image metadata.

The published image currently supports `linux/amd64`.

The included Compose configuration uses `latest`. To use the development
channel or pin an exact build, set `ODYSSEUS_IMAGE_TAG` in `.env`:

```dotenv
ODYSSEUS_IMAGE_TAG=dev
```

## Updates

```bash
docker compose pull
docker compose up -d
```

The upstream `main` and `dev` branches are checked automatically. A new image
is published when the corresponding source or underlying container base image
changes. Stable and development builds have separate immutable CalVer tags,
while `latest`, `main`, and `dev` always point to the current image for their
channel.

Every candidate image must pass image-level compatibility checks and boot with
the complete Compose package before any channel or immutable tag is published.
The full-stack gate verifies Odysseus health, all bundled MCP servers, ChromaDB,
ntfy, and a real SearXNG query through the Odysseus search API. Scheduled runs
repeat the Compose test against current images even when no rebuild is needed.

The Compose package is also synchronized automatically from upstream `main`.
The Odysseus source-build directive is replaced with the corresponding
`ghcr.io/netspeedy/odysseusai` image; upstream service versions and runtime
settings are otherwise preserved.

## Upstream Relationship

This is an independent container packaging project. It is not maintained by,
affiliated with, or endorsed by the Odysseus project.

Odysseus application code is built directly from
[`odysseus-dev/odysseus`](https://github.com/odysseus-dev/odysseus) without
application code patches. The current packaging-only dependency patch enforces
`mcp<2`: upstream `main` leaves MCP unbounded while its bundled servers still
use the MCP 1.x API, and upstream `dev` already declares the same limit. The
patch is applied only to the image build context, recorded in image metadata,
and kept in a small public constraints file. Application features, bugs, and
documentation belong to the upstream project. Image publishing and
deployment-bundle issues belong in this repository.

## License

Odysseus is licensed under AGPL-3.0-or-later. The upstream license is included
in `LICENSE`.
