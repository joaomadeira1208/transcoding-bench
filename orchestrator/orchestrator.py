#!/usr/bin/env python3
"""CLI do Orquestrador: um subcomando por passo da campanha — ver `orchestrator/README.md`."""

from __future__ import annotations

import argparse
import json
import shlex
import string
import sys
import tomllib
from collections.abc import Callable, Sequence
from contextlib import suppress
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
    s3_rm,
    s3_sync,
    ssh_exec,
    ssm_get_parameter,
    sts_caller_identity,
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
from preflight import (
    AMI,
    BOOTSTRAP,
    BUCKETS,
    ENCODE_PUT,
    GIT,
    LAUNCH,
    PERF,
    PERF_EVENT,
    SSM,
    STS,
    SYNC,
    TERMINATE,
    EncodeTarget,
    Outcome,
    PreflightError,
    StepResult,
    encode_put_command,
    encode_target,
    failed,
    perf_counter_value,
    perf_probe_command,
    render_table,
    summarize,
)
from scenario_plan import build_canonical_plan, build_instance_slices, serialize_plan

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

# O papel do bootstrap e o valor da tag `role`. A policy só autoriza o
# `TerminateInstances` sobre `encode`, `judge` e `masters` (ADR-0016): uma tag
# `preflight` deixaria a instância deste passo interminável por quem a lançou.
ENCODE_ROLE = "encode"
PREFLIGHT_INSTANCE_TYPE = "c7g.xlarge"
PREFLIGHT_VOLUME_SIZE_GB = 200
PREFLIGHT_NAME_TAG = "transcoding-bench-preflight"

MASTERS_PREFIX = "masters/"
MANIFEST_NAME = "manifest.json"

SCENARIOS_PREFIX = "scenarios/"
PREFLIGHT_PREFIX = "runs/preflight/"
SELF_CHECK_PREFIX = f"{PREFLIGHT_PREFIX}self-check/"
SELF_CHECK_OBJECT = "probe.txt"
SELF_CHECK_CONTENT = "preflight\n"

# O `aws s3 cp` da preparação roda dentro do container (ADR-0018).
IMDS_HOP_LIMIT = 2

READY_TIMEOUT_SECONDS = 600.0

# O bootstrap dos papéis de medição termina no `docker build`, que compila o
# FFmpeg da ADR-0008 (10 a 20 min, ADR-0013).
BOOTSTRAP_TIMEOUT_SECONDS = 3600.0

# Sem teto, o `curl` do `prepare.sh` que estola pendura o CLI para sempre, com a
# instância faturando e indistinguível das ~2 h de silêncio do caso normal.
PREPARE_TIMEOUT_SECONDS = 10800.0

# Os dois `docker run` do preflight são o `perf stat` sobre um comando trivial e
# um `s3 cp` de nove bytes: o que leva minutos ali é o container subindo.
PROBE_TIMEOUT_SECONDS = 300.0

EXIT_OK = 0
EXIT_FAILURE = 1


class PreparationError(Exception):
    """O resultado que um passo do `prepare-masters` observou e recusou."""


class _Aborted(Exception):
    """Um passo do `preflight` falhou; o que vinha depois dele não roda."""


# O que o `main` já passa a todo subcomando; o resto do `argv` parseado é dele.
_COMMON_ARGUMENTS = frozenset({"infra", "subcommand", "run"})


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
    check = subcommands.add_parser(
        "preflight",
        help="prova o caminho do encode numa instância descartável, antes de a fatura correr",
        description=(
            "Roda a auto-checagem do Orquestrador, lança uma instância de encode do tipo "
            "pedido com a fatia daquela arquitetura, confere o perf e o PutObject de dentro "
            "do container, termina a instância e imprime a tabela passou/falhou."
        ),
    )
    check.add_argument(
        "--instance-type",
        default=PREFLIGHT_INSTANCE_TYPE,
        help=f"tipo da instância descartável (default: {PREFLIGHT_INSTANCE_TYPE})",
    )
    check.add_argument(
        "--bucket",
        default=None,
        help="bucket que recebe a fatia e o objeto de prova (default: o do piloto)",
    )
    check.set_defaults(run=preflight)
    args = parser.parse_args()

    infra_path = args.infra.expanduser()
    try:
        infra = _load_infra(infra_path)
        config = _load_config(EXPERIMENT_TOML)
        return args.run(
            infra=infra,
            config=config,
            work_dir=infra_path.parent,
            **{name: value for name, value in vars(args).items() if name not in _COMMON_ARGUMENTS},
        )
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
        raise PreparationError(_orphan_hint(f"role={MASTERS_ROLE}", error)) from error

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

    host = _wait_for_bootstrapped_instance(instance_id)

    _report(f"{instance_id}: preparando os Masters em s3://{campaign}/{MASTERS_PREFIX}")
    ssh_exec(
        host,
        prepare_masters_command(
            plan=plan,
            bucket=campaign,
            repo_dir=REMOTE_REPO_DIR,
            work_dir=REMOTE_WORK_DIR,
        ),
        timeout=PREPARE_TIMEOUT_SECONDS,
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


def preflight(
    *,
    infra: InfraConfig,
    config: ExperimentConfig,
    work_dir: Path,
    instance_type: str,
    bucket: str | None,
) -> int:
    """O caminho do encode inteiro numa instância de poucos minutos, e a tabela dele.

    A escada é linear e para no primeiro passo que falhar: o que vem depois de um
    `PassRole` recusado não tem o que provar. A instância, se chegou a subir, é
    terminada em todo caminho de saída.
    """
    target = bucket or infra.buckets.pilot
    results: list[StepResult] = []
    instance_id: str | None = None

    try:
        _step(results, STS, sts_caller_identity)
        _step(results, BUCKETS, lambda: _list_both_buckets(infra, target))
        _step(results, SSM, lambda: _read_ssh_key(infra.ssh_private_key_parameter_name))
        commit = _step(results, GIT, git_rev_parse)
        _step(results, SYNC, lambda: _sync_between_buckets(infra, work_dir))

        encode = _step(
            results,
            AMI,
            lambda: encode_target(config, infra.amis, instance_type),
            detail=lambda chosen: f"{instance_type} ({chosen.instance.arch}): {chosen.image_id}",
        )
        slice_key = f"{SCENARIOS_PREFIX}{encode.instance.id}.json"
        instance_id = _step(
            results,
            LAUNCH,
            lambda: _launch_encode(
                encode=encode,
                infra=infra,
                config=config,
                commit=commit,
                bucket=target,
                slice_key=slice_key,
                work_dir=work_dir,
            ),
            detail=lambda launched: f"{launched} no commit {commit}, fatia em {slice_key}",
        )

        host = _step(results, BOOTSTRAP, lambda: _wait_for_bootstrapped_instance(instance_id))
        _step(
            results,
            PERF,
            lambda: perf_counter_value(
                ssh_exec(host, perf_probe_command(), timeout=PROBE_TIMEOUT_SECONDS), PERF_EVENT
            ),
            detail=lambda counted: f"{PERF_EVENT} = {counted:.0f} dentro do container",
        )
        _step(results, ENCODE_PUT, lambda: _put_from_container(host, target, instance_id))
    except _Aborted:
        pass
    finally:
        if instance_id is not None:
            with suppress(_Aborted):
                _step(results, TERMINATE, lambda: _terminate(instance_id))

    table = summarize(results)
    print(render_table(table))
    if failed(table):
        return EXIT_FAILURE

    _report("o preflight passou; o degrau seguinte da escada é o smoke AWS (ADR-0022)")
    return EXIT_OK


def _list_both_buckets(infra: InfraConfig, bucket: str) -> str:
    """O `ListBucket` dos dois, e o manifesto no bucket que a instância vai ler."""
    names = list(dict.fromkeys((infra.buckets.campaign, infra.buckets.pilot, bucket)))
    listings = {name: s3_list_prefix(name, MASTERS_PREFIX) for name in names}

    manifest = f"{MASTERS_PREFIX}{MANIFEST_NAME}"
    if not any(listed.key == manifest for listed in listings[bucket]):
        raise PreflightError(
            f"s3://{bucket}/{manifest} não existe: o preflight roda depois do prepare-masters, "
            f"e sem os Masters a instância só descobre isso depois do build"
        )
    return "; ".join(
        f"{name}: {len(objects)} em {MASTERS_PREFIX}" for name, objects in listings.items()
    )


def _read_ssh_key(parameter_name: str) -> str:
    # O que volta do parâmetro é a chave privada de SSH (ADR-0016) e o destino
    # desta string é o log do tmux: daqui sai o tamanho, nunca o valor lido.
    return f"{parameter_name}: {len(ssm_get_parameter(parameter_name))} caracteres decriptados"


def _sync_between_buckets(infra: InfraConfig, work_dir: Path) -> str:
    """O `s3 sync` do `prepare-masters`, sobre um objeto de poucos bytes."""
    campaign = infra.buckets.campaign
    pilot = infra.buckets.pilot
    key = f"{SELF_CHECK_PREFIX}{SELF_CHECK_OBJECT}"
    probe = work_dir / SELF_CHECK_OBJECT
    probe.write_text(SELF_CHECK_CONTENT, encoding="utf-8")

    try:
        s3_cp(str(probe), f"s3://{campaign}/{key}")
        try:
            s3_sync(f"s3://{campaign}/{SELF_CHECK_PREFIX}", f"s3://{pilot}/{SELF_CHECK_PREFIX}")
            if not s3_list_prefix(pilot, key):
                raise PreflightError(f"s3://{pilot}/{key}: o sync não deixou o objeto no destino")
        finally:
            s3_rm(f"s3://{campaign}/{key}")
            s3_rm(f"s3://{pilot}/{key}")
    finally:
        probe.unlink(missing_ok=True)

    return f"{key}: copiado de {campaign} para {pilot} e apagado dos dois"


def _launch_encode(
    *,
    encode: EncodeTarget,
    infra: InfraConfig,
    config: ExperimentConfig,
    commit: str,
    bucket: str,
    slice_key: str,
    work_dir: Path,
) -> str:
    """Sobe a fatia daquela arquitetura e lança a instância que vai consumi-la."""
    plan = build_instance_slices(build_canonical_plan(config)).get(encode.instance.id)
    if plan is None:
        raise PreflightError(
            f"{encode.instance.id}: o plano do experiment.toml não tem fatia para esta instância"
        )

    local = work_dir / f"{encode.instance.id}.json"
    local.write_text(serialize_plan(plan), encoding="utf-8")
    s3_cp(str(local), f"s3://{bucket}/{slice_key}")

    role_args = [
        "--work-dir",
        REMOTE_WORK_DIR,
        "--bucket",
        bucket,
        "--plan-key",
        slice_key,
        "--manifest-key",
        f"{MASTERS_PREFIX}{MANIFEST_NAME}",
        "--masters-prefix",
        MASTERS_PREFIX,
    ]
    try:
        return run_instances(
            instance_type=encode.instance.instance_type,
            image_id=encode.image_id,
            subnet_id=infra.subnet_id,
            security_group_id=infra.security_groups.ephemeral,
            instance_profile=infra.instance_profiles.encode,
            key_name=infra.key_pair_name,
            user_data=_render_user_data(commit=commit, role=ENCODE_ROLE, role_args=role_args),
            volume_size_gb=PREFLIGHT_VOLUME_SIZE_GB,
            imds_hop_limit=IMDS_HOP_LIMIT,
            tags={"Name": PREFLIGHT_NAME_TAG, "role": ENCODE_ROLE, "commit": commit},
        )
    except OutputError as error:
        raise PreflightError(_orphan_hint(f"Name={PREFLIGHT_NAME_TAG}", error)) from error


def _orphan_hint(tag: str, error: Exception) -> str:
    return (
        f"o run-instances pode ter lançado uma instância que este passo não sabe nomear: "
        f"procure por {tag} no describe-instances e termine-a à mão ({error})"
    )


def _wait_for_bootstrapped_instance(instance_id: str) -> str:
    """Espera `running` com IP privado e depois o `cloud-init`, e devolve o host."""
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
    return host


def _put_from_container(host: str, bucket: str, instance_id: str) -> str:
    """O `PutObject` do papel `encode` pelo caminho real, e a limpeza pelo Orquestrador."""
    key = f"{PREFLIGHT_PREFIX}{instance_id}.txt"
    ssh_exec(host, encode_put_command(bucket=bucket, key=key), timeout=PROBE_TIMEOUT_SECONDS)

    written = s3_list_prefix(bucket, key)
    if not written:
        raise PreflightError(
            f"s3://{bucket}/{key}: o cp do container não deixou o objeto no bucket"
        )
    s3_rm(f"s3://{bucket}/{key}")
    return f"{key}: {written[0].size} bytes escritos pelo container, listados e apagados"


def _terminate(instance_id: str) -> str:
    _report(f"{instance_id}: terminando")
    terminate_instances([instance_id])
    return instance_id


def _step[Observed](
    results: list[StepResult],
    step: str,
    action: Callable[[], Observed],
    detail: Callable[[Observed], str] = str,
) -> Observed:
    """Roda um passo, registra como ele terminou e devolve o que ele observou."""
    _report(f"{step}: rodando")
    try:
        value = action()
    except _FAILURES as error:
        results.append(StepResult(step, Outcome.FAILED, str(error)))
        raise _Aborted from error
    results.append(StepResult(step, Outcome.PASSED, detail(value)))
    return value


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
    PreflightError,
    OSError,
    json.JSONDecodeError,
    tomllib.TOMLDecodeError,
)


if __name__ == "__main__":
    raise SystemExit(main())
