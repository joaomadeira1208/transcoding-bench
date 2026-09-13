"""O `PMU_EVENTS` da análise contra o `config/experiment.toml` que a campanha mede.

O alvo é a deriva silenciosa entre os dois. O `run_table.py` transcreve a lista de
eventos porque o `meta.json` não a carrega, e o `consolidate.py` lê cada contador
por nome: um evento trocado no TOML sem esta amarra não estoura em lugar nenhum —
a coluna nasce, fica nula para a campanha inteira, e o `cache_miss_rate` some sem
que nada o reporte (ADR-0006, ADR-0019).

O papel `analysis/` não importa o validador do `orchestrator/`: são dois papéis com
`requirements.txt` separados (ADR-0017), e o que se confere aqui é o texto do TOML.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from conftest import ROLE_ROOT
from run_table import PMU_EVENTS, TABLE_SCHEMA

CONFIGS = ("experiment", "pilot")


def declared(name: str) -> dict[str, Any]:
    path = Path(ROLE_ROOT).parent / "config" / f"{name}.toml"
    with path.open("rb") as handle:
        return tomllib.load(handle)["instrumentation"]


def metric(name: str, config: str) -> dict[str, Any]:
    return next(record for record in declared(config)["metric"] if record["name"] == name)


@pytest.mark.parametrize("config", CONFIGS)
class TestTheEventsTheAnalysisReads:
    def test_they_are_the_ones_the_campaign_measures_in_order(self, config: str) -> None:
        assert list(PMU_EVENTS) == declared(config)["pmu_events"]

    @pytest.mark.parametrize("name", ["ipc", "cache_miss_rate", "branch_mispredict_rate"])
    def test_every_derived_ratio_is_a_column_and_reads_declared_events(
        self, config: str, name: str
    ) -> None:
        record = metric(name, config)

        assert name in TABLE_SCHEMA.names
        assert {record["numerator"], record["denominator"]} <= set(PMU_EVENTS)


class TestTheColumnNames:
    def test_every_column_is_lower_case(self) -> None:
        # `L1-dcache-loads` é o único evento com maiúscula, e uma coluna
        # `perf_L1_dcache_loads` no meio de um schema em minúsculas faz um
        # `SELECT` correto voltar vazio em engine que diferencia caixa.
        assert TABLE_SCHEMA.names == [name.lower() for name in TABLE_SCHEMA.names]

    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_every_event_has_a_counter_column_and_a_regime_column(self, event: str) -> None:
        column = f"perf_{event.replace('-', '_').lower()}"

        assert column in TABLE_SCHEMA.names
        assert f"{column}_pcnt_running" in TABLE_SCHEMA.names
