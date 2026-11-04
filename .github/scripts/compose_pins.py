#!/usr/bin/env python3
"""Keep the bundle's third-party image pins when they are newer than upstream's.

Dependabot bumps the ChromaDB, SearXNG and ntfy pins in docker-compose.yml, and
the nightly sync regenerates that file from upstream, which doesn't keep its
Compose pins current. Without this step every sync would revert those bumps.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re


SERVICE_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):$")
IMAGE_RE = re.compile(r"^    image: (\S+)$")
VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")
# The Odysseus image is this repository's own, set by the sync.
OWN_SERVICES = frozenset({"odysseus"})


@dataclass(frozen=True)
class Reference:
    """A parsed image reference."""

    repository: str
    tag: str | None
    digest: str | None


def parse_reference(image: str) -> Reference | None:
    """Parse repository[:tag][@digest], or return None for interpolated values."""
    if "$" in image:
        return None
    name, _, digest = image.partition("@")
    slash = name.rfind("/")
    colon = name.rfind(":")
    tag = None
    if colon > slash:
        name, tag = name[:colon], name[colon + 1 :]
    repository = name.removeprefix("docker.io/").removeprefix("library/")
    return Reference(repository, tag, digest or None)


def version_key(tag: str | None) -> tuple[int, ...] | None:
    """Return the leading numeric version of a tag, such as (2026, 11, 2)."""
    match = VERSION_RE.match(tag or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def newer_is_ours(ours: str, upstream: str) -> bool:
    """Return whether our pin should survive upstream's for the same service."""
    our_ref, upstream_ref = parse_reference(ours), parse_reference(upstream)
    if our_ref is None or upstream_ref is None:
        return False
    if our_ref.repository != upstream_ref.repository or our_ref.digest is None:
        return False
    if upstream_ref.digest is None:
        return True
    our_version, upstream_version = version_key(our_ref.tag), version_key(upstream_ref.tag)
    if our_version is None or upstream_version is None:
        return False
    # A tie keeps ours, so digest-only bumps and same-day SearXNG builds stay.
    return our_version >= upstream_version


def service_images(text: str) -> dict[str, tuple[int, str]]:
    """Map each Compose service to its image line number and value."""
    images: dict[str, tuple[int, str]] = {}
    service = None
    for number, line in enumerate(text.splitlines()):
        match = SERVICE_RE.match(line)
        if match:
            service = match.group(1)
            continue
        match = IMAGE_RE.match(line)
        if match and service and service not in images:
            images[service] = (number, match.group(1))
    return images


def keep_newer(previous: str, generated: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Return generated Compose text with our newer pins restored."""
    ours = service_images(previous)
    lines = generated.splitlines(keepends=True)
    kept = []
    for service, (number, upstream) in service_images(generated).items():
        if service in OWN_SERVICES or service not in ours:
            continue
        our_image = ours[service][1]
        if our_image != upstream and newer_is_ours(our_image, upstream):
            lines[number] = lines[number].replace(upstream, our_image, 1)
            kept.append((service, our_image, upstream))
    return "".join(lines), kept


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, type=Path)
    parser.add_argument("--generated", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    """Rewrite the generated Compose file in place."""
    args = parse_args()
    text, kept = keep_newer(
        args.previous.read_text(encoding="utf-8"),
        args.generated.read_text(encoding="utf-8"),
    )
    args.generated.write_text(text, encoding="utf-8")
    for service, ours, upstream in kept:
        print(f"Keeping {ours} for {service}; upstream pins {upstream}")


if __name__ == "__main__":
    main()
