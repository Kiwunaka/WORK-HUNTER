(function todayModule(global) {
  "use strict";

  const UI = global.WorkHunterUI || (global.WorkHunterUI = {});

  function formatter(timeZone) {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
  }

  function partsAt(epoch, timeZone) {
    const values = {};
    for (const part of formatter(timeZone).formatToParts(new Date(epoch))) {
      if (part.type !== "literal") values[part.type] = Number(part.value);
    }
    return {
      year: values.year,
      month: values.month,
      day: values.day,
      hour: values.hour,
      minute: values.minute,
      second: values.second,
    };
  }

  function scalar(parts) {
    return Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour || 0, parts.minute || 0, parts.second || 0);
  }

  function sameParts(left, right) {
    return ["year", "month", "day", "hour", "minute", "second"]
      .every((key) => (left[key] || 0) === (right[key] || 0));
  }

  function zonedLocalToEpoch(target, timeZone) {
    const naive = scalar(target);
    const sampleInstants = [naive - 36 * 3600000, naive - 12 * 3600000, naive, naive + 12 * 3600000, naive + 36 * 3600000];
    const offsets = [...new Set(sampleInstants.map((instant) => scalar(partsAt(instant, timeZone)) - instant))];
    const candidates = offsets.map((offset) => naive - offset);
    const matches = candidates.filter((candidate) => sameParts(partsAt(candidate, timeZone), target));
    if (matches.length) return Math.min(...matches);

    // In a DST gap, Temporal-compatible behavior shifts forward by the gap.
    const afterGap = candidates
      .map((candidate) => ({ candidate, wall: scalar(partsAt(candidate, timeZone)) }))
      .filter((item) => item.wall > naive)
      .sort((left, right) => left.wall - right.wall || left.candidate - right.candidate);
    return afterGap[0]?.candidate ?? Number.NaN;
  }

  function parseZonedValue(value, timeZone) {
    if (typeof value !== "string" || !value.trim()) return Number.NaN;
    const text = value.trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
      const [year, month, day] = text.split("-").map(Number);
      return zonedLocalToEpoch({ year, month, day, hour: 0, minute: 0, second: 0 }, timeZone);
    }
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?$/.test(text)) {
      const match = text.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/);
      return zonedLocalToEpoch({
        year: Number(match[1]),
        month: Number(match[2]),
        day: Number(match[3]),
        hour: Number(match[4]),
        minute: Number(match[5]),
        second: Number(match[6] || 0),
      }, timeZone);
    }
    const parsed = Date.parse(text);
    return Number.isFinite(parsed) ? parsed : Number.NaN;
  }

  function createZonedClock(now, timeZone) {
    const epoch = Date.parse(now);
    if (!Number.isFinite(epoch)) throw new Error("now must be an ISO instant");
    const local = partsAt(epoch, timeZone);
    const dayOrdinal = Math.floor(Date.UTC(local.year, local.month - 1, local.day) / 86400000);
    return Object.freeze({
      epoch,
      timeZone,
      local,
      dayOrdinal,
      parse: (value) => parseZonedValue(value, timeZone),
      localDay(epochValue) {
        const parts = partsAt(epochValue, timeZone);
        return Math.floor(Date.UTC(parts.year, parts.month - 1, parts.day) / 86400000);
      },
    });
  }

  function resourceData(resource) {
    return resource?.status === "ready" && Array.isArray(resource.data) ? resource.data : [];
  }

  function numericId(value) {
    const parsed = Number(value);
    return Number.isSafeInteger(parsed) ? parsed : Number.MAX_SAFE_INTEGER;
  }

  function scoreOf(job) {
    const score = job?.score?.total_score;
    return typeof score === "number" && Number.isFinite(score) ? score : Number.NaN;
  }

  function timestampOfJob(job) {
    const published = Date.parse(job.published_at || "");
    if (Number.isFinite(published)) return published;
    const fetched = Date.parse(job.fetched_at || "");
    return Number.isFinite(fetched) ? fetched : Number.NEGATIVE_INFINITY;
  }

  function selectFreshMatches(jobsResource) {
    return resourceData(jobsResource)
      .filter((job) => job.status === "new" && Number.isFinite(scoreOf(job)))
      .sort((left, right) => (
        scoreOf(right) - scoreOf(left)
        || timestampOfJob(right) - timestampOfJob(left)
        || numericId(right.id) - numericId(left.id)
      ));
  }

  function selectPendingDecision(approvalsResource) {
    return resourceData(approvalsResource)
      .filter((approval) => approval.status === "pending")
      .sort((left, right) => {
        const leftTime = Date.parse(left.created_at || "");
        const rightTime = Date.parse(right.created_at || "");
        const safeLeft = Number.isFinite(leftTime) ? leftTime : Number.POSITIVE_INFINITY;
        const safeRight = Number.isFinite(rightTime) ? rightTime : Number.POSITIVE_INFINITY;
        return safeLeft - safeRight || numericId(left.id) - numericId(right.id);
      })[0] || null;
  }

  function selectUpcoming(eventsResource, clock) {
    return resourceData(eventsResource)
      .map((event) => ({ event, epoch: clock.parse(event.event_date || event.starts_at || event.date || "") }))
      .filter(({ epoch }) => Number.isFinite(epoch) && epoch >= clock.epoch && clock.localDay(epoch) < clock.dayOrdinal + 31)
      .sort((left, right) => left.epoch - right.epoch || numericId(left.event.id) - numericId(right.event.id))
      .map(({ event, epoch }) => ({ ...event, _todayEpoch: epoch }));
  }

  function selectFocus(resources, clock) {
    const approvals = resourceData(resources.approvals)
      .filter((approval) => approval.status === "pending")
      .map((approval) => ({
        ...approval,
        kind: "approval",
        _category: 1,
        _time: Number.isFinite(Date.parse(approval.created_at || "")) ? Date.parse(approval.created_at) : Number.POSITIVE_INFINITY,
      }));

    const dueItems = [];
    for (const task of resourceData(resources.tasks)) {
      if (task.status !== "open") continue;
      const epoch = clock.parse(task.due_at || task.due_date || task.scheduled_at || "");
      if (!Number.isFinite(epoch)) continue;
      const dayDelta = clock.localDay(epoch) - clock.dayOrdinal;
      if (dayDelta < -30 || dayDelta > 0) continue;
      dueItems.push({ ...task, kind: "task", _category: 2, _time: epoch, _kindOrder: 0 });
    }
    for (const event of resourceData(resources.events)) {
      const epoch = clock.parse(event.event_date || event.starts_at || event.date || "");
      if (!Number.isFinite(epoch) || clock.localDay(epoch) !== clock.dayOrdinal) continue;
      dueItems.push({ ...event, kind: "event", _category: 2, _time: epoch, _kindOrder: 1 });
    }

    const jobs = resourceData(resources.jobs)
      .filter((job) => job.status === "new" && scoreOf(job) >= 70)
      .map((job) => ({ ...job, kind: "job", _category: 3, _score: scoreOf(job) }));

    const readiness = (resources.readinessMissing || []).map((missing, index) => ({
      id: `readiness-${missing}`,
      kind: "readiness",
      missing,
      title: missing === "goal" ? "Уточнить цель поиска" : missing === "source" ? "Включить источник" : "Добавить активное резюме",
      _category: 4,
      _order: index,
    }));

    const sourceFailures = resourceData(resources.sources)
      .filter((source) => source.last_error)
      .map((source) => ({
        ...source,
        id: source.source,
        kind: "source",
        title: `Проверить источник ${source.source}`,
        _category: 5,
        _time: Number.isFinite(Date.parse(source.last_sync_at || "")) ? Date.parse(source.last_sync_at) : Number.NEGATIVE_INFINITY,
      }));

    return [...approvals, ...dueItems, ...jobs, ...readiness, ...sourceFailures]
      .sort((left, right) => {
        if (left._category !== right._category) return left._category - right._category;
        if (left._category === 1) return left._time - right._time || numericId(left.id) - numericId(right.id);
        if (left._category === 2) return left._time - right._time || left._kindOrder - right._kindOrder || numericId(left.id) - numericId(right.id);
        if (left._category === 3) return right._score - left._score || numericId(left.id) - numericId(right.id);
        if (left._category === 4) return left._order - right._order;
        return right._time - left._time || String(left.source).localeCompare(String(right.source));
      });
  }

  function compose({ resources, now, timeZone }) {
    const clock = createZonedClock(now, timeZone);
    return {
      readiness: resources.readiness,
      resourceStates: {
        jobs: resources.jobs?.status || "loading",
        tasks: resources.tasks?.status || "loading",
        events: resources.events?.status || "loading",
        approvals: resources.approvals?.status || "loading",
        sources: resources.sources?.status || "loading",
      },
      focus: selectFocus(resources, clock).slice(0, 3),
      freshMatches: selectFreshMatches(resources.jobs).slice(0, 3),
      upcoming: selectUpcoming(resources.events, clock).slice(0, 2),
      pendingDecision: selectPendingDecision(resources.approvals),
      timeZone,
    };
  }

  function clearAndTitle(root, title, subtitle) {
    root.replaceChildren();
    const header = document.createElement("header");
    header.className = "today-section-header";
    const heading = document.createElement("h2");
    heading.textContent = title;
    header.append(heading);
    if (subtitle) {
      const note = document.createElement("span");
      note.textContent = subtitle;
      header.append(note);
    }
    root.append(header);
  }

  function empty(root, text) {
    const box = document.createElement("div");
    box.className = "today-empty";
    box.textContent = text;
    root.append(box);
  }

  function render(model, { navigate } = {}) {
    const readinessRoot = document.querySelector("#today-readiness");
    const focusRoot = document.querySelector("#today-focus");
    const freshRoot = document.querySelector("#today-fresh-matches");
    const upcomingRoot = document.querySelector("#today-upcoming");
    const decisionsRoot = document.querySelector("#today-decisions");
    if (!readinessRoot || !focusRoot || !freshRoot || !upcomingRoot || !decisionsRoot) return;

    readinessRoot.replaceChildren();
    readinessRoot.dataset.state = model.readiness;
    const readinessCopy = document.createElement("div");
    const readinessTitle = document.createElement("strong");
    readinessTitle.textContent = model.readiness === "ready" ? "Готово к поиску" : model.readiness === "loading" ? "Проверяю готовность…" : "Нужна настройка";
    const readinessText = document.createElement("span");
    readinessText.textContent = model.readiness === "ready"
      ? "Цель, источники и активное резюме настроены."
      : "Завершите основные настройки — HH и AI можно подключить позже.";
    readinessCopy.append(readinessTitle, readinessText);
    const find = document.createElement("button");
    find.type = "button";
    find.className = "primary";
    find.dataset.actionId = "today.find-vacancies";
    find.textContent = "Найти вакансии";
    find.addEventListener("click", () => navigate?.("inbox"));
    readinessRoot.append(readinessCopy, find);

    clearAndTitle(focusRoot, "В фокусе", "До трёх действий");
    if (!model.focus.length) empty(focusRoot, "Срочных действий нет.");
    for (const item of model.focus) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "today-row";
      row.dataset.actionId = `today.focus.${item.kind}`;
      const title = item.title || item.action_type || (item.kind === "approval" ? `Решение #${item.id}` : `Запись #${item.id}`);
      const meta = item.kind === "job" ? `Совпадение ${scoreOf(item)}` : item.kind === "approval" ? "Ожидает решения" : item.kind === "event" ? "Сегодня" : "К выполнению";
      row.append(Object.assign(document.createElement("strong"), { textContent: title }), Object.assign(document.createElement("span"), { textContent: meta }));
      row.addEventListener("click", () => navigate?.(item.kind === "job" ? "inbox" : item.kind === "event" || item.kind === "task" ? "calendar" : item.kind === "approval" ? "agent" : "settings"));
      focusRoot.append(row);
    }

    clearAndTitle(freshRoot, "Свежие совпадения", "Лучшие новые вакансии");
    if (!model.freshMatches.length) empty(freshRoot, "Нет оценённых новых вакансий. Запустите поиск и подсчёт score.");
    for (const job of model.freshMatches) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "today-job-row";
      row.dataset.actionId = "today.open-vacancy";
      row.dataset.recordId = String(job.id);
      const score = document.createElement("span");
      score.className = "today-score";
      score.textContent = String(scoreOf(job));
      const copy = document.createElement("span");
      copy.append(Object.assign(document.createElement("strong"), { textContent: job.title || "Без названия" }), Object.assign(document.createElement("small"), { textContent: [job.company, job.location, job.salary].filter(Boolean).join(" · ") || "Подробности в вакансии" }));
      row.append(score, copy);
      row.addEventListener("click", () => navigate?.("inbox", { job: job.id }));
      freshRoot.append(row);
    }

    clearAndTitle(upcomingRoot, "Ближайшее", `Часовой пояс: ${model.timeZone}`);
    if (!model.upcoming.length) empty(upcomingRoot, "В ближайшие 30 дней событий нет.");
    for (const event of model.upcoming) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "today-row";
      row.dataset.actionId = "today.open-event";
      row.append(Object.assign(document.createElement("strong"), { textContent: event.title || "Событие" }), Object.assign(document.createElement("span"), { textContent: event.event_date || event.starts_at || event.date || "" }));
      row.addEventListener("click", () => navigate?.("calendar", { event: event.id }));
      upcomingRoot.append(row);
    }

    clearAndTitle(decisionsRoot, "Ожидает решения");
    if (!model.pendingDecision) empty(decisionsRoot, "Новых запросов на подтверждение нет.");
    else {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "today-decision";
      button.dataset.actionId = "today.open-approval";
      button.textContent = `${model.pendingDecision.action_type || "Действие"} · #${model.pendingDecision.id}`;
      button.addEventListener("click", () => navigate?.("agent", { approval: model.pendingDecision.id }));
      decisionsRoot.append(button);
    }
  }

  UI.today = Object.freeze({
    compose,
    render,
    createZonedClock,
    parseZonedValue,
    selectFocus,
    selectFreshMatches,
    selectUpcoming,
    selectPendingDecision,
  });
})(window);
