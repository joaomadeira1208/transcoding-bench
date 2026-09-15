#!/usr/bin/env python3
"""Os itens 7 e 8 do gate, projetados do piloto — ver `analysis/README.md`.

python analysis/extrapolate.py --parquet runs.parquet \\
    --config config/experiment.toml --outputs outputs.txt
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

import pandas as pd

EXIT_OK = 0
EXIT_OVER_BUDGET = 1
EXIT_UNREADABLE = 2

TIME_BUDGET_HOURS = 100.0
DISK_BUDGET_GB = 147.0
RUN_TIMEOUT_SECONDS = 14400

CELL = ["codec", "video", "instance"]


class IncompleteListing(Exception):
    """Execuções do Parquet sem `output.mkv` na listagem do bucket."""


def output_pixels(config: dict, video: str, tier: str) -> int:
    geometry = next(each for each in config["video"] if each["slug"] == video)["geometry"][tier]
    return geometry["width"] * geometry["height"]


def pair_scaling(config: dict, *, measured_tier: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "input_res": pair["input_res"],
                "output_res": pair["output_res"],
                "video": video["slug"],
                "scaling": output_pixels(config, video["slug"], pair["output_res"])
                / output_pixels(config, video["slug"], measured_tier),
            }
            for pair in config["pair"]
            for video in config["video"]
        ]
    )


def read_output_sizes(listing: Path) -> pd.DataFrame:
    rows = [line.split() for line in listing.read_text(encoding="utf-8").splitlines() if line]
    return pd.DataFrame(
        [{"run_id": row[3].split("/")[1], "output_bytes": int(row[2])} for row in rows]
    )


def join_output_sizes(df: pd.DataFrame, sizes: pd.DataFrame) -> pd.DataFrame:
    joined = df.merge(sizes, on="run_id", how="left")
    missing = joined.run_id[joined.output_bytes.isna()]
    if not missing.empty:
        raise IncompleteListing(
            f"{len(missing)} Execução(ões) sem output.mkv na listagem: {', '.join(missing)}"
        )
    return joined


def measured_cells(joined: pd.DataFrame) -> pd.DataFrame:
    return joined.groupby(CELL)[["time_elapsed_s", "output_bytes"]].mean().reset_index()


def project(
    measured: pd.DataFrame, scaling: pd.DataFrame, *, runs_per_scenario: int
) -> pd.DataFrame:
    joined = measured.merge(scaling, on="video")
    joined["run_seconds"] = joined.time_elapsed_s * joined.scaling
    joined["run_bytes"] = joined.output_bytes * joined.scaling
    totals = joined.groupby("instance")[["run_seconds", "run_bytes"]].sum() * runs_per_scenario
    return pd.DataFrame(
        {"hours": totals.run_seconds / 3600, "gigabytes": totals.run_bytes / 1e9}
    ).round(1)


def longest_run_seconds(df: pd.DataFrame, scaling: pd.DataFrame) -> float:
    longest = df.groupby("video").time_elapsed_s.max().rename("seconds").reset_index()
    joined = longest.merge(scaling, on="video")
    return float((joined.seconds * joined.scaling).max())


def main() -> int:
    parser = argparse.ArgumentParser(description="Projeta a campanha a partir do piloto.")
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path, help="a definição da campanha")
    parser.add_argument("--outputs", required=True, type=Path, help="aws s3 ls de output.mkv")
    parser.add_argument("--time-budget-hours", type=float, default=TIME_BUDGET_HOURS)
    parser.add_argument("--disk-budget-gb", type=float, default=DISK_BUDGET_GB)
    args = parser.parse_args()

    try:
        df = pd.read_parquet(args.parquet)
        config = tomllib.loads(args.config.read_text(encoding="utf-8"))
        sizes = read_output_sizes(args.outputs)
        joined = join_output_sizes(df, sizes)
    except (OSError, IndexError, ValueError, tomllib.TOMLDecodeError, IncompleteListing) as error:
        print(error, file=sys.stderr)
        return EXIT_UNREADABLE

    tiers = df.output_res.unique()
    if len(tiers) != 1:
        print(f"o piloto mediu mais de um par de saída: {tiers}", file=sys.stderr)
        return EXIT_UNREADABLE

    measured = measured_cells(joined)
    scaling = pair_scaling(config, measured_tier=tiers[0])
    runs = config["experiment"]["replications"] + config["experiment"]["warmup_runs"]

    print(f"medido: {len(measured)} células em {df.input_res.iloc[0]} → {tiers[0]}")
    print(f"projetado: {len(config['pair'])} pares, {runs} Execuções por Cenário\n")

    projected = project(measured, scaling, runs_per_scenario=runs)
    print(projected.to_string())

    worst_hours = float(projected.hours.max())
    worst_gb = float(projected.gigabytes.max())
    longest_hours = longest_run_seconds(df, scaling) / 3600
    run_cap_hours = RUN_TIMEOUT_SECONDS / 3600
    verdicts = [
        (
            worst_hours <= args.time_budget_hours,
            f"item 7: pior arquitetura {worst_hours:.1f} h contra "
            f"{args.time_budget_hours:.0f} h de orçamento",
        ),
        (
            worst_gb <= args.disk_budget_gb,
            f"item 8: pior arquitetura {worst_gb:.1f} GB contra "
            f"{args.disk_budget_gb:.0f} GB livres no volume",
        ),
        (
            longest_hours < run_cap_hours,
            f"-  teto por Execução: a mais longa projetada {longest_hours:.1f} h contra "
            f"{run_cap_hours:.0f} h (ADR-0012)",
        ),
    ]
    print()
    for passed, detail in verdicts:
        print(f"[{'ok' if passed else 'FALHOU'}] {detail}")

    return EXIT_OK if all(passed for passed, _ in verdicts) else EXIT_OVER_BUDGET


if __name__ == "__main__":
    sys.exit(main())
