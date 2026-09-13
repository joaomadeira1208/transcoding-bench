"""O `PMU_EVENTS` da análise contra o `config/experiment.toml` que a campanha mede.

O alvo é a deriva silenciosa entre os dois: o `run_table.py` transcreve a lista de
eventos e lê o par de cada razão por nome, e um evento trocado na spec sem esta
amarra não estoura em lugar nenhum — a coluna nasce nula para a campanha inteira
(ADR-0006/0019).

O papel `analysis/` não importa o validador do `orchestrator/`: são dois papéis com
`requirements.txt` separados (ADR-0017), e o que se confere aqui é o texto do TOML.
"""

from __future__ import annotations

import tomllib
from functools import cache
from typing import Any

import pytest
from conftest import ROLE_ROOT
from run_table import PMU_EVENTS, TABLE_SCHEMA, _pcnt_column, _perf_column

CONFIGS = ("experiment", "pilot")

METRICS = ("ipc", "cache_miss_rate", "branch_mispredict_rate")


@cache
def declared(config: str) -> dict[str, Any]:
    with (ROLE_ROOT.parent / "config" / f"{config}.toml").open("rb") as handle:
        return tomllib.load(handle)["instrumentation"]


def metric(config: str, name: str) -> dict[str, Any]:
    return next(record for record in declared(config)["metric"] if record["name"] == name)


@pytest.mark.parametrize("config", CONFIGS)
class TestTheEventsTheAnalysisReads:
    def test_they_are_the_ones_the_campaign_measures_in_order(self, config: str) -> None:
        assert list(PMU_EVENTS) == declared(config)["pmu_events"]

    @pytest.mark.parametrize("name", METRICS)
    def test_every_declared_metric_is_a_column_over_declared_events(
        self, config: str, name: str
    ) -> None:
        record = metric(config, name)

        assert name in TABLE_SCHEMA.names
        assert {record["numerator"], record["denominator"]} <= set(PMU_EVENTS)


class TestTheColumnsOfTheEvents:
    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_every_event_has_a_counter_column_and_a_regime_column(self, event: str) -> None:
        assert _perf_column(event) in TABLE_SCHEMA.names
        assert _pcnt_column(event) in TABLE_SCHEMA.names

    def test_every_column_is_lower_case(self) -> None:
        # `L1-dcache-loads` é o único evento com maiúscula, e uma coluna
        # `perf_L1_dcache_loads` no meio de um schema em minúsculas faz um
        # `SELECT` correto voltar vazio em engine que diferencia caixa.
        assert TABLE_SCHEMA.names == [name.lower() for name in TABLE_SCHEMA.names]
