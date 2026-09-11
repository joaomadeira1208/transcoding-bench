"""Leitura da saída dos comandos externos — funções puras, sem I/O (ADR-0022)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class OutputError(Exception):
    """Saída de comando externo que o Orquestrador recusa a ler."""


@dataclass(frozen=True)
class DescribedInstance:
    """O que o `describe-instances` diz de uma instância, reduzido ao que se usa."""

    instance_id: str
    state: str
    public_ip: str | None
    private_ip: str | None


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int


class CloudInitStatus(Enum):
    DONE = "done"
    ERROR = "error"
    RUNNING = "running"


def parse_run_instances(raw: str) -> str:
    """O id da instância lançada."""
    instances = _field(_parse_object(raw, "run-instances"), "Instances", list)
    if len(instances) != 1:
        raise OutputError(f"run-instances: esperava exatamente 1 instância, veio {len(instances)}")
    return _field(_as_object(instances[0], "Instances[0]"), "InstanceId", str)


def parse_describe_instances(raw: str) -> list[DescribedInstance]:
    """O estado de cada instância descrita, achatando as reservas.

    Resposta sem reservas é lista vazia: a consulta feita logo depois do
    lançamento pode não achar a instância ainda, e isso não é erro.
    """
    reservations = _field(_parse_object(raw, "describe-instances"), "Reservations", list)
    return [
        _described_instance(instance)
        for reservation in reservations
        for instance in _field(_as_object(reservation, "Reservations[]"), "Instances", list)
    ]


def parse_list_objects(raw: str) -> list[S3Object]:
    """As chaves e os tamanhos de uma listagem de prefixo, como a API as devolveu."""
    if not raw.strip():
        return []

    payload = _parse_object(raw, "list-objects-v2")
    _reject_truncation(payload)
    return [_s3_object(entry) for entry in _field(payload, "Contents", list, default=[])]


def parse_get_parameter(raw: str) -> str:
    """O valor do parâmetro, já decriptado pela CLI.

    Nenhuma mensagem daqui interpola o valor: ele é a chave privada de SSH
    (ADR-0016), e o destino das mensagens é o log do Orquestrador.
    """
    parameter = _field(_parse_object(raw, "get-parameter"), "Parameter", dict)
    value = parameter.get("Value")
    if type(value) is not str or not value:
        raise OutputError("get-parameter: Parameter.Value ausente ou não é uma string")
    return value


def parse_caller_identity(raw: str) -> str:
    """O ARN da identidade que a credencial do IMDS resolve.

    O ARN e não a conta: o que a auto-checagem do `preflight` precisa dizer é
    **qual papel** está falando, e é a role errada — não a conta errada — que
    produz o `AccessDenied` no meio do lançamento.
    """
    return _field(_parse_object(raw, "get-caller-identity"), "Arn", str)


def parse_cloud_init_status(raw: str) -> CloudInitStatus:
    """O estado do bootstrap, lido do `cloud-init status`."""
    for line in raw.splitlines():
        _, marker, rest = line.partition("status:")
        if not marker:
            continue
        status = rest.strip().removeprefix(DEGRADED_PREFIX)
        if status == "disabled":
            raise OutputError("cloud-init: desabilitado na instância, o user-data não vai rodar")
        if status not in _CLOUD_INIT_STATUSES:
            raise OutputError(f"cloud-init: status desconhecido: {status!r}")
        return _CLOUD_INIT_STATUSES[status]

    raise OutputError(f"cloud-init: saída sem linha de status: {raw.strip()[:120]!r}")


def _s3_object(entry: Any) -> S3Object:
    content = _as_object(entry, "Contents[]")
    return S3Object(key=_field(content, "Key", str), size=_field(content, "Size", int))


def _described_instance(instance: Any) -> DescribedInstance:
    described = _as_object(instance, "Instances[]")
    return DescribedInstance(
        instance_id=_field(described, "InstanceId", str),
        state=_field(_field(described, "State", dict), "Name", str),
        public_ip=_optional_field(described, "PublicIpAddress", str),
        private_ip=_optional_field(described, "PrivateIpAddress", str),
    )


# `degraded` é prefixo e não estado: o boot concluiu com erro recuperável, e
# tratá-lo como token desconhecido derruba um lançamento saudável.
DEGRADED_PREFIX = "degraded "

_CLOUD_INIT_STATUSES = {
    "done": CloudInitStatus.DONE,
    "error": CloudInitStatus.ERROR,
    "running": CloudInitStatus.RUNNING,
    # Antes de o cloud-init começar; para quem espera, é o mesmo que rodando.
    "not run": CloudInitStatus.RUNNING,
}


def _reject_truncation(payload: dict[str, Any]) -> None:
    """A premissa da ADR-0022, asserida onde ela pode deixar de valer.

    A CLI v2 agrega as páginas sozinha, então uma listagem truncada só chega aqui
    se alguém tiver acrescentado `--page-size`/`--max-items` ao argv — e a partir
    daí a listagem perde chaves em silêncio.
    """
    if payload.get("IsTruncated") or "NextToken" in payload or "NextContinuationToken" in payload:
        raise OutputError(
            "list-objects-v2: página truncada — a CLI v2 agrega páginas sozinha, "
            "então isto é --page-size/--max-items no argv"
        )


def _parse_object(raw: str, command: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise OutputError(f"{command}: saída não é JSON válido: {error}") from error
    return _as_object(payload, command)


def _as_object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OutputError(f"{where}: esperava um objeto JSON, veio {type(value).__name__}")
    return value


def _optional_field(payload: dict[str, Any], name: str, expected: type) -> Any:
    return _field(payload, name, expected) if name in payload else None


_ABSENT = object()


def _field(payload: dict[str, Any], name: str, expected: type, default: Any = _ABSENT) -> Any:
    value = payload.get(name, default)
    if value is _ABSENT:
        raise OutputError(f"{name}: campo ausente na saída")
    # Tipo exato, e não `isinstance`: `Size: true` passaria como inteiro.
    if value is not default and type(value) is not expected:
        raise OutputError(f"{name}: esperava {expected.__name__}, veio {value!r}")
    return value
