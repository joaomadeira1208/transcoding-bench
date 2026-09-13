# Smoke da retomada: o `orchestrator/resume.py` como caixa-preta sobre o bucket
# falso que o `run_all.sh` de verdade escreveu (ADR-0012, ADR-0022). O que se
# prova aqui é o elo que nenhum dos dois papéis pode provar sozinho — que a
# completude é decidida sobre os `meta.json` que o bash escreveu, e não sobre uma
# árvore montada pelo teste.

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    BUCKET,
    INSTANCE_ID,
    PILOT_TOML,
    Loop,
    Resume,
    generate_plan,
    shim_environment,
)

# A arquitetura e o codec que sobram na configuração reduzida: os primeiros
# declarados no `config/pilot.toml`. A arquitetura é a do `INSTANCE_TYPE` com que
# os dois laços rodam — um bloco de outra faria o `meta.json` discordar dele.
INSTANCE = "c7g"

SLICE_NAME = f"{INSTANCE}.json"

RUNS_SOURCE = f"s3://{BUCKET}/runs/"

SLICE_LINE = "fatia reduzida: "

# O par de filtros do `s3_sync_run_metas`, na ordem em que ele os passa.
META_FILTERS = ("--exclude", "*", "--include", "*/meta.json")

PREFLIGHT_ARTIFACTS = {
    "perf.json": '{"counter-value": "1234567", "event": "cycles"}\n',
    "perf.stderr.txt": "Performance counter stats for 'ffmpeg':\n",
}

# O run que falha é o segundo encode do laço — a primeira Replicação do primeiro
# bloco. Uma Replicação, e não o warm-up: o warm-up não entra na completude, e um
# bloco com ele falhado sairia completo.
FAILED_ENCODE = "2"

# Um cabeçalho de tabela de topo do TOML: `[[codec]]` e `[experiment]`, mas não
# `[video.geometry]` nem `[[instrumentation.metric]]`, que pertencem ao registro
# aberto acima deles.
TOP_LEVEL_TABLE = re.compile(r"^\[\[?[^.\]]+\]\]?$")


def keep_first_record(text: str, table: str) -> str:
    """O TOML sem o segundo `[[table]]` em diante, e o resto do arquivo intacto."""
    kept: list[str] = []
    header = f"[[{table}]]"
    seen = 0
    dropping = False
    for line in text.splitlines(keepends=True):
        if TOP_LEVEL_TABLE.match(line.strip()):
            seen += line.strip() == header
            dropping = line.strip() == header and seen > 1
        if not dropping:
            kept.append(line)
    return "".join(kept)


def block_id_of(scenario_id: str) -> str:
    """O nome do bloco: a `scenario_id` sem o sufixo da Execução."""
    return scenario_id.rpartition("_")[0]


def failed_block_id(loop: Loop) -> str:
    """O bloco do run que falhou, pelo `meta.json` que o bash escreveu."""
    (failed,) = [meta for meta in loop.metas().values() if meta["exit_code"] != 0]
    return block_id_of(failed["scenario_id"])


def sync_with_the_shim(shim_bin: Path, s3_root: Path, workdir: Path, *filters: str) -> list[str]:
    """O `s3 sync` do shim invocado direto sobre `runs/`: o que desceu, relativo
    ao destino."""
    workdir.mkdir()
    destination = workdir / "down"
    subprocess.run(
        [str(shim_bin / "aws"), "s3", "sync", RUNS_SOURCE, str(destination), *filters],
        env=shim_environment(shim_bin, workdir, {}, s3_root=s3_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(
        str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()
    )


def report_lines(resume: Resume) -> list[str]:
    """O relatório sem as linhas que nomeiam as fatias escritas.

    O `--out` é um diretório novo a cada invocação, e o caminho que ele imprime é
    a única parte da saída que duas retomadas sobre o mesmo bucket não têm como
    ter igual.
    """
    return [line for line in resume.stdout.splitlines() if not line.startswith(SLICE_LINE)]


@pytest.fixture(scope="session")
def two_blocks_toml(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """O `config/pilot.toml` reduzido a um codec e a uma arquitetura: dois blocos.

    Reduzido, e não escrito à mão, para não virar uma terceira definição do
    Experimento (ADR-0019); por que são dois blocos está no README do papel.
    """
    text = PILOT_TOML.read_text(encoding="utf-8")
    for table in ("codec", "instance"):
        text = keep_first_record(text, table)
    path = tmp_path_factory.mktemp("resume-config") / "pilot.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def two_blocks_plan(
    tmp_path_factory: pytest.TempPathFactory, two_blocks_toml: Path
) -> dict[str, Any]:
    """O canônico da configuração reduzida, pelo mesmo CLI e do mesmo jeito."""
    return generate_plan(two_blocks_toml, tmp_path_factory.mktemp("resume-scenarios"))


@pytest.fixture(scope="session")
def failed_loop(two_blocks_plan: dict[str, Any], run_all) -> Loop:
    """Os dois blocos, com uma Replicação do primeiro falhada."""
    return run_all(
        two_blocks_plan,
        two_blocks_plan["blocks"],
        SMOKE_FFMPEG_EXIT="1",
        SMOKE_FFMPEG_NTH=FAILED_ENCODE,
    )


@pytest.fixture(scope="session")
def healthy_loop(two_blocks_plan: dict[str, Any], run_all) -> Loop:
    """Os mesmos dois blocos, inteiros e sem falha nenhuma."""
    return run_all(two_blocks_plan, two_blocks_plan["blocks"])


@pytest.fixture(scope="session")
def failed_resume(failed_loop: Loop, two_blocks_toml: Path, resume) -> Resume:
    return resume(failed_loop, two_blocks_toml)


@pytest.fixture(scope="session")
def healthy_resume(healthy_loop: Loop, two_blocks_toml: Path, resume) -> Resume:
    return resume(healthy_loop, two_blocks_toml)


@pytest.fixture(scope="session")
def resume_with_the_preflight_trail(
    failed_loop: Loop, failed_resume: Resume, two_blocks_toml: Path, resume
) -> Resume:
    """A mesma retomada, com o rastro do preflight ao lado dos blocos no bucket.

    O `failed_resume` é pedido porque semear o bucket o altera: sem ele na
    assinatura, qual das duas retomadas vê o rastro passa a depender da ordem de
    coleta, e a comparação entre as duas deixa de significar alguma coisa.
    """
    preflight = failed_loop.bucket_dir() / "runs" / "preflight" / INSTANCE_ID
    preflight.mkdir(parents=True)
    for name, content in PREFLIGHT_ARTIFACTS.items():
        (preflight / name).write_text(content, encoding="utf-8")
    return resume(failed_loop, two_blocks_toml)


class TestReducedCampaign:
    def test_it_is_two_blocks_of_one_architecture(self, two_blocks_plan):
        blocks = two_blocks_plan["blocks"]

        assert len(blocks) == 2
        assert {block["instance"] for block in blocks} == {INSTANCE}
        assert all(len(block["runs"]) == 6 for block in blocks)


class TestFailedBlock:
    def test_the_resume_succeeds(self, failed_resume):
        assert failed_resume.returncode == 0, failed_resume.stderr

    def test_the_report_counts_one_complete_and_one_pending(self, failed_resume):
        assert report_lines(failed_resume)[0] == f"{INSTANCE}: 1/2 blocos completos, 1 pendentes"

    def test_the_report_names_the_block_of_the_failed_run(self, failed_resume, failed_loop):
        assert report_lines(failed_resume)[1] == f"  {failed_block_id(failed_loop)}  com falha"

    def test_one_slice_for_the_only_architecture_with_a_pending_block(self, failed_resume):
        assert list(failed_resume.slices()) == [SLICE_NAME]

    def test_the_report_names_the_slice_it_wrote(self, failed_resume):
        written = f"{SLICE_LINE}{failed_resume.out_dir / SLICE_NAME}"

        assert failed_resume.stdout.splitlines()[-1] == written

    def test_the_slice_carries_that_block_whole(self, failed_resume, failed_loop, two_blocks_plan):
        pending = next(
            block
            for block in two_blocks_plan["blocks"]
            if block_id_of(block["runs"][0]["scenario_id"]) == failed_block_id(failed_loop)
        )

        assert failed_resume.slices()[SLICE_NAME] == {**two_blocks_plan, "blocks": [pending]}

    def test_the_block_comes_back_with_the_six_runs_of_the_canonical(self, failed_resume):
        (reduced,) = failed_resume.slices()[SLICE_NAME]["blocks"]

        assert len(reduced["runs"]) == 6
        assert [run["warmup"] for run in reduced["runs"]] == [True, *[False] * 5]


class TestHealthyBlocks:
    def test_there_is_nothing_to_resume(self, healthy_resume):
        assert healthy_resume.returncode == 0, healthy_resume.stderr
        assert report_lines(healthy_resume)[0] == f"{INSTANCE}: 2/2 blocos completos, 0 pendentes"
        assert report_lines(healthy_resume)[-1] == "nada pendente: não há o que retomar"

    def test_no_slice_is_written(self, healthy_resume):
        assert healthy_resume.slices() == {}


class TestSync:
    def test_the_resume_asks_for_the_metas_and_nothing_else(self, failed_resume):
        (argv,) = failed_resume.argv("aws")
        *command, destination = argv

        assert command == ["s3", "sync", "--only-show-errors", *META_FILTERS, RUNS_SOURCE]
        assert Path(destination).is_absolute()

    def test_that_pair_of_filters_brings_one_meta_per_execution(
        self, shim_bin, failed_loop, tmp_path
    ):
        downloaded = sync_with_the_shim(
            shim_bin, failed_loop.s3_root, tmp_path / "filtrado", *META_FILTERS
        )

        assert downloaded == [f"{run_dir.name}/meta.json" for run_dir in failed_loop.run_dirs()]

    def test_the_last_pattern_that_matches_is_the_one_that_decides(
        self, shim_bin, failed_loop, tmp_path
    ):
        # O mesmo par na ordem trocada não quer dizer a mesma coisa: o
        # `--exclude '*'` depois do include apaga o include, e nada desce.
        inverted = (*META_FILTERS[2:], *META_FILTERS[:2])

        assert (
            sync_with_the_shim(shim_bin, failed_loop.s3_root, tmp_path / "trocado", *inverted) == []
        )

    def test_an_empty_prefix_brings_nothing_and_succeeds(self, shim_bin, tmp_path):
        # O bucket da campanha que morreu antes do primeiro upload.
        s3_root = tmp_path / "s3"
        (s3_root / BUCKET).mkdir(parents=True)

        assert sync_with_the_shim(shim_bin, s3_root, tmp_path / "vazio") == []

    def test_the_other_direction_is_refused(self, shim_bin, failed_loop, tmp_path):
        workdir = tmp_path / "subida"
        workdir.mkdir()
        refused = subprocess.run(
            [str(shim_bin / "aws"), "s3", "sync", str(workdir), RUNS_SOURCE],
            env=shim_environment(shim_bin, workdir, {}, s3_root=failed_loop.s3_root),
            capture_output=True,
            text=True,
            check=False,
        )

        assert refused.returncode != 0
        assert "não é shimado" in refused.stderr


class TestPreflightTrail:
    def test_the_report_and_the_slices_do_not_change(
        self, resume_with_the_preflight_trail, failed_resume
    ):
        assert report_lines(resume_with_the_preflight_trail) == report_lines(failed_resume)
        assert resume_with_the_preflight_trail.slices() == failed_resume.slices()

    def test_no_execution_is_reported_as_missing_its_meta(self, resume_with_the_preflight_trail):
        assert resume_with_the_preflight_trail.returncode == 0
        assert resume_with_the_preflight_trail.stderr == ""

    def test_the_trail_stays_in_the_bucket(self, resume_with_the_preflight_trail, failed_loop):
        preflight = failed_loop.bucket_dir() / "runs" / "preflight" / INSTANCE_ID

        assert {path.name for path in preflight.iterdir()} == set(PREFLIGHT_ARTIFACTS)
