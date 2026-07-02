# 03 — Интерфейсы и схемы (ЗАМОРОЖЕННЫЙ КОНТРАКТ)

Это источник истины для типов и сигнатур. Реализация (`src/electricopilot/models.py`)
обязана точно соответствовать. Изменение контракта = сначала правка этого документа.

## 3.1 Перечисления и алиасы

```python
from typing import Literal, Optional
from pydantic import BaseModel, Field

Phase = Literal[1, 3]
Material = Literal["Cu", "Al"]
Insulation = Literal["PVC", "XLPE"]
InstallMethod = Literal["A1", "A2", "B1", "B2", "C", "D", "E", "F", "G"]
DeviceClass = Literal["MCB", "MCCB", "gG_fuse"]
CircuitPurpose = Literal["lighting", "power", "socket", "motor", "general"]
StepStatus = Literal["info", "pass", "fail", "warning"]
OverallStatus = Literal["PASS", "FAIL", "NEEDS_REVIEW"]
DataStatus = Literal["illustrative", "public_standard", "licensed"]
SignStatus = Literal["UNSIGNED_ADVISORY", "SIGNED"]

# governing_constraint: какой критерий связал выбор сечения. 'overload_coordination'
# покрывает ОБА подусловия §433.1 (In≤IZ и I2≤1.45·IZ) — так кейс с gG-предохранителем
# (связал I2) маркируется тем же значением, что и чисто-ампакитный (связал In≤IZ);
# различие фиксируется в детали шага, не в enum. Значения 'none' нет (всегда связывает
# хотя бы координация).
Governing = Literal["overload_coordination", "voltage_drop", "short_circuit"]

# ЗАМОРОЖЕННЫЙ словарь идентификаторов шагов трассы и проверок (report.py и тесты
# ключуются по этим строкам — свободный str недопустим).
StepId = Literal["current", "protection", "ampacity", "voltage_drop", "short_circuit", "summary"]
CheckName = Literal["overload_coordination", "voltage_drop", "short_circuit"]

# Замороженные ключи реестра цитат в норм-пакете (B8): DataPack.citation() принимает только их.
CitationKey = Literal["IB_load", "In_selection", "coord_overload", "voltage_drop",
                      "sc_adiabatic", "install_method"]

# Трасса — это поле SizingResult.audit_trace; алиас для читаемости 02/05.
AuditTrace = list["ReasoningStep"]
```

## 3.2 Прослеживаемость

```python
class Citation(BaseModel):
    """Ссылка на источник нормы. Механизм цитирования РЕАЛЕН; числовые значения,
    к которым он привязан, ИЛЛЮСТРАТИВНЫ (см. DataPackMeta.status / 04)."""
    standard: str              # напр. "IEC 60364-4-43"
    clause: Optional[str] = None   # напр. "433.1"
    table: Optional[str] = None    # напр. "B.52.2"
    edition: Optional[str] = None
    note: Optional[str] = None
```

## 3.3 Ввод

```python
class LoadSpec(BaseModel):
    description: Optional[str] = None
    power_w: Optional[float] = Field(None, gt=0)     # ровно одно из {power_w, current_a}
    current_a: Optional[float] = Field(None, gt=0)
    voltage_v: float = Field(..., gt=0)              # 230 (1ф, L-N) | 400 (3ф, L-L)
    phases: Phase = 1
    power_factor: float = Field(0.9, gt=0, le=1)
    purpose: CircuitPurpose = "general"
    # @model_validator: ровно одно из {power_w, current_a} задано, иначе ValidationError.

class InstallationConditions(BaseModel):
    method: InstallMethod
    material: Material = "Cu"
    insulation: Insulation = "PVC"
    ambient_temp_c: float = 30.0
    grouping_circuits: int = Field(1, ge=1)
    length_m: float = Field(..., gt=0)
    loaded_conductors: Optional[Literal[2, 3]] = None  # None → sizer заполнит из phases (1→2,3→3)

class ProtectionSpec(BaseModel):
    device_class: DeviceClass = "MCB"
    prospective_fault_current_a: Optional[float] = Field(None, gt=0)  # None → адиабатика пропущена
    disconnection_time_s: float = Field(0.1, gt=0)
    max_voltage_drop_pct: Optional[float] = None       # None → из purpose (см. vd_limit)

class SizingRequest(BaseModel):
    load: LoadSpec
    installation: InstallationConditions
    protection: ProtectionSpec = ProtectionSpec()
    project_ref: Optional[str] = None
    designer: Optional[str] = None
```

**Политика off-table (v1):** `ambient_temp_c` и `grouping_circuits` должны иметь **точное**
совпадение в таблицах пакета. Нет совпадения → `DataPackError` с перечислением допустимых
ключей. Интерполяция в v1 не выполняется (согласовано с правилом «не угадывать», `04 §4.5`).

**Разрешение `loaded_conductors`:** сизер нормализует запрос — если поле `None`, ставит
2 (1ф) или 3 (3ф) — ДО вызова движковых функций. Функции ампакитности вправе считать поле
заполненным.

## 3.4 Трасса и проверки

```python
class ReasoningStep(BaseModel):
    id: StepId
    title: str
    inputs: dict                  # display-only; report.py НЕ ключуется по этим ключам
    formula: Optional[str] = None
    computation: Optional[str] = None   # подстановка чисел
    result: dict                  # display-only; типизированные значения — в SizingResult
    citations: list[Citation] = []      # ≥1 на каждый шаг (см. 06 §6.3)
    status: StepStatus = "info"

class Check(BaseModel):
    name: CheckName
    condition: str                # человекочитаемое условие, напр. "IB ≤ In ≤ IZ"
    passed: bool
    detail: str
    citations: list[Citation] = []
```

> **Правило потребления трассы (Tier-1 P):** `report.py` и золотые тесты опираются ТОЛЬКО
> на типизированные поля `SizingResult` (`design_current_a`, `selected_cable.Iz_a`, …).
> `ReasoningStep.inputs/result` — свободные словари только для человекочитаемого показа.
>
> **Кардинальность `checks`:** `SizingResult.checks` ВСЕГДА содержит все три записи
> (`overload_coordination`, `voltage_drop`, `short_circuit`). Если `I_scc` не задан, запись
> `short_circuit` присутствует с `passed=True` и `detail="пропущено: I_scc не задан"`, а в
> `SizingResult.warnings` добавляется предупреждение (проверка не выполнялась).

## 3.5 Выбор и результат

```python
class SelectedCable(BaseModel):
    cross_section_mm2: float
    material: Material
    insulation: Insulation
    It_a: float                   # табличная (опорная) пропускная способность
    correction_total: float       # ∏k
    Iz_a: float                    # скорректированная = It·∏k
    governing_constraint: Governing

class SelectedProtection(BaseModel):
    device_class: DeviceClass
    In_a: float
    I2_over_In: float
    I2_a: float

class SignOff(BaseModel):
    """Структурная гарантия рекомендательного статуса (D6). По умолчанию НЕ подписан."""
    status: SignStatus = "UNSIGNED_ADVISORY"
    engineer_name: Optional[str] = None
    license_id: Optional[str] = None
    signed_at: Optional[str] = None       # ISO-8601, если подписан
    statement: str = (
        "Рекомендательный расчёт. НЕ является сертификацией. Требуется проверка и "
        "подпись квалифицированного инженера перед применением."
    )

class DataPackMeta(BaseModel):
    name: str
    version: str
    status: DataStatus            # "illustrative" в прототипе; "public_standard" для pue-rk (04)
    source_note: str
    source_document: Optional[str] = None   # напр. "ПУЭ РК, приказ №230 от 20.03.2015" (public_standard)

class SizingResult(BaseModel):
    request: SizingRequest
    selected_cable: SelectedCable
    selected_protection: SelectedProtection
    checks: list[Check]
    audit_trace: list[ReasoningStep]
    design_current_a: float       # IB
    voltage_drop_pct: float
    voltage_drop_limit_pct: float
    adiabatic_min_mm2: Optional[float]    # None, если I_scc не задан (проверка пропущена)
    overall_status: OverallStatus
    warnings: list[str] = []
    data_pack: DataPackMeta
    # СТРУКТУРНАЯ провенанс-пометка (B12): непуста, когда data_pack.status="illustrative";
    # report.render_markdown ОБЯЗАН её печатать. Заполняется по data_pack.status.
    data_provenance_note: str
    signoff: SignOff = SignOff()          # всегда присутствует, дефолт UNSIGNED_ADVISORY
    disclaimer: str
```

## 3.6 LLM-контракты

```python
class LlmNarrative(BaseModel):
    text: str
    model: str                    # slug использованной модели ("" для шаблонного фолбэка)
    provenance_ok: bool           # прошёл ли числовой провенанс
    unverified_numbers: list[str] = []   # числа из текста, не найденные в трассе

class VerificationVerdict(BaseModel):
    agrees: bool                  # согласен ли независимый проверяющий с расчётом
    issues: list[str] = []        # найденные расхождения/замечания
    model: str                    # "" при только-детерминированной проверке
    deterministic_ok: bool        # результат ВСЕГДА-выполняемой детерм. проверки
```

## 3.7 Публичный API библиотеки (замороженные сигнатуры)

```python
# --- исключения (модуль electricopilot.exceptions) ---
class ElectriCopilotError(Exception): ...            # база
class DataPackError(ElectriCopilotError): ...        # загрузка/валидация/off-table пакета
class LlmConfigError(ElectriCopilotError): ...       # нет ключа/неизвестный slug (D10)
class LlmError(ElectriCopilotError): ...             # сбой/пустой content живого вызова

# --- норм-пакет (мульти-пак, docs/12 §1.1) ---
# Бандловые паки живут в electricopilot/data/packs/<name>.json, резолвятся ОТ ПАКЕТА через
# importlib.resources, независимо от CWD.
DEFAULT_PACK_NAME = "iec-stub"

class DataPack(BaseModel):
    """Загруженный и провалидированный норм-пакет. Единственный доступ движка к данным —
    через методы-аксессоры (движок НЕ читает сырые словари; это фиксирует нормализацию
    ключей в одном месте, B5)."""
    meta: DataPackMeta
    standard_ratings_a: list[float]
    standard_sections_mm2: list[float]
    device_classes: dict          # {"MCB": {"i2_over_in": 1.45, "citation": {...}}, ...}
    k_material: dict              # {"Cu/PVC": 120, ..., "_citation": {...}}
    resistivity_ohm_mm2_per_m: dict   # {"Cu": 0.0225, "Al": 0.036}
    reactance_ohm_per_m: float
    vd_limits_pct: dict           # {"lighting": 3, "power": 5, ...}
    reference_ambient_c: dict     # {"air": 30, "ground": 20}
    ambient_correction: dict      # {"PVC": {"35": 0.90, ...}, "XLPE": {...}, "_citation": {...}}
    grouping_correction: dict     # {"1": 1.0, "2": 0.85, ..., "_citation": {...}}
    ampacity: dict                # ampacity[method][material][insulation][n] -> {"<S>": It}
    citations: dict               # реестр Citation по ключу шага (см. 01 §1.10 / §3.10)

    # -- аксессоры (все табличные обращения идут через них) --
    def ratings(self) -> list[float]: ...
    def sections(self) -> list[float]: ...
    def rating_for(self, ib: float) -> tuple[float, Citation]: ...          # наименьший In≥IB
    def i2_over_in(self, device: DeviceClass) -> tuple[float, Citation]: ...
    def ampacity_it(self, method, material, insulation, n, size_mm2
                    ) -> tuple[float, Citation]: ...                        # It (KeyError→DataPackError)
    def ambient_factor(self, insulation, ambient_c: float) -> tuple[float, Citation]: ...
    def grouping_factor(self, circuits: int) -> tuple[float, Citation]: ...
    def k_adiabatic(self, material, insulation) -> tuple[float, Citation]: ...
    def resistivity(self, material) -> float: ...
    def reactance(self) -> float: ...
    def vd_limit(self, purpose: CircuitPurpose) -> float: ...
    def citation(self, key: CitationKey) -> Citation: ...

# reference_ambient_c: хранится как метаданные (air 30 / ground 20). В v1 поправка ka —
# только для воздушной опоры (все кейсы — метод C/воздух); аксессора нет намеренно. Земляная
# опора (метод D, 20 °C) — ограничение v1 (07 L3).

def pack_key(x: float | int) -> str:
    """Единая нормализация числового ключа таблицы: 4.0→'4', 2.5→'2.5', 35.0→'35' (B5)."""
    return format(float(x), "g")

def load_data_pack(path: str | Path | None = None) -> DataPack: ...  # raises DataPackError
# path=None → DEFAULT_PACK_NAME. Строка без '/' и без суффикса .json → бандловый пак по имени
# (data/packs/<name>.json, importlib.resources, кэш per-process). Иначе — путь к JSON на диске
# (без кэша). Пример: load_data_pack("pue-rk"), load_data_pack("/tmp/custom.json").

def list_packs() -> list[DataPackMeta]: ...  # enumerate data/packs/*.json, для GET /api/packs и CLI `packs`

# --- движок (детерминированный) ---
def size(request: SizingRequest, *, data_pack: DataPack | None = None) -> SizingResult: ...
def design_current(load: LoadSpec, pack: DataPack) -> tuple[float, ReasoningStep]: ...      # IB
def select_rating(ib: float, device: DeviceClass, pack: DataPack) -> tuple[float, ReasoningStep]: ...  # In
def corrected_ampacity(size_mm2: float, cond: InstallationConditions, pack: DataPack
                       ) -> tuple[float, float, float, ReasoningStep]: ...  # (It, ∏k, Iz); cond.loaded_conductors уже разрешён
def voltage_drop(load: LoadSpec, cond: InstallationConditions, size_mm2: float, ib: float,
                 pack: DataPack) -> tuple[float, float, ReasoningStep]: ...  # (ΔU_V, ΔU_%)
def adiabatic_min_section(prot: ProtectionSpec, cond: InstallationConditions, pack: DataPack
                          ) -> tuple[Optional[float], ReasoningStep]: ...  # S_min | None

# --- гардрейлы ---
def check_numeric_provenance(text: str, result: SizingResult, *, strict: bool
                             ) -> tuple[bool, list[str]]: ...
def apply_provenance_downgrade(result: SizingResult, narrative: LlmNarrative
                               ) -> SizingResult: ...   # возвращает КОПИЮ с overall_status='NEEDS_REVIEW' при провале (Q)

# --- LLM-роли (клиент опционален; фолбэки не требуют ключа) ---
class OpenRouterClient:
    def complete(self, *, model: str, system: str, user: str, json_schema: dict | None = None,
                 max_output_tokens: int = 2048) -> str: ...  # LlmConfigError при неизвестном slug/нет ключа

def intake_parse(text: str, client: OpenRouterClient, *, model: str) -> SizingRequest: ...
def explain_render(result: SizingResult, client: OpenRouterClient, *, model: str) -> LlmNarrative: ...
def explain_render_template(result: SizingResult) -> LlmNarrative: ...      # детерм. фолбэк, без ключа
def verify_review(request: SizingRequest, result: SizingResult, client: OpenRouterClient,
                  *, model: str) -> VerificationVerdict: ...
def verify_deterministic_check(request: SizingRequest, result: SizingResult) -> bool: ...  # всегда

# --- отчёт / персистентность ---
def render_markdown(result: SizingResult, narrative: LlmNarrative | None,
                    verdict: VerificationVerdict | None) -> str: ...
def render_json(result: SizingResult) -> str: ...
def persist(result: SizingResult) -> str: ...   # Neon если DATABASE_URL, иначе JSONL ./runs/; возвращает id/путь
```

**Замечание по reasoning-модели (эмпирически):** `google/gemini-3.1-pro-preview` тратит
токены на скрытое рассуждение; при малом `max_output_tokens` поле `content` приходит пустым.
Клиент ставит щедрый дефолт (2048) и при пустом `content` с `finish_reason="length"`
поднимает `LlmError` (а не молча возвращает "").

## 3.8 CLI (Typer)

```
electricopilot size   [--request FILE.json | флаги нагрузки/условий] [--data-pack NAME|FILE.json]
                      [--explain] [--verify] [--format md|json] [--out FILE] [--sign "Имя <лиценз>"]
electricopilot explain --request FILE.json [--data-pack NAME|FILE.json]
electricopilot verify  --request FILE.json [--data-pack NAME|FILE.json]
electricopilot demo    [--data-pack NAME|FILE.json]  # сквозной пример из data/examples
electricopilot packs                                 # список бандловых норм-пакетов (data/packs/)
electricopilot models                               # список Gemini-slug'ов (если ключ)

Флаги нагрузки/условий: --power / --current, --voltage, --phases, --pf, --purpose,
   --method, --material, --insulation, --ambient, --grouping, --length,
   --device MCB|MCCB|gG_fuse, --iscc, --tdisc, --vdlimit
--data-pack NAME|FILE.json — норм-пакет: имя из data/packs/ (напр. pue-rk) или путь к JSON
   (в т.ч. лицензионный); по умолчанию DEFAULT_PACK_NAME ("iec-stub").
--sign "Имя Фамилия <LICENSE-ID>" — грамматика: имя = всё до '<'; license_id = внутри <…>
   (опционально). Ставит SignOff.status='SIGNED', engineer_name, license_id, signed_at=now(ISO).
   НЕ трогает SizingRequest.designer (это независимое поле авторства запроса).
Только-JSON/API поля (нет CLI-флага): loaded_conductors, designer, project_ref.
Выход: код 0 при PASS/NEEDS_REVIEW, 1 при FAIL, 2 при ошибке ввода/конфига.
```

## 3.9 HTTP API (опционально, FastAPI)

```
POST /size     body: SizingRequest (JSON)   → 200 SizingResult (JSON)
GET  /health                                → 200 {"status":"ok","mode":"live|fallback"}
```

## 3.10 Схема норм-пакета (JSON) — вход `load_data_pack` → `DataPack`

Все числовые ключи таблиц — **строки** по правилу `pack_key` (B5). Значения — **синтетические**
(см. `04`). Каждая табличная секция несёт `_citation`; отдельный реестр `citations` даёт ссылки
для шагов, у которых нет собственной таблицы (IB, выбор In, ВП, адиабатика — `01 §1.10`).

```jsonc
{
  "meta": { "name": "iec-stub", "version": "0.1.0", "status": "illustrative",
            "source_note": "SYNTHETIC values, NOT derived from IEC tables; structure mirrors IEC 60364 for pluggable licensed data" },
  "standard_ratings_a": [6,10,13,16,20,25,32,40,50,63,80,100,125,160,200,250,315,400],
  "standard_sections_mm2": [1.5,2.5,4,6,10,16,25,35,50,70,95,120,150,185,240,300],
  "device_classes": {
    "MCB":     {"i2_over_in": 1.45, "citation": {"standard":"IEC 60898",    "note":"conventional tripping I2=1.45·In"}},
    "MCCB":    {"i2_over_in": 1.30, "citation": {"standard":"IEC 60947-2"}},
    "gG_fuse": {"i2_over_in": 1.60, "citation": {"standard":"IEC 60269"}}
  },
  "k_material": { "Cu/PVC":120, "Cu/XLPE":150, "Al/PVC":80, "Al/XLPE":100,
                  "_citation": {"standard":"IEC 60364-5-54","clause":"434.5.2 / Table 43A","note":"SYNTHETIC k"} },
  "resistivity_ohm_mm2_per_m": { "Cu": 0.0225, "Al": 0.036 },
  "reactance_ohm_per_m": 0.00008,
  "vd_limits_pct": { "lighting":3, "power":5, "socket":5, "motor":5, "general":5 },
  "reference_ambient_c": { "air": 30, "ground": 20 },
  "ambient_correction": {
    "PVC":  {"25":1.05,"30":1.00,"35":0.90,"40":0.80,"45":0.70,"50":0.60,"55":0.50},
    "XLPE": {"25":1.03,"30":1.00,"35":0.94,"40":0.90,"45":0.84,"50":0.78,"55":0.72},
    "_citation": {"standard":"IEC 60364-5-52","table":"B.52.14","note":"SYNTHETIC ka"}
  },
  "grouping_correction": {
    "1":1.00,"2":0.85,"3":0.75,"4":0.70,"5":0.65,"6":0.60,"7":0.55,"8":0.53,"9":0.49,
    "_citation": {"standard":"IEC 60364-5-52","table":"B.52.17","note":"SYNTHETIC kg"}
  },
  "ampacity": {
    "C": { "Cu": {
      "PVC":  { "2": {"1.5":15,"2.5":20,"4":30,"6":40,"10":60,"16":80,"25":110,"35":135,"50":165,"70":205,"95":245,"120":285,"150":325,"185":365,"240":425,"300":485},
                "3": {"...": "synthetic"} },
      "XLPE": { "3": {"1.5":18,"2.5":24,"4":36,"6":48,"10":66,"16":88,"25":120,"35":150,"50":180,"70":225,"95":270,"120":310,"150":355,"185":400,"240":465,"300":530},
                "2": {"...": "synthetic"} }
    } },
    "_citation": {"standard":"IEC 60364-5-52","table":"B.52.2–B.52.13","note":"SYNTHETIC It"}
  },
  "citations": {
    "IB_load":       {"standard":"IEC 60364-5-52","clause":"523"},
    "In_selection":  {"standard":"IEC 60364-4-43","clause":"433.1"},
    "coord_overload":{"standard":"IEC 60364-4-43","clause":"433.1"},
    "voltage_drop":  {"standard":"IEC 60364-5-52","clause":"Annex G (informative)"},
    "sc_adiabatic":  {"standard":"IEC 60364-4-43","clause":"434.5.2"},
    "install_method":{"standard":"IEC 60364-5-52","table":"B.52.1"}
  }
}
```
