#!/usr/bin/env python3
"""CLI do triage do Pass de qualidade: decide o que o Juiz julga e escreve o plano.

    python orchestrator/quality_triage.py --config config/pilot.toml \\
        --bucket <piloto> --out ~/work/quality

Casca sobre `quality_plan`: sincroniza os `meta.json` e os `output.sha256` do
bucket, recusa matriz incompleta com o relatório da retomada, imprime o
relatório do Pass e escreve o `plan.json`. **Não lança nada** — quem sobe o Juiz
é o `orchestrator.py judge` (ADR-0025).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path

from experiment_config import ConfigError
from external import ExternalCommandError, s3_sync_run_metas_and_hashes
from generate_scenarios import load_config
from meta_check import MetaError
from quality_plan import PLAN_FILENAME, TriageError, build_plan, render_report, triage
from resume_plan import InstanceResume, resume
from resume_plan import render_report as render_resume_report
from run_tree import read_runs
from scenario_plan import build_canonical_plan, serialize_plan

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNREADABLE = 2


def fresh_out_dir(value: str) -> Path:
    """Um `--out` que já tem plano é recusado, e recusado antes do `s3 sync`.

    O plano é o que o Juiz lê **e** o que a retenção usa depois para decidir qual
    `output.mkv` sobrevive. Sobrescrever o de um triage anterior no mesmo
    diretório deixaria a limpeza decidindo sobre representantes que o Juiz nunca
    viu.
    """
    out = Path(value)
    if (out / PLAN_FILENAME).exists():
        raise argparse.ArgumentTypeError(
            f"{out} já contém {PLAN_FILENAME}: cada triage escreve num diretório novo, "
            f"porque o plano é também o que a retenção lê depois do Pass"
        )
    return out


def incomplete_refusal(resumes: Sequence[InstanceResume], config: Path, bucket: str) -> str | None:
    """A recusa por matriz incompleta, com o relatório da retomada e o comando dela."""
    if not any(each.pending for each in resumes):
        return None
    return "\n".join(
        (
            "matriz incompleta: o Pass de qualidade só decide sobre blocos completos",
            render_resume_report(resumes),
            f"python orchestrator/resume.py --config {config} --bucket {bucket} "
            f"--out <diretório novo>",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="quality_triage.py",
        description=(
            "Agrupa as Replicações vencedoras por Cenário, escolhe um representante "
            "por bitstream distinto e escreve o plano que o Juiz executa."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="caminho do TOML que gerou o plano daquela campanha",
    )
    parser.add_argument(
        "--bucket",
        required=True,
        help="nome do bucket da campanha, de onde os meta.json e os hashes são baixados",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=fresh_out_dir,
        help=(
            "diretório onde o plano é escrito (criado se não existir; recusado se "
            "já contiver um plano)"
        ),
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as error:
        print(f"{args.config}: {error}", file=sys.stderr)
        return EXIT_UNREADABLE
    plan = build_canonical_plan(config)

    with tempfile.TemporaryDirectory(prefix="quality-triage-") as workspace:
        runs = Path(workspace)
        try:
            s3_sync_run_metas_and_hashes(args.bucket, runs)
        except ExternalCommandError as error:
            print(error, file=sys.stderr)
            return EXIT_UNREADABLE

        try:
            metas, hashes, warnings = read_runs(runs)
        except MetaError as error:
            print(error, file=sys.stderr)
            return EXIT_REFUSED
        except OSError as error:
            print(f"{error.filename}: {error.strerror}", file=sys.stderr)
            return EXIT_UNREADABLE

    for warning in warnings:
        print(warning, file=sys.stderr)

    refusal = incomplete_refusal(resume(plan, metas), args.config, args.bucket)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED

    try:
        triaged = triage(plan, metas, hashes)
    except TriageError as error:
        print(error, file=sys.stderr)
        return EXIT_REFUSED

    written = args.out / PLAN_FILENAME
    try:
        args.out.mkdir(parents=True, exist_ok=True)
        written.write_text(serialize_plan(build_plan(config, triaged)), encoding="utf-8")
    except OSError as error:
        print(f"{args.out}: {error.strerror}", file=sys.stderr)
        return EXIT_UNREADABLE

    print(render_report(triaged))
    print(f"plano: {written}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
