# Smoke de um bloco do piloto: o plano do `config/pilot.toml` pelo mesmo
# `run_all.sh`, os mesmos shims e o mesmo `consolidate.py` da campanha (ADR-0022).

from __future__ import annotations

from typing import Any

import pyarrow.parquet as pq
import pytest
from conftest import (
    INSTANCE_ID,
    INSTANCE_TYPE,
    PILOT,
    Loop,
    check_with_stdlib_checker,
    consolidate_with_cli,
    validate_with_cli,
)
from test_consolidate import perf_column
from test_run_all import DONE_MARKER, PROGRESS_OBJECT, done_marker, scenario_ids
from test_run_scenario import ARTIFACTS, UUID4, contains_subsequence, value_after

BLOCK_SUFFIXES = ["_warmup", "_rep1", "_rep2", "_rep3", "_rep4", "_rep5"]

PILOT_PAIR = "1080p_720p"

PMU_EVENT_COUNT = 10


def codec_record(encoder: str) -> dict[str, Any]:
    return next(codec for codec in PILOT["codec"] if codec["encoder"] == encoder)


def video_record(slug: str) -> dict[str, Any]:
    return next(video for video in PILOT["video"] if video["slug"] == slug)


@pytest.fixture(scope="session")
def pilot_block(pilot_plan: dict[str, Any]) -> dict[str, Any]:
    """O primeiro bloco da fatia que os argumentos de bootstrap dizem estar rodando.

    A fatia sai do `--instance-type`, e não de um id escrito à mão: um bloco de
    outra arquitetura faria o `instance` do `meta.json` discordar dele.
    """
    slice_of = next(
        instance["id"]
        for instance in PILOT["instance"]
        if instance["instance_type"] == INSTANCE_TYPE
    )
    return next(block for block in pilot_plan["blocks"] if block["instance"] == slice_of)


@pytest.fixture(scope="session")
def pilot_loop(pilot_plan: dict[str, Any], pilot_block: dict[str, Any], run_all) -> Loop:
    """A árvore do bloco do piloto: o warm-up mais as cinco Replicações."""
    return run_all(pilot_plan, [pilot_block])


@pytest.fixture(scope="session")
def replication_argv(pilot_loop: Loop) -> list[str]:
    """O argv do encode de uma Replicação do bloco — nunca o do warm-up."""
    metas = pilot_loop.metas()
    encodes = [argv for argv in pilot_loop.argv("ffmpeg") if argv[-1] != "-"]
    by_run_id = dict(zip(pilot_loop.encoded_run_ids(), encodes, strict=True))
    return next(argv for run_id, argv in by_run_id.items() if not metas[run_id]["warmup"])


@pytest.fixture(scope="session")
def pilot_consolidation(pilot_loop: Loop, tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("pilot-consolidated") / "runs.parquet"
    result = consolidate_with_cli(pilot_loop.runs_dir, out)
    assert result.returncode == 0, result.stderr
    return result, pq.read_table(out)


class TestBlock:
    def test_the_loop_succeeds(self, pilot_loop):
        assert pilot_loop.returncode == 0, pilot_loop.stderr

    def test_six_executions_with_distinct_run_ids(self, pilot_loop):
        run_ids = [path.name for path in pilot_loop.run_dirs()]

        assert len(run_ids) == 6
        assert len(set(run_ids)) == 6
        assert all(UUID4.match(run_id) for run_id in run_ids)

    def test_each_execution_has_the_seven_artifacts(self, pilot_loop):
        for run_dir in pilot_loop.run_dirs():
            assert {path.name for path in run_dir.iterdir()} == ARTIFACTS, run_dir

    def test_warmup_first_then_the_file_order(self, pilot_loop, pilot_block):
        expected = [run["scenario_id"] for run in pilot_block["runs"]]

        assert scenario_ids(pilot_loop) == expected
        assert pilot_block["runs"][0]["warmup"] is True
        assert pilot_loop.metas()[pilot_loop.encoded_run_ids()[0]]["warmup"] is True

    def test_every_run_is_uploaded_before_the_next_encode(self, pilot_loop):
        relevant = [tool for tool in pilot_loop.sequence() if tool in {"ffmpeg", "aws"}]

        assert relevant == ["ffmpeg", "ffmpeg", "aws", "aws"] * 6 + ["aws"]

    def test_the_done_marker_closes_the_slice(self, pilot_loop, list_objects):
        assert list_objects(pilot_loop, "status/") == [DONE_MARKER, PROGRESS_OBJECT]
        assert done_marker(pilot_loop)["instance_id"] == INSTANCE_ID

    def test_every_meta_json_reports_success(self, pilot_loop):
        assert {meta["exit_code"] for meta in pilot_loop.metas().values()} == {0}


class TestScenarioIds:
    def test_the_suffixes_are_the_warmup_and_the_five_replications(self, pilot_loop):
        scenarios = scenario_ids(pilot_loop)

        assert len(scenarios) == len(BLOCK_SUFFIXES)
        assert all(
            scenario_id.endswith(suffix)
            for scenario_id, suffix in zip(scenarios, BLOCK_SUFFIXES, strict=True)
        )

    def test_every_scenario_names_the_only_pair_of_the_pilot(self, pilot_loop):
        assert all(f"_{PILOT_PAIR}_" in scenario_id for scenario_id in scenario_ids(pilot_loop))


class TestArgv:
    def test_encoder_preset_and_crf_are_the_declared_ones(self, replication_argv, pilot_block):
        codec = codec_record(pilot_block["encoder"])

        assert value_after(replication_argv, "-c:v") == codec["encoder"]
        assert value_after(replication_argv, "-preset") == codec["preset"]
        assert value_after(replication_argv, "-crf") == str(codec["crf"])

    def test_encoder_specific_arguments_arrive_intact(self, replication_argv, pilot_block):
        assert contains_subsequence(
            replication_argv, codec_record(pilot_block["encoder"])["encoder_args"]
        )

    def test_output_geometry_is_the_720p_of_that_video(self, replication_argv, pilot_block):
        geometry = video_record(pilot_block["video"])["geometry"][pilot_block["output_res"]]
        scale_flags = PILOT["encode"]["scale_flags"]

        assert value_after(replication_argv, "-vf") == (
            f"scale={geometry['width']}:{geometry['height']}:flags={scale_flags}"
        )

    def test_the_master_is_the_input_tier_of_that_video(self, replication_argv, pilot_block):
        master = f"{pilot_block['video']}_{pilot_block['input_res']}.mkv"

        assert value_after(replication_argv, "-i").endswith(master)


class TestMeta:
    def test_accepted_by_the_validation_cli(self, pilot_loop):
        for run_dir in pilot_loop.run_dirs():
            result = validate_with_cli(run_dir / "meta.json")

            assert result.returncode == 0, result.stderr

    def test_accepted_by_the_stdlib_checker(self, pilot_loop):
        for run_dir in pilot_loop.run_dirs():
            result = check_with_stdlib_checker(run_dir / "meta.json")

            assert result.returncode == 0, result.stderr


class TestConsolidation:
    def test_the_block_becomes_five_rows_with_the_warmup_out(
        self, pilot_consolidation, pilot_block
    ):
        result, table = pilot_consolidation
        replications = sorted(
            run["scenario_id"] for run in pilot_block["runs"] if not run["warmup"]
        )

        assert table.num_rows == 5
        assert table.column("scenario_id").to_pylist() == replications
        assert "5 linhas" in result.stdout
        assert "1 warm-ups fora" in result.stdout

    def test_no_artifact_of_the_pilot_tree_is_unreadable(self, pilot_consolidation):
        result, _ = pilot_consolidation

        assert "0 artefatos ilegíveis" in result.stdout
        assert result.stderr == ""

    def test_the_ten_pmu_events_of_the_pilot_become_filled_columns(self, pilot_consolidation):
        _, table = pilot_consolidation
        events = PILOT["instrumentation"]["pmu_events"]

        assert len(events) == PMU_EVENT_COUNT
        for event in events:
            column = table.column(perf_column(event)).to_pylist()

            assert len(column) == 5, event
            assert all(value is not None for value in column), event
