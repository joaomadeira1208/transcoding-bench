# Smoke do `analysis/consolidate.py`: a árvore que o laço acabou de produzir,
# consolidada pela CLI de verdade. É o único lugar em que os parsers dos quatro
# artefatos encontram texto que não foi escrito por eles — o `perf.json` linha a
# linha, o `time.json` do format string, a `pidstat.txt` delimitada por espaço e
# o `-stats` no stderr do FFmpeg.

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from conftest import COMMIT, EXPERIMENT, INSTANCE_TYPE, Loop, consolidate_with_cli

PMU_EVENTS = EXPERIMENT["instrumentation"]["pmu_events"]


def perf_column(event: str) -> str:
    return f"perf_{event.replace('-', '_')}"


@pytest.fixture(scope="session")
def consolidation(loop: Loop, tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("consolidated") / "runs.parquet"
    result = consolidate_with_cli(loop.runs_dir, out)
    assert result.returncode == 0, result.stderr
    return result, pq.read_table(out)


@pytest.fixture(scope="session")
def table(consolidation):
    return consolidation[1]


def replications(block: dict[str, Any]) -> list[str]:
    return sorted(run["scenario_id"] for run in block["runs"] if not run["warmup"])


class TestTable:
    def test_a_block_becomes_five_rows(self, table, block):
        assert table.num_rows == 5
        assert table.column("scenario_id").to_pylist() == replications(block)

    def test_the_warmup_is_out_by_the_field(self, table, loop):
        # O bash ecoou `warmup` verbatim e a `scenario_id` do descartado termina
        # em `_warmup`: quem filtrou foi o campo, e o sufixo é só debug humano.
        warmups = [meta for meta in loop.metas().values() if meta["warmup"]]

        assert len(warmups) == 1
        assert warmups[0]["scenario_id"] not in table.column("scenario_id").to_pylist()

    def test_the_run_metadata_comes_from_the_meta_json(self, table, loop):
        replicated = {run_id for run_id, meta in loop.metas().items() if not meta["warmup"]}

        assert set(table.column("run_id").to_pylist()) == replicated
        assert set(table.column("commit").to_pylist()) == {COMMIT}
        assert set(table.column("instance_type").to_pylist()) == {INSTANCE_TYPE}
        assert set(table.column("exit_code").to_pylist()) == {0}

    def test_the_report_counts_the_five_rows_and_the_warmup(self, consolidation):
        result, _ = consolidation

        assert "5 linhas" in result.stdout
        assert "1 warm-ups fora" in result.stdout
        assert "0 com exit_code != 0" in result.stdout
        # Nenhum dos quatro parsers tropeçou no texto que os shims escreveram.
        assert "0 artefatos ilegíveis" in result.stdout
        assert result.stderr == ""


class TestParsedArtifacts:
    def test_every_pmu_event_of_the_experiment_has_a_column_with_a_value(self, table):
        # A ponte entre o `experiment.toml` e a tabela: trocar um evento lá sem
        # trocar a coluna aqui deixaria a métrica vazia para a campanha inteira,
        # e o `perf stat` não falha quando o evento não existe (ADR-0006).
        for event in PMU_EVENTS:
            column = table.column(perf_column(event)).to_pylist()

            assert column == [1234567.0] * 5, event

    def test_the_time_aggregates_are_columns(self, table):
        assert table.column("time_elapsed_s").to_pylist() == [0.0] * 5
        assert table.column("time_max_rss_kb").to_pylist() == [0] * 5

    def test_the_ffmpeg_stats_come_from_the_stderr(self, table):
        assert table.column("ffmpeg_frames").to_pylist() == [120] * 5
        assert table.column("ffmpeg_fps").to_pylist() == [24.0] * 5
        assert table.column("ffmpeg_bitrate_kbps").to_pylist() == [2021.4] * 5

    def test_the_four_derived_metrics(self, table):
        assert table.column("ipc").to_pylist() == [1.0] * 5
        assert table.column("cache_miss_rate").to_pylist() == [1.0] * 5
        assert table.column("branch_mispredict_rate").to_pylist() == [1.0] * 5
        assert table.column("cpu_pct_avg").to_pylist() == [98.0] * 5

    def test_the_pidstat_series_stays_in_the_raw_dir(self, table, loop):
        # Só o agregado atravessa (ADR-0007): a série continua onde o `pidstat`
        # a escreveu, e a tabela tem uma linha por Execução, não por amostra.
        samples = [
            line
            for run_dir in loop.run_dirs()
            for line in (run_dir / "pidstat.txt").read_text(encoding="utf-8").splitlines()
            if not line.startswith("#")
        ]

        assert samples
        assert table.num_rows == 5
        assert "cpu_pct_avg" in table.column_names


class TestSha256:
    def test_the_bitstream_hash_is_preserved_per_execution(self, table, loop):
        # A divergência cross-arch é re-derivável pelos hashes sem os `.mkv`
        # (ADR-0007), e é por isso que ele é coluna e não só arquivo.
        hashes = {
            (run_dir / "output.sha256").read_text(encoding="utf-8").strip()
            for run_dir in loop.run_dirs()
        }

        assert set(table.column("output_sha256").to_pylist()) == hashes


def test_an_empty_tree_consolidates_to_zero_rows(tmp_path: Path):
    result = consolidate_with_cli(tmp_path, tmp_path / "vazio.parquet")

    assert result.returncode == 0, result.stderr
    assert "0 linhas" in result.stdout
