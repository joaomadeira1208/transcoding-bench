from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLE_ROOT = Path(__file__).resolve().parent

RUN_SCENARIO = REPO_ROOT / "encode" / "run_scenario.sh"
GENERATE_SCENARIOS = REPO_ROOT / "orchestrator" / "generate_scenarios.py"
VALIDATE_META = REPO_ROOT / "analysis" / "validate_meta.py"
META_CHECK_DIR = REPO_ROOT / "orchestrator"
EXPERIMENT_TOML = REPO_ROOT / "config" / "experiment.toml"

COMMIT = "ffd4f43a1b2c3d4e5f60718293a4b5c6d7e8f900"
INSTANCE_ID = "i-0123456789abcdef0"
INSTANCE_TYPE = "c7g.xlarge"

VERSIONS = {
    "base_image": "ubuntu:24.04@sha256:33ceb719",
    "ffmpeg": "n8.1.2",
    "libx264": "b35605ace3ddf7c1a5d67a2eb553f034aef41d55",
    "libx265": "4.2",
    "libsvtav1": "v4.1.0",
    "libvmaf": "v3.1.0",
    "aws_cli": "2.36.38",
}

# Nunca um vídeo: como o `ffmpeg` é shimado ninguém decodifica isto, e gerá-lo de
# verdade custaria uma dependência de binário no CI.
MASTER_BYTES = bytes(range(256)) * 16


@dataclass(frozen=True)
class Execution:
    """O que uma invocação do `run_scenario.sh` deixou para trás."""

    run: dict[str, Any]
    returncode: int
    stdout: str
    stderr: str
    run_dir: Path
    argv_dir: Path

    def argv(self, tool: str) -> list[list[str]]:
        """O argv de cada invocação do shim `tool`, na ordem em que ocorreram."""
        raw = (self.argv_dir / f"{tool}.argv").read_text(encoding="utf-8")
        # Cada argumento é **terminado** por NUL, e não separado por ele: o
        # último campo do split é sempre vazio e não é um argumento.
        return [record.split("\0")[:-1] for record in raw.split("\n") if record]

    def meta(self) -> dict[str, Any]:
        return json.loads((self.run_dir / "meta.json").read_text(encoding="utf-8"))

    def encoder_pid(self) -> str:
        return (self.argv_dir / "ffmpeg.pid").read_text(encoding="utf-8").strip()


def _load_experiment() -> dict[str, Any]:
    with EXPERIMENT_TOML.open("rb") as handle:
        return tomllib.load(handle)


# A expectativa do argv sai daqui, e não do plano: comparar com o plano pularia o
# elo que se quer verificar. Constante, e não fixture, porque a matriz de
# parametrização dos testes sai dela.
EXPERIMENT = _load_experiment()


@pytest.fixture(scope="session")
def plan(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """O plano canônico, gerado invocando o CLI do orquestrador como caixa-preta."""
    out_dir = tmp_path_factory.mktemp("scenarios")
    subprocess.run(
        [
            sys.executable,
            str(GENERATE_SCENARIOS),
            "--config",
            str(EXPERIMENT_TOML),
            "--out",
            str(out_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads((out_dir / "canonical.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def shim_bin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Os shims instalados com o nome do binário que substituem, num dir de PATH."""
    bin_dir = tmp_path_factory.mktemp("bin")
    for shim in sorted((ROLE_ROOT / "shims").glob("*.sh")):
        installed = bin_dir / shim.stem
        shutil.copyfile(shim, installed)
        installed.chmod(0o755)
    return bin_dir


@pytest.fixture(scope="session")
def masters_dir(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> Path:
    """Um placeholder por Master que o plano nomeia."""
    masters = tmp_path_factory.mktemp("masters")
    for name in {run["master"] for block in plan["blocks"] for run in block["runs"]}:
        (masters / name).write_bytes(MASTER_BYTES)
    return masters


@pytest.fixture(scope="session")
def versions_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("image") / "versions.json"
    path.write_text(json.dumps(VERSIONS, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def execute(
    tmp_path_factory: pytest.TempPathFactory,
    shim_bin: Path,
    masters_dir: Path,
    versions_file: Path,
):
    """Roda o `run_scenario.sh` de verdade, com os shims; `shim_env` vira ambiente."""

    def _execute(run: dict[str, Any], **shim_env: str) -> Execution:
        workdir = tmp_path_factory.mktemp("execution")
        argv_dir = workdir / "argv"
        argv_dir.mkdir()
        runs_dir = workdir / "runs"

        env = {
            **os.environ,
            "PATH": f"{shim_bin}{os.pathsep}{os.environ['PATH']}",
            "TIME_BIN": str(shim_bin / "time"),
            "SMOKE_ARGV_DIR": str(argv_dir),
            **shim_env,
        }
        result = subprocess.run(
            [
                "bash",
                str(RUN_SCENARIO),
                "--run",
                json.dumps(run),
                "--masters-dir",
                str(masters_dir),
                "--runs-dir",
                str(runs_dir),
                "--commit",
                COMMIT,
                "--instance-id",
                INSTANCE_ID,
                "--instance-type",
                INSTANCE_TYPE,
                "--versions-file",
                str(versions_file),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        # O `run_scenario.sh` anuncia o diretório do run antes de qualquer coisa
        # poder falhar; se nem isso saiu, o que interessa ao diagnóstico é o
        # stderr dele, não um IndexError aqui.
        assert result.stdout.splitlines(), result.stderr
        return Execution(
            run=run,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            run_dir=Path(result.stdout.splitlines()[0]),
            argv_dir=argv_dir,
        )

    return _execute


def check_with_stdlib_checker(meta_path: Path) -> subprocess.CompletedProcess[str]:
    """Roda o `meta.json` contra o checador stdlib do orquestrador, sem importar.

    Ele é módulo e não CLI, então o subprocesso é o que mantém o `sys.path` de
    outro papel fora do processo do smoke (ADR-0022).
    """
    program = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from meta_check import check_meta;"
        "check_meta(open(sys.argv[2], 'rb').read())"
    )
    return subprocess.run(
        [sys.executable, "-c", program, str(META_CHECK_DIR), str(meta_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def validate_with_cli(meta_path: Path) -> subprocess.CompletedProcess[str]:
    """A CLI de validação do `analysis/`, invocada como caixa-preta."""
    return subprocess.run(
        [sys.executable, str(VALIDATE_META), str(meta_path)],
        capture_output=True,
        text=True,
        check=False,
    )
