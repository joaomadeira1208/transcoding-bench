#!/usr/bin/env python3
"""A checklist do gate sobre o Parquet de um lançamento — ver `analysis/README.md`.

python analysis/gate.py --parquet runs.parquet --config config/pilot.toml \\
    --prices analysis/prices.toml --covers piloto [--quality quality_groups.parquet]
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from run_table import PmuColumns, pcnt_column, perf_column, pmu_columns

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

CYCLES_PER_MS_PER_GHZ = 1e6
FREQUENCY_QUANTILES = (0.05, 0.5, 0.95)
FREQUENCY_WINDOW_HOURS = 6


@dataclass(frozen=True)
class Definition:
    frames: dict[str, int]
    pmu: PmuColumns


def load_definition(path: Path) -> Definition:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    return Definition(
        frames={video["slug"]: video["frames"] for video in config["video"]},
        pmu=pmu_columns(config),
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
    events = [perf_column(event) for event in definition.pmu.events]
    metrics = list(definition.pmu.metrics)
    missing = [column for column in (*events, *metrics) if column not in df.columns]
    if missing:
        return False, f"coluna(s) que a definição declara e o Parquet não tem: {', '.join(missing)}"
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
    offending = []
    for name, (numerator, denominator) in definition.pmu.metrics.items():
        columns = [pcnt_column(numerator), pcnt_column(denominator)]
        if not set(columns) <= set(df.columns):
            offending.append(f"{name}: sem as colunas de regime no Parquet")
        elif not (same := df[columns[0]] == df[columns[1]]).all():
            offending.append(f"{name}: {(~same).sum()}/{len(df)} linhas com janelas distintas")
    if offending:
        return False, "; ".join(offending)
    return True, f"{len(definition.pmu.metrics)} pares na mesma janela em todas as {len(df)} linhas"


def check_quality(groups: pd.DataFrame, df: pd.DataFrame) -> tuple[bool, str]:
    """Passa se todo Cenário do Parquet foi julgado, com todos os seus bitstreams;
    a equivalência é reportada, não exigida (ADR-0005)."""
    detail = (
        f"{len(groups)} grupos, {int(groups.bitstreams.sum())} bitstreams julgados, "
        f"{int(_verdicts(groups).sum())}/{len(groups)} equivalentes"
    )
    if groups.empty:
        return False, f"{detail}; nenhum grupo julgado"
    measured = df.groupby(_scenario_names(df)).output_sha256.nunique()
    judged = groups.set_index("scenario").bitstreams
    missing = measured.index.difference(judged.index).tolist()
    if missing:
        return False, f"{detail}; Cenário sem grupo: {', '.join(missing)}"
    short = [
        f"{name} ({judged[name]}/{measured[name]})"
        for name in judged.index.intersection(measured.index)
        if judged[name] != measured[name]
    ]
    if short:
        return False, f"{detail}; bitstreams julgados/medidos: {', '.join(short)}"
    unjudged = groups.scenario[~groups.judged_ok.astype(bool)].tolist()
    if unjudged:
        return False, f"{detail}; sem julgamento válido: {', '.join(unjudged)}"
    return True, detail


def _scenario_names(df: pd.DataFrame) -> pd.Series:
    """O nome do grupo no `quality.py`: a `scenario_id` sem Instância e Replicação."""
    return df.encoder + "_" + df.input_res + "_" + df.output_res + "_" + df.video


def nonequivalent(groups: pd.DataFrame) -> list[str]:
    """Os grupos julgados fora dos limiares — de onde sai a subseção de casos
    divergentes do artigo. Nulo não entra: sem julgamento não há distância."""
    divergent = groups[~_verdicts(groups) & groups.equivalent.notna()]
    return [
        f"{row.scenario}: vmaf_delta {row.vmaf_delta:.3f}, ssim_delta {row.ssim_delta:.5f}"
        for row in divergent.itertuples()
    ]


def _verdicts(groups: pd.DataFrame) -> pd.Series:
    """`equivalent` com o nulo como não-equivalente, para contar."""
    return groups.equivalent.astype("boolean").fillna(False).astype(bool)


def multiplexing_by_instance(df: pd.DataFrame, definition: Definition) -> pd.DataFrame:
    """As duas famílias de evento separadas.

    Uma média sobre os oito devolve 75% no Graviton3 e esconde a multiplexação:
    eventos de software não passam pela PMU e voltam sempre 100%.
    """
    measured = [e for e in definition.pmu.events if pcnt_column(e) in df.columns]
    software = [pcnt_column(e) for e in measured if e in PERF_SOFTWARE_EVENTS]
    hardware = [pcnt_column(e) for e in measured if e not in PERF_SOFTWARE_EVENTS]
    return pd.DataFrame(
        {
            "hardware_pcnt": df[hardware].mean(axis=1).groupby(df.instance).mean(),
            "software_pcnt": df[software].mean(axis=1).groupby(df.instance).mean(),
        }
    ).round(1)


def effective_ghz(df: pd.DataFrame) -> pd.Series:
    """Sobre o tempo de CPU, e não o de parede: com 4 threads, a de parede daria
    quatro vezes a frequência de um núcleo."""
    return df.perf_cycles / (df.perf_task_clock * CYCLES_PER_MS_PER_GHZ)


def frequency_by_instance(df: pd.DataFrame) -> pd.DataFrame:
    """p5/p50/p95 da frequência efetiva de cada Instância, em GHz."""
    quantiles = effective_ghz(df).groupby(df.instance).quantile(list(FREQUENCY_QUANTILES))
    table = quantiles.unstack()
    table.columns = [f"p{round(q * 100)}" for q in FREQUENCY_QUANTILES]
    return table.round(3)


def frequency_over_launch(
    df: pd.DataFrame, window_hours: int = FREQUENCY_WINDOW_HOURS
) -> pd.DataFrame:
    """A mediana da frequência por janela de horas desde o primeiro run do lançamento.

    Desde o primeiro run do lançamento, e não o de cada Instância: é o relógio de
    parede comum que mostra uma mudança de frequência numa só delas (ADR-0026).
    """
    started = pd.to_datetime(df.started_at, utc=True)
    hours = (started - started.min()).dt.total_seconds() / 3600
    window = (hours // window_hours * window_hours).astype(int).rename("h")
    return effective_ghz(df).groupby([window, df.instance]).median().unstack().round(3)


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
    parser.add_argument("--quality", type=Path, help="a tabela de grupos do quality.py")
    args = parser.parse_args()

    try:
        df = pd.read_parquet(args.parquet)
        definition = load_definition(args.config)
        rate = load_rate(args.prices, args.covers)
        groups = pd.read_parquet(args.quality) if args.quality else None
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        print(error, file=sys.stderr)
        return EXIT_UNREADABLE

    df = with_cost(df, rate)

    checks = [
        ("1  exit_code e teto por run", check_exit_and_timeout(df)),
        ("2  colunas de PMU da definição", check_pmu_complete(df, definition)),
        ("3  ffmpeg_frames vs master", check_frames(df, definition)),
        ("4  cpu_pct_avg acima de um core", check_cpu_saturation(df)),
        ("5  triage e Juiz", check_quality(groups, df) if groups is not None else None),
        ("-  pares de métrica na mesma janela", check_metric_pairs(df, definition)),
    ]
    for label, verdict in checks:
        if verdict is None:
            print(f"[em aberto] {label}: sem --quality")
            continue
        passed, detail = verdict
        print(f"[{'ok' if passed else 'FALHOU'}] {label}: {detail}")

    if groups is not None and (divergent := nonequivalent(groups)):
        print("\ngrupos não equivalentes (ADR-0005):")
        print("\n".join(f"  {line}" for line in divergent))

    print(f"\n{len(df)} Replicações, {len(df.groupby(CELL))} células")
    print("\npcnt-running por instância (ADR-0006):")
    print(multiplexing_by_instance(df, definition).to_string())

    if {"perf_cycles", "perf_task_clock"} <= set(df.columns):
        print("\nfrequência efetiva por instância, GHz (perf_cycles ÷ perf_task_clock, ADR-0026):")
        print(frequency_by_instance(df).to_string())
        print(
            f"\nmediana da frequência por janela de {FREQUENCY_WINDOW_HOURS} h do lançamento, GHz:"
        )
        print(frequency_over_launch(df).to_string())

    print(f"\nCV intra-célula do tempo (ADR-0003 supôs < {REPLICATION_CV_CEILING:.0%}):")
    print(replication_cv(df).describe().round(4).to_string())

    print("\nbitstreams distintos por Cenário entre as arquiteturas (ADR-0005):")
    print(bitstreams_per_scenario(df).to_string())

    print("\ncusto por Cenário, US$ (ADR-0024):")
    print(df.groupby(["instance", "codec"]).cost_usd.mean().round(4).to_string())

    verdicts = [verdict for _, verdict in checks if verdict is not None]
    return EXIT_OK if all(passed for passed, _ in verdicts) else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
