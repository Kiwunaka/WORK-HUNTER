# Доступ к моделям OpenRouter

Проверено 8 сентября 2026. Ключ WORK HUNTER включён, лимита расходов у ключа
нет, отдельные guardrails ему не назначены. Настройки аккаунта и workspace
показывают модели доступными; allowlist/ignore-list провайдеров аккаунта пусты.
Muse Spark 1.3 Contributor отвечает после подтверждения возраста.
Настройки доступа при проверке не изменялись.

**Углублённая проверка Luna, 18:55 UTC:** отказ воспроизведён и в веб-Playground
того же аккаунта на `Reply OK`. Контрольные GPT-4o-mini и Gemini 3.5 Flash
возвращают такой же 403 до выбора endpoint. Значит, проблема затрагивает доступ
аккаунта к нескольким закрытым моделям и воспроизводится независимо от приложения
и ключа WORK HUNTER. Точная причина выставления ограничения не раскрыта.

## GLM 5.3 Flash

**Повторная проверка в 18:25 UTC: Novita/fp8, Z.ai/fp8 и StreamLake/fp8 уже
отвечают HTTP 200. Modal/fp8 по-прежнему возвращает 429 общего пула.**
Все 21 рабочее задание GLM на low/high/max затем получили HTTP 200 через Novita.
Это восстановление доступности провайдеров; настройки аккаунта не менялись.
Ошибки качества отдельных ответов разобраны в отчёте сравнения.

При предыдущей проверке все четыре возвращали HTTP 429. Ответ API явно указывал
`limit_source=upstream_provider_shared_pool`, `provider_error_code=1302`.
Лимит воспроизведён у Novita, Z.ai, StreamLake и Modal. Увеличение лимита
расходов ключа OpenRouter не устраняет этот лимит общего пула провайдеров.

Варианты из ответа API: повторить позже либо подключить собственный API-ключ
одного из этих провайдеров через **Workspace → BYOK → провайдер → Add prioritized key**.
Это отдельный ключ провайдера, а не повторная вставка ключа OpenRouter.
В проверенном workspace BYOK для Z.ai не настроен. Успешный обход лимита
через BYOK не проверялся: ключа провайдера для этой задачи не предоставлено.
Список `provider.only` в Work Hunter сохраняет четыре выбранных провайдера.

[Документация BYOK](https://openrouter.ai/docs/guides/overview/auth/byok).

## GPT-5.6 Luna

HTTP 403 даже на нейтральном запросе `Reply OK`, с reasoning `none` и `low`:

> The request is prohibited due to a violation of provider Terms Of Service.

Повторная проверка в 18:25 UTC дала тот же результат: без параметра reasoning,
с отключённым reasoning и с отдельным выбором OpenAI, Azure и Azure/eu.
Точная причина ограничения по-прежнему не раскрывается. Ключ проверен через
`/api/v1/key`: HTTP 200, `is_free_tier=false`, отдельного лимита ключа нет.

Диагностический заголовок `X-OpenRouter-Metadata: enabled` показывает
`strategy=direct`, `summary=available=3`, доступные endpoints OpenAI/Azure,
`attempt=0`, `selected=false`. Причину отказа и способ её устранения API
не раскрывает. Регион `FRA` в метаданных — поле роутера; по нему нельзя
установить местоположение пользователя или причину ограничения.

Для [поддержки OpenRouter](https://openrouter.ai/support) подготовлен текст:

```text
Hello. On 2026-09-08, requests to openai/gpt-5.6-luna through my WORK HUNTER
API key return HTTP 403:
"The request is prohibited due to a violation of provider Terms Of Service."

This also happens with a neutral "Reply OK" prompt and reasoning.effort=none.
Muse Spark 1.3 Contributor succeeds with the same OpenRouter key.
GLM 5.3 Flash and DeepSeek V4 Flash 0731 also succeed.
The same neutral prompt fails in the model's web Playground, using the same
personal account, independently of the WORK HUNTER API key.
Control requests to openai/gpt-4o-mini and google/gemini-3.5-flash also return
the same 403 before any endpoint is selected.
The key is enabled, has no credit limit, and the UI shows no model/provider
restrictions at the account, workspace or API-key level.

With X-OpenRouter-Metadata: enabled, the response reports strategy=direct,
summary=available=3, attempt=0 and no selected endpoint. The listed available
endpoints are OpenAI/Azure. Could you identify the restriction and explain
the supported steps needed to enable access for this account?

Please clarify whether this is a billing-country or account-region restriction,
a provider-specific account restriction, or another policy check, and identify
the exact reason and supported remediation. I have not received a restriction
notice in my email.

Example generation ID from the X-Generation-Id response header:
gen-1788893685-fdOjMuxR7Cl2MmfQ3Jg0 (2026-09-08, approximately 18:55 UTC).
GET /api/v1/generation for this ID returns 404, so no further details are visible.
```

Обращение не отправлено. В тексте нет полного API-ключа; его отправлять не нужно.

## Что установлено и что остаётся неизвестным

- Ключ действующий; баланс в интерфейсе положительный. Ошибка повторяется в
  веб-интерфейсе, поэтому дефект приложения или исключительно данного API-ключа
  не объясняет отказ.
- У аккаунта нет настроенного списка исключённых провайдеров. ZDR не включён;
  Eligibility Preview показывает 563 доступные модели и 0 недоступных по
  пользовательским настройкам. Этот preview не отражает фактический ToS-отказ.
- У отказов Luna, GPT-4o-mini и Gemini нет выбранного endpoint. Значит, это не
  обычный сбой одного хостинга или ошибка параметра reasoning.
- В подключённой почте по отправителям OpenRouter найдены две квитанции,
  но уведомлений об ограничении нет.
- В просмотренной квитанции страна плательщика не указана. В форме нового
  платежа выбран Germany, но это не доказательство страны предыдущих платежей
  или сохранённой классификации аккаунта. Ничего в платёжных данных не менялось.
- Региональная/платёжная классификация — версия, не установленный факт.
  Правила OpenRouter допускают ограничения моделей по стране/региону и по
  требованиям провайдера; они не объясняют причину именно этого аккаунта.
  [OpenRouter, §§ 5.5 и 5.7](https://openrouter.ai/terms/).

Для установления конкретного триггера требуется разбор аккаунта поддержкой
OpenRouter. Перестановка параметров Work Hunter эту причину не раскрывает.
Доказательства: `outputs/luna-author-access-check.json`,
`outputs/luna-final-diagnostic.json`, `outputs/luna-generation-diagnostic.json`.
