# ElectriCopilot

**Аудируемый AI-копилот проектировщика электрики.** Первый сценарий: подбор сечения кабеля
и аппарата защиты по **IEC 60364** с результатом, прослеживаемым до пунктов норм и обязательной
подписью инженера.

По духу — «Claude Science для электрики»: диалог, объяснение и **независимая перепроверка**
средствами LLM, но **числа рождает детерминированный код**, а не модель.

> ⚠️ **Дисклеймер (обязателен).** ElectriCopilot — **рекомендательный** инструмент. Он **не
> является сертификацией** и не заменяет проектную документацию. Каждый результат несёт статус
> `UNSIGNED_ADVISORY`, пока квалифицированный инженер не проверит и не подпишет его.

> 📄 **Копирайт норм / синтетические данные.** Механизм прослеживаемости реален: каждый шаг
> ссылается на конкретный пункт/таблицу IEC 60364 и применяет реальную методологию. Но
> **числовые значения таблиц в комплекте — СИНТЕТИЧЕСКИЕ** (не выведены из IEC, `meta.status =
> illustrative`) и подлежат замене лицензионными данными IEC через подключаемый норм-пакет.
> Таблицы IEC не скрапятся и не воспроизводятся. Пометки PASS/FAIL отражают арифметику
> относительно синтетических значений, а не соответствие реальному стандарту. Подробнее —
> `docs/04-data-pack-and-copyright.md`.

## Что делает

Для одной радиальной цепи НН (1ф 230 В / 3ф 400 В, Cu/Al, PVC/XLPE):

1. **IB** — расчётный ток из нагрузки.
2. **In** — номинал аппарата из дискретного ряда (`IB ≤ In`).
3. **IZ = It · ka · kg** — пропускная способность кабеля с поправками.
4. **Проверки:** координация перегрузки (`IB≤In≤IZ`, `I2≤1.45·IZ`), падение напряжения,
   термическая стойкость к КЗ (адиабатика).
5. **Отчёт** с полной трассой рассуждения, ссылками на нормы, провенанс-нотой и блоком подписи.

Ключевой доменный нюанс воспроизведён: **gG-предохранитель** (`I2/In=1.6`) связывает выбор
жёстче, чем **MCB** (`1.45`), и заставляет брать большее сечение — см. кейс 2 в `docs/06`.

## Архитектура (кратко)

```
Детерминированное ядро (engine/)  ──►  SizingResult + AuditTrace  ──►  report (Markdown/JSON)
   владеет ВСЕМИ числами                    │                              персистентность
                                            ▼
LLM-слой (llm/, Gemini via OpenRouter): intake (NL→запрос), explain, verify  ← опционально, с фолбэками
Гардрейл числового провенанса: любое число в тексте LLM обязано быть в трассе
```

Полная спецификация — в `docs/00`…`docs/08` (spec-first, прошла адверсариальную самопроверку).

## Установка

```bash
uv sync --extra dev          # Python ≥3.11, ядро + инструменты
# опционально: uv sync --extra dev --extra db --extra api   (Neon / FastAPI)
```

Секреты — только через `.env` (см. `.env.example`, git-ignored). Ключи **не обязательны**:
без них прототип работает в детерминированном фолбэк-режиме.

```bash
cp .env.example .env         # затем вписать OPENROUTER_API_KEY и/или DATABASE_URL
```

| Переменная | Назначение |
|---|---|
| `OPENROUTER_API_KEY` | доступ к Gemini (OpenRouter); пусто → фолбэк |
| `ELECTRICOPILOT_MODEL_STRONG` / `_FAST` | `google/gemini-3.1-pro-preview` / `google/gemini-3-flash-preview` |
| `DATABASE_URL` | Neon/Postgres; пусто → JSONL в `./runs/` |
| `ELECTRICOPILOT_STRICT_PROVENANCE` | строгий провенанс (по умолчанию `true`) |

## Запуск

```bash
# сквозной пример (офлайн, детерминированный):
uv run electricopilot demo

# подбор по флагам:
uv run electricopilot size --power 4600 --voltage 230 --phases 1 --method C \
    --insulation PVC --ambient 35 --length 25 --device MCB --iscc 800

# из JSON-запроса, с объяснением, проверкой и подписью:
uv run electricopilot size --request req.json --explain --verify \
    --sign "И. Иванов <KZ-EE-1234>" --format md --out report.md

# показать доступные slug'и Gemini (нужен ключ):
uv run electricopilot models
```

Как библиотека:

```python
from electricopilot.models import LoadSpec, InstallationConditions, ProtectionSpec, SizingRequest
from electricopilot.engine import size

req = SizingRequest(
    load=LoadSpec(power_w=4600, voltage_v=230, phases=1, power_factor=1.0, purpose="power"),
    installation=InstallationConditions(method="C", insulation="PVC", ambient_temp_c=35, length_m=25),
    protection=ProtectionSpec(device_class="MCB", prospective_fault_current_a=800),
)
result = size(req)
print(result.selected_cable.cross_section_mm2, result.overall_status)  # 4.0 PASS
```

HTTP (опционально, `--extra api`):

```bash
uv run uvicorn electricopilot.api:app   # POST /size  ·  GET /health
```

## Сквозной пример (вывод)

`uv run electricopilot demo` (фидер двигателя 15 кВт, 3ф, gG-предохранитель) →

```
**Статус:** ✅ PASS  ·  **связывающий критерий:** `overload_coordination`
- Кабель: 10 мм² Cu/XLPE · It=66 A · ∏k=0.675 · IZ=44.55 A
- Аппарат: gG_fuse 32 A · I2/In=1.6 · I2=51.2 A
- IB: 25.47 A · ΔU: 1.08% (предел 5%) · Адиабатика: S_min=5.27 мм²

| Проверка | Результат | Ссылка |
| overload_coordination | ✅ | IEC 60364-4-43 §433.1 |
| voltage_drop          | ✅ | IEC 60364-5-52 Annex G |
| short_circuit         | ✅ | IEC 60364-4-43 §434.5.2 |

## Подпись инженера
- НЕ ПОДПИСАНО (UNSIGNED_ADVISORY) — требуется проверка и подпись инженера.
```

Полный отчёт содержит пошаговую трассу с формулами и ссылками, провенанс-ноту и (при ключе)
LLM-объяснение и независимую проверку.

## ElectriCopilot Studio — интерактивный веб-воркбенч (аналог Claude Science)

Визуальная рабочая среда поверх аудируемого ядра: копилот-чат (NL → расчёт → объяснение →
агент-ревьюер), интерактивные графики (**TCC**-кривые координации, свип сечения, профиль ΔU,
дерейтинг, однолинейка), проект-щиток и экспорт отчёта-артефакта. Ключ Gemini — серверный.
Дизайн и границы — `docs/10-studio-design.md`.

**Локальный запуск:**
```bash
uv sync --extra dev --extra api
uv run uvicorn electricopilot.studio_api:app --port 8000
# открыть http://localhost:8000
```
Без `OPENROUTER_API_KEY` работают все детерминированные части (расчёт, графики, трасса);
копилот-чат/ревьюер включаются при заданном ключе.

**Деплой на Vercel (статический фронтенд + Python-serverless бэкенд):**
Репозиторий уже содержит `vercel.json`, `api/index.py`, `requirements.txt`.
1. Импортировать репозиторий на [vercel.com/new](https://vercel.com/new) (или `npx vercel` из корня).
2. В Project → Settings → Environment Variables добавить `OPENROUTER_API_KEY` (для живого
   Gemini). Опционально `ELECTRICOPILOT_MODEL_STRONG/FAST`.
3. Deploy. Каждый `git push` в `main` пересобирает деплой.

Ключи в репозиторий не коммитятся; на Vercel они хранятся в env проекта.

## Тесты и качество (CI)

```bash
uv run pytest            # 36 тестов: golden-кейсы, движок, гардрейлы, датапак, e2e, фолбэк
uv run ruff check .
uv run mypy src          # strict
```

Тесты **не требуют** ключей/БД и **не делают сетевых вызовов** (LLM — через фолбэк/фейк-клиент).
Независимый оракул `scripts/reference_calc.py` пересчитывает золотые кейсы из синтетического пакета.

## Замена синтетических данных на лицензионные

1. Заполнить тот же JSON (`docs/03 §3.10`) лицензионными значениями IEC; `meta.status="licensed"`.
2. `--data-pack путь.json` (CLI) или `size(request, data_pack=…)` (API). Код движка не меняется.

## Что нужно от пользователя

- `OPENROUTER_API_KEY` — для живых ролей Gemini (intake/explain/verify).
- `DATABASE_URL` (Neon) — для персистентности в Postgres (иначе JSONL).
- Оба опциональны: без них проект полностью запускается и проходит тесты.

## Лицензия

MIT (код). Норм-данные в комплекте — синтетические; реальные значения IEC не включены.
