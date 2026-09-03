"""Checagem mínima do `meta.json` em stdlib pura — o outro leitor do contrato.

O `meta.json` tem três leitores programáticos, não um: o `consolidate.py`, em
`analysis/`, valida com o modelo pydantic estrito; `resume.py` (completude por
bloco, ADR-0012) e `quality_triage.py` (filtro + dedup, ADR-0014) moram aqui, e o
papel é stdlib-only por desenho (ADR-0017) — não podem importar aquele modelo.

A saída é **regra duplicada, não código compartilhado**, a mesma política que a
ADR-0019 adota para o dedup: o que se compra com a duplicação é verificação
independente, e o teste que a mantém honesta é o de concordância
(`test_meta_agreement.py`), que roda o mesmo arquivo inválido contra os dois
leitores.

A cobertura é a tabela da ADR-0022 — `schema_version`, `scenario_id`, `warmup`,
`exit_code`, `started_at` —, deliberadamente menor que a do modelo pydantic: são
os campos sobre os quais o orquestrador **decide**, e decidir errado custa
re-executar um bloco ou enviesar uma média. Cada um é verificado por presença,
tipo e valor, porque presença sozinha não basta: quem escreve este arquivo é bash
montando JSON à mão, e `"warmup": "false"` é uma string truthy que passa por
qualquer checagem de presença e faz o warm-up entrar na retomada como Replicação.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

# As versões de forma que este leitor sabe interpretar. Um `meta.json` escrito
# antes de uma mudança de forma continua no S3 durante a janela de retomada da
# ADR-0012, e detectá-lo é o serviço deste campo.
KNOWN_SCHEMA_VERSIONS = frozenset({"1"})


class MetaError(Exception):
    """`meta.json` que o orquestrador recusa a ler, com o campo ofensor nomeado."""


def check_meta(raw: str | bytes) -> dict[str, Any]:
    """Valida os bytes crus de um `meta.json` e devolve o objeto já parseado.

    Devolver o dict é o que faz o chamador não ter uma segunda leitura sem
    checagem: `resume.py` recebe o corpo do objeto vindo do S3, passa por aqui e
    já fica com o que precisa.
    """
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
    # Tipo exato, e não `isinstance`: em Python `bool` é subclasse de `int`, e
    # `isinstance(0, bool)` sendo falso não ajuda no sentido que importa — o que
    # se quer barrar é a string `"false"` que o bash escreve por acidente.
    if type(value) is not bool:
        raise MetaError(f"{field}: esperava booleano JSON, veio {value!r}")


def _check_int(field: str, value: Any) -> None:
    # Pelo mesmo motivo, ao contrário: `isinstance(True, int)` é **verdadeiro**,
    # então um `"exit_code": true` passaria e seria lido como "falhou".
    if type(value) is not int:
        raise MetaError(f"{field}: esperava inteiro, veio {value!r}")


def _check_aware_timestamp(field: str, value: Any) -> None:
    """Parseável **e** timezone-aware — as duas metades, sempre juntas.

    A dedup "último `started_at` vence" (ADR-0019) precisa de uma ordem total
    correta, e os leitores comparam instantes, nunca strings: `date -Is` emite
    `+00:00` e `date -u ...Z` emite `Z` para o mesmo instante, e as duas ordenam
    diferente lexicograficamente. Um timestamp naïve é pior: se o bash escreveu
    hora local sem offset, a informação para normalizar já se perdeu, e a leitura
    é a única janela em que isso é detectável.
    """
    if type(value) is not str:
        raise MetaError(f"{field}: esperava timestamp ISO-8601 como string, veio {value!r}")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise MetaError(f"{field}: timestamp ISO-8601 não parseável: {value!r}") from error

    if parsed.utcoffset() is None:
        raise MetaError(f"{field}: timestamp sem offset de fuso: {value!r}")


# A tabela da ADR-0022, na ordem em que ela a escreve.
_CHECKS: dict[str, Callable[[str, Any], None]] = {
    "schema_version": _check_schema_version,
    "scenario_id": _check_non_empty_str,
    "warmup": _check_bool,
    "exit_code": _check_int,
    "started_at": _check_aware_timestamp,
}
