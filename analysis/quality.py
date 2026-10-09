#!/usr/bin/env python3
"""Projeta uma árvore `quality/results/` nas duas tabelas do Pass (D19).

python analysis/quality.py --results quality/results --config config/pilot.toml \\
    --outputs quality_outputs.parquet --groups quality_groups.parquet

Casca fina sobre `quality_table`, pelas mesmas regras do `consolidate.py`: recebe
um diretório **local** — o `aws s3 sync` é passo do pesquisador.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

import pyarrow.parquet as pq
from json_contract import offending_fields
from judgement import load_judgement
from pydantic import ValidationError
from quality_table import RawJudgement, quality_definition, quality_tables, summarize

EXIT_OK = 0
EXIT_INVALID_JUDGEMENT = 1
EXIT_UNREADABLE = 2

PREFLIGHT_DIR = "preflight"


class InvalidJudgement(Exception):
    """Julgamento que a leitura recusa, com o arquivo ofensor nomeado."""


def read_judgement(result_dir: Path) -> RawJudgement:
    """Um output julgado do disco: `judge.json` validado, log cru."""
    judge_path = result_dir / "judge.json"
    if not judge_path.is_file():
        raise InvalidJudgement(f"{judge_path}: ausente")

    try:
        judgement = load_judgement(judge_path.read_bytes())
    except ValidationError as error:
        raise InvalidJudgement(f"{judge_path}: {'; '.join(offending_fields(error))}") from error

    log_path = result_dir / "vmaf.json"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else None
    return RawJudgement(judgement=judgement, vmaf_log=log)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Projeta uma árvore quality/results/ nas tabelas de outputs e de grupos."
    )
    parser.add_argument(
        "--results", required=True, type=Path, help="diretório com os {run_id}/ do Pass"
    )
    parser.add_argument("--config", required=True, type=Path, help="a definição do lançamento")
    parser.add_argument("--outputs", required=True, type=Path, help="Parquet de outputs a escrever")
    parser.add_argument("--groups", required=True, type=Path, help="Parquet de grupos a escrever")
    args = parser.parse_args()

    if not args.results.is_dir():
        print(f"{args.results}: não é um diretório", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        definition = quality_definition(tomllib.loads(args.config.read_text(encoding="utf-8")))
        judgements = [read_judgement(path) for path in _result_dirs(args.results)]
    except InvalidJudgement as error:
        print(error, file=sys.stderr)
        return EXIT_INVALID_JUDGEMENT
    except OSError as error:
        print(f"{error.filename}: {error.strerror}", file=sys.stderr)
        return EXIT_UNREADABLE
    except (KeyError, tomllib.TOMLDecodeError) as error:
        print(f"{args.config}: {error!r}", file=sys.stderr)
        return EXIT_UNREADABLE

    result = quality_tables(judgements, definition)
    for message in result.invalid:
        print(f"{args.results}/{message}", file=sys.stderr)

    try:
        pq.write_table(result.outputs, args.outputs)
        pq.write_table(result.groups, args.groups)
    except OSError as error:
        print(f"{error.filename or args.outputs}: {error.strerror}", file=sys.stderr)
        return EXIT_UNREADABLE

    print(summarize(result))
    return EXIT_OK


def _result_dirs(results: Path) -> list[Path]:
    return sorted(
        path for path in results.iterdir() if path.is_dir() and path.name != PREFLIGHT_DIR
    )


if __name__ == "__main__":
    sys.exit(main())
