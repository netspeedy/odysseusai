"""Tests for the Odysseus image pipeline helper."""

from pathlib import Path
import sys
import unittest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from image_pipeline import decide_action, dockerfile_base_references, valid_version  # noqa: E402


def image_state(*, digest: str, revision: str, schema: str, base: str, version: str):
    """Return a minimal image state for decision tests."""
    return {
        "labels": {
            "org.opencontainers.image.revision": revision,
            "org.opencontainers.image.version": version,
            "io.netspeedy.odysseus.packaging-schema": schema,
            "io.netspeedy.odysseus.base-image-fingerprint": base,
        },
        "manifest": {"digest": digest},
    }


class DockerfileParsingTests(unittest.TestCase):
    def test_external_base_references_are_unique_and_stage_aliases_are_ignored(self):
        dockerfile = """
        FROM --platform=linux/amd64 python:3.14-slim AS wheels
        FROM python:3.14-slim
        COPY --from=wheels /wheels /wheels
        """

        self.assertEqual(dockerfile_base_references(dockerfile), ["python:3.14-slim"])


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.current = image_state(
            digest="sha256:current",
            revision="abc123",
            schema="5",
            base="sha256:base",
            version="2026.08.14.1",
        )
        self.latest = image_state(
            digest="sha256:current",
            revision="abc123",
            schema="5",
            base="sha256:base",
            version="2026.08.14.1",
        )

    def test_unchanged_stable_image_is_skipped(self):
        action, reason = decide_action(
            branch="main",
            upstream_sha="abc123",
            packaging_schema="5",
            base_fingerprint="sha256:base",
            current=self.current,
            latest=self.latest,
            force=False,
        )

        self.assertEqual(action, "skip")
        self.assertEqual(reason, "Source and image inputs are unchanged")

    def test_base_image_change_triggers_build(self):
        action, reason = decide_action(
            branch="main",
            upstream_sha="abc123",
            packaging_schema="5",
            base_fingerprint="sha256:new-base",
            current=self.current,
            latest=self.latest,
            force=False,
        )

        self.assertEqual(action, "build")
        self.assertIn("Base image changed", reason)

    def test_development_version_uses_suffix(self):
        self.assertTrue(valid_version("dev", "2026.08.14.1-dev"))
        self.assertFalse(valid_version("dev", "2026.08.14.1"))


if __name__ == "__main__":
    unittest.main()
