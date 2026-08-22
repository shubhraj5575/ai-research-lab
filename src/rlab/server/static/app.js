/* AI Research Lab dashboard — dependency-free vanilla JS. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = {
  sessionId: null,
  sessions: [],
  detail: null,
  selectedExperiment: null,
  lastEventSeq: 0,
};

// ---------------------------------------------------------------- utils
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function chip(label) {
  return el("span", { class: `chip ${String(label).toUpperCase()}`, text: label });
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ---------------------------------------------------------------- tabs
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "graph" && state.sessionId) loadGraph();
    if (btn.dataset.tab === "timeline") loadEvents();
  });
});

// ---------------------------------------------------------------- sessions
async function loadSessions() {
  const data = await api("/api/sessions");
  state.sessions = data.sessions;
  const select = $("#session-select");
  select.innerHTML = "";
  for (const s of data.sessions) {
    select.appendChild(
      el("option", { value: s.id, text: `${s.id.slice(0, 14)}… · ${s.domain} · ${s.status}` })
    );
  }
  if (state.sessions.length > 0) {
    if (!state.sessionId || !state.sessions.some((s) => s.id === state.sessionId)) {
      state.sessionId = state.sessions[0].id;
    }
    select.value = state.sessionId;
    await refreshOverview();
  }
}

$("#session-select").addEventListener("change", async (e) => {
  state.sessionId = e.target.value;
  state.selectedExperiment = null;
  await refreshOverview();
});

// ---------------------------------------------------------------- overview
async function refreshOverview() {
  if (!state.sessionId) return;
  const data = await api(`/api/sessions/${state.sessionId}`);
  state.detail = data;

  $("#question").textContent = data.session.question;
  $("#session-meta").textContent =
    `domain=${data.session.domain} · status=${data.session.status}`;
  $("#hyp-count").textContent = `(${data.hypotheses.length})`;

  // hypotheses table
  const tbody = $("#hyp-table tbody");
  tbody.innerHTML = "";
  for (const h of [...data.hypotheses].sort((a, b) => a.number - b.number)) {
    tbody.appendChild(el("tr", {},
      el("td", { text: `H${h.number}` }),
      el("td", {}, chip(h.status)),
      el("td", { text: h.claim.length > 150 ? h.claim.slice(0, 147) + "…" : h.claim }),
      el("td", { text: h.confidence != null ? h.confidence.toFixed(2) : "—" }),
    ));
  }

  // champion from latest analysis
  const exps = data.experiments.filter((e) => e.ranking && e.ranking.length);
  if (exps.length > 0) {
    const last = exps[exps.length - 1];
    $("#champion-card").classList.remove("hidden");
    const [bestVariant, bestMean] = last.ranking[0];
    $("#champion").innerHTML = "";
    $("#champion").appendChild(document.createTextNode(
      `${bestVariant}\nmean ${last.primary_metric} = ${bestMean}` +
      `\n(${last.task}, ${last.budget_label}, n=${last.n_seeds}/variant)`));
  } else {
    $("#champion-card").classList.add("hidden");
  }

  // gaps
  if (data.gaps.length > 0) {
    $("#gap-card").classList.remove("hidden");
    const ul = $("#gaps");
    ul.innerHTML = "";
    for (const g of data.gaps) {
      ul.appendChild(el("li", { text: g.description }));
    }
  }

  // outcome strip
  const strip = $("#outcome-strip");
  strip.innerHTML = "";
  for (const h of [...data.hypotheses].sort((a, b) => a.number - b.number)) {
    strip.appendChild(el("div", { class: `cell ${h.status.toLowerCase()}`, title: h.claim },
      el("b", { text: `H${h.number}` }),
      el("span", { text: h.status }),
    ));
  }
  $("#footer-meta").textContent =
    `session ${data.session.id} · ${data.experiments.length} experiments · ` +
    `config: workers=${data.config.max_parallel_workers}, seeds=${data.config.seeds_per_config}`;

  await renderExperiments();
  if ($("#tab-graph").classList.contains("active")) loadGraph();
  await loadEvents();
}

// ---------------------------------------------------------------- experiments
async function renderExperiments() {
  const data = state.detail;
  const tbody = $("#exp-table tbody");
  tbody.innerHTML = "";
  for (const e of data.experiments) {
    const critCell = e.critique
      ? el("span", {}, chip(e.critique.verdict), " ",
           el("span", { class: "muted small", text: (e.critique.findings || []).join(", ") }))
      : el("span", { class: "muted", text: "—" });
    const openBtn = el("button", { class: "linkish", text: "detail" });
    openBtn.addEventListener("click", () => showExperiment(e.id));
    tbody.appendChild(el("tr", {},
      el("td", { text: `E${e.iteration}` }),
      el("td", { text: `${e.task} · ${e.budget_label}` }),
      el("td", { text: e.variants.join(", ").slice(0, 90) }),
      el("td", { text: String(e.n_seeds) }),
      el("td", {}, chip(e.status)),
      el("td", { text: e.best_variant ?? "—" }),
      el("td", {}, critCell),
      el("td", {}, openBtn),
    ));
  }
  if (state.selectedExperiment) await showExperiment(state.selectedExperiment);
}

async function showExperiment(expId) {
  state.selectedExperiment = expId;
  const e = await api(`/api/experiments/${expId}`);
  $("#exp-detail").classList.remove("hidden");
  $("#exp-detail-title").textContent =
    `E${e.iteration} — ${expId} (${e.config.task}, ${e.config.budget_label})`;

  const body = $("#exp-detail-body");
  body.innerHTML = "";
  body.appendChild(el("pre", { class: "metrics",
    text: JSON.stringify({
      status: e.status,
      seeds_per_variant: e.config.n_seeds,
      seed_root: e.config.seed_root,
      git_commit: e.git_commit,
      code_version: e.code_version,
      spec_hash: e.spec_hash,
      python: e.env_json.python,
      numpy: e.env_json.numpy,
    }, null, 2) }));
  if (e.analysis) {
    body.appendChild(el("h4", { text: `Analysis — primary metric: ${e.analysis.primary_metric}` }));
    for (const c of e.analysis.comparisons) {
      body.appendChild(el("p", { class: "small",
        text: `${c.variant_a} vs ${c.variant_b}: Δ(b−a)=${c.delta} CI=[${c.ci_low}, ${c.ci_high}] ` +
              `adj.p=${c.p_value.toPrecision(3)} d=${c.effect_size} → ` +
              (c.significant ? "SIGNIFICANT" : "n.s.") }));
    }
  }
  if (e.critiques.length > 0) {
    for (const cr of e.critiques) {
      body.appendChild(el("p", { class: "small" }, chip(cr.verdict.toUpperCase()),
        document.createTextNode(` repro-check=${cr.repro_check_passed} · findings: ` +
          cr.issues.map((f) => f.code).join(", "))));
    }
  }
  drawConvergence(e.series);

  const rtbody = $("#runs-table tbody");
  rtbody.innerHTML = "";
  for (const r of e.runs_preview) {
    rtbody.appendChild(el("tr", {},
      el("td", { text: r.variant }), el("td", { text: String(r.seed) }),
      el("td", { text: r.status }),
      el("td", { text: Object.entries(r.metrics).map(([k, v]) => `${k}=${v}`).join("  ")
                    .slice(0, 120) }),
    ));
  }
}

function drawConvergence(seriesData) {
  const svg = $("#conv-chart");
  svg.innerHTML = "";
  const means = seriesData?.means || {};
  const variants = Object.keys(means);
  if (variants.length === 0) return;
  const W = 760, H = 420, ML = 60, MR = 20, MT = 20, MB = 40;
  const pw = W - ML - MR, ph = H - MT - MB;
  let vmax = -Infinity, vmin = Infinity;
  for (const v of variants) for (const x of means[v]) { vmax = Math.max(vmax, x); vmin = Math.min(vmin, x); }
  if (!isFinite(vmax)) return;
  vmin = Math.min(0, vmin);
  const pad = (vmax - vmin) * 0.06 || 1;
  vmax += pad; vmin -= pad * 0.2;
  const colors = ["#4f8cff", "#ef4444", "#10b981", "#f59e0b", "#a78bfa"];
  const n = Math.max(...variants.map((v) => means[v].length));
  const px = (i) => ML + (pw * i) / Math.max(1, n - 1);
  const py = (val) => MT + ph - ph * ((val - vmin) / (vmax - vmin));
  // grid
  for (let g = 0; g <= 5; g++) {
    const val = vmin + ((vmax - vmin) * g) / 5;
    svg.appendChild(el("line", { x1: ML, y1: py(val), x2: W - MR, y2: py(val),
      stroke: "#262b3a", "stroke-width": 1 }));
    const t = el("text", { x: ML - 8, y: py(val) + 4, "text-anchor": "end",
      fill: "#8b93a7", "font-size": 11 });
    t.textContent = val.toPrecision(3);
    svg.appendChild(t);
  }
  variants.forEach((v, idx) => {
    const pts = means[v].map((x, i) => `${px(i)},${py(x)}`).join(" ");
    svg.appendChild(el("polyline", { points: pts, fill: "none",
      stroke: colors[idx % colors.length], "stroke-width": 2 }));
    const lx = ML + 10 + idx * 190, ly = MT + 16 + idx * 18;
    svg.appendChild(el("rect", { x: lx, y: ly - 9, width: 14, height: 4,
      fill: colors[idx % colors.length] }));
    const t = el("text", { x: lx + 20, y: ly - 1, fill: "#e5e7eb", "font-size": 11 });
    t.textContent = v;
    svg.appendChild(t);
  });
}

// ---------------------------------------------------------------- graph
async function loadGraph() {
  if (!state.sessionId) return;
  const data = await api(`/api/graph/${state.sessionId}`);
  const svg = $("#graph-svg");
  svg.innerHTML = "";
  $("#graph-validation").textContent = data.validation.length
    ? "validation issues: " + data.validation.join("; ")
    : "graph validation: OK";

  const KIND_COLOR = { q: "#94a3b8", hy: "#4f8cff", ex: "#22c55e",
                       an: "#eab308", cr: "#f472b6", gp: "#fb923c", sr: "#64748b" };
  // layered layout by kind depth
  const DEPTH = { q: 0, sr: 0, gp: 1, hy: 1, ex: 2, an: 3, cr: 3 };
  const layers = {};
  for (const node of data.nodes) {
    const kind = node.id.split(":")[0];
    const d = DEPTH[kind] ?? 4;
    (layers[d] ||= []).push(node);
  }
  const LAYER_W = 200;
  const pos = {};
  for (const [d, nodes] of Object.entries(layers)) {
    nodes.forEach((node, i) => {
      pos[node.id] = {
        x: 30 + Number(d) * LAYER_W,
        y: 40 + i * 44,
      };
    });
  }
  // edges first
  for (const edge of data.edges) {
    const a = pos[edge.src], b = pos[edge.dst];
    if (!a || !b) continue;
    svg.appendChild(el("line", { x1: a.x + 130, y1: a.y + 12, x2: b.x, y2: b.y + 12,
      stroke: "#334155", "stroke-width": 1.2 }));
  }
  // nodes
  for (const node of data.nodes) {
    const kind = node.id.split(":")[0];
    const p = pos[node.id];
    if (!p) continue;
    const g = el("g", {});
    const color = KIND_COLOR[kind] || "#888";
    const rect = el("rect", { x: p.x, y: p.y, width: 130, height: 24, rx: 6,
      fill: "#171a23", stroke: color, "stroke-width": 1.4 });
    const label = el("text", { x: p.x + 8, y: p.y + 16, fill: "#e5e7eb",
      "font-size": 9.5 });
    label.textContent = node.label.slice(0, 34);
    const titleEl = document.createElementNS("http://www.w3.org/2000/svg", "title");
    titleEl.textContent = `[${node.kind}] ${node.label}`;
    g.appendChild(rect); g.appendChild(label); g.appendChild(titleEl);
    svg.appendChild(g);
  }
  const link = $("#graphml-link");
  link.href = `/api/graph/${state.sessionId}?format=graphml`;
}

// ---------------------------------------------------------------- events
async function loadEvents() {
  if (!state.sessionId) return;
  const data = await api(`/api/events?session=${state.sessionId}&limit=400`);
  const ul = $("#event-log");
  ul.innerHTML = "";
  const rows = data.events.slice().reverse();
  for (const ev of rows) {
    ul.appendChild(eventItem(ev.payload && ev.payload.role
      ? `${ev.type} (${ev.payload.role})` : ev.type, ev.ts, ev.payload));
  }
}

function eventItem(type, ts, payload) {
  const li = el("li", {});
  li.appendChild(el("span", { class: "t", text: fmtTime(ts) }));
  li.appendChild(el("span", { text: type }));
  if (payload) {
    const bits = [];
    for (const k of ["iteration", "number", "strategy", "verdict", "status",
                     "best", "findings", "experiment_id", "hypothesis_id"]) {
      if (payload[k] !== undefined) bits.push(`${k}=${JSON.stringify(payload[k])}`);
    }
    if (bits.length) {
      li.appendChild(el("span", { class: "muted small",
        text: "  " + bits.join(" ").slice(0, 160) }));
    }
  }
  return li;
}

// ---------------------------------------------------------------- live SSE
let es = null;
function connectStream() {
  if (es) es.close();
  es = new EventSource("/api/stream");
  es.onopen = () => {
    $("#live-dot").className = "dot on";
    $("#live-label").textContent = "live";
  };
  es.onerror = () => {
    $("#live-dot").className = "dot off";
    $("#live-label").textContent = "reconnecting…";
  };
  es.onmessage = (msg) => {
    try {
      const ev = JSON.parse(msg.data);
      pushFeed(ev);
      const interesting = ["iteration.completed", "hypothesis.proposed",
                           "experiment.completed", "agent.critic.reviewed",
                           "session.finished"];
      if (interesting.some((t) => ev.type.endsWith(t)) &&
          (!state.sessionId || ev.session_id === state.sessionId)) {
        refreshOverview();
      }
    } catch (_) { /* ignore malformed */ }
  };
}

function pushFeed(ev) {
  const feed = $("#feed");
  feed.prepend(eventItem(ev.type, ev.ts, ev.payload));
  while (feed.children.length > 80) feed.lastChild.remove();
}

// ---------------------------------------------------------------- boot
(async function boot() {
  try {
    const health = await api("/api/health");
    $("#footer-meta").textContent =
      `executor=${health.executor} · db=${health.db} · uptime=${health.uptime_s}s`;
  } catch (_) { /* server info optional */ }
  await loadSessions();
  connectStream();
  setInterval(refreshOverview, 5000);
})();
