# Smoke do `orchestrator.py clean`: a retenção como caixa-preta sobre o bucket
# falso que três laços do encode encheram e o Juiz julgou. É o lugar em que um
# erro custa o dado do artigo; o porquê está no `smoke/README.md`.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import ARM, BUCKET, QUALITY_PLAN_FILENAME, Clean, Pass, Triage

JUDGE_DONE = "status/judge_done"

APPLY = "--apply"

SECOND_JUDGEMENT = "2"


def output_key(run_id: str) -> str:
    return f"runs/{run_id}/output.mkv"


def outputs_in(objects: dict[str, Any]) -> set[str]:
    return {key for key in objects if key.startswith("runs/") and key.endswith("/output.mkv")}


def copies_of(output: dict[str, Any]) -> set[str]:
    return {output_key(each["run_id"]) for each in output["shared_by"]} - {
        output_key(output["run_id"])
    }


def failing_removal(triaged: Triage) -> str:
    """A cópia cujo `s3 rm` o caminho da remoção falha induz."""
    return min(copies_of(triaged.plan()["outputs"][0]))


def removals(cleaned: Clean) -> list[list[str]]:
    return [argv for argv in cleaned.argv("aws") if argv[:2] == ["s3", "rm"]]


def decision_of(cleaned: Clean) -> str:
    return cleaned.stdout


@pytest.fixture(scope="session")
def plan_path(triaged: Triage) -> Path:
    return triaged.out_dir / QUALITY_PLAN_FILENAME


@pytest.fixture(scope="session")
def judged(triaged, campaign, run_quality) -> Pass:
    """O Pass inteiro e são sobre o plano do triage."""
    return run_quality(campaign[ARM], triaged.plan())


@pytest.fixture(scope="session")
def judged_with_a_failed_output(triaged, campaign, run_quality) -> Pass:
    """O segundo julgamento falha; os outros três seguem bem."""
    return run_quality(
        campaign[ARM], triaged.plan(), SMOKE_FFMPEG_EXIT="1", SMOKE_FFMPEG_NTH=SECOND_JUDGEMENT
    )


@pytest.fixture(scope="session")
def dry_run(judged, plan_path, clean) -> Clean:
    return clean(judged, plan_path)


@pytest.fixture(scope="session")
def applied(judged, plan_path, clean) -> Clean:
    return clean(judged, plan_path, APPLY)


@pytest.fixture(scope="session")
def applied_over_a_failed_judgement(judged_with_a_failed_output, plan_path, clean) -> Clean:
    return clean(judged_with_a_failed_output, plan_path, APPLY)


@pytest.fixture(scope="session")
def applied_without_judge_done(judged, plan_path, clean) -> Clean:
    return clean(judged, plan_path, APPLY, prepare=lambda bucket: (bucket / JUDGE_DONE).unlink())


@pytest.fixture(scope="session")
def applied_with_an_invalid_judge_done(judged, plan_path, clean) -> Clean:
    return clean(
        judged, plan_path, APPLY, prepare=lambda bucket: (bucket / JUDGE_DONE).write_text("{}")
    )


@pytest.fixture(scope="session")
def applied_with_a_failed_removal(judged, plan_path, triaged, clean) -> Clean:
    """O `s3 rm` de uma cópia falha; os outros seguem."""
    return clean(judged, plan_path, APPLY, SMOKE_AWS_FAIL_KEY=failing_removal(triaged))


class TestWithoutApply:
    def test_it_succeeds(self, dry_run):
        assert dry_run.returncode == 0, dry_run.stderr

    def test_nothing_disappears_and_nothing_changes(self, dry_run):
        assert dry_run.objects() == dry_run.before

    def test_no_s3_rm_is_issued(self, dry_run):
        assert removals(dry_run) == []

    def test_the_bucket_is_never_listed_beyond_the_marker(self, dry_run):
        listings = [
            argv for argv in dry_run.argv("aws") if argv[:2] == ["s3api", "list-objects-v2"]
        ]

        assert [argv[argv.index("--prefix") + 1] for argv in listings] == [JUDGE_DONE]

    def test_the_decision_names_every_key_it_would_delete(self, dry_run, triaged):
        doomed = outputs_in(dry_run.before) - {
            output_key(output["run_id"]) for output in triaged.plan()["outputs"]
        }

        for key in doomed:
            assert f"  {key}" in dry_run.stdout.splitlines()

    def test_the_decision_counts_what_stays_and_what_goes(self, dry_run):
        assert dry_run.stdout.splitlines()[-1] == "4 a manter, 32 a apagar"

    def test_it_says_nothing_was_deleted(self, dry_run):
        assert "nada foi apagado" in dry_run.stderr


class TestWithApply:
    def test_it_succeeds(self, applied):
        assert applied.returncode == 0, applied.stderr

    def test_exactly_one_output_per_judged_bitstream_survives(self, applied, triaged):
        assert outputs_in(applied.objects()) == {
            output_key(output["run_id"]) for output in triaged.plan()["outputs"]
        }

    def test_what_disappears_is_only_the_output_of_warmups_and_copies(self, applied, triaged):
        warmups = {
            key
            for key in outputs_in(applied.before)
            if json.loads(applied.before[key.replace("output.mkv", "meta.json")])["warmup"]
        }
        copies = set().union(*(copies_of(output) for output in triaged.plan()["outputs"]))

        assert len(warmups) == 6
        assert applied.removed() == warmups | copies

    def test_every_other_artifact_is_byte_identical(self, applied):
        after = applied.objects()

        assert {key: applied.before[key] for key in after} == after

    def test_one_s3_rm_per_key_and_only_under_runs(self, applied):
        targets = [argv[-1] for argv in removals(applied)]

        assert sorted(targets) == sorted(f"s3://{BUCKET}/{key}" for key in applied.removed())

    def test_the_decision_is_the_one_printed_without_apply(self, applied, dry_run):
        assert decision_of(applied) == decision_of(dry_run)


class TestAFailedJudgement:
    def test_it_succeeds(self, applied_over_a_failed_judgement):
        assert applied_over_a_failed_judgement.returncode == 0, (
            applied_over_a_failed_judgement.stderr
        )

    def test_every_copy_of_that_bitstream_stays(self, applied_over_a_failed_judgement, triaged):
        failed = triaged.plan()["outputs"][1]

        survivors = outputs_in(applied_over_a_failed_judgement.objects())

        assert {output_key(each["run_id"]) for each in failed["shared_by"]} <= survivors

    def test_the_copies_of_the_other_bitstreams_still_go(
        self, applied_over_a_failed_judgement, triaged
    ):
        healthy = [output for index, output in enumerate(triaged.plan()["outputs"]) if index != 1]

        for output in healthy:
            assert copies_of(output) <= applied_over_a_failed_judgement.removed()


class TestWithoutJudgeDone:
    def test_it_refuses(self, applied_without_judge_done):
        assert applied_without_judge_done.returncode != 0

    def test_the_refusal_names_the_marker(self, applied_without_judge_done):
        assert JUDGE_DONE in applied_without_judge_done.stderr

    def test_nothing_is_deleted(self, applied_without_judge_done):
        assert applied_without_judge_done.objects() == applied_without_judge_done.before
        assert removals(applied_without_judge_done) == []


class TestAnInvalidJudgeDone:
    def test_it_refuses_naming_the_marker(self, applied_with_an_invalid_judge_done):
        assert applied_with_an_invalid_judge_done.returncode != 0
        assert JUDGE_DONE in applied_with_an_invalid_judge_done.stderr

    def test_nothing_is_deleted(self, applied_with_an_invalid_judge_done):
        assert applied_with_an_invalid_judge_done.objects() == (
            applied_with_an_invalid_judge_done.before
        )
        assert removals(applied_with_an_invalid_judge_done) == []


class TestAFailedRemoval:
    def test_it_ends_non_zero(self, applied_with_a_failed_removal):
        assert applied_with_a_failed_removal.returncode != 0

    def test_the_failure_is_named(self, applied_with_a_failed_removal, triaged):
        doomed = failing_removal(triaged)

        assert doomed in applied_with_a_failed_removal.stderr
        assert doomed in applied_with_a_failed_removal.objects()

    def test_the_other_keys_are_deleted_anyway(
        self, applied_with_a_failed_removal, applied, triaged
    ):
        assert applied_with_a_failed_removal.removed() == applied.removed() - {
            failing_removal(triaged)
        }
