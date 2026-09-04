"""A tabela analítica: uma linha por Execução, projetada de `runs/` (ADR-0007).

Núcleo puro — recebe as Execuções com os artefatos já lidos e devolve a tabela.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pyarrow as pa
from run_artifacts import (
    ArtifactError,
    TimeMetrics,
    parse_ffmpeg_log,
    parse_perf,
    parse_pidstat,
    parse_time,
)
from run_meta import RunMeta

# Os dez eventos da ADR-0006, declarados aqui porque o `meta.json` não os carrega:
# a coluna existe mesmo quando o contador falta, e é assim que um evento
# indisponível numa arquitetura aparece como nulo em vez de sumir do schema.
PMU_EVENTS = (
    "cycles",
    "instructions",
    "cache-references",
    "cache-misses",
    "branch-instructions",
    "branch-misses",
    "task-clock",
    "context-switches",
    "cpu-migrations",
    "page-faults",
)


@dataclass(frozen=True)
class RawRun:
    """Uma Execução como o disco a entrega: `meta.json` tipado, artefatos crus.

    Artefato ausente é `None` — um run morto no meio não escreveu todos.
    """

    meta: RunMeta
    time: str | None
    perf: str | None
    pidstat: str | None
    ffmpeg: str | None
    sha256: str | None


@dataclass(frozen=True)
class Consolidation:
    """A tabela mais o que ficou de fora dela."""

    table: pa.Table
    warmups: int
    duplicates: int
    failed: int
    unreadable: tuple[str, ...]


def consolidate_runs(runs: Iterable[RawRun]) -> Consolidation:
    """Projeta as Execuções na tabela: warm-ups fora, dedup, ordem canônica.

    A ordem é por `scenario_id`, chave única depois do dedup. A de `os.listdir`
    seria não-determinística, e o sintoma seria um `diff` de tabela mudando sem
    nada ter mudado.
    """
    executions = list(runs)
    replications = [run for run in executions if not run.meta.warmup]
    latest = _deduplicate(replications)
    ordered = sorted(latest, key=lambda run: run.meta.scenario_id)
    built = [_row(run) for run in ordered]

    return Consolidation(
        table=_table([row for row, _ in built]),
        warmups=len(executions) - len(replications),
        duplicates=len(replications) - len(ordered),
        failed=sum(1 for run in ordered if run.meta.exit_code != 0),
        unreadable=tuple(message for _, messages in built for message in messages),
    )


def summarize(result: Consolidation) -> str:
    """O relato da consolidação: nada do que ficou de fora fica invisível."""
    return (
        f"{result.table.num_rows} linhas, "
        f"{result.warmups} warm-ups fora, "
        f"{result.duplicates} duplicatas fora, "
        f"{result.failed} com exit_code != 0, "
        f"{len(result.unreadable)} artefatos ilegíveis"
    )


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    """A razão, ou nulo explícito quando o denominador é zero ou ausente."""
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _deduplicate(runs: Iterable[RawRun]) -> list[RawRun]:
    """Uma Execução por `scenario_id`: o último `started_at` vence.

    Pelo instante que o modelo já parseou, nunca pela string: `+00:00` e `-03:00`
    ordenam ao contrário do instante que representam (ADR-0019). O `run_id`
    desempata para que a tabela não dependa da ordem de leitura.
    """
    latest: dict[str, RawRun] = {}
    for run in runs:
        current = latest.get(run.meta.scenario_id)
        if current is None or _instant(run) > _instant(current):
            latest[run.meta.scenario_id] = run
    return list(latest.values())


def _instant(run: RawRun) -> tuple[datetime, str]:
    return run.meta.started_at, run.meta.run_id


def _row(run: RawRun) -> tuple[dict[str, Any], list[str]]:
    unreadable: list[str] = []
    time = _parsed(run, "time.json", parse_time, run.time, unreadable)
    counters = _parsed(run, "perf.json", parse_perf, run.perf, unreadable) or {}
    cpu_pct = _parsed(run, "pidstat.txt", parse_pidstat, run.pidstat, unreadable) or []
    ffmpeg = _parsed(run, "ffmpeg.log", parse_ffmpeg_log, run.ffmpeg, unreadable)

    row = {
        **{name: getattr(run.meta, name) for name, _ in _META_COLUMNS},
        "output_sha256": run.sha256.strip() if run.sha256 else None,
        **{
            f"time_{field}": getattr(time, field) if time else None
            for field in TimeMetrics.model_fields
        },
        **{_perf_column(event): counters.get(event) for event in PMU_EVENTS},
        "ffmpeg_frames": ffmpeg.frames if ffmpeg else None,
        "ffmpeg_fps": ffmpeg.fps if ffmpeg else None,
        "ffmpeg_bitrate_kbps": ffmpeg.bitrate_kbps if ffmpeg else None,
        "ipc": ratio(counters.get("instructions"), counters.get("cycles")),
        "cache_miss_rate": ratio(counters.get("cache-misses"), counters.get("cache-references")),
        "branch_mispredict_rate": ratio(
            counters.get("branch-misses"), counters.get("branch-instructions")
        ),
        "cpu_pct_avg": mean(cpu_pct),
    }
    return row, unreadable


def _parsed[T](
    run: RawRun,
    filename: str,
    parse: Callable[[str], T],
    raw: str | None,
    unreadable: list[str],
) -> T | None:
    """O artefato parseado, ou nulo com o motivo anotado em `unreadable`.

    Anotado só quando a Execução terminou bem: num `exit_code` não-zero o
    artefato torto é o estado esperado — inclusive o `EXIT_INSTRUMENTATION` do
    `run_scenario.sh` —, e relatá-lo afogaria em ruído o caso que importa.
    """
    if not raw:
        return None
    try:
        return parse(raw)
    except ArtifactError as error:
        if run.meta.exit_code == 0:
            unreadable.append(f"{run.meta.run_id}/{filename}: {error}")
        return None


def _perf_column(event: str) -> str:
    return f"perf_{event.replace('-', '_')}"


def _table(rows: list[dict[str, Any]]) -> pa.Table:
    """Coluna a coluna, pelo nome do schema: uma chave que o `_row` deixou de
    escrever estoura aqui em vez de virar uma coluna de nulos."""
    return pa.Table.from_pydict(
        {name: [row[name] for row in rows] for name in TABLE_SCHEMA.names},
        schema=TABLE_SCHEMA,
    )


_META_COLUMNS: list[tuple[str, pa.DataType]] = [
    ("scenario_id", pa.string()),
    ("codec", pa.string()),
    ("encoder", pa.string()),
    ("input_res", pa.string()),
    ("output_res", pa.string()),
    ("video", pa.string()),
    ("instance", pa.string()),
    ("master", pa.string()),
    ("output_width", pa.int64()),
    ("output_height", pa.int64()),
    ("preset", pa.string()),
    ("crf", pa.int64()),
    ("encoder_args", pa.list_(pa.string())),
    ("threads", pa.int64()),
    ("gop_size", pa.int64()),
    ("pix_fmt", pa.string()),
    ("strip_audio", pa.bool_()),
    ("container", pa.string()),
    ("scale_flags", pa.string()),
    ("seed", pa.int64()),
    ("schema_version", pa.string()),
    ("run_id", pa.string()),
    # Em UTC porque o tipo do Arrow guarda um instante: os offsets com que o
    # bash escreveu cada `meta.json` já foram resolvidos na leitura.
    ("started_at", pa.timestamp("us", tz="UTC")),
    ("finished_at", pa.timestamp("us", tz="UTC")),
    ("exit_code", pa.int64()),
    ("commit", pa.string()),
    ("instance_id", pa.string()),
    ("instance_type", pa.string()),
    ("versions", pa.map_(pa.string(), pa.string())),
]

# Pelo tipo anotado no modelo, e por um dicionário: um campo novo de outro tipo
# estoura aqui em vez de virar `int64` calado.
_ARROW_BY_ANNOTATION = {float: pa.float64(), int: pa.int64()}

_TIME_COLUMNS = [
    (f"time_{field}", _ARROW_BY_ANNOTATION[info.annotation])
    for field, info in TimeMetrics.model_fields.items()
]

TABLE_SCHEMA = pa.schema(
    [
        *_META_COLUMNS,
        ("output_sha256", pa.string()),
        *_TIME_COLUMNS,
        *((_perf_column(event), pa.float64()) for event in PMU_EVENTS),
        ("ffmpeg_frames", pa.int64()),
        ("ffmpeg_fps", pa.float64()),
        ("ffmpeg_bitrate_kbps", pa.float64()),
        ("ipc", pa.float64()),
        ("cache_miss_rate", pa.float64()),
        ("branch_mispredict_rate", pa.float64()),
        ("cpu_pct_avg", pa.float64()),
    ]
)
