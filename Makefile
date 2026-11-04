PYTHON ?= python3
HERO := scripts/readme

.PHONY: test readme-hero

test:
	$(PYTHON) -m unittest discover -s .github/scripts/tests

readme-hero:
	$(PYTHON) $(HERO)/build_readme_hero.py
