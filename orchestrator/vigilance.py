"""A decisão de um poll sobre uma arquitetura — função pura, sem I/O (D2/D8/D9).

As três perguntas do poll chegam já respondidas: o que o `describe-instances`
disse, o que o `kill -0` por SSH respondeu e o veredito do `status_check` sobre o
marcador. Quem pergunta é o laço do `watch`; a precedência entre as respostas,
e por que ela é essa, estão no `orchestrator/README.md`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from status_check import check_done_marker

UNANSWERED_POLL_LIMIT = 3

PENDING = "pending"
RUNNING = "running"


class Liveness(Enum):
    """O que o `kill -0` no PID gravado respondeu, por SSH."""

    ALIVE = "alive"
    DEAD = "dead"
    NO_ANSWER = "no answer"
    NOT_DISPATCHED = "not dispatched"


class Marker(Enum):
    """O veredito do `status_check` sobre `status/{instance_type}_done`."""

    VALID = "valid"
    OTHER_INSTANCE = "other instance"
    ABSENT = "absent"


class Vigilance(Enum):
    """O estado de uma arquitetura depois de um poll."""

    BOOTSTRAPPING = "bootstrapping"
    RUNNING = "running"
    READY_TO_TERMINATE = "ready to terminate"
    DEAD = "dead"
    UNRESPONSIVE = "unresponsive"


def marker_verdict(payload: Any, *, instance_id: str) -> Marker:
    """O marcador baixado — ou `None`, quando `status/` não tem o objeto."""
    if payload is None:
        return Marker.ABSENT
    if check_done_marker(payload, instance_id=instance_id) is None:
        return Marker.OTHER_INSTANCE
    return Marker.VALID


def decide_vigilance(
    *,
    described_state: str | None,
    liveness: Liveness,
    marker: Marker,
    unanswered_polls: int,
) -> Vigilance:
    """O estado da arquitetura neste poll; `described_state` é `None` se ela sumiu."""
    if marker is Marker.VALID:
        return Vigilance.READY_TO_TERMINATE
    if described_state == PENDING:
        return Vigilance.BOOTSTRAPPING
    if described_state != RUNNING:
        return Vigilance.DEAD
    if liveness is Liveness.NOT_DISPATCHED:
        return Vigilance.BOOTSTRAPPING
    if liveness is Liveness.NO_ANSWER:
        if unanswered_polls > UNANSWERED_POLL_LIMIT:
            return Vigilance.DEAD
        return Vigilance.UNRESPONSIVE
    if liveness is Liveness.DEAD:
        return Vigilance.DEAD
    return Vigilance.RUNNING
