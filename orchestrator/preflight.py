"""O núcleo puro do `preflight`: o veredito sobre o `perf` e a tabela do passo.

As funções recebem dado já buscado e devolvem dado — quem lança a instância é o
`instance_launch.py`, e quem abre o SSH e lista o bucket é o `orchestrator.py`,
sobre o `external.py`. Os dois `docker run` daqui são argv, e como o resto do
argv do sistema ficam sem teste (ADR-0022): o que ganha teste é a decisão que se
toma sobre a saída deles — e o que o `perf stat` carrega no `-e`, que é desenho
experimental (ADR-0006) e não argv de sistema.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from experiment_config import Instrumentation, MetricRecord
from masters_launch import IMAGE_TAG

# Os três modos de falha que a primeira rodada mostrou, e que pedem decisões
# opostas: o evento não existe na PMU, o contador abriu e nunca rodou, e o
# contador respondeu zero. Só o primeiro era conhecido.
NOT_SUPPORTED = "<not supported>"
NOT_COUNTED = "<not counted>"

PROBE_CONTENT = "preflight"
PROBE_PATH = "/tmp/preflight"

# Os mesmos do `encode/launch_container.sh`: o probe só prova o que roda pelo
# caminho da campanha, e um mount a menos aqui mede outro container.
SCRIPTS_MOUNT = "/opt/encode"
WORK_MOUNT = "/work"
MASTERS_DIR_NAME = "masters"

# Segundos de vídeo, não de encode: o rodízio da PMU gira milhares de vezes nesta
# janela, e é o que transforma um contador zerado de prova ausente em prova.
PROBE_SECONDS = 5

PERF_STDOUT = "/dev/stdout"

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

SELF_CHECK_STEPS = (Step.STS, Step.BUCKETS, Step.SSM, Step.GIT, Step.SYNC)


class Outcome(Enum):
    PASSED = "passou"
    FAILED = "falhou"
    SKIPPED = "não rodou"


@dataclass(frozen=True)
class StepResult:
    step: Step
    outcome: Outcome
    detail: str


def perf_probe_command(
    *,
    run: Mapping[str, Any],
    event_spec: str,
    repo_dir: str,
    work_dir: str,
) -> list[str]:
    """O `perf stat` sobre um encode curto de um Master real, pelo caminho da campanha.

    O `-o` é o stdout e o dump do `-vv` é o stderr: apontar o `-o` para um
    arquivo faria as duas saídas chegarem juntas a quem as guarda, e o
    `perf stat -j` deixaria de ser parseável linha a linha.
    """
    return [
        "sudo",
        "docker",
        "run",
        "--rm",
        "--cap-add=PERFMON",
        "-v",
        f"{repo_dir}/encode:{SCRIPTS_MOUNT}:ro",
        "-v",
        f"{work_dir}:{WORK_MOUNT}",
        IMAGE_TAG,
        "perf",
        "stat",
        "-vv",
        "-j",
        "-e",
        event_spec,
        "-o",
        PERF_STDOUT,
        "--",
        *probe_encode_argv(run),
    ]


def probe_encode_argv(run: Mapping[str, Any]) -> list[str]:
    """O encode do probe: o argv do Cenário que o objeto de run descreve, truncado."""
    argv = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-t",
        str(PROBE_SECONDS),
        "-i",
        f"{WORK_MOUNT}/{MASTERS_DIR_NAME}/{run['master']}",
        "-vf",
        f"scale={run['output_width']}:{run['output_height']}:flags={run['scale_flags']}",
        "-c:v",
        str(run["encoder"]),
        "-preset",
        str(run["preset"]),
        "-crf",
        str(run["crf"]),
        *(str(argument) for argument in run["encoder_args"]),
        "-g",
        str(run["gop_size"]),
        "-pix_fmt",
        str(run["pix_fmt"]),
        "-threads",
        str(run["threads"]),
    ]
    if run["strip_audio"]:
        argv.append("-an")
    # O muxer `null` descarta os pacotes depois de o encoder os produzir: o
    # trabalho que a PMU conta é o mesmo, sem gastar disco da instância.
    return [*argv, "-f", "null", "/dev/null"]


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


def perf_counters(raw: str, instrumentation: Instrumentation) -> dict[str, Counter]:
    """O que o `perf stat -j` mediu por evento, ou a recusa que nomeia o que não mediu.

    Cada fase relata **tudo** o que reprovou: parar no primeiro evento faria o
    pesquisador descobrir o segundo defeito daquela arquitetura na instância
    seguinte, que é o que uma execução do passo custa.
    """
    reported = _reported(raw)
    hardware = set(instrumentation.hardware_events)

    counted: dict[str, Counter] = {}
    refused: list[str] = []
    for event in instrumentation.pmu_events:
        try:
            counted[event] = _counter(reported, event, hardware=event in hardware)
        except PreflightError as error:
            refused.append(str(error))

    if refused:
        raise PreflightError(
            f"{len(refused)} de {len(instrumentation.pmu_events)} eventos sem medição dentro do "
            f"container: {'; '.join(refused)} "
            f"(o perf stat abriu: {', '.join(reported) or 'nada'})"
        )

    implausible = [
        message
        for metric in instrumentation.metrics
        if (message := _implausible(metric, counted)) is not None
    ]
    if implausible:
        raise PreflightError(f"os contadores responderam e não mediram: {'; '.join(implausible)}")
    return counted


def perf_detail(counted: Mapping[str, Counter]) -> str:
    """A linha da tabela do passo: cada evento com o valor **e** o `pcnt-running` dele."""
    return "dentro do container: " + ", ".join(
        f"{event} = {counter.value:.0f} ({counter.regime})" for event, counter in counted.items()
    )


@dataclass(frozen=True)
class Counter:
    """Um contador que abriu: o valor e a fração do tempo em que ele rodou (ADR-0006)."""

    value: float
    pcnt_running: float | None

    @property
    def regime(self) -> str:
        if self.pcnt_running is None:
            return "pcnt-running ausente"
        return f"pcnt-running {self.pcnt_running:.0f}%"


def _counter(reported: Mapping[str, _Reported], event: str, *, hardware: bool) -> Counter:
    found = reported.get(event)
    if found is None:
        raise PreflightError(f"{event}: nenhum contador com esse nome")

    value = found.value.strip()
    if value == NOT_SUPPORTED:
        raise PreflightError(
            f"{event}: {NOT_SUPPORTED} — o evento não existe na PMU desta arquitetura"
        )
    if value == NOT_COUNTED:
        raise PreflightError(
            f"{event}: {NOT_COUNTED} — o contador abriu e nunca rodou nesta arquitetura"
        )
    try:
        measured = float(value)
    except ValueError as error:
        raise PreflightError(f"{event}: counter-value não é um número: {value!r}") from error

    # Zero é medição legítima nos quatro eventos de software — `cpu-migrations = 0`
    # é o resultado desejável —, e é contador que não conta nos seis de hardware:
    # um encode não executa zero ciclo nem toca zero linha de cache.
    if hardware and measured == 0:
        raise PreflightError(
            f"{event}: zero — o contador de hardware respondeu e não contou nada neste guest"
        )
    return Counter(value=measured, pcnt_running=found.pcnt_running)


def _implausible(metric: MetricRecord, counted: Mapping[str, Counter]) -> str | None:
    """Coerência interna da razão, sem depender de arquitetura (ADR-0006)."""
    numerator = counted[metric.numerator].value
    denominator = counted[metric.denominator].value
    if not metric.exceeds(numerator, denominator):
        return None
    return (
        f"{metric.name}: {metric.numerator} = {numerator:.0f} passa de {metric.max_ratio:g} x "
        f"{metric.denominator} = {denominator:.0f}"
    )


def summarize(
    observed: Sequence[StepResult], steps: Sequence[Step] = STEPS
) -> tuple[StepResult, ...]:
    """A tabela inteira, na ordem declarada: o que não rodou aparece dizendo isso."""
    seen: dict[Step, StepResult] = {}
    for result in observed:
        if result.step in seen:
            raise PreflightError(f"{result.step.value}: passo registrado duas vezes")
        seen[result.step] = result

    return tuple(seen.get(step, StepResult(step, Outcome.SKIPPED, "")) for step in steps)


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


@dataclass(frozen=True)
class _Reported:
    value: str
    pcnt_running: float | None


def _reported(raw: str) -> dict[str, _Reported]:
    """O que a saída trouxe por evento, ignorando o cabeçalho que varia com a versão.

    A chave descarta o modificador que o `perf` ecoa (`cycles:u`): comparar o
    nome inteiro recusaria um contador que abriu.
    """
    reported: dict[str, _Reported] = {}
    for record in map(_json_object, raw.splitlines()):
        if record is None or "event" not in record or "counter-value" not in record:
            continue
        event = str(record["event"]).split(":")[0]
        reported.setdefault(
            event,
            _Reported(
                value=str(record["counter-value"]),
                pcnt_running=_optional_float(record.get("pcnt-running")),
            ),
        )
    return reported


def _optional_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _json_object(line: str) -> Mapping[str, object] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None
