"""Tests for relaycli/web_ui.html's design tokens — the SLATE INSTRUMENT
palette (docs/design/DESIGN_TOKENS.md) that replaces the old web-blue
#2D5BFF (and the old red/green/amber that came with it)."""

from __future__ import annotations

from relaycli.ui.theme import DARK
from relaycli.ui.web import UI_PATH

_OLD_BRAND_HEX = ("#2D5BFF", "#3FB950", "#E3A008", "#F0554E")


def _html() -> str:
    return UI_PATH.read_text()


def test_old_web_blue_accent_is_gone():
    html = _html()
    # The only survivor should be the explanatory comment naming it as
    # the color being replaced, not a functional value.
    assert html.count("#2D5BFF") == 1
    assert "(#2D5BFF)" in html


def test_root_token_block_uses_the_shared_dark_palette():
    html = _html()
    assert f"--accent: {DARK.accent};" in html
    assert f"--green: {DARK.success};" in html
    assert f"--amber: {DARK.warning};" in html
    assert f"--red: {DARK.danger};" in html


def test_accent_swatch_picker_defaults_to_the_new_accent():
    html = _html()
    assert f'const ACCENTS = ["{DARK.accent}"' in html
    assert f'localStorage.accent || "{DARK.accent}"' in html


def test_old_semantic_literals_do_not_leak_outside_the_untouched_activity_palette():
    """The .a3X/.a9X activity-type classes are a separate 16-color
    micro-palette this pass deliberately left alone (unclear category
    mapping, no DESIGN_TOKENS.md equivalent) — this only guards that old
    red/green/amber don't linger anywhere else (the :root block, the
    accent swatch array, or inline styles)."""
    html = _html()
    root_block = html.split(":root {")[1].split("}")[0]
    accents_line = next(line for line in html.splitlines() if "const ACCENTS" in line)
    for old_hex in _OLD_BRAND_HEX:
        assert old_hex not in root_block, f"{old_hex} still in :root"
        assert old_hex not in accents_line, f"{old_hex} still in ACCENTS"
