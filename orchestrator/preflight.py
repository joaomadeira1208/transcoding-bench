"""O núcleo puro do `preflight`: os vereditos sobre o `perf` e o `libvmaf`, e a tabela.

As funções recebem dado já buscado e devolvem dado — quem lança a instância é o
`instance_launch.py`, e quem abre o SSH e lista o bucket é o `orchestrator.py`,
sobre o `external.py`. Os `docker run` daqui são argv, e como o resto do
argv do sistema ficam sem teste (ADR-0022): o que ganha teste é a decisão que se
toma sobre a saída deles — e o que o `perf stat` carrega no `-e`, que é desenho
experimental (ADR-0006) e não argv de sistema.
"""

from __future__ import annotations

import json
import math
import shlex
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

# Os mesmos dos `launch_container.sh` de cada papel: o probe só prova o que roda
# pelo caminho da campanha, e um mount a menos aqui mede outro container.
SCRIPTS_MOUNT = "/opt/encode"
JUDGE_SCRIPTS_MOUNT = "/opt/judge"
WORK_MOUNT = "/work"
MASTERS_DIR_NAME = "masters"

# Segundos de vídeo, não de encode: o rodízio da PMU gira milhares de vezes nesta
# janela, e é o que transforma um contador zerado de prova ausente em prova.
PROBE_SECONDS = 5

PERF_STDOUT = "/dev/stdout"

VMAF_PROBE_LOG = "/tmp/vmaf.json"

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
    VMAF = "vmaf"
    PUT = "s3-put"
    TERMINATE = "terminate"


SELF_CHECK_STEPS = (Step.STS, Step.BUCKETS, Step.SSM, Step.GIT, Step.SYNC)

ENCODE_STEPS = (
    *SELF_CHECK_STEPS,
    Step.AMI,
    Step.LAUNCH,
    Step.BOOTSTRAP,
    Step.PERF,
    Step.PUT,
    Step.TERMINATE,
)

JUDGE_STEPS = (
    *SELF_CHECK_STEPS,
    Step.AMI,
    Step.LAUNCH,
    Step.BOOTSTRAP,
    Step.VMAF,
    Step.PUT,
    Step.TERMINATE,
)


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


def vmaf_probe_command(
    *,
    master: Mapping[str, Any],
    scale_flags: str,
    vmaf_model: str,
    repo_dir: str,
    work_dir: str,
) -> list[str]:
    """O `libvmaf` sobre segundos de um Master contra ele mesmo, pelo caminho do Juiz.

    O filtro é o do `judge/run_quality.sh`: a referência escalada para a geometria
    do output, que aqui é a do próprio Master. O log sai pelo stdout e o stderr do
    FFmpeg pelo stderr, com o status do FFmpeg — e o log vai mesmo quando ele
    falha, porque o log parcial é a evidência do passo que reprovou.
    """
    source = f"{WORK_MOUNT}/{MASTERS_DIR_NAME}/{master['name']}"
    clip = ["-t", str(PROBE_SECONDS), "-i", source]
    filtergraph = (
        f"[1:v]scale={master['width']}:{master['height']}:flags={scale_flags}[ref];"
        f"[0:v][ref]libvmaf=model=version={vmaf_model}:feature=name=float_ssim"
        f":log_fmt=json:log_path={VMAF_PROBE_LOG}"
    )
    ffmpeg = ["ffmpeg", "-nostdin", "-y", *clip, *clip, "-filter_complex", filtergraph]
    script = (
        f"{shlex.join([*ffmpeg, '-f', 'null', '-'])} >/dev/null; status=$?; "
        f"cat {VMAF_PROBE_LOG} 2>/dev/null; exit $status"
    )
    return [
        "sudo",
        "docker",
        "run",
        "--rm",
        "-v",
        f"{repo_dir}/judge:{JUDGE_SCRIPTS_MOUNT}:ro",
        "-v",
        f"{work_dir}:{WORK_MOUNT}",
        IMAGE_TAG,
        "bash",
        "-c",
        script,
    ]


def put_command(*, bucket: str, key: str) -> list[str]:
    """O `s3 cp` do papel, pelo caminho real: de dentro do container."""
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
class VmafScore:
    """O que o `libvmaf` do container comparou e o VMAF médio que ele deu."""

    frames: int
    vmaf: float


def vmaf_score(raw: str) -> VmafScore:
    """O log JSON do `libvmaf`, ou a recusa que nomeia por que ele não prova nada."""
    if not raw.strip():
        raise PreflightError("log do libvmaf ausente: o container não deixou log nenhum")
    try:
        log = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PreflightError(f"o log do libvmaf não é JSON válido: {error}") from error
    if not isinstance(log, Mapping):
        raise PreflightError(f"o log do libvmaf não é um objeto JSON: {type(log).__name__}")

    frames = log.get("frames")
    if not isinstance(frames, list) or not frames:
        raise PreflightError(
            f"frames: o libvmaf não comparou frame nenhum (veio {_abridged(frames)})"
        )

    pooled = log.get("pooled_metrics")
    vmaf = pooled.get("vmaf") if isinstance(pooled, Mapping) else None
    mean = vmaf.get("mean") if isinstance(vmaf, Mapping) else None
    if type(mean) not in (int, float) or not math.isfinite(mean):
        raise PreflightError(
            f"VMAF: pooled_metrics.vmaf.mean não é numérico, veio {_abridged(mean)}"
        )
    return VmafScore(frames=len(frames), vmaf=float(mean))


def vmaf_detail(score: VmafScore) -> str:
    return f"dentro do container: {score.frames} frames, VMAF médio {score.vmaf:.2f}"


def _abridged(value: object) -> str:
    return repr(value) if not isinstance(value, list) else f"lista de {len(value)}"


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
    observed: Sequence[StepResult], steps: Sequence[Step] = ENCODE_STEPS
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
