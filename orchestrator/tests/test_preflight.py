# O núcleo puro do `preflight`. Os três alvos falham em silêncio, e os três
# custam uma instância faturando: um `perf stat` que não abriu contador passa por
# "rodou" se ninguém olhar o valor, um passo que some da tabela é uma capacidade
# que ninguém provou e o pesquisador acha que sim, e a AMI da arquitetura errada
# sobe um binário que a instância recusa sem que o tipo pedido tenha mudado.

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from conftest import make_infra, real_config
from experiment_config import InstanceRecord
from infra_config import parse_infra
from preflight import (
    NOT_SUPPORTED,
    STEPS,
    Outcome,
    PreflightError,
    Step,
    StepResult,
    encode_target,
    failed,
    perf_counter_values,
    perf_detail,
    perf_probe_command,
    render_table,
    summarize,
)

PMU_EVENTS = real_config().instrumentation.pmu_events


def counter_line(event: str, value: str) -> str:
    return json.dumps(
        {
            "counter-value": value,
            "unit": "",
            "event": event,
            "event-runtime": 1001233,
            "pcnt-running": 100.0,
            "metric-value": "0.000000",
            "metric-unit": "(null)",
        }
    )


def perf_output(values: dict[str, str]) -> str:
    return "\n".join(counter_line(event, value) for event, value in values.items())


def every_event_counted() -> dict[str, str]:
    return {event: "1234567.000000" for event in PMU_EVENTS}


class TestThePerfProbe:
    def test_the_probe_asks_for_the_events_the_configuration_declares(self):
        # O resto deste argv é argv de sistema e fica sem teste (ADR-0022); a
        # lista, não: transcrevê-la aqui faria trocar um evento no TOML mudar o
        # que a campanha mede sem mudar o que o preflight confere.
        argv = perf_probe_command(PMU_EVENTS)

        assert argv[argv.index("-e") + 1] == ",".join(PMU_EVENTS)


class TestThePerfCounters:
    def test_the_ten_events_with_a_numeric_value_are_the_measurement(self):
        counted = perf_counter_values(perf_output(every_event_counted()), PMU_EVENTS)

        assert list(counted) == list(PMU_EVENTS)
        assert set(counted.values()) == {1234567.0}

    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_not_supported_is_refused_naming_the_event(self, event):
        # O modo de falha inteiro do passo: `perf stat` **não** sai não-zero
        # quando o evento não existe naquela PMU (ADR-0006), então quem só olha o
        # código de saída dá o contador por aberto.
        values = every_event_counted() | {event: NOT_SUPPORTED}

        with pytest.raises(PreflightError, match=re.escape(event)) as refusal:
            perf_counter_values(perf_output(values), PMU_EVENTS)
        assert "not supported" in str(refusal.value)

    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_an_event_absent_from_the_output_is_refused_naming_it(self, event):
        values = every_event_counted()
        del values[event]

        with pytest.raises(PreflightError, match=re.escape(event)):
            perf_counter_values(perf_output(values), PMU_EVENTS)

    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_a_counter_value_that_is_not_a_number_is_refused_naming_the_event(self, event):
        values = every_event_counted() | {event: ""}

        with pytest.raises(PreflightError, match=re.escape(event)):
            perf_counter_values(perf_output(values), PMU_EVENTS)

    def test_every_event_without_a_counter_is_named_in_the_same_refusal(self):
        # Uma execução do passo é uma instância: recusar no primeiro evento faria
        # o pesquisador descobrir o segundo evento indisponível daquela
        # arquitetura na instância seguinte.
        values = every_event_counted() | dict.fromkeys(PMU_EVENTS[:3], NOT_SUPPORTED)

        with pytest.raises(PreflightError) as refusal:
            perf_counter_values(perf_output(values), PMU_EVENTS)
        for event in PMU_EVENTS[:3]:
            assert event in str(refusal.value)

    def test_an_empty_output_is_refused_naming_every_event(self):
        with pytest.raises(PreflightError) as refusal:
            perf_counter_values("", PMU_EVENTS)

        for event in PMU_EVENTS:
            assert event in str(refusal.value)

    def test_the_header_the_perf_version_prints_is_ignored(self):
        output = "\n".join(
            ["# started on Fri Sep 11 18:00:00 2026", "", perf_output(every_event_counted())]
        )

        assert len(perf_counter_values(output, PMU_EVENTS)) == len(PMU_EVENTS)

    def test_the_modifier_perf_appends_to_the_event_still_counts(self):
        # `perf` ecoa o evento com o modificador que aplicou (`cycles:u`), e uma
        # comparação exata recusaria um contador que abriu.
        values = {f"{event}:u": "7.000000" for event in PMU_EVENTS}

        assert perf_counter_values(perf_output(values), PMU_EVENTS) == dict.fromkeys(
            PMU_EVENTS, 7.0
        )

    def test_a_zeroed_counter_is_a_counter(self):
        # Zero é medição, não ausência: um comando trivial pode não gerar evento
        # nenhum atribuído ao contador, e recusá-lo seria falso negativo.
        values = dict.fromkeys(PMU_EVENTS, "0.000000")

        assert perf_counter_values(perf_output(values), PMU_EVENTS) == dict.fromkeys(
            PMU_EVENTS, 0.0
        )

    def test_an_event_the_probe_did_not_ask_for_is_not_the_verdict(self):
        values = every_event_counted() | {"duration_time": "<not supported>"}

        assert len(perf_counter_values(perf_output(values), PMU_EVENTS)) == len(PMU_EVENTS)


class TestTheDetailOfThePerfStep:
    def test_the_line_lists_every_event_with_its_value(self):
        detail = perf_detail(perf_counter_values(perf_output(every_event_counted()), PMU_EVENTS))

        for event in PMU_EVENTS:
            assert f"{event} = 1234567" in detail

    def test_the_line_survives_the_table(self):
        detail = perf_detail(perf_counter_values(perf_output(every_event_counted()), PMU_EVENTS))
        results = summarize([StepResult(Step.PERF, Outcome.PASSED, detail)])

        line = next(
            line for line in render_table(results).splitlines() if line.startswith(Step.PERF.value)
        )
        for event in PMU_EVENTS:
            assert event in line


def amis():
    return parse_infra(make_infra()).amis


class TestTheAmiOfTheRequestedType:
    def test_the_arm_type_gets_the_arm_image(self):
        target = encode_target(real_config(), amis(), "c7g.xlarge")

        assert target.instance.arch == "arm64"
        assert target.image_id == amis().encode_arm64

    def test_the_two_x86_types_get_the_x86_image(self):
        # `x86_64` no `experiment.toml` e `amd64` no arquivo de infra: são dois
        # vocabulários, e a tradução entre eles mora nesta função.
        for instance_type in ("c7i.xlarge", "c7a.xlarge"):
            target = encode_target(real_config(), amis(), instance_type)

            assert target.instance.arch == "x86_64"
            assert target.image_id == amis().encode_amd64

    def test_the_slice_is_named_by_the_short_id_of_the_type(self):
        # A fatia que o bootstrap baixa é `scenarios/{id}.json`: derivar o id do
        # tipo em vez de lê-lo do TOML daria um `plan-key` que não existe.
        assert encode_target(real_config(), amis(), "c7g.xlarge").instance.id == "c7g"

    def test_every_declared_instance_type_resolves_to_an_image(self):
        for instance in real_config().instances:
            assert encode_target(real_config(), amis(), instance.instance_type).image_id

    def test_a_type_the_toml_does_not_declare_is_refused_naming_the_declared_ones(self):
        with pytest.raises(PreflightError, match=re.escape("c7g.xlarge")):
            encode_target(real_config(), amis(), "c7q.xlarge")

    def test_an_arch_with_no_ami_is_refused_naming_the_arch(self):
        strange = InstanceRecord(id="c7x", instance_type="c7x.xlarge", arch="riscv")
        config = replace(real_config(), instances=(strange,))

        with pytest.raises(PreflightError, match="riscv"):
            encode_target(config, amis(), "c7x.xlarge")


def passed(step: Step, detail: str = "") -> StepResult:
    return StepResult(step, Outcome.PASSED, detail)


class TestTheResultTable:
    def test_a_step_that_did_not_run_is_neither_pass_nor_fail(self):
        results = summarize([passed(STEPS[0])])

        assert results[0].outcome is Outcome.PASSED
        assert all(result.outcome is Outcome.SKIPPED for result in results[1:])

    def test_every_declared_step_reaches_the_table(self):
        assert tuple(result.step for result in summarize([])) == STEPS

    def test_the_table_follows_the_declared_order_and_not_the_observed_one(self):
        observed = [passed(STEPS[2]), passed(STEPS[0])]

        assert tuple(result.step for result in summarize(observed)) == STEPS

    def test_a_step_recorded_twice_is_refused(self):
        # O laço registra o passo pelo nome; dois registros do mesmo nome são um
        # passo cujo resultado foi sobrescrito por outro — e some da tabela.
        with pytest.raises(PreflightError, match=STEPS[0].value):
            summarize([passed(STEPS[0]), passed(STEPS[0])])

    def test_a_failure_anywhere_is_a_failure(self):
        results = summarize([StepResult(STEPS[0], Outcome.FAILED, "AccessDenied")])

        assert failed(results) is True

    def test_a_table_without_failures_passes_even_with_steps_that_did_not_run(self):
        assert failed(summarize([passed(STEPS[0])])) is False

    def test_a_full_table_of_passes_passes(self):
        assert failed(summarize([passed(step) for step in STEPS])) is False


class TestTheRenderedTable:
    def test_every_step_gets_its_own_line(self):
        rendered = render_table(summarize([]))

        for step in STEPS:
            lines = [line for line in rendered.splitlines() if line.startswith(step.value)]
            assert len(lines) == 1

    def test_the_outcome_and_the_detail_of_a_step_are_on_its_line(self):
        results = summarize([StepResult(STEPS[0], Outcome.FAILED, "AccessDenied no sts")])

        line = next(
            line for line in render_table(results).splitlines() if line.startswith(STEPS[0].value)
        )
        assert Outcome.FAILED.value in line
        assert "AccessDenied no sts" in line

    def test_the_three_outcomes_read_differently(self):
        assert len({outcome.value for outcome in Outcome}) == len(Outcome)

    def test_the_outcome_column_is_aligned(self):
        results = summarize(
            [passed(STEPS[0], "detalhe"), StepResult(STEPS[-1], Outcome.FAILED, "")]
        )
        lines = render_table(results).splitlines()

        columns = {line.index(line.split()[1]) for line in lines}
        assert len(columns) == 1

    def test_a_detail_of_several_lines_stays_in_its_cell(self):
        # O detalhe de uma falha é o `stderr` do comando externo, e um
        # `AccessDenied` da AWS CLI chega quebrado em linhas: solto, ele desmonta
        # a tabela que o passo existe para imprimir.
        results = summarize([StepResult(STEPS[0], Outcome.FAILED, "erro\n  na segunda linha")])

        assert len(render_table(results).splitlines()) == len(STEPS) + 1

    def test_no_line_carries_trailing_whitespace(self):
        rendered = render_table(summarize([passed(STEPS[0], "detalhe")]))

        assert rendered == "\n".join(line.rstrip() for line in rendered.splitlines())
