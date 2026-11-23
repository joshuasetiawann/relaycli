"""Deterministic creative direction and minimum quality checks for websites."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from relaycli.core.context import ProjectContext
from relaycli.intent import frontend_destination


_GENERIC_COPY = (
    "welcome to our website",
    "this is a landing page",
    "get started",
    "your company",
    "our website",
    "lorem ipsum",
)

_COMPOSITIONS = (
    "an editorial broken grid with oversized type, offset media, and alternating narrative bands",
    "a cinematic full-bleed opening with layered subject imagery and scroll-led story chapters",
    "an asymmetric modular canvas with mixed spans, strong negative space, and one anchored proof rail",
    "a poster-led composition with a dominant statement, cropped visual forms, and staggered content reveals",
)
_CORE_FRONTEND_FILES = ("index.html", "styles.css", "app.js")


def frontend_bundle_root(
    project: ProjectContext, request: str, *, destination: str | None = None
) -> tuple[Path, str]:
    """Resolve an explicit destination or the one obvious generated bundle."""

    folder = destination or frontend_destination(request)
    if folder is not None:
        root = project.resolve(folder)
        return root, folder
    if all((project.root / name).is_file() for name in _CORE_FRONTEND_FILES):
        return project.root, project.root.name or "."
    candidates = [
        child for child in project.root.iterdir()
        if child.is_dir() and all((child / name).is_file() for name in _CORE_FRONTEND_FILES)
    ]
    if len(candidates) == 1:
        return candidates[0], project.relative(candidates[0])
    return project.root, project.root.name or "."


def frontend_design_brief(request: str) -> str:
    """Return a compact, repeatable-but-varied direction derived from the request."""

    lowered = " ".join((request or "").lower().split())
    index = hashlib.sha256(lowered.encode("utf-8")).digest()[0] % len(_COMPOSITIONS)
    composition = _COMPOSITIONS[index]

    if any(term in lowered for term in ("iklan", "campaign", "kampanye", "advert", "landing page")):
        purpose = (
            "This is a conversion/brand campaign, not a storefront: lead with the subject, "
            "then build desire, evidence, and one final action. Do not add a product catalog "
            "unless the user explicitly asks for shopping."
        )
    elif any(term in lowered for term in ("toko", "store", "shop", "commerce")):
        purpose = (
            "This is commerce: make discovery and purchase clear, but use an editorial collection "
            "story instead of a generic hero followed by equal cards."
        )
    elif any(term in lowered for term in ("dashboard", "admin", "platform", "aplikasi", "app ")):
        purpose = (
            "This is a product interface: prioritize the real workflow, useful states, and compact "
            "navigation instead of marketing-page decoration."
        )
    else:
        purpose = (
            "Infer a believable brand and audience from the subject. Build a specific narrative, "
            "not a generic company template."
        )

    return (
        "FRONTEND CREATIVE DIRECTION\n"
        f"- Purpose: {purpose}\n"
        f"- Composition: {composition}. Do not reuse a universal hero + card grid layout.\n"
        "- Art direction: derive one restrained accent and visual motif from the subject; avoid "
        "default green, purple AI gradients, Arial, and Inter-everywhere.\n"
        "- Content: write subject-specific draft copy. Never use placeholder copy such as "
        "'Welcome to Our Website', 'Get Started', or lorem ipsum.\n"
        "- First-draft contract: write one complete, deliberate pass, not a toy placeholder. Use at "
        "least four content sections with different layout rhythms, a subject-specific visual stage, "
        "complete metadata, and a real footer.\n"
        "- CSS contract: use variables, grid/flex intentionally, fluid clamp() type, a mobile media "
        "query, visible focus/hover states, transform/opacity motion, and reduced-motion support. "
        "Style every major section in the actual HTML, including hero, story, membership, contact, "
        "and footer where present; do not stop after styling only one section.\n"
        "- Local-model budget: write exactly one core file per tool turn—complete HTML first, then "
        "complete CSS, then JavaScript. Never squeeze three abbreviated files into one response.\n"
        "- Interaction contract: wire every visible control, including mobile navigation and forms; "
        "provide useful inline state changes and honest demo feedback. Never use inline onclick or "
        "window.alert. After index.html, styles.css, and app.js are complete, stop and report."
    )


def frontend_quality_issues(
    project: ProjectContext, request: str, *, destination: str | None = None
) -> list[str]:
    """Find gross frontend failures that a small local reviewer often approves."""

    try:
        root, folder_label = frontend_bundle_root(
            project, request, destination=destination
        )
    except ValueError:
        folder = destination or frontend_destination(request)
        return [f"The requested destination folder {folder!r} is invalid."]

    index = root / "index.html"
    if not index.is_file():
        return [f"{folder_label}/index.html is missing."]

    html = index.read_text(encoding="utf-8", errors="replace")
    lowered = html.lower()
    css = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.glob("*.css")
        if path.is_file()
    )
    js = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.glob("*.js")
        if path.is_file()
    )
    style_text = (html + "\n" + css).lower()
    issues: list[str] = []

    found_generic = [phrase for phrase in _GENERIC_COPY if phrase in lowered]
    if found_generic:
        issues.append(
            "Replace generic placeholder copy with a believable subject-specific brand and message "
            f"(found: {', '.join(found_generic)})."
        )
    if len(re.findall(r"<section\b", lowered)) < 4 or "<footer" not in lowered:
        issues.append(
            "The page is structurally thin; create at least four distinct subject-specific sections "
            "plus a real footer instead of one empty hero."
        )
    if "name=\"description\"" not in lowered and "name='description'" not in lowered:
        issues.append("Add a specific meta description and complete document metadata.")
    if re.search(r"font(?:-family)?\s*:[^;{}]*\barial\b", style_text, re.I):
        issues.append("Replace the browser-era Arial treatment with a deliberate type system.")
    if any(color in style_text for color in ("#4caf50", "#45a049")):
        issues.append(
            "Replace the default tutorial-green palette with an accent derived from the subject."
        )
    if re.search(
        r"(?:^|[},])\s*section\s*\{(?=[^}]*background)(?=[^}]*border-radius)[^}]*\}",
        css,
        re.DOTALL | re.I,
    ):
        issues.append(
            "Do not render every section as the same rounded card; create distinct layout rhythms "
            "and reserve containers for content that needs them."
        )
    if len(style_text) < 1600 or "@media" not in style_text:
        issues.append("The styling is too thin; add a deliberate responsive layout for mobile and wide screens.")
    section_classes = set(re.findall(r'<section\b[^>]*class=["\']([^"\']+)', html, re.I))
    matched_sections = sum(
        any(re.search(rf"\.{re.escape(name)}(?![\w-])", css) for name in classes.split())
        for classes in section_classes
    )
    if len(section_classes) >= 4 and matched_sections < 3:
        issues.append("Style at least three distinct content sections by their actual HTML classes; generic section rules leave the page visually unfinished.")
    if not any(token in style_text for token in ("transition", "animation", "@keyframes")):
        issues.append("Add restrained hover/entry motion using transform or opacity.")
    if "prefers-reduced-motion" not in style_text:
        issues.append("Add a prefers-reduced-motion fallback for animated or transitioned elements.")
    interaction_text = (html + "\n" + js).lower()
    if not js.strip() or not re.search(r"\b(?:addEventListener|onchange|onsubmit)\s*(?:\(|=)", js):
        issues.append("Add a working JavaScript interaction with an inline state change; an empty or inert app.js is insufficient.")
    if "nav-toggle" in html and "nav-toggle" not in js:
        issues.append("Wire the mobile navigation toggle to JavaScript and update its aria-expanded state.")
    if not re.search(r"<script\b[^>]*\bsrc\s*=", html, re.I):
        issues.append("Load the JavaScript file from index.html so the interaction can run.")
    if "alert(" in interaction_text or "onclick=" in interaction_text:
        issues.append(
            "Replace inline onclick/window.alert interaction with an event listener and a useful inline state."
        )
    pinned = re.search(r"footer\s*\{[^}]*position\s*:\s*(fixed|absolute)", css, re.DOTALL | re.I)
    if pinned:
        # Run 5 spent two cycles on the vaguer wording, then patched index.html
        # with style="position: static" while styles.css stayed pinned.
        issues.append(
            f"styles.css pins the footer over page content (footer {{ position: {pinned.group(1)} }}). "
            "Delete that position line and its bottom/left offsets in styles.css itself; an inline "
            "style= override in index.html is not a fix."
        )
    visual_tokens = ("<img", "<picture", "<video", "<canvas", "<svg", "background-image", "radial-gradient", "linear-gradient")
    if not any(token in style_text for token in visual_tokens):
        # Run 4 answered the vaguer wording with an empty <div class="visual-stage">.
        issues.append(
            "Draw a subject-specific visual instead of a text-only page: an inline <svg> (for "
            "example court lines) or a CSS background-image or gradient on the hero. An empty "
            "container named after a visual does not count."
        )
    if css.strip() and not re.search(r"<link\b[^>]*\.css\b", html, re.I):
        issues.append("Link styles.css from index.html with <link rel=\"stylesheet\">; the CSS never loads.")
    if not re.search(r":focus(?:-visible)?\b", css):
        issues.append("Add a visible keyboard focus style (:focus-visible) for links, buttons, and fields.")
    if re.search(r"""(?:<img\b[^>]*\bsrc|url\()\s*=?\s*['"(]?\s*(?:https?:)?//""", html + css, re.I):
        issues.append("Remove remote images; draw the visual with local CSS/SVG instead.")
    # Observed twice: a revision pasted a second stylesheet into index.html, which
    # drifts from styles.css and hides fixes from the checks above.
    inline_css = "".join(re.findall(r"<style\b[^>]*>(.*?)</style>", html, re.I | re.S))
    if css.strip() and len(inline_css.strip()) > 300:
        issues.append(
            "index.html has its own <style> block next to styles.css; move those rules into "
            "styles.css and delete the block so the page has one stylesheet."
        )
    # Observed in a browser (run 4): `footer { width: 100%; padding: 2rem }` with
    # no border-box anywhere made the page 49px wider than a phone screen.
    all_css = re.sub(r"/\*.*?\*/", "", css + "\n" + inline_css, flags=re.S)
    if not re.search(r"box-sizing\s*:\s*border-box", all_css, re.I):
        too_wide = sorted({
            selector.strip()[:40]
            for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", all_css)
            if re.search(r"(?<![\w-])width\s*:\s*100(?:%|vw)", body, re.I)
            and _has_side_padding(body)
        })
        if too_wide:
            issues.append(
                f"{', '.join(too_wide[:3])} set width: 100% plus side padding without box-sizing: "
                "border-box, so they are wider than the screen (sideways scroll, or clipped edges "
                "when fixed). Add "
                "*, *::before, *::after { box-sizing: border-box; } to styles.css."
            )
    placeholders = sorted(set(re.findall(
        r"""(?:content|href)\s*=\s*["']((?:https?://)?(?:www\.)?example\.(?:com|org)[^"']*|your[-_ ][^"']*)""",
        html, re.I,
    )))
    if placeholders:
        issues.append(
            "Remove template placeholder metadata and links "
            f"({', '.join(repr(p[:40]) for p in placeholders[:3])}); leave out any tag you cannot "
            "fill with a real value."
        )
    # Observed: favicons and a web manifest linked but never written, so every load 404s.
    missing_files = sorted({
        ref for ref in re.findall(r"""\b(?:href|src)\s*=\s*["']([^"'#?]+)""", html, re.I)
        if not re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", ref, re.I)
        and ref.strip("/") and not (root / ref.strip("/")).exists()
    })
    if missing_files:
        issues.append(
            "index.html references local files that do not exist, so the browser gets 404s: "
            f"{', '.join(missing_files[:6])}. Create them or remove the references."
        )
    issues.extend(_broken_control_issues(html, js))
    issues.extend(_js_issues(root, html, js))
    return issues


def _has_side_padding(body: str) -> bool:
    """True when a declaration block pads the left or right side."""
    for prop, value in re.findall(r"(?<![\w-])(padding(?:-left|-right|-inline)?)\s*:\s*([^;}]+)", body, re.I):
        parts = value.replace("!important", "").split()
        side = parts if prop.lower() != "padding" else parts[1:2] or parts[:1]
        if any(re.sub(r"[a-z%]+$", "", p.lower()) not in ("0", "0.0", "") for p in side):
            return True
    return False


def _js_issues(root: Path, html: str, js: str) -> list[str]:
    """Catch scripts that cannot run: syntax errors, missing ids, static aria-expanded."""

    issues: list[str] = []
    node = shutil.which("node")
    for path in sorted(root.glob("*.js")) if node else ():
        try:
            check = subprocess.run(
                [node, "--check", str(path)], capture_output=True, text=True, timeout=20
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if check.returncode:
            detail = next((l for l in check.stderr.splitlines() if "Error" in l), "syntax error")
            issues.append(
                f"{path.name} does not parse ({detail.strip()}), so none of its handlers run. "
                "Rewrite it completely; it is probably truncated."
            )
    ids = set(re.findall(r"""\bid\s*=\s*["']([^"']+)""", html, re.I))
    wanted = re.findall(r"""getElementById\(\s*['"]([\w-]+)['"]""", js)
    wanted += re.findall(r"""querySelector(?:All)?\(\s*['"]#([\w-]+)['"]""", js)
    missing = sorted(set(wanted) - ids)
    if missing:
        issues.append(
            "app.js looks up ids that index.html does not have, so those lookups return null and "
            f"the handlers crash: {', '.join('#' + m for m in missing[:6])}."
        )
    if "aria-expanded" in html.lower() and "aria-expanded" not in js:
        issues.append("app.js never updates aria-expanded; toggle it when the mobile menu opens and closes.")
    # Observed in a browser: the toggle flips aria-expanded, but the CSS opens
    # `#menu[aria-expanded="true"]` — an attribute the menu itself never has.
    styles = html + "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in root.glob("*.css"))
    dead_states = sorted({
        f"{kind}{name}"
        for kind, name in re.findall(r"([#.])([\w-]+)\[aria-expanded", styles)
        for tag in re.findall(
            rf"""<[a-z][^>]*\b{'id' if kind == '#' else 'class'}\s*=\s*["'](?:[^"']*\s)?{re.escape(name)}(?:\s[^"']*)?["'][^>]*>""",
            html, re.I,
        )
        if "aria-expanded" not in tag.lower()
    })
    if dead_states:
        issues.append(
            f"The CSS opens {', '.join(dead_states[:3])} via its own [aria-expanded], but aria-expanded "
            "lives on the toggle button, so the menu never appears. Toggle a class or the hidden "
            "attribute on the menu in app.js and style that instead (or use "
            "[aria-expanded=\"true\"] + menu)."
        )
    # Observed in a browser: `.session` <div>s matched by event.target.classList
    # — a click on the <h3> inside misses, and a keyboard never reaches them.
    fragile = sorted({
        "".join(names)
        for names in re.findall(
            r"""\.target\.(?:classList\.contains\(\s*['"]([\w-]+)|matches\(\s*['"]\.([\w-]+))""", js
        )
        if re.search(
            r"""<(?:div|li|article|section|span|p|tr|td)\b[^>]*\bclass\s*=\s*["'](?:[^"']*\s)?"""
            + re.escape("".join(names)) + r"""(?:\s[^"']*)?["']""",
            html, re.I,
        )
    })
    if fragile:
        issues.append(
            f"app.js picks {', '.join('.' + n for n in fragile[:3])} by checking event.target itself, "
            "but those items are non-interactive elements: clicks on their inner text miss the check "
            "and keyboard users cannot reach them. Make each item a <button> and match it with "
            "event.target.closest()."
        )
    sends = re.search(r"\bfetch\s*\(|XMLHttpRequest|sendBeacon", js) or re.search(
        r"<form\b[^>]*\baction\s*=", html, re.I
    )
    # Whole strings, not the matched fragment: run 4 was told only 'You have'
    # and fixed a different message than the one still lying.
    claims = [] if sends else sorted({
        lit[1][:70]
        for lit in re.findall(r"""(['"`])((?:\\.|(?!\1).)*)\1""", js)
        if not _HONEST.search(lit[1]) and _FALSE_SUCCESS.search(lit[1])
    })
    if claims:
        issues.append(
            "Nothing is sent anywhere, yet these app.js messages claim it was: "
            f"{', '.join(repr(c) for c in claims[:4])}. Rewrite each one to say it is a demo, "
            "for example 'Thursday drills selected (demo only, nothing is booked)'."
        )
    return issues


# Success claims that are false when the page has no network path.
_FALSE_SUCCESS = re.compile(
    r"\b(?:has|have) been (?:sent|submitted|received|booked|confirmed|processed)\b"
    r"|\bthank(?:s| you) for (?:joining|subscribing|signing up|booking|your (?:message|booking|order|payment|enquiry|inquiry))\b"
    r"|\bsuccessfully (?:sent|submitted|booked|joined|subscribed|registered)\b"
    r"|\byou(?:'re| are| have)(?: now)? (?:booked|registered|subscribed|joined|a member)\b"
    r"|\bwe(?:'ll| will) (?:be in touch|get back to you|contact you|reply)\b",
    re.I,
)
_HONEST = re.compile(r"\b(?:no|not|nothing|never|demo|preview|without|wasn't|hasn't|isn't)\b", re.I)


class _ControlScan(HTMLParser):
    """Collect ids, same-page anchors, forms, and buttons outside forms."""

    def __init__(self) -> None:
        super().__init__()
        self.targets: set[str] = set()
        self.anchors: list[str] = []
        self.forms: list[dict[str, str | None]] = []
        self.loose_buttons: list[dict[str, str | None]] = []
        self._form_depth = 0
        self._button: dict[str, str | None] | None = None
        self._button_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if attr.get("id"):
            self.targets.add(attr["id"] or "")
        if tag == "a" and attr.get("name"):
            self.targets.add(attr["name"] or "")
        href = attr.get("href") or ""
        if tag == "a" and href.startswith("#"):
            self.anchors.append(href[1:])
        if tag == "form":
            self._form_depth += 1
            self.forms.append(attr)
        elif tag == "button" and not self._form_depth and "form" not in attr:
            self._button, self._button_text = attr, []

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._form_depth:
            self._form_depth -= 1
        elif tag == "button" and self._button is not None:
            self._button["_text"] = " ".join("".join(self._button_text).split())
            self.loose_buttons.append(self._button)
            self._button = None

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button_text.append(data)


def _js_mentions(js: str, token: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", js))


def _broken_control_issues(html: str, js: str) -> list[str]:
    """Flag concrete dead controls: missing anchor targets, unhandled forms, unwired buttons."""

    scan = _ControlScan()
    scan.feed(html)
    issues: list[str] = []

    if "" in scan.anchors:
        issues.append('Replace href="#" placeholder links with real section targets or buttons.')
    missing = sorted({a for a in scan.anchors if a not in ("", "top") and a not in scan.targets})
    if missing:
        issues.append(
            "These links point to ids that do not exist; add the matching section ids or fix the "
            f"hrefs: {', '.join('#' + a for a in missing[:6])}."
        )

    has_submit_handler = bool(
        re.search(r"""['"`]submit['"`]|\.onsubmit\s*=""", js) or "onsubmit=" in html.lower()
    )
    unhandled = [f for f in scan.forms if not f.get("action") and f.get("method") != "dialog"]
    if unhandled and not has_submit_handler:
        label = ", ".join(
            f.get("id") or f.get("class") or f.get("name") or "form" for f in unhandled[:4]
        )
        hint = " It also sets novalidate, so nothing checks the fields." if any(
            "novalidate" in f for f in unhandled
        ) else ""
        issues.append(
            f"The form ({label}) has no action and app.js has no submit listener, so submitting "
            "just reloads the page. Add a 'submit' listener that calls preventDefault(), validates "
            f"the fields, and shows an inline success or error message.{hint}"
        )

    # Generic delegation like querySelectorAll('button') or closest('button') wires every button.
    if re.search(r"""['"`][^'"`\n]*(?<![\w.#-])button(?![\w-])""", js):
        return issues
    inert = []
    for button in scan.loose_buttons:
        if (button.get("type") or "").lower() == "submit" or {
            "popovertarget", "commandfor", "onclick"
        } & button.keys():
            continue  # native behaviour, or already reported as inline onclick
        hooks = [button.get("id") or ""] + (button.get("class") or "").split()
        hooks += [key for key in button if key.startswith("data-")]
        if not any(hook and _js_mentions(js, hook) for hook in hooks):
            inert.append(button.get("_text") or button.get("id") or button.get("class") or "button")
    if inert:
        issues.append(
            "These buttons are not referenced by app.js, so clicking them does nothing; give each an "
            "id/class/data-* hook and wire it to an inline state change: "
            f"{', '.join(repr(label) for label in inert[:6])}."
        )
    return issues
