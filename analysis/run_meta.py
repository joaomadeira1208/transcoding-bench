"""Modelo do `meta.json` — o contrato cross-language, do lado que lê.

O `meta.json` é escrito em bash na Instância e lido em Python no Mac, sem módulo
compartilhável entre as pontas (ADR-0019). O que fixa o contrato é este modelo
mais o `schema_version`, e a política é falhar alto: um campo ausente ou um tipo
divergente derruba a leitura em vez de virar linha errada no Parquet.

Duas escolhas fazem o trabalho, e nenhuma é detalhe de implementação:

- **`strict=True`**. O default do pydantic é *lax* e **coage** — `"false"` viraria
  `False` —, o que anularia o "falha alto se o tipo divergir" justamente no campo
  mais perigoso do arquivo, e faria este leitor discordar do checador stdlib do
  orquestrador sobre o que é um `meta.json` válido (ADR-0022).
- **Validar os bytes crus**, via `model_validate_json` e nunca `json.load()` +
  `model_validate()`. Em modo estrito o pydantic aplica regras *diferentes* a
  entrada JSON e a entrada Python, porque JSON tem menos tipos: pela porta do
  JSON, `str` → `datetime` continua aceito (JSON não tem tipo de data) enquanto
  `str` → `bool` é rejeitado (JSON *tem* booleano nativo). É exatamente a
  combinação que se quer, e é por isso que `load_meta` existe em vez de cada
  chamador escolher o seu ponto de entrada.

Os campos e as suas proveniências são os da decisão D4 da Spec 2, na ordem em que
ela os lista. O `output.sha256` **não** entra: já é artefato próprio do
`runs/{run_id}/` e o Pass de qualidade o baixa separadamente (ADR-0014, decisão
D4) — duplicá-lo criaria duas fontes para o mesmo dado, com a possibilidade de
discordarem.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Eixo próprio, independente do `schema_version` do plano (decisão D4): os dois
# valem "1" hoje, são artefatos diferentes e evoluem por motivos diferentes.
# Literal de texto, como no bash que o escreve.
SCHEMA_VERSION = "1"

# Todo campo de texto do arquivo é não-vazio: `jq -r` sobre uma chave presente e
# vazia devolve string vazia, e uma `scenario_id` vazia casaria com nada na
# consolidação em vez de estourar.
NonEmptyStr = Annotated[str, Field(min_length=1)]


class RunMeta(BaseModel):
    """Uma Execução, como o `run_scenario.sh` a registra."""

    # `extra="forbid"`: campo novo é mudança de forma, e mudança de forma tem
    # que passar pelo `schema_version` — não entrar em silêncio.
    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[SCHEMA_VERSION]

    # Ecoados verbatim do objeto de run: o bash copia, nunca deriva (ADR-0019).
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

    # Cunhados pela Execução. Os timestamps são `AwareDatetime` porque a dedup
    # "último `started_at` vence" ordena **instantes**: um naïve perdeu a
    # informação para normalizar, e a leitura é a única janela em que isso é
    # detectável (ADR-0022).
    run_id: NonEmptyStr
    started_at: AwareDatetime
    finished_at: AwareDatetime
    exit_code: int

    # Argumentos de bootstrap: o host os passa, a Instância nunca os descobre
    # (decisão D5). O `commit` é o SHA da campanha (ADR-0021).
    commit: NonEmptyStr
    instance_id: NonEmptyStr
    instance_type: NonEmptyStr

    # Copiado verbatim do arquivo de versões que o `docker build` gravou na
    # imagem (decisão D3): componente → versão, nunca `ffmpeg -version` parseado.
    versions: dict[NonEmptyStr, NonEmptyStr]


def load_meta(raw: str | bytes) -> RunMeta:
    """Valida os **bytes crus** de um `meta.json` e devolve a Execução tipada.

    Ponto de entrada único do papel, e é isso que ele compra: um chamador que
    fizesse `json.load()` antes cairia no modo estrito de Python, onde `str` →
    `datetime` também é rejeitado — e a saída natural dessa dor seria voltar para
    o modo lax pelo motivo errado (ADR-0022).
    """
    return RunMeta.model_validate_json(raw)


def render_json_schema() -> str:
    """O JSON Schema do modelo, na forma em que fica commitado.

    Anexo do artigo (ADR-0019), derivado e nunca transcrito à mão. O texto sai
    daqui — e não do `json.dumps` de cada chamador — para que o arquivo
    commitado e o que o teste de sincronia compara sejam byte a byte a mesma
    coisa.
    """
    schema: dict[str, Any] = RunMeta.model_json_schema()
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
