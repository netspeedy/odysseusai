"""Tests for the Odysseus image pipeline helper."""

from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from image_pipeline import (  # noqa: E402
    apply_constraints,
    apply_patches,
    decide_action,
    dockerfile_base_references,
    image_content_digests,
    promotion_command,
    stable_channel_command,
    valid_version,
)


def image_state(*, digest: str, revision: str, schema: str, base: str, version: str):
    """Return a minimal image state for decision tests."""
    return {
        "labels": {
            "org.opencontainers.image.revision": revision,
            "org.opencontainers.image.version": version,
            "io.netspeedy.odysseus.packaging-schema": schema,
            "io.netspeedy.odysseus.packaging-constraint-fingerprint": "sha256:constraints",
            "io.netspeedy.odysseus.packaging-patch-fingerprint": "sha256:patches",
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


class ManifestTests(unittest.TestCase):
    def test_single_platform_index_resolves_to_its_runnable_manifest(self):
        digest = "sha256:" + "c" * 64
        image = {"manifest": {"digest": "sha256:" + "d" * 64}}
        manifest = {"manifests": [{"digest": digest}]}

        self.assertEqual(image_content_digests(image, manifest), {digest})

    def test_image_manifest_resolves_to_its_own_digest(self):
        digest = "sha256:" + "e" * 64
        image = {"manifest": {"digest": digest}}

        self.assertEqual(image_content_digests(image, {}), {digest})


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
            constraint_fingerprint="sha256:constraints",
            patch_fingerprint="sha256:patches",
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
            constraint_fingerprint="sha256:constraints",
            patch_fingerprint="sha256:patches",
            base_fingerprint="sha256:new-base",
            current=self.current,
            latest=self.latest,
            force=False,
        )

        self.assertEqual(action, "build")
        self.assertIn("Base image changed", reason)

    def test_packaging_patch_change_triggers_build(self):
        action, reason = decide_action(
            branch="main",
            upstream_sha="abc123",
            packaging_schema="5",
            constraint_fingerprint="sha256:constraints",
            patch_fingerprint="sha256:new-patches",
            base_fingerprint="sha256:base",
            current=self.current,
            latest=self.latest,
            force=False,
        )

        self.assertEqual(action, "build")
        self.assertIn("Packaging patches changed", reason)

    def test_development_version_uses_suffix(self):
        self.assertTrue(valid_version("dev", "2026.08.14.1-dev"))
        self.assertFalse(valid_version("dev", "2026.08.14.1"))


class ConstraintTests(unittest.TestCase):
    def test_constraint_is_added_to_an_unbounded_upstream_requirement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "requirements.txt").write_text(
                "fastapi\nmcp\nuvicorn\n", encoding="utf-8"
            )
            constraints = root / "constraints.txt"
            constraints.write_text("mcp<2\n", encoding="utf-8")

            configured, fingerprint, changed = apply_constraints(source, constraints)

            self.assertEqual(configured, ["mcp<2"])
            self.assertTrue(fingerprint.startswith("sha256:"))
            self.assertTrue(changed)
            self.assertIn(
                "\n# Compatibility constraints applied by netspeedy/odysseusai packaging.\nmcp<2\n",
                (source / "requirements.txt").read_text(encoding="utf-8"),
            )

    def test_existing_constraint_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "requirements.txt").write_text("mcp<2\n", encoding="utf-8")
            constraints = root / "constraints.txt"
            constraints.write_text("mcp<2\n", encoding="utf-8")

            _, _, changed = apply_constraints(source, constraints)

            self.assertFalse(changed)
            self.assertEqual(
                (source / "requirements.txt").read_text(encoding="utf-8"),
                "mcp<2\n",
            )


class PatchTests(unittest.TestCase):
    def test_patch_is_applied_and_fingerprinted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            patches = root / "patches"
            source.mkdir()
            patches.mkdir()
            (source / "example.txt").write_text("before\n", encoding="utf-8")
            (patches / "0001-example.patch").write_text(
                """diff --git a/example.txt b/example.txt
--- a/example.txt
+++ b/example.txt
@@ -1 +1 @@
-before
+after
""",
                encoding="utf-8",
            )

            configured, fingerprint = apply_patches(source, patches)

            self.assertEqual(configured, ["0001-example.patch"])
            self.assertTrue(fingerprint.startswith("sha256:"))
            self.assertEqual(
                (source / "example.txt").read_text(encoding="utf-8"),
                "after\n",
            )


class PromotionTests(unittest.TestCase):
    def test_promotion_preserves_the_tested_single_platform_manifest(self):
        digest = "sha256:" + "a" * 64

        command = promotion_command(
            "ghcr.io/netspeedy/odysseusai",
            digest,
            [
                "ghcr.io/netspeedy/odysseusai:main",
                "ghcr.io/netspeedy/odysseusai:2026.08.14.1",
            ],
        )

        self.assertEqual(
            command,
            [
                "docker",
                "buildx",
                "imagetools",
                "create",
                "--prefer-index=false",
                "--tag",
                "ghcr.io/netspeedy/odysseusai:main",
                "--tag",
                "ghcr.io/netspeedy/odysseusai:2026.08.14.1",
                f"ghcr.io/netspeedy/odysseusai@{digest}",
            ],
        )

    def test_stable_channel_is_an_annotated_index_over_the_tested_manifest(self):
        digest = "sha256:" + "b" * 64

        command = stable_channel_command(
            "ghcr.io/netspeedy/odysseusai",
            digest,
            "2026.08.14.1",
            "abc123",
            "Prebuilt Odysseus image",
            "123.1",
            "2026-08-14T12:00:00Z",
        )

        self.assertEqual(command[:4], ["docker", "buildx", "imagetools", "create"])
        self.assertIn("index:io.netspeedy.odysseus.channel-pointer=stable", command)
        self.assertIn(f"index:io.netspeedy.odysseus.stable-digest={digest}", command)
        self.assertEqual(
            command[-5:],
            [
                "--tag",
                "ghcr.io/netspeedy/odysseusai:main",
                "--tag",
                "ghcr.io/netspeedy/odysseusai:latest",
                f"ghcr.io/netspeedy/odysseusai@{digest}",
            ],
        )


if __name__ == "__main__":
    unittest.main()
