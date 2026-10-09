# Smoke do `analysis/quality.py` e do `gate.py --quality`: o leitor do Pass como
# caixa-preta sobre os resultados que o `run_quality.sh` deixou no bucket falso.
# O porquê está no `smoke/README.md`.

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from conftest import (
    ARM,
    QUALITY_RESULTS_PREFIX,
    REPO_ROOT,
    Pass,
    Triage,
    consolidate_with_cli,
)

QUALITY = REPO_ROOT / "analysis" / "quality.py"
GATE = REPO_ROOT / "analysis" / "gate.py"
PRICES = REPO_ROOT / "analysis" / "prices.toml"

# O Pass são julga tudo a 91,5; o segundo julga de novo só o output do ARM sobre
# um dos vídeos, a 80. O grupo daquele vídeo fica a 11,5 de distância e o outro a
# zero: um equivalente e um divergente.
SMOKE_VMAF = "91.5"
DIVERGENT_VMAF = "80"
DIVERGENT_VIDEO = "tos"


@pytest.fixture(scope="session")
def judged(triaged: Triage, campaign, run_quality) -> Pass:
    return run_quality(campaign[ARM], triaged.plan(), SMOKE_VMAF=SMOKE_VMAF)


@pytest.fixture(scope="session")
def rejudged(judged: Pass, triaged: Triage, run_quality) -> Pass:
    """O output do ARM sobre o vídeo divergente julgado de novo, sobre o bucket do
    Pass são: o resultado novo sobrescreve o antigo sob o mesmo `run_id`."""
    plan = triaged.plan()
    (divergent,) = [
        output
        for output in plan["outputs"]
        if output["instance"] == ARM and output["video"] == DIVERGENT_VIDEO
    ]
    return run_quality(judged, {**plan, "outputs": [divergent]}, SMOKE_VMAF=DIVERGENT_VMAF)


def results_of(judged: Pass) -> Path:
    return judged.bucket_dir() / QUALITY_RESULTS_PREFIX


def shim_frames(judged: Pass) -> int:
    """Quantos frames o shim do `ffmpeg` pôs em cada log, pelo que ele escreveu."""
    (count,) = {
        len(json.loads(log.read_text(encoding="utf-8"))["frames"])
        for log in results_of(judged).glob("*/vmaf.json")
    }
    return count


def with_frames(config: Path, frames: int, out_dir: Path) -> Path:
    """A definição com os frames que o shim honra.

    É o único fato do arquivo que um `libvmaf` shimado não tem como honrar —
    ninguém decodifica 19 036 frames de um placeholder —, e trocá-lo aqui mantém o
    resto sendo o da definição que o triage leu.
    """
    text = config.read_text(encoding="utf-8")
    patched, count = re.subn(r"^frames = \d+$", f"frames = {frames}", text, flags=re.MULTILINE)
    assert count == text.count("[[video]]")
    path = out_dir / config.name
    path.write_text(patched, encoding="utf-8")
    return path


class Reading:
    def __init__(self, result: subprocess.CompletedProcess[str], out_dir: Path) -> None:
        self.returncode = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr
        self.out_dir = out_dir

    def outputs(self) -> list[dict[str, Any]]:
        return pq.read_table(self.out_dir / "outputs.parquet").to_pylist()

    def groups(self) -> list[dict[str, Any]]:
        return pq.read_table(self.out_dir / "groups.parquet").to_pylist()

    def group(self, video: str) -> dict[str, Any]:
        (row,) = [row for row in self.groups() if row["video"] == video]
        return row


def read_with_cli(results: Path, config: Path, out_dir: Path) -> Reading:
    out_dir.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(QUALITY),
            "--results",
            str(results),
            "--config",
            str(config),
            "--outputs",
            str(out_dir / "outputs.parquet"),
            "--groups",
            str(out_dir / "groups.parquet"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return Reading(result, out_dir)


def gate_with_cli(
    judged: Pass, config: Path, groups: Path, out_dir: Path
) -> subprocess.CompletedProcess[str]:
    runs = out_dir / "runs.parquet"
    consolidated = consolidate_with_cli(judged.bucket_dir() / "runs", runs)
    assert consolidated.returncode == 0, consolidated.stderr
    return subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--parquet",
            str(runs),
            "--config",
            str(config),
            "--prices",
            str(PRICES),
            "--covers",
            "piloto",
            "--quality",
            str(groups),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="session")
def reading(rejudged, one_codec_toml, tmp_path_factory) -> Reading:
    scratch = tmp_path_factory.mktemp("quality")
    config = with_frames(one_codec_toml, shim_frames(rejudged), scratch)
    return read_with_cli(results_of(rejudged), config, scratch / "out")


@pytest.fixture(scope="session")
def reading_against_the_real_frames(rejudged, one_codec_toml, tmp_path_factory) -> Reading:
    return read_with_cli(
        results_of(rejudged), one_codec_toml, tmp_path_factory.mktemp("quality") / "out"
    )


@pytest.fixture(scope="session")
def gate(rejudged, reading, one_codec_toml, tmp_path_factory) -> subprocess.CompletedProcess[str]:
    return gate_with_cli(
        rejudged,
        one_codec_toml,
        reading.out_dir / "groups.parquet",
        tmp_path_factory.mktemp("gate"),
    )


class TestTheOutputsTable:
    def test_it_succeeds(self, reading):
        assert reading.returncode == 0, reading.stderr

    def test_one_row_per_output_of_the_plan_ordered_by_scenario_id(self, reading, triaged):
        expected = sorted(triaged.plan()["outputs"], key=lambda output: output["scenario_id"])

        assert [row["run_id"] for row in reading.outputs()] == [
            output["run_id"] for output in expected
        ]

    def test_the_rejudged_output_carries_the_vmaf_of_the_second_pass(self, reading):
        vmaf = {(row["instance"], row["video"]): row["vmaf_mean"] for row in reading.outputs()}

        assert vmaf[(ARM, DIVERGENT_VIDEO)] == float(DIVERGENT_VMAF)
        assert sorted(set(vmaf.values())) == [float(DIVERGENT_VMAF), float(SMOKE_VMAF)]

    def test_every_output_was_judged_with_the_frames_the_definition_expects(self, reading):
        assert all(row["frames_match"] for row in reading.outputs())


class TestTheGroupsTable:
    def test_one_row_per_scenario_with_two_bitstreams_each(self, reading):
        assert [(row["video"], row["bitstreams"]) for row in reading.groups()] == [
            ("bbb", 2),
            ("tos", 2),
        ]

    def test_the_two_x86_name_the_same_hash_and_the_arm_another(self, reading):
        for row in reading.groups():
            assert row["sha256_c7i"] == row["sha256_c7a"]
            assert row["sha256_c7g"] != row["sha256_c7i"]

    def test_the_group_at_distance_zero_is_equivalent(self, reading):
        group = reading.group("bbb")

        assert (group["judged_ok"], group["vmaf_delta"], group["equivalent"]) == (True, 0.0, True)

    def test_the_group_the_second_pass_moved_is_not(self, reading):
        group = reading.group(DIVERGENT_VIDEO)

        assert group["judged_ok"] is True
        assert group["vmaf_delta"] == pytest.approx(float(SMOKE_VMAF) - float(DIVERGENT_VMAF))
        assert group["equivalent"] is False


class TestTheFrameGuard:
    def test_against_the_frames_of_the_real_definition_no_output_is_valid(
        self, reading_against_the_real_frames
    ):
        # O shim compara 3 frames, e o vídeo da definição tem milhares: é a
        # referência desalinhada que daria um VMAF plausível e errado.
        reading = reading_against_the_real_frames

        assert reading.returncode == 0, reading.stderr
        assert not any(row["frames_match"] for row in reading.outputs())
        assert [(row["judged_ok"], row["equivalent"]) for row in reading.groups()] == [
            (False, None),
            (False, None),
        ]

    def test_each_mismatch_is_reported(self, reading_against_the_real_frames, triaged):
        for output in triaged.plan()["outputs"]:
            assert f"{output['run_id']}/vmaf.json: 3 frames no log" in (
                reading_against_the_real_frames.stderr
            )


class TestTheGate:
    def test_item_5_counts_groups_bitstreams_and_equivalent_ones(self, gate):
        assert (
            "[ok] 5  triage e Juiz: 2 grupos, 4 bitstreams julgados, 1/2 equivalentes"
            in gate.stdout.splitlines()
        )

    def test_the_divergent_group_is_listed_with_both_distances(self, gate, reading):
        name = reading.group(DIVERGENT_VIDEO)["scenario"]

        assert f"  {name}: vmaf_delta 11.500, ssim_delta 0.11500" in gate.stdout.splitlines()
