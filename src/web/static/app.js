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
    if (!confirm("Annuler le dernier commit du dépôt cible (git reset --hard HEAD~1) ? Cette action est destructive.")) {
      return;
    }
    const resp = await fetch("/api/control/rollback", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ target_dir: state.targetDir }),
    });
    const data = await resp.json();
    setStatus("control-status", resp.ok ? data.message : data.detail || "Erreur.", !resp.ok);
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

function switchTab(tab) {
  state.activeTab = tab;

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.add("hidden"));
  qs(`tab-${tab}`).classList.remove("hidden");

  clearInterval(state.markdownTabTimer);
  if (tab === "progress") {
    refreshProgressTab();
    state.markdownTabTimer = setInterval(refreshProgressTab, MARKDOWN_TAB_POLL_MS);
  } else if (tab === "benchmarks") {
    refreshBenchmarksTab();
    state.markdownTabTimer = setInterval(refreshBenchmarksTab, MARKDOWN_TAB_POLL_MS);
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
