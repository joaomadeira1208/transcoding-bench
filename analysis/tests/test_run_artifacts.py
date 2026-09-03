# Os quatro artefatos de instrumentação da ADR-0006 são texto de ferramenta lido
# em Python, e cada parser tem o seu modo de falha calado: uma chave renomeada no
# format string do `/usr/bin/time`, um `<not supported>` do `perf` virando zero em
# vez de ausente, a coluna `%CPU` mudando de posição no `pidstat`, e o `-stats` do
# FFmpeg lido na linha intermediária em vez da final.

from __future__ import annotations

import pytest
from conftest import ABSENT, make_ffmpeg_log, make_perf_json, make_pidstat, make_time_json
from run_artifacts import (
    ArtifactError,
    parse_ffmpeg_log,
    parse_perf,
    parse_pidstat,
    parse_time,
)


class TestTime:
    def test_the_eleven_fields_of_the_format_string(self):
        time = parse_time(make_time_json())

        assert time.elapsed_s == 312.45
        assert time.user_s == 1180.22
        assert time.sys_s == 12.31
        assert time.max_rss_kb == 524288
        assert time.major_page_faults == 0
        assert time.minor_page_faults == 183422
        assert time.fs_inputs == 0
        assert time.fs_outputs == 81920
        assert time.voluntary_ctx_switches == 1204
        assert time.involuntary_ctx_switches == 9812
        assert time.exit_status == 0

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
    def test_every_counter_comes_out_numeric(self):
        counters = parse_perf(make_perf_json())

        assert counters["cycles"] == 4_000_000_000.0
        assert counters["instructions"] == 6_000_000_000.0
        assert counters["page-faults"] == 183422.0

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
    def test_the_last_stats_line_wins(self):
        # O `-stats` reescreve a mesma linha com `\r`: ler a primeira reportaria
        # os 240 frames do começo como o total do encode.
        stats = parse_ffmpeg_log(make_ffmpeg_log())

        assert stats.frames == 7200
        assert stats.fps == 23.4
        assert stats.bitrate_kbps == 4521.3

    def test_a_bitrate_the_ffmpeg_did_not_compute_comes_out_absent(self):
        stats = parse_ffmpeg_log(make_ffmpeg_log(bitrate="N/A"))

        assert stats.frames == 7200
        assert stats.bitrate_kbps is None

    def test_a_log_without_progress_is_rejected(self):
        with pytest.raises(ArtifactError):
            parse_ffmpeg_log("ffmpeg version n7.1\nUnknown encoder 'libsvtav1'\n")
