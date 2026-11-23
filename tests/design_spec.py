"""Read `RelayCLI Terminal UI.dc.html` as a character grid.

The design source is a flexbox mock of a monospace terminal: every screen is
a `width:120ch` (or 80ch / 58ch) box of rows, and every row is a line of
`<span>`s that are either a fixed `width:Nch` field, a `flex:1` spacer, or a
run of literal text. That is a character grid described in CSS, so it can be
read back out as one — which is the only way to check the product against it
without a human eyeballing two screenshots.

`screens()` returns one `Screen` per mock, each row carrying both the exact
characters and the colour of every one of them. tests/test_design_parity.py
renders the same screens through the product and asserts equality.

Kept in tests/ rather than in the package: this parses the design document,
which ships to nobody, and the product must never depend on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "RelayCLI Terminal UI.dc.html"

# The spinner is a live component in the design doc (`{{ spin }}` bound to a
# 90ms interval over 10 braille frames). Every mock that shows a running
# agent renders whatever frame the clock is on, so parity pins frame 0.
SPINNER_FRAME = "⠋"


def _styles(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.split(";"):
        key, sep, value = part.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


@dataclass
class _Node:
    tag: str
    style: dict[str, str]
    children: list = field(default_factory=list)   # _Node | str
    parent: "_Node | None" = None


class _Tree(HTMLParser):
    """Minimal div/span tree. Everything else in the document is chrome."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self._stack = [self.root]
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1
        if tag not in ("div", "span"):
            return
        style = _styles(dict(attrs).get("style") or "")
        node = _Node(tag, style, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        self._stack.append(node)

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip = max(self._skip - 1, 0)
        if tag in ("div", "span") and len(self._stack) > 1:
            self._stack.pop()

    def handle_data(self, data):
        if self._skip:
            return
        # Runs of spaces inside a `white-space:pre` row are content — the
        # design spaces its columns with them. Only the newline+indent
        # between block elements is layout, and that is the only whitespace
        # in this document that contains a newline.
        if "\n" in data and not data.strip():
            return
        self._stack[-1].children.append(data)


@dataclass(frozen=True)
class Cell:
    """One character of a mock, and the colour the design gives it."""

    char: str
    color: str | None       # "#5FA8DC", or None where the design sets none
    bold: bool = False


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]

    @property
    def text(self) -> str:
        return "".join(cell.char for cell in self.cells)

    def colors(self) -> tuple[str | None, ...]:
        return tuple(cell.color for cell in self.cells)


@dataclass(frozen=True)
class Screen:
    label: str            # the caption above the mock, or the section title
    section: str          # "03", "04", …
    width: int            # the mock's own `width:Nch`
    rows: tuple[Row, ...]

    @property
    def text(self) -> str:
        return "\n".join(row.text for row in self.rows)


def _inherited(node: _Node, prop: str) -> str | None:
    while node is not None:
        if prop in node.style:
            return node.style[prop]
        node = node.parent
    return None


def _segments(node: _Node, color: str | None, bold: bool) -> list[tuple[str, str | None, bool]]:
    """Flatten a span's own text and nested spans into coloured runs."""
    color = node.style.get("color", color)
    bold = bold or node.style.get("font-weight") == "700"
    out: list[tuple[str, str | None, bool]] = []
    for child in node.children:
        if isinstance(child, str):
            out.append((child.replace("{{ spin }}", SPINNER_FRAME), color, bold))
        else:
            out.extend(_segments(child, color, bold))
    return out


def _fit(segments: list[tuple[str, str | None, bool]], width: int,
         *, right: bool) -> list[Cell]:
    """Clip or pad a fixed-width field, honouring `text-align:right`.

    `overflow:hidden` on a `white-space:pre` flex item clips at the box edge,
    which is exactly the product's own "clip, never wrap" rule (§6).
    """
    cells = [Cell(ch, color, bold) for text, color, bold in segments for ch in text]
    if len(cells) >= width:
        return cells[:width] if not right else cells[len(cells) - width:]
    pad = [Cell(" ", None)] * (width - len(cells))
    return pad + cells if right else cells + pad


def _row(node: _Node, content_width: int) -> Row:
    """One `display:flex;white-space:pre` div, laid out to `content_width`.

    The returned row carries §4's left gutter column, because every row the
    product renders carries it too — that is what makes the two directly
    comparable. The right-hand gutter is trailing space and is not emitted.
    """
    row_color = node.style.get("color")
    row_bold = node.style.get("font-weight") == "700"

    items: list[tuple[str, object]] = []          # ("fixed"|"flex"|"text", payload)
    fixed = 0
    for child in node.children:
        if isinstance(child, str):
            segs = [(child.replace("{{ spin }}", SPINNER_FRAME), row_color, row_bold)]
            items.append(("text", segs))
            fixed += sum(len(t) for t, _, _ in segs)
            continue
        segs = _segments(child, row_color, row_bold)
        match = re.match(r"(\d+)ch", child.style.get("width", ""))
        if match:
            span = int(match.group(1))
            right = child.style.get("text-align") == "right"
            items.append(("fixed", (segs, span, right)))
            fixed += span
        elif child.style.get("flex", "").startswith("1"):
            items.append(("flex", segs))
        else:
            items.append(("text", segs))
            fixed += sum(len(t) for t, _, _ in segs)

    flex_count = sum(1 for kind, _ in items if kind == "flex")
    slack = max(content_width - fixed, 0)
    cells: list[Cell] = [Cell(" ", None)]
    for index, (kind, payload) in enumerate(items):
        if kind == "fixed":
            segs, span, right = payload            # type: ignore[misc]
            cells.extend(_fit(segs, span, right=right))
        elif kind == "flex":
            # Distribute the remainder; the last flex item takes the rounding.
            share = slack // flex_count
            if index == max(i for i, (k, _) in enumerate(items) if k == "flex"):
                share = slack - share * (flex_count - 1)
            cells.extend(_fit(payload, share, right=False))   # type: ignore[arg-type]
        else:
            cells.extend(Cell(ch, color, bold)
                         for text, color, bold in payload for ch in text)  # type: ignore[misc]
    return Row(tuple(cells))


_SECTION = re.compile(r"^\d\d$")


def screens() -> list[Screen]:
    """Every mock in the design document, in document order."""
    tree = _Tree()
    tree.feed(SOURCE.read_text(encoding="utf-8"))

    found: list[Screen] = []
    section = "00"

    def walk(node: _Node, caption: str) -> None:
        nonlocal section
        rows = [child for child in node.children
                if isinstance(child, _Node) and "white-space" in child.style]
        match = re.match(r"(\d+)ch", node.style.get("width", ""))
        if rows and match:
            width = int(match.group(1))
            # `padding:0 1ch` on each row is §4's gutter: 1 col each side.
            content = width - 2
            # The mock box is `overflow:hidden`, so a row whose natural-width
            # spans run past the edge is clipped there — the characters past
            # the box are not part of the screen, and one row in §09 has
            # several of them.
            found.append(Screen(caption, section, width,
                                tuple(Row(_row(row, content).cells[:width - 1])
                                      for row in rows)))
            return
        last_caption = caption
        for child in node.children:
            if isinstance(child, str):
                if _SECTION.match(child.strip()):
                    section = child.strip()
                elif child.strip():
                    last_caption = re.sub(r"\s+", " ", child.strip())
            else:
                walk(child, last_caption)

    walk(tree.root, "")
    return found


if __name__ == "__main__":  # pragma: no cover - inspection aid
    for screen in screens():
        print(f"\n=== §{screen.section} {screen.width}ch — {screen.label} "
              f"({len(screen.rows)} rows)")
        for row in screen.rows:
            print(f"|{row.text}|")
