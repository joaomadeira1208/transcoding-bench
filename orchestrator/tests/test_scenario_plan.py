# Testes do plano canônico de Cenários.
#
# O critério da ADR-0022 é falha silenciosa, e a matriz do Experimento é o caso
# mais caro dele: um bloco faltando, um par trocado ou uma `scenario_id`
# duplicada não estouram em lugar nenhum — produzem um plano perfeitamente
# executável que gasta ~46h de compute medindo a coisa errada.
#
# Tudo aqui assere **comportamento externo** — o plano emitido —, nunca a
# estrutura interna do gerador: nenhuma asserção sobre nomes de funções
# auxiliares, ordem de laços ou formato intermediário.

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
from conftest import REAL_EXPERIMENT_TOML, REPO_ROOT, make_instance, real_config
from experiment_config import validate_config
from scenario_plan import build_canonical_plan, serialize_plan

# Cardinalidade da spec (ADR-0003): 3 codecs x 9 pares x 2 vídeos x 3 instâncias.
EXPECTED_BLOCKS = 162
EXPECTED_RUNS = EXPECTED_BLOCKS * 6
EXPECTED_REPLICATIONS = EXPECTED_BLOCKS * 5

# Transcrição deliberada da spec (ADR-0004), duplicada de propósito em relação ao
# teste da configuração: contagem é invariante sob **substituição** de par, e o
# que se verifica aqui é o plano, não o TOML. É a mesma política de "regra
# duplicada, não código compartilhado" que a ADR-0019 adota para o dedup.
EXPECTED_PAIRS = {
    ("2160p", "2160p"),
    ("2160p", "1080p"),
    ("2160p", "720p"),
    ("2160p", "480p"),
    ("1080p", "1080p"),
    ("1080p", "720p"),
    ("1080p", "480p"),
    ("720p", "720p"),
    ("720p", "480p"),
}

SCENARIO_ID = re.compile(
    r"^(?P<encoder>[a-z0-9]+)_(?P<input_res>[0-9]+p)_(?P<output_res>[0-9]+p)"
    r"_(?P<video>[a-z0-9]+)_(?P<instance>[a-z0-9]+)_(?P<suffix>warmup|rep[1-9][0-9]*)$"
)

GENERATOR = REPO_ROOT / "orchestrator" / "generate_scenarios.py"


@pytest.fixture(scope="module")
def plan() -> dict:
    return build_canonical_plan(real_config())


def all_runs(plan: dict) -> list[dict]:
    return [run for block in plan["blocks"] for run in block["runs"]]


def scenario_of(block: dict) -> tuple[str, str, str, str, str]:
    return (
        block["encoder"],
        block["input_res"],
        block["output_res"],
        block["video"],
        block["instance"],
    )


class TestCardinality:
    def test_counts_of_the_spec(self, plan):
        runs = all_runs(plan)

        assert len(plan["blocks"]) == EXPECTED_BLOCKS
        assert len(runs) == EXPECTED_RUNS
        assert sum(1 for run in runs if run["warmup"] is False) == EXPECTED_REPLICATIONS

    def test_no_duplicate_scenario_id(self, plan):
        ids = [run["scenario_id"] for run in all_runs(plan)]

        assert len(set(ids)) == len(ids)

    def test_every_cell_of_the_matrix_appears_exactly_once(self, plan):
        # Contagem sozinha não pega troca de célula: 162 blocos com um Cenário
        # duplicado e outro ausente contam igual.
        config = real_config()
        expected = {
            (codec.slug, pair.input_res, pair.output_res, video.slug, instance.id)
            for codec in config.codecs
            for pair in config.pairs
            for video in config.videos
            for instance in config.instances
        }

        declared = [scenario_of(block) for block in plan["blocks"]]

        assert set(declared) == expected
        assert len(declared) == len(expected)

    def test_seed_echoed_from_the_toml(self, plan):
        config = real_config()

        assert plan["seed"] == config.seed
        assert {run["seed"] for run in all_runs(plan)} == {config.seed}


class TestMatrixOfTheSpec:
    def test_blocks_cover_exactly_the_nine_pairs(self, plan):
        assert {(b["input_res"], b["output_res"]) for b in plan["blocks"]} == EXPECTED_PAIRS

    def test_each_pair_appears_in_every_codec_video_and_instance(self, plan):
        # 3 codecs x 2 vídeos x 3 instâncias = 18 blocos por par. Um par que
        # faltasse só para um codec manteria o conjunto acima intacto.
        counts: dict[tuple[str, str], int] = {}
        for block in plan["blocks"]:
            key = (block["input_res"], block["output_res"])
            counts[key] = counts.get(key, 0) + 1

        assert set(counts.values()) == {18}

    def test_no_run_upscales(self, plan):
        # Propriedade estrutural sobre a geometria, nunca sobre o rótulo do tier
        # (ADR-0023): `1080p -> 720p` parece downscale sob qualquer geometria,
        # inclusive uma errada.
        geometry = {video.slug: video.geometry for video in real_config().videos}

        for block in plan["blocks"]:
            source = geometry[block["video"]][block["input_res"]]
            for run in block["runs"]:
                assert run["output_width"] <= source.width
                assert run["output_height"] <= source.height


class TestBlockStructure:
    def test_six_runs_per_block_with_the_warmup_first(self, plan):
        for block in plan["blocks"]:
            flags = [run["warmup"] for run in block["runs"]]

            assert flags == [True, False, False, False, False, False]

    def test_warmup_is_a_real_json_boolean(self, plan):
        # O modo de falha que a ADR-0019/0022 mais teme: `"warmup": "false"` é
        # uma string truthy, e o filtro canônico `warmup == false` dos leitores
        # deixaria o warm-up entrar na média em silêncio.
        reloaded = json.loads(serialize_plan(plan))

        for run in all_runs(reloaded):
            assert type(run["warmup"]) is bool

    def test_block_carries_the_scenario_fields_and_its_runs(self, plan):
        # O bloco é o Cenário do CONTEXT.md mais os runs, e nada além: o
        # `instance_type` da AWS não entra porque ninguém que lê o plano o
        # consome, e a fatia por arquitetura é chaveada pelo id curto (decisão D8).
        for block in plan["blocks"]:
            assert set(block) == {
                "codec",
                "encoder",
                "input_res",
                "output_res",
                "video",
                "instance",
                "runs",
            }


class TestScenarioId:
    def test_format_of_every_scenario_id(self, plan):
        for block in plan["blocks"]:
            for run in block["runs"]:
                match = SCENARIO_ID.match(run["scenario_id"])

                assert match is not None, run["scenario_id"]
                assert match.group("encoder", "input_res", "output_res", "video", "instance") == (
                    scenario_of(block)
                )

    def test_suffixes_of_a_block(self, plan):
        for block in plan["blocks"]:
            suffixes = [run["scenario_id"].rsplit("_", 1)[1] for run in block["runs"]]

            assert suffixes == ["warmup", "rep1", "rep2", "rep3", "rep4", "rep5"]

    def test_the_discarded_run_is_never_rep0(self, plan):
        # `rep0` convidaria a entrar numa média por engano (ADR-0019): warm-up
        # não é Replicação.
        assert not any(run["scenario_id"].endswith("_rep0") for run in all_runs(plan))

    def test_a_known_scenario_id_matches_the_canonical_example(self, plan):
        # O exemplo do CONTEXT.md, literalmente.
        assert "libx264_2160p_1080p_bbb_c7g_rep1" in {run["scenario_id"] for run in all_runs(plan)}


class TestRunParameters:
    def test_a_run_carries_exactly_the_fields_of_the_contract(self, plan):
        # O run tem de bastar sozinho: o `run_scenario.sh` recebe este objeto e o
        # lê com `jq`, sem derivar nada (ADR-0019, decisão D5). Um campo que
        # sumisse daqui viraria um flag perdido no argv do FFmpeg, e o vídeo
        # sairia perfeitamente válido com parâmetro errado.
        for run in all_runs(plan):
            assert set(run) == {
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
            }

    def test_a_known_run_repeats_the_identity_of_its_block(self, plan):
        # O bloco existe para quem raciocina sobre Cenários; o run, para quem só
        # recebe o objeto de run. Os dois têm de contar a mesma história.
        run = one_run(plan, "libx265_2160p_720p_tos_c7i_rep3")

        assert (run["codec"], run["encoder"]) == ("h265", "libx265")
        assert (run["input_res"], run["output_res"]) == ("2160p", "720p")
        assert (run["video"], run["instance"]) == ("tos", "c7i")
        assert run["warmup"] is False

    def test_every_run_carries_the_output_geometry_of_its_tier(self, plan):
        geometry = {video.slug: video.geometry for video in real_config().videos}

        for block in plan["blocks"]:
            target = geometry[block["video"]][block["output_res"]]
            for run in block["runs"]:
                assert (run["output_width"], run["output_height"]) == (target.width, target.height)

    def test_every_run_carries_the_encode_record_of_its_codec(self, plan):
        codecs = {c.slug: c for c in real_config().codecs}

        for run in all_runs(plan):
            codec = codecs[run["encoder"]]

            assert (run["codec"], run["preset"], run["crf"]) == (
                codec.codec,
                codec.preset,
                codec.crf,
            )
            assert run["encoder_args"] == list(codec.encoder_args)

    def test_every_run_carries_the_fixed_encode_params(self, plan):
        encode = real_config().encode

        for run in all_runs(plan):
            assert run["threads"] == encode.threads
            assert run["gop_size"] == encode.gop_size
            assert run["pix_fmt"] == encode.pix_fmt
            assert run["strip_audio"] == encode.strip_audio
            assert run["container"] == encode.container
            assert run["scale_flags"] == encode.scale_flags

    def test_every_run_names_the_master_of_its_input_res(self, plan):
        # Basename apenas: o prefixo S3 vem por argumento de bootstrap (ADR-0011),
        # e a instância nunca decide path.
        for block in plan["blocks"]:
            expected = f"{block['video']}_{block['input_res']}.mkv"
            for run in block["runs"]:
                assert run["master"] == expected
                assert "/" not in run["master"]


class TestDeterminism:
    def test_generating_twice_produces_identical_bytes(self):
        first = serialize_plan(build_canonical_plan(real_config()))
        second = serialize_plan(build_canonical_plan(real_config()))

        assert first == second

    def test_the_plan_carries_no_timestamp_or_environment_field(self, plan):
        assert set(plan) == {"schema_version", "seed", "blocks"}
        assert "generated_at" not in serialize_plan(plan)

    def test_serialization_ends_with_a_newline(self, plan):
        assert serialize_plan(plan).endswith("}\n")

    def test_schema_version_is_a_string(self, plan):
        # Espelha o `meta.json` (ADR-0019/decisão D6): a versão é literal de
        # texto, para que o bash a ecoe sem remarshalling.
        assert plan["schema_version"] == "1"


class TestDeclarationOrder:
    def test_blocks_follow_the_declaration_order_of_the_toml(self, make_raw_config):
        # A ordem pré-shuffle é a ordem de declaração, percorrida em ordem fixa
        # (decisão D7). É sobre ela que o embaralhamento do ticket seguinte opera,
        # então ela precisa ser determinística antes de ser embaralhada.
        raw = make_raw_config(
            instance=[
                make_instance(id="c7g", instance_type="c7g.xlarge", arch="arm64"),
                make_instance(id="c7i", instance_type="c7i.xlarge", arch="x86_64"),
            ]
        )
        blocks = build_canonical_plan(validate_config(raw))["blocks"]

        assert [scenario_of(b) for b in blocks] == [
            ("libx264", "1080p", "1080p", "bbb", "c7g"),
            ("libx264", "1080p", "720p", "bbb", "c7g"),
            ("libx264", "1080p", "1080p", "bbb", "c7i"),
            ("libx264", "1080p", "720p", "bbb", "c7i"),
        ]


class TestCli:
    def test_writes_the_canonical_plan_to_the_output_directory(self, tmp_path):
        # Casca de I/O verificada como caixa-preta, depois do fato (ADR-0022).
        first = tmp_path / "first"
        second = tmp_path / "second"

        for out in (first, second):
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--config",
                    str(REAL_EXPERIMENT_TOML),
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            assert result.returncode == 0, result.stderr

        written = (first / "canonical.json").read_bytes()

        assert written == (second / "canonical.json").read_bytes()
        assert len(json.loads(written)["blocks"]) == EXPECTED_BLOCKS


def one_run(plan: dict, scenario_id: str) -> dict:
    matches = [run for run in all_runs(plan) if run["scenario_id"] == scenario_id]

    assert len(matches) == 1, scenario_id
    return matches[0]
