# Smoke do `encode/run_all.sh`: o laço de verdade sobre um plano de um bloco,
# com os mesmos shims do `run_scenario.sh` mais o do `aws` (ADR-0022).

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from typing import Any

import pytest
from conftest import BUCKET, INSTANCE_TYPE, Loop
from test_run_scenario import ARTIFACTS, UUID4

DONE_MARKER = f"status/{INSTANCE_TYPE}_done"

# Único por processo: a asserção de que o encode travado morreu procura este
# `sleep` no `ps` da máquina inteira, e outra sessão do smoke rodando ao lado
# não pode ser confundida com ele.
HANG_S = f"3571.{os.getpid() % 1000}"


def scenario_ids(loop: Loop) -> list[str]:
    metas = loop.metas()
    return [metas[run_id]["scenario_id"] for run_id in loop.encoded_run_ids()]


def uploads(loop: Loop) -> list[list[str]]:
    return [argv for argv in loop.argv("aws") if argv[:2] == ["s3", "cp"]]


def elapsed_s(meta: dict[str, Any]) -> float:
    started = datetime.fromisoformat(meta["started_at"])
    finished = datetime.fromisoformat(meta["finished_at"])
    return (finished - started).total_seconds()


@pytest.fixture(scope="session")
def block(plan) -> dict[str, Any]:
    return plan["blocks"][0]


@pytest.fixture(scope="session")
def loop(block, run_all) -> Loop:
    return run_all([block])


@pytest.fixture(scope="session")
def loop_with_a_failed_run(block, run_all) -> Loop:
    """O segundo encode do bloco falha; os outros cinco seguem bem."""
    return run_all([block], SMOKE_FFMPEG_EXIT="1", SMOKE_FFMPEG_NTH="2")


@pytest.fixture(scope="session")
def loop_with_a_hung_run(block, run_all) -> Loop:
    """O primeiro encode do bloco trava, e o timeout por Execução vale 2 s."""
    return run_all([block], "--run-timeout", "2", SMOKE_FFMPEG_HANG=HANG_S, SMOKE_FFMPEG_NTH="1")


@pytest.fixture(scope="session")
def loop_over_the_cap(plan, run_all) -> Loop:
    """Dois blocos e um teto de 1 s: o primeiro bloco sozinho já o estoura."""
    return run_all(plan["blocks"][:2], "--total-timeout", "1")


class TestBlock:
    def test_the_loop_succeeds(self, loop):
        assert loop.returncode == 0, loop.stderr

    def test_six_executions_with_distinct_run_ids(self, loop):
        run_ids = [path.name for path in loop.run_dirs()]

        assert len(run_ids) == 6
        assert len(set(run_ids)) == 6
        assert all(UUID4.match(run_id) for run_id in run_ids)

    def test_each_execution_has_the_seven_artifacts(self, loop):
        for run_dir in loop.run_dirs():
            assert {path.name for path in run_dir.iterdir()} == ARTIFACTS, run_dir

    def test_warmup_first_then_the_file_order(self, loop, block):
        expected = [run["scenario_id"] for run in block["runs"]]

        assert scenario_ids(loop) == expected
        assert block["runs"][0]["warmup"] is True
        assert loop.metas()[loop.encoded_run_ids()[0]]["warmup"] is True

    def test_every_run_is_uploaded_before_the_next_encode(self, loop):
        # Encode, extração do bitstream e upload, seis vezes.
        relevant = [tool for tool in loop.sequence() if tool in {"ffmpeg", "aws"}]

        assert relevant == ["ffmpeg", "ffmpeg", "aws"] * 6 + ["aws"]

    def test_every_meta_json_reports_success(self, loop):
        assert {meta["exit_code"] for meta in loop.metas().values()} == {0}


class TestFailedRun:
    def test_the_next_scenario_happens(self, loop_with_a_failed_run):
        loop = loop_with_a_failed_run
        exit_codes = [loop.metas()[run_id]["exit_code"] for run_id in loop.encoded_run_ids()]

        assert len(loop.run_dirs()) == 6
        assert exit_codes == [0, 1, 0, 0, 0, 0]

    def test_the_failed_run_is_uploaded_too(self, loop_with_a_failed_run):
        loop = loop_with_a_failed_run
        failed = loop.runs_dir / loop.encoded_run_ids()[1]

        assert {path.name for path in loop.uploaded(failed).iterdir()} == {
            path.name for path in failed.iterdir()
        }
        assert "meta.json" in {path.name for path in loop.uploaded(failed).iterdir()}

    def test_the_loop_reports_the_failure(self, loop_with_a_failed_run):
        assert loop_with_a_failed_run.returncode != 0
        assert "1 com falha" in loop_with_a_failed_run.stderr


class TestRunTimeout:
    def test_the_hung_run_is_killed_and_the_loop_goes_on(self, loop_with_a_hung_run):
        loop = loop_with_a_hung_run
        metas = loop.metas()
        exit_codes = [metas[run_id]["exit_code"] for run_id in loop.encoded_run_ids()]

        assert len(loop.run_dirs()) == 6
        assert exit_codes[0] != 0
        assert exit_codes[1:] == [0, 0, 0, 0, 0]
        assert elapsed_s(metas[loop.encoded_run_ids()[0]]) < 30

    def test_the_hung_encoder_does_not_outlive_the_run(self, loop_with_a_hung_run):
        processes = subprocess.run(["ps", "-Ao", "args="], capture_output=True, text=True)

        assert f"sleep {HANG_S}" not in processes.stdout

    def test_what_the_hung_run_had_is_uploaded(self, loop_with_a_hung_run):
        loop = loop_with_a_hung_run
        hung = loop.runs_dir / loop.encoded_run_ids()[0]

        assert "meta.json" in {path.name for path in loop.uploaded(hung).iterdir()}

    def test_the_timeout_is_reported(self, loop_with_a_hung_run):
        assert re.search(r"excedeu o timeout de 2s", loop_with_a_hung_run.stderr)
        assert loop_with_a_hung_run.returncode != 0


class TestTotalTimeout:
    def test_the_second_block_does_not_start(self, loop_over_the_cap, plan):
        loop = loop_over_the_cap
        first_block = [run["scenario_id"] for run in plan["blocks"][0]["runs"]]

        assert scenario_ids(loop) == first_block

    def test_the_done_marker_is_still_written(self, loop_over_the_cap, list_objects):
        # Sem ele o Orquestrador espera para sempre por uma Instância que parou.
        assert list_objects(loop_over_the_cap, "status/") == [DONE_MARKER]

    def test_the_cap_is_reported(self, loop_over_the_cap):
        assert "teto de 1s atingido antes do bloco 1" in loop_over_the_cap.stderr
        assert loop_over_the_cap.returncode != 0


class TestPrefixLayout:
    def test_the_objects_appear_under_runs_run_id(self, loop, list_objects):
        expected = {
            f"runs/{run_dir.name}/{artifact}"
            for run_dir in loop.run_dirs()
            for artifact in ARTIFACTS
        }

        assert set(list_objects(loop, "runs/")) == expected

    def test_the_done_marker_is_the_last_object_written(self, loop, list_objects):
        last = loop.argv("aws")[-1]

        assert list_objects(loop, "status/") == [DONE_MARKER]
        assert (loop.bucket_dir() / DONE_MARKER).is_file()
        assert last[:2] == ["s3", "cp"]
        assert last[3:] == [f"s3://{BUCKET}/{DONE_MARKER}"]

    def test_the_upload_is_the_command_of_the_adr(self, loop):
        # `aws s3 cp runs/{run_id}/ s3://bucket/runs/{run_id}/ --recursive`
        # (ADR-0011): a matriz IAM escopa por prefixo, e um `/` a menos põe o
        # `meta.json` num objeto chamado `runs/{run_id}`.
        for argv in uploads(loop)[:-1]:
            run_id = argv[2].rstrip("/").rsplit("/", 1)[-1]

            assert argv == ["s3", "cp", argv[2], f"s3://{BUCKET}/runs/{run_id}/", "--recursive"]

    def test_nothing_lands_outside_the_two_prefixes(self, loop, list_objects):
        keys = list_objects(loop, "")

        assert all(key.startswith(("runs/", "status/")) for key in keys)
        assert len(keys) == 6 * len(ARTIFACTS) + 1
