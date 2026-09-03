#!/usr/bin/env python3
"""CLI do gerador do plano — casca fina sobre `experiment_config` e `scenario_plan`.

Emite o canônico e uma fatia por arquitetura, e é aqui que os nomes do layout de
prefixos da ADR-0011 são decididos: o núcleo devolve as fatias chaveadas pelo id
da instância e não conhece caminho nenhum.

    python orchestrator/generate_scenarios.py \\
        --config config/experiment.toml --out build/scenarios
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from experiment_config import ConfigError, ExperimentConfig, validate_config
from scenario_plan import (
    build_canonical_plan,
    build_instance_slices,
    serialize_plan,
    summarize_plan,
)

EXIT_OK = 0
EXIT_INVALID_CONFIG = 1

CANONICAL_FILENAME = "canonical.json"
SLICE_FILENAME = "{instance}.json"


def load_config(path: Path) -> ExperimentConfig:
    """Lê o TOML do disco e devolve a configuração validada."""
    with path.open("rb") as handle:
        return validate_config(tomllib.load(handle))


def plan_artifacts(plan: dict[str, Any], out_dir: Path) -> dict[Path, dict[str, Any]]:
    """O canônico e as fatias, cada um já com o caminho onde vai ser escrito."""
    return {
        out_dir / CANONICAL_FILENAME: plan,
        **{
            out_dir / SLICE_FILENAME.format(instance=instance): plan_slice
            for instance, plan_slice in build_instance_slices(plan).items()
        },
    }


def write_plans(artifacts: Mapping[Path, dict[str, Any]], out_dir: Path) -> None:
    """Escreve cada artefato no diretório de saída, criado se não existir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, plan in artifacts.items():
        path.write_text(serialize_plan(plan), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="generate_scenarios.py",
        description=(
            "Gera o plano canônico de Cenários e as suas fatias por arquitetura "
            "a partir do config/experiment.toml."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="caminho do experiment.toml",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="diretório onde os artefatos do plano são escritos (criado se não existir)",
    )
    args = parser.parse_args()

    try:
        plan = build_canonical_plan(load_config(args.config))
        artifacts = plan_artifacts(plan, args.out)
        write_plans(artifacts, args.out)
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as error:
        print(f"{args.config}: {error}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    for path, artifact in artifacts.items():
        print(f"{path}: {summarize_plan(artifact)}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
