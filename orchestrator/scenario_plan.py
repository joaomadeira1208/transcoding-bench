"""Construção do plano de Cenários — funções puras, sem I/O (ADR-0019)."""

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

SCHEMA_VERSION = "1"

# Nenhum ADR fixou o nome dos Masters, então ele nasce aqui: quem os gerar tem de
# materializá-los exatamente assim, ou a instância busca um objeto que não existe.
MASTER_EXTENSION = ".mkv"

WARMUP_SUFFIX = "warmup"


@dataclass(frozen=True)
class Scenario:
    """Uma célula da matriz: `codec x input_res -> output_res x vídeo x instância`."""

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

    Os ids vêm do **próprio plano**: passar a configuração de novo criaria uma
    segunda fonte para o mesmo dado, e uma instância declarada no TOML mas
    ausente do plano sairia daqui como fatia vazia em vez de erro.
    """
    return {instance: _slice(plan, instance) for instance in _instances(plan)}


def serialize_plan(plan: dict[str, Any]) -> str:
    """Serializa o plano de forma byte-determinística.

    Sem `generated_at` nem qualquer campo de ambiente: é o que faz "reproduzir o
    plano" ser verificável com `diff`.
    """
    return json.dumps(plan, indent=2, separators=(",", ": ")) + "\n"


def summarize_plan(plan: dict[str, Any]) -> str:
    """Cardinalidade do plano, contada sobre ele mesmo.

    Sobre o plano, e não recalculada da configuração: o número que o pesquisador
    confere é o do arquivo que ele tem na mão.
    """
    runs = [run for block in plan["blocks"] for run in block["runs"]]
    replications = sum(1 for run in runs if not run["warmup"])
    return f"{len(plan['blocks'])} blocks, {len(runs)} runs, {replications} replications"


def _slice(plan: dict[str, Any], instance: str) -> dict[str, Any]:
    """A fatia carrega a mesma forma de topo do canônico.

    Copiar e trocar só os blocos, em vez de remontar o topo à mão, é o que
    mantém a fatia correta depois de um campo novo entrar no plano.
    """
    return {**plan, "blocks": [block for block in plan["blocks"] if block["instance"] == instance]}


def _instances(plan: dict[str, Any]) -> list[str]:
    """Os ids de instância do plano, na ordem de primeira aparição."""
    return list(dict.fromkeys(block["instance"] for block in plan["blocks"]))


def _scenarios(config: ExperimentConfig) -> Iterator[Scenario]:
    """As 162 células da matriz, arch-major sobre uma única ordem embaralhada.

    A sequência é computada **uma vez** e percorrida igual para cada instância:
    a invariante da ADR-0010 fica estrutural, em vez de depender de o shuffle ser
    reexecutado com a mesma seed.
    """
    order = _shuffled_combinations(config)
    for instance in config.instances:
        for codec, pair, video in order:
            yield Scenario(codec=codec, pair=pair, video=video, instance=instance)


def _shuffled_combinations(
    config: ExperimentConfig,
) -> list[tuple[CodecRecord, PairRecord, VideoRecord]]:
    """As 54 combinações `codec x par x vídeo`, embaralhadas com a seed do TOML.

    Gerador próprio, nunca o estado global do módulo `random`: com o global,
    qualquer outra chamada no processo entraria no plano e "mesmo TOML ⇒ mesmos
    bytes" deixaria de valer sem que nada falhasse.
    """
    combinations = list(_combinations(config))
    random.Random(config.seed).shuffle(combinations)
    return combinations


def _combinations(
    config: ExperimentConfig,
) -> Iterator[tuple[CodecRecord, PairRecord, VideoRecord]]:
    """As 54 combinações `codec x par x vídeo`, na ordem de declaração do TOML.

    Laços sobre as listas, sem conjunto nem dicionário no caminho: a ordem
    pré-shuffle precisa ser determinística para que a pós-shuffle seja.
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
        "bitstream_muxer": codec.bitstream_muxer,
        "pmu_events": list(config.instrumentation.pmu_events),
    }


def _identity(scenario: Scenario) -> dict[str, Any]:
    """Os campos legíveis do Cenário, idênticos no bloco e em cada run dele.

    Repetir o dado é deliberado: o run precisa bastar sozinho para o bash, que só
    recebe o objeto de run.
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
    """`{encoder}_{input_res}_{output_res}_{video}_{instance}_{rep|warmup}`."""
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

    A Execução descartada termina em `_warmup`, nunca `_rep0`: `rep0` convidaria
    a entrar numa média por engano.
    """
    return [WARMUP_SUFFIX] * warmup_runs + [f"rep{n}" for n in range(1, replications + 1)]
