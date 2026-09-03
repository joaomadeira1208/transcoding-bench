# A tabela é o que o artigo reporta, e cada regra aqui tem um modo de falha que
# não estoura: warm-up filtrado pela string em vez do campo, dedup ordenando
# timestamps como texto, denominador zero virando `NaN` no meio de uma média, e
# um run falho sumindo da tabela em vez de aparecer com o seu `exit_code`.

from __future__ import annotations

from conftest import make_ffmpeg_log, make_meta_json, make_perf_json, make_pidstat, make_time_json
from run_meta import load_meta
from run_table import TABLE_SCHEMA, RawRun, consolidate_runs, summarize


def raw_run(
    *,
    time: str | None = make_time_json(),
    perf: str | None = make_perf_json(),
    pidstat: str | None = make_pidstat(),
    ffmpeg: str | None = make_ffmpeg_log(),
    sha256: str | None = "a3f1c0de\n",
    **meta_overrides: object,
) -> RawRun:
    return RawRun(
        meta=load_meta(make_meta_json(**meta_overrides)),
        time=time,
        perf=perf,
        pidstat=pidstat,
        ffmpeg=ffmpeg,
        sha256=sha256,
    )


def rows(*runs: RawRun) -> list[dict]:
    return consolidate_runs(runs).table.to_pylist()


def single(**overrides: object) -> dict:
    """A linha de uma Execução só, que é a forma da maioria das asserções aqui."""
    (row,) = rows(raw_run(**overrides))
    return row


class TestSelection:
    def test_warmup_goes_out_by_the_field_and_not_by_the_id(self):
        # As duas `scenario_id` dizem o contrário do campo: se alguém parsear a
        # string, sobra a linha errada e a média sobe com o warm-up dentro.
        warmup = raw_run(scenario_id="libx264_2160p_1080p_bbb_c7g_rep2", warmup=True)
        replication = raw_run(scenario_id="libx264_2160p_1080p_bbb_c7g_warmup", warmup=False)

        assert [row["scenario_id"] for row in rows(warmup, replication)] == [
            "libx264_2160p_1080p_bbb_c7g_warmup"
        ]

    def test_the_latest_started_at_wins_comparing_instants(self):
        # `...T23:00:00+00:00` e `...T01:00:00+03:00` são o mesmo dia em fusos
        # diferentes: o segundo é uma hora **antes** e ordena depois como string.
        earlier = raw_run(run_id="refeito", started_at="2026-08-09T01:00:00+03:00")
        later = raw_run(run_id="original", started_at="2026-08-08T23:00:00+00:00")

        assert [row["run_id"] for row in rows(earlier, later)] == ["original"]

    def test_rows_come_ordered_by_scenario_id(self):
        third = raw_run(scenario_id="libx265_2160p_1080p_bbb_c7g_rep1")
        first = raw_run(scenario_id="libsvtav1_2160p_1080p_bbb_c7g_rep1")
        second = raw_run(scenario_id="libx264_2160p_1080p_bbb_c7g_rep1")

        assert [row["scenario_id"] for row in rows(third, first, second)] == [
            "libsvtav1_2160p_1080p_bbb_c7g_rep1",
            "libx264_2160p_1080p_bbb_c7g_rep1",
            "libx265_2160p_1080p_bbb_c7g_rep1",
        ]

    def test_a_failed_run_stays_in_the_table_with_its_exit_code(self):
        # Removê-lo faria o Parquet mentir por omissão: um Cenário que só falhou
        # sumiria sem rastro.
        failed = raw_run(scenario_id="libx264_2160p_1080p_bbb_c7g_rep2", exit_code=70)

        table = consolidate_runs([raw_run(), failed])

        assert [row["exit_code"] for row in table.table.to_pylist()] == [0, 70]
        assert table.failed == 1

    def test_the_report_counts_what_was_dropped(self):
        result = consolidate_runs(
            [
                raw_run(warmup=True, scenario_id="libx264_2160p_1080p_bbb_c7g_warmup"),
                raw_run(run_id="original", started_at="2026-08-08T10:00:00+00:00"),
                raw_run(run_id="refeito", started_at="2026-08-08T11:00:00+00:00"),
            ]
        )

        assert (result.warmups, result.duplicates, result.failed) == (1, 1, 0)
        assert "1" in summarize(result)


class TestDerived:
    def test_the_four_derived_columns(self):
        row = single()

        assert row["ipc"] == 1.5
        assert row["cache_miss_rate"] == 0.25
        assert row["branch_mispredict_rate"] == 0.02
        assert row["cpu_pct_avg"] == 96.0

    def test_a_zero_denominator_is_an_explicit_null(self):
        row = single(perf=make_perf_json({"cycles": 0, "instructions": 6e9}))

        assert row["ipc"] is None
        assert row["perf_cycles"] == 0.0

    def test_an_absent_counter_is_an_explicit_null(self):
        row = single(perf=make_perf_json({"instructions": 6e9}))

        assert row["ipc"] is None
        assert row["perf_cycles"] is None

    def test_an_unsupported_counter_is_an_explicit_null(self):
        row = single(perf=make_perf_json({"cycles": "<not supported>", "instructions": 6e9}))

        assert row["ipc"] is None

    def test_a_pidstat_without_samples_is_an_explicit_null(self):
        row = single(pidstat=make_pidstat(cpu_pct=()))

        assert row["cpu_pct_avg"] is None


class TestColumns:
    def test_the_pidstat_series_stays_out_of_the_table(self):
        # Três amostras entram como **uma** linha: a série continua no raw dir, e
        # só o agregado atravessa (ADR-0007).
        assert len(rows(raw_run(pidstat=make_pidstat(cpu_pct=(10.0, 20.0, 30.0))))) == 1
        assert "cpu_pct_avg" in TABLE_SCHEMA.names

    def test_the_row_carries_scenario_run_metadata_and_aggregates(self):
        row = single()

        assert row["encoder"] == "libx264"
        assert row["crf"] == 23
        assert row["encoder_args"] == ["-sc_threshold", "0"]
        assert row["instance_type"] == "c7g.xlarge"
        assert dict(row["versions"])["ffmpeg"] == "n7.1"
        assert row["time_elapsed_s"] == 312.45
        assert row["time_max_rss_kb"] == 524288
        assert row["perf_instructions"] == 6_000_000_000.0
        assert row["ffmpeg_frames"] == 7200
        assert row["ffmpeg_bitrate_kbps"] == 4521.3
        assert row["output_sha256"] == "a3f1c0de"

    def test_absent_artifacts_come_out_null(self):
        row = single(time=None, perf=None, pidstat=None, ffmpeg=None, sha256=None)

        assert row["scenario_id"] == "libx264_2160p_1080p_bbb_c7g_rep1"
        assert row["time_elapsed_s"] is None
        assert row["perf_cycles"] is None
        assert row["ffmpeg_frames"] is None
        assert row["cpu_pct_avg"] is None
        assert row["output_sha256"] is None


class TestMalformedArtifacts:
    def test_of_a_failed_run_the_row_survives_in_silence(self):
        # O `run_scenario.sh` marca `exit_code` 70 justamente quando a
        # instrumentação saiu quebrada: relatá-la de novo afogaria em ruído o
        # caso em que o artefato torto é surpresa.
        result = consolidate_runs(
            [raw_run(exit_code=70, time="Command exited with non-zero status 1\n")]
        )
        (row,) = result.table.to_pylist()

        assert row["exit_code"] == 70
        assert row["time_elapsed_s"] is None
        assert result.unreadable == ()

    def test_of_a_successful_run_the_row_survives_and_the_artifact_is_reported(self):
        # Aqui o artefato quebrado não é estado esperado: ou o parser está
        # errado, ou a ferramenta mudou de forma — e a coluna sairia vazia para a
        # campanha inteira. Derrubar a consolidação, porém, apagaria as outras
        # 809 linhas por causa de um run.
        result = consolidate_runs([raw_run(time="Command exited with non-zero status 1\n")])
        (row,) = result.table.to_pylist()

        assert row["time_elapsed_s"] is None
        assert row["ipc"] == 1.5
        assert len(result.unreadable) == 1
        assert "time.json" in result.unreadable[0]

    def test_the_report_names_the_run(self):
        result = consolidate_runs([raw_run(perf="sem contador nenhum\n")])

        assert result.unreadable[0].startswith("9f0c4a2e-6b41-4d5f-8a37-2f1c8de0b7a4/perf.json")
