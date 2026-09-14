#!/usr/bin/env python3
"""CLI do Orquestrador: um subcomando por passo da campanha — ver `orchestrator/README.md`."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from campaign_launch import (
    DISPATCH_LOG_NAME,
    ArchitectureSlice,
    Bootstrap,
    CampaignError,
    CampaignPlan,
    abort_reasons,
    dispatch_command,
    full_campaign,
    refuse_populated_runs,
    resumed_campaign,
    slice_key,
    slice_name,
)
from campaign_state import CampaignState, StateError, TrackedInstance, parse_state, serialize_state
from command_output import OutputError, S3Object, TruncatedListing, parse_dispatched_pid
from experiment_config import ConfigError, ExperimentConfig, validate_config
from external import (
    RUNS_PREFIX,
    CommandOutput,
    ExternalCommandError,
    git_rev_parse,
    run_instances,
    s3_cp,
    s3_list_prefix,
    s3_rm,
    s3_sync,
    ssh_capture,
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
    encode_name,
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
    SELF_CHECK_STEPS,
    Counter,
    Outcome,
    PreflightError,
    Step,
    StepResult,
    encode_put_command,
    failed,
    perf_counters,
    perf_detail,
    perf_probe_command,
    render_table,
    summarize,
)
from scenario_plan import (
    build_canonical_plan,
    build_instance_slices,
    serialize_plan,
    summarize_plan,
)
from vigilance import Vigilance

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

PREFLIGHT_PREFIX = "runs/preflight/"
SELF_CHECK_PREFIX = f"{PREFLIGHT_PREFIX}self-check/"
SELF_CHECK_OBJECT = "probe.txt"
SELF_CHECK_CONTENT = "preflight\n"

STATE_NAME = "state.json"
LOCAL_SCENARIOS_DIR = "scenarios"

RUN_TIMEOUT_SECONDS = 4 * 60 * 60
TOTAL_TIMEOUT_SECONDS = 72 * 60 * 60

# Sem teto, o `curl` do `prepare.sh` que estola pendura o CLI para sempre, com a
# instância faturando e indistinguível das ~2 h de silêncio do caso normal.
PREPARE_TIMEOUT_SECONDS = 10800.0

# O probe do `perf` encoda segundos de um Master de verdade, e no pior par do
# plano isso é 4K num encoder lento: o teto é generoso porque o que ele compra é
# a instância ser terminada sozinha se o comando travar, não cortar um encode.
PERF_PROBE_TIMEOUT_SECONDS = 1800.0

# O do `s3 cp` de nove bytes continua curto: o que leva minutos ali é o container
# subindo, e um teto de meia hora faria um cp pendurado faturar meia hora.
ENCODE_PUT_TIMEOUT_SECONDS = 300.0

# O disparo volta assim que o processo desacoplado nasce: o que demora é o
# `run_all.sh`, e ele já não está do outro lado deste SSH.
DISPATCH_TIMEOUT_SECONDS = 60.0

PROBE_STDOUT_NAME = "perf.json"
PROBE_STDERR_NAME = "perf.stderr.txt"

EXIT_OK = 0
EXIT_FAILURE = 1


class PreparationError(Exception):
    """O resultado que um passo do `prepare-masters` observou e recusou."""


class _Aborted(Exception):
    """Um passo da auto-checagem ou do `preflight` falhou; o que vinha depois dele não roda."""


def positive_seconds(value: str) -> int:
    """Um timeout que o `run_all.sh` aceita: inteiro, e nunca zero — zero é "já estourou"."""
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"esperava segundos inteiros, veio {value!r}") from error
    if seconds <= 0:
        raise argparse.ArgumentTypeError(f"esperava segundos positivos, veio {seconds}")
    return seconds


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
    prepare.set_defaults(
        run=lambda args, **common: prepare_masters(config=_load_config(EXPERIMENT_TOML), **common)
    )
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
            config=_load_config(EXPERIMENT_TOML),
            instance_type=args.instance_type,
            bucket=args.bucket,
            **common,
        )
    )
    campaign = subcommands.add_parser(
        "run",
        help="sobe o plano, lança uma Instância por arquitetura e dispara o run_all.sh em cada uma",
        description=(
            "Roda a auto-checagem, recusa um bucket cujo runs/ já tem Execuções, sobe o "
            "plano para scenarios/, lança uma Instância de encode por registro [[instance]] "
            "da definição, espera os três cloud-init e dispara o run_all.sh desacoplado da "
            "sessão SSH. Termina aqui: as Instâncias seguem sozinhas (ADR-0010) e o arquivo "
            "de estado no work dir é o que o watch lê."
        ),
    )
    campaign.add_argument(
        "--config",
        required=True,
        type=Path,
        help="caminho do TOML da definição: config/pilot.toml ou config/experiment.toml",
    )
    campaign.add_argument(
        "--bucket",
        required=True,
        help="bucket que recebe o plano e as Execuções: o do piloto ou o da campanha",
    )
    campaign.add_argument(
        "--slices",
        type=Path,
        default=None,
        help=(
            "diretório com as fatias reduzidas do resume.py: sobe só elas, sobrescrevendo "
            "scenarios/{id}.json, lança só essas arquiteturas e dispensa a guarda do runs/"
        ),
    )
    campaign.add_argument(
        "--run-timeout",
        type=positive_seconds,
        default=RUN_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=f"teto de cada Execução, repassado ao run_all.sh (default: {RUN_TIMEOUT_SECONDS})",
    )
    campaign.add_argument(
        "--total-timeout",
        type=positive_seconds,
        default=TOTAL_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=f"teto do laço de cada Instância, idem (default: {TOTAL_TIMEOUT_SECONDS})",
    )
    campaign.set_defaults(
        run=lambda args, **common: run_campaign(
            config=_load_config(args.config),
            config_path=args.config,
            bucket=args.bucket,
            slices_dir=args.slices,
            run_timeout=args.run_timeout,
            total_timeout=args.total_timeout,
            **common,
        )
    )
    watch = subcommands.add_parser(
        "watch",
        help="a saída de emergência: --abort termina toda instância do arquivo de estado",
        description=(
            "Lê o arquivo de estado do work dir, o valida e termina todas as instâncias "
            "que ele lista. A vigilância sem --abort ainda não existe."
        ),
    )
    watch.add_argument(
        "--abort",
        action="store_true",
        required=True,
        help="termina todas as instâncias do arquivo de estado e sai",
    )
    watch.set_defaults(run=lambda args, **common: watch_abort(**common))
    args = parser.parse_args()

    infra_path = args.infra.expanduser()
    try:
        infra = _load_infra(infra_path)
        return args.run(args, infra=infra, work_dir=infra_path.parent)
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
        commit = _self_check(results, infra=infra, bucket=target, work_dir=work_dir)

        encode = _step(
            results,
            Step.AMI,
            lambda: encode_target(config, infra.amis, instance_type),
            detail=lambda chosen: f"{instance_type} ({chosen.instance.arch}): {chosen.image_id}",
        )
        key = slice_key(encode.instance.id)
        launched = _step(
            results,
            Step.LAUNCH,
            lambda: _launch_encode(
                encode=encode,
                infra=infra,
                config=config,
                commit=commit,
                bucket=target,
                slice_key=key,
                work_dir=work_dir,
            ),
            detail=lambda started: f"{started.instance_id} no commit {commit}, fatia em {key}",
        )
        instance_id = launched.instance_id

        host = _step(
            results,
            Step.BOOTSTRAP,
            lambda: wait_for_bootstrapped_instance(instance_id, report=_report),
        )
        _step(
            results,
            Step.PERF,
            lambda: _probe_perf(
                host=host,
                run=launched.probe_run,
                config=config,
                bucket=target,
                instance_id=launched.instance_id,
                work_dir=work_dir,
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


def run_campaign(
    *,
    infra: InfraConfig,
    work_dir: Path,
    config: ExperimentConfig,
    config_path: Path,
    bucket: str,
    slices_dir: Path | None,
    run_timeout: int,
    total_timeout: int,
) -> int:
    """A campanha até o disparo: plano no bucket, uma Instância por arquitetura, três PIDs.

    Até o último `cloud-init` qualquer falha termina tudo o que subiu (D7).
    Depois do primeiro disparo a regra inverte: as Instâncias são auto-dirigidas
    (ADR-0010) e ficam de pé, e o que as termina é o `watch --abort`.
    """
    results: list[StepResult] = []
    try:
        commit = _self_check(results, infra=infra, bucket=bucket, work_dir=work_dir)
    except _Aborted:
        commit = None
    print(render_table(summarize(results, SELF_CHECK_STEPS)))
    if commit is None:
        return EXIT_FAILURE

    if slices_dir is None:
        _guard_runs(bucket)
        campaign = full_campaign(config)
    else:
        campaign = resumed_campaign(config, _read_slices(slices_dir))
    _upload_plan(campaign, bucket=bucket, work_dir=work_dir)

    tracked = _StateFile(
        work_dir / STATE_NAME,
        CampaignState(
            bucket=bucket,
            config_path=str(config_path),
            commit=commit,
            slice_keys=tuple(each.key for each in campaign.slices),
            instances=(),
        ),
    )
    tracked.write()

    try:
        _launch_all(tracked, campaign, infra=infra, config=config)
        hosts = _await_bootstraps(tracked.state)
    except _FAILURES as error:
        _fail(error)
        _terminate_all(tracked)
        return EXIT_FAILURE

    try:
        _dispatch_all(tracked, hosts, run_timeout=run_timeout, total_timeout=total_timeout)
    except _FAILURES as error:
        _fail(error)
        _fail(
            f"as instâncias de {tracked.path} continuam de pé (ADR-0010): "
            f"o watch --abort termina todas"
        )
        return EXIT_FAILURE

    _report(
        f"{len(tracked.state.instances)} Instância(s) rodando sozinhas; o arquivo de estado é "
        f"{tracked.path}. A vigilância é o watch (#76); a saída de emergência é o watch --abort"
    )
    return EXIT_OK


def watch_abort(*, infra: InfraConfig, work_dir: Path) -> int:
    """Termina toda instância que o arquivo de estado lista e ainda não deu por morta."""
    tracked = _StateFile.load(work_dir / STATE_NAME)
    terminated = _terminate_all(tracked)
    if not terminated:
        _report(f"{tracked.path}: nenhuma instância de pé, nada a terminar")
        return EXIT_OK

    _report(
        f"{terminated} instância(s) terminada(s); o que já está em "
        f"s3://{tracked.state.bucket}/{RUNS_PREFIX} fica lá, para o resume.py"
    )
    return EXIT_OK


@dataclass
class _StateFile:
    """O arquivo de estado em disco e a última versão dele que este processo escreveu.

    Cada mudança passa pelo `update` e vai para o disco na hora: a instância que
    subiu e só existe na memória de quem depois levantou é a que ninguém termina.
    """

    path: Path
    state: CampaignState

    @classmethod
    def load(cls, path: Path) -> _StateFile:
        try:
            return cls(path, parse_state(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, StateError) as error:
            raise StateError(f"{path}: {error}") from error

    def update(self, instances: Sequence[TrackedInstance]) -> None:
        self.state = replace(self.state, instances=tuple(instances))
        self.write()

    def write(self) -> None:
        self.path.write_text(serialize_state(self.state), encoding="utf-8")


def _self_check(
    results: list[StepResult], *, infra: InfraConfig, bucket: str, work_dir: Path
) -> str:
    """Os cinco passos que provam credencial, infra e Masters antes de lançar (D6)."""
    _step(results, Step.STS, sts_caller_identity)
    _step(results, Step.BUCKETS, lambda: _list_both_buckets(infra, bucket))
    _step(results, Step.SSM, lambda: _read_ssh_key(infra.ssh_private_key_parameter_name))
    commit = _step(results, Step.GIT, git_rev_parse)
    _step(results, Step.SYNC, lambda: _sync_between_buckets(infra, work_dir))
    return commit


def _guard_runs(bucket: str) -> None:
    """A guarda da D13: `runs/` com qualquer Execução, ou truncado, recusa o lançamento."""
    try:
        listing: Sequence[S3Object] | TruncatedListing = s3_list_prefix(bucket, RUNS_PREFIX)
    except TruncatedListing as truncated:
        listing = truncated
    refusal = refuse_populated_runs(listing, preflight_prefix=PREFLIGHT_PREFIX)
    if refusal is not None:
        raise CampaignError(f"s3://{bucket}/{RUNS_PREFIX}: {refusal}")
    _report(f"s3://{bucket}/{RUNS_PREFIX}: sem Execuções, o bucket está livre")


def _read_slices(directory: Path) -> dict[str, Any]:
    """As fatias do `--slices`, chaveadas pelo id que o nome do arquivo carrega."""
    if not directory.is_dir():
        raise CampaignError(f"{directory}: não é um diretório")
    slices: dict[str, Any] = {}
    for path in sorted(directory.glob(slice_name("*"))):
        try:
            slices[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CampaignError(f"{path}: {error}") from error
    if not slices:
        raise CampaignError(f"{directory}: nenhuma fatia {slice_name('{id}')} no diretório")
    return slices


def _upload_plan(campaign: CampaignPlan, *, bucket: str, work_dir: Path) -> None:
    local_dir = work_dir / LOCAL_SCENARIOS_DIR
    local_dir.mkdir(parents=True, exist_ok=True)
    for key, plan in campaign.uploads.items():
        local = local_dir / Path(key).name
        local.write_text(serialize_plan(plan), encoding="utf-8")
        s3_cp(str(local), f"s3://{bucket}/{key}")
        _report(f"s3://{bucket}/{key}: {summarize_plan(plan)}")


def _launch_all(
    tracked: _StateFile,
    campaign: CampaignPlan,
    *,
    infra: InfraConfig,
    config: ExperimentConfig,
) -> None:
    """Uma Instância por fatia, cada uma no arquivo de estado assim que tem id."""
    for each in campaign.slices:
        instance_id = _launch_architecture(each, state=tracked.state, infra=infra, config=config)
        tracked.update(
            [
                *tracked.state.instances,
                TrackedInstance(
                    instance=each.instance.id,
                    instance_id=instance_id,
                    instance_type=each.instance.instance_type,
                    pid=None,
                    block_count=each.block_count,
                    runs_total=each.runs_total,
                    state=Vigilance.BOOTSTRAPPING,
                ),
            ]
        )
        _report(
            f"{instance_id}: {each.instance.id} ({each.instance.instance_type}) lançada no "
            f"commit {tracked.state.commit}, fatia em {each.key}"
        )


def _launch_architecture(
    each: ArchitectureSlice, *, state: CampaignState, infra: InfraConfig, config: ExperimentConfig
) -> str:
    return launch_encode(
        target=encode_target(config, infra.amis, each.instance.instance_type),
        infra=infra,
        commit=state.commit,
        bucket=state.bucket,
        slice_key=each.key,
        manifest_key=f"{MASTERS_PREFIX}{MANIFEST_NAME}",
        masters_prefix=MASTERS_PREFIX,
        volume_size_gb=ENCODE_VOLUME_SIZE_GB,
        tags=encode_tags(name=encode_name(each.instance), commit=state.commit),
    )


def _await_bootstraps(state: CampaignState) -> dict[str, str]:
    """O host de cada arquitetura, ou o aborto de todas na primeira que falhar (D7)."""
    outcomes = {tracked.instance: Bootstrap.NOT_AWAITED for tracked in state.instances}
    hosts: dict[str, str] = {}
    for tracked in state.instances:
        try:
            hosts[tracked.instance] = wait_for_bootstrapped_instance(
                tracked.instance_id, report=_report
            )
        except BootstrapError as error:
            _fail(error)
            outcomes[tracked.instance] = Bootstrap.ERROR
            break
        except WaitTimeout as error:
            _fail(error)
            outcomes[tracked.instance] = Bootstrap.TIMEOUT
            break
        outcomes[tracked.instance] = Bootstrap.DONE

    reasons = abort_reasons(outcomes)
    if reasons:
        raise CampaignError(
            _lines("bootstrap falhou: tudo é terminado antes de qualquer disparo (D7)", reasons)
        )
    return hosts


def _dispatch_all(
    tracked: _StateFile,
    hosts: Mapping[str, str],
    *,
    run_timeout: int,
    total_timeout: int,
) -> None:
    """O `launch_container.sh` desacoplado em cada Instância, e o PID no arquivo de estado."""
    dispatched = list(tracked.state.instances)
    for index, architecture in enumerate(dispatched):
        command = dispatch_command(
            repo_dir=REMOTE_REPO_DIR,
            work_dir=REMOTE_WORK_DIR,
            plan_name=slice_name(architecture.instance),
            bucket=tracked.state.bucket,
            commit=tracked.state.commit,
            instance_id=architecture.instance_id,
            instance_type=architecture.instance_type,
            run_timeout=run_timeout,
            total_timeout=total_timeout,
        )
        pid = parse_dispatched_pid(
            ssh_exec(hosts[architecture.instance], command, timeout=DISPATCH_TIMEOUT_SECONDS)
        )
        dispatched[index] = replace(architecture, pid=pid, state=Vigilance.RUNNING)
        tracked.update(dispatched)
        _report(
            f"{architecture.instance_id}: run_all.sh disparado, PID {pid}, "
            f"log em {REMOTE_WORK_DIR}/{DISPATCH_LOG_NAME}"
        )


def _terminate_all(tracked: _StateFile) -> int:
    """Termina o que está de pé, numa chamada só, e o marca morto; devolve quantas eram."""
    standing = [
        architecture
        for architecture in tracked.state.instances
        if architecture.state is not Vigilance.DEAD
    ]
    if standing:
        _report(f"terminando {', '.join(architecture.instance_id for architecture in standing)}")
        terminate_instances([architecture.instance_id for architecture in standing])
        tracked.update(
            [
                replace(architecture, state=Vigilance.DEAD)
                for architecture in tracked.state.instances
            ]
        )
    return len(standing)


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


@dataclass(frozen=True)
class LaunchedEncode:
    """A instância que subiu e o run, da fatia dela, que o probe do `perf` vai encodar."""

    instance_id: str
    probe_run: dict[str, Any]


def _launch_encode(
    *,
    encode: EncodeTarget,
    infra: InfraConfig,
    config: ExperimentConfig,
    commit: str,
    bucket: str,
    slice_key: str,
    work_dir: Path,
) -> LaunchedEncode:
    """Sobe a fatia daquela arquitetura e lança a instância que vai consumi-la."""
    plan = build_instance_slices(build_canonical_plan(config)).get(encode.instance.id)
    if plan is None:
        raise PreflightError(
            f"{encode.instance.id}: o plano do experiment.toml não tem fatia para esta instância"
        )

    local = work_dir / f"{encode.instance.id}.json"
    local.write_text(serialize_plan(plan), encoding="utf-8")
    s3_cp(str(local), f"s3://{bucket}/{slice_key}")

    instance_id = launch_encode(
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
    return LaunchedEncode(instance_id=instance_id, probe_run=plan["blocks"][0]["runs"][0])


def _probe_perf(
    *,
    host: str,
    run: dict[str, Any],
    config: ExperimentConfig,
    bucket: str,
    instance_id: str,
    work_dir: Path,
) -> dict[str, Counter]:
    """Roda o probe, **guarda a saída crua** e só então julga o que ela diz.

    Nessa ordem: julgar antes faria o passo que reprova — o único cuja saída
    interessa — ser o único a não deixar saída nenhuma.
    """
    probe = ssh_capture(
        host,
        perf_probe_command(
            run=run,
            event_spec=config.instrumentation.event_spec,
            repo_dir=REMOTE_REPO_DIR,
            work_dir=REMOTE_WORK_DIR,
        ),
        timeout=PERF_PROBE_TIMEOUT_SECONDS,
    )
    _keep_probe_evidence(probe, bucket=bucket, instance_id=instance_id, work_dir=work_dir)
    if probe.returncode != 0:
        raise PreflightError(
            f"o probe do perf saiu com status {probe.returncode}; a saída crua está em "
            f"s3://{bucket}/{PREFLIGHT_PREFIX}{instance_id}/"
        )
    return perf_counters(probe.stdout, config.instrumentation)


def _keep_probe_evidence(
    probe: CommandOutput, *, bucket: str, instance_id: str, work_dir: Path
) -> None:
    """As duas saídas do probe no log do Orquestrador e no bucket, **sem apagar**.

    Sem o `s3_rm` que fecha os outros dois objetos de prova deste passo: o deles é
    prova de permissão e some, e este é o dado que o próximo diagnóstico lê.
    """
    local = work_dir / "preflight" / instance_id
    local.mkdir(parents=True, exist_ok=True)
    for name, text in ((PROBE_STDOUT_NAME, probe.stdout), (PROBE_STDERR_NAME, probe.stderr)):
        path = local / name
        path.write_text(text, encoding="utf-8")
        key = f"{PREFLIGHT_PREFIX}{instance_id}/{name}"
        s3_cp(str(path), f"s3://{bucket}/{key}")
        _report(f"{key}: guardado em {path}")
        print(text, file=sys.stderr)


def _put_from_container(host: str, bucket: str, instance_id: str) -> str:
    """O `PutObject` do papel `encode` pelo caminho real, e a limpeza pelo Orquestrador."""
    key = f"{PREFLIGHT_PREFIX}{instance_id}.txt"
    ssh_exec(host, encode_put_command(bucket=bucket, key=key), timeout=ENCODE_PUT_TIMEOUT_SECONDS)

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


def _fail(error: Exception | str) -> None:
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
    CampaignError,
    StateError,
    OSError,
    json.JSONDecodeError,
    tomllib.TOMLDecodeError,
)


if __name__ == "__main__":
    raise SystemExit(main())
