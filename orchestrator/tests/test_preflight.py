# O núcleo puro do `preflight`. Os alvos falham em silêncio, e cada um custa uma
# instância faturando: um `perf stat` que não abriu contador passa por "rodou" se
# ninguém olhar o valor; um contador que responde `0` ou uma razão impossível
# passam por "mediu" se ninguém olhar o que o valor diz; e um passo que some da
# tabela é uma capacidade que ninguém provou e o pesquisador acha que sim.
#
# A primeira rodada do passo reprovou o c7g por `<not counted>` e **aprovou** o
# c7i com o par de cache zerado e o c7a com `branch-misses` quase o dobro de
# `instructions`. Os três casos moram aqui.

from __future__ import annotations

import json
import re

import pytest
from conftest import real_config
from preflight import (
    NOT_COUNTED,
    NOT_SUPPORTED,
    PROBE_SECONDS,
    STEPS,
    Outcome,
    PreflightError,
    Step,
    StepResult,
    failed,
    perf_counters,
    perf_detail,
    perf_probe_command,
    probe_encode_argv,
    render_table,
    summarize,
)
from scenario_plan import build_canonical_plan

INSTRUMENTATION = real_config().instrumentation
PMU_EVENTS = INSTRUMENTATION.pmu_events
HARDWARE_EVENTS = INSTRUMENTATION.hardware_events
SOFTWARE_EVENTS = tuple(event for event in PMU_EVENTS if event not in HARDWARE_EVENTS)
METRICS = INSTRUMENTATION.metrics


def counter_line(event: str, value: str, pcnt_running: float = 100.0) -> str:
    return json.dumps(
        {
            "counter-value": value,
            "unit": "",
            "event": event,
            "event-runtime": 1001233,
            "pcnt-running": pcnt_running,
            "metric-value": "0.000000",
            "metric-unit": "(null)",
        }
    )


def perf_output(values: dict[str, str], pcnt_running: float = 100.0) -> str:
    return "\n".join(counter_line(event, value, pcnt_running) for event, value in values.items())


def every_event_counted() -> dict[str, str]:
    return {event: "1234567.000000" for event in PMU_EVENTS}


def counted(values: dict[str, str], pcnt_running: float = 100.0):
    return perf_counters(perf_output(values, pcnt_running), INSTRUMENTATION)


def first_run() -> dict:
    return build_canonical_plan(real_config())["blocks"][0]["runs"][0]


class TestThePerfProbe:
    def test_the_probe_asks_for_the_events_grouped_as_the_configuration_declares(self):
        # O resto deste argv é argv de sistema e fica sem teste (ADR-0022); o `-e`,
        # não: transcrevê-lo aqui faria trocar um par no TOML mudar o que a
        # campanha mede sem mudar o que o preflight confere.
        argv = perf_probe_command(
            run=first_run(),
            event_spec=INSTRUMENTATION.event_spec,
            repo_dir="/home/ubuntu/transcoding-bench",
            work_dir="/home/ubuntu/work",
        )

        assert argv[argv.index("-e") + 1] == INSTRUMENTATION.event_spec

    def test_the_probe_dumps_the_event_attribute_of_each_name(self):
        # O `-vv` despeja o `perf_event_attr` como o `perf` o abriu, e é onde se
        # lê que o par de cache pediu `PERF_TYPE_HW_CACHE` com o nível L1D — não
        # o evento nativo, que o driver do kernel resolve depois da syscall.
        argv = perf_probe_command(
            run=first_run(),
            event_spec=INSTRUMENTATION.event_spec,
            repo_dir="/home/ubuntu/transcoding-bench",
            work_dir="/home/ubuntu/work",
        )

        assert "-vv" in argv

    def test_the_probe_runs_the_container_of_the_campaign(self):
        # Mesmos mounts e mesma capability do `launch_container.sh`: um degrau só
        # prova o que roda pelo mesmo caminho.
        argv = perf_probe_command(
            run=first_run(),
            event_spec=INSTRUMENTATION.event_spec,
            repo_dir="/home/ubuntu/transcoding-bench",
            work_dir="/home/ubuntu/work",
        )

        assert "--cap-add=PERFMON" in argv
        assert "/home/ubuntu/transcoding-bench/encode:/opt/encode:ro" in argv
        assert "/home/ubuntu/work:/work" in argv

    def test_the_probe_encodes_the_master_the_run_names(self):
        # `-- true` termina em microssegundos: curto demais para o rodízio da PMU
        # girar uma volta, e curto demais para um contador que responde zero
        # provar coisa alguma.
        run = first_run()
        argv = probe_encode_argv(run)

        assert argv[0] == "ffmpeg"
        assert argv[argv.index("-i") + 1].endswith(f"/{run['master']}")
        assert argv[argv.index("-t") + 1] == str(PROBE_SECONDS)

    def test_the_probe_encodes_with_the_parameters_of_that_scenario(self):
        run = first_run()
        argv = probe_encode_argv(run)

        assert argv[argv.index("-c:v") + 1] == run["encoder"]
        assert argv[argv.index("-crf") + 1] == str(run["crf"])
        assert argv[argv.index("-vf") + 1].startswith(
            f"scale={run['output_width']}:{run['output_height']}"
        )

    def test_the_probe_writes_no_output(self):
        # Segundos de encode no disco de uma instância descartável não são dado;
        # o trabalho que a PMU conta é o mesmo com o muxer `null`.
        assert probe_encode_argv(first_run())[-2:] == ["null", "/dev/null"]


class TestThePerfCounters:
    def test_the_ten_events_with_a_numeric_value_are_the_measurement(self):
        measured = counted(every_event_counted())

        assert list(measured) == list(PMU_EVENTS)
        assert {counter.value for counter in measured.values()} == {1234567.0}

    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_not_supported_is_refused_naming_the_event(self, event):
        # `perf stat` **não** sai não-zero quando o evento não existe naquela PMU
        # (ADR-0006), então quem só olha o código de saída dá o contador por
        # aberto.
        values = every_event_counted() | {event: NOT_SUPPORTED}

        with pytest.raises(PreflightError, match=re.escape(event)) as refusal:
            counted(values)
        assert NOT_SUPPORTED in str(refusal.value)

    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_not_counted_is_refused_naming_the_event(self, event):
        # O modo de falha do c7g, e distinto do anterior: o contador abriu e nunca
        # rodou. Um manda trocar o evento, o outro manda olhar o orçamento de
        # contadores — a recusa tem de dizer qual dos dois.
        values = every_event_counted() | {event: NOT_COUNTED}

        with pytest.raises(PreflightError, match=re.escape(event)) as refusal:
            counted(values)
        assert NOT_COUNTED in str(refusal.value)

    @pytest.mark.parametrize("event", HARDWARE_EVENTS)
    def test_a_zeroed_hardware_counter_is_refused_naming_the_event(self, event):
        # O modo de falha do c7i, e o pior dos três: zero é número válido, passa
        # por qualquer guarda de string e vira `cache_miss_rate` nulo para uma
        # arquitetura inteira, descoberto só no `consolidate.py`.
        values = every_event_counted() | {event: "0.000000"}

        with pytest.raises(PreflightError, match=re.escape(event)) as refusal:
            counted(values)
        assert "zero" in str(refusal.value)

    @pytest.mark.parametrize("event", SOFTWARE_EVENTS)
    def test_a_zeroed_software_counter_is_a_measurement(self, event):
        # `context-switches = 0` e `cpu-migrations = 0` são resultados legítimos e
        # desejáveis: recusá-los derrubaria todo run de uma instância ociosa.
        values = every_event_counted() | {event: "0.000000"}

        assert counted(values)[event].value == 0.0

    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_an_event_absent_from_the_output_is_refused_naming_it(self, event):
        values = every_event_counted()
        del values[event]

        with pytest.raises(PreflightError, match=re.escape(event)):
            counted(values)

    @pytest.mark.parametrize("event", PMU_EVENTS)
    def test_a_counter_value_that_is_not_a_number_is_refused_naming_the_event(self, event):
        values = every_event_counted() | {event: ""}

        with pytest.raises(PreflightError, match=re.escape(event)):
            counted(values)

    def test_every_event_without_a_counter_is_named_in_the_same_refusal(self):
        # Uma execução do passo é uma instância: recusar no primeiro evento faria
        # o pesquisador descobrir o segundo evento indisponível daquela
        # arquitetura na instância seguinte.
        values = every_event_counted() | dict.fromkeys(PMU_EVENTS[:3], NOT_SUPPORTED)

        with pytest.raises(PreflightError) as refusal:
            counted(values)
        for event in PMU_EVENTS[:3]:
            assert event in str(refusal.value)

    def test_an_empty_output_is_refused_naming_every_event(self):
        with pytest.raises(PreflightError) as refusal:
            perf_counters("", INSTRUMENTATION)

        for event in PMU_EVENTS:
            assert event in str(refusal.value)

    def test_the_header_the_perf_version_prints_is_ignored(self):
        output = "\n".join(
            ["# started on Fri Sep 11 18:00:00 2026", "", perf_output(every_event_counted())]
        )

        assert len(perf_counters(output, INSTRUMENTATION)) == len(PMU_EVENTS)

    def test_the_modifier_perf_appends_to_the_event_still_counts(self):
        # `perf` ecoa o evento com o modificador que aplicou (`cycles:u`), e uma
        # comparação exata recusaria um contador que abriu.
        values = {f"{event}:u": "7.000000" for event in PMU_EVENTS}

        assert {event: counter.value for event, counter in counted(values).items()} == (
            dict.fromkeys(PMU_EVENTS, 7.0)
        )

    def test_an_event_the_probe_did_not_ask_for_is_not_the_verdict(self):
        values = every_event_counted() | {"duration_time": NOT_SUPPORTED}

        assert len(counted(values)) == len(PMU_EVENTS)


class TestTheMeasurementRegime:
    def test_the_fraction_of_time_each_counter_ran_survives_the_reading(self):
        measured = counted(every_event_counted(), pcnt_running=33.5)

        assert {counter.pcnt_running for counter in measured.values()} == {33.5}

    def test_a_counter_that_ran_a_fraction_of_the_time_is_not_a_refusal(self):
        # Com os pares, fração abaixo de 100 é o regime **esperado** onde a PMU
        # tem menos contadores que eventos: os dois membros do grupo veem a mesma
        # janela, e a razão continua correta. É registro, não recusa.
        assert len(counted(every_event_counted(), pcnt_running=12.0)) == len(PMU_EVENTS)

    def test_a_perf_that_does_not_report_the_fraction_still_counts(self):
        raw = "\n".join(
            json.dumps({"counter-value": "7.000000", "event": event}) for event in PMU_EVENTS
        )

        assert all(
            counter.pcnt_running is None for counter in perf_counters(raw, INSTRUMENTATION).values()
        )


class TestPlausibility:
    @pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.name)
    def test_a_ratio_past_the_declared_ceiling_is_refused_naming_it(self, metric):
        # O que separa "o contador respondeu" de "o contador mediu", e a única
        # coisa que teria transformado os dois `passou` da primeira rodada em
        # falhas honestas: os dois números existem, os dois são não-zero, e nenhum
        # é string de erro.
        values = every_event_counted() | {
            metric.numerator: f"{1234567.0 * metric.max_ratio * 2:.6f}"
        }

        with pytest.raises(PreflightError, match=re.escape(metric.name)) as refusal:
            counted(values)
        assert metric.numerator in str(refusal.value)
        assert metric.denominator in str(refusal.value)

    @pytest.mark.parametrize("metric", METRICS, ids=lambda metric: metric.name)
    def test_a_ratio_exactly_at_the_ceiling_passes(self, metric):
        values = every_event_counted() | {metric.numerator: f"{1234567.0 * metric.max_ratio:.6f}"}

        assert len(counted(values)) == len(PMU_EVENTS)

    def test_the_counters_of_the_c7a_are_refused(self):
        # Os quatro contadores da primeira rodada que sobreviveram à troca do par
        # de cache, verbatim: IPC 18,6 e 942 % de desvios errados. O passo
        # aprovou isto.
        values = every_event_counted() | {
            "cycles": "40210.000000",
            "instructions": "746971.000000",
            "branch-instructions": "146708.000000",
            "branch-misses": "1382176.000000",
        }

        with pytest.raises(PreflightError) as refusal:
            counted(values)
        assert "ipc" in str(refusal.value)
        assert "branch_mispredict_rate" in str(refusal.value)

    def test_every_violated_ratio_is_named_in_the_same_refusal(self):
        values = every_event_counted() | {
            metric.numerator: f"{1234567.0 * metric.max_ratio * 2:.6f}" for metric in METRICS
        }

        with pytest.raises(PreflightError) as refusal:
            counted(values)
        for metric in METRICS:
            assert metric.name in str(refusal.value)

    def test_a_counter_without_a_value_is_reported_before_any_ratio(self):
        # Uma razão sobre um contador que não abriu não diz nada sobre
        # plausibilidade, e o diagnóstico que importa é o do contador.
        values = every_event_counted() | {METRICS[0].denominator: NOT_SUPPORTED}

        with pytest.raises(PreflightError) as refusal:
            counted(values)
        assert METRICS[0].name not in str(refusal.value)


class TestTheDetailOfThePerfStep:
    def test_the_line_lists_every_event_with_its_value(self):
        detail = perf_detail(counted(every_event_counted()))

        for event in PMU_EVENTS:
            assert f"{event} = 1234567" in detail

    def test_the_line_carries_the_regime_next_to_each_value(self):
        # Sem o `pcnt-running` ao lado, uma estimativa e uma contagem entram na
        # mesma coluna indistinguíveis.
        detail = perf_detail(counted(every_event_counted(), pcnt_running=42.0))

        assert detail.count("pcnt-running 42%") == len(PMU_EVENTS)

    def test_the_line_survives_the_table(self):
        detail = perf_detail(counted(every_event_counted()))
        results = summarize([StepResult(Step.PERF, Outcome.PASSED, detail)])

        line = next(
            line for line in render_table(results).splitlines() if line.startswith(Step.PERF.value)
        )
        for event in PMU_EVENTS:
            assert event in line


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
