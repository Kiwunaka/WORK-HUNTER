const state = {
  jobs: [],
  selectedId: null,
  config: null,
  chatMessages: [],
  chatJobId: null,
  profile: null,
  autoSyncInterval: null,
  autoSyncMinutes: 0,
  darkTheme: false,
  agent: {
    preflight: null,
    digest: null,
    research: null,
    operations: null,
    approvals: [],
    templates: [],
    blacklist: [],
    labQuickCalls: [],
    labSnippets: [],
    events: [],
    tasks: [],
  },
};

const $ = (selector) => document.querySelector(selector);

const ROUTES = {
  inbox: { path: "/jobs", title: "Jobs", summary: "Sync sources, then sort by score.", actions: true },
  calendar: { path: "/calendar", title: "Calendar", summary: "Interviews, follow-ups, and reminders.", actions: false },
  favorites: { path: "/favorites", title: "Favorites", summary: "Saved vacancies and notes.", actions: false },
  chat: { path: "/chat", title: "AI Assistant", summary: "Chat with optional selected-job context.", actions: false },
  agent: { path: "/agent", title: "HH Agent", summary: "Local HH runs, approvals, research, and dry-run apply plans.", actions: false },
  settings: { path: "/settings", title: "Settings", summary: "Profiles, HH auth, AI backend, resumes, and config.", actions: false },
  sources: { path: "/sources", title: "Sources", summary: "Source health and last sync state.", actions: false },
  stats: { path: "/stats", title: "Stats", summary: "Pipeline and score distribution.", actions: false },
  trends: { path: "/trends", title: "Trends", summary: "Run market trend analysis explicitly from this page.", actions: false },
};

const PATH_TO_VIEW = Object.fromEntries(Object.entries(ROUTES).map(([view, route]) => [route.path, view]));
PATH_TO_VIEW["/"] = "inbox";

function routeViewFromPath(pathname) {
  return PATH_TO_VIEW[pathname] || "inbox";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

async function loadJobs() {
  const source = $("#source-filter").value;
  const minScore = $("#min-score-filter").value || "0";
  const params = new URLSearchParams({ limit: "200", min_score: minScore });
  if (source) params.set("source", source);
  state.jobs = await api(`/api/jobs?${params.toString()}`);
  renderJobs();
  updateSummary();
}

function renderJobs() {
  const body = $("#jobs-body");
  body.innerHTML = "";
  for (const job of state.jobs) {
    const tr = document.createElement("tr");
    tr.className = job.id === state.selectedId ? "selected" : "";
    tr.addEventListener("click", () => selectJob(job.id));
    const score = job.score ? job.score.total_score : "-";
    const scoreClass = score === "-" ? "" : score >= 70 ? " high" : score < 35 ? " low" : "";
    const checked = selectedJobIds.has(Number(job.id)) ? "checked" : "";
    tr.innerHTML = `
      <td><input type="checkbox" class="job-checkbox" data-job-id="${job.id}" onclick="event.stopPropagation();toggleJobSelect(${job.id}, this.checked)" ${checked}></td>
      <td><span class="score${scoreClass}">${escapeHtml(String(score))}</span></td>
      <td>
        <div class="title">${escapeHtml(job.title || "Без названия")}</div>
        <div class="meta">${escapeHtml(job.company || "Компания не указана")}</div>
        <div class="meta">${escapeHtml([job.salary_text, job.location, job.remote ? "remote" : ""].filter(Boolean).join(" · "))}</div>
      </td>
      <td>${escapeHtml(job.source)}</td>
      <td>${escapeHtml(job.status || "new")}</td>
    `;
    body.appendChild(tr);
  }
  syncBulkCheckboxes();
}

async function selectJob(id) {
  state.selectedId = id;
  renderJobs();
  const job = await api(`/api/jobs/${id}`);
  renderDetail(job);
  updateChatContext();
  loadJobNote();
  try { $("#ai-panels").style.display = "block"; } catch (e) { /* ignore */ }
}

function renderDetail(job) {
  const score = job.score;
  const reasons = score?.reasons || [];
  const flags = score?.red_flags || [];
  const letter = job.latest_letter?.body || "";
  $("#job-detail").className = "detail";
  $("#job-detail").innerHTML = `
    <h2>${escapeHtml(job.title)}</h2>
    <p>${escapeHtml(job.company || "Компания не указана")} · ${escapeHtml(job.source)} · <a href="${escapeAttr(job.url)}" target="_blank" rel="noreferrer">открыть</a></p>
    <div class="detail-actions">
      <button onclick="markSelected('saved')">Сохранить</button>
      <button onclick="markSelected('hidden')">Скрыть</button>
      <button onclick="markSelected('applied')">Откликнулся</button>
      <button onclick="prepareLetter()">Подготовить письмо</button>
      <button onclick="prepareLetterAi()">AI письмо</button>
      <button onclick="fetchFullDescription()">Полное описание</button>
      <button onclick="applyHh(true)">HH apply plan</button>
      <button onclick="applyHh(false)" class="primary">Confirm HH apply</button>
      <button onclick="shareToTelegram()">📤 Telegram</button>
      <button onclick="smartClassify()">AI классификация</button>
      <button onclick="parseJobStructure()">Структура</button>
      <button onclick="runGapAnalysis()">Gap-анализ</button>
    </div>
    <div class="section-title">Score</div>
    <div class="chips">
      <span class="chip">total ${escapeHtml(String(score?.total_score ?? "-"))}</span>
      <span class="chip">title ${escapeHtml(String(score?.title_score ?? "-"))}</span>
      <span class="chip">skills ${escapeHtml(String(score?.skills_score ?? "-"))}</span>
      <span class="chip">salary ${escapeHtml(String(score?.salary_score ?? "-"))}</span>
      <span class="chip">remote ${escapeHtml(String(score?.remote_score ?? "-"))}</span>
    </div>
    <div class="section-title">Причины</div>
    <div class="chips">${reasons.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("") || `<span class="chip">Пока нет причин</span>`}</div>
    <div class="section-title">Красные флаги</div>
    <div class="chips">${flags.map((item) => `<span class="chip red">${escapeHtml(item)}</span>`).join("") || `<span class="chip">Нет</span>`}</div>
    <div class="section-title">Описание</div>
    <p>${escapeHtml(job.description || "Описание не загружено.")}</p>
    <div class="section-title">Письмо</div>
    <textarea id="detail-letter-box" data-letter-box class="letter" spellcheck="true">${escapeHtml(letter)}</textarea>
    <div id="action-output" class="meta"></div>
  `;
  setLetterValue(letter);
}

async function syncJobs() {
  setBusy("#sync-button", true);
  try {
    const result = await api("/api/sync", { method: "POST", body: JSON.stringify({ score: true }) });
    $("#summary-line").textContent = `Синхронизация завершена: ${JSON.stringify(result)}`;
    await loadJobs();
    await loadSources();
  } finally {
    setBusy("#sync-button", false);
  }
}

async function scoreJobs() {
  setBusy("#score-button", true);
  try {
    const result = await api("/api/score", { method: "POST", body: "{}" });
    $("#summary-line").textContent = `Пересчитано: ${result.scored}`;
    await loadJobs();
  } finally {
    setBusy("#score-button", false);
  }
}

async function markSelected(status) {
  if (!state.selectedId) return;
  await api(`/api/jobs/${state.selectedId}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
  if (status === "applied") {
    try {
      await api(`/api/jobs/${state.selectedId}/apply`, { method: "POST", body: JSON.stringify({ status: "applied" }) });
    } catch (e) { /* ignore if endpoint missing */ }
  }
  try { await api(`/api/jobs/${state.selectedId}/record-event`, { method: "POST", body: JSON.stringify({ action: status }) }); } catch (e) {}
  await loadJobs();
  await selectJob(state.selectedId);
}

async function prepareLetter() {
  if (!state.selectedId) return;
  const draft = await api(`/api/jobs/${state.selectedId}/letter`, { method: "POST", body: "{}" });
  setLetterValue(draft.body);
}

async function prepareLetterAi() {
  if (!state.selectedId) return;
  setBusy("[onclick='prepareLetterAi()']", true);
  try {
    const draft = await api(`/api/jobs/${state.selectedId}/letter-ai`, { method: "POST", body: "{}" });
    setLetterValue(draft.body);
  } catch (err) {
    $("#action-output").textContent = `Ошибка AI: ${err.message}. Проверь API-ключ в настройках.`;
  } finally {
    setBusy("[onclick='prepareLetterAi()']", false);
  }
}

async function applyHh(dryRun = true) {
  if (!state.selectedId) return;

  const letter = getLetterValue();
  if (dryRun) {
    const result = await api(`/api/jobs/${state.selectedId}/apply-plan`, {
      method: "POST",
      body: JSON.stringify({ letter }),
    });
    $("#action-output").textContent = JSON.stringify(result, null, 2);
    return;
  }

  if (!confirm("This will send a real HH application from your account. Continue?")) {
    return;
  }

  const plan = await api(`/api/jobs/${state.selectedId}/apply-plan`, {
    method: "POST",
    body: JSON.stringify({ letter }),
  });
  if (plan.status !== "ready") {
    $("#action-output").textContent = JSON.stringify(plan, null, 2);
    return;
  }

  const result = await api(`/api/jobs/${state.selectedId}/confirm-apply`, {
    method: "POST",
    body: JSON.stringify({ confirm: true, resume_id: plan.resume_id, letter }),
  });
  $("#action-output").textContent = JSON.stringify(result, null, 2);

  if (result && result.status === "applied") {
    await loadJobs();
    await selectJob(state.selectedId);
  }
}

async function loadProfile() {
  try {
    state.profile = await api("/api/profile");
    renderProfileSwitcher();
    renderProfileForm();
  } catch (err) {
    console.error("Failed to load profile:", err);
  }
}

function renderProfileSwitcher() {
  const sel = $("#profile-select");
  if (!state.profile) return;
  sel.innerHTML = "";
  for (const id of state.profile.available) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = id;
    if (id === state.profile.active) opt.selected = true;
    sel.appendChild(opt);
  }
}

function renderProfileForm() {
  if (!state.profile) return;
  const d = state.profile.data || {};
  $("#profile-queries").value = (d.queries || []).join(", ");
  $("#profile-roles").value = (d.desired_roles || []).join(", ");
  $("#profile-stopwords").value = (d.stop_words || []).join(", ");
  $("#profile-must-skills").value = (d.must_have_skills || []).join(", ");
  $("#profile-nice-skills").value = (d.nice_to_have_skills || []).join(", ");
  $("#profile-active-label").textContent = `Активный профиль: ${state.profile.active}`;
}

async function switchProfile(profileId) {
  try {
    const result = await api("/api/profile/switch", {
      method: "POST",
      body: JSON.stringify({ profile: profileId }),
    });
    state.profile = { active: result.active, available: state.profile.available, data: result.data };
    renderProfileForm();
    $("#summary-line").textContent = `Профиль переключён на: ${profileId}. Синхронизируй и пересчитай score.`;
  } catch (err) {
    alert("Ошибка переключения профиля: " + err.message);
  }
}

async function saveProfile() {
  const splitList = (text) => text.split(",").map((s) => s.trim()).filter(Boolean);
  const data = {
    queries: splitList($("#profile-queries").value),
    desired_roles: splitList($("#profile-roles").value),
    stop_words: splitList($("#profile-stopwords").value),
    must_have_skills: splitList($("#profile-must-skills").value),
    nice_to_have_skills: splitList($("#profile-nice-skills").value),
  };
  try {
    const updated = await api("/api/profile", { method: "POST", body: JSON.stringify(data) });
    state.profile.data = updated;
    $("#config-editor").value = JSON.stringify(state.config, null, 2);
    $("#summary-line").textContent = "Профиль сохранён. Синхронизируй источники и пересчитай score для обновления.";
  } catch (err) {
    alert("Ошибка сохранения профиля: " + err.message);
  }
}

async function loadConfig() {
  state.config = await api("/api/config");
  $("#config-editor").value = JSON.stringify(state.config, null, 2);

  if (state.config && state.config.sources && state.config.sources.hh) {
    $("#hh-token-input").value = state.config.sources.hh.access_token || "";
    $("#hh-allow-broad").checked = !!state.config.sources.hh.allow_broad_apply;
  }

  if (state.config && state.config.ai) {
    if (state.config.ai.api_key === "***") {
      $("#ai-key-input").placeholder = "*** (скрыт) — введи новый чтобы заменить";
    } else {
      $("#ai-key-input").value = state.config.ai.api_key || "";
    }
    $("#ai-model-input").value = state.config.ai.model || "google/gemini-2.5-flash";
    $("#ai-backend-select").value = state.config.ai.backend || "direct";
    $("#opencode-transport-select").value = state.config.ai.opencode_transport || "cli";
    $("#opencode-command-input").value = state.config.ai.opencode_command || "opencode";
    $("#opencode-model-input").value = state.config.ai.opencode_model || "";
    $("#opencode-agent-input").value = state.config.ai.opencode_agent || "work-hunter-ai";
    $("#opencode-server-input").value = state.config.ai.opencode_server_url || "http://127.0.0.1:4096";
  }

  if (state.config && state.config.ui && state.config.ui.auto_sync) {
    const minutes = state.config.ui.auto_sync || 0;
    $("#auto-sync-interval").value = minutes;
    if (minutes > 0) startAutoSync(minutes);
  }
}

async function saveConfig() {
  const parsed = JSON.parse($("#config-editor").value);
  state.config = await api("/api/config", { method: "POST", body: JSON.stringify(parsed) });
  $("#config-editor").value = JSON.stringify(state.config, null, 2);
}

async function saveHhToken() {
  const token = $("#hh-token-input").value.trim();
  const allowBroad = $("#hh-allow-broad").checked;
  if (!state.config) return;

  if (!state.config.sources) state.config.sources = {};
  if (!state.config.sources.hh) state.config.sources.hh = {};

  state.config.sources.hh.access_token = token;
  state.config.sources.hh.allow_broad_apply = allowBroad;

  state.config = await api("/api/config", { method: "POST", body: JSON.stringify(state.config) });
  $("#config-editor").value = JSON.stringify(state.config, null, 2);
  alert("Настройки HH сохранены в конфигурации.");
}

async function saveAiSettings() {
  const key = $("#ai-key-input").value.trim();
  const model = $("#ai-model-input").value.trim();
  if (!state.config) return;

  if (!state.config.ai) state.config.ai = {};
  if (key) state.config.ai.api_key = key;
  state.config.ai.model = model;
  state.config.ai.backend = $("#ai-backend-select").value;
  state.config.ai.opencode_transport = $("#opencode-transport-select").value;
  state.config.ai.opencode_command = $("#opencode-command-input").value.trim() || "opencode";
  state.config.ai.opencode_model = $("#opencode-model-input").value.trim();
  state.config.ai.opencode_agent = $("#opencode-agent-input").value.trim() || "work-hunter-ai";
  state.config.ai.opencode_server_url = $("#opencode-server-input").value.trim() || "http://127.0.0.1:4096";

  state.config = await api("/api/config", { method: "POST", body: JSON.stringify(state.config) });
  $("#config-editor").value = JSON.stringify(state.config, null, 2);
  alert("AI настройки сохранены.");
}

async function loadSources() {
  const sources = await api("/api/sources");
  const box = $("#sources-list");
  box.innerHTML = "";
  if (!sources.length) {
    box.innerHTML = `<p>Источники еще не синхронизировались.</p>`;
    return;
  }
  for (const source of sources) {
    const row = document.createElement("div");
    row.className = "source-row";
    row.innerHTML = `
      <strong>${escapeHtml(source.source)}</strong>
      <div class="meta">last sync: ${escapeHtml(source.last_sync_at || "-")}</div>
      ${source.last_error ? `<div class="error">${escapeHtml(source.last_error)}</div>` : `<div class="meta">ошибок нет</div>`}
    `;
    box.appendChild(row);
  }
}

function updateChatContext() {
  if (state.selectedId && $("#chat-attach-job")?.checked) {
    state.chatJobId = state.selectedId;
    const job = state.jobs.find((j) => j.id === state.selectedId);
    if (job) {
      $("#chat-context-job").textContent = `Прикреплена: ${job.title} (${job.company || "?"}) — id=${state.selectedId}`;
      $("#chat-title").textContent = "AI: анализ вакансии";
    }
  } else if (!$("#chat-attach-job")?.checked) {
    state.chatJobId = null;
    $("#chat-context-job").textContent = "Вакансия не выбрана. Выбери вакансию в списке.";
    $("#chat-title").textContent = "AI Ассистент";
  }
}

function renderChat() {
  const box = $("#chat-messages");
  box.innerHTML = "";

  if (state.chatMessages.length === 0) {
    box.innerHTML = `
      <div class="chat-welcome">
        <p><strong>Привет!</strong> Я AI-ассистент Work Hunter.</p>
        <p>Выбери вакансию слева, поставь галку «Прикрепить» — и я помогу её проанализировать. Или просто спроси меня о поиске работы.</p>
      </div>`;
    return;
  }

  for (const msg of state.chatMessages) {
    const div = document.createElement("div");
    div.className = `chat-bubble ${msg.role}`;
    div.innerHTML = renderMarkdown(msg.content);
    box.appendChild(div);
  }
  box.scrollTop = box.scrollHeight;
}

function renderMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\n/g, '<br>');
  return html;
}

async function sendChatMessage() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;

  state.chatMessages.push({ role: "user", content: text });
  input.value = "";
  renderChat();

  const body = {
    messages: state.chatMessages.map((m) => ({ role: m.role, content: m.content })),
  };
  if (state.chatJobId) {
    body.job_id = state.chatJobId;
  }

  $("#chat-send-button").disabled = true;
  $("#chat-send-button").textContent = "Думаю...";

  try {
    const result = await api("/api/chat", { method: "POST", body: JSON.stringify(body) });
    state.chatMessages.push({ role: "assistant", content: result.content });
    renderChat();
  } catch (err) {
    state.chatMessages.push({ role: "assistant", content: `Ошибка: ${err.message}. Проверь API-ключ OpenRouter в настройках.` });
    renderChat();
  } finally {
    $("#chat-send-button").disabled = false;
    $("#chat-send-button").textContent = "Отправить";
  }
}

function updateSummary() {
  $("#summary-count").textContent = `${state.jobs.length} вакансий`;
}

function setBusy(selector, busy) {
  const button = $(selector);
  if (!button) return;
  button.disabled = busy;
  button.textContent = busy ? "Работаю..." : button.dataset.label || button.textContent;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value || "#");
}

function escapeJsString(value) {
  return String(value ?? "")
    .replaceAll("\\", "\\\\")
    .replaceAll("'", "\\'")
    .replaceAll("\n", "\\n")
    .replaceAll("\r", "\\r")
    .replaceAll("<", "\\u003c");
}

function letterBoxes() {
  return Array.from(document.querySelectorAll("[data-letter-box]"));
}

function setLetterValue(value) {
  for (const box of letterBoxes()) {
    box.value = value || "";
  }
}

function getLetterValue() {
  const detailValue = $("#detail-letter-box")?.value;
  if (detailValue !== undefined) return detailValue;
  return $("#ai-letter-box")?.value || "";
}

function activateView(view, options = {}) {
  const route = ROUTES[view] || ROUTES.inbox;
  const routeView = ROUTES[view] ? view : "inbox";
  const routePath = route.path;

  document.querySelectorAll(".nav-button").forEach((item) => item.classList.remove("active"));
  document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));

  document.querySelector(`.nav-button[data-view="${routeView}"]`)?.classList.add("active");
  const viewEl = $(`#view-${routeView}`);
  if (viewEl) viewEl.classList.add("active");

  const title = $("#route-topbar h1");
  if (title) title.textContent = route.title;
  const summary = $("#summary-line");
  if (summary) summary.textContent = route.summary;
  const actions = $("#route-actions");
  if (actions) actions.style.display = route.actions ? "flex" : "none";

  if (options.push !== false && window.location.pathname !== routePath) {
    window.history.pushState({ view: routeView }, "", routePath);
  }

  if (routeView === "favorites") loadFavorites().catch(console.error);
  if (routeView === "stats") loadStats().catch(console.error);
  if (routeView === "agent") loadAgentCockpit().catch(console.error);
  refreshIcons();
}

function toggleTheme() {
  state.darkTheme = !state.darkTheme;
  document.documentElement.setAttribute("data-theme", state.darkTheme ? "dark" : "light");
  $("#theme-toggle-button").innerHTML = state.darkTheme ? '<i data-lucide="sun"></i> Светлая' : '<i data-lucide="moon"></i> Тёмная';
  localStorage.setItem("work-hunter-theme", state.darkTheme ? "dark" : "light");
  if (window.lucide) lucide.createIcons();
}

function loadTheme() {
  const saved = localStorage.getItem("work-hunter-theme");
  if (saved === "dark") {
    state.darkTheme = true;
    document.documentElement.setAttribute("data-theme", "dark");
    if ($("#theme-toggle-button")) {
      $("#theme-toggle-button").innerHTML = '<i data-lucide="sun"></i> Светлая';
    }
  }
}

async function getResumeTips() {
  if (!state.selectedId) return;
  setBusy("[onclick='getResumeTips()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/resume-tips`, { method: "POST", body: "{}" });
    $("#resume-tips-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#resume-tips-output").textContent = `Ошибка: ${err.message}`;
  }
  setBusy("[onclick='getResumeTips()']", false);
}

async function getAtsResume() {
  if (!state.selectedId) return;
  setBusy("[onclick='getAtsResume()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/ats-resume`, { method: "POST", body: "{}" });
    $("#ats-resume-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#ats-resume-output").textContent = `Ошибка: ${err.message}`;
  }
  setBusy("[onclick='getAtsResume()']", false);
}

async function getAtsAudit() {
  if (!state.selectedId) return;
  const text = $("#audit-resume-input").value.trim();
  if (!text) { $("#ats-audit-output").textContent = "Вставь текст резюме выше"; return; }
  setBusy("[onclick='getAtsAudit()']", true);
  try {
    const result = await api("/api/ats-audit", { method: "POST", body: JSON.stringify({ resume_text: text, job_id: state.selectedId }) });
    $("#ats-audit-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#ats-audit-output").textContent = `Ошибка: ${err.message}`;
  }
  setBusy("[onclick='getAtsAudit()']", false);
}

async function getSummary() {
  if (!state.selectedId) return;
  setBusy("[onclick='getSummary()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/summarize`, { method: "POST", body: "{}" });
    $("#summary-output").textContent = result.summary;
  } catch (err) {
    $("#summary-output").textContent = `Ошибка: ${err.message}`;
  }
  setBusy("[onclick='getSummary()']", false);
}

async function getAiFit() {
  if (!state.selectedId) return;
  setBusy("[onclick='getAiFit()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/ai-fit`, { method: "POST", body: "{}" });
    const badge = $("#ai-fit-score");
    badge.style.display = "block";
    badge.textContent = `AI Fit: ${result.score}%`;
    badge.style.background = result.score >= 70 ? "#dcfce7" : result.score >= 40 ? "#fef9c3" : "#fee2e2";
    badge.style.color = result.score >= 70 ? "#11845b" : result.score >= 40 ? "#9a5b13" : "#b42318";
    $("#ai-fit-reasoning").textContent = result.reasoning;
  } catch (err) {
    $("#ai-fit-reasoning").textContent = `Ошибка: ${err.message}`;
  }
  setBusy("[onclick='getAiFit()']", false);
}

async function getInterviewQuestions() {
  if (!state.selectedId) return;
  setBusy("[onclick='getInterviewQuestions()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/interview-questions`, { method: "POST", body: "{}" });
    $("#interview-questions-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#interview-questions-output").textContent = `Ошибка: ${err.message}`;
  }
  setBusy("[onclick='getInterviewQuestions()']", false);
}

async function getExperiencePitch() {
  if (!state.selectedId) return;
  setBusy("[onclick='getExperiencePitch()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/pitch`, { method: "POST", body: "{}" });
    $("#pitch-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#pitch-output").textContent = `Ошибка: ${err.message}`;
  }
  setBusy("[onclick='getExperiencePitch()']", false);
}

async function saveJobNote() {
  if (!state.selectedId) return;
  const note = $("#job-notes-input").value;
  await api(`/api/jobs/${state.selectedId}/note`, { method: "POST", body: JSON.stringify({ body: note }) });
  alert("Заметка сохранена.");
}

async function loadJobNote() {
  if (!state.selectedId) return;
  try {
    const result = await api(`/api/jobs/${state.selectedId}/note`);
    $("#job-notes-input").value = result.body || "";
  } catch (e) { /* ignore if endpoint doesn't exist yet */ }
}

async function loadFavorites() {
  const jobs = await api("/api/jobs?status=saved&limit=200");
  const body = $("#favorites-body");
  body.innerHTML = "";
  for (const job of jobs) {
    const tr = document.createElement("tr");
    tr.addEventListener("click", () => selectJob(job.id));
    const score = job.score ? job.score.total_score : "-";
    tr.innerHTML = `
      <td><span class="score">${escapeHtml(String(score))}</span></td>
      <td>
        <div class="title">${escapeHtml(job.title || "Без названия")}</div>
        <div class="meta">${escapeHtml(job.company || "?" )} · ${escapeHtml(job.source)}</div>
      </td>
      <td>${escapeHtml(job.source)}</td>
      <td class="meta">${escapeHtml((job.note_short || "").substring(0, 60))}</td>
    `;
    body.appendChild(tr);
  }
}

async function loadStats() {
  try {
    const stats = await api("/api/stats");
    $("#stat-total").textContent = stats.total_jobs || 0;
    $("#stat-new").textContent = (stats.by_status?.new || 0);
    $("#stat-applied").textContent = stats.total_applications || 0;
    $("#stat-saved").textContent = (stats.by_status?.saved || 0);

    const sourceDiv = $("#stats-by-source");
    sourceDiv.innerHTML = "";
    if (stats.by_source) {
      for (const [source, count] of Object.entries(stats.by_source)) {
        sourceDiv.innerHTML += `<div class="stat-bar"><span>${source}</span><div class="bar"><div class="fill" style="width:${Math.min(count/10, 100)}%"></div></div><span>${count}</span></div>`;
      }
    }

    const distDiv = $("#stats-score-dist");
    distDiv.innerHTML = "";
    if (stats.score_distribution) {
      for (const [bucket, count] of Object.entries(stats.score_distribution)) {
        distDiv.innerHTML += `<div class="stat-bar"><span>${bucket}</span><div class="bar"><div class="fill" style="width:${Math.min(count/5, 100)}%"></div></div><span>${count}</span></div>`;
      }
    }

    const funnelDiv = $("#stats-funnel");
    funnelDiv.innerHTML = "";
    if (stats.applications_by_status) {
      const funnel = stats.applications_by_status;
      const total = stats.total_applications || 1;
      const stages = ["applied", "viewed", "response", "phone_screen", "interview", "offer"];
      for (const stage of stages) {
        const count = funnel[stage] || 0;
        const pct = Math.round((count / total) * 100);
        funnelDiv.innerHTML += `<div class="funnel-stage" style="width:${Math.max(pct, 2)}%;min-width:40px;"><span>${stage}</span><strong>${count}</strong></div>`;
      }
    }
  } catch (e) {
    console.error("Stats error:", e);
  }
}

function startAutoSync(minutes) {
  stopAutoSync();
  if (minutes <= 0) return;
  state.autoSyncMinutes = minutes;
  state.autoSyncInterval = setInterval(async () => {
    try {
      await api("/api/sync", { method: "POST", body: JSON.stringify({ score: true }) });
      await loadJobs();
      $("#auto-sync-status").textContent = `Синхронизировано: ${new Date().toLocaleTimeString()}`;
    } catch (e) {
      $("#auto-sync-status").textContent = `Ошибка: ${e.message}`;
    }
  }, minutes * 60 * 1000);
  $("#auto-sync-status").textContent = `Авто-синхронизация каждые ${minutes} мин.`;
}

function stopAutoSync() {
  if (state.autoSyncInterval) {
    clearInterval(state.autoSyncInterval);
    state.autoSyncInterval = null;
  }
  $("#auto-sync-status").textContent = "Авто-синхронизация выключена";
}

function saveAutoSync() {
  const minutes = parseInt($("#auto-sync-interval").value) || 0;
  startAutoSync(minutes);
}

async function aiSearch() {
  const query = $("#ai-search-input").value.trim();
  if (!query) return;
  $("#ai-search-button").disabled = true;
  $("#ai-search-button").textContent = "Ищу...";
  try {
    const result = await api("/api/jobs/search-ai", { method: "POST", body: JSON.stringify({ query }) });
    if (result.length === 0) {
      $("#summary-line").textContent = "Ничего не найдено по AI-поиску.";
      return;
    }
    state.jobs = result;
    renderJobs();
    updateSummary();
    $("#summary-line").textContent = `AI-поиск: найдено ${result.length} вакансий.`;
  } catch (e) {
    $("#summary-line").textContent = `Ошибка AI-поиска: ${e.message}`;
  } finally {
    $("#ai-search-button").disabled = false;
    $("#ai-search-button").textContent = "Искать";
  }
}

function exportCsv() {
  const params = new URLSearchParams();
  const source = $("#source-filter").value;
  if (source) params.set("source", source);
  window.open(`/api/jobs/export?${params.toString()}`, "_blank");
}

async function fetchFullDescription() {
  if (!state.selectedId) return;
  setBusy("[onclick='fetchFullDescription()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/fetch-full`, { method: "POST", body: "{}" });
    if (result.updated) {
      await selectJob(state.selectedId);
      $("#summary-line").textContent = "Полное описание загружено.";
    } else {
      $("#summary-line").textContent = "Не удалось загрузить описание.";
    }
  } catch (e) {
    $("#summary-line").textContent = `Ошибка: ${e.message}`;
  }
  setBusy("[onclick='fetchFullDescription()']", false);
}

function switchAiTab(panelName) {
  document.querySelectorAll(".ai-tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".ai-panel-content").forEach(p => p.classList.remove("active"));
  document.querySelector(`.ai-tab[data-ai-panel="${panelName}"]`).classList.add("active");
  document.querySelector(`#ai-panel-${panelName}`).classList.add("active");
}

function refreshIcons() {
  if (window.lucide) lucide.createIcons();
}

function switchAgentPanel(panelName) {
  document.querySelectorAll(".agent-tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".agent-panel").forEach(p => p.classList.remove("active"));
  document.querySelector(`.agent-tab[data-agent-panel="${panelName}"]`)?.classList.add("active");
  $(`#agent-panel-${panelName}`)?.classList.add("active");
}

async function loadAgentCockpit() {
  $("#agent-status-line").textContent = "Загружаю статус агента...";
  await Promise.all([
    loadAgentPreflight(),
    loadAgentDigest(),
    loadAgentOperations(),
    loadAgentApprovals(),
    loadAgentTemplates(),
    loadAgentBlacklist(),
    loadHhLabQuickCalls(),
    loadHhLabSnippets(),
    loadAgentEvents(),
    loadAgentTasks(),
  ]);
  renderAgentResearch();
  $("#agent-status-line").textContent = "Готово";
  refreshIcons();
}

async function loadAgentPreflight() {
  state.agent.preflight = await api("/api/agent/preflight?live_auth=true");
  renderAgentDashboard();
}

async function loadAgentDigest() {
  state.agent.digest = await api("/api/agent/digest?limit=8");
  renderAgentDashboard();
  renderAgentDigest();
}

async function loadAgentOperations() {
  state.agent.operations = await api("/api/operations?limit=20");
  renderAgentOperations();
}

async function loadAgentApprovals() {
  state.agent.approvals = await api("/api/approvals");
  renderAgentDashboard();
  renderAgentApprovals();
}

async function loadAgentTemplates() {
  state.agent.templates = await api("/api/templates");
  renderAgentTemplates();
}

async function loadAgentBlacklist() {
  state.agent.blacklist = await api("/api/blacklist");
  renderAgentBlacklist();
}

async function loadAgentEvents() {
  state.agent.events = await api("/api/agent/events?limit=20");
  renderAgentEvents();
}

async function loadAgentTasks() {
  state.agent.tasks = await api("/api/agent/tasks?limit=20");
  renderAgentEvents();
}

async function loadHhLabQuickCalls() {
  state.agent.labQuickCalls = await api("/api/hh/lab/quick-calls");
  renderHhLabQuickCalls();
}

async function loadHhLabSnippets() {
  state.agent.labSnippets = await api("/api/hh/lab/snippets");
  renderHhLabSnippets();
}

function renderAgentDashboard() {
  const preflight = state.agent.preflight || {};
  const digest = state.agent.digest || {};
  const counts = preflight.counts || {};
  const auth = preflight.auth || {};
  const approvals = Array.isArray(state.agent.approvals) ? state.agent.approvals : [];
  const livePending = approvals.filter((item) => item.status === "pending").length;
  $("#agent-auth-status").textContent = auth.status || preflight.status || "—";
  $("#agent-auth-note").textContent = (auth.actions || preflight.actions || []).slice(0, 2).join(" · ") || auth.error || "ready";
  $("#agent-pending-count").textContent = String(livePending ?? counts.pending_approvals ?? digest.approvals?.by_status?.pending ?? 0);
  $("#agent-runs-count").textContent = String(counts.mcp_runs ?? digest.runs?.total ?? 0);
  $("#agent-decisions-count").textContent = String(counts.ai_decisions ?? digest.ai_decisions?.total ?? 0);
  renderAgentInbox();
  renderAgentSettings();
}

function renderAgentDigest() {
  const box = $("#agent-digest-list");
  if (!box) return;
  const digest = state.agent.digest;
  if (!digest) {
    box.innerHTML = '<p class="meta">Нет digest.</p>';
    return;
  }
  const recommendations = digest.summary?.recommendations || [];
  const approvals = digest.approvals?.recent || [];
  const runs = digest.runs?.recent || [];
  box.innerHTML = `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>HH summary</strong>
        <span class="agent-badge">${escapeHtml(String(digest.status || "ok"))}</span>
      </div>
      <div class="agent-inline-stats">
        <span>resumes: ${escapeHtml(String(digest.summary?.resumes?.total ?? 0))}</span>
        <span>negotiations: ${escapeHtml(String(digest.summary?.negotiations?.total ?? 0))}</span>
        <span>contacts: ${escapeHtml(String(digest.summary?.contacts?.total ?? 0))}</span>
        <span>skipped: ${escapeHtml(String(digest.summary?.skipped?.total ?? 0))}</span>
      </div>
      <div class="meta">${recommendations.map(escapeHtml).join(" · ") || "no recommendations"}</div>
    </div>
    <div class="agent-row">
      <div class="agent-row-head"><strong>Recent approvals</strong><span class="agent-badge">${approvals.length}</span></div>
      ${approvals.map((item) => `<div class="meta">#${item.id} ${escapeHtml(item.action_type)} · ${escapeHtml(item.status)} · ${escapeHtml(item.reason || "")}</div>`).join("") || '<p class="meta">Нет approvals.</p>'}
    </div>
    <div class="agent-row">
      <div class="agent-row-head"><strong>Recent runs</strong><span class="agent-badge">${runs.length}</span></div>
      ${runs.map((item) => `<div class="meta">#${item.id} ${escapeHtml(item.tool_name)} · ${escapeHtml(item.status)}</div>`).join("") || '<p class="meta">Нет запусков.</p>'}
    </div>
  `;
  refreshIcons();
}

function renderAgentApprovals() {
  const box = $("#agent-approvals-list");
  if (!box) return;
  const approvals = state.agent.approvals || [];
  if (!approvals.length) {
    box.innerHTML = '<p class="meta">Очередь пустая.</p>';
    return;
  }
  box.innerHTML = approvals.map((item) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>#${item.id} ${escapeHtml(item.action_type)}</strong>
        <span class="agent-badge ${escapeAttr(item.status)}">${escapeHtml(item.status)}</span>
      </div>
      <div class="meta">confidence: ${escapeHtml(String(item.confidence))} · ${escapeHtml(item.reason || "")}</div>
      <pre class="agent-json">${escapeHtml(JSON.stringify(item.payload || {}, null, 2))}</pre>
      <textarea id="agent-modify-${item.id}" class="agent-modify" rows="2" placeholder="Что изменить в payload/сообщении"></textarea>
      <div class="agent-row-actions">
        <button onclick="approveAgentApproval(${item.id})"><i data-lucide="check"></i>Approve</button>
        <button onclick="rejectAgentApproval(${item.id})"><i data-lucide="x"></i>Reject</button>
        <button onclick="modifyAgentApproval(${item.id})"><i data-lucide="pencil"></i>Modify</button>
        <button onclick="flagAgentApproval(${item.id})"><i data-lucide="flag"></i>Flag</button>
      </div>
    </div>
  `).join("");
  refreshIcons();
}

function renderAgentOperations() {
  const box = $("#agent-runs-list");
  const logBox = $("#agent-logs-list");
  if (!box || !logBox) return;
  const operations = state.agent.operations || { runs: [], logs: [] };
  box.innerHTML = operations.runs.length ? operations.runs.map((run) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>#${run.id} ${escapeHtml(run.tool_name)}</strong>
        <span class="agent-badge ${escapeAttr(run.status)}">${escapeHtml(run.status)}</span>
      </div>
      <div class="meta">${escapeHtml(run.started_at || "")} ${run.finished_at ? "→ " + escapeHtml(run.finished_at) : ""}</div>
      <pre class="agent-json">${escapeHtml(JSON.stringify(run.output || run.input || {}, null, 2))}</pre>
      ${run.status === "running" ? `<button onclick="cancelAgentOperation(${run.id})"><i data-lucide="ban"></i>Cancel</button>` : ""}
    </div>
  `).join("") : '<p class="meta">Запусков пока нет.</p>';
  logBox.innerHTML = operations.logs.length ? operations.logs.map((log) => `
    <div class="agent-log-row">
      <span class="agent-badge ${escapeAttr(log.level)}">${escapeHtml(log.level)}</span>
      <span>#${escapeHtml(String(log.operation_id || "—"))}</span>
      <span>${escapeHtml(log.message || "")}</span>
    </div>
  `).join("") : '<p class="meta">Логов пока нет.</p>';
  refreshIcons();
}

function renderAgentInbox() {
  const box = $("#agent-inbox-list");
  if (!box) return;
  const digest = state.agent.digest || {};
  const outbox = (digest.outbox?.recent || []).slice(0, 10);
  const webhooks = (digest.webhooks?.recent || []).slice(0, 10);
  const approvals = Array.isArray(state.agent.approvals) ? state.agent.approvals.filter((item) => item.status === "pending") : [];
  const rows = [
    ...approvals.map((item) => ({
      title: `Approval #${item.id}`,
      badge: item.action_type || "approval",
      body: item.reason || "",
      payload: item.payload || {},
    })),
    ...outbox.map((item) => ({
      title: `Outbox #${item.id}`,
      badge: item.status || "outbox",
      body: `${item.channel || ""} ${item.target || ""}`.trim(),
      payload: item.payload || {},
    })),
    ...webhooks.map((item) => ({
      title: `Webhook #${item.id}`,
      badge: item.status || "webhook",
      body: item.event_type || "",
      payload: item.payload || {},
    })),
  ];
  box.innerHTML = rows.length ? rows.map((item) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>${escapeHtml(item.title)}</strong>
        <span class="agent-badge">${escapeHtml(item.badge)}</span>
      </div>
      <p>${escapeHtml(item.body)}</p>
      <pre class="agent-json">${escapeHtml(JSON.stringify(item.payload, null, 2))}</pre>
    </div>
  `).join("") : `<p class="meta">Inbox is empty.</p>`;
}

function renderAgentEvents() {
  const eventsBox = $("#agent-events-list");
  const tasksBox = $("#agent-tasks-list");
  if (eventsBox) {
    const events = state.agent.events || [];
    eventsBox.innerHTML = events.length ? events.map((item) => `
      <div class="agent-row">
        <div class="agent-row-head">
          <strong>${escapeHtml(item.title || item.event_type || "Event")}</strong>
          <span class="agent-badge">${escapeHtml(item.status || item.event_type || "event")}</span>
        </div>
        <p>${escapeHtml([item.event_at, item.employer_name, item.vacancy_name].filter(Boolean).join(" · "))}</p>
      </div>
    `).join("") : `<p class="meta">No agent events yet.</p>`;
  }
  if (tasksBox) {
    const tasks = state.agent.tasks || [];
    tasksBox.innerHTML = tasks.length ? tasks.map((item) => `
      <div class="agent-row">
        <div class="agent-row-head">
          <strong>${escapeHtml(item.title || item.task_type || "Task")}</strong>
          <span class="agent-badge">${escapeHtml(item.status || item.task_type || "task")}</span>
        </div>
        <p>${escapeHtml([item.due_at, item.employer_name, item.vacancy_name].filter(Boolean).join(" · "))}</p>
      </div>
    `).join("") : `<p class="meta">No agent tasks yet.</p>`;
  }
}

function renderAgentSettings() {
  const box = $("#agent-settings-output");
  if (!box) return;
  box.textContent = JSON.stringify(state.agent.preflight || {}, null, 2);
}

function renderAgentTemplates() {
  const box = $("#agent-templates-list");
  if (!box) return;
  const templates = state.agent.templates || [];
  box.innerHTML = templates.length ? templates.map((item) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>${escapeHtml(item.name)}</strong>
        <button onclick="deleteAgentTemplate('${escapeJsString(item.name)}')"><i data-lucide="trash-2"></i>Удалить</button>
      </div>
      <pre class="agent-json">${escapeHtml(item.body || "")}</pre>
    </div>
  `).join("") : '<p class="meta">Шаблонов пока нет.</p>';
  refreshIcons();
}

function renderAgentBlacklist() {
  const box = $("#agent-blacklist-list");
  if (!box) return;
  const blacklist = state.agent.blacklist || [];
  box.innerHTML = blacklist.length ? blacklist.map((item) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>${escapeHtml(item.employer_name || item.employer_id)}</strong>
        <button onclick="deleteAgentBlacklist('${escapeJsString(item.employer_id)}')"><i data-lucide="trash-2"></i>Удалить</button>
      </div>
      <div class="meta">${escapeHtml(item.employer_id)} · ${escapeHtml(item.reason || "")}</div>
    </div>
  `).join("") : '<p class="meta">Blacklist пустой.</p>';
  refreshIcons();
}

function renderHhLabQuickCalls() {
  const box = $("#hh-lab-quick-calls");
  if (!box) return;
  const calls = state.agent.labQuickCalls || [];
  box.innerHTML = calls.map((item) => `
    <button onclick="runHhLabQuick('${escapeJsString(item.id)}')">
      <i data-lucide="zap"></i>${escapeHtml(item.label || item.path)}
    </button>
  `).join("");
  refreshIcons();
}

function renderHhLabSnippets() {
  const box = $("#hh-lab-snippets-list");
  if (!box) return;
  const snippets = state.agent.labSnippets || [];
  if (!snippets.length) {
    box.innerHTML = '<p class="meta">Snippets list is empty.</p>';
    return;
  }
  box.innerHTML = snippets.map((item) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>${escapeHtml(item.name)}</strong>
        <span class="agent-badge">${escapeHtml(item.method)}</span>
      </div>
      <div class="meta">${escapeHtml(item.path)}</div>
      <pre class="agent-json">${escapeHtml(JSON.stringify({ params: item.params || {}, body: item.body || {} }, null, 2))}</pre>
      <div class="agent-row-actions">
        <button onclick="fillHhLabRequestByName('${escapeJsString(item.name)}')"><i data-lucide="copy"></i>Load</button>
        <button onclick="runHhLabSnippet('${escapeJsString(item.name)}')"><i data-lucide="play"></i>Run</button>
        <button onclick="deleteHhLabSnippet('${escapeJsString(item.name)}')"><i data-lucide="trash-2"></i>Delete</button>
      </div>
    </div>
  `).join("");
  refreshIcons();
}

function fillHhLabRequest(request) {
  $("#hh-lab-method").value = request.method || "GET";
  $("#hh-lab-path").value = request.path || "/me";
  $("#hh-lab-params").value = JSON.stringify(request.params || {}, null, 2);
  const body = request.body === null || request.body === undefined ? "" : JSON.stringify(request.body, null, 2);
  $("#hh-lab-body").value = body === "{}" ? "" : body;
}

function fillHhLabRequestByName(name) {
  const item = (state.agent.labSnippets || []).find((snippet) => snippet.name === name);
  if (!item) return;
  $("#hh-lab-snippet-name").value = item.name;
  fillHhLabRequest(item);
}

function parseHhLabJson(selector, fallback) {
  const raw = $(selector)?.value.trim() || "";
  if (!raw) return fallback;
  return JSON.parse(raw);
}

function writeHhLabOutput(payload) {
  const box = $("#hh-lab-output");
  if (!box) return;
  box.textContent = JSON.stringify(payload, null, 2);
}

const HH_LAB_MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function confirmHhLabMutation(payload) {
  if (!HH_LAB_MUTATING_METHODS.has(payload.method.toUpperCase())) return true;
  if (!window.confirm(`HH API mutation: ${payload.method} ${payload.path}. Continue?`)) return false;
  return window.confirm("Final confirmation: this can change your HH account.");
}

async function runHhLabCall() {
  try {
    const payload = {
      method: $("#hh-lab-method").value,
      path: $("#hh-lab-path").value.trim(),
      params: parseHhLabJson("#hh-lab-params", {}),
      body: parseHhLabJson("#hh-lab-body", null),
    };
    if (!confirmHhLabMutation(payload)) return;
    if (HH_LAB_MUTATING_METHODS.has(payload.method.toUpperCase())) payload.confirm = true;
    writeHhLabOutput({ status: "running", request: payload });
    const result = await api("/api/hh/lab/call", { method: "POST", body: JSON.stringify(payload) });
    writeHhLabOutput(result);
    await loadAgentOperations();
  } catch (err) {
    writeHhLabOutput({ status: "error", error: err.message });
  }
}

async function runHhLabQuick(quick) {
  const item = (state.agent.labQuickCalls || []).find((call) => call.id === quick);
  if (item) fillHhLabRequest(item);
  writeHhLabOutput({ status: "running", quick });
  try {
    const result = await api("/api/hh/lab/call", { method: "POST", body: JSON.stringify({ quick }) });
    writeHhLabOutput(result);
    await loadAgentOperations();
  } catch (err) {
    writeHhLabOutput({ status: "error", error: err.message });
  }
}

async function runHhLabSnippet(name) {
  const item = (state.agent.labSnippets || []).find((snippet) => snippet.name === name);
  if (!item) return;
  fillHhLabRequest(item);
  await runHhLabCall();
}

async function saveHhLabSnippet() {
  try {
    const payload = {
      name: $("#hh-lab-snippet-name").value.trim(),
      method: $("#hh-lab-method").value,
      path: $("#hh-lab-path").value.trim(),
      params: parseHhLabJson("#hh-lab-params", {}),
      body: parseHhLabJson("#hh-lab-body", {}),
    };
    const result = await api("/api/hh/lab/snippets", { method: "POST", body: JSON.stringify(payload) });
    writeHhLabOutput({ status: "snippet_saved", snippet: result });
    await loadHhLabSnippets();
  } catch (err) {
    writeHhLabOutput({ status: "error", error: err.message });
  }
}

async function deleteHhLabSnippet(name) {
  await api("/api/hh/lab/snippets/delete", { method: "POST", body: JSON.stringify({ name }) });
  await loadHhLabSnippets();
}

async function runAgentOperation(operation, button = null) {
  const params = operation === "preflight" ? { live_auth: true } : {};
  if (operation === "update-resumes") {
    if (!window.confirm("Update your HH resumes now?")) {
      $("#agent-operation-note").textContent = `${operation}: cancelled`;
      return;
    }
    params.confirm = true;
  }
  if (button) button.disabled = true;
  $("#agent-operation-note").textContent = `${operation}: running`;
  try {
    const result = await api("/api/agent/run", {
      method: "POST",
      body: JSON.stringify({ operation, params }),
    });
    $("#agent-operation-note").textContent = `${operation}: ${result.result?.status || "ok"}`;
    await Promise.all([loadAgentPreflight(), loadAgentDigest(), loadAgentOperations(), loadAgentApprovals()]);
  } catch (err) {
    $("#agent-operation-note").textContent = `${operation}: ${err.message}`;
  } finally {
    if (button) button.disabled = false;
    refreshIcons();
  }
}

function agentResearchPayload() {
  const rawLimit = Number.parseInt($("#agent-research-limit")?.value || "10", 10);
  return {
    text: $("#agent-research-text")?.value.trim() || "",
    limit: Number.isFinite(rawLimit) ? Math.max(1, Math.min(rawLimit, 100)) : 10,
    resume_id: $("#agent-research-resume-id")?.value.trim() || "",
    confirm_apply: false,
  };
}

async function runAgentResearch(planApply = false) {
  const runButton = $("#agent-research-run-button");
  const planButton = $("#agent-research-plan-button");
  if (runButton) runButton.disabled = true;
  if (planButton) planButton.disabled = true;
  state.agent.research = { result: { status: "running", items: [] } };
  renderAgentResearch();
  try {
    const result = await api("/api/agent/run", {
      method: "POST",
      body: JSON.stringify({
        operation: planApply ? "research-and-apply" : "research-vacancies",
        params: agentResearchPayload(),
      }),
    });
    state.agent.research = result;
    renderAgentResearch();
    await loadAgentOperations();
  } catch (err) {
    state.agent.research = { result: { status: "error", error: err.message, items: [] } };
    renderAgentResearch();
  } finally {
    if (runButton) runButton.disabled = false;
    if (planButton) planButton.disabled = false;
    refreshIcons();
  }
}

function renderAgentResearch() {
  const box = $("#agent-research-output");
  if (!box) return;
  const payload = state.agent.research || {};
  const result = payload.result || payload;
  if (!result || !result.status) {
    box.innerHTML = '<p class="meta">No research run yet.</p>';
    return;
  }
  if (result.status === "running") {
    box.innerHTML = '<p class="meta">Running research...</p>';
    return;
  }
  if (result.status === "error") {
    box.innerHTML = `<p class="error">${escapeHtml(result.error || "Research failed")}</p>`;
    return;
  }
  const counts = result.counts || {};
  const items = result.items || [];
  const header = `
    <div class="source-row">
      <strong>${escapeHtml(String(result.status))}</strong>
      <div class="meta">operation #${escapeHtml(String(payload.operation_id || "-"))} · analyzed ${escapeHtml(String(counts.analyzed || 0))} · planned ${escapeHtml(String(counts.planned || 0))} · blocked ${escapeHtml(String(counts.blocked || 0))} · errors ${escapeHtml(String(counts.errors || 0))}</div>
    </div>
  `;
  const cards = items.map((item) => `
    <div class="source-row agent-research-card">
      <strong>${escapeHtml(item.name || item.title || item.vacancy_id || "-")}</strong>
      <div class="meta">${escapeHtml(item.employer_name || item.company || "-")} · vacancy ${escapeHtml(item.vacancy_id || item.id || "-")} · score ${escapeHtml(String(item.score ?? "-"))} · ${escapeHtml(item.recommended_action || "-")} · ${escapeHtml(item.attempt_status || "not_planned")}</div>
      <div class="chips">
        ${(item.reasons || []).map((reason) => `<span class="chip">${escapeHtml(reason)}</span>`).join("") || '<span class="chip">no reasons</span>'}
        ${(item.risk_flags || []).map((risk) => `<span class="chip red">${escapeHtml(risk)}</span>`).join("")}
      </div>
      ${item.attempt_reason ? `<div class="meta">plan: ${escapeHtml(item.attempt_reason)}</div>` : ""}
    </div>
  `).join("");
  box.innerHTML = header + (cards || '<p class="meta">No items returned.</p>');
  refreshIcons();
}

async function cancelAgentOperation(id) {
  await api(`/api/cancel/${id}`, { method: "POST", body: JSON.stringify({ reason: "cancelled_from_ui" }) });
  await loadAgentOperations();
}

async function approveAgentApproval(id) {
  await api(`/api/approvals/${id}/approve`, { method: "POST", body: JSON.stringify({ reason: "approved_from_ui" }) });
  await Promise.all([loadAgentApprovals(), loadAgentDigest()]);
}

async function rejectAgentApproval(id) {
  await api(`/api/approvals/${id}/reject`, { method: "POST", body: JSON.stringify({ reason: "rejected_from_ui" }) });
  await Promise.all([loadAgentApprovals(), loadAgentDigest()]);
}

async function modifyAgentApproval(id) {
  const instruction = $(`#agent-modify-${id}`)?.value.trim() || "modified_from_ui";
  await api(`/api/approvals/${id}/modify`, {
    method: "POST",
    body: JSON.stringify({ instruction, payload_patch: {} }),
  });
  await Promise.all([loadAgentApprovals(), loadAgentDigest()]);
}

async function flagAgentApproval(id) {
  await api(`/api/approvals/${id}/flag`, { method: "POST", body: JSON.stringify({ reason: "flagged_from_ui" }) });
  await Promise.all([loadAgentApprovals(), loadAgentDigest()]);
}

async function saveAgentTemplate() {
  await api("/api/templates", {
    method: "POST",
    body: JSON.stringify({
      name: $("#agent-template-name").value.trim(),
      body: $("#agent-template-body").value,
    }),
  });
  $("#agent-template-body").value = "";
  await loadAgentTemplates();
}

async function deleteAgentTemplate(name) {
  await api("/api/templates/delete", { method: "POST", body: JSON.stringify({ name }) });
  await loadAgentTemplates();
}

async function saveAgentBlacklist() {
  await api("/api/blacklist", {
    method: "POST",
    body: JSON.stringify({
      employer_id: $("#agent-blacklist-id").value.trim(),
      employer_name: $("#agent-blacklist-name").value.trim(),
      reason: $("#agent-blacklist-reason").value.trim(),
    }),
  });
  $("#agent-blacklist-id").value = "";
  $("#agent-blacklist-name").value = "";
  $("#agent-blacklist-reason").value = "";
  await loadAgentBlacklist();
}

async function deleteAgentBlacklist(employerId) {
  await api("/api/blacklist/delete", { method: "POST", body: JSON.stringify({ employer_id: employerId }) });
  await loadAgentBlacklist();
}

function defaultResumeTemplate() {
  return `# {title}
first_name: {first_name}
last_name: {last_name}
area: {area}
professional_roles: {professional_roles}
email: {email}
phone: {phone}

## Summary
{summary}

## Skills
{skills}

## Experience
{experience}
`;
}

function defaultBatchMatrix() {
  return JSON.stringify({
    resumes: ["resume-id"],
    search_presets: ["backend"],
    letters: ["warm_reply"],
    limits: [10],
    defaults: { min_score: 70, ai_filter_mode: "light" },
  }, null, 2);
}

function setupResumeBuilderDefaults() {
  const template = $("#agent-resume-template");
  const matrix = $("#agent-batch-matrix-input");
  if (template && !template.value.trim()) template.value = defaultResumeTemplate();
  if (matrix && !matrix.value.trim()) matrix.value = defaultBatchMatrix();
}

async function previewAgentResumeTemplate() {
  const template = $("#agent-resume-template").value;
  let context = {};
  try {
    context = JSON.parse($("#agent-resume-context").value || "{}");
  } catch (err) {
    $("#agent-resume-preview-output").textContent = `Invalid context JSON: ${err.message}`;
    return;
  }
  const result = await api("/api/hh/resume-template/preview", {
    method: "POST",
    body: JSON.stringify({ template, context }),
  });
  $("#agent-resume-preview-output").textContent = JSON.stringify(result, null, 2);
}

async function buildAgentBatchMatrix() {
  let matrix = {};
  try {
    matrix = JSON.parse($("#agent-batch-matrix-input").value || "{}");
  } catch (err) {
    $("#agent-batch-matrix-output").textContent = `Invalid matrix JSON: ${err.message}`;
    return;
  }
  const result = await api("/api/hh/batch-matrix", {
    method: "POST",
    body: JSON.stringify({ matrix }),
  });
  $("#agent-batch-matrix-output").textContent = JSON.stringify(result, null, 2);
}

document.addEventListener("DOMContentLoaded", async () => {
  for (const button of document.querySelectorAll("button")) {
    button.dataset.label = button.textContent;
  }
  setupResumeBuilderDefaults();
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => activateView(button.dataset.view));
  });
  $("#sync-button").addEventListener("click", syncJobs);
  $("#score-button").addEventListener("click", scoreJobs);
  $("#refresh-button").addEventListener("click", loadJobs);
  $("#source-filter").addEventListener("change", loadJobs);
  $("#min-score-filter").addEventListener("change", loadJobs);
  $("#save-config-button").addEventListener("click", saveConfig);
  $("#save-token-button").addEventListener("click", saveHhToken);
  $("#save-ai-button").addEventListener("click", saveAiSettings);
  $("#save-profile-button").addEventListener("click", saveProfile);
  $("#rescore-after-save").addEventListener("click", scoreJobs);
  $("#chat-send-button").addEventListener("click", sendChatMessage);
  $("#chat-attach-job").addEventListener("change", updateChatContext);
  $("#profile-select").addEventListener("change", () => switchProfile($("#profile-select").value));
  $("#chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });
  $("#theme-toggle-button")?.addEventListener("click", toggleTheme);
  $("#save-auto-sync-button")?.addEventListener("click", saveAutoSync);
  $("#ai-search-button")?.addEventListener("click", aiSearch);
  $("#export-csv-button")?.addEventListener("click", exportCsv);
  $("#filter-remote-only")?.addEventListener("change", applySmartFilters);
  $("#filter-with-salary")?.addEventListener("change", applySmartFilters);
  $("#filter-level")?.addEventListener("change", applySmartFilters);
  $("#refresh-stats-button")?.addEventListener("click", loadStats);
  $("#agent-refresh-button")?.addEventListener("click", loadAgentCockpit);
  $("#agent-digest-button")?.addEventListener("click", loadAgentDigest);
  $("#agent-save-template-button")?.addEventListener("click", saveAgentTemplate);
  $("#agent-save-blacklist-button")?.addEventListener("click", saveAgentBlacklist);
  $("#agent-resume-preview-button")?.addEventListener("click", previewAgentResumeTemplate);
  $("#agent-batch-matrix-button")?.addEventListener("click", buildAgentBatchMatrix);
  $("#agent-research-run-button")?.addEventListener("click", () => runAgentResearch(false));
  $("#agent-research-plan-button")?.addEventListener("click", () => runAgentResearch(true));
  $("#hh-lab-run-button")?.addEventListener("click", runHhLabCall);
  $("#hh-lab-save-snippet-button")?.addEventListener("click", saveHhLabSnippet);
  document.querySelectorAll("[data-agent-operation]").forEach((button) => {
    button.addEventListener("click", () => runAgentOperation(button.dataset.agentOperation, button));
  });

  document.querySelectorAll(".ai-tab").forEach(tab => {
    tab.addEventListener("click", () => switchAiTab(tab.dataset.aiPanel));
  });
  document.querySelectorAll(".agent-tab").forEach(tab => {
    tab.addEventListener("click", () => switchAgentPanel(tab.dataset.agentPanel));
  });

  window.addEventListener("popstate", () => activateView(routeViewFromPath(window.location.pathname), { push: false }));

  loadTheme();
  refreshIcons();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js");
  }
  await Promise.all([loadJobs(), loadConfig(), loadProfile(), loadSources()]);
  activateView(routeViewFromPath(window.location.pathname), { push: false });
  loadResumes().catch(() => {});
  loadEvents().catch(() => {});
  loadSearches().catch(() => {});
  startSearchAlerts();
});

let editingResumeId = 0;

function showResumeForm(id = 0) {
  editingResumeId = id;
  $("#resume-form").style.display = "block";
  $("#save-resume-button").textContent = id ? "Обновить" : "Сохранить";
  if (id) {
  }
  $("#resume-name-input").focus();
}

function hideResumeForm() {
  $("#resume-form").style.display = "none";
  editingResumeId = 0;
}

async function saveResume() {
  const body = {
    name: $("#resume-name-input").value.trim(),
    body: $("#resume-body-input").value,
    profile_id: state.profile?.active || "default",
    is_active: false,
  };
  if (editingResumeId) body.id = editingResumeId;
  try {
    await api("/api/resumes", { method: "POST", body: JSON.stringify(body) });
    hideResumeForm();
    await loadResumes();
  } catch (e) { alert("Ошибка: " + e.message); }
}

async function loadResumes() {
  try {
    const resumes = await api("/api/resumes");
    const list = $("#resumes-list");
    list.innerHTML = "";
    for (const r of resumes) {
      list.innerHTML += `<div class="source-row">
        <strong>${escapeHtml(r.name)}</strong>
        <div class="meta">${r.is_active ? "★ Активное" : ""} · ATS: ${r.ats_score ?? "—"}</div>
        <div style="margin-top:6px;display:flex;gap:6px;">
          <button onclick="activateResume(${r.id})">Активировать</button>
          <button onclick="showResumeForm(${r.id})">Ред.</button>
          <button onclick="deleteResume(${r.id})">Удалить</button>
        </div>
      </div>`;
    }
  } catch (e) { console.error(e); }
}

async function activateResume(id) {
  await api(`/api/resumes/${id}/activate`, { method: "POST", body: "{}" });
  await loadResumes();
}

async function deleteResume(id) {
  if (!confirm("Удалить резюме?")) return;
  await api(`/api/resumes/${id}/delete`, { method: "POST", body: "{}" });
  await loadResumes();
}

let editingEventId = 0;

function showEventForm(id = 0) {
  editingEventId = id;
  $("#event-form").style.display = "block";
  $("#event-form-title").textContent = id ? "Редактировать событие" : "Новое событие";
  if (!id) {
    $("#event-title-input").value = "";
    $("#event-date-input").value = "";
    $("#event-job-input").value = "";
    $("#event-notes-input").value = "";
  }
}

function hideEventForm() {
  $("#event-form").style.display = "none";
  editingEventId = 0;
}

async function saveEvent() {
  const body = {
    title: $("#event-title-input").value.trim(),
    event_type: $("#event-type-select").value,
    event_date: $("#event-date-input").value,
    job_id: parseInt($("#event-job-input").value) || 0,
    notes: $("#event-notes-input").value,
  };
  if (editingEventId) body.id = editingEventId;
  try {
    await api("/api/events", { method: "POST", body: JSON.stringify(body) });
    hideEventForm();
    await loadEvents();
  } catch (e) { alert("Ошибка: " + e.message); }
}

async function loadEvents() {
  try {
    const events = await api("/api/events");
    const list = $("#events-list");
    list.innerHTML = "";
    if (!events.length) {
      list.innerHTML = '<p class="meta">Нет событий.</p>';
      return;
    }
    for (const ev of events) {
      const date = ev.event_date ? new Date(ev.event_date).toLocaleString("ru-RU") : "—";
      list.innerHTML += `<div class="source-row">
        <strong>${escapeHtml(ev.title)} — ${date}</strong>
        <div class="meta">${escapeHtml(ev.event_type)} · Job #${ev.job_id || "—"}</div>
        <div class="meta">${escapeHtml(ev.notes || "")}</div>
        <button onclick="deleteEvent(${ev.id})" style="margin-top:4px;">Удалить</button>
      </div>`;
    }
  } catch (e) { console.error(e); }
}

async function deleteEvent(id) {
  await api(`/api/events/${id}/delete`, { method: "POST", body: "{}" });
  await loadEvents();
}

let selectedJobIds = new Set();

function toggleSelectAll() {
  const checked = $("#select-all-checkbox").checked;
  selectedJobIds.clear();
  document.querySelectorAll(".job-checkbox").forEach(cb => {
    cb.checked = checked;
    if (checked) selectedJobIds.add(parseInt(cb.dataset.jobId));
  });
  updateBulkToolbar();
}

function toggleJobSelect(jobId, checked) {
  if (checked) selectedJobIds.add(jobId);
  else selectedJobIds.delete(jobId);
  updateBulkToolbar();
}

function updateBulkToolbar() {
  const toolbar = $("#bulk-toolbar");
  toolbar.style.display = selectedJobIds.size > 0 ? "flex" : "none";
  $("#bulk-count").textContent = `Выбрано: ${selectedJobIds.size}`;
}

function syncBulkCheckboxes() {
  const boxes = Array.from(document.querySelectorAll(".job-checkbox"));
  for (const cb of boxes) {
    cb.checked = selectedJobIds.has(Number(cb.dataset.jobId));
  }
  const selectAll = $("#select-all-checkbox");
  if (selectAll) {
    selectAll.checked = boxes.length > 0 && boxes.every((cb) => cb.checked);
  }
  updateBulkToolbar();
}

function clearBulkSelection() {
  selectedJobIds.clear();
  document.querySelectorAll(".job-checkbox").forEach(cb => cb.checked = false);
  $("#select-all-checkbox").checked = false;
  updateBulkToolbar();
}

async function bulkAction(action) {
  if (!selectedJobIds.size) return;
  try {
    await api("/api/jobs/bulk", {
      method: "POST",
      body: JSON.stringify({ job_ids: Array.from(selectedJobIds), action }),
    });
    clearBulkSelection();
    await loadJobs();
    $("#summary-line").textContent = `${action}: обработано.`;
  } catch (e) { alert("Ошибка: " + e.message); }
}

async function applySmartFilters() {
  const remoteOnly = $("#filter-remote-only")?.checked;
  const withSalary = $("#filter-with-salary")?.checked;
  const level = $("#filter-level")?.value;

  let jobs = [...state.jobs];
  if (remoteOnly) jobs = jobs.filter(j => j.remote);
  if (withSalary) jobs = jobs.filter(j => j.salary_from || j.salary_to);
  if (level) jobs = jobs.filter(j => {
    const t = (j.title || "").toLowerCase();
    if (level === "junior") return t.includes("junior") || t.includes("джуниор") || t.includes("начинающий") || t.includes("стажер");
    if (level === "senior") return t.includes("senior") || t.includes("сеньор") || t.includes("ведущий") || t.includes("lead") || t.includes("тимлид");
    if (level === "middle") return !t.includes("senior") && !t.includes("junior") && !t.includes("джуниор") && !t.includes("сеньор") && !t.includes("lead");
    return true;
  });

  state._filteredJobs = jobs;
  renderFilteredJobs(jobs);
  updateSummaryForFiltered(jobs);
}

function renderFilteredJobs(jobs) {
  const body = $("#jobs-body");
  body.innerHTML = "";
  for (const job of jobs) {
    const tr = document.createElement("tr");
    tr.className = job.id === state.selectedId ? "selected" : "";
    tr.addEventListener("click", () => selectJob(job.id));
    const score = job.score ? job.score.total_score : "-";
    const scoreClass = score === "-" ? "" : score >= 70 ? " high" : score < 35 ? " low" : "";
    const checked = selectedJobIds.has(Number(job.id)) ? "checked" : "";
    tr.innerHTML = `
      <td><input type="checkbox" class="job-checkbox" data-job-id="${job.id}" onclick="event.stopPropagation();toggleJobSelect(${job.id}, this.checked)" ${checked}></td>
      <td><span class="score${scoreClass}">${escapeHtml(String(score))}</span></td>
      <td>
        <div class="title">${escapeHtml(job.title || "Без названия")}</div>
        <div class="meta">${escapeHtml(job.company || "Компания не указана")}</div>
        <div class="meta">${escapeHtml([job.salary_text, job.location, job.remote ? "remote" : ""].filter(Boolean).join(" · "))}</div>
      </td>
      <td>${escapeHtml(job.source)}</td>
      <td>${escapeHtml(job.status || "new")}</td>
    `;
    body.appendChild(tr);
  }
  syncBulkCheckboxes();
}

function updateSummaryForFiltered(jobs) {
  $("#summary-count").textContent = `${jobs.length} вакансий`;
}

function showSearchForm() {
  $("#search-form").style.display = "block";
  $("#search-name-input").focus();
}

function hideSearchForm() {
  $("#search-form").style.display = "none";
}

async function saveSearch() {
  const body = {
    name: $("#search-name-input").value.trim(),
    query: $("#search-query-input").value.trim(),
    filters_json: "{}",
    alert_enabled: $("#search-alert-checkbox").checked,
  };
  try {
    await api("/api/saved-searches", { method: "POST", body: JSON.stringify(body) });
    hideSearchForm();
    await loadSearches();
  } catch (e) { alert("Ошибка: " + e.message); }
}

async function loadSearches() {
  try {
    const searches = await api("/api/saved-searches");
    const list = $("#searches-list");
    list.innerHTML = "";
    if (!searches.length) { list.innerHTML = '<p class="meta">Нет сохранённых поисков.</p>'; return; }
    for (const s of searches) {
      list.innerHTML += `<div class="source-row">
        <strong>${escapeHtml(s.name)}</strong>
        <div class="meta">${escapeHtml(s.query)} · alert: ${s.alert_enabled ? "on" : "off"}</div>
        <button onclick="deleteSearch(${s.id})" style="margin-top:4px;">Удалить</button>
      </div>`;
    }
  } catch (e) { console.error(e); }
}

async function deleteSearch(id) {
  await api(`/api/saved-searches/${id}/delete`, { method: "POST", body: "{}" });
  await loadSearches();
}

function startSearchAlerts() {
  setInterval(async () => {
    try {
      const searches = await api("/api/saved-searches");
      for (const s of searches) {
        if (!s.alert_enabled) continue;
      }
    } catch (e) {}
  }, 30 * 60 * 1000);
}

async function loadMarketTrends() {
  $("#trends-output").innerHTML = '<p class="meta">Анализирую рынок...</p>';
  try {
    const result = await api("/api/market-trends", { method: "POST", body: JSON.stringify({ limit: 50 }) });
    $("#trends-output").innerHTML = renderMarkdown(result.content);
  } catch (e) {
    $("#trends-output").innerHTML = `<p class="error">Ошибка: ${e.message}</p>`;
  }
}

async function parseJobStructure() {
  if (!state.selectedId) return;
  setBusy("[onclick='parseJobStructure()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/parse-structure`, { method: "POST", body: "{}" });
    const output = $("#resume-tips-output");
    output.textContent = JSON.stringify(result, null, 2);
  } catch (e) { alert("Ошибка: " + e.message); }
  setBusy("[onclick='parseJobStructure()']", false);
}

async function runGapAnalysis() {
  if (!state.selectedId) return;
  const resumes = await api("/api/resumes");
  const active = resumes.find(r => r.is_active);
  if (!active) { alert("Сначала создай и активируй резюме в настройках."); return; }
  setBusy("[onclick='runGapAnalysis()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/gap-analysis`, {
      method: "POST", body: JSON.stringify({ resume_id: active.id }),
    });
    $("#resume-tips-output").innerHTML = renderMarkdown(result.content);
  } catch (e) { alert("Ошибка: " + e.message); }
  setBusy("[onclick='runGapAnalysis()']", false);
}

async function scoreAtsResume() {
  const text = $("#resume-body-input")?.value || $("#audit-resume-input")?.value;
  if (!text) { alert("Вставь текст резюме."); return; }
  try {
    const result = await api("/api/resumes/ats-score", { method: "POST", body: JSON.stringify({ resume_text: text }) });
    alert(`ATS Score: ${result.score}/100\nПроблемы: ${(result.issues||[]).join(", ")}`);
  } catch (e) { alert("Ошибка: " + e.message); }
}

async function smartClassify() {
  if (!state.selectedId) return;
  try {
    const result = await api(`/api/jobs/${state.selectedId}/smart-classify`, { method: "POST", body: "{}" });
    alert(JSON.stringify(result, null, 2));
  } catch (e) { alert("Ошибка: " + e.message); }
}

async function getInterviewPrep(stage) {
  if (!state.selectedId) return;
  try {
    const result = await api(`/api/jobs/${state.selectedId}/interview-prep`, {
      method: "POST", body: JSON.stringify({ stage }),
    });
    $("#ai-fit-reasoning").innerHTML = renderMarkdown(result.content);
    switchAiTab("interview");
  } catch (e) { alert("Ошибка: " + e.message); }
}

async function getBehaviorSuggestions() {
  try {
    const result = await api("/api/behavior/suggest", { method: "POST", body: "{}" });
    alert(result.content);
  } catch (e) { alert("Ошибка: " + e.message); }
}

async function loadGhostJobs() {
  try {
    const jobs = await api("/api/ghost-jobs?days=7");
    const list = $("#ghost-jobs-list");
    list.innerHTML = "";
    if (!jobs.length) { list.innerHTML = '<p class="meta">Призраков нет!</p>'; return; }
    for (const j of jobs) {
      list.innerHTML += `<div class="source-row">
        <strong>${escapeHtml(j.title)}</strong>
        <div class="meta">${escapeHtml(j.company || "?")} · ${escapeHtml(j.source)}</div>
        <button onclick="markSelected('ghosted');loadGhostJobs();" style="margin-top:4px;">Отметить ghosted</button>
      </div>`;
    }
  } catch (e) { console.error(e); }
}

function shareToTelegram() {
  if (!state.selectedId) return;
  const job = state.jobs.find(j => j.id === state.selectedId);
  if (!job) return;
  const text = `${job.title}\n${job.company || ""}\n${job.salary_text || ""}\n${job.url}`;
  window.open(`https://t.me/share/url?url=${encodeURIComponent(job.url)}&text=${encodeURIComponent(text)}`, "_blank");
}
