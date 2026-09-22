"""Checagem do `meta.json` em stdlib pura — o leitor do orquestrador (ADR-0022).

Cobre os campos sobre os quais este papel decide, e não o arquivo inteiro: é
regra duplicada em relação ao modelo pydantic do `analysis/`, não código
compartilhado, e `test_meta_agreement.py` é o que impede os dois de divergirem.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from field_checks import (
    FieldError,
    check_aware_timestamp,
    check_bool,
    check_int,
    check_non_empty_str,
    check_record,
)

# Um `meta.json` de forma antiga continua no S3 durante a janela de retomada da
# ADR-0012, e detectá-lo é o serviço deste campo.
KNOWN_SCHEMA_VERSIONS = frozenset({"1"})


class MetaError(Exception):
    """`meta.json` que o orquestrador recusa a ler, com o campo ofensor nomeado."""


def check_meta(raw: str | bytes) -> dict[str, Any]:
    """Valida os bytes crus de um `meta.json` e devolve o objeto já parseado."""
    try:
        meta = json.loads(raw)
    except json.JSONDecodeError as error:
        raise MetaError(f"meta.json não é JSON válido: {error}") from error

    if not isinstance(meta, dict):
        raise MetaError(f"meta.json não é um objeto JSON: {type(meta).__name__}")

    try:
        check_record(meta, _CHECKS)
    except FieldError as error:
        raise MetaError(str(error)) from error

    return meta


def _check_schema_version(field: str, value: Any) -> None:
    if type(value) is not str or value not in KNOWN_SCHEMA_VERSIONS:
        known = ", ".join(sorted(KNOWN_SCHEMA_VERSIONS))
        raise FieldError(f"{field}: esperava uma das versões conhecidas ({known}), veio {value!r}")


_CHECKS: dict[str, Callable[[str, Any], None]] = {
    "schema_version": _check_schema_version,
    "scenario_id": check_non_empty_str,
    "warmup": check_bool,
    "exit_code": check_int,
    "run_id": check_non_empty_str,
    "started_at": check_aware_timestamp,
    "commit": check_non_empty_str,
}
