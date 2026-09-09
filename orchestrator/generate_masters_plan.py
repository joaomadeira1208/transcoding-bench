#!/usr/bin/env python3
"""CLI do plano dos Masters — casca fina sobre `experiment_config` e `masters_plan`.

    python orchestrator/generate_masters_plan.py --config config/experiment.toml
    python orchestrator/generate_masters_plan.py \\
        --config config/experiment.toml --out build/masters.json
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from experiment_config import ConfigError, validate_config
from masters_plan import build_masters_plan
from scenario_plan import serialize_plan

EXIT_OK = 0
EXIT_FAILURE = 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="generate_masters_plan.py",
        description=(
            "Gera o plano dos Masters — fonte, nome e propriedades esperadas de cada um "
            "— a partir do config/experiment.toml."
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
        type=Path,
        help="arquivo onde o plano é escrito (default: stdout)",
    )
    args = parser.parse_args()

    try:
        with args.config.open("rb") as handle:
            config = validate_config(tomllib.load(handle))
        plan = serialize_plan(build_masters_plan(config))
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as error:
        print(f"{args.config}: {error}", file=sys.stderr)
        return EXIT_FAILURE

    if args.out is None:
        sys.stdout.write(plan)
        return EXIT_OK

    try:
        args.out.write_text(plan, encoding="utf-8")
    except OSError as error:
        print(f"{args.out}: {error}", file=sys.stderr)
        return EXIT_FAILURE

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
