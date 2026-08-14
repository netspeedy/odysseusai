#!/usr/bin/env python3
"""Write the Odysseus container pipeline GitHub Actions summary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


CHANNEL_ORDER = {"main": 0, "dev": 1}


def cell(value: Any) -> str:
    """Escape a value for a Markdown table cell."""
    if value is None or value == "":
        return "-"
    return str(value).replace("|", "\\|").replace("\n", " ")


def code(value: str) -> str:
    """Return a compact inline-code value."""
    return f"`{value}`" if value else "-"


def short_digest(value: str) -> str:
    """Return a compact digest while retaining the algorithm prefix."""
    if not value:
        return "-"
    algorithm, separator, digest = value.partition(":")
    if not separator:
        return value[:16]
    return f"{algorithm}:{digest[:12]}"


def load_results(directory: Path) -> list[dict[str, Any]]:
    """Load channel result artifacts."""
    results: list[dict[str, Any]] = []
    if not directory.exists():
        return results
    for path in directory.glob("*.json"):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(results, key=lambda result: CHANNEL_ORDER.get(result.get("branch", ""), 99))


def result_label(result: dict[str, Any], build_result: str) -> str:
    """Return the publication state shown in the summary."""
    status = result.get("status")
    if status == "published":
        return "Published"
    if status == "current":
        return "Current"
    if build_result in {"failure", "cancelled", "timed_out"}:
        return "Failed"
    return "Incomplete"


def link(label: str, url: str) -> str:
    """Return a Markdown link."""
    return f"[{cell(label)}]({url})"


def deployment_row() -> tuple[str, str, str]:
    """Return deployment synchronization summary values."""
    sync_result = os.environ.get("SYNC_RESULT", "unknown")
    changed = os.environ.get("SYNC_CHANGED", "false") == "true"
    upstream_sha = os.environ.get("SYNC_UPSTREAM_SHA", "")
    if sync_result == "success":
        status = "Updated" if changed else "Current"
    elif sync_result == "cancelled":
        status = "Cancelled"
    else:
        status = "Failed"

    detail = "Upstream main"
    if upstream_sha:
        commit_url = f"https://github.com/odysseus-dev/odysseus/commit/{upstream_sha}"
        detail = f"Upstream main at {link(upstream_sha[:12], commit_url)}"
    return "Deployment bundle", status, detail


def render(results: list[dict[str, Any]]) -> str:
    """Render the complete GitHub Actions summary."""
    repository = os.environ.get("GITHUB_REPOSITORY", "netspeedy/odysseusai")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    event = os.environ.get("GITHUB_EVENT_NAME", "-")
    build_result = os.environ.get("BUILD_RESULT", "unknown")
    run_url = f"{server}/{repository}/actions/runs/{run_id}" if run_id else ""
    package_url = f"{server}/{repository}/pkgs/container/odysseusai"

    lines = [
        "# Odysseus Container Pipeline",
        "",
        "Automated images built from the official Odysseus source and published by this independent packaging project.",
        "",
        "| Component | Result | Detail |",
        "| --- | --- | --- |",
    ]
    component, status, detail = deployment_row()
    lines.append(f"| {component} | {status} | {detail} |")

    for result in results:
        channel = f"{result.get('channel', result.get('branch', 'Image'))} image"
        version = result.get("version", "")
        lines.append(
            f"| {cell(channel)} | {result_label(result, build_result)} | "
            f"{link(version, package_url) if version else '-'} |"
        )

    if not results:
        fallback = "Failed" if build_result != "success" else "No result artifacts"
        lines.append(f"| Image publishing | {fallback} | - |")

    lines.extend(
        [
            "",
            "## Image Details",
            "",
            "| Channel | Immutable tag | Moving tags | Upstream revision | Digest | Decision |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for result in results:
        upstream_sha = result.get("upstream_sha", "")
        commit_url = f"https://github.com/odysseus-dev/odysseus/commit/{upstream_sha}"
        moving_tags = ", ".join(code(tag) for tag in result.get("moving_tags", []))
        lines.append(
            "| {channel} | {version} | {moving_tags} | {upstream} | {digest} | {reason} |".format(
                channel=cell(result.get("channel", result.get("branch", "-"))),
                version=code(result.get("version", "")),
                moving_tags=moving_tags or "-",
                upstream=link(upstream_sha[:12], commit_url) if upstream_sha else "-",
                digest=code(short_digest(result.get("digest", ""))),
                reason=cell(result.get("reason", "-")),
            )
        )

    if not results:
        lines.append("| - | - | - | - | - | No channel results were available |")

    lines.extend(
        [
            "",
            "## References",
            "",
            "- [Odysseus website](https://odysseus-dev.github.io/odysseus)",
            "- [Official upstream source](https://github.com/odysseus-dev/odysseus)",
            f"- [Published container images]({package_url})",
            f"- [Deployment bundle]({server}/{repository})",
            "",
            f"Trigger: `{event}`" + (f" | [Workflow run]({run_url})" if run_url else ""),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """Load result artifacts and append the run summary."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--results", default="build-results")
    cli.add_argument("--output", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = cli.parse_args()
    if not args.output:
        raise SystemExit("GITHUB_STEP_SUMMARY or --output is required")

    summary = render(load_results(Path(args.results)))
    with Path(args.output).open("a", encoding="utf-8") as output:
        output.write(summary)


if __name__ == "__main__":
    main()
