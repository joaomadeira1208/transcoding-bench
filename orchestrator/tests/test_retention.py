# A retenção é a única operação destrutiva da pipeline, e um erro aqui custa o
# dado do artigo: um `output.mkv` apagado não volta. O predicado da ADR-0007 é
# asserido sobre `meta.json` da factory, hashes atribuídos pelo teste, o plano
# que o triage de verdade escreve sobre eles e `judge.json` da factory.

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import make_instance, make_judgement, make_judgement_json, make_meta
from experiment_config import validate_config
from judgement_check import JUDGE_FILENAME, JudgementError
from quality_plan import build_plan, triage
from retention import (
    CleanError,
    Verdict,
    decide,
    deletions,
    read_judgements,
    render_decision,
    require_finished_pass,
)
from scenario_plan import build_canonical_plan

FIRST_STARTED_AT = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)

LATE = "2026-09-14T12:00:00+00:00"


def three_architectures() -> list[dict[str, Any]]:
    return [
        make_instance(),
        make_instance(id="c7i", instance_type="c7i.xlarge", arch="x86_64"),
        make_instance(id="c7a", instance_type="c7a.xlarge", arch="x86_64"),
    ]


def sha_of(*parts: str) -> str:
    return hashlib.sha256("_".join(parts).encode()).hexdigest()


def scenario_of(run: Mapping[str, Any]) -> str:
    stem, _, _ = run["scenario_id"].rpartition("_")
    stem, _, _ = stem.rpartition("_")
    return stem


def x86_together(run: Mapping[str, Any]) -> str:
    """O caso comum do piloto: Intel e AMD iguais, ARM à parte."""
    return sha_of(scenario_of(run), "c7g" if run["instance"] == "c7g" else "x86")


def everywhere_the_same(run: Mapping[str, Any]) -> str:
    """O mesmo bitstream em todo Cenário: só o Cenário separa uma cópia da outra."""
    return sha_of("constant")


class Bucket:
    """O que o `clean` lê do bucket, montado sobre o plano canônico de uma definição."""

    def __init__(
        self,
        make_raw_config,
        digest: Callable[[Mapping[str, Any]], str] = x86_together,
        **overrides: Any,
    ) -> None:
        self.config = validate_config(
            make_raw_config(**{"instance": three_architectures(), **overrides})
        )
        self.canonical = build_canonical_plan(self.config)
        self.metas: list[dict[str, Any]] = []
        self.hashes: dict[str, str] = {}
        runs = [run for block in self.canonical["blocks"] for run in block["runs"]]
        for index, run in enumerate(runs):
            run_id = f"{index:08x}-6b41-4d5f-8a37-2f1c8de0b7a4"
            self.metas.append(
                make_meta(
                    scenario_id=run["scenario_id"],
                    warmup=run["warmup"],
                    run_id=run_id,
                    instance=run["instance"],
                    started_at=(FIRST_STARTED_AT + timedelta(seconds=index)).isoformat(),
                )
            )
            self.hashes[run_id] = digest(run)

    def triage(self) -> dict[str, Any]:
        """O plano que o triage de verdade escreve sobre este bucket."""
        return build_plan(self.config, triage(self.canonical, self.metas, self.hashes))

    def meta(self, scenario_id: str) -> dict[str, Any]:
        (meta,) = [each for each in self.metas if each["scenario_id"] == scenario_id]
        return meta

    def add(self, scenario_id: str, *, started_at: str, digest: str | None, **fields: Any) -> str:
        """Mais uma Execução daquele `scenario_id`, como a retomada a deixa."""
        run_id = f"{len(self.metas):08x}-0000-4000-8000-000000000000"
        self.metas.append(
            make_meta(scenario_id=scenario_id, run_id=run_id, started_at=started_at, **fields)
        )
        if digest is not None:
            self.hashes[run_id] = digest
        return run_id


def judged(plan: Mapping[str, Any], exit_code: int = 0, **per_run_id: int) -> dict[str, Any]:
    """Um `judge.json` por output do plano; `per_run_id` troca o `exit_code` de um."""
    return {
        output["run_id"]: make_judgement(
            run_id=output["run_id"],
            sha256=output["sha256"],
            exit_code=per_run_id.get(output["run_id"], exit_code),
            finished_at=LATE,
        )
        for output in plan["outputs"]
    }


def key_of(run_id: str, container: str = "mkv") -> str:
    return f"runs/{run_id}/output.{container}"


def verdicts(decision) -> dict[str, Verdict]:
    return {each.key: each.verdict for each in decision}


def verdict_of(decision, bucket: Bucket, scenario_id: str) -> Verdict:
    return verdicts(decision)[key_of(bucket.meta(scenario_id)["run_id"])]


@pytest.fixture
def bucket(make_raw_config) -> Bucket:
    return Bucket(make_raw_config)


class TestTheJudgedBitstream:
    def test_the_representative_is_kept(self, bucket):
        plan = bucket.triage()

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        for output in plan["outputs"]:
            assert verdicts(decision)[key_of(output["run_id"])] is Verdict.KEEP_JUDGED

    def test_one_output_survives_per_judged_bitstream(self, bucket):
        plan = bucket.triage()

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        kept = [each.key for each in decision if not each.verdict.deletes]
        assert sorted(kept) == sorted(key_of(output["run_id"]) for output in plan["outputs"])

    def test_every_other_copy_in_the_same_scenario_is_deleted(self, bucket):
        plan = bucket.triage()

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        assert verdict_of(decision, bucket, "libx264_1080p_1080p_bbb_c7g_rep2") is (
            Verdict.DELETE_COPY
        )
        assert verdict_of(decision, bucket, "libx264_1080p_1080p_bbb_c7a_rep1") is (
            Verdict.DELETE_COPY
        )

    def test_the_shared_by_list_is_exactly_what_goes(self, bucket):
        plan = bucket.triage()

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        copies = {each.key for each in decision if each.verdict is Verdict.DELETE_COPY}
        assert copies == {
            key_of(sharer["run_id"])
            for output in plan["outputs"]
            for sharer in output["shared_by"]
            if sharer["run_id"] != output["run_id"]
        }

    def test_the_same_hash_in_another_scenario_is_not_a_copy(self, make_raw_config):
        bucket = Bucket(make_raw_config, everywhere_the_same)
        plan = bucket.triage()
        other = next(o for o in plan["outputs"] if "_1080p_720p_" in o["scenario_id"])

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan, **{other["run_id"]: 1}))

        assert verdict_of(decision, bucket, "libx264_1080p_720p_bbb_c7i_rep3") is (
            Verdict.KEEP_JUDGEMENT_FAILED
        )
        assert verdict_of(decision, bucket, "libx264_1080p_1080p_bbb_c7i_rep3") is (
            Verdict.DELETE_COPY
        )


class TestWhatIsAlwaysDeleted:
    def test_every_warmup(self, bucket):
        plan = bucket.triage()

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan, exit_code=1))

        warmups = [meta for meta in bucket.metas if meta["warmup"]]
        assert warmups
        for meta in warmups:
            assert verdicts(decision)[key_of(meta["run_id"])] is Verdict.DELETE_WARMUP

    def test_a_failed_run(self, bucket):
        plan = bucket.triage()
        failed = bucket.add(
            "libx264_1080p_1080p_bbb_c7i_rep2",
            started_at="2020-01-01T00:00:00+00:00",
            digest=None,
            exit_code=1,
        )

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        assert verdicts(decision)[key_of(failed)] is Verdict.DELETE_FAILED

    def test_a_run_superseded_by_the_dedup(self, bucket):
        plan = bucket.triage()
        superseded = bucket.add(
            "libx264_1080p_1080p_bbb_c7i_rep2",
            started_at="2020-01-01T00:00:00+00:00",
            digest=sha_of("an unjudged bitstream"),
        )

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        assert verdicts(decision)[key_of(superseded)] is Verdict.DELETE_SUPERSEDED

    def test_even_when_the_bitstream_was_never_judged(self, bucket):
        plan = bucket.triage()

        decision = decide(plan, bucket.metas, bucket.hashes, {})

        deleted = {each.verdict for each in decision if each.verdict.deletes}
        assert deleted == {Verdict.DELETE_WARMUP}


class TestWhatIsKeptWhole:
    def test_a_bitstream_without_judgement_keeps_every_copy(self, bucket):
        plan = bucket.triage()

        decision = decide(plan, bucket.metas, bucket.hashes, {})

        replications = [meta for meta in bucket.metas if not meta["warmup"]]
        for meta in replications:
            assert verdicts(decision)[key_of(meta["run_id"])] is Verdict.KEEP_UNJUDGED

    def test_a_bitstream_judged_with_failure_keeps_every_copy(self, bucket):
        plan = bucket.triage()
        failed = plan["outputs"][0]

        decision = decide(
            plan, bucket.metas, bucket.hashes, judged(plan, **{failed["run_id"]: 234})
        )

        for sharer in failed["shared_by"]:
            assert verdicts(decision)[key_of(sharer["run_id"])] is Verdict.KEEP_JUDGEMENT_FAILED

    def test_the_failure_of_one_bitstream_does_not_spare_the_copies_of_another(self, bucket):
        plan = bucket.triage()
        failed, healthy = plan["outputs"][0], plan["outputs"][1]

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan, **{failed["run_id"]: 1}))

        assert {
            verdicts(decision)[key_of(sharer["run_id"])]
            for sharer in healthy["shared_by"]
            if sharer["run_id"] != healthy["run_id"]
        } == {Verdict.DELETE_COPY}

    def test_a_bitstream_outside_the_plan_keeps_every_copy(self, bucket):
        plan = bucket.triage()
        newer = bucket.add(
            "libx264_1080p_1080p_bbb_c7i_rep2",
            started_at=LATE,
            digest=sha_of("a bitstream the triage never saw"),
            instance="c7i",
        )

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        assert verdicts(decision)[key_of(newer)] is Verdict.KEEP_UNJUDGED

    def test_a_winner_without_output_sha256(self, bucket):
        plan = bucket.triage()
        meta = bucket.meta("libx264_1080p_1080p_bbb_c7a_rep4")
        del bucket.hashes[meta["run_id"]]

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        assert verdicts(decision)[key_of(meta["run_id"])] is Verdict.KEEP_NO_HASH


class TestTheKeysItTouches:
    def test_only_the_output_of_each_run_and_only_under_runs(self, bucket):
        plan = bucket.triage()

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        assert [each.key for each in decision] == [key_of(meta["run_id"]) for meta in bucket.metas]

    def test_the_extension_is_the_container_the_run_declared(self, bucket):
        plan = bucket.triage()
        warmup = next(meta for meta in bucket.metas if meta["warmup"])
        warmup["container"] = "mp4"

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        assert verdicts(decision)[key_of(warmup["run_id"], "mp4")] is Verdict.DELETE_WARMUP

    @pytest.mark.parametrize("container", ["", "../meta.json", "mkv/x", 7, None])
    def test_a_container_that_is_not_an_extension_is_refused(self, bucket, container):
        plan = bucket.triage()
        warmup = next(meta for meta in bucket.metas if meta["warmup"])
        warmup["container"] = container

        with pytest.raises(CleanError, match=warmup["run_id"]):
            decide(plan, bucket.metas, bucket.hashes, judged(plan))

    def test_a_meta_without_container_is_refused(self, bucket):
        plan = bucket.triage()
        warmup = next(meta for meta in bucket.metas if meta["warmup"])
        del warmup["container"]

        with pytest.raises(CleanError, match="container"):
            decide(plan, bucket.metas, bucket.hashes, judged(plan))

    def test_deletions_are_the_keys_the_decision_deletes_in_its_order(self, bucket):
        plan = bucket.triage()

        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        assert deletions(decision) == [each.key for each in decision if each.verdict.deletes]


class TestAPlanThatIsNotThisBucket:
    def test_a_representative_absent_from_the_bucket_is_refused(self, bucket):
        plan = bucket.triage()
        absent = plan["outputs"][0]["run_id"]
        bucket.metas = [meta for meta in bucket.metas if meta["run_id"] != absent]

        with pytest.raises(CleanError, match=absent):
            decide(plan, bucket.metas, bucket.hashes, judged(plan))

    def test_a_representative_whose_hash_changed_is_refused(self, bucket):
        plan = bucket.triage()
        changed = plan["outputs"][0]["run_id"]
        bucket.hashes[changed] = sha_of("another bitstream")

        with pytest.raises(CleanError, match=changed):
            decide(plan, bucket.metas, bucket.hashes, judged(plan))

    def test_a_judgement_of_another_bitstream_is_refused(self, bucket):
        plan = bucket.triage()
        judgements = judged(plan)
        run_id = plan["outputs"][0]["run_id"]
        judgements[run_id]["sha256"] = sha_of("another bitstream")

        with pytest.raises(CleanError, match=run_id):
            decide(plan, bucket.metas, bucket.hashes, judgements)


class TestTheFinishedPass:
    def test_a_marker_after_every_judgement_is_accepted(self, bucket):
        require_finished_pass("2026-09-14T12:00:01+00:00", judged(bucket.triage()))

    def test_a_marker_at_the_same_second_as_the_last_judgement_is_accepted(self, bucket):
        require_finished_pass(LATE, judged(bucket.triage()))

    def test_the_offset_is_honoured(self, bucket):
        require_finished_pass("2026-09-14T09:00:00-03:00", judged(bucket.triage()))

    def test_a_marker_older_than_a_judgement_is_the_previous_pass(self, bucket):
        judgements = judged(bucket.triage())
        newest = next(iter(judgements))
        judgements[newest]["finished_at"] = "2026-09-14T12:30:00+00:00"

        with pytest.raises(CleanError, match=newest):
            require_finished_pass(LATE, judgements)

    def test_no_judgement_at_all_is_not_a_refusal_here(self):
        require_finished_pass(LATE, {})


class TestTheRenderedDecision:
    def test_each_reason_counts_and_lists_its_keys(self, bucket):
        plan = bucket.triage()
        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))

        rendered = render_decision(decision).splitlines()

        for verdict in {each.verdict for each in decision}:
            keys = [each.key for each in decision if each.verdict is verdict]
            header = next(line for line in rendered if verdict.value in line)
            assert f"({len(keys)})" in header
            start = rendered.index(header) + 1
            assert rendered[start : start + len(keys)] == [f"  {key}" for key in keys]

    def test_the_last_line_is_the_total_kept_and_deleted(self, bucket):
        plan = bucket.triage()
        decision = decide(plan, bucket.metas, bucket.hashes, judged(plan))
        kept = sum(not each.verdict.deletes for each in decision)

        last = render_decision(decision).splitlines()[-1]

        assert last == f"{kept} a manter, {len(decision) - kept} a apagar"

    def test_a_reason_without_keys_has_no_section(self, bucket):
        plan = bucket.triage()
        decision = decide(plan, bucket.metas, bucket.hashes, {})

        assert Verdict.DELETE_COPY.value not in render_decision(decision)


def write_judgement(results: Path, directory: str, raw: str) -> None:
    (results / directory).mkdir(parents=True)
    (results / directory / JUDGE_FILENAME).write_text(raw, encoding="utf-8")


class TestTheJudgementsTree:
    def test_each_judgement_comes_back_by_its_run_id(self, tmp_path):
        run_id = "4b1d8e07-2c36-4a59-9f80-51ac7e2d6b43"
        write_judgement(tmp_path, run_id, make_judgement_json(run_id=run_id))

        assert read_judgements(tmp_path) == {run_id: make_judgement(run_id=run_id)}

    def test_a_result_without_judgement_is_not_a_judgement(self, tmp_path):
        (tmp_path / "4b1d8e07-2c36-4a59-9f80-51ac7e2d6b43").mkdir()

        assert read_judgements(tmp_path) == {}

    def test_the_preflight_evidence_is_not_a_judgement(self, tmp_path):
        write_judgement(tmp_path, "preflight/i-0123456789abcdef0", make_judgement_json())

        assert read_judgements(tmp_path) == {}

    def test_an_empty_tree_is_nothing(self, tmp_path):
        assert read_judgements(tmp_path / "never-synced") == {}

    def test_an_invalid_judgement_is_refused_naming_the_key(self, tmp_path):
        run_id = "4b1d8e07-2c36-4a59-9f80-51ac7e2d6b43"
        write_judgement(tmp_path, run_id, make_judgement_json(run_id=run_id, exit_code="0"))

        with pytest.raises(
            JudgementError, match=f"quality/results/{run_id}/judge.json: .*exit_code"
        ):
            read_judgements(tmp_path)

    def test_a_judgement_under_another_run_id_is_refused(self, tmp_path):
        write_judgement(tmp_path, "4b1d8e07-2c36-4a59-9f80-51ac7e2d6b43", make_judgement_json())

        with pytest.raises(JudgementError, match="run_id"):
            read_judgements(tmp_path)

    def test_bytes_that_are_not_json_are_refused(self, tmp_path):
        write_judgement(tmp_path, "x", json.dumps([1])[:-1])

        with pytest.raises(JudgementError, match=re.escape("quality/results/x/judge.json")):
            read_judgements(tmp_path)
