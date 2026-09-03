#!/usr/bin/env python3
"""CLI do contrato do `meta.json` — casca fina sobre o modelo.

Valida um `meta.json` avulso e sai com código não-zero se ele não for válido,
imprimindo no stderr uma mensagem que **nomeia o campo ofensor**: diagnosticar um
arquivo suspeito sem abrir um REPL, e — o motivo pelo qual ela existe agora — dar
ao `smoke/` uma caixa-preta para validar o `meta.json` que o bash acabou de
escrever. O smoke nunca importa; só invoca (ADR-0022, decisão D11).

    python analysis/validate_meta.py runs/<run_id>/meta.json

O outro modo emite o JSON Schema derivado do modelo, que é como o arquivo
commitado (e o anexo do artigo, ADR-0019) é regenerado:

    python analysis/validate_meta.py --emit-schema > analysis/meta.schema.json

O que é um `meta.json` válido é decisão de `run_meta`, e é lá que os testes do
contrato batem; aqui mora só o I/O e a tradução de erro em código de saída.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError
from run_meta import load_meta, render_json_schema

EXIT_OK = 0
EXIT_INVALID_META = 1
# Arquivo ilegível sai com o mesmo código que o argparse usa para invocação
# errada: não se sabe nada sobre o conteúdo, então dizer "inválido" seria mentir.
EXIT_UNREADABLE = 2


def format_errors(path: Path, error: ValidationError) -> str:
    """Uma linha por campo ofensor, prefixada pelo arquivo que as produziu."""
    return "\n".join(
        f"{path}: {'.'.join(str(part) for part in item['loc']) or '<raiz>'}: {item['msg']}"
        for item in error.errors()
    )


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
