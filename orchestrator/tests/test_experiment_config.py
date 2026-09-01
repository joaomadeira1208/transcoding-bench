# Testes do núcleo de validação do `config/experiment.toml`.
#
# O critério da ADR-0022 é falha silenciosa, e este arquivo é o caso extremo dele:
# um `experiment.toml` defeituoso não estoura em lugar nenhum — ele produz uma
# matriz experimental errada, e o erro só apareceria depois de ~46h de compute.
# Por isso cada modo de falha ganha um teste de rejeição próprio, e cada um
# confere que a mensagem **nomeia o registro ofensor**: um `ConfigError` genérico
# manda o pesquisador procurar a agulha à mão.

from __future__ import annotations

import pytest
from conftest import (
    ABSENT,
    make_codec,
    make_encode,
    make_geometry,
    make_instance,
    make_instrumentation,
    make_video,
    real_config,
)
from experiment_config import ConfigError, validate_config

# Transcrição deliberada da spec (ADR-0004), não uma contagem: 162/972/810 e
# `len(pairs) == 9` são invariantes sob **substituição** de par, então trocar
# `2160p → 480p` por outro par passaria despercebido por qualquer contagem.
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

# Os dez eventos da ADR-0006, transcritos **na ordem em que ela os lista**. São
# desenho experimental: trocar um evento muda o que o artigo reporta, e o modo de
# falha é o mais caro do projeto — `perf stat` não falha com um evento que a
# arquitetura não tem, apenas o reporta como `<not supported>` e segue.
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

# O muxer do elementary stream de cada codec (ADR-0005/0007). Transcrito, não
# derivado: é justamente por não sair do `slug` nem do rótulo do codec por
# manipulação de string que ele é dado declarado.
EXPECTED_BITSTREAM_MUXERS = {
    "libx264": "h264",
    "libx265": "hevc",
    "libsvtav1": "obu",
}

# Mesmo instinto, para a tabela da ADR-0023: os tiers são rótulos nominais, e um
# dígito errado aqui é um `scale=` errado que produz vídeo perfeitamente válido.
EXPECTED_GEOMETRY = {
    "bbb": {
        "2160p": (3840, 2160),
        "1080p": (1920, 1080),
        "720p": (1280, 720),
        "480p": (854, 480),
    },
    "tos": {
        "2160p": (4096, 1714),
        "1080p": (1920, 804),
        "720p": (1280, 536),
        "480p": (854, 358),
    },
}


class TestAccepts:
    def test_minimal_config(self, make_raw_config):
        config = validate_config(make_raw_config())

        assert config.seed == 1
        assert config.replications == 5
        assert config.warmup_runs == 1
        assert [c.slug for c in config.codecs] == ["libx264"]
        assert [(p.input_res, p.output_res) for p in config.pairs] == [
            ("1080p", "1080p"),
            ("1080p", "720p"),
        ]

    def test_records_keep_declaration_order(self, make_raw_config):
        # A ordem pré-shuffle do plano é a ordem de declaração do TOML (decisão
        # D7): se a validação reordenasse os registros, o determinismo do plano
        # dependeria de um detalhe invisível aqui.
        raw = make_raw_config(
            instance=[
                make_instance(id="c7i", instance_type="c7i.xlarge", arch="x86_64"),
                make_instance(id="c7g", instance_type="c7g.xlarge", arch="arm64"),
            ]
        )

        assert [i.id for i in validate_config(raw).instances] == ["c7i", "c7g"]


class TestRealExperimentToml:
    """Âncora da spec: o arquivo que o artigo cita, sob a mesma validação do CLI."""

    def test_is_valid(self):
        config = real_config()

        assert config.replications == 5
        assert config.warmup_runs == 1
        assert [c.slug for c in config.codecs] == ["libx264", "libx265", "libsvtav1"]
        assert [v.slug for v in config.videos] == ["bbb", "tos"]
        assert [i.id for i in config.instances] == ["c7g", "c7i", "c7a"]

    def test_declares_exactly_the_nine_pairs_of_the_spec(self):
        pairs = [(p.input_res, p.output_res) for p in real_config().pairs]

        assert set(pairs) == EXPECTED_PAIRS
        assert len(pairs) == len(EXPECTED_PAIRS)

    def test_declares_the_geometry_of_every_tier_of_every_video(self):
        declared = {
            video.slug: {tier: (g.width, g.height) for tier, g in video.geometry.items()}
            for video in real_config().videos
        }

        assert declared == EXPECTED_GEOMETRY

    def test_declares_the_bitstream_muxer_of_every_codec(self):
        # Sem o campo, o `run_scenario.sh` teria de mapear codec → muxer em
        # bash, e o erro sairia como um `output.sha256` de um bitstream que não
        # é o do Cenário — perfeitamente válido, e errado.
        muxers = {c.slug: c.bitstream_muxer for c in real_config().codecs}

        assert muxers == EXPECTED_BITSTREAM_MUXERS

    def test_declares_the_ten_pmu_events_of_the_adr_in_order(self):
        # Lista, não conjunto: a ordem é a que a ADR-0006 escreveu e é a que vai
        # para o `-e` do `perf stat`, então ela é parte do que se congela.
        assert list(real_config().instrumentation.pmu_events) == EXPECTED_PMU_EVENTS

    def test_ties_preset_and_crf_to_each_codec(self):
        codecs = {c.slug: (c.preset, c.crf) for c in real_config().codecs}

        assert codecs == {
            "libx264": ("medium", 23),
            "libx265": ("medium", 28),
            "libsvtav1": ("8", 35),
        }


class TestRejectsUpscale:
    def test_pair_that_upscales(self, make_raw_config):
        raw = make_raw_config(pair=[{"input_res": "720p", "output_res": "1080p"}])

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        message = str(excinfo.value)
        assert "pair[0]" in message
        assert "720p" in message and "1080p" in message
        assert "bbb" in message

    def test_upscale_hidden_behind_a_downscale_label(self, make_raw_config):
        # O rótulo diz 1080p → 720p, que parece downscale; a geometria declarada
        # para este vídeo diz o contrário. A verificação é sobre pixels, nunca
        # sobre o nome do tier — é por isso que a geometria está no TOML.
        raw = make_raw_config(
            video=[
                make_video(geometry=make_geometry(**{"1080p": (1920, 1080), "720p": (2560, 1440)}))
            ]
        )

        with pytest.raises(ConfigError, match=r"pair\[1\]"):
            validate_config(raw)

    def test_upscale_in_only_one_dimension(self, make_raw_config):
        # Altura cabe, largura não: o par continua sendo upscale. Comparar área ou
        # só a altura deixaria isto passar.
        raw = make_raw_config(
            video=[
                make_video(geometry=make_geometry(**{"1080p": (1920, 1080), "720p": (2560, 720)}))
            ]
        )

        with pytest.raises(ConfigError, match=r"pair\[1\]"):
            validate_config(raw)

    def test_upscale_in_a_single_video(self, make_raw_config):
        # O par é downscale para `bbb` e upscale para `tos`: basta um vídeo para
        # o par ser inválido.
        raw = make_raw_config(
            video=[
                make_video(),
                make_video(
                    slug="tos",
                    title="Tears of Steel",
                    geometry=make_geometry(**{"1080p": (1920, 804), "720p": (1920, 900)}),
                ),
            ]
        )

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        assert "tos" in str(excinfo.value)


class TestRejectsBadGeometry:
    def test_tier_referenced_by_a_pair_without_geometry_in_some_video(self, make_raw_config):
        raw = make_raw_config(
            video=[
                make_video(),
                make_video(
                    slug="tos",
                    title="Tears of Steel",
                    geometry=make_geometry(**{"1080p": (1920, 804)}),
                ),
            ]
        )

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        message = str(excinfo.value)
        assert "tos" in message
        assert "720p" in message

    def test_non_positive_dimension(self, make_raw_config):
        raw = make_raw_config(
            video=[make_video(geometry=make_geometry(**{"1080p": (1920, 1080), "720p": (1280, 0)}))]
        )

        with pytest.raises(ConfigError, match="bbb"):
            validate_config(raw)

    def test_odd_dimension(self, make_raw_config):
        # `yuv420p` subamostra o croma 2x2 (ADR-0002/0023): uma altura ímpar não
        # estoura aqui, estoura no FFmpeg depois que o plano já foi distribuído.
        raw = make_raw_config(
            video=[
                make_video(geometry=make_geometry(**{"1080p": (1920, 1080), "720p": (1280, 721)}))
            ]
        )

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        message = str(excinfo.value)
        assert "bbb" in message
        assert "720p" in message


class TestRejectsDuplicates:
    def test_duplicate_codec_slug(self, make_raw_config):
        raw = make_raw_config(codec=[make_codec(), make_codec(crf=26)])

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        message = str(excinfo.value)
        assert "codec[1]" in message
        assert "libx264" in message

    def test_duplicate_video_slug(self, make_raw_config):
        raw = make_raw_config(video=[make_video(), make_video(title="Big Buck Bunny (again)")])

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        message = str(excinfo.value)
        assert "video[1]" in message
        assert "bbb" in message

    def test_duplicate_instance_id(self, make_raw_config):
        raw = make_raw_config(
            instance=[make_instance(), make_instance(instance_type="c7g.2xlarge")]
        )

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        message = str(excinfo.value)
        assert "instance[1]" in message
        assert "c7g" in message

    def test_duplicate_pair(self, make_raw_config):
        raw = make_raw_config(
            pair=[
                {"input_res": "1080p", "output_res": "720p"},
                {"input_res": "1080p", "output_res": "720p"},
            ]
        )

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        message = str(excinfo.value)
        assert "pair[1]" in message
        assert "1080p" in message and "720p" in message


class TestRejectsMissingExperimentDesign:
    def test_missing_seed(self, make_raw_config):
        raw = make_raw_config(experiment={"replications": 5, "warmup_runs": 1})

        with pytest.raises(ConfigError, match="seed"):
            validate_config(raw)

    def test_missing_replications(self, make_raw_config):
        raw = make_raw_config(experiment={"seed": 1, "warmup_runs": 1})

        with pytest.raises(ConfigError, match="replications"):
            validate_config(raw)

    def test_missing_warmup_runs(self, make_raw_config):
        # Sem default silencioso: o descarte do warm-up é desenho experimental
        # (ADR-0003), não conveniência do gerador.
        raw = make_raw_config(experiment={"seed": 1, "replications": 5})

        with pytest.raises(ConfigError, match="warmup_runs"):
            validate_config(raw)

    def test_missing_experiment_table(self, make_raw_config):
        raw = make_raw_config(experiment=ABSENT)

        with pytest.raises(ConfigError, match="experiment"):
            validate_config(raw)

    def test_seed_that_is_not_an_integer(self, make_raw_config):
        raw = make_raw_config(experiment={"seed": "1", "replications": 5, "warmup_runs": 1})

        with pytest.raises(ConfigError, match="seed"):
            validate_config(raw)

    def test_seed_that_is_a_boolean(self, make_raw_config):
        # `bool` é subclasse de `int`: sem guarda explícita, `seed = true` viraria
        # a seed 1 e o Experimento inteiro seria embaralhado por um typo.
        raw = make_raw_config(experiment={"seed": True, "replications": 5, "warmup_runs": 1})

        with pytest.raises(ConfigError, match="seed"):
            validate_config(raw)

    def test_more_than_one_warmup_run(self, make_raw_config):
        # A `scenario_id` tem uma única vaga `_warmup` (ADR-0019): dois warm-ups
        # por Cenário produziriam chaves duplicadas, e a duplicata só apareceria
        # na consolidação, depois do compute gasto.
        raw = make_raw_config(experiment={"seed": 1, "replications": 5, "warmup_runs": 2})

        with pytest.raises(ConfigError, match="warmup_runs"):
            validate_config(raw)

    def test_replications_below_one(self, make_raw_config):
        raw = make_raw_config(experiment={"seed": 1, "replications": 0, "warmup_runs": 1})

        with pytest.raises(ConfigError, match="replications"):
            validate_config(raw)


class TestRejectsIncompleteRecords:
    @pytest.mark.parametrize("family", ["codec", "pair", "video", "instance"])
    def test_empty_family(self, make_raw_config, family):
        with pytest.raises(ConfigError, match=family):
            validate_config(make_raw_config(**{family: []}))

    @pytest.mark.parametrize(
        "family", ["codec", "pair", "video", "instance", "encode", "instrumentation"]
    )
    def test_absent_family(self, make_raw_config, family):
        with pytest.raises(ConfigError, match=family):
            validate_config(make_raw_config(**{family: ABSENT}))

    @pytest.mark.parametrize(
        "field",
        ["slug", "codec", "encoder", "preset", "crf", "encoder_args", "bitstream_muxer"],
    )
    def test_codec_missing_field(self, make_raw_config, field):
        codec = make_codec()
        del codec[field]

        with pytest.raises(ConfigError) as excinfo:
            validate_config(make_raw_config(codec=[codec]))

        assert "codec[0]" in str(excinfo.value)
        assert field in str(excinfo.value)

    @pytest.mark.parametrize(
        "field", ["threads", "gop_size", "pix_fmt", "strip_audio", "container", "scale_flags"]
    )
    def test_fixed_encode_params_missing_field(self, make_raw_config, field):
        encode = make_encode()
        del encode[field]

        with pytest.raises(ConfigError, match=field):
            validate_config(make_raw_config(encode=encode))

    def test_instance_missing_arch(self, make_raw_config):
        instance = make_instance()
        del instance["arch"]

        with pytest.raises(ConfigError) as excinfo:
            validate_config(make_raw_config(instance=[instance]))

        assert "instance[0]" in str(excinfo.value)
        assert "arch" in str(excinfo.value)

    def test_video_missing_geometry(self, make_raw_config):
        video = make_video()
        del video["geometry"]

        with pytest.raises(ConfigError) as excinfo:
            validate_config(make_raw_config(video=[video]))

        assert "video[0]" in str(excinfo.value)
        assert "geometry" in str(excinfo.value)

    def test_crf_that_is_not_an_integer(self, make_raw_config):
        with pytest.raises(ConfigError, match="crf"):
            validate_config(make_raw_config(codec=[make_codec(crf="23")]))

    def test_strip_audio_that_is_a_string(self, make_raw_config):
        # O modo de falha que a ADR-0022 mais teme, aqui no lado que escreve: uma
        # string `"false"` é truthy e passaria por qualquer checagem de presença.
        with pytest.raises(ConfigError, match="strip_audio"):
            validate_config(make_raw_config(encode=make_encode(strip_audio="false")))

    def test_unknown_key_in_a_record(self, make_raw_config):
        # Um `presset = "medium"` deixaria o `preset` real ausente em silêncio se
        # a validação ignorasse o que não conhece.
        with pytest.raises(ConfigError) as excinfo:
            validate_config(make_raw_config(codec=[make_codec(tune="film")]))

        assert "codec[0]" in str(excinfo.value)
        assert "tune" in str(excinfo.value)

    def test_unknown_table_at_the_top_level(self, make_raw_config):
        with pytest.raises(ConfigError, match="quality"):
            validate_config(make_raw_config(quality={"sample": 10}))

    def test_encoder_args_that_is_not_a_list_of_strings(self, make_raw_config):
        with pytest.raises(ConfigError, match="encoder_args"):
            validate_config(make_raw_config(codec=[make_codec(encoder_args="-sc_threshold 0")]))

    def test_empty_bitstream_muxer(self, make_raw_config):
        # String vazia chegaria ao `ffmpeg -c copy -f '' -` como um argumento
        # presente e sem valor: o campo existe, e a extração falha longe daqui.
        with pytest.raises(ConfigError) as excinfo:
            validate_config(make_raw_config(codec=[make_codec(bitstream_muxer="")]))

        message = str(excinfo.value)
        assert "codec[0]" in message
        assert "bitstream_muxer" in message


class TestRejectsBadInstrumentation:
    """Os dez eventos são desenho experimental (ADR-0006), não constante de script."""

    def test_missing_pmu_events(self, make_raw_config):
        with pytest.raises(ConfigError) as excinfo:
            validate_config(make_raw_config(instrumentation={}))

        message = str(excinfo.value)
        assert "instrumentation" in message
        assert "pmu_events" in message

    def test_empty_pmu_events(self, make_raw_config):
        # Lista vazia é o pior caso: `perf stat` sem `-e` roda, coleta o conjunto
        # default de eventos e devolve um JSON plausível — sem os dez que o
        # artigo reporta.
        with pytest.raises(ConfigError) as excinfo:
            validate_config(make_raw_config(instrumentation=make_instrumentation(pmu_events=[])))

        message = str(excinfo.value)
        assert "instrumentation" in message
        assert "pmu_events" in message

    def test_duplicate_pmu_event(self, make_raw_config):
        # `perf stat` aceita o evento repetido e emite duas entradas com o mesmo
        # nome; quem lê o JSON por nome fica com uma delas, escolhida por acaso.
        raw = make_raw_config(
            instrumentation=make_instrumentation(pmu_events=["cycles", "instructions", "cycles"])
        )

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        message = str(excinfo.value)
        assert "pmu_events[2]" in message
        assert "cycles" in message

    def test_empty_pmu_event(self, make_raw_config):
        # Um elemento vazio não é "lista vazia" nem duplicata, e chega ao
        # `perf stat` como um `-e cycles,,instructions` — o mesmo silêncio que a
        # string vazia do `bitstream_muxer`, um nível mais fundo.
        raw = make_raw_config(instrumentation=make_instrumentation(pmu_events=["cycles", ""]))

        with pytest.raises(ConfigError, match="pmu_events"):
            validate_config(raw)

    def test_pmu_events_that_is_not_a_list_of_strings(self, make_raw_config):
        raw = make_raw_config(instrumentation=make_instrumentation(pmu_events="cycles"))

        with pytest.raises(ConfigError, match="pmu_events"):
            validate_config(raw)

    def test_unknown_key_in_the_instrumentation_table(self, make_raw_config):
        raw = make_raw_config(instrumentation=make_instrumentation(sample_rate=1))

        with pytest.raises(ConfigError) as excinfo:
            validate_config(raw)

        message = str(excinfo.value)
        assert "instrumentation" in message
        assert "sample_rate" in message
