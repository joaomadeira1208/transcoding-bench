"""Construção do plano dos Masters — função pura, sem I/O (ADR-0019).

Cada Master sai daqui com o nome pelo qual a preparação o materializa e a Execução
o abre, e com as propriedades que o `ffprobe` da preparação tem de encontrar nele.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from experiment_config import (
    ConfigError,
    ExperimentConfig,
    Geometry,
    PairRecord,
    VideoRecord,
)

SCHEMA_VERSION = "1"

MASTER_EXTENSION = ".mkv"

REMUXED_TIER = "2160p"

REMUXED_CODEC = "h264"
DERIVED_CODEC = "ffv1"


def master_name(video: str, tier: str) -> str:
    return f"{video}_{tier}{MASTER_EXTENSION}"


def build_masters_plan(config: ExperimentConfig) -> dict[str, Any]:
    """Projeta a configuração validada no plano dos Masters, um bloco por vídeo."""
    tiers = _derived_tiers(config.pairs)
    pix_fmt = config.encode.pix_fmt
    return {
        "schema_version": SCHEMA_VERSION,
        "videos": [_video(video, tiers, pix_fmt) for video in config.videos],
    }


def _derived_tiers(pairs: Sequence[PairRecord]) -> tuple[str, ...]:
    """Os tiers que algum par consome como input, sem o 4K, na ordem do TOML.

    Pela `input_res`, e não pela tabela de geometria: o 480p tem geometria em
    todo vídeo e é só saída, então derivá-lo produziria um sétimo Master que
    Execução nenhuma abre (ADR-0023).
    """
    return tuple(dict.fromkeys(pair.input_res for pair in pairs if pair.input_res != REMUXED_TIER))


def _video(video: VideoRecord, tiers: tuple[str, ...], pix_fmt: str) -> dict[str, Any]:
    return {
        "video": video.slug,
        "source": _source(video),
        "master": _master(video, REMUXED_TIER, REMUXED_CODEC, pix_fmt),
        "derived": [_master(video, tier, DERIVED_CODEC, pix_fmt) for tier in tiers],
    }


def _source(video: VideoRecord) -> dict[str, Any]:
    source = video.source
    return {
        "url": source.url,
        "file": source.file,
        "size": source.size,
        "sha256": source.sha256,
    }


def _master(video: VideoRecord, tier: str, codec_name: str, pix_fmt: str) -> dict[str, Any]:
    geometry = _geometry(video, tier)
    return {
        "name": master_name(video.slug, tier),
        "video": video.slug,
        "tier": tier,
        "width": geometry.width,
        "height": geometry.height,
        "codec_name": codec_name,
        "pix_fmt": pix_fmt,
        "frame_rate": video.frame_rate,
        "frames": video.frames,
    }


def _geometry(video: VideoRecord, tier: str) -> Geometry:
    """A geometria do tier, exigida aqui porque a validação da spec não a exige.

    Lá o requisito é ter geometria para os tiers que algum par referencia, e o
    piloto não referencia o 2160p: sem esta guarda, o 4K sairia sem dimensão e a
    preparação compararia o `ffprobe` com vazio.
    """
    if tier not in video.geometry:
        raise ConfigError(
            f"video '{video.slug}': missing geometry for tier '{tier}', "
            "which every video needs as its 4K master"
        )
    return video.geometry[tier]
