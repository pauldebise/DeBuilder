"use strict";

// Duplique volontairement les modeles par defaut de
// src/web/routes_session.py::PROVIDERS : c'est une simple aide de
// saisie cote client (pre-remplissage du champ modele), pas une
// validation - la validation reelle reste faite par le backend.
const PROVIDER_DEFAULT_MODELS = {
  "DeepSeek": "deepseek/deepseek-v4-pro",
  "OpenAI": "openai/gpt-5.2-codex",
  "Anthropic": "anthropic/claude-sonnet-5",
  "Autre (custom)": "",
};

const DASHBOARD_POLL_MS = 7000;
const REQUESTS_POLL_MS = 7000;
const MARKDOWN_TAB_POLL_MS = 8000;
const ITERATIONS_POLL_MS = 10000;

const ITERATIONS_LIMIT = 100;
const FAILURES_ALERT_THRESHOLD = 3;
const NOOPS_ALERT_THRESHOLD = 3;

const NO_ALERT_PLACEHOLDERS = new Set([
  "*Aucune alerte.*",
  "*Aucune alerte detectee.*",
]);

const state = {
  targetDir: null,
  activeTab: "dashboard",
  dashboardTimer: null,
  requestsTimer: null,
  markdownTabTimer: null,
  iterationsTimer: null,
  logSource: null,
  userScrolledUp: false,
};

function qs(id) {
  return document.getElementById(id);
}

function jsonHeaders() {
  return { "Content-Type": "application/json" };
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value);
  return div.innerHTML;
}

// Le backend reprend tel quel le texte des anciens callbacks Gradio,
// qui utilisait les shortcodes emoji supportes nativement par
// gr.Markdown (ex: ":warning:"). marked.js ne les interprete pas :
// on les traduit ici, cote presentation, sans toucher au texte
// renvoye par l'API.
const EMOJI_SHORTCODES = {
  ":warning:": "⚠️",
  ":x:": "❌",
  ":arrows_counterclockwise:": "🔄",
};

function replaceEmojiShortcodes(text) {
  let result = text;
  for (const [code, emoji] of Object.entries(EMOJI_SHORTCODES)) {
    result = result.split(code).join(emoji);
  }
  return result;
}

function renderMarkdownInto(elementId, mdText) {
  const el = qs(elementId);
  const text = replaceEmojiShortcodes(mdText || "");
  const html = window.marked ? window.marked.parse(text) : escapeHtml(text);
  el.innerHTML = window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
}

function setStatus(elementId, message, isError) {
  const el = qs(elementId);
  el.textContent = message || "";
  el.classList.toggle("error", Boolean(isError));
  el.classList.toggle("success", !isError && Boolean(message));
}

function setAlertBanner(elementId, mdText) {
  const el = qs(elementId);
  if (!mdText) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  const text = replaceEmojiShortcodes(mdText);
  const html = window.marked ? window.marked.parse(text) : escapeHtml(text);
  el.innerHTML = window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
}

function hasRealAlert(text) {
  if (!text) return false;
  return !NO_ALERT_PLACEHOLDERS.has(text.trim());
}

function showScreen(id) {
  document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
  qs(id).classList.remove("hidden");
}

// --- Ecran de configuration ---------------------------------------------

function bindConfigForm() {
  qs("f-provider").addEventListener("change", () => {
    const defaultModel = PROVIDER_DEFAULT_MODELS[qs("f-provider").value];
    if (defaultModel !== undefined) {
      qs("f-model").value = defaultModel;
    }
  });

  qs("config-form").addEventListener("submit", async (evt) => {
    evt.preventDefault();
    const payload = {
      repo_url: qs("f-repo-url").value.trim(),
      workspace_dir: qs("f-workspace-dir").value.trim(),
      instructions: qs("f-instructions").value.trim(),
      provider: qs("f-provider").value,
      model: qs("f-model").value.trim(),
      api_key: qs("f-api-key").value.trim(),
      github_token: qs("f-github-token").value.trim(),
      git_name: qs("f-git-name").value.trim(),
      git_email: qs("f-git-email").value.trim(),
    };

    setStatus("config-status", "Démarrage en cours…", false);

    try {
      const resp = await fetch("/api/session/start", {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setStatus("config-status", data.detail || "Erreur inconnue.", true);
        return;
      }
      startDashboard(data.target_dir);
    } catch (err) {
      setStatus("config-status", "Erreur réseau : " + err.message, true);
    }
  });
}

// --- Tableau de bord ------------------------------------------------------

async function refreshDashboard() {
  const resp = await fetch(`/api/dashboard?target_dir=${encodeURIComponent(state.targetDir)}`);
  if (!resp.ok) return;
  const data = await resp.json();

  renderMarkdownInto("activity-text", data.activity_text);
  setAlertBanner("system-alerts", data.system_alerts);
  setAlertBanner("watchdog-alerts", hasRealAlert(data.alerts_text) ? data.alerts_text : "");
  renderBenchmarksSummary(data.benchmarks);
}

function renderBenchmarksSummary(rows) {
  const el = qs("benchmarks-summary");
  if (!rows || rows.length === 0) {
    el.innerHTML = '<p class="muted">Aucune métrique pour le moment.</p>';
    return;
  }
  const last = rows[rows.length - 1];
  el.innerHTML = Object.entries(last)
    .map(
      ([key, value]) =>
        `<div class="kv"><span class="kv-key">${escapeHtml(key)}</span>` +
        `<span class="kv-val">${escapeHtml(value)}</span></div>`
    )
    .join("");
}

async function refreshRequests() {
  const resp = await fetch(`/api/requests?target_dir=${encodeURIComponent(state.targetDir)}`);
  if (!resp.ok) return;
  const data = await resp.json();
  renderMarkdownInto("requests-content", data.content);
}

async function refreshTags() {
  const resp = await fetch(`/api/tags?target_dir=${encodeURIComponent(state.targetDir)}`);
  if (!resp.ok) return;
  const data = await resp.json();
  populateRollbackTargets(data.tags || []);
}

function populateRollbackTargets(tags) {
  const select = qs("rollback-target");
  if (!select) return;
  const current = select.value;
  select.innerHTML = '<option value="">Dernier commit (HEAD~1)</option>';
  for (const tag of tags) {
    const option = document.createElement("option");
    option.value = tag;
    option.textContent = tag;
    select.appendChild(option);
  }
  if (Array.from(select.options).some((opt) => opt.value === current)) {
    select.value = current;
  }
}

function bindDashboardControls() {
  qs("suggestion-form").addEventListener("submit", async (evt) => {
    evt.preventDefault();
    const message = qs("suggestion-input").value;
    const resp = await fetch("/api/suggestions", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ target_dir: state.targetDir, message }),
    });
    const data = await resp.json();
    setStatus("suggestion-status", resp.ok ? data.message : data.detail || "Erreur.", !resp.ok);
    if (resp.ok) qs("suggestion-input").value = "";
  });

  qs("respond-form").addEventListener("submit", async (evt) => {
    evt.preventDefault();
    const response = qs("respond-input").value;
    const resp = await fetch("/api/requests/respond", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ target_dir: state.targetDir, response }),
    });
    const data = await resp.json();
    setStatus("respond-status", resp.ok ? data.message : data.detail || "Erreur.", !resp.ok);
    if (resp.ok) qs("respond-input").value = "";
    refreshRequests();
  });

  qs("kill-btn").addEventListener("click", async () => {
    if (!confirm("Arrêter l'agent (kill-switch) ? L'agent s'arrêtera à la fin de l'itération en cours.")) {
      return;
    }
    const resp = await fetch("/api/control/kill", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ target_dir: state.targetDir }),
    });
    const data = await resp.json();
    setStatus("control-status", resp.ok ? data.message : data.detail || "Erreur.", !resp.ok);
  });

  qs("rollback-btn").addEventListener("click", async () => {
    const to = qs("rollback-target").value;
    const label = to ? `revenir au tag ${to}` : "annuler le dernier commit (HEAD~1)";
    if (!confirm(`Rollback : ${label} (git reset --hard) ? Cette action est destructive.`)) {
      return;
    }
    const resp = await fetch("/api/control/rollback", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ target_dir: state.targetDir, to }),
    });
    const data = await resp.json();
    setStatus("control-status", resp.ok ? data.message : data.detail || "Erreur.", !resp.ok);
    if (resp.ok) refreshTags();
  });

  qs("barrier-enable-btn").addEventListener("click", () => setBarrier(true));
  qs("barrier-disable-btn").addEventListener("click", () => setBarrier(false));

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

async function setBarrier(enabled) {
  const barrierType = qs("barrier-type").value;
  const resp = await fetch("/api/control/barrier", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ target_dir: state.targetDir, barrier_type: barrierType, enabled }),
  });
  const data = await resp.json();
  setStatus("barrier-status", resp.ok ? data.message : data.detail || "Erreur.", !resp.ok);
}

// --- Onglets Progression / Benchmarks (contenu brut) ---------------------

async function refreshProgressTab() {
  const resp = await fetch(`/api/progress?target_dir=${encodeURIComponent(state.targetDir)}`);
  if (!resp.ok) return;
  const data = await resp.json();
  renderMarkdownInto("progress-full", data.content);
}

async function refreshBenchmarksTab() {
  const resp = await fetch(`/api/benchmarks?target_dir=${encodeURIComponent(state.targetDir)}`);
  if (!resp.ok) return;
  const data = await resp.json();
  renderMarkdownInto("benchmarks-full", data.content);
}

// --- Onglet Itérations (journal ITERATIONS.jsonl) -------------------------

async function refreshIterationsTab() {
  const resp = await fetch(
    `/api/iterations?target_dir=${encodeURIComponent(state.targetDir)}&limit=${ITERATIONS_LIMIT}`
  );
  if (!resp.ok) return;
  const data = await resp.json();
  const entries = data.entries || [];
  renderIterationsSummary(entries, data.total);
  drawIterationsChart(entries);
  renderIterationsAnomalies(entries);
}

function formatDuration(seconds) {
  const s = Number(seconds) || 0;
  if (s >= 3600) return `${Math.round(s / 3600)}h${Math.round((s % 3600) / 60)}m`;
  if (s >= 60) return `${Math.floor(s / 60)}m${Math.round(s % 60)}s`;
  return `${Math.round(s * 10) / 10}s`;
}

function renderIterationsSummary(entries, total) {
  const el = qs("iterations-summary");
  const failures = entries.filter((e) => e.failure_type).length;
  const noops = entries.filter((e) => e.no_op).length;
  const durations = entries.map((e) => e.duration_seconds || 0);
  const avg = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : 0;
  const lastModel = entries.length ? entries[entries.length - 1].model : "";
  const tokens = entries
    .map((e) => (e.usage && e.usage.total_tokens) || null)
    .filter((v) => v != null);

  let burnRate;
  if (tokens.length) {
    const avgTokens = Math.round(tokens.reduce((a, b) => a + b, 0) / tokens.length);
    burnRate = `${avgTokens.toLocaleString("fr-FR")} tokens/itération`;
  } else {
    burnRate = "non exposé par le modèle";
  }

  const kv = (key, value) =>
    `<div class="kv"><span class="kv-key">${escapeHtml(key)}</span>` +
    `<span class="kv-val">${escapeHtml(value)}</span></div>`;

  el.innerHTML =
    kv("Itérations (journal)", `${total ?? entries.length} au total, ${entries.length} affichées`) +
    kv("Échecs", String(failures)) +
    kv("No-ops", String(noops)) +
    kv("Durée moyenne", formatDuration(avg)) +
    kv("Dernier modèle", lastModel || "(défaut)") +
    kv("Burn rate", burnRate);
}

function drawIterationsChart(entries) {
  const canvas = qs("iterations-chart");
  if (!canvas) return;
  const parentWidth = Math.max(canvas.parentElement.clientWidth - 40, 60);
  const chartHeight = 220;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = parentWidth * dpr;
  canvas.height = chartHeight * dpr;
  canvas.style.width = `${parentWidth}px`;
  canvas.style.height = `${chartHeight}px`;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, parentWidth, chartHeight);

  ctx.fillStyle = "#9aa0ac";
  ctx.font = "12px sans-serif";
  if (!entries.length) {
    ctx.fillText("Aucune itération enregistrée pour le moment.", 0, 20);
    return;
  }

  const axisX = 44;
  const axisY = 24;
  const chartW = parentWidth - axisX - 6;
  const chartH = chartHeight - axisY - 30;
  const maxDuration = Math.max(1, ...entries.map((e) => e.duration_seconds || 0));
  const slotW = chartW / entries.length;
  const barW = Math.max(2, Math.min(16, slotW * 0.7));

  const colorFor = (entry) => {
    if (entry.no_op) return "#f5a623";
    if (entry.failure_type) return "#e5484d";
    return "#4f8cff";
  };

  entries.forEach((entry, i) => {
    const x = axisX + i * slotW + (slotW - barW) / 2;
    const h = Math.max(1, ((entry.duration_seconds || 0) / maxDuration) * chartH);
    ctx.fillStyle = colorFor(entry);
    ctx.fillRect(x, axisY + chartH - h, barW, h);
  });

  ctx.strokeStyle = "#2a2f3a";
  ctx.beginPath();
  ctx.moveTo(axisX, axisY + chartH);
  ctx.lineTo(axisX + chartW, axisY + chartH);
  ctx.stroke();

  ctx.fillStyle = "#9aa0ac";
  ctx.font = "10px sans-serif";
  ctx.fillText(formatDuration(maxDuration), 2, axisY + 4);
  ctx.fillText("0", axisX - 16, axisY + chartH + 3);

  const labelStep = Math.max(1, Math.ceil(entries.length / 14));
  entries.forEach((entry, i) => {
    if (i % labelStep === 0) {
      ctx.fillText(String(entry.iteration ?? i + 1), axisX + i * slotW, axisY + chartH + 14);
    }
  });
}

function renderIterationsAnomalies(entries) {
  const el = qs("iterations-anomalies");
  if (!entries.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }

  const issues = [];

  let consecutiveFailures = 0;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].failure_type) consecutiveFailures += 1;
    else break;
  }
  if (consecutiveFailures >= FAILURES_ALERT_THRESHOLD) {
    issues.push(`**${consecutiveFailures} échecs consécutifs** (seuil ${FAILURES_ALERT_THRESHOLD}).`);
  }

  let consecutiveNoops = 0;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].no_op) consecutiveNoops += 1;
    else break;
  }
  if (consecutiveNoops >= NOOPS_ALERT_THRESHOLD) {
    issues.push(`**${consecutiveNoops} itérations no-op consécutives** (seuil ${NOOPS_ALERT_THRESHOLD}).`);
  }

  const durations = entries
    .map((e) => e.duration_seconds || 0)
    .filter((d) => d > 0)
    .sort((a, b) => a - b);
  if (durations.length >= 5) {
    const median = durations[Math.floor(durations.length / 2)];
    const last = entries[entries.length - 1].duration_seconds || 0;
    if (median > 0 && last > 2 * median) {
      issues.push(
        `**Burn rate suspect** : la dernière itération (${formatDuration(last)}) dure plus de 2× la médiane (${formatDuration(median)}).`
      );
    }
  }

  const tokens = entries
    .map((e) => (e.usage && e.usage.total_tokens) || null)
    .filter((v) => v != null);
  if (tokens.length >= 5) {
    const sorted = [...tokens].sort((a, b) => a - b);
    const medianTokens = sorted[Math.floor(sorted.length / 2)];
    const lastTokens = tokens[tokens.length - 1];
    if (medianTokens > 0 && lastTokens > 2 * medianTokens) {
      issues.push(
        `**Consommation de tokens anormale** : dernière itération à ${lastTokens.toLocaleString("fr-FR")} tokens (> 2× la médiane de ${medianTokens.toLocaleString("fr-FR")}).`
      );
    }
  }

  if (!issues.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  const text = issues.map((issue) => `- ${issue}`).join("\n");
  const html = window.marked ? window.marked.parse(text) : escapeHtml(text);
  el.innerHTML = window.DOMPurify ? window.DOMPurify.sanitize(html) : html;
}

function switchTab(tab) {
  state.activeTab = tab;

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.add("hidden"));
  qs(`tab-${tab}`).classList.remove("hidden");

  clearInterval(state.markdownTabTimer);
  clearInterval(state.iterationsTimer);
  if (tab === "progress") {
    refreshProgressTab();
    state.markdownTabTimer = setInterval(refreshProgressTab, MARKDOWN_TAB_POLL_MS);
  } else if (tab === "benchmarks") {
    refreshBenchmarksTab();
    state.markdownTabTimer = setInterval(refreshBenchmarksTab, MARKDOWN_TAB_POLL_MS);
  } else if (tab === "iterations") {
    refreshIterationsTab();
    state.iterationsTimer = setInterval(refreshIterationsTab, ITERATIONS_POLL_MS);
  }
}

// --- Flux de logs en direct (SSE) -----------------------------------------

function startLogStream() {
  if (state.logSource) {
    state.logSource.close();
  }

  const terminal = qs("log-terminal");
  terminal.innerHTML = "";
  state.userScrolledUp = false;

  terminal.addEventListener("scroll", () => {
    const distanceFromBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight;
    state.userScrolledUp = distanceFromBottom > 20;
  });

  const source = new EventSource(`/api/logs/stream?target_dir=${encodeURIComponent(state.targetDir)}`);
  source.onmessage = (event) => {
    const line = document.createElement("div");
    line.className = "log-line";
    line.textContent = event.data;
    terminal.appendChild(line);

    while (terminal.childNodes.length > 2000) {
      terminal.removeChild(terminal.firstChild);
    }
    if (!state.userScrolledUp) {
      terminal.scrollTop = terminal.scrollHeight;
    }
  };
  // EventSource reconnecte nativement en cas de coupure ; le serveur
  // etant stateless, rien a faire cote client au-dela du comportement
  // par defaut du navigateur.
  state.logSource = source;
}

// --- Demarrage --------------------------------------------------------

function startDashboard(targetDir) {
  state.targetDir = targetDir;
  showScreen("dashboard-screen");

  clearInterval(state.dashboardTimer);
  clearInterval(state.requestsTimer);

  refreshDashboard();
  refreshRequests();
  refreshTags();
  startLogStream();
  switchTab("dashboard");

  state.dashboardTimer = setInterval(refreshDashboard, DASHBOARD_POLL_MS);
  state.requestsTimer = setInterval(refreshRequests, REQUESTS_POLL_MS);
}

async function init() {
  bindConfigForm();
  bindDashboardControls();

  try {
    const resp = await fetch("/api/session");
    const data = await resp.json();
    if (data && data.target_dir) {
      startDashboard(data.target_dir);
      return;
    }
  } catch (err) {
    // Pas de session active detectable : on retombe sur l'ecran de
    // configuration, comme si aucune session n'existait.
  }
  showScreen("config-screen");
}

document.addEventListener("DOMContentLoaded", init);
