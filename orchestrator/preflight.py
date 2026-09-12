"""O núcleo puro do `preflight`: o veredito sobre o `perf` e a tabela do passo.

As funções recebem dado já buscado e devolvem dado — quem lança a instância é o
`instance_launch.py`, e quem abre o SSH e lista o bucket é o `orchestrator.py`,
sobre o `external.py`. Os dois `docker run` daqui são argv, e como o resto do
argv do sistema ficam sem teste (ADR-0022): o que ganha teste é a decisão que se
toma sobre a saída deles — e a lista de eventos que o `perf stat` carrega, que é
desenho experimental (ADR-0006) e não argv de sistema.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from masters_launch import IMAGE_TAG

NOT_SUPPORTED = "<not supported>"

PROBE_CONTENT = "preflight"
PROBE_PATH = "/tmp/preflight"

HEADER = ("passo", "resultado", "detalhe")


class PreflightError(Exception):
    """O resultado que um passo do `preflight` observou e recusou."""


class Step(Enum):
    STS = "sts"
    BUCKETS = "buckets"
    SSM = "ssm"
    GIT = "git"
    SYNC = "s3-sync"
    AMI = "ami"
    LAUNCH = "launch"
    BOOTSTRAP = "bootstrap"
    PERF = "perf-stat"
    ENCODE_PUT = "s3-put"
    TERMINATE = "terminate"


STEPS = tuple(Step)


class Outcome(Enum):
    PASSED = "passou"
    FAILED = "falhou"
    SKIPPED = "não rodou"


@dataclass(frozen=True)
class StepResult:
    step: Step
    outcome: Outcome
    detail: str


def perf_probe_command(events: Sequence[str]) -> list[str]:
    """O `perf stat` sobre um comando trivial, dentro da imagem que a Execução usa."""
    return [
        "sudo",
        "docker",
        "run",
        "--rm",
        "--cap-add=PERFMON",
        IMAGE_TAG,
        "perf",
        "stat",
        "-j",
        "-e",
        ",".join(events),
        "-o",
        "/dev/stdout",
        "--",
        "true",
    ]


def encode_put_command(*, bucket: str, key: str) -> list[str]:
    """O `s3 cp` do papel `encode`, pelo caminho real: de dentro do container."""
    return [
        "sudo",
        "docker",
        "run",
        "--rm",
        IMAGE_TAG,
        "bash",
        "-c",
        f"printf {PROBE_CONTENT} > {PROBE_PATH} "
        f"&& aws s3 cp --only-show-errors {PROBE_PATH} s3://{bucket}/{key}",
    ]


def perf_counter_values(raw: str, events: Sequence[str]) -> dict[str, float]:
    """O valor que o `perf stat -j` abriu para cada evento pedido, ou a recusa que os nomeia."""
    counters = _counters(raw)
    counted: dict[str, float] = {}
    refused: list[str] = []
    for event in events:
        try:
            counted[event] = _counter_value(counters, event)
        except PreflightError as error:
            refused.append(str(error))

    if refused:
        raise PreflightError(
            f"{len(refused)} de {len(events)} eventos sem contador dentro do container: "
            f"{'; '.join(refused)} "
            f"(o perf stat abriu: {', '.join(name for name, _ in counters) or 'nada'})"
        )
    return counted


def perf_detail(counted: Mapping[str, float]) -> str:
    """A linha da tabela do passo: os eventos que abriram contador, com o valor de cada um."""
    return "dentro do container: " + ", ".join(
        f"{event} = {value:.0f}" for event, value in counted.items()
    )


def _counter_value(counters: Sequence[tuple[str, str]], event: str) -> float:
    reported = [value for name, value in counters if name == event or name.startswith(f"{event}:")]
    if not reported:
        raise PreflightError(f"{event}: nenhum contador com esse nome")

    value = reported[0]
    if value.strip() == NOT_SUPPORTED:
        raise PreflightError(f"{event}: o perf stat voltou {NOT_SUPPORTED}")
    try:
        return float(value)
    except ValueError as error:
        raise PreflightError(f"{event}: counter-value não é um número: {value!r}") from error


def summarize(observed: Sequence[StepResult]) -> tuple[StepResult, ...]:
    """A tabela inteira, na ordem declarada: o que não rodou aparece dizendo isso."""
    seen: dict[Step, StepResult] = {}
    for result in observed:
        if result.step in seen:
            raise PreflightError(f"{result.step.value}: passo registrado duas vezes")
        seen[result.step] = result

    return tuple(seen.get(step, StepResult(step, Outcome.SKIPPED, "")) for step in STEPS)


def failed(results: Sequence[StepResult]) -> bool:
    return any(result.outcome is Outcome.FAILED for result in results)


def render_table(results: Sequence[StepResult]) -> str:
    """A tabela como o pesquisador a lê, e como ela entra no relatório do piloto."""
    rows = [
        HEADER,
        *(
            (result.step.value, result.outcome.value, _single_line(result.detail))
            for result in results
        ),
    ]
    step_width = max(len(step) for step, _, _ in rows)
    outcome_width = max(len(outcome) for _, outcome, _ in rows)
    return "\n".join(
        f"{step:<{step_width}}  {outcome:<{outcome_width}}  {detail}".rstrip()
        for step, outcome, detail in rows
    )


def _single_line(detail: str) -> str:
    """O `stderr` que um comando externo devolveu cabe numa célula, ou não é tabela."""
    return " ".join(detail.split())


def _counters(raw: str) -> list[tuple[str, str]]:
    """Os pares evento/valor da saída, ignorando o cabeçalho que varia com a versão."""
    return [
        (str(line["event"]), str(line["counter-value"]))
        for line in map(_json_object, raw.splitlines())
        if line is not None and "event" in line and "counter-value" in line
    ]


def _json_object(line: str) -> Mapping[str, object] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None
