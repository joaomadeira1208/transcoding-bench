"""Parser do log JSON do `libvmaf` (ADR-0005), ancorado na captura real (D24a).

Núcleo puro: recebe o texto já lido e devolve a série por frame. Quem abre
arquivo é o `quality.py`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from run_artifacts import ArtifactError

VMAF = "vmaf"
SSIM = "float_ssim"


@dataclass(frozen=True)
class VmafLog:
    """A série por frame que o `libvmaf` comparou."""

    vmaf: tuple[float, ...]
    ssim: tuple[float, ...]

    @property
    def frames(self) -> int:
        return len(self.vmaf)


def parse_vmaf_log(raw: str | bytes) -> VmafLog:
    """O VMAF e o SSIM de cada frame, ou `ArtifactError` nomeando o motivo.

    Um frame sem uma das duas métricas recusa o log inteiro: pulá-lo encolheria
    a série, e a contagem de frames é a guarda contra referência desalinhada.
    """
    if not raw.strip():
        raise ArtifactError("log vazio")
    try:
        log = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ArtifactError(f"log truncado ou ilegível: {error}") from error
    if not isinstance(log, Mapping):
        raise ArtifactError(f"o log não é um objeto JSON: {type(log).__name__}")

    frames = log.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ArtifactError(f"frames: o libvmaf não comparou frame nenhum (veio {frames!r:.40})")

    vmaf = tuple(_score(frame, index, VMAF) for index, frame in enumerate(frames))
    ssim = tuple(_score(frame, index, SSIM) for index, frame in enumerate(frames))
    return VmafLog(vmaf=vmaf, ssim=ssim)


def _score(frame: Any, index: int, metric: str) -> float:
    metrics = frame.get("metrics") if isinstance(frame, Mapping) else None
    value = metrics.get(metric) if isinstance(metrics, Mapping) else None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ArtifactError(f"frame {index}: {metric} ausente ou não numérico (veio {value!r})")
    return float(value)
