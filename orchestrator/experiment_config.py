"""Núcleo de validação da spec do Experimento — função pura, sem I/O.

Recebe a configuração **já parseada** (o dict que o `tomllib` devolve) e entrega
uma estrutura validada, ou levanta `ConfigError` nomeando o registro ofensor.
Ler o arquivo e sair com código de erro é casca fina, e mora no CLI (decisão D4
da spec): assim o gerador do plano é função pura do `config/experiment.toml`, e
os testes exercitam a validação sem tocar em disco.

A regra que atravessa o módulo é **nenhum default silencioso**: campo ausente é
erro, tipo divergente é erro, chave desconhecida é erro. Um `experiment.toml`
defeituoso não estoura em lugar nenhum sozinho — ele produz uma matriz
experimental errada, e o custo de descobrir isso é ~46h de compute (ADR-0022).
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_TOP_LEVEL_KEYS = frozenset(
    {"experiment", "encode", "instrumentation", "codec", "pair", "video", "instance"}
)


class ConfigError(Exception):
    """Spec inválida. A mensagem sempre nomeia o registro ofensor."""


@dataclass(frozen=True)
class Geometry:
    """Geometria em pixels de um tier de resolução, para um vídeo específico."""

    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"

    def fits_within(self, other: Geometry) -> bool:
        return self.width <= other.width and self.height <= other.height


@dataclass(frozen=True)
class CodecRecord:
    """Codec com preset, CRF e parâmetros de encoder amarrados (ADR-0002/0019).

    `bitstream_muxer` é o muxer do elementary stream usado na extração que
    precede o `output.sha256` (ADR-0005/0007). Ele é declarado, e não derivado do
    `slug` ou do rótulo do codec, pelo mesmo motivo que a `scenario_id` é formada
    em Python: um mapa codec → muxer em bash é manipulação de string, e o erro
    apareceria como o hash de um bitstream que não é o do Cenário.
    """

    slug: str
    codec: str
    encoder: str
    preset: str
    crf: int
    encoder_args: tuple[str, ...]
    bitstream_muxer: str


@dataclass(frozen=True)
class PairRecord:
    input_res: str
    output_res: str

    def __str__(self) -> str:
        return f"{self.input_res} -> {self.output_res}"


@dataclass(frozen=True)
class VideoRecord:
    slug: str
    title: str
    geometry: Mapping[str, Geometry]


@dataclass(frozen=True)
class InstanceRecord:
    """Id curto e instance type declarados lado a lado: nenhum é derivado do outro."""

    id: str
    instance_type: str
    arch: str


@dataclass(frozen=True)
class FixedEncodeParams:
    """Parâmetros de encode idênticos em todo Cenário (ADR-0002)."""

    threads: int
    gop_size: int
    pix_fmt: str
    strip_audio: bool
    container: str
    scale_flags: str


@dataclass(frozen=True)
class Instrumentation:
    """Os eventos de PMU que instrumentam cada Execução (ADR-0006).

    São desenho experimental, não detalhe de operação: IPC, cache miss rate e
    branch mispredict rate saem deles, e trocar um evento muda o que o artigo
    reporta. Por isso moram na spec e viajam até o objeto de run, como todo o
    resto que o bash copia — o `run_scenario.sh` monta o `-e` do `perf stat` sem
    conhecer a ADR.
    """

    pmu_events: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentConfig:
    seed: int
    replications: int
    warmup_runs: int
    encode: FixedEncodeParams
    instrumentation: Instrumentation
    codecs: tuple[CodecRecord, ...]
    pairs: tuple[PairRecord, ...]
    videos: tuple[VideoRecord, ...]
    instances: tuple[InstanceRecord, ...]


def validate_config(raw: Mapping[str, Any]) -> ExperimentConfig:
    """Valida a configuração já parseada, falhando alto no primeiro defeito."""
    _reject_unknown(raw, _TOP_LEVEL_KEYS, "top level")

    experiment = _table(raw, "experiment")
    _reject_unknown(experiment, {"seed", "replications", "warmup_runs"}, "experiment")

    config = ExperimentConfig(
        seed=_int(experiment, "seed", "experiment", minimum=0),
        replications=_int(experiment, "replications", "experiment", minimum=1),
        warmup_runs=_int(experiment, "warmup_runs", "experiment", minimum=0),
        encode=_encode(_table(raw, "encode")),
        instrumentation=_instrumentation(_table(raw, "instrumentation")),
        codecs=tuple(_codec(r, i) for i, r in enumerate(_records(raw, "codec"))),
        pairs=tuple(_pair(r, i) for i, r in enumerate(_records(raw, "pair"))),
        videos=tuple(_video(r, i) for i, r in enumerate(_records(raw, "video"))),
        instances=tuple(_instance(r, i) for i, r in enumerate(_records(raw, "instance"))),
    )

    _reject_duplicates((c.slug for c in config.codecs), "codec", "slug")
    _reject_duplicates((v.slug for v in config.videos), "video", "slug")
    _reject_duplicates((i.id for i in config.instances), "instance", "id")
    # A identidade de um par são os seus campos — o registro inteiro, que é
    # frozen e portanto hashável. Chavear pelo texto do `__str__` faria uma
    # mudança cosmética na mensagem de erro virar mudança de semântica.
    _reject_duplicates(config.pairs, "pair", "pair")

    _reject_ambiguous_warmup(config.warmup_runs)
    _reject_missing_geometry(config.pairs, config.videos)
    _reject_upscale(config.pairs, config.videos)

    return config


# --- registros ---------------------------------------------------------------


def _encode(record: Mapping[str, Any]) -> FixedEncodeParams:
    where = "encode"
    _reject_unknown(
        record,
        {"threads", "gop_size", "pix_fmt", "strip_audio", "container", "scale_flags"},
        where,
    )
    return FixedEncodeParams(
        threads=_int(record, "threads", where, minimum=0),
        gop_size=_int(record, "gop_size", where, minimum=1),
        pix_fmt=_str(record, "pix_fmt", where),
        strip_audio=_bool(record, "strip_audio", where),
        container=_str(record, "container", where),
        scale_flags=_str(record, "scale_flags", where),
    )


def _instrumentation(record: Mapping[str, Any]) -> Instrumentation:
    where = "instrumentation"
    _reject_unknown(record, {"pmu_events"}, where)

    events = _str_list(record, "pmu_events", where)
    # Lista vazia é o pior caso e não seria pega por "chave presente": um
    # `perf stat` sem `-e` roda, coleta o conjunto default de eventos e devolve
    # um JSON plausível, sem os que o artigo reporta.
    if not events:
        raise ConfigError(f"{where}: 'pmu_events' must declare at least one event")
    # Evento repetido também não estoura no `perf`: ele emite duas entradas com o
    # mesmo nome, e quem lê o JSON por nome fica com uma delas, escolhida por acaso.
    _reject_duplicates(events, "pmu_events", "event")

    return Instrumentation(pmu_events=events)


def _codec(record: Mapping[str, Any], index: int) -> CodecRecord:
    where = _where("codec", index, record.get("slug"))
    _reject_unknown(
        record,
        {"slug", "codec", "encoder", "preset", "crf", "encoder_args", "bitstream_muxer"},
        where,
    )
    return CodecRecord(
        slug=_str(record, "slug", where),
        codec=_str(record, "codec", where),
        encoder=_str(record, "encoder", where),
        preset=_str(record, "preset", where),
        crf=_int(record, "crf", where, minimum=0),
        encoder_args=_str_list(record, "encoder_args", where),
        bitstream_muxer=_str(record, "bitstream_muxer", where),
    )


def _pair(record: Mapping[str, Any], index: int) -> PairRecord:
    where = _where("pair", index, None)
    _reject_unknown(record, {"input_res", "output_res"}, where)
    return PairRecord(
        input_res=_str(record, "input_res", where),
        output_res=_str(record, "output_res", where),
    )


def _video(record: Mapping[str, Any], index: int) -> VideoRecord:
    where = _where("video", index, record.get("slug"))
    _reject_unknown(record, {"slug", "title", "geometry"}, where)
    return VideoRecord(
        slug=_str(record, "slug", where),
        title=_str(record, "title", where),
        geometry=_geometry(record, where),
    )


def _instance(record: Mapping[str, Any], index: int) -> InstanceRecord:
    where = _where("instance", index, record.get("id"))
    _reject_unknown(record, {"id", "instance_type", "arch"}, where)
    return InstanceRecord(
        id=_str(record, "id", where),
        instance_type=_str(record, "instance_type", where),
        arch=_str(record, "arch", where),
    )


def _geometry(record: Mapping[str, Any], where: str) -> Mapping[str, Geometry]:
    table = _require(record, "geometry", where)
    if not isinstance(table, Mapping) or not table:
        raise ConfigError(f"{where}: 'geometry' must be a non-empty table of tiers")

    geometry: dict[str, Geometry] = {}
    for tier, dimensions in table.items():
        tier_where = f"{where}: geometry['{tier}']"
        if not isinstance(dimensions, Mapping):
            raise ConfigError(f"{tier_where} must be a table with 'width' and 'height'")
        _reject_unknown(dimensions, {"width", "height"}, tier_where)
        geometry[tier] = Geometry(
            width=_even(dimensions, "width", tier_where),
            height=_even(dimensions, "height", tier_where),
        )
    return geometry


# --- checagens cruzadas ------------------------------------------------------


def _reject_ambiguous_warmup(warmup_runs: int) -> None:
    """No máximo um warm-up por Cenário: a `scenario_id` tem uma vaga só.

    A chave termina em `_warmup` na Execução descartada (ADR-0019), sem índice —
    uma segunda Execução de warm-up produziria duas chaves idênticas, que é
    exatamente a falha silenciosa que aquela ADR existe para impedir. O desenho
    da ADR-0003 pede um; o que se recusa aqui é o dois.
    """
    if warmup_runs > 1:
        raise ConfigError(
            f"experiment: 'warmup_runs' must be 0 or 1, got {warmup_runs} "
            "(a second warm-up would collide on the scenario_id, which has a single slot)"
        )


def _reject_missing_geometry(pairs: Sequence[PairRecord], videos: Sequence[VideoRecord]) -> None:
    """Todo tier referenciado por um par tem geometria em **todos** os vídeos.

    Sem isto, o `scale=` do FFmpeg receberia dimensão vazia — e a asserção de
    "nunca upscale" abaixo não teria números sobre os quais operar.
    """
    for video_index, video in enumerate(videos):
        where = _where("video", video_index, video.slug)
        for pair_index, pair in enumerate(pairs):
            for tier in (pair.input_res, pair.output_res):
                if tier not in video.geometry:
                    raise ConfigError(
                        f"{where}: missing geometry for tier '{tier}', "
                        f"referenced by pair[{pair_index}] {pair}"
                    )


def _reject_upscale(pairs: Sequence[PairRecord], videos: Sequence[VideoRecord]) -> None:
    """Nenhum par amplia a imagem — verificado sobre a geometria, nunca sobre o rótulo.

    Os tiers são nomes nominais: `1080p` não é 1920x1080 para os dois vídeos
    (ADR-0023), então comparar strings não diz nada sobre pixels.
    """
    for pair_index, pair in enumerate(pairs):
        for video in videos:
            source = video.geometry[pair.input_res]
            target = video.geometry[pair.output_res]
            if not target.fits_within(source):
                raise ConfigError(
                    f"pair[{pair_index}] {pair}: upscale for video '{video.slug}' "
                    f"({source} -> {target})"
                )


def _reject_duplicates(values: Iterable[Hashable], family: str, label: str) -> None:
    seen: dict[Hashable, int] = {}
    for index, value in enumerate(values):
        if value in seen:
            raise ConfigError(
                f"{family}[{index}]: duplicate {label} '{value}' "
                f"(already declared at {family}[{seen[value]}])"
            )
        seen[value] = index


# --- primitivas de leitura ---------------------------------------------------


def _where(family: str, index: int, identity: Any) -> str:
    return f"{family}[{index}] '{identity}'" if isinstance(identity, str) else f"{family}[{index}]"


def _table(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in raw:
        raise ConfigError(f"missing required table '{key}'")
    value = raw[key]
    if not isinstance(value, Mapping):
        raise ConfigError(f"'{key}' must be a table, got {type(value).__name__}")
    return value


def _records(raw: Mapping[str, Any], key: str) -> Sequence[Mapping[str, Any]]:
    if key not in raw:
        raise ConfigError(f"missing required array of tables '{key}'")
    value = raw[key]
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ConfigError(f"'{key}' must be an array of tables")
    if not value:
        raise ConfigError(f"'{key}': at least one record is required")
    return value


def _require(record: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in record:
        raise ConfigError(f"{where}: missing required key '{key}'")
    return record[key]


def _int(record: Mapping[str, Any], key: str, where: str, *, minimum: int | None = None) -> int:
    value = _require(record, key, where)
    # `bool` é subclasse de `int`: sem esta guarda, `seed = true` passaria.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: '{key}' must be an integer, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{where}: '{key}' must be >= {minimum}, got {value}")
    return value


def _even(record: Mapping[str, Any], key: str, where: str) -> int:
    """Dimensão de imagem: positiva e par, porque `yuv420p` subamostra o croma 2x2.

    Uma altura ímpar não estoura aqui — ela chega ao FFmpeg horas depois, quando
    o plano já foi distribuído às instâncias (ADR-0002/0023).
    """
    value = _int(record, key, where, minimum=1)
    if value % 2:
        raise ConfigError(f"{where}: '{key}' must be even for yuv420p, got {value}")
    return value


def _str(record: Mapping[str, Any], key: str, where: str) -> str:
    value = _require(record, key, where)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: '{key}' must be a non-empty string")
    return value


def _bool(record: Mapping[str, Any], key: str, where: str) -> bool:
    value = _require(record, key, where)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: '{key}' must be a boolean, got {type(value).__name__}")
    return value


def _str_list(record: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    """Lista de strings **não vazias**, pela mesma razão que `_str` recusa a vazia.

    Um elemento vazio não some: ele vira um argumento presente e sem valor no
    argv do FFmpeg ou no `-e` do `perf stat`, e o erro aparece longe daqui.
    """
    value = _require(record, key, where)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{where}: '{key}' must be a list of strings")
    if any(not item for item in value):
        raise ConfigError(f"{where}: '{key}' must not contain an empty string")
    return tuple(value)


def _reject_unknown(record: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    """Chave desconhecida é erro: um `presset = "medium"` seria default silencioso."""
    unknown = sorted(set(record) - set(allowed))
    if unknown:
        listed = ", ".join(f"'{key}'" for key in unknown)
        raise ConfigError(f"{where}: unknown key(s) {listed}")
