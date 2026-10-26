#!/usr/bin/env python3
"""As tabelas do relatório dos experimentos complementares (ADR-0026).

python analysis/complementares.py --campaign campaign.parquet --pilot pilot.parquet \\
    --topologia topologia.parquet --repetibilidade repetibilidade.parquet \\
    --contadores contadores.parquet

Cada Parquet é o do `consolidate.py` do lançamento, com a definição dele. Só o
da campanha é obrigatório: as seções dos experimentos que não vierem ficam de fora.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from gate import effective_ghz, frequency_by_instance

EXIT_OK = 0
EXIT_UNREADABLE = 2

SCENARIO_KEY = ["encoder", "input_res", "output_res", "video"]
PAIR = ["input_res", "output_res"]

SMT = "c7i-smt"
NOSMT = "c7i-nosmt"

ARM_ANCESTOR = "c7g"
X86_ANCESTORS = ("c7i", "c7a")


def campaign_instance(instance: str) -> str:
    """A instância da campanha de que um id dos complementares descende: o prefixo."""
    return instance.split("-")[0]


def per_frame(df: pd.DataFrame, column: str) -> pd.Series:
    return df[column] / df.ffmpeg_frames


def with_derived(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(
        cycles_per_frame=per_frame(df, "perf_cycles"),
        instructions_per_frame=per_frame(df, "perf_instructions"),
        ghz=effective_ghz(df),
    )


def topology_table(df: pd.DataFrame, by: Sequence[str]) -> pd.DataFrame:
    """Ciclos por quadro, IPC, frequência e tempo de cada braço."""
    return (
        with_derived(df)
        .groupby(["instance", *by])
        .agg(
            cycles_per_frame=("cycles_per_frame", "mean"),
            ipc=("ipc", "mean"),
            ghz=("ghz", "median"),
            time_elapsed_s=("time_elapsed_s", "mean"),
        )
    )


def smt_effect(df: pd.DataFrame, by: Sequence[str]) -> pd.DataFrame:
    """O braço com SMT sobre o sem: acima de 1 é o que o SMT custa por thread.

    A frequência vai ao lado do tempo: uma razão de tempo sem ela não separa o
    SMT de um braço que rodou num relógio mais lento (ADR-0026)."""
    columns = ["cycles_per_frame", "time_elapsed_s", "ghz"]
    means = with_derived(df).groupby(["instance", *by])[columns].mean()
    return means.loc[SMT] / means.loc[NOSMT]


def bridge(topologia: pd.DataFrame, campaign: pd.DataFrame) -> pd.DataFrame:
    """Os ciclos por quadro do `c7i-smt` contra os da `c7i.xlarge`, nos mesmos Cenários."""
    ancestor = campaign_instance(SMT)
    smt = _cycles_by_scenario(topologia[topologia.instance == SMT])
    xlarge = _cycles_by_scenario(campaign[campaign.instance == ancestor])
    joined = pd.concat({SMT: smt, ancestor: xlarge}, axis=1, join="inner")
    table = joined.groupby(level="encoder").mean()
    return table.assign(ratio=table[SMT] / table[ancestor])


def _cycles_by_scenario(df: pd.DataFrame) -> pd.Series:
    return with_derived(df).groupby(SCENARIO_KEY).cycles_per_frame.mean()


def host_dispersion(df: pd.DataFrame) -> pd.DataFrame:
    """A dispersão do tempo entre as máquinas de cada tipo, um valor por host (ADR-0026)."""
    hosts = df.groupby(["instance_type", "encoder", "instance_id"]).time_elapsed_s.mean()
    by_type = hosts.groupby(level=["instance_type", "encoder"])
    mean = by_type.mean()
    return pd.DataFrame(
        {
            "hosts": by_type.size(),
            "mean_s": mean,
            "cv": by_type.std() / mean,
            "range": (by_type.max() - by_type.min()) / mean,
        }
    )


def frequency_by_host(df: pd.DataFrame) -> pd.Series:
    return effective_ghz(df).groupby([df.instance_type, df.instance_id]).median().round(3)


def hash_agreement(experiment: pd.DataFrame, campaign: pd.DataFrame) -> pd.DataFrame:
    """Os bitstreams de cada instância contra os da instância da campanha de que ela
    descende, no mesmo Cenário. Cenário que a campanha não rodou não concorda."""
    produced = experiment.groupby(["instance", *SCENARIO_KEY]).output_sha256.agg(frozenset)
    reference = campaign.groupby(["instance", *SCENARIO_KEY]).output_sha256.agg(frozenset)
    rows = []
    for (instance, *scenario), hashes in produced.items():
        ancestor = campaign_instance(instance)
        expected = reference.get((ancestor, *scenario), frozenset())
        rows.append(
            {
                "instance": instance,
                "ancestor": ancestor,
                **dict(zip(SCENARIO_KEY, scenario, strict=True)),
                "same": bool(expected) and hashes == expected,
            }
        )
    return pd.DataFrame(rows)


def hash_summary(experiment: pd.DataFrame, campaign: pd.DataFrame) -> str:
    agreement = hash_agreement(experiment, campaign)
    lines = [f"{int(agreement.same.sum())}/{len(agreement)} (instância, Cenário) com os hashes"]
    lines += [
        f"  diverge: {row.instance} x {row.ancestor}: "
        f"{row.encoder}_{row.input_res}_{row.output_res}_{row.video}"
        for row in agreement[~agreement.same].itertuples()
    ]
    return "\n".join(lines)


def instructions_per_frame(df: pd.DataFrame) -> pd.Series:
    return with_derived(df).groupby(["instance", "encoder"]).instructions_per_frame.mean()


def counters_table(contadores: pd.DataFrame, campaign: pd.DataFrame) -> pd.DataFrame:
    """Instruções por quadro da `c7g-pmu`, sem multiplexação, contra a campanha.

    A campanha entra só nos Cenários que a `c7g-pmu` rodou: a média de outros
    pares misturaria outra resolução na comparação.
    """
    scenarios = contadores[SCENARIO_KEY].drop_duplicates()
    same = campaign.merge(scenarios, on=SCENARIO_KEY)
    unmultiplexed = instructions_per_frame(contadores).droplevel("instance").rename("c7g-pmu")
    reference = instructions_per_frame(same).unstack("instance")
    table = pd.concat([unmultiplexed, reference[[ARM_ANCESTOR, *X86_ANCESTORS]]], axis=1)
    return table.assign(
        pmu_sobre_multiplexado=table["c7g-pmu"] / table[ARM_ANCESTOR],
        **{f"pmu_sobre_{x86}": table["c7g-pmu"] / table[x86] for x86 in X86_ANCESTORS},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="As tabelas dos experimentos complementares.")
    parser.add_argument("--campaign", required=True, type=Path)
    parser.add_argument("--pilot", type=Path, help="entra na dispersão entre hosts")
    parser.add_argument("--topologia", type=Path)
    parser.add_argument("--repetibilidade", type=Path)
    parser.add_argument("--contadores", type=Path)
    args = parser.parse_args()

    try:
        campaign = pd.read_parquet(args.campaign)
        read = {
            name: pd.read_parquet(path) if path else None
            for name, path in (
                ("pilot", args.pilot),
                ("topologia", args.topologia),
                ("repetibilidade", args.repetibilidade),
                ("contadores", args.contadores),
            )
        }
    except OSError as error:
        print(error, file=sys.stderr)
        return EXIT_UNREADABLE

    with pd.option_context("display.width", 200, "display.max_columns", 20):
        if (topologia := read["topologia"]) is not None:
            _topology_section(topologia, campaign)
        if (repetibilidade := read["repetibilidade"]) is not None:
            _repeatability_section(repetibilidade, campaign, read["pilot"])
        if (contadores := read["contadores"]) is not None:
            _counters_section(contadores, campaign)
    return EXIT_OK


def _topology_section(topologia: pd.DataFrame, campaign: pd.DataFrame) -> None:
    _heading("topologia: por braço e codec")
    print(topology_table(topologia, ["encoder"]).round(4).to_string())
    _heading("topologia: por braço e par")
    print(topology_table(topologia, PAIR).round(4).to_string())
    _heading(f"efeito do SMT ({SMT} / {NOSMT}), por codec")
    print(smt_effect(topologia, ["encoder"]).round(4).to_string())
    _heading(f"efeito do SMT ({SMT} / {NOSMT}), por par")
    print(smt_effect(topologia, PAIR).round(4).to_string())
    _heading(f"ponte: ciclos por quadro do {SMT} sobre os da c7i.xlarge da campanha")
    print(bridge(topologia, campaign).round(4).to_string())
    _heading("frequência efetiva por braço, GHz")
    print(frequency_by_instance(topologia).to_string())
    _heading("hashes da topologia contra a campanha")
    print(hash_summary(topologia, campaign))


def _repeatability_section(
    repetibilidade: pd.DataFrame, campaign: pd.DataFrame, pilot: pd.DataFrame | None
) -> None:
    scenarios = repetibilidade[SCENARIO_KEY].drop_duplicates()
    earlier = [frame for frame in (campaign, pilot) if frame is not None]
    hosts = pd.concat(
        [repetibilidade, *(frame.merge(scenarios, on=SCENARIO_KEY) for frame in earlier)]
    )
    _heading("repetibilidade: dispersão do tempo entre hosts de cada tipo")
    print(host_dispersion(hosts).round(4).to_string())
    _heading("repetibilidade: frequência efetiva por host, GHz (mediana)")
    print(frequency_by_host(hosts).to_string())
    _heading("hashes da repetibilidade contra a campanha")
    print(hash_summary(repetibilidade, campaign))


def _counters_section(contadores: pd.DataFrame, campaign: pd.DataFrame) -> None:
    _heading("contadores: frequência real da c7g, GHz")
    print(frequency_by_instance(contadores).to_string())
    _heading("contadores: instruções por quadro, sem multiplexação e na campanha")
    print(counters_table(contadores, campaign).round(4).to_string())
    _heading("hashes dos contadores contra a campanha")
    print(hash_summary(contadores, campaign))


def _heading(title: str) -> None:
    print(f"\n== {title}")


if __name__ == "__main__":
    sys.exit(main())
