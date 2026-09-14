"""O arquivo de estado da campanha — funções puras, sem I/O (D12 da Spec 4).

O `run` o escreve no work dir antes do primeiro lançamento e o reescreve a cada
mudança de estado; o `watch` o lê para voltar a vigiar de onde o Orquestrador
caiu. Quem abre o arquivo é o CLI; o que cada campo significa está no
`orchestrator/README.md`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from field_checks import FieldError, check_fields, check_int, check_non_empty_str
from vigilance import Vigilance


class StateError(Exception):
    """Arquivo de estado que o Orquestrador recusa a ler, com o campo nomeado."""


@dataclass(frozen=True)
class TrackedInstance:
    """Uma arquitetura do lançamento: o que se pergunta e o que se responde por ela."""

    instance: str
    instance_id: str
    instance_type: str
    pid: int | None
    block_count: int
    runs_total: int
    state: Vigilance


@dataclass(frozen=True)
class CampaignState:
    """O lançamento inteiro: o que ele mediu, de onde, e em que ponto cada fatia está."""

    bucket: str
    config_path: str
    commit: str
    slice_keys: tuple[str, ...]
    instances: tuple[TrackedInstance, ...]


def parse_state(payload: Any) -> CampaignState:
    """Valida o arquivo já parseado, falhando alto no primeiro campo defeituoso."""
    values = _values(CampaignState, _object(payload, "estado"), "")
    return CampaignState(
        **{
            **values,
            "slice_keys": _slice_keys(values["slice_keys"]),
            "instances": tuple(
                _tracked(entry, f"instances[{index}]")
                for index, entry in enumerate(values["instances"])
            ),
        }
    )


def serialize_state(state: CampaignState) -> str:
    """Serializa o estado de forma byte-determinística, como o plano de Cenários."""
    return json.dumps(asdict(state), indent=2, separators=(",", ": "), default=_enum_value) + "\n"


def _tracked(payload: Any, where: str) -> TrackedInstance:
    values = _values(TrackedInstance, _object(payload, where), f"{where}.")
    return TrackedInstance(**{**values, "state": Vigilance(values["state"])})


def _slice_keys(listed: list[Any]) -> tuple[str, ...]:
    try:
        for index, key in enumerate(listed):
            check_non_empty_str(f"slice_keys[{index}]", key)
    except FieldError as error:
        raise StateError(str(error)) from error
    return tuple(listed)


def _values(record: type, raw: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    try:
        return check_fields(record, raw, _CHECKS)
    except FieldError as error:
        raise StateError(f"{prefix}{error}") from error


def _object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateError(f"{where}: esperava um objeto, veio {type(value).__name__}")
    return value


def _check_list(field: str, value: Any) -> None:
    if not isinstance(value, list):
        raise FieldError(f"{field}: esperava uma lista, veio {type(value).__name__}")


def _check_pid(field: str, value: Any) -> None:
    if value is not None:
        check_int(field, value)


def _check_state(field: str, value: Any) -> None:
    try:
        Vigilance(value)
    except ValueError as error:
        raise FieldError(f"{field}: estado desconhecido: {value!r}") from error


def _enum_value(value: Any) -> str:
    if not isinstance(value, Enum):
        raise TypeError(f"{type(value).__name__} não é serializável no arquivo de estado")
    return value.value


_CHECKS: dict[str, Callable[[str, Any], None]] = {
    "bucket": check_non_empty_str,
    "config_path": check_non_empty_str,
    "commit": check_non_empty_str,
    "slice_keys": _check_list,
    "instances": _check_list,
    "instance": check_non_empty_str,
    "instance_id": check_non_empty_str,
    "instance_type": check_non_empty_str,
    "pid": _check_pid,
    "block_count": check_int,
    "runs_total": check_int,
    "state": _check_state,
}
