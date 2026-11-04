#!/usr/bin/env python3
"""Select the upstream tests that cover the packaging patches.

A patch can apply cleanly and still break upstream behaviour, or leave an
upstream test asserting what the patch changed. Upstream's full suite needs
its own CI sandbox, so the pipeline runs the upstream test files that a patch
edits or that import a module a patch edits.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from image_pipeline import dockerfile_base_references


DIFF_HEADER_RE = re.compile(r"^diff --git a/\S+ b/(\S+)$", re.M)
PYTHON_IMAGE_RE = re.compile(
    r"^(?:(?:docker\.io|index\.docker\.io)/)?(?:library/)?python:(\d+\.\d+)"
)


def patched_paths(patches_dir: Path) -> list[str]:
    """Return the upstream paths that the packaging patches edit."""
    paths: set[str] = set()
    for patch in sorted(patches_dir.glob("*.patch")):
        paths.update(DIFF_HEADER_RE.findall(patch.read_text(encoding="utf-8")))
    return sorted(paths)


def is_test_file(path: str) -> bool:
    """Return whether an upstream path is a pytest test module."""
    parts = Path(path).parts
    return len(parts) > 1 and parts[0] == "tests" and re.fullmatch(
        r"test_.*\.py", parts[-1]
    ) is not None


def module_name(path: str) -> str | None:
    """Return the dotted module name of an upstream source path."""
    if not path.endswith(".py") or path.startswith("tests/"):
        return None
    module = path[: -len(".py")].replace("/", ".")
    return module.removesuffix(".__init__")


def references_module(text: str, module: str) -> bool:
    """Return whether test source imports or names a module."""
    dotted = rf"(?<![\w.]){re.escape(module)}(?!\w)"
    if re.search(dotted, text):
        return True
    if module.replace(".", "/") + ".py" in text:
        return True

    package, _, name = module.rpartition(".")
    if not package:
        return False
    imports = re.finditer(
        rf"^\s*from\s+{re.escape(package)}\s+import\s+(\([^)]*\)|[^\n]*)",
        text,
        re.M,
    )
    return any(re.search(rf"\b{re.escape(name)}\b", m.group(1)) for m in imports)


def select_tests(source: Path, patches_dir: Path) -> list[str]:
    """Return the upstream test files that cover the patched paths."""
    paths = patched_paths(patches_dir)
    selected = {
        path for path in paths if is_test_file(path) and (source / path).is_file()
    }
    modules = [module for module in map(module_name, paths) if module]

    for test in sorted((source / "tests").rglob("test_*.py")):
        relative = test.relative_to(source).as_posix()
        if relative in selected:
            continue
        text = test.read_text(encoding="utf-8", errors="replace")
        if any(references_module(text, module) for module in modules):
            selected.add(relative)
    return sorted(selected)


def python_version(dockerfile: Path) -> str:
    """Return the Python minor version of the Dockerfile's python base image."""
    for reference in dockerfile_base_references(dockerfile.read_text(encoding="utf-8")):
        match = PYTHON_IMAGE_RE.match(reference)
        if match:
            return match.group(1)
    raise ValueError(f"No python base image found in {dockerfile}")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    select_parser = commands.add_parser(
        "select", help="Print the upstream test files that cover the patches"
    )
    select_parser.add_argument("--source", required=True, type=Path)
    select_parser.add_argument("--patches", required=True, type=Path)

    python_parser = commands.add_parser(
        "python", help="Print the Python version the upstream image runs"
    )
    python_parser.add_argument("--source", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    """Run the selected command."""
    args = parse_args()
    if args.command == "select":
        for path in select_tests(args.source, args.patches):
            print(path)
    else:
        print(python_version(args.source / "Dockerfile"))


if __name__ == "__main__":
    main()
