# O que o laço de vigilância decide sem perguntar nada a ninguém (D8, D10 e D25 da
# Spec 4). Os dois alvos falham em silêncio e custam a campanha inteira: um prazo
# menor que o teto de cada Instância termina as três na véspera do fim, com 46 h
# faturadas e nenhum dado; e um veredito que devolve zero com uma arquitetura
# morta faz o pesquisador arquivar como completa uma campanha a que falta um
# terço da matriz — que é exatamente o que a linha do `resume.py` existe para
# evitar.

from __future__ import annotations

from typing import Any

import pytest
from campaign_state import CampaignState, TrackedInstance, parse_state
from campaign_watch import (
    DEADLINE_MARGIN_SECONDS,
    failure_reasons,
    poll_line,
    resume_hint,
    summary_lines,
    watch_deadline_seconds,
)
from conftest import make_campaign_state, make_done_marker, make_progress, make_tracked_instance
from instance_launch import BOOTSTRAP_TIMEOUT_SECONDS
from status_check import check_progress
from vigilance import UNANSWERED_POLL_LIMIT, Vigilance

from orchestrator import TOTAL_TIMEOUT_SECONDS

HOUR = 3600.0

FINISHED = {"state": Vigilance.FINISHED.value, "outcome": make_done_marker()}


def deadline(total_timeout: int) -> float:
    """O prazo com os dois outros termos como o `run` de verdade os passa."""
    return watch_deadline_seconds(
        total_timeout=total_timeout, bootstrap_timeout=BOOTSTRAP_TIMEOUT_SECONDS
    )


def architecture(instance: str = "c7g", **overrides: Any) -> TrackedInstance:
    """Uma arquitetura do arquivo de estado, pelo parser que o `watch` usa."""
    return state(make_tracked_instance(instance=instance, **overrides)).instances[0]


def state(*instances: dict[str, Any]) -> CampaignState:
    return parse_state(make_campaign_state(instances=list(instances)))


class TestTheDeadlineOfTheOrchestrator:
    def test_the_deadline_is_the_sum_of_the_cap_the_bootstrap_and_the_margin(self):
        assert watch_deadline_seconds(total_timeout=10, bootstrap_timeout=20, margin=30) == 60

    def test_the_deadline_outlasts_the_cap_of_each_instance(self):
        assert deadline(TOTAL_TIMEOUT_SECONDS) > TOTAL_TIMEOUT_SECONDS

    def test_the_deadline_gives_the_bootstrap_the_time_the_launch_waits_for_it(self):
        assert deadline(TOTAL_TIMEOUT_SECONDS) - TOTAL_TIMEOUT_SECONDS >= (
            BOOTSTRAP_TIMEOUT_SECONDS
        )

    def test_the_margin_is_on_top_of_the_bootstrap_and_not_instead_of_it(self):
        assert deadline(TOTAL_TIMEOUT_SECONDS) == (
            TOTAL_TIMEOUT_SECONDS + BOOTSTRAP_TIMEOUT_SECONDS + DEADLINE_MARGIN_SECONDS
        )

    def test_the_campaign_of_72h_is_watched_for_less_than_74h(self):
        assert deadline(TOTAL_TIMEOUT_SECONDS) < 74 * HOUR

    def test_a_shorter_cap_shortens_the_deadline_by_the_same_amount(self):
        assert deadline(TOTAL_TIMEOUT_SECONDS) - deadline(TOTAL_TIMEOUT_SECONDS - HOUR) == HOUR


class TestTheExitCodeOfTheCampaign:
    def test_three_architectures_that_finished_clean_are_no_reason_at_all(self):
        finished = [make_tracked_instance(instance=each, **FINISHED) for each in ("c7g", "c7i")]

        assert failure_reasons(state(*finished).instances) == ()

    def test_an_architecture_that_died_is_a_reason_naming_it(self):
        reasons = failure_reasons([architecture("c7a", state=Vigilance.DEAD.value, outcome=None)])

        assert len(reasons) == 1
        assert "c7a" in reasons[0]

    def test_a_marker_with_failed_runs_is_a_reason(self):
        marker = make_done_marker(runs_failed=2, exit_status=0)
        reasons = failure_reasons([architecture(**{**FINISHED, "outcome": marker})])

        assert any("2" in reason for reason in reasons)

    def test_a_marker_that_hit_the_cap_is_a_reason(self):
        marker = make_done_marker(capped=True)

        assert failure_reasons([architecture(**{**FINISHED, "outcome": marker})]) != ()

    def test_a_marker_with_a_non_zero_exit_status_is_a_reason(self):
        marker = make_done_marker(exit_status=1)

        assert failure_reasons([architecture(**{**FINISHED, "outcome": marker})]) != ()

    @pytest.mark.parametrize(
        "state_value",
        [Vigilance.BOOTSTRAPPING, Vigilance.RUNNING, Vigilance.UNRESPONSIVE],
    )
    def test_an_architecture_that_never_settled_is_a_reason(self, state_value: Vigilance):
        reasons = failure_reasons([architecture(state=state_value.value, outcome=None)])

        assert reasons != ()

    def test_the_deadline_that_blew_is_a_reason_by_itself(self):
        finished = architecture(**FINISHED)

        assert failure_reasons([finished], deadline_blown=True) != ()

    def test_every_reason_comes_out_at_once(self):
        dead = make_tracked_instance(instance="c7a", state=Vigilance.DEAD.value, outcome=None)
        failed = make_tracked_instance(
            instance="c7i", **{**FINISHED, "outcome": make_done_marker(runs_failed=1)}
        )
        reasons = failure_reasons(state(dead, failed).instances, deadline_blown=True)

        assert len(reasons) == 3

    def test_the_architecture_that_finished_clean_is_never_named(self):
        clean = make_tracked_instance(instance="c7g", **FINISHED)
        dead = make_tracked_instance(instance="c7a", state=Vigilance.DEAD.value, outcome=None)

        reasons = failure_reasons(state(clean, dead).instances)

        assert not any("c7g" in reason for reason in reasons)


class TestTheSummaryPerArchitecture:
    def test_the_architecture_that_finished_reports_what_the_marker_carried(self):
        marker = make_done_marker(runs_total=30, runs_failed=2, capped=True, exit_status=1)
        line = summary_lines([architecture(**{**FINISHED, "outcome": marker})])[0]

        assert "30/36" in line
        assert "2 falhas" in line
        assert "parada pelo teto" in line
        assert "status 1" in line

    def test_the_architecture_that_died_says_there_is_no_marker(self):
        line = summary_lines([architecture("c7a", state=Vigilance.DEAD.value, outcome=None)])[0]

        assert "c7a" in line
        assert "sem marcador" in line

    def test_one_line_per_architecture(self):
        listed = [
            make_tracked_instance(instance="c7g", **FINISHED),
            make_tracked_instance(instance="c7i", state=Vigilance.DEAD.value, outcome=None),
        ]

        assert len(summary_lines(state(*listed).instances)) == 2


class TestTheLineThatPointsAtTheResume:
    def test_the_command_carries_the_definition_and_the_bucket_of_this_launch(self):
        hint = "\n".join(resume_hint(parse_state(make_campaign_state())))

        assert "resume.py" in hint
        assert "config/pilot.toml" in hint
        assert "transcoding-bench-123456789012-pilot" in hint

    def test_the_relaunch_of_the_reduced_slices_is_named_too(self):
        assert any("--slices" in line for line in resume_hint(parse_state(make_campaign_state())))


def line(state_value: Vigilance, *, progress: Any = None, unanswered_polls: int = 0) -> str:
    return poll_line(
        instance="c7g",
        state=state_value,
        progress=progress,
        runs_total=36,
        unanswered_polls=unanswered_polls,
    )


class TestTheLineOfEachPoll:
    def test_the_architecture_that_is_running_is_the_progress_line_and_nothing_else(self):
        progress = check_progress(make_progress(), instance_id="i-0123456789abcdef0")

        assert line(Vigilance.RUNNING, progress=progress).endswith("1h10m")

    def test_the_architecture_that_died_gets_a_line_of_its_own(self):
        assert "morta" in line(Vigilance.DEAD)

    def test_the_architecture_without_an_answer_says_how_many_polls_are_silent(self):
        rendered = line(Vigilance.UNRESPONSIVE, unanswered_polls=2)

        assert "2" in rendered
        assert str(UNANSWERED_POLL_LIMIT) in rendered

    @pytest.mark.parametrize("state_value", list(Vigilance))
    def test_every_state_the_decision_can_return_has_a_line(self, state_value: Vigilance):
        assert line(state_value).startswith(" ")
