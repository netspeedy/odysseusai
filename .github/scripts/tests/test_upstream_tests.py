"""Tests for the upstream test selection."""

from pathlib import Path
import sys
import tempfile
import textwrap
import unittest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from upstream_tests import (  # noqa: E402
    module_name,
    patched_paths,
    python_version,
    references_module,
    select_tests,
)


PATCH = """\
diff --git a/services/search/providers.py b/services/search/providers.py
index 5a2d9a6..22fffd8 100644
--- a/services/search/providers.py
+++ b/services/search/providers.py
@@ -1 +1 @@
-old
+new
diff --git a/tests/test_provider_guards.py b/tests/test_provider_guards.py
index d7735ec..444acb2 100644
--- a/tests/test_provider_guards.py
+++ b/tests/test_provider_guards.py
@@ -1 +1 @@
-old
+new
"""


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(text), encoding="utf-8")


class SelectionTests(unittest.TestCase):
    def test_patched_paths_reads_every_diff_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            patches = Path(tmp)
            write(patches, "0001-search.patch", PATCH)
            write(
                patches,
                "0002-vision.patch",
                "diff --git a/src/chat_helpers.py b/src/chat_helpers.py\n",
            )

            self.assertEqual(
                patched_paths(patches),
                [
                    "services/search/providers.py",
                    "src/chat_helpers.py",
                    "tests/test_provider_guards.py",
                ],
            )

    def test_module_name_skips_tests_and_non_python(self) -> None:
        self.assertEqual(
            module_name("services/search/providers.py"), "services.search.providers"
        )
        self.assertEqual(module_name("services/search/__init__.py"), "services.search")
        self.assertIsNone(module_name("tests/test_provider_guards.py"))
        self.assertIsNone(module_name("requirements.txt"))

    def test_references_module_forms(self) -> None:
        module = "services.search.providers"
        for text in (
            "from services.search.providers import searxng_search_api\n",
            "import services.search.providers as providers\n",
            "monkeypatch.setattr('services.search.providers.httpx.get', get)\n",
            "    from services.search import providers\n",
            "from services.search import (\n    core,\n    providers,\n)\n",
            "source = ROOT / 'services/search/providers.py'\n",
        ):
            with self.subTest(text=text):
                self.assertTrue(references_module(text, module))

    def test_references_module_ignores_neighbours(self) -> None:
        module = "src.chat_helpers"
        for text in (
            "import routes.chat_helpers as chat_helpers\n",
            "from src import chat_helpers_extra\n",
            "from src.chat_helpers_extra import thing\n",
        ):
            with self.subTest(text=text):
                self.assertFalse(references_module(text, module))

    def test_select_tests_combines_edited_and_importing_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, patches = root / "upstream", root / "patches"
            write(patches, "0001-search.patch", PATCH)
            write(source, "tests/test_provider_guards.py", "def test_guard(): pass\n")
            write(
                source,
                "tests/test_fallbacks.py",
                "from services.search import providers\n",
            )
            write(
                source,
                "tests/search/test_nested.py",
                "import services.search.providers\n",
            )
            write(source, "tests/test_core.py", "from services.search import core\n")
            write(source, "tests/helpers.py", "import services.search.providers\n")

            self.assertEqual(
                select_tests(source, patches),
                [
                    "tests/search/test_nested.py",
                    "tests/test_fallbacks.py",
                    "tests/test_provider_guards.py",
                ],
            )


class PythonVersionTests(unittest.TestCase):
    def test_reads_python_base_through_stage_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text(
                "FROM python:3.14.9-slim AS brotli-base\n"
                "FROM brotli-base AS wheels\n"
                "FROM docker.io/library/node:24-slim AS frontend\n"
                "FROM brotli-base\n",
                encoding="utf-8",
            )

            self.assertEqual(python_version(dockerfile), "3.14")

    def test_missing_python_base_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dockerfile = Path(tmp) / "Dockerfile"
            dockerfile.write_text("FROM debian:trixie-slim\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                python_version(dockerfile)


if __name__ == "__main__":
    unittest.main()
