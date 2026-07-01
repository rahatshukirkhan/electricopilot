"""R1 Intake: natural-language description → SizingRequest (docs/05 §5.2). Live only."""
from __future__ import annotations

from ..models import SizingRequest
from .client import OpenRouterClient, parse_json_lenient

SYSTEM = (
    "Ты — ассистент проектировщика электрики. Ты НЕ придумываешь и НЕ вычисляешь числа. "
    "Твоя задача — извлечь параметры цепи из описания в строгий JSON. Недостающие поля "
    "оставляй незаполненными (не угадывай). Ты не сертифицируешь; итог требует подписи инженера."
)

_SCHEMA_HINT = """Верни ТОЛЬКО JSON вида:
{
  "load": {"power_w"?: число, "current_a"?: число, "voltage_v": число,
           "phases": 1|3, "power_factor"?: число, "purpose"?: "lighting|power|socket|motor|general",
           "description"?: строка},
  "installation": {"method": "A1|A2|B1|B2|C|D|E|F|G", "material"?: "Cu|Al",
                   "insulation"?: "PVC|XLPE", "ambient_temp_c"?: число,
                   "grouping_circuits"?: целое, "length_m": число, "loaded_conductors"?: 2|3},
  "protection": {"device_class"?: "MCB|MCCB|gG_fuse", "prospective_fault_current_a"?: число,
                 "disconnection_time_s"?: число, "max_voltage_drop_pct"?: число}
}
Укажи ровно одно из load.power_w / load.current_a."""


def intake_parse(text: str, client: OpenRouterClient, *, model: str) -> SizingRequest:
    raw = client.complete(
        model=model, system=SYSTEM,
        user=f"Описание цепи:\n{text}\n\n{_SCHEMA_HINT}",
        json_schema={"type": "object"},
    )
    data = parse_json_lenient(raw)
    return SizingRequest.model_validate(data)
