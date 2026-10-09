# O item 5 da checklist (D21): o gate passa se todo grupo foi julgado com
# sucesso, e a equivalência é **reportada**, não exigida (ADR-0005). Os dois
# modos de falha calados são um grupo não julgado contado como equivalente e um
# grupo divergente que some da lista de onde sai a subseção do artigo.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from conftest import ROLE_ROOT, make_ffmpeg_log, make_meta_json
from gate import check_quality, nonequivalent
from run_meta import load_meta
from run_table import RawRun, consolidate_runs

CLI = ROLE_ROOT / "gate.py"


def group(scenario: str, **fields: object) -> dict[str, object]:
    return {
        "scenario": scenario,
        "bitstreams": 2,
        "vmaf_delta": 0.1,
        "ssim_delta": 0.0001,
        "judged_ok": True,
        "equivalent": True,
        **fields,
    }


def groups(*rows: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame(list(rows)).astype({"equivalent": "boolean"})


class TestTheLine:
    def test_every_group_judged_passes_and_counts_the_equivalent_ones(self):
        df = groups(
            group("libx264_1080p_720p_bbb"),
            group("libx264_1080p_720p_tos", bitstreams=3, vmaf_delta=1.2, equivalent=False),
        )

        assert check_quality(df) == (
            True,
            "2 grupos, 5 bitstreams julgados, 1/2 equivalentes",
        )

    def test_a_group_not_judged_fails_and_is_named(self):
        df = groups(
            group("libx264_1080p_720p_bbb"),
            group(
                "libx264_1080p_720p_tos",
                vmaf_delta=None,
                ssim_delta=None,
                judged_ok=False,
                equivalent=None,
            ),
        )

        assert check_quality(df) == (
            False,
            "2 grupos, 4 bitstreams julgados, 1/2 equivalentes; "
            "sem julgamento válido: libx264_1080p_720p_tos",
        )

    def test_an_empty_table_fails(self):
        # `all()` de nada é verdadeiro: uma árvore vazia passaria o item calada.
        df = pd.DataFrame(columns=["scenario", "bitstreams", "judged_ok", "equivalent"])

        assert check_quality(df) == (
            False,
            "0 grupos, 0 bitstreams julgados, 0/0 equivalentes; nenhum grupo julgado",
        )


class TestTheNonEquivalentList:
    def test_each_non_equivalent_group_comes_with_both_distances(self):
        df = groups(
            group("libx264_1080p_720p_bbb"),
            group("libx265_1080p_720p_tos", vmaf_delta=1.2345, ssim_delta=0.0021, equivalent=False),
        )

        assert nonequivalent(df) == ["libx265_1080p_720p_tos: vmaf_delta 1.234, ssim_delta 0.00210"]

    def test_a_group_not_judged_is_not_listed_as_divergent(self):
        # Nulo não é falso: o grupo sem veredito não tem distância a reportar.
        df = groups(group("libx264_1080p_720p_tos", judged_ok=False, equivalent=None))

        assert nonequivalent(df) == []


def run_gate(tmp_path: Path, *quality: str) -> subprocess.CompletedProcess[str]:
    runs = tmp_path / "runs.parquet"
    pq.write_table(
        consolidate_runs(
            [
                RawRun(
                    meta=load_meta(make_meta_json()),
                    time=None,
                    perf=None,
                    pidstat=None,
                    ffmpeg=make_ffmpeg_log(),
                    sha256="a3f1c0de\n",
                )
            ]
        ).table,
        runs,
    )
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--parquet",
            str(runs),
            "--config",
            str(ROLE_ROOT.parent / "config" / "pilot.toml"),
            "--prices",
            str(ROLE_ROOT / "prices.toml"),
            "--covers",
            "piloto",
            *quality,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


class TestTheCli:
    def test_with_quality_it_prints_item_5_and_the_divergent_groups(self, tmp_path):
        path = tmp_path / "groups.parquet"
        groups(
            group("libx264_1080p_720p_bbb"),
            group("libx265_1080p_720p_tos", vmaf_delta=1.2345, ssim_delta=0.0021, equivalent=False),
        ).to_parquet(path)

        result = run_gate(tmp_path, "--quality", str(path))

        assert (
            "[ok] 5  triage e Juiz: 2 grupos, 4 bitstreams julgados, 1/2 equivalentes"
            in result.stdout.splitlines()
        )
        assert "  libx265_1080p_720p_tos: vmaf_delta 1.234, ssim_delta 0.00210" in (
            result.stdout.splitlines()
        )

    def test_without_quality_item_5_is_open(self, tmp_path):
        result = run_gate(tmp_path)

        assert "[em aberto] 5  triage e Juiz: sem --quality" in result.stdout.splitlines()

    def test_a_group_not_judged_fails_the_gate(self, tmp_path):
        # Os itens 1 a 4 já falham sobre um Parquet de uma linha só; o que se quer
        # ver é a linha 5 dizendo FALHOU.
        path = tmp_path / "groups.parquet"
        groups(group("libx264_1080p_720p_tos", judged_ok=False, equivalent=None)).to_parquet(path)

        result = run_gate(tmp_path, "--quality", str(path))

        assert result.returncode == 1
        assert any(
            line.startswith("[FALHOU] 5  triage e Juiz:") for line in result.stdout.splitlines()
        )
