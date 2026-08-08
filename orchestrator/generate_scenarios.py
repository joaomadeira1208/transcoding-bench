#!/usr/bin/env python3
"""CLI do gerador do plano de Cenários — casca fina sobre o núcleo puro.

Aqui mora só o I/O: parsear argv, ler o `config/experiment.toml` do disco,
escrever o plano no diretório de saída e traduzir `ConfigError` em código de
saída. O que é uma spec válida é decisão de `experiment_config.validate_config`,
e o que é o plano é decisão de `scenario_plan.build_canonical_plan` — as duas são
funções puras e é nelas que os testes batem (decisão D4 da spec).

Neste ticket o comando emite o **canônico**; as fatias por arquitetura entram no
próximo passo da spec, por baixo do mesmo entrypoint.

    python orchestrator/generate_scenarios.py \\
        --config config/experiment.toml --out build/scenarios

Sai com 0 se a spec é válida e com 1 se não é, imprimindo no stderr uma mensagem
que nomeia o registro ofensor — nada de default silencioso salvando uma matriz
experimental defeituosa. O arquivo escrito é função pura do TOML: gerá-lo de novo
produz bytes idênticos, então "reproduzir o plano" se verifica com `diff`.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

from experiment_config import ConfigError, ExperimentConfig, validate_config
from scenario_plan import build_canonical_plan, serialize_plan, summarize_plan

EXIT_OK = 0
EXIT_INVALID_CONFIG = 1

CANONICAL_FILENAME = "canonical.json"


def load_config(path: Path) -> ExperimentConfig:
    """Lê o TOML do disco e devolve a configuração validada."""
    with path.open("rb") as handle:
        return validate_config(tomllib.load(handle))


def write_canonical_plan(plan: dict[str, Any], out_dir: Path) -> Path:
    """Escreve o plano canônico no diretório de saída e devolve o caminho."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / CANONICAL_FILENAME
    path.write_text(serialize_plan(plan), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="generate_scenarios.py",
        description="Gera o plano canônico de Cenários a partir do config/experiment.toml.",
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
        help="diretório onde o plano é escrito (criado se não existir)",
    )
    args = parser.parse_args()

    try:
        plan = build_canonical_plan(load_config(args.config))
        path = write_canonical_plan(plan, args.out)
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as error:
        print(f"{args.config}: {error}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    print(f"{path}: {summarize_plan(plan)}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
