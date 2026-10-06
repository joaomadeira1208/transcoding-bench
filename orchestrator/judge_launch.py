"""O núcleo puro do `judge`: o lançamento do Juiz, a entrada dele no arquivo de estado
e o disparo desacoplado (D16 a D18 da Spec 5) — ver `orchestrator/README.md`.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

from campaign_launch import detached_command
from campaign_state import CampaignState, TrackedInstance
from experiment_config import ExperimentConfig
from infra_config import InfraConfig
from instance_launch import (
    IMDS_HOP_LIMIT,
    NAME_PREFIX,
    InstanceLaunch,
    bootstrap_args,
    image_for_arch,
    render_user_data,
)
from quality_plan import PLAN_FILENAME
from status_check import JUDGE_STEM, Role
from vigilance import Vigilance

JUDGE_ROLE = "judge"
JUDGE_NAME = f"{NAME_PREFIX}-{JUDGE_ROLE}"

JUDGE_VOLUME_SIZE_GB = 100

QUALITY_PREFIX = "quality/"
QUALITY_PLAN_KEY = f"{QUALITY_PREFIX}{PLAN_FILENAME}"
QUALITY_RESULTS_PREFIX = f"{QUALITY_PREFIX}results/"

JUDGE_LAUNCH_SCRIPT = "judge/launch_container.sh"


class JudgeError(Exception):
    """O que o `judge` observou antes de lançar e recusou."""


def judge_launch(
    config: ExperimentConfig,
    infra: InfraConfig,
    *,
    commit: str,
    bucket: str,
    manifest_key: str,
    masters_prefix: str,
) -> InstanceLaunch:
    """O `run-instances` do Juiz, do tipo e da arquitetura que `[quality.judge]` declara."""
    judge = config.quality.judge
    return InstanceLaunch(
        instance_type=judge.instance_type,
        image_id=image_for_arch(infra.amis, judge.arch, judge.instance_type),
        subnet_id=infra.subnet_id,
        security_group_id=infra.security_groups.ephemeral,
        instance_profile=infra.instance_profiles.judge,
        key_name=infra.key_pair_name,
        user_data=render_user_data(
            commit=commit,
            role=JUDGE_ROLE,
            role_args=bootstrap_args(
                bucket=bucket,
                plan_key=QUALITY_PLAN_KEY,
                manifest_key=manifest_key,
                masters_prefix=masters_prefix,
            ),
        ),
        volume_size_gb=JUDGE_VOLUME_SIZE_GB,
        imds_hop_limit=IMDS_HOP_LIMIT,
        tags={"Name": JUDGE_NAME, "role": JUDGE_ROLE, "commit": commit},
    )


def judge_state(
    previous: CampaignState | None,
    *,
    bucket: str,
    config_path: str,
    commit: str,
    total_timeout: int,
) -> CampaignState:
    """O arquivo a que a entrada do Juiz vai ser acrescentada, ou o que nasce para ela (D17)."""
    if previous is None:
        return CampaignState(
            bucket=bucket,
            config_path=config_path,
            commit=commit,
            total_timeout=total_timeout,
            slice_keys=(),
            instances=(),
        )
    if previous.bucket != bucket:
        raise JudgeError(
            f"o arquivo de estado é do bucket {previous.bucket}, e o Juiz foi pedido sobre "
            f"{bucket}: o poll procuraria o marcador dele no bucket errado. Mova o "
            f"arquivo para o lado, sem apagá-lo, e o judge cria um novo só com o Juiz"
        )
    return previous


def launched_judge(
    instance_id: str,
    *,
    instance_type: str,
    plan: Mapping[str, Any],
    commit: str,
    total_timeout: int,
) -> TrackedInstance:
    """O Juiz recém-lançado como o arquivo de estado o guarda, antes do disparo.

    Cada output julgado é um run do Juiz (D15); o plano dele não tem blocos.
    """
    return TrackedInstance(
        role=Role.JUDGE,
        instance=JUDGE_STEM,
        instance_id=instance_id,
        instance_type=instance_type,
        pid=None,
        block_count=0,
        runs_total=len(plan["outputs"]),
        commit=commit,
        total_timeout=total_timeout,
        state=Vigilance.BOOTSTRAPPING,
        outcome=None,
    )


def judge_dispatch_command(
    *,
    repo_dir: str,
    work_dir: str,
    bucket: str,
    commit: str,
    instance_id: str,
    instance_type: str,
    output_timeout: int,
    total_timeout: int,
) -> list[str]:
    """O `launch_container.sh` do Juiz, pelo mesmo disparo desacoplado do encode."""
    launch = shlex.join(
        [
            "bash",
            f"{repo_dir}/{JUDGE_LAUNCH_SCRIPT}",
            "--work-dir",
            work_dir,
            "--plan",
            PLAN_FILENAME,
            "--bucket",
            bucket,
            "--commit",
            commit,
            "--instance-id",
            instance_id,
            "--instance-type",
            instance_type,
            "--output-timeout",
            str(output_timeout),
            "--total-timeout",
            str(total_timeout),
        ]
    )
    return detached_command(launch, work_dir=work_dir)
