# O `conftest.py` no nível do papel é o que torna o núcleo importável pelos testes
# sem `pyproject.toml` e sem `sys.path` manipulado: o pytest insere no `sys.path`
# o diretório de cada `conftest.py` que coleta.

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROLE_ROOT = Path(__file__).resolve().parent

META_SCHEMA_PATH = ROLE_ROOT / "meta.schema.json"

CAPTURES = ROLE_ROOT / "tests" / "fixtures"

# Um encoder por captura: os três atravessam o mesmo `/usr/bin/time` e o mesmo
# `pidstat`, mas o `ffmpeg.log` do SVT-AV1 traz o stderr que só ele escreve.
ENCODERS = ("libx264", "libx265", "libsvtav1")

# 5 s a 24 fps: o clip que a camada de aceite gera dentro da imagem. Regenerar as
# capturas com outra duração muda o total que o `-stats` reporta.
CLIP_FRAMES = 120


def read_capture(encoder: str, artifact: str) -> str:
    """A saída crua que a ferramenta de verdade escreveu, capturada pela camada de
    aceite manual (ADR-0022). Âncora do que a factory abaixo só imita."""
    return (CAPTURES / f"{encoder}.{artifact}").read_text(encoding="utf-8")


# Âncora fraca de propósito: escrita em Python, pelo mesmo raciocínio que
# escreveu o modelo, ela valida Python contra Python. A âncora do contrato
# cross-language é um `meta.json` que o bash produziu, e vem do smoke.
_VALID: dict[str, Any] = {
    "schema_version": "1",
    "scenario_id": "libx264_2160p_1080p_bbb_c7g_rep1",
    "warmup": False,
    "seed": 20260808,
    "codec": "h264",
    "encoder": "libx264",
    "input_res": "2160p",
    "output_res": "1080p",
    "video": "bbb",
    "instance": "c7g",
    "master": "bbb_2160p.mkv",
    "output_width": 1920,
    "output_height": 1080,
    "preset": "medium",
    "crf": 23,
    "encoder_args": ["-sc_threshold", "0"],
    "threads": 0,
    "gop_size": 48,
    "pix_fmt": "yuv420p",
    "strip_audio": True,
    "container": "mkv",
    "scale_flags": "lanczos",
    "run_id": "9f0c4a2e-6b41-4d5f-8a37-2f1c8de0b7a4",
    "started_at": "2026-08-08T10:00:00+00:00",
    "finished_at": "2026-08-08T10:12:31+00:00",
    "exit_code": 0,
    "commit": "ffd4f43a1b2c3d4e5f60718293a4b5c6d7e8f900",
    "instance_id": "i-0123456789abcdef0",
    "instance_type": "c7g.xlarge",
    "versions": {
        "ffmpeg": "n7.1",
        "libx264": "31e19f92",
        "libx265": "4.1",
        "libsvtav1": "v2.3.0",
        "libvmaf": "v3.0.0",
    },
}


# O format string do `run_scenario.sh` do outro lado da fronteira: as chaves são o
# contrato, e os valores são os que o GNU time emite — segundos com decimal,
# contadores inteiros.
_VALID_TIME: dict[str, Any] = {
    "elapsed_s": 312.45,
    "user_s": 1180.22,
    "sys_s": 12.31,
    "max_rss_kb": 524288,
    "major_page_faults": 0,
    "minor_page_faults": 183422,
    "fs_inputs": 0,
    "fs_outputs": 81920,
    "voluntary_ctx_switches": 1204,
    "involuntary_ctx_switches": 9812,
    "exit_status": 0,
}

# Os dez eventos da ADR-0006, com valores que fazem os derivados sair redondos:
# IPC 1.5, cache miss rate 0.25, branch mispredict rate 0.02.
_VALID_COUNTERS: dict[str, Any] = {
    "cycles": 4_000_000_000,
    "instructions": 6_000_000_000,
    "cache-references": 200_000_000,
    "cache-misses": 50_000_000,
    "branch-instructions": 800_000_000,
    "branch-misses": 16_000_000,
    "task-clock": 312450.0,
    "context-switches": 1204,
    "cpu-migrations": 12,
    "page-faults": 183422,
}

_VALID_CPU_PCT = (98.0, 96.0, 94.0)


def make_meta(**overrides: Any) -> dict[str, Any]:
    """Um `meta.json` válido como dict, com overrides por campo de topo."""
    return _with_overrides(_VALID, overrides)


def make_meta_json(**overrides: Any) -> str:
    """O mesmo, já serializado — é sobre os **bytes crus** que o modelo roda."""
    return json.dumps(make_meta(**overrides), indent=2) + "\n"


def make_time_json(**overrides: Any) -> str:
    """Um `time.json`, como o `/usr/bin/time` o emite pelo seu format string."""
    return json.dumps(_with_overrides(_VALID_TIME, overrides)) + "\n"


def make_perf_json(counters: Mapping[str, Any] | None = None, header: str = "") -> str:
    """Um `perf.json`: um objeto por linha, o contador como string.

    `header` entra como primeira linha porque o cabeçalho do `perf stat -j` varia
    com a versão e o parser tem de atravessá-lo.
    """
    if counters is None:
        counters = _VALID_COUNTERS
    lines = [header] if header else []
    lines += [
        json.dumps(
            {
                "counter-value": value if isinstance(value, str) else f"{value:.6f}",
                "unit": "",
                "event": event,
                "event-runtime": 1000000,
                "pcnt-running": 100.00,
            }
        )
        for event, value in counters.items()
    ]
    return "\n".join(lines) + "\n"


def make_pidstat(cpu_pct: Sequence[float] = _VALID_CPU_PCT) -> str:
    """Uma `pidstat.txt`: o banner, o cabeçalho prefixado por `#` e uma amostra
    por segundo — texto delimitado por espaço, nunca CSV (ADR-0007)."""
    lines = [
        "Linux 6.8.0-31-generic (ip-10-0-0-1) \t08/08/2026 \t_aarch64_\t(4 CPU)",
        "",
        "#      Time        UID       PID    %usr %system  %guest   %wait    %CPU   CPU"
        "  minflt/s  majflt/s     VSZ     RSS   %MEM  Command",
    ]
    lines += [
        f" {1786_000_000 + second}          0      4242   92.00    6.00    0.00    0.00"
        f"  {pct:6.2f}     0    120.00      0.00 2314520  524288   1.50  ffmpeg"
        for second, pct in enumerate(cpu_pct)
    ]
    return "\n".join(lines) + "\n"


def make_ffmpeg_log(frames: int = 7200, fps: float = 23.4, bitrate: str = "4521.3") -> str:
    """Um `ffmpeg.log`: o banner, e as linhas de `-stats` separadas por `\r`.

    A última é a que conta, e é a única que traz `Lsize` em vez de `size`.
    """
    progress = "\r".join(
        (
            "frame=  240 fps= 24.0 q=28.0 size=    1024KiB time=00:00:10.00 "
            "bitrate=3900.0kbits/s speed=1.10x",
            f"frame={frames:5d} fps={fps:5.1f} q=-1.0 Lsize=  184320KiB time=00:05:00.00 "
            f"bitrate={bitrate}kbits/s speed=0.96x",
        )
    )
    return f"ffmpeg version n7.1 Copyright (c) 2000-2026 the FFmpeg developers\n{progress}\n"


def _with_overrides(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(base)
    for field, value in overrides.items():
        if value is _ABSENT:
            record.pop(field, None)
        else:
            record[field] = value
    return record


class _Absent:
    def __repr__(self) -> str:  # pragma: no cover - só aparece em falha de teste
        return "<absent>"


# `make_meta(warmup=ABSENT)` remove o campo em vez de escrevê-lo como `null`:
# "ausente" e "nulo" são modos de falha distintos.
_ABSENT = _Absent()
ABSENT: Any = _ABSENT
