# O `conftest.py` no nível do papel é o que torna o núcleo importável pelos testes
# sem `pyproject.toml` e sem `sys.path` manipulado: o pytest insere no `sys.path`
# o diretório de cada `conftest.py` que coleta.

from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from command_output import DescribedInstance
from experiment_config import ExperimentConfig, validate_config
from masters_plan import build_masters_plan, iter_masters

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_EXPERIMENT_TOML = REPO_ROOT / "config" / "experiment.toml"
REAL_PILOT_TOML = REPO_ROOT / "config" / "pilot.toml"


def real_config() -> ExperimentConfig:
    """A spec real do Experimento, validada — âncora dos testes que a citam."""
    return _real_config(REAL_EXPERIMENT_TOML)


def real_pilot_config() -> ExperimentConfig:
    """A spec real do piloto (ADR-0022), pelo mesmo validador da campanha."""
    return _real_config(REAL_PILOT_TOML)


def _real_config(path: Path) -> ExperimentConfig:
    with path.open("rb") as handle:
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
            "frame_rate": "30/1",
            "frames": 19036,
            "geometry": {
                "1080p": {"width": 1920, "height": 1080},
                "720p": {"width": 1280, "height": 720},
            },
            "source": {
                "url": (
                    "https://download.blender.org/demo/movies/BBB/"
                    "bbb_sunflower_2160p_30fps_normal.mp4.zip"
                ),
                "file": "bbb_sunflower_2160p_30fps_normal.mp4",
                "size": 633016449,
                "sha256": "37f0ff251a606c2dcfa26c19fe6bf843234b4e7a8889cfab50bc26f644e55520",
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


# O manifesto da preparação, derivado do `experiment.toml` real pelo mesmo plano
# que o script de preparação recebe: uma factory escrita à mão envelheceria em
# silêncio a cada mudança da spec, e o checador passaria a ser conferido contra
# ela em vez de contra a spec.

_OBSERVED_MASTER_SIZE = 1073741824


def make_manifest(**overrides: Any) -> dict[str, Any]:
    """Um `masters/manifest.json` válido como dict, com overrides por chave de topo."""
    plan = build_masters_plan(real_config())
    manifest = {
        "schema_version": "1",
        "versions": copy.deepcopy(_VALID_META["versions"]),
        "sources": {video["video"]: dict(video["source"]) for video in plan["videos"]},
        "masters": [_observed(master) for master in iter_masters(plan)],
    }
    return manifest | overrides


def manifest_master(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    """O Master de um nome dentro do manifesto, para o teste o deformar no lugar."""
    matches = [master for master in manifest["masters"] if master["name"] == name]

    assert len(matches) == 1, name
    return matches[0]


def _observed(master: dict[str, Any]) -> dict[str, Any]:
    """O que o `ffprobe` da preparação viu: o Master planejado mais tamanho e digest."""
    return {
        "name": master["name"],
        "size": _OBSERVED_MASTER_SIZE,
        "sha256": hashlib.sha256(master["name"].encode()).hexdigest(),
        **{field: value for field, value in master.items() if field != "name"},
    }


def make_geometry(**tiers: tuple[int, int]) -> dict[str, dict[str, int]]:
    """`make_geometry(**{"1080p": (1920, 1080)})` → tabela de geometria por tier."""
    return {tier: {"width": w, "height": h} for tier, (w, h) in tiers.items()}


def make_codec(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["codec"][0]), **overrides}


def make_video(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["video"][0]), **overrides}


def make_source(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["video"][0]["source"]), **overrides}


def make_instance(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["instance"][0]), **overrides}


def make_experiment(**overrides: Any) -> dict[str, Any]:
    return {**copy.deepcopy(_MINIMAL["experiment"]), **overrides}


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


# Payloads da AWS CLI v2 escritos à mão, no formato documentado, até o primeiro
# preflight capturar os reais (ADR-0022).


def make_launched_instance(instance_id: str = "i-0123456789abcdef0") -> dict[str, Any]:
    return {
        "AmiLaunchIndex": 0,
        "ImageId": "ami-0abcdef1234567890",
        "InstanceId": instance_id,
        "InstanceType": "c7g.xlarge",
        "KeyName": "transcoding-bench",
        "LaunchTime": "2026-09-07T12:00:00+00:00",
        "PrivateIpAddress": "10.0.1.42",
        "State": {"Code": 0, "Name": "pending"},
        "SubnetId": "subnet-0a1b2c3d4e5f60718",
    }


def make_run_instances_payload(*instances: dict[str, Any]) -> str:
    return json.dumps(
        {
            "Groups": [],
            "Instances": list(instances),
            "OwnerId": "123456789012",
            "ReservationId": "r-0a1b2c3d4e5f60718",
        }
    )


def make_described_instance(
    instance_id: str = "i-0123456789abcdef0",
    state: str = "running",
    public_ip: str | None = "54.210.1.2",
    private_ip: str | None = "10.0.1.42",
) -> dict[str, Any]:
    instance = {
        "ImageId": "ami-0abcdef1234567890",
        "InstanceId": instance_id,
        "InstanceType": "c7g.xlarge",
        "KeyName": "transcoding-bench",
        "LaunchTime": "2026-09-07T12:00:00+00:00",
        "State": {"Code": 16, "Name": state},
        "StateTransitionReason": "",
        "SubnetId": "subnet-0a1b2c3d4e5f60718",
    }
    if public_ip is not None:
        instance["PublicIpAddress"] = public_ip
    if private_ip is not None:
        instance["PrivateIpAddress"] = private_ip
    return instance


def make_describe_payload(*reservations: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "Reservations": [
                {
                    "Groups": [],
                    "Instances": instances,
                    "OwnerId": "123456789012",
                    "ReservationId": "r-0a1b2c3d4e5f60718",
                }
                for instances in reservations
            ]
        }
    )


def make_s3_object(key: str, size: int = 4096) -> dict[str, Any]:
    return {
        "Key": key,
        "LastModified": "2026-09-07T12:00:00+00:00",
        "ETag": '"9f0c4a2e6b414d5f8a372f1c8de0b7a4"',
        "Size": size,
        "StorageClass": "STANDARD",
    }


def make_list_objects_payload(*contents: dict[str, Any], **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "Contents": list(contents),
        "IsTruncated": False,
        "KeyCount": len(contents),
        "MaxKeys": 1000,
        "Name": "transcoding-bench-runs",
        "Prefix": "runs/",
    }
    return json.dumps(payload | overrides)


def make_instance_state(
    state: str = "running",
    public_ip: str | None = "54.210.1.2",
    private_ip: str | None = "10.0.1.42",
) -> DescribedInstance:
    """O estado já parseado, que é o que o predicado de prontidão recebe."""
    return DescribedInstance(
        instance_id="i-0123456789abcdef0",
        state=state,
        public_ip=public_ip,
        private_ip=private_ip,
    )
