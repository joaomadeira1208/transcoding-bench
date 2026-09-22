# O triage decide o que o Juiz vai computar e o que a retenção vai apagar. Um
# grupo mal formado compara bitstreams de Cenários diferentes; um representante
# escolhido por sorte torna a limpeza irreproduzível; um `output.sha256` lido
# como ausente vira um bitstream a menos em silêncio. Por isso a regra da
# ADR-0025 é asserida sobre `meta.json` da factory do papel mais hashes
# atribuídos pelo teste, e não sobre a crença de que foi transcrita certo.

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import make_instance, make_meta, real_config, real_pilot_config
from experiment_config import validate_config
from quality_plan import (
    SCHEMA_VERSION,
    TriageError,
    build_plan,
    check_plan,
    render_report,
    triage,
)
from scenario_plan import build_canonical_plan, serialize_plan

FIRST_STARTED_AT = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)


def three_architectures() -> list[dict[str, Any]]:
    """Os três registros `[[instance]]` da definição real, na ordem dela."""
    return [
        make_instance(),
        make_instance(id="c7i", instance_type="c7i.xlarge", arch="x86_64"),
        make_instance(id="c7a", instance_type="c7a.xlarge", arch="x86_64"),
    ]


def plan_of(raw_config: dict[str, Any]) -> dict[str, Any]:
    return build_canonical_plan(validate_config(raw_config))


def group_of(run: Mapping[str, Any]) -> str:
    """O Cenário da ADR-0025: a `scenario_id` sem a arquitetura e sem o sufixo."""
    stem, _, _ = run["scenario_id"].rpartition("_")
    stem, _, _ = stem.rpartition("_")
    return stem


def sha_of(*parts: str) -> str:
    return hashlib.sha256("_".join(parts).encode()).hexdigest()


def per_cell(run: Mapping[str, Any]) -> str:
    """Um bitstream por célula: o caso em que as três arquiteturas divergem."""
    return sha_of(group_of(run), run["instance"])


def x86_together(run: Mapping[str, Any]) -> str:
    """O caso comum do piloto: Intel e AMD iguais, ARM à parte."""
    return sha_of(group_of(run), "c7g" if run["instance"] == "c7g" else "x86")


def all_equal(run: Mapping[str, Any]) -> str:
    """O grupo de bitstream único, que a ADR-0025 manda julgar do mesmo jeito."""
    return sha_of(group_of(run))


def executions(
    plan: Mapping[str, Any],
    digest: Callable[[Mapping[str, Any]], str | None] = per_cell,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Os `meta.json` de uma campanha inteira do plano e os hashes que o teste atribui.

    `digest` devolve o conteúdo do `output.sha256` de cada Execução, ou `None`
    para a que não tem o arquivo.
    """
    metas: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for index, run in enumerate(run for block in plan["blocks"] for run in block["runs"]):
        run_id = f"{index:08x}-6b41-4d5f-8a37-2f1c8de0b7a4"
        metas.append(
            make_meta(
                scenario_id=run["scenario_id"],
                warmup=run["warmup"],
                run_id=run_id,
                started_at=(FIRST_STARTED_AT + timedelta(seconds=index)).isoformat(),
            )
        )
        value = digest(run)
        if value is not None:
            hashes[run_id] = value
    return metas, hashes


def meta_of(metas: list[dict[str, Any]], scenario_id: str) -> dict[str, Any]:
    matches = [meta for meta in metas if meta["scenario_id"] == scenario_id]

    assert len(matches) == 1, scenario_id
    return matches[0]


def representatives(triaged) -> list[str]:
    return [bitstream.representative.scenario_id for bitstream in triaged.outputs]


@pytest.fixture
def one_scenario(make_raw_config):
    """Um Cenário só, rodado pelas três arquiteturas: um grupo, três células."""
    return plan_of(
        make_raw_config(
            instance=three_architectures(),
            pair=[{"input_res": "1080p", "output_res": "1080p"}],
        )
    )


class TestDistinctBitstreams:
    def test_a_hash_per_architecture_is_three_outputs(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, per_cell))

        assert len(triaged.groups) == 1
        assert len(triaged.outputs) == 3

    def test_the_two_x86_agreeing_is_two_outputs(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, x86_together))

        assert len(triaged.outputs) == 2

    def test_the_three_agreeing_is_a_single_output(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, all_equal))

        assert len(triaged.outputs) == 1

    def test_a_group_is_never_judged_zero_times(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, all_equal))

        assert all(group.bitstreams for group in triaged.groups)

    def test_the_sharers_of_a_bitstream_include_the_representative(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, all_equal))
        (bitstream,) = triaged.outputs

        assert bitstream.representative in bitstream.shared_by
        assert len(bitstream.shared_by) == 15

    def test_the_architecture_of_a_shared_bitstream_is_every_one_that_produced_it(
        self, one_scenario
    ):
        triaged = triage(one_scenario, *executions(one_scenario, x86_together))
        arm, x86 = triaged.outputs

        assert {each.instance for each in arm.shared_by} == {"c7g"}
        assert {each.instance for each in x86.shared_by} == {"c7i", "c7a"}


class TestTheRepresentative:
    def test_it_is_the_smallest_replication_of_the_first_declared_architecture(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, all_equal))

        assert representatives(triaged) == ["libx264_1080p_1080p_bbb_c7g_rep1"]

    def test_swapping_the_order_of_instance_swaps_the_representative(self, make_raw_config):
        c7g, c7i, c7a = three_architectures()
        pair = [{"input_res": "1080p", "output_res": "1080p"}]
        plan = plan_of(make_raw_config(instance=[c7i, c7g, c7a], pair=pair))

        triaged = triage(plan, *executions(plan, all_equal))

        assert representatives(triaged) == ["libx264_1080p_1080p_bbb_c7i_rep1"]

    def test_it_is_not_the_smallest_run_id(self, one_scenario):
        metas, hashes = executions(one_scenario, all_equal)
        winner = meta_of(metas, "libx264_1080p_1080p_bbb_c7a_rep5")
        hashes["00000000-0000-4000-8000-000000000000"] = hashes.pop(winner["run_id"])
        winner["run_id"] = "00000000-0000-4000-8000-000000000000"

        triaged = triage(one_scenario, metas, hashes)

        assert representatives(triaged) == ["libx264_1080p_1080p_bbb_c7g_rep1"]

    def test_it_is_not_the_earliest_started_at(self, one_scenario):
        metas, hashes = executions(one_scenario, all_equal)
        earliest = meta_of(metas, "libx264_1080p_1080p_bbb_c7a_rep5")
        earliest["started_at"] = "2020-01-01T00:00:00+00:00"

        triaged = triage(one_scenario, metas, hashes)

        assert representatives(triaged) == ["libx264_1080p_1080p_bbb_c7g_rep1"]


class TestTheWinningReplications:
    def test_the_warmup_is_not_a_bitstream_to_judge(self, one_scenario):
        metas, hashes = executions(
            one_scenario,
            lambda run: sha_of("warmup") if run["warmup"] else all_equal(run),
        )

        triaged = triage(one_scenario, metas, hashes)

        assert [bitstream.sha256 for bitstream in triaged.outputs] != [sha_of("warmup")]
        assert len(triaged.outputs) == 1

    def test_a_failed_replication_is_not_a_bitstream_to_judge(self, one_scenario):
        metas, hashes = executions(one_scenario, all_equal)
        failed = meta_of(metas, "libx264_1080p_1080p_bbb_c7i_rep2")
        failed["exit_code"] = 1
        hashes[failed["run_id"]] = sha_of("failed")

        triaged = triage(one_scenario, metas, hashes)

        assert len(triaged.outputs) == 1
        assert failed["run_id"] not in {each.run_id for each in triaged.outputs[0].shared_by}

    def test_the_later_instant_wins_even_when_the_string_sorts_the_other_way(self, one_scenario):
        metas, hashes = executions(one_scenario, all_equal)
        superseded = meta_of(metas, "libx264_1080p_1080p_bbb_c7g_rep1")
        superseded["started_at"] = "2026-08-08T23:00:00+03:00"
        remade = make_meta(
            scenario_id=superseded["scenario_id"],
            warmup=False,
            run_id="deadbeef-6b41-4d5f-8a37-2f1c8de0b7a4",
            started_at="2026-08-08T21:00:00-03:00",
        )
        hashes[remade["run_id"]] = hashes[superseded["run_id"]]

        triaged = triage(one_scenario, [*metas, remade], hashes)

        assert triaged.outputs[0].shared_by[0].run_id == remade["run_id"]


class TestTheDivergentCell:
    def test_two_hashes_in_a_cell_are_two_outputs_and_the_cell_is_named(self, one_scenario):
        odd = "libx264_1080p_1080p_bbb_c7g_rep3"
        metas, hashes = executions(
            one_scenario,
            lambda run: sha_of("odd") if run["scenario_id"] == odd else all_equal(run),
        )

        triaged = triage(one_scenario, metas, hashes)
        (group,) = triaged.groups

        assert len(triaged.outputs) == 2
        assert group.divergent_cells == ("libx264_1080p_1080p_bbb_c7g",)
        assert all(bitstream.cell_divergent for bitstream in triaged.outputs)

    def test_a_cell_that_holds_together_is_not_named(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, per_cell))
        (group,) = triaged.groups

        assert group.divergent_cells == ()
        assert not any(bitstream.cell_divergent for bitstream in triaged.outputs)

    def test_the_divergent_cell_is_judged_whole(self, one_scenario):
        odd = "libx264_1080p_1080p_bbb_c7g_rep3"
        metas, hashes = executions(
            one_scenario,
            lambda run: sha_of("odd") if run["scenario_id"] == odd else per_cell(run),
        )

        triaged = triage(one_scenario, metas, hashes)

        assert representatives(triaged) == [
            "libx264_1080p_1080p_bbb_c7g_rep1",
            "libx264_1080p_1080p_bbb_c7g_rep3",
            "libx264_1080p_1080p_bbb_c7i_rep1",
            "libx264_1080p_1080p_bbb_c7a_rep1",
        ]


class TestTheHashOfTheWinner:
    def test_a_missing_hash_brings_the_triage_down_naming_the_run(self, one_scenario):
        absent = "libx264_1080p_1080p_bbb_c7i_rep4"
        metas, hashes = executions(
            one_scenario,
            lambda run: None if run["scenario_id"] == absent else all_equal(run),
        )
        run_id = meta_of(metas, absent)["run_id"]

        with pytest.raises(TriageError, match=run_id):
            triage(one_scenario, metas, hashes)

    def test_a_hash_that_is_not_sixty_four_hexadecimals_brings_the_triage_down(self, one_scenario):
        metas, hashes = executions(one_scenario, all_equal)
        truncated = meta_of(metas, "libx264_1080p_1080p_bbb_c7a_rep1")["run_id"]
        hashes[truncated] = "a3f1c0de"

        with pytest.raises(TriageError, match=truncated):
            triage(one_scenario, metas, hashes)

    def test_an_empty_hash_file_is_refused(self, one_scenario):
        metas, hashes = executions(one_scenario, all_equal)
        empty = meta_of(metas, "libx264_1080p_1080p_bbb_c7a_rep1")["run_id"]
        hashes[empty] = ""

        with pytest.raises(TriageError, match=empty):
            triage(one_scenario, metas, hashes)

    def test_the_hash_of_a_run_that_is_not_judged_is_never_required(self, one_scenario):
        metas, hashes = executions(
            one_scenario, lambda run: None if run["warmup"] else all_equal(run)
        )

        assert len(triage(one_scenario, metas, hashes).outputs) == 1


class TestThePlanFile:
    def test_the_shape_is_the_one_the_judge_reads(self, make_raw_config, one_scenario):
        config = validate_config(
            make_raw_config(
                instance=three_architectures(),
                pair=[{"input_res": "1080p", "output_res": "1080p"}],
            )
        )
        triaged = triage(one_scenario, *executions(one_scenario, x86_together))

        plan = build_plan(config, triaged)

        assert plan["schema_version"] == SCHEMA_VERSION
        assert plan["quality"] == {
            "vmaf_model": "vmaf_v0.6.1",
            "vmaf_delta_max": 0.5,
            "ssim_delta_max": 0.001,
        }
        assert plan["outputs"][0] == {
            "run_id": plan["outputs"][0]["run_id"],
            "scenario_id": "libx264_1080p_1080p_bbb_c7g_rep1",
            "codec": "h264",
            "encoder": "libx264",
            "input_res": "1080p",
            "output_res": "1080p",
            "video": "bbb",
            "instance": "c7g",
            "sha256": sha_of("libx264_1080p_1080p_bbb", "c7g"),
            "master": "bbb_1080p.mkv",
            "output_width": 1920,
            "output_height": 1080,
            "scale_flags": "lanczos",
            "container": "mkv",
            "frames": 19036,
            "shared_by": plan["outputs"][0]["shared_by"],
            "cell_divergent": False,
        }

    def test_every_sharer_is_named_by_instance_scenario_and_run(
        self, make_raw_config, one_scenario
    ):
        config = validate_config(
            make_raw_config(
                instance=three_architectures(),
                pair=[{"input_res": "1080p", "output_res": "1080p"}],
            )
        )
        triaged = triage(one_scenario, *executions(one_scenario, x86_together))

        shared = build_plan(config, triaged)["outputs"][1]["shared_by"]

        assert len(shared) == 10
        assert set(shared[0]) == {"instance", "scenario_id", "run_id"}
        assert shared[0]["scenario_id"] == "libx264_1080p_1080p_bbb_c7i_rep1"

    def test_the_outputs_follow_the_order_of_the_canonical_plan(self, make_raw_config):
        plan = plan_of(make_raw_config(instance=three_architectures()))
        config = validate_config(make_raw_config(instance=three_architectures()))
        triaged = triage(plan, *executions(plan, per_cell))

        ordered = [output["scenario_id"] for output in build_plan(config, triaged)["outputs"]]
        canonical = [run["scenario_id"] for block in plan["blocks"] for run in block["runs"]]

        assert _is_subsequence(ordered, canonical)

    def test_two_triages_over_the_same_bucket_write_the_same_bytes(self, make_raw_config):
        plan = plan_of(make_raw_config(instance=three_architectures()))
        config = validate_config(make_raw_config(instance=three_architectures()))
        metas, hashes = executions(plan, x86_together)
        shuffled = list(metas)
        random.Random(7).shuffle(shuffled)

        first = serialize_plan(build_plan(config, triage(plan, metas, hashes)))
        second = serialize_plan(build_plan(config, triage(plan, shuffled, hashes)))

        assert first == second

    def test_the_written_plan_is_what_the_reader_accepts(self, make_raw_config, one_scenario):
        config = validate_config(
            make_raw_config(
                instance=three_architectures(),
                pair=[{"input_res": "1080p", "output_res": "1080p"}],
            )
        )
        triaged = triage(one_scenario, *executions(one_scenario, x86_together))

        raw = serialize_plan(build_plan(config, triaged))

        assert check_plan(raw) == json.loads(raw)


def _is_subsequence(ordered: list[str], canonical: list[str]) -> bool:
    remaining = iter(canonical)
    return all(name in remaining for name in ordered)


class TestTheReport:
    def test_it_names_every_group_with_the_architecture_of_each_bitstream(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, x86_together))

        report = render_report(triaged)

        assert "libx264_1080p_1080p_bbb" in report
        assert "c7g | c7i=c7a" in report

    def test_the_histogram_counts_groups_by_distinct_bitstreams(self, make_raw_config):
        plan = plan_of(make_raw_config(instance=three_architectures()))
        triaged = triage(plan, *executions(plan, x86_together))

        assert "2 bitstreams: 2 grupos" in render_report(triaged)

    def test_a_single_group_of_a_single_bitstream_is_said_in_the_singular(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, all_equal))

        report = render_report(triaged)

        assert "bitstreams distintos por grupo: 1 bitstream: 1 grupo" in report
        assert "1 grupo, 1 output a julgar" in report

    def test_the_total_of_outputs_to_judge_is_in_the_report(self, make_raw_config):
        plan = plan_of(make_raw_config(instance=three_architectures()))
        triaged = triage(plan, *executions(plan, x86_together))

        assert "2 grupos, 4 outputs a julgar" in render_report(triaged)

    def test_a_divergent_cell_is_named_in_the_report(self, one_scenario):
        odd = "libx264_1080p_1080p_bbb_c7g_rep3"
        metas, hashes = executions(
            one_scenario,
            lambda run: sha_of("odd") if run["scenario_id"] == odd else all_equal(run),
        )

        report = render_report(triage(one_scenario, metas, hashes))

        assert "libx264_1080p_1080p_bbb_c7g" in report.splitlines()[-2]

    def test_no_divergent_cell_is_said_out_loud(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, per_cell))

        assert "nenhuma célula divergente" in render_report(triaged)

    def test_the_report_carries_no_bytes(self, one_scenario):
        triaged = triage(one_scenario, *executions(one_scenario, per_cell))

        assert ".mkv" not in render_report(triaged)


class TestTheRealDefinitions:
    def test_the_campaign_has_fifty_four_groups(self):
        plan = build_canonical_plan(real_config())

        triaged = triage(plan, *executions(plan, x86_together))

        assert len(triaged.groups) == 54
        assert len(triaged.outputs) == 108

    def test_the_pilot_has_six_groups(self):
        plan = build_canonical_plan(real_pilot_config())

        triaged = triage(plan, *executions(plan, x86_together))

        assert len(triaged.groups) == 6
        assert len(triaged.outputs) == 12

    def test_the_real_plan_is_what_the_reader_accepts(self):
        config = real_pilot_config()
        plan = build_canonical_plan(config)
        triaged = triage(plan, *executions(plan, per_cell))

        raw = serialize_plan(build_plan(config, triaged))

        assert len(check_plan(raw)["outputs"]) == 18
