# Smoke do `masters/prepare.sh`: a preparação dos seis Masters de verdade, do
# download ao manifesto, com os shims do `curl`, do `unzip` e do `ffprobe` ao
# lado dos do encode (ADR-0022).
#
# A asserção central é a mesma do `run_scenario.sh` — a cadeia `experiment.toml`
# → gerador → plano → `jq` → argv —, e aqui ela guarda o passo que roda uma vez
# e cujo erro é o mais caro do projeto: um Master com a geometria errada vira
# seis Execuções medidas sobre a entrada errada, e ninguém olha os pixels.

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    BUCKET,
    EXPERIMENT,
    SOURCE_SHA256,
    SOURCE_SIZE,
    VERSIONS,
    Preparation,
    masters_of,
    probe_response,
    validate_manifest_with_cli,
)
from test_run_scenario import contains_subsequence, value_after

REMUXED_TIER = "2160p"
DERIVED_CODEC = "ffv1"

# Não sai do `[encode]` do TOML, que é o do scaling da Execução: o Lanczos do
# downscale dos Masters é decisão própria da ADR-0004.
SCALE_FLAGS = "lanczos"

MANIFEST_PREFIX = "masters/"
MANIFEST_KEY = f"{MANIFEST_PREFIX}manifest.json"

MASTER_FIELDS = frozenset(
    {
        "name",
        "size",
        "sha256",
        "video",
        "tier",
        "width",
        "height",
        "codec_name",
        "pix_fmt",
        "frame_rate",
        "frames",
    }
)

DERIVED_TIERS = tuple(
    dict.fromkeys(
        pair["input_res"] for pair in EXPERIMENT["pair"] if pair["input_res"] != REMUXED_TIER
    )
)


def master_name(slug: str, tier: str) -> str:
    return f"{slug}_{tier}.mkv"


def video_record(slug: str) -> dict[str, Any]:
    return next(video for video in EXPERIMENT["video"] if video["slug"] == slug)


SPEC_MASTERS = [
    (video["slug"], tier)
    for video in EXPERIMENT["video"]
    for tier in (REMUXED_TIER, *DERIVED_TIERS)
]

SPEC_DERIVED = [(slug, tier) for slug, tier in SPEC_MASTERS if tier != REMUXED_TIER]


def encode_argv(preparation: Preparation, name: str) -> list[str]:
    """O argv da invocação do `ffmpeg` que produziu o Master `name`."""
    return next(argv for argv in preparation.argv("ffmpeg") if Path(argv[-1]).name == name)


@pytest.fixture(scope="session")
def preparation(masters_plan, prepare) -> Preparation:
    """Os seis Masters da campanha preparados uma vez, do download ao manifesto."""
    return prepare(masters_plan)


@pytest.fixture(scope="session")
def a_source_that_arrived_corrupt(masters_plan, prepare) -> Preparation:
    """O sha256 que o plano pede não é o do arquivo que chegou."""
    plan = copy.deepcopy(masters_plan)
    plan["videos"][0]["source"]["sha256"] = "0" * 64
    return prepare(plan)


@pytest.fixture(scope="session")
def a_master_that_came_out_wrong(masters_plan, prepare) -> Preparation:
    """O último dos seis sai com a largura errada: os cinco anteriores estão prontos."""
    master = masters_of(masters_plan)[-1]
    response = probe_response(master)
    response["streams"][0]["width"] = master["width"] + 2
    return prepare(masters_plan, {master["name"]: response})


class TestPreparation:
    def test_the_preparation_succeeds(self, preparation):
        assert preparation.returncode == 0, preparation.stderr

    def test_each_video_is_downloaded_unzipped_and_turned_into_its_three_masters(self, preparation):
        relevant = [tool for tool in preparation.sequence() if tool in {"curl", "unzip", "ffmpeg"}]
        per_video = ["curl", "unzip"] + ["ffmpeg"] * (1 + len(DERIVED_TIERS))

        assert relevant == per_video * len(EXPERIMENT["video"])

    def test_nothing_is_uploaded_before_the_six_are_probed(self, preparation):
        sequence = preparation.sequence()
        last_probe = max(index for index, tool in enumerate(sequence) if tool == "ffprobe")

        assert sequence.count("ffprobe") == len(SPEC_MASTERS)
        assert last_probe < sequence.index("aws")


class TestArgv:
    @pytest.mark.parametrize("slug", [video["slug"] for video in EXPERIMENT["video"]])
    def test_the_4k_master_is_the_source_remuxed_without_re_encode(self, preparation, slug):
        argv = encode_argv(preparation, master_name(slug, REMUXED_TIER))

        assert value_after(argv, "-i").endswith(f"/{video_record(slug)['source']['file']}")
        assert contains_subsequence(argv, ["-c", "copy"])
        assert argv[-1].endswith(f"/{master_name(slug, REMUXED_TIER)}")

    @pytest.mark.parametrize(("slug", "tier"), SPEC_DERIVED)
    def test_each_derived_is_a_lanczos_ffv1_downscale_of_the_4k(self, preparation, slug, tier):
        geometry = video_record(slug)["geometry"][tier]
        argv = encode_argv(preparation, master_name(slug, tier))

        assert value_after(argv, "-i").endswith(f"/{master_name(slug, REMUXED_TIER)}")
        assert value_after(argv, "-vf") == (
            f"scale={geometry['width']}:{geometry['height']}:flags={SCALE_FLAGS}"
        )
        assert value_after(argv, "-c:v") == DERIVED_CODEC
        assert value_after(argv, "-pix_fmt") == EXPERIMENT["encode"]["pix_fmt"]
        assert "-an" in argv

    @pytest.mark.parametrize(("slug", "tier"), SPEC_MASTERS)
    def test_each_master_is_probed_by_packet_count(self, preparation, slug, tier):
        name = master_name(slug, tier)
        argv = next(argv for argv in preparation.argv("ffprobe") if Path(argv[-1]).name == name)

        assert value_after(argv, "-select_streams") == "v:0"
        assert "-count_packets" in argv
        assert "nb_read_packets" in value_after(argv, "-show_entries")


class TestUploads:
    def test_the_six_masters_of_the_spec_land_under_the_masters_prefix(
        self, preparation, list_objects
    ):
        expected = {f"{MANIFEST_PREFIX}{master_name(slug, tier)}" for slug, tier in SPEC_MASTERS}

        assert set(list_objects(preparation, MANIFEST_PREFIX)) == expected | {MANIFEST_KEY}

    def test_the_manifest_is_the_last_object_written(self, preparation):
        last = preparation.argv("aws")[-1]

        assert last[:2] == ["s3", "cp"]
        assert last[3:] == [f"s3://{BUCKET}/{MANIFEST_KEY}"]

    def test_nothing_lands_outside_the_masters_prefix(self, preparation, list_objects):
        keys = list_objects(preparation, "")

        assert all(key.startswith(MANIFEST_PREFIX) for key in keys)
        assert len(keys) == len(SPEC_MASTERS) + 1


class TestManifest:
    def test_accepted_by_the_checker_cli(self, preparation, masters_toml):
        result = validate_manifest_with_cli(preparation.manifest_path(), masters_toml)

        assert result.returncode == 0, result.stderr

    def test_the_versions_are_the_image_file_verbatim(self, preparation):
        assert preparation.manifest()["versions"] == VERSIONS

    def test_every_master_of_the_spec_carries_the_eleven_fields(self, preparation):
        masters = preparation.manifest()["masters"]

        assert [master["name"] for master in masters] == [
            master_name(slug, tier) for slug, tier in SPEC_MASTERS
        ]
        for master in masters:
            assert set(master) == MASTER_FIELDS, master["name"]
            assert master["size"] > 0, master["name"]

    def test_the_sources_are_what_the_download_observed(self, preparation):
        sources = preparation.manifest()["sources"]

        for video in EXPERIMENT["video"]:
            observed = sources[video["slug"]]

            assert observed["url"] == video["source"]["url"]
            assert observed["file"] == video["source"]["file"]
            assert observed["size"] == SOURCE_SIZE
            assert observed["sha256"] == SOURCE_SHA256


class TestDivergence:
    def test_a_source_that_does_not_match_the_plan_stops_naming_the_video(
        self, a_source_that_arrived_corrupt
    ):
        stderr = a_source_that_arrived_corrupt.stderr
        video = EXPERIMENT["video"][0]

        assert a_source_that_arrived_corrupt.returncode != 0
        assert video["slug"] in stderr
        assert video["source"]["file"] in stderr

    def test_the_corrupt_source_never_reaches_the_bucket(self, a_source_that_arrived_corrupt):
        assert "aws" not in a_source_that_arrived_corrupt.sequence()
        assert not a_source_that_arrived_corrupt.bucket_dir().exists()

    def test_a_master_that_does_not_match_the_plan_stops_naming_it_and_the_field(
        self, a_master_that_came_out_wrong, masters_plan
    ):
        stderr = a_master_that_came_out_wrong.stderr

        assert a_master_that_came_out_wrong.returncode != 0
        assert masters_of(masters_plan)[-1]["name"] in stderr
        assert "width" in stderr

    def test_not_even_the_five_that_came_out_right_are_uploaded(self, a_master_that_came_out_wrong):
        assert "aws" not in a_master_that_came_out_wrong.sequence()
        assert not a_master_that_came_out_wrong.bucket_dir().exists()
