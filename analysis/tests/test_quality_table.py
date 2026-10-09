# As duas tabelas do Pass são o que o artigo reporta sobre a premissa de
# iso-qualidade (ADR-0005), e cada regra aqui tem um modo de falha que não
# estoura: o VMAF de um bitstream atribuído à arquitetura errada, um output
# truncado dando um VMAF plausível, um grupo com um julgamento falho declarado
# equivalente pelo que sobrou, e a distância medida sobre um bitstream a menos.

from __future__ import annotations

import tomllib
from typing import Any

import pandas as pd
import pytest
from conftest import ROLE_ROOT, make_judgement_json, make_meta_json, make_vmaf_log
from judgement import load_judgement
from quality_table import (
    QualityDefinition,
    RawJudgement,
    quality_definition,
    quality_tables,
    summarize,
)
from run_meta import load_meta
from run_table import RawRun, consolidate_runs

ARM = "a" * 64
INTEL = "b" * 64
AMD = "c" * 64

DEFINITION = QualityDefinition(
    frames={"bbb": 3, "tos": 3},
    instances=("c7g", "c7i", "c7a"),
    vmaf_delta_max=0.5,
    ssim_delta_max=0.001,
)

_LOG: Any = object()


def judged(
    instance: str,
    sha256: str,
    *,
    sharers: tuple[str, ...] = (),
    vmaf: tuple[float, ...] = (90.0, 90.0, 90.0),
    ssim: tuple[float, ...] | None = None,
    log: str | None = _LOG,
    video: str = "tos",
    rep: int = 1,
    **overrides: object,
) -> RawJudgement:
    """O julgamento do bitstream que `instance` produziu, compartilhado com `sharers`."""
    scenario = f"libx265_1080p_720p_{video}"
    shared_by = [
        {
            "instance": each,
            "scenario_id": f"{scenario}_{each}_rep{rep}",
            "run_id": f"{each}-{video}-rep{rep}",
        }
        for each in (instance, *sharers)
    ]
    fields = {
        "run_id": f"{instance}-{video}-rep{rep}",
        "scenario_id": f"{scenario}_{instance}_rep{rep}",
        "video": video,
        "master": f"{video}_1080p.mkv",
        "instance": instance,
        "sha256": sha256,
        "shared_by": shared_by,
        **overrides,
    }
    return RawJudgement(
        judgement=load_judgement(make_judgement_json(**fields)),
        vmaf_log=make_vmaf_log(vmaf, ssim) if log is _LOG else log,
    )


def outputs(*judgements: RawJudgement) -> list[dict[str, Any]]:
    return quality_tables(judgements, DEFINITION).outputs.to_pylist()


def groups(*judgements: RawJudgement) -> list[dict[str, Any]]:
    return quality_tables(judgements, DEFINITION).groups.to_pylist()


def single_output(**overrides: Any) -> dict[str, Any]:
    (row,) = outputs(judged("c7i", INTEL, sharers=("c7a",), **overrides))
    return row


def the_group(*judgements: RawJudgement) -> dict[str, Any]:
    (row,) = groups(*judgements)
    return row


def agreeing_x86(**arm: Any) -> tuple[RawJudgement, RawJudgement]:
    """O padrão do piloto: o ARM à parte, Intel e AMD com o mesmo bitstream."""
    return judged("c7g", ARM, **arm), judged("c7i", INTEL, sharers=("c7a",))


class TestTheDefinition:
    def test_it_reads_what_the_reader_needs_from_the_toml(self):
        config = tomllib.loads((ROLE_ROOT.parent / "config" / "pilot.toml").read_text())

        assert quality_definition(config) == QualityDefinition(
            frames={"bbb": 19036, "tos": 17616},
            instances=("c7g", "c7i", "c7a"),
            vmaf_delta_max=0.5,
            ssim_delta_max=0.001,
        )


class TestMetrics:
    def test_the_four_metrics_of_the_adr_come_out_of_the_series(self):
        # Desvio populacional e p5 por interpolação linear: a série é o vídeo
        # inteiro, não uma amostra dele. Com o amostral o desvio daria 2,83.
        row = single_output(vmaf=(90.0, 94.0), ssim=(0.95, 0.97), frames=2)

        assert row["vmaf_mean"] == pytest.approx(92.0)
        assert row["vmaf_std"] == pytest.approx(2.0)
        assert row["vmaf_p5"] == pytest.approx(90.2)
        assert row["ssim_mean"] == pytest.approx(0.96)

    def test_a_single_frame_has_no_spread(self):
        row = single_output(vmaf=(91.0,), ssim=(0.99,))

        assert row["vmaf_std"] == 0.0
        assert row["vmaf_p5"] == 91.0

    def test_the_frame_count_is_the_one_the_log_compared(self):
        assert single_output(vmaf=(90.0,) * 5)["frames"] == 5


class TestTheFrameGuard:
    def test_the_count_of_the_definition_matches(self):
        assert single_output()["frames_match"] is True

    def test_a_count_other_than_the_definition_marks_the_output_invalid(self):
        # O `frames` do plano copiado no `judge.json` diz 17616, e não é contra ele
        # que se confere: é contra o vídeo da definição que o leitor recebeu.
        row = single_output(vmaf=(90.0,) * 4)

        assert row["frames"] == 4
        assert row["frames_match"] is False

    def test_the_mismatch_is_reported_with_both_counts(self):
        result = quality_tables([judged("c7i", INTEL, vmaf=(90.0,) * 4)], DEFINITION)

        assert result.invalid == ("c7i-tos-rep1/vmaf.json: 4 frames no log, 3 na definição",)

    def test_a_video_the_definition_does_not_declare_never_matches(self):
        assert single_output(video="sintel")["frames_match"] is False


class TestFailedJudgements:
    def test_a_failed_judgement_stays_with_its_exit_code_and_no_metrics(self):
        row = single_output(exit_code=1, log=None)

        assert row["exit_code"] == 1
        assert row["vmaf_mean"] is None
        assert row["frames"] is None
        assert row["frames_match"] is False

    def test_an_unreadable_log_of_a_successful_judgement_is_reported(self):
        result = quality_tables([judged("c7i", INTEL, log='{"frames": [')], DEFINITION)

        (row,) = result.outputs.to_pylist()
        assert row["vmaf_mean"] is None
        assert row["frames_match"] is False
        (message,) = result.invalid
        assert message.startswith("c7i-tos-rep1/vmaf.json: log truncado")

    def test_a_missing_log_of_a_successful_judgement_is_reported(self):
        result = quality_tables([judged("c7i", INTEL, log=None)], DEFINITION)

        assert result.invalid == ("c7i-tos-rep1/vmaf.json: ausente",)

    def test_a_failed_judgement_is_not_reported_twice(self):
        # O `exit_code` já o conta: o log torto de um julgamento falho é o estado
        # esperado, e relatá-lo afogaria o caso que importa.
        result = quality_tables([judged("c7i", INTEL, exit_code=1, log=None)], DEFINITION)

        assert result.invalid == ()


class TestTheOutputsTable:
    def test_one_row_per_run_id_ordered_by_scenario_id(self):
        rows = outputs(
            judged("c7i", INTEL, sharers=("c7a",), video="tos"),
            judged("c7g", ARM, video="tos"),
            judged("c7g", ARM, video="bbb"),
        )

        assert [row["scenario_id"] for row in rows] == [
            "libx265_1080p_720p_bbb_c7g_rep1",
            "libx265_1080p_720p_tos_c7g_rep1",
            "libx265_1080p_720p_tos_c7i_rep1",
        ]

    def test_the_latest_finished_at_wins_comparing_instants(self):
        # O segundo é uma hora **antes** do primeiro e ordena depois como string.
        earlier = judged("c7i", INTEL, finished_at="2026-09-15T01:00:00+03:00", vmaf=(80.0,) * 3)
        later = judged("c7i", INTEL, finished_at="2026-09-14T23:00:00+00:00")

        result = quality_tables([later, earlier], DEFINITION)

        assert [row["vmaf_mean"] for row in result.outputs.to_pylist()] == [90.0]
        assert result.duplicates == 1

    def test_the_row_carries_the_scenario_the_bitstream_the_judge_and_the_versions(self):
        row = single_output()

        assert {
            name: row[name]
            for name in (
                "scenario_id",
                "codec",
                "encoder",
                "input_res",
                "output_res",
                "video",
                "instance",
                "run_id",
                "sha256",
                "exit_code",
                "instance_id",
                "instance_type",
            )
        } == {
            "scenario_id": "libx265_1080p_720p_tos_c7i_rep1",
            "codec": "h265",
            "encoder": "libx265",
            "input_res": "1080p",
            "output_res": "720p",
            "video": "tos",
            "instance": "c7i",
            "run_id": "c7i-tos-rep1",
            "sha256": INTEL,
            "exit_code": 0,
            "instance_id": "i-0123456789abcdef0",
            "instance_type": "c7i.4xlarge",
        }
        assert dict(row["versions"])["libvmaf"] == "v3.0.0"

    def test_no_table_carries_who_shares_the_bitstream(self):
        # A atribuição a toda Execução é a junção por `output_sha256` (D19): uma
        # segunda fonte de verdade sobre quem compartilha o quê envelheceria.
        result = quality_tables(agreeing_x86(), DEFINITION)

        assert "shared_by" not in result.outputs.column_names
        assert "shared_by" not in result.groups.column_names


class TestTheGroupsTable:
    def test_one_row_per_scenario_ordered_by_name(self):
        rows = groups(
            *agreeing_x86(),
            judged("c7g", ARM, video="bbb"),
            judged("c7i", INTEL, sharers=("c7a",), video="bbb"),
        )

        assert [row["scenario"] for row in rows] == [
            "libx265_1080p_720p_bbb",
            "libx265_1080p_720p_tos",
        ]
        assert {row["video"] for row in rows} == {"bbb", "tos"}

    def test_it_counts_the_distinct_bitstreams_and_names_the_hash_of_each_architecture(self):
        row = the_group(*agreeing_x86())

        assert row["bitstreams"] == 2
        assert (row["sha256_c7g"], row["sha256_c7i"], row["sha256_c7a"]) == (
            [ARM],
            [INTEL],
            [INTEL],
        )

    def test_three_distinct_bitstreams_are_three(self):
        row = the_group(judged("c7g", ARM), judged("c7i", INTEL), judged("c7a", AMD))

        assert row["bitstreams"] == 3

    def test_a_divergent_cell_names_both_hashes_of_that_architecture(self):
        # D3: as Replicações de uma Instância não deram o mesmo bitstream, e a
        # coluna dela não pode escolher um dos dois em silêncio.
        row = the_group(
            judged("c7g", ARM, rep=1, cell_divergent=True),
            judged("c7g", AMD, rep=2, cell_divergent=True),
            judged("c7i", INTEL, sharers=("c7a",)),
        )

        assert row["bitstreams"] == 3
        assert row["sha256_c7g"] == [ARM, AMD]

    def test_the_distances_are_the_maximum_minus_the_minimum_of_the_means(self):
        row = the_group(
            judged("c7g", ARM, vmaf=(90.0,) * 3, ssim=(0.9500,) * 3),
            judged("c7i", INTEL, vmaf=(90.3,) * 3, ssim=(0.9504,) * 3),
            judged("c7a", AMD, vmaf=(89.9,) * 3, ssim=(0.9502,) * 3),
        )

        assert row["vmaf_delta"] == pytest.approx(0.4)
        assert row["ssim_delta"] == pytest.approx(0.0004)

    def test_a_single_bitstream_is_at_distance_zero_and_equivalent(self):
        row = the_group(judged("c7g", ARM, sharers=("c7i", "c7a")))

        assert row["bitstreams"] == 1
        assert (row["vmaf_delta"], row["ssim_delta"]) == (0.0, 0.0)
        assert row["equivalent"] is True


class TestTheVerdict:
    def test_both_distances_within_the_thresholds_is_equivalent(self):
        row = the_group(*agreeing_x86(vmaf=(90.5,) * 3, ssim=(0.901,) * 3))

        assert row["judged_ok"] is True
        assert row["equivalent"] is True

    def test_the_vmaf_distance_beyond_its_threshold_is_not_equivalent(self):
        row = the_group(*agreeing_x86(vmaf=(90.6,) * 3, ssim=(0.9,) * 3))

        assert row["equivalent"] is False

    def test_the_ssim_distance_beyond_its_threshold_is_not_equivalent(self):
        row = the_group(*agreeing_x86(vmaf=(90.0,) * 3, ssim=(0.902,) * 3))

        assert row["equivalent"] is False

    def test_a_failed_judgement_leaves_the_verdict_null_and_never_true(self):
        # Os dois bitstreams que sobraram estão a distância zero: medido sobre
        # eles, o grupo sairia equivalente sem um terço do que deveria comparar.
        row = the_group(
            judged("c7g", ARM, exit_code=1, log=None),
            judged("c7i", INTEL),
            judged("c7a", AMD),
        )

        assert row["judged_ok"] is False
        assert row["equivalent"] is None
        assert (row["vmaf_delta"], row["ssim_delta"]) == (None, None)

    def test_a_frame_mismatch_leaves_the_verdict_null(self):
        row = the_group(*agreeing_x86(vmaf=(90.0,) * 4))

        assert row["judged_ok"] is False
        assert row["equivalent"] is None

    def test_an_architecture_no_judgement_covers_leaves_the_verdict_null(self):
        # Um resultado que nunca chegou ao bucket não se anuncia: o grupo tem um
        # bitstream a menos, e só a coluna vazia da arquitetura o denuncia.
        row = the_group(judged("c7g", ARM), judged("c7i", INTEL))

        assert row["sha256_c7a"] is None
        assert row["judged_ok"] is False
        assert row["equivalent"] is None


class TestTheJoinWithTheMainTable:
    def test_the_vmaf_of_a_bitstream_reaches_every_replication_that_produced_it(self):
        runs = consolidate_runs(
            RawRun(
                meta=load_meta(
                    make_meta_json(
                        run_id=f"{instance}-rep{rep}",
                        scenario_id=f"libx265_1080p_720p_tos_{instance}_rep{rep}",
                        instance=instance,
                    )
                ),
                time=None,
                perf=None,
                pidstat=None,
                ffmpeg=None,
                sha256=f"{sha256}\n",
            )
            for instance, sha256 in (("c7g", ARM), ("c7i", INTEL), ("c7a", INTEL))
            for rep in range(1, 6)
        ).table.to_pandas()
        judgements = quality_tables(agreeing_x86(vmaf=(80.0,) * 3), DEFINITION).outputs

        attributed = runs.merge(
            judgements.to_pandas()[["sha256", "vmaf_mean"]],
            left_on="output_sha256",
            right_on="sha256",
            how="left",
        )

        assert len(attributed) == 15
        by_instance = attributed.groupby("instance").vmaf_mean.agg(["count", "min", "max"])
        assert by_instance.loc["c7g"].tolist() == [5, 80.0, 80.0]
        assert by_instance.loc["c7i"].tolist() == [5, 90.0, 90.0]
        assert by_instance.loc["c7a"].tolist() == [5, 90.0, 90.0]


class TestDeterminism:
    def test_the_same_judgements_in_any_order_give_the_same_tables(self):
        judgements = [
            *agreeing_x86(),
            judged("c7g", ARM, video="bbb"),
            judged("c7i", INTEL, video="bbb"),
            judged("c7a", AMD, video="bbb"),
        ]

        forward = quality_tables(judgements, DEFINITION)
        backward = quality_tables(reversed(judgements), DEFINITION)

        assert forward.outputs.equals(backward.outputs)
        assert forward.groups.equals(backward.groups)

    def test_the_groups_table_reads_back_into_pandas_with_null_verdicts(self):
        frame = quality_tables(
            [judged("c7g", ARM, exit_code=1, log=None), judged("c7i", INTEL, sharers=("c7a",))],
            DEFINITION,
        ).groups.to_pandas()

        assert pd.isna(frame.equivalent.iloc[0])


class TestSummary:
    def test_nothing_left_out_is_invisible(self):
        result = quality_tables(
            [
                *agreeing_x86(),
                judged("c7i", INTEL, sharers=("c7a",), finished_at="2026-09-14T09:00:00+00:00"),
                judged("c7g", AMD, video="bbb", exit_code=1, log=None),
                judged("c7i", INTEL, video="bbb", vmaf=(90.0,) * 4),
            ],
            DEFINITION,
        )

        assert summarize(result) == (
            "4 outputs, 2 grupos, 1 duplicata(s) fora, 1 com exit_code != 0, "
            "1 com o log inválido ou frames divergentes"
        )
