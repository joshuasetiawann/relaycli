// Applied first, synchronously, before any render*() call reads it
// (renderSettings() does, to set the theme toggle's initial position) —
// unlike the accent restore at the end of this script, nothing here can
// safely wait until the script finishes running.
document.documentElement.dataset.theme = localStorage.theme ||
  (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
const $ = (id) => document.getElementById(id);
const DESC = {
  explorer: "scouts the codebase", planner: "decomposes the goal",
  coder: "writes and edits code", tester: "runs the verification step",
  reviewer: "reviews the result", agent: "single-agent run",
};
const GLYPH = { explorer: "◎", planner: "≣", coder: "⟨⟩", tester: "✓", reviewer: "⌕", agent: "❯" };
// SLATE INSTRUMENT accent/success/warning/waiting/danger — the same 5
// hues used everywhere else, so picking "the green swatch" here matches
// the green used for state elsewhere instead of a mismatched leftover.
const ACCENTS = ["#5FA8DC", "#6FB77F", "#D6A05C", "#8E93CC", "#D4695C"];
let since = 0, mode = "auto-edit", busy = false, state = null;
const activity = {}, status = {}, steps = {};
// One entry per task-graph node (--experimental-parallel), keyed by task_id —
// separate from status{}/activity{} above, which are keyed by role name and
// shaped for the OLD linear relay/single-agent rail.
const taskLanes = {};
	let runStart = 0, elapsedTimer = null;
	let activityTimer = null;
	let modelFilter = "";
	let selectedProvider = "";
	let cmdIndex = 0, cmdMatches = [];
	let activitySeq = 0;
	const activityFeed = [], activeToolByAgent = {}, activeModelByAgent = {};
	function stepsOf(name) { return steps[name] || 0; }

	function setPanelCollapsed(which, collapsed) {
	  localStorage[which + "Collapsed"] = collapsed ? "1" : "0";
	  applyPanelLayout();
	}
	function applyPanelLayout() {
	  const left = localStorage.leftCollapsed === "1";
	  const right = localStorage.rightCollapsed === "1";
	  $("app").classList.toggle("left-collapsed", left);
	  $("app").classList.toggle("right-collapsed", right);
	  $("chatShow").classList.toggle("hidden", !left);
	  $("sideShow").classList.toggle("hidden", !right);
	}

// textContent->innerHTML escapes & < > but NOT quotes, and esc() output is
// interpolated into double-quoted HTML attributes throughout this file
// (title="${esc(...)}", data-role="${esc(...)}", value="${esc(...)}" —
// config-derived strings like roster role ids or MCP command lines flow
// through these). Escape quotes too so a value can never break out of an
// attribute context.
function esc(s) {
  const d = document.createElement("div"); d.textContent = s == null ? "" : s;
  return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
async function api(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) });
  return r;
}

/* ---- top-bar menus ---- */
	function closeMenus(except) {
	  if (except !== "project") $("projectMenu").classList.add("hidden");
	  if (except !== "model") $("modelMenu").classList.add("hidden");
	  if (except !== "config") $("configMenu").classList.add("hidden");
	  if (except !== "settings") $("settingsMenu").classList.add("hidden");
	}
function toggleMenu(id, tag) {
  const m = $(id); const h = m.classList.contains("hidden");
  closeMenus(tag); m.classList.toggle("hidden", !h);
}
	$("projectBtn").onclick = (e) => { e.stopPropagation(); toggleMenu("projectMenu", "project"); $("projectPath").focus(); };
$("modelBtn").onclick = (e) => { e.stopPropagation(); toggleMenu("modelMenu", "model"); };
$("configBtn").onclick = (e) => { e.stopPropagation(); toggleMenu("configMenu", "config"); };
$("gearBtn").onclick = (e) => { e.stopPropagation(); toggleMenu("settingsMenu", "settings"); };
	// clicks inside a menu must not bubble to the document close-handler
	for (const id of ["projectMenu", "modelMenu", "configMenu", "settingsMenu"])
	  $(id).addEventListener("click", (e) => e.stopPropagation());
	document.addEventListener("click", () => closeMenus());

	async function setProjectPath() {
	  const path = $("projectPath").value.trim();
	  if (!path) return;
	  const r = await api("/api/project", { path });
	  let body = {};
	  try { body = await r.json(); } catch (e) {}
	  if (r.ok) {
	    closeMenus(); await loadState();
	    addLog("project", "cwd → " + (body.path || path), "ok");
	  } else {
	    addLog("project", body.error || "could not change directory", "bad");
	  }
	}
	$("projectSet").onclick = setProjectPath;
	$("projectPath").addEventListener("keydown", (e) => { if (e.key === "Enter") setProjectPath(); });

// Provider quick-picks for the free-form model field. No maximum: type any
// LiteLLM id; the chip just prefills a starter prefix.
const PROVIDERS = [
  ["openai", "gpt-4o"], ["anthropic", "claude-3-5-sonnet-latest"],
  ["gemini", "gemini/"], ["deepseek", "deepseek/"], ["qwen", "dashscope/qwen-"],
  ["glm", "zhipu/glm-"], ["groq", "groq/"], ["mistral", "mistral/"],
  ["openrouter", "openrouter/"], ["ollama", "ollama_chat/"],
];
function renderProvChips() {
  const c = $("provChips");
  c.innerHTML = PROVIDERS.map(([p], i) => `<button class="chip2" data-i="${i}">${p}</button>`).join("");
  for (const b of c.children) b.onclick = () => {
    const tmpl = PROVIDERS[+b.dataset.i][1];
    const inp = $("customModel"); inp.value = tmpl; inp.focus();
    inp.setSelectionRange(tmpl.length, tmpl.length);
  };
}
async function setCustom() {
  const v = $("customModel").value.trim(); if (!v) return;
  await api("/api/model", { model: v });
  $("customModel").value = ""; closeMenus(); await loadState();
  addLog("model", "switched to " + v);
}
$("customSet").onclick = setCustom;
$("customModel").addEventListener("keydown", (e) => { if (e.key === "Enter") setCustom(); });
$("modelQ").addEventListener("input", (e) => { modelFilter = e.target.value.trim().toLowerCase(); renderModels(); });
$("modelAdvancedToggle").onclick = () => {
  const adv = $("modelAdvanced");
  const collapsed = !adv.classList.contains("collapsed");
  adv.classList.toggle("collapsed", collapsed);
  $("modelAdvancedToggle").textContent = collapsed ? "Advanced" : "Hide";
};
async function pullOllama() {
  const inp = $("ollamaModel");
  const model = inp.value.trim(); if (!model) return;
  const r = await api("/api/ollama/pull", { model });
  let body = {};
  try { body = await r.json(); } catch (e) {}
  if (r.ok) {
    inp.value = "";
    addLog("ollama", "pull started: " + (body.model || model), "warn");
  } else {
    addLog("ollama", body.error || "pull failed", "bad");
  }
}
$("ollamaPull").onclick = pullOllama;
$("ollamaModel").addEventListener("keydown", (e) => { if (e.key === "Enter") pullOllama(); });
renderProvChips();

function renderSetupBanner() {
  const box = $("setupBanner"); if (!box || !state) return;
  const ob = state.onboarding || {};
  const problem = ob.preflight || ob.tool_warning || "";
  const local = ob.ollama_model || "";
  const show = !!problem;
  box.classList.toggle("hidden", !show);
  if (!show) return;
  box.classList.toggle("bad", !!problem);
  $("setupTitle").textContent = problem ? "setup attention" : "local model ready";
  $("setupState").textContent = local ? short(local) : "init";
  const bits = [];
  if (problem) bits.push(problem);
  if (local) bits.push("Ollama at " + (ob.ollama_host || "localhost:11434") + " can use " + local + ".");
  bits.push("For a guided setup, run relaycli init in the terminal.");
  $("setupBody").textContent = bits.join(" ");
  $("setupCopy").onclick = async () => {
    const cmd = local ? `relaycli init --model ${local} --yes` : "relaycli init";
    try { await navigator.clipboard.writeText(cmd); addLog("setup", "copied: " + cmd); }
    catch (e) { addLog("setup", cmd); }
  };
}

function modelMatches(m, q) {
    if (!q) return true;
    return [m.id, m.name, m.desc, m.group, m.provider, m.source]
      .join(" ").toLowerCase().includes(q);
}
function modelButton(m, i, prefix) {
  if (prefix === "recent") {
    return `<button class="recent-pill ${m.current ? "on" : ""}" data-i="${i}">` +
      `<span class="dot"></span><span class="rnm" title="${esc(m.id)}">${esc(m.name)}</span></button>`;
  }
  return `<button class="item ${m.current ? "on" : ""}" data-i="${i}" data-src="${prefix}">` +
    `<span class="nm">${esc(m.name)}</span><span class="ds">${esc(m.desc)}</span>` +
    `<span class="src">${esc(m.source || "catalog")}</span>` +
    `<span class="ck"><svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5 9 17.5 20 6.5"></path></svg></span></button>`;
}
function bindModelButtons(root, models) {
  for (const b of root.querySelectorAll(".item,.recent-pill")) b.onclick = async () => {
    const m = models[+b.dataset.i];
    if (!m) return;
    await api("/api/model", { model: m.id });
    closeMenus(); await loadState();
    addLog("model", "switched to " + m.name);
  };
}
function renderModels() {
  const all = state.models || [];
  const recent = all.filter(m => m.group === "Recent");
  const providers = [];
  const seen = new Set();
  all.filter(m => m.group !== "Recent" && m.group !== "Current").forEach(m => {
    const key = m.provider || m.group;
    if (!key || seen.has(key)) return;
    seen.add(key);
    providers.push({ id: key, label: m.group || key, count: all.filter(x => x.provider === key && x.group !== "Recent").length });
  });
  if (providers.length && !providers.some(p => p.id === selectedProvider)) {
    const current = all.find(m => m.current && m.provider);
    selectedProvider = (current && current.provider) || providers[0].id;
  }

  $("recentItems").innerHTML = recent.length
    ? `<div class="head">Recent</div><div class="recent-row">` +
      recent.slice(0, 8).map((m, i) => modelButton(m, i, "recent")).join("") + `</div>`
    : "";
  bindModelButtons($("recentItems"), recent.slice(0, 8));

  $("providerItems").innerHTML = providers.map(p =>
    `<button class="${p.id === selectedProvider ? "on" : ""}" data-p="${esc(p.id)}">` +
    `<span>${esc(p.label)}</span><small>${p.count}</small></button>`
  ).join("");
  for (const b of $("providerItems").children) b.onclick = () => {
    selectedProvider = b.dataset.p; modelFilter = ""; $("modelQ").value = ""; renderModels();
  };

  const q = modelFilter;
  const models = all.filter(m =>
    m.group !== "Recent" && m.group !== "Current" && m.provider === selectedProvider && modelMatches(m, q)
  );
  $("modelQ").placeholder = selectedProvider ? `search ${selectedProvider} models` : "search models";
  let html = "", lastGroup = null;
  const visible = models.slice(0, 48);
  visible.forEach((m, i) => {
    if (m.group !== lastGroup) { html += `<div class="head grp">${esc(m.group)}</div>`; lastGroup = m.group; }
    html += modelButton(m, i, "provider");
  });
  if (models.length > visible.length) {
    html += `<div class="model-more">${models.length - visible.length} more hidden · narrow search</div>`;
  }
  $("modelItems").innerHTML = html || `<div class="model-empty">pick a provider or adjust search</div>`;
  bindModelButtons($("modelItems"), visible);
}

// A11y: .tog/.swatch are custom controls built from div/span (not
// button/input), so Enter/Space activation isn't native — bridge it to
// the same click handler instead of duplicating each one's logic.
// aria-checked/aria-pressed are synced next to every classList.toggle
// ("on", ...) call below so the state is visible to assistive tech, not
// only sighted users.
function bridgeKeyboard(el) {
  el.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); }
  };
}

// CONFIGURATION panel — pipeline, the roster (roles & models), providers & keys.
function renderConfig() {
  for (const t of document.querySelectorAll("#configMenu .tog[data-flag]")) {
    const on = !!state[t.dataset.flag];
    t.classList.toggle("on", on);
    t.setAttribute("aria-checked", String(on));
    t.onclick = async () => {
      const next = !t.classList.contains("on");
      const r = await api("/api/flag", { name: t.dataset.flag, on: next });
      if (r.ok) { await loadState(); }
    };
    bridgeKeyboard(t);
  }
  renderRoster();
  renderApiKeys();
  renderMcp();
  $("cfgPath").textContent = state.config_file || "";
  $("ver").textContent = "v" + (state.version || "");
}

// SETTINGS panel — general preferences only (appearance).
const ACCENT_NAMES = ["blue", "green", "amber", "purple", "red"];
function renderSettings() {
  $("ver2").textContent = "v" + (state.version || "");
  const sw = $("swatches");
  sw.innerHTML = ACCENTS.map((c, i) => `<span class="swatch" data-c="${c}" style="background:${c}"
    role="button" tabindex="0" aria-pressed="false" aria-label="${ACCENT_NAMES[i] || "accent"} accent"></span>`).join("");
  for (const s of sw.children) {
    const on = s.dataset.c === (localStorage.accent || "#5FA8DC");
    s.classList.toggle("on", on);
    s.setAttribute("aria-pressed", String(on));
    s.onclick = () => { document.documentElement.style.setProperty("--accent", s.dataset.c);
      localStorage.accent = s.dataset.c;
      for (const x of sw.children) { const sel = x === s; x.classList.toggle("on", sel); x.setAttribute("aria-pressed", String(sel)); } };
    bridgeKeyboard(s);
  }
  const tt = $("themeTog");
  const lightNow = document.documentElement.dataset.theme === "light";
  tt.classList.toggle("on", lightNow);
  tt.setAttribute("aria-checked", String(lightNow));
  tt.onclick = () => {
    const light = document.documentElement.dataset.theme !== "light";
    document.documentElement.dataset.theme = light ? "light" : "dark";
    localStorage.theme = light ? "light" : "dark";
    tt.classList.toggle("on", light);
    tt.setAttribute("aria-checked", String(light));
  };
  bridgeKeyboard(tt);
  const rt = $("reduceTog");
  const reduceNow = localStorage.reduce === "1";
  rt.classList.toggle("on", reduceNow);
  rt.setAttribute("aria-checked", String(reduceNow));
  document.body.classList.toggle("reduce", reduceNow);
  rt.onclick = () => { const on = !rt.classList.contains("on"); rt.classList.toggle("on", on);
    rt.setAttribute("aria-checked", String(on));
    localStorage.reduce = on ? "1" : "0"; document.body.classList.toggle("reduce", on); };
  bridgeKeyboard(rt);
}

/* ---- slash command palette ---- */
function commandRows() { return (state && state.commands) || []; }
function commandMatch(row, q) {
  if (!q) return true;
  const hay = [row.name, row.usage, row.description, row.group].join(" ").toLowerCase();
  return hay.includes(q) || row.name.split("").filter((ch, i) => ch === q[i]).length >= Math.min(q.length, 3);
}
function renderCommandPalette() {
  const input = $("input");
  const text = input.value;
  const box = $("cmdPalette");
  if (!text.startsWith("/")) {
    box.classList.add("hidden"); cmdMatches = []; return;
  }
  const raw = text.slice(1);
  const hasArg = /\s/.test(raw.trimEnd());
  if (hasArg && raw.trim()) {
    box.classList.add("hidden"); cmdMatches = []; return;
  }
  const q = raw.trim().toLowerCase();
  cmdMatches = commandRows().filter(c => commandMatch(c, q)).slice(0, 12);
  if (!cmdMatches.length) {
    box.innerHTML = `<div class="cmd-top"><b>No command match</b><span>try /help</span></div>`;
    box.classList.remove("hidden"); return;
  }
  cmdIndex = Math.max(0, Math.min(cmdIndex, cmdMatches.length - 1));
  let last = "";
  const body = cmdMatches.map((c, i) => {
    const group = c.group || "Commands";
    const head = group !== last ? `<div class="cmd-group">${esc(group)}</div>` : "";
    last = group;
    return head + `<button class="cmd-item ${i === cmdIndex ? "sel" : ""}" data-i="${i}">` +
      `<span class="cu">${esc(c.usage)}</span><span class="cd">${esc(c.description)}</span></button>`;
  }).join("");
  box.innerHTML = `<div class="cmd-top"><b>Command palette</b><span>↑↓ pilih · Tab isi · Enter jalan</span></div>` +
    `<div class="cmd-list">${body}</div>`;
  box.classList.remove("hidden");
  for (const b of box.querySelectorAll(".cmd-item")) {
    b.onmouseenter = () => { cmdIndex = +b.dataset.i; renderCommandPalette(); };
    b.onclick = () => applyCommand(cmdMatches[+b.dataset.i]);
  }
}
function applyCommand(command) {
  if (!command) return;
  const value = command.hint ? `/${command.name} ` : `/${command.name}`;
  $("input").value = value;
  $("input").focus();
  $("cmdPalette").classList.add("hidden");
}
function commandHelpText() {
  const rows = commandRows();
  if (!rows.length) return "Ketik /model, /mode, /relay, /clear, atau /help.";
  return "Command yang tersedia di desktop:\n" +
    rows.map(c => `${c.usage.padEnd(22)} ${c.description}`).join("\n") +
    "\n\nTip: ketik / lalu lanjutkan beberapa huruf untuk mencari command.";
}
function nearestCommand(name) {
  const rows = commandRows();
  return rows.find(c => c.name.startsWith(name))
    || rows.find(c => c.name.includes(name))
    || rows[0];
}
async function runClientSlash(text) {
  const raw = text.slice(1).trim();
  const [cmd = "", ...rest] = raw.split(/\s+/);
  const arg = rest.join(" ").trim();
  addMsg("you", "you", text);
  addLog("you", text);
  if (!cmd || cmd === "help" || cmd === "?") {
    addMsg("", "guide", commandHelpText());
    addLog("guide", "slash command palette shown", "ok");
    return true;
  }
  if (cmd === "clear") {
    const r = await api("/api/reset", { force: true });
    let ok = false; try { ok = (await r.json()).ok; } catch (e) {}
    if (ok) {
      $("thread").innerHTML = ""; since = 0; $("goal").innerHTML = '<span style="color:var(--t4)">no run yet</span>';
      for (const k in status) { delete status[k]; delete activity[k]; }
      for (const k in steps) delete steps[k];
      for (const k in taskLanes) delete taskLanes[k];
      addLog("guide", "conversation cleared", "ok");
      renderCards(); renderFlow(); updateProgress();
    }
    return true;
  }
  if (cmd === "mode") {
    if (!arg) {
      addMsg("", "guide", "Mode aktif sekarang: " + mode + "\nKamu bisa ganti ke suggest, auto-edit, atau full-auto.");
      return true;
    }
    if (!["suggest", "auto-edit", "full-auto"].includes(arg)) {
      addMsg("err", "guide", "Mode tidak dikenal. Pilih: suggest, auto-edit, full-auto.");
      return true;
    }
    const r = await api("/api/mode", { mode: arg });
    let ok = false; try { ok = (await r.json()).ok; } catch (e) {}
    if (!ok) {
      addMsg("err", "guide", "Mode gagal disimpan. Coba ulang.");
      return true;
    }
    mode = arg;
    for (const b of $("modes").children) b.classList.toggle("on", b.dataset.m === mode);
    await loadState();
    addMsg("", "guide", "Mode sudah diganti ke " + mode + " dan tersimpan.");
    addLog("config", "mode -> " + mode + " (saved)", mode === "full-auto" ? "warn" : "ok");
    return true;
  }
  if (cmd === "relay") {
    if (!["on", "off"].includes(arg)) {
      addMsg("", "guide", "Relay bisa dinyalakan dengan /relay on atau dimatikan dengan /relay off.");
      return true;
    }
    await api("/api/flag", { name: "relay", on: arg === "on" });
    await loadState();
    addMsg("", "guide", "Relay sekarang " + arg + ".");
    addLog("config", "relay -> " + arg, "ok");
    return true;
  }
  if (cmd === "model") {
    if (!arg) {
      toggleMenu("modelMenu", "model"); $("modelQ").focus();
      addMsg("", "guide", "Model picker sudah dibuka. Pilih recent model, pilih provider, atau cari model yang kamu mau.");
      return true;
    }
    if (arg.startsWith("search ")) {
      modelFilter = arg.slice(7).trim().toLowerCase(); $("modelQ").value = modelFilter;
      toggleMenu("modelMenu", "model"); renderModels(); $("modelQ").focus();
      return true;
    }
    if (arg.startsWith("provider ")) {
      selectedProvider = arg.slice(9).trim(); toggleMenu("modelMenu", "model"); renderModels();
      return true;
    }
    await api("/api/model", { model: arg });
    await loadState();
    addMsg("", "guide", "Model aktif sekarang " + arg + ".");
    addLog("model", "switched to " + arg, "ok");
    return true;
  }
  if (cmd === "config" || cmd === "settings") {
    toggleMenu(cmd === "config" ? "configMenu" : "settingsMenu", cmd);
    addMsg("", "guide", (cmd === "config" ? "Config" : "Settings") + " sudah dibuka.");
    return true;
  }
  if (cmd === "setup" || cmd === "init") {
    addMsg("", "guide", "Setup terpandu jalan dari terminal:\nrelaycli init\n\nUntuk Ollama lokal, kamu juga bisa pakai:\nrelaycli config ollama");
    return true;
  }
  if (cmd === "doctor") {
    addMsg("", "guide", "Health check jalan dari terminal:\nrelaycli doctor");
    return true;
  }
  if (cmd === "desktop") {
    addMsg("", "guide", "Kamu sudah berada di RelayCLI Desktop.");
    return true;
  }
  if (cmd === "exit" || cmd === "quit") {
    addMsg("", "guide", "Tutup tab browser untuk keluar dari desktop UI.");
    return true;
  }
  const near = nearestCommand(cmd);
  addMsg("err", "guide", `Command /${cmd} belum tersedia di desktop. ${near ? "Yang paling dekat: " + near.usage + "." : "Ketik /help untuk melihat opsi."}`);
  return true;
}

const TIERS = ["fast", "balanced", "strong"];
function short(id) { return id.split("/").pop(); }

function modelOptions(selected) {
  // tiers first (roster assignments are usually a tier), then the full catalog.
  let html = `<optgroup label="Tier">` +
    TIERS.map(t => `<option value="${t}"${t === selected ? " selected" : ""}>${t}</option>`).join("") +
    `</optgroup>`;
  let group = null, buf = "";
  (state.models || []).forEach(m => {
    if (m.group === "Current") return;
    if (m.group !== group) { if (buf) html += `<optgroup label="${esc(group)}">${buf}</optgroup>`; buf = ""; group = m.group; }
    buf += `<option value="${esc(m.id)}"${m.id === selected ? " selected" : ""}>${esc(m.name)}</option>`;
  });
  if (buf) html += `<optgroup label="${esc(group)}">${buf}</optgroup>`;
  return html;
}

// The 16-role roster: enable/disable + assign a model or tier per role.
function renderRoster() {
  $("roster").innerHTML = (state.roster || []).map(r =>
    `<div class="rmrow">` +
    `<div class="tog rtog ${r.enabled ? "on" : ""}" data-role="${esc(r.id)}"><span></span></div>` +
    `<span class="rn" title="${esc(r.id)}">${esc(r.id)}</span>` +
    `<select data-role="${esc(r.id)}">${modelOptions(r.assigned)}</select></div>`
  ).join("");
  for (const t of $("roster").querySelectorAll(".rtog")) t.onclick = async () => {
    const next = !t.classList.contains("on");
    await api("/api/roster", { role: t.dataset.role, enabled: next });
    await loadState();
    addLog("config", t.dataset.role + (next ? " enabled" : " disabled"));
  };
  for (const s of $("roster").querySelectorAll("select")) s.onchange = async () => {
    await api("/api/roster", { role: s.dataset.role, model: s.value });
    await loadState();
    addLog("config", s.dataset.role + " → " + short(s.value));
  };
}
function renderMcp() {
  const rows = state.mcp || [];
  $("mcpList").innerHTML = rows.length
    ? rows.map(s => {
        const color = s.state === "running" ? "var(--green)"
          : s.state === "failed" ? "var(--red)" : "var(--t5)";
        const tools = s.state === "running" ? ` · ${s.tools} tool${s.tools === 1 ? "" : "s"}` : "";
        return `<div class="keyrow"><span class="kdot" style="background:${color}"></span>` +
          `<span class="kl" title="${esc(s.command)}">${esc(s.name)}</span>` +
          `<span style="font-family:var(--mono);font-size:10px;color:var(--t4)">${esc(s.state)}${tools}</span></div>`;
      }).join("")
    : `<div style="font-size:10.5px;color:var(--t4)">none configured — <span style="font-family:var(--mono)">relaycli mcp add &lt;preset&gt;</span></div>`;
}

function renderApiKeys() {
  $("apiKeys").innerHTML = (state.providers || []).map((p, i) =>
    `<div class="keyrow"><span class="kdot ${p.detected ? "on" : ""}"></span>` +
    `<span class="kl">${esc(p.label)}</span>` +
    `<input type="password" data-p="${esc(p.id)}" placeholder="${p.detected ? "•••• set" : p.env}" autocomplete="off">` +
    `<button data-p="${esc(p.id)}">Save</button></div>`
  ).join("");
  for (const b of $("apiKeys").querySelectorAll("button")) b.onclick = async () => {
    const inp = $("apiKeys").querySelector(`input[data-p="${b.dataset.p}"]`);
    const key = inp.value.trim(); if (!key) return;
    await api("/api/key", { provider: b.dataset.p, key });
    inp.value = ""; await loadState();
    addLog("config", b.dataset.p + " key saved");
  };
}

/* ---- agent views ----
   participants() is the workflow's cast: the enabled pipeline roles PLUS any
   specialist that actually ran (from the event stream), inserted before the
   reviewer. This is what fixes the "chaotic when roles are added" problem —
   the view is driven by who really participates, not a fixed 5, and the flow
   below scales to any number of them. */
let zoom = 1;
let roleModel = {};   // name -> model, filled from run events (overrides roster)

function rosterModelOf(name) {
  const r = (state.roster || []).find(x => x.id === name);
  return roleModel[name] || (r && r.model) || "";
}

function participants() {
  if (!state || !state.relay) {
    return [{ name: "agent", model: state ? state.model_short : "" }];
  }
  const pipeline = state.roles.filter(r => r.enabled).map(r => ({ name: r.name, model: r.model }));
  const known = new Set(pipeline.map(p => p.name));
  // The configured team is visible even before a run: enabled roster
  // specialists, plus any that actually ran this run.
  const names = new Set([...(state.specialists || []), ...Object.keys(status)]);
  const extras = [...names].filter(k => !known.has(k))
    .map(k => ({ name: k, model: rosterModelOf(k), specialist: true }));
  if (!extras.length) return pipeline;
  const ri = pipeline.findIndex(p => p.name === "reviewer");
  return ri >= 0 ? [...pipeline.slice(0, ri), ...extras, ...pipeline.slice(ri)] : [...pipeline, ...extras];
}

function fmtDuration(ms) {
  const sec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h}h ${m}m ${s}s`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}
function compactStatusText(text, limit = 88) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  return clean.length > limit ? clean.slice(0, limit - 1).trimEnd() + "…" : clean;
}
function toolLabel(name, done) {
  const labels = {
    read_file: ["Reading file", "Read file"],
    list_dir: ["Listing directory", "Listed directory"],
    find_files: ["Searching code", "Searched code"],
    search: ["Searching code", "Searched code"],
    write_file: ["Writing file", "Wrote file"],
    edit_file: ["Editing file", "Edited file"],
    create_folder: ["Creating folder", "Created folder"],
    run_command: ["Running command", "Ran command"],
  };
  const pair = labels[name] || [`Using ${name}`, `Used ${name}`];
  return done ? pair[1] : pair[0];
}
function iconFor(kind) {
  if (kind === "model") return "ai";
  if (kind === "cmd") return "$";
  if (kind === "file") return "f";
  if (kind === "search") return "s";
  if (kind === "agent") return ">";
  if (kind === "done") return "✓";
  if (kind === "bad") return "!";
  return "·";
}
function activityKindForTool(name) {
  if (name === "run_command") return "cmd";
  if (name === "find_files" || name === "search") return "search";
  if (name.includes("file") || name === "list_dir" || name === "create_folder") return "file";
  return "tool";
}
function pushActivity(item) {
  item.id = ++activitySeq;
  activityFeed.push(item);
  while (activityFeed.length > 32) activityFeed.shift();
  renderActivity();
  return item;
}
function startActivity(agent, kind, text, meta) {
  return pushActivity({
    agent, kind, text: compactStatusText(text), meta: compactStatusText(meta || agent, 72),
    started: Date.now(), ended: null, active: true, ok: true,
  });
}
function finishActivity(item, text, ok = true) {
  if (!item) return;
  item.active = false;
  item.ok = ok;
  item.ended = Date.now();
  if (text) item.text = compactStatusText(text);
  renderActivity();
}
function renderActivity() {
  const list = $("activityList"); if (!list) return;
  $("activityCount").textContent = activityFeed.length ? activityFeed.length : "";
  if (!activityFeed.length) {
    list.innerHTML = `<div class="activity-empty">Aktivitas agent akan muncul di sini: membaca file, berpikir, mengedit, menjalankan command, dan selesai.</div>`;
    return;
  }
  list.innerHTML = activityFeed.slice(-18).map(item => {
    const active = item.active;
    const cls = active ? "active" : (item.ok ? "done" : "bad");
    const elapsedMs = item.started ? (item.ended || Date.now()) - item.started : 0;
    const elapsed = item.started && (active || elapsedMs >= 1000) ? fmtDuration(elapsedMs) : "";
    return `<div class="actitem ${cls}">` +
      `<span class="actico">${esc(iconFor(item.kind))}</span>` +
      `<div><div class="actmain"><span class="acttext">${esc(item.text)}</span>` +
      `${elapsed ? `<span class="acttime">${esc(elapsed)}</span>` : ""}</div>` +
      `<div class="actmeta">${esc(item.meta || "")}</div></div></div>`;
  }).join("");
  list.scrollTop = list.scrollHeight;
}
function resetActivityFeed() {
  activityFeed.length = 0;
  for (const k in activeToolByAgent) delete activeToolByAgent[k];
  for (const k in activeModelByAgent) delete activeModelByAgent[k];
  renderActivity();
}
function startToolStatus(ev) {
  const match = String(ev.text || "").match(/^→ tool\s+([^\s{]+)/);
  if (!match) return;
  const name = match[1];
  const kind = activityKindForTool(name);
  const item = startActivity(ev.agent || "agent", kind, toolLabel(name, false), ev.text);
  activeToolByAgent[ev.agent || "agent"] = { item, name };
}
function finishToolStatus(ev) {
  const agent = ev.agent || "agent";
  const active = activeToolByAgent[agent];
  const summary = ev.summary || "tool";
  const ok = ev.ok !== false;
  if (active) {
    finishActivity(active.item, toolLabel(active.name, true), ok);
    delete activeToolByAgent[agent];
    active.item.meta = compactStatusText(summary, 72);
  } else {
    pushActivity({
      agent, kind: ok ? "done" : "bad", text: compactStatusText(summary),
      meta: agent, started: null, ended: null, active: false, ok,
    });
  }
  renderActivity();
}

function renderCards() {
  if (Object.keys(taskLanes).length) {
    const tasks = Object.values(taskLanes);
    $("agentCount").textContent = tasks.length;
    $("cards").innerHTML = tasks.map(t => {
      const cls = t.status === "running" ? "active" : t.status === "done" ? "done" : "";
      const pill = t.status === "running" ? "Working" : t.status === "done" ? "Done"
        : t.status === "failed" ? "Failed" : t.status === "blocked" ? "Blocked" : "Idle";
      return `<div class="card ${cls}"><div class="row"><span class="name">${esc(t.role_id)}</span>` +
        `<span class="pill"><i></i>${pill}</span></div>` +
        `<div class="desc">${esc(DESC[t.role_id] || "specialist")}</div>` +
        `<div class="act">${esc(t.goal)}</div></div>`;
    }).join("");
    return;
  }
  const roles = participants();
  $("agentCount").textContent = roles.length;
  $("cards").innerHTML = roles.map(r => {
    const st = status[r.name] || "idle";
    const cls = st === "active" ? "active" : st === "done" ? "done" : "";
    const pill = st === "active" ? "Working" : st === "done" ? "Done" : "Idle";
    const act = activity[r.name] || "waiting";
    return `<div class="card ${cls}"><div class="row"><span class="name">${esc(r.name)}</span>` +
      `<span class="pill"><i></i>${pill}</span></div>` +
      `<div class="desc">${esc(DESC[r.name] || "specialist")}${r.model ? " · " + esc(short(r.model)) : ""}</div>` +
      `<div class="act">${esc(act)}</div></div>`;
  }).join("");
}

// Task lanes: the --experimental-parallel view. One row per task-graph node,
// concurrent rather than linear, so this does not reuse the rail's single
// ring/segment metaphor below — a task can be "waiting on a dependency"
// while a sibling is already running, which the rail has no way to show.
function statusGlyph(st) {
  return { pending: "○", ready: "◇", running: "◆", blocked: "⊘",
    done: "✓", failed: "✗", cancelled: "╌" }[st] || "○";
}
function statusClass(st) {
  return { running: "active", done: "done", failed: "err", blocked: "wait" }[st] || "";
}
function taskDepth(id, seen) {
  const t = taskLanes[id];
  if (!t || !t.depends_on.length || seen.has(id)) return 0;
  seen.add(id);
  return 1 + Math.max(0, ...t.depends_on.map(d => taskDepth(d, seen)));
}
function renderTaskLanes() {
  const stage = $("stage"); if (!stage) return;
  stage.classList.add("tasks");
  stage.style.transform = `scale(${zoom})`;
  const ids = Object.keys(taskLanes);
  if (!ids.length) {
    stage.innerHTML = `<div class="hint">parallel orchestrator — <b>send a request</b> to watch tasks run</div>`;
    return;
  }
  // Depth first, then the orchestrator's own planned order — Array.sort is
  // stable, so omitting a tiebreak preserves insertion order. An earlier
  // alphabetical tiebreak floated an unstarted "docs-pass" above the
  // finished "survey-api" it was planned after, which reads as wrong.
  const sorted = ids.slice().sort((a, b) => taskDepth(a, new Set()) - taskDepth(b, new Set()));
  stage.innerHTML = sorted.map(id => {
    const t = taskLanes[id];
    const cls = statusClass(t.status);
    const elapsed = t.started ? fmtDuration((t.ended || Date.now()) - t.started) : "";
    const deps = t.depends_on.length
      ? `<span class="tdeps">waits: ${t.depends_on.map(esc).join(", ")}</span>` : "";
    const stat = (t.tokens || t.cost_usd)
      ? `<span class="tstat">${t.tokens} tok · $${t.cost_usd.toFixed(4)}</span>` : "";
    return `<div class="tlane ${cls}">` +
      `<span class="tglyph">${statusGlyph(t.status)}</span>` +
      `<div class="tbody"><div class="thead"><span class="tid">${esc(id)}</span>` +
      `<span class="trole">${esc(t.role_id)}</span></div>` +
      `<div class="tgoal">${esc(t.goal)}</div>${deps}</div>` +
      `<div class="tside">${stat}${elapsed ? `<span class="ttime">${esc(elapsed)}</span>` : ""}</div>` +
      `</div>`;
  }).join("");
}

// The orchestration rail: one station per participant on a vertical rail.
// The segment INTO the active station flows; segments past finished stations
// set green; task-split specialists sit on an indented branch lane. Scales to
// any number of participants — never cramped, never chaotic.
function renderFlow() {
  const stage = $("stage"); if (!stage) return;
  // Task lanes take over once there's data to show, or pre-emptively once
  // parallel is the configured pipeline (true by default) — the empty-state
  // hint should describe what the NEXT send will actually run, not always
  // default to the old rail's single-agent framing.
  if (Object.keys(taskLanes).length || (state && state.experimental_parallel)) {
    renderTaskLanes(); return;
  }
  stage.classList.remove("tasks");
  const relay = state && state.relay;
  const roles = participants();
  const rows = [];
  if (relay) rows.push({ name: "orchestrator", desc: "coordinates the relay", orch: true });
  roles.forEach(r => rows.push({
    name: r.name, desc: DESC[r.name] || "specialist", model: r.model, spec: !!r.specialist,
  }));

  const anyRun = busy || Object.keys(status).length > 0;
  stage.style.transform = `scale(${zoom})`;
  const hint = anyRun ? "" :
    `<div class="hint">team ready — <b>send a request</b> to watch it work</div>`;

  stage.innerHTML = hint + rows.map((row, i) => {
    const st = row.orch ? (busy ? "active" : "idle") : (status[row.name] || "idle");
    const cls = st === "active" ? "active" : st === "done" ? "done" : st === "err" ? "err"
      : (busy && !row.orch ? "dim" : "");
    const glyph = st === "done" ? "✓" : st === "err" ? "✕" : (row.orch ? "◈" : (GLYPH[row.name] || "●"));
    // rail segments: above flows while this station is active; below goes
    // green once the station is done.
    const segTop = i === 0 ? "none" : (st === "active" && busy ? "flow" : (st === "done" ? "done" : ""));
    const segBot = i === rows.length - 1 ? "none" : (st === "done" ? "done" : "");
    const steps = stepsOf(row.name);
    const act = (!row.orch && st === "active" && activity[row.name])
      ? `<div class="sact">${esc(activity[row.name])}</div>` : "";
    const chip = row.model ? `<span class="schip" title="${esc(row.model)}">${esc(short(row.model))}</span>` : "";
    const stepsBadge = steps ? `<span class="ssteps">${steps} step${steps > 1 ? "s" : ""}</span>` : "";
    const prevSpec = i > 0 && rows[i - 1].spec;
    const fork = row.spec && !prevSpec ? `<div class="elbow"></div>` : "";
    const join = !row.spec && prevSpec ? `<div class="elbow join"></div>` : "";
    return fork + join + `<div class="st ${cls} ${row.orch ? "orch" : ""} ${row.spec ? "branch" : ""} lane${i % 3}">` +
      `<div class="rail"><i class="seg ${segTop}"></i><span class="ring">${esc(glyph)}</span>` +
      `<i class="seg ${segBot}"></i></div>` +
      `<div class="scard"><div class="shead"><span class="sname">${esc(row.name)}</span>${chip}</div>` +
      `<div class="sdesc">${esc(row.desc)}${stepsBadge}</div>${act}</div></div>`;
  }).join("");
}
function updateProgress() {
  if (Object.keys(taskLanes).length) {
    const tasks = Object.values(taskLanes);
    const done = tasks.filter(t => t.status === "done").length;
    const anyActive = tasks.some(t => t.status === "running");
    const pct = busy ? Math.min(95, Math.round((done + (anyActive ? 0.5 : 0)) / tasks.length * 100))
      : Math.round(done / tasks.length * 100);
    $("progBar").style.width = pct + "%";
    $("progPct").textContent = pct + "%";
    return;
  }
  const roles = participants();
  const done = roles.filter(r => (status[r.name] || "idle") === "done").length;
  const anyActive = roles.some(r => status[r.name] === "active");
  const pct = busy ? Math.min(95, Math.round((done + (anyActive ? 0.5 : 0)) / roles.length * 100))
    : (done ? 100 : 0);
  $("progBar").style.width = pct + "%";
  $("progPct").textContent = busy || done ? pct + "%" : "—";
}

function addMsg(cls, who, text) {
  const el = document.createElement("div");
  const dot = cls === "you" ? "" : `<i style="background:${cls === "err" ? "var(--red)" : "var(--accent)"}"></i>`;
  el.className = "msg " + cls;
  el.innerHTML = `<div class="who">${dot}<span>${esc(who)}</span><span class="ago">now</span></div><div class="bubble">${esc(text)}</div>`;
  $("thread").appendChild(el); $("thread").scrollTop = 1e9;
}
/* ANSI SGR → spans (colors 30-37/90-97, bold; everything else stripped). */
function ansiToHtml(s) {
  const parts = String(s == null ? "" : s).split(/\x1b\[([0-9;]*)m/);
  let html = "", classes = [];
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      if (!parts[i]) continue;
      const text = esc(parts[i].replace(/[\x00-\x08\x0b-\x1f]/g, ""));
      html += classes.length ? `<span class="${classes.join(" ")}">${text}</span>` : text;
    } else {
      for (const code of (parts[i] || "0").split(";")) {
        const n = +code || 0;
        if (n === 0) classes = [];
        else if (n === 1) classes.push("ab");
        else if ((n >= 30 && n <= 37) || (n >= 90 && n <= 97)) {
          classes = classes.filter(c => !/^a\d+$/.test(c)); classes.push("a" + n);
        }
      }
    }
  }
  return html;
}

let logLines = 0, unseen = 0;
function logPinned() {
  const log = $("log");
  return log.scrollTop + log.clientHeight >= log.scrollHeight - 12;
}
function addLog(tag, msg, cls) {
  const log = $("log");
  const pinned = logPinned();
  const el = document.createElement("div");
  el.className = "line" + (cls ? " " + cls : "");
  el.innerHTML = `<span class="tag ${tag === "you" ? "hot" : ""}">[${esc(tag)}]</span>` +
    `<span class="m ${cls || ""}">${ansiToHtml(msg)}</span>`;
  log.appendChild(el);
  logLines++;
  while (logLines > 2000) { log.removeChild(log.firstChild); logLines--; }
  $("lineCount").textContent = logLines + " lines";
  if (pinned || $("term").classList.contains("collapsed")) {
    log.scrollTop = 1e9;
  } else {
    unseen++;
    const chip = $("newLines");
    chip.textContent = `↓ ${unseen} new`;
    chip.classList.remove("hidden");
  }
}
$("log").addEventListener("scroll", () => {
  if (logPinned()) { unseen = 0; $("newLines").classList.add("hidden"); }
});
$("newLines").onclick = () => { $("log").scrollTop = 1e9; unseen = 0; $("newLines").classList.add("hidden"); };

function setBusy(b) {
  busy = b;
  $("app").classList.toggle("busy", b);
  document.title = b ? "RelayCLI · running" : "RelayCLI";
  $("live").className = "live" + (b ? " on" : "");
  $("liveText").textContent = b ? "running" : "idle";
  $("runLabel").className = "label" + (b ? " on" : "");
  $("send").disabled = b; $("stopBtn").disabled = !b;
  if (!b) for (const k in status) if (status[k] === "active") status[k] = "done";
  if (b && !elapsedTimer) {
    if (!runStart) runStart = Date.now();
    elapsedTimer = setInterval(() => {
      $("stElapsed").textContent = ((Date.now() - runStart) / 1000).toFixed(0) + "s";
      renderActivity();
    }, 1000);
  } else if (!b && elapsedTimer) {
    clearInterval(elapsedTimer); elapsedTimer = null; runStart = 0;
  }
  renderCards(); renderFlow(); updateProgress();
}

function handle(ev) {
  if (ev.kind === "user") {
    addMsg("you", "you", ev.text); addLog("you", ev.text);
    resetActivityFeed();
    pushActivity({
      agent: "you", kind: "agent", text: "Request received", meta: compactStatusText(ev.text, 72),
      started: null, ended: null, active: false, ok: true,
    });
    for (const k in status) { status[k] = "idle"; activity[k] = "waiting"; }
    for (const k in steps) delete steps[k];
    for (const k in taskLanes) delete taskLanes[k];
    runStart = Date.now();
    $("goal").textContent = ev.text;
  } else if (ev.kind === "role") {
    for (const k in status) if (status[k] === "active") status[k] = "done";
    status[ev.agent] = "active"; activity[ev.agent] = "started (" + ev.model + ")";
    roleModel[ev.agent] = ev.model;   // remember the specialist's model for the flow
    pushActivity({
      agent: ev.agent, kind: "agent", text: `${ev.agent} started`,
      meta: ev.model, started: null, ended: null, active: false, ok: true,
    });
    addLog(ev.agent, "started · " + ev.model);
  } else if (ev.kind === "text") {
    status[ev.agent] = "active"; addMsg("", ev.agent, ev.text);
    addLog(ev.agent, ev.text.split("\n")[0].slice(0, 140));
  } else if (ev.kind === "guide") {
    addMsg("", ev.agent || "guide", ev.text);
    addLog("guide", ev.text.split("\n")[0].slice(0, 140), "ok");
  } else if (ev.kind === "tool") {
    status[ev.agent] = "active"; activity[ev.agent] = ev.summary;
    steps[ev.agent] = (steps[ev.agent] || 0) + 1;
    finishToolStatus(ev);
    addLog(ev.agent, ev.summary, ev.ok ? "" : "bad");
  } else if (ev.kind === "log") {
    status[ev.agent] = "active"; activity[ev.agent] = ev.text;
    const cls = ev.level === "bad" ? "bad" : (ev.level === "warn" ? "warn" : "");
    const text = ev.text || "";
    if (text.startsWith("→ model step")) {
      const agent = ev.agent || "agent";
      activeModelByAgent[agent] = startActivity(agent, "model", "Thinking", text.replace(/^→\s*/, ""));
    } else if (text.startsWith("← model")) {
      const agent = ev.agent || "agent";
      const doneText = compactStatusText(text.replace(/^← model\s*/, "Model "));
      if (activeModelByAgent[agent]) {
        finishActivity(activeModelByAgent[agent], doneText, ev.level !== "bad");
        delete activeModelByAgent[agent];
      } else {
        pushActivity({
          agent, kind: ev.level === "bad" ? "bad" : "done", text: doneText,
          meta: agent, started: null, ended: null, active: false, ok: ev.level !== "bad",
        });
      }
    } else if (text.startsWith("→ tool")) {
      startToolStatus(ev);
    }
    addLog(ev.agent || "log", ev.text, cls);
  } else if (ev.kind === "note") {
    addLog("note", ev.text, "warn");
    const note = ev.text || "";
    if (note.startsWith("Ollama model installed:") || note.startsWith("Model auto-switched:")) {
      setTimeout(loadState, 400);
    }
  } else if (ev.kind === "summary") {
    const p = ["■ " + ev.stopped];
    if (ev.verdict) p.push("verdict " + ev.verdict);
    if (ev.tasks && ev.tasks.length) p.push(ev.tasks.length + " tasks");
    p.push(ev.tokens + " tok", "$" + (ev.cost || 0).toFixed(4), ev.elapsed + "s");
    addLog("run", p.join(" · "), ev.stopped === "done" ? "ok" : "bad");
    pushActivity({
      agent: "run", kind: ev.stopped === "done" ? "done" : "bad",
      text: ev.stopped === "done" ? "Run complete" : "Run stopped",
      meta: p.slice(1).join(" · "), started: null, ended: null, active: false,
      ok: ev.stopped === "done",
    });
    if (ev.text) addMsg(ev.stopped === "done" ? "" : "err", "run", ev.text);
    $("stElapsed").textContent = ev.elapsed + "s"; $("stTokens").textContent = ev.tokens;
  } else if (ev.kind === "error") {
    pushActivity({
      agent: ev.agent || "error", kind: "bad", text: "Error",
      meta: compactStatusText(ev.text, 72), started: null, ended: null, active: false, ok: false,
    });
    addMsg("err", "error", ev.text); addLog("error", ev.text, "bad");
  } else if (ev.kind === "task_status") {
    const prev = taskLanes[ev.task_id];
    const startedNow = ev.status === "running" && (!prev || prev.status !== "running");
    const terminal = ev.status === "done" || ev.status === "failed" || ev.status === "cancelled";
    taskLanes[ev.task_id] = {
      role_id: ev.role_id, goal: ev.goal, status: ev.status, depends_on: ev.depends_on || [],
      tokens: ev.tokens || 0, cost_usd: ev.cost_usd || 0,
      started: startedNow ? Date.now() : ((prev && prev.started) || null),
      ended: terminal ? Date.now() : null,
    };
    pushActivity({
      agent: ev.role_id, kind: ev.status === "failed" ? "bad" : ev.status === "done" ? "done" : "agent",
      text: `${ev.task_id} → ${ev.status}`, meta: compactStatusText(ev.goal, 72),
      started: null, ended: null, active: ev.status === "running", ok: ev.status !== "failed",
    });
    addLog(ev.role_id, `${ev.task_id} (${ev.role_id}) → ${ev.status}`,
      ev.status === "failed" ? "bad" : ev.status === "done" ? "ok" : "");
  }
  renderCards(); renderFlow(); updateProgress();
}

async function poll() {
  try {
    const r = await (await fetch("/api/events?since=" + since)).json();
    for (const ev of r.events) { since = ev.n + 1; handle(ev); }
    if (busy !== r.busy) setBusy(r.busy);
  } catch (e) {}
  setTimeout(poll, 600);
}

async function loadState() {
	  state = await (await fetch("/api/state")).json();
	  $("proj").textContent = state.project;
	  $("cwd").textContent = state.cwd;
	  if (document.activeElement !== $("projectPath")) $("projectPath").value = state.cwd || "";
	  $("modelName").textContent = state.model_short;
  $("modelMenuCurrent").textContent = state.model || state.model_short || "";
  $("stModel").textContent = state.model_short;
  mode = state.mode;
  for (const b of $("modes").children) b.classList.toggle("on", b.dataset.m === mode);
  renderModels(); renderSetupBanner(); renderConfig(); renderSettings(); renderCards(); renderFlow();
}

// poll() must start regardless of whether the FIRST loadState() succeeds —
// a single failed/non-JSON /api/state (e.g. a malformed config.toml 500)
// used to leave `state` null and never schedule poll() at all, permanently
// freezing the page with no events, no busy tracking, and no retry.
// renderCards/renderFlow/participants() already null-guard `state`, so
// degrading to "no state yet" instead of "app is dead" is safe.
async function boot() {
  try {
    await loadState();
  } catch (e) {
    addLog("error", "could not load initial state — retrying…", "bad");
    const retry = setInterval(async () => {
      try { await loadState(); clearInterval(retry); }
      catch (e) {}
    }, 3000);
  }
  poll();
  if (!activityTimer) {
    activityTimer = setInterval(() => {
      if (activityFeed.some(item => item.active)) renderActivity();
      if (Object.values(taskLanes).some(t => t.status === "running")) renderTaskLanes();
    }, 1000);
  }
}

$("modes").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const next = b.dataset.m;
  mode = next; for (const x of $("modes").children) x.classList.toggle("on", x === b);
  const r = await api("/api/mode", { mode: next });
  let ok = false; try { ok = (await r.json()).ok; } catch (err) {}
  if (ok) {
    await loadState();
    addLog("config", "mode -> " + next + " (saved)", next === "full-auto" ? "warn" : "ok");
  } else {
    addLog("config", "mode save failed", "bad");
  }
});
	$("stopBtn").onclick = async () => { await api("/api/stop", {}); addLog("note", "stop requested — halting after the current step", "warn"); };
	$("chatCollapse").onclick = () => setPanelCollapsed("left", true);
	$("chatShow").onclick = () => setPanelCollapsed("left", false);
	$("sideCollapse").onclick = () => setPanelCollapsed("right", true);
	$("sideShow").onclick = () => setPanelCollapsed("right", false);
	$("newBtn").onclick = async () => {
	  const r = await api("/api/reset", { force: true });
  if ((await r.json()).ok) { $("thread").innerHTML = ""; since = 0;
    $("goal").innerHTML = '<span style="color:var(--t4)">no run yet</span>';
    for (const k in status) { delete status[k]; delete activity[k]; }
    for (const k in steps) delete steps[k];
    for (const k in taskLanes) delete taskLanes[k];
    $("stElapsed").textContent = "—"; $("stTokens").textContent = "—";
    renderCards(); renderFlow(); updateProgress(); }
};
$("termToggle").onclick = () => $("term").classList.toggle("collapsed");
$("termClear").onclick = () => {
  $("log").innerHTML = ""; logLines = 0; unseen = 0;
  $("lineCount").textContent = ""; $("newLines").classList.add("hidden");
};
$("termCopy").onclick = async () => {
  const text = [...$("log").children]
    .map(l => `${l.children[0].textContent} ${l.children[1].textContent}`).join("\n");
  try { await navigator.clipboard.writeText(text); addLog("note", "output copied"); }
  catch (e) { addLog("note", "copy failed — clipboard unavailable", "warn"); }
};
	// drag the grip or use +/- to resize the terminal (persisted)
	(() => {
	  const body = $("log");
	  const setTermHeight = (h) => {
	    const px = Math.max(70, Math.min(window.innerHeight * .64, h));
	    body.style.height = px + "px";
	    localStorage.termH = Math.round(px);
	  };
	  if (localStorage.termH) setTermHeight(+localStorage.termH);
	  $("termSmaller").onclick = () => setTermHeight(body.clientHeight - 42);
	  $("termLarger").onclick = () => setTermHeight(body.clientHeight + 42);
	  $("termGrip").addEventListener("mousedown", (e) => {
	    e.preventDefault();
	    const startY = e.clientY, startH = body.clientHeight;
	    const move = (ev) => {
	      setTermHeight(startH + (startY - ev.clientY));
	    };
	    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
	    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
	  });
	})();
function setZoom(z) { zoom = Math.max(0.5, Math.min(1.3, z)); $("zVal").textContent = Math.round(zoom * 100) + "%";
  const s = $("stage"); if (s) s.style.transform = `scale(${zoom})`; }
$("zIn").onclick = () => setZoom(zoom + 0.1);
$("zOut").onclick = () => setZoom(zoom - 0.1);

async function send() {
  const text = $("input").value.trim(); if (!text || busy) return;
  $("input").value = "";
  $("cmdPalette").classList.add("hidden");
  if (text.startsWith("/")) {
    await runClientSlash(text);
    return;
  }
  const r = await api("/api/send", { text, mode });
  if (r.status === 409) addLog("note", "a run is already in progress", "warn"); else setBusy(true);
}
$("send").onclick = send;
$("input").addEventListener("input", () => { cmdIndex = 0; renderCommandPalette(); });
$("input").addEventListener("keydown", (e) => {
  const open = !$("cmdPalette").classList.contains("hidden") && cmdMatches.length;
  if (open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    cmdIndex = (cmdIndex + (e.key === "ArrowDown" ? 1 : -1) + cmdMatches.length) % cmdMatches.length;
    renderCommandPalette();
    return;
  }
  if (open && (e.key === "Tab")) {
    e.preventDefault();
    applyCommand(cmdMatches[cmdIndex]);
    return;
  }
  if (e.key === "Escape") {
    $("cmdPalette").classList.add("hidden");
    return;
  }
  if (e.key === "Enter") {
    if (open && $("input").value.trim() === "/") {
      e.preventDefault();
      applyCommand(cmdMatches[cmdIndex]);
      return;
    }
    send();
  }
});

if (localStorage.accent) document.documentElement.style.setProperty("--accent", localStorage.accent);
applyPanelLayout();
addLog("relay", "desktop ready — agent output appears here");
boot();
