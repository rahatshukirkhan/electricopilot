"""Norm data pack module (multi-pack; see docs/04, docs/12 §1.1)."""
from .loader import (
    DEFAULT_PACK_NAME,
    DataPack,
    list_packs,
    load_data_pack,
    load_pack_by_name,
    pack_key,
)

__all__ = [
    "DataPack",
    "load_data_pack",
    "load_pack_by_name",
    "list_packs",
    "pack_key",
    "DEFAULT_PACK_NAME",
]
