# O `conftest.py` no nível do papel é o que torna o núcleo importável pelos testes
# sem `pyproject.toml` e sem `sys.path` manipulado: o pytest insere no `sys.path`
# o diretório de cada `conftest.py` que coleta.

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROLE_ROOT = Path(__file__).resolve().parent

META_SCHEMA_PATH = ROLE_ROOT / "meta.schema.json"

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


def make_meta(**overrides: Any) -> dict[str, Any]:
    """Um `meta.json` válido como dict, com overrides por campo de topo."""
    meta = copy.deepcopy(_VALID)
    for field, value in overrides.items():
        if value is _ABSENT:
            meta.pop(field, None)
        else:
            meta[field] = value
    return meta


def make_meta_json(**overrides: Any) -> str:
    """O mesmo, já serializado — é sobre os **bytes crus** que o modelo roda."""
    return json.dumps(make_meta(**overrides), indent=2) + "\n"


class _Absent:
    def __repr__(self) -> str:  # pragma: no cover - só aparece em falha de teste
        return "<absent>"


# `make_meta(warmup=ABSENT)` remove o campo em vez de escrevê-lo como `null`:
# "ausente" e "nulo" são modos de falha distintos.
_ABSENT = _Absent()
ABSENT: Any = _ABSENT
