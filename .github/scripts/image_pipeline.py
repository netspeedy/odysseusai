#!/usr/bin/env python3
"""Plan and verify Odysseus container publications."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
STABLE_VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}\.[1-9]\d*$")
DEVELOPMENT_VERSION_RE = re.compile(
    r"^\d{4}\.\d{2}\.\d{2}\.[1-9]\d*-dev$"
)
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"


def command_output(args: list[str], *, check: bool = True, cwd: Path | None = None) -> str:
    """Run a command and return trimmed stdout."""
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Command failed ({' '.join(args)}): {message}")
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def logical_dockerfile_lines(text: str) -> list[str]:
    """Return Dockerfile instructions with line continuations joined."""
    lines: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        lines.append(pending)
        pending = ""
    if pending:
        lines.append(pending)
    return lines


def dockerfile_base_references(text: str) -> list[str]:
    """Return unique external FROM references from a Dockerfile."""
    aliases: set[str] = set()
    references: list[str] = []

    for line in logical_dockerfile_lines(text):
        tokens = shlex.split(line, comments=True)
        if not tokens or tokens[0].lower() != "from":
            continue

        index = 1
        while index < len(tokens) and tokens[index].startswith("--"):
            index += 1
        if index >= len(tokens):
            raise ValueError(f"Invalid FROM instruction: {line}")

        reference = tokens[index]
        if "$" in reference:
            raise ValueError(
                f"Dockerfile FROM variables are not supported by the image planner: {reference}"
            )
        if reference.lower() not in aliases and reference not in references:
            references.append(reference)

        remaining = tokens[index + 1 :]
        for token_index, token in enumerate(remaining[:-1]):
            if token.lower() == "as":
                aliases.add(remaining[token_index + 1].lower())
                break

    if not references:
        raise ValueError("Dockerfile does not contain an external base image")
    return references


def inspect_image(reference: str) -> dict[str, Any] | None:
    """Return labels and manifest details for an image reference."""
    labels_raw = command_output(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{json .Image.Config.Labels}}",
        ],
        check=False,
    )
    if not labels_raw:
        return None

    manifest_raw = command_output(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{json .Manifest}}",
        ]
    )
    return {
        "labels": json.loads(labels_raw),
        "manifest": json.loads(manifest_raw),
    }


def resolve_base_images(dockerfile: Path) -> tuple[list[str], str]:
    """Resolve Dockerfile base references and return a stable fingerprint."""
    references = dockerfile_base_references(dockerfile.read_text(encoding="utf-8"))
    resolved: list[str] = []
    for reference in references:
        digest = command_output(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                reference,
                "--format",
                "{{.Manifest.Digest}}",
            ]
        )
        if not DIGEST_RE.fullmatch(digest):
            raise RuntimeError(f"Invalid manifest digest for {reference}: {digest}")
        resolved.append(f"{reference}@{digest}")

    canonical = "\n".join(sorted(resolved))
    fingerprint = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
    return resolved, fingerprint


def valid_version(branch: str, version: str) -> bool:
    """Return whether a published version matches the channel format."""
    pattern = STABLE_VERSION_RE if branch == "main" else DEVELOPMENT_VERSION_RE
    return bool(pattern.fullmatch(version))


def decide_action(
    *,
    branch: str,
    upstream_sha: str,
    packaging_schema: str,
    base_fingerprint: str,
    current: dict[str, Any] | None,
    latest: dict[str, Any] | None,
    force: bool,
) -> tuple[str, str]:
    """Return the build action and a human-readable decision reason."""
    if force:
        return "build", "Manual rebuild requested"
    if current is None:
        return "build", "Channel image is not published"

    labels = current["labels"]
    reasons: list[str] = []
    if labels.get("org.opencontainers.image.revision") != upstream_sha:
        reasons.append("Upstream revision changed")
    if labels.get("io.netspeedy.odysseus.packaging-schema") != packaging_schema:
        reasons.append("Packaging definition changed")
    if labels.get("io.netspeedy.odysseus.base-image-fingerprint") != base_fingerprint:
        reasons.append("Base image changed")
    if not valid_version(branch, labels.get("org.opencontainers.image.version", "")):
        reasons.append("Published version format is outdated")

    if branch == "main":
        current_digest = current.get("manifest", {}).get("digest")
        latest_digest = (latest or {}).get("manifest", {}).get("digest")
        if not latest_digest:
            reasons.append("Latest tag is missing")
        elif latest_digest != current_digest:
            reasons.append("Latest tag does not match stable")

    if reasons:
        return "build", "; ".join(reasons)
    return "skip", "Source and image inputs are unchanged"


def next_version(image_name: str, branch: str, now: datetime) -> str:
    """Return the first unused CalVer tag for the current UTC date."""
    date_version = now.astimezone(timezone.utc).strftime("%Y.%m.%d")
    suffix = "" if branch == "main" else "-dev"
    sequence = 1
    while inspect_image(f"{image_name}:{date_version}.{sequence}{suffix}") is not None:
        sequence += 1
    return f"{date_version}.{sequence}{suffix}"


def github_output(values: dict[str, str]) -> None:
    """Append values to the current GitHub Actions output file."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as output:
        for name, value in values.items():
            if "\n" in value:
                raise ValueError(f"GitHub output {name} must be a single line")
            output.write(f"{name}={value}\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a deterministic JSON result artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def plan(args: argparse.Namespace) -> None:
    """Plan one channel publication and write its result artifact."""
    source = Path(args.source)
    result_path = Path(args.result)
    image_name = os.environ["IMAGE_NAME"]
    branch = os.environ["UPSTREAM_BRANCH"]
    channel_name = os.environ["CHANNEL_NAME"]
    packaging_schema = os.environ["PACKAGING_SCHEMA"]
    force = os.environ.get("FORCE_BUILD", "false").lower() == "true"
    moving_tags = [tag.strip() for tag in os.environ["MOVING_TAGS"].split(",")]

    upstream_sha = command_output(["git", "rev-parse", "HEAD"], cwd=source)
    base_images, base_fingerprint = resolve_base_images(source / "Dockerfile")
    current = inspect_image(f"{image_name}:{branch}")
    latest = inspect_image(f"{image_name}:latest") if branch == "main" else None
    action, reason = decide_action(
        branch=branch,
        upstream_sha=upstream_sha,
        packaging_schema=packaging_schema,
        base_fingerprint=base_fingerprint,
        current=current,
        latest=latest,
        force=force,
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    if action == "build":
        version = next_version(image_name, branch, now)
    else:
        version = current["labels"]["org.opencontainers.image.version"]

    base_images_value = ",".join(base_images)
    build_created = now.isoformat().replace("+00:00", "Z")
    result = {
        "action": action,
        "base_fingerprint": base_fingerprint,
        "base_images": base_images,
        "branch": branch,
        "channel": channel_name,
        "moving_tags": moving_tags,
        "reason": reason,
        "status": "planned",
        "upstream_sha": upstream_sha,
        "version": version,
    }
    write_json(result_path, result)
    github_output(
        {
            "action": action,
            "base_fingerprint": base_fingerprint,
            "base_images": base_images_value,
            "build_created": build_created,
            "reason": reason,
            "upstream_sha": upstream_sha,
            "version": version,
        }
    )
    print(f"{channel_name}: {action} {version} ({reason})")


def require_equal(actual: Any, expected: Any, description: str) -> None:
    """Raise a useful verification error when values differ."""
    if actual != expected:
        raise RuntimeError(f"{description}: expected {expected!r}, found {actual!r}")


def verify(args: argparse.Namespace) -> None:
    """Verify one channel and finalize its result artifact."""
    result_path = Path(args.result)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    image_name = os.environ["IMAGE_NAME"]
    description = os.environ["IMAGE_DESCRIPTION"]
    branch = os.environ["UPSTREAM_BRANCH"]
    upstream_sha = os.environ["UPSTREAM_SHA"]
    packaging_schema = os.environ["PACKAGING_SCHEMA"]
    expected_version = os.environ["EXPECTED_VERSION"]
    base_fingerprint = os.environ["BASE_IMAGE_FINGERPRINT"]
    base_images = os.environ["BASE_IMAGES"]
    image_ref = f"{image_name}:{branch}"

    image = inspect_image(image_ref)
    if image is None:
        raise RuntimeError(f"Published image is unavailable: {image_ref}")
    raw_manifest = json.loads(
        command_output(["docker", "buildx", "imagetools", "inspect", image_ref, "--raw"])
    )
    labels = image["labels"]

    require_equal(image["manifest"].get("mediaType"), OCI_MANIFEST_MEDIA_TYPE, "Media type")
    require_equal(
        raw_manifest.get("annotations", {}).get("org.opencontainers.image.description"),
        description,
        "Manifest description",
    )
    require_equal(labels.get("org.opencontainers.image.description"), description, "Image description")
    require_equal(labels.get("org.opencontainers.image.revision"), upstream_sha, "Upstream revision")
    require_equal(labels.get("org.opencontainers.image.version"), expected_version, "Image version")
    require_equal(labels.get("io.netspeedy.odysseus.channel"), branch, "Image channel")
    require_equal(
        labels.get("io.netspeedy.odysseus.packaging-schema"),
        packaging_schema,
        "Packaging schema",
    )
    require_equal(
        labels.get("io.netspeedy.odysseus.base-image-fingerprint"),
        base_fingerprint,
        "Base image fingerprint",
    )
    require_equal(labels.get("io.netspeedy.odysseus.base-images"), base_images, "Base images")

    if not valid_version(branch, expected_version):
        raise RuntimeError(f"Invalid {branch} CalVer tag: {expected_version}")

    channel_digest = image["manifest"]["digest"]
    version_image = inspect_image(f"{image_name}:{expected_version}")
    if version_image is None:
        raise RuntimeError(f"Version tag is unavailable: {expected_version}")
    require_equal(
        version_image["manifest"].get("digest"),
        channel_digest,
        "Version tag digest",
    )

    if branch == "main":
        latest_image = inspect_image(f"{image_name}:latest")
        if latest_image is None:
            raise RuntimeError("Stable latest tag is unavailable")
        require_equal(
            latest_image["manifest"].get("digest"),
            channel_digest,
            "Latest tag digest",
        )

    result["digest"] = channel_digest
    result["status"] = "published" if result["action"] == "build" else "current"
    write_json(result_path, result)
    github_output({"digest": channel_digest, "version": expected_version})
    print(f"Verified {image_ref}@{channel_digest}")


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan", help="Plan an image publication")
    plan_parser.add_argument("--source", required=True)
    plan_parser.add_argument("--result", required=True)
    plan_parser.set_defaults(handler=plan)

    verify_parser = commands.add_parser("verify", help="Verify a published image")
    verify_parser.add_argument("--result", required=True)
    verify_parser.set_defaults(handler=verify)
    return cli


def main() -> None:
    """Run the requested pipeline helper command."""
    args = parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
