#!/usr/bin/env python3
"""CLI do contrato do manifesto — casca fina sobre `manifest_check`.

    python orchestrator/validate_manifest.py \\
        masters/manifest.json --config config/experiment.toml
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from experiment_config import ConfigError, validate_config
from manifest_check import check_manifest

EXIT_OK = 0
EXIT_INVALID_MANIFEST = 1
EXIT_UNREADABLE = 2


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="validate_manifest.py",
        description=(
            "Valida um masters/manifest.json contra a spec: forma, tipos e cada Master "
            "com a geometria, o codec, a cadência e a fonte que o config/experiment.toml pede."
        ),
    )
    parser.add_argument(
        "manifest",
        type=Path,
        help="caminho do manifest.json a validar",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="caminho do experiment.toml contra o qual o manifesto é conferido",
    )
    args = parser.parse_args()

    try:
        with args.config.open("rb") as handle:
            config = validate_config(tomllib.load(handle))
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as error:
        print(f"{args.config}: {error}", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        raw = args.manifest.read_bytes()
    except OSError as error:
        print(f"{args.manifest}: {error.strerror}", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as error:
        print(f"{args.manifest}: not valid JSON: {error}", file=sys.stderr)
        return EXIT_INVALID_MANIFEST

    try:
        errors = check_manifest(manifest, config)
    except ConfigError as error:
        print(f"{args.config}: {error}", file=sys.stderr)
        return EXIT_UNREADABLE

    if not errors:
        return EXIT_OK

    for error in errors:
        print(f"{args.manifest}: {error}", file=sys.stderr)
    return EXIT_INVALID_MANIFEST


if __name__ == "__main__":
    raise SystemExit(main())
