let candidateAbout = {};

function candidateExperienceFromForm() {
  return [...document.querySelectorAll("[data-candidate-experience]")].map((row, index) => {
    const result = { ...(candidateAbout.experience || [])[index] };
    row.querySelectorAll("[data-fact-field]").forEach((input) => {
      const key = input.dataset.factField;
      result[key] = ["details", "results", "tech"].includes(key)
        ? input.value.split(key === "tech" ? "," : "\n").map((part) => part.trim()).filter(Boolean)
        : input.value.trim();
    });
    return result;
  });
}

function renderCandidateExperience() {
  const fields = { role: "Должность", company: "Компания", project: "Проект", start: "Начало работы",
    end: "Окончание или «по настоящее время»", contribution: "Личный вклад", details: "Задачи, по одной на строку",
    results: "Результаты, по одному на строку", tech: "Технологии, через запятую", evidence_url: "Ссылка на работу или подтверждение" };
  $("#candidate-experience-list").innerHTML = (candidateAbout.experience || []).map((item, index) => `
    <fieldset data-candidate-experience><legend>Опыт ${index + 1}</legend><div class="settings-fields-grid">
    ${Object.entries(fields).map(([key, label]) => {
      const value = Array.isArray(item[key]) ? item[key].join(key === "tech" ? ", " : "\n") : item[key] || "";
      return `<label>${label}${["details", "results", "contribution"].includes(key)
        ? `<textarea data-fact-field="${key}" rows="3">${escapeHtml(value)}</textarea>`
        : `<input data-fact-field="${key}" value="${escapeAttr(value)}">`}</label>`;
    }).join("")}</div><button type="button" data-remove-experience="${index}">Убрать из базы опыта</button></fieldset>`).join("");
}

async function loadCandidateFacts() {
  candidateAbout = await api("/api/about");
  $("#candidate-summary").value = candidateAbout.summary || "";
  $("#candidate-skills").value = (candidateAbout.all_skills || []).join(", ");
  renderCandidateExperience();
}

async function saveCandidateFacts() {
  const next = { ...candidateAbout, summary: $("#candidate-summary").value.trim(),
    all_skills: $("#candidate-skills").value.split(",").map((value) => value.trim()).filter(Boolean),
    experience: candidateExperienceFromForm() };
  candidateAbout = await api("/api/about", { method: "POST", body: JSON.stringify(next) });
  $("#candidate-status").textContent = "Опыт сохранён. Проверьте существующие версии резюме, если факты изменились.";
}

async function createCandidateResume() {
  await saveCandidateFacts();
  const resume = await api("/api/resumes/from-facts", { method: "POST",
    body: JSON.stringify({ role: $("#candidate-resume-role").value.trim() }) });
  await loadResumes();
  showResumeForm(resume.id);
  $("#candidate-status").textContent = "Версия создана. Проверьте формулировки и расставьте акценты под выбранную роль.";
}

async function downloadResume(id, format, button) {
  button.disabled = true;
  try {
    const response = await fetch(`/api/resumes/${requirePositiveInteger(id)}/export`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format, download: true }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || "Экспорт не прошёл проверку текста");
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url; link.download = `resume-${id}.${format}`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify("success", "resume", "exported", "Экспорт проверен", "Текст прочитан обратно в исходном порядке.");
    await loadResumes();
  } finally { button.disabled = false; }
}

async function loadLaunchReadiness() {
  const report = await api("/api/launch-readiness");
  $("#launch-readiness").innerHTML = `<p><strong>${report.status === "local_ready" ? "Локальная подготовка завершена" : "Нужно заполнить настройки"}</strong></p>
    <ul>${report.checks.map((item) => `<li>${item.status === "ok" ? "✓" : "○"} ${escapeHtml(item.message)}</li>`).join("")}</ul>
    ${report.warnings.map((message) => `<p>${escapeHtml(message)}</p>`).join("")}
    <p>Вопросов в очереди: ${report.pending_questions}. После входа: ${escapeHtml(report.live_checks_remaining.join("; "))}.</p>`;
  return report;
}

async function probeConfiguredAi(purpose = "", button = $("#ai-check-button")) {
  button.disabled = true;
  try {
    const result = await api("/api/ai/probe", { method: "POST", body: JSON.stringify({ purpose }) });
    $("#ai-connection-title").textContent = result.status === "ok" ? "AI ответил" : "Генерация не работает";
    $("#ai-connection-note").textContent = [result.model, result.answer || result.error_code].filter(Boolean).join(" · ");
    await loadAiUsage();
  } finally { button.disabled = false; }
}

document.querySelectorAll("[data-ai-probe]").forEach((button) => {
  button.addEventListener("click", () => probeConfiguredAi(button.dataset.aiProbe, button).catch((error) => notifyError("ai-probe", error)));
});

async function loadAiUsage() {
  const usage = await api("/api/ai/usage");
  const box = $("#ai-usage");
  if (box) box.textContent = `Запросов: ${usage.requests}, ошибок: ${usage.errors || 0}. Стоимость по данным API: ${usage.reported_cost_usd == null ? "не сообщена" : "$" + Number(usage.reported_cost_usd).toFixed(4)} (${usage.requests_with_cost} запросов с известной стоимостью).`;
}

async function loadResumeOutcomes() {
  const report = await api("/api/resumes/outcomes");
  $("#resume-outcomes").innerHTML = report.groups.length
    ? `<table><thead><tr><th>Резюме / версия</th><th>Поиск</th><th>Доставлено</th><th>Статусы HH</th></tr></thead><tbody>${report.groups.map((row) => `<tr><td>${escapeHtml(row.resume_id)} / ${escapeHtml(row.version.slice(0, 8))}</td><td>${escapeHtml(row.search)}</td><td>${row.delivered} / ${row.attempts}</td><td>${escapeHtml(JSON.stringify(row.observed_states))}</td></tr>`).join("")}</tbody></table><p>${escapeHtml(report.note)}</p>`
    : "Пока нет откликов с сохранённой версией резюме.";
}

$("#candidate-add-experience").addEventListener("click", () => {
  candidateAbout.experience = [...candidateExperienceFromForm(), {}]; renderCandidateExperience();
});
$("#candidate-experience-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-experience]");
  if (!button) return;
  candidateAbout.experience = candidateExperienceFromForm();
  candidateAbout.experience.splice(Number(button.dataset.removeExperience), 1); renderCandidateExperience();
});
for (const [id, action] of [["candidate-save", saveCandidateFacts], ["candidate-create-resume", createCandidateResume],
  ["launch-readiness-refresh", loadLaunchReadiness], ["resume-outcomes-refresh", loadResumeOutcomes]]) {
  $("#" + id).addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    try { await action(); } catch (error) { notifyError(id, error); }
    finally { $("#" + id).disabled = false; }
  });
}
$("#resumes-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-export-resume]");
  if (button) downloadResume(button.dataset.exportResume, button.dataset.format, button).catch((error) => notifyError("resume-export", error));
});
loadCandidateFacts().catch((error) => notifyError("candidate-facts", error));
