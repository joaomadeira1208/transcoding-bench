#!/usr/bin/env python3
"""CLI do Orquestrador: um subcomando por passo da campanha (ADR-0010).

    python orchestrator/orchestrator.py --infra ~/work/infra.json prepare-masters

Roda na instância do Orquestrador, dentro de `tmux`: os subcomandos bloqueiam por
horas, e a sessão SSH que cair não pode levar a campanha junto.
"""

from __future__ import annotations

import argparse
import json
import shlex
import string
import sys
import tomllib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from command_output import OutputError
from experiment_config import ConfigError, ExperimentConfig, validate_config
from external import (
    ExternalCommandError,
    cloud_init_status,
    described_instance,
    git_rev_parse,
    run_instances,
    s3_cp,
    s3_list_prefix,
    s3_sync,
    ssh_exec,
    terminate_instances,
)
from infra_config import InfraConfig, InfraError, parse_infra
from instance_wait import (
    BootstrapError,
    WaitTimeout,
    wait_for_bootstrap,
    wait_for_instance_ready,
)
from manifest_check import check_manifest
from masters_launch import mirror_differences, prepare_masters_command
from masters_plan import build_masters_plan
from scenario_plan import serialize_plan

PROG = "orchestrator.py"

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENT_TOML = REPO_ROOT / "config" / "experiment.toml"
USER_DATA_TEMPLATE = REPO_ROOT / "orchestrator" / "user-data.sh"

REPO_URL = "https://github.com/joaomadeira1208/transcoding-bench.git"

# O caminho do clone é literal no `user-data.sh`, e não um placeholder: mudá-lo
# lá sem mudá-lo aqui faz o `docker run` montar um diretório que não existe.
REMOTE_REPO_DIR = "/home/ubuntu/transcoding-bench"
REMOTE_WORK_DIR = "/home/ubuntu/work"

MASTERS_ROLE = "masters"
MASTERS_INSTANCE_TYPE = "c7g.xlarge"
MASTERS_VOLUME_SIZE_GB = 100

MASTERS_PREFIX = "masters/"
MANIFEST_NAME = "manifest.json"

# O `aws s3 cp` da preparação roda dentro do container (ADR-0018).
IMDS_HOP_LIMIT = 2

READY_TIMEOUT_SECONDS = 600.0

# O bootstrap dos papéis de medição termina no `docker build`, que compila o
# FFmpeg da ADR-0008 (10 a 20 min, ADR-0013).
BOOTSTRAP_TIMEOUT_SECONDS = 3600.0

EXIT_OK = 0
EXIT_FAILURE = 1


class PreparationError(Exception):
    """O resultado que um passo do `prepare-masters` observou e recusou."""


def main() -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Dispara os passos da campanha sobre a infraestrutura do Terraform.",
    )
    parser.add_argument(
        "--infra",
        required=True,
        type=Path,
        help="caminho do infra.json que o bootstrap gravou no work dir",
    )
    subcommands = parser.add_subparsers(dest="subcommand", required=True)
    prepare = subcommands.add_parser(
        "prepare-masters",
        help="prepara os Masters numa instância efêmera e os espelha no bucket do piloto",
        description=(
            "Lança a instância de preparação, dispara os seis Masters por SSH, espelha "
            "masters/ da campanha no piloto e termina a instância. O manifesto baixado "
            "fica ao lado do arquivo de infra, para o gate humano da ADR-0012."
        ),
    )
    prepare.set_defaults(run=prepare_masters)
    args = parser.parse_args()

    infra_path = args.infra.expanduser()
    try:
        infra = _load_infra(infra_path)
        config = _load_config(EXPERIMENT_TOML)
        return args.run(infra=infra, config=config, work_dir=infra_path.parent)
    except _FAILURES as error:
        _fail(error)
        return EXIT_FAILURE


def prepare_masters(*, infra: InfraConfig, config: ExperimentConfig, work_dir: Path) -> int:
    """Os seis Masters nos dois buckets, por uma instância que não sobrevive ao passo."""
    plan = serialize_plan(build_masters_plan(config))
    commit = git_rev_parse()

    try:
        instance_id = run_instances(
            instance_type=MASTERS_INSTANCE_TYPE,
            image_id=infra.amis.encode_arm64,
            subnet_id=infra.subnet_id,
            security_group_id=infra.security_groups.ephemeral,
            instance_profile=infra.instance_profiles.masters,
            key_name=infra.key_pair_name,
            user_data=_render_user_data(
                commit=commit,
                role=MASTERS_ROLE,
                role_args=["--work-dir", REMOTE_WORK_DIR],
            ),
            volume_size_gb=MASTERS_VOLUME_SIZE_GB,
            imds_hop_limit=IMDS_HOP_LIMIT,
            tags={
                "Name": f"transcoding-bench-{MASTERS_ROLE}",
                "role": MASTERS_ROLE,
                "commit": commit,
            },
        )
    except OutputError as error:
        # O único caminho em que a instância pode ter subido sem um id que o
        # `finally` abaixo termine: a resposta do lançamento é que veio ilegível.
        raise PreparationError(
            f"o run-instances pode ter lançado uma instância que este passo não sabe nomear: "
            f"procure por role={MASTERS_ROLE} no describe-instances e termine-a à mão ({error})"
        ) from error

    _report(f"{instance_id}: lançada no commit {commit}")

    try:
        _drive_preparation(instance_id, plan=plan, infra=infra, config=config, work_dir=work_dir)
    except _FAILURES as error:
        # A falha é impressa aqui, e não deixada para o `main`: se o terminate
        # abaixo também falhar, é a exceção dele que sobe, e esta causa — a que
        # explica o passo — sumiria do log do tmux.
        _fail(error)
        return EXIT_FAILURE
    finally:
        _report(f"{instance_id}: terminando")
        terminate_instances([instance_id])

    _report("os Masters estão nos dois buckets; o gate humano da ADR-0012 é o próximo passo")
    return EXIT_OK


def _drive_preparation(
    instance_id: str,
    *,
    plan: str,
    infra: InfraConfig,
    config: ExperimentConfig,
    work_dir: Path,
) -> None:
    campaign = infra.buckets.campaign
    pilot = infra.buckets.pilot

    instance = wait_for_instance_ready(
        lambda: described_instance(instance_id),
        instance_id=instance_id,
        timeout=READY_TIMEOUT_SECONDS,
    )
    host = instance.private_ip
    _report(f"{instance_id}: running em {host}, esperando o cloud-init")
    wait_for_bootstrap(
        lambda: cloud_init_status(host),
        instance_id=instance_id,
        timeout=BOOTSTRAP_TIMEOUT_SECONDS,
    )

    _report(f"{instance_id}: preparando os Masters em s3://{campaign}/{MASTERS_PREFIX}")
    ssh_exec(
        host,
        prepare_masters_command(
            plan=plan,
            bucket=campaign,
            repo_dir=REMOTE_REPO_DIR,
            work_dir=REMOTE_WORK_DIR,
        ),
    )

    _report(f"espelhando {MASTERS_PREFIX} de {campaign} em {pilot}")
    s3_sync(f"s3://{campaign}/{MASTERS_PREFIX}", f"s3://{pilot}/{MASTERS_PREFIX}")
    differences = mirror_differences(
        s3_list_prefix(campaign, MASTERS_PREFIX),
        s3_list_prefix(pilot, MASTERS_PREFIX),
    )
    if differences:
        raise PreparationError(_lines("o espelho dos dois buckets diverge", differences))

    manifest = work_dir / MANIFEST_NAME
    s3_cp(f"s3://{campaign}/{MASTERS_PREFIX}{MANIFEST_NAME}", str(manifest))
    try:
        downloaded = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PreparationError(f"{manifest}: {error}") from error

    errors = check_manifest(downloaded, config)
    if errors:
        raise PreparationError(_lines(f"{manifest} recusado pelo checker", errors))
    _report(f"o manifesto dos seis Masters está em {manifest}")


def _render_user_data(*, commit: str, role: str, role_args: Sequence[str]) -> str:
    """O user-data fino de um papel (ADR-0013/0021), com os argumentos do bootstrap dele."""
    template = string.Template(USER_DATA_TEMPLATE.read_text(encoding="utf-8"))
    return template.substitute(
        repo_url=REPO_URL,
        commit=commit,
        role=role,
        role_args=shlex.join(role_args),
    )


def _load_infra(path: Path) -> InfraConfig:
    """O arquivo de infra validado, com o caminho em toda mensagem de erro."""
    try:
        return parse_infra(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, InfraError) as error:
        raise InfraError(f"{path}: {error}") from error


def _load_config(path: Path) -> ExperimentConfig:
    try:
        with path.open("rb") as handle:
            return validate_config(tomllib.load(handle))
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as error:
        raise ConfigError(f"{path}: {error}") from error


def _lines(headline: str, details: Sequence[str]) -> str:
    return "\n".join([headline, *(f"  {detail}" for detail in details)])


def _report(message: str) -> None:
    """O progresso, com hora: o passo dura horas e o pesquisador o lê de dentro do tmux."""
    print(f"{datetime.now(UTC):%H:%M:%S} {PROG}: {message}", file=sys.stderr)


def _fail(error: Exception) -> None:
    print(f"{PROG}: {error}", file=sys.stderr)


_FAILURES = (
    ExternalCommandError,
    OutputError,
    InfraError,
    ConfigError,
    WaitTimeout,
    BootstrapError,
    PreparationError,
    OSError,
    json.JSONDecodeError,
    tomllib.TOMLDecodeError,
)


if __name__ == "__main__":
    raise SystemExit(main())
