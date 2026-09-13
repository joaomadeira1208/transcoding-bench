# A lista de eventos e o par de cada razão são transcritos no `run_table.py`, e um
# evento trocado no TOML sem esta amarra não estoura em lugar nenhum: a coluna
# nasce nula para a campanha inteira (ADR-0006).

from __future__ import annotations

import tomllib
from functools import cache
from typing import Any

import pytest
from conftest import ROLE_ROOT
from run_table import METRIC_OPERANDS, PMU_EVENTS, TABLE_SCHEMA, _pcnt_column, _perf_column

CONFIGS = ("experiment", "pilot")


@cache
def declared(config: str) -> dict[str, Any]:
    with (ROLE_ROOT.parent / "config" / f"{config}.toml").open("rb") as handle:
        return tomllib.load(handle)["instrumentation"]


def operands(config: str) -> dict[str, tuple[str, str]]:
    return {
        record["name"]: (record["numerator"], record["denominator"])
        for record in declared(config)["metric"]
    }


@pytest.mark.parametrize("config", CONFIGS)
class TestTheEventsTheAnalysisReads:
    def test_they_are_the_ones_the_campaign_measures_in_order(self, config: str) -> None:
        assert list(PMU_EVENTS) == declared(config)["pmu_events"]

    def test_every_ratio_divides_the_pair_the_metric_declares(self, config: str) -> None:
        # Pelos dois nomes, e não por eles pertencerem ao `pmu_events`: repontar
        # o `branch_mispredict_rate` para outro par que já esteja na lista deixaria a
        # análise dividindo o par antigo com tudo verde.
        assert operands(config) == METRIC_OPERANDS

    @pytest.mark.parametrize("name", METRIC_OPERANDS)
    def test_every_ratio_is_a_column_over_events_the_campaign_measures(
        self, config: str, name: str
    ) -> None:
        assert name in TABLE_SCHEMA.names
        assert set(operands(config)[name]) <= set(PMU_EVENTS)


class TestTheColumnsOfTheEvents:
    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_every_event_has_a_counter_column_and_a_regime_column(self, event: str) -> None:
        assert _perf_column(event) in TABLE_SCHEMA.names
        assert _pcnt_column(event) in TABLE_SCHEMA.names

    def test_every_column_is_lower_case(self) -> None:
        assert TABLE_SCHEMA.names == [name.lower() for name in TABLE_SCHEMA.names]
