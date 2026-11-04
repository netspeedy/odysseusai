# Repository Guidelines

This repository publishes `ghcr.io/netspeedy/odysseusai` container images of
upstream Odysseus (`odysseus-dev/odysseus`) and the Compose bundle that runs
them. It is an independent packaging project: application code stays
upstream, and only narrow compatibility changes are applied here, to the
temporary build context.

## Layout

- The deployment bundle is `docker-compose.yml`, `.env.example`, `LICENSE`,
  `config/searxng/`, `docker/*.yml` and `scripts/migrate_searxng_settings.py`.
  Everything except `config/searxng/limiter.toml` is generated from upstream
  main by `.github/scripts/sync-upstream-runtime.sh`. Don't hand-edit those
  files; change the sync script, which owns the packaging overrides. The list
  of synced files lives in `.github/scripts/lib/bundle.sh`, and the automated
  pull request may only touch those paths. The one other writer is Dependabot,
  which moves the third-party image pins in `docker-compose.yml`;
  `compose_pins.py` keeps a pin of ours through the sync when it is at least
  as new as upstream's.
- Packaging patches are `.github/patches/NNNN-name.patch`, applied in order to
  a fresh upstream checkout. Dependency pins are
  `.github/constraints/requirements.txt`. Both are fingerprinted into the image
  labels, so changing either triggers a rebuild.
- Pipeline scripts are in `.github/scripts/`, with unit tests in
  `.github/scripts/tests/` (stdlib `unittest`). `image_pipeline.py` plans,
  promotes and verifies publications.
- `make readme-hero` generates `assets/odysseusai-readme-hero.svg` and the
  social card, `assets/odysseusai-social-card.png`, with
  `scripts/readme/build_readme_hero.py`. Edit the script, not the generated
  files. `measure_headline.py` is shared byte-for-byte with the other
  netspeedy repositories; leave it as it is.

## Workflows

- Sync Deployment Bundle (`sync-bundle.yml`, 03:41 UTC): syncs the bundle
  from upstream main in a job with no write credentials, validates it and
  smoke-tests SearXNG, then a second job applies only the allowlisted files and
  opens or updates one pull request on `automation/sync-deployment-bundle`,
  authored as the maintainer. Keep that split: the job that can push must never
  run upstream code.
- Build and Validate (`build-and-validate.yml`): pull requests, pushes to
  main and dispatch. Lints, unit tests, patches against upstream main and dev
  together with upstream's tests for the patched code, and the bundle checks,
  ending with the full Compose stack on the published stable image so pin
  bumps are booted before they merge.
- Automation Auto Merge (04:05 UTC, and after Build and Validate) merges
  Dependabot and bundle sync pull requests once Build and Validate has passed
  for the head revision, ahead of the image run. Sync pull requests are
  rebase-merged so the maintainer stays the commit author; a squash would make
  `github-actions[bot]` the author. Dependabot pull requests are squashed.
- Odysseus Container Pipeline (`build-image.yml`, 04:17 UTC and on pushes
  that touch the pipeline): per channel, a plan job reads GHCR anonymously,
  decides the tags and runs upstream's tests for the patched code with
  `test-upstream.sh`. It has no registry credential, so upstream code never
  runs next to one; keep it that way. It uploads the plan before the tests
  and a `tested-<branch>` marker after them. A build leg per platform
  (`linux/amd64` on `ubuntu-24.04`, `linux/arm64` on `ubuntu-24.04-arm`)
  builds the planned commit, pushes it by digest only and gates it natively
  with `smoke-test-image.sh` and `smoke-test-compose.sh`. The publish job
  promotes only when every platform passed, as one index to the channel and
  upstream tags, then verifies the labels and platforms. Each
  channel's legs depend only on that channel's artifacts, so a failed dev
  build never holds back stable. Failed candidates are deleted, but never
  once promotion has started: a promoted candidate is an untagged child of a
  tagged index, and deleting it breaks every tag. At 05:17 UTC on Sundays
  both channels are rebuilt without the layer cache (`fresh_build`) and
  published, because upstream's requirements are mostly unpinned and the
  cached pip layer would otherwise keep old releases until the base image or
  requirements change. A last job prunes package versions no tag reaches,
  such as the image a rebuild replaced; `prunable_versions` keeps anything a
  tagged index still holds and anything younger than an hour. A dispatch
  with `upstream_commit` backfills one older upstream main commit: only its
  pin, plus its `X.Y.Z` if that release has no image, and the dev leg and
  the landing version are left alone.
- Dependabot (`.github/dependabot.yml`, daily) covers the Compose images, the
  GitHub Actions and the actionlint image in `scripts/actionlint/Dockerfile`.

## Rules

- Only people appear in this repository's history. Automated commits are
  authored as the maintainer, never as `github-actions[bot]`.
- A patch must be a compatibility fix that upstream doesn't have, with the
  reason in the patch's own comments and in the README's "What the packaging
  changes" section. Features belong upstream. When upstream fixes something,
  drop the patch rather than keeping it as a no-op.
- If a patch changes behaviour that an upstream test asserts, the same patch
  updates that test. `upstream_tests.py` selects the upstream test files a
  patch edits or that import a module it edits, and `test-upstream.sh` runs
  them on the Python version of upstream's Dockerfile.
- Registry and network calls retry: use `registry_output` in
  `image_pipeline.py` and `retry` from `.github/scripts/lib/retry.sh`. A
  registry error must never be read as "tag not published".
- Workflows declare least-privilege permissions per job; the repository default
  token is read-only. Pin actions to major versions (`@v7`). setup-uv
  publishes no major tags, so it's pinned to its exact release and Dependabot
  moves it.
- Every version this repository pins must be one Dependabot can move. Don't
  put an image or tool version where it can't see it, such as a workflow
  `env:` value or a `docker://` reference; actionlint lives in a Dockerfile
  for that reason. The exception is the README hero generator's PEP 723
  pins, which keep rebuilds byte-identical, are shared with the other
  netspeedy repositories and ship nothing.
- Publish the tags upstream publishes. Upstream tags main `X.Y.Z` and
  `X.Y.Z-<sha7>` and dev `X.Y.Z-dev.<sha7>`, from `APP_VERSION` in
  `src/constants.py`; `app_version` and `upstream_tags` in `image_pipeline.py`
  mirror its docker-publish workflow, so change them when upstream changes
  that. The commit pins are immutable upstream, so a pin is promoted only by
  the first build of its commit that has every platform in `PLATFORMS`; a
  pin missing one is republished once. Publish nothing else: stable is
  `latest`, `X.Y.Z` and the pin, dev is `dev` and its pin, chosen by
  `publication_tags`. The image's `org.opencontainers.image.version` is
  upstream's number too (`X.Y.Z` on main, the dev pin on dev, from
  `image_version`). Our build label, `io.netspeedy.odysseus.build`, is the
  UTC date and the run number, and planning reads it to decide whether a
  channel is current.
- Use Conventional Commit subjects. Bodies are prose that explain why and how
  the change was checked.

## Checks

Before handing work back, run what applies:

```bash
make test
shellcheck -x .github/scripts/*.sh
make actionlint
.github/scripts/validate-bundle.sh
.github/scripts/smoke-test-searxng.sh
```

For a patch change, run `python3 .github/scripts/image_pipeline.py prepare`
against fresh clones of upstream main and dev, then
`.github/scripts/test-upstream.sh <clone>` on each. Only the image workflow
builds images, so a new candidate's image and Compose smoke tests run after
merge. Build and Validate boots the Compose stack with the published image.

## Documentation sync

- Update the README's "What the packaging changes" section whenever a patch,
  constraint or Compose override changes.
- Update the README's download-only file list and `lib/bundle.sh` together
  when upstream adds or removes a bundle file.
- Update the README's "How it works" section when a workflow's role or
  schedule changes.
- After changing the hero copy or scene, run `make readme-hero`, then upload
  the new PNG in Settings > General > Social preview.
