"""O que os dois contratos validados na leitura compartilham (ADR-0019).

A duplicação que a ADR-0022 licencia é **entre papéis** — o modelo daqui e o
checador stdlib do `orchestrator/`, em venvs separados. Aqui é o mesmo papel e o
mesmo venv, e um segundo `argparse` idêntico não compraria verificação
independente de nada.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

EXIT_OK = 0
EXIT_INVALID = 1
# Não se sabe nada sobre o conteúdo, então dizer "inválido" seria mentir.
EXIT_UNREADABLE = 2

# `jq -r` sobre uma chave presente e vazia devolve string vazia, e uma chave
# vazia casaria com nada na consolidação em vez de estourar.
NonEmptyStr = Annotated[str, Field(min_length=1)]

# Zero é o que um `jq` sobre uma chave ausente escreve, e um denominador zero
# atravessa a média em silêncio.
PositiveInt = Annotated[int, Field(gt=0)]


class Strict(BaseModel):
    """Modo estrito sobre os bytes crus, e campo desconhecido é erro.

    O default do pydantic é *lax* e **coage**: `"false"` viraria `False`, e é o
    campo booleano o mais perigoso dos dois arquivos. `extra="forbid"` porque
    campo novo é mudança de forma, e tem que passar pelo `schema_version` em vez
    de entrar em silêncio.
    """

    model_config = ConfigDict(strict=True, extra="forbid")


def offending_fields(error: ValidationError) -> list[str]:
    """Uma entrada `campo: motivo` por campo ofensor."""
    return [
        f"{'.'.join(str(part) for part in item['loc']) or '<raiz>'}: {item['msg']}"
        for item in error.errors()
    ]


def json_schema(model: type[BaseModel]) -> str:
    """O JSON Schema de um modelo, na forma exata em que fica commitado."""
    schema: dict[str, Any] = model.model_json_schema()
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def validate_cli(*, artifact: str, model: type[BaseModel]) -> int:
    """A casca das CLIs de contrato: valida um arquivo, ou emite o JSON Schema."""
    parser = argparse.ArgumentParser(
        description=f"Valida um {artifact} contra o modelo estrito do papel analysis/."
    )
    parser.add_argument("path", nargs="?", type=Path, help=f"caminho do {artifact} a validar")
    parser.add_argument(
        "--emit-schema",
        action="store_true",
        help="imprime o JSON Schema do modelo no stdout e sai",
    )
    args = parser.parse_args()

    if args.emit_schema:
        if args.path is not None:
            parser.error("--emit-schema não recebe arquivo")
        sys.stdout.write(json_schema(model))
        return EXIT_OK

    if args.path is None:
        parser.error(f"informe o {artifact} a validar (ou use --emit-schema)")

    try:
        raw = args.path.read_bytes()
    except OSError as error:
        print(f"{args.path}: {error.strerror}", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        model.model_validate_json(raw)
    except ValidationError as error:
        errors = "\n".join(f"{args.path}: {field}" for field in offending_fields(error))
        print(errors, file=sys.stderr)
        return EXIT_INVALID

    return EXIT_OK
