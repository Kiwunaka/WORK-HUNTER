# WORK HUNTER: первый проход по плану от 5 сентября 2026

Исходный HEAD: `ddbf05dfbdcc527681a3554d7c5fb204cb669b1d`, совпадает с аудитом.
Перед началом рабочее дерево было чистым. Изменения оставлены локально для review.
Основание: `WORK_HUNTER_AUDIT_AND_IMPLEMENTATION_2026-09-05.md`, предоставленный пользователем.

Это журнал двух пакетов изменений, а не закрытие всего аудита.

## Статус заданий

| Задание | Сделано | Что остаётся |
|---|---|---|
| WO-01 Baseline | Воспроизведены тесты, установлена причина сбоя Actions, исправлена упаковка, обновлён workflow | 413 замечаний полного ruff; billing GitHub; зелёная удалённая матрица Python 3.11/3.12 |
| WO-02 Truthful submit | Убраны ложные критерии успеха, Next отделён от final submit, uncertainty сохраняется до dispatch, повтор блокируется после restart и при конкуренции | Проверенные контракты квитанций остальных источников, автоматический reconciliation, popup/frame flows |
| WO-03 Form semantics | Раздельные RU/EN поля, происхождение ответов, ограниченные согласия, файл выбранного CV и его хеш | Полный UI для неизвестных вопросов и согласий; живые формы |
| WO-04 UI consistency | Гонки карточки/письма/заметок, независимый ledger API с cursor, разделение исходов, прокрутка всех карточек | Полная проекция подготовленных планов, расширение на все AI-панели и аккаунтные фильтры |
| WO-05 AI contracts | JSON Schema runtime validation, nullable ошибки fit/ATS, запрет скрытого fallback | Сквозной учёт tokens/cost, жёсткий deadline транспорта, остальные AI-методы |
| WO-06 Resume workspace | Файл локальной версии, неизменяемый артефакт, версия и hash в preview | Полный fact store, export/readback PDF/DOCX, fact diff и единый snapshot во всех AI-методах |
| WO-07 Match quality | Токены навыков и aliases, защита сравнения валют/периодов/grossness, remote unknown | Нормализованные поля зарплаты в collectors, географическая eligibility, настройка единиц в UI |
| WO-08 Source reliability | Отдельные support level/verified поля; UI не считает общий адаптер проверенным автооткликом | Live readiness, health/empty/challenge, source/destination identity |
| WO-09 Credentials | Шифрование HAR/session состояния, атомарная миграция, точный HTTPS origin, запрет redirect | Retention UI, полный аудит экспортов и изоляции browser profiles |
| WO-10 Expansion | Не начато | После зависимостей; живые контракты и права каждой площадки |

## WO-01: дефекты, изменения, доказательства

### CI

[Run 33961729055](https://github.com/Kiwunaka/WORK-HUNTER/actions/runs/33961729055)
имеет failure у всех четырёх jobs и пустые списки шагов.
Annotations у quality и browser указывают, что задания не стартовали из-за
неуспешной оплаты аккаунта или необходимости увеличить spending limit.
Это инфраструктурная блокировка, а не результат pytest. Billing не менялся,
workflow не отправлялся на GitHub и удалённый зелёный CI не заявляется.

Дополнительно исправлены проблемы, которые проявились бы после запуска runner:

- Bootstrap setuptools согласован с build-system: `>=83,<85` вместо `>=75,<81`.
- `test_telegram_chat_infographic_browser.py` перенесён в browser job, где есть
  Playwright и Chromium. Новый внешний wizard проверяется там же. Проверки не удалены.
- Добавлены JUnit, таймауты jobs, сохранение диагностики зависимостей и ruff.
- Локальные UI fixtures сохраняют screenshot и trace при падении. Артефакты
  ограничены тестовыми контекстами, срок хранения в Actions — семь дней.
- Python в CI использует UTF-8. Локальные отчёты и `test-results/` исключены из git.

Полный ruff 0.16.5 обнаруживает **413 замечаний исходной базы**, после изменений
их также 413. Отдельная проверка `E4,E7,E9,F` проходит, но она не заменяет полный
quality gate; workflow продолжает запускать полный ruff. Новые файлы тестов
проходят полный ruff. Три новых обработчика неизвестных исключений имеют
адресные пояснения BLE001: после dispatch они обязаны сохранить uncertainty,
включая неожиданный сбой библиотеки, и не превращают исключение в успех.

### Упаковка WH-16

Исходный release test проверял только верхний уровень static. После расширения
проверки на все файлы `web/static` чистая сборка воспроизводимо теряла 18 vendor
файлов, включая логотипы, CSS и WOFF2. Тест упал до исправления package-data.

Добавлены каталоги `vendor/brands/*` и `vendor/phosphor/*`. Wheel, sdist и
изолированный smoke теперь проверяют полный набор. Doctor проверяет также
модули UI, redesign.css, используемые логотипы и шрифт; удаление WOFF2 из
fixture-пакета переводит его в error.

Финальный wheel установлен со всеми runtime/UI-зависимостями в отдельный venv
в системном Temp. Запуск выполнен с `python -I`, cwd вне репозитория.
Импорт идёт из site-packages, doctor сообщает `wheel/ok`. Настоящий HTTP handler
приложения вернул **200 и правильное содержимое для всех 28 статических файлов**.

SHA-256 wheel: `6B732DCC99616304E1E9C5E982F25090CB0699365149E3F2FE8441D1E3920C59`.

## WO-02: поведение после изменений

- HTTP 2xx остаётся транспортным результатом. Session adapter без проверенного
  контракта квитанции возвращает `submission_unknown`, включая 200 с прикладной
  ошибкой, 202 processing, произвольный application_id, HTTP 500 и потерю ответа.
- Redirect, универсальный текст успеха и кнопка Done не подтверждают подачу.
  Для LinkedIn оставлены узкие признаки квитанции с проверкой неизменности URL;
  отметка другой вакансии после redirect не подтверждает исходную.
- Next/Review выполняются до поиска final submit. Общий `type=submit` удалён
  из критериев финального действия. Трёхшаговая HTML fixture отправляется один раз.
- Заполнение ограничено единственной видимой формой или известным диалогом.
  Несколько неразличимых форм блокируют действие.
- Начало final click записывается до самого клика: timeout после принятого
  запроса остаётся unknown.
- `claim_external_apply()` атомарно сохраняет `submitting` через SQLite
  `BEGIN IMMEDIATE` до запуска адаптера. Другой worker и перезапуск не получают
  право повторить отправку. Замена resume_id не обходит эту проверку.
- Unknown записывается в applications, не меняет вакансию на applied и не
  увеличивает счётчик отправленных откликов в `Storage.get_stats()`.
- Повтор возвращает `reconciliation_required` с прежним plan_id. Исходные
  HH executor, lease/fencing и state machine не изменялись.

### Ограничения, которые нельзя считать закрытыми

Автоматическая сверка с площадкой ещё не реализована. Unknown удерживается до
проверки; готового UI для безопасного снятия такой блокировки после доказанного
«не отправлено» пока нет. TTL сам по себе не разрешает повтор. Это предохранитель
первого этапа, а не законченный recovery workflow.

Блокировка пока действует на вакансию во всех вариантах CV/аккаунта. Это
консервативное ограничение текущего внешнего dispatcher; отдельные проверенные
account identity, destination deduplication и immutable bundle остаются в плане.

Сопоставление полей, автоматические required-checkbox, AI-ответы и выбранный
файл резюме ещё требуют WO-03/05/06. Generic browser не объявляется готовым
полным auto apply. Реальные логины, отклики и загрузки пользовательского CV
при проверке не выполнялись.

### Capability-матрица проверенного объёма

| Контур | Что подтверждено локальными тестами | Живой контракт |
|---|---|---|
| HH | Существующие deterministic/recovery contracts сохраняются | Не перепроверялся |
| LinkedIn browser | Узкие receipt selectors, отказ при redirect другой вакансии | Не проверялся; auto_apply_verified не присваивается |
| Прочие внешние browser sources | Навигация и заполнение fixture, остановка при отсутствии квитанции | Verifier отсутствует; доставка unknown |
| HAR/session apply | Транспорт отделён от доставки, ошибки не дают applied | Источник-специфичных receipt contracts пока нет |
| Новые RU/ATS площадки | Ничего не добавлено | WO-10 не начато |

Существующий UI readiness всех источников ещё не исправлен: это WO-08/WH-11.
Эта таблица фиксирует фактически проверенный объём, а не новые обещания интеграций.

## Проверки

Среда: Windows, Python 3.12.5, pytest 9.1.1, Playwright 1.62.0, setuptools 84.0.0,
build 1.6.0, mypy 2.3.1, ruff 0.16.5.

| Проверка | Результат |
|---|---|
| Baseline без основного browser-файла | 1674 passed, 4 skipped |
| Baseline основного UI browser-файла | 93 passed |
| Полный deterministic после основного пакета | 1685 passed, 4 skipped |
| Финальный browser job: UI, infographic, external wizard | 112 passed |
| Финальные external unit/browser/recovery после дополнительной URL-проверки | 31 passed |
| Service/apply/HH recovery regression | 71 passed |
| Wheel/sdist/изолированная установка | 8 passed |
| Полная установка финального wheel и HTTP assets | 28/28 |
| mypy | 85 source files, ошибок нет |
| pip check / uv pip check чистого venv | Успешно |
| pip-audit | Известных уязвимостей не найдено |
| git diff --check | Успешно |
| Полный ruff | 413 исходных замечаний, gate открыт |

Четыре пропуска: POSIX mode bits, два fork/SIGALRM-теста и модуль проверки
исходного Telegram export, которого нет на машине. Условия пропуска не менялись.
Наборы пересекаются: количества в таблице нельзя складывать.

JUnit и диагностика: `outputs/audit-2026-09-05/` (локальные игнорируемые файлы).
Воспроизведение основных проверок после установки extras `[dev,browser,ui,release]`
и Chromium:

```powershell
$env:PYTHONUTF8='1'
.venv/Scripts/python.exe -m pytest -q -m 'not live_canary' --ignore=tests/test_web_ui_browser.py --ignore=tests/test_telegram_chat_infographic_browser.py --ignore=tests/test_external_apply_browser.py
.venv/Scripts/python.exe -m pytest tests/test_web_ui_browser.py tests/test_telegram_chat_infographic_browser.py tests/test_external_apply_browser.py -q
.venv/Scripts/python.exe -m pytest tests/test_release_artifact.py -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy work_hunter
```

## Проверка интерфейса

Поток: локальная страница `/today` в fixture-режиме → подтверждение отклика →
unknown/reconciliation_required → открытое предупреждение, заблокированный submit,
доступная отмена, отсутствие success toast. Проверены 1280×900 и 390×900.

Browser plugin not available: использован существующий Python Playwright workflow.
Сервер работает на `http://127.0.0.1:<случайный порт>/today`; все внешние запросы
тестового браузера блокируются. Проверки URL/title, содержимого страницы,
console/page errors и действий проходят. Скриншоты просмотрены: сообщение и
отмена доступны, горизонтального переполнения документа нет.

Найденный mobile overflow вызывала `.visually-hidden` с position:fixed без
координат: скрытая подпись прокручиваемого фильтра расширяла документ до 551 px.
Фиксированные top/left устранили выход за viewport без удаления доступного label.

Скриншоты сохранены вне репозитория:

- `C:/Users/kiwun/.codex/visualizations/2026/09/05/01a07172-176a-7e12-9b32-168e6cca55d2/submission-unknown-1280.png`
- `C:/Users/kiwun/.codex/visualizations/2026/09/05/01a07172-176a-7e12-9b32-168e6cca55d2/submission-unknown-390.png`

Следующий пакет: закрыть WO-03 (семантика полей/согласий и фактический CV upload),
параллельно по зависимостям довести WO-02 до проверяемого reconciliation и
закончить WO-01 после устранения lint/billing-блокировок. WO-04–10 остаются открытыми.

## Второй пакет и проверка встроенным браузером

Продолжение по просьбам «Доделывай если надо войду в свой акк hh» и «потести через Браузер».
Таблица выше отражает актуальный объём; описание первого пакета оставлено как история проверки.

### Изменённое поведение

- `FormAnswer` хранит категорию и provenance. Имя, фамилия, полное имя и компания
  разделены; произвольное display name не разбирается на выдуманные компоненты.
  Checkbox требует решения пользователя с точными label, job, origin и expiry.
  Модель не превращает неизвестные факты анкеты в разрешённые ответы.
- Локальная версия резюме имеет свой `file_path`. Перед preview создаётся
  отдельный файл по SHA-256; смена файла, профиля, вакансии, письма или политики
  после preview блокирует старое подтверждение. В окне показаны название/версия CV,
  профиль кандидата и адрес подачи. Это ещё не проверенная удалённая identity аккаунта.
- Структурированные ответы проверяются JSON Schema: required, types, enum, range,
  лишние свойства, duplicate keys и non-finite числа. Ошибка fit/ATS даёт `score=null`.
  Оценка ATS названа оценкой текста; она не доказывает прохождение ATS работодателя.
  OpenCode server error больше не вызывает скрытую повторную генерацию через CLI.
- Воронка читает `/api/applications` с cursor независимо от поиска вакансий.
  Отказы, офферы, интервью, unknown и ручные записи разделены. Все карточки доступны;
  длинная колонка прокручивается и имеет доступное имя/keyboard focus.
  Записи старого формата без transport показываются как ручные, а не проверенные.
- Непроверенное локальное CV больше не получает 0% из `Number(null)`.
  Счётчик проверенного автоотклика не считает наличие общего адаптера проверкой площадки.
- HAR sessions записываются зашифрованными: Windows DPAPI текущего пользователя;
  POSIX Fernet с отдельным ключом в каталоге пользователя 0700/файле 0600.
  Старый plaintext формат атомарно мигрирует перед использованием. Повреждённое
  хранилище не откатывается на plaintext. DPAPI API сверены с
  [документацией Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).
  Проверяется scheme/host/port; redirects 301/302/303/307/308 не выполняются.

### Результаты второго пакета

| Проверка | Результат |
|---|---|
| Полный deterministic | 1727 passed, 5 skipped |
| Browser job: UI, infographic, external forms | 114 passed |
| Задержанные ответы A после отрисовки B: карточка, письмо, заметка | 3 passed |
| Финальный refresh/гонки/session encryption/origin/redirect subset | 22 passed, 1 skipped |
| mypy | 87 source files, ошибок нет |
| Полный ruff | 410 замечаний; gate остаётся открытым |
| Ruff для новых candidate/secret/AI/ledger/scoring unit файлов | Успешно |
| git diff --check | Успешно |

Пятый platform skip — POSIX permissions encrypted vault на Windows. Наборы
пересекаются. Результаты сохранены в локальном `outputs/audit-2026-09-05/phase2-*`.
Полный deterministic включает release artifact tests с новой зависимостью jsonschema.
POSIX backend шифрования реализован, но в этой Windows-сессии не исполнялся.

### Встроенный браузер

Старый Browser runtime из резервного bundle не загрузился: `Importing module
"node:process" is not allowed in node_repl`. Использован доступный текущий
интерфейс CUA к тому же Codex In-app Browser; отдельный Chrome для этого прогона
не запускался. Таблица и действия проверялись по DOM/AX и скриншотам.

Тестовая копия: `http://127.0.0.1:50198`, временная БД, вымышленные кандидат/CV
и вакансии. Только внешний dispatcher заменён локальным unknown-результатом;
реальные UI, API, fingerprint, storage и восстановление состояния выполняются.
Сборщики выключены. Реальные пользовательские аккаунты, CV и отправки не использовались.

Потоки:

1. `/applications` загружает 208 строк, включая вторую API-страницу; последняя
   карточка открывает вакансию 210. Найденное растяжение страницы длинной колонкой исправлено.
2. Настройки → Резюме → Редактировать показывает имя, файл именно этой версии и текст.
   Без оценки видно «Не проверено».
3. Вакансия 1 → письмо → Отправить → preview → подтверждение → unknown.
   Окно остаётся, submit заблокирован, success toast отсутствует. В ledger появляется
   ещё одна unknown-запись: всего 209, sent по-прежнему 201.
4. Фильтр вакансий HH даёт пустой список; воронка сохраняет 209 записей и 2 unknown.
5. Источники показывают 0 проверенных автоинтеграций вместо прежних 13.
6. 1280×720, обычный размер панели около 1019 px и 390×900: нет горизонтального
   overflow документа; на мобильном предупреждение/отмена доступны прокруткой.
   Title/URL корректны, содержимое непустое, error overlay отсутствует,
   relevant console errors/warnings отсутствуют. Override viewport сброшен.

Снимки вне репозитория:

- `C:/Users/kiwun/.codex/visualizations/2026/09/05/01a07172-176a-7e12-9b32-168e6cca55d2/iab-ledger-desktop.png`
- `C:/Users/kiwun/.codex/visualizations/2026/09/05/01a07172-176a-7e12-9b32-168e6cca55d2/iab-unknown-mobile.png`

Аудит целиком не закрыт. В частности, автоматической сверки внешних unknown с
историей площадки, полноценного resume fact workspace и живых контрактов новых
площадок ещё нет. Вход в HH для выполненных fixture-проверок не потребовался.
