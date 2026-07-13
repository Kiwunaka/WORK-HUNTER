(function onboardingModule(global) {
  "use strict";

  const UI = global.WorkHunterUI || (global.WorkHunterUI = {});
  const PERMANENT_KEY = "work-hunter:onboarding:v2";
  const SESSION_KEY = "work-hunter:guidance-session:v2";
  const VERSION = 2;
  const STEPS = ["goal", "sources", "resume"];

  function defaultProgress() {
    return {
      version: VERSION,
      completed: false,
      completedSteps: [],
      pendingResumeActivationId: null,
      finalization: {
        syncStatus: "not_started",
        syncCompletedAt: null,
        error: null,
      },
    };
  }

  function defaultSession() {
    return { onboardingDeferred: false, forceReview: false };
  }

  function safeJson(storage, key, fallback) {
    try {
      const parsed = JSON.parse(storage.getItem(key) || "null");
      return parsed && typeof parsed === "object" ? parsed : fallback;
    } catch (_error) {
      return fallback;
    }
  }

  function parseProgress(storage = global.localStorage) {
    const raw = safeJson(storage, PERMANENT_KEY, null);
    if (!raw || raw.version !== VERSION) return defaultProgress();
    const completedSteps = Array.isArray(raw.completedSteps)
      ? [...new Set(raw.completedSteps.filter((step) => STEPS.includes(step)))]
      : [];
    const pending = Number(raw.pendingResumeActivationId);
    const syncStatuses = new Set(["not_started", "running", "succeeded", "failed"]);
    const finalization = raw.finalization && typeof raw.finalization === "object"
      ? raw.finalization
      : {};
    return {
      version: VERSION,
      completed: raw.completed === true,
      completedSteps,
      pendingResumeActivationId: Number.isSafeInteger(pending) && pending > 0 ? pending : null,
      finalization: {
        syncStatus: syncStatuses.has(finalization.syncStatus) ? finalization.syncStatus : "not_started",
        syncCompletedAt: typeof finalization.syncCompletedAt === "string" ? finalization.syncCompletedAt : null,
        error: typeof finalization.error === "string" ? finalization.error : null,
      },
    };
  }

  function parseSession(storage = global.sessionStorage) {
    const raw = safeJson(storage, SESSION_KEY, null);
    return {
      onboardingDeferred: raw?.onboardingDeferred === true,
      forceReview: raw?.forceReview === true,
    };
  }

  function persist(storage, key, value) {
    storage.setItem(key, JSON.stringify(value));
  }

  function deriveReadiness({ profile, config, resumes }) {
    const resources = [profile, config, resumes];
    if (resources.some((item) => item?.status === "loading")) return "loading";
    if (resources.some((item) => item?.status === "error")) return "unknown";
    const profileInfo = profile?.data || {};
    const profileData = profileInfo.data || {};
    const profiles = config?.data?.profiles || {};
    if (!profileInfo.active || !profiles[profileInfo.active]) return "unknown";
    const hasGoal = [...(profileData.desired_roles || []), ...(profileData.queries || [])]
      .some((value) => String(value).trim());
    const hasSource = Object.values(config?.data?.sources || {})
      .some((source) => source?.enabled === true);
    const hasResume = (resumes?.data || []).some(
      (resume) => resume.profile_id === profileInfo.active && resume.is_active === true,
    );
    return hasGoal && hasSource && hasResume ? "ready" : "incomplete";
  }

  function missingDomainStep(resources) {
    const profileInfo = resources.profile.data;
    const profileData = profileInfo.data || {};
    const hasGoal = [...(profileData.desired_roles || []), ...(profileData.queries || [])]
      .some((value) => String(value).trim());
    if (!hasGoal) return "goal";
    const hasSource = Object.values(resources.config.data.sources || {})
      .some((source) => source?.enabled === true);
    if (!hasSource) return "sources";
    const hasResume = resources.resumes.data.some(
      (resume) => resume.profile_id === profileInfo.active && resume.is_active === true,
    );
    return hasResume ? null : "resume";
  }

  function nextOnboardingState({ readiness, serverVersion, progress, session, resources }) {
    if (readiness === "loading" || readiness === "unknown") return { kind: readiness };
    if (session.onboardingDeferred) return { kind: "today", guidance: readiness === "ready" };
    if (session.forceReview || serverVersion < VERSION) {
      const step = STEPS.find((id) => !progress.completedSteps.includes(id));
      return step ? { kind: "step", step } : { kind: "summary", forced: session.forceReview };
    }
    if (readiness === "incomplete") {
      return { kind: "step", step: missingDomainStep(resources) || "goal" };
    }
    return { kind: "today", guidance: true };
  }

  function splitLines(value) {
    return String(value || "").split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function createController({ api, notifications, overlays, onResources } = {}) {
    const readinessBanner = document.querySelector("#readiness-banner");
    let progress = parseProgress();
    let session = parseSession();
    let resources = {
      profile: { status: "loading" },
      config: { status: "loading" },
      resumes: { status: "loading" },
    };
    let readiness = "loading";
    let panel = null;
    let currentStep = null;
    let requestLocked = false;
    let forcedReview = false;

    function saveProgress() {
      persist(global.localStorage, PERMANENT_KEY, progress);
    }

    function saveSession() {
      persist(global.sessionStorage, SESSION_KEY, session);
    }

    function markStep(step) {
      if (!progress.completedSteps.includes(step)) {
        progress.completedSteps.push(step);
        saveProgress();
      }
    }

    function setInlineError(message) {
      const box = panel?.querySelector("[data-onboarding-error]");
      if (!box) return;
      box.textContent = message || "";
      box.hidden = !message;
    }

    function setBusy(button, busy, busyText = "Сохраняю…") {
      if (!button) return;
      if (busy) {
        button.dataset.idleText = button.textContent;
        button.textContent = busyText;
      } else {
        button.textContent = button.dataset.idleText || button.textContent;
      }
      button.disabled = busy;
    }

    function renderReadiness(error = null) {
      if (!readinessBanner) return;
      readinessBanner.dataset.readinessState = readiness;
      readinessBanner.replaceChildren();
      if (readiness === "ready" || readiness === "loading") {
        readinessBanner.hidden = true;
        return;
      }
      readinessBanner.hidden = false;
      const content = element("div", "readiness-banner-copy");
      content.append(
        element("strong", "", readiness === "unknown" ? "Не удалось проверить готовность" : "Настройка не завершена"),
        element("span", "", readiness === "unknown"
          ? "Часть данных не загрузилась. Ничего не было сброшено."
          : "Добавьте цель, источник и активное резюме, чтобы начать поиск."),
      );
      const action = element("button", readiness === "unknown" ? "" : "primary", readiness === "unknown" ? "Повторить" : "Продолжить настройку");
      action.type = "button";
      action.addEventListener("click", async () => {
        session.onboardingDeferred = false;
        saveSession();
        await boot();
      });
      readinessBanner.append(content, action);
      if (error) readinessBanner.title = String(error.message || error);
    }

    async function loadResources() {
      resources = {
        profile: { status: "loading" },
        config: { status: "loading" },
        resumes: { status: "loading" },
      };
      readiness = "loading";
      renderReadiness();
      const [profileResult, configResult] = await Promise.allSettled([
        api("/api/profile"),
        api("/api/config"),
      ]);
      resources.profile = profileResult.status === "fulfilled"
        ? { status: "ready", data: profileResult.value, stale: false }
        : { status: "error", error: profileResult.reason, stale: false };
      resources.config = configResult.status === "fulfilled"
        ? { status: "ready", data: configResult.value, stale: false }
        : { status: "error", error: configResult.reason, stale: false };

      if (resources.profile.status === "ready") {
        const profileId = encodeURIComponent(resources.profile.data.active || "default");
        try {
          resources.resumes = {
            status: "ready",
            data: await api(`/api/resumes?profile_id=${profileId}`),
            stale: false,
          };
        } catch (error) {
          resources.resumes = { status: "error", error, stale: false };
        }
      } else {
        resources.resumes = { status: "error", error: new Error("profile unavailable"), stale: false };
      }

      if (progress.pendingResumeActivationId && resources.resumes.status === "ready") {
        const pending = resources.resumes.data.find(
          (resume) => Number(resume.id) === progress.pendingResumeActivationId,
        );
        if (!pending) {
          progress.pendingResumeActivationId = null;
          saveProgress();
        } else if (!pending.is_active) {
          try {
            await api(`/api/resumes/${pending.id}/activate`, { method: "POST", body: "{}" });
            const profileId = encodeURIComponent(resources.profile.data.active);
            resources.resumes.data = await api(`/api/resumes?profile_id=${profileId}`);
            const activated = resources.resumes.data.some(
              (resume) => Number(resume.id) === Number(pending.id) && resume.is_active === true,
            );
            if (activated) {
              progress.pendingResumeActivationId = null;
              markStep("resume");
              saveProgress();
            }
          } catch (_error) {
            // Keep the pending id so a reload retries activation without creating a duplicate.
          }
        } else {
          progress.pendingResumeActivationId = null;
          markStep("resume");
          saveProgress();
        }
      }

      readiness = deriveReadiness(resources);
      const firstError = [resources.profile, resources.config, resources.resumes]
        .find((item) => item.status === "error")?.error;
      renderReadiness(firstError);
      onResources?.(resources);
      return resources;
    }

    function commonFrame(step, title, detail) {
      panel.replaceChildren();
      panel.setAttribute("data-onboarding-step", step);
      panel.classList.add("onboarding-sheet");
      const header = element("header", "onboarding-header");
      const progressText = step === "summary" ? "Проверка настроек" : `Шаг ${STEPS.indexOf(step) + 1} из 3`;
      header.append(
        element("div", "onboarding-progress", progressText),
        element("h2", "", title),
        element("p", "", detail),
      );
      const main = element("div", "onboarding-body");
      const error = element("div", "onboarding-inline-error");
      error.dataset.onboardingError = "";
      error.setAttribute("role", "alert");
      error.hidden = true;
      const footer = element("footer", "onboarding-footer");
      const defer = element("button", "onboarding-defer", "Настроить позже");
      defer.type = "button";
      defer.dataset.onboardingDefer = "";
      defer.addEventListener("click", deferOnboarding);
      footer.append(defer);
      panel.append(header, main, error, footer);
      return { main, footer };
    }

    function addNext(footer, label = "Продолжить") {
      const next = element("button", "primary", label);
      next.type = "button";
      next.dataset.onboardingNext = "";
      footer.append(next);
      return next;
    }

    function renderGoal() {
      const { main, footer } = commonFrame(
        "goal",
        "Что вы хотите найти?",
        "Уточните роли и ключевые навыки. Это можно изменить позже в настройках.",
      );
      main.innerHTML = `
        <label class="onboarding-field" for="onboarding-roles">
          <span>Желаемые роли</span>
          <textarea id="onboarding-roles" rows="3" aria-describedby="onboarding-roles-help"></textarea>
          <small id="onboarding-roles-help">Одна роль на строку</small>
        </label>
        <label class="onboarding-field" for="onboarding-skills">
          <span>Ключевые навыки</span>
          <input id="onboarding-skills" type="text" placeholder="Python, FastAPI, PostgreSQL">
        </label>`;
      const profile = resources.profile.data.data || {};
      main.querySelector("#onboarding-roles").value = (profile.desired_roles || profile.queries || []).join("\n");
      main.querySelector("#onboarding-skills").value = (profile.must_have_skills || []).join(", ");
      const next = addNext(footer);
      next.addEventListener("click", async () => {
        if (requestLocked) return;
        const roles = splitLines(main.querySelector("#onboarding-roles").value);
        const skills = splitLines(main.querySelector("#onboarding-skills").value);
        if (!roles.length) {
          setInlineError("Добавьте хотя бы одну желаемую роль.");
          return;
        }
        requestLocked = true;
        setBusy(next, true);
        try {
          const updated = await api("/api/profile", {
            method: "POST",
            body: JSON.stringify({
              desired_roles: roles,
              queries: roles,
              must_have_skills: skills,
            }),
          });
          resources.profile.data.data = updated;
          markStep("goal");
          renderStep("sources");
        } catch (error) {
          setInlineError(String(error?.message || error));
        } finally {
          requestLocked = false;
          setBusy(next, false);
        }
      });
    }

    function renderSources() {
      const { main, footer } = commonFrame(
        "sources",
        "Где искать вакансии?",
        "Оставьте хотя бы один источник. HH-токен необязателен для публичного поиска.",
      );
      const list = element("div", "onboarding-source-list");
      const labels = {
        hh: "HeadHunter",
        habr: "Хабр Карьера",
        geekjob: "GeekJob",
        telegram: "Telegram-каналы",
        getmatch: "GetMatch",
        relocate_me: "Relocate.me",
      };
      for (const [key, source] of Object.entries(resources.config.data.sources || {})) {
        const row = element("label", "onboarding-source-row");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = source?.enabled === true;
        checkbox.dataset.sourceKey = key;
        const copy = element("span", "");
        copy.append(
          element("strong", "", labels[key] || key),
          element("small", "", key === "hh" ? "Токен можно добавить позже" : "Публичный источник"),
        );
        row.append(checkbox, copy);
        list.append(row);
      }
      main.append(list);
      const next = addNext(footer);
      next.addEventListener("click", async () => {
        if (requestLocked) return;
        const selected = [...list.querySelectorAll("[data-source-key]:checked")];
        if (!selected.length) {
          setInlineError("Выберите хотя бы один источник.");
          return;
        }
        requestLocked = true;
        setBusy(next, true);
        try {
          const updatedConfig = structuredClone(resources.config.data);
          for (const checkbox of list.querySelectorAll("[data-source-key]")) {
            const key = checkbox.dataset.sourceKey;
            updatedConfig.sources[key].enabled = checkbox.checked;
          }
          resources.config.data = await api("/api/config", {
            method: "POST",
            body: JSON.stringify(updatedConfig),
          });
          markStep("sources");
          renderStep("resume");
        } catch (error) {
          setInlineError(String(error?.message || error));
        } finally {
          requestLocked = false;
          setBusy(next, false);
        }
      });
    }

    function renderResume() {
      const { main, footer } = commonFrame(
        "resume",
        "Выберите основное резюме",
        "Оно будет использоваться для оценки соответствия и подготовки откликов.",
      );
      const resumes = resources.resumes.data || [];
      if (resumes.length) {
        const list = element("div", "onboarding-resume-list");
        for (const resume of resumes) {
          const row = element("label", "onboarding-resume-row");
          const radio = document.createElement("input");
          radio.type = "radio";
          radio.name = "onboarding-resume";
          radio.value = String(resume.id);
          radio.checked = resume.is_active === true || (!list.childElementCount && !resumes.some((item) => item.is_active));
          const copy = element("span", "");
          copy.append(
            element("strong", "", resume.name || `Резюме #${resume.id}`),
            element("small", "", resume.is_active ? "Активно" : "Можно сделать активным"),
          );
          row.append(radio, copy);
          list.append(row);
        }
        main.append(list);
      } else {
        main.innerHTML = `
          <label class="onboarding-field" for="onboarding-resume-name">
            <span>Название</span>
            <input id="onboarding-resume-name" type="text" placeholder="Основное резюме">
          </label>
          <label class="onboarding-field" for="onboarding-resume-body">
            <span>Текст резюме</span>
            <textarea id="onboarding-resume-body" rows="8" aria-describedby="onboarding-resume-help"></textarea>
            <small id="onboarding-resume-help">Вставьте текст — форматирование можно доработать позже</small>
          </label>`;
      }
      const next = addNext(footer, "Выбрать резюме");
      next.addEventListener("click", async () => {
        if (requestLocked) return;
        requestLocked = true;
        setBusy(next, true, "Активирую…");
        try {
          let resumeId = progress.pendingResumeActivationId;
          if (!resumeId && resumes.length) {
            resumeId = Number(main.querySelector("input[name='onboarding-resume']:checked")?.value);
          }
          if (!resumeId && !resumes.length) {
            const name = main.querySelector("#onboarding-resume-name").value.trim();
            const body = main.querySelector("#onboarding-resume-body").value.trim();
            if (!name || !body) throw new Error("Заполните название и текст резюме.");
            const created = await api("/api/resumes", {
              method: "POST",
              body: JSON.stringify({
                name,
                body,
                profile_id: resources.profile.data.active,
                is_active: false,
              }),
            });
            resumeId = Number(created.id);
            if (!Number.isSafeInteger(resumeId) || resumeId <= 0) throw new Error("Сервис не вернул ID резюме.");
          }
          if (!resumeId) throw new Error("Выберите резюме.");
          progress.pendingResumeActivationId = resumeId;
          saveProgress();
          await api(`/api/resumes/${resumeId}/activate`, { method: "POST", body: "{}" });
          const profileId = encodeURIComponent(resources.profile.data.active);
          resources.resumes.data = await api(`/api/resumes?profile_id=${profileId}`);
          const active = resources.resumes.data.some(
            (resume) => Number(resume.id) === resumeId && resume.is_active === true,
          );
          if (!active) throw new Error("Не удалось подтвердить активацию резюме.");
          progress.pendingResumeActivationId = null;
          markStep("resume");
          saveProgress();
          renderStep("summary");
        } catch (error) {
          setInlineError(String(error?.message || error));
        } finally {
          requestLocked = false;
          setBusy(next, false);
        }
      });
    }

    function renderSummary() {
      const { main, footer } = commonFrame(
        "summary",
        "Всё готово к первому поиску",
        "Проверьте настройки. Поиск можно повторить в любой момент на экране вакансий.",
      );
      const profile = resources.profile.data.data || {};
      const enabledSources = Object.entries(resources.config.data.sources || {})
        .filter(([, source]) => source?.enabled)
        .map(([key]) => key);
      const activeResume = (resources.resumes.data || []).find((resume) => resume.is_active);
      const summary = element("div", "onboarding-summary");
      for (const [label, value] of [
        ["Цель", (profile.desired_roles || profile.queries || []).join(", ")],
        ["Источники", enabledSources.join(", ")],
        ["Резюме", activeResume?.name || "Выбрано"],
      ]) {
        const row = element("div", "onboarding-summary-row");
        row.append(element("span", "onboarding-check", "✓"), element("strong", "", label), element("span", "", value));
        summary.append(row);
      }
      main.append(summary);
      if (progress.finalization.error) {
        const note = element("div", "onboarding-finalization-note", progress.finalization.error);
        main.append(note);
      }

      const finish = element("button", "primary", forcedReview ? "Завершить обзор" : (
        progress.finalization.syncStatus === "succeeded" ? "Завершить настройку" : "Найти первые вакансии"
      ));
      finish.type = "button";
      finish.dataset.onboardingFinish = "";
      footer.append(finish);
      finish.addEventListener("click", () => finishOnboarding(finish, !forcedReview));

      if (!forcedReview && progress.finalization.syncStatus === "failed") {
        const continueButton = element("button", "", "Продолжить без повторного поиска");
        continueButton.type = "button";
        continueButton.addEventListener("click", () => finishOnboarding(continueButton, false));
        footer.insertBefore(continueButton, finish);
      }
    }

    function renderStep(step) {
      currentStep = step;
      if (step === "goal") renderGoal();
      else if (step === "sources") renderSources();
      else if (step === "resume") renderResume();
      else renderSummary();
    }

    function openWizard(step) {
      currentStep = step;
      const result = overlays.request({
        kind: "sheet",
        label: "Настройка Work Hunter",
        trigger: document.querySelector("#readiness-banner button") || document.activeElement,
        dismissible: false,
        render(root) {
          panel = root;
          renderStep(step);
        },
      });
      if (!result.accepted) {
        notifications?.push({
          type: "warning",
          scope: "onboarding",
          code: result.reason,
          title: "Сначала закройте открытое окно",
        });
      }
      return result;
    }

    function deferOnboarding() {
      session.onboardingDeferred = true;
      session.forceReview = false;
      saveSession();
      overlays.closeBlocking();
      panel = null;
      currentStep = null;
      renderReadiness();
    }

    async function finishOnboarding(button, runSync) {
      if (requestLocked) return;
      requestLocked = true;
      setBusy(button, true, runSync ? "Ищу вакансии…" : "Завершаю…");
      try {
        if (forcedReview) {
          session.forceReview = false;
          progress.completed = true;
          saveSession();
          saveProgress();
          overlays.closeBlocking();
          return;
        }
        if (runSync && progress.finalization.syncStatus !== "succeeded") {
          progress.finalization = { syncStatus: "running", syncCompletedAt: null, error: null };
          saveProgress();
          try {
            await api("/api/sync", { method: "POST", body: JSON.stringify({ score: true }) });
            progress.finalization = {
              syncStatus: "succeeded",
              syncCompletedAt: new Date().toISOString(),
              error: null,
            };
            saveProgress();
          } catch (error) {
            progress.finalization = {
              syncStatus: "failed",
              syncCompletedAt: null,
              error: `Первый поиск не завершён: ${String(error?.message || error)}`,
            };
            saveProgress();
            renderSummary();
            return;
          }
        }
        const updatedConfig = structuredClone(resources.config.data);
        updatedConfig.ui = { ...(updatedConfig.ui || {}), onboarding_version: VERSION };
        try {
          resources.config.data = await api("/api/config", {
            method: "POST",
            body: JSON.stringify(updatedConfig),
          });
        } catch (error) {
          progress.finalization.error = `Поиск завершён, но настройку не удалось сохранить: ${String(error?.message || error)}`;
          saveProgress();
          renderSummary();
          return;
        }
        progress.completed = true;
        progress.finalization.error = null;
        saveProgress();
        session.forceReview = false;
        saveSession();
        readiness = deriveReadiness(resources);
        renderReadiness();
        overlays.closeBlocking();
        panel = null;
        currentStep = null;
        notifications?.push({
          type: "success",
          scope: "onboarding",
          code: "completed",
          title: "Work Hunter готов",
          message: runSync ? "Первый поиск завершён." : "Настройка сохранена.",
        });
      } finally {
        requestLocked = false;
        if (button.isConnected) setBusy(button, false);
      }
    }

    async function boot() {
      progress = parseProgress();
      session = parseSession();
      await loadResources();
      if (readiness === "loading" || readiness === "unknown") return readiness;
      const serverVersion = Number(resources.config.data.ui?.onboarding_version || 0);
      const next = nextOnboardingState({
        readiness,
        serverVersion,
        progress,
        session,
        resources,
      });
      forcedReview = next.forced === true;
      if (next.kind === "step") openWizard(next.step);
      else if (next.kind === "summary") openWizard("summary");
      return next.kind;
    }

    function review() {
      progress = defaultProgress();
      saveProgress();
      session = { ...session, onboardingDeferred: false, forceReview: true };
      saveSession();
      return boot();
    }

    global.addEventListener("storage", (event) => {
      if (event.key !== PERMANENT_KEY) return;
      progress = parseProgress();
      if (panel && currentStep) renderStep(currentStep);
    });

    return Object.freeze({
      boot,
      review,
      deriveReadiness,
      snapshot: () => ({ readiness, currentStep, progress, session }),
    });
  }

  UI.onboarding = Object.freeze({
    PERMANENT_KEY,
    SESSION_KEY,
    deriveReadiness,
    nextOnboardingState,
    parseProgress,
    createController,
  });
})(window);
