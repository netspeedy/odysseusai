#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools==4.67.0", "brotli==1.2.0"]
# ///
"""Build the animated README hero, assets/odysseusai-readme-hero.svg.

The banner follows the soakes/cv README hero, like the netspeedy/s3ctl and netspeedy/zquota
ones: the odysseusai mark, a two-line headline, a short body and a meta row on the left, and a
generated scene on the right. The scene is a star chart with the publish pipeline plotted on it
as a course, from upstream main through the patch, image smoke test and Compose stack checks to
GHCR. A comet runs the course and lights each waypoint, then the destination flares amber and
the promoted tags appear above it.

The same copy and chart also make assets/odysseusai-social-card.svg, the repository's 1280x640
social preview. GitHub only takes an uploaded image for that, so `make social-card` renders the
PNG beside it, which is uploaded by hand in the repository settings.

Edit the copy, layout or timing here and run `make readme-hero` rather than editing the
generated SVG. The headline is positioned from measured advances (see ADVANCE); if you change
its words, measure them again with measure_headline.py. Inter and JetBrains Mono (SIL Open Font
License 1.1) are the Google Fonts builds, kept on assets.netspeedy.io and pinned by checksum in
FAMILIES. They're subset here to the characters the finished SVG renders and embedded, so the
hero renders the same on every platform, and because fontTools is pinned too, a rebuild gives
the same bytes. It runs under `uv run`, which installs that fontTools, and the first run needs
network access to download the fonts.
"""

from __future__ import annotations

import base64
import hashlib
import io
import math
import random
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "assets" / "odysseusai-readme-hero.svg"
SOCIAL_OUTPUT = ROOT / "assets" / "odysseusai-social-card.svg"
# assets.netspeedy.io's Cloudflare zone turns away urllib's default user agent.
USER_AGENT = "netspeedy-readme-hero"

# The netspeedy banner inks, with an iris accent of its own so this banner doesn't borrow the
# s3ctl blue, the zquota teal or upstream Odysseus's coral.
BG = "#060B13"
INK = "#F1F5FA"
INK_2 = "#B5C0CD"
INK_3 = "#808D9E"
IRIS = "#8C9BFF"
IRIS_2 = "#C9D0FF"
IRIS_3 = "#4C58D0"
AMBER = "#FFB300"
AMBER_2 = "#FFE08A"

# Fonts: css class -> (source font, its sha256, weight range, embedded family name). The
# sources are the Google Fonts builds of Inter 4.001 and JetBrains Mono 2.211, kept on
# assets.netspeedy.io and cached after the first download. A new version goes up there
# under a new filename, so these files never change.
FONT_HOST = "https://assets.netspeedy.io/versioned/fonts"
FONT_CACHE = Path.home() / ".cache" / "netspeedy-fonts"
FAMILIES = {
    "sans": (
        "inter-opsz-wght.4.001.ttf",
        "29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031",
        "400 800",
        "Hero Sans",
    ),
    "mono": (
        "jetbrains-mono-wght.2.211.ttf",
        "48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda",
        "400 700",
        "Hero Mono",
    ),
}
FONT_CSS = (
    ".sans{font-family:'Hero Sans',-apple-system,'Segoe UI','Helvetica Neue',Arial,sans-serif;"
    "font-optical-sizing:auto;}"
    ".mono{font-family:'Hero Mono','SF Mono',Menlo,Consolas,monospace;}"
)
TWINKLE_CSS = (
    ".tw{animation:tw 5.2s ease-in-out infinite;transform-box:fill-box;transform-origin:center}"
    ".tw1{animation-duration:6.4s}.tw2{animation-duration:4.6s}.tw3{animation-duration:7.3s}"
    "@keyframes tw{0%,100%{opacity:.85}50%{opacity:.15}}"
)

# Starfield: seeded, so rebuilding gives the same sky. No star sits behind the copy.
STAR_SEED = 17
STARS = 110
TWINKLING = 16
COPY_BOX = (36, 30, 540, 340)

# Headline advances in px, measured by measure_headline.py on Inter 800 at 44px (opsz 32,
# letter-spacing -1.3). The swash and the gradients are placed from these.
ADVANCE = {
    "Odysseus, ": 212.61,
    "prebuilt": 152.67,
    "Odysseus, prebuilt.": 375.17,
    "Tested before it ships.": 427.67,
}

# The last stable tags the pipeline published, so the scene shows real names.
TAGS = ("latest", "1.0.3", "1.0.3-9d0d372")


# Fonts


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def font_source(name: str, sha256: str) -> bytes:
    """A pinned source font from FONT_HOST, checked against its sha256 and cached."""
    cached = FONT_CACHE / name
    if cached.exists():
        data = cached.read_bytes()
        if hashlib.sha256(data).hexdigest() == sha256:
            return data
    data = fetch(f"{FONT_HOST}/{name}")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit(f"{FONT_HOST}/{name} doesn't match its pinned sha256")
    FONT_CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(data)
    return data


def subset_font(source: bytes, text: str) -> bytes:
    """The variable font cut down to the given characters, as woff2.

    Every axis is kept: trimming them with fontTools' instancer moved the headline's
    pixels. The source's head timestamp is kept too, so the same text always gives the
    same bytes.
    """
    font = TTFont(io.BytesIO(source), recalcTimestamp=False)
    options = subset.Options()
    options.hinting = False
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    font.flavor = "woff2"
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def collect_chars(svg: str) -> dict[str, set[str]]:
    """Characters rendered by each font class, inherited through groups."""
    chars: dict[str, set[str]] = {name: set() for name in FAMILIES}

    def family_of(element: ET.Element, inherited: str | None) -> str | None:
        for cls in (element.get("class") or "").split():
            if cls in FAMILIES:
                return cls
        return inherited

    def walk(element: ET.Element, inherited: str | None, in_text: bool) -> None:
        tag = element.tag.split("}")[-1]
        family = family_of(element, inherited)
        if tag in ("style", "title", "desc", "script"):
            return
        is_text = in_text or tag in ("text", "tspan", "textPath")
        if is_text and tag in ("text", "tspan", "textPath") and element.text:
            if family is None:
                raise SystemExit(f"text without a font class: {element.text!r}")
            chars[family].update(element.text)
        for child in element:
            walk(child, family, is_text)
            if is_text and child.tail and child.tail.strip():
                chars[family or "sans"].update(child.tail)

    walk(ET.fromstring(svg), None, False)
    return chars


def font_faces(svg: str) -> str:
    """One @font-face per family, subset to exactly the characters the SVG renders."""
    chars = collect_chars(svg)
    faces = []
    for cls, (name, sha256, weights, local) in FAMILIES.items():
        used = chars[cls]
        if not used:
            continue
        text = "".join(sorted((used - set("\n\t\r")) | {" "}))
        data = base64.b64encode(subset_font(font_source(name, sha256), text)).decode()
        faces.append(
            f"@font-face{{font-family:'{local}';font-weight:{weights};"
            f"font-style:normal;src:url(data:font/woff2;base64,{data}) "
            "format('woff2');}"
        )
    return "".join(faces)


# Small helpers


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def num(value: float) -> str:
    """One decimal place, without a trailing .0."""
    text = f"{value:.1f}"
    if text == "-0.0":
        return "0"
    return text[:-2] if text.endswith(".0") else text


def pct(value: float) -> str:
    """A keyframe offset: up to two decimal places, no trailing zeros."""
    return f"{value:.2f}".rstrip("0").rstrip(".") + "%"


def mono_width(chars: int, size: float, spacing: float = 0.0) -> float:
    """JetBrains Mono advances exactly 0.6 em; letter-spacing applies per glyph."""
    return chars * (size * 0.6 + spacing)


def chip_width(label: str, size: float = 10.5, spacing: float = 1.2) -> float:
    return mono_width(len(label), size, spacing) - spacing + 16


def gradient(kind: str, attrs: str, stops: list[tuple[str, str, str]]) -> list[str]:
    lines = [f"    <{kind} {attrs}>"]
    for offset, colour, opacity in stops:
        alpha = f' stop-opacity="{opacity}"' if opacity else ""
        lines.append(f'      <stop offset="{offset}" stop-color="{colour}"{alpha} />')
    return [*lines, f"    </{kind}>"]


def chip(
    x: float,
    y: float,
    label: str,
    colour: str,
    cls: str = "",
    style: str = "",
    size: float = 10.5,
    spacing: float = 1.2,
) -> str:
    """A mono label in a small outlined box; (x, y) is the box's top-left corner."""
    width = chip_width(label, size, spacing)
    attr = f' class="{cls}"' if cls else ""
    if style:
        attr += f' style="{style}"'
    return (
        f'<g{attr}><rect x="{num(x)}" y="{num(y)}" width="{num(width)}" height="20" rx="4" '
        f'fill="#0A0F18" fill-opacity="0.9" stroke="{colour}" stroke-opacity="0.4" />'
        f'<text class="mono" x="{num(x + width / 2)}" y="{num(y + 13.8)}" text-anchor="middle" '
        f'font-size="{size}" letter-spacing="{spacing}" fill="{colour}">{esc(label)}</text></g>'
    )


# Shared card


def starfield(
    free: list[tuple[float, float, float, float]],
    width: float = 1200,
    height: float = 400,
    count: int = STARS,
) -> tuple[list[str], list[str]]:
    """Background gradient and nebula defs, plus the body: base, glows and seeded stars."""
    rng = random.Random(STAR_SEED)
    sx, sy = width / 1200, height / 400
    stars = []
    tries = 0
    while len(stars) < count and tries < count * 50:
        tries += 1
        x, y = rng.uniform(8, width - 8), rng.uniform(8, height - 8)
        if any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in free):
            continue
        radius = rng.choice([0.55, 0.6, 0.7, 0.8, 0.9, 1.0, 1.15])
        stars.append((x, y, radius, rng.uniform(0.18, 0.6)))
    twinkling = [
        f'<circle class="tw tw{i % 4}" cx="{num(x)}" cy="{num(y)}" r="{num(r + 0.25)}" '
        f'fill="#E4E8FF" opacity="{o + 0.25:.2f}" '
        f'style="animation-delay:-{rng.uniform(0, 6):.1f}s" />'
        for i, (x, y, r, o) in enumerate(stars[:TWINKLING])
    ]
    plain = "".join(
        f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(r)}" opacity="{o:.2f}" />'
        for x, y, r, o in stars[TWINKLING:]
    )
    defs = [
        '    <linearGradient id="spaceBg" x1="0" y1="0" x2="0.35" y2="1">',
        '      <stop offset="0" stop-color="#090F22" />',
        f'      <stop offset="0.55" stop-color="{BG}" />',
        '      <stop offset="1" stop-color="#0B1226" />',
        "    </linearGradient>",
        *gradient(
            "radialGradient",
            'id="nebIris" cx="0.5" cy="0.5" r="0.5"',
            [("0", IRIS_3, "0.42"), ("1", IRIS_3, "0")],
        ),
        *gradient(
            "radialGradient",
            'id="nebIndigo" cx="0.5" cy="0.5" r="0.5"',
            [("0", "#3B4BB8", "0.3"), ("1", "#3B4BB8", "0")],
        ),
    ]
    body = [
        f'    <rect width="{num(width)}" height="{num(height)}" fill="url(#spaceBg)" />',
        f'    <ellipse cx="{num(190 * sx)}" cy="{num(-30 * sy)}" rx="{num(520 * sx)}" '
        f'ry="{num(260 * sy)}" fill="url(#nebIndigo)" />',
        f'    <ellipse cx="{num(990 * sx)}" cy="{num(430 * sy)}" rx="{num(560 * sx)}" '
        f'ry="{num(300 * sy)}" fill="url(#nebIris)" opacity="0.55" />',
        f'    <g fill="#D6DDFF">{plain}</g>',
        f'    <g>{"".join(twinkling)}</g>',
    ]
    return defs, body


def mark_symbols() -> list[str]:
    """Image layers: an amber top slab, the promoted build, over two iris chevrons."""
    chevron = (
        'fill="none" stroke="url(#markLayer)" stroke-width="4.4" stroke-linecap="round" '
        'stroke-linejoin="round"'
    )
    return [
        *gradient(
            "linearGradient",
            'id="markLayer" x1="0" y1="0" x2="1" y2="0"',
            [("0", IRIS_2, ""), ("1", IRIS, "")],
        ),
        '    <symbol id="mark" viewBox="0 0 64 64">',
        f'      <path d="M32 13.5L50 22.5L32 31.5L14 22.5Z" fill="{AMBER}" />',
        f'      <path d="M14 31.5L32 40.5L50 31.5" {chevron} />',
        f'      <path d="M14 40.5L32 49.5L50 40.5" {chevron} opacity="0.5" />',
        "    </symbol>",
    ]


def lockup() -> list[str]:
    return [
        '  <rect x="48.5" y="38.5" width="33" height="33" rx="8" fill="#10141F" stroke="#3B4560" '
        'stroke-width="1.2" />',
        '  <use href="#mark" x="49" y="39" width="32" height="32" />',
        f'  <text class="sans" x="94" y="52.5" font-size="15" font-weight="600" fill="{INK}">'
        "odysseusai</text>",
        f'  <text class="sans" x="94" y="69.5" font-size="12" fill="{INK_3}">'
        "netspeedy · Odysseus container images</text>",
    ]


def headline(prefix: str, word: str, suffix: str, second: str) -> tuple[list[str], list[str]]:
    """Two lines at 44px: line one with a highlighted word and an amber swash under it."""
    x = 46.0
    first = prefix + word + suffix
    start = x + ADVANCE[prefix]
    bare = word.rstrip(",.")
    end = start + ADVANCE[bare]
    defs = [
        *gradient(
            "linearGradient",
            f'id="titleWhite" gradientUnits="userSpaceOnUse" x1="{num(x)}" y1="0" '
            f'x2="{num(x + ADVANCE[first])}" y2="0"',
            [("0", "#FFFFFF", ""), ("1", "#DDE1FF", "")],
        ),
        *gradient(
            "linearGradient",
            f'id="hl" gradientUnits="userSpaceOnUse" x1="{num(start)}" y1="0" '
            f'x2="{num(end)}" y2="0"',
            [("0", IRIS_2, ""), ("0.5", "#F4F6FF", ""), ("1", AMBER, "")],
        ),
        *gradient(
            "linearGradient",
            f'id="titleIris" gradientUnits="userSpaceOnUse" x1="{num(x)}" y1="0" '
            f'x2="{num(x + ADVANCE[second])}" y2="0"',
            [("0", IRIS_2, ""), ("0.55", IRIS, ""), ("1", "#6E7BF2", "")],
        ),
        *gradient(
            "linearGradient",
            'id="uline" x1="0" y1="0" x2="1" y2="0"',
            [("0", AMBER, "0.35"), ("0.5", AMBER, ""), ("1", AMBER, "0.5")],
        ),
    ]
    span = end - start
    tail = f'<tspan fill="url(#titleWhite)">{esc(suffix)}</tspan>' if suffix else ""
    els = [
        f'  <text class="sans" x="{num(x)}" y="160" font-size="44" font-weight="800" '
        'letter-spacing="-1.3" xml:space="preserve">'
        f'<tspan fill="url(#titleWhite)">{esc(prefix)}</tspan>'
        f'<tspan fill="url(#hl)">{esc(word)}</tspan>{tail}</text>',
        f'  <path d="M{num(start + 2)} 172.5q{num(span * 0.5)} -6.5 {num(span - 6)} -1.5" '
        'fill="none" stroke="url(#uline)" stroke-width="2.6" stroke-linecap="round" />',
        f'  <text class="sans" x="{num(x)}" y="212" font-size="44" font-weight="800" '
        f'letter-spacing="-1.3" fill="url(#titleIris)">{esc(second)}</text>',
    ]
    return defs, els


def body(lines: list[list[tuple[str, bool]]]) -> list[str]:
    out = [f'  <text class="sans" font-size="15.5" fill="{INK_2}" xml:space="preserve">']
    for i, segments in enumerate(lines):
        spans = "".join(
            (
                f'<tspan class="mono" font-size="14" fill="{IRIS_2}">{esc(text)}</tspan>'
                if mono
                else esc(text)
            )
            for text, mono in segments
        )
        out.append(f'    <tspan x="48" y="{254 + 23 * i}">{spans}</tspan>')
    out.append("  </text>")
    return out


def meta_row(labels: list[str]) -> str:
    x = 48.0
    parts = []
    for i, label in enumerate(labels):
        if i:
            parts.append(f'<circle cx="{num(x + 9)}" cy="318.3" r="2.1" fill="{AMBER}" />')
            x += 19
        parts.append(f'<text x="{num(x)}" y="322">{esc(label)}</text>')
        x += mono_width(len(label), 11.5, 1.5)
    return (
        f'  <g class="mono" font-size="11.5" letter-spacing="1.5" fill="{INK_3}">'
        f'{"".join(parts)}</g>'
    )


# Scene: chart

LOOP = 9.0
# Waypoints: position, chip label and which side of the star the chip sits. The last one is
# GHCR, which gets the tags instead of a chip.
WAYPOINTS = [
    ((664.0, 312.0), "UPSTREAM main", "below"),
    ((790.0, 254.0), "PATCH", "above"),
    ((904.0, 290.0), "IMAGE SMOKE", "below"),
    ((1000.0, 200.0), "COMPOSE STACK", "right"),
    ((1112.0, 122.0), "", ""),
]
# Share of the loop: the comet sets off, arrives, and everything fades for the next run.
DEPART, ARRIVE, FADE, GONE = 6.0, 62.0, 90.0, 96.0


def catmull(points: list[tuple[float, float]], samples: int = 24) -> list[tuple[float, float]]:
    """A Catmull-Rom curve through the points, sampled evenly per segment."""
    out = []
    pts = [points[0], *points, points[-1]]
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(samples):
            u = s / samples
            out.append(
                tuple(
                    0.5
                    * (
                        2 * p1[j]
                        + (p2[j] - p0[j]) * u
                        + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * u * u
                        + (3 * p1[j] - p0[j] - 3 * p2[j] + p3[j]) * u * u * u
                    )
                    for j in (0, 1)
                )
            )
    out.append(points[-1])
    return out


def graticule() -> str:
    """Parallels round a pole far below the card and meridians towards one far above, dotted
    and faded out before they reach the copy."""
    lines = [f'<circle cx="900" cy="1420" r="{r}" />' for r in range(1060, 1340, 56)]
    lines += [f'<path d="M{900 + k * 18} -520L{900 + k * 120} 420" />' for k in range(-5, 6)]
    return (
        '<g mask="url(#chartFade)" fill="none" stroke="#B9C2FF" stroke-opacity="0.16" '
        'stroke-width="1" stroke-dasharray="1 7" stroke-linecap="round">' + "".join(lines) + "</g>"
    )


def compass(cx: float, cy: float, r: float) -> str:
    """A small eight-point rose with north in amber."""

    def at(radius: float, a: float) -> str:
        return f"{num(cx + radius * math.sin(a))} {num(cy - radius * math.cos(a))}"

    parts = [
        f'<circle cx="{num(cx)}" cy="{num(cy)}" r="{num(r)}" fill="none" stroke="{INK_3}" '
        'stroke-opacity="0.35" />'
    ]
    for k in range(8):
        a = math.radians(45 * k)
        cardinal = k % 2 == 0
        tip = r - 2 if cardinal else r * 0.55
        side = 4.2 if cardinal else 3.0
        colour = AMBER if k == 0 else INK_3
        opacity = "0.9" if k == 0 else ("0.55" if cardinal else "0.35")
        parts.append(
            f'<path d="M{at(side, a - math.pi / 2)}L{at(tip, a)}L{at(side, a + math.pi / 2)}Z" '
            f'fill="{colour}" fill-opacity="{opacity}" />'
        )
    parts.append(
        f'<text class="mono" x="{num(cx)}" y="{num(cy - r - 6)}" text-anchor="middle" '
        f'font-size="9.5" letter-spacing="1" fill="{AMBER_2}" fill-opacity="0.8">N</text>'
    )
    return "".join(parts)


def scene_chart() -> tuple[list[str], str, str, list[tuple[float, float, float, float]]]:
    """The publish pipeline as a course on a star chart, from upstream to GHCR."""
    course = catmull([p for p, _, _ in WAYPOINTS])
    lengths = [0.0]
    for (x0, y0), (x1, y1) in zip(course, course[1:]):
        lengths.append(lengths[-1] + math.hypot(x1 - x0, y1 - y0))
    total = lengths[-1]

    def at_share(share: float) -> float:
        return DEPART + (ARRIVE - DEPART) * share

    # Waypoint stars. Each is drawn lit, which is what reduced motion shows; the animation
    # keeps it dark until the comet reaches it.
    stars, labels, css = [], [], []
    for i, ((x, y), label, side) in enumerate(WAYPOINTS):
        dest = i == len(WAYPOINTS) - 1
        nearest = min(range(len(course)), key=lambda k: math.dist(course[k], (x, y)))
        when = at_share(lengths[nearest] / total)
        size = 5.2 if dest else 3.6
        stars.append(
            f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(size)}" fill="#1B2335" '
            f'stroke="{INK_3}" stroke-opacity="0.6" />'
            f'<g class="nd{i}"><circle cx="{num(x)}" cy="{num(y)}" r="{num(size * 5)}" '
            f'fill="url(#{"destGlow" if dest else "nodeGlow"})" />'
            f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(size)}" '
            f'fill="{AMBER if dest else IRIS_2}" /></g>'
        )
        css.append(
            f".nd{i}{{animation:nd{i} {LOOP}s linear infinite}}"
            f"@keyframes nd{i}{{0%,{pct(when - 0.4)}{{opacity:0}}{pct(when + 0.6)},{pct(FADE)}"
            f"{{opacity:1}}{pct(GONE)},100%{{opacity:0}}}}"
        )
        if not label:
            continue
        width = chip_width(label)
        if side == "below":
            bx, by = x - width / 2, y + 16
        elif side == "above":
            bx, by = x - width / 2, y - 36
        else:
            bx, by = x + 14, y + 12
        labels.append(chip(bx, by, label, IRIS_2))

    # The comet: a head and a tail of smaller dots running the same keyframes a moment later.
    # At rest it is hidden; the lit course says the same thing.
    sx, sy = course[0]
    ex, ey = course[-1]
    keys = [
        "0%{transform:translate(0,0);opacity:0}"
        f"{pct(DEPART)}{{transform:translate(0,0);opacity:1}}"
    ]
    for k in range(2, len(course) - 1, 2):
        x, y = course[k]
        keys.append(
            f"{pct(at_share(lengths[k] / total))}"
            f"{{transform:translate({num(x - sx)}px,{num(y - sy)}px)}}"
        )
    end = f"transform:translate({num(ex - sx)}px,{num(ey - sy)}px)"
    keys.append(f"{pct(ARRIVE)}{{{end};opacity:1}}{pct(ARRIVE + 2)},100%{{{end};opacity:0}}")
    css.append(
        f".cm{{opacity:0;animation:cm {LOOP}s linear infinite}}@keyframes cm{{{''.join(keys)}}}"
    )
    comet = "".join(
        f'<circle class="cm" cx="{num(sx)}" cy="{num(sy)}" r="{num(3.6 - 0.35 * k)}" '
        f'fill="{IRIS_2}" fill-opacity="{0.95 - 0.11 * k:.2f}" '
        f'style="animation-delay:{0.045 * k:.3f}s" />'
        for k in range(7, -1, -1)
    )

    # The promoted tags in a row above GHCR, popping in right to left once the comet lands.
    # They're drawn visible for the rest frame.
    widths = [chip_width(label) for label in TAGS]
    tx = 1186 - sum(widths) - 6 * (len(TAGS) - 1)
    tags = []
    for k, (label, width) in enumerate(zip(TAGS, widths)):
        delay = 0.15 * (len(TAGS) - 1 - k)
        tags.append(chip(tx, ey - 60, label, AMBER_2, "tg", f"animation-delay:{delay:.2f}s"))
        tx += width + 6
    css.append(
        f".tg{{animation:tg {LOOP}s ease-out infinite backwards}}"
        f"@keyframes tg{{0%,{pct(ARRIVE)}{{opacity:0}}{pct(ARRIVE + 3)},{pct(FADE)}"
        f"{{opacity:1}}{pct(GONE)},100%{{opacity:0}}}}"
    )
    destination = (
        f'<path class="tg" d="M{num(ex)} {num(ey - 9)}V{num(ey - 38)}" stroke="{AMBER}" '
        'stroke-opacity="0.6" />'
        f'<text class="mono" x="{num(ex + 12)}" y="{num(ey + 22)}" font-size="10.5" '
        f'letter-spacing="0.6" fill="{AMBER_2}" fill-opacity="0.85">ghcr.io</text>'
    )

    course_path = "M" + "L".join(f"{num(x)} {num(y)}" for x, y in course)
    defs = [
        *gradient(
            "linearGradient",
            'id="fadeX" x1="560" y1="0" x2="720" y2="0" gradientUnits="userSpaceOnUse"',
            [("0", "#FFFFFF", "0"), ("1", "#FFFFFF", "1")],
        ),
        '    <mask id="chartFade"><rect width="1200" height="400" fill="url(#fadeX)" /></mask>',
        *gradient(
            "radialGradient",
            'id="nodeGlow" cx="0.5" cy="0.5" r="0.5"',
            [("0", IRIS, "0.55"), ("0.4", IRIS, "0.14"), ("1", IRIS, "0")],
        ),
        *gradient(
            "radialGradient",
            'id="destGlow" cx="0.5" cy="0.5" r="0.5"',
            [("0", AMBER, "0.7"), ("0.35", AMBER, "0.2"), ("1", AMBER, "0")],
        ),
        *gradient(
            "linearGradient",
            'id="courseInk" x1="664" y1="0" x2="1112" y2="0" gradientUnits="userSpaceOnUse"',
            [("0", IRIS, "0.5"), ("0.75", IRIS_2, "0.8"), ("1", AMBER, "0.9")],
        ),
        *gradient(
            "radialGradient",
            'id="aura" cx="0.5" cy="0.5" r="0.5"',
            [("0", IRIS_3, "0.32"), ("0.6", IRIS_3, "0.08"), ("1", IRIS_3, "0")],
        ),
    ]
    scene = "".join(
        [
            '<ellipse cx="900" cy="220" rx="340" ry="190" fill="url(#aura)" />',
            graticule(),
            compass(1136, 318, 30),
            f'<path d="{course_path}" fill="none" stroke="url(#courseInk)" stroke-width="1.6" '
            'stroke-dasharray="2 6" stroke-linecap="round" />',
            "".join(stars),
            comet,
            "".join(labels),
            destination,
            "".join(tags),
        ]
    )
    return defs, "".join(css), scene, [(610, 40, 1200, 380)]


# Copy

CARD = {
    "title": "odysseusai - Odysseus, prebuilt, tested before it ships",
    "desc": (
        "A star chart with the publish pipeline plotted as a course, from upstream main "
        "through the patch, image smoke test and Compose stack checks to an amber star at "
        "ghcr.io, where the image is tagged latest with upstream's version and commit pin."
    ),
    "headline": ("Odysseus, ", "prebuilt", ".", "Tested before it ships."),
    "body": [
        [("Stable and dev images built from upstream Odysseus and", False)],
        [
            ("booted with the full stack first. Then ", False),
            ("docker compose up -d", True),
            (".", False),
        ],
    ],
    "meta": ["SYNC", "PATCH", "SMOKE TEST", "PROMOTE"],
    "scene": scene_chart,
}


def build_svg(spec: dict = CARD) -> str:
    scene_defs, scene_css, scene, free = spec["scene"]()
    star_defs, star_body = starfield([COPY_BOX, *free])
    head_defs, head_els = headline(*spec["headline"])
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 400" width="960" height="320" '
        'role="img" aria-labelledby="title desc">',
        f'  <title id="title">{esc(spec["title"])}</title>',
        f'  <desc id="desc">{esc(spec["desc"])}</desc>',
        "",
        "  <defs>",
        "    <style>",
        "      /* Inter and JetBrains Mono, SIL Open Font License 1.1, subset to the glyphs used "
        "here. */",
        "      __FONT_FACES__",
        f"      {FONT_CSS}",
        f"      {TWINKLE_CSS}",
        f"      {scene_css}",
        "      @media (prefers-reduced-motion: reduce){",
        "        *{animation:none !important}",
        "      }",
        "    </style>",
        *star_defs,
        *mark_symbols(),
        *head_defs,
        *scene_defs,
        '    <clipPath id="frame"><rect x="1" y="1" width="1198" height="398" rx="20" />'
        "</clipPath>",
        "  </defs>",
        "",
        '  <g clip-path="url(#frame)">',
        *star_body,
        f"    {scene}",
        "  </g>",
        '  <rect x="0.75" y="0.75" width="1198.5" height="398.5" rx="20" fill="none" '
        'stroke="#1A2238" stroke-width="1.5" />',
        "",
        "  <!-- Left: brand and headline -->",
        *lockup(),
        "",
        *head_els,
        *body(spec["body"]),
        meta_row(spec["meta"]),
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


# Social card: the repository's social preview, 1280x640 as GitHub recommends. The same copy and
# chart as the hero, laid out for 2:1 and still: it is uploaded as a PNG, so it shows the rest
# frame, with every waypoint lit and the tags in place.

CARD_W, CARD_H = 1280, 640
CARD_COPY = "translate(16 44) scale(1.3)"
CARD_SCENE = "translate(52 96)"


def build_social_card(spec: dict = CARD) -> str:
    scene_defs, _, scene, _ = spec["scene"]()
    star_defs, star_body = starfield(
        [(60, 60, 650, 470), (640, 140, 1250, 470), (60, 500, 1220, 590)], CARD_W, CARD_H, 150
    )
    head_defs, head_els = headline(*spec["headline"])
    footer_y = 566
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CARD_W} {CARD_H}" '
        f'width="{CARD_W}" height="{CARD_H}" role="img" aria-labelledby="title desc">',
        f'  <title id="title">{esc(spec["title"])}</title>',
        f'  <desc id="desc">{esc(spec["desc"])}</desc>',
        "",
        "  <defs>",
        "    <style>",
        "      /* Inter and JetBrains Mono, SIL Open Font License 1.1, subset to the glyphs used "
        "here. */",
        "      __FONT_FACES__",
        f"      {FONT_CSS}",
        "      .cm{opacity:0}",
        "    </style>",
        *star_defs,
        *mark_symbols(),
        *head_defs,
        *scene_defs,
        "  </defs>",
        "",
        *star_body,
        f'  <g transform="{CARD_SCENE}">{scene}</g>',
        f'  <g transform="{CARD_COPY}">',
        *lockup(),
        *head_els,
        *body(spec["body"]),
        "  </g>",
        f'  <path d="M72 {footer_y - 46}H1208" stroke="#1F2A44" stroke-width="1.5" />',
        f'  <text class="mono" x="72" y="{footer_y}" font-size="21" letter-spacing="0.4" '
        f'fill="{IRIS_2}">ghcr.io/netspeedy/odysseusai</text>',
        f'  <text class="mono" x="1208" y="{footer_y}" text-anchor="end" font-size="17" '
        f'letter-spacing="0.4" fill="{INK_3}">github.com/netspeedy/odysseusai</text>',
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def write_svg(path: Path, svg: str) -> None:
    svg = svg.replace("__FONT_FACES__", font_faces(svg))
    if "http://" in svg.replace("http://www.w3.org/2000/svg", "") or "https://" in svg:
        raise SystemExit(f"external reference found in {path.name}")
    ET.fromstring(svg)  # well-formed, or this raises
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT)} ({len(svg.encode()) / 1024:.1f} KiB)")


def main() -> None:
    write_svg(OUTPUT, build_svg())
    write_svg(SOCIAL_OUTPUT, build_social_card())


if __name__ == "__main__":
    main()
