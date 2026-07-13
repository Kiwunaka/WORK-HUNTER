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

  UI.feedback = Object.freeze({ createNotificationCenter, createOverlayManager, notificationKey });

  const toastRoot = document.querySelector("#toast-region");
  const sheetRoot = document.querySelector("#sheet-root");
  const popoverRoot = document.querySelector("#popover-root");
  if (toastRoot) global.appNotifications = createNotificationCenter(toastRoot);
  if (sheetRoot && popoverRoot) {
    global.appOverlays = createOverlayManager({ sheetRoot, popoverRoot });
  }
})(window);
