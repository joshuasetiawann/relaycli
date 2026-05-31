const products = [
  {
    id: 1,
    name: "Sneakers Orbit Street",
    category: "Fashion",
    price: 229000,
    rating: 4.8,
    sold: 1250,
    badge: "Flash",
    image: "https://images.unsplash.com/photo-1542291026-7eec264c27ff?auto=format&fit=crop&w=700&q=80",
  },
  {
    id: 2,
    name: "Headphone Nova Bass",
    category: "Elektronik",
    price: 349000,
    rating: 4.7,
    sold: 840,
    badge: "Best",
    image: "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=700&q=80",
  },
  {
    id: 3,
    name: "Jam Tangan Metro Silver",
    category: "Aksesoris",
    price: 189000,
    rating: 4.6,
    sold: 610,
    badge: "Baru",
    image: "https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=700&q=80",
  },
  {
    id: 4,
    name: "Smartphone Runa Lite",
    category: "Elektronik",
    price: 1599000,
    rating: 4.9,
    sold: 430,
    badge: "Hot",
    image: "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=700&q=80",
  },
  {
    id: 5,
    name: "Tas Ransel Komuter",
    category: "Fashion",
    price: 275000,
    rating: 4.5,
    sold: 980,
    badge: "Promo",
    image: "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=700&q=80",
  },
  {
    id: 6,
    name: "Set Skincare Fresh Glow",
    category: "Beauty",
    price: 149000,
    rating: 4.8,
    sold: 1500,
    badge: "Diskon",
    image: "https://images.unsplash.com/photo-1556228720-195a672e8a03?auto=format&fit=crop&w=700&q=80",
  },
  {
    id: 7,
    name: "Lampu Meja Norda",
    category: "Rumah",
    price: 99000,
    rating: 4.4,
    sold: 320,
    badge: "Hemat",
    image: "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?auto=format&fit=crop&w=700&q=80",
  },
  {
    id: 8,
    name: "Keyboard Compact K82",
    category: "Elektronik",
    price: 399000,
    rating: 4.7,
    sold: 730,
    badge: "Favorit",
    image: "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=700&q=80",
  },
];

const rupiah = new Intl.NumberFormat("id-ID", {
  style: "currency",
  currency: "IDR",
  maximumFractionDigits: 0,
});

let activeCategory = "Semua";
let searchTerm = "";
let cart = [];

const productGrid = document.querySelector("#products");
const categoryTabs = document.querySelector("#categoryTabs");
const cartPanel = document.querySelector("#cartPanel");
const cartItems = document.querySelector("#cartItems");
const cartCount = document.querySelector("#cartCount");
const subtotalEl = document.querySelector("#subtotal");
const discountEl = document.querySelector("#discount");
const totalEl = document.querySelector("#total");
const scrim = document.querySelector("#scrim");

function categories() {
  return ["Semua", ...new Set(products.map((product) => product.category))];
}

function renderCategories() {
  categoryTabs.innerHTML = categories()
    .map(
      (category) => `
        <button class="${category === activeCategory ? "active" : ""}" data-category="${category}">
          ${category}
        </button>
      `,
    )
    .join("");
}

function visibleProducts() {
  return products.filter((product) => {
    const matchCategory = activeCategory === "Semua" || product.category === activeCategory;
    const haystack = `${product.name} ${product.category}`.toLowerCase();
    return matchCategory && haystack.includes(searchTerm.toLowerCase());
  });
}

function renderProducts() {
  const items = visibleProducts();
  productGrid.innerHTML = items.length
    ? items
        .map(
          (product) => `
            <article class="product-card">
              <img src="${product.image}" alt="${product.name}" loading="lazy" />
              <div class="product-body">
                <div class="badge-row">
                  <span class="badge">${product.badge}</span>
                  <span class="rating">${product.rating} rating</span>
                </div>
                <h3>${product.name}</h3>
                <div class="price">${rupiah.format(product.price)}</div>
                <div class="meta">${product.sold.toLocaleString("id-ID")} terjual</div>
                <button class="add-cart" data-id="${product.id}">Tambah ke keranjang</button>
              </div>
            </article>
          `,
        )
        .join("")
    : `<p class="cart-empty">Produk tidak ditemukan.</p>`;
}

function addToCart(id) {
  const product = products.find((item) => item.id === id);
  const existing = cart.find((item) => item.id === id);
  if (existing) {
    existing.qty += 1;
  } else {
    cart.push({ ...product, qty: 1 });
  }
  renderCart();
  openCart();
}

function updateQty(id, delta) {
  cart = cart
    .map((item) => (item.id === id ? { ...item, qty: item.qty + delta } : item))
    .filter((item) => item.qty > 0);
  renderCart();
}

function cartSubtotal() {
  return cart.reduce((sum, item) => sum + item.price * item.qty, 0);
}

function renderCart() {
  const itemCount = cart.reduce((sum, item) => sum + item.qty, 0);
  const subtotal = cartSubtotal();
  const discount = subtotal >= 500000 ? Math.round(subtotal * 0.08) : 0;

  cartCount.textContent = itemCount;
  subtotalEl.textContent = rupiah.format(subtotal);
  discountEl.textContent = `-${rupiah.format(discount)}`;
  totalEl.textContent = rupiah.format(subtotal - discount);

  cartItems.innerHTML = cart.length
    ? cart
        .map(
          (item) => `
            <div class="cart-item">
              <img src="${item.image}" alt="${item.name}" />
              <div>
                <h3>${item.name}</h3>
                <strong>${rupiah.format(item.price)}</strong>
                <div class="quantity">
                  <button data-qty="${item.id}" data-delta="-1">-</button>
                  <span>${item.qty}</span>
                  <button data-qty="${item.id}" data-delta="1">+</button>
                </div>
              </div>
            </div>
          `,
        )
        .join("")
    : `<p class="cart-empty">Keranjang masih kosong.</p>`;
}

function openCart() {
  cartPanel.classList.add("open");
  scrim.classList.add("open");
  cartPanel.setAttribute("aria-hidden", "false");
}

function closeCart() {
  cartPanel.classList.remove("open");
  scrim.classList.remove("open");
  cartPanel.setAttribute("aria-hidden", "true");
}

function tickTimer() {
  const now = new Date();
  const end = new Date();
  end.setHours(23, 59, 59, 999);
  const remaining = Math.max(0, end - now);
  const hours = String(Math.floor(remaining / 3600000)).padStart(2, "0");
  const minutes = String(Math.floor((remaining % 3600000) / 60000)).padStart(2, "0");
  const seconds = String(Math.floor((remaining % 60000) / 1000)).padStart(2, "0");
  document.querySelector("#saleTimer").textContent = `${hours}:${minutes}:${seconds}`;
}

categoryTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-category]");
  if (!button) return;
  activeCategory = button.dataset.category;
  renderCategories();
  renderProducts();
});

productGrid.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-id]");
  if (!button) return;
  addToCart(Number(button.dataset.id));
});

cartItems.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-qty]");
  if (!button) return;
  updateQty(Number(button.dataset.qty), Number(button.dataset.delta));
});

document.querySelector("#searchForm").addEventListener("submit", (event) => {
  event.preventDefault();
  searchTerm = document.querySelector("#searchInput").value.trim();
  renderProducts();
});

document.querySelector("#cartButton").addEventListener("click", openCart);
document.querySelector("#closeCart").addEventListener("click", closeCart);
scrim.addEventListener("click", closeCart);

document.querySelector("#checkoutButton").addEventListener("click", () => {
  if (!cart.length) return;
  alert("Checkout simulasi berhasil. Total: " + totalEl.textContent);
  cart = [];
  renderCart();
  closeCart();
});

renderCategories();
renderProducts();
renderCart();
tickTimer();
setInterval(tickTimer, 1000);
