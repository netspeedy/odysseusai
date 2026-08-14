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
import time
from typing import Any
import urllib.parse
import urllib.request


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
STABLE_VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}\.[1-9]\d*$")
DEVELOPMENT_VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}\.[1-9]\d*-dev$")
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
REQUIREMENT_NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)")


def command_output(
    args: list[str], *, check: bool = True, cwd: Path | None = None
) -> str:
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


def requirement_name(requirement: str) -> str:
    """Return a normalized package name from a requirement line."""
    match = REQUIREMENT_NAME_RE.match(requirement.strip())
    if not match:
        raise ValueError(f"Invalid requirement: {requirement}")
    return match.group(1).lower().replace("_", "-")


def configured_constraints(path: Path) -> list[str]:
    """Return active packaging constraints from a requirements file."""
    constraints = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not constraints:
        raise ValueError(f"Packaging constraint file is empty: {path}")
    return constraints


def apply_constraints(
    source: Path, constraints_path: Path
) -> tuple[list[str], str, bool]:
    """Apply packaging constraints to the upstream build context."""
    requirements_path = source / "requirements.txt"
    requirements_text = requirements_path.read_text(encoding="utf-8")
    requirements = [
        line.strip()
        for line in requirements_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    requirement_names = {requirement_name(line) for line in requirements}
    constraints = configured_constraints(constraints_path)

    missing = [
        constraint
        for constraint in constraints
        if requirement_name(constraint) not in requirement_names
    ]
    if missing:
        raise RuntimeError(
            "Packaging constraints no longer match upstream requirements: "
            + ", ".join(missing)
        )

    additions = [
        constraint for constraint in constraints if constraint not in requirements
    ]
    if additions:
        block = [
            "",
            "# Compatibility constraints applied by netspeedy/odysseusai packaging.",
            *additions,
        ]
        requirements_path.write_text(
            requirements_text.rstrip() + "\n" + "\n".join(block) + "\n",
            encoding="utf-8",
        )

    canonical = "\n".join(sorted(constraints))
    fingerprint = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
    return constraints, fingerprint, bool(additions)


def valid_version(branch: str, version: str) -> bool:
    """Return whether a published version matches the channel format."""
    pattern = STABLE_VERSION_RE if branch == "main" else DEVELOPMENT_VERSION_RE
    return bool(pattern.fullmatch(version))


def decide_action(
    *,
    branch: str,
    upstream_sha: str,
    packaging_schema: str,
    constraint_fingerprint: str,
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
    if (
        labels.get("io.netspeedy.odysseus.packaging-constraint-fingerprint")
        != constraint_fingerprint
    ):
        reasons.append("Packaging constraints changed")
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
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def prepare(args: argparse.Namespace) -> None:
    """Apply packaging constraints before image planning and building."""
    constraints, fingerprint, changed = apply_constraints(
        Path(args.source), Path(args.constraints)
    )
    constraints_value = ",".join(constraints)
    github_output(
        {
            "changed": str(changed).lower(),
            "constraint_fingerprint": fingerprint,
            "constraints": constraints_value,
        }
    )
    action = "applied" if changed else "already satisfied"
    print(f"Packaging constraints {action}: {constraints_value}")


def plan(args: argparse.Namespace) -> None:
    """Plan one channel publication and write its result artifact."""
    source = Path(args.source)
    result_path = Path(args.result)
    image_name = os.environ["IMAGE_NAME"]
    branch = os.environ["UPSTREAM_BRANCH"]
    channel_name = os.environ["CHANNEL_NAME"]
    packaging_schema = os.environ["PACKAGING_SCHEMA"]
    packaging_constraints = os.environ["PACKAGING_CONSTRAINTS"]
    constraint_fingerprint = os.environ["PACKAGING_CONSTRAINT_FINGERPRINT"]
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
        constraint_fingerprint=constraint_fingerprint,
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
        "packaging_constraint_fingerprint": constraint_fingerprint,
        "packaging_constraints": packaging_constraints,
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
    packaging_constraints = os.environ["PACKAGING_CONSTRAINTS"]
    constraint_fingerprint = os.environ["PACKAGING_CONSTRAINT_FINGERPRINT"]
    expected_version = os.environ["EXPECTED_VERSION"]
    base_fingerprint = os.environ["BASE_IMAGE_FINGERPRINT"]
    base_images = os.environ["BASE_IMAGES"]
    image_ref = f"{image_name}:{branch}"

    image = inspect_image(image_ref)
    if image is None:
        raise RuntimeError(f"Published image is unavailable: {image_ref}")
    raw_manifest = json.loads(
        command_output(
            ["docker", "buildx", "imagetools", "inspect", image_ref, "--raw"]
        )
    )
    labels = image["labels"]

    require_equal(
        image["manifest"].get("mediaType"), OCI_MANIFEST_MEDIA_TYPE, "Media type"
    )
    require_equal(
        raw_manifest.get("annotations", {}).get("org.opencontainers.image.description"),
        description,
        "Manifest description",
    )
    require_equal(
        labels.get("org.opencontainers.image.description"),
        description,
        "Image description",
    )
    require_equal(
        labels.get("org.opencontainers.image.revision"),
        upstream_sha,
        "Upstream revision",
    )
    require_equal(
        labels.get("org.opencontainers.image.version"),
        expected_version,
        "Image version",
    )
    require_equal(labels.get("io.netspeedy.odysseus.channel"), branch, "Image channel")
    require_equal(
        labels.get("io.netspeedy.odysseus.packaging-schema"),
        packaging_schema,
        "Packaging schema",
    )
    require_equal(
        labels.get("io.netspeedy.odysseus.packaging-constraints"),
        packaging_constraints,
        "Packaging constraints",
    )
    require_equal(
        labels.get("io.netspeedy.odysseus.packaging-constraint-fingerprint"),
        constraint_fingerprint,
        "Packaging constraint fingerprint",
    )
    require_equal(
        labels.get("io.netspeedy.odysseus.base-image-fingerprint"),
        base_fingerprint,
        "Base image fingerprint",
    )
    require_equal(
        labels.get("io.netspeedy.odysseus.base-images"), base_images, "Base images"
    )

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


def promotion_command(image_name: str, digest: str, tags: list[str]) -> list[str]:
    """Return a command that copies one tested manifest to its publication tags."""
    if not DIGEST_RE.fullmatch(digest):
        raise RuntimeError(f"Invalid candidate digest: {digest}")
    if not tags:
        raise RuntimeError("No promotion tags were supplied")
    if any(not tag.startswith(f"{image_name}:") for tag in tags):
        raise RuntimeError(f"Promotion tags must belong to {image_name}: {tags}")

    command = [
        "docker",
        "buildx",
        "imagetools",
        "create",
        "--prefer-index=false",
    ]
    for tag in tags:
        command.extend(["--tag", tag])
    command.append(f"{image_name}@{digest}")
    return command


def promote(args: argparse.Namespace) -> None:
    """Promote a tested digest to its channel and immutable tags."""
    del args
    image_name = os.environ["IMAGE_NAME"]
    digest = os.environ["IMAGE_DIGEST"]
    tags = [tag.strip() for tag in os.environ["TAGS"].splitlines() if tag.strip()]
    command_output(promotion_command(image_name, digest, tags))
    print(f"Promoted {image_name}@{digest} to {', '.join(tags)}")


def cleanup(args: argparse.Namespace) -> None:
    """Delete an untagged candidate package version after a failed test."""
    del args
    token = os.environ["GH_TOKEN"]
    owner = os.environ["GITHUB_REPOSITORY_OWNER"]
    image_name = os.environ["IMAGE_NAME"]
    digest = os.environ["IMAGE_DIGEST"]
    if not DIGEST_RE.fullmatch(digest):
        raise RuntimeError(f"Invalid candidate digest: {digest}")

    package = urllib.parse.quote(image_name.rsplit("/", 1)[-1], safe="")
    versions_url = (
        f"https://api.github.com/orgs/{owner}/packages/container/{package}/versions"
        "?per_page=100"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    for attempt in range(10):
        with urllib.request.urlopen(
            urllib.request.Request(versions_url, headers=headers), timeout=15
        ) as response:
            versions = json.load(response)

        candidates = [
            version
            for version in versions
            if version.get("name") == digest
            and not version.get("metadata", {}).get("container", {}).get("tags", [])
        ]
        if candidates:
            version_id = candidates[0]["id"]
            delete_url = versions_url.split("?", 1)[0] + f"/{version_id}"
            request = urllib.request.Request(
                delete_url, headers=headers, method="DELETE"
            )
            with urllib.request.urlopen(request, timeout=15):
                pass
            print(f"Deleted failed untagged candidate {digest}")
            return

        if attempt < 9:
            time.sleep(2)

    raise RuntimeError(f"Untagged candidate package version was not found: {digest}")


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)

    prepare_parser = commands.add_parser(
        "prepare", help="Apply packaging constraints to the build context"
    )
    prepare_parser.add_argument("--source", required=True)
    prepare_parser.add_argument("--constraints", required=True)
    prepare_parser.set_defaults(handler=prepare)

    plan_parser = commands.add_parser("plan", help="Plan an image publication")
    plan_parser.add_argument("--source", required=True)
    plan_parser.add_argument("--result", required=True)
    plan_parser.set_defaults(handler=plan)

    verify_parser = commands.add_parser("verify", help="Verify a published image")
    verify_parser.add_argument("--result", required=True)
    verify_parser.set_defaults(handler=verify)

    promote_parser = commands.add_parser("promote", help="Promote a tested digest")
    promote_parser.set_defaults(handler=promote)

    cleanup_parser = commands.add_parser(
        "cleanup", help="Delete a failed untagged candidate digest"
    )
    cleanup_parser.set_defaults(handler=cleanup)
    return cli


def main() -> None:
    """Run the requested pipeline helper command."""
    args = parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
