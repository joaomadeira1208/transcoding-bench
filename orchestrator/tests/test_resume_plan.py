# O núcleo da retomada é o que decide o que uma campanha interrompida vai pagar de
# novo. Um bloco lido como completo quando não está sai do experimento como buraco
# silencioso; um lido como pendente quando está completo custa ~40 min de compute.
# Por isso a completude é asserida sobre `meta.json` montados pela factory do
# papel, e não sobre a crença de que a regra da ADR-0019 foi transcrita certo.

from __future__ import annotations

from typing import Any

import pytest
from conftest import make_instance, make_meta, real_config, real_pilot_config
from experiment_config import validate_config
from resume_plan import Pending, block_id, reduced_slices, render_report, resume
from scenario_plan import build_canonical_plan, build_instance_slices, serialize_plan

EXCLUDED_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def plan_of(raw_config: dict[str, Any]) -> dict[str, Any]:
    return build_canonical_plan(validate_config(raw_config))


def two_architectures() -> list[dict[str, Any]]:
    """Dois registros `[[instance]]`: é o que faz o plano ter mais de uma fatia."""
    return [make_instance(), make_instance(id="c7i", instance_type="c7i.xlarge", arch="x86_64")]


def block_of(plan: dict[str, Any], index: int = 0) -> dict[str, Any]:
    return plan["blocks"][index]


def block_metas(
    block: dict[str, Any], *, attempt: int = 0, **overrides: Any
) -> list[dict[str, Any]]:
    """Os seis `meta.json` que a Instância deixa ao rodar um bloco inteiro.

    `attempt` desloca o instante e o `run_id`: uma segunda tentativa do mesmo
    bloco é outro conjunto de Execuções sobre as mesmas `scenario_id`.
    """
    return [
        make_meta(
            **{
                "scenario_id": run["scenario_id"],
                "warmup": run["warmup"],
                "run_id": f"{attempt}{index}0c4a2e-6b41-4d5f-8a37-2f1c8de0b7a4",
                "started_at": f"2026-08-0{8 + attempt}T1{index}:00:00+00:00",
                **overrides,
            }
        )
        for index, run in enumerate(block["runs"])
    ]


def reason_by_block(resumes) -> dict[str, Pending]:
    return {block.block_id: block.reason for each in resumes for block in each.pending}


def complete_blocks(resumes) -> set[str]:
    return {name for each in resumes for name in each.complete}


@pytest.fixture
def one_block(make_raw_config):
    """Um plano de um bloco só e os `meta.json` de uma execução inteira dele."""
    plan = plan_of(make_raw_config(pair=[{"input_res": "1080p", "output_res": "1080p"}]))
    return plan, block_of(plan)


class TestCompleteness:
    def test_five_replications_with_exit_zero_is_complete(self, one_block):
        plan, block = one_block

        resumes = resume(plan, block_metas(block))

        assert complete_blocks(resumes) == {block_id(block)}
        assert reason_by_block(resumes) == {}

    def test_four_replications_is_pending_as_partial(self, one_block):
        plan, block = one_block
        metas = block_metas(block)

        resumes = resume(plan, metas[:-1])

        assert reason_by_block(resumes) == {block_id(block): Pending.PARTIAL}

    def test_one_failed_replication_is_pending_as_failed(self, one_block):
        plan, block = one_block
        metas = block_metas(block)
        metas[3]["exit_code"] = 1

        resumes = resume(plan, metas)

        assert reason_by_block(resumes) == {block_id(block): Pending.FAILED}

    def test_the_warmup_alone_does_not_count_as_a_replication(self, one_block):
        plan, block = one_block
        warmups = [meta for meta in block_metas(block) if meta["warmup"]]

        resumes = resume(plan, warmups)

        assert reason_by_block(resumes) == {block_id(block): Pending.MISSING}

    def test_a_failed_warmup_does_not_make_a_complete_block_pending(self, one_block):
        plan, block = one_block
        metas = block_metas(block)
        metas[0]["exit_code"] = 1

        resumes = resume(plan, metas)

        assert complete_blocks(resumes) == {block_id(block)}

    def test_no_meta_at_all_is_every_block_missing(self, make_raw_config):
        plan = plan_of(make_raw_config())

        resumes = resume(plan, [])

        assert reason_by_block(resumes) == {
            block_id(block): Pending.MISSING for block in plan["blocks"]
        }

    def test_a_complete_block_next_to_a_pending_one(self, make_raw_config):
        plan = plan_of(make_raw_config())
        done, missing = plan["blocks"]

        resumes = resume(plan, block_metas(done))

        assert complete_blocks(resumes) == {block_id(done)}
        assert reason_by_block(resumes) == {block_id(missing): Pending.MISSING}


class TestDeduplication:
    def test_the_later_instant_wins_against_the_lexicographic_order(self, one_block):
        plan, block = one_block
        # `18:00-03:00` é 21:00 UTC, uma hora **depois** de `20:00+00:00`, e ordena
        # antes dele como string: é aqui que comparar a string daria o veredito
        # oposto ao do instante (ADR-0019).
        earlier = block_metas(block, exit_code=1, started_at="2026-08-08T20:00:00+00:00")
        later = block_metas(block, exit_code=0, started_at="2026-08-08T18:00:00-03:00")

        assert reason_by_block(resume(plan, earlier + later)) == {}
        assert reason_by_block(resume(plan, later + earlier)) == {}

    def test_the_earlier_instant_loses_even_when_it_succeeded(self, one_block):
        plan, block = one_block
        succeeded = block_metas(block, exit_code=0)
        failed = block_metas(block, attempt=1, exit_code=1)

        resumes = resume(plan, succeeded + failed)

        assert reason_by_block(resumes) == {block_id(block): Pending.FAILED}

    def test_an_aborted_block_redone_counts_once(self, one_block):
        plan, block = one_block
        aborted = block_metas(block, exit_code=1)[:3]
        redone = block_metas(block, attempt=1)

        resumes = resume(plan, aborted + redone)

        assert complete_blocks(resumes) == {block_id(block)}

    def test_the_run_id_breaks_the_tie_regardless_of_reading_order(self, one_block):
        plan, block = one_block
        same_instant = "2026-08-08T10:00:00+00:00"
        losers = block_metas(block, exit_code=1, started_at=same_instant)
        winners = block_metas(
            block,
            exit_code=0,
            started_at=same_instant,
            run_id="ffffffff-6b41-4d5f-8a37-2f1c8de0b7a4",
        )

        assert reason_by_block(resume(plan, losers + winners)) == {}
        assert reason_by_block(resume(plan, winners + losers)) == {}


class TestExcludedCommit:
    def test_a_complete_block_from_an_excluded_commit_becomes_pending(self, one_block):
        plan, block = one_block

        resumes = resume(
            plan, block_metas(block, commit=EXCLUDED_COMMIT), excluded_commits=[EXCLUDED_COMMIT]
        )

        assert reason_by_block(resumes) == {block_id(block): Pending.EXCLUDED}

    def test_a_single_excluded_replication_is_enough(self, one_block):
        plan, block = one_block
        metas = block_metas(block)
        metas[-1]["commit"] = EXCLUDED_COMMIT

        resumes = resume(plan, metas, excluded_commits=[EXCLUDED_COMMIT])

        assert reason_by_block(resumes) == {block_id(block): Pending.EXCLUDED}

    def test_an_excluded_commit_that_only_the_warmup_carries_changes_nothing(self, one_block):
        plan, block = one_block
        metas = block_metas(block)
        metas[0]["commit"] = EXCLUDED_COMMIT

        resumes = resume(plan, metas, excluded_commits=[EXCLUDED_COMMIT])

        assert complete_blocks(resumes) == {block_id(block)}

    def test_an_excluded_commit_that_lost_the_dedup_changes_nothing(self, one_block):
        plan, block = one_block
        contaminated = block_metas(block, commit=EXCLUDED_COMMIT)
        redone = block_metas(block, attempt=1)

        resumes = resume(plan, contaminated + redone, excluded_commits=[EXCLUDED_COMMIT])

        assert complete_blocks(resumes) == {block_id(block)}

    def test_excluding_a_commit_that_ran_nothing_changes_nothing(self, one_block):
        plan, block = one_block

        resumes = resume(plan, block_metas(block), excluded_commits=[EXCLUDED_COMMIT])

        assert complete_blocks(resumes) == {block_id(block)}


class TestReducedSlices:
    def test_a_pending_block_comes_back_whole(self, one_block):
        plan, block = one_block
        metas = block_metas(block)[:-1]

        slices = reduced_slices(plan, resume(plan, metas))

        assert list(slices) == ["c7g"]
        assert slices["c7g"]["blocks"] == [block]
        assert len(slices["c7g"]["blocks"][0]["runs"]) == 6

    def test_the_slice_keeps_the_top_shape_of_a_slice(self, one_block):
        plan, _ = one_block

        reduced = reduced_slices(plan, resume(plan, []))["c7g"]

        assert reduced.keys() == build_instance_slices(plan)["c7g"].keys()
        assert reduced["schema_version"] == plan["schema_version"]
        assert reduced["seed"] == plan["seed"]

    def test_only_the_pending_blocks_enter_and_in_the_canonical_order(self, make_raw_config):
        plan = plan_of(make_raw_config())
        done = plan["blocks"][0]

        reduced = reduced_slices(plan, resume(plan, block_metas(done)))["c7g"]

        assert [block_id(block) for block in reduced["blocks"]] == [
            block_id(block) for block in plan["blocks"] if block is not done
        ]

    def test_an_architecture_without_pendencies_gets_no_file(self, make_raw_config):
        plan = plan_of(
            make_raw_config(
                pair=[{"input_res": "1080p", "output_res": "1080p"}],
                instance=two_architectures(),
            )
        )
        done = [block for block in plan["blocks"] if block["instance"] == "c7i"]

        slices = reduced_slices(plan, resume(plan, block_metas(done[0])))

        assert list(slices) == ["c7g"]

    def test_nothing_pending_gives_no_slice_at_all(self, one_block):
        plan, block = one_block

        assert reduced_slices(plan, resume(plan, block_metas(block))) == {}

    @pytest.mark.parametrize("load", [real_config, real_pilot_config], ids=["campaign", "pilot"])
    def test_without_any_meta_the_reduced_slices_are_the_original_ones(self, load):
        plan = build_canonical_plan(load())

        reduced = reduced_slices(plan, resume(plan, []))

        assert {name: serialize_plan(each) for name, each in reduced.items()} == {
            name: serialize_plan(each) for name, each in build_instance_slices(plan).items()
        }


class TestReport:
    def test_every_architecture_of_the_plan_gets_a_line(self, make_raw_config):
        plan = plan_of(make_raw_config(instance=two_architectures()))

        report = render_report(resume(plan, []))

        assert "c7g" in report
        assert "c7i" in report

    def test_a_pending_block_is_named_with_its_reason(self, one_block):
        plan, block = one_block
        metas = block_metas(block)
        metas[2]["exit_code"] = 1

        report = render_report(resume(plan, metas))

        assert block_id(block) in report
        assert Pending.FAILED.value in report

    def test_a_complete_block_is_not_named_one_by_one(self, one_block):
        plan, block = one_block

        report = render_report(resume(plan, block_metas(block)))

        assert block_id(block) not in report
