(function bootstrapWorkHunterCore(global) {
  "use strict";

  const UI = global.WorkHunterUI = global.WorkHunterUI || {};
  const DESTINATIONS = Object.freeze({
    today: "/today",
    vacancies: "/jobs",
    applications: "/applications",
    calendar: "/calendar",
    assistant: "/assistant",
    analytics: "/analytics",
    sources: "/sources",
    settings: "/settings",
  });

  const PATHS = Object.freeze({
    "/": ["today", "/today", {}],
    "/today": ["today", "/today", {}],
    "/jobs": ["vacancies", "/jobs", {}],
    "/favorites": ["vacancies", "/jobs", { filter: "saved" }],
    "/applications": ["applications", "/applications", {}],
    "/agent": ["applications", "/applications", { tab: "agent" }],
    "/calendar": ["calendar", "/calendar", {}],
    "/assistant": ["assistant", "/assistant", {}],
    "/chat": ["assistant", "/assistant", {}],
    "/analytics": ["analytics", "/analytics", {}],
    "/stats": ["analytics", "/analytics", { tab: "overview" }],
    "/trends": ["analytics", "/analytics", { tab: "trends" }],
    "/sources": ["sources", "/sources", {}],
    "/settings": ["settings", "/settings", {}],
  });

  const RECOGNIZED = Object.freeze({
    today: [],
    vacancies: ["filter", "source", "min_score", "status", "job"],
    applications: ["tab", "approval", "run", "operation"],
    calendar: ["event"],
    assistant: ["job"],
    analytics: ["tab"],
    sources: [],
    settings: ["section"],
  });

  function canonicalInteger(value, min, max = Number.MAX_SAFE_INTEGER) {
    if (!/^(0|[1-9]\d*)$/.test(String(value ?? ""))) return null;
    const parsed = Number(value);
    return Number.isSafeInteger(parsed) && parsed >= min && parsed <= max
      ? String(parsed)
      : null;
  }

  function resolve(pathname, search = "", hash = "", sourceKeys = null) {
    const params = new URLSearchParams(search);
    const [destination, path, overrides] = PATHS[pathname] || ["today", "/today", {}];
    const recognized = new Set(RECOGNIZED[destination]);
    const output = new URLSearchParams();
    const warnings = [];
    const first = (key) => params.has(key) ? params.getAll(key)[0] : null;

    function enumValue(key, allowed, fallback = null) {
      const value = Object.prototype.hasOwnProperty.call(overrides, key)
        ? overrides[key]
        : first(key);
      if (value === null) return fallback;
      if (allowed.includes(value)) return value;
      warnings.push(`invalid_${key}`);
      return fallback;
    }

    function integerValue(key, min, max = Number.MAX_SAFE_INTEGER) {
      const raw = first(key);
      if (raw === null) return null;
      const value = canonicalInteger(raw, min, max);
      if (value === null) warnings.push(`invalid_${key}`);
      return value;
    }

    if (destination === "vacancies") {
      output.set("filter", enumValue("filter", ["all", "saved"], "all"));
      const source = first("source");
      if (source && (sourceKeys === null || sourceKeys.includes(source))) {
        output.set("source", source);
      } else if (source) {
        warnings.push("invalid_source");
      }
      const minScore = integerValue("min_score", 0, 100);
      if (minScore !== null) output.set("min_score", minScore);
      const status = enumValue(
        "status",
        ["new", "saved", "hidden", "applied", "ghosted"],
      );
      if (status !== null) output.set("status", status);
      const job = integerValue("job", 1);
      if (job !== null) output.set("job", job);
    } else if (destination === "applications") {
      output.set(
        "tab",
        enumValue("tab", ["pipeline", "agent", "automation"], "pipeline"),
      );
      for (const key of ["approval", "run", "operation"]) {
        const value = integerValue(key, 1);
        if (value !== null) output.set(key, value);
      }
    } else if (destination === "calendar" || destination === "assistant") {
      const key = destination === "calendar" ? "event" : "job";
      const value = integerValue(key, 1);
      if (value !== null) output.set(key, value);
    } else if (destination === "analytics") {
      output.set("tab", enumValue("tab", ["overview", "trends"], "overview"));
    } else if (destination === "settings") {
      output.set(
        "section",
        enumValue(
          "section",
          [
            "profile",
            "resumes",
            "search",
            "hh",
            "ai",
            "notifications",
            "appearance",
            "help",
            "advanced",
          ],
          "profile",
        ),
      );
    }

    for (const [key, value] of params.entries()) {
      if (!recognized.has(key)) output.append(key, value);
    }
    const query = output.toString();
    const canonicalUrl = `${path}${query ? `?${query}` : ""}${hash}`;
    return { destination, path, query, hash, canonicalUrl, warnings };
  }

  function activate(resolved, { historyMode = "replace" } = {}) {
    const current = `${global.location.pathname}${global.location.search}${global.location.hash}`;
    if (current !== resolved.canonicalUrl) {
      const method = historyMode === "push" ? "pushState" : "replaceState";
      global.history[method]({ destination: resolved.destination }, "", resolved.canonicalUrl);
    }
    global.dispatchEvent(new CustomEvent("work-hunter:route", { detail: resolved }));
    return resolved;
  }

  function syncFromLocation(sourceKeys = null) {
    return activate(resolve(
      global.location.pathname,
      global.location.search,
      global.location.hash,
      sourceKeys,
    ));
  }

  UI.route = Object.freeze({ DESTINATIONS, canonicalInteger, resolve, activate, syncFromLocation });
  UI.resources = Object.freeze({
    loading: () => ({ status: "loading", stale: false }),
    ready: (data, { updatedAt = null, stale = false } = {}) => ({
      status: "ready",
      data,
      updatedAt,
      stale,
    }),
    error: (error, { data, stale = data !== undefined } = {}) => ({
      status: "error",
      error,
      ...(data === undefined ? {} : { data }),
      stale,
    }),
  });

  global.addEventListener("popstate", () => syncFromLocation());
  syncFromLocation();
})(window);
