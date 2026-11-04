PYTHON ?= python3
HERO := scripts/readme

.PHONY: test actionlint readme-hero

test:
	$(PYTHON) -m unittest discover -s .github/scripts/tests

# The same pinned actionlint image CI runs.
actionlint:
	docker build --quiet --tag odysseusai-actionlint scripts/actionlint >/dev/null
	docker run --rm --volume "$(CURDIR):/repo" --workdir /repo odysseusai-actionlint -color

# Writes the README hero and the social card PNG, which needs Chrome or
# Chromium (set CHROME if it isn't found).
readme-hero:
	uv run $(HERO)/build_readme_hero.py
