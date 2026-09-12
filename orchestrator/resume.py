#!/usr/bin/env python3
"""CLI da retomada: decide o que falta e escreve as fatias reduzidas (ADR-0012).

    python orchestrator/resume.py --config config/experiment.toml \\
        --bucket <campanha> --out ~/work/resume [--exclude-commit <sha>]...

Casca sobre `resume_plan`: sincroniza os `meta.json` do bucket, valida cada um
pelo `meta_check`, imprime o relatório por arquitetura e escreve uma fatia
reduzida por arquitetura com pendência. **Não lança nada** — quem executa a
fatia reduzida é o `orchestrator.py run --slices`.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from experiment_config import ConfigError
from external import RUN_META_FILENAME, RUNS_PREFIX, ExternalCommandError, s3_sync_run_metas
from generate_scenarios import SLICE_FILENAME, load_config, write_plans
from meta_check import MetaError, check_meta
from resume_plan import reduced_slices, render_report, resume
from scenario_plan import build_canonical_plan

EXIT_OK = 0
EXIT_INVALID_META = 1
EXIT_UNREADABLE = 2

COMMIT_SHA_DIGITS = 40

_HEX_DIGITS = frozenset("0123456789abcdef")


def excluded_commit(value: str) -> str:
    """O SHA completo que o `meta.json` registra, e não a abreviação do `git log`.

    Um `--exclude-commit ffd4f43` não casaria com `commit` nenhum do bucket, e a
    retomada sairia zero declarando completos exatamente os blocos que o hotfix
    contaminou — a falha que este argumento existe para evitar.
    """
    if len(value) != COMMIT_SHA_DIGITS or set(value) - _HEX_DIGITS:
        raise argparse.ArgumentTypeError(
            f"esperava os {COMMIT_SHA_DIGITS} dígitos hexadecimais minúsculos que o "
            f"meta.json registra (git rev-parse <ref>), veio {value!r}"
        )
    return value


def read_metas(runs: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Os `meta.json` da árvore sincronizada, com um aviso por Execução sem o seu.

    O arquivo ofensor é nomeado pela **chave no bucket**: a árvore vive num
    diretório temporário que já não existe quando alguém vai procurar o objeto.
    """
    metas: list[dict[str, Any]] = []
    warnings: list[str] = []
    for run_dir in sorted(path for path in runs.iterdir() if path.is_dir()):
        key = f"{RUNS_PREFIX}{run_dir.name}/{RUN_META_FILENAME}"
        meta_path = run_dir / RUN_META_FILENAME
        if not meta_path.is_file():
            warnings.append(f"{key}: ausente, Execução ignorada")
            continue
        try:
            metas.append(check_meta(meta_path.read_bytes()))
        except MetaError as error:
            raise MetaError(f"{key}: {error}") from error
    return metas, warnings


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="resume.py",
        description=(
            "Decide o que falta de uma campanha interrompida e escreve as fatias "
            "reduzidas que o 'orchestrator.py run --slices' executa."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="caminho do TOML que gerou o plano da campanha",
    )
    parser.add_argument(
        "--bucket",
        required=True,
        help="nome do bucket da campanha, de onde os meta.json são baixados",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="diretório onde as fatias reduzidas são escritas (criado se não existir)",
    )
    parser.add_argument(
        "--exclude-commit",
        action="append",
        default=[],
        type=excluded_commit,
        metavar="SHA",
        help=(
            "invalida todo bloco com Replicação vencedora neste commit; "
            "repetível (hotfix classe 1, ADR-0021)"
        ),
    )
    args = parser.parse_args()

    try:
        plan = build_canonical_plan(load_config(args.config))
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as error:
        print(f"{args.config}: {error}", file=sys.stderr)
        return EXIT_UNREADABLE

    with tempfile.TemporaryDirectory(prefix="resume-") as workspace:
        runs = Path(workspace)
        try:
            s3_sync_run_metas(args.bucket, runs)
        except ExternalCommandError as error:
            print(error, file=sys.stderr)
            return EXIT_UNREADABLE

        try:
            metas, warnings = read_metas(runs)
        except MetaError as error:
            print(error, file=sys.stderr)
            return EXIT_INVALID_META
        except OSError as error:
            print(f"{error.filename}: {error.strerror}", file=sys.stderr)
            return EXIT_UNREADABLE

    for warning in warnings:
        print(warning, file=sys.stderr)

    resumes = resume(plan, metas, excluded_commits=args.exclude_commit)
    slices = reduced_slices(plan, resumes)
    artifacts = {
        args.out / SLICE_FILENAME.format(instance=instance): reduced
        for instance, reduced in slices.items()
    }

    try:
        write_plans(artifacts, args.out)
    except OSError as error:
        print(f"{args.out}: {error.strerror}", file=sys.stderr)
        return EXIT_UNREADABLE

    print(render_report(resumes))
    for path in artifacts:
        print(f"fatia reduzida: {path}")
    if not artifacts:
        print("nada pendente: não há o que retomar")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
