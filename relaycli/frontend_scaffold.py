"""Deterministic frontend scaffolds for common small website requests.

Small local models sometimes emit fake JSON tool calls instead of actually
calling RelayCLI tools. For very common, low-risk requests such as "buat web
toko sepatu frontend di folder sepatuu", a tiny local scaffold is more helpful
and more predictable than another model round-trip.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

from relaycli.core.context import ProjectContext
from relaycli.intent import frontend_destination
from relaycli.tools.base import atomic_write


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
    wants_web = any(
        word in lowered
        for word in ("web", "website", "frontend", "front end", "halaman", "landing page")
    )
    wants_create = any(word in lowered for word in ("buat", "buatin", "buatkan", "bikin", "build"))
    if not (wants_web and wants_create):
        return None
    folder = frontend_destination(raw)
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
    mentions_mineral_water = any(
        word in lowered
        for word in ("mineral water", "air mineral", "air minum", "bottled water")
    )
    named_brand = re.search(
        r"\b(?:bernama|named)\s+[\"']?([a-z0-9][a-z0-9_-]{1,30})",
        raw,
        re.IGNORECASE,
    )
    negates_shoe = "bukan sepatu" in lowered or "bukan toko sepatu" in lowered
    negates_fashion = "bukan baju" in lowered or "bukan toko baju" in lowered
    if mentions_mandarin:
        if not folder and wants_themed_static:
            folder = "belajar-mandarin"
        if folder:
            return FrontendScaffold(folder=folder, title="MandarinLab", theme="mandarin", tone=tone)
    if mentions_mineral_water and folder:
        title = named_brand.group(1).upper() if named_brand else "AQUAVERA"
        return FrontendScaffold(folder=folder, title=title, theme="water", tone=tone)
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
        "styles.css": _styles_css(req),
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
    if req.theme == "water":
        categories = [
            ("all", "Semua"),
            ("personal", "Personal"),
            ("family", "Keluarga"),
            ("event", "Acara"),
        ]
        products = [
            {"name": "Pure 330", "type": "personal", "price": "330 ml", "color": "12 botol", "mark": "330"},
            {"name": "Daily 600", "type": "personal", "price": "600 ml", "color": "24 botol", "mark": "600"},
            {"name": "Family 1.5", "type": "family", "price": "1,5 liter", "color": "12 botol", "mark": "1.5"},
            {"name": "Home Gallon", "type": "family", "price": "19 liter", "color": "Isi ulang", "mark": "19L"},
            {"name": "Event Pack", "type": "event", "price": "48 x 220 ml", "color": "Cup", "mark": "48"},
            {"name": "Office Supply", "type": "event", "price": "Langganan", "color": "Mingguan", "mark": "PRO"},
        ]
        return {
            "mark": "A",
            "nav_primary": "Pilihan",
            "nav_secondary": "Komitmen",
            "nav_tertiary": "Kualitas",
            "cart_label": "Pesanan",
            "eyebrow": "Mineral alami · terlindungi",
            "headline": "Segar dari sumbernya, jernih sampai tetes terakhir.",
            "lead": "AQUAVERA melewati penyaringan berlapis untuk menjaga mineral alami dan rasa ringan yang menyegarkan setiap hari.",
            "primary_label": "Pilih ukuran",
            "secondary_label": "Lihat kualitas",
            "hero_aria": "Botol AQUAVERA unggulan",
            "badge": "Natural mineral",
            "featured": "AQUAVERA Pure 600",
            "featured_price": "Hidrasi praktis sepanjang hari",
            "hero_visual": '<div class="product-art hero-art" aria-hidden="true">H₂O</div>',
            "filters_aria": "Filter ukuran air mineral",
            "section_eyebrow": "Untuk setiap momen",
            "section_title": "Pilih hidrasi Anda",
            "promo_eyebrow": "Komitmen AQUAVERA",
            "promo_headline": "Botol lebih ringan, proses lebih efisien, dan kesegaran yang tetap terjaga.",
            "promo_button": "Jelajahi pilihan",
            "reviews": [
                ("7 tahap", "Penyaringan berlapis yang terkontrol"),
                ("24/7", "Pemantauan mutu dari sumber hingga botol"),
                ("100%", "Rasa segar tanpa tambahan pemanis"),
            ],
            "categories": categories,
            "products": products,
            "add_label": "Pesan",
            "added_label": "Ditambah",
            "readme_label": "kampanye air mineral",
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

def _water_index_html(req: FrontendScaffold) -> str:
    brand = html.escape(req.title)
    email_brand = re.sub(r"[^a-z0-9]", "", req.title.lower()) or "water"
    description = html.escape(
        f"{req.title}, air mineral premium dari mata air pegunungan—jernih, seimbang, "
        "dan dikemas untuk ritual hidrasi setiap hari."
    )
    return f"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{description}">
  <meta name="theme-color" content="#071214">
  <title>{brand} — Stillness, bottled.</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body class="water-page">
  <header class="water-nav" data-reveal>
    <a class="water-brand" href="#home" aria-label="{brand}, kembali ke awal">
      <svg viewBox="0 0 32 40" aria-hidden="true"><path d="M16 1C12 9 3 17 3 26a13 13 0 0 0 26 0C29 17 20 9 16 1Z"/></svg>
      <span>{brand}</span>
    </a>
    <nav aria-label="Navigasi utama"><a href="#sumber">Sumber</a><a href="#mineral">Mineral</a><a href="#ritual">Ritual</a></nav>
    <a class="nav-cta" href="#ritual">Temukan profilmu</a>
  </header>

  <main>
    <section class="water-hero" id="home" aria-labelledby="hero-title">
      <div class="hero-ambient" aria-hidden="true"></div>
      <div class="hero-copy" data-reveal>
        <p class="water-kicker"><span>Elevasi 1.420 mdpl</span><span>Jawa, Indonesia</span></p>
        <h1 id="hero-title">Hening.<br><em>Dalam setiap</em><br>tegukan.</h1>
        <p class="hero-lead">{brand} lahir dari batuan vulkanik dan waktu—disaring perlahan hingga rasa jernihnya terasa nyaris tak berbobot.</p>
        <div class="hero-actions"><a class="water-button" href="#sumber">Ikuti perjalanan air</a><span class="batch-note">Batch 08 · mineral alami</span></div>
      </div>
      <div class="bottle-stage" id="bottleStage" aria-label="Botol {brand}" data-reveal>
        <div class="orbit orbit-one" aria-hidden="true"></div><div class="orbit orbit-two" aria-hidden="true"></div>
        <div class="water-bottle" aria-hidden="true">
          <span class="bottle-cap"></span><span class="bottle-glass"></span>
          <span class="bottle-label"><small>Natural mineral water</small><strong>{brand}</strong><i>1420</i></span>
          <span class="bottle-shine"></span>
        </div>
        <p class="stage-caption"><span>01</span> Dipahat alam, bukan direkayasa.</p>
      </div>
      <a class="scroll-cue" href="#sumber"><span>Gulir untuk menyelam</span><i aria-hidden="true"></i></a>
    </section>

    <section class="source-story" id="sumber" aria-labelledby="source-title">
      <div class="section-index" data-reveal><span>01</span><p>Asal</p></div>
      <div class="source-copy" data-reveal>
        <p class="water-kicker">Perjalanan yang tidak singkat</p>
        <h2 id="source-title">Tiga puluh tahun<br>di bawah permukaan.</h2>
        <p>Hujan pegunungan meresap melewati lapisan pasir, tanah, dan batu vulkanik. Di kedalaman, ia menemukan keseimbangannya sendiri sebelum muncul kembali sebagai mata air {brand}.</p>
      </div>
      <div class="topography" aria-hidden="true" data-reveal>
        <svg viewBox="0 0 720 440"><path d="M-20 323C72 247 135 370 239 296s144-193 259-116 141 35 242-50"/><path d="M-30 354C79 269 150 400 253 320S402 112 519 204s139 38 236-62"/><path d="M-17 388C102 291 160 427 270 344S421 142 546 231s132 29 225-48"/><path d="M28 267c86-63 143 42 225-24s123-151 216-86 126 28 214-43"/><circle cx="493" cy="183" r="7"/><circle cx="493" cy="183" r="19"/></svg>
        <div class="spring-pin"><span>Mata air</span><strong>07°24' S</strong></div>
      </div>
      <dl class="source-facts" data-reveal><div><dt>1.420</dt><dd>meter elevasi</dd></div><div><dt>12.8°</dt><dd>suhu alami</dd></div><div><dt>30 th</dt><dd>filtrasi geologis</dd></div></dl>
    </section>

    <section class="mineral-composition" id="mineral" aria-labelledby="mineral-title">
      <div class="section-index" data-reveal><span>02</span><p>Komposisi</p></div>
      <div class="mineral-heading" data-reveal><p class="water-kicker">Tidak ditambah. Tidak dikurangi.</p><h2 id="mineral-title">Keseimbangan<br>yang terasa ringan.</h2></div>
      <div class="mineral-rail" data-reveal>
        <article><span>Ca</span><strong>32</strong><small>mg/L · Kalsium</small></article>
        <article><span>Mg</span><strong>08</strong><small>mg/L · Magnesium</small></article>
        <article><span>pH</span><strong>7.4</strong><small>Seimbang alami</small></article>
        <article><span>TDS</span><strong>118</strong><small>mg/L · mineral total</small></article>
      </div>
      <blockquote data-reveal>“Rasa terbaik adalah rasa yang tidak perlu menjelaskan dirinya.”</blockquote>
    </section>

    <section class="water-ritual" id="ritual" aria-labelledby="ritual-title">
      <div class="ritual-copy" data-reveal>
        <div class="section-index"><span>03</span><p>Ritual</p></div><p class="water-kicker">Atur momenmu</p>
        <h2 id="ritual-title">Bagaimana kamu ingin merasa hari ini?</h2>
        <p>Pilih profil. Kami akan menyarankan cara menikmati {brand}—dari suhu, waktu, sampai ritme tegukannya.</p>
        <div class="profile-tabs" role="group" aria-label="Pilih profil hidrasi">
          <button class="profile-tab is-active" type="button" data-profile="jernih" aria-pressed="true">Jernih</button>
          <button class="profile-tab" type="button" data-profile="fokus" aria-pressed="false">Fokus</button>
          <button class="profile-tab" type="button" data-profile="pulih" aria-pressed="false">Pulih</button>
        </div>
      </div>
      <div class="profile-display" data-reveal aria-live="polite">
        <p>Profil hidrasi</p><strong id="profileValue">12.8°C</strong><div class="profile-meter"><span id="profileMeter"></span></div>
        <h3 id="profileTitle">Jernih di pagi hari</h3><p id="profileCopy">Nikmati perlahan sebelum rutinitas dimulai. Suhu alami menjaga karakter mineralnya tetap lembut.</p>
      </div>
      <div class="final-call" data-reveal><p>Air tidak perlu lebih ramai.<br>Ia hanya perlu lebih jujur.</p><a class="water-button water-button-light" href="mailto:hello@{email_brand}.water">Bawa {brand} ke mejamu</a></div>
    </section>
  </main>

  <footer class="water-footer"><a class="water-brand" href="#home"><span>{brand}</span></a><p>Natural mineral water · Indonesia</p><p>© <span id="year"></span> {brand}</p></footer>
  <script src="app.js"></script>
</body>
</html>
"""


def _index_html(req: FrontendScaffold) -> str:
    if req.theme == "water":
        return _water_index_html(req)
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
    description = html.escape(f"{req.title}. {data['lead']}")
    return f"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{description}">
  <meta name="theme-color" content="#111217">
  <title>{req.title}</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body class="theme-{req.tone} layout-{req.theme}">
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

  <footer class="site-footer">
    <a class="brand" href="#home"><span class="brand-mark">{data["mark"]}</span><span>{req.title}</span></a>
    <p>{data["readme_label"]} · dibuat ringan, responsif, dan siap digunakan.</p>
    <p>© <span id="year"></span> {req.title}</p>
  </footer>

  <script src="app.js"></script>
</body>
</html>
"""


def _water_styles_css() -> str:
    return r"""*,*::before,*::after{box-sizing:border-box}
:root{--night:#071214;--deep:#0b1c1f;--ink:#eaf4f1;--mist:#a6b9b5;--line:rgba(205,231,224,.18);--aqua:#83e8d1;--glow:#c7fff1;--display:Georgia,"Times New Roman",serif;--sans:"Segoe UI Variable","Aptos",system-ui,sans-serif}
html{scroll-behavior:smooth;background:var(--night)}body{margin:0;overflow-x:hidden;background:var(--night);color:var(--ink);font-family:var(--sans)}a{color:inherit;text-decoration:none}button{font:inherit}
.water-nav{position:absolute;inset:0 0 auto;z-index:20;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:2rem;padding:1.4rem clamp(1.25rem,4vw,4.5rem);border-bottom:1px solid var(--line)}
.water-brand{display:inline-flex;align-items:center;gap:.7rem;width:max-content;font-size:.78rem;font-weight:700;letter-spacing:.22em}.water-brand svg{width:18px;height:23px;fill:none;stroke:var(--aqua);stroke-width:1.4}
.water-nav nav{display:flex;gap:clamp(1rem,3vw,2.6rem);color:var(--mist);font-size:.72rem;letter-spacing:.12em;text-transform:uppercase}.water-nav nav a,.nav-cta{transition:color .25s ease,opacity .25s ease}.water-nav nav a:hover,.water-nav nav a:focus-visible{color:var(--ink)}.nav-cta{justify-self:end;padding-bottom:.35rem;border-bottom:1px solid var(--aqua);color:var(--glow);font-size:.72rem}
.water-hero{position:relative;min-height:100svh;display:grid;grid-template-columns:minmax(0,1.1fr) minmax(360px,.9fr);align-items:center;padding:9rem clamp(1.25rem,6vw,7.5rem) 5rem;isolation:isolate}.hero-ambient{position:absolute;inset:0;z-index:-2;background:radial-gradient(circle at 75% 38%,rgba(77,199,177,.15),transparent 25%),linear-gradient(115deg,#071214 8%,#0b1a1d 55%,#061012)}
.hero-ambient::after{content:"";position:absolute;inset:0;opacity:.08;background-image:linear-gradient(rgba(255,255,255,.12) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.08) 1px,transparent 1px);background-size:72px 72px;mask-image:linear-gradient(to right,transparent,#000 45%,#000)}.hero-copy{position:relative;z-index:2;width:100%;min-width:0;max-width:760px}
.water-kicker{display:flex;flex-wrap:wrap;gap:1rem;margin:0 0 1.8rem;color:var(--aqua);font-size:.68rem;font-weight:700;letter-spacing:.18em;text-transform:uppercase}.water-kicker span+span::before{content:"·";margin-right:1rem;color:var(--mist)}h1,h2,h3,p{margin-top:0}h1,h2{font-family:var(--display);font-weight:400;letter-spacing:-.045em}h1{margin-bottom:2rem;font-size:clamp(4.6rem,9vw,10rem);line-height:.76}h1 em{color:var(--mist);font-weight:400}.hero-lead{max-width:560px;color:var(--mist);font-size:clamp(1rem,1.35vw,1.22rem);line-height:1.75}
.hero-actions{display:flex;align-items:center;flex-wrap:wrap;gap:1.4rem;margin-top:2.4rem}.water-button{display:inline-flex;align-items:center;justify-content:center;min-height:50px;padding:0 1.4rem;border:1px solid var(--aqua);color:var(--glow);font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;transition:color .3s ease,background .3s ease,transform .3s ease}.water-button:hover,.water-button:focus-visible{background:var(--aqua);color:var(--night);transform:translateY(-2px)}.batch-note{color:var(--mist);font-size:.7rem;letter-spacing:.13em;text-transform:uppercase}
.bottle-stage{position:relative;min-height:680px;display:grid;place-items:center;perspective:1000px;--rx:0deg;--ry:0deg}.water-bottle{position:relative;z-index:3;width:190px;height:515px;border:1px solid rgba(224,255,247,.4);border-radius:62px 62px 42px 42px;background:linear-gradient(100deg,rgba(255,255,255,.04),rgba(189,255,239,.16) 42%,rgba(255,255,255,.025) 74%);box-shadow:inset 16px 0 35px rgba(222,255,248,.08),inset -20px 0 35px rgba(0,0,0,.25),0 45px 90px rgba(0,0,0,.45);transform:rotateX(var(--rx)) rotateY(var(--ry));transition:transform .18s ease-out;animation:bottle-float 6s ease-in-out infinite}
.bottle-cap{position:absolute;left:50%;top:-48px;width:76px;height:61px;transform:translateX(-50%);border-radius:12px 12px 8px 8px;background:repeating-linear-gradient(90deg,#9ecdc3 0 3px,#6fa59a 3px 5px);box-shadow:0 5px 10px rgba(0,0,0,.3)}.bottle-cap::after{content:"";position:absolute;left:-7px;right:-7px;bottom:-8px;height:19px;border-radius:7px;background:#8abbb0}.bottle-glass{position:absolute;inset:18px 13px 15px;border-radius:50px 50px 34px 34px;border:1px solid rgba(255,255,255,.1);background:linear-gradient(to top,rgba(77,190,170,.22) 0 58%,transparent 58%)}
.bottle-label{position:absolute;z-index:2;left:-2px;right:-2px;top:190px;min-height:150px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.65rem;padding:1rem;color:#08201f;background:rgba(215,242,235,.9);text-align:center}.bottle-label small{font-size:.5rem;letter-spacing:.2em;text-transform:uppercase}.bottle-label strong{font-family:var(--display);font-size:1.85rem;font-weight:400;letter-spacing:.16em}.bottle-label i{font-style:normal;font-size:.58rem;letter-spacing:.2em}.bottle-shine{position:absolute;z-index:4;top:35px;bottom:45px;left:22px;width:8px;border-radius:99px;background:linear-gradient(transparent,rgba(255,255,255,.7),transparent);opacity:.42}
.orbit{position:absolute;z-index:1;border:1px solid rgba(131,232,209,.22);border-radius:50%;animation:orbit-pulse 6s ease-in-out infinite}.orbit-one{width:min(44vw,560px);aspect-ratio:1}.orbit-two{width:min(33vw,410px);aspect-ratio:1;animation-delay:-2s}.stage-caption{position:absolute;right:0;bottom:2rem;display:flex;gap:1rem;max-width:190px;color:var(--mist);font-size:.68rem;line-height:1.5;text-transform:uppercase;letter-spacing:.1em}.stage-caption span{color:var(--aqua)}.scroll-cue{position:absolute;left:clamp(1.25rem,6vw,7.5rem);bottom:2.2rem;display:flex;align-items:center;gap:.9rem;color:var(--mist);font-size:.62rem;letter-spacing:.14em;text-transform:uppercase}.scroll-cue i{width:44px;height:1px;background:var(--aqua);transform-origin:left;animation:cue 2s ease-in-out infinite}
.source-story,.mineral-composition{position:relative;display:grid;grid-template-columns:minmax(80px,.3fr) 1fr 1.15fr;gap:clamp(2rem,5vw,6rem);padding:clamp(6rem,11vw,11rem) clamp(1.25rem,6vw,7.5rem);border-top:1px solid var(--line)}.source-story{background:#dce9e4;color:#0b1b1d}.section-index{display:flex;gap:.7rem;align-items:baseline;height:max-content;font-size:.65rem;letter-spacing:.15em;text-transform:uppercase}.section-index span{color:#4c847b}.section-index p{margin:0}.source-copy h2,.mineral-heading h2,.ritual-copy h2{margin-bottom:2.3rem;font-size:clamp(3.3rem,6.5vw,7.5rem);line-height:.88}.source-copy>p:last-child{max-width:540px;color:#4b6462;line-height:1.8}
.topography{position:relative;align-self:center}.topography svg{width:100%;overflow:visible}.topography path,.topography circle{fill:none;stroke:rgba(13,71,65,.28);stroke-width:1}.topography circle:last-of-type{stroke-dasharray:3 4;animation:spin 12s linear infinite;transform-origin:493px 183px}.spring-pin{position:absolute;left:68%;top:36%;display:flex;flex-direction:column;gap:.3rem;color:#244f4a;font-size:.62rem;letter-spacing:.12em;text-transform:uppercase}.spring-pin strong{color:#0b1b1d}.source-facts{grid-column:2/-1;display:grid;grid-template-columns:repeat(3,1fr);margin:2rem 0 0;padding-top:2rem;border-top:1px solid rgba(11,27,29,.2)}.source-facts div{padding-right:2rem}.source-facts dt{font-family:var(--display);font-size:clamp(2.5rem,5vw,5rem)}.source-facts dd{margin:.4rem 0 0;color:#58716e;font-size:.68rem;letter-spacing:.13em;text-transform:uppercase}
.mineral-composition{grid-template-columns:minmax(80px,.3fr) 1fr 1fr;background:var(--deep)}.mineral-heading{grid-column:2/-1}.mineral-heading .water-kicker{margin-bottom:1.4rem}.mineral-rail{grid-column:2/-1;display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.mineral-rail article{min-height:260px;display:flex;flex-direction:column;justify-content:space-between;padding:1.5rem 1.2rem;border-right:1px solid var(--line)}.mineral-rail article:last-child{border-right:0}.mineral-rail span{color:var(--aqua);font-size:.75rem}.mineral-rail strong{font-family:var(--display);font-size:clamp(3rem,5vw,5.4rem);font-weight:400}.mineral-rail small{color:var(--mist);font-size:.65rem;letter-spacing:.1em;text-transform:uppercase}.mineral-composition blockquote{grid-column:2/-1;max-width:920px;margin:4rem 0 0;color:var(--mist);font-family:var(--display);font-size:clamp(2rem,4vw,4.5rem);line-height:1.12}
.water-ritual{display:grid;grid-template-columns:1.15fr .85fr;background:#a8d2c8;color:#0a1b1b}.ritual-copy,.profile-display{padding:clamp(4rem,9vw,9rem) clamp(1.25rem,6vw,7rem)}.ritual-copy{border-right:1px solid rgba(7,18,20,.2)}.ritual-copy .section-index{margin-bottom:5rem}.ritual-copy .water-kicker{color:#285f57}.ritual-copy>p:not(.water-kicker){max-width:570px;color:#385c58;line-height:1.8}.profile-tabs{display:flex;flex-wrap:wrap;gap:.7rem;margin-top:2.2rem}.profile-tab{min-width:96px;padding:.8rem 1.1rem;border:1px solid rgba(7,18,20,.32);border-radius:99px;background:transparent;color:#153a36;cursor:pointer;transition:background .25s ease,color .25s ease,transform .25s ease}.profile-tab:hover,.profile-tab:focus-visible,.profile-tab.is-active{background:#0b2323;color:#d9fff5;transform:translateY(-2px)}
.profile-display{align-self:stretch;display:flex;flex-direction:column;justify-content:center;background:#0b2323;color:var(--ink);transition:background .4s ease}.profile-display>p:first-child{color:var(--aqua);font-size:.68rem;letter-spacing:.16em;text-transform:uppercase}.profile-display>strong{font-family:var(--display);font-size:clamp(4.5rem,8vw,8rem);font-weight:400;line-height:1}.profile-meter{height:2px;margin:2.2rem 0;overflow:hidden;background:rgba(255,255,255,.16)}.profile-meter span{display:block;width:38%;height:100%;background:var(--aqua);transition:width .55s cubic-bezier(.2,.8,.2,1)}.profile-display h3{margin-bottom:.8rem;font-family:var(--display);font-size:clamp(1.8rem,3vw,3rem);font-weight:400}.profile-display>p:last-child{max-width:460px;color:var(--mist);line-height:1.75}
.final-call{grid-column:1/-1;display:flex;justify-content:space-between;align-items:end;gap:2rem;padding:clamp(4rem,8vw,8rem) clamp(1.25rem,6vw,7rem);background:#071214;color:var(--ink)}.final-call p{margin:0;font-family:var(--display);font-size:clamp(2.6rem,5vw,6rem);line-height:.95}.water-button-light{flex:0 0 auto}.water-footer{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:1rem;padding:2rem clamp(1.25rem,6vw,7rem);border-top:1px solid var(--line);color:var(--mist);font-size:.65rem;letter-spacing:.1em;text-transform:uppercase}.water-footer p{margin:0}.water-footer p:last-child{justify-self:end}
[data-reveal]{opacity:0;transform:translateY(24px);transition:opacity .8s ease,transform .8s cubic-bezier(.2,.8,.2,1)}[data-reveal].is-visible{opacity:1;transform:none}@keyframes bottle-float{0%,100%{translate:0 0}50%{translate:0 -16px}}@keyframes orbit-pulse{0%,100%{scale:.96;opacity:.45}50%{scale:1.04;opacity:.8}}@keyframes cue{0%,100%{scale:.45 1;opacity:.5}50%{scale:1 1;opacity:1}}@keyframes spin{to{rotate:360deg}}
@media(max-width:900px){.water-nav{grid-template-columns:1fr auto}.water-nav nav{display:none}.water-hero{grid-template-columns:minmax(0,1fr);padding-top:9rem}.hero-copy,.bottle-stage{min-width:0}.bottle-stage{min-height:620px}.source-story,.mineral-composition{grid-template-columns:1fr}.source-copy,.mineral-heading,.mineral-rail,.mineral-composition blockquote,.source-facts{grid-column:1}.section-index{margin-bottom:1rem}.water-ritual{grid-template-columns:1fr}.ritual-copy{border-right:0;border-bottom:1px solid rgba(7,18,20,.2)}.water-footer{grid-template-columns:1fr 1fr}.water-footer p:nth-child(2){display:none}}
@media(max-width:620px){.nav-cta{font-size:0}.nav-cta::after{content:"Profil";font-size:.7rem}h1{font-size:clamp(4rem,22vw,6.3rem)}.hero-copy{width:100%;max-width:calc(100vw - 2.5rem)}.hero-lead{width:100%;max-width:100%;overflow-wrap:anywhere}.hero-actions{align-items:flex-start;flex-direction:column}.batch-note{max-width:100%;white-space:normal}.bottle-stage{width:100%;max-width:calc(100vw - 2.5rem);min-height:540px}.water-bottle{scale:.82}.orbit-one{width:92vw}.orbit-two{width:72vw}.stage-caption{right:1rem}.source-facts{grid-template-columns:1fr;gap:2rem}.mineral-rail{grid-template-columns:repeat(2,1fr)}.mineral-rail article:nth-child(2){border-right:0}.mineral-rail article{min-height:210px;border-bottom:1px solid var(--line)}.ritual-copy,.profile-display{padding:5rem 1.25rem}.ritual-copy .section-index{margin-bottom:3rem}.final-call{align-items:stretch;flex-direction:column}.water-button-light{width:100%}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}[data-reveal]{opacity:1;transform:none}}
"""


def _styles_css(req: FrontendScaffold) -> str:
    if req.theme == "water":
        return _water_styles_css()
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

.hero-product, .product, .primary, .secondary, .buy, .filter {
  transition: transform .28s ease, border-color .28s ease, background-color .28s ease, color .28s ease;
}
.hero-product:hover { transform: translateY(-4px); }
.product:hover { transform: translateY(-6px); }
.primary:hover, .secondary:hover, .buy:hover, .filter:hover { transform: translateY(-2px); }
.site-footer {
  width: min(1280px, calc(100% - 40px));
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 24px;
  margin: 0 auto;
  padding: 36px 0;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
}
.site-footer p { margin: 0; }
.site-footer p:last-child { justify-self: end; }

/* Domain-specific compositions. These are deliberately structural—not merely
   palette swaps—so unrelated briefs do not collapse into one storefront. */
.layout-fashion {
  --soft: #eee9df;
  --paper: #f8f4eb;
  --ink: #191817;
  --muted: #6d675f;
  --line: #cfc7b8;
  --brand: #8f372d;
  font-family: "Aptos", "Segoe UI", sans-serif;
}
.layout-fashion.theme-dark {
  --soft: #11100f;
  --paper: #1c1a18;
  --ink: #f1eadf;
  --muted: #aaa196;
  --line: #403b35;
  --brand: #e26855;
}
.layout-fashion main { width: min(1380px, calc(100% - 48px)); }
.layout-fashion .hero { grid-template-columns: minmax(310px, .82fr) minmax(0, 1.18fr); gap: 0; min-height: 780px; }
.layout-fashion .hero-product { order: -1; min-height: 680px; border-radius: 0; border: 0; background: #292326; }
.layout-fashion .shoe-card { width: 100%; height: 100%; display: flex; flex-direction: column; justify-content: flex-end; border-radius: 0; box-shadow: none; background: transparent; color: #f8f4eb; }
.layout-fashion .hero-art { min-height: 420px; background: transparent; color: #d8baa4; font-family: Georgia, serif; font-size: clamp(9rem, 18vw, 17rem); letter-spacing: -.09em; }
.layout-fashion .hero-copy { padding: clamp(3rem, 7vw, 8rem); }
.layout-fashion h1 { font-family: Georgia, serif; font-weight: 400; font-size: clamp(4rem, 7vw, 7.5rem); line-height: .88; letter-spacing: -.055em; }
.layout-fashion .products { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; background: var(--line); }
.layout-fashion .product { border: 0; border-radius: 0; box-shadow: none; }
.layout-fashion .product-art { min-height: 260px; background: var(--paper); color: var(--brand); font-family: Georgia, serif; font-size: 4.5rem; }

.layout-shoe {
  --soft: #0a0a09;
  --paper: #151613;
  --ink: #f4f6ed;
  --muted: #a7aa9d;
  --line: #34362f;
  --brand: #d7ff36;
  --blue: #d7ff36;
}
.layout-shoe .topbar { background: rgba(10, 10, 9, .92); }
.layout-shoe main { width: min(1320px, calc(100% - 40px)); }
.layout-shoe .hero { grid-template-columns: minmax(0, 1.18fr) minmax(300px, .82fr); min-height: 720px; }
.layout-shoe .hero-product { order: -1; min-height: 600px; border: 0; border-radius: 0; background: #d7ff36; clip-path: polygon(0 8%, 100% 0, 92% 92%, 7% 100%); }
.layout-shoe .shoe-card { width: min(500px, 86%); background: #f4f6ed; color: #10110e; border-radius: 2px; box-shadow: 24px 28px 0 #22251d; transform: rotate(-3deg); }
.layout-shoe h1 { font-size: clamp(4.4rem, 8vw, 8.5rem); line-height: .82; text-transform: uppercase; letter-spacing: -.065em; }
.layout-shoe .eyebrow { color: var(--brand); letter-spacing: .18em; }
.layout-shoe .products { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.layout-shoe .product { display: grid; grid-template-columns: .9fr 1.1fr; min-height: 240px; border-radius: 2px; box-shadow: none; }
.layout-shoe .product-art { min-height: 100%; background: #20221d; color: var(--brand); font-size: 4rem; }
.layout-shoe .buy { color: #10110e; }

.layout-mandarin {
  --soft: #f1eadf;
  --paper: #fffaf1;
  --ink: #251d18;
  --muted: #70655d;
  --line: #d9cbbb;
  --brand: #ad2e24;
  --blue: #ad2e24;
}
.layout-mandarin main { width: min(1240px, calc(100% - 40px)); }
.layout-mandarin .topbar { border-bottom: 2px solid var(--ink); }
.layout-mandarin .hero { grid-template-columns: minmax(0, 1.25fr) minmax(300px, .75fr); gap: 0; min-height: 660px; border-left: 1px solid var(--line); border-right: 1px solid var(--line); }
.layout-mandarin .hero-copy { padding: clamp(2.5rem, 6vw, 6rem); }
.layout-mandarin h1 { max-width: 820px; font-family: Georgia, "Times New Roman", serif; font-size: clamp(3.7rem, 6.5vw, 6.8rem); font-weight: 400; line-height: .96; letter-spacing: -.045em; }
.layout-mandarin .hero-product { align-self: stretch; border: 0; border-left: 1px solid var(--line); border-radius: 0; background: var(--brand); }
.layout-mandarin .shoe-card { width: 82%; border-radius: 0; box-shadow: none; background: var(--paper); }
.layout-mandarin .hero-art { min-height: 260px; background: var(--paper); color: var(--brand); font-family: "Microsoft YaHei", serif; font-size: clamp(5rem, 10vw, 9rem); }
.layout-mandarin .products { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.layout-mandarin .product { display: grid; grid-template-columns: 140px 1fr; min-height: 180px; border-radius: 0; box-shadow: none; }
.layout-mandarin .product-art { min-height: 100%; border-right: 1px solid var(--line); background: #eadbc9; color: var(--brand); font-family: "Microsoft YaHei", serif; }

.layout-store main { width: min(1280px, calc(100% - 40px)); }
.layout-store .hero { display: block; min-height: 610px; padding: clamp(4rem, 8vw, 8rem); position: relative; overflow: hidden; background: var(--paper); border: 1px solid var(--line); }
.layout-store .hero-copy { position: relative; z-index: 2; width: 62%; }
.layout-store .hero-product { position: absolute; z-index: 1; right: 4%; top: 9%; width: 38%; min-height: 82%; border-radius: 50% 50% 16px 16px; }
.layout-store .products { grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; }
.layout-store .product { grid-column: span 4; border-radius: 10px; }
.layout-store .product:nth-child(1), .layout-store .product:nth-child(5) { grid-column: span 8; display: grid; grid-template-columns: 1fr 1fr; }
.layout-store .product:nth-child(1) .product-art, .layout-store .product:nth-child(5) .product-art { min-height: 260px; }

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
  .site-footer { grid-template-columns: 1fr; width: min(100% - 24px, 1280px); }
  .site-footer p:last-child { justify-self: start; }
}

@media (max-width: 920px) {
  .layout-fashion .hero, .layout-shoe .hero, .layout-mandarin .hero { grid-template-columns: 1fr; min-height: auto; }
  .layout-fashion .hero-product, .layout-shoe .hero-product { min-height: 480px; }
  .layout-mandarin .hero-product { border-left: 0; border-top: 1px solid var(--line); }
  .layout-fashion .products, .layout-shoe .products, .layout-mandarin .products { grid-template-columns: 1fr 1fr; }
  .layout-store .hero { padding: 3rem 2rem 28rem; }
  .layout-store .hero-copy { width: 100%; }
  .layout-store .hero-product { inset: auto 2rem 2rem; width: calc(100% - 4rem); min-height: 24rem; }
  .layout-store .product, .layout-store .product:nth-child(1), .layout-store .product:nth-child(5) { grid-column: span 6; display: block; }
}

@media (max-width: 560px) {
  .layout-fashion main, .layout-shoe main, .layout-mandarin main, .layout-store main { width: min(100% - 24px, 1380px); }
  .layout-fashion .hero-copy, .layout-mandarin .hero-copy { padding: 3rem 1.25rem; }
  .layout-fashion h1, .layout-shoe h1, .layout-mandarin h1 { font-size: clamp(3.2rem, 15vw, 5rem); }
  .layout-fashion .products, .layout-shoe .products, .layout-mandarin .products { grid-template-columns: 1fr; }
  .layout-shoe .product, .layout-mandarin .product { grid-template-columns: 110px 1fr; }
  .layout-store .hero { padding: 3rem 1.25rem 24rem; }
  .layout-store .hero-product { inset: auto 1.25rem 1.25rem; width: calc(100% - 2.5rem); min-height: 20rem; }
  .layout-store .product, .layout-store .product:nth-child(1), .layout-store .product:nth-child(5) { grid-column: 1 / -1; }
}

:focus-visible { outline: 2px solid currentColor; outline-offset: 3px; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; }
}
"""


def _water_app_js() -> str:
    return r"""const profiles={jernih:{value:"12.8°C",meter:"38%",title:"Jernih di pagi hari",copy:"Nikmati perlahan sebelum rutinitas dimulai. Suhu alami menjaga karakter mineralnya tetap lembut.",color:"#0b2323"},fokus:{value:"320 ml",meter:"66%",title:"Fokus tanpa jeda",copy:"Tiga teguk setiap dua puluh menit membantu menjaga ritme saat pikiran bekerja paling dalam.",color:"#102b2d"},pulih:{value:"08 mg",meter:"84%",title:"Pulih setelah bergerak",copy:"Sajikan dingin setelah aktivitas. Magnesium alaminya menemani tubuh kembali ke tempo yang tenang.",color:"#163538"}};
const value=document.querySelector("#profileValue");const meter=document.querySelector("#profileMeter");const title=document.querySelector("#profileTitle");const copy=document.querySelector("#profileCopy");const display=document.querySelector(".profile-display");
document.querySelectorAll(".profile-tab").forEach(button=>{button.addEventListener("click",()=>{const profile=profiles[button.dataset.profile];document.querySelectorAll(".profile-tab").forEach(item=>{const active=item===button;item.classList.toggle("is-active",active);item.setAttribute("aria-pressed",String(active))});value.textContent=profile.value;meter.style.width=profile.meter;title.textContent=profile.title;copy.textContent=profile.copy;display.style.backgroundColor=profile.color})});
const reduceMotion=window.matchMedia("(prefers-reduced-motion: reduce)").matches;const revealItems=document.querySelectorAll("[data-reveal]");if(reduceMotion||!("IntersectionObserver" in window)){revealItems.forEach(item=>item.classList.add("is-visible"))}else{const observer=new IntersectionObserver(entries=>{entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add("is-visible");observer.unobserve(entry.target)}})},{threshold:.14});revealItems.forEach(item=>observer.observe(item))}
const stage=document.querySelector("#bottleStage");if(stage&&!reduceMotion){stage.addEventListener("pointermove",event=>{const bounds=stage.getBoundingClientRect();const x=(event.clientX-bounds.left)/bounds.width-.5;const y=(event.clientY-bounds.top)/bounds.height-.5;stage.style.setProperty("--ry",`${x*8}deg`);stage.style.setProperty("--rx",`${y*-6}deg`)});stage.addEventListener("pointerleave",()=>{stage.style.setProperty("--ry","0deg");stage.style.setProperty("--rx","0deg")})}
document.querySelector("#year").textContent=new Date().getFullYear();
"""


def _app_js(req: FrontendScaffold) -> str:
    if req.theme == "water":
        return _water_app_js()
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
document.querySelector("#year").textContent = new Date().getFullYear();

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
