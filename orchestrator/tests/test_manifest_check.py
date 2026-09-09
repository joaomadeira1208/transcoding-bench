# O manifesto é o que o gate humano da ADR-0012 confere antes de a campanha
# subir, e é a lista pela qual o bootstrap do encode baixa os Masters: um sha256
# truncado, uma geometria trocada entre os dois vídeos ou um Master a menos não
# estouram em lugar nenhum — viram 972 Execuções medidas sobre a entrada errada.

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
    make_manifest,
    manifest_master,
    real_config,
)
from manifest_check import check_manifest

DERIVED = "bbb_1080p.mkv"
REMUXED = "bbb_2160p.mkv"

CHECKER = REPO_ROOT / "orchestrator" / "validate_manifest.py"


def errors(manifest) -> tuple[str, ...]:
    return check_manifest(manifest, real_config())


def only_error(manifest) -> str:
    found = errors(manifest)

    assert len(found) == 1, found
    return found[0]


class TestTheManifestThePreparationWrites:
    def test_the_factory_manifest_is_accepted(self):
        assert errors(make_manifest()) == ()

    def test_a_manifest_that_is_not_an_object_is_rejected(self):
        assert only_error([]) != ""

    @pytest.mark.parametrize("key", ["schema_version", "versions", "sources", "masters"])
    def test_a_missing_top_level_key_is_rejected_naming_it(self, key):
        manifest = make_manifest()
        del manifest[key]

        assert key in only_error(manifest)


class TestSchemaVersion:
    def test_an_unknown_schema_version_is_rejected(self):
        assert "schema_version" in only_error(make_manifest(schema_version="2"))

    def test_a_schema_version_as_a_number_is_rejected(self):
        # O eixo é string, como no `meta.json`: um `2` do `jq` sem `--arg` passaria
        # por qualquer comparação frouxa.
        assert "schema_version" in only_error(make_manifest(schema_version=1))


class TestVersions:
    def test_the_versions_of_the_image_are_carried_verbatim_and_not_compared(self):
        # A spec não declara versão de ferramenta nenhuma: o campo é o que a
        # imagem gravou, e o checador só exige que seja uma tabela de strings.
        assert errors(make_manifest(versions={"ffmpeg": "n8.0", "libx264": "0"})) == ()

    def test_versions_that_is_not_a_table_is_rejected(self):
        assert "versions" in only_error(make_manifest(versions="n7.1"))

    def test_a_version_that_is_not_a_string_is_rejected_naming_the_tool(self):
        error = only_error(make_manifest(versions={"ffmpeg": 7.1}))

        assert "versions" in error
        assert "ffmpeg" in error

    def test_empty_versions_is_rejected(self):
        assert "versions" in only_error(make_manifest(versions={}))


class TestTheSixMasters:
    def test_a_master_missing_is_rejected_naming_it(self):
        manifest = make_manifest()
        manifest["masters"] = [
            master for master in manifest["masters"] if master["name"] != "tos_720p.mkv"
        ]

        assert "tos_720p.mkv" in only_error(manifest)

    def test_a_master_too_many_is_rejected_naming_it(self):
        manifest = make_manifest()
        extra = dict(manifest_master(manifest, DERIVED), name="bbb_480p.mkv", tier="480p")
        manifest["masters"].append(extra)

        assert "bbb_480p.mkv" in only_error(manifest)

    def test_a_master_listed_twice_is_rejected_naming_it(self):
        # A lista é o que o bootstrap do encode itera: um nome repetido baixa o
        # mesmo objeto duas vezes e esconde o Master que ficou de fora.
        manifest = make_manifest()
        manifest["masters"].append(dict(manifest_master(manifest, DERIVED)))

        assert DERIVED in only_error(manifest)

    def test_masters_that_is_not_a_list_is_rejected(self):
        assert "masters" in only_error(make_manifest(masters={}))

    def test_a_master_that_is_not_an_object_is_rejected(self):
        manifest = make_manifest()
        manifest["masters"][0] = REMUXED

        assert errors(manifest) != ()

    def test_a_master_without_a_name_is_rejected(self):
        manifest = make_manifest()
        del manifest["masters"][0]["name"]

        assert any("name" in error for error in errors(manifest))


class TestWhatTheProbeSaw:
    def test_a_geometry_swapped_between_the_two_videos_is_rejected(self):
        # Os tiers são nominais (ADR-0023): `tos_1080p` é 1920x856, e uma troca
        # entre os dois vídeos sobrevive a qualquer conferência por rótulo.
        manifest = make_manifest()
        bbb = manifest_master(manifest, DERIVED)
        tos = manifest_master(manifest, "tos_1080p.mkv")
        bbb["width"], tos["width"] = tos["width"], bbb["width"]
        bbb["height"], tos["height"] = tos["height"], bbb["height"]

        found = errors(manifest)

        assert any(DERIVED in error and "height" in error for error in found)
        assert any("tos_1080p.mkv" in error and "height" in error for error in found)

    def test_a_divergent_frame_count_is_rejected_naming_the_master_and_the_field(self):
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["frames"] -= 1

        error = only_error(manifest)

        assert DERIVED in error
        assert "frames" in error

    def test_a_frame_rate_as_a_number_is_rejected(self):
        # `30/1` é racional, não float: o `jq` sem `--arg` emite `30` e a
        # comparação com a spec deixa de dizer o que dizia.
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["frame_rate"] = 30

        error = only_error(manifest)

        assert DERIVED in error
        assert "frame_rate" in error

    def test_a_frame_count_as_a_string_is_rejected(self):
        manifest = make_manifest()
        master = manifest_master(manifest, DERIVED)
        master["frames"] = str(master["frames"])

        assert "frames" in only_error(manifest)

    def test_the_wrong_codec_on_a_derived_master_is_rejected(self):
        # O derivado é FFV1 lossless (ADR-0004): um `h264` aqui é a preparação
        # tendo reencodado com perda o que a campanha mede.
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["codec_name"] = "h264"

        error = only_error(manifest)

        assert DERIVED in error
        assert "codec_name" in error

    def test_the_wrong_codec_on_the_remuxed_master_is_rejected(self):
        manifest = make_manifest()
        manifest_master(manifest, REMUXED)["codec_name"] = "ffv1"

        assert REMUXED in only_error(manifest)

    def test_a_divergent_pix_fmt_is_rejected(self):
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["pix_fmt"] = "yuv444p"

        assert "pix_fmt" in only_error(manifest)

    def test_a_master_attributed_to_the_other_video_is_rejected(self):
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["video"] = "tos"

        assert "video" in only_error(manifest)

    def test_a_master_attributed_to_another_tier_is_rejected(self):
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["tier"] = "720p"

        assert "tier" in only_error(manifest)

    @pytest.mark.parametrize(
        "field", ["size", "sha256", "video", "tier", "width", "height", "frames"]
    )
    def test_a_missing_field_is_rejected_naming_the_master_and_the_field(self, field):
        manifest = make_manifest()
        del manifest_master(manifest, DERIVED)[field]

        error = only_error(manifest)

        assert DERIVED in error
        assert field in error


class TestTheObservedSizeAndDigest:
    def test_a_short_sha256_is_rejected(self):
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["sha256"] = "abc123"

        error = only_error(manifest)

        assert DERIVED in error
        assert "sha256" in error

    def test_an_uppercase_sha256_is_rejected(self):
        # O `sha256sum` do bootstrap compara texto: um digest maiúsculo aqui
        # reprova todo Master que a instância baixou íntegro.
        manifest = make_manifest()
        master = manifest_master(manifest, DERIVED)
        master["sha256"] = master["sha256"].upper()

        assert "sha256" in only_error(manifest)

    def test_a_sha256_that_is_not_a_string_is_rejected(self):
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["sha256"] = 0

        assert "sha256" in only_error(manifest)

    @pytest.mark.parametrize("size", [0, -1])
    def test_a_size_that_is_not_positive_is_rejected(self, size):
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["size"] = size

        assert "size" in only_error(manifest)

    def test_a_size_as_a_string_is_rejected(self):
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["size"] = "1073741824"

        assert "size" in only_error(manifest)


class TestTheSources:
    def test_a_source_digest_diverging_from_the_declared_one_is_rejected(self):
        # É o sha256 que a ADR-0004 pinou: divergir dele é o publisher ter
        # trocado o arquivo sob a mesma URL.
        manifest = make_manifest()
        manifest["sources"]["bbb"]["sha256"] = "0" * 64

        error = only_error(manifest)

        assert "bbb" in error
        assert "sha256" in error

    def test_a_source_size_diverging_from_the_declared_one_is_rejected(self):
        manifest = make_manifest()
        manifest["sources"]["bbb"]["size"] += 1

        assert "size" in only_error(manifest)

    @pytest.mark.parametrize("field", ["url", "file"])
    def test_a_source_naming_another_file_is_rejected(self, field):
        manifest = make_manifest()
        manifest["sources"]["bbb"][field] = "other"

        error = only_error(manifest)

        assert "bbb" in error
        assert field in error

    def test_a_video_missing_from_sources_is_rejected_naming_it(self):
        manifest = make_manifest()
        del manifest["sources"]["tos"]

        assert "tos" in only_error(manifest)

    def test_a_video_that_the_spec_does_not_declare_is_rejected(self):
        manifest = make_manifest()
        manifest["sources"]["sintel"] = dict(manifest["sources"]["bbb"])

        assert "sintel" in only_error(manifest)

    def test_sources_that_is_not_a_table_is_rejected(self):
        assert "sources" in only_error(make_manifest(sources=[]))


class TestPurity:
    def test_the_checker_reports_every_divergence_at_once(self):
        # O gate é humano e roda uma vez: parar no primeiro erro faria o
        # pesquisador descobrir os seis defeitos em seis rodadas.
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["frames"] = 1
        manifest_master(manifest, REMUXED)["codec_name"] = "ffv1"

        assert len(errors(manifest)) == 2

    def test_the_checker_does_not_mutate_the_manifest_it_reads(self):
        manifest = make_manifest()
        before = json.dumps(manifest, sort_keys=True)

        errors(manifest)

        assert json.dumps(manifest, sort_keys=True) == before


class TestCli:
    def test_the_factory_manifest_is_accepted_as_a_file(self, tmp_path):
        result = validate(write(tmp_path, make_manifest()))

        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    def test_a_broken_manifest_exits_non_zero_naming_the_master_and_the_field(self, tmp_path):
        manifest = make_manifest()
        manifest_master(manifest, DERIVED)["frames"] += 1

        result = validate(write(tmp_path, manifest))

        assert result.returncode != 0
        assert DERIVED in result.stderr
        assert "frames" in result.stderr

    def test_a_manifest_that_is_not_json_exits_non_zero(self, tmp_path):
        path = tmp_path / "manifest.json"
        path.write_text("{", encoding="utf-8")

        result = validate(path)

        assert result.returncode != 0

    def test_a_missing_manifest_exits_non_zero(self, tmp_path):
        result = validate(tmp_path / "absent.json")

        assert result.returncode != 0

    def test_a_missing_config_exits_non_zero(self, tmp_path):
        result = validate(write(tmp_path, make_manifest()), config=tmp_path / "absent.toml")

        assert result.returncode != 0

    def test_a_config_the_masters_plan_rejects_exits_non_zero_naming_it(self, tmp_path):
        # Uma spec que valida mas não dá geometria 4K a um vídeo só estoura ao
        # projetar o plano, que é dentro do checker: fora do `try`, o pesquisador
        # recebe um traceback no lugar da linha que nomeia o arquivo.
        without_4k = tmp_path / "no-4k.toml"
        without_4k.write_text(
            "".join(
                line
                for line in REAL_PILOT_TOML.read_text(encoding="utf-8").splitlines(keepends=True)
                if not line.startswith("2160p = ")
            ),
            encoding="utf-8",
        )

        result = validate(write(tmp_path, make_manifest()), config=without_4k)

        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert str(without_4k) in result.stderr


def write(tmp_path: Path, manifest: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def validate(manifest: Path, config: Path = REAL_EXPERIMENT_TOML):
    return subprocess.run(
        [sys.executable, str(CHECKER), str(manifest), "--config", str(config)],
        capture_output=True,
        text=True,
        check=False,
    )
