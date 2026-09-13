"""Os dois objetos de `status/` que o `run_all.sh` escreve, lidos pelo Orquestrador.

O contrato, campo a campo, está no `encode/README.md`; o que este papel decide
sobre ele, no `orchestrator/README.md`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

INSTANCE_WIDTH = 4
HOUR_WIDTH = 8

LONGEST_SCENARIO_ID = "libsvtav1_2160p_2160p_bbb_c7g_warmup"
SCENARIO_WIDTH = len(LONGEST_SCENARIO_ID) + 1

SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60


class StatusError(Exception):
    """Objeto de `status/` que o Orquestrador recusa a ler, com o campo ofensor nomeado."""


@dataclass(frozen=True)
class DoneMarker:
    """`status/{instance_type}_done`: o resultado do `run_all.sh` daquela fatia."""

    instance_id: str
    finished_at: str
    runs_total: int
    runs_failed: int
    capped: bool
    exit_status: int


@dataclass(frozen=True)
class Progress:
    """`status/{instance_type}_progress`: onde a Instância estava no último run."""

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


def check_done_marker(payload: Any, *, instance_id: str) -> DoneMarker | None:
    """O marcador, ou `None` quando ele é o da tentativa anterior."""
    return _checked(DoneMarker, payload, "marcador", instance_id)


def check_progress(payload: Any, *, instance_id: str) -> Progress | None:
    """O progresso, ou `None` quando ele é o da tentativa anterior — a ignorar."""
    return _checked(Progress, payload, "progresso", instance_id)


def progress_line(progress: Progress | None, *, instance: str, runs_total: int) -> str:
    """A linha daquela arquitetura, do objeto mais o total de runs da fatia."""
    if progress is None:
        return (
            f"{'':{HOUR_WIDTH}} {instance:<{INSTANCE_WIDTH}} "
            f"sem progresso ainda, 0/{runs_total} runs reportados"
        )

    return (
        f"{_hour(progress.written_at)} {instance:<{INSTANCE_WIDTH}} "
        f"bloco {_index_over_total(progress.block_index, progress.block_count)}  "
        f"run {_index_over_total(progress.run_index, progress.run_count)}  "
        f"{progress.scenario_id:<{SCENARIO_WIDTH}}"
        f"{_index_over_total(progress.runs_total, runs_total)} runs, "
        f"{progress.runs_failed} falhas, {_elapsed(progress.elapsed_seconds)}"
    )


def _checked[T](record: type[T], payload: Any, what: str, instance_id: str) -> T | None:
    """Os campos do registro, conferidos um a um, e só então a identidade."""
    if not isinstance(payload, Mapping):
        raise StatusError(f"{what}: não é um objeto JSON: {type(payload).__name__}")

    for field in fields(record):
        if field.name not in payload:
            raise StatusError(f"{field.name}: campo obrigatório ausente")
        _CHECKS[field.name](field.name, payload[field.name])

    if payload["instance_id"] != instance_id:
        return None
    return record(**{field.name: payload[field.name] for field in fields(record)})


def _index_over_total(index: int, total: int) -> str:
    """`i/n` com o `i` na largura do `n`."""
    return f"{index:>{len(str(total))}}/{total}"


def _hour(written_at: str) -> str:
    """A hora no relógio da própria Instância, que é o offset que ela escreveu."""
    return datetime.fromisoformat(written_at).strftime("%H:%M:%S")


def _elapsed(seconds: int) -> str:
    hours, rest = divmod(seconds, SECONDS_PER_HOUR)
    minutes = rest // SECONDS_PER_MINUTE
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _check_non_empty_str(field: str, value: Any) -> None:
    if type(value) is not str or not value:
        raise StatusError(f"{field}: esperava str não-vazia, veio {value!r}")


def _check_bool(field: str, value: Any) -> None:
    # Tipo exato: o que se barra é o `"false"` que um `--arg` no lugar de um
    # `--argjson` faria o `jq` do `run_all.sh` escrever.
    if type(value) is not bool:
        raise StatusError(f"{field}: esperava booleano JSON, veio {value!r}")


def _check_int(field: str, value: Any) -> None:
    # E ao contrário: `isinstance(True, int)` é verdadeiro, e um
    # `"exit_status": true` passaria como "saiu com 1".
    if type(value) is not int:
        raise StatusError(f"{field}: esperava inteiro, veio {value!r}")


def _check_aware_timestamp(field: str, value: Any) -> None:
    if type(value) is not str:
        raise StatusError(f"{field}: esperava timestamp ISO-8601 como string, veio {value!r}")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise StatusError(f"{field}: timestamp ISO-8601 não parseável: {value!r}") from error

    if parsed.utcoffset() is None:
        raise StatusError(f"{field}: timestamp sem offset de fuso: {value!r}")


_CHECKS: dict[str, Callable[[str, Any], None]] = {
    "instance_id": _check_non_empty_str,
    "finished_at": _check_aware_timestamp,
    "written_at": _check_aware_timestamp,
    "scenario_id": _check_non_empty_str,
    "block_index": _check_int,
    "block_count": _check_int,
    "run_index": _check_int,
    "run_count": _check_int,
    "runs_total": _check_int,
    "runs_failed": _check_int,
    "elapsed_seconds": _check_int,
    "capped": _check_bool,
    "exit_status": _check_int,
}
