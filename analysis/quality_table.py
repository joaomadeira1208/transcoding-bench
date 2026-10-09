"""As duas tabelas do Pass de qualidade: uma linha por output julgado, uma por
Cenário (ADR-0005/0025, D19 e D20 da Spec 5).

Núcleo puro — recebe os julgamentos com o log já lido e devolve as tabelas.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from typing import Any

import pyarrow as pa
from judgement import Judgement
from run_artifacts import ArtifactError
from vmaf_log import VmafLog, parse_vmaf_log

SCENARIO_KEY = ("codec", "encoder", "input_res", "output_res", "video")

P5 = 0.05


@dataclass(frozen=True)
class QualityDefinition:
    """O que o leitor tira da definição: os frames de cada vídeo, as arquiteturas
    na ordem de declaração e os dois limiares de equivalência."""

    frames: Mapping[str, int]
    instances: tuple[str, ...]
    vmaf_delta_max: float
    ssim_delta_max: float


def quality_definition(config: Mapping[str, Any]) -> QualityDefinition:
    """A definição a partir do TOML já parseado, que o validador do
    `orchestrator/` aceitou."""
    return QualityDefinition(
        frames={video["slug"]: video["frames"] for video in config["video"]},
        instances=tuple(instance["id"] for instance in config["instance"]),
        vmaf_delta_max=config["quality"]["vmaf_delta_max"],
        ssim_delta_max=config["quality"]["ssim_delta_max"],
    )


@dataclass(frozen=True)
class RawJudgement:
    """Um output julgado como o disco o entrega: `judge.json` tipado, log cru."""

    judgement: Judgement
    vmaf_log: str | None


@dataclass(frozen=True)
class QualityTables:
    """As duas tabelas mais o que ficou de fora delas."""

    outputs: pa.Table
    groups: pa.Table
    duplicates: int
    failed: int
    invalid: tuple[str, ...]


def quality_tables(
    judgements: Iterable[RawJudgement], definition: QualityDefinition
) -> QualityTables:
    """Projeta os julgamentos nas duas tabelas, em ordem canônica.

    Outputs por `scenario_id` e grupos pelo nome do Cenário: a ordem de
    `os.listdir` seria não-determinística, e o sintoma seria um `diff` de tabela
    mudando sem nada ter mudado.
    """
    received = list(judgements)
    latest = sorted(_deduplicate(received), key=lambda raw: raw.judgement.scenario_id)
    built = [_output_row(raw, definition) for raw in latest]
    rows = [row for row, _ in built]

    return QualityTables(
        outputs=_table(rows, OUTPUTS_SCHEMA),
        groups=_table(_group_rows(rows, definition), groups_schema(definition)),
        duplicates=len(received) - len(latest),
        failed=sum(1 for row in rows if row["exit_code"] != 0),
        invalid=tuple(message for _, message in built if message is not None),
    )


def summarize(result: QualityTables) -> str:
    """O relato da leitura: nada do que ficou de fora fica invisível."""
    return (
        f"{result.outputs.num_rows} outputs, "
        f"{result.groups.num_rows} grupos, "
        f"{result.duplicates} duplicata(s) fora, "
        f"{result.failed} com exit_code != 0, "
        f"{len(result.invalid)} com o log inválido ou frames divergentes"
    )


def _deduplicate(judgements: Iterable[RawJudgement]) -> list[RawJudgement]:
    """Um julgamento por `run_id`: o último `finished_at` vence, comparando
    instantes — `+00:00` e `-03:00` ordenam ao contrário como string (ADR-0019)."""
    latest: dict[str, RawJudgement] = {}
    for raw in judgements:
        current = latest.get(raw.judgement.run_id)
        if current is None or _instant(raw) > _instant(current):
            latest[raw.judgement.run_id] = raw
    return list(latest.values())


def _instant(raw: RawJudgement) -> tuple[datetime, datetime]:
    return raw.judgement.finished_at, raw.judgement.started_at


def _output_row(
    raw: RawJudgement, definition: QualityDefinition
) -> tuple[dict[str, Any], str | None]:
    judgement = raw.judgement
    expected = definition.frames.get(judgement.video)
    log, problem = _parsed_log(raw.vmaf_log)
    if log is not None and log.frames != expected:
        problem = f"{log.frames} frames no log, {expected} na definição"

    row = {
        **{name: getattr(judgement, name) for name, _ in _JUDGEMENT_COLUMNS},
        "vmaf_mean": statistics.fmean(log.vmaf) if log else None,
        "vmaf_std": statistics.pstdev(log.vmaf) if log else None,
        "vmaf_p5": _percentile(log.vmaf, P5) if log else None,
        "ssim_mean": statistics.fmean(log.ssim) if log else None,
        "frames": log.frames if log else None,
        "frames_match": log is not None and log.frames == expected,
        "sharers": {sharer.instance for sharer in judgement.shared_by},
    }
    # Num `exit_code` não-zero o log torto é o estado esperado, e o código já o
    # conta: relatá-lo de novo afogaria em ruído o caso que importa.
    if problem is None or judgement.exit_code != 0:
        return row, None
    return row, f"{judgement.run_id}/vmaf.json: {problem}"


def _parsed_log(raw: str | None) -> tuple[VmafLog | None, str | None]:
    if raw is None:
        return None, "ausente"
    try:
        return parse_vmaf_log(raw), None
    except ArtifactError as error:
        return None, str(error)


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    below = math.floor(position)
    above = min(below + 1, len(ordered) - 1)
    return ordered[below] + (ordered[above] - ordered[below]) * (position - below)


def _group_rows(
    outputs: list[dict[str, Any]], definition: QualityDefinition
) -> list[dict[str, Any]]:
    named = sorted(outputs, key=_scenario_name)
    return [
        _group_row(name, list(members), definition)
        for name, members in groupby(named, key=_scenario_name)
    ]


def _scenario_name(output: Mapping[str, Any]) -> str:
    """A `scenario_id` do representante sem a Instância e a Replicação."""
    return output["scenario_id"].rpartition(f"_{output['instance']}_")[0]


def _group_row(
    name: str, members: list[dict[str, Any]], definition: QualityDefinition
) -> dict[str, Any]:
    hashes = {
        instance: sorted({output["sha256"] for output in members if instance in output["sharers"]})
        or None
        for instance in definition.instances
    }
    judged_ok = all(output["exit_code"] == 0 and output["frames_match"] for output in members) and (
        None not in hashes.values()
    )
    vmaf_delta = _spread(output["vmaf_mean"] for output in members) if judged_ok else None
    ssim_delta = _spread(output["ssim_mean"] for output in members) if judged_ok else None
    equivalent = (
        _within(vmaf_delta, definition.vmaf_delta_max)
        and _within(ssim_delta, definition.ssim_delta_max)
        if vmaf_delta is not None and ssim_delta is not None
        else None
    )
    return {
        "scenario": name,
        **{key: members[0][key] for key in SCENARIO_KEY},
        "bitstreams": len({output["sha256"] for output in members}),
        **{_hash_column(instance): found for instance, found in hashes.items()},
        "vmaf_delta": vmaf_delta,
        "ssim_delta": ssim_delta,
        "judged_ok": judged_ok,
        "equivalent": equivalent,
    }


def _spread(means: Iterable[float]) -> float:
    values = list(means)
    return max(values) - min(values)


def _within(delta: float, limit: float) -> bool:
    """Inclusivo, e no limite pelo valor decimal que a definição declara: `0.901 -
    0.9` dá um ulp acima de `0.001` em binário."""
    return delta <= limit or math.isclose(delta, limit)


def _hash_column(instance: str) -> str:
    return f"sha256_{instance}"


def _table(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    """Coluna a coluna, pelo nome do schema: uma chave que a linha deixou de
    escrever estoura aqui em vez de virar uma coluna de nulos."""
    return pa.Table.from_pydict(
        {name: [row[name] for row in rows] for name in schema.names}, schema=schema
    )


_JUDGEMENT_COLUMNS: list[tuple[str, pa.DataType]] = [
    ("scenario_id", pa.string()),
    ("codec", pa.string()),
    ("encoder", pa.string()),
    ("input_res", pa.string()),
    ("output_res", pa.string()),
    ("video", pa.string()),
    ("instance", pa.string()),
    ("run_id", pa.string()),
    ("sha256", pa.string()),
    ("cell_divergent", pa.bool_()),
    ("exit_code", pa.int64()),
    ("instance_id", pa.string()),
    ("instance_type", pa.string()),
    ("versions", pa.map_(pa.string(), pa.string())),
]

OUTPUTS_SCHEMA = pa.schema(
    [
        *_JUDGEMENT_COLUMNS,
        ("vmaf_mean", pa.float64()),
        ("vmaf_std", pa.float64()),
        ("vmaf_p5", pa.float64()),
        ("ssim_mean", pa.float64()),
        ("frames", pa.int64()),
        ("frames_match", pa.bool_()),
    ]
)


def groups_schema(definition: QualityDefinition) -> pa.Schema:
    return pa.schema(
        [
            ("scenario", pa.string()),
            *((key, pa.string()) for key in SCENARIO_KEY),
            ("bitstreams", pa.int64()),
            *((_hash_column(instance), pa.list_(pa.string())) for instance in definition.instances),
            ("vmaf_delta", pa.float64()),
            ("ssim_delta", pa.float64()),
            ("judged_ok", pa.bool_()),
            ("equivalent", pa.bool_()),
        ]
    )
