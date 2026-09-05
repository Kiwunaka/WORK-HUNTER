# Проверка AIHawk, GodsScion и HH API parity

Дата проверки: 2026-08-07.

Проверялись актуальные `main` и исходники, а не только описания репозиториев:

- AIHawk: `d0ebd4142cd44faaca36052a7742718b6b205584`
- GodsScion Auto Job Applier: `8d74e8ccb85b356fd7be15a70fe47ce85559261c`
- hh-applicant-tool: `a7704115d31344e9fd422854a2b4e840b3d19292` (`v1.8.20`)

## Вывод

1. Текущий AIHawk нельзя использовать как готовый LinkedIn applier. Автор удалил
   provider-плагины. В репозитории остались LLM-ядро, генерация резюме и cover
   letter, но нет поиска LinkedIn, Easy Apply modal loop и отправки заявки.
2. GodsScion — рабочий LinkedIn-донор. В коде есть поиск, фильтры, Easy Apply,
   многошаговая форма, загрузка резюме, история, blacklist/already-applied и
   OpenAI/DeepSeek/Gemini. Это монолитный Selenium-скрипт без тестов. Некоторые
   возможности из README в основном цикле всё ещё помечены `In Development` или
   закомментированы, включая полноценную генерацию tailored resume.
3. Work Hunter шире обоих проектов по источникам и общему агентному контуру, но
   LinkedIn browser apply ещё нельзя считать проверенным production-аналогом
   GodsScion до одного подтверждённого live Easy Apply после ручного логина.
4. HH APK декомпилирован и проиндексирован, но утверждение «реализованы все API
   методы мобильного приложения» неверно. Реализован applicant-контур, нужный для
   поиска и откликов; весь мобильный продукт не портирован.

## LinkedIn

| Возможность | AIHawk current main | GodsScion | Work Hunter |
| --- | --- | --- | --- |
| Поиск вакансий | Нет provider-плагина | Да, Selenium | Да, public guest endpoint |
| Фильтры/blacklist | Конфиг остался, выполнять некому | Да | Да, общий scoring/policy |
| Persistent login | Нет LinkedIn flow | Chrome profile/manual login | Playwright persistent profile |
| Easy Apply modal loop | Нет | Да | Да |
| Text/textarea/select/radio/checkbox | Нет | Да | Да |
| AI-ответы | LLM-ядро есть | OpenAI/DeepSeek/Gemini | Настраиваемый AI backend |
| Upload resume | Нет apply flow | Да | Да |
| Проверка already applied | Нет | Да | Да |
| Подтверждение успешной отправки | Нет | Частично по кнопкам | Явный success marker либо `submitted_unconfirmed` |
| История и единая база по источникам | Нет apply flow | CSV + простой Flask UI | SQLite + UI/CLI/MCP |
| Автотесты | 0 test-файлов в current main | 0 test-файлов | Есть |

Из GodsScion в Work Hunter перенесены две важные для надёжности идеи: работа с
полями только внутри Easy Apply modal и распознавание уже отправленной заявки.
Дополнительно Work Hunter больше не считает сам клик по Submit доказательством:
без success marker сохраняется `submitted_unconfirmed` и скриншот.

## HH: что реально разобрано

- APK: `ru.hh.android` 26.28.1.
- Подпись проверена: signer `CN=HeadHunter`, присутствует Google source stamp.
- JADX дал 54 066 Java-файлов.
- Автоматический отчёт нашёл 175 URL, 86 host, 25 кандидатов на credential и
  500 route hints (лимит сканера).
- Отдельный проход по реальным Retrofit-аннотациям восстановил 246 уникальных
  статически заданных пар `HTTP method + endpoint`.

Число 246 не означает полный сетевой протокол: часть URL задаётся динамически,
часть работы идёт через web/deep links/websocket, а приложение содержит не только
поиск работы, но и карьерную платформу, отзывы, платные сервисы, аналитику и др.

## HH parity по рабочим операциям

| Операция | hh-applicant-tool | Work Hunter | Статус |
| --- | --- | --- | --- |
| `/me`, refresh auth | Да | Да | Реализовано; текущий токен невалиден |
| Поиск и карточка вакансии | Да | Да | Реализовано |
| Similar/recommended vacancies | Да | Да | Реализовано |
| Список/карточка резюме | Да | Да | Реализовано |
| Создание резюме | Да | Да | Реализовано |
| Publish/поднятие резюме | Да | Да | Реализовано |
| Отклик `/negotiations` | Да | Да | Реализовано, нужен live canary после auth |
| Тест/assessment при отклике | Web+AI | API/browser+AI | Реализован challenge-контур, нужен live canary |
| Переговоры и история сообщений | Да | Да | Реализовано |
| Ответ работодателю | Да | Да | Реализовано |
| Отмена отклика | Да | Да | Реализовано |
| Blacklist работодателя | Да | Да | Реализовано |
| Произвольный API вызов | Да | Да, API Lab | Реализовано, не равно tested typed support |
| Favorite/hidden vacancy | Частично hidden | Нет отдельной команды | Пробел |
| Saved searches/autosearch | Нет основного workflow | Нет HH remote workflow | Пробел |
| Полный `/chats` API | Через negotiation messages | Частично messages | Пробел |
| Resume access/views/whitelist/delete/edit profile | Частично | Нет typed workflow | Пробел |
| Весь mobile API (246+ пар) | Нет | Нет | И не требуется для рассылки |

## Текущая готовность локальной установки

`work-hunter doctor --json` на момент проверки:

- core, DB, UI, MCP, Playwright: готовы;
- активный профиль: `bi_analyst`;
- не заполнены `email`, `phone`, `resume_path`;
- HH: `403 bad_authorization`, refresh token есть, но попытка refresh вернула
  `400 invalid_request`; HH cookies не импортированы;
- LinkedIn: поиск уже синхронизировался, но browser session ещё требует ручной логин;
- рекомендованный режим: external-first, HH после повторной авторизации.

До этих действий нельзя честно говорить «можно нажать и всё разошлётся»:

1. Заполнить email, phone и существующий PDF в `resume_path`.
2. Выполнить `work-hunter hh auth login --account default` и войти вручную:
   сохранённый refresh token уже проверен и не обновляется.
3. После входа проверить `work-hunter hh-auth-status`; при необходимости
   импортировать HH cookies.
4. Выполнить `work-hunter browser-login linkedin` и войти вручную.
5. Сделать по одному контролируемому отклику HH и LinkedIn и проверить удалённый
   факт отправки. После canary можно включать серию.
