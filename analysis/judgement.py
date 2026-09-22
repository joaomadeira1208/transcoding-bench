"""Modelo do `judge.json` — o contrato do Pass, do lado que lê (ADR-0019/0025)."""

from __future__ import annotations

from typing import Annotated, Literal

from json_contract import NonEmptyStr, PositiveInt, Strict, json_schema
from pydantic import AwareDatetime, Field

# Eixo próprio: o `judge.json` e o `meta.json` valem "1" hoje e mudam de forma
# por motivos diferentes.
SCHEMA_VERSION = "1"

SHA256_DIGITS = 64

# O digest inteiro e minúsculo, que é a forma que o `sha256sum` emite e a única
# que ele compara de volta: um truncado casa com nenhuma das cópias
# bit-idênticas que a retenção procura, e as cinco ficam no bucket em silêncio.
Sha256 = Annotated[str, Field(pattern=rf"^[0-9a-f]{{{SHA256_DIGITS}}}$")]


class Sharer(Strict):
    """Uma Execução que produziu o mesmo bitstream, o representante incluído."""

    instance: NonEmptyStr
    scenario_id: NonEmptyStr
    run_id: NonEmptyStr


class Judgement(Strict):
    """Um output julgado, como o `run_quality.sh` o registra.

    Os campos até `cell_divergent` são a entrada do `quality/plan.json`, e os
    tipos aqui são os do leitor do plano: afrouxá-los faria este papel aceitar
    um `frames: 0` que o `orchestrator/` recusa na origem.
    """

    schema_version: Literal[SCHEMA_VERSION]

    run_id: NonEmptyStr
    scenario_id: NonEmptyStr
    codec: NonEmptyStr
    encoder: NonEmptyStr
    input_res: NonEmptyStr
    output_res: NonEmptyStr
    video: NonEmptyStr
    instance: NonEmptyStr
    sha256: Sha256
    master: NonEmptyStr
    output_width: PositiveInt
    output_height: PositiveInt
    scale_flags: NonEmptyStr
    container: NonEmptyStr
    frames: PositiveInt
    shared_by: Annotated[list[Sharer], Field(min_length=1)]
    cell_divergent: bool

    started_at: AwareDatetime
    finished_at: AwareDatetime
    exit_code: int

    commit: NonEmptyStr
    instance_id: NonEmptyStr
    instance_type: NonEmptyStr

    versions: dict[NonEmptyStr, NonEmptyStr]


def load_judgement(raw: str | bytes) -> Judgement:
    """Valida os **bytes crus** de um `judge.json` e devolve o julgamento tipado.

    Bytes crus, e não `json.load()` antes: em modo estrito o pydantic rejeitaria
    `str` → `datetime` pela porta do Python, e a saída natural dessa dor seria
    voltar para o modo lax pelo motivo errado.
    """
    return Judgement.model_validate_json(raw)


def render_json_schema() -> str:
    """O JSON Schema do modelo, na forma exata em que fica commitado."""
    return json_schema(Judgement)
