# Smoke do `judge/run_quality.sh`: o laço do Juiz de verdade sobre o plano que o
# `quality_triage.py` acabou de escrever, com `ffmpeg` e `aws` shimados, sem
# Docker, sem AWS e sem FFmpeg (ADR-0022). O que este módulo exercita e por quê
# está no `smoke/README.md`.

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    ARM,
    BUCKET,
    COMMIT,
    DONE_MARKER_TYPES,
    JUDGE_FILENAME,
    JUDGE_INSTANCE_ID,
    JUDGE_INSTANCE_TYPE,
    JUDGE_THREADS,
    PILOT,
    QUALITY_RESULTS_PREFIX,
    VERSIONS,
    Pass,
    check_judgement_with_stdlib_checker,
    offset_aware,
    typed_fields,
    validate_judgement_with_cli,
)

JUDGE_PROGRESS = "status/judge_progress"
JUDGE_DONE = "status/judge_done"

VMAF_LOG = "vmaf.json"
FFMPEG_LOG = "ffmpeg.log"

# Os três artefatos por output julgado da ADR-0011.
ARTIFACTS = frozenset({VMAF_LOG, JUDGE_FILENAME, FFMPEG_LOG})

# O VMAF que o shim devolve no Pass são: um valor escolhido, e não o default,
# porque o que se verifica é que o log do bucket é o que o `libvmaf` escreveu.
SMOKE_VMAF = "91.5"

# O contrato cross-language do `status/` do Juiz: o Orquestrador lê estes campos
# com estes tipos, e `bool` não é `int` do lado de quem lê.
PROGRESS_TYPES = {
    "instance_id": str,
    "output_index": int,
    "output_count": int,
    "run_id": str,
    "scenario_id": str,
    "runs_total": int,
    "runs_failed": int,
    "elapsed_seconds": int,
    "written_at": str,
}

# Único por processo, como no `test_run_all.py`: a asserção de que o julgamento
# travado morreu procura este `sleep` no `ps` da máquina inteira.
HANG_S = f"3572.{os.getpid() % 1000}"

# O output que os dois desvios escolhem — a falha induzida e o travamento.
FIRST_JUDGEMENT = "1"
SECOND_JUDGEMENT = "2"


def video_record(slug: str) -> dict[str, Any]:
    return next(video for video in PILOT["video"] if video["slug"] == slug)


def geometry_of(output: dict[str, Any]) -> dict[str, int]:
    """A geometria do tier de saída daquele vídeo, pela definição.

    Os tiers são rótulos nominais: "1080p" não é 1920x1080 nos dois vídeos.
    """
    return video_record(output["video"])["geometry"][output["output_res"]]


def value_after(argv: list[str], flag: str) -> str:
    assert flag in argv, argv
    return argv[argv.index(flag) + 1]


def values_after(argv: list[str], flag: str) -> list[str]:
    return [argv[index + 1] for index, each in enumerate(argv) if each == flag]


def filtergraph(argv: list[str]) -> str:
    return value_after(argv, "-filter_complex")


def libvmaf_options(argv: list[str]) -> list[str]:
    _, _, options = filtergraph(argv).partition("libvmaf=")
    return options.split(":")


def judged_run_ids(judged: Pass) -> list[str]:
    """O `run_id` de cada julgamento, na ordem em que o shim recebeu as passadas."""
    return [Path(value_after(argv, "-i")).stem for argv in judged.argv("ffmpeg")]


def copies(judged: Pass) -> list[list[str]]:
    return [argv for argv in judged.argv("aws") if argv[:2] == ["s3", "cp"]]


def copy_kind(argv: list[str]) -> str:
    """Qual dos quatro objetos aquele `s3 cp` moveu."""
    source, destination = argv[2], argv[3]
    if source.startswith("s3://"):
        return "download"
    key = destination.removeprefix(f"s3://{BUCKET}/")
    if key == JUDGE_DONE:
        return "done"
    if key == JUDGE_PROGRESS:
        return "progress"
    return "result"


def copy_kinds(judged: Pass) -> list[str]:
    return [copy_kind(argv) for argv in copies(judged)]


def progress_versions(judged: Pass) -> list[dict[str, Any]]:
    """O objeto de progresso como ele estava depois de cada output."""
    return [json.loads(payload) for payload in judged.object_versions(JUDGE_PROGRESS)]


def done_marker(judged: Pass) -> dict[str, Any]:
    return json.loads((judged.bucket_dir() / JUDGE_DONE).read_text(encoding="utf-8"))


def vmaf_log(judged: Pass, run_id: str) -> dict[str, Any]:
    return json.loads((judged.results(run_id) / VMAF_LOG).read_text(encoding="utf-8"))


def exit_codes(judged: Pass) -> list[int]:
    return [judged.judgement(output["run_id"])["exit_code"] for output in judged.outputs()]


@pytest.fixture(scope="session")
def judged(triaged, campaign, run_quality) -> Pass:
    """O Pass inteiro e são sobre o plano do triage."""
    return run_quality(campaign[ARM], triaged.plan(), SMOKE_VMAF=SMOKE_VMAF)


@pytest.fixture(scope="session")
def judged_with_a_failed_output(triaged, campaign, run_quality) -> Pass:
    """O segundo julgamento falha; os outros três seguem bem."""
    return run_quality(
        campaign[ARM],
        triaged.plan(),
        SMOKE_FFMPEG_EXIT="1",
        SMOKE_FFMPEG_NTH=SECOND_JUDGEMENT,
    )


@pytest.fixture(scope="session")
def judged_with_a_hung_output(triaged, campaign, run_quality) -> Pass:
    """O primeiro `libvmaf` trava, e o timeout por output vale 2 s."""
    return run_quality(
        campaign[ARM],
        triaged.plan(),
        "--output-timeout",
        "2",
        SMOKE_FFMPEG_HANG=HANG_S,
        SMOKE_FFMPEG_NTH=FIRST_JUDGEMENT,
    )


@pytest.fixture(scope="session")
def judged_with_no_progress_upload(triaged, campaign, run_quality) -> Pass:
    """Todo `s3 cp` do objeto de progresso falha; os dos resultados seguem bem."""
    return run_quality(campaign[ARM], triaged.plan(), SMOKE_AWS_FAIL_KEY=JUDGE_PROGRESS)


@pytest.fixture(scope="session")
def judged_with_no_result_upload(triaged, campaign, run_quality) -> Pass:
    """O upload do resultado do segundo output falha; os outros três sobem bem."""
    failed = triaged.plan()["outputs"][1]["run_id"]
    return run_quality(
        campaign[ARM],
        triaged.plan(),
        SMOKE_AWS_FAIL_KEY=f"{QUALITY_RESULTS_PREFIX}/{failed}/",
    )


@pytest.fixture(scope="session")
def judged_over_the_cap(triaged, campaign, run_quality) -> Pass:
    """O teto conferido antes do primeiro output.

    Zero segundos, e não um punhado: o teto é conferido antes de cada output, e um
    julgamento shimado leva milissegundos — qualquer teto positivo caparia o plano
    num ponto que depende da máquina. O que se verifica aqui é o que o teto tem de
    fazer em qualquer ponto: parar o laço e ainda assim escrever o marcador.
    """
    return run_quality(campaign[ARM], triaged.plan(), "--total-timeout", "0")


class TestThePass:
    def test_it_succeeds(self, judged):
        assert judged.returncode == 0, judged.stderr

    def test_it_judges_every_output_of_the_plan_in_the_file_order(self, judged):
        assert judged_run_ids(judged) == [output["run_id"] for output in judged.outputs()]

    def test_the_plan_is_the_one_the_triage_wrote(self, judged, triaged):
        assert judged.plan == triaged.plan()
        assert len(judged.outputs()) > 1

    def test_each_output_is_downloaded_judged_and_uploaded_before_the_next(self, judged):
        # Download, o `libvmaf`, o upload do resultado e o do progresso, por
        # output, e o marcador por último.
        relevant = [tool for tool in judged.sequence() if tool in {"ffmpeg", "aws"}]

        assert relevant == ["aws", "ffmpeg", "aws", "aws"] * len(judged.outputs()) + ["aws"]

    def test_every_judgement_reports_success(self, judged):
        assert exit_codes(judged) == [0] * len(judged.outputs())


class TestArgv:
    def test_the_two_inputs_are_the_output_and_then_the_master(self, judged):
        for output, argv in zip(judged.outputs(), judged.argv("ffmpeg"), strict=True):
            inputs = [Path(each).name for each in values_after(argv, "-i")]

            assert inputs == [
                f"{output['run_id']}.{output['container']}",
                output["master"],
            ]

    def test_the_reference_is_the_master_scaled_by_the_tier_geometry_of_that_video(self, judged):
        flags = PILOT["encode"]["scale_flags"]

        for output, argv in zip(judged.outputs(), judged.argv("ffmpeg"), strict=True):
            geometry = geometry_of(output)

            assert filtergraph(argv).startswith(
                f"[1:v]scale={geometry['width']}:{geometry['height']}:flags={flags}[ref];"
            )

    def test_the_distorted_input_is_the_first_and_the_reference_the_second(self, judged):
        # Trocar a ordem aqui é medir o Master contra o output e reportar o
        # número como se fosse o do encoder.
        for argv in judged.argv("ffmpeg"):
            _, _, comparison = filtergraph(argv).partition(";")

            assert comparison.startswith("[0:v][ref]libvmaf=")

    def test_the_model_is_the_one_the_definition_declares(self, judged):
        declared = f"model=version={PILOT['quality']['vmaf_model']}"

        for argv in judged.argv("ffmpeg"):
            assert declared in libvmaf_options(argv)

    def test_ssim_comes_out_of_the_same_pass(self, judged):
        for argv in judged.argv("ffmpeg"):
            assert "feature=name=float_ssim" in libvmaf_options(argv)

    def test_the_thread_count_is_the_one_it_received(self, judged):
        for argv in judged.argv("ffmpeg"):
            assert f"n_threads={JUDGE_THREADS}" in libvmaf_options(argv)

    def test_the_log_is_json_and_lands_in_the_result_dir_of_that_output(self, judged):
        for output, argv in zip(judged.outputs(), judged.argv("ffmpeg"), strict=True):
            options = libvmaf_options(argv)
            (log_path,) = [
                each.removeprefix("log_path=") for each in options if each.startswith("log_path=")
            ]

            assert "log_fmt=json" in options
            assert Path(log_path) == judged.local_results(output["run_id"]) / VMAF_LOG

    def test_nothing_is_encoded_and_the_output_is_discarded(self, judged):
        for argv in judged.argv("ffmpeg"):
            assert value_after(argv, "-f") == "null"
            assert argv[-1] == "-"


class TestTheArtifacts:
    def test_each_output_leaves_the_three_files_under_its_run_id(self, judged):
        for output in judged.outputs():
            results = judged.results(output["run_id"])

            assert {path.name for path in results.iterdir()} == ARTIFACTS, results

    def test_the_mkv_is_downloaded_by_the_key_the_plan_names(self, judged):
        downloads = [argv for argv in copies(judged) if copy_kind(argv) == "download"]

        assert [argv[2] for argv in downloads] == [
            f"s3://{BUCKET}/runs/{output['run_id']}/output.{output['container']}"
            for output in judged.outputs()
        ]

    def test_the_result_dir_goes_up_under_the_prefix_of_the_adr(self, judged):
        # `aws s3 cp results/{run_id}/ s3://bucket/quality/results/{run_id}/ --recursive`
        # (ADR-0011): a matriz IAM escopa por prefixo, e um `/` a menos põe os três
        # arquivos num objeto chamado `quality/results/{run_id}`.
        uploads = [argv for argv in copies(judged) if copy_kind(argv) == "result"]

        assert [argv[3:] for argv in uploads] == [
            [f"s3://{BUCKET}/quality/results/{output['run_id']}/", "--recursive"]
            for output in judged.outputs()
        ]

    def test_the_local_mkv_is_gone_once_the_output_is_judged(self, judged):
        for output in judged.outputs():
            assert not judged.local_output(output).exists()

    def test_the_vmaf_log_is_the_series_the_libvmaf_wrote(self, judged):
        for output in judged.outputs():
            log = vmaf_log(judged, output["run_id"])
            frames = [frame["metrics"] for frame in log["frames"]]

            assert frames
            assert [frame["vmaf"] for frame in frames] == [float(SMOKE_VMAF)] * len(frames)
            assert [frame["float_ssim"] for frame in frames] == [
                pytest.approx(float(SMOKE_VMAF) / 100)
            ] * len(frames)

    def test_the_ffmpeg_log_keeps_the_stderr_of_that_judgement(self, judged):
        for output in judged.outputs():
            written = (judged.results(output["run_id"]) / FFMPEG_LOG).read_text(encoding="utf-8")

            assert "frame=" in written

    def test_nothing_lands_outside_the_prefixes_the_judge_writes(self, judged, list_objects):
        written = set(list_objects(judged, "quality/")) | set(list_objects(judged, "status/judge"))

        assert written == {
            f"quality/results/{output['run_id']}/{artifact}"
            for output in judged.outputs()
            for artifact in ARTIFACTS
        } | {JUDGE_DONE, JUDGE_PROGRESS}


class TestTheJudgement:
    def test_the_cli_of_the_analysis_accepts_it(self, judged):
        for output in judged.outputs():
            written = judged.results(output["run_id"]) / JUDGE_FILENAME
            validated = validate_judgement_with_cli(written)

            assert validated.returncode == 0, validated.stderr

    def test_the_stdlib_checker_of_the_orchestrator_accepts_it(self, judged):
        for output in judged.outputs():
            written = judged.results(output["run_id"]) / JUDGE_FILENAME
            checked = check_judgement_with_stdlib_checker(written)

            assert checked.returncode == 0, checked.stderr

    def test_it_copies_the_entry_of_the_plan_verbatim(self, judged):
        for output in judged.outputs():
            judgement = judged.judgement(output["run_id"])

            assert {name: judgement[name] for name in output} == output

    def test_it_carries_the_provenance_of_the_judge(self, judged):
        for output in judged.outputs():
            judgement = judged.judgement(output["run_id"])

            assert judgement["schema_version"] == "1"
            assert judgement["commit"] == COMMIT
            assert judgement["instance_id"] == JUDGE_INSTANCE_ID
            assert judgement["instance_type"] == JUDGE_INSTANCE_TYPE
            assert judgement["versions"] == VERSIONS

    def test_the_clock_fields_are_offset_aware_and_ordered(self, judged):
        for output in judged.outputs():
            judgement = judged.judgement(output["run_id"])

            assert offset_aware(judgement["started_at"])
            assert offset_aware(judgement["finished_at"])
            assert judgement["started_at"] <= judgement["finished_at"]


class TestProgress:
    def test_one_progress_object_after_each_output(self, judged):
        assert len(progress_versions(judged)) == len(judged.outputs())

    def test_it_goes_up_between_outputs_never_inside_one(self, judged):
        assert copy_kinds(judged) == ["download", "result", "progress"] * len(judged.outputs()) + [
            "done"
        ]

    def test_the_fields_and_their_types_are_the_contract(self, judged):
        for payload in progress_versions(judged):
            assert typed_fields(payload) == PROGRESS_TYPES

    def test_the_index_walks_the_plan(self, judged):
        payloads = progress_versions(judged)
        total = len(judged.outputs())

        assert [payload["output_index"] for payload in payloads] == list(range(1, total + 1))
        assert {payload["output_count"] for payload in payloads} == {total}

    def test_each_one_names_the_output_that_just_ended(self, judged):
        payloads = progress_versions(judged)

        assert [(payload["run_id"], payload["scenario_id"]) for payload in payloads] == [
            (output["run_id"], output["scenario_id"]) for output in judged.outputs()
        ]

    def test_the_counters_follow_the_loop(self, judged):
        payloads = progress_versions(judged)

        assert [payload["runs_total"] for payload in payloads] == list(
            range(1, len(judged.outputs()) + 1)
        )
        assert {payload["runs_failed"] for payload in payloads} == {0}

    def test_runs_failed_rises_on_the_induced_failure(self, judged_with_a_failed_output):
        payloads = progress_versions(judged_with_a_failed_output)

        assert [payload["runs_failed"] for payload in payloads] == [0, 1, 1, 1]

    def test_it_carries_the_identity_it_received(self, judged):
        assert {payload["instance_id"] for payload in progress_versions(judged)} == {
            JUDGE_INSTANCE_ID
        }

    def test_the_clock_fields_are_offset_aware_and_monotonic(self, judged):
        payloads = progress_versions(judged)
        elapsed = [payload["elapsed_seconds"] for payload in payloads]

        assert all(offset_aware(payload["written_at"]) for payload in payloads)
        assert elapsed == sorted(elapsed)

    def test_a_progress_that_does_not_go_up_does_not_end_the_pass(
        self, judged_with_no_progress_upload
    ):
        judged = judged_with_no_progress_upload

        assert judged.returncode == 0, judged.stderr
        assert progress_versions(judged) == []
        assert done_marker(judged)["runs_total"] == len(judged.outputs())


class TestDoneMarker:
    def test_the_fields_and_their_types_are_the_contract(self, judged):
        assert typed_fields(done_marker(judged)) == DONE_MARKER_TYPES

    def test_it_carries_the_identity_the_vigilance_needs(self, judged):
        marker = done_marker(judged)

        assert marker["instance_id"] == JUDGE_INSTANCE_ID
        assert offset_aware(marker["finished_at"])

    def test_the_healthy_pass_closes_clean(self, judged):
        marker = done_marker(judged)

        assert marker["runs_total"] == len(judged.outputs())
        assert marker["runs_failed"] == 0
        assert marker["capped"] is False
        assert marker["exit_status"] == 0
        assert marker["exit_status"] == judged.returncode

    def test_it_is_the_last_object_the_judge_writes(self, judged):
        last = judged.argv("aws")[-1]

        assert last[:2] == ["s3", "cp"]
        assert last[3:] == [f"s3://{BUCKET}/{JUDGE_DONE}"]


class TestFailedOutput:
    def test_the_next_output_is_judged_anyway(self, judged_with_a_failed_output):
        judged = judged_with_a_failed_output

        assert judged_run_ids(judged) == [output["run_id"] for output in judged.outputs()]
        assert exit_codes(judged) == [0, 1, 0, 0]

    def test_the_failed_output_still_leaves_its_judgement_in_the_bucket(
        self, judged_with_a_failed_output
    ):
        judged = judged_with_a_failed_output
        failed = judged.outputs()[1]
        results = judged.results(failed["run_id"])

        # Sem o log do `libvmaf`: o FFmpeg que falhou não terminou de escrevê-lo,
        # e é o `judge.json` que carrega a falha para quem lê o Pass.
        assert {path.name for path in results.iterdir()} == {JUDGE_FILENAME, FFMPEG_LOG}

    def test_the_mkv_of_the_failed_output_is_deleted_too(self, judged_with_a_failed_output):
        judged = judged_with_a_failed_output

        assert not judged.local_output(judged.outputs()[1]).exists()

    def test_the_failure_reaches_the_marker(self, judged_with_a_failed_output):
        judged = judged_with_a_failed_output
        marker = done_marker(judged)

        assert marker["runs_total"] == len(judged.outputs())
        assert marker["runs_failed"] == 1
        assert marker["capped"] is False
        assert marker["exit_status"] == judged.returncode
        assert marker["exit_status"] != 0


class TestFailedResultUpload:
    def test_the_next_output_is_judged_anyway(self, judged_with_no_result_upload):
        judged = judged_with_no_result_upload

        assert judged_run_ids(judged) == [output["run_id"] for output in judged.outputs()]

    def test_that_result_never_reaches_the_bucket_and_the_others_do(
        self, judged_with_no_result_upload
    ):
        judged = judged_with_no_result_upload
        reached = [judged.results(output["run_id"]).is_dir() for output in judged.outputs()]

        assert reached == [True, False, True, True]

    def test_the_judgement_it_wrote_locally_still_says_it_measured(
        self, judged_with_no_result_upload
    ):
        # O `judge.json` é escrito **antes** do upload, então o `exit_code` dele não
        # pode carregar a falha do upload: quem a carrega é o marcador. Narrar isso
        # ao contrário no README foi o que a revisão pegou.
        judged = judged_with_no_result_upload
        written = judged.local_results(judged.outputs()[1]["run_id"]) / JUDGE_FILENAME

        assert json.loads(written.read_text(encoding="utf-8"))["exit_code"] == 0

    def test_the_failure_reaches_the_marker(self, judged_with_no_result_upload):
        judged = judged_with_no_result_upload
        marker = done_marker(judged)

        assert marker["runs_total"] == len(judged.outputs())
        assert marker["runs_failed"] == 1
        assert marker["exit_status"] == judged.returncode
        assert marker["exit_status"] != 0


class TestOutputTimeout:
    def test_the_hung_output_is_killed_and_the_pass_goes_on(self, judged_with_a_hung_output):
        judged = judged_with_a_hung_output
        codes = exit_codes(judged)

        assert judged_run_ids(judged) == [output["run_id"] for output in judged.outputs()]
        assert codes[0] != 0
        assert codes[1:] == [0] * (len(judged.outputs()) - 1)

    def test_the_hung_ffmpeg_does_not_outlive_the_output(self, judged_with_a_hung_output):
        processes = subprocess.run(["ps", "-Ao", "args="], capture_output=True, text=True)

        assert f"sleep {HANG_S}" not in processes.stdout

    def test_the_timeout_is_reported(self, judged_with_a_hung_output):
        judged = judged_with_a_hung_output

        assert "excedeu o timeout de 2s" in judged.stderr
        assert judged.returncode != 0

    def test_the_marker_is_still_written(self, judged_with_a_hung_output):
        marker = done_marker(judged_with_a_hung_output)

        assert marker["runs_failed"] == 1
        assert marker["capped"] is False


class TestTotalTimeout:
    def test_no_output_is_judged(self, judged_over_the_cap):
        judged = judged_over_the_cap

        assert judged.argv_dir.joinpath("ffmpeg.argv").exists() is False
        assert not judged.results(judged.outputs()[0]["run_id"]).exists()

    def test_the_marker_is_still_written(self, judged_over_the_cap, list_objects):
        # Sem ele o Orquestrador espera para sempre por um Juiz que parou.
        marker = done_marker(judged_over_the_cap)

        assert list_objects(judged_over_the_cap, "status/judge") == [JUDGE_DONE]
        assert marker["runs_total"] == 0
        assert marker["capped"] is True
        assert marker["exit_status"] == judged_over_the_cap.returncode
        assert marker["exit_status"] != 0

    def test_the_cap_is_reported(self, judged_over_the_cap):
        assert "teto de 0s atingido antes do output 1" in judged_over_the_cap.stderr
