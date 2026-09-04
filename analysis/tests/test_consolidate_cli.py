# A casca do `consolidate.py`, exercitada como **processo**: o que se verifica é
# o contrato com quem a invoca — a árvore que entra, o Parquet que sai, o código
# de saída e o relato. Um `meta.json` inválido tem de derrubar a consolidação
# nomeando o arquivo, e nenhuma linha errada pode entrar em silêncio.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq
from conftest import (
    ROLE_ROOT,
    make_ffmpeg_log,
    make_meta,
    make_perf_json,
    make_pidstat,
    make_time_json,
)

CLI = ROLE_ROOT / "consolidate.py"

SCENARIO = "libx264_2160p_1080p_bbb_c7g"


def run_cli(runs: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "--runs", str(runs), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )


def write_run(runs: Path, run_id: str, meta: object = None, **overrides: object) -> Path:
    """Um `runs/{run_id}/` com os seis artefatos, como a Instância o deixa."""
    run_dir = runs / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        json.dumps(meta if meta is not None else make_meta(run_id=run_id, **overrides), indent=2),
        encoding="utf-8",
    )
    (run_dir / "time.json").write_text(make_time_json(), encoding="utf-8")
    (run_dir / "perf.json").write_text(make_perf_json(), encoding="utf-8")
    (run_dir / "pidstat.txt").write_text(make_pidstat(), encoding="utf-8")
    (run_dir / "ffmpeg.log").write_text(make_ffmpeg_log(), encoding="utf-8")
    (run_dir / "output.sha256").write_text("a3f1c0de\n", encoding="utf-8")
    return run_dir


def write_block(runs: Path, **overrides: object) -> Path:
    """A árvore de um bloco: o warm-up mais as cinco Replicações (ADR-0003)."""
    for index, suffix in enumerate(("warmup", "rep1", "rep2", "rep3", "rep4", "rep5")):
        write_run(
            runs,
            f"0000000{index}-6b41-4d5f-8a37-2f1c8de0b7a4",
            scenario_id=f"{SCENARIO}_{suffix}",
            warmup=suffix == "warmup",
            started_at=f"2026-08-08T1{index}:00:00+00:00",
            **overrides,
        )
    return runs


def test_a_block_becomes_five_rows(tmp_path):
    runs = write_block(tmp_path / "runs")
    out = tmp_path / "runs.parquet"

    result = run_cli(runs, out)

    assert result.returncode == 0, result.stderr
    table = pq.read_table(out)
    assert table.num_rows == 5
    assert table.column("scenario_id").to_pylist() == [f"{SCENARIO}_rep{n}" for n in range(1, 6)]
    assert "5 linhas" in result.stdout


def test_consolidating_twice_gives_the_same_table(tmp_path):
    # Determinismo é de conteúdo e ordem, nunca de bytes: o `pyarrow` grava
    # metadado próprio, e prometer `diff` seria promessa falsa (decisão D15).
    runs = write_block(tmp_path / "runs")
    first, second = tmp_path / "first.parquet", tmp_path / "second.parquet"

    assert run_cli(runs, first).returncode == 0
    assert run_cli(runs, second).returncode == 0

    assert pq.read_table(first).equals(pq.read_table(second))


def test_the_report_counts_the_failed_rows(tmp_path):
    runs = write_block(tmp_path / "runs")
    write_run(
        runs,
        "99999999-6b41-4d5f-8a37-2f1c8de0b7a4",
        scenario_id=f"{SCENARIO}_rep5",
        started_at="2026-08-08T20:00:00+00:00",
        exit_code=70,
    )

    result = run_cli(runs, tmp_path / "runs.parquet")

    assert result.returncode == 0, result.stderr
    assert "1 com exit_code != 0" in result.stdout
    assert "1 warm-ups fora" in result.stdout
    assert "1 duplicatas fora" in result.stdout


def test_an_unreadable_artifact_is_reported_and_the_table_is_written(tmp_path):
    # Só o `meta.json` derruba a consolidação: um `perf.json` ilegível num run
    # que terminou bem é relatado nomeando o arquivo, e as outras linhas seguem.
    runs = write_block(tmp_path / "runs")
    (runs / "00000003-6b41-4d5f-8a37-2f1c8de0b7a4" / "perf.json").write_text(
        "sem contador nenhum\n", encoding="utf-8"
    )
    out = tmp_path / "runs.parquet"

    result = run_cli(runs, out)

    assert result.returncode == 0, result.stderr
    assert "00000003-6b41-4d5f-8a37-2f1c8de0b7a4/perf.json" in result.stderr
    assert "1 artefatos ilegíveis" in result.stdout
    assert pq.read_table(out).num_rows == 5


def test_an_invalid_meta_stops_the_consolidation_naming_the_file(tmp_path):
    runs = write_block(tmp_path / "runs")
    offender = write_run(
        runs,
        "88888888-6b41-4d5f-8a37-2f1c8de0b7a4",
        meta=make_meta(scenario_id=f"{SCENARIO}_rep6", warmup="false"),
    )
    out = tmp_path / "runs.parquet"

    result = run_cli(runs, out)

    assert result.returncode != 0
    assert str(offender / "meta.json") in result.stderr
    assert "warmup" in result.stderr
    assert not out.exists()


def test_a_run_without_meta_stops_the_consolidation(tmp_path):
    runs = write_block(tmp_path / "runs")
    (runs / "sem-meta").mkdir()

    result = run_cli(runs, tmp_path / "runs.parquet")

    assert result.returncode != 0
    assert "sem-meta" in result.stderr


def test_a_runs_directory_that_is_not_there(tmp_path):
    result = run_cli(tmp_path / "ausente", tmp_path / "runs.parquet")

    assert result.returncode != 0
    assert "ausente" in result.stderr


def test_an_out_path_that_cannot_be_written(tmp_path):
    runs = write_block(tmp_path / "runs")

    result = run_cli(runs, tmp_path / "ausente" / "runs.parquet")

    assert result.returncode != 0
    assert "runs.parquet" in result.stderr
