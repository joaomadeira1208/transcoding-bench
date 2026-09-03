"""Modelo do `meta.json` — o contrato cross-language, do lado que lê (ADR-0019)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

# Eixo próprio, independente do `schema_version` do plano: os dois valem "1" hoje
# e evoluem por motivos diferentes.
SCHEMA_VERSION = "1"

# `jq -r` sobre uma chave presente e vazia devolve string vazia, e uma
# `scenario_id` vazia casaria com nada na consolidação em vez de estourar.
NonEmptyStr = Annotated[str, Field(min_length=1)]


class RunMeta(BaseModel):
    """Uma Execução, como o `run_scenario.sh` a registra."""

    # `extra="forbid"`: campo novo é mudança de forma, e tem que passar pelo
    # `schema_version` em vez de entrar em silêncio.
    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[SCHEMA_VERSION]

    scenario_id: NonEmptyStr
    warmup: bool
    seed: int
    codec: NonEmptyStr
    encoder: NonEmptyStr
    input_res: NonEmptyStr
    output_res: NonEmptyStr
    video: NonEmptyStr
    instance: NonEmptyStr
    master: NonEmptyStr
    output_width: int
    output_height: int
    preset: NonEmptyStr
    crf: int
    encoder_args: list[NonEmptyStr]
    threads: int
    gop_size: int
    pix_fmt: NonEmptyStr
    strip_audio: bool
    container: NonEmptyStr
    scale_flags: NonEmptyStr

    # `AwareDatetime` porque a dedup "último `started_at` vence" ordena instantes:
    # um naïve perdeu a informação para normalizar, e a leitura é a única janela
    # em que isso é detectável.
    run_id: NonEmptyStr
    started_at: AwareDatetime
    finished_at: AwareDatetime
    exit_code: int

    commit: NonEmptyStr
    instance_id: NonEmptyStr
    instance_type: NonEmptyStr

    versions: dict[NonEmptyStr, NonEmptyStr]


def load_meta(raw: str | bytes) -> RunMeta:
    """Valida os **bytes crus** de um `meta.json` e devolve a Execução tipada.

    Bytes crus, e não `json.load()` antes: em modo estrito o pydantic rejeitaria
    `str` → `datetime` pela porta do Python, e a saída natural dessa dor seria
    voltar para o modo lax pelo motivo errado.
    """
    return RunMeta.model_validate_json(raw)


def offending_fields(error: ValidationError) -> list[str]:
    """Uma entrada `campo: motivo` por campo ofensor."""
    return [
        f"{'.'.join(str(part) for part in item['loc']) or '<raiz>'}: {item['msg']}"
        for item in error.errors()
    ]


def render_json_schema() -> str:
    """O JSON Schema do modelo, na forma exata em que fica commitado."""
    schema: dict[str, Any] = RunMeta.model_json_schema()
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
