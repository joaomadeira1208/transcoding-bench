"""O lançamento de uma Instância de encode e a espera pelo bootstrap dela.

O user-data fino e a espera não são do papel `encode`: o `prepare-masters` sobe a
instância dele pelos mesmos dois. Ver `orchestrator/README.md`.
"""

from __future__ import annotations

import shlex
import string
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from command_output import OutputError
from experiment_config import ExperimentConfig, InstanceRecord
from external import cloud_init_status, described_instance, run_instances
from infra_config import Amis, InfraConfig
from instance_wait import wait_for_bootstrap, wait_for_instance_ready

USER_DATA_TEMPLATE = Path(__file__).resolve().parent / "user-data.sh"

REPO_URL = "https://github.com/joaomadeira1208/transcoding-bench.git"

# O caminho do clone é literal no `user-data.sh`, e não um placeholder: mudá-lo
# lá sem mudá-lo aqui faz o `docker run` montar um diretório que não existe.
REMOTE_REPO_DIR = "/home/ubuntu/transcoding-bench"
REMOTE_WORK_DIR = "/home/ubuntu/work"

# O papel do bootstrap e o valor da tag `role`. A policy só autoriza o
# `TerminateInstances` sobre `encode`, `judge` e `masters` (ADR-0016): uma tag
# `preflight` deixaria a instância deste passo interminável por quem a lançou.
ENCODE_ROLE = "encode"

NAME_PREFIX = "transcoding-bench"

ENCODE_VOLUME_SIZE_GB = 200

# O `aws` do papel roda dentro do container (ADR-0018).
IMDS_HOP_LIMIT = 2

READY_TIMEOUT_SECONDS = 600.0

# O bootstrap dos papéis de medição termina no `docker build`, que compila o
# FFmpeg da ADR-0008 (10 a 20 min, ADR-0013).
BOOTSTRAP_TIMEOUT_SECONDS = 3600.0


class LaunchError(Exception):
    """O que o lançamento de uma instância observou e recusou."""


@dataclass(frozen=True)
class EncodeTarget:
    """O que o lançamento precisa saber do tipo pedido: o registro e a AMI dele."""

    instance: InstanceRecord
    image_id: str


def encode_target(config: ExperimentConfig, amis: Amis, instance_type: str) -> EncodeTarget:
    """Resolve o tipo pedido contra a spec: nenhuma arquitetura é derivada do nome."""
    declared = {instance.instance_type: instance for instance in config.instances}
    instance = declared.get(instance_type)
    if instance is None:
        raise LaunchError(
            f"{instance_type} não é um tipo declarado no experiment.toml "
            f"(declarados: {', '.join(sorted(declared))})"
        )

    images = {"arm64": amis.encode_arm64, "x86_64": amis.encode_amd64}
    image_id = images.get(instance.arch)
    if image_id is None:
        raise LaunchError(
            f"{instance_type}: o arquivo de infra não tem AMI para a arquitetura "
            f"'{instance.arch}' (tem: {', '.join(sorted(images))})"
        )
    return EncodeTarget(instance=instance, image_id=image_id)


def encode_name(instance: InstanceRecord) -> str:
    """O `Name` de uma Instância da campanha, uma por arquitetura (D14 da Spec 4).

    Recebe o registro, e não o id curto solto: em todo o resto deste módulo
    `instance_id` é o `i-…` da EC2, e um `Name` construído sobre esse é aceito
    pelo `run-instances` sem nada acusar.
    """
    return f"{NAME_PREFIX}-{ENCODE_ROLE}-{instance.id}"


def encode_tags(*, name: str, commit: str) -> dict[str, str]:
    """As três tags de uma Instância de encode, para o `run-instances`."""
    return {"Name": name, "role": ENCODE_ROLE, "commit": commit}


def launch_encode(
    *,
    target: EncodeTarget,
    infra: InfraConfig,
    commit: str,
    bucket: str,
    slice_key: str,
    manifest_key: str,
    masters_prefix: str,
    volume_size_gb: int,
    tags: Mapping[str, str],
) -> str:
    """Lança a Instância que vai consumir a fatia daquela arquitetura."""
    role_args = [
        "--work-dir",
        REMOTE_WORK_DIR,
        "--bucket",
        bucket,
        "--plan-key",
        slice_key,
        "--manifest-key",
        manifest_key,
        "--masters-prefix",
        masters_prefix,
    ]
    try:
        return run_instances(
            instance_type=target.instance.instance_type,
            image_id=target.image_id,
            subnet_id=infra.subnet_id,
            security_group_id=infra.security_groups.ephemeral,
            instance_profile=infra.instance_profiles.encode,
            key_name=infra.key_pair_name,
            user_data=render_user_data(commit=commit, role=ENCODE_ROLE, role_args=role_args),
            volume_size_gb=volume_size_gb,
            imds_hop_limit=IMDS_HOP_LIMIT,
            tags=tags,
        )
    except OutputError as error:
        raise LaunchError(orphan_hint(f"Name={tags['Name']}", error)) from error


def wait_for_bootstrapped_instance(
    instance_id: str,
    *,
    report: Callable[[str], None],
) -> str:
    """Espera `running` com IP privado e depois o `cloud-init`, e devolve o host."""
    instance = wait_for_instance_ready(
        lambda: described_instance(instance_id),
        instance_id=instance_id,
        timeout=READY_TIMEOUT_SECONDS,
    )
    host = instance.private_ip
    report(f"{instance_id}: running em {host}, esperando o cloud-init")
    wait_for_bootstrap(
        lambda: cloud_init_status(host),
        instance_id=instance_id,
        timeout=BOOTSTRAP_TIMEOUT_SECONDS,
    )
    return host


def render_user_data(*, commit: str, role: str, role_args: Sequence[str]) -> str:
    """O user-data fino de um papel (ADR-0013/0021), com os argumentos do bootstrap dele."""
    template = string.Template(USER_DATA_TEMPLATE.read_text(encoding="utf-8"))
    return template.substitute(
        repo_url=REPO_URL,
        commit=commit,
        role=role,
        role_args=shlex.join(role_args),
    )


def orphan_hint(tag: str, error: Exception) -> str:
    return (
        f"o run-instances pode ter lançado uma instância que este passo não sabe nomear: "
        f"procure por {tag} no describe-instances e termine-a à mão ({error})"
    )
