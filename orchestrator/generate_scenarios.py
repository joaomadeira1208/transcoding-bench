#!/usr/bin/env python3
"""CLI do gerador do plano de Cenários — casca fina sobre o núcleo puro.

Aqui mora só o I/O: parsear argv, ler o `config/experiment.toml` do disco e
traduzir `ConfigError` em código de saída. A decisão de o que é uma spec válida
é de `experiment_config.validate_config`, que é função pura e é onde os testes
batem (decisão D4 da spec).

Neste ticket o comando **valida e sai**: emitir o `canonical.json` e as fatias
por arquitetura é o próximo passo da spec, e entra por baixo do mesmo entrypoint.

    python orchestrator/generate_scenarios.py --config config/experiment.toml

Sai com 0 se a spec é válida e com 1 se não é, imprimindo no stderr uma mensagem
que nomeia o registro ofensor — nada de default silencioso salvando uma matriz
experimental defeituosa.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from experiment_config import ConfigError, ExperimentConfig, validate_config

EXIT_OK = 0
EXIT_INVALID_CONFIG = 1


def load_config(path: Path) -> ExperimentConfig:
    """Lê o TOML do disco e devolve a configuração validada."""
    with path.open("rb") as handle:
        return validate_config(tomllib.load(handle))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="generate_scenarios.py",
        description="Valida a definição do Experimento (config/experiment.toml).",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="caminho do experiment.toml",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as error:
        print(f"{args.config}: {error}", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    print(
        f"{args.config}: valid "
        f"({len(config.codecs)} codecs, {len(config.pairs)} pairs, "
        f"{len(config.videos)} videos, {len(config.instances)} instances)"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
