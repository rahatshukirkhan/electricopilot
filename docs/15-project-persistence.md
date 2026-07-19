# 15 — Персистентность проектов и read-only share

Фаза 4 добавляет адресуемое хранилище щитов поверх канонического schema v2. `localStorage`
остаётся быстрым offline-источником Studio; серверная копия нужна для переноса между браузерами
и share-ссылок. Это хранилище не заменяет `sizing_sessions` и не меняет аудит отдельных расчётов.

## 15.1 Контракт хранилища

`store.py` определяет `ProjectStore` с операциями `list/get/put/delete/share/get_shared` и две
реализации:

- `MemoryStore` — тот же контракт без сети для unit/API-тестов;
- `PostgresStore` — короткое соединение psycopg на одну операцию, без process-local пула.

Перед сохранением каждый проект проходит единый `Project`-контракт из `project_contract.py`.
Сервер хранит и возвращает только канонический schema v2. Клиентский `circuit.result` сохраняется
для round-trip, но не используется расчётным ядром или read-only выдачей.

Схема создаётся отдельно командой `uv run python scripts/init_db.py`:

```sql
projects(id text primary key, workspace text, name text, data jsonb, updated_at timestamptz)
shares(token text primary key, project_id text references projects(id), created_at timestamptz)
```

Миграция идемпотентна и не создаёт, не удаляет и не изменяет `sizing_sessions`.

## 15.2 Workspace и модель угроз

Studio создаёт случайный `ec_v2_workspace` в `localStorage` и передаёт его как `X-Workspace`.
Все list/get/put/delete/share операции ограничены этим значением. Это только разграничение
анонимных рабочих областей, **не аутентификация**: доступ к браузеру или копия workspace-id дают
те же права. Нельзя позиционировать механизм как аккаунтную безопасность.

Share-токен создаётся системным криптографическим генератором, не содержит project/workspace id и
не логируется приложением. Любой, кто получил ссылку, может читать проект. Отзыв отдельной ссылки
в этой фазе не реализован; удаление проекта каскадно удаляет его share-токены.

## 15.3 API и ошибки

- `GET /api/projects`, `GET|PUT|DELETE /api/projects/{id}` — требуют `X-Workspace`;
- `POST /api/projects/{id}/share` — требует `X-Workspace`, возвращает opaque `token`;
- `GET /api/shared/{token}` — анонимный read-only ответ `{project, report}`.

Shared endpoint заново вызывает `build_project_report`; сохранённые инженерные snapshots не
являются источником истины. Ответ сохраняет identity/verification норм-пака, provenance,
дисклеймер и `UNSIGNED_ADVISORY`.

Без `DATABASE_URL` project/share API возвращают HTTP 503 с `error=no_db`, а Studio продолжает
работу из `localStorage`. Пустой `X-Workspace` даёт `workspace_required`, чужой/неизвестный id —
`project_not_found`, неизвестный token — `share_not_found`.

## 15.4 Синхронизация и конфликты

`projSet` сначала атомарно обновляет локальный проект и индекс, затем ставит один debounce PUT на
две секунды. `updated_at` — ISO-8601 timestamp с timezone и версия для last-write-wins:

- более новый snapshot заменяет старый;
- повтор того же snapshot идемпотентен;
- более старый snapshot или разные данные с одинаковой меткой получают HTTP 409
  `project_conflict` с текущей серверной копией.

При 409 Studio не затирает серверную версию: записывает её локально без нового sync-цикла,
обновляет экран и показывает «проект обновлён с другого устройства». На старте remote-проекты
сливаются с локальными по `id`: более свежая копия побеждает, более новая локальная ставится на
синхронизацию, поэтому повторный запуск не создаёт дубликаты.

`#/s/<token>` — отдельный экран без editor, delete, sign, import, export и иных мутаций. Он
показывает только свежий серверный report и явно сообщает, что ссылка анонимная и read-only.
