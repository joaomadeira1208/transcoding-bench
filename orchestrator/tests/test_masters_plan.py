# Um Master a menos, uma geometria trocada entre os dois vídeos ou um `ffv1` onde
# o remux só copia não estouram em lugar nenhum: a preparação sobe seis arquivos
# plausíveis e a campanha inteira mede a entrada errada. Tudo aqui assere o plano
# emitido, nunca a estrutura do gerador.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import (
    REAL_EXPERIMENT_TOML,
    REAL_PILOT_TOML,
    REPO_ROOT,
    make_geometry,
    make_video,
    real_config,
    real_pilot_config,
)
from experiment_config import ConfigError, validate_config
from masters_plan import build_masters_plan
from scenario_plan import build_canonical_plan, serialize_plan

# Transcrição da ADR-0004: três tiers de Master por vídeo, e o 480p só saída.
EXPECTED_MASTERS = {
    "bbb_2160p.mkv",
    "bbb_1080p.mkv",
    "bbb_720p.mkv",
    "tos_2160p.mkv",
    "tos_1080p.mkv",
    "tos_720p.mkv",
}

# O piloto roda um par só, `1080p → 720p` (ADR-0022): o 1080p é Master porque é
# input, o 720p não é porque nenhum par o consome, e os dois 4K entram porque o
# 4K é sempre remuxado.
PILOT_MASTERS = {
    "bbb_2160p.mkv",
    "bbb_1080p.mkv",
    "tos_2160p.mkv",
    "tos_1080p.mkv",
}

# Geometria da ADR-0023, transcrita: o teste que a lê do próprio TOML não
# distingue "cada Master leva a geometria do seu tier" de "todos levam a mesma".
EXPECTED_GEOMETRY = {
    "bbb_2160p.mkv": (3840, 2160),
    "bbb_1080p.mkv": (1920, 1080),
    "bbb_720p.mkv": (1280, 720),
    "tos_2160p.mkv": (3840, 1714),
    "tos_1080p.mkv": (1920, 856),
    "tos_720p.mkv": (1280, 572),
}

MASTER_FIELDS = {
    "name",
    "video",
    "tier",
    "width",
    "height",
    "codec_name",
    "pix_fmt",
    "frame_rate",
    "frames",
}

GENERATOR = REPO_ROOT / "orchestrator" / "generate_masters_plan.py"


@pytest.fixture(scope="module")
def plan() -> dict:
    return build_masters_plan(real_config())


@pytest.fixture(scope="module")
def pilot_plan() -> dict:
    return build_masters_plan(real_pilot_config())


@pytest.fixture(
    scope="module",
    params=[real_config, real_pilot_config],
    ids=["campaign", "pilot"],
)
def either_config(request):
    return request.param


def all_masters(plan: dict) -> list[dict]:
    return [master for video in plan["videos"] for master in (video["master"], *video["derived"])]


def names(plan: dict) -> set[str]:
    return {master["name"] for master in all_masters(plan)}


def by_name(plan: dict) -> dict[str, dict]:
    return {master["name"]: master for master in all_masters(plan)}


def scenario_masters(config) -> set[str]:
    plan = build_canonical_plan(config)
    return {run["master"] for block in plan["blocks"] for run in block["runs"]}


def one_video(plan: dict, slug: str) -> dict:
    matches = [video for video in plan["videos"] if video["video"] == slug]

    assert len(matches) == 1, slug
    return matches[0]


class TestMastersOfTheSpec:
    def test_exactly_the_six_masters_of_the_adr(self, plan):
        masters = all_masters(plan)

        assert names(plan) == EXPECTED_MASTERS
        assert len(masters) == len(EXPECTED_MASTERS)

    def test_the_names_are_the_masters_the_scenario_plan_cites(self, plan):
        # O elo entre os dois planos: se o conjunto de tiers derivados divergir,
        # a instância de encode busca um objeto que a preparação não produziu.
        assert names(plan) == scenario_masters(real_config())

    def test_480p_is_only_an_output_and_never_a_master(self, plan):
        # A ADR-0023 o exclui por ser só saída, e a regra de derivação — tier que
        # aparece como `input_res` — é o que o realiza.
        assert "480p" not in {master["tier"] for master in all_masters(plan)}

    def test_every_master_carries_the_geometry_of_its_video_and_tier(self, plan):
        geometry = {
            name: (master["width"], master["height"]) for name, master in by_name(plan).items()
        }

        assert geometry == EXPECTED_GEOMETRY

    def test_the_4k_is_the_remuxed_h264_and_the_derived_are_ffv1(self, plan):
        # O 4K é a versão canônica publicada, copiada com `-c copy`; os derivados
        # saem do downscale Lanczos lossless (ADR-0004).
        codecs = {name: master["codec_name"] for name, master in by_name(plan).items()}

        assert codecs == {
            "bbb_2160p.mkv": "h264",
            "bbb_1080p.mkv": "ffv1",
            "bbb_720p.mkv": "ffv1",
            "tos_2160p.mkv": "h264",
            "tos_1080p.mkv": "ffv1",
            "tos_720p.mkv": "ffv1",
        }

    def test_every_master_carries_the_pix_fmt_of_the_encode_record(self, plan):
        pix_fmt = real_config().encode.pix_fmt

        assert {master["pix_fmt"] for master in all_masters(plan)} == {pix_fmt}

    def test_every_master_carries_the_frame_rate_and_frames_of_its_video(self, plan):
        # Nem o remux nem o downscale mexem na cadência: o Master herda a do vídeo.
        videos = {video.slug: video for video in real_config().videos}

        for master in all_masters(plan):
            video = videos[master["video"]]

            assert (master["frame_rate"], master["frames"]) == (video.frame_rate, video.frames)

    def test_every_video_carries_the_source_declared_in_the_toml(self, plan):
        expected = {
            video.slug: {
                "url": video.source.url,
                "file": video.source.file,
                "size": video.source.size,
                "sha256": video.source.sha256,
            }
            for video in real_config().videos
        }

        assert {video["video"]: video["source"] for video in plan["videos"]} == expected

    def test_a_video_carries_exactly_the_fields_of_the_contract(self, plan):
        for video in plan["videos"]:
            assert set(video) == {"video", "source", "master", "derived"}

    def test_a_master_carries_exactly_the_fields_of_the_contract(self, plan):
        # O script de preparação copia campo a campo para o manifesto: um que
        # sumisse daqui viraria um `ffprobe` que não confere nada.
        for master in all_masters(plan):
            assert set(master) == MASTER_FIELDS

    def test_every_master_names_itself_after_its_video_and_tier(self, plan):
        for master in all_masters(plan):
            assert master["name"] == f"{master['video']}_{master['tier']}.mkv"

    def test_a_name_is_a_basename_and_no_bucket_path_is_decided_here(self, plan):
        # O prefixo vem por argumento do Orquestrador (ADR-0011): um `masters/`
        # aqui seria a segunda casa de uma decisão que já tem dona.
        for master in all_masters(plan):
            assert "/" not in master["name"]

    def test_the_4k_of_each_video_is_the_tier_that_gets_remuxed(self, plan):
        for video in plan["videos"]:
            assert video["master"]["tier"] == "2160p"
            assert "2160p" not in {master["tier"] for master in video["derived"]}

    def test_the_derived_tiers_follow_the_ladder(self, plan):
        for video in plan["videos"]:
            assert [master["tier"] for master in video["derived"]] == ["1080p", "720p"]

    def test_the_videos_are_the_ones_of_the_toml(self, plan):
        assert [video["video"] for video in plan["videos"]] == [
            video.slug for video in real_config().videos
        ]


class TestPilot:
    def test_the_masters_the_pilot_needs(self, pilot_plan):
        assert names(pilot_plan) == PILOT_MASTERS

    def test_the_pilot_masters_are_masters_of_the_campaign(self, pilot_plan, plan):
        # A preparação roda uma vez, sobre a campanha, e o bucket do piloto
        # recebe uma cópia (decisão D16): um Master que só o piloto pedisse não
        # seria produzido por ninguém.
        assert names(pilot_plan) <= names(plan)

    def test_the_pilot_covers_the_masters_its_scenario_plan_cites(self, pilot_plan):
        assert scenario_masters(real_pilot_config()) <= names(pilot_plan)

    def test_the_pilot_carries_the_same_videos_and_sources(self, pilot_plan, plan):
        assert [video["source"] for video in pilot_plan["videos"]] == [
            video["source"] for video in plan["videos"]
        ]


class TestDerivedTiers:
    def test_a_single_pair_yields_the_4k_and_its_input(self, make_raw_config):
        raw = make_raw_config(
            pair=[{"input_res": "1080p", "output_res": "720p"}],
            video=[
                make_video(
                    geometry=make_geometry(
                        **{"2160p": (3840, 2160), "1080p": (1920, 1080), "720p": (1280, 720)}
                    )
                )
            ],
        )

        plan = build_masters_plan(validate_config(raw))

        assert names(plan) == {"bbb_2160p.mkv", "bbb_1080p.mkv"}

    def test_the_4k_enters_once_even_when_a_pair_uses_it_as_input(self, make_raw_config):
        # `2160p` é `input_res` de um par **e** o tier sempre remuxado: derivá-lo
        # também o poria duas vezes na lista, e a preparação o produziria por
        # downscale de si mesmo.
        raw = make_raw_config(
            pair=[
                {"input_res": "2160p", "output_res": "1080p"},
                {"input_res": "1080p", "output_res": "720p"},
            ],
            video=[
                make_video(
                    geometry=make_geometry(
                        **{"2160p": (3840, 2160), "1080p": (1920, 1080), "720p": (1280, 720)}
                    )
                )
            ],
        )

        plan = build_masters_plan(validate_config(raw))

        assert names(plan) == {"bbb_2160p.mkv", "bbb_1080p.mkv"}
        assert [master["tier"] for master in one_video(plan, "bbb")["derived"]] == ["1080p"]

    def test_an_output_only_tier_never_becomes_a_master(self, make_raw_config):
        raw = make_raw_config(
            pair=[{"input_res": "1080p", "output_res": "480p"}],
            video=[
                make_video(
                    geometry=make_geometry(
                        **{"2160p": (3840, 2160), "1080p": (1920, 1080), "480p": (854, 480)}
                    )
                )
            ],
        )

        plan = build_masters_plan(validate_config(raw))

        assert names(plan) == {"bbb_2160p.mkv", "bbb_1080p.mkv"}

    def test_a_video_without_the_4k_tier_is_rejected_naming_it(self, make_raw_config):
        raw = make_raw_config(pair=[{"input_res": "1080p", "output_res": "720p"}])

        with pytest.raises(ConfigError) as error:
            build_masters_plan(validate_config(raw))

        assert "bbb" in str(error.value)
        assert "2160p" in str(error.value)


class TestDeterminism:
    def test_generating_twice_produces_identical_bytes(self, either_config):
        first = serialize_plan(build_masters_plan(either_config()))
        second = serialize_plan(build_masters_plan(either_config()))

        assert first == second

    def test_the_plan_carries_no_timestamp_or_environment_field(self, plan):
        assert set(plan) == {"schema_version", "videos"}
        assert "generated_at" not in serialize_plan(plan)

    def test_schema_version_is_a_string(self, plan):
        assert plan["schema_version"] == "1"

    def test_the_json_types_the_manifest_checker_expects(self, plan):
        # `"frames": "19036"` passaria por qualquer comparação feita em bash e
        # falharia na do checker, depois de os seis Masters estarem no bucket.
        reloaded = json.loads(serialize_plan(plan))

        for master in all_masters(reloaded):
            assert type(master["frame_rate"]) is str
            assert type(master["frames"]) is int
            assert type(master["width"]) is int
            assert type(master["height"]) is int

    def test_serialization_ends_with_a_newline(self, plan):
        assert serialize_plan(plan).endswith("}\n")


class TestCli:
    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            pytest.param(REAL_EXPERIMENT_TOML, EXPECTED_MASTERS, id="campaign"),
            pytest.param(REAL_PILOT_TOML, PILOT_MASTERS, id="pilot"),
        ],
    )
    def test_writes_the_plan_to_stdout(self, config, expected):
        result = generate(config)

        assert result.returncode == 0, result.stderr

        plan = json.loads(result.stdout)

        assert names(plan) == expected

    def test_out_writes_the_same_bytes_it_would_have_printed(self, tmp_path):
        target = tmp_path / "masters.json"

        printed = generate(REAL_EXPERIMENT_TOML)
        written = generate(REAL_EXPERIMENT_TOML, "--out", str(target))

        assert written.returncode == 0, written.stderr
        assert target.read_text(encoding="utf-8") == printed.stdout

    def test_two_invocations_produce_identical_bytes(self):
        # É o que faz o plano poder ir no argv do SSH sem que dois disparos da
        # preparação escrevam Masters diferentes.
        assert generate(REAL_EXPERIMENT_TOML).stdout == generate(REAL_EXPERIMENT_TOML).stdout

    def test_an_invalid_config_exits_non_zero_naming_the_file(self, tmp_path):
        broken = tmp_path / "broken.toml"
        broken.write_text(REAL_EXPERIMENT_TOML.read_text(encoding="utf-8").replace("seed", "sed"))

        result = generate(broken)

        assert result.returncode != 0
        assert result.stdout == ""
        assert str(broken) in result.stderr

    def test_a_missing_config_exits_non_zero(self, tmp_path):
        result = generate(tmp_path / "absent.toml")

        assert result.returncode != 0
        assert result.stdout == ""


def generate(config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GENERATOR), "--config", str(config), *args],
        capture_output=True,
        text=True,
        check=False,
    )
