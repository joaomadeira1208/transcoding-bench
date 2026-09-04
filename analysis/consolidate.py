#!/usr/bin/env python3
"""Projeta uma árvore `runs/` na tabela analítica em Parquet (ADR-0007).

python analysis/consolidate.py --runs runs/ --out runs.parquet

Casca fina sobre `run_table`: percorre o diretório, lê os artefatos e escreve o
Parquet. Recebe um diretório **local** — o `aws s3 sync` que o traz do bucket é
passo separado (ADR-0014), e é o que mantém a consolidação testável sem rede.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pyarrow.parquet as pq
from pydantic import ValidationError
from run_meta import load_meta, offending_fields
from run_table import RawRun, consolidate_runs, summarize

EXIT_OK = 0
EXIT_INVALID_RUN = 1
# Não se sabe nada sobre o conteúdo, então dizer "inválido" seria mentir.
EXIT_UNREADABLE = 2


class InvalidRun(Exception):
    """Execução que a consolidação recusa a ler, com o arquivo ofensor nomeado."""


def read_run(run_dir: Path) -> RawRun:
    """Uma Execução do disco: `meta.json` validado, artefatos crus como texto."""
    meta_path = run_dir / "meta.json"
    if not meta_path.is_file():
        raise InvalidRun(f"{meta_path}: ausente")

    try:
        meta = load_meta(meta_path.read_bytes())
    except ValidationError as error:
        raise InvalidRun(f"{meta_path}: {'; '.join(offending_fields(error))}") from error

    return RawRun(
        meta=meta,
        time=_read_artifact(run_dir / "time.json"),
        perf=_read_artifact(run_dir / "perf.json"),
        pidstat=_read_artifact(run_dir / "pidstat.txt"),
        ffmpeg=_read_artifact(run_dir / "ffmpeg.log"),
        sha256=_read_artifact(run_dir / "output.sha256"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolida uma árvore runs/ na tabela analítica em Parquet."
    )
    parser.add_argument("--runs", required=True, type=Path, help="diretório com os runs/{run_id}/")
    parser.add_argument("--out", required=True, type=Path, help="caminho do Parquet a escrever")
    args = parser.parse_args()

    if not args.runs.is_dir():
        print(f"{args.runs}: não é um diretório", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        runs = [read_run(run_dir) for run_dir in sorted(_run_dirs(args.runs))]
    except InvalidRun as error:
        print(error, file=sys.stderr)
        return EXIT_INVALID_RUN
    except OSError as error:
        print(f"{error.filename}: {error.strerror}", file=sys.stderr)
        return EXIT_UNREADABLE

    result = consolidate_runs(runs)
    for artifact in result.unreadable:
        print(f"{args.runs}/{artifact}", file=sys.stderr)

    try:
        pq.write_table(result.table, args.out)
    except OSError as error:
        print(f"{args.out}: {error.strerror}", file=sys.stderr)
        return EXIT_UNREADABLE

    print(summarize(result))
    return EXIT_OK


def _run_dirs(runs: Path) -> list[Path]:
    return [path for path in runs.iterdir() if path.is_dir()]


def _read_artifact(path: Path) -> str | None:
    """O texto do artefato, ou `None` para o que a Execução não deixou.

    `errors="replace"`: o `ffmpeg.log` é stderr de terceiros, e um byte inválido
    nele não é motivo para a tabela inteira não existir.
    """
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    sys.exit(main())
