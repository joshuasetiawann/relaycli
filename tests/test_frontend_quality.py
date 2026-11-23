import shutil

from relaycli.core.context import ProjectContext
from relaycli.frontend_quality import frontend_design_brief, frontend_quality_issues
from relaycli.frontend_scaffold import create_frontend_scaffold, detect_frontend_scaffold


def test_design_brief_varies_by_product_purpose():
    campaign = frontend_design_brief('buat website iklan mineral water di folder "Toko"')
    store = frontend_design_brief('buat website toko sepatu di folder "Shop"')

    assert "not a storefront" in campaign
    assert "commerce" in store
    assert campaign != store
    assert "universal hero + card grid" in campaign
    assert "one core file per tool turn" in campaign


def test_quality_gate_rejects_the_observed_placeholder_landing_page(tmp_path):
    root = tmp_path / "Landing Page"
    root.mkdir()
    (root / "index.html").write_text(
        '<h1>Welcome to Our Website</h1><p>This is a landing page for our website.</p><button>Get Started</button>',
        encoding="utf-8",
    )
    (root / "styles.css").write_text("body{font-family:Arial}", encoding="utf-8")
    (root / "app.js").write_text("alert('clicked')", encoding="utf-8")

    issues = frontend_quality_issues(
        ProjectContext(tmp_path), 'buat landing page di folder "Landing Page"'
    )

    assert any("placeholder" in issue for issue in issues)
    assert any("structurally thin" in issue for issue in issues)
    assert any("Arial" in issue for issue in issues)
    assert any("window.alert" in issue for issue in issues)


def test_quality_gate_does_not_confuse_page_copy_with_arial_font(tmp_path):
    (tmp_path / "index.html").write_text("<p>Our actuarial team loves tennis.</p>", encoding="utf-8")
    assert not any("Arial" in issue for issue in frontend_quality_issues(
        ProjectContext(tmp_path), "build a tennis site"
    ))


def test_quality_gate_rejects_inert_javascript(tmp_path):
    (tmp_path / "index.html").write_text("<script src='app.js'></script>", encoding="utf-8")
    (tmp_path / "app.js").write_text("// TODO", encoding="utf-8")
    assert any("inert app.js" in issue for issue in frontend_quality_issues(
        ProjectContext(tmp_path), "build a tennis site"
    ))


def test_quality_gate_catches_unstyled_sections_and_inert_mobile_menu(tmp_path):
    (tmp_path / "index.html").write_text(
        '<section class="hero"></section><section class="story"></section>'
        '<section class="sessions"></section><section class="membership"></section>'
        '<button class="nav-toggle" aria-expanded="false"></button>'
        '<script src="app.js"></script>', encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text("section { margin: 2rem }", encoding="utf-8")
    (tmp_path / "app.js").write_text("document.addEventListener('click', () => {})", encoding="utf-8")
    issues = frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site")
    assert any("distinct content sections" in issue for issue in issues)
    assert any("mobile navigation toggle" in issue for issue in issues)


def test_quality_gate_reports_the_observed_dead_controls(tmp_path):
    (tmp_path / "index.html").write_text(
        '<section id="story"></section>'
        '<section class="membership"><button class="plan-btn">Join Club</button></section>'
        '<form id="contact-form" novalidate><input name="email"><button type="submit">Send</button></form>'
        '<footer><a href="#story">Story</a><a href="#coaching">Coaching</a><a href="#">Top</a></footer>'
        '<script src="app.js"></script>', encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        "document.querySelector('.nav-toggle').addEventListener('click', () => {})",
        encoding="utf-8",
    )
    issues = frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site")

    link = [i for i in issues if "ids that do not exist" in i]
    assert link and "#coaching" in link[0] and "#story" not in link[0]
    form = [i for i in issues if "no submit listener" in i]
    assert form and "contact-form" in form[0] and "novalidate" in form[0]
    button = [i for i in issues if "buttons are not referenced" in i]
    assert button and "'Join Club'" in button[0] and "Send" not in button[0]


def test_dead_control_check_accepts_wired_controls(tmp_path):
    (tmp_path / "index.html").write_text(
        '<a id="top"></a><section id="plans"><button data-plan="pro">Pro</button>'
        '<button id="open-faq">FAQ</button><button popovertarget="menu">Menu</button></section>'
        '<form id="contact"><button>Send</button></form>'
        '<a href="#plans">Plans</a><a href="#top">Top</a>', encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        "document.querySelectorAll('[data-plan]').forEach(b => b.addEventListener('click', f));"
        "document.getElementById('open-faq').onclick = f;"
        "document.getElementById('contact').addEventListener('submit', e => e.preventDefault());",
        encoding="utf-8",
    )
    issues = frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site")
    assert not any(
        phrase in issue
        for issue in issues
        for phrase in ("ids that do not exist", "no submit listener", "buttons are not referenced")
    )

    # Delegated `button` selectors wire every button, so none are reported as inert.
    (tmp_path / "app.js").write_text(
        "document.addEventListener('click', e => e.target.closest('button'))", encoding="utf-8"
    )
    issues = frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site")
    assert not any("buttons are not referenced" in issue for issue in issues)


def test_dead_control_check_respects_anchor_targets_and_dialog_forms(tmp_path):
    (tmp_path / "index.html").write_text(
        '<input name="email"><a href="#email">Email</a>'
        '<a name="legacy"></a><a href="#legacy">Legacy</a>'
        '<form method="dialog"><button type="reset">Reset</button><button>Close</button></form>', encoding="utf-8",
    )
    (tmp_path / "app.js").write_text("document.addEventListener('click', () => {})", encoding="utf-8")
    issues = frontend_quality_issues(ProjectContext(tmp_path), "build a site")
    assert any("#email" in issue for issue in issues)
    assert not any("#legacy" in issue or "'Reset'" in issue or "no submit listener" in issue for issue in issues)


def test_quality_gate_can_inspect_a_project_root_without_folder_in_request(tmp_path):
    (tmp_path / "index.html").write_text(
        "<meta name='description' content='x'>"
        "<section></section><section></section><section></section><section></section>"
        "<footer></footer><button onclick=\"alert('x')\">Pesan</button>",
        encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text("body{font-family:Arial}", encoding="utf-8")
    (tmp_path / "app.js").write_text("", encoding="utf-8")

    issues = frontend_quality_issues(ProjectContext(tmp_path), "audit the current frontend")

    assert any("Arial" in issue for issue in issues)
    assert any("inline onclick" in issue for issue in issues)


def test_quality_gate_discovers_single_generated_src_bundle(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.html").write_text("<h1>Welcome to Our Website</h1>", encoding="utf-8")
    (src / "styles.css").write_text("body{font-family:Arial}", encoding="utf-8")
    (src / "app.js").write_text("alert('x')", encoding="utf-8")

    issues = frontend_quality_issues(ProjectContext(tmp_path), "buat website tenis")

    assert any("placeholder" in issue for issue in issues)


def test_quality_gate_rejects_tutorial_green_and_identical_section_cards(tmp_path):
    (tmp_path / "index.html").write_text(
        "<meta name='description' content='Arunika'>"
        "<section></section><section></section><section></section><section></section>"
        "<footer></footer><svg></svg>",
        encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text(
        "section { background: #4CAF50; border-radius: 12px; padding: 4rem; }"
        "@media(max-width: 700px){section{padding:2rem}}"
        "@media(prefers-reduced-motion: reduce){*{animation:none}}"
        ".x{transition:opacity .2s} " * 30,
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text("", encoding="utf-8")

    issues = frontend_quality_issues(ProjectContext(tmp_path), "audit current frontend")

    assert any("tutorial-green" in issue for issue in issues)
    assert any("same rounded card" in issue for issue in issues)


def test_branded_water_scaffold_is_campaign_specific_and_passes_quality_gate(tmp_path):
    request = (
        'Buat landing page iklan air mineral premium bernama ARUNIKA di folder "proof". '
        'Desain dark, unik, interaktif, responsive, dengan animasi halus.'
    )
    scaffold = detect_frontend_scaffold(request)

    assert scaffold is not None
    assert scaffold.title == "ARUNIKA"
    assert scaffold.theme == "water"
    create_frontend_scaffold(ProjectContext(tmp_path), scaffold)

    html = (tmp_path / "proof" / "index.html").read_text(encoding="utf-8")
    css = (tmp_path / "proof" / "styles.css").read_text(encoding="utf-8")
    js = (tmp_path / "proof" / "app.js").read_text(encoding="utf-8")
    assert "Hening." in html
    assert 'class="product"' not in html
    assert "profile-tab" in html
    assert ".hero-copy{position:relative;z-index:2;width:100%;min-width:0" in css
    assert ".hero-actions{align-items:flex-start;flex-direction:column}" in css
    assert ".water-hero{grid-template-columns:minmax(0,1fr)" in css
    assert ".hero-copy{width:100%;max-width:calc(100vw - 2.5rem)}" in css
    assert "addEventListener" in js
    assert frontend_quality_issues(ProjectContext(tmp_path), request) == []


def test_local_scaffold_domains_use_distinct_layout_compositions(tmp_path):
    requests = {
        "shoe": 'buat website toko sepatu di folder "shoe"',
        "fashion": 'buat website toko baju di folder "fashion"',
        "mandarin": 'buat website platform belajar mandarin di folder "mandarin"',
        "store": 'buat website toko online di folder "store"',
    }

    for theme, request in requests.items():
        scaffold = detect_frontend_scaffold(request)
        assert scaffold is not None
        assert scaffold.theme == theme
        create_frontend_scaffold(ProjectContext(tmp_path), scaffold)
        generated_html = (tmp_path / theme / "index.html").read_text(encoding="utf-8")
        assert f"layout-{theme}" in generated_html
        assert "fonts.googleapis.com" not in generated_html
        assert frontend_quality_issues(ProjectContext(tmp_path), request) == []

    css = (tmp_path / "fashion" / "styles.css").read_text(encoding="utf-8")
    assert ".layout-fashion .hero-product { order: -1" in css
    assert ".layout-shoe .hero-product { order: -1" in css
    assert ".layout-mandarin .product { display: grid" in css
    assert ".layout-store .products { grid-template-columns: repeat(12" in css


def test_quality_gate_catches_scripts_that_cannot_run(tmp_path):
    # Observed: a local model's app.js stopped mid-statement and the gate passed it.
    (tmp_path / "index.html").write_text(
        '<a href="#">Top</a><button class="menu-btn" aria-expanded="false">Menu</button>'
        '<div id="booking-summary"></div><script src="app.js"></script>', encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text("body{color:#111}", encoding="utf-8")
    (tmp_path / "app.js").write_text(
        "const s = document.getElementById('booking-summary');\n"
        "const d = document.getElementById('selected-day');\n"
        "document.querySelector('.menu-btn').addEventListener('click', function() {\n"
        "  s.style.display\n", encoding="utf-8",
    )
    issues = " ".join(frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site"))
    if shutil.which("node"):
        assert "does not parse" in issues
    assert "#selected-day" in issues and "#booking-summary" not in issues
    assert "aria-expanded" in issues
    assert 'href="#"' in issues
    assert "Link styles.css" in issues
    assert "focus" in issues


def test_quality_gate_rejects_remote_images(tmp_path):
    (tmp_path / "index.html").write_text(
        '<img src="https://images.example.com/court.jpg">', encoding="utf-8"
    )
    (tmp_path / "styles.css").write_text(".hero{background:url('//cdn.example.com/x.png')}", encoding="utf-8")
    assert any("remote images" in i for i in frontend_quality_issues(
        ProjectContext(tmp_path), "build a tennis site"
    ))


def test_quality_gate_rejects_false_success_claims_without_a_network_path(tmp_path):
    # Observed NIGHT COURT draft: no fetch/action, yet "Your message has been sent!".
    (tmp_path / "index.html").write_text('<form id="f"></form><script src="app.js"></script>', encoding="utf-8")
    js = tmp_path / "app.js"
    js.write_text(
        "f.addEventListener('submit', e => { e.preventDefault();\n"
        "  out.textContent = 'Your message has been sent!';\n"
        "  plan.textContent = \"Thank you for joining NIGHT COURT!\"; });", encoding="utf-8",
    )
    issues = " ".join(frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site"))
    assert "'Your message has been sent!'" in issues and "'Thank you for joining NIGHT COURT!'" in issues

    # Run 4: the whole lying string is quoted, so the model can find it.
    js.write_text(
        "out.textContent = `You have booked the ${sessionType} session.`;\n"
        "f.textContent = 'Thank you for your message. This is a demo and no data was sent.';", encoding="utf-8",
    )
    issues = " ".join(frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site"))
    assert "'You have booked the ${sessionType} session.'" in issues
    assert "Thank you for your message" not in issues

    js.write_text(
        "f.addEventListener('submit', e => { e.preventDefault();\n"
        "  out.textContent = 'Demo only: nothing has been sent.'; });", encoding="utf-8",
    )
    assert "Nothing is sent" not in " ".join(
        frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site")
    )

    js.write_text("fetch('/api').then(() => { out.textContent = 'Your message has been sent!'; });", encoding="utf-8")
    assert "Nothing is sent" not in " ".join(
        frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site")
    )


def test_quality_gate_rejects_a_pasted_second_stylesheet_and_placeholder_meta(tmp_path):
    # Observed NIGHT COURT cycle 2: the coder pasted the whole stylesheet into
    # index.html and filled the head with template verification codes.
    inline = "<style>" + ".hero{padding:2rem;color:#111}\n" * 20 + "</style>"
    (tmp_path / "index.html").write_text(
        f'<head>{inline}<meta name="google-site-verification" content="your-google-site-verification-code">'
        '<link rel="canonical" href="https://example.com"></head>', encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text(".hero{padding:2rem}", encoding="utf-8")
    issues = " ".join(frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site"))
    assert "own <style> block" in issues
    assert "your-google-site-verifica" in issues and "https://example.com" in issues

    # A small inline style and real metadata are fine.
    (tmp_path / "index.html").write_text(
        '<head><style>.skip{left:0}</style><link rel="canonical" href="https://nightcourt.club/"></head>',
        encoding="utf-8",
    )
    issues = " ".join(frontend_quality_issues(ProjectContext(tmp_path), "build a tennis site"))
    assert "own <style> block" not in issues and "placeholder metadata" not in issues


def test_quality_gate_reports_linked_local_files_that_were_never_written(tmp_path):
    # Observed NIGHT COURT cycle 2: favicons and a manifest linked, none created.
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="styles.css"><link rel="manifest" href="/site.webmanifest">'
        '<link rel="icon" href="/favicon-32x32.png?v=2"><a href="#top">Top</a>'
        '<a href="https://nightcourt.club/">Home</a><a href="mailto:hi@nightcourt.club">Mail</a>'
        '<a href="/">Root</a><script src="app.js"></script>', encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "app.js").write_text("", encoding="utf-8")
    issues = [i for i in frontend_quality_issues(ProjectContext(tmp_path), "build a site") if "do not exist, so the browser" in i]
    assert len(issues) == 1
    assert "/favicon-32x32.png" in issues[0] and "/site.webmanifest" in issues[0]
    assert "styles.css" not in issues[0] and "app.js" not in issues[0] and "mailto" not in issues[0]


def test_quality_gate_reports_a_menu_opened_by_an_attribute_it_never_gets(tmp_path):
    # Observed in a browser on NIGHT COURT: aria-expanded flipped, menu stayed hidden.
    (tmp_path / "index.html").write_text(
        '<button id="menu-toggle" aria-expanded="false" aria-controls="menu">Menu</button>'
        '<ul id="menu" class="nav-list" hidden></ul><script src="app.js"></script>', encoding="utf-8",
    )
    css = tmp_path / "styles.css"
    css.write_text('@media (max-width:600px){#menu{display:none}#menu[aria-expanded="true"]{display:flex}}', encoding="utf-8")
    (tmp_path / "app.js").write_text(
        "t.addEventListener('click', () => t.setAttribute('aria-expanded', 'true'));", encoding="utf-8",
    )
    issues = " ".join(frontend_quality_issues(ProjectContext(tmp_path), "build a site"))
    assert "opens #menu via its own [aria-expanded]" in issues

    # Keying on the toggle's own attribute is correct.
    css.write_text('#menu{display:none}#menu-toggle[aria-expanded="true"] + #menu{display:flex}', encoding="utf-8")
    assert "via its own [aria-expanded]" not in " ".join(
        frontend_quality_issues(ProjectContext(tmp_path), "build a site")
    )


def test_quality_gate_reports_selectable_divs_matched_on_event_target(tmp_path):
    # Observed in a browser on NIGHT COURT run 3: clicking a session's <h3> did nothing.
    (tmp_path / "index.html").write_text(
        '<div id="session-list"><div class="session" data-session="s1"><h3>Baseline</h3></div></div>'
        '<p id="booking-summary" aria-live="polite"></p><script src="app.js"></script>', encoding="utf-8",
    )
    js = tmp_path / "app.js"
    js.write_text(
        "list.addEventListener('click', e => { if (e.target.classList.contains('session')) "
        "{ out.textContent = e.target.dataset.session; } });", encoding="utf-8",
    )
    issues = " ".join(frontend_quality_issues(ProjectContext(tmp_path), "build a site"))
    assert "app.js picks .session by checking event.target itself" in issues

    # closest() on real buttons is the fix and is not flagged.
    (tmp_path / "index.html").write_text(
        '<div id="session-list"><button class="session" data-session="s1"><b>Baseline</b></button></div>'
        '<p id="booking-summary" aria-live="polite"></p><script src="app.js"></script>', encoding="utf-8",
    )
    js.write_text(
        "list.addEventListener('click', e => { const b = e.target.closest('.session'); "
        "if (b) out.textContent = b.dataset.session; });", encoding="utf-8",
    )
    assert "by checking event.target itself" not in " ".join(
        frontend_quality_issues(ProjectContext(tmp_path), "build a site")
    )


def test_quality_gate_reports_full_width_padding_without_border_box(tmp_path):
    # Observed in a browser on NIGHT COURT run 4: 49px of sideways scroll at 390px.
    (tmp_path / "index.html").write_text('<link rel="stylesheet" href="styles.css"><footer></footer>', encoding="utf-8")
    css = tmp_path / "styles.css"
    css.write_text("/* FOOTER STYLES */\nfooter { padding: 2rem; position: relative; width: 100%; }", encoding="utf-8")
    issues = " ".join(frontend_quality_issues(ProjectContext(tmp_path), "build a site"))
    assert "footer set width: 100% plus side padding" in issues

    for ok in (
        "*,*::before,*::after{box-sizing:border-box} footer{padding:2rem;width:100%}",
        "footer{padding:2rem 0;width:100%} .bar{padding-top:1rem;width:100%}",
        "footer{padding:2rem;max-width:100%}",
    ):
        css.write_text(ok, encoding="utf-8")
        assert "plus side padding" not in " ".join(
            frontend_quality_issues(ProjectContext(tmp_path), "build a site")
        ), ok


def test_pinned_footer_issue_names_the_file_and_declaration(tmp_path):
    # Observed NIGHT COURT run 5: the vague wording led to an inline style= patch.
    (tmp_path / "index.html").write_text('<footer style="position: static;"></footer>', encoding="utf-8")
    (tmp_path / "styles.css").write_text("footer { position: fixed; bottom: 0; }", encoding="utf-8")
    issues = " ".join(frontend_quality_issues(ProjectContext(tmp_path), "build a site"))
    assert "styles.css pins the footer over page content (footer { position: fixed })" in issues
