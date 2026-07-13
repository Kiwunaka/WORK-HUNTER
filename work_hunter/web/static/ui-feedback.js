(function feedbackModule(global) {
  "use strict";

  const UI = global.WorkHunterUI || (global.WorkHunterUI = {});

  function notificationKey(input) {
    return `${input.type || "info"}:${input.scope || "app"}:${input.code || "message"}`;
  }

  function createNotificationCenter(root, alertRoot = document.querySelector("#toast-alert-region")) {
    const entries = new Map();
    const timers = new Map();

    function clearTimer(key) {
      const timer = timers.get(key);
      if (timer?.id) global.clearTimeout(timer.id);
      timers.delete(key);
    }

    function dismiss(key) {
      clearTimer(key);
      entries.delete(key);
      renderNotificationStack();
    }

    function resolve(key) {
      dismiss(key);
    }

    function scheduleNotificationExpiry(key, remainingMs) {
      const entry = entries.get(key);
      if (!entry || entry.persistent) return;
      clearTimer(key);
      const duration = remainingMs ?? entry.timeoutMs;
      timers.set(key, {
        id: global.setTimeout(() => dismiss(key), duration),
        remainingMs: duration,
        startedAt: Date.now(),
      });
    }

    function pauseNotificationTimer(key) {
      const timer = timers.get(key);
      if (!timer) return;
      global.clearTimeout(timer.id);
      timer.remainingMs = Math.max(0, timer.remainingMs - (Date.now() - timer.startedAt));
      timer.id = null;
    }

    function resumeNotificationTimer(key) {
      const timer = timers.get(key);
      if (!timer || timer.id) return;
      scheduleNotificationExpiry(key, timer.remainingMs);
    }

    function enforceVisibleLimit(limit = 4) {
      while (entries.size > limit) {
        const oldestKey = entries.keys().next().value;
        clearTimer(oldestKey);
        entries.delete(oldestKey);
      }
    }

    function renderNotificationStack() {
      root.replaceChildren();
      for (const entry of entries.values()) {
        const toast = document.createElement("section");
        toast.className = `app-toast app-toast-${entry.type}`;
        toast.dataset.toastKey = entry.key;
        toast.tabIndex = entry.action ? 0 : -1;

        const marker = document.createElement("span");
        marker.className = "app-toast-marker";
        marker.setAttribute("aria-hidden", "true");
        marker.textContent = entry.type === "success" ? "✓" : entry.type === "error" ? "!" : "i";

        const content = document.createElement("div");
        content.className = "app-toast-content";
        const title = document.createElement("strong");
        title.textContent = entry.title || "Уведомление";
        content.append(title);
        if (entry.message) {
          const message = document.createElement("span");
          message.textContent = entry.message;
          content.append(message);
        }

        if (entry.count > 1) {
          const count = document.createElement("span");
          count.className = "app-toast-count";
          count.setAttribute("aria-label", `Повторено ${entry.count} раза`);
          count.textContent = `×${entry.count}`;
          content.append(count);
        }

        const controls = document.createElement("div");
        controls.className = "app-toast-controls";
        if (entry.action?.label && typeof entry.action.run === "function") {
          const action = document.createElement("button");
          action.type = "button";
          action.className = "app-toast-action";
          action.textContent = entry.action.label;
          action.addEventListener("click", async () => {
            action.disabled = true;
            try {
              const result = await entry.action.run();
              if (result !== false) dismiss(entry.key);
            } catch (error) {
              action.disabled = false;
              push({
                type: "error",
                scope: entry.scope,
                code: `${entry.code}-retry`,
                title: "Повтор не выполнен",
                message: String(error?.message || error),
              });
            }
          });
          controls.append(action);
        }

        const close = document.createElement("button");
        close.type = "button";
        close.className = "app-toast-close";
        close.setAttribute("aria-label", "Закрыть уведомление");
        close.textContent = "×";
        close.addEventListener("click", () => dismiss(entry.key));
        controls.append(close);

        toast.append(marker, content, controls);
        toast.addEventListener("pointerenter", () => pauseNotificationTimer(entry.key));
        toast.addEventListener("pointerleave", () => resumeNotificationTimer(entry.key));
        toast.addEventListener("focusin", () => pauseNotificationTimer(entry.key));
        toast.addEventListener("focusout", (event) => {
          if (!toast.contains(event.relatedTarget)) resumeNotificationTimer(entry.key);
        });
        root.append(toast);
      }
    }

    function announceAssertive(entry) {
      if (!alertRoot || entry.type !== "error") return;
      alertRoot.textContent = "";
      global.requestAnimationFrame(() => {
        alertRoot.textContent = [entry.title, entry.message].filter(Boolean).join(". ");
      });
    }

    function push(input) {
      const normalized = { ...input, type: input.type || "info" };
      const key = notificationKey(normalized);
      const now = Date.now();
      const existing = entries.get(key);
      if (existing && now - existing.lastAt <= 2000) {
        existing.count += 1;
        existing.lastAt = now;
        if (!existing.persistent) scheduleNotificationExpiry(key, existing.timeoutMs);
        renderNotificationStack();
        announceAssertive(existing);
        return key;
      }

      const entry = {
        ...normalized,
        key,
        count: 1,
        lastAt: now,
        persistent: normalized.type === "error" || Boolean(normalized.action),
        timeoutMs: normalized.type === "warning" ? 8000 : 5000,
      };
      entries.set(key, entry);
      enforceVisibleLimit();
      renderNotificationStack();
      scheduleNotificationExpiry(key);
      announceAssertive(entry);
      return key;
    }

    return Object.freeze({
      push,
      dismiss,
      resolve,
      list: () => [...entries.values()].map((entry) => ({ ...entry })),
    });
  }

  function createOverlayManager({ sheetRoot, popoverRoot }) {
    const state = { blocking: null, childPopover: null, base: null };
    const appSurfaces = [...document.querySelectorAll(".sidebar, .shell")];

    function restoreTriggerFocus(trigger) {
      if (trigger instanceof HTMLElement && trigger.isConnected && !trigger.closest("[inert]")) {
        trigger.focus();
      }
    }

    function setBaseInert(value) {
      for (const surface of appSurfaces) surface.inert = value;
      document.body.classList.toggle("has-blocking-overlay", value);
    }

    function makeLayer(descriptor, blocking) {
      const layer = document.createElement("div");
      layer.className = blocking ? "overlay-backdrop" : "popover-layer";
      layer.dataset.overlayKind = descriptor.kind;
      const panel = document.createElement("section");
      panel.className = blocking ? "overlay-sheet" : "overlay-popover";
      panel.setAttribute("role", "dialog");
      if (blocking) panel.setAttribute("aria-modal", "true");
      panel.setAttribute("aria-label", descriptor.label || "Диалог");
      layer.append(panel);
      descriptor.render?.(panel);
      return { layer, panel };
    }

    function focusInitial(record) {
      global.queueMicrotask(() => {
        const target = record.panel.querySelector("[data-initial-focus], [autofocus], button, input, select, textarea, [tabindex]:not([tabindex='-1'])");
        target?.focus();
      });
    }

    function closeChildPopover({ restoreFocus = true } = {}) {
      const record = state.childPopover;
      if (!record) return false;
      record.layer.remove();
      state.childPopover = null;
      if (restoreFocus) restoreTriggerFocus(record.trigger);
      return true;
    }

    function closeBase({ restoreFocus = true } = {}) {
      const record = state.base;
      if (!record) return false;
      record.layer.remove();
      state.base = null;
      if (restoreFocus) restoreTriggerFocus(record.trigger);
      return true;
    }

    function closeBlocking() {
      const record = state.blocking;
      if (!record) return false;
      closeChildPopover({ restoreFocus: false });
      record.layer.remove();
      state.blocking = null;
      setBaseInert(false);
      restoreTriggerFocus(record.trigger);
      return true;
    }

    function openBlocking(descriptor) {
      const { layer, panel } = makeLayer(descriptor, true);
      const record = { ...descriptor, layer, panel };
      state.blocking = record;
      sheetRoot.replaceChildren(layer);
      setBaseInert(true);
      layer.addEventListener("pointerdown", (event) => {
        if (event.target === layer && descriptor.dismissible !== false) closeBlocking();
      });
      focusInitial(record);
      return { accepted: true, kind: descriptor.kind };
    }

    function openChildPopover(descriptor) {
      if (!state.blocking) return { accepted: false, reason: "missing_blocking_parent" };
      closeChildPopover({ restoreFocus: false });
      const { layer, panel } = makeLayer(descriptor, false);
      const record = { ...descriptor, layer, panel };
      state.childPopover = record;
      popoverRoot.append(layer);
      focusInitial(record);
      return { accepted: true, kind: descriptor.kind };
    }

    function openBase(descriptor) {
      if (state.blocking) return { accepted: false, reason: "blocking_occupied" };
      closeBase({ restoreFocus: false });
      const { layer, panel } = makeLayer(descriptor, false);
      const record = { ...descriptor, layer, panel };
      state.base = record;
      popoverRoot.replaceChildren(layer);
      focusInitial(record);
      return { accepted: true, kind: descriptor.kind };
    }

    function request(descriptor) {
      if (!descriptor || typeof descriptor !== "object" || typeof descriptor.render !== "function") {
        return { accepted: false, reason: "invalid_descriptor" };
      }
      if (descriptor.kind === "mobileMenu" || descriptor.kind === "sheet") {
        if (state.blocking) return { accepted: false, reason: "blocking_occupied" };
        closeBase({ restoreFocus: false });
        return openBlocking(descriptor);
      }
      if (state.blocking?.kind === "mobileMenu") {
        return { accepted: false, reason: "mobile_menu_open" };
      }
      return descriptor.kind === "childPopover"
        ? openChildPopover(descriptor)
        : openBase(descriptor);
    }

    function closeTop() {
      if (state.childPopover) return closeChildPopover();
      if (state.base) return closeBase();
      if (state.blocking?.dismissible !== false) return closeBlocking();
      return false;
    }

    function topPanel() {
      return state.childPopover?.panel || state.base?.panel || state.blocking?.panel || null;
    }

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && closeTop()) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      if (event.key !== "Tab" || !state.blocking) return;
      const panel = topPanel();
      const focusable = panel ? [...panel.querySelectorAll("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex='-1'])")] : [];
      if (!focusable.length) {
        event.preventDefault();
        panel?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }, true);

    document.addEventListener("pointerdown", (event) => {
      const child = state.childPopover;
      if (child && !child.panel.contains(event.target) && !child.trigger?.contains?.(event.target)) {
        closeChildPopover();
        return;
      }
      const base = state.base;
      if (base && !base.panel.contains(event.target) && !base.trigger?.contains?.(event.target)) {
        closeBase();
      }
    }, true);

    return Object.freeze({
      request,
      closeTop,
      closeBlocking,
      snapshot: () => ({
        blocking: state.blocking?.kind || null,
        childPopover: state.childPopover?.kind || null,
        base: state.base?.kind || null,
      }),
    });
  }

  const LIVE_ROW_KEYS = Object.freeze({
    apply: ["vacancy", "company", "resume", "letter"],
    reply: ["negotiation", "employer", "recipient", "message"],
    campaign: ["run", "count", "filters", "resume"],
    cleanup: ["object_type", "count", "criteria"],
    resume_account: ["resume_or_account", "changes"],
    api_lab: ["method", "path", "params", "body"],
  });

  function stableSerialize(value, seen = new WeakSet()) {
    if (value === null || typeof value !== "object") {
      if (typeof value === "function" || typeof value === "undefined") return null;
      return value;
    }
    if (seen.has(value)) return "[circular]";
    seen.add(value);
    if (Array.isArray(value)) return value.map((item) => stableSerialize(item, seen));
    const result = {};
    for (const key of Object.keys(value).sort()) {
      if (["trigger", "revalidate", "execute"].includes(key)) continue;
      result[key] = stableSerialize(value[key], seen);
    }
    return result;
  }

  function stableActionFingerprint(value) {
    const serialized = JSON.stringify(stableSerialize(value));
    let hash = 2166136261;
    for (let index = 0; index < serialized.length; index += 1) {
      hash ^= serialized.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return `wh-${(hash >>> 0).toString(16).padStart(8, "0")}`;
  }

  function maskForUi(value) {
    const secretKey = /(authorization|password|secret|token|cookie|credential)/i;
    function mask(item, key = "", seen = new WeakSet()) {
      if (secretKey.test(key) && item !== null && item !== "") return "••••••";
      if (typeof item === "string") return item.length > 280 ? `${item.slice(0, 277)}…` : item;
      if (item === null || typeof item !== "object") return item;
      if (seen.has(item)) return "[circular]";
      seen.add(item);
      if (Array.isArray(item)) return item.map((part) => mask(part, key, seen));
      return Object.fromEntries(Object.entries(item).map(([name, part]) => [name, mask(part, name, seen)]));
    }
    const masked = mask(value);
    if (typeof masked === "string") return masked;
    const text = JSON.stringify(masked);
    return text.length > 600 ? `${text.slice(0, 597)}…` : text;
  }

  function validateLiveDescriptor(descriptor) {
    const required = LIVE_ROW_KEYS[descriptor?.operationType];
    if (!required) return { valid: false, code: "unknown_operation" };
    const rows = Array.isArray(descriptor.targetRows) ? descriptor.targetRows : [];
    const keys = rows.map((row) => row?.key);
    if (new Set(keys).size !== keys.length || required.some((key) => !keys.includes(key))) {
      return { valid: false, code: "missing_required_rows" };
    }
    if (rows.some((row) => !row || typeof row.label !== "string" || !("safeValue" in row))) {
      return { valid: false, code: "invalid_target_row" };
    }
    if (typeof descriptor.revalidate !== "function" || typeof descriptor.execute !== "function") {
      return { valid: false, code: "missing_action_boundary" };
    }
    if (!descriptor.fingerprint || !descriptor.title || !descriptor.consequence) {
      return { valid: false, code: "missing_review_content" };
    }
    return { valid: true, code: "ok" };
  }

  function validationError(code, message) {
    return {
      status: "error",
      code,
      message,
      canExecute: false,
      updatedDescriptor: null,
    };
  }

  function normalizeLiveValidation(descriptor, raw) {
    if (!raw || typeof raw !== "object") {
      return validationError("invalid_validation", "Не удалось подтвердить актуальность действия.");
    }
    const auth = raw.auth || { status: "error" };
    const capability = raw.capability || { available: false, code: "unknown" };
    const blockers = Array.isArray(raw.blockers) ? raw.blockers : [];

    if (raw.status === "error") {
      return validationError("validation_error", raw.safeMessage || "Проверка завершилась ошибкой.");
    }
    if (auth.status !== "ready") {
      return { ...raw, status: "auth_required", canExecute: false };
    }
    if (capability.available !== true) {
      return { ...raw, status: "capability_lost", canExecute: false };
    }
    if (blockers.length) {
      return { ...raw, status: "blocked", canExecute: false };
    }
    if (raw.status === "changed" || raw.fingerprint !== descriptor.fingerprint) {
      const replacement = raw.updatedDescriptor;
      const replacementCheck = validateLiveDescriptor(replacement);
      if (!replacementCheck.valid || replacement.fingerprint !== raw.fingerprint) {
        return validationError(
          "invalid_changed_descriptor",
          "Данные изменились, но новое описание недоступно. Закрой лист и попробуй повторить.",
        );
      }
      return { ...raw, status: "changed", canExecute: false };
    }
    if (raw.status !== "executable" || raw.canExecute !== true) {
      return validationError("not_executable", "Действие не прошло повторную проверку.");
    }
    if (raw.updatedDescriptor) {
      return validationError("unexpected_descriptor", "Проверка вернула несогласованные данные.");
    }
    return { ...raw, status: "executable", canExecute: true };
  }

  function validationMessage(result) {
    if (result.message) return result.message;
    if (result.status === "auth_required") return result.auth?.safeMessage || "Требуется повторная авторизация HH.";
    if (result.status === "capability_lost") return result.capability?.safeMessage || "Эта операция больше недоступна.";
    if (result.status === "blocked") return result.blockers?.map((item) => item.safeMessage).filter(Boolean).join(" · ") || "Операция заблокирована.";
    if (result.status === "changed") return "Данные изменились. Проверь обновлённое описание и подтверди заново.";
    return "Проверено. Выполняю действие…";
  }

  function openLiveAction(initialDescriptor) {
    const check = validateLiveDescriptor(initialDescriptor);
    if (!check.valid) {
      global.appNotifications?.push({
        type: "error",
        scope: "live-action",
        code: check.code,
        title: "Небезопасное действие заблокировано",
        message: check.code,
      });
      return { accepted: false, reason: check.code };
    }

    let descriptor = initialDescriptor;
    let panel = null;
    let submitting = false;
    let blocked = false;

    function render(root) {
      panel = root;
      root.replaceChildren();
      root.dataset.liveAction = descriptor.operationType;
      root.classList.add("live-action-sheet");

      const eyebrow = document.createElement("div");
      eyebrow.className = "live-action-eyebrow";
      eyebrow.textContent = "Реальное действие HH";
      const title = document.createElement("h2");
      title.textContent = descriptor.title;
      const consequence = document.createElement("p");
      consequence.className = "live-action-consequence";
      consequence.dataset.liveConsequence = "";
      consequence.textContent = descriptor.consequence;

      const rows = document.createElement("dl");
      rows.className = "live-action-rows";
      for (const row of descriptor.targetRows) {
        const term = document.createElement("dt");
        term.textContent = row.label;
        const detail = document.createElement("dd");
        detail.textContent = String(row.safeValue ?? "—");
        rows.append(term, detail);
      }

      let preview = null;
      if (descriptor.preview) {
        preview = document.createElement("pre");
        preview.className = "live-action-preview";
        preview.textContent = maskForUi(descriptor.preview);
      }

      const risks = document.createElement("ul");
      risks.className = "live-action-risks";
      for (const risk of descriptor.riskFlags || []) {
        const item = document.createElement("li");
        item.textContent = risk.safeMessage || risk.description || risk.code;
        risks.append(item);
      }

      const status = document.createElement("p");
      status.className = "live-action-status";
      status.dataset.liveStatus = "";
      status.setAttribute("role", "status");

      const acknowledgement = document.createElement("label");
      acknowledgement.className = "live-action-ack";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.liveAck = "";
      checkbox.dataset.initialFocus = "true";
      const acknowledgementText = document.createElement("span");
      acknowledgementText.textContent = descriptor.acknowledgement || "Я проверил цель и последствия действия";
      acknowledgement.append(checkbox, acknowledgementText);

      const actions = document.createElement("div");
      actions.className = "live-action-actions";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.dataset.liveCancel = "";
      cancel.textContent = "Отмена";
      const confirm = document.createElement("button");
      confirm.type = "button";
      confirm.className = "live-action-confirm";
      confirm.dataset.liveConfirm = "";
      confirm.textContent = descriptor.confirmLabel || "Выполнить";
      confirm.disabled = true;
      actions.append(cancel, confirm);

      checkbox.addEventListener("change", () => {
        confirm.disabled = blocked || submitting || !checkbox.checked;
      });
      cancel.addEventListener("click", () => global.appOverlays?.closeBlocking());
      confirm.addEventListener("click", async () => {
        if (submitting || blocked || !checkbox.checked) return;
        submitting = true;
        confirm.disabled = true;
        const idleLabel = confirm.textContent;
        confirm.textContent = "Проверяю…";
        status.textContent = "Сверяю авторизацию, возможность операции и выбранные данные…";
        try {
          const validation = normalizeLiveValidation(descriptor, await descriptor.revalidate());
          status.textContent = validationMessage(validation);
          if (validation.status === "changed") {
            descriptor = validation.updatedDescriptor;
            submitting = false;
            blocked = false;
            render(root);
            const changedStatus = root.querySelector("[data-live-status]");
            if (changedStatus) changedStatus.textContent = validationMessage(validation);
            return;
          }
          if (validation.status !== "executable" || validation.canExecute !== true) {
            submitting = false;
            blocked = true;
            checkbox.checked = false;
            confirm.textContent = idleLabel;
            return;
          }
          confirm.textContent = descriptor.confirmLabel ? `${descriptor.confirmLabel}…` : "Выполняю…";
          const result = await descriptor.execute(true);
          if (result?.status === "blocked" || result?.status === "error") {
            submitting = false;
            blocked = true;
            checkbox.checked = false;
            confirm.textContent = idleLabel;
            status.textContent = result.message || result.error || "Сервис заблокировал действие.";
            return;
          }
          global.appNotifications?.push({
            type: "success",
            scope: "live-action",
            code: descriptor.operationType,
            title: "Действие выполнено",
          });
          global.appOverlays?.closeBlocking();
        } catch (error) {
          submitting = false;
          blocked = true;
          checkbox.checked = false;
          confirm.textContent = idleLabel;
          status.textContent = String(error?.message || error);
        }
      });

      root.append(eyebrow, title, consequence, rows);
      if (preview) root.append(preview);
      if (risks.childElementCount) root.append(risks);
      root.append(status, acknowledgement, actions);
    }

    return global.appOverlays?.request({
      kind: "sheet",
      label: initialDescriptor.title,
      trigger: initialDescriptor.trigger || document.activeElement,
      render,
    }) || { accepted: false, reason: "overlay_unavailable" };
  }

  async function runValidationAdapter(descriptor, check) {
    try {
      const evidence = typeof check === "function" ? await check() : (check || {});
      return {
        status: evidence.status || "executable",
        fingerprint: evidence.fingerprint || descriptor.fingerprint,
        auth: evidence.auth || { status: "ready" },
        capability: evidence.capability || { available: true, code: "ok" },
        blockers: evidence.blockers || [],
        riskFlags: evidence.riskFlags || descriptor.riskFlags || [],
        canExecute: evidence.canExecute ?? true,
        ...(evidence.updatedDescriptor ? { updatedDescriptor: evidence.updatedDescriptor } : {}),
      };
    } catch (error) {
      return { status: "error", safeMessage: String(error?.message || error), canExecute: false };
    }
  }

  const validateApplyMutation = runValidationAdapter;
  const validateReplyMutation = runValidationAdapter;
  const validateCampaignMutation = runValidationAdapter;
  const validateCleanupMutation = runValidationAdapter;
  const validateResumeAccountMutation = runValidationAdapter;
  const validateLabMutation = runValidationAdapter;

  UI.feedback = Object.freeze({
    createNotificationCenter,
    createOverlayManager,
    notificationKey,
    validateLiveDescriptor,
    normalizeLiveValidation,
    openLiveAction,
    stableActionFingerprint,
    maskForUi,
    validateApplyMutation,
    validateReplyMutation,
    validateCampaignMutation,
    validateCleanupMutation,
    validateResumeAccountMutation,
    validateLabMutation,
  });

  const toastRoot = document.querySelector("#toast-region");
  const sheetRoot = document.querySelector("#sheet-root");
  const popoverRoot = document.querySelector("#popover-root");
  if (toastRoot) global.appNotifications = createNotificationCenter(toastRoot);
  if (sheetRoot && popoverRoot) {
    global.appOverlays = createOverlayManager({ sheetRoot, popoverRoot });
  }
})(window);
