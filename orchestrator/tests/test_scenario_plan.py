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

import itertools
import json
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import (
    REAL_EXPERIMENT_TOML,
    REPO_ROOT,
    make_codec,
    make_instance,
    make_video,
    real_config,
)
from experiment_config import validate_config
from scenario_plan import build_canonical_plan, build_instance_slices, serialize_plan

# Cardinalidade da spec (ADR-0003): 3 codecs x 9 pares x 2 vídeos x 3 instâncias.
EXPECTED_COMBINATIONS = 54
EXPECTED_BLOCKS = 162
EXPECTED_RUNS = EXPECTED_BLOCKS * 6
EXPECTED_REPLICATIONS = EXPECTED_BLOCKS * 5

# Por fatia: as 54 combinações de uma arquitetura, x6 runs, x5 replicações.
EXPECTED_SLICE_BLOCKS = EXPECTED_COMBINATIONS
EXPECTED_SLICE_RUNS = EXPECTED_SLICE_BLOCKS * 6
EXPECTED_SLICE_REPLICATIONS = EXPECTED_SLICE_BLOCKS * 5

# Os três ids curtos da spec (ADR-0001), transcritos como o conjunto de pares
# acima. A ordem entre eles não é requisito de ADR nenhum — arch-major diz que os
# blocos de cada arquitetura são contíguos, não qual vem primeiro —, então quem
# os usa compara conjuntos.
EXPECTED_INSTANCES = ("c7g", "c7i", "c7a")

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

# Transcrição da ADR-0006, duplicada em relação ao teste da configuração pelo
# mesmo motivo que os pares acima: lá se verifica o TOML, aqui o plano. A ordem
# faz parte do que se congela — é ela que vai para o `-e` do `perf stat`.
EXPECTED_PMU_EVENTS = [
    "cycles",
    "instructions",
    "cache-references",
    "cache-misses",
    "branch-instructions",
    "branch-misses",
    "task-clock",
    "context-switches",
    "cpu-migrations",
    "page-faults",
]

SCENARIO_ID = re.compile(
    r"^(?P<encoder>[a-z0-9]+)_(?P<input_res>[0-9]+p)_(?P<output_res>[0-9]+p)"
    r"_(?P<video>[a-z0-9]+)_(?P<instance>[a-z0-9]+)_(?P<suffix>warmup|rep[1-9][0-9]*)$"
)

GENERATOR = REPO_ROOT / "orchestrator" / "generate_scenarios.py"

# Nome do canônico transcrito, não importado: o teste do CLI é caixa-preta, e o
# nome é contrato do layout de prefixos da ADR-0011.
CANONICAL_FILENAME = "canonical.json"


@pytest.fixture(scope="module")
def plan() -> dict:
    return build_canonical_plan(real_config())


@pytest.fixture(scope="module")
def slices(plan: dict) -> dict[str, dict]:
    return build_instance_slices(plan)


def all_runs(plan: dict) -> list[dict]:
    return [run for block in plan["blocks"] for run in block["runs"]]


def scenario_ids(plan: dict) -> set[str]:
    return {run["scenario_id"] for run in all_runs(plan)}


def block_key(block: dict) -> str:
    """Identidade de um bloco: a `scenario_id` do seu warm-up, única no plano."""
    return block["runs"][0]["scenario_id"]


def blocks_of(plan: dict, instance: str) -> list[dict]:
    return [block for block in plan["blocks"] if block["instance"] == instance]


def combination_of(block: dict) -> tuple[str, str, str, str]:
    """O Cenário sem o eixo `instância` — o que o shuffle ordena (decisão D1)."""
    return (block["encoder"], block["input_res"], block["output_res"], block["video"])


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
                "bitstream_muxer",
                "pmu_events",
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
            assert run["bitstream_muxer"] == codec.bitstream_muxer

    def test_every_run_carries_the_fixed_encode_params(self, plan):
        encode = real_config().encode

        for run in all_runs(plan):
            assert run["threads"] == encode.threads
            assert run["gop_size"] == encode.gop_size
            assert run["pix_fmt"] == encode.pix_fmt
            assert run["strip_audio"] == encode.strip_audio
            assert run["container"] == encode.container
            assert run["scale_flags"] == encode.scale_flags

    def test_every_run_carries_the_pmu_events_of_the_spec(self, plan):
        # Os dez eventos viajam em cada run como todo o resto que o bash copia
        # (decisão D5): o `run_scenario.sh` recebe o objeto de run e monta o `-e`
        # do `perf stat` sem conhecer a ADR-0006.
        events = list(real_config().instrumentation.pmu_events)

        for run in all_runs(plan):
            assert run["pmu_events"] == events

    def test_a_known_run_carries_the_bitstream_muxer_and_the_pmu_events(self, plan):
        # Sobre um Cenário conhecido, e no codec cujo muxer menos se parece com o
        # nome do encoder: `libsvtav1` → `obu` não sai de manipulação de string
        # nenhuma, que é exatamente o motivo de o campo ser dado declarado. Os
        # eventos são conferidos contra a ADR-0006 — os testes acima os conferem
        # contra o TOML, e o que se quer aqui é que o TOML seja o da ADR.
        run = one_run(plan, "libsvtav1_2160p_480p_bbb_c7a_rep2")

        assert run["bitstream_muxer"] == "obu"
        assert run["pmu_events"] == EXPECTED_PMU_EVENTS

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

    def test_the_plan_does_not_depend_on_the_global_random_state(self):
        # O embaralhamento usa uma instância própria de gerador semeada pela seed
        # do TOML (decisão D7). Se ele lesse o estado global do módulo `random`,
        # qualquer outra chamada no processo — hoje nenhuma, amanhã qualquer uma
        # — mudaria o plano, e o determinismo prometido ao `diff` sumiria sem
        # ninguém notar.
        random.seed(1)
        first = serialize_plan(build_canonical_plan(real_config()))
        random.seed(2)
        for _ in range(10):
            random.random()
        second = serialize_plan(build_canonical_plan(real_config()))

        assert first == second

    def test_the_plan_does_not_consume_the_global_random_stream(self):
        # A recíproca: gerar o plano não pode mexer no estado global, ou o
        # gerador passaria a interferir em quem o importar.
        random.seed(7)
        expected = [random.random() for _ in range(3)]

        random.seed(7)
        build_canonical_plan(real_config())

        assert [random.random() for _ in range(3)] == expected

    def test_the_plan_carries_no_timestamp_or_environment_field(self, plan):
        assert set(plan) == {"schema_version", "seed", "blocks"}
        assert "generated_at" not in serialize_plan(plan)

    def test_serialization_ends_with_a_newline(self, plan):
        assert serialize_plan(plan).endswith("}\n")

    def test_schema_version_is_a_string(self, plan):
        # Espelha o `meta.json` (ADR-0019/decisão D6): a versão é literal de
        # texto, para que o bash a ecoe sem remarshalling.
        assert plan["schema_version"] == "1"


class TestSeededShuffle:
    def test_the_three_architectures_see_the_same_order_of_combinations(self, plan):
        # A propriedade que a ADR-0010 compra, asserida como invariante sobre a
        # matriz real: com a mesma ordem nas três instâncias, efeitos temporais do
        # host atingem os mesmos Cenários em todas as arquiteturas e se cancelam
        # na comparação cross-arch. É por isso que o shuffle roda sobre as 54
        # combinações e o eixo `instância` entra depois (decisão D1).
        orders = {
            instance: [combination_of(b) for b in blocks_of(plan, instance)]
            for instance in EXPECTED_INSTANCES
        }

        assert len(set(map(tuple, orders.values()))) == 1
        assert len(orders["c7g"]) == EXPECTED_COMBINATIONS

    def test_the_canonical_is_arch_major_with_contiguous_slices(self, plan):
        # Decisão D8: os 54 blocos de cada arquitetura são contíguos, o que torna
        # o fatiamento do ticket seguinte uma fatia contígua e mantém
        # trivialmente a invariante de ordem relativa. Um canônico intercalado
        # passaria em todos os outros testes deste arquivo.
        instances = [block["instance"] for block in plan["blocks"]]
        runs = [instance for instance, _ in itertools.groupby(instances)]

        assert runs == ["c7g", "c7i", "c7a"]

    def test_the_order_is_not_the_declaration_order_of_the_toml(self, plan):
        # Sem isto, um shuffle que virasse no-op (seed trocada por constante,
        # `shuffle` removido numa refatoração) passaria em tudo o mais: ordem
        # replicada nas três arquiteturas e determinismo continuariam verdes.
        config = real_config()
        declared = [
            (codec.slug, pair.input_res, pair.output_res, video.slug)
            for codec in config.codecs
            for pair in config.pairs
            for video in config.videos
        ]

        assert [combination_of(b) for b in blocks_of(plan, "c7g")] != declared

    def test_the_order_is_the_shuffle_of_the_seed_of_the_toml(self, make_raw_config):
        # Golden inline (ADR-0022): a sequência congelada sobre um
        # `experiment.toml` mínimo. Nada de `scenarios.json` commitado — o que se
        # quer pegar é `random.shuffle` mudando entre versões de Python, ou
        # alguém reordenando o produto cartesiano sem perceber. Congelar a ordem
        # **é** o requisito: o cancelamento de efeitos temporais da ADR-0010
        # depende de ela não mudar por baixo do experimento.
        raw = make_raw_config(
            experiment={"seed": 20260808, "replications": 5, "warmup_runs": 1},
            codec=[make_codec(slug="libx264"), make_codec(slug="libx265", codec="h265")],
            video=[make_video(slug="bbb"), make_video(slug="tos")],
            instance=[
                make_instance(id="c7g", instance_type="c7g.xlarge", arch="arm64"),
                make_instance(id="c7i", instance_type="c7i.xlarge", arch="x86_64"),
            ],
        )

        blocks = build_canonical_plan(validate_config(raw))["blocks"]

        assert [block["runs"][0]["scenario_id"] for block in blocks] == [
            "libx264_1080p_720p_tos_c7g_warmup",
            "libx265_1080p_1080p_tos_c7g_warmup",
            "libx265_1080p_720p_tos_c7g_warmup",
            "libx264_1080p_1080p_tos_c7g_warmup",
            "libx265_1080p_1080p_bbb_c7g_warmup",
            "libx264_1080p_720p_bbb_c7g_warmup",
            "libx265_1080p_720p_bbb_c7g_warmup",
            "libx264_1080p_1080p_bbb_c7g_warmup",
            "libx264_1080p_720p_tos_c7i_warmup",
            "libx265_1080p_1080p_tos_c7i_warmup",
            "libx265_1080p_720p_tos_c7i_warmup",
            "libx264_1080p_1080p_tos_c7i_warmup",
            "libx265_1080p_1080p_bbb_c7i_warmup",
            "libx264_1080p_720p_bbb_c7i_warmup",
            "libx265_1080p_720p_bbb_c7i_warmup",
            "libx264_1080p_1080p_bbb_c7i_warmup",
        ]

    def test_a_different_seed_produces_a_different_order(self, make_raw_config):
        def order(seed: int) -> list[tuple[str, str, str, str]]:
            raw = make_raw_config(
                experiment={"seed": seed, "replications": 5, "warmup_runs": 1},
                codec=[make_codec(slug="libx264"), make_codec(slug="libx265", codec="h265")],
                video=[make_video(slug="bbb"), make_video(slug="tos")],
            )
            return [combination_of(b) for b in build_canonical_plan(validate_config(raw))["blocks"]]

        assert order(1) != order(2)
        assert sorted(order(1)) == sorted(order(2))


class TestSlices:
    # Invariantes escritas à mão, nunca golden: o que se quer garantir é a
    # *relação* entre canônico e fatias — nenhuma célula da matriz de comparação
    # duplicada ou vazia —, e uma lista congelada de `scenario_id` não expressa
    # relação nenhuma. O golden do repositório é um só, o do shuffle, porque lá
    # congelar a ordem *é* o requisito.

    def test_one_slice_per_instance_keyed_by_the_short_id(self, plan, slices):
        # Id curto, nunca o `instance_type`: é o que a decisão D8 fixou para o
        # nome do artefato (`c7g.json`, não `c7g.xlarge.json`), e é a mesma
        # divergência de string (`c7g` vs `c7g.xlarge`) que a ADR-0019 evita ao
        # tirar a seleção do bash.
        assert set(slices) == set(EXPECTED_INSTANCES)

    def test_counts_of_each_slice(self, slices):
        for instance, plan_slice in slices.items():
            runs = all_runs(plan_slice)

            assert len(plan_slice["blocks"]) == EXPECTED_SLICE_BLOCKS, instance
            assert len(runs) == EXPECTED_SLICE_RUNS, instance
            assert sum(1 for run in runs if run["warmup"] is False) == EXPECTED_SLICE_REPLICATIONS

    def test_each_slice_carries_only_blocks_of_its_architecture(self, slices):
        # A Instância roda **todo** bloco do arquivo que recebeu (ADR-0019): um
        # bloco alheio numa fatia não é filtrado por ninguém — ele é executado, e
        # a arquitetura registrada no `meta.json` seria a errada.
        for instance, plan_slice in slices.items():
            assert {block["instance"] for block in plan_slice["blocks"]} == {instance}

    def test_the_union_of_the_slices_is_the_canonical(self, plan, slices):
        # Célula ausente = coluna vazia na comparação cross-arch da ADR-0010,
        # descoberta só na consolidação. Igualdade profunda, não contagem: uma
        # projeção que remontasse os blocos em vez de reaproveitá-los — e
        # remontasse algum campo errado — contaria igual.
        united = [block for plan_slice in slices.values() for block in plan_slice["blocks"]]

        assert sorted(united, key=block_key) == sorted(plan["blocks"], key=block_key)

    def test_the_slices_are_pairwise_disjoint(self, slices):
        # A recíproca: um bloco em duas fatias roda duas vezes, em arquiteturas
        # diferentes, e a `scenario_id` duplicada vira dedup silencioso ("último
        # `started_at` vence") no `consolidate.py`.
        for first, second in itertools.combinations(slices.values(), 2):
            assert scenario_ids(first) & scenario_ids(second) == set()

    def test_the_relative_order_of_the_canonical_is_preserved(self, plan, slices):
        # Fatiar não pode desfazer a randomização: se a ordem dentro da fatia
        # divergisse da do canônico, as três arquiteturas deixariam de ver a
        # mesma sequência e o cancelamento de efeitos temporais da ADR-0010 iria
        # embora — sem que contagem, união ou disjunção acusassem nada.
        position = {block_key(block): index for index, block in enumerate(plan["blocks"])}

        for instance, plan_slice in slices.items():
            positions = [position[block_key(block)] for block in plan_slice["blocks"]]

            assert positions == sorted(positions), instance

    def test_a_slice_carries_the_top_shape_of_the_canonical(self, plan, slices):
        # Quem lê uma fatia não precisa saber que ela é um recorte: mesma forma
        # de topo, mesmo `schema_version`, mesma seed.
        for plan_slice in slices.values():
            assert set(plan_slice) == set(plan)
            assert plan_slice["schema_version"] == plan["schema_version"]
            assert plan_slice["seed"] == plan["seed"]

    def test_the_projection_leaves_the_canonical_untouched(self, plan):
        # Projeção é função pura: se ela mutasse o canônico — um `pop` no lugar
        # de uma cópia —, o arquivo escrito depois dela seria o recorte.
        before = serialize_plan(plan)

        build_instance_slices(plan)

        assert serialize_plan(plan) == before

    def test_slicing_twice_produces_identical_bytes(self):
        first = build_instance_slices(build_canonical_plan(real_config()))
        second = build_instance_slices(build_canonical_plan(real_config()))

        assert [serialize_plan(s) for s in first.values()] == [
            serialize_plan(s) for s in second.values()
        ]


class TestCli:
    def test_writes_the_canonical_and_one_slice_per_architecture(self, tmp_path):
        # A casca de I/O tem uma verificação só, de caixa-preta e depois do fato
        # (ADR-0022): o CLI invocado como processo sobre o `experiment.toml`
        # real, duas vezes, em diretórios temporários. Duas invocações porque o
        # determinismo byte-a-byte prometido ao `diff` vale para os quatro
        # artefatos, não só para o canônico. Nada aqui importa o núcleo.
        first = generate_into(tmp_path / "first")
        second = generate_into(tmp_path / "second")
        expected = {CANONICAL_FILENAME, *(f"{instance}.json" for instance in EXPECTED_INSTANCES)}

        assert {path.name for path in first.iterdir()} == expected

        for name in expected:
            written = (first / name).read_bytes()

            assert written == (second / name).read_bytes(), name

        canonical = json.loads((first / CANONICAL_FILENAME).read_text(encoding="utf-8"))

        assert len(canonical["blocks"]) == EXPECTED_BLOCKS

        for instance in EXPECTED_INSTANCES:
            plan_slice = json.loads((first / f"{instance}.json").read_text(encoding="utf-8"))

            assert len(plan_slice["blocks"]) == EXPECTED_SLICE_BLOCKS, instance
            assert {block["instance"] for block in plan_slice["blocks"]} == {instance}


def generate_into(out: Path) -> Path:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--config", str(REAL_EXPERIMENT_TOML), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    return out


def one_run(plan: dict, scenario_id: str) -> dict:
    matches = [run for run in all_runs(plan) if run["scenario_id"] == scenario_id]

    assert len(matches) == 1, scenario_id
    return matches[0]
