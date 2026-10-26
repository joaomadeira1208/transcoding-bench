# As colunas de PMU saem da definição do lançamento (ADR-0006). Os modos de falha
# não estouram: uma coluna fixa que a definição não mede nasce nula e reprova o
# gate de um lançamento são — o `contadores.toml` não tem os eventos de branch —,
# e um evento que a definição mede e a tabela não declara some do Parquet.

from __future__ import annotations

import tomllib
from functools import cache
from typing import Any

import pytest
from conftest import (
    CONFIG_DIR,
    definition_pmu,
    make_ffmpeg_log,
    make_meta_json,
    make_perf_json,
    make_pidstat,
    make_time_json,
)
from run_meta import load_meta
from run_table import RawRun, consolidate_runs, pcnt_column, perf_column, table_schema

CONFIGS = tuple(sorted(path.stem for path in CONFIG_DIR.glob("*.toml")))


@cache
def declared(config: str) -> dict[str, Any]:
    with (CONFIG_DIR / f"{config}.toml").open("rb") as handle:
        return tomllib.load(handle)["instrumentation"]


def operands(config: str) -> dict[str, tuple[str, str]]:
    return {
        record["name"]: (record["numerator"], record["denominator"])
        for record in declared(config)["metric"]
    }


@pytest.mark.parametrize("config", CONFIGS)
class TestTheColumnsOfEachDefinition:
    def test_the_events_are_the_ones_it_measures_in_order(self, config: str) -> None:
        assert list(definition_pmu(config).events) == declared(config)["pmu_events"]

    def test_every_ratio_divides_the_pair_the_metric_declares(self, config: str) -> None:
        # Pelos dois nomes, e não por eles pertencerem ao `pmu_events`: repontar
        # o `branch_mispredict_rate` para outro par que já esteja na lista deixaria a
        # análise dividindo o par antigo com tudo verde.
        assert dict(definition_pmu(config).metrics) == operands(config)

    def test_every_event_has_a_counter_column_and_a_regime_column(self, config: str) -> None:
        names = table_schema(definition_pmu(config)).names

        for event in declared(config)["pmu_events"]:
            assert perf_column(event) in names
            assert pcnt_column(event) in names

    def test_every_ratio_is_a_column(self, config: str) -> None:
        names = table_schema(definition_pmu(config)).names

        assert set(operands(config)) <= set(names)

    def test_every_column_is_lower_case(self, config: str) -> None:
        names = table_schema(definition_pmu(config)).names

        assert names == [name.lower() for name in names]


# O que o `perf stat` da `c7g-pmu` escreve: um par de hardware e os quatro de
# software, nada multiplexado.
REDUCED_COUNTERS = {
    "cycles": 4_000_000_000,
    "instructions": 6_000_000_000,
    "task-clock": 312450.0,
    "context-switches": 1204,
    "cpu-migrations": 12,
    "page-faults": 183422,
}


class TestTheReducedInstrumentation:
    def test_the_table_has_no_column_for_what_the_definition_does_not_measure(self):
        names = table_schema(definition_pmu("contadores")).names

        assert "perf_branch_misses" not in names
        assert "branch_mispredict_rate" not in names
        assert {"perf_cycles", "perf_instructions", "ipc"} <= set(names)

    def test_a_run_of_the_reduced_events_consolidates_without_nulls(self):
        run = RawRun(
            meta=load_meta(make_meta_json(instance="c7g-pmu")),
            time=make_time_json(),
            perf=make_perf_json(REDUCED_COUNTERS),
            pidstat=make_pidstat(),
            ffmpeg=make_ffmpeg_log(),
            sha256="a3f1c0de\n",
        )

        result = consolidate_runs([run], definition_pmu("contadores"))
        (row,) = result.table.to_pylist()

        assert result.unreadable == ()
        assert row["ipc"] == 1.5
        assert row["perf_cycles_pcnt_running"] == 100.0
        assert all(value is not None for name, value in row.items() if name.startswith("perf_"))

    def test_a_definition_that_is_not_the_one_the_run_measured_is_reported(self):
        # O modo de falha que a ADR-0006 temia ao recusar o TOML na consolidação:
        # a definição errada sobre runs certos daria colunas nulas sem dizer nada.
        run = RawRun(
            meta=load_meta(make_meta_json()),
            time=make_time_json(),
            perf=make_perf_json(),
            pidstat=make_pidstat(),
            ffmpeg=make_ffmpeg_log(),
            sha256="a3f1c0de\n",
        )

        result = consolidate_runs([run], definition_pmu("contadores"))

        (message,) = result.unreadable
        assert message.startswith("9f0c4a2e-6b41-4d5f-8a37-2f1c8de0b7a4/perf.json")
        assert "branch-misses" in message
