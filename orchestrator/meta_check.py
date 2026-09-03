"""Checagem do `meta.json` em stdlib pura — o leitor do orquestrador (ADR-0022).

Cobre os cinco campos sobre os quais este papel decide, e não o arquivo inteiro:
é regra duplicada em relação ao modelo pydantic do `analysis/`, não código
compartilhado, e `test_meta_agreement.py` é o que impede os dois de divergirem.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

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

    for field, check in _CHECKS.items():
        if field not in meta:
            raise MetaError(f"{field}: campo obrigatório ausente")
        check(field, meta[field])

    return meta


def _check_schema_version(field: str, value: Any) -> None:
    if type(value) is not str or value not in KNOWN_SCHEMA_VERSIONS:
        known = ", ".join(sorted(KNOWN_SCHEMA_VERSIONS))
        raise MetaError(f"{field}: esperava uma das versões conhecidas ({known}), veio {value!r}")


def _check_non_empty_str(field: str, value: Any) -> None:
    if type(value) is not str or not value:
        raise MetaError(f"{field}: esperava str não-vazia, veio {value!r}")


def _check_bool(field: str, value: Any) -> None:
    # Tipo exato, e não `isinstance`: o que se barra é a string `"false"` que o
    # bash escreve por acidente.
    if type(value) is not bool:
        raise MetaError(f"{field}: esperava booleano JSON, veio {value!r}")


def _check_int(field: str, value: Any) -> None:
    # Ao contrário: `isinstance(True, int)` é verdadeiro, e um `"exit_code": true`
    # passaria como "falhou".
    if type(value) is not int:
        raise MetaError(f"{field}: esperava inteiro, veio {value!r}")


def _check_aware_timestamp(field: str, value: Any) -> None:
    """Parseável **e** timezone-aware — as duas metades, sempre juntas.

    Naïve é rejeitado porque a informação para normalizar já se perdeu, e a
    leitura é a única janela em que isso é detectável.
    """
    if type(value) is not str:
        raise MetaError(f"{field}: esperava timestamp ISO-8601 como string, veio {value!r}")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise MetaError(f"{field}: timestamp ISO-8601 não parseável: {value!r}") from error

    if parsed.utcoffset() is None:
        raise MetaError(f"{field}: timestamp sem offset de fuso: {value!r}")


_CHECKS: dict[str, Callable[[str, Any], None]] = {
    "schema_version": _check_schema_version,
    "scenario_id": _check_non_empty_str,
    "warmup": _check_bool,
    "exit_code": _check_int,
    "started_at": _check_aware_timestamp,
}
