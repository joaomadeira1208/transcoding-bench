# conftest.py no nível do papel: é ele que torna o núcleo do orquestrador
# importável pelos testes de `tests/`. O pytest insere no `sys.path` o diretório
# de cada `conftest.py` que coleta (import mode `prepend`), então `import
# experiment_config` funciona sem `pyproject.toml`, sem `pip install -e` e sem
# `sys.path` manipulado nos módulos de teste — as três coisas que as ADR-0017 e
# 0022 rejeitaram (decisão D9 da spec).
#
# Também é a casa das factories de teste (ADR-0022): fixture/factory compartilhada
# dentro de um papel, tudo bem; helper de asserção compartilhado, não.

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_EXPERIMENT_TOML = REPO_ROOT / "config" / "experiment.toml"


# Configuração mínima e válida, na forma que o `tomllib` devolve — não a matriz
# real. Os testes de rejeição sobrescrevem uma família de cada vez, de modo que a
# falha asserida seja a única diferença em relação a um arquivo que passa.
_MINIMAL: dict[str, Any] = {
    "experiment": {"seed": 1, "replications": 5, "warmup_runs": 1},
    "encode": {
        "threads": 0,
        "gop_size": 48,
        "pix_fmt": "yuv420p",
        "strip_audio": True,
        "container": "mkv",
        "scale_flags": "lanczos",
    },
    "codec": [
        {
            "slug": "libx264",
            "codec": "h264",
            "encoder": "libx264",
            "preset": "medium",
            "crf": 23,
            "encoder_args": ["-sc_threshold", "0"],
        }
    ],
    "pair": [
        {"input_res": "1080p", "output_res": "1080p"},
        {"input_res": "1080p", "output_res": "720p"},
    ],
    "video": [
        {
            "slug": "bbb",
            "title": "Big Buck Bunny",
            "geometry": {
                "1080p": {"width": 1920, "height": 1080},
                "720p": {"width": 1280, "height": 720},
            },
        }
    ],
    "instance": [{"id": "c7g", "instance_type": "c7g.xlarge", "arch": "arm64"}],
}


def make_geometry(**tiers: tuple[int, int]) -> dict[str, dict[str, int]]:
    """`make_geometry(**{"1080p": (1920, 1080)})` → tabela de geometria por tier."""
    return {tier: {"width": w, "height": h} for tier, (w, h) in tiers.items()}


def make_codec(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["codec"][0]), **overrides}


def make_video(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["video"][0]), **overrides}


def make_instance(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["instance"][0]), **overrides}


def make_encode(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["encode"]), **overrides}


@pytest.fixture
def make_raw_config():
    """Devolve uma factory de configuração já parseada, com overrides por chave de topo."""

    def _make(**overrides: Any) -> dict[str, Any]:
        raw = copy.deepcopy(_MINIMAL)
        for key, value in overrides.items():
            if value is _ABSENT:
                raw.pop(key, None)
            else:
                raw[key] = value
        return raw

    return _make


class _Absent:
    def __repr__(self) -> str:  # pragma: no cover - só aparece em falha de teste
        return "<absent>"


# Sentinela: `make_raw_config(encode=ABSENT)` remove a chave em vez de escrevê-la
# como `None`, porque "chave ausente" e "chave nula" são modos de falha distintos.
_ABSENT = _Absent()
ABSENT: Any = _ABSENT
