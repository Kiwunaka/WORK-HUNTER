# Мост основного браузера

Используется официальное расширение Microsoft Playwright, не копирование профиля
и не извлечение cookies. HH работает независимо от этого моста.

1. Установить [Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm)
   в основной Chrome, где уже выполнен вход на площадки.
2. Один раз: `npm install --prefix scripts/browser-bridge --ignore-scripts`.
3. Запустить `.venv/Scripts/python scripts/browser-bridge/start.py`.
4. Ввести токен из расширения в скрытый запрос терминала. В файл он не сохраняется.

Процесс должен оставаться запущенным. Он держит официальное соединение расширения
и предоставляет комбайну локальный Windows named pipe (без открытого TCP-порта).
Рабочая группа вкладок отделена от остальных вкладок браузера; Gmail в неё не добавлять.
Остановка — Ctrl+C; основной браузер и его профиль не удаляются.

После подключения существующие `browser-login` и браузерные адаптеры Habr,
GeekJob, HireHi, Careerspace, Getmatch и RVC используют основной профиль.
Каждая операция создаёт и закрывает только свою вкладку. Подключение не означает,
что каждый сайт уже поддерживает автоматическую отправку или что резюме опубликовано.
При потере моста операции блокируются, а не переключаются на старый аккаунт.

Проверка без отправки:

```
.venv/Scripts/python -m work_hunter browser-login habr --url https://career.habr.com/profile/personal/edit --wait 5
```

Ожидаются `authenticated`, `browser_mode=main_browser_bridge` и нужный `profile_login`.
Версии Playwright Node/Python должны совпадать по minor (сейчас 1.62).
