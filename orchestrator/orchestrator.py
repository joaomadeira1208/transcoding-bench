#!/usr/bin/env python3
"""CLI do Orquestrador: um subcomando por passo da campanha — ver `orchestrator/README.md`."""

from __future__ import annotations

import argparse
import json
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
from instance_launch import (
    ENCODE_VOLUME_SIZE_GB,
    IMDS_HOP_LIMIT,
    REMOTE_REPO_DIR,
    REMOTE_WORK_DIR,
    EncodeTarget,
    LaunchError,
    encode_tags,
    encode_target,
    launch_encode,
    orphan_hint,
    render_user_data,
    wait_for_bootstrapped_instance,
)
from instance_wait import BootstrapError, WaitTimeout
from manifest_check import check_manifest
from masters_launch import mirror_differences, prepare_masters_command
from masters_plan import build_masters_plan
from preflight import (
    Outcome,
    PreflightError,
    Step,
    StepResult,
    encode_put_command,
    failed,
    perf_counter_values,
    perf_detail,
    perf_probe_command,
    render_table,
    summarize,
)
from scenario_plan import build_canonical_plan, build_instance_slices, serialize_plan

PROG = "orchestrator.py"

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENT_TOML = REPO_ROOT / "config" / "experiment.toml"

MASTERS_ROLE = "masters"
MASTERS_INSTANCE_TYPE = "c7g.xlarge"
MASTERS_VOLUME_SIZE_GB = 100

PREFLIGHT_INSTANCE_TYPE = "c7g.xlarge"
PREFLIGHT_NAME_TAG = "transcoding-bench-preflight"

MASTERS_PREFIX = "masters/"
MANIFEST_NAME = "manifest.json"

SCENARIOS_PREFIX = "scenarios/"
PREFLIGHT_PREFIX = "runs/preflight/"
SELF_CHECK_PREFIX = f"{PREFLIGHT_PREFIX}self-check/"
SELF_CHECK_OBJECT = "probe.txt"
SELF_CHECK_CONTENT = "preflight\n"

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
    prepare.set_defaults(run=lambda args, **common: prepare_masters(**common))
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
    check.set_defaults(
        run=lambda args, **common: preflight(
            instance_type=args.instance_type, bucket=args.bucket, **common
        )
    )
    args = parser.parse_args()

    infra_path = args.infra.expanduser()
    try:
        infra = _load_infra(infra_path)
        config = _load_config(EXPERIMENT_TOML)
        return args.run(args, infra=infra, config=config, work_dir=infra_path.parent)
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
            user_data=render_user_data(
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
        raise PreparationError(orphan_hint(f"role={MASTERS_ROLE}", error)) from error

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

    host = wait_for_bootstrapped_instance(instance_id, report=_report)

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
        _step(results, Step.STS, sts_caller_identity)
        _step(results, Step.BUCKETS, lambda: _list_both_buckets(infra, target))
        _step(results, Step.SSM, lambda: _read_ssh_key(infra.ssh_private_key_parameter_name))
        commit = _step(results, Step.GIT, git_rev_parse)
        _step(results, Step.SYNC, lambda: _sync_between_buckets(infra, work_dir))

        encode = _step(
            results,
            Step.AMI,
            lambda: encode_target(config, infra.amis, instance_type),
            detail=lambda chosen: f"{instance_type} ({chosen.instance.arch}): {chosen.image_id}",
        )
        slice_key = f"{SCENARIOS_PREFIX}{encode.instance.id}.json"
        instance_id = _step(
            results,
            Step.LAUNCH,
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

        host = _step(
            results,
            Step.BOOTSTRAP,
            lambda: wait_for_bootstrapped_instance(instance_id, report=_report),
        )
        events = config.instrumentation.pmu_events
        _step(
            results,
            Step.PERF,
            lambda: perf_counter_values(
                ssh_exec(host, perf_probe_command(events), timeout=PROBE_TIMEOUT_SECONDS), events
            ),
            detail=perf_detail,
        )
        _step(results, Step.ENCODE_PUT, lambda: _put_from_container(host, target, instance_id))
    except _Aborted:
        pass
    finally:
        if instance_id is not None:
            with suppress(_Aborted):
                _step(results, Step.TERMINATE, lambda: _terminate(instance_id))

    table = summarize(results)
    print(render_table(table))
    if failed(table):
        return EXIT_FAILURE

    _report(
        "o preflight passou; o degrau seguinte da escada é o primeiro bloco do piloto, "
        "que é a primeira Execução real (ADR-0022)"
    )
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

    return launch_encode(
        target=encode,
        infra=infra,
        commit=commit,
        bucket=bucket,
        slice_key=slice_key,
        manifest_key=f"{MASTERS_PREFIX}{MANIFEST_NAME}",
        masters_prefix=MASTERS_PREFIX,
        volume_size_gb=ENCODE_VOLUME_SIZE_GB,
        tags=encode_tags(name=PREFLIGHT_NAME_TAG, commit=commit),
    )


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
    step: Step,
    action: Callable[[], Observed],
    detail: Callable[[Observed], str] = str,
) -> Observed:
    """Roda um passo, registra como ele terminou e devolve o que ele observou."""
    _report(f"{step.value}: rodando")
    try:
        value = action()
    except _FAILURES as error:
        results.append(StepResult(step, Outcome.FAILED, str(error)))
        raise _Aborted from error
    results.append(StepResult(step, Outcome.PASSED, detail(value)))
    return value


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
    LaunchError,
    PreparationError,
    PreflightError,
    OSError,
    json.JSONDecodeError,
    tomllib.TOMLDecodeError,
)


if __name__ == "__main__":
    raise SystemExit(main())
