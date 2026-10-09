#!/usr/bin/env python3
"""CLI do Orquestrador: um subcomando por passo da campanha — ver `orchestrator/README.md`."""

from __future__ import annotations

import argparse
import json
import sys
import time
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
    launched_instance,
    refuse_populated_runs,
    refuse_standing_instances,
    resumed_campaign,
    slice_key,
    slice_name,
)
from campaign_state import (
    CampaignState,
    StateError,
    TrackedInstance,
    appended,
    parse_state,
    serialize_state,
)
from campaign_watch import (
    LIVENESS_TIMEOUT_SECONDS,
    ORCHESTRATOR_CLI,
    POLL_INTERVAL_SECONDS,
    failure_reasons,
    liveness_command,
    poll_line,
    resume_hint,
    settled_line,
    summary_lines,
    watch_deadline_seconds,
    watched,
)
from command_output import (
    DescribedInstance,
    OutputError,
    S3Object,
    TruncatedListing,
    parse_dispatched_pid,
)
from experiment_config import ConfigError, ExperimentConfig, validate_config
from external import (
    INSTANCE_NOT_FOUND_ERROR_CODE,
    RUNS_PREFIX,
    CommandOutput,
    ExternalCommandError,
    described_instance,
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
    BOOTSTRAP_TIMEOUT_SECONDS,
    ENCODE_VOLUME_SIZE_GB,
    IMDS_HOP_LIMIT,
    REMOTE_REPO_DIR,
    REMOTE_WORK_DIR,
    EncodeTarget,
    LaunchError,
    encode_name,
    encode_tags,
    encode_target,
    image_for_arch,
    launch_encode,
    launch_instance,
    orphan_hint,
    render_user_data,
    wait_for_bootstrapped_instance,
)
from instance_wait import BootstrapError, WaitTimeout
from judge_launch import (
    QUALITY_PLAN_KEY,
    QUALITY_RESULTS_PREFIX,
    JudgeError,
    judge_dispatch_command,
    judge_launch,
    judge_state,
    launched_judge,
)
from manifest_check import check_manifest
from masters_launch import mirror_differences, prepare_masters_command
from masters_plan import build_masters_plan, iter_masters
from preflight import (
    ENCODE_STEPS,
    JUDGE_STEPS,
    SELF_CHECK_STEPS,
    Counter,
    Outcome,
    PreflightError,
    Step,
    StepResult,
    VmafScore,
    failed,
    perf_counters,
    perf_detail,
    perf_probe_command,
    put_command,
    render_table,
    summarize,
    vmaf_detail,
    vmaf_probe_command,
    vmaf_score,
)
from quality_plan import PLAN_FILENAME, PlanError, check_empty_plan, check_plan, empty_plan
from scenario_plan import (
    build_canonical_plan,
    build_instance_slices,
    serialize_plan,
    summarize_plan,
)
from status_check import (
    STATUS_PREFIX,
    JudgeProgress,
    Progress,
    Role,
    StatusError,
)
from vigilance import Liveness, Vigilance, decide_vigilance, is_standing, marker_verdict

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

JUDGE_PREFLIGHT_PREFIX = f"{QUALITY_RESULTS_PREFIX}preflight/"
SELF_CHECK_PREFIX = f"{PREFLIGHT_PREFIX}self-check/"
SELF_CHECK_OBJECT = "probe.txt"
SELF_CHECK_CONTENT = "preflight\n"

STATE_NAME = "state.json"
LOCAL_SCENARIOS_DIR = "scenarios"

RUN_TIMEOUT_SECONDS = 4 * 60 * 60
TOTAL_TIMEOUT_SECONDS = 120 * 60 * 60

JUDGE_OUTPUT_TIMEOUT_SECONDS = 2 * 60 * 60
JUDGE_TOTAL_TIMEOUT_SECONDS = 24 * 60 * 60

# Sem teto, o `curl` do `prepare.sh` que estola pendura o CLI para sempre, com a
# instância faturando e indistinguível das ~2 h de silêncio do caso normal.
PREPARE_TIMEOUT_SECONDS = 10800.0

# Os probes do `perf` e do `libvmaf` trabalham segundos de um Master de verdade,
# e no pior caso isso é 4K num encoder lento: o teto é generoso porque o que ele
# compra é a instância ser terminada sozinha se o comando travar, não cortar o probe.
PROBE_TIMEOUT_SECONDS = 1800.0

# O do `s3 cp` de nove bytes continua curto: o que leva minutos ali é o container
# subindo, e um teto de meia hora faria um cp pendurado faturar meia hora.
PUT_TIMEOUT_SECONDS = 300.0

# O disparo volta assim que o processo desacoplado nasce: o que demora é o
# `run_all.sh`, e ele já não está do outro lado deste SSH.
DISPATCH_TIMEOUT_SECONDS = 60.0

PROBE_STDOUT_NAME = "perf.json"
PROBE_STDERR_NAME = "perf.stderr.txt"

VMAF_STDOUT_NAME = "vmaf.json"
VMAF_STDERR_NAME = "ffmpeg.log"

JUDGE_PUT_NAME = "probe.txt"

EXIT_OK = 0
EXIT_FAILURE = 1

# O status com que um shell reporta a morte por SIGINT, e o que distingue "o
# pesquisador parou de olhar" de "a campanha tem pendência" (D11).
EXIT_INTERRUPTED = 130

_VIGILANT_SUBCOMMANDS = frozenset({"run", "judge", "watch"})


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
        help="prova o caminho do encode ou do Juiz numa instância descartável",
        description=(
            "Roda a auto-checagem do Orquestrador, lança uma instância de encode do tipo "
            "pedido com a fatia daquela arquitetura, confere o perf e o PutObject de dentro "
            "do container, termina a instância e imprime a tabela passou/falhou. Com "
            "--judge, lança um Juiz descartável sobre um plano vazio e confere o libvmaf e "
            "o PutObject em quality/results/preflight/ no lugar do perf."
        ),
    )
    leg = check.add_mutually_exclusive_group()
    leg.add_argument(
        "--instance-type",
        default=PREFLIGHT_INSTANCE_TYPE,
        help=f"tipo da instância de encode descartável (default: {PREFLIGHT_INSTANCE_TYPE})",
    )
    leg.add_argument(
        "--judge",
        action="store_true",
        help="a perna do Juiz, com o tipo e a arquitetura de [quality.judge]",
    )
    check.add_argument(
        "--bucket",
        default=None,
        help="bucket que recebe a fatia e o objeto de prova (default: o do piloto)",
    )
    check.set_defaults(
        run=lambda args, **common: preflight(
            config=_load_config(EXPERIMENT_TOML),
            instance_type=None if args.judge else args.instance_type,
            bucket=args.bucket,
            **common,
        )
    )
    campaign = subcommands.add_parser(
        "run",
        help="sobe o plano, lança uma Instância por arquitetura e dispara o run_all.sh em cada uma",
        description=(
            "Roda a auto-checagem, recusa um arquivo de estado com instância de pé e um "
            "bucket cujo runs/ já tem Execuções, sobe o plano para scenarios/, lança uma "
            "Instância de encode por registro [[instance]] "
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
    quality = subcommands.add_parser(
        "judge",
        help="sobe o plano do Pass, lança o Juiz e dispara o run_quality.sh nele",
        description=(
            "Roda a auto-checagem, valida o plano do quality_triage.py, recusa um arquivo "
            "de estado com instância de pé, sobe o plano para quality/plan.json, lança o "
            "Juiz de [quality.judge], espera o cloud-init e dispara o run_quality.sh "
            "desacoplado da sessão SSH. A entrada do Juiz é acrescentada ao arquivo de "
            "estado, e a vigilância é a mesma do run."
        ),
    )
    quality.add_argument(
        "--config",
        required=True,
        type=Path,
        help="caminho do TOML da definição, de onde sai o [quality.judge]",
    )
    quality.add_argument(
        "--bucket",
        required=True,
        help="bucket que recebe o plano e os resultados: o do piloto ou o da campanha",
    )
    quality.add_argument(
        "--plan",
        required=True,
        type=Path,
        help="o plan.json que o quality_triage.py escreveu",
    )
    quality.add_argument(
        "--output-timeout",
        type=positive_seconds,
        default=JUDGE_OUTPUT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            f"orçamento de cada output, repassado ao run_quality.sh "
            f"(default: {JUDGE_OUTPUT_TIMEOUT_SECONDS})"
        ),
    )
    quality.add_argument(
        "--total-timeout",
        type=positive_seconds,
        default=JUDGE_TOTAL_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=f"teto do Pass inteiro, idem (default: {JUDGE_TOTAL_TIMEOUT_SECONDS})",
    )
    quality.set_defaults(
        run=lambda args, **common: judge(
            config=_load_config(args.config),
            config_path=args.config,
            bucket=args.bucket,
            plan_path=args.plan.expanduser(),
            output_timeout=args.output_timeout,
            total_timeout=args.total_timeout,
            **common,
        )
    )
    watch = subcommands.add_parser(
        "watch",
        help="retoma a vigilância pelo arquivo de estado; --abort é a saída de emergência",
        description=(
            "Lê o arquivo de estado do work dir, o valida e volta ao mesmo laço do run "
            "e do judge, de onde o Orquestrador caiu: as três perguntas a cada 5 minutos, "
            "a terminação de cada instância no poll em que o marcador dela aparece e o "
            "resumo ao fim. As que o arquivo já dá por terminadas são puladas. Com "
            "--abort não vigia: termina toda instância que o arquivo lista e sai."
        ),
    )
    watch.add_argument(
        "--abort",
        action="store_true",
        help="não vigia: termina todas as instâncias do arquivo de estado e sai",
    )
    watch.set_defaults(
        run=lambda args, **common: (watch_abort if args.abort else watch_campaign)(**common)
    )
    args = parser.parse_args()

    infra_path = args.infra.expanduser()
    try:
        infra = _load_infra(infra_path)
        return args.run(args, infra=infra, work_dir=infra_path.parent)
    except KeyboardInterrupt:
        return _interrupted(infra_path, args.subcommand)
    except _FAILURES as error:
        _fail(error)
        return EXIT_FAILURE


def _interrupted(infra_path: Path, subcommand: str) -> int:
    """O `Ctrl-C` para só a vigilância, e diz como voltar e como abortar (D11).

    Só nos dois subcomandos que vigiam: nos outros o Ctrl-C interrompe um passo
    que **lança** instância, e mandar rodar um `watch` que talvez não tenha
    arquivo de estado seria a instrução errada em cima da instância esquecida.
    """
    if subcommand not in _VIGILANT_SUBCOMMANDS:
        _fail(
            f"Ctrl-C: o {subcommand} parou no meio; confira no describe-instances se a "
            f"instância dele ficou de pé"
        )
        return EXIT_INTERRUPTED

    watch = f"{ORCHESTRATOR_CLI} --infra {infra_path} watch"
    _fail("Ctrl-C: a vigilância parou e nenhuma instância foi terminada (ADR-0010)")
    _fail(f"  para voltar a vigiar:  {watch}")
    _fail(f"  para terminar tudo:    {watch} --abort")
    return EXIT_INTERRUPTED


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
    instance_type: str | None,
    bucket: str | None,
) -> int:
    """O caminho do encode, ou o do Juiz quando não há tipo, numa instância de minutos.

    A escada é linear e para no primeiro passo que falhar: o que vem depois de um
    `PassRole` recusado não tem o que provar. A instância, se chegou a subir, é
    terminada em todo caminho de saída.
    """
    target = bucket or infra.buckets.pilot
    judge = instance_type is None
    results: list[StepResult] = []
    launched: list[str] = []

    try:
        commit = _self_check(results, infra=infra, bucket=target, work_dir=work_dir)
        if judge:
            _judge_leg(
                results,
                launched,
                infra=infra,
                config=config,
                commit=commit,
                bucket=target,
                work_dir=work_dir,
            )
        else:
            _encode_leg(
                results,
                launched,
                infra=infra,
                config=config,
                commit=commit,
                bucket=target,
                work_dir=work_dir,
                instance_type=instance_type,
            )
    except _Aborted:
        pass
    finally:
        if launched:
            with suppress(_Aborted):
                _step(results, Step.TERMINATE, lambda: _terminate(launched[0]))

    table = summarize(results, JUDGE_STEPS if judge else ENCODE_STEPS)
    print(render_table(table))
    if failed(table):
        return EXIT_FAILURE

    if judge:
        _report(
            f"o preflight do Juiz passou; a evidência fica em "
            f"s3://{target}/{JUDGE_PREFLIGHT_PREFIX}, e o degrau seguinte é o triage do piloto"
        )
    else:
        _report(
            "o preflight passou; o degrau seguinte da escada é o primeiro bloco do piloto, "
            "que é a primeira Execução real (ADR-0022)"
        )
    return EXIT_OK


def _encode_leg(
    results: list[StepResult],
    launched: list[str],
    *,
    infra: InfraConfig,
    config: ExperimentConfig,
    commit: str,
    bucket: str,
    work_dir: Path,
    instance_type: str,
) -> None:
    encode = _step(
        results,
        Step.AMI,
        lambda: encode_target(config, infra.amis, instance_type),
        detail=lambda chosen: f"{instance_type} ({chosen.instance.arch}): {chosen.image_id}",
    )
    key = slice_key(encode.instance.id)
    started = _step(
        results,
        Step.LAUNCH,
        lambda: _launch_encode(
            encode=encode,
            infra=infra,
            config=config,
            commit=commit,
            bucket=bucket,
            slice_key=key,
            work_dir=work_dir,
        ),
        detail=lambda started: f"{started.instance_id} no commit {commit}, fatia em {key}",
    )
    instance_id = started.instance_id
    launched.append(instance_id)

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
            run=started.probe_run,
            config=config,
            bucket=bucket,
            instance_id=instance_id,
            work_dir=work_dir,
        ),
        detail=perf_detail,
    )
    _step(results, Step.PUT, lambda: _put_from_container(host, bucket, instance_id))


def _judge_leg(
    results: list[StepResult],
    launched: list[str],
    *,
    infra: InfraConfig,
    config: ExperimentConfig,
    commit: str,
    bucket: str,
    work_dir: Path,
) -> None:
    judge = config.quality.judge
    _step(
        results,
        Step.AMI,
        lambda: image_for_arch(infra.amis, judge.arch, judge.instance_type),
        detail=lambda image: f"{judge.instance_type} ({judge.arch}): {image}",
    )
    instance_id = _step(
        results,
        Step.LAUNCH,
        lambda: _launch_judge(
            infra=infra,
            config=config,
            commit=commit,
            bucket=bucket,
            work_dir=work_dir,
        ),
        detail=lambda started: f"{started} no commit {commit}, plano vazio em {QUALITY_PLAN_KEY}",
    )
    launched.append(instance_id)

    host = _step(
        results,
        Step.BOOTSTRAP,
        lambda: wait_for_bootstrapped_instance(instance_id, report=_report),
    )
    _step(
        results,
        Step.VMAF,
        lambda: _probe_vmaf(
            host=host, config=config, bucket=bucket, instance_id=instance_id, work_dir=work_dir
        ),
        detail=vmaf_detail,
    )
    _step(results, Step.PUT, lambda: _put_from_judge(host, bucket, instance_id))


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
    """A campanha inteira: plano no bucket, uma Instância por arquitetura, e a vigilância.

    Até o último `cloud-init` qualquer falha termina tudo o que subiu (D7).
    Depois do primeiro disparo a regra inverte: as Instâncias são auto-dirigidas
    (ADR-0010) e ficam de pé, e o que as termina é o marcador de cada uma, o
    prazo de D10 ou o `watch --abort`.
    """
    started = time.monotonic()
    results: list[StepResult] = []
    try:
        commit = _self_check(results, infra=infra, bucket=bucket, work_dir=work_dir)
    except _Aborted:
        commit = None
    print(render_table(summarize(results, SELF_CHECK_STEPS)))
    if commit is None:
        return EXIT_FAILURE

    state_path = work_dir / STATE_NAME
    if _guard_state(state_path) is not None:
        _report(f"{state_path}: só instâncias mortas, o arquivo de estado pode ser reescrito")
    if slices_dir is None:
        _guard_runs(bucket)
        campaign = full_campaign(config)
    else:
        campaign = resumed_campaign(config, _read_slices(slices_dir))
    _upload_plan(campaign, bucket=bucket, work_dir=work_dir)

    tracked = _StateFile(
        state_path,
        CampaignState(
            bucket=bucket,
            config_path=str(config_path),
            commit=commit,
            total_timeout=total_timeout,
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
        f"{tracked.path}. Ctrl-C para a vigilância sem terminar nada, e o watch a retoma"
    )
    return _drive_vigilance(tracked, started=started)


def judge(
    *,
    infra: InfraConfig,
    work_dir: Path,
    config: ExperimentConfig,
    config_path: Path,
    bucket: str,
    plan_path: Path,
    output_timeout: int,
    total_timeout: int,
) -> int:
    """O Pass de qualidade: plano no bucket, o Juiz, e a mesma vigilância do `run`."""
    started = time.monotonic()
    results: list[StepResult] = []
    try:
        commit = _self_check(results, infra=infra, bucket=bucket, work_dir=work_dir)
    except _Aborted:
        commit = None
    print(render_table(summarize(results, SELF_CHECK_STEPS)))
    if commit is None:
        return EXIT_FAILURE

    plan = check_plan(plan_path.read_bytes())
    state_path = work_dir / STATE_NAME
    tracked = _StateFile(
        state_path,
        judge_state(
            _guard_state(state_path),
            bucket=bucket,
            config_path=str(config_path),
            commit=commit,
            total_timeout=total_timeout,
        ),
    )

    s3_cp(str(plan_path), f"s3://{bucket}/{QUALITY_PLAN_KEY}")
    _report(f"s3://{bucket}/{QUALITY_PLAN_KEY}: {len(plan['outputs'])} output(s) a julgar")
    tracked.write()

    launch = judge_launch(
        config,
        infra,
        commit=commit,
        bucket=bucket,
        manifest_key=f"{MASTERS_PREFIX}{MANIFEST_NAME}",
        masters_prefix=MASTERS_PREFIX,
    )
    instance_id = launch_instance(launch)
    entry = launched_judge(
        instance_id,
        instance_type=launch.instance_type,
        plan=plan,
        commit=commit,
        total_timeout=total_timeout,
    )
    tracked.append(entry)
    _report(
        f"{instance_id}: Juiz ({launch.instance_type}) lançado no commit {commit}, "
        f"acrescentado a {tracked.path}"
    )

    try:
        host = wait_for_bootstrapped_instance(instance_id, report=_report)
    except _FAILURES as error:
        _fail(error)
        _terminate_all(tracked)
        return EXIT_FAILURE

    try:
        _dispatch_judge(
            tracked, entry, host, output_timeout=output_timeout, total_timeout=total_timeout
        )
    except _FAILURES as error:
        _fail(error)
        _fail(f"o Juiz de {tracked.path} continua de pé (ADR-0010): o watch --abort o termina")
        return EXIT_FAILURE

    _report(
        f"o Juiz roda sozinho; o arquivo de estado é {tracked.path}. Ctrl-C para a "
        f"vigilância sem terminar nada, e o watch a retoma"
    )
    return _drive_vigilance(tracked, started=started)


def watch_campaign(*, infra: InfraConfig, work_dir: Path) -> int:
    """A vigilância retomada de onde o Orquestrador caiu, pelo arquivo de estado (D12)."""
    started = time.monotonic()
    tracked = _StateFile.load(work_dir / STATE_NAME)
    settled = [each for each in tracked.state.instances if not is_standing(each.state)]
    if settled:
        _report(
            f"{tracked.path}: {', '.join(each.instance for each in settled)} já "
            f"terminada(s) no arquivo de estado, puladas"
        )
    return _drive_vigilance(tracked, started=started)


def watch_abort(*, infra: InfraConfig, work_dir: Path) -> int:
    """Termina toda instância que o arquivo de estado lista e ainda não deu por morta."""
    tracked = _StateFile.load(work_dir / STATE_NAME)
    terminated = _terminate_all(tracked)
    if not terminated:
        _report(f"{tracked.path}: nenhuma instância de pé, nada a terminar")
        return EXIT_OK

    _report(
        f"{terminated} instância(s) terminada(s); o que já está em "
        f"s3://{tracked.state.bucket}/{RUNS_PREFIX} fica lá, para o resume.py, e o que "
        f"o Juiz julgou, em s3://{tracked.state.bucket}/{QUALITY_RESULTS_PREFIX}"
    )
    return EXIT_OK


def _drive_vigilance(tracked: _StateFile, *, started: float) -> int:
    """O laço até nada mais estar de pé, o resumo e o código de saída (D2, D8, D10)."""
    limit = watch_deadline_seconds(
        total_timeout=max(
            (each.total_timeout for each in watched(tracked.state)),
            default=tracked.state.total_timeout,
        ),
        bootstrap_timeout=BOOTSTRAP_TIMEOUT_SECONDS,
    )
    blown = _watch_loop(tracked, started=started, limit=limit)

    launch = watched(tracked.state)
    _report("o lançamento terminou:")
    for line in summary_lines(launch):
        print(line)
    reasons = failure_reasons(launch, deadline_blown=blown)
    judged = any(each.role is Role.JUDGE for each in launch)
    if not reasons:
        if judged:
            _report(
                f"o Juiz julgou todo output do plano; os resultados estão em "
                f"s3://{tracked.state.bucket}/{QUALITY_RESULTS_PREFIX}"
            )
        else:
            _report(
                f"as {len(launch)} arquiteturas terminaram sem pendência; "
                f"as Execuções estão em s3://{tracked.state.bucket}/{RUNS_PREFIX}"
            )
        return EXIT_OK

    if judged:
        _fail(_lines("o Pass de qualidade terminou com pendência:", reasons))
        return EXIT_FAILURE
    _fail(_lines("a campanha terminou com pendência:", reasons))
    for line in resume_hint(tracked.state):
        _fail(line)
    return EXIT_FAILURE


def _watch_loop(tracked: _StateFile, *, started: float, limit: float) -> bool:
    """Pergunta a cada 5 minutos até nada restar de pé; devolve se o prazo estourou."""
    unanswered: dict[str, int] = {}
    while _standing(tracked):
        _poll(tracked, unanswered)
        if not _standing(tracked):
            return False
        if time.monotonic() - started >= limit:
            _fail(f"o prazo de {limit / 3600:.1f} h do Orquestrador estourou (D10)")
            _terminate_all(tracked)
            return True
        time.sleep(POLL_INTERVAL_SECONDS)
    return False


def _standing(tracked: _StateFile) -> list[TrackedInstance]:
    """As instâncias do lançamento vigiado que ainda faturam: são elas que o poll pergunta."""
    return [each for each in watched(tracked.state) if is_standing(each.state)]


def _poll(tracked: _StateFile, unanswered: dict[str, int]) -> None:
    """As três perguntas por arquitetura de pé, e uma linha para cada arquitetura (D2)."""
    listed = _status_listing(tracked)

    for each in watched(tracked.state):
        if not is_standing(each.state):
            print(settled_line(each))
        elif listed is None:
            _fail(f"{each.instance}: o poll não pôde ser feito desta vez, sem a listagem")
        else:
            _poll_architecture(tracked, each, listed=listed, unanswered=unanswered)


def _status_listing(tracked: _StateFile) -> set[str] | None:
    """A listagem de `status/`, uma só para as três, ou `None` se ela não pôde ser feita."""
    try:
        return {entry.key for entry in s3_list_prefix(tracked.state.bucket, STATUS_PREFIX)}
    except _FAILURES as error:
        _fail(f"s3://{tracked.state.bucket}/{STATUS_PREFIX}: a listagem falhou neste poll: {error}")
        return None


def _poll_architecture(
    tracked: _StateFile,
    each: TrackedInstance,
    *,
    listed: set[str],
    unanswered: dict[str, int],
) -> None:
    """O poll de uma arquitetura, e o que ele decide sobre ela.

    A falha de qualquer uma das três perguntas deixa a arquitetura como estava e
    o laço segue: apagar o `except` faz um `describe-instances` estrangulado uma
    vez em quatro dias derrubar a vigilância das outras duas.
    """
    try:
        described = described_instance(each.instance_id)
        liveness = _liveness(each, described)
        payload = _status_object(tracked, each.status_keys.done, listed)
        marker, outcome = marker_verdict(payload, instance_id=each.instance_id)
        progress = _progress(tracked, each, listed)
    except _FAILURES as error:
        _fail(f"{each.instance}: o poll não pôde ser feito desta vez: {error}")
        return

    silent = unanswered.get(each.instance_id, 0) + 1 if liveness is Liveness.NO_ANSWER else 0
    unanswered[each.instance_id] = silent
    state = decide_vigilance(
        described_state=described.state if described else None,
        liveness=liveness,
        marker=marker,
        unanswered_polls=silent,
    )
    print(poll_line(each, state=state, progress=progress, unanswered_polls=silent))

    if state is Vigilance.READY_TO_TERMINATE:
        _remember(tracked, each.instance_id, state=state, outcome=outcome)
        _terminate_architecture(tracked, each, Vigilance.FINISHED)
    elif state is Vigilance.DEAD:
        _terminate_architecture(tracked, each, Vigilance.DEAD)
    elif state is not each.state:
        _remember(tracked, each.instance_id, state=state)


def _liveness(each: TrackedInstance, described: DescribedInstance | None) -> Liveness:
    """O `kill -0` no PID gravado: "sem resposta" é um resultado, e não uma exceção (D2)."""
    if each.pid is None:
        return Liveness.NOT_DISPATCHED
    if described is None or described.private_ip is None:
        return Liveness.NO_ANSWER

    try:
        probe = ssh_capture(
            described.private_ip, liveness_command(each.pid), timeout=LIVENESS_TIMEOUT_SECONDS
        )
    except ExternalCommandError as error:
        _report(f"{each.instance}: o kill -0 não respondeu ({error})")
        return Liveness.NO_ANSWER
    return Liveness.ALIVE if probe.returncode == 0 else Liveness.DEAD


def _status_object(tracked: _StateFile, key: str, listed: set[str]) -> Any:
    """O objeto de `status/` baixado e parseado, ou `None` enquanto o bucket não o tem."""
    if key not in listed:
        return None
    local = tracked.path.parent / key
    local.parent.mkdir(parents=True, exist_ok=True)
    s3_cp(f"s3://{tracked.state.bucket}/{key}", str(local))
    return json.loads(local.read_text(encoding="utf-8"))


def _progress(
    tracked: _StateFile, each: TrackedInstance, listed: set[str]
) -> Progress | JudgeProgress | None:
    """O progresso desta Instância; o da tentativa anterior chega como `None` e some.

    Apagar o `except` faz um objeto de progresso deformado — telemetria, que o
    `run_all.sh` também trata como descartável — esconder o marcador, e a fatia
    só acabaria no prazo de D10.
    """
    payload = _status_object(tracked, each.status_keys.progress, listed)
    if payload is None:
        return None
    try:
        return each.read_progress(payload)
    except StatusError as error:
        _report(f"{each.instance}: o objeto de progresso veio deformado ({error})")
        return None


def _terminate_architecture(tracked: _StateFile, each: TrackedInstance, state: Vigilance) -> None:
    """Termina aquela arquitetura, e só a marca quando a chamada voltou (D9).

    Nessa ordem, e só assim: uma arquitetura marcada sobre um terminate que
    falhou sai do laço e da lista do `watch --abort`, que é a única coisa que
    ainda a alcançaria — a `xlarge` viva que ninguém mais termina. Deixada de pé,
    ela volta no poll seguinte, e o `terminate-instances` é idempotente. A
    exceção é a instância que a API já não conhece: aí não há o que terminar.
    """
    _report(f"{each.instance_id} ({each.instance}): terminando, {state.value}")
    try:
        terminate_instances([each.instance_id])
    except _FAILURES as error:
        if INSTANCE_NOT_FOUND_ERROR_CODE not in str(error):
            _fail(
                f"{each.instance_id}: o terminate-instances falhou ({error}); ela segue "
                f"no arquivo de estado, e o próximo poll tenta de novo"
            )
            return
    _remember(tracked, each.instance_id, state=state)


def _remember(tracked: _StateFile, instance_id: str, **changes: Any) -> None:
    """A mudança de uma instância, que vai para o disco na hora (D12).

    Pelo `instance_id`, e não pelo nome: o Juiz de um Pass repetido se chama
    `judge` como o anterior, e a mudança de um reescreveria o outro.
    """
    tracked.update(
        [
            replace(each, **changes) if each.instance_id == instance_id else each
            for each in tracked.state.instances
        ]
    )


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

    def append(self, entry: TrackedInstance) -> None:
        self.state = appended(self.state, entry)
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


def _guard_state(path: Path) -> CampaignState | None:
    """A guarda da D12, e o arquivo que ela leu, ou `None` se não havia arquivo."""
    if not path.exists():
        return None
    state = _StateFile.load(path).state
    refusal = refuse_standing_instances(state)
    if refusal is not None:
        raise CampaignError(f"{path}: {refusal}")
    return state


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
        tracked.append(
            launched_instance(
                each,
                instance_id,
                commit=tracked.state.commit,
                total_timeout=tracked.state.total_timeout,
            )
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


def _dispatch_judge(
    tracked: _StateFile,
    judge: TrackedInstance,
    host: str,
    *,
    output_timeout: int,
    total_timeout: int,
) -> None:
    """O `launch_container.sh` do Juiz desacoplado, e o PID no arquivo de estado."""
    command = judge_dispatch_command(
        repo_dir=REMOTE_REPO_DIR,
        work_dir=REMOTE_WORK_DIR,
        bucket=tracked.state.bucket,
        commit=judge.commit,
        instance_id=judge.instance_id,
        instance_type=judge.instance_type,
        output_timeout=output_timeout,
        total_timeout=total_timeout,
    )
    pid = parse_dispatched_pid(ssh_exec(host, command, timeout=DISPATCH_TIMEOUT_SECONDS))
    _remember(tracked, judge.instance_id, pid=pid, state=Vigilance.RUNNING)
    _report(
        f"{judge.instance_id}: run_quality.sh disparado, PID {pid}, "
        f"log em {REMOTE_WORK_DIR}/{DISPATCH_LOG_NAME}"
    )


def _terminate_all(tracked: _StateFile) -> int:
    """Termina o que está de pé, numa chamada só, e o marca morto; devolve quantas eram.

    O `replace` sobre **todas** apagaria, da arquitetura que o marcador já
    encerrou, o `outcome` de que saem o resumo final e o código de saída.
    """
    standing = [each for each in tracked.state.instances if is_standing(each.state)]
    if standing:
        _report(f"terminando {', '.join(architecture.instance_id for architecture in standing)}")
        terminate_instances([architecture.instance_id for architecture in standing])
        tracked.update(
            [
                replace(architecture, state=Vigilance.DEAD)
                if is_standing(architecture.state)
                else architecture
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
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    _keep_probe_evidence(
        probe,
        names=(PROBE_STDOUT_NAME, PROBE_STDERR_NAME),
        bucket=bucket,
        prefix=f"{PREFLIGHT_PREFIX}{instance_id}/",
        local=work_dir / "preflight" / instance_id,
    )
    if probe.returncode != 0:
        raise PreflightError(
            f"o probe do perf saiu com status {probe.returncode}; a saída crua está em "
            f"s3://{bucket}/{PREFLIGHT_PREFIX}{instance_id}/"
        )
    return perf_counters(probe.stdout, config.instrumentation)


def _keep_probe_evidence(
    probe: CommandOutput,
    *,
    names: tuple[str, str],
    bucket: str,
    prefix: str,
    local: Path,
) -> None:
    """As duas saídas do probe no log do Orquestrador e no bucket, **sem apagar**.

    Sem o `s3_rm` que fecha os outros dois objetos de prova deste passo: o deles é
    prova de permissão e some, e este é o dado que o próximo diagnóstico lê.
    """
    local.mkdir(parents=True, exist_ok=True)
    stdout_name, stderr_name = names
    for name, text in ((stdout_name, probe.stdout), (stderr_name, probe.stderr)):
        path = local / name
        path.write_text(text, encoding="utf-8")
        key = f"{prefix}{name}"
        s3_cp(str(path), f"s3://{bucket}/{key}")
        _report(f"{key}: guardado em {path}")
        print(text, file=sys.stderr)


def _put_from_container(host: str, bucket: str, instance_id: str) -> str:
    """O `PutObject` do papel `encode` pelo caminho real, e a limpeza pelo Orquestrador."""
    key = f"{PREFLIGHT_PREFIX}{instance_id}.txt"
    size = _put_and_list(host, bucket, key)
    s3_rm(f"s3://{bucket}/{key}")
    return f"{key}: {size} bytes escritos pelo container, listados e apagados"


def _put_and_list(host: str, bucket: str, key: str) -> int:
    ssh_exec(host, put_command(bucket=bucket, key=key), timeout=PUT_TIMEOUT_SECONDS)
    written = s3_list_prefix(bucket, key)
    if not written:
        raise PreflightError(
            f"s3://{bucket}/{key}: o cp do container não deixou o objeto no bucket"
        )
    return written[0].size


def _launch_judge(
    *,
    infra: InfraConfig,
    config: ExperimentConfig,
    commit: str,
    bucket: str,
    work_dir: Path,
) -> str:
    text = serialize_plan(empty_plan(config))
    check_empty_plan(text)

    local = work_dir / "preflight" / PLAN_FILENAME
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(text, encoding="utf-8")
    s3_cp(str(local), f"s3://{bucket}/{QUALITY_PLAN_KEY}")

    launch = judge_launch(
        config,
        infra,
        commit=commit,
        bucket=bucket,
        manifest_key=f"{MASTERS_PREFIX}{MANIFEST_NAME}",
        masters_prefix=MASTERS_PREFIX,
    )
    return launch_instance(replace(launch, tags={**launch.tags, "Name": PREFLIGHT_NAME_TAG}))


def _probe_vmaf(
    *, host: str, config: ExperimentConfig, bucket: str, instance_id: str, work_dir: Path
) -> VmafScore:
    """Guarda a saída crua antes de julgá-la, pelo motivo do `_probe_perf`."""
    probe = ssh_capture(
        host,
        vmaf_probe_command(
            master=next(iter_masters(build_masters_plan(config))),
            scale_flags=config.encode.scale_flags,
            vmaf_model=config.quality.vmaf_model,
            repo_dir=REMOTE_REPO_DIR,
            work_dir=REMOTE_WORK_DIR,
        ),
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    prefix = f"{JUDGE_PREFLIGHT_PREFIX}{instance_id}/"
    _keep_probe_evidence(
        probe,
        names=(VMAF_STDOUT_NAME, VMAF_STDERR_NAME),
        bucket=bucket,
        prefix=prefix,
        local=work_dir / "preflight" / instance_id,
    )
    if probe.returncode != 0:
        raise PreflightError(
            f"o probe do libvmaf saiu com status {probe.returncode}; a saída crua está em "
            f"s3://{bucket}/{prefix}"
        )
    return vmaf_score(probe.stdout)


def _put_from_judge(host: str, bucket: str, instance_id: str) -> str:
    """O `PutObject` do papel `judge` de dentro do container, listado e **deixado**.

    O `DeleteObject` do Orquestrador não alcança `quality/` (ADR-0016).
    """
    key = f"{JUDGE_PREFLIGHT_PREFIX}{instance_id}/{JUDGE_PUT_NAME}"
    return f"{key}: {_put_and_list(host, bucket, key)} bytes escritos pelo container e listados"


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
    JudgeError,
    PlanError,
    StateError,
    StatusError,
    OSError,
    json.JSONDecodeError,
    tomllib.TOMLDecodeError,
)


if __name__ == "__main__":
    raise SystemExit(main())
