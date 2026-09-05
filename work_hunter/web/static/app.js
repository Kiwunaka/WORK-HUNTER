const state = {
  jobs: [],
  resumes: [],
  selectedId: null,
  config: null,
  chatMessages: [],
  chatJobId: null,
  profile: null,
  autoSyncInterval: null,
  autoSyncMinutes: 0,
  darkTheme: false,
  route: null,
  sourceKeys: null,
  sourceCapabilities: {},
  hhWebStatus: null,
  workMode: "apply",
  initialLoadsPending: true,
  routeNeedsReload: false,
  calendarEvents: [],
  calendarCursor: new Date(),
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
    chats: null,
    chatReplyPlan: null,
    chatAuthStarting: false,
  },
  hhAutopilot: {
    config: null,
    managed: [],
    status: null,
    historyOffset: 0,
  },
};

const $ = (selector) => document.querySelector(selector);
const {
  openLiveAction,
  stableActionFingerprint,
  maskForUi,
  validateApplyMutation,
  validateResumeAccountMutation,
  validateLabMutation,
} = window.WorkHunterUI.feedback;

function notify(type, scope, code, title, message = "", action = null) {
  return window.appNotifications?.push({ type, scope, code, title, message, action });
}

function notifyError(scope, error, title = "Не удалось выполнить действие") {
  return notify("error", scope, "request-failed", title, String(error?.message || error));
}

const ROUTES = {
  today: { url: "/today", title: "Сегодня", summary: "Главные действия, новые совпадения и ближайшие события.", actions: false },
  inbox: { url: "/jobs?filter=all", title: "Вакансии", summary: "Поиск, оценка и подготовка точных откликов.", actions: true },
  calendar: { url: "/calendar", title: "Календарь", summary: "Собеседования, напоминания и следующие шаги.", actions: false },
  favorites: { url: "/jobs?filter=saved", view: "inbox", title: "Сохранённые", summary: "Сохранённые вакансии и заметки.", actions: true },
  chat: { url: "/assistant", title: "Ассистент", summary: "Диалог с контекстом выбранной вакансии.", actions: false },
  agent: { url: "/applications?tab=pipeline", title: "Отклики", summary: "Воронка, подтверждения и автоматизация HH.", actions: false },
  settings: { url: "/settings?section=profile", title: "Настройки", summary: "Профили, резюме, интеграции и внешний вид.", actions: false },
  sources: { url: "/sources", title: "Источники", summary: "Состояние подключений и последняя синхронизация.", actions: false },
  stats: { url: "/analytics?tab=overview", title: "Аналитика", summary: "Воронка, распределение score и источники.", actions: false },
  trends: { url: "/analytics?tab=trends", view: "stats", title: "Тренды", summary: "Анализ рынка по загруженным вакансиям.", actions: false },
};

const DESTINATION_TO_VIEW = Object.freeze({
  today: "today",
  vacancies: "inbox",
  applications: "agent",
  calendar: "calendar",
  assistant: "chat",
  analytics: "stats",
  sources: "sources",
  settings: "settings",
});

function routeViewFromPath(pathname, search = window.location.search) {
  if (pathname === "/today" && window.__WORK_HUNTER_TEST_LEGACY_ROOT__) return "inbox";
  const resolved = window.WorkHunterUI.route.resolve(pathname, search, window.location.hash, state.sourceKeys);
  return DESTINATION_TO_VIEW[resolved.destination] || "today";
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

function requirePositiveInteger(value, fieldName = "id") {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`Invalid ${fieldName}`);
  }
  return parsed;
}

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value ?? ""), window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch (_error) {
    return "#";
  }
}

function numericRecordAction(action, value, fieldName) {
  const id = requirePositiveInteger(value, fieldName);
  return `data-record-action="${escapeAttr(action)}" data-record-id="${id}"`;
}

function keyedRecordAction(action, value) {
  return `data-record-action="${escapeAttr(action)}" data-record-key="${escapeAttr(value)}"`;
}

const sectionLoadingCounts = new WeakMap();

function setSectionLoading(name, busy) {
  const section = document.querySelector(`[data-load-section="${name}"]`);
  if (!section) return;
  const activeCount = sectionLoadingCounts.get(section) || 0;
  const nextCount = busy ? activeCount + 1 : Math.max(0, activeCount - 1);
  if (nextCount > 0) {
    sectionLoadingCounts.set(section, nextCount);
  } else {
    sectionLoadingCounts.delete(section);
  }
  const isBusy = nextCount > 0;
  section.setAttribute("aria-busy", String(isBusy));
  if (name === "jobs") {
    const skeleton = $("#jobs-skeleton");
    if (skeleton) skeleton.style.display = isBusy ? "block" : "none";
  }
}

async function loadJobs() {
  setSectionLoading("jobs", true);
  try {
    const source = $("#source-filter").value;
    const minScore = $("#min-score-filter").value || "0";
    const params = new URLSearchParams({ limit: "200", min_score: minScore });
    if (source) params.set("source", source);
    const routeParams = state.route?.destination === "vacancies"
      ? new URLSearchParams(state.route.query)
      : new URLSearchParams();
    const status = routeParams.get("status")
      || (routeParams.get("filter") === "saved" ? "saved" : "");
    if (status) params.set("status", status);
    state.jobs = await api(`/api/jobs?${params.toString()}`);
    renderJobs();
    renderApplicationPipeline();
    updateSummary();
    await applyJobRouteSelection();
    if (state.route?.destination === "vacancies" && !state.selectedId && state.jobs.length) {
      await selectJob(requirePositiveInteger(state.jobs[0].id, "job id"));
    }
  } finally {
    setSectionLoading("jobs", false);
  }
}

async function applyJobRouteSelection() {
  if (!state.route || !["vacancies", "assistant"].includes(state.route.destination)) return;
  const params = new URLSearchParams(state.route.query);
  const rawJobId = params.get("job");
  if (!rawJobId) return;
  const jobId = requirePositiveInteger(rawJobId, "job id");
  try {
    if (Number(state.selectedId) !== jobId) await selectJob(jobId);
    if (state.route.destination === "assistant") {
      const attach = $("#chat-attach-job");
      if (attach) attach.checked = true;
      updateChatContext();
    }
  } catch (error) {
    notify("warning", "route-job", "record-not-found", "Вакансия из ссылки не найдена");
  }
}

function bindJobRowActivation(row, job) {
  row.tabIndex = 0;
  row.setAttribute("role", "button");
  row.setAttribute("aria-label", `Открыть ${job.title || "вакансию"}`);
  const activate = () => selectJob(requirePositiveInteger(job.id, "job id"));
  row.addEventListener("click", (event) => {
    if (!event.target.closest("input,button,a")) activate();
  });
  row.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && !event.target.closest("input,button,a")) {
      event.preventDefault();
      activate();
    }
  });
}

function renderJobs() {
  const body = $("#jobs-body");
  body.innerHTML = "";
  for (const job of state.jobs) {
    const jobId = requirePositiveInteger(job.id, "job id");
    const tr = document.createElement("tr");
    tr.className = jobId === state.selectedId ? "selected" : "";
    bindJobRowActivation(tr, job);
    const score = job.score ? job.score.total_score : "-";
    const scoreClass = score === "-" ? "" : score >= 70 ? " high" : score < 35 ? " low" : "";
    const scoreGuide = score === "-" ? "" : ' data-guide="match-score"';
    const checked = selectedJobIds.has(jobId) ? "checked" : "";
    const statusKey = String(job.status || "new").toLowerCase();
    const statusLabel = ({ new: "Новая", saved: "Сохранена", applied: "Отклик отправлен", hidden: "Скрыта", replied: "Ответили", interview: "Собеседование" })[statusKey] || job.status || "Новая";
    const sourceKey = String(job.source || "").toLowerCase();
    const sourceLabel = SOURCE_META[sourceKey]?.label || job.source || "Источник";
    tr.innerHTML = `
      <td><input type="checkbox" class="job-checkbox" data-job-id="${jobId}" data-record-action="toggle-job-select" data-record-id="${jobId}" ${checked}></td>
      <td><span class="score${scoreClass}"${scoreGuide}>${escapeHtml(String(score))}</span></td>
      <td>
        <div class="title">${escapeHtml(job.title || "Без названия")}</div>
        <div class="meta">${escapeHtml(job.company || "Компания не указана")}</div>
        <div class="meta">${escapeHtml([job.salary_text, job.location, job.remote ? "remote" : ""].filter(Boolean).join(" · "))}</div>
      </td>
      <td><span class="job-source">${sourceLogoMarkup(sourceKey, "compact")}${escapeHtml(sourceLabel)}</span></td>
      <td><span class="job-status ${escapeAttr(statusKey)}">${escapeHtml(String(statusLabel))}</span></td>
    `;
    body.appendChild(tr);
  }
  syncBulkCheckboxes();
}

function pipelineStage(job) {
  const status = String(job.status || "new").toLowerCase();
  if (status.includes("interview")) return "interview";
  if (["replied", "response", "responded", "offer", "rejected"].some((value) => status.includes(value))) return "replied";
  if (["applied", "sent", "submitted"].some((value) => status.includes(value))) return "sent";
  if (["saved", "draft", "prepared", "ready"].some((value) => status.includes(value))) return "prepared";
  return null;
}

function renderApplicationPipeline() {
  const root = $("#application-pipeline");
  if (!root) return;
  const grouped = { prepared: [], sent: [], replied: [], interview: [] };
  for (const job of state.jobs) {
    const stage = pipelineStage(job);
    if (stage) grouped[stage].push(job);
  }
  for (const [stage, jobs] of Object.entries(grouped)) {
    const column = root.querySelector(`[data-pipeline-column="${stage}"]`);
    if (!column) continue;
    const count = column.querySelector("h3 span");
    const list = column.querySelector(".pipeline-list");
    count.textContent = String(jobs.length);
    list.replaceChildren();
    for (const job of jobs.slice(0, 8)) {
      const card = document.createElement("article");
      card.className = "pipeline-card";
      card.innerHTML = `
        <div class="pipeline-card-head"><span class="metric-icon blue"><i class="ph ph-briefcase" aria-hidden="true"></i></span><span><strong>${escapeHtml(job.title || "Вакансия")}</strong><small>${escapeHtml(job.company || "Компания не указана")}</small></span></div>
        <div class="meta"><span class="job-source">${sourceLogoMarkup(job.source, "compact")}${escapeHtml(SOURCE_META[String(job.source || "").toLowerCase()]?.label || job.source || "источник")}</span><span>${escapeHtml(job.status || "")}</span></div>
        <button type="button"><i class="ph ph-arrow-right" aria-hidden="true"></i>Открыть вакансию</button>`;
      card.querySelector("button").addEventListener("click", () => {
        window.WorkHunterUI.route.activate(
          resolvedRouteForUrl(`/jobs?filter=all&job=${requirePositiveInteger(job.id, "job id")}`),
          { historyMode: "push" },
        );
      });
      list.append(card);
    }
    if (!jobs.length) {
      const empty = document.createElement("div");
      empty.className = "pipeline-empty";
      empty.textContent = stage === "prepared" ? "Сохраните подходящую вакансию" : "Пока пусто";
      list.append(empty);
    }
  }
  const total = Object.values(grouped).reduce((sum, jobs) => sum + jobs.length, 0);
  $("#pipeline-total").textContent = String(total);
  $("#pipeline-replied").textContent = String(grouped.replied.length);
  $("#pipeline-interviews").textContent = String(grouped.interview.length);
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
  const detailSourceKey = String(job.source || "").toLowerCase();
  const detailSourceLabel = SOURCE_META[detailSourceKey]?.label || job.source || "Источник";
  $("#job-detail").innerHTML = `
    <div class="detail-heading"><span class="metric-icon blue"><i class="ph ph-briefcase" aria-hidden="true"></i></span><div><span class="eyebrow">Выбранная вакансия</span><h2>${escapeHtml(job.title)}</h2><p class="detail-source-line">${escapeHtml(job.company || "Компания не указана")} · <span class="job-source">${sourceLogoMarkup(detailSourceKey, "compact")}${escapeHtml(detailSourceLabel)}</span> · <a href="${escapeAttr(safeExternalUrl(job.url))}" target="_blank" rel="noreferrer">Открыть на площадке <i class="ph ph-arrow-square-out" aria-hidden="true"></i></a></p></div></div>
    <div class="detail-actions" data-guide="vacancy-actions">
      <button data-ui-action="mark-selected" data-status="saved" data-action-id="job.save"><i class="ph ph-bookmark-simple" aria-hidden="true"></i>Сохранить</button>
      <button data-ui-action="mark-selected" data-status="hidden" data-action-id="job.hide"><i class="ph ph-eye-slash" aria-hidden="true"></i>Скрыть</button>
      <button data-ui-action="mark-selected" data-status="applied" data-action-id="job.applied"><i class="ph ph-check-circle" aria-hidden="true"></i>Откликнулся</button>
    </div>
    <details class="detail-disclosure">
      <summary>Подготовить</summary>
      <div class="detail-actions">
        <button data-ui-action="prepare-letter" data-action-id="job.letter.local"><i class="ph ph-envelope-simple" aria-hidden="true"></i>Подготовить письмо</button>
        <button data-ui-action="prepare-letter-ai" data-action-id="job.letter.ai"><i class="ph ph-sparkle" aria-hidden="true"></i>AI-письмо</button>
        <button data-ui-action="fetch-full-description" data-action-id="job.description.fetch"><i class="ph ph-download-simple" aria-hidden="true"></i>Загрузить описание</button>
      </div>
    </details>
    <details class="detail-disclosure">
      <summary>Дополнительно</summary>
      <div class="detail-actions">
        <button data-ui-action="apply-job" data-dry-run="true" data-action-id="job.apply.plan"><i class="ph ph-list-checks" aria-hidden="true"></i>Проверить план</button>
        <button data-ui-action="apply-job" data-dry-run="false" data-action-id="job.apply.live" data-guide="live-hh-action" class="primary"><i class="ph ph-paper-plane-tilt" aria-hidden="true"></i>Отправить отклик</button>
        <button data-ui-action="share-to-telegram" data-action-id="job.telegram.share">Поделиться в Telegram</button>
        <button data-ui-action="smart-classify" data-action-id="job.ai.classify">AI-классификация</button>
        <button data-ui-action="parse-job-structure" data-action-id="job.ai.structure">Структура</button>
        <button data-ui-action="run-gap-analysis" data-action-id="job.ai.gap">Gap-анализ</button>
      </div>
    </details>
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
  window.queueMicrotask(() => scheduleGuidance("vacancies"));
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

async function updateJobStatus(jobId, status) {
  const id = requirePositiveInteger(jobId, "job id");
  await api(`/api/jobs/${id}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
  if (status === "applied") {
    await api(`/api/jobs/${id}/apply`, {
      method: "POST",
      body: JSON.stringify({ status: "applied" }),
    });
  }
  await api(`/api/jobs/${id}/record-event`, {
    method: "POST",
    body: JSON.stringify({ action: status }),
  });
  return id;
}

async function markSelected(status) {
  if (!state.selectedId) return;
  const id = requirePositiveInteger(state.selectedId, "job id");
  await updateJobStatus(id, status);
  await loadJobs();
  await selectJob(id);
}

async function prepareLetter() {
  if (!state.selectedId) return;
  const draft = await api(`/api/jobs/${state.selectedId}/letter`, { method: "POST", body: "{}" });
  setLetterValue(draft.body);
}

async function prepareLetterAi() {
  if (!state.selectedId) return;
  setBusy('[data-ui-action="prepare-letter-ai"]', true);
  try {
    const draft = await api(`/api/jobs/${state.selectedId}/letter-ai`, { method: "POST", body: "{}" });
    setLetterValue(draft.body);
  } catch (err) {
    $("#action-output").textContent = `Ошибка AI: ${err.message}. Проверь API-ключ в настройках.`;
  } finally {
    setBusy('[data-ui-action="prepare-letter-ai"]', false);
  }
}

function buildApplyMutationDescriptor({ jobId, letter, plan, trigger }) {
  const job = state.jobs.find((item) => Number(item.id) === Number(jobId)) || {};
  const sourceLabel = String(job.source || plan.source || "площадку");
  const review = {
    jobId,
    resumeId: plan.resume_id,
    letter,
    vacancy: job.title || `#${jobId}`,
    company: job.company || "Не указана",
  };
  const fingerprint = stableActionFingerprint(review);
  let descriptor;
  descriptor = {
    operationType: "apply",
    title: `Отправить отклик через ${sourceLabel}?`,
    consequence: `${sourceLabel} получит реальный отклик от вашего аккаунта. Отменить его после отправки может быть невозможно.`,
    targetRows: [
      { key: "vacancy", label: "Вакансия", safeValue: review.vacancy },
      { key: "company", label: "Компания", safeValue: review.company },
      { key: "resume", label: "Резюме", safeValue: `#${plan.resume_id}` },
      { key: "letter", label: "Письмо", safeValue: letter ? `${letter.length} символов` : "Без письма" },
    ],
    preview: letter || null,
    riskFlags: [{ code: "external_mutation", safeMessage: `Действие изменит данные внешнего аккаунта ${sourceLabel}.` }],
    acknowledgement: "Я проверил вакансию, резюме и текст письма",
    confirmLabel: "Отправить отклик",
    fingerprint,
    trigger,
    revalidate: async () => validateApplyMutation(descriptor, async () => {
      const currentPlan = await api(`/api/jobs/${jobId}/apply-plan`, {
        method: "POST",
        body: JSON.stringify({ letter }),
      });
      if (currentPlan.status !== "ready") {
        return {
          status: "blocked",
          fingerprint,
          blockers: [{ code: currentPlan.status || "not_ready", safeMessage: currentPlan.message || "Отклик сейчас недоступен." }],
          canExecute: false,
        };
      }
      const updatedDescriptor = buildApplyMutationDescriptor({ jobId, letter, plan: currentPlan, trigger });
      if (updatedDescriptor.fingerprint !== fingerprint) {
        return { status: "changed", fingerprint: updatedDescriptor.fingerprint, updatedDescriptor, canExecute: false };
      }
      return { status: "executable", fingerprint, canExecute: true };
    }),
    execute: async (confirm) => {
      const result = await api(`/api/jobs/${jobId}/confirm-apply`, {
        method: "POST",
        body: JSON.stringify({ confirm: confirm === true, resume_id: plan.resume_id, letter }),
      });
      $("#action-output").textContent = JSON.stringify(result, null, 2);
      if (result?.status === "applied") {
        await loadJobs();
        await selectJob(jobId);
      }
      return result;
    },
  };
  return descriptor;
}

async function applyJob(dryRun = true) {
  if (!state.selectedId) return;
  const jobId = state.selectedId;
  const letter = getLetterValue();
  const plan = await api(`/api/jobs/${jobId}/apply-plan`, {
    method: "POST",
    body: JSON.stringify({ letter }),
  });
  $("#action-output").textContent = JSON.stringify(plan, null, 2);
  if (dryRun || plan.status !== "ready") return;

  const descriptor = buildApplyMutationDescriptor({
    jobId,
    letter,
    plan,
    trigger: document.activeElement,
  });
  openLiveAction(descriptor);
}

async function loadProfile() {
  state.profile = await api("/api/profile");
  renderProfileSwitcher();
  renderProfileForm();
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
  $("#profile-contact-name").value = d.name || "";
  $("#profile-email").value = d.email || "";
  $("#profile-phone").value = d.phone || "";
  $("#profile-city").value = d.city || "";
  $("#profile-linkedin").value = d.linkedin_url || "";
  $("#profile-portfolio").value = d.portfolio_url || "";
  $("#profile-resume-path").value = d.resume_path || "";
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
    notifyError("profile-switch", err, "Не удалось переключить профиль");
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
    name: $("#profile-contact-name").value.trim(),
    email: $("#profile-email").value.trim(),
    phone: $("#profile-phone").value.trim(),
    city: $("#profile-city").value.trim(),
    linkedin_url: $("#profile-linkedin").value.trim(),
    portfolio_url: $("#profile-portfolio").value.trim(),
    resume_path: $("#profile-resume-path").value.trim(),
  };
  try {
    const updated = await api("/api/profile", { method: "POST", body: JSON.stringify(data) });
    state.profile.data = updated;
    $("#config-editor").value = JSON.stringify(state.config, null, 2);
    $("#summary-line").textContent = "Профиль сохранён. Синхронизируй источники и пересчитай score для обновления.";
  } catch (err) {
    notifyError("profile-save", err, "Не удалось сохранить профиль");
  }
}

async function loadConfig() {
  state.config = await api("/api/config");
  const legacyBroadKey = ["allow", "broad", "apply"].join("_");
  if (state.config?.sources?.hh) delete state.config.sources.hh[legacyBroadKey];
  for (const account of state.config?.sources?.hh?.autopilot?.accounts || []) {
    delete account.enabled;
    delete account.authorization_generation;
  }
  $("#config-editor").value = JSON.stringify(state.config, null, 2);

  if (state.config && state.config.sources && state.config.sources.hh) {
    $("#hh-token-input").value = state.config.sources.hh.access_token || "";
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
    if ($("#ai-provider-label")) $("#ai-provider-label").textContent = state.config.ai.backend === "opencode" ? "OpenCode" : "OpenAI-совместимый";
    if ($("#ai-model-label")) $("#ai-model-label").textContent = state.config.ai.model || state.config.ai.opencode_model || "Не выбрана";
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
  if (!state.config) return;

  if (!state.config.sources) state.config.sources = {};
  if (!state.config.sources.hh) state.config.sources.hh = {};

  state.config.sources.hh.access_token = token;

  state.config = await api("/api/config", { method: "POST", body: JSON.stringify(state.config) });
  $("#config-editor").value = JSON.stringify(state.config, null, 2);
  notify("success", "settings-hh", "saved", "Настройки HH сохранены");
}

const HH_AUTOPILOT_COMMON_FIELDS = [
  ["#hh-autopilot-timezone", "timezone", "text"],
  ["#hh-autopilot-schedule-start", "schedule.start", "text"],
  ["#hh-autopilot-schedule-end", "schedule.end", "text"],
  ["#hh-autopilot-interval", "schedule.interval_minutes", "number"],
  ["#hh-autopilot-max-pages", "search.max_pages", "number"],
  ["#hh-autopilot-max-results", "search.max_results_per_run", "number"],
  ["#hh-autopilot-required-keywords", "filters.required_keywords", "list"],
  ["#hh-autopilot-excluded-keywords", "filters.excluded_keywords", "list"],
  ["#hh-autopilot-remote", "filters.remote", "text"],
  ["#hh-autopilot-minimum-salary", "filters.minimum_salary", "number"],
  ["#hh-autopilot-minimum-score", "ranking.minimum_score", "number"],
  ["#hh-autopilot-ai-mode", "ranking.ai_mode", "text"],
  ["#hh-autopilot-daily-limit", "limits.daily_success", "number"],
  ["#hh-autopilot-run-limit", "limits.per_run_success", "number"],
  ["#hh-autopilot-delay-min", "limits.send_delay_min_seconds", "number"],
  ["#hh-autopilot-delay-max", "limits.send_delay_max_seconds", "number"],
];

function hhAutopilotAccount() {
  const value = $("#hh-autopilot-account")?.value || "";
  return value.startsWith("__") ? null : value;
}

function hhAutopilotQuery(extra = {}) {
  const params = new URLSearchParams(extra);
  const account = hhAutopilotAccount();
  if (account) params.set("account", account);
  const query = params.toString();
  return query ? `?${query}` : "";
}

function hhAutopilotScope(action) {
  const selected = $("#hh-autopilot-account")?.value || "";
  if (selected === "__all__") return { all: true };
  if (selected === "__global__") {
    if (!["kill-switch", "clear-kill-switch", "recover-now"].includes(action)) {
      throw new Error("Глобальный scope доступен только для kill/recovery");
    }
    return action === "recover-now" ? {} : { global: true };
  }
  if (!selected) throw new Error("Выберите HH-аккаунт");
  return { account: selected };
}

function getAutopilotValue(config, path) {
  return path.split(".").reduce((value, key) => value?.[key], config);
}

function setAutopilotValue(config, path, value) {
  const parts = path.split(".");
  let target = config;
  parts.slice(0, -1).forEach((key) => {
    if (!target[key] || typeof target[key] !== "object") target[key] = {};
    target = target[key];
  });
  target[parts.at(-1)] = value;
}

function humanList(value, fallback) {
  return Array.isArray(value) && value.length ? value.join(", ") : fallback;
}

function renderHumanAutopilotSummary(config) {
  const required = getAutopilotValue(config, "filters.required_keywords");
  const excluded = getAutopilotValue(config, "filters.excluded_keywords");
  const remote = getAutopilotValue(config, "filters.remote");
  const salary = Number(getAutopilotValue(config, "filters.minimum_salary") || 0);
  const start = getAutopilotValue(config, "schedule.start") || "08:00";
  const end = getAutopilotValue(config, "schedule.end") || "21:00";
  const daily = Number(getAutopilotValue(config, "limits.daily_success") || 0);
  const remoteLabel = remote === "only" ? "Только удалённо" : (remote === "exclude" ? "Без удалёнки" : "Любой");
  const values = {
    "#hh-rule-keywords": humanList(required, "По правилам профиля"),
    "#hh-rule-remote": remoteLabel,
    "#hh-rule-salary": salary > 0 ? `От ${salary.toLocaleString("ru-RU")} ₽` : "Без минимума",
    "#hh-rule-excluded": humanList(excluded, "Не задано"),
    "#hh-rule-schedule": `${start}–${end}`,
    "#hh-rule-limit": daily > 0 ? `${daily} откликов в день` : "По лимиту площадки",
  };
  for (const [selectorName, text] of Object.entries(values)) {
    const element = $(selectorName);
    if (element) element.textContent = text;
  }
}

function syncHumanAccountSelector() {
  const source = $("#hh-autopilot-account");
  const target = $("#hh-human-account");
  if (!source || !target) return;
  const previous = target.value;
  const accountOptions = [...source.options].filter((option) => !option.value.startsWith("__"));
  target.replaceChildren(...accountOptions.map((option) => new Option(option.textContent, option.value)));
  if (!target.options.length) target.add(new Option("default", "default"));
  target.value = [...target.options].some((option) => option.value === previous)
    ? previous
    : target.options[0].value;
}

function renderHhAutopilotConfig(payload) {
  state.hhAutopilot.config = structuredClone(payload.config || {});
  state.hhAutopilot.managed = payload.managed?.accounts || [];
  const selector = $("#hh-autopilot-account");
  const previous = selector.value;
  selector.replaceChildren();
  for (const item of state.hhAutopilot.managed) {
    selector.add(new Option(item.profile_id, item.profile_id));
  }
  selector.add(new Option("Все аккаунты", "__all__"));
  selector.add(new Option("Global control", "__global__"));
  selector.value = [...selector.options].some((option) => option.value === previous)
    ? previous
    : (state.hhAutopilot.managed[0]?.profile_id || "__all__");
  syncHumanAccountSelector();
  $("#hh-autopilot-config").value = JSON.stringify(state.hhAutopilot.config, null, 2);
  for (const [selectorName, path, type] of HH_AUTOPILOT_COMMON_FIELDS) {
    const input = $(selectorName);
    const value = getAutopilotValue(state.hhAutopilot.config, path);
    input.value = type === "list" ? (value || []).join(", ") : (value ?? "");
  }
  const migration = payload.migration || {};
  const migrationNote = $("#hh-autopilot-migration-note");
  migrationNote.hidden = !migration.legacy_broad_apply_present;
  migrationNote.textContent = migration.message || "";
  renderHumanAutopilotSummary(state.hhAutopilot.config);
  syncHumanSearchRuleControls(state.hhAutopilot.config);
  syncHhAutopilotScopeBadge();
}

function syncHhAutopilotScopeBadge() {
  const selected = $("#hh-autopilot-account")?.value || "";
  $("#hh-autopilot-scope-badge").textContent = selected === "__all__"
    ? "all accounts"
    : (selected === "__global__" ? "global" : "account");
}

function hhAutopilotBadge(label, value, tone = "") {
  const badge = document.createElement("span");
  badge.className = `agent-badge ${tone}`.trim();
  badge.textContent = `${label}: ${value}`;
  return badge;
}

function renderHhAutopilotStatus(payload) {
  state.hhAutopilot.status = payload;
  const target = $("#hh-autopilot-status");
  target.replaceChildren();
  const statuses = payload.accounts
    ? Object.entries(payload.accounts)
    : [[payload.account || hhAutopilotAccount() || "all", payload]];
  for (const [account, status] of statuses) {
    const row = document.createElement("div");
    row.className = "hh-autopilot-status-row";
    const controls = status.controls || {};
    const quota = status.quota || {};
    row.append(
      hhAutopilotBadge("account", account),
      hhAutopilotBadge("enabled", controls.enabled === true ? "yes" : "no", controls.enabled ? "ok" : "warning"),
      hhAutopilotBadge("grant", status.grant ? "active" : "none", status.grant ? "ok" : "warning"),
      hhAutopilotBadge("policy", status.policy_match === true ? "match" : "reauthorize", status.policy_match ? "ok" : "warning"),
      hhAutopilotBadge("paused", controls.paused === true ? "yes" : "no", controls.paused ? "warning" : "ok"),
      hhAutopilotBadge("kill", controls.kill_switch === true ? "on" : "off", controls.kill_switch ? "error" : "ok"),
      hhAutopilotBadge("quota", `${quota.used || 0}/${quota.daily_limit || "—"}`),
      hhAutopilotBadge("next", status.schedule?.next_run || "—"),
      hhAutopilotBadge("lease", status.lease?.expires_at || "free"),
    );
    target.append(row);
  }
  renderHumanAutopilotStatus();
}

function hhIsConnected() {
  const status = state.hhWebStatus || {};
  return status.authorized === true || status.can_search_url === true || status.can_chatik === true;
}

function selectedHumanAutopilotStatus() {
  const payload = state.hhAutopilot.status || {};
  const account = $("#hh-human-account")?.value || hhAutopilotAccount() || "default";
  return payload.accounts?.[account] || payload;
}

function renderHumanAutopilotStatus() {
  const connected = hhIsConnected();
  const status = selectedHumanAutopilotStatus();
  const controls = status.controls || {};
  const quota = status.quota || {};
  const authState = $("#hh-human-auth-status");
  const authNote = $("#hh-human-auth-note");
  const readyTitle = $("#hh-human-ready-title");
  const readyNote = $("#hh-human-ready-note");
  const startButton = $("#hh-human-start");
  const sourceState = $("#source-hh-state");
  if (authState) {
    authState.textContent = connected ? "Подключено" : "Нужно войти";
    authState.classList.toggle("warning", !connected);
  }
  if (sourceState) {
    sourceState.textContent = connected ? "Подключено" : "Нужно войти";
    sourceState.classList.toggle("warning", !connected);
  }
  if (authNote) {
    authNote.textContent = connected
      ? "Аккаунт готов. Можно выбрать резюме и запускать поиск."
      : "Пароль вводится в отдельном окне HH и не сохраняется в Work Hunter.";
  }
  if (!readyTitle || !readyNote || !startButton) return;
  if (!connected) {
    readyTitle.textContent = "Сначала войдите в HH";
    readyNote.textContent = "После входа проверим резюме, правила и доступный лимит.";
    startButton.textContent = "Войти и продолжить";
    return;
  }
  const used = Number(quota.used || 0);
  const limit = Number(quota.daily_limit || getAutopilotValue(state.hhAutopilot.config || {}, "limits.daily_success") || 0);
  if (controls.paused) {
    readyTitle.textContent = "Автоотклики на паузе";
    readyNote.textContent = "Продолжите работу с текущими правилами и лимитами.";
    startButton.textContent = "Продолжить";
  } else if (controls.enabled) {
    readyTitle.textContent = "Автоотклики включены";
    readyNote.textContent = limit > 0 ? `Сегодня отправлено ${used} из ${limit}.` : "Можно запустить поиск прямо сейчас.";
    startButton.textContent = "Запустить сейчас";
  } else {
    readyTitle.textContent = "Всё готово к запуску";
    readyNote.textContent = "Перед первым запуском покажем точный аккаунт и лимит.";
    startButton.textContent = "Запустить автоотклики";
  }
}

async function loadHhConnectionStatus() {
  try {
    const [auth, web] = await Promise.all([
      api("/api/hh/auth/status"),
      api("/api/hh/web/status"),
    ]);
    state.hhWebStatus = { ...web, authorized: auth.authorized === true, apiStatus: auth.status };
  } catch (_error) {
    state.hhWebStatus = { authorized: false };
  }
  renderHumanAutopilotStatus();
  return state.hhWebStatus;
}

function appendAutopilotRow(target, title, detail, actions = []) {
  const row = document.createElement("article");
  row.className = "agent-row";
  const head = document.createElement("div");
  head.className = "agent-row-head";
  const strong = document.createElement("strong");
  strong.textContent = title;
  head.append(strong);
  const body = document.createElement("div");
  body.className = "meta";
  body.textContent = detail;
  row.append(head, body);
  if (actions.length) {
    const actionRow = document.createElement("div");
    actionRow.className = "agent-row-actions";
    for (const action of actions) actionRow.append(action);
    row.append(actionRow);
  }
  target.append(row);
}

function renderHhAutopilotQueue(payload) {
  const target = $("#hh-autopilot-queue");
  target.replaceChildren();
  const statuses = payload.accounts ? Object.entries(payload.accounts) : [[payload.account, payload]];
  for (const [account, status] of statuses) {
    const queue = status.queue || {};
    appendAutopilotRow(
      target,
      account || "Очередь",
      `pending ${queue.pending || 0} · retry ${queue.retry || 0} · reconciling ${queue.reconciling || 0} · manual ${queue.manual || 0} · dead ${queue.dead || 0}`,
    );
    for (const run of status.runs || []) {
      appendAutopilotRow(target, `Run #${run.id}: ${run.status}`, `${run.trigger} · ${run.started_at || ""} · ${run.error || "без ошибки"}`);
    }
  }
  if (!target.children.length) appendAutopilotRow(target, "Очередь пуста", "Нет активных элементов");
}

function flattenAutopilotPayload(payload, key) {
  if (!payload.accounts) return payload[key] || [];
  return Object.entries(payload.accounts).flatMap(([account, value]) =>
    (value[key] || []).map((item) => ({ ...item, account: item.account || account })),
  );
}

function challengeButton(label, challenge, action) {
  const button = document.createElement("button");
  button.textContent = label;
  button.addEventListener("click", () => resolveHhAutopilotChallenge(challenge, action, button));
  return button;
}

function renderHhAutopilotChallenges(payload) {
  const target = $("#hh-autopilot-challenges");
  target.replaceChildren();
  for (const challenge of flattenAutopilotPayload(payload, "challenges")) {
    const actions = [];
    const type = String(challenge.challenge_type || challenge.type || "manual");
    if (challenge.sanitized_url) {
      const link = document.createElement("a");
      link.href = challenge.sanitized_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = type.includes("captcha") ? "Открыть CAPTCHA" : "Открыть handoff";
      actions.push(link);
    }
    if (type.includes("ambigu")) {
      actions.push(
        challengeButton("Уже применён", challenge, "confirmed_applied"),
        challengeButton("Не применён — retry", challenge, "confirmed_not_applied_retry"),
        challengeButton("Не применён — skip", challenge, "confirmed_not_applied_skip"),
        challengeButton("Проверить ещё", challenge, "retry_reconciliation"),
      );
    } else {
      actions.push(
        challengeButton(type.includes("auth") ? "Авторизация восстановлена" : "Я завершил", challenge, type.includes("auth") ? "auth_restored" : "completed"),
        challengeButton("Закрыть", challenge, "dismissed"),
      );
    }
    appendAutopilotRow(
      target,
      `#${challenge.id} · ${type}`,
      `${challenge.account || challenge.account_profile_id || ""} · до ${challenge.expires_at || "—"}${type.includes("ambigu") ? " · квота удерживается до решения" : ""}`,
      actions,
    );
  }
  if (!target.children.length) appendAutopilotRow(target, "Нет challenges", "Ручное действие не требуется");
}

function renderHhAutopilotHistory(payload, { append = false } = {}) {
  const target = $("#hh-autopilot-history");
  if (!append) target.replaceChildren();
  for (const event of flattenAutopilotPayload(payload, "events")) {
    const actions = [];
    if (Number.isInteger(event.item_id) && event.item_id > 0 && event.account) {
      const retry = document.createElement("button");
      retry.textContent = "Retry item";
      retry.addEventListener("click", () => runHhAutopilotMutation("retry", { account: event.account, item_id: event.item_id }, retry));
      actions.push(retry);
    }
    appendAutopilotRow(
      target,
      `${event.event_type || event.type || event.action || "event"} · ${event.vacancy_id || ""}`,
      `${event.account || ""} · ${event.created_at || event.at || ""} · ${event.reason || ""}`,
      actions,
    );
  }
  if (!target.children.length) appendAutopilotRow(target, "Журнал пуст", "Событий пока нет");
  const nextOffset = payload.next_offset;
  $("#hh-autopilot-history-more").hidden = !Number.isInteger(nextOffset);
  state.hhAutopilot.historyOffset = Number.isInteger(nextOffset) ? nextOffset : 0;
}

async function loadHhAutopilot() {
  await loadHhConnectionStatus();
  const configPayload = await api(`/api/hh/autopilot/config${hhAutopilotQuery()}`);
  renderHhAutopilotConfig(configPayload);
  await loadHhAutopilotData();
}

async function loadHhAutopilotData() {
  syncHhAutopilotScopeBadge();
  const query = hhAutopilotQuery();
  const [status, challenges, history] = await Promise.all([
    api(`/api/hh/autopilot/status${query}`),
    api(`/api/hh/autopilot/challenges${query}`),
    api(`/api/hh/autopilot/history${query}`),
  ]);
  renderHhAutopilotStatus(status);
  renderHhAutopilotQueue(status);
  renderHhAutopilotChallenges(challenges);
  renderHhAutopilotHistory(history);
}

async function loadMoreHhAutopilotHistory() {
  const offset = state.hhAutopilot.historyOffset;
  if (!offset) return;
  const payload = await api(`/api/hh/autopilot/history${hhAutopilotQuery({ offset })}`);
  renderHhAutopilotHistory(payload, { append: true });
}

async function saveHhAutopilotConfig() {
  const config = JSON.parse($("#hh-autopilot-config").value);
  if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error("Config должен быть JSON object");
  for (const [selectorName, path, type] of HH_AUTOPILOT_COMMON_FIELDS) {
    const raw = $(selectorName).value.trim();
    let value = raw;
    if (type === "number") {
      value = Number(raw);
      if (!Number.isFinite(value)) throw new Error(`${path}: нужно число`);
    } else if (type === "list") {
      value = raw.split(",").map((item) => item.trim()).filter(Boolean);
    }
    setAutopilotValue(config, path, value);
  }
  for (const account of config.accounts || []) {
    delete account.enabled;
    delete account.authorization_generation;
  }
  const body = { config };
  const account = hhAutopilotAccount();
  if (account) body.account = account;
  const result = await api("/api/hh/autopilot/config", { method: "POST", body: JSON.stringify(body) });
  renderHhAutopilotConfig(result);
  $("#hh-autopilot-policy-note").textContent = result.reauthorization_required
    ? "Политика изменилась: включите Autopilot снова."
    : "Настройки сохранены, текущая авторизация подходит.";
  await loadHhAutopilotData();
}

async function runHhAutopilotMutation(action, extra = {}, trigger = null, confirmed = false) {
  const scope = extra.account ? {} : hhAutopilotScope(action);
  const payload = { ...scope, ...extra };
  if (confirmed) payload.confirm = true;
  if (trigger) trigger.disabled = true;
  try {
    const result = await api(`/api/hh/autopilot/${action}`, { method: "POST", body: JSON.stringify(payload) });
    notify("success", "hh-autopilot", action, `HH Autopilot: ${action}`);
    await loadHhAutopilot();
    return result;
  } finally {
    if (trigger) trigger.disabled = false;
  }
}

function confirmHhAutopilotMutation(action, title, extra, trigger) {
  let scope;
  try {
    scope = hhAutopilotScope(action);
  } catch (error) {
    notifyError("hh-autopilot-scope", error);
    return;
  }
  const reviewed = { action, scope, ...extra };
  const fingerprint = stableActionFingerprint(reviewed);
  const descriptor = {
    operationType: "resume_account",
    title,
    consequence: "Команда изменит автономный режим и может привести к реальным действиям в HH.",
    targetRows: [
      { key: "resume_or_account", label: "Scope", safeValue: scope.account || (scope.all ? "all accounts" : "global") },
      { key: "changes", label: "Команда", safeValue: action },
    ],
    riskFlags: [{ code: "external_mutation", safeMessage: "Проверьте scope, лимиты и точную цель." }],
    acknowledgement: "Я проверил аккаунт, параметры и понимаю последствия",
    confirmLabel: "Подтвердить",
    fingerprint,
    trigger,
    revalidate: async () => ({ status: "executable", fingerprint, canExecute: true }),
    execute: async (confirm) => runHhAutopilotMutation(action, extra, trigger, confirm === true),
  };
  openLiveAction(descriptor);
}

async function resolveHhAutopilotChallenge(challenge, action, trigger) {
  const account = challenge.account || challenge.account_profile_id || hhAutopilotAccount();
  if (!account) throw new Error("Для challenge нужен точный account");
  await runHhAutopilotMutation("resolve-challenge", { account, challenge_id: Number(challenge.id), action }, trigger);
}

async function validateHhAutopilot() {
  const result = await api(`/api/hh/autopilot/validate${hhAutopilotQuery()}`);
  $("#hh-autopilot-policy-note").textContent = result.valid ? `Конфигурация валидна: ${result.accounts.join(", ")}` : "Конфигурация невалидна";
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
  if ($("#ai-provider-label")) $("#ai-provider-label").textContent = state.config.ai.backend === "opencode" ? "OpenCode" : "OpenAI-совместимый";
  if ($("#ai-model-label")) $("#ai-model-label").textContent = state.config.ai.model || state.config.ai.opencode_model || "Не выбрана";
  notify("success", "settings-ai", "saved", "Настройки AI сохранены");
}

const SOURCE_META = Object.freeze({
  hh: { label: "HeadHunter", domain: "hh.ru", url: "https://hh.ru/account/login", icon: "/vendor/brands/hh.png" },
  linkedin: { label: "LinkedIn", domain: "linkedin.com", url: "https://www.linkedin.com/login", icon: "/vendor/brands/linkedin.png" },
  habr: { label: "Хабр Карьера", domain: "career.habr.com", url: "https://career.habr.com/login", icon: "/vendor/brands/habr.png" },
  geekjob: { label: "GeekJob", domain: "geekjob.ru", url: "https://geekjob.ru/login", icon: "/vendor/brands/geekjob.png" },
  getmatch: { label: "Getmatch", domain: "getmatch.ru", url: "https://getmatch.ru/auth/signin", icon: "/vendor/brands/getmatch.png" },
  relocate_me: { label: "Relocate.me", domain: "relocate.me", url: "https://relocate.me", icon: "/vendor/brands/relocate_me.png" },
  rvc: { label: "RVC", domain: "app.rvc.global", url: "https://app.rvc.global/auth/sign-in", icon: "/vendor/brands/rvc.jpg" },
  hirehi: { label: "HireHi", domain: "hirehi.ru", url: "https://hirehi.ru/login", icon: "/vendor/brands/hirehi.png" },
  careerspace: { label: "CareerSpace", domain: "careerspace.app", url: "https://careerspace.app/login", icon: "/vendor/brands/careerspace.png" },
  another_it: { label: "Another-IT", domain: "another-it.ru", url: "https://another-it.ru/login", icon: "/vendor/brands/another_it.png" },
  jabka: { label: "Жабка", domain: "jabka.work", url: "https://jabka.work", icon: "/vendor/brands/jabka.png" },
  indeed: { label: "Indeed", domain: "indeed.com", url: "https://secure.indeed.com/auth", icon: "/vendor/brands/indeed.png" },
  telegram: { label: "Telegram-каналы", domain: "telegram.org", url: "https://web.telegram.org", icon: "/vendor/brands/telegram.png" },
});

function sourceLogoMarkup(sourceName, className = "") {
  const meta = SOURCE_META[String(sourceName || "").toLowerCase()];
  const classes = `source-logo ${className}`.trim();
  if (meta?.icon) return `<img class="${escapeAttr(classes)}" src="${escapeAttr(meta.icon)}" alt="">`;
  return `<span class="${escapeAttr(classes)} source-logo-fallback" aria-hidden="true"><i class="ph ph-globe"></i></span>`;
}

function sourceSyncLabel(source) {
  if (!source?.last_sync_at) return "Ещё не синхронизировано";
  const date = new Date(source.last_sync_at);
  return Number.isNaN(date.getTime())
    ? "Синхронизировано"
    : `Обновлено ${date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}`;
}

function selectHumanAccount(account) {
  const technical = $("#hh-autopilot-account");
  if (!technical) return;
  const next = account || "default";
  if ([...technical.options].some((option) => option.value === next)) technical.value = next;
  syncHhAutopilotScopeBadge();
}

function setWorkMode(mode) {
  state.workMode = mode === "search" ? "search" : "apply";
  document.querySelectorAll("[data-work-mode], [data-hh-mode]").forEach((button) => {
    const buttonMode = button.dataset.workMode || button.dataset.hhMode;
    const active = buttonMode === state.workMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", String(active));
  });
  const start = $("#work-mode-start");
  if (start) {
    start.textContent = $("#work-mode-hero")?.dataset.readiness === "ready"
      ? (state.workMode === "search" ? "Найти вакансии" : "Запустить автоотклики")
      : "Завершить настройку";
  }
}

function confirmHumanAutopilotStart(trigger) {
  const account = $("#hh-human-account")?.value || "default";
  selectHumanAccount(account);
  const status = selectedHumanAutopilotStatus();
  const config = state.hhAutopilot.config || {};
  const daily = getAutopilotValue(config, "limits.daily_success") || "по настройкам";
  const fingerprint = stableActionFingerprint({ action: "enable-and-run", account, daily });
  openLiveAction({
    operationType: "resume_account",
    title: "Запустить автоотклики?",
    consequence: "Work Hunter включит автопилот и начнёт отправлять реальные отклики по вашим правилам.",
    targetRows: [
      { key: "resume_or_account", label: "Аккаунт", safeValue: account },
      { key: "changes", label: "Дневной лимит", safeValue: String(daily) },
    ],
    riskFlags: [{ code: "external_mutation", safeMessage: "Отклики будут отправлены работодателям от вашего аккаунта." }],
    acknowledgement: "Я проверил аккаунт, резюме и лимит откликов",
    confirmLabel: "Запустить",
    fingerprint,
    trigger,
    revalidate: async () => ({ status: "executable", fingerprint, canExecute: true }),
    execute: async () => {
      if (!status.controls?.enabled) await runHhAutopilotMutation("enable", {}, trigger, true);
      return runHhAutopilotMutation("run-now", {}, trigger);
    },
  });
}

async function handleHumanAutopilotStart(eventOrTrigger) {
  const trigger = eventOrTrigger?.currentTarget || eventOrTrigger;
  const account = $("#hh-human-account")?.value || "default";
  selectHumanAccount(account);
  if (!hhIsConnected()) {
    await startHhLogin({ account, trigger });
    return;
  }
  const status = selectedHumanAutopilotStatus();
  if (status.controls?.paused) {
    await runHhAutopilotMutation("resume", {}, trigger);
    await runHhAutopilotMutation("run-now", {}, trigger);
    return;
  }
  if (status.controls?.enabled) {
    await runHhAutopilotMutation("run-now", {}, trigger);
    return;
  }
  confirmHumanAutopilotStart(trigger);
}

async function handlePrimaryWorkMode() {
  if ($("#work-mode-hero")?.dataset.readiness !== "ready") {
    activateView("settings");
    switchSettingsSection("profile");
    return;
  }
  if (state.workMode === "search") {
    await syncJobs();
    activateView("inbox");
    return;
  }
  if (hhIsConnected()) {
    await handleHumanAutopilotStart($("#work-mode-start"));
  } else {
    activateView("settings");
    switchSettingsSection("hh");
    $("#hh-login-button")?.focus();
  }
}

function promptForHhAccount(trigger) {
  const entered = window.prompt("Название аккаунта HH", "work");
  const account = String(entered || "").trim();
  if (!account) return;
  startHhLogin({ account, trigger }).catch((error) => notifyError("hh-login", error));
}

function openSourceLogin(sourceName, url) {
  if (sourceName === "hh") {
    activateView("settings");
    switchSettingsSection("hh");
    return;
  }
  const safeUrl = safeExternalUrl(url);
  if (safeUrl === "#") return;
  window.open(safeUrl, "_blank", "noopener,noreferrer");
}

async function startSourceBrowserLogin(sourceName, url, trigger) {
  if (sourceName === "hh") {
    activateView("settings");
    switchSettingsSection("hh");
    return;
  }
  const meta = SOURCE_META[sourceName] || { label: sourceName };
  setBusy(trigger, true, "Открываю вход...");
  try {
    await api("/api/sources/browser-login", {
      method: "POST",
      body: JSON.stringify({ source: sourceName, url }),
    });
    notify(
      "success",
      `source-login-${sourceName}`,
      "started",
      `Открываем ${meta.label}`,
      "Войдите в отдельном окне. Work Hunter сохранит эту браузерную сессию для откликов.",
    );
  } finally {
    setBusy(trigger, false);
  }
}

function renderSourceCapabilities(sources, capabilities) {
  const box = $("#sources-list");
  if (!box) return;
  const syncByName = Object.fromEntries(sources.map((source) => [source.source, source]));
  const entries = Object.entries(capabilities).sort(([left], [right]) => {
    const preferred = ["hh", "linkedin", "habr", "getmatch", "geekjob", "careerspace", "indeed", "relocate_me", "hirehi", "another_it", "jabka", "telegram"];
    const leftIndex = preferred.indexOf(left);
    const rightIndex = preferred.indexOf(right);
    return (leftIndex < 0 ? 999 : leftIndex) - (rightIndex < 0 ? 999 : rightIndex);
  });
  box.replaceChildren();
  if (!sources.length) {
    const emptyNote = document.createElement("p");
    emptyNote.className = "meta source-empty-note";
    emptyNote.textContent = "Источники еще не синхронизировались";
    box.append(emptyNote);
  }
  for (const [sourceName, capability] of entries) {
    const meta = SOURCE_META[sourceName] || { label: sourceName, domain: sourceName, url: "" };
    const row = document.createElement("article");
    row.className = "source-capability-row";
    const searchAvailable = !["", "none"].includes(String(capability.search || ""));
    const applyKind = String(capability.apply || "none");
    const canApply = !["", "none"].includes(applyKind);
    const usesBrowser = ["browser", "session_or_browser"].includes(applyKind);
    const requiresAuth = Boolean(capability.requires_auth);
    const badges = [
      searchAvailable ? '<span class="capability-badge">Поиск</span>' : "",
      canApply ? `<span class="capability-badge apply">${applyKind === "external_contact" ? "Связаться" : "Автоотклик"}</span>` : "",
      usesBrowser ? '<span class="capability-badge browser">Через браузер</span>' : "",
      requiresAuth ? '<span class="capability-badge auth"><i class="ph ph-lock-key" aria-hidden="true"></i>Нужен вход</span>' : "",
    ].join("");
    const detail = applyKind === "official_api"
      ? "Поиск, отклики и ответы работодателям"
      : (usesBrowser
        ? (requiresAuth ? "Автоотклик использует сохранённую браузерную сессию" : "Поиск автоматический; отклик откроется в браузере")
        : "Вакансии загружаются в общую ленту");
    const actionType = sourceName === "hh" ? "manage" : (requiresAuth ? "login" : "open");
    const actionLabel = sourceName === "hh"
      ? "Аккаунт HH"
      : (requiresAuth ? `Войти в ${meta.label}` : (applyKind === "external_contact" ? "Открыть канал" : "Открыть отклик"));
    const actionIcon = requiresAuth ? "ph-sign-in" : (sourceName === "hh" ? "ph-user-circle" : "ph-arrow-square-out");
    row.innerHTML = `
      <div class="source-capability-name">${sourceLogoMarkup(sourceName)}<span><strong>${escapeHtml(meta.label)}</strong><small>${escapeHtml(meta.domain)}</small></span></div>
      <div class="source-capability-badges">${badges}</div>
      <div class="source-capability-description">${escapeHtml(detail)}<br><span class="meta">${escapeHtml(sourceSyncLabel(syncByName[sourceName]))}</span></div>
      <button type="button" class="source-action ${requiresAuth ? "login" : ""}" data-source-name="${escapeAttr(sourceName)}" data-source-action="${escapeAttr(actionType)}" data-source-url="${escapeAttr(meta.url)}"><i class="ph ${escapeAttr(actionIcon)}" aria-hidden="true"></i>${escapeHtml(actionLabel)}</button>`;
    box.append(row);
  }
  const enabled = entries.filter(([, capability]) => capability.enabled).length;
  const summary = $("#sources-summary");
  if (summary) summary.textContent = `${enabled} площадок включено`;
  const searchCount = entries.filter(([, capability]) => !["", "none"].includes(String(capability.search || ""))).length;
  const applyCount = entries.filter(([, capability]) => !["", "none"].includes(String(capability.apply || "none"))).length;
  const authCount = entries.filter(([, capability]) => capability.requires_auth).length;
  const connectedCount = sources.filter((source) => source.last_sync_at && !source.last_error).length;
  if ($("#sources-search-count")) $("#sources-search-count").textContent = String(searchCount);
  if ($("#sources-apply-count")) $("#sources-apply-count").textContent = String(applyCount);
  if ($("#sources-auth-count")) $("#sources-auth-count").textContent = String(authCount);
  if ($("#sources-connected-count")) $("#sources-connected-count").textContent = String(connectedCount);
  if ($("#search-source-count")) $("#search-source-count").textContent = String(searchCount);
}

async function loadSources() {
  const [sources, capabilities] = await Promise.all([
    api("/api/sources"),
    api("/api/source-capabilities"),
    loadHhConnectionStatus(),
  ]);
  state.sourceCapabilities = capabilities;
  state.sourceKeys = [...new Set(sources.map((source) => String(source.source || "")).filter(Boolean))];
  const sourceFilter = $("#source-filter");
  for (const sourceKey of state.sourceKeys) {
    if (![...sourceFilter.options].some((option) => option.value === sourceKey)) {
      sourceFilter.add(new Option(sourceKey, sourceKey));
    }
  }
  const resolved = window.WorkHunterUI.route.syncFromLocation(state.sourceKeys);
  if (resolved.warnings.includes("invalid_source")) {
    state.routeNeedsReload = true;
    notify("warning", "route-source", "invalid-source", "Источник из ссылки больше недоступен");
  }
  renderSourceCapabilities(sources, capabilities);
}

async function todayResource(path) {
  try {
    return {
      status: "ready",
      data: await api(path),
      stale: false,
      updatedAt: new Date().toISOString(),
    };
  } catch (error) {
    return {
      status: "error",
      error: { message: String(error?.message || error) },
      stale: false,
    };
  }
}

function navigateFromToday(view, params = {}) {
  const route = ROUTES[view] || ROUTES.today;
  const target = new URL(route.url, window.location.origin);
  if (view === "agent" && params.approval) target.searchParams.set("tab", "agent");
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      target.searchParams.set(key, String(value));
    }
  }
  window.WorkHunterUI.route.activate(
    window.WorkHunterUI.route.resolve(
      target.pathname,
      target.search,
      target.hash,
      state.sourceKeys,
    ),
    { historyMode: "push" },
  );
}

async function loadToday() {
  if (!$("#view-today")) return;
  const onboardingSnapshot = window.appOnboarding?.snapshot?.();
  const initialResources = {
    readiness: onboardingSnapshot?.readiness || "loading",
    readinessMissing: onboardingSnapshot?.missingStep ? [onboardingSnapshot.missingStep] : [],
    jobs: { status: "loading", stale: false },
    tasks: { status: "loading", stale: false },
    events: { status: "loading", stale: false },
    approvals: { status: "loading", stale: false },
    sources: { status: "loading", stale: false },
  };
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  window.WorkHunterUI.today.render(
    window.WorkHunterUI.today.compose({
      resources: initialResources,
      now: new Date().toISOString(),
      timeZone,
    }),
    { navigate: navigateFromToday },
  );

  const [jobs, tasks, events, approvals, sources] = await Promise.all([
    todayResource("/api/jobs"),
    todayResource("/api/agent/tasks?status=open&limit=100"),
    todayResource("/api/events"),
    todayResource("/api/approvals"),
    todayResource("/api/sources"),
  ]);
  const currentSnapshot = window.appOnboarding?.snapshot?.();
  const resources = {
    readiness: currentSnapshot?.readiness || initialResources.readiness,
    readinessMissing: currentSnapshot?.missingStep ? [currentSnapshot.missingStep] : [],
    jobs,
    tasks,
    events,
    approvals,
    sources,
  };
  window.WorkHunterUI.today.render(
    window.WorkHunterUI.today.compose({
      resources,
      now: new Date().toISOString(),
      timeZone,
    }),
    { navigate: navigateFromToday },
  );
  window.queueMicrotask(() => scheduleGuidance("today"));
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
  $("#chat-send-button").innerHTML = '<i class="ph ph-circle-notch" aria-hidden="true"></i><span>Думаю…</span>';

  try {
    const result = await api("/api/chat", { method: "POST", body: JSON.stringify(body) });
    state.chatMessages.push({ role: "assistant", content: result.content });
    renderChat();
  } catch (err) {
    state.chatMessages.push({ role: "assistant", content: `Ошибка: ${err.message}. Проверь API-ключ OpenRouter в настройках.` });
    renderChat();
  } finally {
    $("#chat-send-button").disabled = false;
    $("#chat-send-button").innerHTML = '<i class="ph ph-paper-plane-tilt" aria-hidden="true"></i><span>Отправить</span>';
  }
}

function updateSummary() {
  $("#summary-count").textContent = `${state.jobs.length} вакансий`;
}

const SECTION_LABELS = {
  jobs: "Вакансии",
  config: "Настройки",
  profile: "Профиль",
  sources: "Источники",
  "agent-auth": "Проверка HH auth",
};

function clearSectionError(name) {
  const container = $("#init-errors");
  if (!container) return;
  const existing = Array.from(container.children).find(
    (item) => item.dataset.sectionError === name,
  );
  if (existing) existing.remove();
  container.hidden = container.childElementCount === 0;
}

function showSectionError(name, error, retry) {
  const container = $("#init-errors");
  if (!container) return;
  clearSectionError(name);
  const item = document.createElement("div");
  item.className = "section-load-error";
  item.dataset.sectionError = name;
  const message = document.createElement("span");
  message.textContent = `${SECTION_LABELS[name] || name}: ${error?.message || error}`;
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Повторить";
  button.addEventListener("click", retry);
  item.append(message, button);
  container.appendChild(item);
  container.hidden = false;
}

async function runIsolatedLoad(name, loader) {
  try {
    clearSectionError(name);
    await loader();
  } catch (error) {
    showSectionError(name, error, () => runIsolatedLoad(name, loader));
  }
}

const busyButtonStates = new WeakMap();

function setBusy(target, busy, busyLabel = "Работаю...") {
  const button = typeof target === "string" ? document.querySelector(target) : target;
  if (!button) return;
  if (busy) {
    const state = busyButtonStates.get(button);
    if (state) {
      state.activeCount += 1;
    } else {
      busyButtonStates.set(button, {
        activeCount: 1,
        originalLabel: button.textContent,
        originalMarkup: button.innerHTML,
      });
    }
    button.disabled = true;
    button.textContent = busyLabel;
  } else {
    const state = busyButtonStates.get(button);
    if (!state) return;
    state.activeCount -= 1;
    if (state.activeCount > 0) return;
    button.disabled = false;
    button.innerHTML = state.originalMarkup || escapeHtml(state.originalLabel);
    busyButtonStates.delete(button);
  }
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
  return escapeHtml(value);
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

function resolvedRouteForUrl(url) {
  const target = new URL(url, window.location.origin);
  return window.WorkHunterUI.route.resolve(
    target.pathname,
    target.search,
    target.hash,
    state.sourceKeys,
  );
}

function syncVacancyControls(resolved) {
  if (resolved.destination !== "vacancies") return;
  const params = new URLSearchParams(resolved.query);
  const source = params.get("source") || "";
  const sourceFilter = $("#source-filter");
  if (source && ![...sourceFilter.options].some((option) => option.value === source)) {
    sourceFilter.add(new Option(source, source));
  }
  sourceFilter.value = source;
  $("#min-score-filter").value = params.get("min_score") || "0";
}

function applyApplicationRecordRoute(params) {
  let panelName = null;
  let selector = null;
  if (params.get("approval")) {
    panelName = "approvals";
    selector = `[data-approval-id="${params.get("approval")}"]`;
  } else if (params.get("run")) {
    panelName = "runs";
    selector = `[data-run-id="${params.get("run")}"]`;
  } else if (params.get("operation")) {
    panelName = "runs";
    selector = `[data-operation-id="${params.get("operation")}"]`;
  }
  if (!panelName) return;
  switchAgentPanel(panelName);
  const record = selector ? document.querySelector(selector) : null;
  if (record instanceof HTMLElement) {
    record.tabIndex = -1;
    record.focus({ preventScroll: true });
    record.scrollIntoView({ block: "nearest" });
  }
}

function activateView(view, options = {}) {
  const route = ROUTES[view] || ROUTES.inbox;
  if (options.push !== false) {
    return window.WorkHunterUI.route.activate(
      resolvedRouteForUrl(route.url),
      { historyMode: "push" },
    );
  }
  const resolved = options.resolved || window.WorkHunterUI.route.resolve(
    window.location.pathname,
    window.location.search,
    window.location.hash,
    state.sourceKeys,
  );
  state.route = resolved;
  const canonicalView = DESTINATION_TO_VIEW[resolved.destination] || route.view || view || "today";
  const routeView = resolved.destination === "today" && window.__WORK_HUNTER_TEST_LEGACY_ROOT__
    ? "inbox"
    : canonicalView;
  const viewRoute = ROUTES[routeView] || route;
  const params = new URLSearchParams(resolved.query);
  syncVacancyControls(resolved);

  document.querySelectorAll(".nav-button").forEach((item) => item.classList.remove("active"));
  document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));

  document.querySelector(`.nav-button[data-view="${routeView}"]`)?.classList.add("active");
  const viewEl = $(`#view-${routeView}`);
  if (viewEl) viewEl.classList.add("active");

  const title = $("#route-topbar h1");
  if (title) title.textContent = viewRoute.title;
  const summary = $("#summary-line");
  if (summary) summary.textContent = viewRoute.summary;
  const actions = $("#route-actions");
  if (actions) actions.style.display = viewRoute.actions ? "flex" : "none";

  const shouldLoad = options.load !== false;
  if (shouldLoad && routeView === "stats") loadStats().catch(console.error);
  if (shouldLoad && routeView === "inbox") loadJobs().catch((error) => notifyError("jobs-route", error));
  if (shouldLoad && routeView === "chat") applyJobRouteSelection();
  if (shouldLoad && routeView === "calendar") loadEvents().catch(console.error);
  if (shouldLoad && routeView === "agent") {
    loadAgentCockpit()
      .then(() => applyApplicationRecordRoute(params))
      .catch(console.error);
  }
  if (shouldLoad && routeView === "today") loadToday().catch((error) => notifyError("today", error, "Не удалось загрузить экран Сегодня"));
  if (routeView === "agent") {
    switchApplicationsTab(params.get("tab") || "pipeline", { push: false });
    applyApplicationRecordRoute(params);
  }
  if (routeView === "stats") {
    switchAnalyticsTab(params.get("tab") || "overview", { push: false });
  }
  if (routeView === "settings") {
    switchSettingsSection(params.get("section") || "profile", { push: false });
  }
}

function toggleTheme() {
  state.darkTheme = !state.darkTheme;
  document.documentElement.setAttribute("data-theme", state.darkTheme ? "dark" : "light");
  $("#theme-toggle-button").textContent = state.darkTheme ? "Светлая" : "Тёмная";
  localStorage.setItem("work-hunter-theme", state.darkTheme ? "dark" : "light");
}

function loadTheme() {
  const saved = localStorage.getItem("work-hunter-theme");
  if (saved === "dark") {
    state.darkTheme = true;
    document.documentElement.setAttribute("data-theme", "dark");
    if ($("#theme-toggle-button")) {
      $("#theme-toggle-button").textContent = "Светлая";
    }
  }
}

async function getResumeTips() {
  if (!state.selectedId) return;
  setBusy('[data-ui-action="get-resume-tips"]', true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/resume-tips`, { method: "POST", body: "{}" });
    $("#resume-tips-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#resume-tips-output").textContent = `Ошибка: ${err.message}`;
  } finally {
    setBusy('[data-ui-action="get-resume-tips"]', false);
  }
}

async function getAtsResume() {
  if (!state.selectedId) return;
  setBusy('[data-ui-action="get-ats-resume"]', true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/ats-resume`, { method: "POST", body: "{}" });
    $("#ats-resume-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#ats-resume-output").textContent = `Ошибка: ${err.message}`;
  } finally {
    setBusy('[data-ui-action="get-ats-resume"]', false);
  }
}

async function getAtsAudit() {
  if (!state.selectedId) return;
  const text = $("#audit-resume-input").value.trim();
  if (!text) { $("#ats-audit-output").textContent = "Вставь текст резюме выше"; return; }
  setBusy('[data-ui-action="get-ats-audit"]', true);
  try {
    const result = await api("/api/ats-audit", { method: "POST", body: JSON.stringify({ resume_text: text, job_id: state.selectedId }) });
    $("#ats-audit-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#ats-audit-output").textContent = `Ошибка: ${err.message}`;
  } finally {
    setBusy('[data-ui-action="get-ats-audit"]', false);
  }
}

async function getSummary() {
  if (!state.selectedId) return;
  setBusy('[data-ui-action="get-summary"]', true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/summarize`, { method: "POST", body: "{}" });
    $("#summary-output").textContent = result.summary;
  } catch (err) {
    $("#summary-output").textContent = `Ошибка: ${err.message}`;
  } finally {
    setBusy('[data-ui-action="get-summary"]', false);
  }
}

async function getAiFit() {
  if (!state.selectedId) return;
  setBusy('[data-ui-action="get-ai-fit"]', true);
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
  } finally {
    setBusy('[data-ui-action="get-ai-fit"]', false);
  }
}

async function getInterviewQuestions() {
  if (!state.selectedId) return;
  setBusy('[data-ui-action="get-interview-questions"]', true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/interview-questions`, { method: "POST", body: "{}" });
    $("#interview-questions-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#interview-questions-output").textContent = `Ошибка: ${err.message}`;
  } finally {
    setBusy('[data-ui-action="get-interview-questions"]', false);
  }
}

async function getExperiencePitch() {
  if (!state.selectedId) return;
  setBusy('[data-ui-action="get-experience-pitch"]', true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/pitch`, { method: "POST", body: "{}" });
    $("#pitch-output").innerHTML = renderMarkdown(result.content);
  } catch (err) {
    $("#pitch-output").textContent = `Ошибка: ${err.message}`;
  } finally {
    setBusy('[data-ui-action="get-experience-pitch"]', false);
  }
}

async function saveJobNote() {
  if (!state.selectedId) return;
  const note = $("#job-notes-input").value;
  await api(`/api/jobs/${state.selectedId}/note`, { method: "POST", body: JSON.stringify({ body: note }) });
  notify("success", "job-note", "saved", "Заметка сохранена");
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
    bindJobRowActivation(tr, job);
    const score = job.score ? job.score.total_score : "-";
    tr.innerHTML = `
      <td><span class="score">${escapeHtml(String(score))}</span></td>
      <td>
        <div class="title">${escapeHtml(job.title || "Без названия")}</div>
        <div class="meta">${escapeHtml(job.company || "?" )} · ${escapeHtml(job.source)}</div>
      </td>
      <td>${escapeHtml(job.source)}</td>
      <td class="meta">${escapeHtml((job.note_preview || "").substring(0, 60))}</td>
    `;
    body.appendChild(tr);
  }
}

function funnelWidth(count, total) {
  const numericCount = Number(count);
  const numericTotal = Number(total);
  if (!Number.isFinite(numericCount) || !Number.isFinite(numericTotal) || numericTotal <= 0) {
    return 0;
  }
  return Math.min(100, Math.max(0, (numericCount / numericTotal) * 100));
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
        const numericCount = Number(count);
        const width = Number.isFinite(numericCount)
          ? Math.min(100, Math.max(0, numericCount / 10))
          : 0;
        sourceDiv.innerHTML += `<div class="stat-bar"><span>${escapeHtml(source)}</span><div class="bar"><div class="fill" style="width:${width}%"></div></div><span>${escapeHtml(String(count))}</span></div>`;
      }
    }

    const distDiv = $("#stats-score-dist");
    distDiv.innerHTML = "";
    if (stats.score_distribution) {
      for (const [bucket, count] of Object.entries(stats.score_distribution)) {
        const numericCount = Number(count);
        const width = Number.isFinite(numericCount)
          ? Math.min(100, Math.max(0, numericCount / 5))
          : 0;
        distDiv.innerHTML += `<div class="stat-bar"><span>${escapeHtml(bucket)}</span><div class="bar"><div class="fill" style="width:${width}%"></div></div><span>${escapeHtml(String(count))}</span></div>`;
      }
    }

    const funnelDiv = $("#stats-funnel");
    funnelDiv.innerHTML = "";
    if (stats.application_funnel) {
      const total = stats.total_applications || 0;
      const funnelLabels = {
        prepared: "Подготовлено",
        sent: "Отправлено",
        applied: "Отклики",
        response: "Ответы",
        responded: "Ответы",
        replied: "Ответы",
        phone_screen: "Скрининг",
        interview: "Интервью",
        offer: "Офферы",
        rejected: "Отказы",
      };
      for (const item of stats.application_funnel) {
        const count = Number(item.count);
        const safeCount = Number.isFinite(count) ? count : 0;
        const status = String(item.status || "");
        const label = funnelLabels[status.toLowerCase()] || status;
        funnelDiv.innerHTML += `<div class="funnel-stage" style="width:${funnelWidth(safeCount, total)}%;min-width:40px;"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(item.count))}</strong></div>`;
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
  setBusy('[data-ui-action="fetch-full-description"]', true);
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
  } finally {
    setBusy('[data-ui-action="fetch-full-description"]', false);
  }
}

function switchAiTab(panelName) {
  document.querySelectorAll(".ai-tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".ai-panel-content").forEach(p => p.classList.remove("active"));
  document.querySelector(`.ai-tab[data-ai-panel="${panelName}"]`).classList.add("active");
  document.querySelector(`#ai-panel-${panelName}`).classList.add("active");
}

function switchAgentPanel(panelName) {
  document.querySelectorAll(".agent-tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".agent-panel").forEach(p => p.classList.remove("active"));
  document.querySelector(`.agent-tab[data-agent-panel="${panelName}"]`)?.classList.add("active");
  $(`#agent-panel-${panelName}`)?.classList.add("active");
  if (panelName === "chats" && !state.agent.chats) loadAgentChats();
}

const APPLICATION_TAB_PANELS = Object.freeze({
  pipeline: ["inbox"],
  agent: ["dashboard", "chats", "approvals", "runs", "events"],
  automation: ["research", "templates", "resume-builder", "blacklist", "settings"],
});

function pushDestinationSubview(path, key, value, push = true) {
  if (!push) return;
  const params = new URLSearchParams();
  params.set(key, value);
  window.history.pushState({}, "", `${path}?${params.toString()}`);
}

function switchApplicationsTab(tab, { push = true } = {}) {
  const selected = APPLICATION_TAB_PANELS[tab] ? tab : "pipeline";
  const panels = APPLICATION_TAB_PANELS[selected];
  document.querySelectorAll("[data-applications-tab]").forEach((button) => {
    const active = button.dataset.applicationsTab === selected;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll(".agent-tab").forEach((button) => {
    button.hidden = !panels.includes(button.dataset.agentPanel);
  });
  const agentTabBar = $("#view-agent > .agent-tabs");
  if (agentTabBar) agentTabBar.hidden = panels.length === 1;
  const activePanel = document.querySelector(".agent-panel.active")?.id.replace("agent-panel-", "");
  switchAgentPanel(panels.includes(activePanel) ? activePanel : panels[0]);
  pushDestinationSubview("/applications", "tab", selected, push);
}

function switchAnalyticsTab(tab, { push = true } = {}) {
  const selected = tab === "trends" ? "trends" : "overview";
  document.querySelectorAll("[data-analytics-tab]").forEach((button) => {
    const active = button.dataset.analyticsTab === selected;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  const overview = $("#analytics-overview-panel");
  const trends = $("#analytics-trends-panel");
  if (overview) overview.hidden = selected !== "overview";
  if (trends) trends.hidden = selected !== "trends";
  pushDestinationSubview("/analytics", "tab", selected, push);
}

const SETTINGS_SECTIONS = new Set([
  "profile", "resumes", "search", "hh", "ai", "notifications", "appearance", "help", "advanced",
]);

function switchSettingsSection(section, { push = true } = {}) {
  const selected = SETTINGS_SECTIONS.has(section) ? section : "profile";
  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    const active = button.dataset.settingsTab === selected;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("[data-settings-panel]").forEach((panelElement) => {
    panelElement.hidden = window.__WORK_HUNTER_TEST_LEGACY_ROOT__
      ? false
      : panelElement.dataset.settingsPanel !== selected;
  });
  pushDestinationSubview("/settings", "section", selected, push);
}

function bindRovingTablist(selector, dataKey, activate) {
  const tabs = [...document.querySelectorAll(selector)];
  for (const tab of tabs) {
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const currentIndex = tabs.indexOf(tab);
      let nextIndex = currentIndex;
      if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = tabs.length - 1;
      else if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
      else nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
      const next = tabs[nextIndex];
      activate(next.dataset[dataKey]);
      next.focus();
    });
  }
}

function setupDestinationPanels() {
  const statsView = $("#view-stats");
  const statsTabs = statsView?.querySelector(".destination-tabs");
  if (statsView && statsTabs && !$("#analytics-overview-panel")) {
    const overview = document.createElement("div");
    overview.id = "analytics-overview-panel";
    for (const child of [...statsView.children]) {
      if (child !== statsTabs) overview.append(child);
    }
    const trends = document.createElement("div");
    trends.id = "analytics-trends-panel";
    const legacyTrends = $("#view-trends");
    for (const child of [...legacyTrends.children]) trends.append(child);
    statsView.append(overview, trends);
  }

  const apiLab = $("#agent-panel-api-lab");
  const advancedSlot = $("#settings-advanced-slot");
  const advancedFragments = $("#advanced-fragments");
  for (const fragment of document.querySelectorAll("#view-settings > .advanced-fragment")) {
    const title = fragment.querySelector("h2")?.textContent || "Служебные данные";
    const details = document.createElement("details");
    details.className = "advanced-disclosure advanced-section";
    const summary = document.createElement("summary");
    summary.innerHTML = `<i class="ph ph-folder-open" aria-hidden="true"></i><span>${escapeHtml(title)}</span><small>Открыть только при необходимости</small>`;
    const body = document.createElement("div");
    body.className = "advanced-section-body";
    while (fragment.firstChild) body.append(fragment.firstChild);
    details.append(summary, body);
    advancedFragments?.append(details);
    fragment.remove();
  }
  if (apiLab && advancedSlot && advancedFragments) {
    apiLab.classList.remove("agent-panel", "active");
    apiLab.removeAttribute("hidden");
    const details = document.createElement("details");
    details.className = "advanced-disclosure advanced-section";
    const summary = document.createElement("summary");
    summary.innerHTML = '<i class="ph ph-code" aria-hidden="true"></i><span>HH API Lab</span><small>Ручные запросы и сохранённые шаблоны</small>';
    const body = document.createElement("div");
    body.className = "advanced-section-body";
    body.append(apiLab);
    details.append(summary, body);
    advancedFragments.append(details);
    document.querySelector('.agent-tab[data-agent-panel="api-lab"]')?.remove();
  }
}

const LOCAL_SETTINGS_KEY = "work-hunter-ui-preferences";

function localSettingsSnapshot() {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_SETTINGS_KEY) || "{}");
  } catch (_error) {
    return {};
  }
}

function persistLocalSettings() {
  const settings = localSettingsSnapshot();
  for (const control of document.querySelectorAll("[data-local-setting]")) {
    settings[control.dataset.localSetting] = control.type === "checkbox" ? control.checked : control.value;
  }
  localStorage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify(settings));
  notify("success", "local-settings", "saved", "Настройки сохранены");
}

function applyAppearanceChoice({ theme, density, fontScale } = {}) {
  if (theme) {
    document.documentElement.dataset.themeChoice = theme;
    const dark = theme === "dark" || (theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
    state.darkTheme = dark;
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    localStorage.setItem("work-hunter-theme", dark ? "dark" : "light");
    localStorage.setItem("work-hunter-theme-choice", theme);
    $("#theme-toggle-button").textContent = dark ? "Светлая" : "Тёмная";
  }
  if (density) {
    document.documentElement.dataset.density = density;
    localStorage.setItem("work-hunter-density", density);
  }
  if (fontScale) {
    document.documentElement.style.setProperty("--font-scale", String(fontScale));
    localStorage.setItem("work-hunter-font-scale", String(fontScale));
  }
}

function loadLocalUiPreferences() {
  const settings = localSettingsSnapshot();
  for (const control of document.querySelectorAll("[data-local-setting]")) {
    if (!(control.dataset.localSetting in settings)) continue;
    if (control.type === "checkbox") control.checked = Boolean(settings[control.dataset.localSetting]);
    else control.value = String(settings[control.dataset.localSetting]);
  }
  const theme = localStorage.getItem("work-hunter-theme-choice") || (localStorage.getItem("work-hunter-theme") === "dark" ? "dark" : "system");
  const density = localStorage.getItem("work-hunter-density") || "comfortable";
  const fontScale = Number(localStorage.getItem("work-hunter-font-scale") || 1);
  applyAppearanceChoice({ theme, density, fontScale });
  document.querySelectorAll("[data-theme-choice]").forEach((button) => button.classList.toggle("active", button.dataset.themeChoice === theme));
  document.querySelectorAll("[data-density]").forEach((button) => button.classList.toggle("active", button.dataset.density === density));
  document.querySelectorAll("[data-font-scale]").forEach((button) => button.classList.toggle("active", Number(button.dataset.fontScale) === fontScale));
}

function syncHumanSearchRuleControls(config = state.hhAutopilot.config || {}) {
  const values = {
    "#search-rule-query": humanList(getAutopilotValue(config, "filters.required_keywords"), ""),
    "#search-rule-keywords": humanList(getAutopilotValue(config, "filters.required_keywords"), ""),
    "#search-rule-excluded": humanList(getAutopilotValue(config, "filters.excluded_keywords"), ""),
    "#search-rule-remote": getAutopilotValue(config, "filters.remote") || "any",
    "#search-rule-salary": String(getAutopilotValue(config, "filters.minimum_salary") || 0),
    "#search-rule-score": String(getAutopilotValue(config, "ranking.minimum_score") || 60),
    "#search-rule-start": getAutopilotValue(config, "schedule.start") || "08:00",
    "#search-rule-end": getAutopilotValue(config, "schedule.end") || "21:00",
    "#search-rule-interval": String(getAutopilotValue(config, "schedule.interval_minutes") || 60),
    "#search-rule-limit": String(getAutopilotValue(config, "limits.daily_success") || 50),
  };
  for (const [selector, value] of Object.entries(values)) {
    if ($(selector)) $(selector).value = value;
  }
  updateSearchRulePreview();
}

function updateSearchRulePreview() {
  const query = $("#search-rule-query")?.value.trim() || "По правилам профиля";
  const remote = $("#search-rule-remote")?.value || "any";
  const salary = Number($("#search-rule-salary")?.value || 0);
  const score = Number($("#search-rule-score")?.value || 0);
  const limit = Number($("#search-rule-limit")?.value || 0);
  if ($("#search-rule-score-value")) $("#search-rule-score-value").textContent = `${score}%`;
  if ($("#search-preview-role")) $("#search-preview-role").textContent = query.split(",")[0] || "По правилам профиля";
  if ($("#search-preview-remote")) $("#search-preview-remote").textContent = remote === "only" ? "Только удалённо" : remote === "exclude" ? "Офис или гибрид" : "Любой формат";
  if ($("#search-preview-salary")) $("#search-preview-salary").textContent = salary > 0 ? `От ${salary.toLocaleString("ru-RU")} ₽` : "Без минимума";
  if ($("#search-preview-score")) $("#search-preview-score").textContent = `Score от ${score}`;
  if ($("#search-preview-limit")) $("#search-preview-limit").textContent = limit > 0 ? `До ${limit} откликов в день` : "По лимиту площадки";
}

async function saveHumanSearchRules() {
  const config = structuredClone(state.hhAutopilot.config || JSON.parse($("#hh-autopilot-config")?.value || "{}"));
  const list = (value) => String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
  setAutopilotValue(config, "filters.required_keywords", list($("#search-rule-keywords")?.value || $("#search-rule-query")?.value));
  setAutopilotValue(config, "filters.excluded_keywords", list($("#search-rule-excluded")?.value));
  setAutopilotValue(config, "filters.remote", $("#search-rule-remote")?.value || "any");
  setAutopilotValue(config, "filters.minimum_salary", Number($("#search-rule-salary")?.value || 0));
  setAutopilotValue(config, "ranking.minimum_score", Number($("#search-rule-score")?.value || 0));
  setAutopilotValue(config, "schedule.start", $("#search-rule-start")?.value || "08:00");
  setAutopilotValue(config, "schedule.end", $("#search-rule-end")?.value || "21:00");
  setAutopilotValue(config, "schedule.interval_minutes", Number($("#search-rule-interval")?.value || 60));
  setAutopilotValue(config, "limits.daily_success", Number($("#search-rule-limit")?.value || 50));
  $("#hh-autopilot-config").value = JSON.stringify(config, null, 2);
  for (const [selectorName, path, type] of HH_AUTOPILOT_COMMON_FIELDS) {
    const input = $(selectorName);
    if (!input) continue;
    const value = getAutopilotValue(config, path);
    input.value = type === "list" ? (value || []).join(", ") : (value ?? "");
  }
  await saveHhAutopilotConfig();
  notify("success", "search-rules", "saved", "Правила поиска сохранены");
}

function setupNavigationAccessibility() {
  document.querySelectorAll(".sidebar .nav-button").forEach((button) => {
    const label = button.querySelector("span")?.textContent.trim() || button.dataset.view;
    button.setAttribute("aria-label", label);
    button.title = label;
  });
}

function openMobileMenu() {
  const trigger = $("#mobile-menu-button");
  trigger?.setAttribute("aria-expanded", "true");
  const result = window.appOverlays.request({
    kind: "mobileMenu",
    label: "Основная навигация",
    trigger,
    onClose() {
      trigger?.setAttribute("aria-expanded", "false");
    },
    render(root) {
      root.classList.add("mobile-menu-sheet");
      const title = document.createElement("h2");
      title.textContent = "Разделы";
      const list = document.createElement("nav");
      list.className = "mobile-menu-list";
      list.setAttribute("aria-label", "Мобильная навигация");
      document.querySelectorAll(".sidebar .nav-button").forEach((source) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.mobileDestination = source.dataset.view;
        button.textContent = source.querySelector("span")?.textContent || source.getAttribute("aria-label");
        button.addEventListener("click", () => {
          window.appOverlays.closeBlocking();
          activateView(source.dataset.view);
        });
        list.append(button);
      });
      root.append(title, list);
    },
  });
  if (!result.accepted) trigger?.setAttribute("aria-expanded", "false");
  return result;
}

function scheduleGuidance(route) {
  if (!window.appGuidance) return;
  const snapshot = window.appOnboarding?.snapshot?.();
  window.appGuidance.schedule(route, {
    canFind: snapshot?.readiness === "ready",
    hasScoredJob: state.jobs.some((job) => Number.isFinite(job.score?.total_score)),
    hasStatusActions: Boolean(state.selectedId && $("[data-guide='vacancy-actions']")),
    hasLivePlan: Boolean(state.selectedId && $("[data-guide='live-hh-action']")),
  });
}

async function loadAgentCockpit({ liveAuth = false } = {}) {
  $("#agent-status-line").textContent = "Загружаю статус агента...";
  await Promise.all([
    loadAgentPreflight({ liveAuth }),
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
}

async function loadAgentPreflight({ liveAuth = false } = {}) {
  const url = liveAuth
    ? "/api/agent/preflight?live_auth=true"
    : "/api/agent/preflight";
  state.agent.preflight = await api(url);
  renderAgentDashboard();
}

async function checkAgentLiveAuth() {
  setBusy("#agent-live-auth-button", true);
  clearSectionError("agent-auth");
  try {
    await loadAgentPreflight({ liveAuth: true });
  } catch (error) {
    showSectionError("agent-auth", error, checkAgentLiveAuth);
  } finally {
    setBusy("#agent-live-auth-button", false);
  }
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

function agentChatLimit() {
  const parsed = Number.parseInt($("#agent-chats-limit")?.value || "20", 10);
  return Number.isFinite(parsed) ? Math.max(1, Math.min(parsed, 100)) : 20;
}

async function loadAgentChats() {
  const button = $("#agent-chats-refresh-button");
  if (button) button.disabled = true;
  $("#agent-chats-status").textContent = "Проверяю входящие чаты…";
  clearAgentChatPlan();
  try {
    state.agent.chats = await api(`/api/hh/chats?limit=${agentChatLimit()}&awaiting_only=true`);
    renderAgentChats();
  } catch (error) {
    state.agent.chats = { status: "error", message: error.message, chats: [] };
    renderAgentChats();
  } finally {
    if (button) button.disabled = false;
  }
}

async function startHhLogin({ account = "default", trigger = null, refreshChats = false } = {}) {
  const button = trigger || $("#agent-chats-login-button");
  if (state.agent.chatAuthStarting) return;
  state.agent.chatAuthStarting = true;
  if (button) {
    button.disabled = true;
    button.textContent = "Открываю окно входа…";
  }
  $("#agent-chats-status").textContent = "Открываю безопасное окно входа HH…";
  try {
    await api("/api/hh/auth/login", {
      method: "POST",
      body: JSON.stringify({ account }),
    });
    $("#agent-chats-status").textContent = "Завершите вход в открывшемся окне. Проверяю подключение…";
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      await loadHhConnectionStatus();
      if (hhIsConnected()) {
        state.agent.chatAuthStarting = false;
        if (refreshChats) await loadAgentChats();
        await loadHhAutopilot().catch(() => {});
        return;
      }
    }
    $("#agent-chats-status").textContent = "Вход пока не подтверждён. Нажмите «Войти в HH», чтобы попробовать ещё раз.";
  } catch (error) {
    $("#agent-chats-status").textContent = `Не удалось открыть вход HH: ${error.message}`;
  } finally {
    state.agent.chatAuthStarting = false;
    if (button) {
      button.disabled = false;
      button.textContent = "Войти в HH";
    }
  }
}

async function startAgentChatLogin() {
  return startHhLogin({
    account: $("#hh-human-account")?.value || "default",
    trigger: $("#agent-chats-login-button"),
    refreshChats: true,
  });
}

function agentChatActionCount(plan) {
  return Number(plan?.count || 0) + Number(plan?.leave_count || 0);
}

function setAgentChatButtons({ canPlan = false, canSend = false } = {}) {
  const planButton = $("#agent-chats-plan-button");
  const sendButton = $("#agent-chats-send-button");
  if (planButton) planButton.disabled = !canPlan;
  if (sendButton) sendButton.disabled = !canSend;
}

function clearAgentChatPlan() {
  state.agent.chatReplyPlan = null;
  const output = $("#agent-chats-output");
  if (output) {
    output.hidden = true;
    output.innerHTML = "";
  }
  setAgentChatButtons();
}

function renderAgentChats() {
  const box = $("#agent-chats-list");
  const status = $("#agent-chats-status");
  const connection = $("#agent-chats-connection");
  if (!box || !status) return;
  const payload = state.agent.chats || {};
  const chats = Array.isArray(payload.chats) ? payload.chats : [];
  const blocked = payload.status === "blocked";
  const failed = payload.status === "error";
  if (connection) connection.hidden = !blocked;
  const loginButton = $("#agent-chats-login-button");
  if (loginButton) loginButton.disabled = state.agent.chatAuthStarting;
  if (blocked) {
    status.textContent = "HH не подключён — ответы и отправка пока недоступны.";
  } else if (failed) {
    status.textContent = `Не удалось загрузить чаты: ${payload.message || "неизвестная ошибка"}`;
  } else if (chats.length) {
    status.textContent = `Ждут ответа: ${chats.length}`;
  } else {
    status.textContent = "Новых сообщений, требующих ответа, нет.";
  }
  setAgentChatButtons({ canPlan: !blocked && !failed && chats.length > 0, canSend: false });
  box.innerHTML = chats.length ? chats.map((chat) => {
    const options = Array.isArray(chat.reply_options) ? chat.reply_options : [];
    return `
      <div class="agent-row">
        <div class="agent-row-head">
          <strong>${escapeHtml(chat.vacancy_name || `Chat #${chat.chat_id}`)}</strong>
          <span class="agent-badge ${chat.discarded ? "error" : "active"}">${chat.discarded ? "отказ" : "ждёт ответа"}</span>
        </div>
        <div class="meta">${escapeHtml([chat.company_name, chat.contact_name, chat.resume_title].filter(Boolean).join(" · "))}</div>
        <p>${escapeHtml(chat.last_message_text || "Без текста")}</p>
        ${options.length ? `<div class="agent-inline-stats">${options.map((option) => `<span>${escapeHtml(option)}</span>`).join("")}</div>` : ""}
      </div>`;
  }).join("") : blocked
    ? ""
    : '<p class="meta">Здесь появятся сообщения работодателей, на которые нужно ответить.</p>';
}

function renderAgentChatReplyPlan(result) {
  const output = $("#agent-chats-output");
  const status = $("#agent-chats-status");
  if (!output || !status) return;
  const replies = Array.isArray(result?.replies) ? result.replies : [];
  const leaves = Array.isArray(result?.leaves) ? result.leaves : [];
  const actionCount = agentChatActionCount(result);
  if (result?.status === "blocked") {
    output.hidden = true;
    output.innerHTML = "";
    status.textContent = "Сначала подключите HH, затем обновите список чатов.";
    setAgentChatButtons();
    return;
  }
  if (!actionCount) {
    output.hidden = true;
    output.innerHTML = "";
    status.textContent = "Готовых ответов нет — отправлять нечего.";
    setAgentChatButtons({ canPlan: Array.isArray(state.agent.chats?.chats) && state.agent.chats.chats.length > 0 });
    return;
  }
  const replyRows = replies.map((item) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>${escapeHtml(item.vacancy_name || `Чат #${item.chat_id || "—"}`)}</strong>
        <span class="agent-badge active">ответ</span>
      </div>
      <p>${escapeHtml(item.message || "")}</p>
    </div>
  `);
  const leaveRows = leaves.map((item) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>${escapeHtml(item.vacancy_name || `Чат #${item.chat_id || "—"}`)}</strong>
        <span class="agent-badge warning">скрыть</span>
      </div>
      <p class="meta">Работодатель уже отказал — чат будет убран из входящих.</p>
    </div>
  `);
  output.innerHTML = `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>Ответы готовы к проверке</strong>
        <span class="agent-badge approved">${actionCount}</span>
      </div>
      <p class="meta">Проверьте тексты ниже. Ничего не уйдёт без отдельного подтверждения.</p>
    </div>
    ${replyRows.join("")}
    ${leaveRows.join("")}
  `;
  output.hidden = false;
  status.textContent = `Подготовлено: ${replies.length} ответов, ${leaves.length} чатов скрыть.`;
  setAgentChatButtons({ canPlan: true, canSend: true });
}

async function requestAgentChatReplyPlan() {
  const result = await api("/api/hh/chats/reply", {
    method: "POST",
    body: JSON.stringify({
      use_ai: true,
      limit: agentChatLimit(),
      leave_discarded: $("#agent-chats-leave-discarded")?.checked === true,
      confirm: false,
    }),
  });
  state.agent.chatReplyPlan = result;
  renderAgentChatReplyPlan(result);
  return result;
}

async function planAgentChatReplies() {
  const button = $("#agent-chats-plan-button");
  if (button) button.disabled = true;
  try {
    await requestAgentChatReplyPlan();
  } catch (error) {
    $("#agent-chats-status").textContent = `Не удалось подготовить ответы: ${error.message}`;
    setAgentChatButtons({ canPlan: true, canSend: false });
  } finally {
    if (button && state.agent.chatReplyPlan) button.disabled = false;
  }
}

function chatReplyPlanFingerprint(plan) {
  return stableActionFingerprint({
    replies: (plan.replies || []).map((item) => ({
      chat_id: item.chat_id,
      message: item.message,
      source: item.source_message_fingerprint,
    })),
    leaves: (plan.leaves || []).map((item) => ({
      chat_id: item.chat_id,
      source: item.source_message_fingerprint,
    })),
  });
}

async function sendAgentChatReplies() {
  const trigger = $("#agent-chats-send-button");
  if (trigger) trigger.disabled = true;
  let plan;
  try {
    plan = await requestAgentChatReplyPlan();
  } catch (error) {
    $("#agent-chats-status").textContent = `Не удалось проверить ответы: ${error.message}`;
    setAgentChatButtons({ canPlan: true, canSend: false });
    return;
  }
  const actionCount = agentChatActionCount(plan);
  if (!actionCount) {
    $("#agent-chats-status").textContent = "Нет готовых действий для отправки.";
    return;
  }
  const fingerprint = chatReplyPlanFingerprint(plan);
  let descriptor;
  descriptor = {
    operationType: "resume_account",
    title: "Отправить ответы в чаты HH?",
    consequence: "Работодатели получат реальные сообщения; отказные чаты будут скрыты, если включена очистка.",
    targetRows: [
      { key: "resume_or_account", label: "Аккаунт", safeValue: plan.account || "default" },
      { key: "changes", label: "Действия", safeValue: `${plan.count || 0} ответов · ${plan.leave_count || 0} закрытий` },
    ],
    preview: (plan.replies || []).map((item) => `${item.vacancy_name}: ${item.message}`).join("\n\n") || null,
    riskFlags: [{ code: "external_mutation", safeMessage: "Сообщения уйдут в реальные чаты HH." }],
    acknowledgement: "Я проверил тексты ответов и список чатов",
    confirmLabel: "Отправить ответы",
    fingerprint,
    trigger,
    revalidate: async () => validateResumeAccountMutation(descriptor, async () => {
      const current = await requestAgentChatReplyPlan();
      const currentFingerprint = chatReplyPlanFingerprint(current);
      if (currentFingerprint !== fingerprint) {
        return { status: "changed", fingerprint: currentFingerprint, canExecute: false };
      }
      return { status: "executable", fingerprint, canExecute: true, blockers: [] };
    }),
    execute: async (confirm) => {
      const result = await api("/api/hh/chats/reply", {
        method: "POST",
        body: JSON.stringify({
          use_ai: true,
          limit: agentChatLimit(),
          leave_discarded: $("#agent-chats-leave-discarded")?.checked === true,
          confirm: confirm === true,
        }),
      });
      state.agent.chats = null;
      await loadAgentChats();
      $("#agent-chats-status").textContent = `Готово: отправлено ${result.sent_count || result.count || 0} ответов.`;
      return result;
    },
  };
  openLiveAction(descriptor);
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
      ${approvals.map((item) => `<div class="meta">#${escapeHtml(String(item.id))} ${escapeHtml(item.action_type)} · ${escapeHtml(item.status)} · ${escapeHtml(item.reason || "")}</div>`).join("") || '<p class="meta">Нет approvals.</p>'}
    </div>
    <div class="agent-row">
      <div class="agent-row-head"><strong>Recent runs</strong><span class="agent-badge">${runs.length}</span></div>
      ${runs.map((item) => `<div class="meta">#${escapeHtml(String(item.id))} ${escapeHtml(item.tool_name)} · ${escapeHtml(item.status)}</div>`).join("") || '<p class="meta">Нет запусков.</p>'}
    </div>
  `;
}

function renderAgentApprovals() {
  const box = $("#agent-approvals-list");
  if (!box) return;
  const approvals = state.agent.approvals || [];
  if (!approvals.length) {
    box.innerHTML = '<p class="meta">Очередь пустая.</p>';
    return;
  }
  box.innerHTML = approvals.map((item) => {
    const approvalId = requirePositiveInteger(item.id, "approval id");
    return `
      <div class="agent-row" data-approval-id="${approvalId}">
        <div class="agent-row-head">
          <strong>#${approvalId} ${escapeHtml(item.action_type)}</strong>
          <span class="agent-badge ${escapeAttr(item.status)}">${escapeHtml(item.status)}</span>
        </div>
        <div class="meta">confidence: ${escapeHtml(String(item.confidence))} · ${escapeHtml(item.reason || "")}</div>
        <pre class="agent-json">${escapeHtml(JSON.stringify(item.payload || {}, null, 2))}</pre>
        <textarea id="agent-modify-${approvalId}" class="agent-modify" rows="2" placeholder="Что изменить в payload/сообщении"></textarea>
        <div class="agent-row-actions">
          <button ${numericRecordAction("approve-agent-approval", approvalId, "approval id")}>Approve</button>
          <button ${numericRecordAction("reject-agent-approval", approvalId, "approval id")}>Reject</button>
          <button ${numericRecordAction("modify-agent-approval", approvalId, "approval id")}>Modify</button>
          <button ${numericRecordAction("flag-agent-approval", approvalId, "approval id")}>Flag</button>
        </div>
      </div>
    `;
  }).join("");
}

function renderAgentOperations() {
  const box = $("#agent-runs-list");
  const logBox = $("#agent-logs-list");
  if (!box || !logBox) return;
  const operations = state.agent.operations || { runs: [], logs: [] };
  box.innerHTML = operations.runs.length ? operations.runs.map((run) => `
    <div class="agent-row" data-run-id="${escapeAttr(String(run.id))}">
      <div class="agent-row-head">
        <strong>#${escapeHtml(String(run.id))} ${escapeHtml(run.tool_name)}</strong>
        <span class="agent-badge ${escapeAttr(run.status)}">${escapeHtml(run.status)}</span>
      </div>
      <div class="meta">${escapeHtml(run.started_at || "")} ${run.finished_at ? "→ " + escapeHtml(run.finished_at) : ""}</div>
      <pre class="agent-json">${escapeHtml(JSON.stringify(run.output || run.input || {}, null, 2))}</pre>
      ${run.status === "running" ? `<button ${numericRecordAction("cancel-agent-operation", run.id, "operation id")}>Cancel</button>` : ""}
    </div>
  `).join("") : '<p class="meta">Запусков пока нет.</p>';
  logBox.innerHTML = operations.logs.length ? operations.logs.map((log) => `
    <div class="agent-log-row" data-operation-id="${escapeAttr(String(log.operation_id || ""))}">
      <span class="agent-badge ${escapeAttr(log.level)}">${escapeHtml(log.level)}</span>
      <span>#${escapeHtml(String(log.operation_id || "—"))}</span>
      <span>${escapeHtml(log.message || "")}</span>
    </div>
  `).join("") : '<p class="meta">Логов пока нет.</p>';
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
        <button ${keyedRecordAction("delete-agent-template", item.name)}>Удалить</button>
      </div>
      <pre class="agent-json">${escapeHtml(item.body || "")}</pre>
    </div>
  `).join("") : '<p class="meta">Шаблонов пока нет.</p>';
}

function renderAgentBlacklist() {
  const box = $("#agent-blacklist-list");
  if (!box) return;
  const blacklist = state.agent.blacklist || [];
  box.innerHTML = blacklist.length ? blacklist.map((item) => `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>${escapeHtml(item.employer_name || item.employer_id)}</strong>
        <button ${keyedRecordAction("delete-agent-blacklist", item.employer_id)}>Удалить</button>
      </div>
      <div class="meta">${escapeHtml(item.employer_id)} · ${escapeHtml(item.reason || "")}</div>
    </div>
  `).join("") : '<p class="meta">Blacklist пустой.</p>';
}

function renderHhLabQuickCalls() {
  const box = $("#hh-lab-quick-calls");
  if (!box) return;
  const calls = state.agent.labQuickCalls || [];
  box.innerHTML = calls.map((item) => `
    <button ${keyedRecordAction("run-hh-lab-quick", item.id)}>${escapeHtml(item.label || item.path)}</button>
  `).join("");
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
        <button ${keyedRecordAction("fill-hh-lab-snippet", item.name)}>Load</button>
        <button ${keyedRecordAction("run-hh-lab-snippet", item.name)}>Run</button>
        <button ${keyedRecordAction("delete-hh-lab-snippet", item.name)}>Delete</button>
      </div>
    </div>
  `).join("");
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

function currentHhLabPayload() {
  return {
    method: $("#hh-lab-method").value,
    path: $("#hh-lab-path").value.trim(),
    params: parseHhLabJson("#hh-lab-params", {}),
    body: parseHhLabJson("#hh-lab-body", null),
  };
}

function buildLabMutationDescriptor(payload, trigger = document.activeElement) {
  const reviewed = {
    method: payload.method.toUpperCase(),
    path: payload.path,
    params: payload.params,
    body: payload.body,
  };
  const fingerprint = stableActionFingerprint(reviewed);
  let descriptor;
  descriptor = {
    operationType: "api_lab",
    title: "Выполнить изменяющий запрос к HH?",
    consequence: `${reviewed.method} ${reviewed.path} может изменить данные вашего HH-аккаунта.`,
    targetRows: [
      { key: "method", label: "Метод", safeValue: reviewed.method },
      { key: "path", label: "Путь", safeValue: reviewed.path },
      { key: "params", label: "Параметры", safeValue: maskForUi(reviewed.params) },
      { key: "body", label: "Тело", safeValue: maskForUi(reviewed.body) },
    ],
    riskFlags: [{ code: "api_lab", safeMessage: "API Lab обходит обычные продуктовые сценарии. Проверьте метод, путь и тело." }],
    acknowledgement: "Я проверил метод, путь, параметры и тело запроса",
    confirmLabel: "Выполнить запрос",
    fingerprint,
    trigger,
    revalidate: async () => validateLabMutation(descriptor, () => {
      let current;
      try {
        current = currentHhLabPayload();
      } catch (error) {
        return { status: "error", safeMessage: String(error?.message || error), canExecute: false };
      }
      const updatedDescriptor = buildLabMutationDescriptor(current, trigger);
      if (updatedDescriptor.fingerprint !== fingerprint) {
        return { status: "changed", fingerprint: updatedDescriptor.fingerprint, updatedDescriptor, canExecute: false };
      }
      return { status: "executable", fingerprint, canExecute: true };
    }),
    execute: async (confirm) => {
      const request = { ...reviewed, confirm: confirm === true };
      writeHhLabOutput({ status: "running", request: { ...request, body: maskForUi(request.body) } });
      const result = await api("/api/hh/lab/call", { method: "POST", body: JSON.stringify(request) });
      writeHhLabOutput(result);
      await loadAgentOperations();
      return result;
    },
  };
  return descriptor;
}

async function runHhLabCall() {
  try {
    const payload = currentHhLabPayload();
    if (HH_LAB_MUTATING_METHODS.has(payload.method.toUpperCase())) {
      const descriptor = buildLabMutationDescriptor(payload, $("#hh-lab-run-button"));
      openLiveAction(descriptor);
      return;
    }
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

async function executeAgentOperation(operation, params = {}, button = null) {
  if (button) button.disabled = true;
  $("#agent-operation-note").textContent = `${operation}: running`;
  try {
    const result = await api("/api/agent/run", {
      method: "POST",
      body: JSON.stringify({ operation, params }),
    });
    $("#agent-operation-note").textContent = `${operation}: ${result.result?.status || "ok"}`;
    await Promise.all([loadAgentPreflight(), loadAgentDigest(), loadAgentOperations(), loadAgentApprovals()]);
    return result;
  } catch (err) {
    $("#agent-operation-note").textContent = `${operation}: ${err.message}`;
    throw err;
  } finally {
    if (button) button.disabled = false;
  }
}

function buildResumeAccountMutationDescriptor(operation, button) {
  const fingerprint = stableActionFingerprint({ operation, profile: state.profile?.active || "default" });
  let descriptor;
  descriptor = {
    operationType: "resume_account",
    title: "Обновить резюме в HH?",
    consequence: "HH обновит данные резюме в вашем внешнем аккаунте.",
    targetRows: [
      { key: "resume_or_account", label: "Аккаунт", safeValue: state.profile?.active || "default" },
      { key: "changes", label: "Изменение", safeValue: "Обновление HH-резюме" },
    ],
    riskFlags: [{ code: "external_mutation", safeMessage: "Изменения будут видны в HH." }],
    acknowledgement: "Я проверил аккаунт и понимаю последствия обновления",
    confirmLabel: "Обновить резюме",
    fingerprint,
    trigger: button,
    revalidate: async () => validateResumeAccountMutation(descriptor, async () => {
      const preflight = await api("/api/agent/preflight?live_auth=true");
      const authorized = preflight.auth?.authorized === true;
      const available = preflight.capabilities?.api_token === true;
      const blockers = preflight.agent?.paused
        ? [{ code: "agent_paused", safeMessage: preflight.agent.pause_reason || "HH-агент приостановлен." }]
        : [];
      return {
        status: "executable",
        fingerprint,
        auth: { status: authorized ? "ready" : "missing", safeMessage: "Сначала проверьте авторизацию HH." },
        capability: { available, code: available ? "ok" : "missing_api_token", safeMessage: "Нет доступа к HH API." },
        blockers,
        canExecute: authorized && available && blockers.length === 0,
      };
    }),
    execute: async (confirm) => executeAgentOperation(operation, { confirm: confirm === true }, button),
  };
  return descriptor;
}

async function runAgentOperation(operation, button = null) {
  if (operation === "update-resumes") {
    const descriptor = buildResumeAccountMutationDescriptor(operation, button);
    openLiveAction(descriptor);
    return;
  }
  return executeAgentOperation(operation, {}, button);
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
}

async function cancelAgentOperation(id) {
  const operationId = requirePositiveInteger(id, "operation id");
  await api(`/api/cancel/${operationId}`, { method: "POST", body: JSON.stringify({ reason: "cancelled_from_ui" }) });
  await loadAgentOperations();
}

async function approveAgentApproval(id) {
  const approvalId = requirePositiveInteger(id, "approval id");
  await api(`/api/approvals/${approvalId}/approve`, { method: "POST", body: JSON.stringify({ reason: "approved_from_ui" }) });
  await Promise.all([loadAgentApprovals(), loadAgentDigest()]);
}

async function rejectAgentApproval(id) {
  const approvalId = requirePositiveInteger(id, "approval id");
  await api(`/api/approvals/${approvalId}/reject`, { method: "POST", body: JSON.stringify({ reason: "rejected_from_ui" }) });
  await Promise.all([loadAgentApprovals(), loadAgentDigest()]);
}

async function modifyAgentApproval(id) {
  const approvalId = requirePositiveInteger(id, "approval id");
  const instruction = $(`#agent-modify-${approvalId}`)?.value.trim() || "modified_from_ui";
  await api(`/api/approvals/${approvalId}/modify`, {
    method: "POST",
    body: JSON.stringify({ instruction, payload_patch: {} }),
  });
  await Promise.all([loadAgentApprovals(), loadAgentDigest()]);
}

async function flagAgentApproval(id) {
  const approvalId = requirePositiveInteger(id, "approval id");
  await api(`/api/approvals/${approvalId}/flag`, { method: "POST", body: JSON.stringify({ reason: "flagged_from_ui" }) });
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


const delegatedUiActions = Object.freeze({
  "bulk-action": (control) => bulkAction(control.dataset.status),
  "clear-bulk-selection": () => clearBulkSelection(),
  "toggle-select-all": (_control, event) => {
    event.stopPropagation();
    toggleSelectAll();
  },
  "get-resume-tips": () => getResumeTips(),
  "get-ats-resume": () => getAtsResume(),
  "get-ats-audit": () => getAtsAudit(),
  "get-ai-fit": () => getAiFit(),
  "get-summary": () => getSummary(),
  "get-experience-pitch": () => getExperiencePitch(),
  "get-interview-questions": () => getInterviewQuestions(),
  "prepare-letter": () => prepareLetter(),
  "prepare-letter-ai": () => prepareLetterAi(),
  "save-job-note": () => saveJobNote(),
  "show-event-form": () => showEventForm(),
  "save-event": () => saveEvent(),
  "hide-event-form": () => hideEventForm(),
  "load-agent-digest": () => loadAgentDigest(),
  "load-agent-approvals": () => loadAgentApprovals(),
  "load-agent-operations": () => loadAgentOperations(),
  "show-resume-form": () => showResumeForm(),
  "save-resume": () => saveResume(),
  "hide-resume-form": () => hideResumeForm(),
  "show-search-form": () => showSearchForm(),
  "save-search": () => saveSearch(),
  "hide-search-form": () => hideSearchForm(),
  "load-ghost-jobs": () => loadGhostJobs(),
  "load-market-trends": () => loadMarketTrends(),
  "sync-from-sources": () => syncJobs(),
  "toggle-theme-from-settings": () => toggleTheme(),
  "restart-onboarding": () => window.appOnboarding?.review(),
  "restart-guidance": () => {
    window.WorkHunterUI.onboarding.resetGuidance();
    notify("success", "guidance", "reset", "Контекстные советы включены снова");
  },
  "mark-selected": (control) => markSelected(control.dataset.status),
  "fetch-full-description": () => fetchFullDescription(),
  "apply-job": (control) => applyJob(control.dataset.dryRun !== "false"),
  "apply-hh": (control) => applyJob(control.dataset.dryRun !== "false"),
  "share-to-telegram": () => shareToTelegram(),
  "smart-classify": () => smartClassify(),
  "parse-job-structure": () => parseJobStructure(),
  "run-gap-analysis": () => runGapAnalysis(),
});


function handleDelegatedUiAction(event) {
  const control = event.target instanceof Element
    ? event.target.closest("[data-ui-action]")
    : null;
  if (!control) return;
  const handler = delegatedUiActions[control.dataset.uiAction];
  if (!handler) return;
  try {
    Promise.resolve(handler(control, event)).catch(reportDynamicActionError);
  } catch (error) {
    reportDynamicActionError(error);
  }
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

const dynamicRecordActions = Object.freeze({
  "toggle-job-select": (control) => toggleJobSelect(
    requirePositiveInteger(control.dataset.recordId, "job id"),
    control.checked,
  ),
  "approve-agent-approval": (control) => approveAgentApproval(control.dataset.recordId),
  "reject-agent-approval": (control) => rejectAgentApproval(control.dataset.recordId),
  "modify-agent-approval": (control) => modifyAgentApproval(control.dataset.recordId),
  "flag-agent-approval": (control) => flagAgentApproval(control.dataset.recordId),
  "cancel-agent-operation": (control) => cancelAgentOperation(control.dataset.recordId),
  "delete-agent-template": (control) => deleteAgentTemplate(control.dataset.recordKey),
  "delete-agent-blacklist": (control) => deleteAgentBlacklist(control.dataset.recordKey),
  "run-hh-lab-quick": (control) => runHhLabQuick(control.dataset.recordKey),
  "fill-hh-lab-snippet": (control) => fillHhLabRequestByName(control.dataset.recordKey),
  "run-hh-lab-snippet": (control) => runHhLabSnippet(control.dataset.recordKey),
  "delete-hh-lab-snippet": (control) => deleteHhLabSnippet(control.dataset.recordKey),
  "activate-resume": (control) => activateResume(control.dataset.recordId),
  "edit-resume": (control) => showResumeForm(control.dataset.recordId),
  "delete-resume": (control) => deleteResume(control.dataset.recordId),
  "edit-event": (control) => showEventForm(control.dataset.recordId),
  "delete-event": (control) => deleteEvent(control.dataset.recordId),
  "delete-search": (control) => deleteSearch(control.dataset.recordId),
  "mark-ghost-job": (control) => markGhostJob(control.dataset.recordId),
});

function reportDynamicActionError(error) {
  notifyError("dynamic-action", error);
}

function handleDynamicRecordAction(event) {
  const control = event.target instanceof Element
    ? event.target.closest("[data-record-action]")
    : null;
  if (!control) return;
  const handler = dynamicRecordActions[control.dataset.recordAction];
  if (!handler) return;
  if (control.matches(".job-checkbox")) event.stopPropagation();
  try {
    Promise.resolve(handler(control)).catch(reportDynamicActionError);
  } catch (error) {
    reportDynamicActionError(error);
  }
}

function bindEnhancedSettingsInteractions() {
  const searchInputs = ["#search-rule-query", "#search-rule-keywords", "#search-rule-excluded", "#search-rule-remote", "#search-rule-salary", "#search-rule-score", "#search-rule-start", "#search-rule-end", "#search-rule-interval", "#search-rule-limit"];
  for (const selector of searchInputs) {
    $(selector)?.addEventListener("input", updateSearchRulePreview);
    $(selector)?.addEventListener("change", updateSearchRulePreview);
  }
  $("#search-rules-save")?.addEventListener("click", () => saveHumanSearchRules().catch((error) => notifyError("search-rules", error, "Не удалось сохранить правила")));
  $("#search-rules-reset")?.addEventListener("click", () => {
    const defaults = { "#search-rule-query": "", "#search-rule-keywords": "", "#search-rule-excluded": "", "#search-rule-remote": "any", "#search-rule-salary": "0", "#search-rule-score": "60", "#search-rule-start": "08:00", "#search-rule-end": "21:00", "#search-rule-interval": "60", "#search-rule-limit": "50" };
    for (const [selector, value] of Object.entries(defaults)) if ($(selector)) $(selector).value = value;
    updateSearchRulePreview();
  });
  document.querySelectorAll("[data-theme-choice]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll("[data-theme-choice]").forEach((item) => item.classList.toggle("active", item === button));
    applyAppearanceChoice({ theme: button.dataset.themeChoice });
  }));
  document.querySelectorAll("[data-density]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll("[data-density]").forEach((item) => item.classList.toggle("active", item === button));
    applyAppearanceChoice({ density: button.dataset.density });
  }));
  document.querySelectorAll("[data-font-scale]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll("[data-font-scale]").forEach((item) => item.classList.toggle("active", item === button));
    applyAppearanceChoice({ fontScale: Number(button.dataset.fontScale) });
  }));
  $("#appearance-save")?.addEventListener("click", persistLocalSettings);
  $("#appearance-reset")?.addEventListener("click", () => {
    applyAppearanceChoice({ theme: "system", density: "comfortable", fontScale: 1 });
    document.querySelectorAll("[data-theme-choice]").forEach((button) => button.classList.toggle("active", button.dataset.themeChoice === "system"));
    document.querySelectorAll("[data-density]").forEach((button) => button.classList.toggle("active", button.dataset.density === "comfortable"));
    document.querySelectorAll("[data-font-scale]").forEach((button) => button.classList.toggle("active", button.dataset.fontScale === "1"));
  });
  $("#save-notifications-button")?.addEventListener("click", persistLocalSettings);
  $("#ai-check-button")?.addEventListener("click", () => {
    const model = $("#ai-model-input")?.value.trim() || $("#opencode-model-input")?.value.trim();
    notify(model ? "success" : "warning", "settings-ai", "checked", model ? "AI настроен" : "Выберите модель", model ? `Модель: ${model}` : "Укажите модель перед проверкой.");
  });
  $("#resumes-sync-hh")?.addEventListener("click", () => Promise.allSettled([loadResumes(), loadHhAutopilot()]).then(() => notify("success", "resumes", "synced", "Резюме обновлены")));
  $("#chat-new-button")?.addEventListener("click", () => {
    state.chatMessages = [];
    renderChat();
    $("#chat-input")?.focus();
  });
  document.querySelectorAll("[data-analytics-destination]").forEach((button) => button.addEventListener("click", () => {
    activateView("settings");
    switchSettingsSection(button.dataset.analyticsDestination);
  }));
  document.querySelectorAll("[data-help-target]").forEach((button) => button.addEventListener("click", () => switchSettingsSection(button.dataset.helpTarget)));
  $("#help-run-check")?.addEventListener("click", () => Promise.allSettled([loadConfig(), loadResumes(), loadHhConnectionStatus()]).then(() => notify("success", "help", "checked", "Проверка завершена", "Состояние основных подключений обновлено.")));
  $("#copy-diagnostics")?.addEventListener("click", async () => {
    const payload = `Work Hunter\nURL: ${location.href}\nUser agent: ${navigator.userAgent}\nTime: ${new Date().toISOString()}`;
    await navigator.clipboard?.writeText(payload);
    notify("success", "help", "copied", "Диагностика скопирована");
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  setupDestinationPanels();
  setupNavigationAccessibility();
  bindEnhancedSettingsInteractions();
  setupResumeBuilderDefaults();
  let routeApplied = false;
  const initialRoute = window.WorkHunterUI.route.resolve(
    window.location.pathname,
    window.location.search,
    window.location.hash,
    state.sourceKeys,
  );
  state.route = initialRoute;
  syncVacancyControls(initialRoute);
  window.addEventListener("work-hunter:route", (event) => {
    routeApplied = true;
    const resolved = event.detail;
    const view = DESTINATION_TO_VIEW[resolved.destination] || "today";
    activateView(view, { push: false, resolved, load: !state.initialLoadsPending });
  });
  document.addEventListener("click", handleDelegatedUiAction);
  document.addEventListener("click", handleDynamicRecordAction);
  document.addEventListener("click", (event) => {
    const sourceButton = event.target.closest("[data-source-name]");
    if (sourceButton) {
      const action = sourceButton.dataset.sourceAction || "open";
      if (action === "login") {
        startSourceBrowserLogin(sourceButton.dataset.sourceName, sourceButton.dataset.sourceUrl, sourceButton)
          .catch((error) => notifyError(`source-login-${sourceButton.dataset.sourceName}`, error));
      } else {
        openSourceLogin(sourceButton.dataset.sourceName, sourceButton.dataset.sourceUrl);
      }
    }
    const staticLogin = event.target.closest("[data-source-login]");
    if (staticLogin) {
      const meta = SOURCE_META[staticLogin.dataset.sourceLogin];
      startSourceBrowserLogin(staticLogin.dataset.sourceLogin, meta?.url || "", staticLogin)
        .catch((error) => notifyError(`source-login-${staticLogin.dataset.sourceLogin}`, error));
    }
  });
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => activateView(button.dataset.view));
  });
  $("#mobile-menu-button")?.addEventListener("click", openMobileMenu);
  $("#sync-button").addEventListener("click", syncJobs);
  $("#score-button").addEventListener("click", scoreJobs);
  $("#refresh-button").addEventListener("click", loadJobs);
  $("#source-filter").addEventListener("change", loadJobs);
  $("#min-score-filter").addEventListener("change", loadJobs);
  $("#save-config-button").addEventListener("click", saveConfig);
  $("#save-token-button").addEventListener("click", saveHhToken);
  document.querySelectorAll("[data-work-mode], [data-hh-mode]").forEach((button) => {
    button.addEventListener("click", () => setWorkMode(button.dataset.workMode || button.dataset.hhMode));
  });
  $("#work-mode-start")?.addEventListener("click", () => handlePrimaryWorkMode().catch((error) => notifyError("work-mode", error)));
  $("#hh-human-account")?.addEventListener("change", (event) => {
    selectHumanAccount(event.currentTarget.value);
    loadHhAutopilotData().catch((error) => notifyError("hh-autopilot-load", error));
  });
  $("#hh-login-button")?.addEventListener("click", (event) => startHhLogin({
    account: $("#hh-human-account")?.value || "default",
    trigger: event.currentTarget,
  }).catch((error) => notifyError("hh-login", error)));
  $("#source-hh-login")?.addEventListener("click", (event) => startHhLogin({ account: "default", trigger: event.currentTarget }).catch((error) => notifyError("hh-login", error)));
  $("#hh-add-account-button")?.addEventListener("click", (event) => promptForHhAccount(event.currentTarget));
  $("#source-hh-add-account")?.addEventListener("click", (event) => promptForHhAccount(event.currentTarget));
  $("#hh-human-start")?.addEventListener("click", (event) => handleHumanAutopilotStart(event).catch((error) => notifyError("hh-autopilot-start", error)));
  $("#hh-edit-rules-button")?.addEventListener("click", () => switchSettingsSection("search"));
  $("#hh-open-sources-button")?.addEventListener("click", () => activateView("sources"));
  $("#hh-autopilot-account")?.addEventListener("change", () => loadHhAutopilotData().catch((error) => notifyError("hh-autopilot-load", error)));
  $("#hh-autopilot-refresh")?.addEventListener("click", () => loadHhAutopilot().catch((error) => notifyError("hh-autopilot-load", error)));
  $("#hh-autopilot-validate")?.addEventListener("click", () => validateHhAutopilot().catch((error) => notifyError("hh-autopilot-validate", error)));
  $("#hh-autopilot-save-config")?.addEventListener("click", () => saveHhAutopilotConfig().catch((error) => notifyError("hh-autopilot-config", error)));
  $("#hh-autopilot-enable")?.addEventListener("click", (event) => confirmHhAutopilotMutation("enable", "Включить HH Autopilot?", {}, event.currentTarget));
  $("#hh-autopilot-disable")?.addEventListener("click", (event) => confirmHhAutopilotMutation("disable", "Выключить HH Autopilot?", {}, event.currentTarget));
  $("#hh-autopilot-kill")?.addEventListener("click", (event) => confirmHhAutopilotMutation("kill-switch", "Включить kill switch?", {}, event.currentTarget));
  $("#hh-autopilot-clear-kill")?.addEventListener("click", (event) => confirmHhAutopilotMutation("clear-kill-switch", "Снять kill switch?", {}, event.currentTarget));
  $("#hh-autopilot-pause")?.addEventListener("click", (event) => runHhAutopilotMutation("pause", {}, event.currentTarget).catch((error) => notifyError("hh-autopilot-pause", error)));
  $("#hh-autopilot-resume")?.addEventListener("click", (event) => runHhAutopilotMutation("resume", {}, event.currentTarget).catch((error) => notifyError("hh-autopilot-resume", error)));
  $("#hh-autopilot-run-now")?.addEventListener("click", (event) => runHhAutopilotMutation("run-now", {}, event.currentTarget).catch((error) => notifyError("hh-autopilot-run", error)));
  $("#hh-autopilot-recover")?.addEventListener("click", (event) => runHhAutopilotMutation("recover-now", {}, event.currentTarget).catch((error) => notifyError("hh-autopilot-recover", error)));
  $("#hh-autopilot-stop")?.addEventListener("click", (event) => runHhAutopilotMutation("stop", { run_id: Number($("#hh-autopilot-run-id").value) }, event.currentTarget).catch((error) => notifyError("hh-autopilot-stop", error)));
  $("#hh-autopilot-shadow")?.addEventListener("click", (event) => runHhAutopilotMutation("shadow", {
    resume_id: $("#hh-autopilot-shadow-resume").value.trim() || undefined,
    preset: $("#hh-autopilot-shadow-preset").value.trim() || undefined,
  }, event.currentTarget).catch((error) => notifyError("hh-autopilot-shadow", error)));
  $("#hh-autopilot-canary")?.addEventListener("click", (event) => {
    const extra = {
      resume_id: $("#hh-autopilot-canary-resume").value.trim(),
      vacancy_id: $("#hh-autopilot-canary-vacancy").value.trim(),
    };
    if (!extra.resume_id || !extra.vacancy_id) {
      notify("warning", "hh-autopilot", "canary-target", "Укажите resume id и vacancy id");
      return;
    }
    confirmHhAutopilotMutation("canary", "Запустить точный canary-отклик?", extra, event.currentTarget);
  });
  $("#hh-autopilot-history-more")?.addEventListener("click", () => loadMoreHhAutopilotHistory().catch((error) => notifyError("hh-autopilot-history", error)));
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
  document.querySelectorAll("[data-chat-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      $("#chat-input").value = button.dataset.chatPrompt;
      $("#chat-input").focus();
    });
  });
  $("#theme-toggle-button")?.addEventListener("click", toggleTheme);
  $("#save-auto-sync-button")?.addEventListener("click", saveAutoSync);
  $("#ai-search-button")?.addEventListener("click", aiSearch);
  $("#export-csv-button")?.addEventListener("click", exportCsv);
  $("#filter-remote-only")?.addEventListener("change", applySmartFilters);
  $("#filter-with-salary")?.addEventListener("change", applySmartFilters);
  $("#filter-level")?.addEventListener("change", applySmartFilters);
  $("#refresh-stats-button")?.addEventListener("click", loadStats);
  $("#calendar-prev-month")?.addEventListener("click", () => {
    state.calendarCursor = new Date(state.calendarCursor.getFullYear(), state.calendarCursor.getMonth() - 1, 1);
    renderCalendarBoard();
  });
  $("#calendar-next-month")?.addEventListener("click", () => {
    state.calendarCursor = new Date(state.calendarCursor.getFullYear(), state.calendarCursor.getMonth() + 1, 1);
    renderCalendarBoard();
  });
  $("#calendar-today")?.addEventListener("click", () => {
    state.calendarCursor = new Date();
    renderCalendarBoard();
  });
  $("#agent-refresh-button")?.addEventListener("click", () => loadAgentCockpit());
  $("#agent-live-auth-button")?.addEventListener("click", checkAgentLiveAuth);
  $("#agent-digest-button")?.addEventListener("click", loadAgentDigest);
  $("#agent-save-template-button")?.addEventListener("click", saveAgentTemplate);
  $("#agent-save-blacklist-button")?.addEventListener("click", saveAgentBlacklist);
  $("#agent-resume-preview-button")?.addEventListener("click", previewAgentResumeTemplate);
  $("#agent-batch-matrix-button")?.addEventListener("click", buildAgentBatchMatrix);
  $("#agent-chats-refresh-button")?.addEventListener("click", loadAgentChats);
  $("#agent-chats-login-button")?.addEventListener("click", startAgentChatLogin);
  $("#agent-chats-plan-button")?.addEventListener("click", planAgentChatReplies);
  $("#agent-chats-send-button")?.addEventListener("click", sendAgentChatReplies);
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
  document.querySelectorAll("[data-applications-tab]").forEach(tab => {
    tab.addEventListener("click", () => switchApplicationsTab(tab.dataset.applicationsTab));
  });
  document.querySelectorAll("[data-analytics-tab]").forEach(tab => {
    tab.addEventListener("click", () => switchAnalyticsTab(tab.dataset.analyticsTab));
  });
  document.querySelectorAll("[data-settings-tab]").forEach(tab => {
    tab.addEventListener("click", () => switchSettingsSection(tab.dataset.settingsTab));
  });
  bindRovingTablist("[data-applications-tab]", "applicationsTab", switchApplicationsTab);
  bindRovingTablist("[data-analytics-tab]", "analyticsTab", switchAnalyticsTab);
  bindRovingTablist("[data-settings-tab]", "settingsTab", switchSettingsSection);

  setWorkMode(state.workMode);

  loadTheme();
  loadLocalUiPreferences();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js");
  }
  await Promise.allSettled([
    runIsolatedLoad("jobs", loadJobs),
    runIsolatedLoad("config", loadConfig),
    runIsolatedLoad("hh-autopilot", loadHhAutopilot),
    runIsolatedLoad("profile", loadProfile),
    runIsolatedLoad("sources", loadSources),
  ]);
  if (!routeApplied) window.WorkHunterUI.route.syncFromLocation(state.sourceKeys);
  state.initialLoadsPending = false;
  if (state.routeNeedsReload) {
    state.routeNeedsReload = false;
    activateView(DESTINATION_TO_VIEW[state.route.destination] || "today", {
      push: false,
      resolved: state.route,
    });
  } else if (["applications", "analytics"].includes(state.route?.destination)) {
    activateView(DESTINATION_TO_VIEW[state.route.destination], {
      push: false,
      resolved: state.route,
    });
  }
  window.appOnboarding = window.WorkHunterUI.onboarding.createController({
    api,
    notifications: window.appNotifications,
    overlays: window.appOverlays,
  });
  window.appGuidance = window.WorkHunterUI.onboarding.createGuidanceController({
    overlays: window.appOverlays,
  });
  loadResumes().catch(() => {});
  loadEvents().catch(() => {});
  loadSearches().catch(() => {});
  startSearchAlerts();
  window.appOnboarding.boot()
    .then(() => {
      if (routeViewFromPath(window.location.pathname) === "today") return loadToday();
      return null;
    })
    .catch((error) => {
      notifyError("onboarding", error, "Не удалось проверить первоначальную настройку");
    });
});

let editingResumeId = 0;

function resetResumeForm() {
  editingResumeId = 0;
  $("#resume-name-input").value = "";
  $("#resume-body-input").value = "";
}

function showResumeForm(id = 0) {
  editingResumeId = id ? requirePositiveInteger(id, "resume id") : 0;
  const resume = state.resumes.find((item) => Number(item.id) === editingResumeId);
  $("#resume-name-input").value = resume?.name || "";
  $("#resume-body-input").value = resume?.body || "";
  $("#save-resume-button").textContent = editingResumeId ? "Обновить" : "Сохранить";
  $("#resume-form").style.display = "block";
  $("#resume-name-input").focus();
}

function resumePayloadFromForm() {
  const original = state.resumes.find((item) => Number(item.id) === editingResumeId);
  return {
    id: editingResumeId,
    name: $("#resume-name-input").value.trim(),
    body: $("#resume-body-input").value,
    profile_id: original?.profile_id || state.profile?.active || "default",
    is_active: original?.is_active === true,
    ats_score: original?.ats_score ?? null,
  };
}

function hideResumeForm() {
  $("#resume-form").style.display = "none";
  resetResumeForm();
}

async function saveResume() {
  const body = resumePayloadFromForm();
  try {
    await api("/api/resumes", { method: "POST", body: JSON.stringify(body) });
    hideResumeForm();
    await loadResumes();
  } catch (e) { notifyError("resume-save", e, "Не удалось сохранить резюме"); }
}

async function loadResumes() {
  try {
    const resumes = await api("/api/resumes");
    state.resumes = resumes;
    const humanResume = $("#hh-human-resume");
    if (humanResume) {
      const previous = humanResume.value;
      humanResume.replaceChildren();
      if (!resumes.length) humanResume.add(new Option("Сначала добавьте резюме", ""));
      for (const resume of resumes) {
        humanResume.add(new Option(`${resume.name}${resume.is_active ? " · основное" : ""}`, String(resume.id)));
      }
      if ([...humanResume.options].some((option) => option.value === previous)) humanResume.value = previous;
      else if (resumes.some((resume) => resume.is_active)) humanResume.value = String(resumes.find((resume) => resume.is_active).id);
    }
    const list = $("#resumes-list");
    list.innerHTML = "";
    for (const r of resumes) {
      const resumeId = requirePositiveInteger(r.id, "resume id");
      const score = Number.isFinite(Number(r.ats_score)) ? `${Number(r.ats_score)}%` : "Не проверено";
      list.innerHTML += `<article class="resume-card${r.is_active ? " active" : ""}">
        <span class="resume-select-indicator" aria-hidden="true"><i class="ph ${r.is_active ? "ph-check-circle" : "ph-circle"}"></i></span>
        <div class="resume-card-copy"><div class="resume-card-title"><strong>${escapeHtml(r.name)}</strong>${r.is_active ? '<span class="connection-state">Основное</span>' : ""}</div>
        <span class="meta">Локальное резюме · Профиль ${escapeHtml(r.profile_id || state.profile?.active || "default")}</span>
        <div class="resume-card-meta"><span><i class="ph ph-chart-line-up" aria-hidden="true"></i>ATS: ${escapeHtml(score)}</span><span><i class="ph ph-link" aria-hidden="true"></i>Готово для площадок</span></div>
        <div class="resume-card-actions"><button aria-label="Сделать основным ${escapeAttr(r.name)}" ${numericRecordAction("activate-resume", resumeId, "resume id")}><i class="ph ph-check-circle" aria-hidden="true"></i>${r.is_active ? "Основное" : "Сделать основным"}</button><button aria-label="Редактировать резюме ${escapeAttr(r.name)}" ${numericRecordAction("edit-resume", resumeId, "resume id")}><i class="ph ph-pencil-simple" aria-hidden="true"></i>Редактировать</button><button class="danger-action" aria-label="Удалить резюме ${escapeAttr(r.name)}" ${numericRecordAction("delete-resume", resumeId, "resume id")}><i class="ph ph-trash" aria-hidden="true"></i>Удалить</button></div></div>
      </article>`;
    }
    if (!resumes.length) {
      list.innerHTML = '<div class="designed-empty"><span class="metric-icon blue"><i class="ph ph-file-plus" aria-hidden="true"></i></span><strong>Добавьте первое резюме</strong><p>Оно понадобится для автооткликов и заполнения форм.</p></div>';
    }
    const primary = resumes.find((resume) => resume.is_active) || resumes[0];
    const total = $("#resumes-total-count");
    const primaryName = $("#resumes-primary-name");
    if (total) total.textContent = String(resumes.length);
    if (primaryName) primaryName.textContent = primary?.name || "Не выбрано";
    const preview = $("#resume-preview-card");
    if (preview && primary) {
      preview.innerHTML = `<span class="metric-icon blue"><i class="ph ph-file-text" aria-hidden="true"></i></span><span class="eyebrow">Основное резюме</span><h3>${escapeHtml(primary.name)}</h3><p>${escapeHtml((primary.body || "Текст резюме пока не добавлен.").slice(0, 240))}</p><dl><div><dt>ATS</dt><dd>${escapeHtml(String(primary.ats_score ?? "—"))}</dd></div><div><dt>Профиль</dt><dd>${escapeHtml(primary.profile_id || state.profile?.active || "default")}</dd></div></dl><button type="button" ${numericRecordAction("edit-resume", requirePositiveInteger(primary.id, "resume id"), "resume id")}><i class="ph ph-pencil-simple" aria-hidden="true"></i>Открыть резюме</button>`;
    }
  } catch (e) { console.error(e); }
}

async function activateResume(id) {
  const resumeId = requirePositiveInteger(id, "resume id");
  await api(`/api/resumes/${resumeId}/activate`, { method: "POST", body: "{}" });
  await loadResumes();
}

async function deleteResume(id) {
  const resumeId = requirePositiveInteger(id, "resume id");
  const resume = state.resumes.find((item) => Number(item.id) === resumeId);
  const fingerprint = stableActionFingerprint({ resumeId, name: resume?.name || "" });
  let descriptor;
  descriptor = {
    operationType: "resume_account",
    title: "Удалить резюме?",
    consequence: "Резюме будет удалено из Work Hunter. Это действие нельзя отменить.",
    targetRows: [
      { key: "resume_or_account", label: "Резюме", safeValue: resume?.name || `#${resumeId}` },
      { key: "changes", label: "Изменение", safeValue: "Удаление локального резюме" },
    ],
    riskFlags: [{ code: "destructive", safeMessage: "Удаление необратимо." }],
    acknowledgement: "Я понимаю, что резюме будет удалено без возможности восстановления",
    confirmLabel: "Удалить резюме",
    fingerprint,
    trigger: document.activeElement,
    revalidate: async () => validateResumeAccountMutation(descriptor, async () => {
      const profileId = encodeURIComponent(state.profile?.active || "default");
      const current = await api(`/api/resumes?profile_id=${profileId}`);
      const exists = current.some((item) => Number(item.id) === resumeId);
      return {
        status: exists ? "executable" : "blocked",
        fingerprint,
        blockers: exists ? [] : [{ code: "missing_resume", safeMessage: "Резюме уже удалено." }],
        canExecute: exists,
      };
    }),
    execute: async (confirm) => {
      if (confirm !== true) return { status: "blocked", message: "Подтверждение не получено." };
      const result = await api(`/api/resumes/${resumeId}/delete`, { method: "POST", body: "{}" });
      await loadResumes();
      return result;
    },
  };
  openLiveAction(descriptor);
}

let editingEventId = 0;

function showEventForm(id = 0) {
  editingEventId = id ? requirePositiveInteger(id, "event id") : 0;
  const event = state.calendarEvents.find((item) => Number(item.id) === editingEventId);
  $("#event-form").style.display = "block";
  $("#event-form-title").textContent = editingEventId ? "Редактировать событие" : "Новое событие";
  if (event) {
    $("#event-title-input").value = event.title || "";
    $("#event-type-select").value = event.event_type || "interview";
    $("#event-date-input").value = String(event.event_date || "").slice(0, 16);
    $("#event-job-input").value = event.job_id || "";
    $("#event-notes-input").value = event.notes || "";
  } else {
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
  } catch (e) { notifyError("event-save", e, "Не удалось сохранить событие"); }
}

function calendarDateKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function renderCalendarBoard(events = state.calendarEvents) {
  const grid = $("#calendar-month-grid");
  const list = $("#events-list");
  if (!grid || !list) return;
  const cursor = new Date(state.calendarCursor);
  cursor.setDate(1);
  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const monthLabel = $("#calendar-month-label");
  if (monthLabel) monthLabel.textContent = cursor.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  const byDay = new Map();
  for (const event of events) {
    const key = calendarDateKey(event.event_date);
    if (!key) continue;
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(event);
  }
  const startOffset = (new Date(year, month, 1).getDay() + 6) % 7;
  const firstCell = new Date(year, month, 1 - startOffset);
  const todayKey = calendarDateKey(new Date());
  grid.replaceChildren();
  for (let index = 0; index < 42; index += 1) {
    const date = new Date(firstCell);
    date.setDate(firstCell.getDate() + index);
    const key = calendarDateKey(date);
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "calendar-day";
    if (date.getMonth() !== month) cell.classList.add("outside");
    if (key === todayKey) cell.classList.add("today");
    cell.dataset.calendarDate = key;
    const dayEvents = byDay.get(key) || [];
    cell.innerHTML = `<span class="calendar-day-number">${date.getDate()}</span><span class="calendar-day-events">${dayEvents.slice(0, 3).map((event) => `<i class="calendar-event ${escapeAttr(event.event_type || "other")}">${escapeHtml(event.title || "Событие")}</i>`).join("")}</span>`;
    cell.addEventListener("click", () => {
      document.querySelectorAll(".calendar-day.selected").forEach((item) => item.classList.remove("selected"));
      cell.classList.add("selected");
      renderCalendarAgenda(dayEvents, date);
    });
    grid.append(cell);
  }
  const todayEvents = byDay.get(todayKey) || events.filter((event) => new Date(event.event_date) >= new Date()).slice(0, 8);
  renderCalendarAgenda(todayEvents, new Date());
}

function renderCalendarAgenda(events, date) {
  const list = $("#events-list");
  const title = $("#calendar-agenda-title");
  if (!list || !title) return;
  title.textContent = date.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  list.replaceChildren();
  if (!events.length) {
    list.innerHTML = '<p class="meta">На этот день ничего не запланировано.</p>';
    return;
  }
  for (const event of events) {
    const eventId = requirePositiveInteger(event.id, "event id");
    const eventDate = new Date(event.event_date);
    const row = document.createElement("article");
    row.className = "calendar-agenda-row";
    row.dataset.eventId = String(eventId);
    row.innerHTML = `
      <time>${escapeHtml(eventDate.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }))}</time>
      <div><strong>${escapeHtml(event.title || "Событие")}</strong><span>${escapeHtml(event.notes || event.event_type || "")}</span></div>
      <button ${numericRecordAction("edit-event", eventId, "event id")}>Изменить</button>`;
    list.append(row);
  }
}

async function loadEvents() {
  try {
    const events = await api("/api/events");
    state.calendarEvents = events;
    renderCalendarBoard(events);
    if (state.route?.destination === "calendar") {
      const eventId = new URLSearchParams(state.route.query).get("event");
      if (eventId && events.some((event) => Number(event.id) === Number(eventId))) {
        showEventForm(eventId);
      }
    }
  } catch (e) { console.error(e); }
}

async function deleteEvent(id) {
  const eventId = requirePositiveInteger(id, "event id");
  await api(`/api/events/${eventId}/delete`, { method: "POST", body: "{}" });
  await loadEvents();
}

let selectedJobIds = new Set();

function toggleSelectAll() {
  const checked = $("#select-all-checkbox").checked;
  selectedJobIds.clear();
  document.querySelectorAll(".job-checkbox").forEach(cb => {
    cb.checked = checked;
    if (checked) selectedJobIds.add(requirePositiveInteger(cb.dataset.jobId, "job id"));
  });
  updateBulkToolbar();
}

function toggleJobSelect(jobId, checked) {
  const id = requirePositiveInteger(jobId, "job id");
  if (checked) selectedJobIds.add(id);
  else selectedJobIds.delete(id);
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
    cb.checked = selectedJobIds.has(requirePositiveInteger(cb.dataset.jobId, "job id"));
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
  } catch (e) { notifyError("bulk-action", e); }
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
    const jobId = requirePositiveInteger(job.id, "job id");
    const tr = document.createElement("tr");
    tr.className = jobId === state.selectedId ? "selected" : "";
    bindJobRowActivation(tr, job);
    const score = job.score ? job.score.total_score : "-";
    const scoreClass = score === "-" ? "" : score >= 70 ? " high" : score < 35 ? " low" : "";
    const checked = selectedJobIds.has(jobId) ? "checked" : "";
    tr.innerHTML = `
      <td><input type="checkbox" class="job-checkbox" data-job-id="${jobId}" data-record-action="toggle-job-select" data-record-id="${jobId}" ${checked}></td>
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
  } catch (e) { notifyError("search-save", e, "Не удалось сохранить поиск"); }
}

async function loadSearches() {
  try {
    const searches = await api("/api/saved-searches");
    const list = $("#searches-list");
    list.innerHTML = "";
    if (!searches.length) { list.innerHTML = '<p class="meta">Нет сохранённых поисков.</p>'; return; }
    for (const s of searches) {
      const searchId = requirePositiveInteger(s.id, "search id");
      list.innerHTML += `<div class="source-row">
        <strong>${escapeHtml(s.name)}</strong>
        <div class="meta">${escapeHtml(s.query)} · alert: ${s.alert_enabled ? "on" : "off"}</div>
        <button ${numericRecordAction("delete-search", searchId, "search id")} style="margin-top:4px;">Удалить</button>
      </div>`;
    }
  } catch (e) { console.error(e); }
}

async function deleteSearch(id) {
  const searchId = requirePositiveInteger(id, "search id");
  await api(`/api/saved-searches/${searchId}/delete`, { method: "POST", body: "{}" });
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
    const output = $("#trends-output");
    output.classList.add("error");
    output.textContent = `Ошибка: ${e.message}`;
  }
}

async function parseJobStructure() {
  if (!state.selectedId) return;
  setBusy('[data-ui-action="parse-job-structure"]', true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/parse-structure`, { method: "POST", body: "{}" });
    const output = $("#resume-tips-output");
    output.textContent = JSON.stringify(result, null, 2);
  } catch (e) {
    notifyError("job-structure", e, "Не удалось разобрать вакансию");
  } finally {
    setBusy('[data-ui-action="parse-job-structure"]', false);
  }
}

async function runGapAnalysis() {
  if (!state.selectedId) return;
  const resumes = await api("/api/resumes");
  const active = resumes.find(r => r.is_active);
  if (!active) {
    notify("warning", "gap-analysis", "resume-required", "Нужно активное резюме", "Создай и активируй резюме в настройках.");
    return;
  }
  setBusy('[data-ui-action="run-gap-analysis"]', true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/gap-analysis`, {
      method: "POST", body: JSON.stringify({ resume_id: active.id }),
    });
    $("#resume-tips-output").innerHTML = renderMarkdown(result.content);
  } catch (e) {
    notifyError("gap-analysis", e, "Не удалось выполнить gap-анализ");
  } finally {
    setBusy('[data-ui-action="run-gap-analysis"]', false);
  }
}

async function scoreAtsResume() {
  const text = $("#resume-body-input")?.value || $("#audit-resume-input")?.value;
  if (!text) {
    notify("warning", "ats-score", "resume-text-required", "Добавь текст резюме");
    return;
  }
  try {
    const result = await api("/api/resumes/ats-score", { method: "POST", body: JSON.stringify({ resume_text: text }) });
    notify("info", "ats-score", "result", `ATS Score: ${result.score}/100`, (result.issues || []).join(", "));
  } catch (e) { notifyError("ats-score", e, "Не удалось оценить резюме"); }
}

async function smartClassify() {
  if (!state.selectedId) return;
  try {
    const result = await api(`/api/jobs/${state.selectedId}/smart-classify`, { method: "POST", body: "{}" });
    notify("info", "smart-classify", "result", "Классификация готова", JSON.stringify(result));
  } catch (e) { notifyError("smart-classify", e, "Не удалось классифицировать вакансию"); }
}

async function getInterviewPrep(stage) {
  if (!state.selectedId) return;
  try {
    const result = await api(`/api/jobs/${state.selectedId}/interview-prep`, {
      method: "POST", body: JSON.stringify({ stage }),
    });
    $("#ai-fit-reasoning").innerHTML = renderMarkdown(result.content);
    switchAiTab("interview");
  } catch (e) { notifyError("interview-prep", e, "Не удалось подготовить материалы"); }
}

async function getBehaviorSuggestions() {
  try {
    const result = await api("/api/behavior/suggest", { method: "POST", body: "{}" });
    notify("info", "behavior-suggestions", "result", "Рекомендации готовы", result.content);
  } catch (e) { notifyError("behavior-suggestions", e, "Не удалось получить рекомендации"); }
}

async function loadGhostJobs() {
  try {
    const jobs = await api("/api/ghost-jobs?days=7");
    const list = $("#ghost-jobs-list");
    list.innerHTML = "";
    if (!jobs.length) { list.innerHTML = '<p class="meta">Призраков нет!</p>'; return; }
    for (const j of jobs) {
      const jobId = requirePositiveInteger(j.id, "job id");
      list.innerHTML += `<div class="source-row">
        <strong>${escapeHtml(j.title)}</strong>
        <div class="meta">${escapeHtml(j.company || "?")} · ${escapeHtml(j.source)}</div>
        <button aria-label="Отметить ghosted: ${escapeAttr(j.title)}" data-record-action="mark-ghost-job" data-record-id="${jobId}" data-job-id="${jobId}" style="margin-top:4px;">Отметить ghosted</button>
      </div>`;
    }
  } catch (e) { console.error(e); }
}

async function markGhostJob(jobId) {
  const id = await updateJobStatus(jobId, "ghosted");
  await loadJobs();
  if (Number(state.selectedId) === id) {
    await selectJob(id);
  }
  await loadGhostJobs();
}

function shareToTelegram() {
  if (!state.selectedId) return;
  const job = state.jobs.find(j => j.id === state.selectedId);
  if (!job) return;
  const text = `${job.title}\n${job.company || ""}\n${job.salary_text || ""}\n${job.url}`;
  window.open(`https://t.me/share/url?url=${encodeURIComponent(job.url)}&text=${encodeURIComponent(text)}`, "_blank");
}
