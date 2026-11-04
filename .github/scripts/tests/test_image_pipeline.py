"""Tests for the Odysseus image pipeline helper."""

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import image_pipeline  # noqa: E402
from image_pipeline import (  # noqa: E402
    REGISTRY_ATTEMPTS,
    app_version,
    apply_constraints,
    apply_patches,
    decide_action,
    dockerfile_base_references,
    image_content_digests,
    image_version,
    index_platforms,
    inspect_image,
    next_version,
    promoted_upstream_tags,
    promotion_command,
    registry_output,
    stable_channel_command,
    tested_candidates,
    upstream_tags,
    valid_version,
)


def image_state(*, digest: str, revision: str, schema: str, base: str, build: str):
    """Return a minimal image state for decision tests."""
    return {
        "labels": {
            "org.opencontainers.image.revision": revision,
            "org.opencontainers.image.version": "1.0.3",
            "io.netspeedy.odysseus.build": build,
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
            build="2026.08.14.1",
        )
        self.latest = image_state(
            digest="sha256:current",
            revision="abc123",
            schema="5",
            base="sha256:base",
            build="2026.08.14.1",
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

    def test_image_without_a_build_label_is_rebuilt(self):
        # Images published before the build label carried CalVer as their version.
        labels = self.current["labels"]
        labels["org.opencontainers.image.version"] = labels.pop("io.netspeedy.odysseus.build")

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

        self.assertEqual(action, "build")
        self.assertIn("Published build tag is missing or outdated", reason)

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
    def test_promotion_publishes_the_tested_manifests_as_one_annotated_index(self):
        amd64 = "sha256:" + "a" * 64
        arm64 = "sha256:" + "c" * 64

        command = promotion_command(
            "ghcr.io/netspeedy/odysseusai",
            [amd64, arm64],
            [
                "ghcr.io/netspeedy/odysseusai:main",
                "ghcr.io/netspeedy/odysseusai:2026.08.14.1",
            ],
            {"org.opencontainers.image.version": "1.0.3"},
        )

        self.assertEqual(
            command,
            [
                "docker",
                "buildx",
                "imagetools",
                "create",
                "--annotation",
                "index:org.opencontainers.image.version=1.0.3",
                "--tag",
                "ghcr.io/netspeedy/odysseusai:main",
                "--tag",
                "ghcr.io/netspeedy/odysseusai:2026.08.14.1",
                f"ghcr.io/netspeedy/odysseusai@{amd64}",
                f"ghcr.io/netspeedy/odysseusai@{arm64}",
            ],
        )

    def test_promotion_refuses_foreign_tags_and_bad_digests(self):
        digest = "sha256:" + "a" * 64
        with self.assertRaises(RuntimeError):
            promotion_command("ghcr.io/netspeedy/odysseusai", [digest], ["ghcr.io/x/y:main"], {})
        with self.assertRaises(RuntimeError):
            promotion_command(
                "ghcr.io/netspeedy/odysseusai", ["sha256:short"], ["ghcr.io/netspeedy/odysseusai:main"], {}
            )

    def test_every_platform_must_pass_before_promotion(self):
        platforms = ["linux/amd64", "linux/arm64"]
        amd64 = "sha256:" + "a" * 64
        arm64 = "sha256:" + "c" * 64
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "amd64").write_text(f"{amd64}\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "linux/arm64 leg did not pass"):
                tested_candidates(directory, platforms, "build")

            (directory / "arm64").write_text(f"{arm64}\n", encoding="utf-8")
            self.assertEqual(tested_candidates(directory, platforms, "build"), [amd64, arm64])

            # A current channel's legs only boot the stack and record nothing.
            (directory / "amd64").write_text("\n", encoding="utf-8")
            (directory / "arm64").write_text("\n", encoding="utf-8")
            self.assertEqual(tested_candidates(directory, platforms, "skip"), [])
            with self.assertRaisesRegex(RuntimeError, "Invalid linux/amd64 candidate"):
                tested_candidates(directory, platforms, "build")

    def test_index_platforms_ignore_attestations(self):
        manifest = {
            "manifests": [
                {"platform": {"os": "linux", "architecture": "arm64"}},
                {"platform": {"os": "unknown", "architecture": "unknown"}},
                {"platform": {"os": "linux", "architecture": "amd64"}},
            ]
        }
        self.assertEqual(index_platforms(manifest), ["linux/amd64", "linux/arm64"])
        self.assertEqual(index_platforms({}), [])

    def test_stable_channel_is_an_annotated_index_over_the_tested_manifest(self):
        digest = "sha256:" + "b" * 64

        command = stable_channel_command(
            "ghcr.io/netspeedy/odysseusai",
            digest,
            "2026.08.14.1",
            "1.0.3",
            "abc123",
            "Prebuilt Odysseus image",
            "123.1",
            "2026-08-14T12:00:00Z",
        )

        self.assertEqual(command[:4], ["docker", "buildx", "imagetools", "create"])
        self.assertIn("index:io.netspeedy.odysseus.channel-pointer=stable", command)
        self.assertIn(f"index:io.netspeedy.odysseus.stable-digest={digest}", command)
        self.assertIn("index:org.opencontainers.image.version=1.0.3", command)
        self.assertIn("index:io.netspeedy.odysseus.build=2026.08.14.1", command)
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


def completed(returncode: int, stdout: str = "", stderr: str = ""):
    """Return a finished process result for registry command tests."""
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


MISSING = completed(1, stderr="ERROR: ghcr.io/netspeedy/odysseusai:2026.11.04.1: not found")
OUTAGE = completed(
    1,
    stderr='ERROR: failed to do request: Head "https://ghcr.io/v2/": dial tcp: i/o timeout',
)


@mock.patch.object(image_pipeline.time, "sleep")
class RegistryTests(unittest.TestCase):
    def test_missing_reference_is_reported_without_retrying(self, sleep):
        with mock.patch.object(image_pipeline.subprocess, "run", return_value=MISSING) as run:
            self.assertIsNone(inspect_image("ghcr.io/netspeedy/odysseusai:2026.11.04.1"))
        run.assert_called_once()
        sleep.assert_not_called()

    def test_registry_outage_is_retried_then_raised(self, sleep):
        with mock.patch.object(image_pipeline.subprocess, "run", return_value=OUTAGE) as run:
            with self.assertRaises(RuntimeError):
                inspect_image("ghcr.io/netspeedy/odysseusai:main")
        self.assertEqual(run.call_count, REGISTRY_ATTEMPTS)
        self.assertEqual(sleep.call_count, REGISTRY_ATTEMPTS - 1)

    def test_transient_failure_recovers_on_retry(self, sleep):
        results = [OUTAGE, completed(0, stdout="sha256:" + "a" * 64)]
        with mock.patch.object(image_pipeline.subprocess, "run", side_effect=results):
            self.assertEqual(registry_output(["docker", "buildx"]), "sha256:" + "a" * 64)
        sleep.assert_called_once()

    def test_missing_reference_is_an_error_unless_allowed(self, sleep):
        del sleep
        with mock.patch.object(image_pipeline.subprocess, "run", return_value=MISSING):
            with self.assertRaises(RuntimeError):
                registry_output(["docker", "buildx", "imagetools", "inspect", "x", "--raw"])

    def test_next_version_never_reuses_a_tag_because_of_an_outage(self, sleep):
        del sleep
        now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
        with mock.patch.object(image_pipeline.subprocess, "run", return_value=OUTAGE):
            with self.assertRaises(RuntimeError):
                next_version("ghcr.io/netspeedy/odysseusai", "main", now)
        with mock.patch.object(image_pipeline.subprocess, "run", return_value=MISSING):
            self.assertEqual(
                next_version("ghcr.io/netspeedy/odysseusai", "main", now), "2026.11.04.1"
            )


class UpstreamTagTests(unittest.TestCase):
    SHA = "9d0d3720f4ca181d87bef94da826d730acc6d483"

    def app_version_from(self, constants: str | None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            if constants is not None:
                (source / "src").mkdir()
                (source / "src" / "constants.py").write_text(constants, encoding="utf-8")
            return app_version(source)

    def test_app_version_is_read_like_upstreams_workflow(self):
        self.assertEqual(
            self.app_version_from('"""Constants."""\nAPP_VERSION = "1.0.3"\nAPP_NAME = "x"\n'),
            "1.0.3",
        )
        # Upstream's sed keeps the last quoted string on the first APP_VERSION line.
        self.assertEqual(
            self.app_version_from('APP_VERSION = os.environ.get("V", "1.0.4")\n'), "1.0.4"
        )
        self.assertEqual(self.app_version_from('  APP_VERSION = "9.9.9"\n'), "")

    def test_unusable_app_version_is_empty(self):
        self.assertEqual(self.app_version_from(None), "")
        self.assertEqual(self.app_version_from("APP_NAME = 'x'\n"), "")
        self.assertEqual(self.app_version_from('APP_VERSION = "1.0 beta"\n'), "")
        # A CalVer-shaped version would collide with the build tags.
        self.assertEqual(self.app_version_from('APP_VERSION = "2026.11.04.1"\n'), "")

    def test_upstream_tags_match_upstreams_scheme(self):
        self.assertEqual(
            upstream_tags("main", "1.0.3", self.SHA), ("1.0.3", "1.0.3-9d0d372")
        )
        self.assertEqual(upstream_tags("dev", "1.0.3", self.SHA), ("", "1.0.3-dev.9d0d372"))
        self.assertEqual(upstream_tags("main", "", self.SHA), ("", ""))

    def test_image_reports_upstreams_version(self):
        self.assertEqual(image_version("main", "1.0.3", self.SHA, "2026.11.04.5"), "1.0.3")
        self.assertEqual(
            image_version("dev", "1.0.3", self.SHA, "2026.11.04.5-dev"), "1.0.3-dev.9d0d372"
        )
        # Without a usable APP_VERSION there's only our build tag to report.
        self.assertEqual(image_version("main", "", self.SHA, "2026.11.04.5"), "2026.11.04.5")

    @mock.patch.object(image_pipeline.time, "sleep")
    def test_published_pin_is_never_moved(self, sleep):
        del sleep
        image = "ghcr.io/netspeedy/odysseusai"
        platforms = ["linux/amd64", "linux/arm64"]
        both = {
            "manifests": [
                {"platform": {"os": "linux", "architecture": "amd64"}},
                {"platform": {"os": "linux", "architecture": "arm64"}},
            ]
        }
        with mock.patch.object(image_pipeline.subprocess, "run", return_value=MISSING):
            self.assertEqual(
                promoted_upstream_tags(image, "main", "1.0.3", self.SHA, "build", platforms),
                ("1.0.3", "1.0.3-9d0d372"),
            )
        with mock.patch.object(image_pipeline, "inspect_image", return_value={"labels": {}}):
            with mock.patch.object(image_pipeline, "inspect_raw_manifest", return_value=both):
                self.assertEqual(
                    promoted_upstream_tags(image, "main", "1.0.3", self.SHA, "build", platforms),
                    ("1.0.3", ""),
                )
            # A pin from before arm64 builds is a plain amd64 manifest.
            with mock.patch.object(image_pipeline, "inspect_raw_manifest", return_value={}):
                self.assertEqual(
                    promoted_upstream_tags(image, "main", "1.0.3", self.SHA, "build", platforms),
                    ("1.0.3", "1.0.3-9d0d372"),
                )
        with mock.patch.object(image_pipeline.subprocess, "run", return_value=OUTAGE):
            with self.assertRaises(RuntimeError):
                promoted_upstream_tags(image, "dev", "1.0.3", self.SHA, "build", platforms)
        self.assertEqual(
            promoted_upstream_tags(image, "main", "1.0.3", self.SHA, "skip", platforms), ("", "")
        )

if __name__ == "__main__":
    unittest.main()
