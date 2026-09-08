"""A única casa de `subprocess.run` do Orquestrador — ver `orchestrator/README.md`."""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from command_output import (
    CloudInitStatus,
    DescribedInstance,
    S3Object,
    parse_cloud_init_status,
    parse_describe_instances,
    parse_get_parameter,
    parse_list_objects,
    parse_run_instances,
)

# A AMI Ubuntu 24.04 (ADR-0015) expõe o volume raiz aqui; o `--block-device-mappings`
# de um device que a AMI não declara é aceito e cria um volume extra, deixando o
# raiz no tamanho default.
ROOT_DEVICE_NAME = "/dev/sda1"

VOLUME_TYPE = "gp3"

SSH_USER = "ubuntu"

SSH_KEY_PATH = Path.home() / ".ssh" / "transcoding-bench.pem"

SSH_CONNECT_TIMEOUT_SECONDS = 10
SSH_KEEPALIVE_INTERVAL_SECONDS = 15
SSH_KEEPALIVE_COUNT_MAX = 4

# O `ssh` reserva o 255 para as falhas dele próprio; qualquer outro código veio
# do comando remoto.
SSH_UNREACHABLE_RETURNCODE = 255

# O `cloud-init status` sai 1 em `error` e 2 em `degraded`. Restringir a `(0,)`
# faz uma instância que falhou o bootstrap chegar como falha de comando, e aí o
# `BootstrapError` do laço de espera nunca dispara.
CLOUD_INIT_RETURNCODES = (0, 1, 2)

CLOUD_INIT_PROBE_TIMEOUT_SECONDS = 60.0


class ExternalCommandError(Exception):
    """Comando externo que terminou em erro, com o `stderr` preservado."""

    def __init__(self, message: str, *, returncode: int | None) -> None:
        super().__init__(message)
        self.returncode = returncode


def run_instances(
    *,
    instance_type: str,
    image_id: str,
    subnet_id: str,
    security_group_id: str,
    instance_profile: str,
    key_name: str,
    user_data: str,
    volume_size_gb: int,
    imds_hop_limit: int,
    tags: Mapping[str, str],
) -> str:
    """Lança uma instância e devolve o id dela."""
    root_volume = [
        {
            "DeviceName": ROOT_DEVICE_NAME,
            "Ebs": {
                "VolumeSize": volume_size_gb,
                "VolumeType": VOLUME_TYPE,
                "DeleteOnTermination": True,
            },
        }
    ]
    metadata_options = {
        "HttpEndpoint": "enabled",
        "HttpTokens": "required",
        "HttpPutResponseHopLimit": imds_hop_limit,
    }
    tag_specifications = [
        {
            "ResourceType": "instance",
            "Tags": [{"Key": key, "Value": value} for key, value in tags.items()],
        }
    ]
    return parse_run_instances(
        _run(
            [
                "aws",
                "ec2",
                "run-instances",
                "--output",
                "json",
                "--count",
                "1",
                "--image-id",
                image_id,
                "--instance-type",
                instance_type,
                "--subnet-id",
                subnet_id,
                "--security-group-ids",
                security_group_id,
                "--iam-instance-profile",
                json.dumps({"Name": instance_profile}),
                "--key-name",
                key_name,
                "--user-data",
                user_data,
                "--block-device-mappings",
                json.dumps(root_volume),
                "--metadata-options",
                json.dumps(metadata_options),
                "--tag-specifications",
                json.dumps(tag_specifications),
            ]
        )
    )


def terminate_instances(instance_ids: Sequence[str]) -> None:
    _run(["aws", "ec2", "terminate-instances", "--output", "json", "--instance-ids", *instance_ids])


def describe_instances(instance_ids: Sequence[str]) -> list[DescribedInstance]:
    return parse_describe_instances(
        _run(
            [
                "aws",
                "ec2",
                "describe-instances",
                "--output",
                "json",
                "--instance-ids",
                *instance_ids,
            ]
        )
    )


def s3_cp(source: str, destination: str) -> None:
    """Copia um objeto, nos dois sentidos: cada ponta é caminho local ou `s3://`."""
    _run(["aws", "s3", "cp", "--only-show-errors", source, destination])


def s3_sync(source: str, destination: str) -> None:
    _run(["aws", "s3", "sync", "--only-show-errors", source, destination])


def s3_list_prefix(bucket: str, prefix: str) -> list[S3Object]:
    return parse_list_objects(
        _run(
            [
                "aws",
                "s3api",
                "list-objects-v2",
                "--output",
                "json",
                "--bucket",
                bucket,
                "--prefix",
                prefix,
            ]
        )
    )


def ssm_get_parameter(name: str) -> str:
    return parse_get_parameter(
        _run(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--output",
                "json",
                "--name",
                name,
                "--with-decryption",
            ]
        )
    )


def ssh_exec(
    host: str,
    command: Sequence[str],
    *,
    key_path: Path = SSH_KEY_PATH,
    allowed_returncodes: Sequence[int] = (0,),
    timeout: float | None = None,
) -> str:
    """Roda um comando na instância e devolve o `stdout`, bloqueando até o fim."""
    return _run(
        [
            "ssh",
            "-i",
            str(key_path),
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}",
            "-o",
            f"ServerAliveInterval={SSH_KEEPALIVE_INTERVAL_SECONDS}",
            "-o",
            f"ServerAliveCountMax={SSH_KEEPALIVE_COUNT_MAX}",
            f"{SSH_USER}@{host}",
            shlex.join(command),
        ],
        allowed_returncodes=allowed_returncodes,
        timeout=timeout,
    )


def cloud_init_status(host: str, *, key_path: Path = SSH_KEY_PATH) -> CloudInitStatus | None:
    """O estado do bootstrap, ou `None` enquanto a instância não atende SSH."""
    try:
        output = ssh_exec(
            host,
            ["cloud-init", "status"],
            key_path=key_path,
            allowed_returncodes=CLOUD_INIT_RETURNCODES,
            timeout=CLOUD_INIT_PROBE_TIMEOUT_SECONDS,
        )
    except ExternalCommandError as error:
        if error.returncode == SSH_UNREACHABLE_RETURNCODE:
            return None
        raise
    return parse_cloud_init_status(output)


def git_rev_parse(ref: str = "HEAD") -> str:
    """O SHA que as instâncias vão receber para clonar (ADR-0021)."""
    return _run(["git", "rev-parse", "--verify", ref]).strip()


def _run(
    argv: Sequence[str],
    *,
    allowed_returncodes: Sequence[int] = (0,),
    timeout: float | None = None,
) -> str:
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired as error:
        raise ExternalCommandError(
            f"{_command_name(argv)} não respondeu em {timeout:g}s", returncode=None
        ) from error

    if completed.returncode not in allowed_returncodes:
        raise ExternalCommandError(
            f"{_command_name(argv)} falhou (exit {completed.returncode}): "
            f"{completed.stderr.strip()}",
            returncode=completed.returncode,
        )
    return completed.stdout


def _command_name(argv: Sequence[str]) -> str:
    # Só o nome do subcomando: o argv inteiro carrega o user-data e o comando
    # remoto, e o que resolve um `AccessDenied` é o `stderr` (ADR-0012).
    return " ".join(argv[:3])
