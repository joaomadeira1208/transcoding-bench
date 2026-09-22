"""Os dois objetos de `status/` que o `run_all.sh` escreve, lidos pelo Orquestrador.

O contrato, campo a campo, está no `encode/README.md`; o que este papel decide
sobre ele, no `orchestrator/README.md`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from field_checks import (
    FieldError,
    check_aware_timestamp,
    check_bool,
    check_fields,
    check_int,
    check_non_empty_str,
)

# O que a coluna tem de acomodar não é um id de arquitetura e sim o `judge`, que
# é o mais largo dos quatro nomes que o resumo do fim põe um debaixo do outro.
INSTANCE_WIDTH = 5
HOUR_WIDTH = 8

LONGEST_SCENARIO_ID = "libsvtav1_2160p_2160p_bbb_c7g_warmup"
SCENARIO_WIDTH = len(LONGEST_SCENARIO_ID) + 1

SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60

STATUS_PREFIX = "status/"

# A chave é contrato da ADR-0011 e o valor do papel é contrato do arquivo de
# estado: colapsar os dois num `role.value` faz renomear um renomear o outro.
JUDGE_STEM = "judge"


class StatusError(Exception):
    """Objeto de `status/` que o Orquestrador recusa a ler, com o campo ofensor nomeado."""


class Role(Enum):
    """O papel de uma entrada rastreada — quem escreveu os objetos de `status/` dela."""

    ENCODE = "encode"
    JUDGE = "judge"


@dataclass(frozen=True)
class DoneMarker:
    """O marcador de `status/`: o resultado do `run_all.sh` daquela fatia."""

    instance_id: str
    finished_at: str
    runs_total: int
    runs_failed: int
    capped: bool
    exit_status: int


@dataclass(frozen=True)
class Progress:
    """O progresso de `status/`: onde a Instância estava no último run."""

    instance_id: str
    block_index: int
    block_count: int
    run_index: int
    run_count: int
    scenario_id: str
    runs_total: int
    runs_failed: int
    elapsed_seconds: int
    written_at: str


@dataclass(frozen=True)
class JudgeProgress:
    """O progresso de `status/` do Juiz: que output do plano ele está julgando."""

    instance_id: str
    output_index: int
    output_count: int
    run_id: str
    scenario_id: str
    runs_total: int
    runs_failed: int
    elapsed_seconds: int
    written_at: str


@dataclass(frozen=True)
class StatusKeys:
    """As duas chaves de `status/` de uma entrada: o marcador (D3) e o progresso (D4)."""

    done: str
    progress: str

    @classmethod
    def of(cls, role: Role, instance_type: str) -> StatusKeys:
        """As chaves que aquele papel dá aos seus objetos."""
        stem = instance_type if role is Role.ENCODE else JUDGE_STEM
        return cls(
            done=f"{STATUS_PREFIX}{stem}_done",
            progress=f"{STATUS_PREFIX}{stem}_progress",
        )


def check_done_marker(payload: Any, *, instance_id: str) -> DoneMarker | None:
    """O marcador, ou `None` quando ele é o da tentativa anterior."""
    return _checked(DoneMarker, payload, "marcador", instance_id)


def check_progress(payload: Any, *, instance_id: str) -> Progress | None:
    """O progresso, ou `None` quando ele é o da tentativa anterior — a ignorar."""
    return _checked(Progress, payload, "progresso", instance_id)


def check_judge_progress(payload: Any, *, instance_id: str) -> JudgeProgress | None:
    """O progresso do Juiz, ou `None` quando ele é o do Pass anterior — a ignorar."""
    return _checked(JudgeProgress, payload, "progresso do Juiz", instance_id)


def progress_line(progress: Progress | None, *, instance: str, runs_total: int) -> str:
    """A linha daquela arquitetura, do objeto mais o total de runs da fatia."""
    if progress is None:
        return (
            f"{'':{HOUR_WIDTH}} {instance:<{INSTANCE_WIDTH}} "
            f"sem progresso ainda, 0/{runs_total} runs reportados"
        )

    return (
        f"{hour_of(progress.written_at)} {instance:<{INSTANCE_WIDTH}} "
        f"bloco {_index_over_total(progress.block_index, progress.block_count)}  "
        f"run {_index_over_total(progress.run_index, progress.run_count)}  "
        f"{progress.scenario_id:<{SCENARIO_WIDTH}}"
        f"{_index_over_total(progress.runs_total, runs_total)} runs, "
        f"{progress.runs_failed} falhas, {_elapsed(progress.elapsed_seconds)}"
    )


def judge_progress_line(progress: JudgeProgress | None) -> str:
    """A linha do Juiz, só do objeto: o plano inteiro foi para ele, e o total é dele."""
    if progress is None:
        return (
            f"{'':{HOUR_WIDTH}} {JUDGE_STEM:<{INSTANCE_WIDTH}} "
            f"sem progresso ainda, nenhum output julgado"
        )

    return (
        f"{hour_of(progress.written_at)} {JUDGE_STEM:<{INSTANCE_WIDTH}} "
        f"output {_index_over_total(progress.output_index, progress.output_count)}  "
        f"{progress.scenario_id:<{SCENARIO_WIDTH}}"
        f"{_index_over_total(progress.runs_total, progress.output_count)} runs, "
        f"{progress.runs_failed} falhas, {_elapsed(progress.elapsed_seconds)}"
    )


def hour_of(timestamp: str) -> str:
    """A hora no relógio da própria Instância, que é o offset que ela escreveu."""
    return datetime.fromisoformat(timestamp).strftime("%H:%M:%S")


def _checked[T](record: type[T], payload: Any, what: str, instance_id: str) -> T | None:
    """Os campos do registro, conferidos um a um, e só então a identidade."""
    if not isinstance(payload, Mapping):
        raise StatusError(f"{what}: não é um objeto JSON: {type(payload).__name__}")

    try:
        values = check_fields(record, payload, _CHECKS)
    except FieldError as error:
        raise StatusError(str(error)) from error

    if payload["instance_id"] != instance_id:
        return None
    return record(**values)


def _index_over_total(index: int, total: int) -> str:
    """`i/n` com o `i` na largura do `n`."""
    return f"{index:>{len(str(total))}}/{total}"


def _elapsed(seconds: int) -> str:
    hours, rest = divmod(seconds, SECONDS_PER_HOUR)
    minutes = rest // SECONDS_PER_MINUTE
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


_CHECKS: dict[str, Callable[[str, Any], None]] = {
    "instance_id": check_non_empty_str,
    "finished_at": check_aware_timestamp,
    "written_at": check_aware_timestamp,
    "scenario_id": check_non_empty_str,
    "block_index": check_int,
    "block_count": check_int,
    "run_index": check_int,
    "run_count": check_int,
    "run_id": check_non_empty_str,
    "output_index": check_int,
    "output_count": check_int,
    "runs_total": check_int,
    "runs_failed": check_int,
    "elapsed_seconds": check_int,
    "capped": check_bool,
    "exit_status": check_int,
}
