#!/usr/bin/env python3
"""A checklist do gate sobre o Parquet de um lançamento — ver `analysis/README.md`.

python analysis/gate.py --parquet runs.parquet --config config/pilot.toml \\
    --prices analysis/prices.toml --covers piloto
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from run_table import _pcnt_column, _perf_column

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNREADABLE = 2

RUN_TIMEOUT_SECONDS = 14400
RUN_TIMEOUT_HEADROOM = 0.5

VCPUS = 4
CPU_PCT_FLOOR = 100.0

PERF_SOFTWARE_EVENTS = frozenset(
    {
        "task-clock",
        "cpu-clock",
        "context-switches",
        "cpu-migrations",
        "page-faults",
        "minor-faults",
        "major-faults",
        "alignment-faults",
        "emulation-faults",
    }
)

SCENARIO_KEY = ["codec", "input_res", "output_res", "video"]
CELL = [*SCENARIO_KEY, "instance"]

REPLICATION_CV_CEILING = 0.05


@dataclass(frozen=True)
class Definition:
    frames: dict[str, int]
    events: tuple[str, ...]
    metrics: dict[str, tuple[str, str]]


def load_definition(path: Path) -> Definition:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    return Definition(
        frames={video["slug"]: video["frames"] for video in config["video"]},
        events=tuple(config["instrumentation"]["pmu_events"]),
        metrics={
            metric["name"]: (metric["numerator"], metric["denominator"])
            for metric in config["instrumentation"]["metric"]
        },
    )


def check_exit_and_timeout(df: pd.DataFrame) -> tuple[bool, str]:
    failed = int((df.exit_code != 0).sum())
    longest = float(df.time_elapsed_s.max())
    share = longest / RUN_TIMEOUT_SECONDS
    return failed == 0 and share < RUN_TIMEOUT_HEADROOM, (
        f"{failed} com exit_code != 0; run mais longo {longest:.0f} s "
        f"({share:.1%} do teto de {RUN_TIMEOUT_SECONDS} s)"
    )


def check_pmu_complete(df: pd.DataFrame, definition: Definition) -> tuple[bool, str]:
    events = [_perf_column(event) for event in definition.events]
    metrics = list(definition.metrics)
    nulls = int(df[events].isna().sum().sum()) + int(df[metrics].isna().sum().sum())
    return nulls == 0, f"{len(events)} eventos + {len(metrics)} métricas, {nulls} nulo(s)"


def check_frames(df: pd.DataFrame, definition: Definition) -> tuple[bool, str]:
    seen = df.groupby("video").ffmpeg_frames.agg(["min", "max"])
    unknown = [video for video in seen.index if video not in definition.frames]
    if unknown:
        return False, f"vídeo fora da definição: {', '.join(unknown)}"
    wrong = [
        f"{video}: {row['min']}..{row['max']}, esperado {definition.frames[video]}"
        for video, row in seen.iterrows()
        if row["min"] != definition.frames[video] or row["max"] != definition.frames[video]
    ]
    if wrong:
        return False, "; ".join(wrong)
    return True, ", ".join(f"{video} {definition.frames[video]}" for video in seen.index)


def check_cpu_saturation(df: pd.DataFrame) -> tuple[bool, str]:
    floor = float(df.cpu_pct_avg.min())
    mean = float(df.cpu_pct_avg.mean())
    available = VCPUS * 100
    return floor > CPU_PCT_FLOOR, (
        f"mínimo {floor:.1f}%, média {mean:.1f}% de {available}% "
        f"({mean / available:.0%} de ocupação)"
    )


def check_metric_pairs(df: pd.DataFrame, definition: Definition) -> tuple[bool, str]:
    offending = [
        f"{name}: {(~same).sum()}/{len(df)} linhas com janelas distintas"
        for name, (numerator, denominator) in definition.metrics.items()
        if not (same := df[_pcnt_column(numerator)] == df[_pcnt_column(denominator)]).all()
    ]
    if offending:
        return False, "; ".join(offending)
    return True, f"{len(definition.metrics)} pares na mesma janela em todas as {len(df)} linhas"


def multiplexing_by_instance(df: pd.DataFrame, definition: Definition) -> pd.DataFrame:
    """As duas famílias de evento separadas.

    Uma média sobre os oito devolve 75% no Graviton3 e esconde a multiplexação:
    eventos de software não passam pela PMU e voltam sempre 100%.
    """
    software = [_pcnt_column(e) for e in definition.events if e in PERF_SOFTWARE_EVENTS]
    hardware = [_pcnt_column(e) for e in definition.events if e not in PERF_SOFTWARE_EVENTS]
    return pd.DataFrame(
        {
            "hardware_pcnt": df[hardware].mean(axis=1).groupby(df.instance).mean(),
            "software_pcnt": df[software].mean(axis=1).groupby(df.instance).mean(),
        }
    ).round(1)


def replication_cv(df: pd.DataFrame) -> pd.Series:
    return df.groupby(CELL).time_elapsed_s.agg(lambda cell: cell.std() / cell.mean())


def bitstreams_per_scenario(df: pd.DataFrame) -> pd.Series:
    return df.groupby(SCENARIO_KEY).output_sha256.nunique().value_counts().sort_index()


def with_cost(df: pd.DataFrame, rate: dict[str, float]) -> pd.DataFrame:
    return df.assign(cost_usd=df.time_elapsed_s * df.instance_type.map(rate) / 3600)


def load_rate(path: Path, covers: str) -> dict[str, float]:
    for quote in tomllib.loads(path.read_text(encoding="utf-8"))["quote"]:
        if quote["covers"] == covers:
            return quote["instance_usd_per_hour"]
    raise KeyError(f"{path}: nenhuma consulta com covers = {covers!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="A checklist do gate sobre um Parquet.")
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path, help="a definição do lançamento")
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--covers", required=True, help="qual consulta de preço usar")
    args = parser.parse_args()

    try:
        df = pd.read_parquet(args.parquet)
        definition = load_definition(args.config)
        rate = load_rate(args.prices, args.covers)
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        print(error, file=sys.stderr)
        return EXIT_UNREADABLE

    df = with_cost(df, rate)

    checks = [
        ("1  exit_code e teto por run", check_exit_and_timeout(df)),
        ("2  dez colunas de PMU", check_pmu_complete(df, definition)),
        ("3  ffmpeg_frames vs master", check_frames(df, definition)),
        ("4  cpu_pct_avg acima de um core", check_cpu_saturation(df)),
        ("-  pares de métrica na mesma janela", check_metric_pairs(df, definition)),
    ]
    for label, (passed, detail) in checks:
        print(f"[{'ok' if passed else 'FALHOU'}] {label}: {detail}")

    print(f"\n{len(df)} Replicações, {len(df.groupby(CELL))} células")
    print("\npcnt-running por instância (ADR-0006):")
    print(multiplexing_by_instance(df, definition).to_string())

    print(f"\nCV intra-célula do tempo (ADR-0003 supôs < {REPLICATION_CV_CEILING:.0%}):")
    print(replication_cv(df).describe().round(4).to_string())

    print("\nbitstreams distintos por Cenário entre as arquiteturas (ADR-0005):")
    print(bitstreams_per_scenario(df).to_string())

    print("\ncusto por Cenário, US$ (ADR-0024):")
    print(df.groupby(["instance", "codec"]).cost_usd.mean().round(4).to_string())

    return EXIT_OK if all(passed for _, (passed, _) in checks) else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
