# Smoke do `encode/run_all.sh`: o laço de verdade sobre um plano de um bloco,
# com os mesmos shims do `run_scenario.sh` mais o do `aws` (ADR-0022).

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any

import pytest
from conftest import BUCKET, INSTANCE_ID, INSTANCE_TYPE, Loop
from test_run_scenario import ARTIFACTS, UUID4

DONE_MARKER = f"status/{INSTANCE_TYPE}_done"
PROGRESS_OBJECT = f"status/{INSTANCE_TYPE}_progress"

# O contrato cross-language do `status/`: o Orquestrador lê estes campos com
# estes tipos, e `bool` não é `int` do lado de quem lê.
PROGRESS_TYPES = {
    "instance_id": str,
    "block_index": int,
    "block_count": int,
    "run_index": int,
    "run_count": int,
    "scenario_id": str,
    "runs_total": int,
    "runs_failed": int,
    "elapsed_seconds": int,
    "written_at": str,
}

DONE_TYPES = {
    "instance_id": str,
    "finished_at": str,
    "runs_total": int,
    "runs_failed": int,
    "capped": bool,
    "exit_status": int,
}

# Único por processo: a asserção de que o encode travado morreu procura este
# `sleep` no `ps` da máquina inteira, e outra sessão do smoke rodando ao lado
# não pode ser confundida com ele.
HANG_S = f"3571.{os.getpid() % 1000}"


def scenario_ids(loop: Loop) -> list[str]:
    metas = loop.metas()
    return [metas[run_id]["scenario_id"] for run_id in loop.encoded_run_ids()]


def uploads(loop: Loop) -> list[list[str]]:
    return [argv for argv in loop.argv("aws") if argv[:2] == ["s3", "cp"]]


def upload_kind(argv: list[str]) -> str:
    """Qual dos três objetos aquele `s3 cp` subiu."""
    key = argv[3].removeprefix(f"s3://{BUCKET}/")
    if key == DONE_MARKER:
        return "done"
    if key == PROGRESS_OBJECT:
        return "progress"
    return "run"


def upload_kinds(loop: Loop) -> list[str]:
    return [upload_kind(argv) for argv in uploads(loop)]


def progress_versions(loop: Loop) -> list[dict[str, Any]]:
    """O objeto de progresso como ele estava depois de cada Execução."""
    return [json.loads(payload) for payload in loop.object_versions(PROGRESS_OBJECT)]


def done_marker(loop: Loop) -> dict[str, Any]:
    return json.loads((loop.bucket_dir() / DONE_MARKER).read_text(encoding="utf-8"))


def typed_fields(payload: dict[str, Any]) -> dict[str, type]:
    return {name: type(value) for name, value in payload.items()}


def offset_aware(timestamp: str) -> bool:
    return datetime.fromisoformat(timestamp).utcoffset() is not None


def elapsed_s(meta: dict[str, Any]) -> float:
    started = datetime.fromisoformat(meta["started_at"])
    finished = datetime.fromisoformat(meta["finished_at"])
    return (finished - started).total_seconds()


@pytest.fixture(scope="session")
def loop_with_a_failed_run(plan, block, run_all) -> Loop:
    """O segundo encode do bloco falha; os outros cinco seguem bem."""
    return run_all(plan, [block], SMOKE_FFMPEG_EXIT="1", SMOKE_FFMPEG_NTH="2")


@pytest.fixture(scope="session")
def loop_with_a_hung_run(plan, block, run_all) -> Loop:
    """O primeiro encode do bloco trava, e o timeout por Execução vale 2 s."""
    return run_all(
        plan, [block], "--run-timeout", "2", SMOKE_FFMPEG_HANG=HANG_S, SMOKE_FFMPEG_NTH="1"
    )


@pytest.fixture(scope="session")
def loop_with_no_progress_upload(plan, block, run_all) -> Loop:
    """Todo `s3 cp` do objeto de progresso falha; os dos runs seguem bem."""
    return run_all(plan, [block], SMOKE_AWS_FAIL_KEY=PROGRESS_OBJECT)


@pytest.fixture(scope="session")
def loop_over_the_cap(plan, run_all) -> Loop:
    """Dois blocos e um teto de 1 s: o primeiro bloco sozinho já o estoura."""
    return run_all(plan, plan["blocks"][:2], "--total-timeout", "1")


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
        # Encode, extração do bitstream, upload do run e upload do progresso,
        # seis vezes, e o marcador por último.
        relevant = [tool for tool in loop.sequence() if tool in {"ffmpeg", "aws"}]

        assert relevant == ["ffmpeg", "ffmpeg", "aws", "aws"] * 6 + ["aws"]

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
        assert list_objects(loop_over_the_cap, "status/") == [DONE_MARKER, PROGRESS_OBJECT]
        assert upload_kinds(loop_over_the_cap)[-1] == "done"

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

        assert list_objects(loop, "status/") == [DONE_MARKER, PROGRESS_OBJECT]
        assert (loop.bucket_dir() / DONE_MARKER).is_file()
        assert last[:2] == ["s3", "cp"]
        assert last[3:] == [f"s3://{BUCKET}/{DONE_MARKER}"]

    def test_the_upload_is_the_command_of_the_adr(self, loop):
        # `aws s3 cp runs/{run_id}/ s3://bucket/runs/{run_id}/ --recursive`
        # (ADR-0011): a matriz IAM escopa por prefixo, e um `/` a menos põe o
        # `meta.json` num objeto chamado `runs/{run_id}`.
        for argv in uploads(loop):
            if upload_kind(argv) != "run":
                continue
            run_id = argv[2].rstrip("/").rsplit("/", 1)[-1]

            assert argv == ["s3", "cp", argv[2], f"s3://{BUCKET}/runs/{run_id}/", "--recursive"]

    def test_nothing_lands_outside_the_two_prefixes(self, loop, list_objects):
        keys = list_objects(loop, "")

        assert all(key.startswith(("runs/", "status/")) for key in keys)
        assert len(keys) == 6 * len(ARTIFACTS) + 2


class TestProgress:
    def test_one_progress_object_after_each_run(self, loop):
        assert len(progress_versions(loop)) == len(loop.run_dirs())

    def test_it_goes_up_between_runs_never_inside_one(self, loop):
        assert upload_kinds(loop) == ["run", "progress"] * 6 + ["done"]

    def test_the_fields_and_their_types_are_the_contract(self, loop):
        for payload in progress_versions(loop):
            assert typed_fields(payload) == PROGRESS_TYPES

    def test_the_run_index_walks_the_block(self, loop, block):
        payloads = progress_versions(loop)

        assert [payload["run_index"] for payload in payloads] == [1, 2, 3, 4, 5, 6]
        assert {payload["run_count"] for payload in payloads} == {len(block["runs"])}

    def test_the_block_index_is_the_only_block_of_the_slice(self, loop):
        payloads = progress_versions(loop)

        assert {payload["block_index"] for payload in payloads} == {1}
        assert {payload["block_count"] for payload in payloads} == {1}

    def test_each_one_names_the_execution_that_just_ended(self, loop):
        assert [payload["scenario_id"] for payload in progress_versions(loop)] == scenario_ids(loop)

    def test_the_counters_follow_the_loop(self, loop):
        payloads = progress_versions(loop)

        assert [payload["runs_total"] for payload in payloads] == [1, 2, 3, 4, 5, 6]
        assert {payload["runs_failed"] for payload in payloads} == {0}

    def test_runs_failed_rises_on_the_induced_failure(self, loop_with_a_failed_run):
        payloads = progress_versions(loop_with_a_failed_run)

        assert [payload["runs_failed"] for payload in payloads] == [0, 1, 1, 1, 1, 1]

    def test_it_carries_the_identity_it_received(self, loop):
        assert {payload["instance_id"] for payload in progress_versions(loop)} == {INSTANCE_ID}

    def test_the_clock_fields_are_offset_aware_and_monotonic(self, loop):
        payloads = progress_versions(loop)
        elapsed = [payload["elapsed_seconds"] for payload in payloads]

        assert all(offset_aware(payload["written_at"]) for payload in payloads)
        assert elapsed == sorted(elapsed)

    def test_a_progress_that_does_not_go_up_does_not_end_the_slice(
        self, loop_with_no_progress_upload
    ):
        loop = loop_with_no_progress_upload

        assert loop.returncode == 0, loop.stderr
        assert len(loop.run_dirs()) == 6
        assert progress_versions(loop) == []
        assert done_marker(loop)["runs_total"] == 6

    def test_the_two_blocks_of_the_cap_run_show_the_slice_total(self, loop_over_the_cap):
        payloads = progress_versions(loop_over_the_cap)

        assert {payload["block_count"] for payload in payloads} == {2}
        assert {payload["block_index"] for payload in payloads} == {1}


class TestDoneMarker:
    def test_the_fields_and_their_types_are_the_contract(self, loop):
        assert typed_fields(done_marker(loop)) == DONE_TYPES

    def test_it_carries_the_identity_the_resume_needs(self, loop):
        marker = done_marker(loop)

        assert marker["instance_id"] == INSTANCE_ID
        assert offset_aware(marker["finished_at"])

    def test_the_healthy_slice_closes_clean(self, loop):
        marker = done_marker(loop)

        assert marker["runs_total"] == 6
        assert marker["runs_failed"] == 0
        assert marker["capped"] is False
        assert marker["exit_status"] == 0
        assert marker["exit_status"] == loop.returncode

    def test_the_failed_run_reaches_the_marker(self, loop_with_a_failed_run):
        marker = done_marker(loop_with_a_failed_run)

        assert marker["runs_total"] == 6
        assert marker["runs_failed"] == 1
        assert marker["capped"] is False
        assert marker["exit_status"] == loop_with_a_failed_run.returncode
        assert marker["exit_status"] != 0

    def test_the_cap_reaches_the_marker(self, loop_over_the_cap):
        marker = done_marker(loop_over_the_cap)

        assert marker["runs_total"] == 6
        assert marker["runs_failed"] == 0
        assert marker["capped"] is True
        assert marker["exit_status"] == loop_over_the_cap.returncode
        assert marker["exit_status"] != 0
