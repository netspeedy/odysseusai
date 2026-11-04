"""Tests for keeping the bundle's newer image pins through a sync."""

from pathlib import Path
import sys
import unittest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from compose_pins import (  # noqa: E402
    Reference,
    keep_newer,
    newer_is_ours,
    parse_reference,
    version_key,
)


NTFY_OLD = "docker.io/binwiederhier/ntfy:v2.32.0@sha256:" + "a" * 64
NTFY_NEW = "docker.io/binwiederhier/ntfy:v2.37.2@sha256:" + "b" * 64
SEARXNG_OLD = "docker.io/searxng/searxng:2026.10.24-ef0ec03da@sha256:" + "c" * 64
SEARXNG_NEW = "docker.io/searxng/searxng:2026.11.2-cccbc9e15@sha256:" + "d" * 64


def compose(ntfy: str, searxng: str, odysseus: str = "ghcr.io/odysseus-dev/odysseus:latest") -> str:
    return (
        "services:\n"
        "  odysseus:\n"
        f"    image: {odysseus}\n"
        "    environment:\n"
        "      - SEARXNG_INSTANCE=http://searxng:8080\n"
        "\n"
        "  searxng:\n"
        "    # A comment\n"
        f"    image: {searxng}\n"
        "\n"
        "  ntfy:\n"
        f"    image: {ntfy}\n"
        "    command: serve\n"
        "\n"
        "volumes:\n"
        "  chromadb-data:\n"
    )


class ReferenceTests(unittest.TestCase):
    def test_parse_reference(self) -> None:
        self.assertEqual(
            parse_reference(NTFY_OLD),
            Reference("binwiederhier/ntfy", "v2.32.0", "sha256:" + "a" * 64),
        )
        self.assertEqual(
            parse_reference("registry.example:5000/team/app"),
            Reference("registry.example:5000/team/app", None, None),
        )
        self.assertEqual(
            parse_reference("docker.io/library/python:3.14-slim"),
            Reference("python", "3.14-slim", None),
        )
        self.assertIsNone(parse_reference("${SEARXNG_IMAGE:-searxng/searxng:1}"))

    def test_version_key(self) -> None:
        self.assertEqual(version_key("2026.11.2-cccbc9e15"), (2026, 11, 2))
        self.assertEqual(version_key("v2.37.2"), (2, 37, 2))
        self.assertGreater(version_key("2026.11.2-x"), version_key("2026.10.24-y"))
        self.assertIsNone(version_key("latest"))
        self.assertIsNone(version_key(None))


class ChoiceTests(unittest.TestCase):
    def test_newer_or_equal_pin_is_ours(self) -> None:
        self.assertTrue(newer_is_ours(NTFY_NEW, NTFY_OLD))
        self.assertTrue(newer_is_ours(NTFY_OLD.replace("a" * 64, "e" * 64), NTFY_OLD))
        self.assertFalse(newer_is_ours(NTFY_OLD, NTFY_NEW))

    def test_pinned_ours_beats_unpinned_upstream(self) -> None:
        self.assertTrue(newer_is_ours(NTFY_OLD, "docker.io/binwiederhier/ntfy"))
        self.assertTrue(newer_is_ours(NTFY_OLD, "binwiederhier/ntfy:latest"))
        self.assertFalse(newer_is_ours("binwiederhier/ntfy:v9.0.0", NTFY_OLD))

    def test_upstream_wins_when_it_cannot_be_compared(self) -> None:
        self.assertFalse(newer_is_ours(NTFY_NEW, "docker.io/other/ntfy:v1.0.0@sha256:" + "f" * 64))
        self.assertFalse(newer_is_ours(NTFY_NEW, "binwiederhier/ntfy:edge@sha256:" + "f" * 64))
        self.assertFalse(newer_is_ours("${NTFY_IMAGE:-x}", NTFY_OLD))


class KeepNewerTests(unittest.TestCase):
    def test_dependabot_bump_survives_an_older_upstream_pin(self) -> None:
        previous = compose(NTFY_NEW, SEARXNG_NEW, "ghcr.io/netspeedy/odysseusai:latest")
        generated = compose(NTFY_OLD, SEARXNG_OLD, "ghcr.io/netspeedy/odysseusai:latest")

        text, kept = keep_newer(previous, generated)

        self.assertEqual(text, previous)
        self.assertEqual(
            kept,
            [("searxng", SEARXNG_NEW, SEARXNG_OLD), ("ntfy", NTFY_NEW, NTFY_OLD)],
        )

    def test_newer_upstream_pin_wins(self) -> None:
        previous = compose(NTFY_OLD, SEARXNG_NEW)
        generated = compose(NTFY_NEW, SEARXNG_OLD)

        text, kept = keep_newer(previous, generated)

        self.assertEqual(text, compose(NTFY_NEW, SEARXNG_NEW))
        self.assertEqual(kept, [("searxng", SEARXNG_NEW, SEARXNG_OLD)])

    def test_own_image_and_new_services_come_from_the_sync(self) -> None:
        previous = "services:\n  odysseus:\n    image: mine:1@sha256:" + "a" * 64 + "\n"
        generated = compose(NTFY_OLD, SEARXNG_OLD)

        text, kept = keep_newer(previous, generated)

        self.assertEqual(text, generated)
        self.assertEqual(kept, [])


if __name__ == "__main__":
    unittest.main()
