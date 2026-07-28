"""Tests for the desktop web console's design tokens — the SLATE
INSTRUMENT palette (docs/design/DESIGN_TOKENS.md) that replaces the old
web-blue #2D5BFF (and the old red/green/amber that came with it).

web_ui.html/styles.css/app.js (Stage 5c split) are three separate files
on disk now; these tests read whichever one actually holds the content
in question rather than assuming everything is still in web_ui.html."""

from __future__ import annotations

from relaycli.ui.theme import DARK, LIGHT
from relaycli.ui.web import APP_JS_PATH, STYLES_PATH, UI_PATH

_OLD_BRAND_HEX = ("#2D5BFF", "#3FB950", "#E3A008", "#F0554E")


def _html() -> str:
    return UI_PATH.read_text()


def _css() -> str:
    return STYLES_PATH.read_text()


def _js() -> str:
    return APP_JS_PATH.read_text()


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
