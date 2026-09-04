#!/usr/bin/env python3
"""CLI do contrato do `meta.json` — casca fina sobre o modelo de `run_meta`.

python analysis/validate_meta.py runs/<run_id>/meta.json
python analysis/validate_meta.py --emit-schema > analysis/meta.schema.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError
from run_meta import load_meta, offending_fields, render_json_schema

EXIT_OK = 0
EXIT_INVALID_META = 1
# Não se sabe nada sobre o conteúdo, então dizer "inválido" seria mentir.
EXIT_UNREADABLE = 2


def format_errors(path: Path, error: ValidationError) -> str:
    """Uma linha por campo ofensor, prefixada pelo arquivo que as produziu."""
    return "\n".join(f"{path}: {field}" for field in offending_fields(error))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida um meta.json contra o modelo estrito do papel analysis/."
    )
    parser.add_argument(
        "meta",
        nargs="?",
        type=Path,
        help="caminho do meta.json a validar",
    )
    parser.add_argument(
        "--emit-schema",
        action="store_true",
        help="imprime o JSON Schema do modelo no stdout e sai",
    )
    args = parser.parse_args()

    if args.emit_schema:
        if args.meta is not None:
            parser.error("--emit-schema não recebe arquivo")
        sys.stdout.write(render_json_schema())
        return EXIT_OK

    if args.meta is None:
        parser.error("informe o meta.json a validar (ou use --emit-schema)")

    try:
        raw = args.meta.read_bytes()
    except OSError as error:
        print(f"{args.meta}: {error.strerror}", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        load_meta(raw)
    except ValidationError as error:
        print(format_errors(args.meta, error), file=sys.stderr)
        return EXIT_INVALID_META

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
