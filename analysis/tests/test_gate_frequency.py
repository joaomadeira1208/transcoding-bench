# A frequência efetiva é o que separou, na campanha, o núcleo da `c7i` do relógio
# dela (ADR-0026): ~3,6 GHz nas primeiras ~20 h e ~3,0 GHz depois. As falhas
# caladas são o `task-clock` lido em segundos, que dá frequências mil vezes
# menores e plausíveis lado a lado; o tempo de parede no lugar do de CPU, que dá
# a soma dos quatro núcleos; e a janela contada do começo de cada Instância, que
# alinha a mudança de uma com o começo da outra.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest
from conftest import (
    CONFIG_DIR,
    ROLE_ROOT,
    make_ffmpeg_log,
    make_meta,
    make_perf_json,
    make_pidstat,
    make_time_json,
)
from gate import effective_ghz, frequency_by_instance, frequency_over_launch

GATE = ROLE_ROOT / "gate.py"
CONSOLIDATE = ROLE_ROOT / "consolidate.py"


def runs(*rows: tuple[str, str, float, float]) -> pd.DataFrame:
    """`(instance, started_at, cycles, task_clock_ms)` por Replicação."""
    return pd.DataFrame(
        rows, columns=["instance", "started_at", "perf_cycles", "perf_task_clock"]
    ).assign(started_at=lambda df: pd.to_datetime(df.started_at, utc=True))


class TestTheEffectiveFrequency:
    def test_cycles_over_cpu_milliseconds_is_gigahertz(self):
        # 3,6e9 ciclos em 1000 ms de CPU: um núcleo a 3,6 GHz por um segundo.
        df = runs(("c7i", "2026-10-01T00:00:00+00:00", 3.6e9, 1000.0))

        assert effective_ghz(df).tolist() == [pytest.approx(3.6)]

    def test_the_quantiles_are_per_instance(self):
        df = runs(
            *(("c7i", "2026-10-01T00:00:00+00:00", ghz * 1e9, 1000.0) for ghz in (3.0, 3.6)),
            *(("c7a", "2026-10-01T00:00:00+00:00", 3.7e9, 1000.0) for _ in range(2)),
        )

        table = frequency_by_instance(df)

        assert list(table.columns) == ["p5", "p50", "p95"]
        assert table.loc["c7a"].tolist() == [3.7, 3.7, 3.7]
        assert table.loc["c7i", "p50"] == pytest.approx(3.3)

    def test_the_windows_count_from_the_first_run_of_the_launch(self):
        # A `c7a` começa 7 h depois: contada do começo dela, a primeira hora da
        # `c7a` cairia na janela 0, ao lado da primeira hora da `c7i`.
        df = runs(
            ("c7i", "2026-10-01T00:00:00+00:00", 3.6e9, 1000.0),
            ("c7i", "2026-10-01T13:00:00+00:00", 3.0e9, 1000.0),
            ("c7a", "2026-10-01T07:00:00+00:00", 3.7e9, 1000.0),
        )

        table = frequency_over_launch(df, window_hours=6)

        assert table.index.tolist() == [0, 6, 12]
        assert table.loc[0, "c7i"] == pytest.approx(3.6)
        assert table.loc[6, "c7a"] == pytest.approx(3.7)
        assert table.loc[12, "c7i"] == pytest.approx(3.0)
        assert pd.isna(table.loc[0, "c7a"])


# Um bloco da `c7g-pmu` como a Instância o deixa: o `perf.json` só com os eventos
# do `contadores.toml`, os quadros do Big Buck Bunny e os quatro núcleos ocupados.
REDUCED_COUNTERS = {
    "cycles": 2_600_000_000_000,
    "instructions": 6_000_000_000_000,
    "task-clock": 1_000_000.0,
    "context-switches": 1204,
    "cpu-migrations": 12,
    "page-faults": 183422,
}
BBB_FRAMES = 19036


def write_reduced_block(runs_dir: Path) -> None:
    for index, suffix in enumerate(("warmup", "rep1", "rep2", "rep3", "rep4", "rep5")):
        run_id = f"0000000{index}-6b41-4d5f-8a37-2f1c8de0b7a4"
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True)
        meta = make_meta(
            run_id=run_id,
            scenario_id=f"libx264_1080p_720p_bbb_c7g-pmu_{suffix}",
            warmup=suffix == "warmup",
            input_res="1080p",
            output_res="720p",
            master="bbb_1080p.mkv",
            output_width=1280,
            output_height=720,
            instance="c7g-pmu",
            instance_type="c7g.xlarge",
            started_at=f"2026-10-27T0{index}:00:00+00:00",
            finished_at=f"2026-10-27T0{index}:30:00+00:00",
        )
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (run_dir / "time.json").write_text(make_time_json(), encoding="utf-8")
        (run_dir / "perf.json").write_text(make_perf_json(REDUCED_COUNTERS), encoding="utf-8")
        (run_dir / "pidstat.txt").write_text(
            make_pidstat(cpu_pct=(392.0, 388.0, 395.0)), encoding="utf-8"
        )
        (run_dir / "ffmpeg.log").write_text(make_ffmpeg_log(frames=BBB_FRAMES), encoding="utf-8")
        (run_dir / "output.sha256").write_text("a3f1c0de\n", encoding="utf-8")


def cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True, check=False)


class TestTheReducedInstrumentationThroughTheClis:
    """O critério de aceite do #119: uma árvore com o `pmu_events` reduzido consolida,
    e o gate passa sobre ela."""

    @pytest.fixture
    def consolidated(self, tmp_path: Path) -> Path:
        write_reduced_block(tmp_path / "runs")
        out = tmp_path / "runs.parquet"
        config = CONFIG_DIR / "contadores.toml"

        result = cli(
            str(CONSOLIDATE), "--runs", str(tmp_path / "runs"), "--config", str(config),
            "--out", str(out),
        )  # fmt: skip

        assert result.returncode == 0, result.stderr
        return out

    def test_the_table_has_the_columns_of_the_definition(self, consolidated):
        names = pq.read_schema(consolidated).names

        assert "perf_branch_misses" not in names
        assert {"perf_cycles", "perf_instructions", "ipc"} <= set(names)

    def test_the_gate_passes_and_prints_the_frequency_of_the_graviton(self, consolidated):
        result = cli(
            str(GATE), "--parquet", str(consolidated),
            "--config", str(CONFIG_DIR / "contadores.toml"),
            "--prices", str(ROLE_ROOT / "prices.toml"), "--covers", "complementares",
        )  # fmt: skip

        assert result.returncode == 0, result.stdout + result.stderr
        assert "[ok] 2  colunas de PMU da definição: 6 eventos + 1 métricas, 0 nulo(s)" in (
            result.stdout.splitlines()
        )
        assert any(
            line.split() == ["c7g-pmu", "2.6", "2.6", "2.6"] for line in result.stdout.splitlines()
        )

    def test_the_campaign_definition_over_the_same_tree_fails_item_2(self, consolidated):
        # A definição errada é a que declara os eventos de branch que a `c7g-pmu`
        # não mediu: o gate tem de dizer quais colunas faltam, e não estourar.
        result = cli(
            str(GATE), "--parquet", str(consolidated),
            "--config", str(CONFIG_DIR / "experiment.toml"),
            "--prices", str(ROLE_ROOT / "prices.toml"), "--covers", "complementares",
        )  # fmt: skip

        assert result.returncode == 1, result.stderr
        (line,) = [line for line in result.stdout.splitlines() if " 2  " in line]
        assert line.startswith("[FALHOU] 2")
        assert "perf_branch_misses" in line
