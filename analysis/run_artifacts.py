"""Parsers dos quatro artefatos de instrumentação de uma Execução (ADR-0006).

Núcleo puro: cada função recebe o texto já lido e devolve estrutura. Quem abre
arquivo é o `consolidate.py`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from run_meta import offending_fields


class ArtifactError(Exception):
    """Artefato presente cuja forma não é a que o parser conhece."""


class TimeMetrics(BaseModel):
    """O `time.json`, cujas chaves são o format string do `run_scenario.sh`."""

    model_config = ConfigDict(strict=True, extra="forbid")

    elapsed_s: float
    user_s: float
    sys_s: float
    max_rss_kb: int
    major_page_faults: int
    minor_page_faults: int
    fs_inputs: int
    fs_outputs: int
    voluntary_ctx_switches: int
    involuntary_ctx_switches: int
    exit_status: int


@dataclass(frozen=True)
class FfmpegStats:
    """Os parseados do `-stats` no stderr do FFmpeg (ADR-0006)."""

    frames: int | None
    fps: float | None
    bitrate_kbps: float | None


def parse_time(raw: str | bytes) -> TimeMetrics:
    """Valida os bytes crus de um `time.json` e devolve os agregados tipados."""
    try:
        return TimeMetrics.model_validate_json(raw)
    except ValidationError as error:
        raise ArtifactError("; ".join(offending_fields(error))) from error


def parse_perf(raw: str) -> dict[str, float | None]:
    """Os contadores de um `perf stat -j`, por evento.

    Evento indisponível sai como `None`, nunca como zero — que entraria na razão
    como medição.
    """
    counters: dict[str, float | None] = {}
    for line in raw.splitlines():
        record = _json_object(line)
        if record is None or "event" not in record or "counter-value" not in record:
            continue
        counters[str(record["event"])] = _optional_float(record["counter-value"])

    if not counters:
        raise ArtifactError("nenhum contador de perf stat -j")
    return counters


def parse_pidstat(raw: str) -> list[float]:
    """A série de `%CPU` a 1 Hz, na ordem das amostras.

    A coluna é achada pelo cabeçalho: com índice cravado, um flag a mais no
    `pidstat` faria ler `%MEM` como se fosse `%CPU`.
    """
    lines = raw.splitlines()
    header = next((index for index, line in enumerate(lines) if line.startswith("#")), None)
    if header is None:
        raise ArtifactError("pidstat sem linha de cabeçalho")

    columns = lines[header].lstrip("#").split()
    if CPU_COLUMN not in columns:
        raise ArtifactError(f"pidstat sem a coluna {CPU_COLUMN}")
    position = columns.index(CPU_COLUMN)

    return [
        sample
        for line in lines[header + 1 :]
        if (sample := _cpu_sample(line, position)) is not None
    ]


def parse_ffmpeg_log(raw: str) -> FfmpegStats:
    """Frames, fps e bitrate da **última** linha de progresso do FFmpeg.

    A última, porque o `-stats` reescreve a mesma linha com `\\r`: a primeira
    reportaria como total do encode o que ele tinha feito nos primeiros segundos.
    """
    progress = [chunk for chunk in re.split(r"[\r\n]", raw) if _FRAMES.search(chunk)]
    if not progress:
        raise ArtifactError("ffmpeg.log sem linha de -stats")

    last = progress[-1]
    return FfmpegStats(
        frames=_first_group(_FRAMES, last, int),
        fps=_first_group(_FPS, last, float),
        bitrate_kbps=_first_group(_BITRATE, last, float),
    )


CPU_COLUMN = "%CPU"

_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
_FRAMES = re.compile(r"frame=\s*([0-9]+)")
_FPS = re.compile(rf"fps=\s*{_NUMBER}")
_BITRATE = re.compile(rf"bitrate=\s*{_NUMBER}kbits/s")

# O `pidstat` fecha a saída com a média das amostras, e a linha tem a largura de
# uma amostra: sem descartá-la pelo rótulo, a média entra na série como medição.
_AVERAGE_PREFIX = "Average"


def _json_object(line: str) -> dict[str, Any] | None:
    """O objeto de uma linha, ou `None` para o que não é um — o cabeçalho do
    `perf stat -j`, que varia com a versão."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_group(pattern: re.Pattern[str], line: str, cast: Callable[[str], Any]) -> Any:
    """O valor do campo, ou `None` quando o FFmpeg não o computou (`bitrate=N/A`)."""
    match = pattern.search(line)
    return cast(match.group(1)) if match else None


def _cpu_sample(line: str, position: int) -> float | None:
    if not line or line.startswith("#") or line.startswith(_AVERAGE_PREFIX):
        return None
    fields = line.split()
    if len(fields) <= position:
        return None
    return _optional_float(fields[position])
