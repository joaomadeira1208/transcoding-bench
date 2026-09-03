# O `conftest.py` no nível do papel é o que torna o núcleo importável pelos testes
# sem `pyproject.toml` e sem `sys.path` manipulado: o pytest insere no `sys.path`
# o diretório de cada `conftest.py` que coleta.

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from experiment_config import ExperimentConfig, validate_config

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_EXPERIMENT_TOML = REPO_ROOT / "config" / "experiment.toml"


def real_config() -> ExperimentConfig:
    """A spec real do Experimento, validada — âncora dos testes que a citam."""
    with REAL_EXPERIMENT_TOML.open("rb") as handle:
        return validate_config(tomllib.load(handle))


# Os testes de rejeição sobrescrevem uma família de cada vez, de modo que a falha
# asserida seja a única diferença em relação a um arquivo que passa.
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
            "bitstream_muxer": "h264",
        }
    ],
    "instrumentation": {"pmu_events": ["cycles", "instructions"]},
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


# Deliberadamente duplicado em relação ao do `analysis/` (ADR-0022). O checador
# daqui só olha cinco campos, mas a factory carrega o arquivo inteiro: um
# `meta.json` de teste que só tivesse os campos checados não seria um `meta.json`.
_VALID_META: dict[str, Any] = {
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
    meta = copy.deepcopy(_VALID_META)
    for field, value in overrides.items():
        if value is _ABSENT:
            meta.pop(field, None)
        else:
            meta[field] = value
    return meta


def make_meta_json(**overrides: Any) -> str:
    """O mesmo, já serializado — o checador recebe os bytes que o bash escreveu."""
    return json.dumps(make_meta(**overrides), indent=2) + "\n"


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


def make_instrumentation(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["instrumentation"]), **overrides}


@pytest.fixture
def make_raw_config():
    """Uma factory de configuração já parseada, com overrides por chave de topo."""

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


# `make_raw_config(encode=ABSENT)` remove a chave em vez de escrevê-la como
# `None`: "ausente" e "nula" são modos de falha distintos.
_ABSENT = _Absent()
ABSENT: Any = _ABSENT
