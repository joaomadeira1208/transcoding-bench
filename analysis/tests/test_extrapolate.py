# A projeção é aritmética sobre a geometria, e a aritmética não é o risco: o risco
# é o merge com a listagem do bucket, que descarta em silêncio toda Execução sem
# `output.mkv` listado e projeta uma campanha menor com exit 0. Tudo aqui assere
# o que o script diz e o código com que sai, nunca a estrutura da conta.

from __future__ import annotations

import subprocess
import sys
import tomllib
from functools import cache
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from conftest import ROLE_ROOT
from extrapolate import (
    EXIT_OK,
    EXIT_OVER_BUDGET,
    EXIT_UNREADABLE,
    IncompleteListing,
    join_output_sizes,
    longest_run_seconds,
    measured_cells,
    pair_scaling,
    project,
)

CLI = ROLE_ROOT / "extrapolate.py"
CONFIG = ROLE_ROOT.parent / "config" / "experiment.toml"

PIXELS_720P_BBB = 1280 * 720


@cache
def config() -> dict[str, Any]:
    with CONFIG.open("rb") as handle:
        return tomllib.load(handle)


def make_pilot(cells: list[tuple[str, str, str, float]], *, replications: int = 2) -> pd.DataFrame:
    """As Replicações de um piloto no par 1080p → 720p, uma linha por Execução."""
    rows = [
        {
            "run_id": f"{codec}-{video}-{instance}-{rep}",
            "codec": codec,
            "video": video,
            "instance": instance,
            "input_res": "1080p",
            "output_res": "720p",
            "time_elapsed_s": seconds,
        }
        for codec, video, instance, seconds in cells
        for rep in range(1, replications + 1)
    ]
    return pd.DataFrame(rows)


def make_listing(df: pd.DataFrame, output_bytes: int = 1_000_000) -> str:
    return "".join(
        f"2026-09-14 21:39:30 {output_bytes:>10} runs/{run_id}/output.mkv\n" for run_id in df.run_id
    )


def sizes_of(listing: str) -> pd.DataFrame:
    rows = [line.split() for line in listing.splitlines() if line]
    return pd.DataFrame(
        [{"run_id": row[3].split("/")[1], "output_bytes": int(row[2])} for row in rows]
    )


class TestTheListingHasToCoverEveryRun:
    def test_a_run_missing_from_the_listing_fails_naming_it(self):
        df = make_pilot([("libx264", "bbb", "c7g", 100.0)])
        listing = make_listing(df.iloc[:-1])

        with pytest.raises(IncompleteListing, match="libx264-bbb-c7g-2"):
            join_output_sizes(df, sizes_of(listing))

    def test_a_complete_listing_keeps_every_run(self):
        df = make_pilot([("libx264", "bbb", "c7g", 100.0), ("libx265", "tos", "c7i", 200.0)])

        joined = join_output_sizes(df, sizes_of(make_listing(df)))

        assert len(joined) == len(df)
        assert (joined.output_bytes == 1_000_000).all()


class TestTheScalingIsByOutputPixels:
    def test_the_top_pair_is_nine_times_the_measured_720p(self):
        scaling = pair_scaling(config(), measured_tier="720p")
        top = scaling[(scaling.output_res == "2160p") & (scaling.video == "bbb")]

        assert top.scaling.item() == (3840 * 2160) / PIXELS_720P_BBB

    def test_the_measured_pair_scales_by_one(self):
        scaling = pair_scaling(config(), measured_tier="720p")
        same = scaling[(scaling.input_res == "1080p") & (scaling.output_res == "720p")]

        assert (same.scaling == 1.0).all()

    def test_every_pair_of_the_definition_is_projected_for_every_video(self):
        scaling = pair_scaling(config(), measured_tier="720p")

        assert len(scaling) == len(config()["pair"]) * len(config()["video"])


class TestTheProjection:
    def test_hours_are_the_cell_mean_times_every_pair_times_the_runs_per_scenario(self):
        df = make_pilot([("libx264", "bbb", "c7g", 90.0), ("libx264", "bbb", "c7g", 110.0)])
        measured = measured_cells(join_output_sizes(df, sizes_of(make_listing(df))))
        scaling = pair_scaling(config(), measured_tier="720p")
        pixel_sum = scaling[scaling.video == "bbb"].scaling.sum()

        projected = project(measured, scaling, runs_per_scenario=6)

        assert projected.hours.item() == round(100.0 * pixel_sum * 6 / 3600, 1)

    def test_the_longest_run_is_the_slowest_execution_at_the_top_pair(self):
        df = make_pilot([("libx265", "tos", "c7i", 600.0), ("libx264", "tos", "c7i", 100.0)])
        scaling = pair_scaling(config(), measured_tier="720p")
        top = scaling[(scaling.output_res == "2160p") & (scaling.video == "tos")].scaling.item()

        assert longest_run_seconds(df, scaling) == 600.0 * top


class TestTheCli:
    @staticmethod
    def run_cli(tmp_path: Path, df: pd.DataFrame, listing: str, *extra: str):
        parquet = tmp_path / "runs.parquet"
        outputs = tmp_path / "outputs.txt"
        df.to_parquet(parquet)
        outputs.write_text(listing, encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--parquet",
                str(parquet),
                "--config",
                str(CONFIG),
                "--outputs",
                str(outputs),
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_an_incomplete_listing_exits_unreadable_and_names_the_run(self, tmp_path: Path):
        df = make_pilot([("libx264", "bbb", "c7g", 100.0)])

        result = self.run_cli(tmp_path, df, make_listing(df.iloc[:-1]))

        assert result.returncode == EXIT_UNREADABLE
        assert "libx264-bbb-c7g-2" in result.stderr

    def test_a_campaign_that_fits_exits_ok(self, tmp_path: Path):
        df = make_pilot([("libx264", "bbb", "c7g", 100.0)])

        result = self.run_cli(tmp_path, df, make_listing(df))

        assert result.returncode == EXIT_OK
        assert "[ok] item 7" in result.stdout
        assert "[ok] item 8" in result.stdout
        assert "[ok] -  teto por Execução" in result.stdout

    def test_a_campaign_over_the_time_budget_exits_over_budget(self, tmp_path: Path):
        df = make_pilot([("libx264", "bbb", "c7g", 100.0)])

        result = self.run_cli(tmp_path, df, make_listing(df), "--time-budget-hours", "0.1")

        assert result.returncode == EXIT_OVER_BUDGET
        assert "[FALHOU] item 7" in result.stdout

    def test_a_run_projected_past_the_run_cap_fails(self, tmp_path: Path):
        df = make_pilot([("libx265", "bbb", "c7i", 2000.0)])

        result = self.run_cli(tmp_path, df, make_listing(df))

        assert result.returncode == EXIT_OVER_BUDGET
        assert "[FALHOU] -  teto por Execução" in result.stdout
