# As tabelas do relatório dos experimentos complementares (ADR-0026). Cada uma
# tem um modo de falha que dá número plausível: ciclos por quadro somados em vez
# de divididos pelos quadros do vídeo, o efeito do SMT invertido, a dispersão
# entre hosts calculada sobre Replicações em vez de hosts, e o hash de um braço
# comparado com o de outra arquitetura da campanha.

from __future__ import annotations

import pandas as pd
import pytest
from complementares import (
    bridge,
    campaign_instance,
    hash_agreement,
    host_dispersion,
    per_frame,
    smt_effect,
)

SCENARIO = {"encoder": "libx264", "input_res": "1080p", "output_res": "720p", "video": "bbb"}


def replications(instance: str, *, rows: int = 2, **fields: object) -> list[dict[str, object]]:
    base = {
        **SCENARIO,
        "instance": instance,
        "instance_type": f"{instance.split('-')[0]}.xlarge",
        "instance_id": f"i-{instance}",
        "perf_cycles": 2.0e12,
        "perf_instructions": 4.0e12,
        "perf_task_clock": 1.0e6,
        "ffmpeg_frames": 1000,
        "time_elapsed_s": 100.0,
        "output_sha256": "aaaa",
    }
    return [{**base, **fields} for _ in range(rows)]


def frame(*groups: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame([row for group in groups for row in group])


class TestTheAncestorInTheCampaign:
    @pytest.mark.parametrize(
        ("instance", "ancestor"),
        [("c7i-smt", "c7i"), ("c7i-nosmt", "c7i"), ("c7g-r2", "c7g"), ("c7g-pmu", "c7g")],
    )
    def test_it_is_the_id_before_the_hyphen(self, instance, ancestor):
        assert campaign_instance(instance) == ancestor


class TestPerFrame:
    def test_counts_are_divided_by_the_frames_of_the_run(self):
        df = frame(replications("c7i-smt", perf_cycles=3.0e9, ffmpeg_frames=1500, rows=1))

        assert per_frame(df, "perf_cycles").tolist() == [2.0e6]


class TestTheSmtEffect:
    def test_it_is_the_arm_with_smt_over_the_arm_without(self):
        # Dois threads por núcleo dividem o pipeline: cada thread anda mais devagar,
        # e o encode leva mais ciclos por quadro com SMT. A razão é smt/nosmt, e um
        # valor acima de 1 é o custo do SMT.
        df = frame(
            replications("c7i-smt", perf_cycles=3.0e12, perf_task_clock=1.5e6, time_elapsed_s=150.0),
            replications("c7i-nosmt", perf_cycles=2.0e12, time_elapsed_s=100.0),
        )

        effect = smt_effect(df, by=["encoder"])

        assert effect.loc["libx264", "cycles_per_frame"] == pytest.approx(1.5)
        assert effect.loc["libx264", "time_elapsed_s"] == pytest.approx(1.5)
        assert effect.loc["libx264", "ghz"] == pytest.approx(1.0)


class TestTheBridge:
    def test_the_arm_with_smt_is_compared_with_the_c7i_of_the_campaign(self):
        topologia = frame(replications("c7i-smt", perf_cycles=2.2e12))
        campaign = frame(
            replications("c7i", perf_cycles=2.0e12),
            replications("c7a", perf_cycles=9.0e12),
        )

        table = bridge(topologia, campaign)

        assert table.loc["libx264", "ratio"] == pytest.approx(1.1)


class TestTheDispersionBetweenHosts:
    def test_each_host_counts_once_however_many_replications_it_has(self):
        # Um host com dez Replicações e outro com duas: pela Replicação, o primeiro
        # pesaria cinco vezes mais na média do tipo.
        df = frame(
            replications("c7i-r1", time_elapsed_s=100.0, rows=10),
            replications("c7i-r2", time_elapsed_s=120.0, rows=2),
        )

        table = host_dispersion(df)

        row = table.loc[("c7i.xlarge", "libx264")]
        assert row["hosts"] == 2
        assert row["mean_s"] == pytest.approx(110.0)
        assert row["range"] == pytest.approx(20.0 / 110.0)

    def test_the_hosts_are_the_machines_and_not_the_ids(self):
        df = frame(
            replications("c7i", instance_id="i-campanha", time_elapsed_s=100.0),
            replications("c7i", instance_id="i-piloto", time_elapsed_s=104.0),
        )

        assert host_dispersion(df).loc[("c7i.xlarge", "libx264"), "hosts"] == 2


class TestTheHashesAgainstTheCampaign:
    def test_the_same_bitstream_of_the_ancestor_agrees(self):
        experiment = frame(replications("c7g-pmu", output_sha256="arm"))
        campaign = frame(
            replications("c7g", output_sha256="arm"), replications("c7i", output_sha256="x86")
        )

        (row,) = hash_agreement(experiment, campaign).itertuples()

        assert (row.instance, row.ancestor, row.same) == ("c7g-pmu", "c7g", True)

    def test_another_bitstream_is_reported(self):
        experiment = frame(replications("c7i-nosmt", output_sha256="outro"))
        campaign = frame(replications("c7i", output_sha256="x86"))

        (row,) = hash_agreement(experiment, campaign).itertuples()

        assert row.same is False

    def test_a_scenario_the_campaign_did_not_run_is_not_an_agreement(self):
        experiment = frame(replications("c7g-r1", video="tos"))
        campaign = frame(replications("c7g"))

        (row,) = hash_agreement(experiment, campaign).itertuples()

        assert row.same is False
