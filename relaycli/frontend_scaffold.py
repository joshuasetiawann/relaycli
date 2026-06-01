"""Deterministic frontend scaffolds for common small website requests.

Small local models sometimes emit fake JSON tool calls instead of actually
calling RelayCLI tools. For very common, low-risk requests such as "buat web
toko sepatu frontend di folder sepatuu", a tiny local scaffold is more helpful
and more predictable than another model round-trip.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from relaycli.context import ProjectContext
from relaycli.tools.base import atomic_write


_FOLDER_QUOTED_RE = re.compile(
    r"(?:di\s+)?folder(?:\s+baru)?(?:\s+(?:namanya|nama|bernama))?\s+"
    r"(?:\"([^\"]{1,80})\"|'([^']{1,80})'|“([^”]{1,80})”)",
    re.IGNORECASE,
)
_FOLDER_BARE_RE = re.compile(
    r"(?:di\s+)?folder(?:\s+baru)?(?:\s+(?:namanya|nama|bernama))\s+"
    r"([A-Za-z0-9][A-Za-z0-9._-]{0,63})|"
    r"(?:di\s+)?folder(?:\s+baru)?\s+([A-Za-z0-9][A-Za-z0-9._-]{0,63})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FrontendScaffold:
    folder: str
    title: str
    theme: str
    tone: str = "light"


@dataclass(frozen=True)
class ScaffoldResult:
    folder: str
    files: tuple[str, ...]


def detect_frontend_scaffold(text: str) -> FrontendScaffold | None:
    """Return a scaffold request when the user's text is concrete enough."""

    raw = " ".join((text or "").strip().split())
    if not raw:
        return None
    lowered = raw.lower()
    wants_web = any(word in lowered for word in ("web", "website", "frontend", "front end", "halaman"))
    wants_create = any(word in lowered for word in ("buat", "buatin", "buatkan", "bikin", "build"))
    if not (wants_web and wants_create):
        return None
    folder = _extract_folder(raw)
    tone = "dark" if any(word in lowered for word in ("hitam", "dark", "black", "gelap")) else "light"
    wants_frontend_only = any(word in lowered for word in ("frontend", "front end", "ui", "tampilan"))
    wants_static = wants_frontend_only or any(word in lowered for word in ("html", "css", "javascript", " js"))
    wants_themed_static = wants_web or wants_static
    mentions_shoe = any(word in lowered for word in ("sepatu", "spatu", "shoe"))
    mentions_fashion = any(word in lowered for word in ("baju", "pakaian", "fashion", "kaos", "kemeja"))
    mentions_mandarin = any(
        word in lowered
        for word in ("mandarin", "hanzi", "hsk", "bahasa china", "bahasa cina", "chinese")
    )
    negates_shoe = "bukan sepatu" in lowered or "bukan toko sepatu" in lowered
    negates_fashion = "bukan baju" in lowered or "bukan toko baju" in lowered
    if mentions_mandarin:
        if not folder and wants_themed_static:
            folder = "belajar-mandarin"
        if folder:
            return FrontendScaffold(folder=folder, title="MandarinLab", theme="mandarin", tone=tone)
    if mentions_fashion and not negates_fashion:
        if not folder and wants_themed_static:
            folder = "toko-baju"
        if folder:
            return FrontendScaffold(folder=folder, title="BajuWear Store", theme="fashion", tone=tone)
    if mentions_shoe and not negates_shoe:
        if not folder and wants_themed_static:
            folder = "toko-sepatu"
        if folder:
            return FrontendScaffold(folder=folder, title="Sepatuu Store", theme="shoe", tone=tone)
    if any(word in lowered for word in ("toko", "shop", "store")):
        if not folder and wants_static:
            folder = "storefront"
        if folder:
            return FrontendScaffold(folder=folder, title="Storefront", theme="store", tone=tone)
    return None


def create_frontend_scaffold(project: ProjectContext, req: FrontendScaffold) -> ScaffoldResult:
    """Create a self-contained static frontend inside ``project``."""

    root = project.resolve(req.folder)
    files = {
        "index.html": _index_html(req),
        "styles.css": _styles_css(),
        "app.js": _app_js(req),
        "README.md": _readme(req),
    }
    written: list[str] = []
    for name, content in files.items():
        path = root / name
        # Resolving the child path guards against weird folder names and symlinks.
        safe = project.resolve(Path(req.folder) / name)
        atomic_write(safe, content)
        written.append(project.relative(path))
    return ScaffoldResult(folder=project.relative(root), files=tuple(written))


def _extract_folder(text: str) -> str | None:
    quoted = _FOLDER_QUOTED_RE.search(text)
    folder = None
    if quoted:
        folder = next((part for part in quoted.groups() if part), None)
    else:
        bare = _FOLDER_BARE_RE.search(text)
        if bare:
            folder = next((part for part in bare.groups() if part), None)
    if folder is None:
        return None
    folder = " ".join(folder.strip().strip(".,;:!?").split())
    if folder in {".", "..", "bernama", "namanya", "nama"} or "/" in folder or "\\" in folder:
        return None
    return folder



def _theme_data(req: FrontendScaffold) -> dict:
    if req.theme == "mandarin":
        categories = [
            ("all", "Semua"),
            ("pemula", "Pemula"),
            ("hanzi", "Hanzi"),
            ("percakapan", "Percakapan"),
        ]
        products = [
            {"name": "Nada dan Pinyin", "type": "pemula", "price": "4 bab", "color": "Audio", "mark": "声"},
            {"name": "Sapaan Sehari-hari", "type": "percakapan", "price": "6 dialog", "color": "Speaking", "mark": "你"},
            {"name": "Hanzi Dasar", "type": "hanzi", "price": "80 karakter", "color": "Writing", "mark": "汉"},
            {"name": "Angka dan Waktu", "type": "pemula", "price": "Latihan cepat", "color": "Quiz", "mark": "一"},
            {"name": "Belanja dan Makan", "type": "percakapan", "price": "12 frasa", "color": "Roleplay", "mark": "买"},
            {"name": "Radikal Populer", "type": "hanzi", "price": "24 radikal", "color": "Memory", "mark": "部"},
        ]
        return {
            "mark": "中",
            "nav_primary": "Materi",
            "nav_secondary": "Latihan",
            "nav_tertiary": "Progress",
            "cart_label": "Kelas",
            "eyebrow": "Platform belajar mandarin",
            "headline": "Belajar Mandarin dari pinyin, hanzi, sampai percakapan harian.",
            "lead": "Ikuti modul pendek, dengarkan audio, latihan hanzi, dan bangun kebiasaan belajar 15 menit setiap hari.",
            "primary_label": "Mulai belajar",
            "secondary_label": "Lihat latihan",
            "hero_aria": "Kartu modul unggulan",
            "badge": "HSK 1",
            "featured": "Nada dan Pinyin",
            "featured_price": "4 bab interaktif",
            "hero_visual": '<div class="product-art hero-art" aria-hidden="true">你好</div>',
            "filters_aria": "Filter materi",
            "section_eyebrow": "Jalur belajar",
            "section_title": "Modul populer",
            "promo_eyebrow": "Latihan harian",
            "promo_headline": "Bangun streak 7 hari dengan kuis cepat, audio pendek, dan review kosakata.",
            "promo_button": "Mulai latihan",
            "reviews": [
                ("1.200+", "Kosakata dasar siap dipelajari bertahap"),
                ("15 menit", "Sesi pendek untuk belajar konsisten"),
                ("7 hari", "Target streak awal agar tidak cepat berhenti"),
            ],
            "categories": categories,
            "products": products,
            "add_label": "Pelajari",
            "added_label": "Dipilih",
            "readme_label": "platform belajar mandarin",
        }
    if req.theme == "fashion":
        categories = [("all", "Semua"), ("atasan", "Atasan"), ("outer", "Outer"), ("celana", "Celana")]
        products = [
            {"name": "Everyday Cotton Tee", "type": "atasan", "price": "Rp129.000", "color": "Ivory", "mark": "ET"},
            {"name": "Relaxed Oxford Shirt", "type": "atasan", "price": "Rp219.000", "color": "Blue", "mark": "RO"},
            {"name": "City Coach Jacket", "type": "outer", "price": "Rp349.000", "color": "Navy", "mark": "CJ"},
            {"name": "Soft Knit Cardigan", "type": "outer", "price": "Rp299.000", "color": "Sage", "mark": "KC"},
            {"name": "Straight Chino Pants", "type": "celana", "price": "Rp279.000", "color": "Khaki", "mark": "CP"},
            {"name": "Wide Linen Trousers", "type": "celana", "price": "Rp319.000", "color": "Black", "mark": "LT"},
        ]
        return {
            "mark": "B",
            "headline": "Baju harian yang rapi, nyaman, dan gampang dipadukan.",
            "lead": "Pilih kaos, kemeja, outer, dan celana dengan potongan bersih untuk kerja, kuliah, atau akhir pekan.",
            "featured": "City Coach Jacket",
            "featured_price": "Mulai Rp349.000",
            "hero_visual": '<div class="product-art hero-art" aria-hidden="true">BW</div>',
            "categories": categories,
            "products": products,
            "readme_label": "toko baju",
        }
    if req.theme == "store":
        categories = [("all", "Semua"), ("daily", "Daily"), ("home", "Home"), ("gift", "Gift")]
        products = [
            {"name": "Daily Essentials Pack", "type": "daily", "price": "Rp149.000", "color": "Mixed", "mark": "DE"},
            {"name": "Desk Organizer", "type": "home", "price": "Rp89.000", "color": "Oak", "mark": "DO"},
            {"name": "Mini Gift Box", "type": "gift", "price": "Rp119.000", "color": "Coral", "mark": "GB"},
            {"name": "Travel Pouch", "type": "daily", "price": "Rp99.000", "color": "Slate", "mark": "TP"},
            {"name": "Ceramic Cup Set", "type": "home", "price": "Rp159.000", "color": "White", "mark": "CS"},
            {"name": "Greeting Bundle", "type": "gift", "price": "Rp79.000", "color": "Warm", "mark": "GR"},
        ]
        return {
            "mark": "S",
            "headline": "Toko online sederhana dengan katalog bersih dan checkout cepat.",
            "lead": "Tampilkan produk pilihan, filter kategori, promo, dan tombol beli dalam satu halaman statis yang ringan.",
            "featured": "Daily Essentials Pack",
            "featured_price": "Mulai Rp149.000",
            "hero_visual": '<div class="product-art hero-art" aria-hidden="true">ST</div>',
            "categories": categories,
            "products": products,
            "readme_label": "toko online",
        }
    categories = [("all", "Semua"), ("sneakers", "Sneakers"), ("running", "Running"), ("casual", "Casual")]
    products = [
        {"name": "Aero Street Runner", "type": "sneakers", "price": "Rp329.000", "color": "Orange", "mark": "SR"},
        {"name": "Cloud Pace Knit", "type": "running", "price": "Rp419.000", "color": "Blue", "mark": "CP"},
        {"name": "Daily Slip-On", "type": "casual", "price": "Rp249.000", "color": "Black", "mark": "DS"},
        {"name": "Court Flex Low", "type": "sneakers", "price": "Rp379.000", "color": "White", "mark": "CF"},
        {"name": "Sprint Lite", "type": "running", "price": "Rp399.000", "color": "Green", "mark": "SL"},
        {"name": "Weekend Loafer", "type": "casual", "price": "Rp289.000", "color": "Brown", "mark": "WL"},
    ]
    return {
        "mark": "S",
        "headline": "Sepatu harian yang ringan, rapi, dan siap dipakai kemana saja.",
        "lead": "Pilih sneakers, running shoes, sampai slip-on dengan tampilan bersih dan harga ramah.",
        "featured": "Aero Street Runner",
        "featured_price": "Mulai Rp329.000",
        "hero_visual": '<div class="shoe-visual">\n            <span class="sole"></span>\n            <span class="upper"></span>\n            <span class="lace"></span>\n          </div>',
        "categories": categories,
        "products": products,
        "readme_label": "toko sepatu",
    }


def _filter_buttons(data: dict) -> str:
    return "\n      ".join(
        f'<button class="filter {"active" if value == "all" else ""}" data-filter="{value}">{label}</button>'
        for value, label in data["categories"]
    )

def _index_html(req: FrontendScaffold) -> str:
    data = _theme_data(req)
    reviews = data.get("reviews") or [
        ("4.9/5", "Rating pelanggan"),
        ("24 jam", "Pengiriman cepat area kota besar"),
        ("7 hari", "Tukar ukuran tanpa ribet"),
    ]
    nav_primary = data.get("nav_primary", "Koleksi")
    nav_secondary = data.get("nav_secondary", "Promo")
    nav_tertiary = data.get("nav_tertiary", "Ulasan")
    cart_label = data.get("cart_label", "Cart")
    eyebrow = data.get("eyebrow", "Drop baru minggu ini")
    primary_label = data.get("primary_label", "Belanja sekarang")
    secondary_label = data.get("secondary_label", "Lihat promo")
    hero_aria = data.get("hero_aria", "Produk unggulan")
    badge = data.get("badge", "Best pick")
    filters_aria = data.get("filters_aria", "Filter produk")
    section_eyebrow = data.get("section_eyebrow", "Koleksi pilihan")
    section_title = data.get("section_title", "Produk populer")
    promo_eyebrow = data.get("promo_eyebrow", "Flash deal")
    promo_headline = data.get(
        "promo_headline",
        "Gratis ongkir dan diskon sampai 35% untuk checkout hari ini.",
    )
    promo_button = data.get("promo_button", "Ambil promo")
    return f"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{req.title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="styles.css">
</head>
<body class="theme-{req.tone}">
  <header class="topbar">
    <a class="brand" href="#home" aria-label="{req.title} home">
      <span class="brand-mark">{data["mark"]}</span>
      <span>{req.title}</span>
    </a>
    <nav aria-label="Kategori utama">
      <a href="#koleksi">{nav_primary}</a>
      <a href="#promo">{nav_secondary}</a>
      <a href="#ulasan">{nav_tertiary}</a>
    </nav>
    <button class="cart-button" type="button" id="cartButton">
      <span>{cart_label}</span>
      <strong id="cartCount">0</strong>
    </button>
  </header>

  <main id="home">
    <section class="hero">
      <div class="hero-copy">
        <p class="eyebrow">{eyebrow}</p>
        <h1>{data["headline"]}</h1>
        <p class="lead">{data["lead"]}</p>
        <div class="hero-actions">
          <a class="primary" href="#koleksi">{primary_label}</a>
          <a class="secondary" href="#promo">{secondary_label}</a>
        </div>
      </div>
      <div class="hero-product" aria-label="{hero_aria}">
        <div class="shoe-card">
          <span class="badge">{badge}</span>
          {data["hero_visual"]}
          <h2>{data["featured"]}</h2>
          <p>{data["featured_price"]}</p>
        </div>
      </div>
    </section>

    <section class="filters" aria-label="{filters_aria}">
      {_filter_buttons(data)}
    </section>

    <section class="catalog" id="koleksi" aria-labelledby="koleksi-title">
      <div class="section-head">
        <p class="eyebrow">{section_eyebrow}</p>
        <h2 id="koleksi-title">{section_title}</h2>
      </div>
      <div class="products" id="products"></div>
    </section>

    <section class="promo" id="promo">
      <div>
        <p class="eyebrow">{promo_eyebrow}</p>
        <h2>{promo_headline}</h2>
      </div>
      <a class="primary" href="#koleksi">{promo_button}</a>
    </section>

    <section class="reviews" id="ulasan">
      <article>
        <strong>{reviews[0][0]}</strong>
        <span>{reviews[0][1]}</span>
      </article>
      <article>
        <strong>{reviews[1][0]}</strong>
        <span>{reviews[1][1]}</span>
      </article>
      <article>
        <strong>{reviews[2][0]}</strong>
        <span>{reviews[2][1]}</span>
      </article>
    </section>
  </main>

  <script src="app.js"></script>
</body>
</html>
"""


def _styles_css() -> str:
    return """* {
  box-sizing: border-box;
}

:root {
  --ink: #15161a;
  --muted: #686b75;
  --line: #e5e7ee;
  --paper: #ffffff;
  --soft: #f5f7fb;
  --brand: #ee4d2d;
  --blue: #2557d6;
  --green: #168a52;
}

body {
  margin: 0;
  font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--ink);
  background: var(--soft);
}

.theme-dark {
  --ink: #f6f7fb;
  --muted: #b8bdc9;
  --line: #2c313a;
  --paper: #171b22;
  --soft: #080a0f;
  --brand: #ff6a3d;
  --blue: #8fa8ff;
  --green: #45d18a;
}

.theme-dark .topbar {
  background: rgba(10, 12, 18, 0.92);
}

.theme-dark .secondary,
.theme-dark .filter,
.theme-dark .product,
.theme-dark .reviews article {
  background: var(--paper);
}

.theme-dark .cart-button,
.theme-dark .filter.active,
.theme-dark .promo {
  background: #f6f7fb;
  color: #101217;
}

.theme-dark .cart-button strong {
  background: #101217;
  color: #f6f7fb;
}

.theme-dark .hero-product,
.theme-dark .product-art {
  background: linear-gradient(145deg, #141923, #0c0f15);
}

.theme-dark .shoe-card {
  background: rgba(23, 27, 34, 0.88);
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.36);
}

a {
  color: inherit;
  text-decoration: none;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 24px;
  min-height: 68px;
  padding: 0 clamp(18px, 5vw, 72px);
  background: rgba(255, 255, 255, 0.92);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(14px);
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-weight: 800;
  letter-spacing: 0;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: var(--brand);
  color: white;
}

nav {
  display: flex;
  gap: 18px;
  color: var(--muted);
  font-size: 14px;
  margin-left: auto;
}

.cart-button {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--line);
  background: var(--ink);
  color: white;
  border-radius: 999px;
  padding: 9px 13px;
  cursor: pointer;
}

.cart-button strong {
  display: grid;
  place-items: center;
  min-width: 22px;
  height: 22px;
  border-radius: 999px;
  background: white;
  color: var(--ink);
  font-size: 12px;
}

main {
  width: min(1160px, calc(100% - 32px));
  margin: 0 auto;
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(280px, 0.95fr);
  gap: clamp(24px, 5vw, 72px);
  align-items: center;
  min-height: 560px;
  padding: 54px 0 34px;
}

.eyebrow {
  margin: 0 0 12px;
  color: var(--brand);
  font-weight: 800;
  text-transform: uppercase;
  font-size: 12px;
  letter-spacing: 0.08em;
}

h1, h2, p {
  margin-top: 0;
}

h1 {
  max-width: 720px;
  font-size: clamp(42px, 8vw, 76px);
  line-height: 0.96;
  margin-bottom: 20px;
  letter-spacing: 0;
}

.lead {
  max-width: 560px;
  color: var(--muted);
  font-size: 18px;
  line-height: 1.7;
}

.hero-actions, .promo {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.primary, .secondary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  border-radius: 999px;
  padding: 0 18px;
  font-weight: 700;
}

.primary {
  background: var(--brand);
  color: white;
}

.secondary {
  background: white;
  border: 1px solid var(--line);
}

.hero-product {
  min-height: 390px;
  display: grid;
  place-items: center;
  border-radius: 28px;
  background:
    radial-gradient(circle at 20% 20%, rgba(37, 87, 214, 0.16), transparent 32%),
    linear-gradient(145deg, #fff6f2, #eef4ff);
  border: 1px solid var(--line);
}

.shoe-card {
  width: min(360px, 86%);
  padding: 26px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.78);
  box-shadow: 0 24px 70px rgba(28, 35, 59, 0.16);
}

.badge {
  display: inline-flex;
  padding: 6px 10px;
  border-radius: 999px;
  background: #ffefe9;
  color: var(--brand);
  font-weight: 800;
  font-size: 12px;
}

.shoe-visual {
  position: relative;
  height: 180px;
  margin: 20px 0 16px;
}

.sole, .upper, .lace {
  position: absolute;
  display: block;
}

.sole {
  left: 28px;
  right: 20px;
  bottom: 38px;
  height: 28px;
  border-radius: 999px;
  background: var(--ink);
  transform: rotate(-7deg);
}

.upper {
  left: 46px;
  right: 52px;
  bottom: 58px;
  height: 86px;
  border-radius: 80px 90px 34px 38px;
  background: linear-gradient(135deg, var(--brand), #ffb14a);
  transform: rotate(-7deg);
}

.lace {
  left: 126px;
  bottom: 103px;
  width: 100px;
  height: 9px;
  border-radius: 999px;
  background: white;
  box-shadow: 0 22px 0 rgba(255,255,255,.86);
  transform: rotate(-7deg);
}

.filters {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding: 8px 0 24px;
}

.filter {
  border: 1px solid var(--line);
  background: white;
  color: var(--muted);
  border-radius: 999px;
  padding: 10px 14px;
  font-weight: 700;
  cursor: pointer;
}

.filter.active {
  background: var(--ink);
  color: white;
  border-color: var(--ink);
}

.section-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 20px;
  margin: 20px 0;
}

.section-head h2, .promo h2 {
  font-size: clamp(28px, 4vw, 44px);
  line-height: 1.06;
  margin-bottom: 0;
}

.products {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.product {
  background: white;
  border: 1px solid var(--line);
  border-radius: 18px;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(17, 20, 28, 0.06);
}

.product-art {
  display: grid;
  place-items: center;
  min-height: 170px;
  background: linear-gradient(145deg, #fff6f2, #eef4ff);
  font-size: 58px;
}

.product-body {
  padding: 16px;
}

.product h3 {
  margin: 0 0 8px;
  font-size: 16px;
}

.meta {
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 14px;
}

.buy-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.price {
  font-weight: 800;
  flex: 1;
}

.buy {
  border: 0;
  border-radius: 999px;
  background: var(--blue);
  color: white;
  font-weight: 800;
  padding: 9px 12px;
  cursor: pointer;
}

.promo {
  justify-content: space-between;
  margin: 58px 0 18px;
  padding: clamp(22px, 4vw, 40px);
  border-radius: 24px;
  background: var(--ink);
  color: white;
}

.promo .eyebrow {
  color: #ffb14a;
}

.reviews {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin: 20px 0 54px;
}

.reviews article {
  background: white;
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 18px;
}

.reviews strong {
  display: block;
  font-size: 26px;
  margin-bottom: 6px;
}

.reviews span {
  color: var(--muted);
}

@media (max-width: 920px) {
  nav {
    display: none;
  }

  .hero {
    grid-template-columns: 1fr;
    min-height: auto;
  }

  .products {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .topbar {
    padding: 0 14px;
    gap: 10px;
  }

  .brand span:last-child {
    display: none;
  }

  .cart-button span {
    display: none;
  }

  main {
    width: min(100% - 24px, 1160px);
  }

  h1 {
    font-size: 42px;
  }

  .products, .reviews {
    grid-template-columns: 1fr;
  }

  .hero-product {
    min-height: 320px;
  }
}
"""


def _app_js(req: FrontendScaffold) -> str:
    data = _theme_data(req)
    products = json.dumps(data["products"], ensure_ascii=False, indent=2)
    add_label = json.dumps(data.get("add_label", "Tambah"), ensure_ascii=False)
    added_label = json.dumps(data.get("added_label", "Masuk"), ensure_ascii=False)
    return (
        "const products = " + products + ";\n"
        f"const addLabel = {add_label};\n"
        f"const addedLabel = {added_label};\n"
        + r"""

let activeFilter = "all";
let cart = 0;

const productsEl = document.querySelector("#products");
const cartCount = document.querySelector("#cartCount");

function renderProducts() {
  const visible = activeFilter === "all"
    ? products
    : products.filter((item) => item.type === activeFilter);

  productsEl.innerHTML = visible.map((item) => `
    <article class="product">
      <div class="product-art" aria-hidden="true">${item.mark}</div>
      <div class="product-body">
        <h3>${item.name}</h3>
        <div class="meta">${item.color} | ${item.type}</div>
        <div class="buy-row">
          <span class="price">${item.price}</span>
          <button class="buy" type="button">${addLabel}</button>
        </div>
      </div>
    </article>
  `).join("");

  document.querySelectorAll(".buy").forEach((button) => {
    button.addEventListener("click", () => {
      cart += 1;
      cartCount.textContent = cart;
      button.textContent = addedLabel;
      setTimeout(() => { button.textContent = addLabel; }, 900);
    });
  });
}

document.querySelectorAll(".filter").forEach((button) => {
  button.addEventListener("click", () => {
    activeFilter = button.dataset.filter;
    document.querySelectorAll(".filter").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    renderProducts();
  });
});

renderProducts();
"""
    )


def _readme(req: FrontendScaffold) -> str:
    data = _theme_data(req)
    return f"""# {req.title}

Frontend {data['readme_label']} statis yang dibuat oleh RelayCLI.

File utama:

- `index.html`
- `styles.css`
- `app.js`

Buka `index.html` langsung di browser untuk mencoba tampilannya.
"""
