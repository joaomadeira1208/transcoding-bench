"""Checagem do `judge.json` em stdlib pura — o leitor do orquestrador (D14).

Cobre os campos sobre os quais o `clean` decide, e não o arquivo inteiro: é
regra duplicada em relação ao modelo pydantic do `analysis/`, não código
compartilhado, e `test_judge_agreement.py` é o que impede os dois de divergirem.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from experiment_config import SHA256_DIGITS, is_sha256
from field_checks import (
    FieldError,
    check_aware_timestamp,
    check_int,
    check_json_record,
    check_non_empty_str,
    check_schema_version,
)

# Um `judge.json` de forma antiga continua no bucket depois de o Pass ser
# repetido, e detectá-lo é o serviço deste campo.
KNOWN_SCHEMA_VERSIONS = frozenset({"1"})

JUDGE_FILENAME = "judge.json"


class JudgementError(Exception):
    """`judge.json` que o Orquestrador recusa a ler, com o campo ofensor nomeado."""


def check_judgement(raw: str | bytes) -> dict[str, Any]:
    """Valida os bytes crus de um `judge.json` e devolve o objeto já parseado."""
    return check_json_record(raw, JUDGE_FILENAME, _CHECKS, JudgementError)


def _check_sha256(field: str, value: Any) -> None:
    if type(value) is not str or not is_sha256(value):
        raise FieldError(
            f"{field}: esperava {SHA256_DIGITS} hexadecimais minúsculos, veio {value!r}"
        )


_CHECKS: dict[str, Callable[[str, Any], None]] = {
    "schema_version": check_schema_version(KNOWN_SCHEMA_VERSIONS),
    "run_id": check_non_empty_str,
    "sha256": _check_sha256,
    "exit_code": check_int,
    "finished_at": check_aware_timestamp,
}
