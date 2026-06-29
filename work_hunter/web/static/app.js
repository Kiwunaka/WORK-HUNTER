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
  sourceSetup: {
    guide: null,
    log: [],
  },
};

const UX_RU_COPY = {
  nav: {
    inbox: ["Вакансии", "Очередь найденных вакансий, фильтры и быстрые действия."],
    setup: ["Настройка", "Проверка готовности AI, импорта и окружения."],
    onboarding: ["Онбординг", "Ответы, из которых собирается профиль кандидата."],
    "candidate-map": ["Карта кандидата", "Факты, навыки и подтверждения профиля."],
    resumes: ["Резюме", "Импорт, варианты и активное резюме."],
    "job-detail": ["Карточка вакансии", "Отдельный просмотр вакансии по ID."],
    calendar: ["Календарь", "Собеседования, дедлайны и напоминания."],
    favorites: ["Избранное", "Сохранённые вакансии и быстрый возврат к ним."],
    chat: ["AI Ассистент", "Вопросы по выбранной вакансии и профилю."],
    agent: ["HH Агент", "Операции, согласования и шаблоны HH."],
    "application-preview": ["Черновик отклика", "Предпросмотр письма, резюме и внешней формы."],
    campaigns: ["Кампании", "Планирование и безопасный запуск серий откликов."],
    pipeline: ["Воронка", "Статусы вакансий и подготовка follow-up."],
    "interview-prep": ["Подготовка к собеседованию", "Пакет подготовки под этап интервью."],
    replay: ["История действий", "Таймлайн событий и экспорт отчёта."],
    "audit-security": ["Аудит безопасности", "Проверки редактирования секретов и безопасных действий."],
    "browser-lab": ["Лаборатория браузера", "Проверка сессий, HAR и внешних форм."],
    settings: ["Настройки", "Профиль поиска, AI, токены и автосинхронизация."],
    sources: ["Источники", "Синхронизация, сертификация и readiness источников."],
    stats: ["Статистика", "Сводка по вакансиям, источникам и score."],
    trends: ["Тренды", "AI-обзор рынка по последним вакансиям."],
    help: ["Справка", "Понятные инструкции, подсказки и частые вопросы."],
  },
  headings: {
    ".shell > .topbar h1": "Очередь вакансий",
    "#view-setup h1": "Настройка",
    "#view-onboarding h1": "Онбординг",
    "#view-candidate-map h1": "Карта кандидата",
    "#view-application-preview h1": "Черновик отклика",
    "#view-campaigns h1": "Кампании",
    "#view-pipeline h1": "Воронка",
    "#view-interview-prep h1": "Подготовка к собеседованию",
    "#view-replay h1": "История действий",
    "#view-audit-security h1": "Аудит безопасности",
    "#view-browser-lab h1": "Лаборатория браузера",
    "#view-job-detail h1": "Карточка вакансии",
    "#view-sources .agent-row-head strong": "Действия источников",
  },
  fields: {
    "ai-test-route": ["AI-маршрут", "Оставь smart для обычной проверки маршрутизации."],
    "ai-test-prompt": ["Промпт проверки", "Короткий текст для dry run без реального отклика."],
    "setup-import-wo-source": ["Папка источника", "Локальная папка WO/FLOW для предварительного импорта."],
    "onboarding-question-select": ["Вопрос профиля", "Выбери блок, который сейчас готов заполнить."],
    "onboarding-answer": ["Ответ", "Пиши свободно: опыт, ограничения, достижения и предпочтения."],
    "resume-variant-job-id": ["ID вакансии", "Подставляется автоматически после выбора вакансии."],
    "resume-variant-resume-id": ["ID резюме", "Оставь пустым, если нужен активный профиль."],
    "application-preview-job-id": ["ID вакансии", "Вакансия для черновика отклика."],
    "application-preview-resume-variant-id": ["ID варианта резюме", "Можно оставить пустым для текущего варианта."],
    "application-preview-letter": ["Сопроводительное письмо", "Текст письма, которое уйдёт вместе с откликом."],
    "application-preview-payload": ["Payload источника (JSON)", "Доп. поля для площадки. Оставь {} если не нужны."],
    "external-apply-form-json": ["Внешняя форма (JSON)", "Описание полей формы площадки для dry run."],
    "campaign-limit": ["Лимит вакансий", "Сколько вакансий максимум взять в план кампании."],
    "campaign-min-score": ["Мин. score", "Брать в кампанию только вакансии с этим score и выше."],
    "campaign-daily-cap": ["Лимит в день", "Максимум откликов в сутки. Защита от спама."],
    "campaign-ai-filter": ["AI-фильтр", "Доп. отсев вакансий через AI: off / light / heavy."],
    "external-campaign-source": ["Внешний источник", "Площадка для внешней кампании откликов."],
    "pipeline-job-id": ["ID вакансии", "Вакансия, для которой ведём этап воронки."],
    "pipeline-stage-select": ["Этап", "Стадия найма: HR, тех, финал или оффер."],
    "pipeline-event-at": ["Дата и время", "Когда запланировать follow-up или событие."],
    "interview-prep-job-id": ["ID вакансии", "Вакансия, под которую собрать подготовку."],
    "interview-prep-stage-select": ["Этап интервью", "Стадия, под которую собрать пакет подготовки."],
    "replay-job-id": ["ID вакансии", "Фильтр таймлайна по конкретной вакансии."],
    "replay-run-id": ["ID запуска", "Фильтр таймлайна по конкретному запуску кампании."],
    "replay-source-filter": ["Источник", "Показать события только выбранной площадки."],
    "replay-event-type-filter": ["Тип события", "Например application_pack_built."],
    "browser-lab-source": ["Источник", "Например getmatch, hirehi или careerspace."],
    "browser-lab-har-path": ["Путь к HAR", "Локальный файл сессии, экспортированный из браузера."],
    "browser-lab-hosts": ["Разрешённые хосты", "Домены через запятую, которые можно использовать."],
    "source-action-source": ["Источник", "Выбери адаптер для синхронизации или теста."],
    "source-action-limit": ["Лимит", "0 означает лимит по умолчанию для источника."],
    "source-certification-level": ["Уровень", "L5 — внешний отклик, L6 — кампании."],
    "source-external-target-session": ["Сессия", "Имя локальной сессии для внешнего отклика."],
    "source-external-target-url": ["URL отклика", "Endpoint площадки, куда уходит отклик."],
    "source-external-target-method": ["Метод", "HTTP-метод запроса отклика (обычно POST)."],
    "source-external-target-payload-template": ["Шаблон payload (JSON)", "{source_id} подставится из вакансии."],
    "source-external-har-path": ["Путь к HAR", "Файл сессии, из которого извлечь настройки отклика."],
    "source-external-har-hosts": ["Разрешённые хосты", "Домены через запятую для разбора HAR."],
    "source-redaction-scan-payload": ["Payload (JSON)", "Проверяется на токены, cookie и секреты."],
    "source-redaction-scan-text": ["Текст-образец", "Любой текст для проверки на утечку секретов."],
    "source-certification-evidence-json": ["Evidence (JSON)", "Доказательства готовности: тесты, replay и т.д."],
    "source-setup-source": ["Источник", "Начни с HH: это самый короткий путь к первому безопасному отклику."],
    "source-setup-level": ["Режим сертификации", "Нужен только для внешних площадок с HAR и dry-run."],
    "source-setup-har-path": ["Путь к HAR", "Файл HAR, экспортированный из твоего браузера."],
    "source-setup-har-hosts": ["Разрешённые хосты", "Домены через запятую, например getmatch.ru."],
    "source-setup-redaction-payload": ["Payload redaction", "Безопасный образец headers/payload для проверки маскирования."],
    "source-setup-redaction-text": ["Текст redaction", "Текстовый образец для поиска токенов, cookies и секретов."],
    "source-setup-form-json": ["Форма dry-run", "JSON-описание формы для безопасного заполнения без отправки."],
    "source-setup-persona-json": ["Персона dry-run", "Факты кандидата, которые можно подставить в форму."],
    "hh-lab-method": ["Метод", "HTTP-метод запроса к HH API."],
    "hh-lab-path": ["Путь", "Путь HH API, например /me."],
    "hh-lab-snippet-name": ["Имя сниппета", "Под этим именем сохранится запрос."],
    "hh-lab-params": ["Параметры (JSON)", "Query-параметры запроса."],
    "hh-lab-body": ["Тело (JSON)", "Тело запроса для POST/PUT/PATCH."],
    "agent-template-name": ["Название", "Короткое имя шаблона письма."],
    "agent-template-body": ["Текст", "Тело шаблона. Доступны плейсхолдеры вида {name}."],
    "agent-blacklist-id": ["ID работодателя", "Числовой ID компании на HH."],
    "agent-blacklist-name": ["Название", "Название компании для чёрного списка."],
    "agent-blacklist-reason": ["Причина", "Почему компания в чёрном списке."],
    "agent-resume-template": ["Markdown-шаблон", "Шаблон резюме с плейсхолдерами профиля."],
    "agent-resume-context": ["Доп. контекст (JSON)", "Поля, которые подставятся в шаблон."],
    "agent-batch-matrix-input": ["Матрица (JSON)", "Резюме × пресеты × письма × лимиты."],
    "job-detail-id-input": ["ID вакансии", "Введи ID, чтобы открыть карточку вакансии."],
    "resume-import-path": ["Путь к файлу", "Локальный путь к резюме (.md/.json) для импорта."],
    "auto-sync-interval": ["Интервал (минуты)", "0 — выключить автосинхронизацию."],
    "ai-key-input": ["Ключ OpenRouter", "Хранится локально. Уже сохранённый ключ скрыт как ***"],
    "ai-model-input": ["Модель", "ID модели OpenRouter, например google/gemini-2.5-flash."],
    "ai-backend-select": ["Бэкенд AI", "Прямой OpenAI-совместимый API или OpenCode."],
  },
};

const CONTROL_HINTS = {
  "sync-button": ["Синхронизировать", "Синхронизировать вакансии из всех активных источников"],
  "score-button": ["Пересчитать score", "Проверить и пересчитать релевантность вакансий"],
  "refresh-button": ["Обновить список", "Заново загрузить очередь вакансий с текущими фильтрами"],
  "ai-search-button": ["AI-поиск", "Найти вакансии по смысловому запросу"],
  "export-csv-button": ["Экспорт CSV", "Скачать текущий список вакансий в CSV"],
  "setup-refresh-button": ["Обновить", "Проверить готовность настроек и AI-маршрутов"],
  "ai-test-button": ["Проверить AI", "Запустить безопасную dry run проверку AI"],
  "setup-import-wo-preview-button": ["Предпросмотр", "Показать, что будет импортировано без записи"],
  "setup-import-wo-apply-button": ["Импортировать", "Применить редактированный импорт в локальный проект"],
  "onboarding-refresh-button": ["Обновить", "Перезагрузить вопросы и статус профиля"],
  "onboarding-submit-button": ["Сохранить ответ", "Сохранить ответ и обновить карту кандидата"],
  "candidate-refresh-button": ["Обновить", "Перезагрузить факты и полноту профиля"],
  "resume-variant-button": ["Собрать вариант", "Создать адаптацию резюме под вакансию"],
  "application-preview-button": ["Предпросмотр", "Собрать черновик отклика перед отправкой"],
  "external-apply-dry-run-button": ["Проверить форму", "Прогнать внешнюю форму без отправки"],
  "external-apply-confirm-button": ["Подтвердить внешне", "Подтвердить внешний отклик после проверки"],
  "campaign-refresh-button": ["Обновить", "Перезагрузить кампании и статус паузы"],
  "campaign-plan-button": ["План HH", "Собрать безопасный план HH-кампании"],
  "external-campaign-plan-button": ["План внешних", "Собрать план для внешних источников"],
  "external-campaign-run-button": ["Подтвердить внешние", "Запустить внешний план только после проверки"],
  "campaign-confirm-run-button": ["Подтвердить запуск", "Запустить HH-кампанию после preflight"],
  "campaign-kill-switch-button": ["Стоп", "Немедленно поставить кампании на паузу"],
  "campaign-resume-button": ["Возобновить", "Снять паузу с кампаний"],
  "pipeline-refresh-button": ["Обновить", "Перезагрузить статус воронки"],
  "pipeline-prep-pack-button": ["Пакет подготовки", "Собрать материалы для текущего этапа"],
  "pipeline-schedule-followup-button": ["Запланировать follow-up", "Создать напоминание по выбранному этапу"],
  "interview-prep-pack-button": ["Собрать подготовку", "Сгенерировать вопросы и план подготовки"],
  "replay-refresh-button": ["Загрузить", "Загрузить историю действий по фильтрам"],
  "replay-export-button": ["Экспорт Markdown", "Скачать историю действий как Markdown"],
  "audit-security-refresh-button": ["Обновить", "Перезагрузить аудит безопасности"],
  "audit-security-redaction-button": ["Сканировать", "Проверить текст на секреты и приватные данные"],
  "browser-lab-status-button": ["Статус", "Проверить состояние браузерной сессии"],
  "browser-lab-open-login-button": ["План входа", "Открыть план безопасного входа в источник"],
  "browser-lab-import-har-button": ["Импорт HAR", "Импортировать HAR для настройки внешней формы"],
  "browser-lab-map-form-button": ["Сопоставить форму", "Сопоставить поля формы с профилем кандидата"],
  "browser-lab-dry-run-button": ["Dry run", "Проверить заполнение формы без отправки"],
  "browser-lab-execute-dry-run-button": ["Заполнить и скриншот", "Заполнить форму в dry run и сохранить снимок"],
  "source-sync-button": ["Синхронизировать", "Запустить синхронизацию выбранного источника"],
  "source-test-button": ["Тест", "Проверить выбранный источник без массового импорта"],
  "source-certification-plan-button": ["План evidence", "Показать, чего не хватает для сертификации"],
  "source-external-target-button": ["Настроить", "Сохранить endpoint внешнего отклика"],
  "source-external-har-button": ["Настроить из HAR", "Извлечь настройки отклика из HAR"],
  "source-redaction-scan-button": ["Сканировать", "Проверить payload и текст на приватные данные"],
  "source-certification-evidence-button": ["Записать evidence", "Сохранить доказательство готовности источника"],
  "source-certification-promote-button": ["Повысить уровень", "Повысить сертификацию источника"],
  "open-source-setup-button": ["Подключить источник", "Открыть пошаговый мастер подключения площадки"],
  "sidebar-source-setup-button": ["Подключить источник", "Открыть простой мастер подключения площадки"],
  "source-setup-refresh-button": ["Обновить", "Перезагрузить статус подключения источника"],
  "source-setup-run-next": ["Выполнить следующий шаг", "Запустить безопасную встроенную проверку из мастера"],
  "job-detail-load-button": ["Загрузить", "Открыть карточку вакансии по ID"],
  "theme-toggle-button": ["Переключить тему", "Сменить светлую и тёмную тему"],
  "open-onboarding-view": ["Заполнить профиль", "Перейти к вопросам онбординга"],
  "dismiss-onboarding-guide": ["Скрыть", "Скрыть подсказки первого запуска на этом устройстве"],
  "chat-send-button": ["Отправить", "Отправить сообщение AI-ассистенту (Enter)"],
  "add-event-button": ["Событие", "Добавить собеседование, звонок или напоминание"],
  "save-event-button": ["Сохранить", "Сохранить событие в календарь"],
  "agent-refresh-button": ["Обновить", "Перезагрузить весь статус HH-агента"],
  "agent-digest-button": ["Дайджест", "Собрать свежую сводку по HH-аккаунту"],
  "agent-save-template-button": ["Сохранить", "Сохранить шаблон письма"],
  "agent-save-blacklist-button": ["Сохранить", "Добавить работодателя в чёрный список"],
  "agent-resume-preview-button": ["Предпросмотр", "Собрать черновик резюме из markdown-шаблона"],
  "agent-batch-matrix-button": ["Собрать", "Построить матрицу пресетов кампаний"],
  "hh-lab-run-button": ["Выполнить", "Отправить запрос к HH API"],
  "hh-lab-save-snippet-button": ["Сохранить запрос", "Сохранить текущий запрос как сниппет"],
  "add-resume-button": ["Добавить резюме", "Создать новое резюме вручную"],
  "resume-import-button": ["Импортировать", "Импортировать резюме из файла"],
  "save-resume-button": ["Сохранить", "Сохранить резюме"],
  "save-profile-button": ["Сохранить профиль", "Сохранить настройки профиля поиска"],
  "rescore-after-save": ["Пересчитать score", "Пересчитать релевантность после изменений"],
  "save-ai-button": ["Сохранить настройки AI", "Сохранить ключ, модель и бэкенд AI"],
  "save-auto-sync-button": ["Сохранить", "Включить или выключить автосинхронизацию"],
  "save-token-button": ["Обновить токен", "Сохранить токен доступа HH локально"],
  "add-search-button": ["Поиск", "Добавить сохранённый поиск с уведомлениями"],
  "save-search-button": ["Сохранить", "Сохранить поисковый запрос"],
  "check-ghost-button": ["Проверить", "Найти вакансии без ответа 7+ дней"],
  "save-config-button": ["Сохранить JSON", "Сохранить всю конфигурацию (для опытных)"],
  "refresh-stats-button": ["Обновить", "Пересчитать статистику и воронку"],
  "load-trends-button": ["Анализировать", "Собрать AI-обзор трендов рынка"],
};

// Подсказки для вкладок (без отдельных id): по data-атрибуту.
const TAB_HINTS = {
  resume: "Советы, ATS-резюме и аудит под вакансию",
  analysis: "AI-оценка соответствия, саммари и питч опыта",
  interview: "Вероятные вопросы на собеседовании",
  "letter-panel": "Сопроводительное письмо: шаблон и AI-версия",
  notes: "Личные заметки по вакансии",
  dashboard: "Сводка, операции и последний дайджест HH",
  inbox: "Очередь задач, outbox и webhooks",
  approvals: "Очередь согласований действий агента",
  runs: "Запуски операций и логи",
  "api-lab": "Песочница HH API с сохранением сниппетов",
  templates: "Шаблоны сопроводительных писем",
  "resume-builder": "Сборка резюме и матрица кампаний",
  blacklist: "Чёрный список работодателей",
  events: "События и задачи из переписки HH",
  settings: "Локальный снимок preflight агента",
};

// Русские названия категорий профиля кандидата.
const CATEGORY_RU = {
  identity: "О себе",
  skills: "Навыки",
  target: "Цель поиска",
  experience: "Опыт",
  portfolio: "Портфолио",
  constraints: "Ограничения",
  writing_style: "Стиль письма",
  resume_assets: "Резюме",
  permissions: "Разрешения",
};

// Русские формулировки вопросов онбординга (по id, бэкенд отдаёт английский).
const ONBOARDING_RU = {
  identity: {
    title: "О себе",
    prompt: "Кто ты, в каком городе, какие языки знаешь и в каком часовом поясе работаешь?",
    example: "Например: Алексей, Москва, русский и английский (B2), часовой пояс МСК (UTC+3).",
  },
  stack: {
    title: "Технологии",
    prompt: "Какие технологии, инструменты, фреймворки, базы данных и облака ты реально знаешь? Пиши честно.",
    example: "Например: Python, FastAPI, Django, PostgreSQL, Redis, Docker, немного Kubernetes.",
  },
  roles: {
    title: "Желаемые роли",
    prompt: "Какие должности, грейд (junior/middle/senior), сферы и типы задач тебе подходят?",
    example: "Например: Backend-разработчик (middle/senior), финтех и SaaS, без поддержки legacy на 1С.",
  },
  experience: {
    title: "Опыт работы",
    prompt: "Опиши реальный опыт: компании, проекты, какой стек использовал и какие были результаты в цифрах.",
    example: "Например: 4 года backend. В X построил API на FastAPI, ускорил отчёты в 3 раза, вёл 2 джунов.",
  },
  projects: {
    title: "Проекты",
    prompt: "Какие проекты можно показать, обсудить или дать на них ссылку как доказательство?",
    example: "Например: pet-проект на GitHub (ссылка), коммерческий проект X (под NDA, расскажу устно).",
  },
  achievements: {
    title: "Достижения",
    prompt: "Какие достижения можно подтвердить цифрами, ссылками, рекомендациями или артефактами?",
    example: "Например: сократил расходы на инфраструктуру на 30%, доклад на митапе (ссылка на видео).",
  },
  forbidden_claims: {
    title: "Чего НЕ писать",
    prompt: "Что Work Hunter никогда не должен указывать в резюме, письмах и откликах? (то, чего ты не умеешь)",
    example: "Например: не приписывать промышленный Kubernetes, не указывать английский C1, не врать про годы.",
  },
  salary_format: {
    title: "Зарплата и формат",
    prompt: "Какие условия по зарплате, городу, удалёнке, релокации, графику и формату работы важны?",
    example: "Например: от 250к на руки, только удалёнка, без релокации, полный день, без ночных дежурств.",
  },
  avoid: {
    title: "Что исключить",
    prompt: "Какие компании, сферы, темы или типы вакансий стоит исключить из поиска?",
    example: "Например: без гемблинга и беттинга, без аутстаффа, не рассматриваю стартапы без зарплаты.",
  },
  writing_style: {
    title: "Стиль общения",
    prompt: "В каком тоне писать сопроводительные письма и сообщения, а чего избегать?",
    example: "Например: коротко и по делу, на «вы», без канцелярита и без воды, дружелюбно но профессионально.",
  },
  resume_assets: {
    title: "Готовые резюме",
    prompt: "Какие резюме уже есть: ID резюме на HH, PDF, DOCX, Markdown или JSON?",
    example: "Например: основное резюме на hh.ru (Backend Python), PDF на английском в папке /resumes.",
  },
  apply_permissions: {
    title: "Разрешения на отклик",
    prompt: "Где Work Hunter может входить в аккаунт, хранить профиль браузера, готовить и (после подтверждения) отправлять отклики?",
    example: "Например: можно готовить отклики на hh.ru и getmatch, отправлять только после моего подтверждения.",
  },
};


const $ = (selector) => document.querySelector(selector);

function setUiError(message = "") {
  const box = $("#ui-error-line");
  if (!box) return;
  box.textContent = message;
  box.hidden = !message;
}

// Русские статусы вакансий + цветные бейджи.
const STATUS_RU = {
  new: ["Новая", "new"],
  saved: ["В избранном", "saved"],
  applied: ["Откликнулся", "applied"],
  hidden: ["Скрыта", "hidden"],
  ghosted: ["Тишина", "ghosted"],
  viewed: ["Просмотрена", "viewed"],
  response: ["Ответ", "applied"],
  rejected: ["Отказ", "rejected"],
  interview: ["Интервью", "applied"],
  offer: ["Оффер", "applied"],
};

function statusBadge(status) {
  const key = String(status || "new").toLowerCase();
  const entry = STATUS_RU[key] || [status || "new", ""];
  return `<span class="status-badge status-${escapeAttr(entry[1] || key)}">${escapeHtml(entry[0])}</span>`;
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
  toast(message, "error");
}

// ── Тосты (всплывающие уведомления) ──────────
const TOAST_ICONS = { success: "check-circle-2", error: "alert-triangle", info: "info" };

function toast(message, type = "info", timeout = 4200, action = null) {
  if (!message) return;
  let host = document.getElementById("toast-container");
  if (!host) {
    host = document.createElement("div");
    host.id = "toast-container";
    host.className = "toast-container";
    host.setAttribute("aria-live", "polite");
    document.body.appendChild(host);
  }
  const item = document.createElement("div");
  item.className = `toast toast-${type}`;
  item.setAttribute("role", type === "error" ? "alert" : "status");
  const actionHtml = action && action.label
    ? `<button class="toast-action">${escapeHtml(action.label)}</button>`
    : "";
  item.innerHTML = `<i data-lucide="${TOAST_ICONS[type] || "info"}"></i><span>${escapeHtml(String(message))}</span>${actionHtml}<button class="toast-close" aria-label="Закрыть">×</button>`;
  host.appendChild(item);
  if (window.lucide) lucide.createIcons();
  const close = () => {
    item.classList.add("leaving");
    setTimeout(() => item.remove(), 220);
  };
  item.querySelector(".toast-close")?.addEventListener("click", close);
  if (action && action.onClick) {
    item.querySelector(".toast-action")?.addEventListener("click", () => {
      try { action.onClick(); } finally { close(); }
    });
  }
  if (timeout > 0) setTimeout(close, timeout);
}


async function loadJobs() {
  const source = $("#source-filter").value;
  const minScore = $("#min-score-filter").value || "0";
  const params = new URLSearchParams({ limit: "200", min_score: minScore });
  if (source) params.set("source", source);
  state.jobs = await api(`/api/inbox?${params.toString()}`);
  renderJobs();
  updateSummary();
  renderOnboardingGuide();
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
      <td>${statusBadge(job.status)}</td>
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
  renderOnboardingGuide();
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
      <button onclick="markSelected('saved')" title="Добавить вакансию в избранное">Сохранить</button>
      <button onclick="markSelected('hidden')" title="Скрыть вакансию из списка">Скрыть</button>
      <button onclick="markSelected('applied')" title="Отметить, что отклик отправлен">Откликнулся</button>
      <button onclick="prepareLetter()" title="Сгенерировать письмо по шаблону">Подготовить письмо</button>
      <button onclick="prepareLetterAi()" title="Сгенерировать письмо с помощью AI">AI-письмо</button>
      <button onclick="fetchFullDescription()" title="Загрузить полный текст вакансии с площадки">Полное описание</button>
      <button onclick="applyHh(true)" title="Собрать безопасный план отклика на HH (без отправки)">План отклика HH</button>
      <button onclick="applyHh(false)" class="primary" title="Отправить реальный отклик на HH (с подтверждением)">Откликнуться на HH</button>
      <button onclick="shareToTelegram()" title="Поделиться вакансией в Telegram"><i data-lucide="send"></i>Telegram</button>
      <button onclick="smartClassify()" title="Классифицировать вакансию через AI">AI-классификация</button>
      <button onclick="parseJobStructure()" title="Разобрать вакансию на структуру">Структура</button>
      <button onclick="runGapAnalysis()" title="Сравнить вакансию с активным резюме">Gap-анализ</button>
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
      <span class="chip">итог ${escapeHtml(String(score?.total_score ?? "-"))}</span>
      <span class="chip">название ${escapeHtml(String(score?.title_score ?? "-"))}</span>
      <span class="chip">навыки ${escapeHtml(String(score?.skills_score ?? "-"))}</span>
      <span class="chip">зарплата ${escapeHtml(String(score?.salary_score ?? "-"))}</span>
      <span class="chip">удалёнка ${escapeHtml(String(score?.remote_score ?? "-"))}</span>
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
    output.innerHTML = "<p>Выбери вакансию во вкладке «Вакансии» или введи её ID.</p>";
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
    const added = result?.added ?? result?.new ?? result?.imported;
    $("#summary-line").textContent = `Синхронизация завершена: ${JSON.stringify(result)}`;
    toast(added != null ? `Синхронизация завершена · новых: ${added}` : "Синхронизация завершена", "success");
    await loadJobs();
    await loadSources();
  } catch (err) {
    toast("Ошибка синхронизации: " + err.message, "error");
  } finally {
    setBusy("#sync-button", false);
  }
}

async function scoreJobs() {
  setBusy("#score-button", true);
  try {
    const result = await api("/api/score", { method: "POST", body: "{}" });
    $("#summary-line").textContent = `Пересчитано: ${result.scored}`;
    toast(`Score пересчитан для ${result.scored} вакансий`, "success");
    await loadJobs();
  } catch (err) {
    toast("Ошибка пересчёта: " + err.message, "error");
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

  if (!confirm("Будет отправлен реальный отклик на HH из твоего аккаунта. Продолжить?")) {
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
    toast(`Профиль переключён: ${profileId}`, "success");
  } catch (err) {
    toast("Ошибка переключения профиля: " + err.message, "error");
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
    toast("Профиль сохранён", "success");
  } catch (err) {
    toast("Ошибка сохранения профиля: " + err.message, "error");
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
    if ($("#codex-command-input")) $("#codex-command-input").value = state.config.ai.codex_command || "codex";
    if ($("#codex-model-input")) $("#codex-model-input").value = state.config.ai.codex_model || "";
    if ($("#codex-reasoning-select")) $("#codex-reasoning-select").value = state.config.ai.codex_reasoning || "";
    if ($("#codex-server-input")) $("#codex-server-input").value = state.config.ai.codex_server_url || "";
    updateAiBackendFields();
  }

  if (state.config && state.config.ui && state.config.ui.auto_sync) {
    const minutes = state.config.ui.auto_sync || 0;
    $("#auto-sync-interval").value = minutes;
    if (minutes > 0) startAutoSync(minutes);
  }
}

async function saveConfig() {
  try {
    const parsed = JSON.parse($("#config-editor").value);
    state.config = await api("/api/config", { method: "POST", body: JSON.stringify(parsed) });
    $("#config-editor").value = JSON.stringify(state.config, null, 2);
    toast("Конфигурация сохранена", "success");
  } catch (err) {
    toast("Ошибка JSON: " + err.message, "error");
  }
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
  toast("Настройки HH сохранены", "success");
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
  state.config.ai.codex_command = $("#codex-command-input")?.value.trim() || "codex";
  state.config.ai.codex_model = $("#codex-model-input")?.value.trim() || "";
  state.config.ai.codex_reasoning = $("#codex-reasoning-select")?.value || "";
  state.config.ai.codex_server_url = $("#codex-server-input")?.value.trim() || "";

  state.config = await api("/api/config", { method: "POST", body: JSON.stringify(state.config) });
  $("#config-editor").value = JSON.stringify(state.config, null, 2);
  toast("Настройки AI сохранены", "success");
}

const AI_BACKEND_NOTES = {
  direct: "Нужен ключ API (например, OpenRouter). Платишь за токены по факту.",
  codex: "Использует Codex CLI и твою подписку ChatGPT. Один раз выполни в терминале: codex login. Ключ API не нужен.",
  opencode: "Использует OpenCode (CLI или локальный сервер). Ключ API не нужен.",
  codex_server: "Запускает локальный codex app-server (через stdio). Тоже нужен вход: codex login. Экспериментально.",
};

function updateAiBackendFields() {
  const backend = $("#ai-backend-select")?.value || "direct";
  document.querySelectorAll("[data-ai-backend]").forEach((el) => {
    const backends = (el.getAttribute("data-ai-backend") || "").split(/\s+/);
    el.style.display = backends.includes(backend) ? "" : "none";
  });
  const note = $("#ai-backend-note");
  if (note) note.textContent = AI_BACKEND_NOTES[backend] || "";
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
  const label = busy ? "Работаю..." : button.dataset.label || button.dataset.idleLabel || button.textContent.trim();
  setButtonCopy(button, label, button.title);
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

function setButtonCopy(button, label, hint = "") {
  if (!button || !label) return;
  const icon = button.querySelector("svg, i[data-lucide]");
  const iconClone = icon ? icon.cloneNode(true) : null;
  button.textContent = "";
  if (iconClone) {
    button.appendChild(iconClone);
    button.appendChild(document.createTextNode(" "));
  }
  button.appendChild(document.createTextNode(label));
  button.dataset.label = label;
  button.dataset.idleLabel = label;
  button.setAttribute("aria-label", hint || label);
  button.setAttribute("title", hint || label);
}

function setFieldCopy(controlId, label, hint = "") {
  const control = document.getElementById(controlId);
  if (!control) return;
  const wrapper = control.closest("label");
  if (wrapper) {
    let wroteLabel = false;
    for (const node of Array.from(wrapper.childNodes)) {
      if (node === control || (node.contains && node.contains(control))) break;
      if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
        node.textContent = `${label} `;
        wroteLabel = true;
        break;
      }
    }
    if (!wroteLabel) wrapper.insertBefore(document.createTextNode(`${label} `), wrapper.firstChild);
    if (hint && !wrapper.querySelector(`[data-hint-for="${controlId}"]`)) {
      const help = document.createElement("span");
      help.className = "field-hint";
      help.dataset.hintFor = controlId;
      help.textContent = hint;
      wrapper.appendChild(help);
    }
  }
  control.setAttribute("aria-label", label);
  control.setAttribute("title", hint || label);
}

function applyRussianUxCopy() {
  for (const button of document.querySelectorAll(".nav-button")) {
    const copy = UX_RU_COPY.nav[button.dataset.view];
    if (!copy) continue;
    setButtonCopy(button, copy[0], copy[1]);
  }
  for (const [selector, label] of Object.entries(UX_RU_COPY.headings)) {
    const element = document.querySelector(selector);
    if (element) element.textContent = label;
  }
  for (const [controlId, copy] of Object.entries(UX_RU_COPY.fields)) {
    setFieldCopy(controlId, copy[0], copy[1]);
  }
  const summary = $("#summary-line");
  if (summary && !state.jobs.length) {
    summary.textContent = "Сначала заполни профиль и синхронизируй источники, затем отсортируй вакансии по score.";
  }
}

function enhanceButtonHints(root = document) {
  for (const [id, copy] of Object.entries(CONTROL_HINTS)) {
    if (id === "source-setup-run-next") continue;
    const button = root.getElementById ? root.getElementById(id) : document.getElementById(id);
    if (button) setButtonCopy(button, copy[0], copy[1]);
  }
  for (const button of root.querySelectorAll ? root.querySelectorAll("button") : []) {
    const label = button.textContent.trim() || button.getAttribute("aria-label") || "Действие";
    if (!button.dataset.label) button.dataset.label = label;
    if (!button.dataset.idleLabel) button.dataset.idleLabel = button.dataset.label;
    if (!button.hasAttribute("aria-label")) button.setAttribute("aria-label", label);
    if (!button.hasAttribute("title")) button.setAttribute("title", label);
  }
  enhanceTabHints(root);
}

function enhanceTabHints(root = document) {
  const scope = root.querySelectorAll ? root : document;
  for (const tab of scope.querySelectorAll(".ai-tab, .agent-tab")) {
    const key = tab.dataset.aiPanel || tab.dataset.agentPanel;
    const hint = TAB_HINTS[key];
    tab.setAttribute("role", "tab");
    if (hint && !tab.dataset.hinted) {
      tab.setAttribute("title", hint);
      tab.setAttribute("aria-label", `${tab.textContent.trim()} — ${hint}`);
      tab.dataset.hinted = "1";
    }
  }
}

function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (event) => {
    // Escape: закрыть открытые формы, снять выделение, убрать фокус с поля.
    if (event.key === "Escape") {
      const sourceSetup = document.getElementById("source-setup-modal");
      if (sourceSetup && !sourceSetup.hidden) { closeSourceSetupWizard(); event.preventDefault(); return; }
      const wizard = document.getElementById("wizard-modal");
      if (wizard && !wizard.hidden) { closeWizard(); event.preventDefault(); return; }
      let acted = false;
      for (const formId of ["event-form", "resume-form", "search-form"]) {
        const form = document.getElementById(formId);
        if (form && form.style.display !== "none") { form.style.display = "none"; acted = true; }
      }
      if (typeof selectedJobIds !== "undefined" && selectedJobIds.size) { clearBulkSelection(); acted = true; }
      if (isTypingTarget(document.activeElement)) { document.activeElement.blur(); acted = true; }
      if (acted) event.preventDefault();
      return;
    }
    if (isTypingTarget(event.target)) return;
    // "/": быстрый фокус на AI-поиск.
    if (event.key === "/") {
      const search = $("#ai-search-input");
      if (search) {
        activateView("inbox");
        search.focus();
        event.preventDefault();
      }
      return;
    }
    // "?": показать/скрыть гид первого запуска.
    if (event.key === "?") {
      const dismissed = localStorage.getItem("work-hunter-onboarding-dismissed") === "true";
      if (dismissed) reopenOnboardingGuide();
      else dismissOnboardingGuide();
      event.preventDefault();
    }
  });
}

function updateNavA11y(activeView) {
  for (const button of document.querySelectorAll(".nav-button")) {
    if (button.dataset.view === activeView) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  }
}

function onboardingSteps(status = {}, facts = []) {
  const hasFacts = Array.isArray(facts) && facts.length > 0;
  const answered = Number(status.answered_count || status.answers || status.completed_questions || 0);
  const hasProfile = Boolean(state.profile?.data && Object.keys(state.profile.data).length);
  const hasJobs = state.jobs.length > 0;
  const ai = state.config?.ai || {};
  const aiReady = Boolean(ai.api_key || (ai.backend === "opencode" && (ai.opencode_model || ai.opencode_command)));
  return [
    {
      title: "Ответь на вопросы профиля",
      body: "Система поймёт опыт, ограничения, желаемые роли и сильные доказательства.",
      done: hasFacts || answered > 0 || hasProfile,
      view: "onboarding",
      action: "Заполнить",
    },
    {
      title: "Подключи AI",
      body: "Добавь ключ OpenRouter или OpenCode — для писем, анализа и подсказок. Можно пропустить.",
      done: aiReady,
      view: "settings",
      action: "Настроить",
      optional: true,
    },
    {
      title: "Синхронизируй источники",
      body: "Загрузи свежие вакансии с подключённых площадок и проверь готовность адаптеров.",
      done: hasJobs,
      view: "sources",
      action: "Открыть источники",
    },
    {
      title: "Выбери вакансию",
      body: "Клик по строке откроет детали, AI-панели, письмо и действия по статусу.",
      done: Boolean(state.selectedId),
      view: "inbox",
      action: "К списку",
    },
    {
      title: "Проверь черновик отклика",
      body: "Собери предпросмотр перед реальным откликом: письмо, резюме, payload и форму.",
      done: false,
      view: "application-preview",
      action: "Собрать",
    },
  ];
}

function renderOnboardingGuide(status = {}, facts = []) {
  const guide = $("#onboarding-guide");
  const strip = $("#ux-help-strip");
  const list = $("#onboarding-guide-steps");
  const progress = $("#onboarding-progress span");
  const reopen = $("#reopen-onboarding-guide");
  if (!guide || !strip || !list || !progress) return;
  const dismissed = localStorage.getItem("work-hunter-onboarding-dismissed") === "true";
  if (dismissed) {
    guide.hidden = true;
    strip.hidden = true;
    if (reopen) reopen.hidden = false;
    return;
  }
  if (reopen) reopen.hidden = true;
  const steps = onboardingSteps(status, facts);
  const required = steps.filter((step) => !step.optional);
  const done = required.filter((step) => step.done).length;
  const pct = Math.round((done / required.length) * 100);
  progress.style.width = `${pct}%`;
  const progressLabel = $("#onboarding-progress");
  if (progressLabel) progressLabel.setAttribute("aria-valuenow", String(pct));
  const allDone = done === required.length;
  list.innerHTML = steps.map((step, index) => `
    <article class="onboarding-step${step.done ? " done" : ""}">
      <span class="agent-badge ${step.done ? "ready" : ""}">${step.done ? "готово" : `шаг ${index + 1}`}</span>
      <strong>${escapeHtml(step.title)}${step.optional ? ' <span class="step-optional">(по желанию)</span>' : ""}</strong>
      <p>${escapeHtml(step.body)}</p>
      <button data-onboarding-view="${escapeAttr(step.view)}">${step.done ? "Открыть" : escapeHtml(step.action || "Перейти")}</button>
    </article>
  `).join("");
  const headNote = guide.querySelector(".onboarding-guide-head p");
  if (headNote) {
    headNote.textContent = allDone
      ? "Отлично! Все основные шаги пройдены — можно откликаться на вакансии."
      : `Пройдено ${done} из ${required.length}. Мини-карта, чтобы не потеряться в большом рабочем столе.`;
  }
  const onInbox = $("#view-inbox")?.classList.contains("active") !== false;
  guide.hidden = !onInbox;
  strip.hidden = !onInbox;
  enhanceButtonHints(guide);
}

function dismissOnboardingGuide() {
  localStorage.setItem("work-hunter-onboarding-dismissed", "true");
  const guide = $("#onboarding-guide");
  const strip = $("#ux-help-strip");
  const reopen = $("#reopen-onboarding-guide");
  if (guide) guide.hidden = true;
  if (strip) strip.hidden = true;
  if (reopen) reopen.hidden = false;
  toast("Гид скрыт. Вернуть его можно кнопкой «Гид» вверху.", "info");
}

function reopenOnboardingGuide() {
  localStorage.removeItem("work-hunter-onboarding-dismissed");
  renderOnboardingGuide();
  activateView("inbox");
}

// ── Мастер первого отклика ──────────
const wizardState = { step: 0, jobId: null, letter: "" };
const WIZARD_TITLES = ["Профиль", "Поиск", "Вакансия", "Письмо", "Готово"];
const WIZARD_TOTAL = WIZARD_TITLES.length;

function openWizard() {
  wizardState.step = 0;
  wizardState.jobId = state.selectedId || null;
  wizardState.letter = "";
  const modal = $("#wizard-modal");
  if (!modal) return;
  modal.hidden = false;
  document.body.classList.add("wizard-open");
  renderWizard();
}

function closeWizard() {
  const modal = $("#wizard-modal");
  if (modal) modal.hidden = true;
  document.body.classList.remove("wizard-open");
}

function wizardNext() {
  if (wizardState.step < WIZARD_TOTAL - 1) {
    wizardState.step += 1;
    renderWizard();
  } else {
    closeWizard();
  }
}

function wizardBack() {
  if (wizardState.step > 0) {
    wizardState.step -= 1;
    renderWizard();
  }
}

function wizardTopJobs() {
  return [...state.jobs]
    .sort((a, b) => (b.score?.total_score || 0) - (a.score?.total_score || 0))
    .slice(0, 6);
}

function renderWizard() {
  const body = $("#wizard-body");
  if (!body) return;
  const stepsEl = $("#wizard-steps");
  if (stepsEl) {
    stepsEl.innerHTML = WIZARD_TITLES.map((title, index) => {
      const cls = index === wizardState.step ? "active" : index < wizardState.step ? "done" : "";
      return `<span class="wizard-dot ${cls}">${index + 1}. ${escapeHtml(title)}</span>`;
    }).join("");
  }
  const progress = $("#wizard-progress");
  if (progress) progress.textContent = `Шаг ${wizardState.step + 1} из ${WIZARD_TOTAL}`;
  const back = $("#wizard-back");
  if (back) back.disabled = wizardState.step === 0;
  const next = $("#wizard-next");
  if (next) next.textContent = wizardState.step === WIZARD_TOTAL - 1 ? "Закрыть" : "Далее";

  if (wizardState.step === 0) {
    const hasProfile = Boolean(state.profile?.data && Object.keys(state.profile.data).length);
    body.innerHTML = `
      <h3>Шаг 1. Профиль кандидата</h3>
      <p>Чтобы подбор и письма были точными, сначала заполни профиль: опыт, навыки, пожелания по зарплате и формату.</p>
      <div class="wizard-status ${hasProfile ? "ok" : ""}">${hasProfile ? "✓ Профиль уже заполнен — можно идти дальше." : "Профиль ещё пустой."}</div>
      <div class="wizard-actions">
        <button class="primary" onclick="wizardGoOnboarding()"><i data-lucide="user-check"></i>Заполнить профиль</button>
      </div>
      <p class="meta">Уже заполнял раньше? Просто нажми «Далее».</p>`;
  } else if (wizardState.step === 1) {
    body.innerHTML = `
      <h3>Шаг 2. Найти вакансии</h3>
      <p>Загрузим свежие вакансии из подключённых источников. Это безопасно.</p>
      <div class="wizard-actions">
        <button id="wizard-sync" class="primary" onclick="wizardSync()"><i data-lucide="refresh-cw"></i>Синхронизировать</button>
      </div>
      <div id="wizard-sync-status" class="wizard-status">${state.jobs.length ? `Сейчас в списке: ${state.jobs.length} вакансий.` : "Вакансий пока нет — нажми «Синхронизировать»."}</div>`;
  } else if (wizardState.step === 2) {
    const jobs = wizardTopJobs();
    body.innerHTML = `
      <h3>Шаг 3. Выбери вакансию</h3>
      <p>Лучшие по соответствию. Нажми на подходящую.</p>
      ${jobs.length ? `<div class="wizard-joblist">${jobs.map((job) => `
        <button class="wizard-job${job.id === wizardState.jobId ? " selected" : ""}" onclick="wizardPickJob(${job.id})">
          <span class="score">${escapeHtml(String(job.score?.total_score ?? "-"))}</span>
          <span class="wizard-job-main">
            <strong>${escapeHtml(job.title || "Без названия")}</strong>
            <span class="meta">${escapeHtml([job.company, job.source].filter(Boolean).join(" · "))}</span>
          </span>
        </button>`).join("")}</div>` : `<div class="wizard-status">Список пуст. Вернись на шаг 2 и синхронизируй источники.</div>`}`;
  } else if (wizardState.step === 3) {
    if (!wizardState.jobId) {
      body.innerHTML = `<h3>Шаг 4. Сопроводительное письмо</h3><div class="wizard-status">Сначала выбери вакансию на шаге 3.</div>`;
    } else {
      body.innerHTML = `
        <h3>Шаг 4. Сопроводительное письмо</h3>
        <p>Сгенерируй черновик и при желании поправь. Это ещё не отправка.</p>
        <div class="wizard-actions">
          <button onclick="wizardLetter('template')"><i data-lucide="file-text"></i>Шаблон</button>
          <button class="primary" onclick="wizardLetter('ai')"><i data-lucide="sparkles"></i>AI-письмо</button>
        </div>
        <textarea id="wizard-letter" class="wizard-letter" rows="9" placeholder="Здесь появится письмо..." oninput="wizardState.letter=this.value">${escapeHtml(wizardState.letter)}</textarea>`;
    }
  } else {
    const job = state.jobs.find((item) => item.id === wizardState.jobId);
    body.innerHTML = `
      <h3>Готово! Последний шаг — отклик</h3>
      <p>Откроем карточку вакансии${job ? ` «${escapeHtml(job.title || "")}»` : ""}. Там есть кнопка <b>«Откликнуться на HH»</b> — реальный отклик уходит только после твоего подтверждения.</p>
      <div class="wizard-status ok">Письмо ${wizardState.letter.trim() ? "готово и будет подставлено в карточку." : "можно дописать в карточке."}</div>
      <div class="wizard-actions">
        <button onclick="wizardCheckPlan()" id="wizard-check-plan"><i data-lucide="scan-line"></i>Проверить план отклика</button>
        <button class="primary" onclick="wizardFinish()"><i data-lucide="external-link"></i>Открыть вакансию</button>
      </div>
      <pre id="wizard-plan-output" class="agent-json" hidden></pre>
      <p class="meta">«Проверить план отклика» — это безопасная проверка без отправки: покажет, всё ли готово.</p>`;
  }
  refreshIcons();
}

async function wizardCheckPlan() {
  if (!wizardState.jobId) return;
  const out = $("#wizard-plan-output");
  setBusy("#wizard-check-plan", true);
  try {
    const result = await api(`/api/jobs/${wizardState.jobId}/apply-plan`, {
      method: "POST",
      body: JSON.stringify({ letter: wizardState.letter || "" }),
    });
    if (out) {
      out.hidden = false;
      out.textContent = JSON.stringify(result, null, 2);
    }
    const ready = result?.status === "ready";
    toast(ready ? "План готов — можно открывать вакансию и отправлять" : `Статус плана: ${result?.status || "см. детали"}`, ready ? "success" : "info");
  } catch (err) {
    if (out) { out.hidden = false; out.textContent = "Ошибка: " + err.message; }
    toast("Не удалось собрать план: " + err.message, "error");
  } finally {
    setBusy("#wizard-check-plan", false);
  }
}

function wizardGoOnboarding() {
  closeWizard();
  activateView("onboarding");
}

async function wizardSync() {
  setBusy("#wizard-sync", true);
  const status = $("#wizard-sync-status");
  try {
    await api("/api/sync", { method: "POST", body: JSON.stringify({ score: true }) });
    await loadJobs();
    if (status) status.textContent = `Готово! Найдено ${state.jobs.length} вакансий. Жми «Далее».`;
    toast(`Найдено ${state.jobs.length} вакансий`, "success");
  } catch (err) {
    if (status) status.textContent = "Ошибка синхронизации: " + err.message;
    toast("Ошибка синхронизации: " + err.message, "error");
  } finally {
    setBusy("#wizard-sync", false);
  }
}

function wizardPickJob(id) {
  wizardState.jobId = Number(id);
  renderWizard();
}

async function wizardLetter(kind) {
  if (!wizardState.jobId) return;
  const area = $("#wizard-letter");
  if (area) area.value = "Генерирую...";
  try {
    const path = kind === "ai" ? "letter-ai" : "letter";
    const draft = await api(`/api/jobs/${wizardState.jobId}/${path}`, { method: "POST", body: "{}" });
    wizardState.letter = draft.body || "";
    if (area) area.value = wizardState.letter;
  } catch (err) {
    if (area) area.value = "";
    toast("Не удалось сгенерировать письмо: " + err.message + (kind === "ai" ? " (проверь настройки AI)" : ""), "error");
  }
}

async function wizardFinish() {
  const jobId = wizardState.jobId;
  const letter = wizardState.letter;
  closeWizard();
  if (!jobId) { activateView("inbox"); return; }
  activateView("inbox", { load: false });
  await selectJob(jobId);
  if (letter && $("#letter-box")) $("#letter-box").value = letter;
  toast("Карточка вакансии открыта — отклик отправится только после подтверждения", "info", 6000);
}

// ── Мастер подключения источника ──────────
const SOURCE_SETUP_LANE_LABELS = {
  hh: "Самый простой путь: вход в HH, резюме, затем подтверждение отклика",
  certifiable_external: "Внешняя площадка: HAR, проверка, dry-run, затем подтверждение",
  manual_or_search_only: "Поиск и ручной отклик без обещания auto-apply",
};

const SOURCE_SETUP_GOAL_LABELS = {
  ready: "можно использовать",
  ready_to_certify: "можно включить",
  blocked: "нужно действие",
  manual_handoff: "ручной режим",
};

const SOURCE_SETUP_ACTION_LABELS = {
  preflight: "Проверить вход в HH",
  source_status: "Показать статус",
  source_sync: "Загрузить вакансии",
  source_test: "Проверить источник",
  browser_login_plan: "Показать план входа",
  har_import: "Подключить HAR-файл",
  redaction_scan: "Проверить секреты",
  dry_run: "Проверить форму без отправки",
  certification_audit: "Показать, что осталось",
  certify: "Включить после проверок",
};

const SOURCE_SETUP_SOURCE_LABELS = {
  hh: "HH.ru",
  habr: "Habr Career",
  geekjob: "GeekJob",
  getmatch: "Getmatch",
  hirehi: "Hirehi",
  careerspace: "CareerSpace",
  jabka: "Jabka",
  telegram: "Telegram",
  relocate_me: "Relocate.me",
  another_it: "Another IT",
  rvc: "RVC",
};

function openSourceSetupWizard() {
  const modal = $("#source-setup-modal");
  if (!modal) return;
  modal.hidden = false;
  document.body.classList.add("wizard-open");
  if (!state.sourceSetup.guide) {
    loadSourceSetupGuide();
  } else {
    renderSourceSetupGuide();
  }
}

function closeSourceSetupWizard() {
  const modal = $("#source-setup-modal");
  if (modal) modal.hidden = true;
  document.body.classList.remove("wizard-open");
}

function sourceSetupSource() {
  return $("#source-setup-source")?.value || "hh";
}

function sourceSetupLevel() {
  const value = Number($("#source-setup-level")?.value || 5);
  return value === 6 ? 6 : 5;
}

function sourceSetupSourceLabel(source) {
  const value = String(source || sourceSetupSource() || "hh");
  return SOURCE_SETUP_SOURCE_LABELS[value] || value;
}

function sourceSetupStepCounts(guide) {
  const steps = Array.isArray(guide?.steps) ? guide.steps : [];
  return {
    ready: steps.filter((step) => step.status === "ready").length,
    blocked: steps.filter((step) => step.status === "blocked").length,
    total: steps.length,
  };
}

function sourceSetupNeedsTechnicalInputs(guide) {
  return guide?.lane === "certifiable_external";
}

function sourceSetupPrimaryNote(guide) {
  if (guide?.lane === "hh") {
    return "Главная кнопка только проверит вход, резюме и очередь подтверждений. Реальный отклик останется на отдельном экране подтверждения.";
  }
  if (guide?.lane === "manual_or_search_only") {
    return "Для этой площадки мастер доводит до поиска и ручного отклика, без обещания автоматической отправки.";
  }
  return "Для внешней площадки сначала нужны HAR и dry-run. Cookies, токены и payload не выводятся в интерфейс без маскирования.";
}

function resetSourceSetupGuide() {
  state.sourceSetup.guide = null;
  state.sourceSetup.log = [];
  renderSourceSetupLog();
  loadSourceSetupGuide();
}

async function loadSourceSetupGuide() {
  const source = encodeURIComponent(sourceSetupSource());
  const level = sourceSetupLevel();
  const status = $("#source-setup-status");
  if (status) status.innerHTML = `<span class="agent-badge">загрузка</span><span class="meta">Проверяю ${escapeHtml(sourceSetupSourceLabel(sourceSetupSource()))}...</span>`;
  try {
    const guide = await api(`/api/source-setup/guide?source=${source}&level=${level}`);
    state.sourceSetup.guide = guide;
    appendSourceSetupLog("guide", guide);
    renderSourceSetupGuide();
  } catch (err) {
    renderActionError($("#source-setup-log"), err);
  }
}

function renderSourceSetupGuide() {
  const guide = state.sourceSetup.guide || {};
  const status = $("#source-setup-status");
  const steps = $("#source-setup-steps");
  const next = $("#source-setup-next-action");
  if (!status || !steps || !next) return;
  const sourceLabel = sourceSetupSourceLabel(guide.source);
  const lane = SOURCE_SETUP_LANE_LABELS[guide.lane] || guide.lane || "источник";
  const goal = SOURCE_SETUP_GOAL_LABELS[guide.goal_status] || guide.goal_status || "проверка";
  const badgeClass = guide.goal_status === "ready" ? "ready" : guide.goal_status === "blocked" ? "blocked" : "";
  const counts = sourceSetupStepCounts(guide);
  const countText = counts.total ? `${counts.ready} из ${counts.total} шагов готово` : "жду проверки";
  const technicalInputs = $(".source-setup-inputs");
  if (technicalInputs) {
    technicalInputs.hidden = !sourceSetupNeedsTechnicalInputs(guide);
    if (technicalInputs.hidden) technicalInputs.open = false;
  }
  status.innerHTML = `
    <div class="source-setup-status-main">
      <span class="meta">Подключаем</span>
      <strong>${escapeHtml(sourceLabel)}</strong>
      <span class="source-setup-status-text">${escapeHtml(lane)}</span>
    </div>
    <span class="agent-badge ${escapeAttr(badgeClass)}">${escapeHtml(goal)}</span>
    <span class="source-setup-count">${escapeHtml(countText)}</span>
    ${guide.can_certify ? '<span class="agent-badge ready">можно сертифицировать</span>' : ""}
  `;
  steps.innerHTML = (guide.steps || []).map((step) => `
    <article class="source-setup-step ${escapeAttr(step.status || "pending")}">
      <span class="agent-badge ${step.status === "ready" ? "ready" : step.status === "blocked" ? "blocked" : ""}">${escapeHtml(sourceSetupStatusLabel(step.status))}</span>
      <strong>${escapeHtml(step.title || step.id || "")}</strong>
      <p>${escapeHtml(step.body || "")}</p>
    </article>
  `).join("");
  const action = guide.next_action || {};
  const primaryAction = action.action || "";
  const primaryLabel = SOURCE_SETUP_ACTION_LABELS[primaryAction] || action.label || "Следующий шаг";
  const secondaryActions = (guide.available_actions || []).filter((item) => item !== primaryAction);
  const actionButtons = secondaryActions.map((item) => `
    <button data-source-setup-action="${escapeAttr(item)}">${escapeHtml(SOURCE_SETUP_ACTION_LABELS[item] || item)}</button>
  `).join("");
  next.innerHTML = `
    <span class="source-setup-kicker">Что сделать сейчас</span>
    <strong>${escapeHtml(primaryLabel)}</strong>
    <p>${escapeHtml(action.description || "Обнови статус, чтобы увидеть рекомендацию.")}</p>
    <p class="source-setup-primary-note">${escapeHtml(sourceSetupPrimaryNote(guide))}</p>
    ${secondaryActions.length ? `
      <details class="source-setup-actions-advanced">
        <summary>Расширенные проверки</summary>
        <div class="source-setup-actions">${actionButtons}</div>
      </details>
    ` : ""}
  `;
  const runNext = $("#source-setup-run-next");
  if (runNext) {
    runNext.dataset.nextSourceSetupAction = primaryAction || "source_status";
    runNext.disabled = !primaryAction;
    runNext.innerHTML = `<i data-lucide="play"></i>${escapeHtml(primaryLabel)}`;
    runNext.dataset.label = primaryLabel;
    runNext.dataset.idleLabel = primaryLabel;
    runNext.setAttribute("aria-label", primaryLabel);
    runNext.setAttribute("title", primaryLabel);
  }
  renderSourceSetupLog();
  enhanceButtonHints($("#source-setup-modal") || document);
  refreshIcons();
}

function sourceSetupStatusLabel(status) {
  const value = String(status || "pending");
  if (value === "ready") return "готово";
  if (value === "blocked") return "нужно действие";
  return "ожидает";
}

function appendSourceSetupLog(kind, payload) {
  const item = {
    at: new Date().toLocaleTimeString(),
    kind,
    payload,
  };
  state.sourceSetup.log = [item, ...state.sourceSetup.log].slice(0, 6);
  renderSourceSetupLog();
}

function renderSourceSetupLog() {
  const log = $("#source-setup-log");
  const summary = $("#source-setup-log-summary");
  if (!log) return;
  if (!state.sourceSetup.log.length) {
    if (summary) summary.textContent = "пока пуст";
    log.textContent = "Проверок ещё не было.";
    return;
  }
  if (summary) summary.textContent = `${state.sourceSetup.log.length} записей`;
  log.textContent = state.sourceSetup.log.map(sourceSetupCompactLogLine).join("\n");
}

function sourceSetupCompactLogLine(item) {
  const payload = item.payload || {};
  const guide = payload.guide || payload;
  const next = guide?.next_action?.action || payload.action || "";
  const nextLabel = SOURCE_SETUP_ACTION_LABELS[next] || guide?.next_action?.label || next || "обновить статус";
  const status = guide.goal_status || payload.status || "ok";
  const counts = sourceSetupStepCounts(guide);
  const countText = counts.total ? `${counts.ready}/${counts.total} готово` : "";
  const submitText = payload.submit === false ? "отправки нет" : "";
  const pieces = [sourceSetupLogKindLabel(item.kind), sourceSetupGoalLabel(status), countText, submitText].filter(Boolean);
  return `[${item.at}] ${pieces.join(" · ")}\nследующий шаг: ${nextLabel}`;
}

function sourceSetupLogKindLabel(kind) {
  if (kind === "guide") return "статус";
  return SOURCE_SETUP_ACTION_LABELS[kind] || kind || "проверка";
}

function sourceSetupGoalLabel(status) {
  const value = String(status || "");
  if (value === "blocked") return "нужно действие";
  if (value === "ready") return "готово";
  if (value === "ready_to_certify") return "готово к включению";
  if (value === "manual_handoff") return "ручной режим";
  if (value === "ok") return "проверено";
  return value;
}

function sourceSetupJson(selector, fallback = {}) {
  const raw = ($(selector)?.value || "").trim();
  if (!raw) return fallback;
  return JSON.parse(raw);
}

function sourceSetupActionPayload(action) {
  const payload = {
    action,
    source: sourceSetupSource(),
    level: sourceSetupLevel(),
  };
  if (action === "har_import") {
    payload.path = $("#source-setup-har-path")?.value.trim() || "";
    payload.allowed_hosts = ($("#source-setup-har-hosts")?.value || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  }
  if (action === "redaction_scan") {
    payload.payload = sourceSetupJson("#source-setup-redaction-payload", {});
    payload.text = $("#source-setup-redaction-text")?.value || "";
  }
  if (action === "dry_run") {
    payload.form = sourceSetupJson("#source-setup-form-json", {});
    payload.persona = sourceSetupJson("#source-setup-persona-json", {});
    payload.headless = true;
  }
  if (action === "preflight") {
    payload.live_auth = false;
  }
  return payload;
}

async function runSourceSetupAction(action) {
  const actionName = action || state.sourceSetup.guide?.next_action?.action || "source_status";
  const output = $("#source-setup-log");
  try {
    setSourceSetupBusy(true);
    const result = await api("/api/source-setup/action", {
      method: "POST",
      body: JSON.stringify(sourceSetupActionPayload(actionName)),
    });
    appendSourceSetupLog(actionName, result);
    if (result.guide) state.sourceSetup.guide = result.guide;
    else await loadSourceSetupGuide();
    renderSourceSetupGuide();
    const stillBlocked = result.status === "blocked" || result.guide?.goal_status === "blocked";
    toast(stillBlocked ? "Проверка готова: нужен следующий шаг" : "Проверка выполнена", stillBlocked ? "info" : "success");
  } catch (err) {
    renderActionError(output, err);
  } finally {
    setSourceSetupBusy(false);
  }
}

function setSourceSetupBusy(busy) {
  for (const button of document.querySelectorAll("#source-setup-run-next, [data-source-setup-action]")) {
    if (!button.dataset.sourceSetupIdleLabel) {
      button.dataset.sourceSetupIdleLabel = button.dataset.idleLabel || button.dataset.label || button.textContent.trim();
    }
    button.disabled = busy;
    const label = busy ? "Работаю..." : button.dataset.sourceSetupIdleLabel;
    setButtonCopy(button, label, button.title);
  }
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
  toast("Заметка сохранена", "success");
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
    const funnel = stats.applications_by_status || {};
    const stages = [
      ["applied", "Откликнулись"],
      ["viewed", "Просмотрено"],
      ["response", "Ответ"],
      ["phone_screen", "Созвон / скрининг"],
      ["interview", "Интервью"],
      ["offer", "Оффер"],
    ];
    const base = Math.max(funnel.applied || 0, stats.total_applications || 0, 1);
    const hasData = stages.some(([key]) => (funnel[key] || 0) > 0);
    if (!hasData) {
      funnelDiv.innerHTML = '<p class="meta">Пока нет откликов — воронка появится после первых откликов.</p>';
    } else {
      funnelDiv.innerHTML = stages.map(([key, label]) => {
        const count = funnel[key] || 0;
        const pct = Math.round((count / base) * 100);
        const conv = (funnel.applied || 0) > 0 ? Math.round((count / (funnel.applied || 1)) * 100) : 0;
        return `<div class="stat-bar funnel-row">
          <span>${label}</span>
          <div class="bar"><div class="fill" style="width:${count > 0 ? Math.max(pct, 6) : 0}%"></div></div>
          <span title="${conv}% от откликов">${count}</span>
        </div>`;
      }).join("");
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
  enhanceButtonHints();
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
    box.innerHTML = '<p class="meta">Дайджест пуст.</p>';
    return;
  }
  const recommendations = digest.summary?.recommendations || [];
  const approvals = digest.approvals?.recent || [];
  const runs = digest.runs?.recent || [];
  box.innerHTML = `
    <div class="agent-row">
      <div class="agent-row-head">
        <strong>Сводка HH</strong>
        <span class="agent-badge">${escapeHtml(String(digest.status || "ok"))}</span>
      </div>
      <div class="agent-inline-stats">
        <span>резюме: ${escapeHtml(String(digest.summary?.resumes?.total ?? 0))}</span>
        <span>переписки: ${escapeHtml(String(digest.summary?.negotiations?.total ?? 0))}</span>
        <span>контакты: ${escapeHtml(String(digest.summary?.contacts?.total ?? 0))}</span>
        <span>пропущено: ${escapeHtml(String(digest.summary?.skipped?.total ?? 0))}</span>
      </div>
      <div class="meta">${recommendations.map(escapeHtml).join(" · ") || "нет рекомендаций"}</div>
    </div>
    <div class="agent-row">
      <div class="agent-row-head"><strong>Недавние согласования</strong><span class="agent-badge">${approvals.length}</span></div>
      ${approvals.map((item) => `<div class="meta">#${item.id} ${escapeHtml(item.action_type)} · ${escapeHtml(item.status)} · ${escapeHtml(item.reason || "")}</div>`).join("") || '<p class="meta">Согласований нет.</p>'}
    </div>
    <div class="agent-row">
      <div class="agent-row-head"><strong>Недавние запуски</strong><span class="agent-badge">${runs.length}</span></div>
      ${runs.map((item) => `<div class="meta">#${item.id} ${escapeHtml(item.tool_name)} · ${escapeHtml(item.status)}</div>`).join("") || '<p class="meta">Запусков нет.</p>'}
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
      <textarea id="agent-modify-${item.id}" class="agent-modify" rows="2" placeholder="Что изменить в payload или сообщении"></textarea>
      <div class="agent-row-actions">
        <button onclick="approveAgentApproval(${item.id})" title="Одобрить и разрешить действие"><i data-lucide="check"></i>Одобрить</button>
        <button onclick="rejectAgentApproval(${item.id})" title="Отклонить действие"><i data-lucide="x"></i>Отклонить</button>
        <button onclick="modifyAgentApproval(${item.id})" title="Изменить и одобрить"><i data-lucide="pencil"></i>Изменить</button>
        <button onclick="flagAgentApproval(${item.id})" title="Пометить для ручной проверки"><i data-lucide="flag"></i>Пометить</button>
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
      ${run.status === "running" ? `<button onclick="cancelAgentOperation(${run.id})" title="Отменить запущенную операцию"><i data-lucide="ban"></i>Отменить</button>` : ""}
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
      title: `Согласование #${item.id}`,
      badge: item.action_type || "approval",
      body: item.reason || "",
      payload: item.payload || {},
    })),
    ...outbox.map((item) => ({
      title: `Исходящее #${item.id}`,
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
  `).join("") : `<p class="meta">Входящие пусты.</p>`;
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
    `).join("") : `<p class="meta">Событий агента пока нет.</p>`;
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
    `).join("") : `<p class="meta">Задач агента пока нет.</p>`;
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
  `).join("") : '<p class="meta">Чёрный список пуст.</p>';
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
    box.innerHTML = '<p class="meta">Сохранённых сниппетов нет.</p>';
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
        <button onclick="fillHhLabRequestByName('${escapeJsString(item.name)}')" title="Загрузить сниппет в редактор"><i data-lucide="copy"></i>Загрузить</button>
        <button onclick="runHhLabSnippet('${escapeJsString(item.name)}')" title="Выполнить сниппет"><i data-lucide="play"></i>Выполнить</button>
        <button onclick="deleteHhLabSnippet('${escapeJsString(item.name)}')" title="Удалить сниппет"><i data-lucide="trash-2"></i>Удалить</button>
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
  const note = $("#ai-default-route-note");
  const defaultRoute = ai?.default_route || "smart";
  const defaultStatus = ai?.routes?.[defaultRoute] || null;
  if (note) {
    const readyText = defaultStatus?.ready
      ? "готов к безопасной проверке"
      : "требует настройки";
    note.textContent = `Основной AI-маршрут: ${defaultRoute}. Если прямой API не заполнен, AI-кнопки используют этот маршрут; сейчас он ${readyText}.`;
  }
  if (!routes.length) {
    box.innerHTML = '<p class="meta">Маршруты AI не настроены.</p>';
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
  if (!window.confirm("Применить отредактированный импорт WO/FLOW в этот локальный проект?")) return;
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
  const ru = (q) => ONBOARDING_RU[q.id] || { title: q.title || q.id, prompt: q.prompt || "", example: "" };
  const answeredIds = new Set((Array.isArray(facts) ? facts : []).map((f) => f.key));
  const select = $("#onboarding-question-select");
  if (select) {
    select.innerHTML = questions.map((q) => {
      const done = answeredIds.has(q.id) ? "✓ " : "";
      return `<option value="${escapeAttr(q.id)}">${done}${escapeHtml(ru(q).title)}</option>`;
    }).join("");
  }
  updateOnboardingPrompt();
  $("#onboarding-questions").innerHTML = questions.map((q) => {
    const r = ru(q);
    const done = answeredIds.has(q.id);
    return `
    <div class="agent-row onboarding-q${done ? " done" : ""}">
      <div class="agent-row-head">
        <strong>${escapeHtml(r.title)}</strong>
        <span class="agent-badge ${done ? "ready" : ""}">${done ? "заполнено" : escapeHtml(CATEGORY_RU[q.category] || q.category || "")}</span>
      </div>
      <div class="meta">${escapeHtml(r.prompt)}</div>
      ${r.example ? `<div class="onboarding-example">${escapeHtml(r.example)}</div>` : ""}
      <button onclick="pickOnboardingQuestion('${escapeJsString(q.id)}')">${done ? "Изменить ответ" : "Ответить"}</button>
    </div>`;
  }).join("") || `<p class="meta">Вопросы профиля пока не загружены. Нажми «Обновить» или проверь подключение.</p>`;
  $("#onboarding-status").textContent = JSON.stringify({ status, facts }, null, 2);
  renderOnboardingGuide(status, facts);
  refreshIcons();
}

function updateOnboardingPrompt() {
  const select = $("#onboarding-question-select");
  const answer = $("#onboarding-answer");
  if (!select || !answer) return;
  const r = ONBOARDING_RU[select.value];
  if (r) answer.placeholder = r.example || r.prompt || "Напиши ответ свободным текстом...";
}

function pickOnboardingQuestion(id) {
  const select = $("#onboarding-question-select");
  if (select) { select.value = id; updateOnboardingPrompt(); }
  const answer = $("#onboarding-answer");
  if (answer) { answer.focus(); answer.scrollIntoView({ behavior: "smooth", block: "center" }); }
}

async function submitOnboardingAnswer() {
  const questionId = $("#onboarding-question-select")?.value || "experience";
  const answer = $("#onboarding-answer")?.value || "";
  if (!answer.trim()) { toast("Сначала напиши ответ", "info"); return; }
  try {
    await api("/api/onboarding/answer", {
      method: "POST",
      body: JSON.stringify({ question_id: questionId, answer, source: "web_ui" }),
    });
    $("#onboarding-answer").value = "";
    await loadOnboardingQuestions();
    await loadCandidateMap();
    toast("Ответ сохранён", "success", 6000, { label: "Открыть мастер", onClick: openWizard });
  } catch (err) {
    toast("Ошибка сохранения: " + err.message, "error");
  }
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
      ${fact.status === "confirmed" ? "" : `<button onclick="confirmCandidateFact(${Number(fact.id)})" title="Подтвердить факт для использования в резюме">Подтвердить</button>`}
    </div>
  `).join("") || `<p class="meta">Фактов пока нет. Заполни онбординг.</p>`;
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
      <div class="agent-row-head"><strong>Изменения варианта</strong><span class="agent-badge">+${added.length} / −${removed.length}</span></div>
      <div class="meta">Добавлено</div>
      <pre class="agent-json">${escapeHtml(added.join("\n") || "нет")}</pre>
      <div class="meta">Удалено</div>
      <pre class="agent-json">${escapeHtml(removed.join("\n") || "нет")}</pre>
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
    ? "Отправить через сертифицированный внешний адаптер?"
    : "Подготовить ручную передачу внешнего отклика?";
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
      <button onclick="loadCampaignRun(${Number(run.id)})" title="Показать вакансии этого запуска">Показать вакансии</button>
    </div>
  `).join("") || `<p class="meta">Запусков кампаний нет.</p>`;
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
  if (!window.confirm("Подтвердить реальный запуск HH-кампании с откликами?")) return;
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
  if (!window.confirm("Подтвердить реальный запуск внешней кампании с откликами?")) return;
  const result = await api(`/api/campaigns/${runId}/run-external`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
  $("#campaign-plan-output").textContent = JSON.stringify(result, null, 2);
  await loadCampaignRuns();
}

async function killCampaigns() {
  if (!window.confirm("Поставить на паузу все кампании прямо сейчас?")) return;
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
    $("#pipeline-status-output").textContent = "Сначала укажи дату и время события.";
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
  `).join("") || `<p class="meta">Событий в истории нет.</p>`;
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

function activateView(viewName, options = {}) {
  const target = $(`#view-${viewName}`);
  if (!target) return;
  document.querySelectorAll(".nav-button").forEach((item) => item.classList.remove("active"));
  document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
  document.querySelector(`.nav-button[data-view="${viewName}"]`)?.classList.add("active");
  target.classList.add("active");
  updateNavA11y(viewName);
  syncOnboardingVisibility(viewName);
  if (target.scrollIntoView) window.scrollTo({ top: 0, behavior: "smooth" });
  if (options.load !== false) loadRoadmapViewSafe(viewName);
}

// Гид первого запуска и подсказки показываем только на экране «Вакансии».
function syncOnboardingVisibility(viewName) {
  const onInbox = viewName === "inbox";
  const dismissed = localStorage.getItem("work-hunter-onboarding-dismissed") === "true";
  const guide = $("#onboarding-guide");
  const strip = $("#ux-help-strip");
  if (guide) guide.hidden = !onInbox || dismissed;
  if (strip) strip.hidden = !onInbox || dismissed;
  const reopen = $("#reopen-onboarding-guide");
  if (reopen) reopen.hidden = !(onInbox && dismissed);
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
    button.dataset.label = button.textContent.trim();
    button.dataset.idleLabel = button.dataset.label;
  }
  setupResumeBuilderDefaults();
  applyRussianUxCopy();
  enhanceButtonHints();
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => {
      activateView(button.dataset.view);
    });
  });
  $("#open-onboarding-view")?.addEventListener("click", () => activateView("onboarding"));
  $("#dismiss-onboarding-guide")?.addEventListener("click", dismissOnboardingGuide);
  $("#reopen-onboarding-guide")?.addEventListener("click", reopenOnboardingGuide);
  $("#open-wizard-button")?.addEventListener("click", openWizard);
  $("#sidebar-wizard-button")?.addEventListener("click", openWizard);
  $("#help-wizard-button")?.addEventListener("click", openWizard);
  $("#open-source-setup-button")?.addEventListener("click", openSourceSetupWizard);
  $("#sidebar-source-setup-button")?.addEventListener("click", openSourceSetupWizard);
  $("#sidebar-help-button")?.addEventListener("click", () => activateView("help"));
  $("#wizard-close")?.addEventListener("click", closeWizard);
  $("#wizard-back")?.addEventListener("click", wizardBack);
  $("#wizard-next")?.addEventListener("click", wizardNext);
  $("#wizard-modal")?.addEventListener("click", (event) => {
    if (event.target === $("#wizard-modal")) closeWizard();
  });
  $("#source-setup-close")?.addEventListener("click", closeSourceSetupWizard);
  $("#source-setup-refresh-button")?.addEventListener("click", loadSourceSetupGuide);
  $("#source-setup-run-next")?.addEventListener("click", (event) => runSourceSetupAction(event.currentTarget.dataset.nextSourceSetupAction));
  $("#source-setup-source")?.addEventListener("change", resetSourceSetupGuide);
  $("#source-setup-level")?.addEventListener("change", resetSourceSetupGuide);
  $("#source-setup-modal")?.addEventListener("click", (event) => {
    if (event.target === $("#source-setup-modal")) closeSourceSetupWizard();
    const button = event.target.closest("[data-source-setup-action]");
    if (button) runSourceSetupAction(button.dataset.sourceSetupAction);
  });
  $("#onboarding-guide")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-onboarding-view]");
    if (button) activateView(button.dataset.onboardingView);
  });
  $("#sync-button").addEventListener("click", syncJobs);
  $("#score-button").addEventListener("click", scoreJobs);
  $("#refresh-button").addEventListener("click", loadJobs);
  $("#source-filter").addEventListener("change", loadJobs);
  $("#min-score-filter").addEventListener("change", loadJobs);
  $("#save-config-button").addEventListener("click", saveConfig);
  $("#save-token-button").addEventListener("click", saveHhToken);
  $("#save-ai-button").addEventListener("click", saveAiSettings);
  $("#ai-backend-select")?.addEventListener("change", updateAiBackendFields);
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
  $("#onboarding-question-select")?.addEventListener("change", updateOnboardingPrompt);
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
  applyRussianUxCopy();
  enhanceButtonHints();
  updateNavA11y("inbox");
  setupKeyboardShortcuts();
  refreshIcons();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js");
  }
  await Promise.all([loadJobs(), loadConfig(), loadProfile(), loadSources()]);
  renderOnboardingGuide();
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
    toast("Резюме сохранено", "success");
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
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
    toast("Событие сохранено", "success");
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
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
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
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
      <td>${statusBadge(job.status)}</td>
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
    toast("Поиск сохранён", "success");
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
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
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
  setBusy("[onclick='parseJobStructure()']", false);
}

async function runGapAnalysis() {
  if (!state.selectedId) return;
  const resumes = await api("/api/resumes");
  const active = resumes.find(r => r.is_active);
  if (!active) { toast("Сначала создай и активируй резюме в настройках.", "info"); return; }
  setBusy("[onclick='runGapAnalysis()']", true);
  try {
    const result = await api(`/api/jobs/${state.selectedId}/gap-analysis`, {
      method: "POST", body: JSON.stringify({ resume_id: active.id }),
    });
    $("#resume-tips-output").innerHTML = renderMarkdown(result.content);
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
  setBusy("[onclick='runGapAnalysis()']", false);
}

async function scoreAtsResume() {
  const text = $("#resume-body-input")?.value || $("#audit-resume-input")?.value;
  if (!text) { toast("Вставь текст резюме.", "info"); return; }
  try {
    const result = await api("/api/resumes/ats-score", { method: "POST", body: JSON.stringify({ resume_text: text }) });
    toast(`ATS Score: ${result.score}/100 · Проблемы: ${(result.issues||[]).join(", ") || "нет"}`, "info", 7000);
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
}

async function smartClassify() {
  if (!state.selectedId) return;
  try {
    const result = await api(`/api/jobs/${state.selectedId}/smart-classify`, { method: "POST", body: "{}" });
    const output = $("#resume-tips-output");
    if (output) { output.textContent = JSON.stringify(result, null, 2); switchAiTab("resume"); }
    toast("AI-классификация готова — см. вкладку «Резюме»", "success");
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
}

async function getInterviewPrep(stage) {
  if (!state.selectedId) return;
  try {
    const result = await api(`/api/jobs/${state.selectedId}/interview-prep`, {
      method: "POST", body: JSON.stringify({ stage }),
    });
    $("#ai-fit-reasoning").innerHTML = renderMarkdown(result.content);
    switchAiTab("interview");
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
}

async function getBehaviorSuggestions() {
  try {
    const result = await api("/api/behavior/suggest", { method: "POST", body: "{}" });
    toast(result.content, "info", 8000);
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
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
