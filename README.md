# ElectriCopilot

**Аудируемый AI-копилот проектировщика электрики.** Подбирает сечение кабеля и аппарат защиты
по методологии **IEC 60364**, проверяет щит целиком и выпускает комплект документов — так, что
каждое число прослеживается до формулы и пункта нормы, а итог ждёт подписи инженера.

**Живое демо → [electricopilot.vercel.app](https://electricopilot.vercel.app)** · работает без регистрации

> *In English:* an auditable AI copilot for low-voltage electrical design. A deterministic engine
> sizes cables and protective devices (IEC 60364 methodology) and owns every number; the LLM only
> parses input, explains and cross-checks, and a provenance guardrail rejects any number it did not
> get from the engine. Every result carries a clause-level audit trail and stays
> `UNSIGNED_ADVISORY` until an engineer signs it. Docs and UI are in Russian.

## В чём идея

LLM удобны в диалоге, но в инженерном расчёте им нельзя доверять цифры. ElectriCopilot разводит
роли жёстко:

- **Числа рождает только детерминированный код.** Ядро (`engine/`) считает токи, поправки,
  падение напряжения и термическую стойкость. Один и тот же вход всегда даёт один и тот же выход.
- **LLM — только интерфейс и второй взгляд.** Разбирает запрос на естественном языке, объясняет
  результат, независимо его перепроверяет. **Гардрейл провенанса**: любое число в тексте модели
  обязано совпасть с числом из трассы ядра, иначе ответ понижается в статусе.
- **Аудит-трасса до пункта нормы.** Каждый шаг расчёта несёт формулу, подставленные значения и
  ссылку на пункт/таблицу; ссылки на ПУЭ РК открываются во встроенной читалке норм.
- **Человек подписывает.** Любой результат остаётся `UNSIGNED_ADVISORY`, пока квалифицированный
  инженер его не проверит. Без LLM-ключа всё расчётное работает в полном объёме.

## Честно о статусе

> ⚠️ **Это рекомендательный инструмент, не сертификация.** Он не заменяет проектную документацию
> и ответственность инженера.

Нормативные данные подключаются **паками**, и ни один из поставляемых пока не даёт «зелёного» PASS:

| Пак | Происхождение | Что это значит |
|---|---|---|
| `iec-stub` (по умолчанию) | `illustrative` | Числа таблиц **синтетические**. Таблицы IEC защищены авторским правом и не воспроизводятся — реальны только методология и ссылки на пункты. |
| `pue-rk` | `public_standard` | Значения извлечены **кодом** из открытого текста ПУЭ РК (приказ № 230 от 20.03.2015, adilet.zan.kz). Покрытие узкое: методы C/B1, Cu/Al, PVC; предел ΔU пока синтетический, проверяющий не назначен. |

Поэтому арифметически успешный расчёт честно понижается до **`NEEDS_REVIEW`** с перечнем
недоверенных секций. Лицензионные данные IEC можно подложить своим JSON-паком без правки кода —
протокол в [`docs/04`](docs/04-data-pack-and-copyright.md).

## Что умеет

**Расчёт цепи** (радиальная НН, 1ф 230 В / 3ф 400 В, Cu/Al, PVC/XLPE):
расчётный ток `IB` → номинал аппарата `In` из стандартного ряда → допустимый ток
`IZ = It · ka · kg` с поправками на температуру и группу → проверки: координация перегрузки
(`IB ≤ In ≤ IZ`, `I2 ≤ 1.45·IZ`), падение напряжения, адиабатика КЗ. Воспроизведён доменный
нюанс: gG-предохранитель (`I2/In = 1.6`) связывает выбор жёстче автомата (`1.45`) и вынуждает
брать большее сечение.

**Studio — веб-воркбенч щита:**

- копилот-чат: читает отчёт, запускает расчёт и предлагает правки цепей; сервер ничего не
  применяет сам — сначала показывается детерминированный diff, изменение вступает в силу только
  по кнопке «Применить», с undo;
- графики: TCC-кривые координации, свип сечений, профиль ΔU, дерейтинг, однолинейная схема;
- **нормоконтроль щита** R01–R10: сервер пересчитывает каждую цепь заново (сохранённым
  результатам не верит) и отдаёт замечания с observed / required / ссылкой; нет входа или
  доверенного источника — честное «НЕ ПРОВЕРЕНО», а не PASS;
- **импорт чужого щита** из XLSX/CSV: маппинг колонок с предпросмотром; при живом LLM модель
  видит только заголовки, значения ячеек читает исключительно код;
- **комплект документов** одним ZIP: отчёт, кабельный журнал и спецификация (XLSX),
  однолинейная схема (SVG + DXF), манифест расчёта;
- **библиотека норм**: 4 нормативных акта РК (ПУЭ, ПТЭ, НТД в электроэнергетике, техрегламент
  о безопасности зданий) — 18 442 пункта с поиском и читалкой;
- проекты хранятся в браузере и синхронизируются в Postgres; read-only share-ссылка показывает
  получателю свежий серверный пересчёт.

**Идентичность расчёта.** Отчёт, нормоконтроль, share-ссылка и документы несут один
`calculation_id` — SHA-256 по инженерному вводу и полному норм-паку. Экспорт байт-детерминирован,
поэтому хеш ZIP пригоден для аудита изменений, а манифест проверяется офлайн командой
`verify-manifest`. Это проверка целостности, не инженерная подпись.

## Архитектура

```
Детерминированное ядро (engine/)  ──►  SizingResult + AuditTrace  ──►  отчёт · документы · манифест
   владеет ВСЕМИ числами                      │
                                              ▼
LLM-слой (llm/, copilot/ — Gemini через OpenRouter): intake · explain · verify · агент щита
   опционален, с детерминированными фолбэками
Гардрейл провенанса: любое число в тексте LLM обязано быть в трассе ядра
```

Python 3.11+ · FastAPI · Pydantic · статический фронтенд без сборки (`web/`) ·
Postgres (Neon) опционально · деплой на Vercel (`api/`).

## Быстрый старт

```bash
uv sync --extra dev --extra api

uv run electricopilot demo                                        # сквозной пример в терминале
uv run uvicorn electricopilot.studio_api:app --port 8000          # Studio → http://localhost:8000
```

Ключи и база **не нужны**: без них работает всё детерминированное — расчёт, графики, трасса,
нормоконтроль, экспорт. Для живого копилота скопируйте `.env.example` в `.env` и задайте
`OPENROUTER_API_KEY`; для серверного хранения проектов — `DATABASE_URL` и один раз
`uv run python scripts/init_db.py` ([`docs/15-project-persistence`](docs/15-project-persistence.md)).

| Переменная | Назначение |
|---|---|
| `OPENROUTER_API_KEY` | доступ к Gemini (OpenRouter); пусто → фолбэк |
| `ELECTRICOPILOT_MODEL_STRONG` / `_FAST` | `google/gemini-3.1-pro-preview` / `google/gemini-3-flash-preview` |
| `ELECTRICOPILOT_MODEL_COPILOT` | модель tool-цикла копилота щита; пусто → быстрая (`_FAST`) |
| `DATABASE_URL` | Neon/Postgres; пусто → аудит JSONL, проекты только в localStorage |
| `ELECTRICOPILOT_STRICT_PROVENANCE` | строгий провенанс (по умолчанию `true`) |
| `ELECTRICOPILOT_LLM_ADMISSION_MODE` | доступ к live-LLM: `local` (оконная квота) или `disabled` ([`docs/19`](docs/19-public-api-admission-control.md)) |
| `ELECTRICOPILOT_BUILD_IDENTITY` | identity сборки для манифеста расчёта; на Vercel fallback — `VERCEL_GIT_COMMIT_SHA` |

`electricopilot demo` — фидер двигателя 15 кВт, 3ф, gG-предохранитель (фрагмент вывода):

```
**Статус:** ⚠️ NEEDS_REVIEW  ·  **связывающий критерий:** `overload_coordination`
- Кабель: 10 мм² Cu/XLPE · It=66 A · ∏k=0.675 · IZ=44.55 A
- Аппарат: gG_fuse 32 A · I2/In=1.6 · I2=51.2 A
- IB: 25.4713 A · ΔU: 1.078% (предел 5%)

| overload_coordination | ✅ | IB=25.47 ≤ In=32 ≤ IZ=44.55; I2=51.20 ≤ 1.45·IZ=64.60 | IEC 60364-4-43 §433.1   |
| voltage_drop          | ✅ | ΔU=1.08% (предел 5%)                                  | IEC 60364-5-52 Annex G  |
| short_circuit         | ✅ | S=10 мм² ≥ S_min=5.27 мм²                             | IEC 60364-4-43 §434.5.2 |

- Данные норм-пакета требуют проверки; арифметический PASS понижен до NEEDS_REVIEW.
- НЕ ПОДПИСАНО (UNSIGNED_ADVISORY) — требуется проверка и подпись инженера.
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
print(result.selected_cable.cross_section_mm2, result.overall_status)
```

Остальные команды CLI (`size`, `explain`, `verify`, `packs`, `verify-manifest`, `norms`) —
`uv run electricopilot --help`.

## Тесты

```bash
uv run pytest            # без ключей, БД и сети
uv run ruff check .
uv run mypy src          # strict
```

Те же три проверки гоняет CI на каждый pull request. Golden-кейсы дополнительно сверяются
независимым оракулом `scripts/reference_calc.py <pack>` — второй, не связанной с ядром
реализацией формул. Browser-e2e (Playwright) запускается отдельно и по явному флагу:
`ELECTRICOPILOT_BROWSER_E2E=1 uv run pytest -m browser_e2e`.

## Документация

Проект ведётся spec-first: сначала спецификация в `docs/`, затем код.

| | |
|---|---|
| [`00`](docs/00-plan-and-decisions.md)–[`08`](docs/08-self-check.md) | базовая спецификация: домен, архитектура, схемы, LLM-интеграция, тесты, допущения |
| [`04`](docs/04-data-pack-and-copyright.md) | норм-паки, копирайт, протокол ввода реальных данных |
| [`10`](docs/10-studio-design.md), [`11`](docs/11-studio-project-tool.md) | дизайн Studio и контракт проекта |
| [`13`](docs/13-document-export.md) | экспорт документов |
| [`14`](docs/14-normcheck.md) | нормоконтроль R01–R10 и импорт XLSX/CSV (R11) |
| [`15-copilot-tools`](docs/15-copilot-tools.md) · [`15-project-persistence`](docs/15-project-persistence.md) | агент щита · хранение и share-ссылки, модель угроз |
| [`18`](docs/18-calculation-identity.md) | идентичность расчёта и манифест |
| [`19`](docs/19-public-api-admission-control.md) | квоты и admission control публичного API |
| [`20`](docs/20-norm-library.md) | библиотека норм |

Ограничения и допущения собраны в [`docs/07`](docs/07-assumptions-and-limitations.md). Не в
скоупе сейчас: действующие СН/СП РК, другие инженерные дисциплины, автоизвлечение таблиц норм.

## Лицензия

Код — [MIT](LICENSE). Тексты IEC не включены и не воспроизводятся. Тексты нормативных актов РК
(тестовые фикстуры, значения пака `pue-rk`) взяты из открытой публикации на adilet.zan.kz.
