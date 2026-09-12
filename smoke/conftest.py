from __future__ import annotations

import hashlib
import json
import os
import re
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
RUN_ALL = REPO_ROOT / "encode" / "run_all.sh"
FETCH_MASTERS = REPO_ROOT / "encode" / "fetch_masters.sh"
PREPARE_MASTERS = REPO_ROOT / "masters" / "prepare.sh"
GENERATE_SCENARIOS = REPO_ROOT / "orchestrator" / "generate_scenarios.py"
GENERATE_MASTERS_PLAN = REPO_ROOT / "orchestrator" / "generate_masters_plan.py"
VALIDATE_META = REPO_ROOT / "analysis" / "validate_meta.py"
VALIDATE_MANIFEST = REPO_ROOT / "orchestrator" / "validate_manifest.py"
CONSOLIDATE = REPO_ROOT / "analysis" / "consolidate.py"
META_CHECK_DIR = REPO_ROOT / "orchestrator"
EXPERIMENT_TOML = REPO_ROOT / "config" / "experiment.toml"
PILOT_TOML = REPO_ROOT / "config" / "pilot.toml"

COMMIT = "ffd4f43a1b2c3d4e5f60718293a4b5c6d7e8f900"
INSTANCE_ID = "i-0123456789abcdef0"
INSTANCE_TYPE = "c7g.xlarge"
BUCKET = "smoke-bucket"
MASTERS_PREFIX = "masters/"
MANIFEST_SCHEMA_VERSION = "1"

VERSIONS = {
    "base_image": "ubuntu:24.04@sha256:33ceb719",
    "ffmpeg": "n8.1.2",
    "libx264": "b35605ace3ddf7c1a5d67a2eb553f034aef41d55",
    "libx265": "4.2",
    "libsvtav1": "v4.1.0",
    "libvmaf": "v3.1.0",
    "aws_cli": "2.36.38",
}

# O que faz as vezes de um Master no disco e de um source baixado. Nunca um
# vídeo: como o `ffmpeg` é shimado ninguém decodifica isto, e gerá-lo de
# verdade custaria uma dependência de binário no CI.
PLACEHOLDER_BYTES = bytes(range(256)) * 16


def master_bytes(name: str) -> bytes:
    """O placeholder daquele Master.

    O nome entra no conteúdo: com seis placeholders idênticos, um `s3 cp` que
    trouxesse o Master errado casaria o sha256 do manifesto assim mesmo.
    """
    return PLACEHOLDER_BYTES + name.encode("utf-8")


@dataclass(frozen=True)
class ShimTrail:
    """O que os shims registraram durante uma invocação: argv por ferramenta e a
    ordem em que todas foram chamadas."""

    argv_dir: Path
    s3_root: Path

    def argv(self, tool: str) -> list[list[str]]:
        """O argv de cada invocação do shim `tool`, na ordem em que ocorreram."""
        raw = (self.argv_dir / f"{tool}.argv").read_text(encoding="utf-8")
        # Cada argumento é **terminado** por NUL, e não separado por ele: o
        # último campo do split é sempre vazio e não é um argumento.
        return [record.split("\0")[:-1] for record in raw.split("\n") if record]

    def sequence(self) -> list[str]:
        """O nome de cada shim invocado, na ordem, atravessando ferramentas."""
        return (self.argv_dir / "sequence").read_text(encoding="utf-8").split()

    def encoder_pid(self) -> str:
        return (self.argv_dir / "ffmpeg.pid").read_text(encoding="utf-8").strip()

    def bucket_dir(self) -> Path:
        return self.s3_root / BUCKET

    def object_versions(self, key: str) -> list[bytes]:
        """Cada versão daquele objeto, na ordem em que o shim as recebeu."""
        versions = self.argv_dir / "versions" / key
        if not versions.is_dir():
            return []
        return [path.read_bytes() for path in sorted(versions.iterdir())]

    def uploaded(self, run_dir: Path) -> Path:
        """Onde o shim do `aws` deixou a cópia de `runs/{run_id}/`."""
        return self.bucket_dir() / "runs" / run_dir.name


@dataclass(frozen=True)
class Execution(ShimTrail):
    """O que uma invocação do `run_scenario.sh` deixou para trás."""

    run: dict[str, Any]
    returncode: int
    stdout: str
    stderr: str
    run_dir: Path

    def meta(self) -> dict[str, Any]:
        return json.loads((self.run_dir / "meta.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Preparation(ShimTrail):
    """O que uma invocação do `masters/prepare.sh` deixou para trás."""

    plan: dict[str, Any]
    returncode: int
    stdout: str
    stderr: str
    work_dir: Path

    def manifest_path(self) -> Path:
        return self.work_dir / "manifest.json"

    def manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path().read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Loop(ShimTrail):
    """O que uma invocação do `run_all.sh` deixou para trás."""

    plan: dict[str, Any]
    returncode: int
    stdout: str
    stderr: str
    runs_dir: Path

    def run_dirs(self) -> list[Path]:
        return sorted(path for path in self.runs_dir.iterdir() if path.is_dir())

    def metas(self) -> dict[str, dict[str, Any]]:
        """`meta.json` por `run_id`."""
        return {
            path.name: json.loads((path / "meta.json").read_text(encoding="utf-8"))
            for path in self.run_dirs()
        }

    def encoded_run_ids(self) -> list[str]:
        """Os `run_id` na ordem em que o shim do `ffmpeg` recebeu cada encode."""
        return [Path(argv[-1]).parent.name for argv in self.argv("ffmpeg") if argv[-1] != "-"]


@dataclass(frozen=True)
class Fetch(ShimTrail):
    """O que uma invocação do `fetch_masters.sh` deixou para trás."""

    manifest: dict[str, Any]
    manifest_path: Path
    returncode: int
    stdout: str
    stderr: str
    dest: Path

    def downloaded(self) -> set[str]:
        return {path.name for path in self.dest.iterdir()}


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


# A expectativa do argv sai daqui, e não do plano: comparar com o plano pularia o
# elo que se quer verificar. Constantes, e não fixtures, porque a matriz de
# parametrização dos testes sai delas.
EXPERIMENT = _load_config(EXPERIMENT_TOML)
PILOT = _load_config(PILOT_TOML)


def _generate_plan(config: Path, out_dir: Path) -> dict[str, Any]:
    """O canônico de uma definição, gerado invocando o CLI do orquestrador."""
    subprocess.run(
        [
            sys.executable,
            str(GENERATE_SCENARIOS),
            "--config",
            str(config),
            "--out",
            str(out_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads((out_dir / "canonical.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def plan(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """O plano canônico da campanha, pelo CLI como caixa-preta."""
    return _generate_plan(EXPERIMENT_TOML, tmp_path_factory.mktemp("scenarios"))


@pytest.fixture(scope="session")
def pilot_plan(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """O plano canônico do piloto, pelo mesmo CLI e do mesmo jeito."""
    return _generate_plan(PILOT_TOML, tmp_path_factory.mktemp("pilot-scenarios"))


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
def masters_dir(
    tmp_path_factory: pytest.TempPathFactory,
    plan: dict[str, Any],
    pilot_plan: dict[str, Any],
) -> Path:
    """Um placeholder por Master que a união dos dois planos nomeia.

    A união e não só os da campanha: um `pilot.toml` que amanhã declare um vídeo
    a mais tem de quebrar o smoke por asserção, não por Master ausente do disco.
    """
    masters = tmp_path_factory.mktemp("masters")
    named = {
        run["master"]
        for definition in (plan, pilot_plan)
        for block in definition["blocks"]
        for run in block["runs"]
    }
    for name in named:
        (masters / name).write_bytes(PLACEHOLDER_BYTES)
    return masters


def _generate_masters_plan() -> dict[str, Any]:
    """O plano dos Masters da campanha, pelo CLI do orquestrador como caixa-preta."""
    result = subprocess.run(
        [sys.executable, str(GENERATE_MASTERS_PLAN), "--config", str(EXPERIMENT_TOML)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="session")
def masters_manifest() -> dict[str, Any]:
    """O manifesto que a preparação escreveria, montado aqui sobre os placeholders.

    O plano sai do CLI, e não de uma lista transcrita: o que o bash tira do
    manifesto são o nome e o sha256 de cada objeto, e os nomes têm de ser os que
    a preparação materializa.
    """
    plan = _generate_masters_plan()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "versions": VERSIONS,
        "sources": {video["video"]: video["source"] for video in plan["videos"]},
        "masters": [
            {
                **master,
                "size": len(master_bytes(master["name"])),
                "sha256": hashlib.sha256(master_bytes(master["name"])).hexdigest(),
            }
            for video in plan["videos"]
            for master in (video["master"], *video["derived"])
        ],
    }


@pytest.fixture(scope="session")
def versions_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("image") / "versions.json"
    path.write_text(json.dumps(VERSIONS, indent=2) + "\n", encoding="utf-8")
    return path


def shim_environment(shim_bin: Path, workdir: Path, shim_env: dict[str, str]) -> dict[str, str]:
    """O ambiente em que os shims interceptam: PATH, `TIME_BIN`, onde registram o
    argv e onde fica o bucket falso. Os dois diretórios são criados aqui."""
    (workdir / "argv").mkdir()
    (workdir / "s3").mkdir()
    return {
        **os.environ,
        "PATH": f"{shim_bin}{os.pathsep}{os.environ['PATH']}",
        "TIME_BIN": str(shim_bin / "time"),
        "SMOKE_ARGV_DIR": str(workdir / "argv"),
        "SMOKE_S3_ROOT": str(workdir / "s3"),
        **shim_env,
    }


def bootstrap_arguments(masters_dir: Path, runs_dir: Path, versions_file: Path) -> list[str]:
    """Os argumentos que os dois scripts recebem do host: nada é descoberto."""
    return [
        "--masters-dir",
        str(masters_dir),
        "--runs-dir",
        str(runs_dir),
        "--bucket",
        BUCKET,
        "--commit",
        COMMIT,
        "--instance-id",
        INSTANCE_ID,
        "--instance-type",
        INSTANCE_TYPE,
        "--versions-file",
        str(versions_file),
    ]


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
        env = shim_environment(shim_bin, workdir, shim_env)
        result = subprocess.run(
            [
                "bash",
                str(RUN_SCENARIO),
                "--run",
                json.dumps(run),
                *bootstrap_arguments(masters_dir, workdir / "runs", versions_file),
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
            argv_dir=Path(env["SMOKE_ARGV_DIR"]),
            s3_root=Path(env["SMOKE_S3_ROOT"]),
        )

    return _execute


# Um encode do shim leva ~0,5 s; um laço que passe disto está preso, e o que se
# quer ver então é a falha, não um `pytest` pendurado numa hora de `sleep`.
LOOP_DEADLINE_S = 120


@pytest.fixture(scope="session")
def run_all(
    tmp_path_factory: pytest.TempPathFactory,
    shim_bin: Path,
    masters_dir: Path,
    versions_file: Path,
):
    """Roda o `run_all.sh` de verdade sobre os `blocks` dados do `plan` dado.

    O restante da fatia é o topo do canônico de onde os blocos saíram — campanha
    ou piloto: a fatia que a Instância recebe tem a forma daquele plano, só com
    menos blocos, e é essa forma que o laço tem de atravessar.
    """

    def _run_all(
        plan: dict[str, Any],
        blocks: list[dict[str, Any]],
        *flags: str,
        **shim_env: str,
    ) -> Loop:
        workdir = tmp_path_factory.mktemp("loop")
        env = shim_environment(shim_bin, workdir, shim_env)
        plan_slice = {**plan, "blocks": blocks}
        plan_path = workdir / "slice.json"
        plan_path.write_text(json.dumps(plan_slice), encoding="utf-8")
        runs_dir = workdir / "runs"
        result = subprocess.run(
            [
                "bash",
                str(RUN_ALL),
                "--plan",
                str(plan_path),
                *bootstrap_arguments(masters_dir, runs_dir, versions_file),
                *flags,
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=LOOP_DEADLINE_S,
        )
        return Loop(
            plan=plan_slice,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            runs_dir=runs_dir,
            argv_dir=Path(env["SMOKE_ARGV_DIR"]),
            s3_root=Path(env["SMOKE_S3_ROOT"]),
        )

    return _run_all


@pytest.fixture(scope="session")
def fetch_masters(
    tmp_path_factory: pytest.TempPathFactory,
    shim_bin: Path,
    masters_manifest: dict[str, Any],
):
    """Roda o `fetch_masters.sh` de verdade sobre um bucket falso semeado com um
    placeholder por Master do manifesto; `corrupt` troca um byte no objeto
    daquele Master, do lado do bucket."""

    def _fetch_masters(*, corrupt: str | None = None, **shim_env: str) -> Fetch:
        workdir = tmp_path_factory.mktemp("fetch")
        env = shim_environment(shim_bin, workdir, shim_env)
        prefix_dir = Path(env["SMOKE_S3_ROOT"]) / BUCKET / MASTERS_PREFIX
        prefix_dir.mkdir(parents=True)
        for master in masters_manifest["masters"]:
            payload = bytearray(master_bytes(master["name"]))
            if master["name"] == corrupt:
                payload[0] ^= 0xFF
            (prefix_dir / master["name"]).write_bytes(payload)

        manifest_path = workdir / "manifest.json"
        manifest_path.write_text(json.dumps(masters_manifest), encoding="utf-8")
        dest = workdir / "masters"
        result = subprocess.run(
            [
                "bash",
                str(FETCH_MASTERS),
                "--manifest",
                str(manifest_path),
                "--bucket",
                BUCKET,
                "--prefix",
                MASTERS_PREFIX,
                "--dest",
                str(dest),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return Fetch(
            manifest=masters_manifest,
            manifest_path=manifest_path,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            dest=dest,
            argv_dir=Path(env["SMOKE_ARGV_DIR"]),
            s3_root=Path(env["SMOKE_S3_ROOT"]),
        )

    return _fetch_masters


@pytest.fixture(scope="session")
def block(plan: dict[str, Any]) -> dict[str, Any]:
    return plan["blocks"][0]


@pytest.fixture(scope="session")
def loop(plan: dict[str, Any], block: dict[str, Any], run_all) -> Loop:
    """A árvore de um bloco: o warm-up mais as cinco Replicações."""
    return run_all(plan, [block])


SOURCE_SHA256 = hashlib.sha256(PLACEHOLDER_BYTES).hexdigest()
SOURCE_SIZE = len(PLACEHOLDER_BYTES)


@pytest.fixture(scope="session")
def masters_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """O arquivo que o shim do `curl` entrega no lugar dos GB de cada fonte."""
    path = tmp_path_factory.mktemp("source") / "source.bin"
    path.write_bytes(PLACEHOLDER_BYTES)
    return path


def _substitute_once_per_video(text: str, pattern: str, replacement: str) -> str:
    """Uma linha por `[video.source]`, e a contagem é a asserção.

    Hoje `sha256` e `size` só aparecem lá. Um campo homônimo em outro registro
    — um sha256 por Master, digamos — passaria a ser reescrito junto, e o
    manifesto seria conferido contra um TOML adulterado sem que nada avisasse.
    """
    patched, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    assert count == len(EXPERIMENT["video"]), pattern
    return patched


@pytest.fixture(scope="session")
def masters_toml(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """O `config/experiment.toml` com o sha256 e o tamanho do placeholder.

    É o único fato do arquivo que um download shimado não tem como honrar, e
    trocá-lo aqui é o que mantém o resto — as fontes, a geometria de cada tier, a
    cadência, os frames — sendo o do repositório: é contra este TOML que a CLI do
    checker confere o manifesto que o bash escreveu.
    """
    text = _substitute_once_per_video(
        EXPERIMENT_TOML.read_text(encoding="utf-8"),
        r'^sha256 = ".*"$',
        f'sha256 = "{SOURCE_SHA256}"',
    )
    text = _substitute_once_per_video(text, r"^size = \d+$", f"size = {SOURCE_SIZE}")
    path = tmp_path_factory.mktemp("masters-config") / "experiment.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def masters_plan(tmp_path_factory: pytest.TempPathFactory, masters_toml: Path) -> dict[str, Any]:
    """O plano dos Masters, gerado pelo CLI do orquestrador como caixa-preta."""
    out = tmp_path_factory.mktemp("masters-plan") / "masters.json"
    subprocess.run(
        [
            sys.executable,
            str(GENERATE_MASTERS_PLAN),
            "--config",
            str(masters_toml),
            "--out",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def masters_of(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Os Masters do plano: por vídeo, o 4K e os seus derivados.

    O `derived` sai com default porque um dos casos é o plano que não o traz, e
    quem tem que recusá-lo é o `prepare.sh`. Sem o default, o harness estoura
    montando as respostas do `ffprobe` e o script nem chega a ser invocado.
    """
    return [
        master
        for video in plan["videos"]
        for master in (video["master"], *video.get("derived", ()))
    ]


def probe_response(master: dict[str, Any]) -> dict[str, Any]:
    """O que o `ffprobe` emitiria sobre um Master que saiu como o plano pediu."""
    return {
        "streams": [
            {
                "codec_name": master["codec_name"],
                "width": master["width"],
                "height": master["height"],
                "pix_fmt": master["pix_fmt"],
                "r_frame_rate": master["frame_rate"],
                "nb_read_packets": str(master["frames"]),
            }
        ]
    }


@pytest.fixture(scope="session")
def prepare(
    tmp_path_factory: pytest.TempPathFactory,
    shim_bin: Path,
    masters_source: Path,
    versions_file: Path,
):
    """Roda o `masters/prepare.sh` de verdade, com os shims.

    `probes` substitui a resposta do `ffprobe` de um Master pela que o caso
    precisa; sem ele, cada um dos seis é inspecionado com sucesso.
    """

    def _prepare(
        plan: dict[str, Any],
        probes: dict[str, dict[str, Any]] | None = None,
        **shim_env: str,
    ) -> Preparation:
        workdir = tmp_path_factory.mktemp("preparation")
        probe_dir = workdir / "probes"
        probe_dir.mkdir()
        responses = {master["name"]: probe_response(master) for master in masters_of(plan)}
        responses.update(probes or {})
        for name, response in responses.items():
            (probe_dir / f"{name}.json").write_text(json.dumps(response), encoding="utf-8")

        env = shim_environment(
            shim_bin,
            workdir,
            {
                "SMOKE_SOURCE_FILE": str(masters_source),
                "SMOKE_PROBE_DIR": str(probe_dir),
                **shim_env,
            },
        )
        work_dir = workdir / "work"
        result = subprocess.run(
            [
                "bash",
                str(PREPARE_MASTERS),
                "--plan",
                json.dumps(plan),
                "--bucket",
                BUCKET,
                "--work-dir",
                str(work_dir),
                "--versions-file",
                str(versions_file),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return Preparation(
            plan=plan,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            work_dir=work_dir,
            argv_dir=Path(env["SMOKE_ARGV_DIR"]),
            s3_root=Path(env["SMOKE_S3_ROOT"]),
        )

    return _prepare


@pytest.fixture(scope="session")
def list_objects(tmp_path_factory: pytest.TempPathFactory, shim_bin: Path):
    """O lado leitor do layout de prefixos: `s3api list-objects-v2` pelo shim,
    como o `resume.py` fará, devolvendo as chaves sob `prefix`."""

    def _list_objects(trail: ShimTrail, prefix: str) -> list[str]:
        scratch = tmp_path_factory.mktemp("reader")
        result = subprocess.run(
            [
                str(shim_bin / "aws"),
                "s3api",
                "list-objects-v2",
                "--bucket",
                BUCKET,
                "--prefix",
                prefix,
                "--output",
                "json",
            ],
            env={
                **os.environ,
                "SMOKE_ARGV_DIR": str(scratch),
                "SMOKE_S3_ROOT": str(trail.s3_root),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        if not result.stdout.strip():
            return []
        return [item["Key"] for item in json.loads(result.stdout)["Contents"]]

    return _list_objects


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


def validate_manifest_with_cli(
    manifest_path: Path, config: Path
) -> subprocess.CompletedProcess[str]:
    """A CLI do contrato do manifesto, do `orchestrator/`, invocada como caixa-preta.

    O config é do chamador: o `test_fetch_masters.py` confere um manifesto montado
    sobre o `config/experiment.toml`, e o `test_prepare_masters.py`, um que o bash
    escreveu a partir do TOML remendado com o sha256 do placeholder. Fixar um dos
    dois aqui deixaria o outro conferindo contra a spec errada.
    """
    return subprocess.run(
        [sys.executable, str(VALIDATE_MANIFEST), str(manifest_path), "--config", str(config)],
        capture_output=True,
        text=True,
        check=False,
    )


def consolidate_with_cli(runs_dir: Path, out: Path) -> subprocess.CompletedProcess[str]:
    """A CLI de consolidação do `analysis/`, sobre a árvore que o laço produziu."""
    return subprocess.run(
        [sys.executable, str(CONSOLIDATE), "--runs", str(runs_dir), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )


DOCKER_OPTION = "--docker"
CAPTURE_DIR_OPTION = "--capture-dir"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        DOCKER_OPTION,
        action="store_true",
        default=False,
        help="roda a camada de aceite: builda a imagem e exercita as ferramentas reais",
    )
    parser.addoption(
        CAPTURE_DIR_OPTION,
        default=None,
        help="diretório onde depositar as saídas cruas capturadas, para virarem fixtures",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", f"docker: camada de aceite dentro da imagem, só coletada com {DOCKER_OPTION}"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Desmarca o aceite quando não se pediu por ele.

    Desmarcar, e não pular: um `skip` ainda montaria as fixtures do módulo, e com
    elas o build da imagem.
    """
    if config.getoption(DOCKER_OPTION):
        return
    deselected = [item for item in items if item.get_closest_marker("docker")]
    if not deselected:
        return
    config.hook.pytest_deselected(items=deselected)
    items[:] = [item for item in items if item.get_closest_marker("docker") is None]


@pytest.fixture(scope="session")
def capture_dir(pytestconfig: pytest.Config) -> Path | None:
    """O diretório de `--capture-dir`, criado; `None` quando não se pediu por ele."""
    requested = pytestconfig.getoption(CAPTURE_DIR_OPTION)
    if requested is None:
        return None
    destination = Path(requested).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    return destination
