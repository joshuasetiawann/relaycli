"""One Console factory, so a colour word means a design token everywhere.

The frame renderers (`ui/frame.py`, `ui/lanes.py`, `ui/render.py`) resolve
every hue through `theme.style_for`. The rest of the product — the slash
commands, `relaycli config`, `doctor`, the plugin and MCP subcommands —
was written earlier against Rich's own colour words: `[yellow]`, `[cyan]`,
`[dim]`. Those are not the palette. `yellow` is the terminal's yellow,
which is a different hue from `warning` in dark, a *much* different one in
light, and stays coloured under `NO_COLOR` where the design says nothing
should be.

Rewriting ~140 call sites by hand would be a large diff that fixes them
once and drifts again on the next one. Rich resolves a markup tag through
`Console.get_style`, which consults the console's theme *before* parsing
the tag as a colour — so binding the old words to the new tokens on the
Console itself retires them everywhere at once, including in code not yet
written.

Compound tags (`[bold yellow]`) are looked up whole and so are listed
whole: `Style.parse` splits its own words and never reaches the theme.
"""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

from relaycli.ui import theme

# Rich colour word -> the SLATE token it stood in for. `dim` is muted, not
# a dimmed foreground: §1 reserves dim intensity for frame rules alone, and
# every `[dim]` in this codebase means "metadata", which is what muted is.
LEGACY_COLORS: dict[str, str] = {
    "yellow": "warning",
    "cyan": "accent",
    "blue": "accent",
    "green": "success",
    "red": "danger",
    "magenta": "waiting",
    "white": "text",
    "dim": "muted",
}


def slate_theme(mode: theme.ColorMode) -> Theme:
    """The legacy colour words, bound to `mode`'s palette.

    Under `no_color` every one of them resolves to no style at all, and the
    bold compounds keep only their bold — which is §04's own substitution:
    weight replaces hue, and no fact is lost with it.
    """
    styles: dict[str, str] = {}
    for word, token in LEGACY_COLORS.items():
        style = theme.style_for(mode, token) or ""
        styles[word] = style
        # `[bold cyan]` never reaches this table through `Style.parse`, so
        # each compound actually in use is bound in its own right.
        styles[f"bold {word}"] = f"bold {style}".strip()
    return Theme(styles, inherit=True)


def session_color_mode() -> theme.ColorMode:
    """The dark / light / NO_COLOR mode this session draws in.

    Guarded: this runs at import time for the module-level consoles the
    Typer subcommands share, and a config file that is missing, unreadable
    or half-written must not stop the CLI from starting. Dark is the
    documented default, so falling back to it loses a preference, never a
    session.
    """
    try:
        from relaycli.config.manager import load_app_config

        preference = load_app_config().preference("theme")
    except Exception:  # pragma: no cover - defensive; see docstring
        preference = None
    return theme.current_color_mode(preference)


def slate_console(**kwargs) -> Console:
    """A Console whose colour words are the design's, not Rich's."""
    kwargs.setdefault("theme", slate_theme(session_color_mode()))
    return Console(**kwargs)
