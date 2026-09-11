"""Leitura do arquivo de infra — função pura, sem I/O (ADR-0019).

O que o Terraform sabe e o Orquestrador não pode adivinhar: subnet, security
groups, perfis, AMIs e buckets. Descoberta por tag foi rejeitada — a seleção é
explícita, e a forma do arquivo está no `orchestrator/README.md`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any


class InfraError(Exception):
    """Arquivo de infra que o Orquestrador recusa a ler. A mensagem nomeia o campo."""


@dataclass(frozen=True)
class SecurityGroups:
    orchestrator: str
    ephemeral: str


@dataclass(frozen=True)
class InstanceProfiles:
    orchestrator: str
    encode: str
    judge: str
    masters: str


@dataclass(frozen=True)
class Amis:
    orchestrator: str
    encode_amd64: str
    encode_arm64: str


@dataclass(frozen=True)
class Buckets:
    campaign: str
    pilot: str


@dataclass(frozen=True)
class InfraConfig:
    subnet_id: str
    security_groups: SecurityGroups
    instance_profiles: InstanceProfiles
    key_pair_name: str
    amis: Amis
    buckets: Buckets
    ssh_private_key_parameter_name: str


def parse_infra(payload: Mapping[str, Any]) -> InfraConfig:
    """Valida o arquivo já parseado, falhando alto no primeiro campo defeituoso."""
    raw = _object(payload, "infra")
    return InfraConfig(
        subnet_id=_identifier(raw, "subnet_id", "infra"),
        security_groups=_section(raw, "security_groups", SecurityGroups),
        instance_profiles=_section(raw, "instance_profiles", InstanceProfiles),
        key_pair_name=_identifier(raw, "key_pair_name", "infra"),
        amis=_section(raw, "amis", Amis),
        buckets=_section(raw, "buckets", Buckets),
        ssh_private_key_parameter_name=_identifier(raw, "ssh_private_key_parameter_name", "infra"),
    )


def _section[Section](raw: Mapping[str, Any], key: str, section: type[Section]) -> Section:
    table = _object(_require(raw, key, "infra"), key)
    return section(**{f.name: _identifier(table, f.name, key) for f in fields(section)})


def _object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InfraError(f"{where}: esperava um objeto, veio {type(value).__name__}")
    return value


def _require(raw: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in raw:
        raise InfraError(f"{where}: campo ausente: {_path(where, key)}")
    return raw[key]


def _identifier(raw: Mapping[str, Any], key: str, where: str) -> str:
    """Identificador da AWS: string não-vazia, porque é assim que ele vira argv."""
    value = _require(raw, key, where)
    if not isinstance(value, str) or not value:
        raise InfraError(
            f"{where}: {_path(where, key)} tem que ser uma string não-vazia, veio {value!r}"
        )
    return value


def _path(where: str, key: str) -> str:
    return key if where == "infra" else f"{where}.{key}"
