"""O núcleo do Pass de qualidade: o que o Juiz julga, o plano que ele lê e o leitor dele.

Funções puras sobre o plano canônico, os `meta.json` já validados pelo
`meta_check` e os `output.sha256` da árvore sincronizada. Quem baixa, imprime e
escreve é o `quality_triage.py`; o que este módulo não faz é lançar coisa alguma
(ADR-0025).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from experiment_config import SHA256_DIGITS, ExperimentConfig, is_sha256
from external import RUN_HASH_FILENAME, RUNS_PREFIX
from field_checks import FieldError, check_bool, check_int, check_non_empty_str, check_record
from resume_plan import block_id, winning_replications

SCHEMA_VERSION = "1"

# Um plano escrito por um triage anterior continua no bucket depois de o Pass ser
# repetido, e detectá-lo é o serviço deste campo.
KNOWN_SCHEMA_VERSIONS = frozenset({"1"})

PLAN_FILENAME = "plan.json"


class TriageError(Exception):
    """Replicação vencedora sem o `output.sha256` que a torna um bitstream, nomeada."""


class PlanError(Exception):
    """Plano de qualidade que o Orquestrador recusa a ler, com o campo ofensor nomeado."""


@dataclass(frozen=True)
class Execution:
    """Uma Replicação vencedora: a definição que o canônico traz e o `run_id` da instância."""

    run: Mapping[str, Any]
    run_id: str

    @property
    def instance(self) -> str:
        return self.run["instance"]

    @property
    def scenario_id(self) -> str:
        return self.run["scenario_id"]


@dataclass(frozen=True)
class Bitstream:
    """Um bitstream distinto de um Cenário: o que o Juiz computa uma vez (ADR-0025)."""

    sha256: str
    representative: Execution
    shared_by: tuple[Execution, ...]
    cell_divergent: bool


@dataclass(frozen=True)
class ScenarioGroup:
    """A unidade do Pass: um Cenário e os bitstreams distintos que as Execuções dele deram."""

    group_id: str
    bitstreams: tuple[Bitstream, ...]
    divergent_cells: tuple[str, ...]


@dataclass(frozen=True)
class Triage:
    """A decisão inteira: os bitstreams a julgar e os grupos que explicam por quê."""

    outputs: tuple[Bitstream, ...]
    groups: tuple[ScenarioGroup, ...]


@dataclass(frozen=True)
class _Cell:
    """Um Cenário numa arquitetura: as 5 Replicações que a ADR-0025 espera bit-idênticas."""

    group: str
    instance: str
    name: str
    executions: tuple[tuple[str, Execution], ...]

    @property
    def divergent(self) -> bool:
        return len({sha256 for sha256, _ in self.executions}) > 1


def group_id(block: Mapping[str, Any]) -> str:
    """O nome do Cenário da ADR-0025: o do bloco sem a arquitetura."""
    stem, _, _ = block_id(block).rpartition("_")
    return stem


def triage(
    plan: Mapping[str, Any],
    metas: Iterable[Mapping[str, Any]],
    hashes: Mapping[str, str],
) -> Triage:
    """Agrupa as Replicações vencedoras por Cenário e escolhe um representante por bitstream.

    O primeiro a aparecer na ordem do canônico **é** o representante da ADR-0025:
    o plano é arch-major na ordem de `[[instance]]` e cada bloco vem `rep1..repN`,
    então a primeira ocorrência de um `sha256` é a menor Replicação da primeira
    arquitetura que o produziu — sem ordenar por `run_id` nem por `started_at`.
    """
    winners = winning_replications(metas)
    cells = tuple(_cell(block, winners, hashes) for block in plan["blocks"])
    divergent = frozenset((cell.group, cell.instance) for cell in cells if cell.divergent)

    shares: dict[tuple[str, str], list[Execution]] = {}
    for cell in cells:
        for sha256, execution in cell.executions:
            shares.setdefault((cell.group, sha256), []).append(execution)

    outputs: list[Bitstream] = []
    grouped: dict[str, list[Bitstream]] = {}
    for (group, sha256), executions in shares.items():
        bitstream = Bitstream(
            sha256=sha256,
            representative=executions[0],
            shared_by=tuple(executions),
            cell_divergent=any((group, each.instance) in divergent for each in executions),
        )
        outputs.append(bitstream)
        grouped.setdefault(group, []).append(bitstream)

    divergent_cells: dict[str, list[str]] = {}
    for cell in cells:
        if cell.divergent:
            divergent_cells.setdefault(cell.group, []).append(cell.name)

    return Triage(
        outputs=tuple(outputs),
        groups=tuple(
            ScenarioGroup(
                group_id=group,
                bitstreams=tuple(bitstreams),
                divergent_cells=tuple(divergent_cells.get(group, ())),
            )
            for group, bitstreams in grouped.items()
        ),
    )


def build_plan(config: ExperimentConfig, triaged: Triage) -> dict[str, Any]:
    """O `quality/plan.json`: o que julgar, com que modelo e contra que limiares."""
    frames = {video.slug: video.frames for video in config.videos}
    return {
        "schema_version": SCHEMA_VERSION,
        "quality": {
            "vmaf_model": config.quality.vmaf_model,
            "vmaf_delta_max": config.quality.vmaf_delta_max,
            "ssim_delta_max": config.quality.ssim_delta_max,
        },
        "outputs": [_output(bitstream, frames) for bitstream in triaged.outputs],
    }


def render_report(triaged: Triage) -> str:
    return "\n".join(_report_lines(triaged))


def check_plan(raw: str | bytes) -> dict[str, Any]:
    """Valida os bytes crus de um `quality/plan.json` e devolve o objeto já parseado."""
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PlanError(f"{PLAN_FILENAME} não é JSON válido: {error}") from error

    if not isinstance(plan, dict):
        raise PlanError(f"{PLAN_FILENAME} não é um objeto JSON: {type(plan).__name__}")

    _check_schema_version(plan)
    _check_quality(plan)
    _check_outputs(plan)
    return plan


def _cell(
    block: Mapping[str, Any],
    winners: Mapping[str, Mapping[str, Any]],
    hashes: Mapping[str, str],
) -> _Cell:
    executions = []
    for run in block["runs"]:
        if run["warmup"]:
            continue
        meta = winners.get(run["scenario_id"])
        if meta is None or meta["exit_code"] != 0:
            continue
        executions.append((_digest(meta, hashes), Execution(run=run, run_id=meta["run_id"])))
    return _Cell(
        group=group_id(block),
        instance=block["instance"],
        name=block_id(block),
        executions=tuple(executions),
    )


def _digest(meta: Mapping[str, Any], hashes: Mapping[str, str]) -> str:
    """O bitstream de uma Replicação vencedora, que o Pass não pode ter de adivinhar."""
    key = f"{RUNS_PREFIX}{meta['run_id']}/{RUN_HASH_FILENAME}"
    digest = hashes.get(meta["run_id"])
    if digest is None:
        raise TriageError(
            f"{key}: ausente, e {meta['scenario_id']} saiu com exit_code 0 — "
            f"o Cenário ficaria com um bitstream a menos"
        )
    if not _is_sha256(digest):
        raise TriageError(
            f"{key}: esperava {SHA256_DIGITS} hexadecimais minúsculos, veio {digest!r}"
        )
    return digest


def _is_sha256(value: Any) -> bool:
    return type(value) is str and is_sha256(value)


def _output(bitstream: Bitstream, frames: Mapping[str, int]) -> dict[str, Any]:
    run = bitstream.representative.run
    return {
        "run_id": bitstream.representative.run_id,
        "scenario_id": run["scenario_id"],
        "codec": run["codec"],
        "encoder": run["encoder"],
        "input_res": run["input_res"],
        "output_res": run["output_res"],
        "video": run["video"],
        "instance": run["instance"],
        "sha256": bitstream.sha256,
        "master": run["master"],
        "output_width": run["output_width"],
        "output_height": run["output_height"],
        "scale_flags": run["scale_flags"],
        "container": run["container"],
        "frames": frames[run["video"]],
        "shared_by": [
            {
                "instance": each.instance,
                "scenario_id": each.scenario_id,
                "run_id": each.run_id,
            }
            for each in bitstream.shared_by
        ],
        "cell_divergent": bitstream.cell_divergent,
    }


def _report_lines(triaged: Triage) -> Iterable[str]:
    width = max((len(group.group_id) for group in triaged.groups), default=0)
    for group in triaged.groups:
        yield (
            f"{group.group_id:<{width}}  {_plural(len(group.bitstreams), 'bitstream')}  "
            f"{_architectures(group)}"
        )

    histogram = Counter(len(group.bitstreams) for group in triaged.groups)
    yield "bitstreams distintos por grupo: " + ", ".join(
        f"{_plural(distinct, 'bitstream')}: {_plural(histogram[distinct], 'grupo')}"
        for distinct in sorted(histogram)
    )

    cells = [name for group in triaged.groups for name in group.divergent_cells]
    if cells:
        yield f"células divergentes, julgadas por inteiro ({len(cells)}):"
        yield from (f"  {name}" for name in cells)
    else:
        yield "nenhuma célula divergente: as Replicações de cada Instância são bit-idênticas"

    groups = _plural(len(triaged.groups), "grupo")
    yield f"{groups}, {_plural(len(triaged.outputs), 'output')} a julgar"


def _architectures(group: ScenarioGroup) -> str:
    """`c7g | c7i=c7a`: as arquiteturas de cada bitstream, na ordem do canônico."""
    return " | ".join(
        "=".join(dict.fromkeys(each.instance for each in bitstream.shared_by))
        for bitstream in group.bitstreams
    )


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _check_schema_version(plan: Mapping[str, Any]) -> None:
    value = plan.get("schema_version")
    if type(value) is not str or value not in KNOWN_SCHEMA_VERSIONS:
        known = ", ".join(sorted(KNOWN_SCHEMA_VERSIONS))
        raise PlanError(
            f"schema_version: esperava uma das versões conhecidas ({known}), veio {value!r}"
        )


def _check_outputs(plan: Mapping[str, Any]) -> None:
    outputs = plan.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise PlanError(f"outputs: esperava uma lista não-vazia, veio {outputs!r}")
    for index, output in enumerate(outputs):
        try:
            check_record(output, _OUTPUT_CHECKS)
        except FieldError as error:
            raise PlanError(f"outputs[{index}]: {error}") from error


def _check_quality(plan: Mapping[str, Any]) -> None:
    """O modelo e os dois limiares, copiados da definição: o Juiz nunca os escolhe."""
    try:
        check_record(plan.get("quality"), _QUALITY_CHECKS)
    except FieldError as error:
        raise PlanError(f"quality: {error}") from error


def _check_positive_number(field: str, value: Any) -> None:
    if type(value) not in (int, float) or value <= 0:
        raise FieldError(f"{field}: esperava número positivo, veio {value!r}")


def _check_positive_int(field: str, value: Any) -> None:
    check_int(field, value)
    if value <= 0:
        raise FieldError(f"{field}: esperava inteiro positivo, veio {value!r}")


def _check_sha256(field: str, value: Any) -> None:
    if not _is_sha256(value):
        raise FieldError(
            f"{field}: esperava {SHA256_DIGITS} hexadecimais minúsculos, veio {value!r}"
        )


def _check_shared_by(field: str, value: Any) -> None:
    """Toda Execução que produziu o bitstream, o representante incluído (D9).

    Vazia é recusada: é a lista pela qual o `clean` acha as cópias bit-idênticas a
    apagar, e uma lista vazia faria a retenção manter as cinco em silêncio.
    """
    if not isinstance(value, list) or not value:
        raise FieldError(f"{field}: esperava uma lista não-vazia, veio {value!r}")
    for index, sharer in enumerate(value):
        if not isinstance(sharer, Mapping):
            raise FieldError(f"{field}[{index}]: esperava um objeto JSON, veio {sharer!r}")
        for name in ("instance", "scenario_id", "run_id"):
            if name not in sharer:
                raise FieldError(f"{field}[{index}]: {name}: campo obrigatório ausente")
            check_non_empty_str(f"{field}[{index}]: {name}", sharer[name])


_QUALITY_CHECKS = {
    "vmaf_model": check_non_empty_str,
    "vmaf_delta_max": _check_positive_number,
    "ssim_delta_max": _check_positive_number,
}

_OUTPUT_CHECKS = {
    "run_id": check_non_empty_str,
    "scenario_id": check_non_empty_str,
    "codec": check_non_empty_str,
    "encoder": check_non_empty_str,
    "input_res": check_non_empty_str,
    "output_res": check_non_empty_str,
    "video": check_non_empty_str,
    "instance": check_non_empty_str,
    "sha256": _check_sha256,
    "master": check_non_empty_str,
    "output_width": _check_positive_int,
    "output_height": _check_positive_int,
    "scale_flags": check_non_empty_str,
    "container": check_non_empty_str,
    "frames": _check_positive_int,
    "shared_by": _check_shared_by,
    "cell_divergent": check_bool,
}
