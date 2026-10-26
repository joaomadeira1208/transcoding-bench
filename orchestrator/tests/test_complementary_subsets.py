# Os três experimentos complementares (ADR-0026) só se comparam com a campanha
# Cenário a Cenário: um CRF copiado errado, um par a mais ou uma seed trocada não
# estouram em lugar nenhum, e a ponte `c7i-smt` x `c7i.xlarge` passaria a comparar
# dois encodes diferentes. O que cada definição pode declarar à sua maneira são as
# instâncias, e no `contadores.toml` a instrumentação.

from __future__ import annotations

import json
import tomllib
from dataclasses import replace
from typing import Any

import pytest
from conftest import REPO_ROOT, real_config
from experiment_config import CpuOptions, ExperimentConfig, validate_config
from scenario_plan import build_canonical_plan
from test_pilot_subset import subset_divergences

INSTANCE_FREE = frozenset({"instance"})

# Os campos do objeto de run que a instrumentação decide: os únicos que o
# `contadores.toml` muda.
INSTRUMENTATION_FIELDS = ("pmu_events", "perf_event_spec", "pmu_hardware_events", "pmu_metrics")

DEFINITIONS = {
    "topologia": INSTANCE_FREE,
    "repetibilidade": INSTANCE_FREE,
    "contadores": INSTANCE_FREE | {"instrumentation"},
}


def definition(name: str) -> ExperimentConfig:
    with (REPO_ROOT / "config" / f"{name}.toml").open("rb") as handle:
        return validate_config(tomllib.load(handle))


def scenarios(config: ExperimentConfig, *, without: tuple[str, ...] = ()) -> dict[str, set[str]]:
    """Os objetos de run de cada instância, sem o que os amarra a ela.

    A `scenario_id` perde o segmento da instância e o campo `instance` sai: o que
    sobra é o encode, que é o que tem de ser o mesmo da campanha.
    """
    plan = build_canonical_plan(config)
    by_instance: dict[str, set[str]] = {}
    for block in plan["blocks"]:
        for run in block["runs"]:
            by_instance.setdefault(run["instance"], set()).add(_frozen(run, without))
    return by_instance


def _frozen(run: dict[str, Any], without: tuple[str, ...]) -> str:
    detached = {key: value for key, value in run.items() if key not in {"instance", *without}}
    detached["scenario_id"] = run["scenario_id"].replace(f"_{run['instance']}_", "_{instance}_")
    return json.dumps(detached, sort_keys=True)


def campaign_scenarios(*, without: tuple[str, ...] = ()) -> set[str]:
    (c7g, *_) = scenarios(real_config(), without=without).values()
    return c7g


@pytest.mark.parametrize(("name", "free"), DEFINITIONS.items())
class TestEachDefinition:
    def test_it_diverges_from_the_campaign_only_where_it_may(self, name, free):
        assert subset_divergences(definition(name), real_config(), free=free) == []

    def test_every_scenario_of_every_instance_is_a_campaign_scenario(self, name, free):
        without = INSTRUMENTATION_FIELDS if "instrumentation" in free else ()
        campaign = campaign_scenarios(without=without)

        for instance, runs in scenarios(definition(name), without=without).items():
            assert runs - campaign == set(), instance


class TestTheGuardItself:
    def test_another_crf_in_one_arm_is_caught_at_the_scenario(self):
        topologia = definition("topologia")
        recrf = replace(
            topologia,
            codecs=tuple(replace(codec, crf=codec.crf + 1) for codec in topologia.codecs),
        )

        for runs in scenarios(recrf).values():
            assert runs - campaign_scenarios()

    def test_the_instance_does_not_leak_into_what_is_compared(self):
        assert all("c7i-smt" not in run for run in scenarios(definition("topologia"))["c7i-smt"])


class TestTopologia:
    def test_each_arm_runs_the_whole_campaign_matrix(self):
        for runs in scenarios(definition("topologia")).values():
            assert runs == campaign_scenarios()

    def test_the_two_arms_are_the_same_type_with_the_two_topologies(self):
        arms = {
            each.id: (each.instance_type, each.cpu_options)
            for each in definition("topologia").instances
        }

        assert arms == {
            "c7i-smt": ("c7i.2xlarge", CpuOptions(core_count=2, threads_per_core=2)),
            "c7i-nosmt": ("c7i.2xlarge", CpuOptions(core_count=4, threads_per_core=1)),
        }

    def test_both_arms_see_the_four_vcpus_of_the_campaign(self):
        assert {each.cpu_options.vcpus for each in definition("topologia").instances} == {4}


class TestRepetibilidade:
    def test_three_new_hosts_of_each_type_of_the_campaign(self):
        hosts = [(each.id, each.instance_type) for each in definition("repetibilidade").instances]

        assert hosts == [
            (f"{family}-r{n}", f"{family}.xlarge")
            for family in ("c7g", "c7i", "c7a")
            for n in (1, 2, 3)
        ]

    def test_the_scope_is_the_three_codecs_of_bbb_from_1080p_to_720p(self):
        config = definition("repetibilidade")

        assert [codec.slug for codec in config.codecs] == ["libx264", "libx265", "libsvtav1"]
        assert [str(pair) for pair in config.pairs] == ["1080p -> 720p"]
        assert [video.slug for video in config.videos] == ["bbb"]

    def test_each_host_runs_the_block_of_six(self):
        plan = build_canonical_plan(definition("repetibilidade"))

        assert {len(block["runs"]) for block in plan["blocks"]} == {6}
        assert len(plan["blocks"]) == 9 * 3


class TestContadores:
    def test_one_graviton_with_no_topology_of_its_own(self):
        (instance,) = definition("contadores").instances

        assert (instance.id, instance.instance_type, instance.cpu_options) == (
            "c7g-pmu",
            "c7g.xlarge",
            None,
        )

    def test_a_single_hardware_pair_and_the_four_software_events(self):
        instrumentation = definition("contadores").instrumentation

        assert instrumentation.pmu_events == (
            "cycles",
            "instructions",
            "task-clock",
            "context-switches",
            "cpu-migrations",
            "page-faults",
        )
        assert [metric.name for metric in instrumentation.metrics] == ["ipc"]
        assert instrumentation.hardware_events == ("cycles", "instructions")

    def test_the_scope_is_the_one_of_the_repeatability(self):
        assert scenarios(definition("contadores"), without=INSTRUMENTATION_FIELDS)[
            "c7g-pmu"
        ] == next(
            iter(scenarios(definition("repetibilidade"), without=INSTRUMENTATION_FIELDS).values())
        )
