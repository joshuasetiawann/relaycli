"""Tests for the desktop web console's design tokens — the SLATE
INSTRUMENT palette (docs/design/DESIGN_TOKENS.md) that replaces the old
web-blue #2D5BFF (and the old red/green/amber that came with it).

web_ui.html/styles.css/app.js (Stage 5c split) are three separate files
on disk now; these tests read whichever one actually holds the content
in question rather than assuming everything is still in web_ui.html."""

from __future__ import annotations

import re

from relaycli.ui.theme import DARK, LIGHT
from relaycli.ui.web import APP_JS_PATH, STYLES_PATH, UI_PATH

_OLD_BRAND_HEX = ("#2D5BFF", "#3FB950", "#E3A008", "#F0554E")

# The .a3X/.a9X classes are literal ANSI SGR colors (a terminal's own 16-color
# palette), not chrome — they are supposed to be fixed hex. Pure black/white
# are shading operands in color-mix()/box-shadow, not surface colors.
_ANSI_CLASS_RE = re.compile(r"\.a(3[0-7]|9[0-7])\s*\{")
_SHADING_LITERALS = ("#000", "#fff")


def _token_blocks() -> tuple[dict[str, str], dict[str, str]]:
    """The dark and light custom-property tables, as {name: hex}."""
    css = _css()
    dark_block = css.split(":root {")[1].split("}")[0]
    light_block = css.split(':root[data-theme="light"] {')[1].split("}")[0]
    pattern = r"(--[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})"
    return dict(re.findall(pattern, dark_block)), dict(re.findall(pattern, light_block))


def _relative_luminance(hex_color: str) -> float:
    channels = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    lums = sorted((_relative_luminance(fg), _relative_luminance(bg)))
    return (lums[1] + 0.05) / (lums[0] + 0.05)


def _html() -> str:
    return UI_PATH.read_text(encoding="utf-8")


def _css() -> str:
    return STYLES_PATH.read_text(encoding="utf-8")


def _js() -> str:
    return APP_JS_PATH.read_text(encoding="utf-8")


def test_old_web_blue_accent_is_gone():
    css = _css()
    # The only survivor should be the explanatory comment naming it as
    # the color being replaced, not a functional value.
    assert css.count("#2D5BFF") == 1
    assert "(#2D5BFF)" in css
    assert "#2D5BFF" not in _js()
    assert "#2D5BFF" not in _html()


def test_root_token_block_uses_the_shared_dark_palette():
    css = _css()
    assert f"--accent: {DARK.accent};" in css
    assert f"--green: {DARK.success};" in css
    assert f"--amber: {DARK.warning};" in css
    assert f"--red: {DARK.danger};" in css


def test_accent_swatch_picker_defaults_to_the_new_accent():
    js = _js()
    assert f'const ACCENTS = ["{DARK.accent}"' in js
    assert f'localStorage.accent || "{DARK.accent}"' in js


def test_old_semantic_literals_do_not_leak_outside_the_untouched_activity_palette():
    """The .a3X/.a9X activity-type classes are a separate 16-color
    micro-palette this pass deliberately left alone (unclear category
    mapping, no DESIGN_TOKENS.md equivalent) — this only guards that old
    red/green/amber don't linger anywhere else (the :root block, the
    accent swatch array, or inline styles)."""
    css = _css()
    root_block = css.split(":root {")[1].split("}")[0]
    accents_line = next(line for line in _js().splitlines() if "const ACCENTS" in line)
    for old_hex in _OLD_BRAND_HEX:
        assert old_hex not in root_block, f"{old_hex} still in :root"
        assert old_hex not in accents_line, f"{old_hex} still in ACCENTS"


# --- light theme -------------------------------------------------------------
def test_light_theme_block_uses_the_shared_light_palette():
    css = _css()
    light_block = css.split(':root[data-theme="light"] {')[1].split("}")[0]
    assert f"--accent: {LIGHT.accent};" in light_block
    assert f"--green: {LIGHT.success};" in light_block
    assert f"--amber: {LIGHT.warning};" in light_block
    assert f"--red: {LIGHT.danger};" in light_block


def test_theme_toggle_control_exists_and_is_wired():
    assert 'id="themeTog"' in _html()
    assert "localStorage.theme" in _js()


def test_theme_init_runs_before_any_render_call_reads_it():
    """Regression: renderSettings() (called eagerly at load, not lazily on
    first open) reads document.documentElement.dataset.theme to set the
    toggle's initial position. Putting the theme-init line at the end of
    the script (matching where the pre-existing accent-restore line
    sits) meant renderSettings() ran with dataset.theme still unset. The
    init must appear before the *call* to renderSettings(), not just
    before its definition — a naive "theme init exists" check wouldn't
    catch this ordering bug."""
    js = _js()
    init_pos = js.index("document.documentElement.dataset.theme =")
    render_call_pos = js.index("renderSettings();")
    assert init_pos < render_call_pos


# --- every chrome color is a token, so it flips with the theme ----------------
def test_no_hardcoded_chrome_hex_outside_the_token_tables():
    """Leftovers from the pre-SLATE-INSTRUMENT palette were hardcoded, so
    they could not flip: `.menu { background: #15161B }` stayed near-black
    on a light page, and `.bubble { color: #C9CAD0 }` stayed near-white.
    Every chrome color must now be a var(--token)."""
    css = _css()
    lines = css.splitlines()
    dark_block_end = css[: css.index(':root[data-theme="light"]')].count("\n")
    light_block_end = css[: css.index("* { box-sizing")].count("\n")

    offenders = []
    for lineno, line in enumerate(lines, 1):
        if lineno <= light_block_end:      # the two :root token tables
            continue
        if _ANSI_CLASS_RE.search(line) or "#2D5BFF" in line:
            continue                        # literal ANSI palette / retired-accent comment
        for match in re.finditer(r"#[0-9A-Fa-f]{3,8}\b", line):
            if match.group(0).lower() not in _SHADING_LITERALS:
                offenders.append(f"L{lineno}: {match.group(0)} in {line.strip()[:70]}")
    assert not offenders, "hardcoded chrome hex found:\n" + "\n".join(offenders)
    assert dark_block_end < light_block_end  # sanity: blocks located in order


def test_every_color_token_used_is_defined_in_both_themes():
    """A var(--x) defined only in :root silently keeps its dark value in
    light mode — the same class of bug as a hardcoded hex, just harder to
    spot."""
    dark, light = _token_blocks()
    structural = {"--mono", "--sans", "--term-h", "--lane-shift", "--x"}
    used = set(re.findall(r"var\((--[a-z0-9-]+)", _css())) - structural
    assert used, "no tokens found — the regex or the file layout changed"
    assert not (used - dark.keys()), f"used but undefined in dark: {sorted(used - dark.keys())}"
    assert not (used - light.keys()), f"used but undefined in light: {sorted(used - light.keys())}"


def test_body_text_tokens_stay_readable_in_both_themes():
    """Regression with real numbers: before tokenizing, the mode switcher,
    chat bubbles and terminal body used hardcoded near-white text that in
    light mode landed at 1.3-1.9:1 against its own background — far under
    WCAG AA's 4.5:1 for body text. --t2 on the recessed surfaces those
    rules actually use must clear the bar in BOTH themes."""
    dark, light = _token_blocks()
    for theme_name, table in (("dark", dark), ("light", light)):
        ratio = _contrast(table["--t2"], table["--well"])
        assert ratio >= 4.5, f"--t2 on --well is only {ratio:.2f}:1 in {theme_name}"


def test_surface_tokens_actually_invert_between_themes():
    """--card/--panel/--raise/--well are the surfaces the tokenized chrome
    now points at; if any failed to invert, a 'fixed' rule would still
    render a dark slab on a light page."""
    dark, light = _token_blocks()
    for token in ("--card", "--panel", "--raise", "--well"):
        dark_lum = _relative_luminance(dark[token])
        light_lum = _relative_luminance(light[token])
        assert dark_lum < 0.1, f"{token} is not dark in the dark theme ({dark_lum:.3f})"
        assert light_lum > 0.5, f"{token} is not light in the light theme ({light_lum:.3f})"


# --- Stage 5c file split -----------------------------------------------------
def test_the_three_files_exist_on_disk():
    assert UI_PATH.is_file()
    assert STYLES_PATH.is_file()
    assert APP_JS_PATH.is_file()


def test_html_has_no_leftover_inline_style_or_script():
    html = _html()
    assert "<style>" not in html
    assert "<script>" not in html
    assert '<link rel="stylesheet" href="/styles.css">' in html
    assert '<script src="/app.js"></script>' in html


def test_css_and_js_files_contain_no_stray_html_tags():
    for content in (_css(), _js()):
        assert "<style>" not in content
        assert "<script>" not in content
        assert "<html" not in content
