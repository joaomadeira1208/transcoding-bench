"""O núcleo puro do `run`: as duas guardas, o que sobe e quem é lançado, e a
decisão depois dos bootstraps (D7, D12, D13 e D18 da Spec 4) — ver `orchestrator/README.md`.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from campaign_state import CampaignState
from command_output import S3Object, TruncatedListing
from experiment_config import ExperimentConfig, InstanceRecord
from generate_scenarios import CANONICAL_FILENAME, SLICE_FILENAME
from scenario_plan import build_canonical_plan, build_instance_slices
from vigilance import is_standing

SCENARIOS_PREFIX = "scenarios/"
CANONICAL_KEY = f"{SCENARIOS_PREFIX}{CANONICAL_FILENAME}"

LAUNCH_SCRIPT = "encode/launch_container.sh"
DISPATCH_LOG_NAME = "launch_container.log"
DISPATCH_PID_NAME = "launch_container.pid"


class CampaignError(Exception):
    """O que o `run` observou antes de lançar e recusou."""


class Bootstrap(Enum):
    """Como a espera pelo `cloud-init` de uma arquitetura terminou."""

    DONE = "cloud-init concluído"
    ERROR = "cloud-init terminou em erro"
    TIMEOUT = "cloud-init não concluiu no prazo"
    NOT_AWAITED = "não esperada: o run parou na primeira falha"


@dataclass(frozen=True)
class ArchitectureSlice:
    """Uma arquitetura do lançamento: o registro que a lança e a fatia que ela consome."""

    instance: InstanceRecord
    key: str
    plan: dict[str, Any]

    @property
    def block_count(self) -> int:
        return len(self.plan["blocks"])

    @property
    def runs_total(self) -> int:
        return sum(len(block["runs"]) for block in self.plan["blocks"])


@dataclass(frozen=True)
class CampaignPlan:
    """O que sobe para `scenarios/`, por chave, e quem é lançado, na ordem declarada."""

    uploads: dict[str, dict[str, Any]]
    slices: tuple[ArchitectureSlice, ...]


def slice_name(instance: str) -> str:
    """O nome da fatia no bucket e no work dir da instância: o bootstrap a salva pelo basename."""
    return SLICE_FILENAME.format(instance=instance)


def slice_key(instance: str) -> str:
    return f"{SCENARIOS_PREFIX}{slice_name(instance)}"


def refuse_populated_runs(
    listing: Sequence[S3Object] | TruncatedListing, *, preflight_prefix: str
) -> str | None:
    """O motivo de recusar o bucket, ou `None` se `runs/` só tem o rastro do preflight (D13)."""
    if isinstance(listing, TruncatedListing):
        return "runs/ veio numa listagem truncada: o bucket já tem uma campanha"
    executions = [entry.key for entry in listing if not entry.key.startswith(preflight_prefix)]
    if not executions:
        return None
    return "\n".join(
        [
            f"runs/ já tem {len(executions)} objeto(s) fora de {preflight_prefix}: "
            f"é o bucket de outra campanha, ou desta disparada duas vezes",
            *(f"  {key}" for key in executions),
        ]
    )


def refuse_standing_instances(state: CampaignState) -> str | None:
    """O motivo de recusar o estado do work dir, ou `None` se nada nele está de pé."""
    standing = [each for each in state.instances if is_standing(each.state)]
    if not standing:
        return None
    return "\n".join(
        [
            f"{len(standing)} instância(s) de um lançamento anterior ainda de pé: "
            f"rode o watch --abort antes de um run novo",
            *(f"  {each.instance_id} ({each.instance}): {each.state.value}" for each in standing),
        ]
    )


def full_campaign(config: ExperimentConfig) -> CampaignPlan:
    """O canônico e uma fatia por registro `[[instance]]`, todas lançadas."""
    plan = build_canonical_plan(config)
    slices = build_instance_slices(plan)
    launched = tuple(
        ArchitectureSlice(instance=record, key=slice_key(record.id), plan=slices[record.id])
        for record in config.instances
    )
    return CampaignPlan(
        uploads={CANONICAL_KEY: plan, **{each.key: each.plan for each in launched}},
        slices=launched,
    )


def resumed_campaign(config: ExperimentConfig, slices: Mapping[str, Any]) -> CampaignPlan:
    """Só as fatias presentes sobem, na mesma chave, e só essas arquiteturas seguem (D18)."""
    if not slices:
        raise CampaignError("nenhuma fatia no diretório do --slices: não há o que lançar")

    declared = {record.id: record for record in config.instances}
    unknown = sorted(set(slices) - set(declared))
    if unknown:
        raise CampaignError(
            f"{', '.join(unknown)}: não é um id de instância declarado no --config "
            f"(declarados: {', '.join(sorted(declared))})"
        )

    launched = tuple(
        ArchitectureSlice(
            instance=record,
            key=slice_key(instance),
            plan=_checked_slice(instance, slices[instance]),
        )
        for instance, record in declared.items()
        if instance in slices
    )
    return CampaignPlan(uploads={each.key: each.plan for each in launched}, slices=launched)


def abort_reasons(outcomes: Mapping[str, Bootstrap]) -> tuple[str, ...]:
    """Vazio é "as três de pé, dispare"; qualquer linha é "termine as três" (D7)."""
    if not outcomes:
        return ("nenhuma arquitetura lançada",)
    return tuple(
        f"{instance}: {outcome.value}"
        for instance, outcome in outcomes.items()
        if outcome is not Bootstrap.DONE
    )


def dispatch_command(
    *,
    repo_dir: str,
    work_dir: str,
    plan_name: str,
    bucket: str,
    commit: str,
    instance_id: str,
    instance_type: str,
    run_timeout: int,
    total_timeout: int,
) -> list[str]:
    """O `launch_container.sh` desacoplado da sessão SSH, e o PID dele no stdout (D1).

    `setsid` e `nohup` com as três saídas redirecionadas: qualquer uma presa ao
    `ssh` faz a sessão esperar o `run_all.sh` acabar, e a queda dela fechar o
    stderr do laço. O PID vai para o arquivo do work dir e volta pelo stdout.
    """
    launch = shlex.join(
        [
            "bash",
            f"{repo_dir}/{LAUNCH_SCRIPT}",
            "--work-dir",
            work_dir,
            "--plan",
            plan_name,
            "--bucket",
            bucket,
            "--commit",
            commit,
            "--instance-id",
            instance_id,
            "--instance-type",
            instance_type,
            "--run-timeout",
            str(run_timeout),
            "--total-timeout",
            str(total_timeout),
        ]
    )
    log = shlex.quote(f"{work_dir}/{DISPATCH_LOG_NAME}")
    pid_file = shlex.quote(f"{work_dir}/{DISPATCH_PID_NAME}")
    return [
        "bash",
        "-c",
        f"setsid nohup {launch} </dev/null >{log} 2>&1 & echo $! | tee {pid_file}",
    ]


def _checked_slice(instance: str, payload: Any) -> dict[str, Any]:
    name = slice_name(instance)
    if not isinstance(payload, dict) or not isinstance(payload.get("blocks"), list):
        raise CampaignError(f"{name}: não é uma fatia: esperava um objeto com a lista blocks")
    foreign = sorted(
        {
            str(_block_instance(block))
            for block in payload["blocks"]
            if _block_instance(block) != instance
        }
    )
    if foreign:
        raise CampaignError(
            f"{name}: tem blocos de outra arquitetura ({', '.join(foreign)}): "
            f"a fatia de {instance} só pode ter blocos de {instance}"
        )
    return payload


def _block_instance(block: Any) -> str | None:
    return block.get("instance") if isinstance(block, dict) else None
