# Smoke do triage do Pass de qualidade: o `orchestrator/quality_triage.py` como
# caixa-preta sobre o bucket falso que três laços do `run_all.sh` de verdade
# encheram, um por arquitetura (ADR-0025, ADR-0022). O que se prova aqui é o elo
# que nenhum dos dois papéis pode provar sozinho — que a amostragem é decidida
# sobre os `meta.json` e os `output.sha256` que o bash escreveu, e não sobre uma
# árvore montada pelo teste.

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import (
    BUCKET,
    PILOT,
    PILOT_TOML,
    PLAN_LINE,
    QUALITY_PLAN_FILENAME,
    RUNS_SOURCE,
    Loop,
    Triage,
    block_id_of,
    failed_block_id,
    generate_plan,
    keep_first_record,
    seed_preflight_trail,
    sync_with_the_shim,
)

# As três arquiteturas do `config/pilot.toml`, na ordem em que ele as declara: é
# essa ordem que decide o representante de um bitstream compartilhado (ADR-0025).
# O ARM é o que diverge dos dois x86, que é o que o piloto mediu.
ARM = "c7g"
X86 = ("c7i", "c7a")
ARCHITECTURES = (ARM, *X86)

# O bitstream que só o laço do ARM devolve. Os dois x86 ficam com o default do
# shim: é o que faz o hash deles coincidir sem nenhuma combinação entre os laços.
ARM_BITSTREAM = "arm64"

# O encode que os dois desvios escolhem — o bitstream que diverge e o run que
# falha: o segundo do laço é a primeira Replicação do primeiro bloco. Uma
# Replicação, e não o warm-up, que nem o Pass julga nem a completude conta.
FIRST_REPLICATION_ENCODE = "2"

DIVERGENT_BITSTREAM = "divergente"

META_FILENAME = "meta.json"
HASH_FILENAME = "output.sha256"

# Os filtros do `s3_sync_run_metas_and_hashes`, na ordem em que ele os passa.
RUN_FILTERS = (
    "--exclude",
    "*",
    "--include",
    f"*/{META_FILENAME}",
    "--include",
    f"*/{HASH_FILENAME}",
)

EXIT_REFUSED = 1


def instance_id_of(architecture: str) -> str:
    """Um id por máquina: as três escrevem no mesmo bucket, e dois `meta.json` com
    o mesmo `instance_id` seriam uma máquina só."""
    return f"i-{ARCHITECTURES.index(architecture):017x}"


def instance_type_of(architecture: str) -> str:
    (record,) = [each for each in PILOT["instance"] if each["id"] == architecture]
    return record["instance_type"]


def bitstream_environment(architecture: str) -> dict[str, str]:
    return {"SMOKE_BITSTREAM": ARM_BITSTREAM} if architecture == ARM else {}


def blocks_of(plan: dict[str, Any], architecture: str) -> list[dict[str, Any]]:
    return [block for block in plan["blocks"] if block["instance"] == architecture]


def block_name(block: dict[str, Any]) -> str:
    """O nome do bloco: a `scenario_id` das suas Execuções sem o sufixo."""
    return block_id_of(block["runs"][0]["scenario_id"])


def scenario_of(scenario_id: str) -> str:
    """O Cenário da ADR-0025: a `scenario_id` sem a Replicação e sem a arquitetura."""
    return block_id_of(block_id_of(scenario_id))


def block_scenario(block: dict[str, Any]) -> str:
    return scenario_of(block["runs"][0]["scenario_id"])


def scenarios(plan: dict[str, Any]) -> list[str]:
    """Os Cenários na ordem do canônico, que é a ordem do relatório."""
    return list(dict.fromkeys(block_scenario(block) for block in plan["blocks"]))


def replication_run_ids(loop: Loop) -> dict[str, str]:
    """O `run_id` de cada Replicação, pelo `meta.json` que o bash escreveu."""
    return {
        meta["scenario_id"]: run_id for run_id, meta in loop.metas().items() if not meta["warmup"]
    }


def run_ids_of(loop: Loop, scenario: str) -> list[str]:
    """Os `run_id` das Replicações daquele Cenário no laço, na ordem do canônico.

    A fatia que o laço rodou é a que ele carrega: o bloco e a ordem das
    Replicações saem dela, e não de uma segunda leitura do canônico.
    """
    (block,) = [each for each in loop.plan["blocks"] if block_scenario(each) == scenario]
    run_ids = replication_run_ids(loop)
    return [run_ids[run["scenario_id"]] for run in block["runs"] if not run["warmup"]]


def digests(loop: Loop) -> dict[str, str]:
    """O `output.sha256` que a extração do bitstream escreveu, por `run_id`."""
    return {
        run_dir.name: (run_dir / HASH_FILENAME).read_text(encoding="utf-8").strip()
        for run_dir in loop.run_dirs()
        if (run_dir / HASH_FILENAME).is_file()
    }


def outputs_of(triage: Triage, scenario: str) -> list[dict[str, Any]]:
    """Os outputs daquele Cenário, na ordem em que o plano os traz."""
    return [
        output
        for output in triage.plan()["outputs"]
        if scenario_of(output["scenario_id"]) == scenario
    ]


@pytest.fixture(scope="session")
def one_codec_toml(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """O `config/pilot.toml` reduzido a um codec: dois Cenários nas três arquiteturas.

    A mesma redução do `test_resume.py`, sem recortar `[[instance]]`: o que o Pass
    agrupa é um Cenário atravessando as arquiteturas, e com uma só não haveria
    bitstream compartilhado nem representante a escolher.
    """
    text = keep_first_record(PILOT_TOML.read_text(encoding="utf-8"), "codec")
    path = tmp_path_factory.mktemp("triage-config") / PILOT_TOML.name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def campaign_plan(tmp_path_factory: pytest.TempPathFactory, one_codec_toml: Path) -> dict[str, Any]:
    """O canônico da configuração reduzida, pelo mesmo CLI e do mesmo jeito."""
    return generate_plan(one_codec_toml, tmp_path_factory.mktemp("triage-scenarios"))


@pytest.fixture(scope="session")
def campaign(campaign_plan: dict[str, Any], run_all) -> dict[str, Loop]:
    """Um laço por arquitetura, todos no mesmo bucket falso e na ordem do canônico.

    Um bucket só porque é o que a campanha faz: as três máquinas sobem para
    `runs/` sem saber uma da outra, e quem as reúne é o triage.
    """
    loops: dict[str, Loop] = {}
    s3_root = None
    for architecture in ARCHITECTURES:
        loops[architecture] = run_all(
            campaign_plan,
            blocks_of(campaign_plan, architecture),
            s3_root=s3_root,
            instance_id=instance_id_of(architecture),
            instance_type=instance_type_of(architecture),
            **bitstream_environment(architecture),
        )
        s3_root = loops[architecture].s3_root
    return loops


@pytest.fixture(scope="session")
def triaged(campaign: dict[str, Loop], one_codec_toml: Path, quality_triage) -> Triage:
    """O triage sobre a matriz inteira e sã.

    O rastro é o do primeiro laço porque os três compartilham o bucket; qualquer
    um deles chega ao mesmo lugar.
    """
    return quality_triage(campaign[ARM], one_codec_toml)


@pytest.fixture(scope="session")
def triaged_again(
    triaged: Triage, campaign: dict[str, Loop], one_codec_toml: Path, quality_triage
) -> Triage:
    """Um segundo triage sobre o mesmo bucket, com nada tendo mudado nele."""
    return quality_triage(campaign[ARM], one_codec_toml)


@pytest.fixture(scope="session")
def triaged_with_the_preflight_trail(
    triaged_again: Triage, campaign: dict[str, Loop], one_codec_toml: Path, quality_triage
) -> Triage:
    """O mesmo triage, com o rastro do preflight ao lado dos blocos no bucket.

    Os triages anteriores estão na assinatura porque semear o bucket o altera:
    sem eles, qual dos três vê o rastro passa a depender da ordem de coleta, e a
    comparação entre eles deixa de significar alguma coisa.
    """
    seed_preflight_trail(campaign[ARM], instance_id_of(ARM))
    return quality_triage(campaign[ARM], one_codec_toml)


@pytest.fixture(scope="session")
def divergent_cell(
    triaged_with_the_preflight_trail: Triage,
    campaign: dict[str, Loop],
    campaign_plan: dict[str, Any],
    run_all,
) -> Loop:
    """O laço de um x86 repetido, com uma Replicação devolvendo outro bitstream.

    Repetido sobre o mesmo bucket, e não um bucket à parte: é o que a campanha faz
    depois de uma retomada, e as Execuções novas vencem a dedup por `started_at`.
    O laço é o de um x86 porque o `SMOKE_BITSTREAM_NTH` deixa as outras quatro
    Replicações com o bitstream default do shim, que é o que o outro x86 produziu.
    """
    architecture = X86[0]
    return run_all(
        campaign_plan,
        blocks_of(campaign_plan, architecture),
        s3_root=campaign[architecture].s3_root,
        instance_id=instance_id_of(architecture),
        instance_type=instance_type_of(architecture),
        SMOKE_BITSTREAM=DIVERGENT_BITSTREAM,
        SMOKE_BITSTREAM_NTH=FIRST_REPLICATION_ENCODE,
    )


@pytest.fixture(scope="session")
def divergent_triage(divergent_cell: Loop, one_codec_toml: Path, quality_triage) -> Triage:
    return quality_triage(divergent_cell, one_codec_toml)


@pytest.fixture(scope="session")
def failed_replication(
    divergent_triage: Triage,
    campaign: dict[str, Loop],
    campaign_plan: dict[str, Any],
    run_all,
) -> Loop:
    """O laço do ARM repetido com uma Replicação falhada, depois de tudo o mais.

    Depois porque a falha derruba o Pass inteiro: o triage recusa antes de
    agrupar, e um bucket com ela dentro não responde mais nada sobre bitstreams.
    """
    return run_all(
        campaign_plan,
        blocks_of(campaign_plan, ARM),
        s3_root=campaign[ARM].s3_root,
        instance_id=instance_id_of(ARM),
        instance_type=instance_type_of(ARM),
        SMOKE_BITSTREAM=ARM_BITSTREAM,
        SMOKE_FFMPEG_EXIT="1",
        SMOKE_FFMPEG_NTH=FIRST_REPLICATION_ENCODE,
    )


@pytest.fixture(scope="session")
def refused(failed_replication: Loop, one_codec_toml: Path, quality_triage) -> Triage:
    return quality_triage(failed_replication, one_codec_toml)


class TestReducedCampaign:
    def test_the_plan_is_arch_major_in_the_order_the_toml_declares(self, campaign_plan):
        declared = [block["instance"] for block in campaign_plan["blocks"]]

        assert list(dict.fromkeys(declared)) == list(ARCHITECTURES)

    def test_every_architecture_runs_the_same_two_scenarios(self, campaign_plan):
        named = scenarios(campaign_plan)

        assert len(named) == 2
        assert {
            architecture: [
                block_scenario(block) for block in blocks_of(campaign_plan, architecture)
            ]
            for architecture in ARCHITECTURES
        } == {architecture: named for architecture in ARCHITECTURES}

    def test_each_block_is_the_warmup_and_the_five_replications(self, campaign_plan):
        assert all(len(block["runs"]) == 6 for block in campaign_plan["blocks"])


class TestTheBucketTheLoopsLeft:
    def test_each_architecture_wrote_one_bitstream_for_every_run_of_its_loop(self, campaign):
        assert {
            architecture: len(set(digests(loop).values()))
            for architecture, loop in campaign.items()
        } == dict.fromkeys(ARCHITECTURES, 1)

    def test_the_two_x86_agree_and_the_arm_diverges(self, campaign):
        written = {
            architecture: set(digests(loop).values()) for architecture, loop in campaign.items()
        }

        assert written[X86[0]] == written[X86[1]]
        assert written[ARM].isdisjoint(written[X86[0]])


class TestTheHealthyMatrix:
    def test_the_triage_succeeds_without_warning_about_the_tree_it_read(self, triaged):
        assert triaged.returncode == 0, triaged.stderr
        assert triaged.stderr == ""

    def test_the_report_gives_each_scenario_two_bitstreams_with_the_x86_sharing_one(
        self, triaged, campaign_plan
    ):
        named = scenarios(campaign_plan)

        assert [line.split() for line in triaged.report_lines()[: len(named)]] == [
            [scenario, "2", "bitstreams", ARM, "|", "=".join(X86)] for scenario in named
        ]

    def test_the_histogram_gives_two_bitstreams_for_every_group(self, triaged, campaign_plan):
        groups = len(scenarios(campaign_plan))

        assert triaged.report_lines()[groups] == (
            f"bitstreams distintos por grupo: 2 bitstreams: {groups} grupos"
        )

    def test_no_cell_is_reported_as_divergent(self, triaged):
        assert triaged.report_lines()[-2] == (
            "nenhuma célula divergente: as Replicações de cada Instância são bit-idênticas"
        )

    def test_the_last_line_counts_the_groups_and_the_judgements(self, triaged, campaign_plan):
        groups = len(scenarios(campaign_plan))

        assert triaged.report_lines()[-1] == f"{groups} grupos, {2 * groups} outputs a julgar"

    def test_the_report_names_the_plan_it_wrote(self, triaged):
        written = f"{PLAN_LINE}{triaged.out_dir / QUALITY_PLAN_FILENAME}"

        assert triaged.stdout.splitlines()[-1] == written


class TestThePlan:
    def test_it_judges_the_arm_and_the_first_x86_of_each_scenario(self, triaged, campaign_plan):
        named = scenarios(campaign_plan)

        assert {
            scenario: [output["instance"] for output in outputs_of(triaged, scenario)]
            for scenario in named
        } == {scenario: [ARM, X86[0]] for scenario in named}

    def test_the_representative_is_the_first_replication_of_that_architecture(
        self, triaged, campaign, campaign_plan
    ):
        named = scenarios(campaign_plan)

        assert {
            scenario: [output["run_id"] for output in outputs_of(triaged, scenario)]
            for scenario in named
        } == {
            scenario: [
                run_ids_of(campaign[ARM], scenario)[0],
                run_ids_of(campaign[X86[0]], scenario)[0],
            ]
            for scenario in named
        }

    def test_shared_by_names_every_execution_that_produced_the_bitstream(
        self, triaged, campaign, campaign_plan
    ):
        named = scenarios(campaign_plan)

        assert {
            scenario: [
                [each["run_id"] for each in output["shared_by"]]
                for output in outputs_of(triaged, scenario)
            ]
            for scenario in named
        } == {
            scenario: [
                run_ids_of(campaign[ARM], scenario),
                run_ids_of(campaign[X86[0]], scenario) + run_ids_of(campaign[X86[1]], scenario),
            ]
            for scenario in named
        }

    def test_the_second_x86_shares_the_bitstream_instead_of_being_judged(
        self, triaged, campaign_plan
    ):
        named = scenarios(campaign_plan)

        assert {
            scenario: [
                [each["instance"] for each in output["shared_by"]]
                for output in outputs_of(triaged, scenario)
            ]
            for scenario in named
        } == {scenario: [[ARM] * 5, [X86[0]] * 5 + [X86[1]] * 5] for scenario in named}

    def test_each_output_carries_the_sha256_the_bash_wrote(self, triaged, campaign):
        written = {
            run_id: digest for loop in campaign.values() for run_id, digest in digests(loop).items()
        }
        outputs = triaged.plan()["outputs"]

        assert [output["sha256"] for output in outputs] == [
            written[output["run_id"]] for output in outputs
        ]

    def test_no_output_comes_from_a_divergent_cell(self, triaged):
        outputs = triaged.plan()["outputs"]

        assert [output["cell_divergent"] for output in outputs] == [False] * len(outputs)


class TestSync:
    def test_the_triage_asks_for_the_metas_and_the_hashes(self, triaged):
        (argv,) = triaged.argv("aws")
        *command, destination = argv

        assert command == ["s3", "sync", "--only-show-errors", *RUN_FILTERS, RUNS_SOURCE]
        assert Path(destination).is_absolute()

    def test_that_set_of_filters_brings_one_meta_and_one_hash_per_execution(
        self, shim_bin, triaged, campaign, tmp_path
    ):
        # Contido, e não igual: os laços que os casos adiante repetem sobre este
        # mesmo bucket põem Execuções a mais nele, e uma igualdade aqui passaria
        # a depender da ordem em que o pytest coleta os casos.
        downloaded = sync_with_the_shim(
            shim_bin, campaign[ARM].s3_root, tmp_path / "filtrado", *RUN_FILTERS
        )

        assert {
            f"{run_dir.name}/{name}"
            for loop in campaign.values()
            for run_dir in loop.run_dirs()
            for name in (META_FILENAME, HASH_FILENAME)
        } <= set(downloaded)
        assert {Path(key).name for key in downloaded} == {META_FILENAME, HASH_FILENAME}


class TestTwoTriagesOverTheSameBucket:
    def test_the_plan_comes_out_byte_for_byte_the_same(self, triaged, triaged_again):
        assert triaged_again.plan_bytes() == triaged.plan_bytes()

    def test_so_does_the_report(self, triaged, triaged_again):
        assert triaged_again.report_lines() == triaged.report_lines()


class TestThePreflightTrail:
    def test_the_report_and_the_plan_do_not_change(self, triaged_with_the_preflight_trail, triaged):
        assert triaged_with_the_preflight_trail.report_lines() == triaged.report_lines()
        assert triaged_with_the_preflight_trail.plan_bytes() == triaged.plan_bytes()

    def test_no_execution_is_reported_as_missing_its_meta(self, triaged_with_the_preflight_trail):
        assert triaged_with_the_preflight_trail.returncode == 0
        assert triaged_with_the_preflight_trail.stderr == ""

    def test_the_filters_leave_the_trail_in_the_bucket(
        self, triaged_with_the_preflight_trail, shim_bin, campaign, tmp_path
    ):
        downloaded = sync_with_the_shim(
            shim_bin, campaign[ARM].s3_root, tmp_path / "com-rastro", *RUN_FILTERS
        )

        assert not any(key.startswith("preflight/") for key in downloaded)


class TestTheDivergentCell:
    def test_the_triage_still_succeeds(self, divergent_triage):
        assert divergent_triage.returncode == 0, divergent_triage.stderr

    def test_the_report_gives_that_scenario_a_third_bitstream(
        self, divergent_triage, campaign_plan
    ):
        block = blocks_of(campaign_plan, X86[0])[0]
        scenario = block_scenario(block)
        lines = divergent_triage.report_lines()

        assert [line.split() for line in lines if line.startswith(scenario)] == [
            [scenario, "3", "bitstreams", ARM, "|", X86[0], "|", "=".join(X86)]
        ]

    def test_the_report_names_the_cell(self, divergent_triage, campaign_plan):
        block = blocks_of(campaign_plan, X86[0])[0]
        lines = divergent_triage.report_lines()

        assert "células divergentes, julgadas por inteiro (1):" in lines
        assert f"  {block_name(block)}" in lines

    def test_the_extra_hash_becomes_one_more_output(self, divergent_triage, triaged):
        assert len(divergent_triage.plan()["outputs"]) == len(triaged.plan()["outputs"]) + 1

    def test_the_extra_output_is_the_replication_that_diverged(
        self, divergent_triage, divergent_cell, campaign_plan
    ):
        scenario = block_scenario(blocks_of(campaign_plan, X86[0])[0])
        replications = run_ids_of(divergent_cell, scenario)
        extra = outputs_of(divergent_triage, scenario)[1]

        assert extra["run_id"] == replications[0]
        assert extra["sha256"] == digests(divergent_cell)[replications[0]]
        assert [each["run_id"] for each in extra["shared_by"]] == [replications[0]]

    def test_the_other_four_replications_keep_the_bitstream_of_the_second_x86(
        self, divergent_triage, divergent_cell, campaign, campaign_plan
    ):
        scenario = block_scenario(blocks_of(campaign_plan, X86[0])[0])
        shared = outputs_of(divergent_triage, scenario)[2]

        assert shared["run_id"] == run_ids_of(divergent_cell, scenario)[1]
        assert [each["run_id"] for each in shared["shared_by"]] == (
            run_ids_of(divergent_cell, scenario)[1:] + run_ids_of(campaign[X86[1]], scenario)
        )

    def test_only_the_outputs_of_that_cell_are_marked(self, divergent_triage, campaign_plan):
        divergent, intact = (
            block_scenario(blocks_of(campaign_plan, X86[0])[index]) for index in (0, 1)
        )

        assert [output["cell_divergent"] for output in outputs_of(divergent_triage, divergent)] == [
            False,
            True,
            True,
        ]
        assert [output["cell_divergent"] for output in outputs_of(divergent_triage, intact)] == [
            False,
            False,
        ]


class TestTheFailedReplication:
    def test_the_triage_refuses(self, refused):
        assert refused.returncode == EXIT_REFUSED

    def test_it_says_the_pass_only_decides_over_complete_blocks(self, refused):
        assert refused.stderr.splitlines()[0] == (
            "matriz incompleta: o Pass de qualidade só decide sobre blocos completos"
        )

    def test_it_names_the_block_of_the_failed_run(self, refused, failed_replication):
        assert f"  {failed_block_id(failed_replication)}  com falha" in refused.stderr.splitlines()

    def test_it_points_at_the_resume_that_resolves_it(self, refused, one_codec_toml):
        assert refused.stderr.splitlines()[-1] == (
            f"python orchestrator/resume.py --config {one_codec_toml} "
            f"--bucket {BUCKET} --out <diretório novo>"
        )

    def test_no_plan_is_written(self, refused):
        assert not refused.out_dir.exists()
