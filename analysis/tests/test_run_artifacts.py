# Os quatro artefatos de instrumentação da ADR-0006 são texto de ferramenta lido
# em Python, e cada parser tem o seu modo de falha calado: uma chave renomeada no
# format string do `/usr/bin/time`, um `<not supported>` do `perf` virando zero em
# vez de ausente, a coluna `%CPU` mudando de posição no `pidstat`, e o `-stats` do
# FFmpeg lido na linha intermediária em vez da final.
#
# O caso feliz de cada parser roda contra a **captura real** que a camada de
# aceite manual trouxe de dentro da imagem (ADR-0022): uma factory dessas quatro
# saídas é o autor do parser adivinhando o que a ferramenta emite. A factory fica
# com o que só ela sabe fazer — as variações que a ferramenta não produz sob
# encomenda.

from __future__ import annotations

import json

import pytest
from conftest import (
    ABSENT,
    CLIP_FRAMES,
    ENCODERS,
    make_ffmpeg_log,
    make_perf_json,
    make_pidstat,
    make_time_json,
    read_capture,
)
from run_artifacts import (
    ArtifactError,
    parse_ffmpeg_log,
    parse_perf,
    parse_pidstat,
    parse_time,
)
from run_table import PMU_EVENTS


@pytest.mark.parametrize("encoder", ENCODERS)
class TestTheRealTools:
    def test_the_format_string_and_the_model_agree_on_every_field(self, encoder):
        # O modelo é estrito e proíbe extra, então **atravessar** já é a asserção:
        # uma chave a mais ou a menos no format string do bash estoura aqui.
        time = parse_time(read_capture(encoder, "time.json"))

        assert time.exit_status == 0
        assert time.elapsed_s > 0
        assert time.max_rss_kb > 0

    def test_every_counter_of_the_perf_stat_comes_out_numeric_or_absent(self, encoder):
        counters = parse_perf(read_capture(encoder, "perf.json"))

        # A ponte entre o `-e` que o `run_scenario.sh` passou e as colunas que a
        # tabela declara: trocar um evento de um lado só deixaria a métrica vazia
        # para a campanha inteira, e o `perf stat` não reclama disso.
        assert set(counters) == set(PMU_EVENTS)
        assert all(value is None or value > 0 for value in counters.values())

    def test_the_absent_counters_are_exactly_the_unsupported_ones(self, encoder):
        # A captura vem sem PMU (o Docker no Mac não a expõe ao guest), então ela
        # traz o `<not supported>` que o `perf` de verdade escreve. Quando ela for
        # regenerada onde há PMU os dois lados ficam vazios, e é por isso que a
        # rejeição determinística continua na factory abaixo.
        raw = read_capture(encoder, "perf.json")
        counters = parse_perf(raw)

        assert {event for event, value in counters.items() if value is None} == {
            json.loads(line)["event"] for line in raw.splitlines() if "<not supported>" in line
        }

    def test_the_cpu_series_has_one_entry_per_sample(self, encoder):
        # O `pidstat -h` de verdade **repete o cabeçalho antes de cada amostra**,
        # coisa que a factory nunca fez: um parser que pulasse só o primeiro
        # contaria cabeçalho como medição.
        raw = read_capture(encoder, "pidstat.txt")
        samples = [line for line in raw.splitlines() if line.endswith("ffmpeg")]

        series = parse_pidstat(raw)

        assert len(series) == len(samples)
        assert all(sample > 0 for sample in series)

    def test_the_last_stats_line_counts_every_frame_of_the_clip(self, encoder):
        # O `-stats` reescreve a mesma linha com `\r`, e no FFmpeg de verdade a
        # última ainda vem depois do resumo de muxing: ler qualquer outra
        # reportaria como total do encode o que ele tinha feito no meio.
        stats = parse_ffmpeg_log(read_capture(encoder, "ffmpeg.log"))

        assert stats.frames == CLIP_FRAMES
        assert stats.fps > 0
        assert stats.bitrate_kbps > 0


class TestTime:
    def test_integer_seconds_are_accepted(self):
        # O `%e` de um encode instantâneo sai como `0`, e um modelo que exigisse
        # decimal derrubaria o run em vez de medi-lo.
        assert parse_time(make_time_json(elapsed_s=0)).elapsed_s == 0.0

    def test_a_renamed_field_is_rejected(self):
        # O format string mora no bash: renomear uma chave lá deixaria a coluna
        # vazia para a campanha inteira se o parser aceitasse o que sobrou.
        with pytest.raises(ArtifactError, match="elapsed_s"):
            parse_time(make_time_json(elapsed_s=ABSENT))

    def test_a_field_the_model_does_not_know_is_rejected(self):
        with pytest.raises(ArtifactError, match="cpu_pct"):
            parse_time(make_time_json(cpu_pct=99.0))

    def test_a_number_written_as_string_is_rejected(self):
        with pytest.raises(ArtifactError, match="max_rss_kb"):
            parse_time(make_time_json(max_rss_kb="524288"))

    def test_content_that_is_not_json_is_rejected(self):
        # O `--quiet` some da invocação e o GNU time prefixa "Command exited with
        # non-zero status": é assim que o `time.json` deixa de ser JSON.
        with pytest.raises(ArtifactError):
            parse_time("Command exited with non-zero status 1\n" + make_time_json())


class TestPerf:
    def test_a_header_line_is_skipped(self):
        # O cabeçalho do `perf stat -j` varia com a versão, e é por isso que o
        # `run_scenario.sh` confere o arquivo textualmente.
        counters = parse_perf(make_perf_json(header='{"cpu" : "0", "thread" : "ffmpeg"}'))

        assert counters["cycles"] == 4_000_000_000.0

    def test_an_unsupported_event_comes_out_absent_not_zero(self):
        # `perf stat` não falha quando o evento não existe na arquitetura; zero
        # entraria numa média como medição.
        counters = parse_perf(make_perf_json({"cycles": "<not supported>"}))

        assert counters["cycles"] is None

    def test_an_absent_event_is_absent_from_the_mapping(self):
        counters = parse_perf(make_perf_json({"instructions": 1.0}))

        assert "cycles" not in counters

    def test_content_without_a_single_counter_is_rejected(self):
        with pytest.raises(ArtifactError):
            parse_perf("Performance counter stats for 'ffmpeg':\n")


class TestPidstat:
    def test_the_cpu_series_comes_out_in_order(self):
        # A série é a linha do tempo do encode: reordená-la não estoura, e some
        # na média que a tabela guarda.
        assert parse_pidstat(make_pidstat()) == [98.0, 96.0, 94.0]

    def test_the_column_is_found_by_name_not_by_position(self):
        # `-h -r -u` fixa as colunas hoje; um flag a mais amanhã as desloca, e
        # índice cravado leria `%MEM` como se fosse `%CPU`.
        shifted = make_pidstat().replace("   %CPU   CPU", "   CPU   %CPU")

        assert parse_pidstat(shifted) != parse_pidstat(make_pidstat())

    def test_the_average_line_is_not_a_sample(self):
        raw = make_pidstat() + "Average:       0      4242   92.00    6.00  ffmpeg\n"

        assert parse_pidstat(raw) == [98.0, 96.0, 94.0]

    def test_a_header_that_never_arrived_is_rejected(self):
        # `pidstat` morto antes do primeiro flush deixa o arquivo com bytes que
        # não são amostra nenhuma; devolver série vazia diria "0% de CPU".
        with pytest.raises(ArtifactError):
            parse_pidstat("Linux 6.8.0-31-generic (ip-10-0-0-1) \t08/08/2026\n")


class TestFfmpegLog:
    def test_a_bitrate_the_ffmpeg_did_not_compute_comes_out_absent(self):
        stats = parse_ffmpeg_log(make_ffmpeg_log(bitrate="N/A"))

        assert stats.frames == 7200
        assert stats.bitrate_kbps is None

    def test_a_log_without_progress_is_rejected(self):
        with pytest.raises(ArtifactError):
            parse_ffmpeg_log("ffmpeg version n7.1\nUnknown encoder 'libsvtav1'\n")
