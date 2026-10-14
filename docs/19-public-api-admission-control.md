# Admission control публичного API

## Модель угроз

Studio — публичный serverless API. CORS не является авторизацией: прямой HTTP-клиент
может отправить большой JSON/multipart payload, занять расчёт/экспорт или вызвать платный
OpenRouter. Инженерные числа, норм-паки и провенанс при этом остаются владением
детерминированного ядра; эта политика ограничивает только ресурсы.

## Политика приложения

Каждый POST/PUT/PATCH обязан иметь `Content-Length`. До Pydantic, LLM, пересчёта или
сборки ZIP API отвергает запрос сверх `ELECTRICOPILOT_MAX_REQUEST_BYTES` с `413
request_too_large`; отсутствующий размер получает `411`. NL-ввод и сообщения Copilot
ограничены 8000 символами, история Copilot — 20 сообщениями, проект — 128 цепями.
Конфигурация может ужесточить лимит цепей; 128 остаётся жёстким верхним safety ceiling.
Значения — продуктовая конфигурация, не нормативные данные.

Локальный режим `ELECTRICOPILOT_LLM_ADMISSION_MODE=local` использует оконную квоту
клиента и общий процессный бюджет, возвращая `429 llm_client_quota` либо `503
llm_global_budget` с `Retry-After`. Он предназначен только для локальной разработки и
offline-тестов: счётчик не хранит payload/ключи и не выдаётся за распределённый лимит.
Минимальная count-only телеметрия допускает только коды решений (`llm_admitted`,
`llm_client_quota`, `llm_global_budget`, `heavy_concurrency`), без IP, NL-текста,
проекта или секретов.

Кодовое значение по умолчанию на Vercel — `disabled`: при наличии ключа live-LLM
fail-closed с `503 llm_admission_not_configured`, а детерминированные endpoints и
fallback без ключа продолжают работать. Включение live-LLM — ручной stop-gate
владельца.

Решение владельца (август 2026): для этого деплоя live-LLM включён явно — `vercel.json`
задаёт `ELECTRICOPILOT_LLM_ADMISSION_MODE=local`. Кодовый default остаётся fail-closed
для любых других деплоев без этой переменной. Оконная квота клиента и процессный бюджет
понимаются как best-effort защита в пределах одного инстанса, а не распределённый лимит;
Vercel Firewall rate-limit для LLM-путей остаётся рекомендованным периметром сверху.
Текущий режим виден в `/api/health` как `llm_admission`.

## Эксплуатация

В Vercel Firewall создать rate-limit правила минимум для `/api/intake`, `/api/explain`,
`/api/verify`, `/api/copilot`, `/api/normcheck` и отдельный общий предел `/api/`;
проверить `429` и заголовки на preview перед production. Настройка firewall и production
переменных не входит в этот PR и не должна выполняться автоматически. Секреты задаются
только в Vercel Environment Variables, не в репозитории.
