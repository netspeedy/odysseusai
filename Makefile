PYTHON ?= python3
HERO := scripts/readme
CHROME ?= /Applications/Google Chrome.app/Contents/MacOS/Google Chrome

.PHONY: test readme-hero social-card

test:
	$(PYTHON) -m unittest discover -s .github/scripts/tests

# Writes the README hero and the social card SVG.
readme-hero:
	uv run $(HERO)/build_readme_hero.py

# GitHub's social preview takes a PNG, so render the card in headless Chrome,
# which honours the embedded fonts. Upload it in Settings > Social preview.
social-card: readme-hero
	"$(CHROME)" --headless=new --disable-gpu --hide-scrollbars --window-size=1280,640 \
		--virtual-time-budget=3000 --screenshot="$(CURDIR)/assets/odysseusai-social-card.png" \
		"file://$(CURDIR)/assets/odysseusai-social-card.svg"
