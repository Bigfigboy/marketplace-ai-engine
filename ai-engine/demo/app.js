// AI Engine demo client. Plain JS, no dependencies. All server text is rendered with
// textContent / DOM nodes (never innerHTML), so model output cannot inject markup.
"use strict";

const state = { key: "", agents: [], agent: null, history: {}, files: [], selected: new Set() };
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function loadKey() {
  try { state.key = sessionStorage.getItem("aiEngineKey") || ""; } catch (_) { state.key = ""; }
  $("apiKey").value = state.key;
}
function saveKey() {
  state.key = $("apiKey").value.trim();
  try { sessionStorage.setItem("aiEngineKey", state.key); } catch (_) { /* storage unavailable */ }
  init();
}

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (state.key) headers["Authorization"] = "Bearer " + state.key;
  const res = await fetch(path, Object.assign({}, options, { headers }));
  let body = null;
  if (res.status !== 204) {
    try { body = await res.json(); } catch (_) { body = null; }
  }
  if (!res.ok) {
    const err = (body && body.error) || { code: "http_" + res.status, message: res.statusText };
    const e = new Error(err.message);
    e.code = err.code; e.status = res.status; e.details = err.details;
    throw e;
  }
  return body;
}

// ── Minimal, safe Markdown renderer ─────────────────────────────────────────
function inline(text) {
  const frag = document.createDocumentFragment();
  const re = /(\*\*[^*]+\*\*|`[^`]+`|_[^_\s][^_]*_)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) frag.append(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) frag.append(el("strong", {}, tok.slice(2, -2)));
    else if (tok.startsWith("`")) frag.append(el("code", {}, tok.slice(1, -1)));
    else frag.append(el("em", {}, tok.slice(1, -1)));
    last = m.index + tok.length;
  }
  if (last < text.length) frag.append(text.slice(last));
  return frag;
}

function renderMarkdown(src) {
  const root = document.createDocumentFragment();
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { root.append(el(h[1].length <= 2 ? "h3" : "h4", {}, inline(h[2]))); i++; continue; }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { root.append(el("hr")); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const table = el("table");
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        const cells = lines[i].trim().slice(1, -1).split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) {
          const tr = el("tr");
          cells.forEach((c) => tr.append(el(table.rows.length === 0 ? "th" : "td", {}, inline(c))));
          table.append(tr);
        }
        i++;
      }
      root.append(table);
      continue;
    }
    const isUl = (s) => /^\s*[-*•]\s+/.test(s);
    const isOl = (s) => /^\s*\d+[.)]\s+/.test(s);
    if (isUl(line) || isOl(line)) {
      const ordered = isOl(line);
      const list = el(ordered ? "ol" : "ul");
      while (i < lines.length && (ordered ? isOl(lines[i]) : isUl(lines[i]) || /^\s{2,}[-*•]\s+/.test(lines[i]))) {
        list.append(el("li", {}, inline(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ""))));
        i++;
      }
      root.append(list);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*\||\s*[-*•]\s|\s*\d+[.)]\s|\s*---)/.test(lines[i])) {
      para.push(lines[i]); i++;
    }
    if (!para.length) { para.push(lines[i]); i++; }
    const p = el("p");
    para.forEach((t, idx) => { if (idx) p.append(el("br")); p.append(inline(t)); });
    root.append(p);
  }
  return root;
}

// ── Health, agents, files ────────────────────────────────────────────────────
async function loadHealth() {
  try {
    const h = await api("/health");
    const box = $("health");
    box.className = "health " + h.status;
    box.textContent = `${h.status === "ok" ? "●" : "▲"} ${h.status} · LLM: ${h.llm_mode}` +
      (h.llm_configured ? ` (${h.model})` : " — ключ не настроен") + ` · БД: ${h.database}`;
  } catch (e) {
    $("health").textContent = "Сервер недоступен";
  }
}

async function loadAgents() {
  const box = $("agents");
  box.replaceChildren();
  try {
    const data = await api("/agents");
    state.agents = data.agents;
    for (const a of data.agents) {
      const caps = el("div", { class: "caps" }, ...a.capabilities.slice(0, 5).map((c) => el("span", { class: "tag" }, c)));
      const card = el("button", { class: "agent-card", type: "button", "data-slug": a.slug, onclick: () => selectAgent(a.slug) },
        el("div", { class: "name" }, a.name),
        el("div", { class: "meta" }, `${a.slug} · v${a.version} · ${a.supported_languages.join("/")}`),
        caps);
      box.append(card);
    }
    if (!state.agent && data.agents.length) selectAgent(data.agents[0].slug);
  } catch (e) {
    box.append(el("p", { class: "hint" }, `Ошибка: ${e.code || ""} ${e.message}`));
  }
}

function selectAgent(slug) {
  state.agent = state.agents.find((a) => a.slug === slug) || null;
  document.querySelectorAll(".agent-card").forEach((c) => c.classList.toggle("active", c.dataset.slug === slug));
  $("chatTitle").textContent = state.agent ? state.agent.name : "Выберите агента";
  $("chatDesc").textContent = state.agent ? state.agent.description : "";
  $("sendBtn").disabled = !state.agent;
  const placeholders = {
    marketing: "Например: Создай контент-план для кофейни в Алматы",
    support: "Например: Сколько стоит доставка? (сначала загрузите базу знаний)",
    hr: "Например: Сделай сводку резюме (загрузите вымышленное резюме и отметьте его)",
  };
  $("message").placeholder = placeholders[slug] || "Сообщение агенту";
  renderMessages();
  renderFiles();
}

async function loadFiles() {
  try {
    const data = await api("/files");
    state.files = data.files;
    const ids = new Set(state.files.map((f) => f.id));
    state.selected.forEach((id) => { if (!ids.has(id)) state.selected.delete(id); });
  } catch (_) { state.files = []; }
  renderFiles();
}

function renderFiles() {
  const list = $("files");
  list.replaceChildren();
  const accepted = state.agent ? state.agent.accepted_file_purposes : [];
  for (const f of state.files) {
    const usable = accepted.includes(f.purpose);
    const box = el("input", { type: "checkbox", title: usable ? "Использовать в запросе" : "Не подходит для этого агента" });
    box.checked = usable && state.selected.has(f.id);
    box.disabled = !usable;
    box.addEventListener("change", () => { box.checked ? state.selected.add(f.id) : state.selected.delete(f.id); updateSelected(); });
    const del = el("button", { class: "btn small ghost", type: "button", title: "Удалить файл", onclick: () => deleteFile(f.id) }, "✕");
    list.append(el("li", {}, box,
      el("span", { class: "fname", title: f.filename }, f.filename),
      el("span", { class: "tag" }, f.purpose), del));
  }
  updateSelected();
}

function updateSelected() {
  const accepted = state.agent ? state.agent.accepted_file_purposes : [];
  const ids = state.files.filter((f) => state.selected.has(f.id) && accepted.includes(f.purpose));
  $("selectedFiles").textContent = ids.length ? `Файлы в запросе: ${ids.map((f) => f.filename).join(", ")}` : "";
}

async function upload(ev) {
  ev.preventDefault();
  const input = $("fileInput");
  const status = $("uploadStatus");
  if (!input.files.length) return;
  const fd = new FormData();
  fd.append("file", input.files[0]);
  fd.append("purpose", $("purpose").value);
  status.className = "status-line";
  status.textContent = "Загрузка…";
  try {
    const f = await api("/files/upload", { method: "POST", body: fd });
    state.selected.add(f.id);
    status.className = "status-line ok";
    status.textContent = `Загружено: ${f.filename} (${f.char_count} символов` +
      (f.chunk_count ? `, ${f.chunk_count} фрагм.` : "") + ")" +
      (f.warnings.length ? ` · ${f.warnings.join(", ")}` : "");
    input.value = "";
    await loadFiles();
  } catch (e) {
    status.className = "status-line err";
    status.textContent = `Ошибка (${e.code}): ${e.message}`;
  }
}

async function deleteFile(id) {
  try {
    await api(`/files/${encodeURIComponent(id)}`, { method: "DELETE" });
    state.selected.delete(id);
    await loadFiles();
  } catch (e) {
    $("uploadStatus").className = "status-line err";
    $("uploadStatus").textContent = `Ошибка удаления (${e.code}): ${e.message}`;
  }
}

// ── Chat ─────────────────────────────────────────────────────────────────────
function conversation() {
  const slug = state.agent ? state.agent.slug : "_";
  state.history[slug] = state.history[slug] || [];
  return state.history[slug];
}

function renderMessages() {
  const box = $("messages");
  box.replaceChildren();
  for (const m of conversation()) box.append(renderMessage(m));
  box.scrollTop = box.scrollHeight;
}

function fmtCost(usage) {
  if (usage.estimated_cost === null || usage.estimated_cost === undefined) return "стоимость: н/д (тарифы не заданы)";
  return `≈ ${usage.estimated_cost.toFixed(6)} ${usage.currency}`;
}

function renderMessage(m) {
  if (m.role === "user") return el("div", { class: "msg user" }, el("div", { class: "body" }, m.content));
  if (m.pending) return el("div", { class: "msg assistant" }, el("div", { class: "body pending" }, "Агент работает…"));
  if (m.error) {
    return el("div", { class: "msg assistant error" },
      el("div", { class: "body" }, `Ошибка ${m.error.status || ""} · ${m.error.code}: ${m.error.message}`),
      m.error.details && m.error.details.run_id ? el("div", { class: "meta-row" }, el("span", { class: "chip" }, `run_id: ${m.error.details.run_id}`)) : null);
  }
  const r = m.result;
  const body = el("div", { class: "body" });
  body.append(renderMarkdown(r.response));
  const meta = el("div", { class: "meta-row" },
    el("span", { class: `chip st-${r.status}` }, `статус: ${r.status}`),
    el("span", { class: `chip mode-${r.mode}` }, r.mode === "mock" ? "MOCK — не ИИ" : r.mode === "openai" ? `OpenAI · ${r.model}` : "без вызова LLM"),
    el("span", { class: "chip" }, `язык: ${r.language}`),
    el("span", { class: "chip" }, `⏱ ${r.execution_time_ms} мс`),
    el("span", { class: "chip" }, `токены: ${r.usage.input_tokens} вх / ${r.usage.output_tokens} вых`),
    el("span", { class: "chip" }, fmtCost(r.usage)));
  const node = el("div", { class: "msg assistant" }, body, meta);
  if (r.sources && r.sources.length) {
    const ol = el("ol");
    r.sources.forEach((s) => ol.append(el("li", {},
      el("strong", {}, s.filename), ` · фрагмент ${s.chunk_index}` + (s.score !== null ? ` · score ${s.score}` : ""),
      el("span", { class: "excerpt" }, s.excerpt))));
    node.append(el("details", { class: "sources", open: "" }, el("summary", {}, `Источники (${r.sources.length})`), ol));
  }
  if (r.data && r.data.handoff) {
    const hnd = r.data.handoff;
    node.append(el("details", { class: "sources" }, el("summary", {}, "Черновик обращения для менеджера (не отправлен)"),
      el("p", {}, hnd.summary), el("p", { class: "hint" }, `${hnd.category} · приоритет: ${hnd.priority}`)));
  }
  if (r.warnings && r.warnings.length) node.append(el("div", { class: "warnings" }, "⚠ " + r.warnings.join(", ")));
  node.append(el("div", { class: "hint" }, `run_id: ${r.run_id}`));
  return node;
}

async function send(ev) {
  ev.preventDefault();
  if (!state.agent) return;
  const text = $("message").value.trim();
  if (!text) return;
  const conv = conversation();
  const history = conv.filter((m) => (m.role === "user" || m.result) && !m.pending)
    .slice(-10).map((m) => ({ role: m.role === "user" ? "user" : "assistant",
      content: (m.role === "user" ? m.content : m.result.response).slice(0, 4000) }));
  conv.push({ role: "user", content: text });
  const pending = { role: "assistant", pending: true };
  conv.push(pending);
  renderMessages();
  $("message").value = "";
  $("sendBtn").disabled = true;
  const accepted = state.agent.accepted_file_purposes;
  const fileIds = state.files.filter((f) => state.selected.has(f.id) && accepted.includes(f.purpose)).map((f) => f.id);
  const lang = $("language").value;
  const payload = { message: text, file_ids: fileIds, history };
  if (lang !== "auto") payload.language = lang;
  try {
    pending.result = await api(`/agents/${encodeURIComponent(state.agent.slug)}/run`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
  } catch (e) {
    pending.error = { code: e.code, message: e.message, status: e.status, details: e.details };
  }
  pending.pending = false;
  $("sendBtn").disabled = false;
  renderMessages();
}

function init() {
  loadHealth();
  if (!state.key) return;
  loadAgents().then(loadFiles);
}

document.addEventListener("DOMContentLoaded", () => {
  loadKey();
  $("saveKey").addEventListener("click", saveKey);
  $("apiKey").addEventListener("keydown", (e) => { if (e.key === "Enter") saveKey(); });
  $("uploadForm").addEventListener("submit", upload);
  $("chatForm").addEventListener("submit", send);
  $("message").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("chatForm").requestSubmit();
  });
  $("clearChat").addEventListener("click", () => { state.history[state.agent ? state.agent.slug : "_"] = []; renderMessages(); });
  init();
});
