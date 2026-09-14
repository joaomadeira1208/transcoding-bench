# A decisão que o laço de vigilância toma a cada poll (D2/D8/D9 da Spec 4). O que
# pode falhar em silêncio aqui é a precedência entre as três perguntas: uma ordem
# trocada dá "morta" a uma arquitetura que acabou de escrever o marcador — e a
# campanha sai com erro mandando retomar o que já está completo.

from __future__ import annotations

import pytest
from command_output import parse_describe_instances
from conftest import captured, make_done_marker
from status_check import StatusError
from vigilance import (
    UNANSWERED_POLL_LIMIT,
    Liveness,
    Marker,
    Vigilance,
    decide_vigilance,
    marker_verdict,
)

LAUNCHED = "i-0123456789abcdef0"
PREVIOUS_ATTEMPT = "i-0fedcba9876543210"

DEAD_STATES = ("shutting-down", "terminated", "stopping", "stopped")


def decided(
    *,
    described_state: str | None = "running",
    liveness: Liveness = Liveness.ALIVE,
    marker: Marker = Marker.ABSENT,
    unanswered_polls: int = 0,
) -> Vigilance:
    """Um poll saudável, com o caso sob teste como única diferença."""
    return decide_vigilance(
        described_state=described_state,
        liveness=liveness,
        marker=marker,
        unanswered_polls=unanswered_polls,
    )


def described(name: str) -> str:
    """O estado como a CLI de verdade o escreveu — `tests/fixtures/README.md`."""
    instances = parse_describe_instances(captured(name))

    assert len(instances) == 1, name
    return instances[0].state


class TestTheArchitectureStillWorking:
    def test_an_instance_running_with_a_live_process_is_running(self):
        assert decided() is Vigilance.RUNNING

    def test_the_marker_of_the_previous_attempt_with_a_live_process_is_running(self):
        assert decided(marker=Marker.OTHER_INSTANCE) is Vigilance.RUNNING

    def test_the_state_the_cli_calls_running_is_the_one_that_asks_about_the_process(self):
        assert decided(described_state=described("describe-instances-running.json")) is (
            Vigilance.RUNNING
        )


class TestTheArchitectureThatFinished:
    def test_a_valid_marker_is_ready_to_terminate(self):
        assert decided(marker=Marker.VALID) is Vigilance.READY_TO_TERMINATE

    def test_the_normal_end_is_the_marker_with_the_process_already_gone(self):
        assert decided(marker=Marker.VALID, liveness=Liveness.DEAD) is Vigilance.READY_TO_TERMINATE

    def test_the_marker_decides_even_when_the_instance_already_vanished(self):
        assert (
            decided(marker=Marker.VALID, described_state=None, liveness=Liveness.NO_ANSWER)
            is Vigilance.READY_TO_TERMINATE
        )

    @pytest.mark.parametrize(("exit_status", "capped"), [(0, False), (1, False), (1, True)])
    def test_the_exit_status_the_marker_carries_does_not_change_the_decision(
        self, exit_status, capped
    ):
        verdict = marker_verdict(
            make_done_marker(exit_status=exit_status, capped=capped), instance_id=LAUNCHED
        )

        assert decided(marker=verdict, liveness=Liveness.DEAD) is Vigilance.READY_TO_TERMINATE


class TestTheArchitectureThatDied:
    def test_a_dead_process_without_marker_is_dead(self):
        assert decided(liveness=Liveness.DEAD) is Vigilance.DEAD

    def test_a_dead_process_with_the_marker_of_the_previous_attempt_is_dead(self):
        assert decided(liveness=Liveness.DEAD, marker=Marker.OTHER_INSTANCE) is Vigilance.DEAD

    def test_an_instance_absent_from_describe_is_dead(self):
        assert decided(described_state=None) is Vigilance.DEAD

    @pytest.mark.parametrize("state", DEAD_STATES)
    def test_an_instance_on_its_way_out_is_dead(self, state):
        assert decided(described_state=state) is Vigilance.DEAD

    def test_the_state_the_cli_writes_for_a_terminated_instance_is_death(self):
        assert decided(described_state=described("describe-instances-terminated.json")) is (
            Vigilance.DEAD
        )


class TestTheArchitectureThatDoesNotAnswer:
    def test_ssh_without_answer_on_a_running_instance_is_not_death(self):
        assert decided(liveness=Liveness.NO_ANSWER, unanswered_polls=1) is Vigilance.UNRESPONSIVE

    def test_the_poll_at_the_limit_is_still_unresponsive(self):
        assert (
            decided(liveness=Liveness.NO_ANSWER, unanswered_polls=UNANSWERED_POLL_LIMIT)
            is Vigilance.UNRESPONSIVE
        )

    def test_the_poll_after_the_limit_is_death(self):
        assert (
            decided(liveness=Liveness.NO_ANSWER, unanswered_polls=UNANSWERED_POLL_LIMIT + 1)
            is Vigilance.DEAD
        )

    def test_an_instance_that_vanished_is_dead_at_the_first_silent_poll(self):
        assert decided(described_state=None, liveness=Liveness.NO_ANSWER) is Vigilance.DEAD


class TestTheArchitectureStillComingUp:
    def test_an_architecture_launched_and_not_yet_dispatched_is_bootstrapping(self):
        assert decided(liveness=Liveness.NOT_DISPATCHED) is Vigilance.BOOTSTRAPPING

    def test_the_silence_before_the_dispatch_never_becomes_death(self):
        assert (
            decided(liveness=Liveness.NOT_DISPATCHED, unanswered_polls=UNANSWERED_POLL_LIMIT + 1)
            is Vigilance.BOOTSTRAPPING
        )

    def test_an_instance_that_describe_still_calls_pending_is_bootstrapping(self):
        assert decided(described_state="pending", liveness=Liveness.NOT_DISPATCHED) is (
            Vigilance.BOOTSTRAPPING
        )

    def test_an_instance_that_vanished_before_the_dispatch_is_dead(self):
        assert decided(described_state=None, liveness=Liveness.NOT_DISPATCHED) is Vigilance.DEAD


class TestTheVerdictAboutTheMarker:
    def test_no_object_in_status_is_an_absent_marker(self):
        assert marker_verdict(None, instance_id=LAUNCHED) is Marker.ABSENT

    def test_the_marker_of_the_launched_instance_is_valid(self):
        assert marker_verdict(make_done_marker(), instance_id=LAUNCHED) is Marker.VALID

    def test_the_marker_of_the_previous_attempt_is_of_another_instance(self):
        assert marker_verdict(make_done_marker(), instance_id=PREVIOUS_ATTEMPT) is (
            Marker.OTHER_INSTANCE
        )

    def test_a_malformed_marker_is_refused_by_the_reader_that_owns_it(self):
        with pytest.raises(StatusError, match="capped"):
            marker_verdict(make_done_marker(capped="false"), instance_id=LAUNCHED)
