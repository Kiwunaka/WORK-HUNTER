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
  selectedCampaignRunId: null,
  agent: {
    preflight: null,
    digest: null,
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

function setUiError(message = "") {
  const box = $("#ui-error-line");
  if (!box) return;
  box.textContent = message;
  box.hidden = !message;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    const message = data.error || response.statusText;
    setUiError(message);
    throw new Error(message);
  }
  return data;
}

function renderActionError(output, err) {
  const message = err?.message || String(err || "Action failed");
  if (output) {
    output.textContent = JSON.stringify({ ok: false, error: message }, null, 2);
  }
  setUiError(message);
}

async function loadJobs() {
  const source = $("#source-filter").value;
  const minScore = $("#min-score-filter").value || "0";
  const params = new URLSearchParams({ limit: "200", min_score: minScore });
  if (source) params.set("source", source);
  state.jobs = await api(`/api/inbox?${params.toString()}`);
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
    tr.innerHTML = `
      <td><input type="checkbox" class="job-checkbox" data-job-id="${job.id}" ${selectedJobIds.has(job.id) ? "checked" : ""} onclick="event.stopPropagation();toggleJobSelect(${job.id}, this.checked)"></td>
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
}

async function selectJob(id) {
  state.selectedId = id;
  renderJobs();
  const job = await api(`/api/jobs/${id}`);
  renderDetail(job);
  fillSelectedJobControls(id);
  updateChatContext();
  loadJobNote();
  try { $("#ai-panels").style.display = "block"; } catch (e) { /* ignore */ }
}

function fillSelectedJobControls(id) {
  if (!id) return;
  const ids = [
    "resume-variant-job-id",
    "job-detail-id-input",
    "application-preview-job-id",
    "replay-job-id",
    "pipeline-job-id",
    "interview-prep-job-id",
  ];
  for (const controlId of ids) {
    const control = $(`#${controlId}`);
    if (control && !control.value) control.value = String(id);
  }
}

function renderJobDetailHtml(job, options = {}) {
  const includeActions = options.includeActions !== false;
  const includeLetter = options.includeLetter !== false;
  const actionOutputId = options.actionOutputId || "action-output";
  const letterBoxId = options.letterBoxId || "letter-box";
  const score = job.score;
  const reasons = score?.reasons || [];
  const flags = score?.red_flags || [];
  const letter = job.latest_letter?.body || "";
  const actions = includeActions ? `
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
  ` : "";
  const letterEditor = includeLetter ? `
    <div class="section-title">Письмо</div>
    <textarea id="${escapeAttr(letterBoxId)}" class="letter" spellcheck="true">${escapeHtml(letter)}</textarea>
    <div id="${escapeAttr(actionOutputId)}" class="meta"></div>
  ` : "";
  return `
    <h2>${escapeHtml(job.title)}</h2>
    <p>${escapeHtml(job.company || "Компания не указана")} · ${escapeHtml(job.source)} · <a href="${escapeAttr(job.url)}" target="_blank" rel="noreferrer">открыть</a></p>
    ${actions}
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
    ${letterEditor}
  `;
}

function renderDetail(job) {
  $("#job-detail").className = "detail";
  $("#job-detail").innerHTML = renderJobDetailHtml(job);
}

async function loadJobDetailView() {
  const input = $("#job-detail-id-input");
  const output = $("#job-detail-output");
  const jobId = Number(input?.value || state.selectedId || 0);
  if (!output) return;
  if (!jobId) {
    output.className = "detail empty";
    output.innerHTML = "<p>Select a job in Inbox or enter a job ID.</p>";
    return;
  }
  const job = await api(`/api/jobs/${jobId}`);
  state.selectedId = jobId;
  if (input) input.value = String(jobId);
  fillSelectedJobControls(jobId);
  renderJobs();
  output.className = "detail";
  output.innerHTML = renderJobDetailHtml(job, { includeActions: false, includeLetter: false });
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
  $("#letter-box").value = draft.body;
}

async function prepareLetterAi() {
  if (!state.selectedId) return;
  setBusy("[onclick='prepareLetterAi()']", true);
  try {
    const draft = await api(`/api/jobs/${state.selectedId}/letter-ai`, { method: "POST", body: "{}" });
    $("#letter-box").value = draft.body;
  } catch (err) {
    $("#action-output").textContent = `Ошибка AI: ${err.message}. Проверь API-ключ в настройках.`;
  } finally {
    setBusy("[onclick='prepareLetterAi()']", false);
  }
}

async function previewHumanLetter(useForCampaign = false) {
  if (!state.selectedId) return;
  const selector = useForCampaign ? "#letter-use-campaign-button" : "#letter-preview-button";
  const template = $("#letter-template-select")?.value || "A";
  setBusy(selector, true);
  try {
    const preview = await api(`/api/jobs/${state.selectedId}/letter-preview`, {
      method: "POST",
      body: JSON.stringify({ template, use_for_campaign: useForCampaign }),
    });
    const selected = preview.campaign_letter || preview.variants?.find((item) => item.template === preview.selected_template);
    if (selected?.body) $("#letter-box").value = selected.body;
    $("#letter-preview-output").textContent = JSON.stringify(preview, null, 2);
  } catch (err) {
    $("#letter-preview-output").textContent = `Ошибка: ${err.message}`;
  } finally {
    setBusy(selector, false);
  }
}

async function applyHh(dryRun = true) {
  if (!state.selectedId) return;

  const letter = $("#letter-box")?.value || "";
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
  const certificationLevel = sourceCertificationLevel();
  const [sources, readiness, certification] = await Promise.all([
    api("/api/sources"),
    api("/api/source-status"),
    api("/api/sources/certification-matrix", { method: "POST", body: JSON.stringify({ level: certificationLevel }) }),
  ]);
  renderSourceReadiness(readiness);
  renderSourceCertificationMatrix(certification);
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

function selectedSourceActionName() {
  return $("#source-action-source")?.value || "hirehi";
}

async function syncSelectedSource() {
  const source = selectedSourceActionName();
  const output = $("#source-action-output");
  const limit = Number.parseInt($("#source-action-limit")?.value || "0", 10) || 0;
  try {
    setBusy("#source-sync-button", true);
    const result = await api(`/api/sources/${encodeURIComponent(source)}/sync`, {
      method: "POST",
      body: JSON.stringify({ limit }),
    });
    if (output) output.textContent = JSON.stringify(result, null, 2);
    await loadSources();
  } catch (err) {
    renderActionError(output, err);
  } finally {
    setBusy("#source-sync-button", false);
  }
}

async function testSelectedSource() {
  const source = selectedSourceActionName();
  const output = $("#source-action-output");
  try {
    setBusy("#source-test-button", true);
    const result = await api(`/api/sources/${encodeURIComponent(source)}/test`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    if (output) output.textContent = JSON.stringify(result, null, 2);
  } catch (err) {
    renderActionError(output, err);
  } finally {
    setBusy("#source-test-button", false);
  }
}

function sourceCertificationLevel() {
  const value = Number($("#source-certification-level")?.value || 5);
  return value === 6 ? 6 : 5;
}

function renderSourceCertificationMatrix(matrix) {
  const summary = $("#source-certification-summary");
  const box = $("#source-certification-matrix");
  if (!summary || !box) return;
  const counts = matrix?.summary || {};
  summary.textContent = `Certification L${matrix?.requested_level || 5}: ${counts.ready || 0}/${counts.total || 0} ready, ${counts.blocked || 0} blocked`;
  box.innerHTML = "";
  for (const [name, audit] of Object.entries(matrix?.sources || {})) {
    const missing = matrix?.missing_by_source?.[name] || audit.missing || [];
    const hasPromotion = Boolean(matrix?.promotion_payloads?.[name] || audit.promotion_payload);
    const row = document.createElement("div");
    row.className = "source-row";
    row.innerHTML = `
      <div class="agent-row-head">
        <strong>${escapeHtml(name)}</strong>
        <span class="agent-badge ${audit.ready ? "ready" : "blocked"}">${audit.ready ? "ready" : "blocked"}</span>
      </div>
      <div class="meta">requested L${escapeHtml(String(audit.requested_level || matrix?.requested_level || 5))}${hasPromotion ? " - promotion payload ready" : ""}</div>
      ${missing.length ? `<div class="error">${escapeHtml(missing.join(", "))}</div>` : `<div class="meta">evidence package complete</div>`}
    `;
    box.appendChild(row);
  }
}

async function loadSourceCertificationPlan() {
  const output = $("#source-certification-plan-output");
  try {
    const result = await api("/api/sources/certification-plan", {
      method: "POST",
      body: JSON.stringify({ level: sourceCertificationLevel() }),
    });
    if (output) output.textContent = JSON.stringify(result, null, 2);
  } catch (err) {
    renderActionError(output, err);
  }
}

async function recordSourceCertificationEvidence() {
  const source = $("#source-certification-source")?.value || "hirehi";
  const input = $("#source-certification-evidence-json");
  const output = $("#source-certification-evidence-output");
  let evidence = {};
  try {
    evidence = JSON.parse(input?.value || "{}");
  } catch (err) {
    const message = `Invalid evidence JSON: ${err.message || err}`;
    if (output) output.textContent = message;
    setUiError(message);
    return;
  }
  const result = await api(`/api/sources/${encodeURIComponent(source)}/certification-evidence`, {
    method: "POST",
    body: JSON.stringify({ level: sourceCertificationLevel(), evidence }),
  });
  if (output) output.textContent = JSON.stringify(result, null, 2);
  await loadSources();
}

async function promoteSourceCertification() {
  const source = $("#source-certification-promote-source")?.value || "hirehi";
  const output = $("#source-certification-promote-output");
  try {
    const result = await api(`/api/sources/${encodeURIComponent(source)}/certify`, {
      method: "POST",
      body: JSON.stringify({ level: sourceCertificationLevel() }),
    });
    if (output) output.textContent = JSON.stringify(result, null, 2);
    await loadSources();
  } catch (err) {
    renderActionError(output, err);
  }
}

async function recordSourceRedactionScan() {
  const source = $("#source-redaction-scan-source")?.value || "hirehi";
  const input = $("#source-redaction-scan-payload");
  const output = $("#source-redaction-scan-output");
  let payload = {};
  try {
    payload = JSON.parse(input?.value || "{}");
  } catch (err) {
    const message = `Invalid redaction payload JSON: ${err.message || err}`;
    if (output) output.textContent = message;
    setUiError(message);
    return;
  }
  const result = await api(`/api/sources/${encodeURIComponent(source)}/redaction-scan`, {
    method: "POST",
    body: JSON.stringify({
      level: sourceCertificationLevel(),
      payload,
      text: $("#source-redaction-scan-text")?.value || "",
    }),
  });
  if (output) output.textContent = JSON.stringify(result, null, 2);
  await loadSources();
}

async function configureSourceExternalApplyTarget() {
  const source = $("#source-external-target-source")?.value || "hirehi";
  const output = $("#source-external-target-output");
  const input = $("#source-external-target-payload-template");
  let payloadTemplate = {};
  try {
    payloadTemplate = JSON.parse(input?.value || "{}");
  } catch (err) {
    const message = `Invalid payload template JSON: ${err.message || err}`;
    if (output) output.textContent = message;
    setUiError(message);
    return;
  }
  const result = await api(`/api/sources/${encodeURIComponent(source)}/external-apply-target`, {
    method: "POST",
    body: JSON.stringify({
      level: sourceCertificationLevel(),
      session: $("#source-external-target-session")?.value || "",
      url: $("#source-external-target-url")?.value || "",
      method: $("#source-external-target-method")?.value || "POST",
      payload_template: payloadTemplate,
    }),
  });
  if (output) output.textContent = JSON.stringify(result, null, 2);
  await loadSources();
}

async function configureSourceExternalApplyFromHar() {
  const source = $("#source-external-har-source")?.value || "getmatch";
  const output = $("#source-external-har-output");
  const hosts = ($("#source-external-har-hosts")?.value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  try {
    const result = await api(`/api/sources/${encodeURIComponent(source)}/external-apply-from-har`, {
      method: "POST",
      body: JSON.stringify({
        level: sourceCertificationLevel(),
        path: $("#source-external-har-path")?.value || "",
        hosts,
      }),
    });
    if (output) output.textContent = JSON.stringify(result, null, 2);
    await loadSources();
  } catch (err) {
    renderActionError(output, err);
  }
}

function renderSourceReadiness(readiness) {
  const box = $("#source-readiness-list");
  if (!box) return;
  box.innerHTML = "";
  for (const [name, source] of Object.entries(readiness || {})) {
    const row = document.createElement("div");
    row.className = "source-row";
    const blockers = source.blockers || [];
    row.innerHTML = `
      <div class="agent-row-head">
        <strong>${escapeHtml(name)}</strong>
        <span class="agent-badge">${escapeHtml(source.readiness_badge || source.level_name || "")}</span>
      </div>
      <div class="meta">${escapeHtml(source.adapter_status || "")} · ${escapeHtml(source.adapter_class || "")}</div>
      <div class="meta">search: ${escapeHtml(source.search || "-")} · detail: ${escapeHtml(source.detail || "-")} · apply: ${escapeHtml(source.apply || "-")}</div>
      ${blockers.length ? `<div class="error">${escapeHtml(blockers.join(", "))}</div>` : `<div class="meta">ready for configured level</div>`}
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
  $("#agent-status-line").textContent = "Готово";
  refreshIcons();
}

async function loadAgentPreflight() {
  state.agent.preflight = await api("/api/agent/preflight");
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
  $("#agent-auth-note").textContent = (preflight.actions || auth.actions || []).slice(0, 2).join(" · ") || "ready";
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

async function runHhLabCall() {
  try {
    const payload = {
      method: $("#hh-lab-method").value,
      path: $("#hh-lab-path").value.trim(),
      params: parseHhLabJson("#hh-lab-params", {}),
      body: parseHhLabJson("#hh-lab-body", null),
    };
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
  if (button) button.disabled = true;
  $("#agent-operation-note").textContent = `${operation}: running`;
  try {
    const result = await api("/api/agent/run", {
      method: "POST",
      body: JSON.stringify({ operation, params: operation === "preflight" ? { live_auth: false } : {} }),
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

async function loadSetupStatus() {
  const [summary, doctor, ai] = await Promise.all([
    api("/api/init/status"),
    api("/api/doctor"),
    api("/api/ai/status"),
  ]);
  renderAiReadiness(ai);
  $("#setup-summary").textContent = JSON.stringify({ summary, doctor, ai }, null, 2);
}

function renderAiReadiness(ai) {
  const box = $("#ai-readiness-list");
  if (!box) return;
  const routes = Object.entries(ai?.routes || {});
  if (!routes.length) {
    box.innerHTML = '<p class="meta">No AI routes configured.</p>';
    return;
  }
  box.innerHTML = routes.map(([name, route]) => {
    const ready = Boolean(route.ready);
    const badge = ready ? "ready" : "blocked";
    const model = route.model || "model not set";
    const adapter = route.adapter || "adapter not set";
    const auth = route.auth || "";
    const actions = (route.actions || []).join(", ");
    return `
      <div class="agent-row">
        <div class="agent-row-head">
          <strong>${escapeHtml(name)}</strong>
          <span class="agent-badge ${badge}">${ready ? "ready" : "blocked"}</span>
        </div>
        <div class="meta">${escapeHtml(adapter)} · ${escapeHtml(model)}${auth ? ` · ${escapeHtml(auth)}` : ""}</div>
        ${actions ? `<div class="error">${escapeHtml(actions)}</div>` : ""}
      </div>
    `;
  }).join("");
}

async function testAiRoute() {
  const route = $("#ai-test-route")?.value.trim() || "smart";
  const prompt = $("#ai-test-prompt")?.value.trim() || "ping";
  const result = await api("/api/ai/test", {
    method: "POST",
    body: JSON.stringify({ route, prompt, dry_run: true }),
  });
  $("#setup-summary").textContent = JSON.stringify(result, null, 2);
}

function setupWoImportSource() {
  return $("#setup-import-wo-source")?.value.trim() || "";
}

async function previewSetupWoImport() {
  const output = $("#setup-import-wo-output");
  try {
    setBusy("#setup-import-wo-preview-button", true);
    const result = await api("/api/init/import-wo/preview", {
      method: "POST",
      body: JSON.stringify({ source: setupWoImportSource() }),
    });
    if (output) output.textContent = JSON.stringify(result, null, 2);
  } catch (err) {
    renderActionError(output, err);
  } finally {
    setBusy("#setup-import-wo-preview-button", false);
  }
}

async function applySetupWoImport() {
  const output = $("#setup-import-wo-output");
  if (!window.confirm("Apply redacted WO/FLOW import into this local project?")) return;
  try {
    setBusy("#setup-import-wo-apply-button", true);
    const result = await api("/api/init/import-wo", {
      method: "POST",
      body: JSON.stringify({ source: setupWoImportSource() }),
    });
    if (output) output.textContent = JSON.stringify(result, null, 2);
    await loadSetupStatus();
  } catch (err) {
    renderActionError(output, err);
  } finally {
    setBusy("#setup-import-wo-apply-button", false);
  }
}

async function loadOnboardingQuestions() {
  const [questions, status, facts] = await Promise.all([
    api("/api/onboarding/questions"),
    api("/api/onboarding/status"),
    api("/api/candidate/facts"),
  ]);
  const select = $("#onboarding-question-select");
  if (select) {
    select.innerHTML = questions.map((q) => `<option value="${escapeAttr(q.id)}">${escapeHtml(q.title || q.id)}</option>`).join("");
  }
  $("#onboarding-questions").innerHTML = questions.map((q) => `
    <div class="agent-row">
      <div class="agent-row-head"><strong>${escapeHtml(q.title || q.id)}</strong><span class="agent-badge">${escapeHtml(q.category || "")}</span></div>
      <div class="meta">${escapeHtml(q.prompt || "")}</div>
    </div>
  `).join("");
  $("#onboarding-status").textContent = JSON.stringify({ status, facts }, null, 2);
}

async function submitOnboardingAnswer() {
  const questionId = $("#onboarding-question-select")?.value || "experience";
  const answer = $("#onboarding-answer")?.value || "";
  if (!answer.trim()) return;
  await api("/api/onboarding/answer", {
    method: "POST",
    body: JSON.stringify({ question_id: questionId, answer, source: "web_ui" }),
  });
  $("#onboarding-answer").value = "";
  await loadOnboardingQuestions();
  await loadCandidateMap();
}

async function loadCandidateMap() {
  const [profile, facts, completeness] = await Promise.all([
    api("/api/candidate/profile"),
    api("/api/candidate/facts"),
    api("/api/candidate/completeness"),
  ]);
  $("#candidate-facts-list").innerHTML = facts.map((fact) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>${escapeHtml(fact.key || "fact")}</strong>
        <span class="agent-badge ${escapeAttr(fact.status || "")}">${escapeHtml(fact.status || "")}</span>
      </div>
      <div>${escapeHtml(String(fact.value || ""))}</div>
      <div class="meta">${escapeHtml(fact.source || "")}</div>
      ${fact.status === "confirmed" ? "" : `<button onclick="confirmCandidateFact(${Number(fact.id)})">Confirm</button>`}
    </div>
  `).join("") || `<p class="meta">No facts yet.</p>`;
  $("#candidate-map-output").textContent = JSON.stringify({ profile, completeness }, null, 2);
}

async function confirmCandidateFact(factId) {
  await api("/api/candidate/confirm-fact", {
    method: "POST",
    body: JSON.stringify({ fact_id: factId }),
  });
  await loadCandidateMap();
  await loadOnboardingQuestions();
}

async function buildResumeVariant() {
  const jobId = Number($("#resume-variant-job-id")?.value || 0);
  const resumeId = Number($("#resume-variant-resume-id")?.value || 0);
  if (!jobId || !resumeId) return;
  const result = await api("/api/resume-variants/build", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId, resume_id: resumeId }),
  });
  $("#resume-variant-output").textContent = JSON.stringify(result, null, 2);
  renderResumeVariantDiff(result.diff || {});
}

function renderResumeVariantDiff(diff) {
  const target = $("#resume-variant-diff");
  if (!target) return;
  const added = diff.added_lines || [];
  const removed = diff.removed_lines || [];
  target.innerHTML = `
    <div class="agent-row">
      <div class="agent-row-head"><strong>Variant diff</strong><span class="agent-badge">${added.length} added / ${removed.length} removed</span></div>
      <div class="meta">Added</div>
      <pre class="agent-json">${escapeHtml(added.join("\n") || "none")}</pre>
      <div class="meta">Removed</div>
      <pre class="agent-json">${escapeHtml(removed.join("\n") || "none")}</pre>
    </div>
  `;
}

async function buildApplicationPreview() {
  const jobId = Number($("#application-preview-job-id")?.value || 0);
  if (!jobId) return;
  let sourcePayload = {};
  try {
    sourcePayload = JSON.parse($("#application-preview-payload")?.value || "{}");
  } catch (err) {
    $("#application-preview-output").textContent = `Invalid payload JSON: ${err.message}`;
    return;
  }
  const result = await api("/api/applications/build-pack", {
    method: "POST",
    body: JSON.stringify({
      job_id: jobId,
      resume_variant: {
        id: $("#application-preview-resume-variant-id")?.value || "",
      },
      cover_letter: $("#application-preview-letter")?.value || "",
      source_payload: sourcePayload,
      campaign_policy: { enabled: true, real_apply: false },
    }),
  });
  $("#application-preview-output").textContent = JSON.stringify(result, null, 2);
}

function externalApplyFormPayload() {
  try {
    return JSON.parse($("#external-apply-form-json")?.value || "{}");
  } catch (err) {
    $("#application-preview-output").textContent = `Invalid external form JSON: ${err.message}`;
    return null;
  }
}

function externalApplyRequestBody(confirm = false) {
  const form = externalApplyFormPayload();
  if (!form) return null;
  return {
    form,
    resume_variant: {
      id: $("#application-preview-resume-variant-id")?.value || "",
    },
    cover_letter: $("#application-preview-letter")?.value || "",
    campaign_policy: { enabled: true, real_apply: false },
    confirm,
    submit_certified: Boolean($("#external-apply-submit-certified")?.checked),
    campaign_policy_apply: false,
  };
}

async function dryRunExternalApply() {
  const jobId = Number($("#application-preview-job-id")?.value || 0);
  if (!jobId) return;
  const body = externalApplyRequestBody(false);
  if (!body) return;
  const result = await api(`/api/jobs/${jobId}/external-apply/dry-run`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  $("#application-preview-output").textContent = JSON.stringify(result, null, 2);
}

async function confirmExternalApply() {
  const jobId = Number($("#application-preview-job-id")?.value || 0);
  if (!jobId) return;
  const body = externalApplyRequestBody(true);
  if (!body) return;
  const message = body.submit_certified
    ? "Submit through a certified external adapter?"
    : "Prepare external manual submit handoff?";
  if (!window.confirm(message)) return;
  const result = await api(`/api/jobs/${jobId}/external-apply/confirm`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  $("#application-preview-output").textContent = JSON.stringify(result, null, 2);
  await loadJobs();
}

async function loadCampaignRuns() {
  const [runs, preflight] = await Promise.all([
    api("/api/hh/campaigns"),
    api("/api/agent/preflight"),
  ]);
  const pauseState = $("#campaign-pause-state");
  if (pauseState) {
    const paused = Boolean(preflight.agent?.paused);
    pauseState.textContent = paused ? `paused: ${preflight.agent?.pause_reason || "manual"}` : "running";
    pauseState.className = `agent-badge ${paused ? "blocked" : "ready"}`;
  }
  $("#campaign-runs-list").innerHTML = runs.map((run) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>Run #${escapeHtml(String(run.id))}</strong>
        <span class="agent-badge ${escapeAttr(run.status || "")}">${escapeHtml(run.status || "")}</span>
      </div>
      <pre class="agent-json">${escapeHtml(JSON.stringify(run.counts || {}, null, 2))}</pre>
      <button onclick="loadCampaignRun(${Number(run.id)})">Load items</button>
    </div>
  `).join("") || `<p class="meta">No campaign runs.</p>`;
}

async function loadCampaignRun(runId) {
  state.selectedCampaignRunId = Number(runId);
  const detail = await api(`/api/hh/campaigns/${runId}`);
  $("#campaign-plan-output").textContent = JSON.stringify(detail, null, 2);
}

async function planHhCampaign() {
  const dailyCap = Number($("#campaign-daily-cap")?.value || 0);
  const body = {
    limit: Number($("#campaign-limit")?.value || 50),
    min_score: Number($("#campaign-min-score")?.value || 70),
    skip_tests: true,
    ai_filter_mode: $("#campaign-ai-filter")?.value || "off",
  };
  if (dailyCap > 0) body.daily_cap = dailyCap;
  const result = await api("/api/hh/campaigns/plan", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (result.id) state.selectedCampaignRunId = Number(result.id);
  $("#campaign-plan-output").textContent = JSON.stringify(result, null, 2);
  await loadCampaignRuns();
}

async function planExternalCampaign() {
  const dailyCap = Number($("#campaign-daily-cap")?.value || 0);
  const body = {
    source: $("#external-campaign-source")?.value || "hirehi",
    limit: Number($("#campaign-limit")?.value || 50),
    min_score: Number($("#campaign-min-score")?.value || 70),
  };
  if (dailyCap > 0) body.daily_cap = dailyCap;
  const result = await api("/api/campaigns/external/plan", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (result.id) state.selectedCampaignRunId = Number(result.id);
  $("#campaign-plan-output").textContent = JSON.stringify(result, null, 2);
  await loadCampaignRuns();
}

async function confirmCampaignRun() {
  const runId = Number(state.selectedCampaignRunId || 0);
  if (!runId) return;
  if (!window.confirm("Confirm real HH campaign apply?")) return;
  const result = await api(`/api/hh/campaigns/${runId}/confirm`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
  $("#campaign-plan-output").textContent = JSON.stringify(result, null, 2);
  await loadCampaignRuns();
}

async function confirmExternalCampaignRun() {
  const runId = Number(state.selectedCampaignRunId || 0);
  if (!runId) return;
  if (!window.confirm("Confirm real external campaign apply?")) return;
  const result = await api(`/api/campaigns/${runId}/run-external`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
  $("#campaign-plan-output").textContent = JSON.stringify(result, null, 2);
  await loadCampaignRuns();
}

async function killCampaigns() {
  if (!window.confirm("Pause all campaign execution now?")) return;
  try {
    setBusy("#campaign-kill-switch-button", true);
    const result = await api("/api/agent/pause", {
      method: "POST",
      body: JSON.stringify({ reason: "campaign_ui_kill_switch" }),
    });
    $("#campaign-plan-output").textContent = JSON.stringify(result, null, 2);
    await loadCampaignRuns();
  } catch (err) {
    renderActionError($("#campaign-plan-output"), err);
  } finally {
    setBusy("#campaign-kill-switch-button", false);
  }
}

async function resumeCampaigns() {
  try {
    setBusy("#campaign-resume-button", true);
    const result = await api("/api/agent/resume", {
      method: "POST",
      body: JSON.stringify({ reason: "campaign_ui_resume" }),
    });
    $("#campaign-plan-output").textContent = JSON.stringify(result, null, 2);
    await loadCampaignRuns();
  } catch (err) {
    renderActionError($("#campaign-plan-output"), err);
  } finally {
    setBusy("#campaign-resume-button", false);
  }
}

function pipelineJobId() {
  return Number($("#pipeline-job-id")?.value || state.selectedId || 0);
}

function interviewPrepJobId() {
  return Number($("#interview-prep-job-id")?.value || state.selectedId || 0);
}

async function loadPipelineStatus() {
  const jobId = pipelineJobId();
  if (!jobId) return;
  const result = await api(`/api/pipeline/jobs/${jobId}`);
  $("#pipeline-status-output").textContent = JSON.stringify(result, null, 2);
}

async function buildPipelinePrepPack() {
  const jobId = pipelineJobId();
  if (!jobId) return;
  const result = await api(`/api/pipeline/jobs/${jobId}/prep-pack`, {
    method: "POST",
    body: JSON.stringify({ stage: $("#pipeline-stage-select")?.value || "tech" }),
  });
  $("#pipeline-prep-output").textContent = JSON.stringify(result, null, 2);
}

async function buildInterviewPrepPack() {
  const jobId = interviewPrepJobId();
  if (!jobId) return;
  const result = await api(`/api/pipeline/jobs/${jobId}/prep-pack`, {
    method: "POST",
    body: JSON.stringify({ stage: $("#interview-prep-stage-select")?.value || "tech" }),
  });
  $("#interview-prep-output").textContent = JSON.stringify(result, null, 2);
}

async function schedulePipelineFollowup() {
  const jobId = pipelineJobId();
  if (!jobId) return;
  const eventAt = $("#pipeline-event-at")?.value || "";
  if (!eventAt) {
    $("#pipeline-status-output").textContent = "Set event date and time first.";
    return;
  }
  const result = await api(`/api/pipeline/jobs/${jobId}/event`, {
    method: "POST",
    body: JSON.stringify({ event_type: "follow_up", event_at: eventAt }),
  });
  $("#pipeline-status-output").textContent = JSON.stringify(result, null, 2);
  await loadEvents();
}

async function loadReplayTimeline() {
  const runId = $("#replay-run-id")?.value;
  const jobId = $("#replay-job-id")?.value;
  if (!runId && !jobId) return;
  const params = replayQueryParams();
  const query = params.toString() ? `?${params.toString()}` : "";
  const replay = runId
    ? await api(`/api/replay/runs/${encodeURIComponent(runId)}${query}`)
    : await api(`/api/replay/jobs/${encodeURIComponent(jobId)}${query}`);
  const events = replay.events || [];
  $("#replay-timeline-list").innerHTML = events.map((event) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>${escapeHtml(event.title || event.event_type || "event")}</strong>
        <span class="agent-badge">${escapeHtml(event.event_type || "")}</span>
      </div>
      <div class="meta">${escapeHtml(event.created_at || "")}</div>
      <pre class="agent-json">${escapeHtml(JSON.stringify(event.data || {}, null, 2))}</pre>
    </div>
  `).join("") || `<p class="meta">No replay events.</p>`;
}

function replayQueryParams() {
  const params = new URLSearchParams();
  const source = $("#replay-source-filter")?.value.trim();
  const eventType = $("#replay-event-type-filter")?.value.trim();
  if (source) params.set("source", source);
  if (eventType) params.set("event_type", eventType);
  return params;
}

function exportReplayMarkdown() {
  const runId = $("#replay-run-id")?.value;
  const jobId = $("#replay-job-id")?.value;
  if (!runId && !jobId) return;
  const params = replayQueryParams();
  const query = params.toString() ? `?${params.toString()}` : "";
  const path = runId
    ? `/api/replay/runs/${encodeURIComponent(runId)}/export${query}`
    : `/api/replay/jobs/${encodeURIComponent(jobId)}/export${query}`;
  window.open(path, "_blank");
}

async function loadSecurityStatus() {
  const result = await api("/api/security/status");
  $("#audit-security-output").textContent = JSON.stringify(result, null, 2);
}

async function runAuditSecurityRedactionScan() {
  const output = $("#audit-security-redaction-output");
  try {
    setBusy("#audit-security-redaction-button", true);
    const result = await api("/api/init/redaction-scan", {
      method: "POST",
      body: JSON.stringify({ text: $("#audit-security-redaction-text")?.value || "" }),
    });
    if (output) output.textContent = JSON.stringify(result, null, 2);
  } catch (err) {
    renderActionError(output, err);
  } finally {
    setBusy("#audit-security-redaction-button", false);
  }
}

function browserLabSource() {
  return $("#browser-lab-source")?.value.trim() || "getmatch";
}

async function loadBrowserLabStatus() {
  const source = encodeURIComponent(browserLabSource());
  const result = await api(`/api/browser-lab/status?source=${source}`);
  $("#browser-lab-output").textContent = JSON.stringify(result, null, 2);
}

async function openBrowserLabLogin() {
  const result = await api("/api/browser-lab/open-login", {
    method: "POST",
    body: JSON.stringify({ source: browserLabSource() }),
  });
  $("#browser-lab-output").textContent = JSON.stringify(result, null, 2);
}

async function importBrowserLabHar() {
  const hosts = ($("#browser-lab-hosts")?.value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const result = await api("/api/browser-lab/import-har", {
    method: "POST",
    body: JSON.stringify({
      source: browserLabSource(),
      path: $("#browser-lab-har-path")?.value.trim() || "",
      allowed_hosts: hosts,
      configure_external_apply: Boolean($("#browser-lab-configure-external-apply")?.checked),
    }),
  });
  $("#browser-lab-output").textContent = JSON.stringify(result, null, 2);
}

function browserLabRequestContext() {
  const output = $("#browser-lab-output");
  let form = {};
  try {
    form = JSON.parse($("#browser-lab-form-json")?.value || "{}");
  } catch (err) {
    if (output) output.textContent = `Invalid form JSON: ${err.message}`;
    return null;
  }
  let persona = {};
  try {
    persona = JSON.parse($("#browser-lab-persona-json")?.value || "{}");
  } catch (err) {
    if (output) output.textContent = `Invalid persona JSON: ${err.message}`;
    return null;
  }
  return { form, persona };
}

async function mapBrowserLabForm() {
  const context = browserLabRequestContext();
  if (!context) return;
  const result = await api("/api/browser-lab/forms/map", {
    method: "POST",
    body: JSON.stringify({ source: browserLabSource(), form: context.form, persona: context.persona }),
  });
  $("#browser-lab-output").textContent = JSON.stringify(result, null, 2);
}

async function dryRunBrowserLabForm() {
  const context = browserLabRequestContext();
  if (!context) return;
  const result = await api("/api/browser-lab/forms/dry-run", {
    method: "POST",
    body: JSON.stringify({ source: browserLabSource(), form: context.form, persona: context.persona }),
  });
  $("#browser-lab-output").textContent = JSON.stringify(result, null, 2);
}

async function executeBrowserLabDryRun() {
  const context = browserLabRequestContext();
  if (!context) return;
  const result = await api("/api/browser-lab/forms/execute-dry-run", {
    method: "POST",
    body: JSON.stringify({ source: browserLabSource(), form: context.form, persona: context.persona, headless: true }),
  });
  $("#browser-lab-output").textContent = JSON.stringify(result, null, 2);
}

async function loadRoadmapView(view) {
  if (view === "setup") await loadSetupStatus();
  if (view === "onboarding") await loadOnboardingQuestions();
  if (view === "candidate-map") await loadCandidateMap();
  if (view === "job-detail") await loadJobDetailView();
  if (view === "campaigns") await loadCampaignRuns();
  if (view === "pipeline") await loadPipelineStatus();
  if (view === "interview-prep") fillSelectedJobControls(state.selectedId);
  if (view === "audit-security") await loadSecurityStatus();
  if (view === "browser-lab") await loadBrowserLabStatus();
}

async function loadRoadmapViewSafe(view) {
  try {
    setUiError("");
    await loadRoadmapView(view);
  } catch (err) {
    setUiError(err.message || String(err));
    console.error(err);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  for (const button of document.querySelectorAll("button")) {
    button.dataset.label = button.textContent;
  }
  setupResumeBuilderDefaults();
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".nav-button").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      const view = $(`#view-${button.dataset.view}`);
      if (!view) return;
      view.classList.add("active");
      loadRoadmapViewSafe(button.dataset.view);
    });
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
  $("#hh-lab-run-button")?.addEventListener("click", runHhLabCall);
  $("#hh-lab-save-snippet-button")?.addEventListener("click", saveHhLabSnippet);
  $("#setup-refresh-button")?.addEventListener("click", loadSetupStatus);
  $("#ai-test-button")?.addEventListener("click", testAiRoute);
  $("#setup-import-wo-preview-button")?.addEventListener("click", previewSetupWoImport);
  $("#setup-import-wo-apply-button")?.addEventListener("click", applySetupWoImport);
  $("#onboarding-refresh-button")?.addEventListener("click", loadOnboardingQuestions);
  $("#onboarding-submit-button")?.addEventListener("click", submitOnboardingAnswer);
  $("#candidate-refresh-button")?.addEventListener("click", loadCandidateMap);
  $("#resume-variant-button")?.addEventListener("click", buildResumeVariant);
  $("#job-detail-load-button")?.addEventListener("click", () => loadJobDetailView().catch((err) => renderActionError($("#job-detail-output"), err)));
  $("#application-preview-button")?.addEventListener("click", buildApplicationPreview);
  $("#external-apply-dry-run-button")?.addEventListener("click", dryRunExternalApply);
  $("#external-apply-confirm-button")?.addEventListener("click", confirmExternalApply);
  $("#campaign-refresh-button")?.addEventListener("click", loadCampaignRuns);
  $("#campaign-plan-button")?.addEventListener("click", planHhCampaign);
  $("#external-campaign-plan-button")?.addEventListener("click", planExternalCampaign);
  $("#campaign-confirm-run-button")?.addEventListener("click", confirmCampaignRun);
  $("#external-campaign-run-button")?.addEventListener("click", confirmExternalCampaignRun);
  $("#campaign-kill-switch-button")?.addEventListener("click", killCampaigns);
  $("#campaign-resume-button")?.addEventListener("click", resumeCampaigns);
  $("#pipeline-refresh-button")?.addEventListener("click", loadPipelineStatus);
  $("#pipeline-prep-pack-button")?.addEventListener("click", buildPipelinePrepPack);
  $("#pipeline-schedule-followup-button")?.addEventListener("click", schedulePipelineFollowup);
  $("#interview-prep-pack-button")?.addEventListener("click", buildInterviewPrepPack);
  $("#replay-refresh-button")?.addEventListener("click", loadReplayTimeline);
  $("#replay-export-button")?.addEventListener("click", exportReplayMarkdown);
  $("#audit-security-refresh-button")?.addEventListener("click", loadSecurityStatus);
  $("#audit-security-redaction-button")?.addEventListener("click", runAuditSecurityRedactionScan);
  $("#browser-lab-status-button")?.addEventListener("click", loadBrowserLabStatus);
  $("#browser-lab-open-login-button")?.addEventListener("click", openBrowserLabLogin);
  $("#browser-lab-import-har-button")?.addEventListener("click", importBrowserLabHar);
  $("#browser-lab-map-form-button")?.addEventListener("click", mapBrowserLabForm);
  $("#browser-lab-dry-run-button")?.addEventListener("click", dryRunBrowserLabForm);
  $("#browser-lab-execute-dry-run-button")?.addEventListener("click", executeBrowserLabDryRun);
  $("#source-sync-button")?.addEventListener("click", syncSelectedSource);
  $("#source-test-button")?.addEventListener("click", testSelectedSource);
  $("#source-external-target-button")?.addEventListener("click", configureSourceExternalApplyTarget);
  $("#source-external-har-button")?.addEventListener("click", configureSourceExternalApplyFromHar);
  $("#source-redaction-scan-button")?.addEventListener("click", recordSourceRedactionScan);
  $("#source-certification-plan-button")?.addEventListener("click", loadSourceCertificationPlan);
  $("#source-certification-evidence-button")?.addEventListener("click", recordSourceCertificationEvidence);
  $("#source-certification-promote-button")?.addEventListener("click", promoteSourceCertification);
  $("#resume-import-button")?.addEventListener("click", importResume);
  document.querySelectorAll("[data-agent-operation]").forEach((button) => {
    button.addEventListener("click", () => runAgentOperation(button.dataset.agentOperation, button));
  });

  document.querySelectorAll(".ai-tab").forEach(tab => {
    tab.addEventListener("click", () => switchAiTab(tab.dataset.aiPanel));
  });
  document.querySelectorAll(".agent-tab").forEach(tab => {
    tab.addEventListener("click", () => switchAgentPanel(tab.dataset.agentPanel));
  });

  document.querySelector("[data-view='favorites']")?.addEventListener("click", loadFavorites);
  document.querySelector("[data-view='resumes']")?.addEventListener("click", loadResumes);
  document.querySelector("[data-view='job-detail']")?.addEventListener("click", () => loadJobDetailView().catch(console.error));
  document.querySelector("[data-view='interview-prep']")?.addEventListener("click", () => fillSelectedJobControls(state.selectedId));
  document.querySelector("[data-view='stats']")?.addEventListener("click", loadStats);
  document.querySelector("[data-view='trends']")?.addEventListener("click", loadMarketTrends);
  document.querySelector("[data-view='agent']")?.addEventListener("click", () => loadAgentCockpit().catch(console.error));

  loadTheme();
  refreshIcons();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js");
  }
  await Promise.all([loadJobs(), loadConfig(), loadProfile(), loadSources()]);
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

async function importResume() {
  const path = $("#resume-import-path")?.value.trim();
  if (!path) return;
  const result = await api("/api/resumes/import", {
    method: "POST",
    body: JSON.stringify({
      path,
      activate: Boolean($("#resume-import-activate")?.checked),
    }),
  });
  $("#resume-import-output").textContent = JSON.stringify(result, null, 2);
  if (result.status === "imported") {
    await loadResumes();
  }
}

async function loadResumes() {
  try {
    const resumes = await api("/api/resumes");
    const list = $("#resumes-list");
    list.innerHTML = "";
    for (const r of resumes) {
      const details = [r.is_active ? "★ Активное" : "", r.source_format || "", r.imported_from || "", `ATS: ${r.ats_score ?? "—"}`].filter(Boolean).join(" · ");
      list.innerHTML += `<div class="source-row">
        <strong>${escapeHtml(r.name)}</strong>
        <div class="meta">${escapeHtml(details)}</div>
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
    tr.innerHTML = `
      <td><input type="checkbox" class="job-checkbox" data-job-id="${job.id}" ${selectedJobIds.has(job.id) ? "checked" : ""} onclick="event.stopPropagation();toggleJobSelect(${job.id}, this.checked)"></td>
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
