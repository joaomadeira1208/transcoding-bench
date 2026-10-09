# A casca do `quality.py`, exercitada como **processo**: a árvore de
# `quality/results/` que entra, os dois Parquets que saem, o código de saída e o
# relato. Um `judge.json` inválido tem de derrubar a leitura nomeando o arquivo,
# e a evidência do `preflight` deixada no mesmo prefixo nunca vira julgamento.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from conftest import ROLE_ROOT, make_judgement, make_vmaf_log

CLI = ROLE_ROOT / "quality.py"

ARM = "a" * 64
X86 = "b" * 64


def config_with_three_frames(tmp_path: Path) -> Path:
    """O `config/pilot.toml` com os frames que o log da factory traz."""
    text = (ROLE_ROOT.parent / "config" / "pilot.toml").read_text(encoding="utf-8")
    text = text.replace("frames = 19036", "frames = 3").replace("frames = 17616", "frames = 3")
    path = tmp_path / "pilot.toml"
    path.write_text(text, encoding="utf-8")
    return path


def run_cli(results: Path, config: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--results",
            str(results),
            "--config",
            str(config),
            "--outputs",
            str(out / "outputs.parquet"),
            "--groups",
            str(out / "groups.parquet"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def write_result(
    results: Path,
    instance: str,
    sha256: str,
    *,
    sharers: tuple[str, ...] = (),
    judge: object = None,
    log: str | None = None,
) -> Path:
    """Um `quality/results/{run_id}/` como o Juiz o deixa."""
    run_id = f"{instance}-rep1"
    result_dir = results / run_id
    result_dir.mkdir(parents=True)
    record = make_judgement(
        run_id=run_id,
        scenario_id=f"libx265_1080p_720p_tos_{instance}_rep1",
        instance=instance,
        sha256=sha256,
        shared_by=[
            {
                "instance": each,
                "scenario_id": f"libx265_1080p_720p_tos_{each}_rep1",
                "run_id": f"{each}-rep1",
            }
            for each in (instance, *sharers)
        ],
    )
    (result_dir / "judge.json").write_text(
        judge if isinstance(judge, str) else json.dumps(record, indent=2), encoding="utf-8"
    )
    (result_dir / "vmaf.json").write_text(log or make_vmaf_log(), encoding="utf-8")
    (result_dir / "ffmpeg.log").write_text("frame=    3\n", encoding="utf-8")
    return result_dir


@pytest.fixture
def results(tmp_path: Path) -> Path:
    """O Pass de um grupo: o ARM à parte, Intel e AMD com o mesmo bitstream."""
    tree = tmp_path / "results"
    write_result(tree, "c7g", ARM)
    write_result(tree, "c7i", X86, sharers=("c7a",))
    return tree


@pytest.fixture
def config(tmp_path: Path) -> Path:
    return config_with_three_frames(tmp_path)


@pytest.fixture
def out(tmp_path: Path) -> Path:
    path = tmp_path / "out"
    path.mkdir()
    return path


class TestTheTwoTables:
    def test_it_writes_one_row_per_output_and_one_per_group(self, results, config, out):
        result = run_cli(results, config, out)

        assert result.returncode == 0, result.stderr
        assert pq.read_table(out / "outputs.parquet").column("run_id").to_pylist() == [
            "c7g-rep1",
            "c7i-rep1",
        ]
        (group,) = pq.read_table(out / "groups.parquet").to_pylist()
        assert (group["scenario"], group["bitstreams"], group["equivalent"]) == (
            "libx265_1080p_720p_tos",
            2,
            True,
        )

    def test_it_reports_what_it_read(self, results, config, out):
        result = run_cli(results, config, out)

        assert result.stdout.strip() == (
            "2 outputs, 1 grupos, 0 duplicata(s) fora, 0 com exit_code != 0, "
            "0 com o log inválido ou frames divergentes"
        )

    def test_the_preflight_prefix_is_ignored(self, results, config, out):
        # O `preflight --judge` deixa lá um log sem `judge.json`, e nada o apaga.
        evidence = results / "preflight" / "i-0123456789abcdef0"
        evidence.mkdir(parents=True)
        (evidence / "vmaf.json").write_text("{}", encoding="utf-8")

        result = run_cli(results, config, out)

        assert result.returncode == 0, result.stderr
        assert pq.read_table(out / "outputs.parquet").num_rows == 2

    def test_the_same_tree_gives_the_same_rows_in_the_same_order(self, results, config, tmp_path):
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()

        run_cli(results, config, first)
        run_cli(results, config, second)

        for name in ("outputs.parquet", "groups.parquet"):
            assert pq.read_table(first / name).equals(pq.read_table(second / name))

    def test_an_invalid_log_is_reported_naming_the_run_and_the_file(
        self, results, config, out, tmp_path
    ):
        write_result(results, "c7a", "c" * 64, log=make_vmaf_log((90.0,) * 4))

        result = run_cli(results, config, out)

        assert result.returncode == 0, result.stderr
        assert f"{results}/c7a-rep1/vmaf.json: 4 frames no log, 3 na definição" in result.stderr


class TestRefusals:
    def test_an_invalid_judge_json_brings_the_read_down_naming_the_file(self, results, config, out):
        broken = write_result(results, "c7a", "c" * 64, judge='{"schema_version": "1"}')

        result = run_cli(results, config, out)

        assert result.returncode == 1
        assert f"{broken / 'judge.json'}: run_id" in result.stderr
        assert not (out / "outputs.parquet").exists()

    def test_a_result_without_judge_json_brings_the_read_down(self, results, config, out):
        (results / "c7g-rep1" / "judge.json").unlink()

        result = run_cli(results, config, out)

        assert result.returncode == 1
        assert f"{results / 'c7g-rep1' / 'judge.json'}: ausente" in result.stderr

    def test_a_results_path_that_is_not_a_directory_is_unreadable(self, tmp_path, config, out):
        result = run_cli(tmp_path / "nowhere", config, out)

        assert result.returncode == 2
        assert "não é um diretório" in result.stderr

    def test_an_unreadable_definition_is_unreadable(self, results, tmp_path, out):
        result = run_cli(results, tmp_path / "missing.toml", out)

        assert result.returncode == 2
