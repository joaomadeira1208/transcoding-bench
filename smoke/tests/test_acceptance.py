# A camada de aceite (ADR-0022): a imagem buildada aqui, e dentro dela o FFmpeg, o
# `/usr/bin/time`, o `pidstat` e o `perf` de verdade em volta do argv que o plano
# gerou. Fica fora do CI e desmarcada por padrão — `pytest smoke/ --docker`.

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from conftest import EXPERIMENT, REPO_ROOT, ROLE_ROOT, Execution

pytestmark = pytest.mark.docker

ACCEPTANCE = ROLE_ROOT / "acceptance.sh"
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"
IMAGE_TAG = "transcoding-bench:acceptance"

# O default do `TIME_BIN` do `run_scenario.sh`, que o shim substituiu na camada
# de baixo e que aqui volta a ser o binário.
TIME_BIN = "/usr/bin/time"

CONTAINER_MASTERS = "/work/masters"
CONTAINER_OUT = "/work/out"

# 5 s a 24 fps: curto o bastante para o ciclo caber em minutos, longo o bastante
# para o `pidstat` a 1 Hz deixar mais de uma amostra.
CLIP_SECONDS = 5
CLIP_FPS = 24

# O que vira fixture. O `output.sha256` fica de fora: é digest, não texto a
# parsear.
CAPTURED = ("time.json", "perf.json", "pidstat.txt", "ffmpeg.log")

ENCODERS = [codec["encoder"] for codec in EXPERIMENT["codec"]]

# Um par sem downscale, e o menor que exercite o caminho inteiro: a geometria não
# é o que se verifica aqui, e um 2160p faria o aceite custar dezenas de minutos
# por codec.
TIER = "1080p"

SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
FRAMES = re.compile(r"frame=\s*([0-9]+)")


@dataclass(frozen=True)
class Capture:
    """As saídas cruas que as ferramentas de verdade deixaram, trazidas ao host."""

    run: dict[str, Any]
    returncode: int
    stderr: str
    out_dir: Path

    def artifact(self, name: str) -> str:
        # `errors="replace"`: o `ffmpeg.log` é stderr de terceiros, e um byte
        # inválido nele não é motivo para a asserção não acontecer.
        return (self.out_dir / name).read_text(encoding="utf-8", errors="replace")

    def wrote(self, name: str) -> bool:
        return (self.out_dir / name).is_file()


def instrumentation_chain(execution: Execution, clip: str, output: str) -> list[str]:
    """A cadeia inteira que o `run_scenario.sh` montou, com os caminhos do host
    trocados pelos do container.

    Sai do rastro dos shims e não é escrita aqui: o `--quiet` do `time`, o `-j` do
    `perf` e a ordem dos wrappers são o que o aceite existe para exercitar, e
    transcrevê-los faria a captura concordar com este arquivo enquanto divergia
    do script que a campanha roda.
    """
    chain = [TIME_BIN, *execution.argv("time")[0]]
    for index, argument in enumerate(chain):
        if argument == "-o":
            chain[index + 1] = f"{CONTAINER_OUT}/{Path(chain[index + 1]).name}"
        elif argument == "-i":
            chain[index + 1] = clip
    chain[-1] = output
    return chain


def pidstat_arguments(execution: Execution) -> tuple[str, str]:
    """Os flags e o intervalo com que o `run_scenario.sh` chamou o `pidstat`. O
    `-p` fica de fora: o PID é outro dentro do container."""
    argv = execution.argv("pidstat")[0]
    return " ".join(argv[: argv.index("-p")]), argv[-1]


def master_geometry(run: dict[str, Any]) -> dict[str, int]:
    """A geometria do tier de entrada daquele vídeo: o clip faz as vezes do
    Master, e escalar a partir de outra coisa mudaria o trabalho do encode."""
    video = next(video for video in EXPERIMENT["video"] if video["slug"] == run["video"])
    return video["geometry"][run["input_res"]]


@pytest.fixture(scope="session")
def image() -> str:
    subprocess.run(
        ["docker", "build", "--file", str(DOCKERFILE), "--tag", IMAGE_TAG, str(DOCKERFILE.parent)],
        check=True,
    )
    return IMAGE_TAG


@pytest.fixture(scope="session")
def capture(tmp_path_factory: pytest.TempPathFactory, image: str, execute):
    """Roda a cadeia de um run dentro da imagem e traz as saídas cruas ao host."""

    def _capture(run: dict[str, Any]) -> Capture:
        execution = execute(run)
        pidstat_flags, pidstat_interval = pidstat_arguments(execution)
        geometry = master_geometry(run)
        clip = f"{CONTAINER_MASTERS}/{run['master']}"
        output = f"{CONTAINER_OUT}/output.{run['container']}"
        out_dir = tmp_path_factory.mktemp("capture")
        container = f"acceptance-{uuid4().hex[:12]}"

        try:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--name",
                    container,
                    # Sem a capability o `perf_event_open` é recusado, e a cadeia
                    # inteira sai não-zero antes de o encode começar.
                    "--cap-add=PERFMON",
                    "--interactive",
                    image,
                    # O script chega pelo stdin: um bind-mount dependeria de o
                    # diretório do repositório estar entre os que a VM do Docker
                    # compartilha, que varia de máquina para máquina.
                    "bash",
                    "-s",
                    "--",
                    "--out-dir",
                    CONTAINER_OUT,
                    "--clip",
                    clip,
                    "--clip-size",
                    f"{geometry['width']}x{geometry['height']}",
                    "--clip-seconds",
                    str(CLIP_SECONDS),
                    "--clip-fps",
                    str(CLIP_FPS),
                    "--pidstat-flags",
                    pidstat_flags,
                    "--pidstat-interval",
                    pidstat_interval,
                    "--muxer",
                    run["bitstream_muxer"],
                    "--",
                    *instrumentation_chain(execution, clip, output),
                ],
                input=ACCEPTANCE.read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(
                ["docker", "cp", f"{container}:{CONTAINER_OUT}/.", str(out_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            subprocess.run(["docker", "rm", "--force", container], capture_output=True, check=False)

        return Capture(run=run, returncode=result.returncode, stderr=result.stderr, out_dir=out_dir)

    return _capture


@pytest.fixture(scope="session")
def acceptance_runs(plan) -> dict[str, dict[str, Any]]:
    """Uma Replicação por encoder, escolhida pela `scenario_id` mínima entre as do
    tier — nunca pela ordem do plano, que é o embaralhamento com a seed."""
    selected: dict[str, dict[str, Any]] = {}
    for block in plan["blocks"]:
        if block["input_res"] != TIER or block["output_res"] != TIER:
            continue
        for run in block["runs"]:
            if run["warmup"]:
                continue
            current = selected.get(run["encoder"])
            if current is None or run["scenario_id"] < current["scenario_id"]:
                selected[run["encoder"]] = run
    return selected


@pytest.fixture(scope="session", params=ENCODERS)
def captured(request, acceptance_runs, capture, capture_dir) -> Capture:
    """A captura de um encoder, e — quando se pediu — a sua cópia como fixture."""
    encoder = request.param
    result = capture(acceptance_runs[encoder])
    if capture_dir is not None:
        for artifact in CAPTURED:
            if result.wrote(artifact):
                shutil.copyfile(result.out_dir / artifact, capture_dir / f"{encoder}.{artifact}")
    return result


def output_path(captured: Capture) -> Path:
    return captured.out_dir / f"output.{captured.run['container']}"


class TestEncode:
    def test_the_real_ffmpeg_accepted_the_argv_the_plan_generates(self, captured):
        # O que o shim não pode dizer: que o encoder aceita o preset, o CRF e os
        # `encoder_args` que o `config/experiment.toml` declara para ele.
        assert captured.returncode == 0, captured.stderr

    def test_the_encode_wrote_the_output_the_argv_named(self, captured):
        assert output_path(captured).stat().st_size > 0

    def test_the_stats_line_counts_every_frame_of_the_clip(self, captured):
        # Um encode que parasse cedo apareceria aqui como um total menor.
        log = captured.artifact("ffmpeg.log")
        frames = [int(match.group(1)) for match in FRAMES.finditer(log)]

        assert frames, log
        assert max(frames) == CLIP_SECONDS * CLIP_FPS


class TestInstrumentation:
    def test_the_gnu_time_wrote_json_with_the_format_string(self, captured):
        # Sem o `--quiet` da invocação, o GNU time prefixa "Command exited with
        # non-zero status" e o `time.json` de todo run falho deixa de ser JSON.
        time = json.loads(captured.artifact("time.json"))

        assert time["exit_status"] == 0
        assert all(isinstance(value, int | float) for value in time.values())

    def test_the_perf_echoes_back_every_event_the_spec_declares(self, captured):
        # `perf stat` recusa um nome de evento que não conhece, então isto é o que
        # verifica a lista do `config/experiment.toml` sem PMU. Se cada evento
        # retorna valor é outra pergunta, e é do smoke AWS.
        events = [
            json.loads(line)["event"]
            for line in captured.artifact("perf.json").splitlines()
            if "counter-value" in line
        ]

        assert events == EXPERIMENT["instrumentation"]["pmu_events"]

    def test_the_pidstat_sampled_the_encoder(self, captured):
        # O cabeçalho vem do `-h`, e é dele que o parser tira a posição do `%CPU`.
        lines = captured.artifact("pidstat.txt").splitlines()
        header = next(line for line in lines if line.startswith("#"))
        samples = [line for line in lines if line.endswith("ffmpeg")]

        assert "%CPU" in header.split()
        assert samples


class TestBitstream:
    def test_the_extraction_produced_a_digest(self, captured):
        # Um muxer que o FFmpeg não aceitasse deixaria o run sem `output.sha256`.
        assert SHA256.match(captured.artifact("output.sha256").strip())

    def test_the_digest_is_the_bitstream_and_not_the_container(self, captured):
        digest = captured.artifact("output.sha256").strip()

        assert digest != hashlib.sha256(output_path(captured).read_bytes()).hexdigest()
