"""Espera por uma instância: predicado puro mais laço fino (ADR-0022, D23)."""

from __future__ import annotations

import time
from collections.abc import Callable

from command_output import CloudInitStatus, DescribedInstance

POLL_INTERVAL_SECONDS = 15.0


class WaitTimeout(Exception):
    """A espera estourou, nomeando a instância e o que se esperava dela."""


class BootstrapError(Exception):
    """O `cloud-init` da instância terminou em erro."""


def is_instance_ready(instance: DescribedInstance | None) -> bool:
    """`running` **e** com IP público — sem endereço não há SSH a abrir."""
    return instance is not None and instance.state == "running" and instance.public_ip is not None


def wait_for_instance_ready(
    probe: Callable[[], DescribedInstance | None],
    *,
    instance_id: str,
    timeout: float,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DescribedInstance:
    """Consulta o estado até a instância estar pronta, e devolve esse estado."""
    deadline = clock() + timeout
    while True:
        instance = probe()
        if is_instance_ready(instance):
            return instance
        if clock() >= deadline:
            last = instance.state if instance else "ausente do describe-instances"
            raise WaitTimeout(
                f"{instance_id}: esperava estado running com IP público em {timeout:g}s, "
                f"último estado foi {last}"
            )
        sleep(poll_interval)


def wait_for_bootstrap(
    probe: Callable[[], CloudInitStatus],
    *,
    instance_id: str,
    timeout: float,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Consulta o `cloud-init` até o bootstrap concluir.

    Erro é conclusão, não paciência: insistir até o timeout num `cloud-init` que
    já falhou é meia hora de instância faturando pela mesma resposta.
    """
    deadline = clock() + timeout
    while True:
        status = probe()
        if status is CloudInitStatus.DONE:
            return
        if status is CloudInitStatus.ERROR:
            raise BootstrapError(f"{instance_id}: cloud-init terminou em erro")
        if clock() >= deadline:
            raise WaitTimeout(f"{instance_id}: cloud-init não concluiu em {timeout:g}s")
        sleep(poll_interval)
