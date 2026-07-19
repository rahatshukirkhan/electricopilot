# 18 — Криптографическая идентичность расчёта

Этот контракт дополняет замороженные интерфейсы `docs/03`: он не меняет формулы ядра и не
превращает хеш в инженерную подпись. Его задача — однозначно связать project-report,
нормоконтроль, R11, read-only share и экспорт с одинаковыми эффективными входами.

## 18.1 Канонический расчётный ввод

`effective_project_input(project)` сначала применяет schema v2 и затем оставляет только поля,
которые реально потребляют engine, project-report, R01–R11 и topology:

- `schema_version`, `supply`, `diversity`;
- для каждой цепи — `id`, `ref`, `sort_index`, весь типизированный `request`;
- из `meta` — назначенную фазу, УЗО, категорию одновременности, жилы, PE и заявленные для R11
  сечение/номинал.

Клиентский `circuit.result`, rollup, timestamps, signoff, отображаемые имя/адрес и сырые значения
импорта не входят в инженерную идентичность. Канонический JSON — UTF-8, ключи сортированы,
разделители фиксированы, NaN запрещён. Его SHA-256 — `project_sha256`.

## 18.2 Норм-пак и build identity

`norm_pack_sha256` считается по полностью валидированному `DataPack.model_dump(mode="json")`,
а не по имени/версии файла. Поэтому изменение числовой, citation или provenance-секции меняет
идентичность даже без bump версии. Форматирование исходного JSON на результат не влияет.

Версия приложения берётся из `electricopilot.__version__`. Build identity разрешается в порядке
`ELECTRICOPILOT_BUILD_IDENTITY`, `VERCEL_GIT_COMMIT_SHA`; при отсутствии манифест явно содержит
`status=unavailable`, а не локальный git-вызов или выдуманный SHA. Wall-clock не участвует.

## 18.3 `CalculationManifest` v1

Манифест содержит schema version, SHA-256 эффективного project input и полного норм-пака,
идентичность/статус пакета, секции данных с их verification status, версию приложения и build
identity. `manifest_sha256` считается по этим полям; `calculation_id` — стабильный короткий
`calc-v1-<20 hex>` из полного digest. Типизированный валидатор перепроверяет оба поля.

Набор секций определяется из типизированного project input независимо от endpoint: sizing-секции,
которые может потребить проект, плюс настроенные `normcheck.Rxx`. Это сохраняет один объект для
report, normcheck, R11, share и машинных интерфейсов.

## 18.4 Экспорт и offline-проверка

ZIP содержит `manifest.json`, `calculation-input.json` и `norm-pack.json`. Последние два файла
каноничны и обязаны совпадать с `project_sha256`/`norm_pack_sha256`; исходный `project.json`
сохраняется отдельно для round-trip, но его недоверенный UI snapshot не меняет calculation id.

`electricopilot verify-manifest` без сети пересчитывает манифест из project + pack и выдаёт
структурированный mismatch. Build identity проверяется против текущего окружения либо явного
`--build-identity`. Хеш фиксирует целостность и воспроизводимость, но не заменяет
`UNSIGNED_ADVISORY` и подпись квалифицированного инженера.
