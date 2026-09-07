# Mudar o CRF do x265 no `experiment.toml` e esquecer o `pilot.toml` não estoura em
# lugar nenhum: o piloto aprova flags que a campanha não vai usar, e a divergência
# só aparece na campanha (ADR-0019/0022).

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import fields
from typing import Any

import pytest
from conftest import (
    make_codec,
    make_encode,
    make_experiment,
    make_geometry,
    make_instrumentation,
    make_video,
    real_config,
    real_pilot_config,
)
from experiment_config import ExperimentConfig, PairRecord, validate_config
from scenario_plan import build_canonical_plan

# O `480p` só é usado pela campanha: apagá-lo daqui deixa de exercitar a
# geometria que o piloto não referencia, que é metade de "a regra é o registro
# inteiro".
FIXTURE_GEOMETRY = {"1080p": (1920, 1080), "720p": (1280, 720), "480p": (854, 480)}

# As famílias com regra própria; o resto do `ExperimentConfig` são os escalares do
# `[experiment]`, e sai de `fields()` pelo mesmo motivo que os campos de um
# registro saem.
_FAMILIES_WITH_THEIR_OWN_RULE = frozenset(
    {"encode", "instrumentation", "codecs", "pairs", "videos", "instances"}
)


def subset_divergences(pilot: ExperimentConfig, campaign: ExperimentConfig) -> list[str]:
    """Onde o piloto deixa de ser a campanha em escopo menor, uma mensagem por campo."""
    return [
        *_experiment_divergences(pilot, campaign),
        *_field_divergences("encode", None, pilot.encode, campaign.encode),
        *_field_divergences(
            "instrumentation", None, pilot.instrumentation, campaign.instrumentation
        ),
        *_record_divergences("codec", "slug", pilot.codecs, campaign.codecs),
        *_record_divergences("video", "slug", pilot.videos, campaign.videos),
        *_record_divergences("instance", "id", pilot.instances, campaign.instances),
        *_pair_divergences(pilot.pairs, campaign.pairs),
    ]


def run_objects(config: ExperimentConfig) -> set[str]:
    """Os objetos de run do canônico, congelados em JSON de chaves ordenadas.

    A comparação é sobre o objeto inteiro, e não sobre uma lista de parâmetros:
    um campo que entre no run numa spec futura entra na guarda sozinho.
    """
    plan = build_canonical_plan(config)
    return {json.dumps(run, sort_keys=True) for block in plan["blocks"] for run in block["runs"]}


def _experiment_divergences(pilot: ExperimentConfig, campaign: ExperimentConfig) -> Iterator[str]:
    for field in fields(pilot):
        if field.name in _FAMILIES_WITH_THEIR_OWN_RULE:
            continue
        mine, theirs = getattr(pilot, field.name), getattr(campaign, field.name)
        if mine != theirs:
            yield _divergence("experiment", None, field.name, mine, theirs)


def _record_divergences(
    family: str, identity: str, pilot_records: Sequence[Any], campaign_records: Sequence[Any]
) -> Iterator[str]:
    homonyms = {getattr(record, identity): record for record in campaign_records}
    for record in pilot_records:
        name = getattr(record, identity)
        if name not in homonyms:
            yield _absence(family, name)
        else:
            yield from _field_divergences(family, name, record, homonyms[name])


def _pair_divergences(
    pilot_pairs: Sequence[PairRecord], campaign_pairs: Sequence[PairRecord]
) -> Iterator[str]:
    declared = set(campaign_pairs)
    for pair in pilot_pairs:
        if pair not in declared:
            yield _absence("pair", str(pair))


def _field_divergences(
    family: str, identity: str | None, pilot_record: Any, campaign_record: Any
) -> Iterator[str]:
    # Os campos saem do próprio dataclass: enumerá-los aqui deixaria um campo novo
    # do `CodecRecord` fora da guarda sem que nada falhasse.
    for field in fields(pilot_record):
        mine = getattr(pilot_record, field.name)
        theirs = getattr(campaign_record, field.name)
        if isinstance(mine, Mapping) and isinstance(theirs, Mapping):
            yield from _table_divergences(family, identity, field.name, mine, theirs)
        elif mine != theirs:
            yield _divergence(family, identity, field.name, mine, theirs)


def _table_divergences(
    family: str,
    identity: str | None,
    field: str,
    pilot_table: Mapping[str, Any],
    campaign_table: Mapping[str, Any],
) -> Iterator[str]:
    for key in sorted(set(pilot_table) | set(campaign_table)):
        mine, theirs = pilot_table.get(key), campaign_table.get(key)
        if mine != theirs:
            yield _divergence(family, identity, f"{field}['{key}']", mine, theirs)


def _divergence(
    family: str, identity: str | None, field: str, pilot_value: Any, campaign_value: Any
) -> str:
    return (
        f"{_where(family, identity)}: '{field}' differs "
        f"(pilot {pilot_value!r}, campaign {campaign_value!r})"
    )


def _absence(family: str, identity: str) -> str:
    return f"{_where(family, identity)}: not declared by the campaign"


def _where(family: str, identity: str | None) -> str:
    return f"{family} '{identity}'" if identity is not None else family


@pytest.fixture
def make_subset(make_raw_config):
    """Uma campanha e um piloto subconjunto dela, com overrides no lado do piloto."""

    def _make(**pilot_overrides) -> tuple[ExperimentConfig, ExperimentConfig]:
        campaign = make_raw_config(
            pair=[
                {"input_res": "1080p", "output_res": "1080p"},
                {"input_res": "1080p", "output_res": "720p"},
                {"input_res": "1080p", "output_res": "480p"},
            ],
            video=[make_video(geometry=make_geometry(**FIXTURE_GEOMETRY))],
        )
        pilot = {
            **campaign,
            "pair": [{"input_res": "1080p", "output_res": "720p"}],
            **pilot_overrides,
        }
        return validate_config(pilot), validate_config(campaign)

    return _make


def only_divergence(pilot: ExperimentConfig, campaign: ExperimentConfig) -> str:
    divergences = subset_divergences(pilot, campaign)

    assert len(divergences) == 1, divergences
    return divergences[0]


class TestRealConfigs:
    """Âncora dos dois arquivos: é este teste que quebra no CI quando um deles é editado sozinho."""

    def test_the_pilot_diverges_from_the_campaign_in_nothing(self):
        assert subset_divergences(real_pilot_config(), real_config()) == []

    def test_every_pilot_run_exists_in_the_campaign_plan(self):
        orphans = run_objects(real_pilot_config()) - run_objects(real_config())

        assert orphans == set()


class TestConfigLevelRejects:
    def test_codec_with_another_crf(self, make_subset):
        message = only_divergence(*make_subset(codec=[make_codec(crf=30)]))

        assert "codec" in message
        assert "libx264" in message
        assert "'crf'" in message

    def test_geometry_of_a_tier_the_pilot_uses(self, make_subset):
        pilot, campaign = make_subset(
            video=[make_video(geometry=make_geometry(**{**FIXTURE_GEOMETRY, "720p": (1278, 720)}))]
        )

        message = only_divergence(pilot, campaign)

        assert "video" in message
        assert "bbb" in message
        assert "geometry" in message and "720p" in message

    def test_geometry_of_a_tier_the_pilot_does_not_use(self, make_subset):
        pilot, campaign = make_subset(
            video=[make_video(geometry=make_geometry(**{**FIXTURE_GEOMETRY, "480p": (852, 480)}))]
        )

        message = only_divergence(pilot, campaign)

        assert "video" in message
        assert "bbb" in message
        assert "geometry" in message and "480p" in message

    def test_codec_without_a_homonym_in_the_campaign(self, make_subset):
        message = only_divergence(*make_subset(codec=[make_codec(slug="libaom")]))

        assert "codec" in message
        assert "libaom" in message

    def test_another_fixed_encode_parameter(self, make_subset):
        message = only_divergence(*make_subset(encode=make_encode(gop_size=60)))

        assert "encode" in message
        assert "'gop_size'" in message

    def test_another_pmu_event_list(self, make_subset):
        pilot, campaign = make_subset(
            instrumentation=make_instrumentation(pmu_events=["cycles", "cache-misses"])
        )

        message = only_divergence(pilot, campaign)

        assert "instrumentation" in message
        assert "'pmu_events'" in message

    def test_pair_the_campaign_does_not_declare(self, make_subset):
        message = only_divergence(*make_subset(pair=[{"input_res": "720p", "output_res": "720p"}]))

        assert "pair" in message
        assert "720p -> 720p" in message

    def test_another_replication_count(self, make_subset):
        message = only_divergence(*make_subset(experiment=make_experiment(replications=3)))

        assert "experiment" in message
        assert "'replications'" in message

    def test_another_seed(self, make_subset):
        message = only_divergence(*make_subset(experiment=make_experiment(seed=2)))

        assert "experiment" in message
        assert "'seed'" in message


class TestPlanLevelRejects:
    def test_another_seed(self, make_subset):
        pilot, campaign = make_subset(experiment=make_experiment(seed=2))

        assert run_objects(pilot) - run_objects(campaign)
