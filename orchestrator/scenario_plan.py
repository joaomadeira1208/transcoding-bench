"""Construção do plano de Cenários — funções puras, sem I/O.

Recebe a configuração **já validada** (`experiment_config.ExperimentConfig`) e
devolve o plano canônico como estrutura JSON-serializável, mais a projeção dele
numa fatia por arquitetura; escrever os arquivos é casca e mora no CLI (decisão
D4 da spec). Assim o plano é função pura do `config/experiment.toml`: mesma
entrada, mesmos bytes.

A forma é a da ADR-0019. O plano é **aninhado**: blocos de Cenário, cada bloco
com os 6 runs pré-formados (1 warm-up + 5 Replicações), warm-up primeiro. A
atomicidade do bloco e o descarte do warm-up viram estrutura do dado, não
convenção posicional que o bash precise respeitar.

Cada run carrega o conjunto **completo** de parâmetros do encode (decisão D5),
inclusive os que se repetem no bloco: o `run_scenario.sh` recebe a entrada de run
como objeto JSON e a lê com `jq`, então ela precisa bastar sozinha — o bash copia,
nunca deriva. Pelo mesmo motivo a `scenario_id` é formada aqui, uma única vez, e
o `meta.json` a ecoa verbatim.

Não há `run_id`: ele é cunhado pela instância no momento da execução (ADR-0019).
Não há índice de replicação como campo próprio: o sufixo da `scenario_id` já o
expressa e a completude é avaliada por ela (ADR-0012).

A ordem dos blocos é o **embaralhamento com a seed do TOML** das 54 combinações
`codec x par x vídeo`, replicado nas três arquiteturas: o eixo `instância` é
aplicado depois do shuffle (decisão D1 da spec, emenda à ADR-0019). É isso que
faz as três instâncias executarem os Cenários na mesma sequência, que é a
premissa de cancelamento de efeitos temporais da ADR-0010 — embaralhar os 162
blocos do produto completo daria a cada arquitetura uma ordem *diferente* das
suas 54 combinações e destruiria a propriedade.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from experiment_config import (
    CodecRecord,
    ExperimentConfig,
    InstanceRecord,
    PairRecord,
    VideoRecord,
)

# Espelha o `schema_version` do `meta.json` (ADR-0019, decisão D6): a janela de
# re-applies e retomada da ADR-0012 pode atravessar uma mudança de forma, e
# detectar isso é melhor que passar em silêncio. Literal de texto, como lá.
SCHEMA_VERSION = "1"

# Convenção de nome dos Masters. Eles são gerados no bootstrap do Orquestrador
# (ADR-0014) e vivem sob `masters/` no S3 (ADR-0011); o plano nomeia só o
# **basename**, porque o prefixo chega à instância por argumento de bootstrap e a
# instância nunca decide path. Nenhum ADR fixou este nome, então ele nasce aqui:
# quem gerar os Masters tem de materializá-los exatamente assim, ou a instância
# vai buscar um objeto que não existe.
MASTER_EXTENSION = ".mkv"

WARMUP_SUFFIX = "warmup"


@dataclass(frozen=True)
class Scenario:
    """Uma célula da matriz: `codec x input_res -> output_res x vídeo x instância`.

    É o termo do CONTEXT.md com os registros que o definem em vez dos rótulos —
    os campos legíveis saem de `_identity`, os parâmetros de encode saem dos
    registros, e nada precisa ser derivado de string.
    """

    codec: CodecRecord
    pair: PairRecord
    video: VideoRecord
    instance: InstanceRecord


def build_canonical_plan(config: ExperimentConfig) -> dict[str, Any]:
    """Materializa a matriz inteira do Experimento em blocos de Cenário."""
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": config.seed,
        "blocks": [_block(config, scenario) for scenario in _scenarios(config)],
    }


def build_instance_slices(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Projeta o canônico numa fatia por arquitetura, chaveada pelo id curto.

    É a fatia que cada Instância de encode recebe, e ela roda **todo** bloco do
    arquivo que recebeu — a seleção por arquitetura mora aqui, no lado
    inteligente (ADR-0019). Um `jq select(.instance == ...)` no bash
    reintroduziria a divergência de string (`c7g` vs `c7g.xlarge`) que já
    justifica o Python formar a `scenario_id`.

    Os ids vêm do **próprio plano**, na ordem em que aparecem nele: passar a
    configuração de novo criaria uma segunda fonte para o mesmo dado, e uma
    instância declarada no TOML mas ausente do plano (ou vice-versa) sairia
    daqui como fatia vazia em vez de erro.

    A projeção é pura: o canônico não é tocado, e como ele é arch-major
    (decisão D8) cada fatia é um trecho contíguo dele — a ordem relativa dos
    blocos sobrevive por construção, não por cuidado.
    """
    return {instance: _slice(plan, instance) for instance in _instances(plan)}


def serialize_plan(plan: dict[str, Any]) -> str:
    """Serializa o plano de forma byte-determinística (decisão D7).

    Sem `generated_at` nem qualquer campo de ambiente — o que o plano carrega vem
    todo do TOML —, com as chaves na ordem em que foram montadas, separadores
    fixos e newline final. É o que faz "reproduzir o plano" ser verificável com
    `diff`.
    """
    return json.dumps(plan, indent=2, separators=(",", ": ")) + "\n"


def summarize_plan(plan: dict[str, Any]) -> str:
    """Cardinalidade do plano, contada sobre ele mesmo.

    O CLI imprime isto em vez de recalcular as contagens a partir da
    configuração: o número que o pesquisador confere é o do plano que ele tem na
    mão, não uma segunda derivação que pode discordar dele em silêncio.
    """
    runs = [run for block in plan["blocks"] for run in block["runs"]]
    replications = sum(1 for run in runs if not run["warmup"])
    return f"{len(plan['blocks'])} blocks, {len(runs)} runs, {replications} replications"


def _slice(plan: dict[str, Any], instance: str) -> dict[str, Any]:
    """A fatia carrega a mesma forma de topo do canônico.

    Copiar o plano e trocar só os blocos — em vez de remontar `schema_version` e
    `seed` à mão — é o que faz quem lê uma fatia não precisar saber que ela é um
    recorte, inclusive depois de um campo novo entrar no topo do plano.
    """
    return {**plan, "blocks": [block for block in plan["blocks"] if block["instance"] == instance]}


def _instances(plan: dict[str, Any]) -> list[str]:
    """Os ids de instância do plano, na ordem de primeira aparição."""
    return list(dict.fromkeys(block["instance"] for block in plan["blocks"]))


def _scenarios(config: ExperimentConfig) -> Iterator[Scenario]:
    """As 162 células da matriz, arch-major sobre uma única ordem embaralhada.

    A sequência embaralhada é computada **uma vez** e percorrida igual para cada
    instância: a invariante da ADR-0010 — as três arquiteturas veem a mesma
    ordem relativa de combinações — é estrutural aqui, não algo que dependa de o
    shuffle ser reexecutado com a mesma seed.

    Instância por fora deixa os 54 blocos de cada arquitetura contíguos
    (decisão D8), o que torna o fatiamento uma fatia contígua do canônico.
    """
    order = _shuffled_combinations(config)
    for instance in config.instances:
        for codec, pair, video in order:
            yield Scenario(codec=codec, pair=pair, video=video, instance=instance)


def _shuffled_combinations(
    config: ExperimentConfig,
) -> list[tuple[CodecRecord, PairRecord, VideoRecord]]:
    """As 54 combinações `codec x par x vídeo`, embaralhadas com a seed do TOML.

    O gerador é uma instância própria, semeada aqui — nunca o estado global do
    módulo `random` (decisão D7). Com o global, qualquer outra chamada no
    processo entraria no plano, e "mesmo TOML ⇒ mesmos bytes" deixaria de valer
    sem que nada falhasse.
    """
    combinations = list(_combinations(config))
    random.Random(config.seed).shuffle(combinations)
    return combinations


def _combinations(
    config: ExperimentConfig,
) -> Iterator[tuple[CodecRecord, PairRecord, VideoRecord]]:
    """As 54 combinações `codec x par x vídeo`, na ordem de declaração do TOML.

    A ordem pré-shuffle precisa ser determinística para que a pós-shuffle seja:
    laços aninhados sobre as listas do TOML, sem conjunto nem dicionário no
    caminho.
    """
    for codec in config.codecs:
        for pair in config.pairs:
            for video in config.videos:
                yield codec, pair, video


def _block(config: ExperimentConfig, scenario: Scenario) -> dict[str, Any]:
    return {
        **_identity(scenario),
        "runs": [
            _run(config, scenario, suffix)
            for suffix in _suffixes(config.warmup_runs, config.replications)
        ],
    }


def _run(config: ExperimentConfig, scenario: Scenario, suffix: str) -> dict[str, Any]:
    codec = scenario.codec
    encode = config.encode
    output = scenario.video.geometry[scenario.pair.output_res]
    return {
        "scenario_id": _scenario_id(scenario, suffix),
        "warmup": suffix == WARMUP_SUFFIX,
        # A seed é repetida em cada run para que o bash a ecoe no `meta.json`
        # (ADR-0003/0010) lendo um nível só do JSON que recebeu.
        "seed": config.seed,
        **_identity(scenario),
        "master": f"{scenario.video.slug}_{scenario.pair.input_res}{MASTER_EXTENSION}",
        "output_width": output.width,
        "output_height": output.height,
        "preset": codec.preset,
        "crf": codec.crf,
        "encoder_args": list(codec.encoder_args),
        "threads": encode.threads,
        "gop_size": encode.gop_size,
        "pix_fmt": encode.pix_fmt,
        "strip_audio": encode.strip_audio,
        "container": encode.container,
        "scale_flags": encode.scale_flags,
        # O que o run precisa **depois** do encode: o muxer com que o bitstream é
        # extraído do container antes do `output.sha256` (ADR-0005/0007) e os
        # eventos que instrumentam a Execução (ADR-0006). Viajam no run como todo
        # o resto pelo mesmo motivo (decisão D5): o bash copia, nunca deriva.
        "bitstream_muxer": codec.bitstream_muxer,
        "pmu_events": list(config.instrumentation.pmu_events),
    }


def _identity(scenario: Scenario) -> dict[str, Any]:
    """Os campos legíveis do Cenário, idênticos no bloco e em cada run dele.

    Repetir o dado é deliberado (decisão D5): o bloco existe para quem raciocina
    sobre Cenários, e o run precisa bastar sozinho para quem só recebe o objeto
    de run.
    """
    return {
        "codec": scenario.codec.codec,
        "encoder": scenario.codec.encoder,
        "input_res": scenario.pair.input_res,
        "output_res": scenario.pair.output_res,
        "video": scenario.video.slug,
        "instance": scenario.instance.id,
    }


def _scenario_id(scenario: Scenario, suffix: str) -> str:
    """`{encoder}_{input_res}_{output_res}_{video}_{instance}_{rep|warmup}`.

    O exemplo canônico do CONTEXT.md (`libx264_2160p_1080p_bbb_c7g_rep1`) usa o
    nome do encoder, que é o `slug` do registro de codec; o rótulo do codec
    (`h264`) é leitura humana e não entra na chave.
    """
    return "_".join(
        (
            scenario.codec.slug,
            scenario.pair.input_res,
            scenario.pair.output_res,
            scenario.video.slug,
            scenario.instance.id,
            suffix,
        )
    )


def _suffixes(warmup_runs: int, replications: int) -> list[str]:
    """Warm-up primeiro, depois `rep1`..`repN`.

    A Execução descartada termina em `_warmup`, nunca `_rep0`: warm-up não é
    Replicação (CONTEXT.md), e `rep0` convidaria a entrar numa média por engano.
    Mais de um warm-up colidiria na chave, e é a validação da spec que barra isso
    (`experiment_config`), antes de o plano existir.
    """
    return [WARMUP_SUFFIX] * warmup_runs + [f"rep{n}" for n in range(1, replications + 1)]
