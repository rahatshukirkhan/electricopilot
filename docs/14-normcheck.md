# Нормоконтроль щита и импорт расписания (фазы 3a/3b)

## 1. Назначение и границы

Нормоконтроль — детерминированный второй слой поверх серверного расчёта щита. Он
получает проект, заново вызывает числовое ядро для каждой цепи через
`build_project_report()`/`size()` и возвращает проверяемые замечания. Поля
`circuit.result` и другие сохранённые клиентские снимки не являются входом правил.

Результат рекомендательный и не заменяет инженерную экспертизу. Отсутствие замечания
не означает полного соответствия нормам: правило без входных данных, конфигурации или
доверенного источника возвращает `not_checked`, а не скрытый PASS.

Фаза 3a не включает импорт XLSX/CSV, каталог оборудования и изменение проекта на сервере.
Импорт и специальное diff-правило R11 добавляются отдельно фазой 3b по контракту раздела 8.

## 2. Контракт результата

`Finding`:

| Поле | Тип | Смысл |
|---|---|---|
| `rule_id` | `R01`…`R10` | стабильный идентификатор правила |
| `severity` | `error | warning | info` | приоритет правила |
| `status` | `violation | not_checked` | найдено нарушение либо проверка невозможна |
| `scope` | `circuit | board` | область |
| `circuit_id`, `circuit_ref` | `str | null` | связь со строкой щита |
| `title`, `detail` | `str` | краткое и полное объяснение |
| `observed`, `required` | `dict` | только детерминированные значения |
| `citation` | `Citation | null` | источник правила, если он задан паком |
| `source_section` | `str` | `normcheck.Rxx` |
| `data_sections` | `list[str]` | числовые секции ядра, от которых зависит observed |
| `source_trusted` | `bool` | результат провенанс-гейта для секции |
| `reason` | `str | null` | код причины `not_checked` |

Сортировка стабильна: `error → warning → info`, затем `circuit_ref`, затем
`rule_id`, затем `status`. API возвращает также summary:
`{errors, warnings, infos, not_checked, total}`. Счётчики severity включают только
`violation`; `not_checked` считается отдельно.

## 3. BoardContext и реестр

Runner собирает `BoardContext` из исходного проекта, выбранного `DataPack`, свежего
`project report` и свежих `SizingResult`. Реестр содержит ровно одну чистую функцию на
правило. Правила не вызывают LLM, сеть или часы и не мутируют проект/пак.

Нормативные параметры доступны только через `DataPack.normcheck_rule("Rxx")`.
Python и JavaScript не содержат дубликатов порогов.
Если observed зависит от sizing-результата (R01/R03/R04/R08/R10), trust-гейт включает
не только `normcheck.Rxx`, но и все числовые секции ядра, участвующие в получении этого
observed. Недоверенная зависимость также переводит правило в `not_checked`.

## 4. Раздел дата-пака

```json
{
  "provenance": {
    "normcheck.R05": {
      "origin": "illustrative",
      "source_document": "SYNTHETIC normcheck fixture",
      "entered_by": "fixture",
      "verified_by": null,
      "verified_at": null
    }
  },
  "normcheck": {
    "R05": {
      "max_imbalance_pct": 20,
      "citation": {
        "standard": "SYNTHETIC",
        "note": "ILLUSTRATIVE threshold; not an IEC value"
      }
    }
  }
}
```

Числа `iec-stub` синтетические и явно `illustrative`. В `pue-rk` правила без
официально извлечённого и проверенного раздела не конфигурируются: runner возвращает
`not_checked/rule_not_configured`. Для тестов активации используется копия пака с
полностью атрибутированным `provenance["normcheck.Rxx"]`; это не shipped norm data.

Наличие конфигурации недостаточно для нормативного вывода. Rule source считается
доверенным только по общему PER-5 гейту: допустимое происхождение, документ/URL для
public-standard, `entered_by`, `verified_by`, `verified_at`. Недоверенная секция
возвращает `not_checked/untrusted_source` вместе с наблюдаемыми данными, но не
`violation`.

## 5. Правила R01–R10

| ID | Scope | Severity | Конфигурация пака | Вход проекта |
|---|---|---|---|---|
| R01 | circuit | error | citation | свежий `SizingResult.overall_status` и governing |
| R02 | circuit | error | `max_rcd_ma` | `purpose=socket`, `meta.rcd` |
| R03 | circuit | error | `max_total_vd_pct_by_purpose` | `supply.feeder.{length_m,section_mm2,material}` и валидная топология |
| R04 | circuit | warning | citation | `supply.incomer.In_a`, рассчитанный отходящий In |
| R05 | board | warning | `max_imbalance_pct` | свежий 3ф rollup; для 1ф — `not_applicable` |
| R06 | board | warning | `min_spare_pct` | `ways_total`, количество цепей |
| R07 | circuit | warning | `motor_disallowed_curves` | purpose и `trip_curve_type` |
| R08 | circuit | error | `min_al_section_mm2` | материал и рассчитанное сечение |
| R09 | circuit | warning | citation | `prospective_fault_current_a` |
| R10 | circuit | info | `pe_section_table` | `meta.pe_section_mm2` и рассчитанное фазное сечение |

R03 вычисляет падение на фидере детерминированно по току активных фаз свежего report,
напряжению, длине, сечению и `pack.resistivity(material)`, затем складывает его с
рассчитанным ΔU цепи. Общий topology helper использует коэффициент `2` для 1ф и `√3` для
3ф. Его `data_sections` включает `voltage_drop_limit` вместе с остальными секциями sizing,
потому что выбранное сечение и наблюдаемое ΔU могут зависеть от лимита основного расчёта.
Нет любого обязательного поля — `missing_input`; недоверенная зависимость — `not_checked`.

R05 применим только к `supply.phases=3`. Для 1ф report возвращает
`imbalance_applicable=false`, а finding — `status=not_checked`, `reason=not_applicable`; L2/L3
не участвуют в среднем и не создают ложное нарушение.

`pe_section_table` — упорядоченный список строк. Каждая строка задаёт границу
`phase_max_mm2` (последняя может быть `null`) и ровно одно из:
`same_as_phase`, `fixed_mm2`, `factor`. Все границы/коэффициенты принадлежат паку.

## 6. API и Studio

`POST /api/normcheck` с `{project}` возвращает:

```json
{
  "findings": [],
  "summary": {"errors": 0, "warnings": 0, "infos": 0, "not_checked": 10, "total": 10},
  "norm_pack": {"name": "iec-stub", "version": "0.1.0", "status": "illustrative"},
  "data_provenance": {"verification_status": "NEEDS_REVIEW", "used_sections": []},
  "disclaimer": "…",
  "signoff_notice": "UNSIGNED_ADVISORY — …",
  "narrative": null
}
```

Normcheck доступен без LLM-ключа. Опциональный LLM-нарратив запускается только после
runner и не может менять findings/summary. При успехе `narrative` — стандартный объект
`LlmNarrative`; без ключа это `null`. Все числа текста должны совпадать с typed числами
findings/summary; иначе narrative отклоняется и возвращается `null`.

Studio показывает кнопку и панель «Нормоконтроль», summary, статус/версию пака,
дисклеймер и `UNSIGNED_ADVISORY`. Circuit finding содержит переход к редактору, а строка
schedule получает маркер максимальной severity. `not_checked` визуально не маскируется
под PASS.

## 7. Приёмка

- позитивный и негативный offline-тест для каждого активируемого R01–R10;
- отдельные тесты сортировки, изменения порога в паке, неполных входов,
  недоверенного источника, `pue-rk` и отсутствия LLM-ключа;
- демо: пример щита, затем убрать УЗО у розеточной цепи и увеличить длину;
- однофазное демо: питание 230 В/1ф, только L1, проверить ток ввода, feeder ΔU и R05
  `not_applicable`; затем намеренно назначить L2 и увидеть HTTP 422;
- `pytest`, Ruff и strict mypy; визуальная проверка Studio;
- отдельный PR `feat/v3-phase-3a-normcheck`, стоп перед мержем.

## 8. Импорт XLSX/CSV и R11 (фаза 3b)

### 8.1 Граница доверия и API

`POST /api/import-schedule` принимает `multipart/form-data`: файл `.xlsx` или `.csv`
размером не более 1 МиБ, опциональный JSON-объект `mapping`, `norm_pack` и флаг
`confirmed`. Сервер ограничивает также распакованный XLSX, число строк и столбцов до
передачи данных в `openpyxl`; неверный тип, превышение лимита и повреждённый файл
возвращают структурированный 4xx без частичного проекта.

Ответ имеет поля `project_draft`, `headers`, `mapping`, `preview`, `issues`,
`requires_confirmation`, `assumptions_confirmed` и `diff`. Черновик соответствует
каноническому `schema_version: 2`, получает новые project/circuit id и не сохраняется на
сервере. Studio записывает его в localStorage только после повторного запроса с проверенным
mapping и явным `confirmed=true`.

Парсер читает значения ячеек только кодом. Опциональный LLM получает исключительно список
строк-заголовков и может вернуть лишь соответствие исходного заголовка одному из закрытого
списка полей:

`ref`, `description`, `power_kw`, `voltage_v`, `phases`, `power_factor`, `length_m`,
`device_class`, `declared_in_a`, `trip_curve_type`, `rcd_ma`,
`declared_section_mm2`, `material`, `insulation`, `installation_method`.

LLM-ответ не содержит и не переопределяет значения строк. Без ключа и при невалидном
LLM-ответе применяется стабильный русско-английский словарь. Ручной mapping проходит ту же
валидацию: неизвестные заголовки/поля и дубли канонического поля отклоняются.

### 8.2 Assumptions и провенанс импорта

Числовая нормализация (`кВт → Вт`, десятичная запятая), enum-преобразования и значения
ячеек детерминированы. Неоднозначное или отсутствующее обязательное значение не угадывается
моделью: код применяет документированный UI-default, добавляет issue `assumed_value` и
пишет поле в `circuit.meta.import_declaration.assumed_fields`. Назначенная фаза также
является assumption до подтверждения пользователя.

`project.import_info` хранит имя/формат/SHA-256 исходного файла, mapping, список
project-level assumptions и признак подтверждения. Каждая цепь хранит номер строки,
исходные значения, заявленные `section_mm2`/`In_a` и assumptions отдельно от свежего
`SizingResult`. Исходные значения никогда не подменяют числовой вход ядра или сохранённый
клиентский `circuit.result`.

### 8.3 Специальное diff-правило R11

R11 не содержит нормативного порога и не входит в реестр R01–R10 дата-пака. Для каждой
импортированной цепи сервер заново вызывает `size()` выбранного пака и сравнивает:

- заявленное сечение меньше выбранного ядром — `error`;
- заявленный номинал не равен выбранному ядром — `warning`;
- отсутствующее заявленное поле — `not_checked/missing_input`.

R11 возвращает и заявленные (`observed`), и рассчитанные (`required`) значения даже при
`not_checked`. Доверие берётся из всех секций, фактически использованных свежим sizing:
при `NEEDS_REVIEW` арифметический mismatch понижается до `not_checked/untrusted_source`, а
не изображается нормативным нарушением. Ответ всегда содержит identity пака, секционный
провенанс, общий дисклеймер и `UNSIGNED_ADVISORY`.

### 8.4 Приёмка импорта

- аккуратный XLSX, русский CSV и таблица с пропусками покрыты offline-фикстурами;
- тест доказывает, что LLM видит заголовки, но не sentinel-значения строк;
- повторный import с ручным mapping воспроизводим, а подтверждённые проекты всегда имеют
  новые id и сохраняют исходные declared-значения;
- R11 покрыт trusted match/error/warning, missing input и untrusted-source сценариями;
- ручной сценарий Studio: загрузить файл, исправить mapping, подтвердить assumptions,
  создать проект и увидеть diff до записи в localStorage;
- `pytest`, Ruff, strict mypy и отдельный PR `feat/v3-phase-3b-import`, затем стоп-гейт.
