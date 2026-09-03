# Smoke do `encode/run_scenario.sh`: o script de verdade, dirigido no Mac com
# `ffmpeg`, `perf`, `pidstat` e `/usr/bin/time` shimados, sem Docker, sem AWS e
# sem FFmpeg (ADR-0022).
#
# A asserção central é a do **argv**, e o que ela verifica é a cadeia inteira
# `experiment.toml` → gerador → plano → `jq` → argv: uma aspa mal posta no `jq`
# derruba o CRF em silêncio e produz um `.mkv` válido com parâmetro errado. Como a
# ADR-0021 permite editar o script **durante** a campanha, esta é a única guarda
# automática contra um hotfix que derrube uma flag.

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

import pytest
from conftest import (
    COMMIT,
    EXPERIMENT,
    INSTANCE_ID,
    INSTANCE_TYPE,
    VERSIONS,
    Execution,
    check_with_stdlib_checker,
    validate_with_cli,
)

# Os 7 artefatos da ADR-0007, já com a emenda D21 (`pidstat.txt`).
ARTIFACTS = frozenset(
    {
        "meta.json",
        "time.json",
        "perf.json",
        "pidstat.txt",
        "ffmpeg.log",
        "output.mkv",
        "output.sha256",
    }
)

# Os campos que o `meta.json` ecoa verbatim do objeto de run (decisão D4).
ECHOED_FIELDS = (
    "scenario_id",
    "warmup",
    "seed",
    "codec",
    "encoder",
    "input_res",
    "output_res",
    "video",
    "instance",
    "master",
    "output_width",
    "output_height",
    "preset",
    "crf",
    "encoder_args",
    "threads",
    "gop_size",
    "pix_fmt",
    "strip_audio",
    "container",
    "scale_flags",
)

UUID4 = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")

# Os parâmetros específicos do encoder variam com o primeiro eixo e a geometria de
# saída com o segundo.
MATRIX = [
    (codec["encoder"], video["slug"])
    for codec in EXPERIMENT["codec"]
    for video in EXPERIMENT["video"]
]


def codec_record(encoder: str) -> dict[str, Any]:
    return next(codec for codec in EXPERIMENT["codec"] if codec["encoder"] == encoder)


def video_record(slug: str) -> dict[str, Any]:
    return next(video for video in EXPERIMENT["video"] if video["slug"] == slug)


def replication(block: dict[str, Any]) -> dict[str, Any]:
    return next(run for run in block["runs"] if not run["warmup"])


def encode_argv(execution: Execution) -> list[str]:
    return execution.argv("ffmpeg")[0]


def extraction_argv(execution: Execution) -> list[str]:
    return execution.argv("ffmpeg")[1]


def value_after(argv: list[str], flag: str) -> str:
    assert flag in argv, argv
    return argv[argv.index(flag) + 1]


def contains_subsequence(argv: list[str], fragment: list[str]) -> bool:
    """O fragmento aparece contíguo e na ordem — que é como o FFmpeg o lê."""
    return any(argv[i : i + len(fragment)] == fragment for i in range(len(argv)))


@pytest.fixture(scope="session")
def executions(plan, execute) -> dict[tuple[str, str], Execution]:
    """Uma Replicação por `encoder x vídeo`, escolhida pela `scenario_id` mínima.

    Pela `scenario_id` e não pela ordem do plano: a ordem é o embaralhamento com a
    seed, e escolher por ela faria o par de tiers que cada Cenário exercita mudar
    em silêncio quando alguém trocasse a seed. Assim a cobertura é função da
    matriz, que é o que o artigo reporta.
    """
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for block in sorted(plan["blocks"], key=lambda block: replication(block)["scenario_id"]):
        selected.setdefault((block["encoder"], block["video"]), block)
    return {key: execute(replication(block)) for key, block in selected.items()}


@pytest.fixture(scope="session")
def execution(executions) -> Execution:
    """Uma Execução qualquer, para o que não varia por Cenário."""
    return executions[MATRIX[0]]


@pytest.fixture(scope="session")
def warmup_execution(plan, execute) -> Execution:
    return execute(plan["blocks"][0]["runs"][0])


@pytest.fixture(scope="session")
def failed_encode(plan, execute) -> Execution:
    return execute(replication(plan["blocks"][0]), SMOKE_FFMPEG_EXIT="1")


@pytest.fixture(scope="session")
def omitted_event(plan, execute) -> Execution:
    return execute(replication(plan["blocks"][0]), SMOKE_PERF_OMIT="instructions")


@pytest.fixture(scope="session")
def perf_failure(plan, execute) -> Execution:
    return execute(replication(plan["blocks"][0]), SMOKE_PERF_EXIT="1")


@pytest.fixture(scope="session")
def unsupported_event(plan, execute) -> Execution:
    return execute(replication(plan["blocks"][0]), SMOKE_PERF_UNSUPPORTED="cache-misses")


@pytest.fixture(scope="session")
def unresolved_pid(plan, execute) -> Execution:
    return execute(replication(plan["blocks"][0]), SMOKE_ENCODER_INVISIBLE="1")


@pytest.fixture(scope="session")
def repeated_executions(plan, execute) -> list[Execution]:
    """O mesmo Cenário rodado duas vezes, com bitstreams diferentes.

    Cobre as duas coisas que só um par de Execuções mostra: `run_id` distintos
    para o mesmo Cenário, e o `output.sha256` divergindo quando — e só quando — o
    bitstream diverge.
    """
    run = replication(plan["blocks"][0])
    return [execute(run, SMOKE_BITSTREAM=payload) for payload in ("primeiro", "segundo")]


class TestArgv:
    @pytest.mark.parametrize(("encoder", "video"), MATRIX)
    def test_encoder_preset_and_crf_are_the_declared_ones(self, executions, encoder, video):
        argv = encode_argv(executions[(encoder, video)])
        codec = codec_record(encoder)

        assert value_after(argv, "-c:v") == codec["encoder"]
        assert value_after(argv, "-preset") == codec["preset"]
        assert value_after(argv, "-crf") == str(codec["crf"])

    @pytest.mark.parametrize(("encoder", "video"), MATRIX)
    def test_encoder_specific_arguments_arrive_intact(self, executions, encoder, video):
        # `-x265-params scenecut=0` e `-svtav1-params scd=0` são pares: o flag
        # separado do seu valor é FFmpeg lendo outra coisa, ou nada.
        argv = encode_argv(executions[(encoder, video)])

        assert contains_subsequence(argv, codec_record(encoder)["encoder_args"])

    @pytest.mark.parametrize(("encoder", "video"), MATRIX)
    def test_output_geometry_is_the_tier_geometry_of_that_video(self, executions, encoder, video):
        # Os tiers são rótulos nominais: "1080p" não é 1920x1080 nos dois vídeos.
        execution = executions[(encoder, video)]
        geometry = video_record(video)["geometry"][execution.run["output_res"]]
        scale_flags = EXPERIMENT["encode"]["scale_flags"]

        assert value_after(encode_argv(execution), "-vf") == (
            f"scale={geometry['width']}:{geometry['height']}:flags={scale_flags}"
        )

    @pytest.mark.parametrize(("encoder", "video"), MATRIX)
    def test_the_master_is_the_one_the_plan_named(self, executions, encoder, video):
        execution = executions[(encoder, video)]

        assert value_after(encode_argv(execution), "-i").endswith(execution.run["master"])

    def test_fixed_parameters_are_present(self, execution):
        # Por presença: não variam por Cenário, e o que se barra é um sumir.
        argv = encode_argv(execution)

        assert "-g" in argv
        assert "-pix_fmt" in argv
        assert "-threads" in argv
        assert "-an" in argv
        assert "flags=" in value_after(argv, "-vf")

    def test_the_output_is_the_container_the_run_declares(self, execution):
        assert encode_argv(execution)[-1].endswith(f".{execution.run['container']}")


class TestInstrumentation:
    def test_perf_is_the_innermost_wrapper(self, execution):
        # Um wrapper entre o `perf` e o encode entra na contagem de instruções.
        perf_argv = execution.argv("perf")[0]

        assert perf_argv[perf_argv.index("--") + 1 :] == ["ffmpeg", *encode_argv(execution)]

    def test_time_wraps_perf(self, execution):
        time_argv = execution.argv("time")[0]
        perf_argv = execution.argv("perf")[0]

        assert time_argv[time_argv.index("perf") :] == ["perf", *perf_argv]

    def test_perf_receives_the_pmu_events_of_the_spec(self, execution):
        events = value_after(execution.argv("perf")[0], "-e")

        assert events.split(",") == EXPERIMENT["instrumentation"]["pmu_events"]

    def test_pidstat_follows_the_ffmpeg_process(self, execution):
        # Apontar para o wrapper daria um `pidstat.txt` de processo ocioso e um
        # `cpu_pct_avg` de quase-zeros, descoberto depois da campanha.
        pidstat_argv = execution.argv("pidstat")[0]

        assert value_after(pidstat_argv, "-p") == execution.encoder_pid()

    def test_pidstat_samples_at_one_hertz(self, execution):
        pidstat_argv = execution.argv("pidstat")[0]

        assert pidstat_argv[-1] == "1"
        assert {"-h", "-r", "-u"} <= set(pidstat_argv)


class TestArtifacts:
    def test_the_seven_artifacts_of_the_adr(self, execution):
        assert {path.name for path in execution.run_dir.iterdir()} == ARTIFACTS

    def test_the_run_succeeded(self, execution):
        assert execution.returncode == 0, execution.stderr

    def test_time_json_is_json_born_from_the_tool(self, execution):
        assert isinstance(json.loads((execution.run_dir / "time.json").read_text()), dict)

    def test_perf_json_is_json_born_from_the_tool(self, execution):
        lines = (execution.run_dir / "perf.json").read_text().splitlines()

        assert [json.loads(line)["event"] for line in lines] == (
            EXPERIMENT["instrumentation"]["pmu_events"]
        )

    def test_ffmpeg_log_keeps_the_raw_stderr(self, execution):
        # O artefato recebe o stderr da cadeia inteira; a igualdade fixa que num
        # run bem-sucedido nada dos wrappers entra nele.
        log = (execution.run_dir / "ffmpeg.log").read_text()

        assert "frame=" in log
        assert log.splitlines() == [line for line in log.splitlines() if line.startswith("frame=")]

    def test_pidstat_output_is_kept_raw(self, execution):
        assert (execution.run_dir / "pidstat.txt").read_text().startswith("# Time")

    def test_each_execution_mints_a_fresh_uuid4(self, repeated_executions):
        first, second = (execution.meta()["run_id"] for execution in repeated_executions)

        assert UUID4.match(first) and UUID4.match(second)
        assert first != second


class TestBitstreamHash:
    def test_the_hash_covers_the_bitstream_the_shim_emitted(self, repeated_executions):
        execution = repeated_executions[0]
        digest = (execution.run_dir / "output.sha256").read_text().strip()

        assert digest == hashlib.sha256(b"primeiro").hexdigest()

    def test_the_hash_is_not_the_container(self, repeated_executions):
        # Hashear o container faria dois outputs idênticos divergirem pelo
        # metadado de mux (ADR-0005/0007).
        execution = repeated_executions[0]
        digest = (execution.run_dir / "output.sha256").read_text().strip()

        assert digest != hashlib.sha256((execution.run_dir / "output.mkv").read_bytes()).hexdigest()

    def test_divergent_bitstreams_give_divergent_hashes(self, repeated_executions):
        # É o que torna um grupo hash-divergente exercitável quando o triage
        # chegar — na campanha real ele quase certamente nunca dispara.
        digests = {
            (execution.run_dir / "output.sha256").read_text() for execution in repeated_executions
        }

        assert len(digests) == 2

    @pytest.mark.parametrize(("encoder", "video"), MATRIX)
    def test_extraction_uses_the_muxer_the_run_object_carries(self, executions, encoder, video):
        # O muxer é dado do TOML e não derivação do nome do codec (decisão D6).
        argv = extraction_argv(executions[(encoder, video)])

        assert value_after(argv, "-f") == codec_record(encoder)["bitstream_muxer"]
        assert contains_subsequence(argv, ["-c", "copy"])


class TestMeta:
    def test_accepted_by_the_validation_cli(self, execution):
        result = validate_with_cli(execution.run_dir / "meta.json")

        assert result.returncode == 0, result.stderr

    def test_accepted_by_the_stdlib_checker(self, execution):
        result = check_with_stdlib_checker(execution.run_dir / "meta.json")

        assert result.returncode == 0, result.stderr

    def test_echoes_the_run_object_verbatim(self, execution):
        meta = execution.meta()

        assert {field: meta[field] for field in ECHOED_FIELDS} == {
            field: execution.run[field] for field in ECHOED_FIELDS
        }

    def test_warmup_is_a_real_boolean(self, execution, warmup_execution):
        # `"warmup": "false"` é uma string truthy e faria o warm-up entrar na
        # retomada como Replicação, enviesando a média (ADR-0003/0019).
        assert execution.meta()["warmup"] is False
        assert warmup_execution.meta()["warmup"] is True

    def test_carries_what_the_execution_minted(self, execution):
        meta = execution.meta()

        assert meta["run_id"] == execution.run_dir.name
        assert meta["exit_code"] == 0
        assert meta["schema_version"] == "1"

    def test_timestamps_are_ordered_instants_with_offset(self, execution):
        # A dedup "último `started_at` vence" ordena instantes: um timestamp
        # naïve perdeu a informação para normalizar, e a leitura é a única janela
        # em que isso é detectável (ADR-0022).
        meta = execution.meta()
        started = datetime.fromisoformat(meta["started_at"])
        finished = datetime.fromisoformat(meta["finished_at"])

        assert started.utcoffset() is not None
        assert finished.utcoffset() is not None
        assert started <= finished

    def test_carries_the_bootstrap_arguments(self, execution):
        meta = execution.meta()

        assert meta["commit"] == COMMIT
        assert meta["instance_id"] == INSTANCE_ID
        assert meta["instance_type"] == INSTANCE_TYPE

    def test_copies_the_versions_of_the_image(self, execution):
        assert execution.meta()["versions"] == VERSIONS


class TestFailedRun:
    def test_the_run_reports_the_failure(self, failed_encode):
        assert failed_encode.returncode != 0
        assert failed_encode.meta()["exit_code"] == 1

    def test_the_artifacts_it_had_are_preserved(self, failed_encode):
        # Fica no disco para o `resume.py` o tratar como não-completo.
        present = {path.name for path in failed_encode.run_dir.iterdir()}

        assert present == ARTIFACTS - {"output.sha256"}

    def test_the_meta_json_is_still_a_valid_meta_json(self, failed_encode):
        result = validate_with_cli(failed_encode.run_dir / "meta.json")

        assert result.returncode == 0, result.stderr


class TestInstrumentationFailure:
    # Nunca existe run "bem-sucedido" sem os contadores que são o achado
    # principal, e não há flag que desligue a medição (decisão D8).

    def test_perf_blowing_up_fails_the_run(self, perf_failure):
        assert perf_failure.returncode != 0
        assert perf_failure.meta()["exit_code"] != 0

    def test_a_silently_absent_counter_fails_the_run(self, omitted_event):
        # `instructions` é omitido de propósito: é substring de
        # `branch-instructions`, e uma guarda por nome solto o daria por presente.
        assert omitted_event.returncode != 0
        assert omitted_event.meta()["exit_code"] != 0

    def test_an_unsupported_event_fails_the_run(self, unsupported_event):
        # O `perf` não falha: reporta `<not supported>` e segue. A coluna de
        # cache miss rate viria vazia para uma arquitetura inteira.
        assert unsupported_event.returncode != 0
        assert unsupported_event.meta()["exit_code"] != 0

    def test_an_unresolved_encoder_pid_fails_the_run(self, unresolved_pid):
        # O `pidstat` sequer chega a ser lançado — é o que distingue este
        # caminho do da instrumentação que falhou depois.
        assert unresolved_pid.returncode != 0
        assert unresolved_pid.meta()["exit_code"] != 0
        assert not (unresolved_pid.run_dir / "pidstat.txt").exists()

    def test_the_failed_run_is_still_readable_by_both_readers(self, perf_failure):
        meta = perf_failure.run_dir / "meta.json"

        assert validate_with_cli(meta).returncode == 0
        assert check_with_stdlib_checker(meta).returncode == 0
