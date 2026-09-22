"""As primitivas de tipo que os leitores de JSON do Orquestrador compartilham.

Cada leitor monta a sua tabela de campos e embrulha o `FieldError` na exceção
que nomeia o arquivo recusado; o porquê de serem compartilhadas está no
`README.md`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import fields
from datetime import datetime
from typing import Any


class FieldError(Exception):
    """Campo fora do tipo que o contrato promete, nomeado."""


def check_fields(
    record: type, raw: Mapping[str, Any], checks: Mapping[str, Callable[[str, Any], None]]
) -> dict[str, Any]:
    """Os campos do registro, presentes e do tipo prometido, como vieram."""
    for field in fields(record):
        if field.name not in raw:
            raise FieldError(f"{field.name}: campo obrigatório ausente")
        checks[field.name](field.name, raw[field.name])
    return {field.name: raw[field.name] for field in fields(record)}


def check_record(record: Any, checks: Mapping[str, Callable[[str, Any], None]]) -> None:
    """O mesmo laço, quando o registro é a própria tabela de campos e não uma dataclass."""
    if not isinstance(record, Mapping):
        raise FieldError(f"esperava um objeto JSON, veio {record!r}")
    for field, check in checks.items():
        if field not in record:
            raise FieldError(f"{field}: campo obrigatório ausente")
        check(field, record[field])


def check_json_record(
    raw: str | bytes,
    filename: str,
    checks: Mapping[str, Callable[[str, Any], None]],
    error: type[Exception],
) -> dict[str, Any]:
    """Os bytes crus de um arquivo do contrato, validados campo a campo, como objeto."""
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as problem:
        raise error(f"{filename} não é JSON válido: {problem}") from problem

    if not isinstance(record, dict):
        raise error(f"{filename} não é um objeto JSON: {type(record).__name__}")

    try:
        check_record(record, checks)
    except FieldError as problem:
        raise error(str(problem)) from problem

    return record


def check_schema_version(known: frozenset[str]) -> Callable[[str, Any], None]:
    """O checador do campo de versão, contra as versões que aquele leitor conhece."""

    def check(field: str, value: Any) -> None:
        if type(value) is not str or value not in known:
            versions = ", ".join(sorted(known))
            raise FieldError(
                f"{field}: esperava uma das versões conhecidas ({versions}), veio {value!r}"
            )

    return check


def check_non_empty_str(field: str, value: Any) -> None:
    if type(value) is not str or not value:
        raise FieldError(f"{field}: esperava str não-vazia, veio {value!r}")


def check_bool(field: str, value: Any) -> None:
    # Tipo exato, e não `isinstance`: o que se barra é a string `"false"` que um
    # `--arg` no lugar de um `--argjson` faz o `jq` do bash escrever.
    if type(value) is not bool:
        raise FieldError(f"{field}: esperava booleano JSON, veio {value!r}")


def check_int(field: str, value: Any) -> None:
    # Ao contrário: `isinstance(True, int)` é verdadeiro, e um `"exit_code": true`
    # passaria como "falhou".
    if type(value) is not int:
        raise FieldError(f"{field}: esperava inteiro, veio {value!r}")


def check_aware_timestamp(field: str, value: Any) -> None:
    """Parseável **e** timezone-aware — as duas metades, sempre juntas.

    Naïve é rejeitado porque a informação para normalizar já se perdeu, e a
    leitura é a única janela em que isso é detectável.
    """
    if type(value) is not str:
        raise FieldError(f"{field}: esperava timestamp ISO-8601 como string, veio {value!r}")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise FieldError(f"{field}: timestamp ISO-8601 não parseável: {value!r}") from error

    if parsed.utcoffset() is None:
        raise FieldError(f"{field}: timestamp sem offset de fuso: {value!r}")
