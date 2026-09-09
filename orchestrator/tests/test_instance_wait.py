# O laço de espera com relógio falso: é o que permite exercer timeout e conclusão
# sem AWS, sem `sleep` e sem fake de `subprocess` (ADR-0022). O probe é uma lista
# de respostas prontas — a regra "função pura recebe dado já buscado", aplicada
# ao tempo.

from __future__ import annotations

from typing import Any

import pytest
from command_output import CloudInitStatus
from conftest import make_instance_state
from instance_wait import (
    BootstrapError,
    WaitTimeout,
    is_instance_ready,
    wait_for_bootstrap,
    wait_for_instance_ready,
)


class FakeClock:
    """Relógio monotônico que só anda quando o laço dorme."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Probe:
    """Respostas prontas, e a última se repete enquanto o laço insistir."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        return self.answers[min(self.calls, len(self.answers)) - 1]


class TestIsInstanceReady:
    def test_running_with_a_private_ip(self):
        assert is_instance_ready(make_instance_state("running", private_ip="10.0.1.42")) is True

    def test_running_without_a_private_ip(self):
        # É pelo IP privado que o SSH abre: a regra de ingress das efêmeras
        # referencia o security group do Orquestrador (ADR-0015) e só casa
        # tráfego que chega por dentro da VPC. Com o público no lugar dele o
        # predicado diria "pronta" para uma instância inalcançável.
        assert is_instance_ready(make_instance_state("running", private_ip=None)) is False

    def test_running_without_a_public_ip(self):
        assert is_instance_ready(make_instance_state("running", public_ip=None)) is True

    def test_pending(self):
        assert is_instance_ready(make_instance_state("pending", None, None)) is False

    def test_terminated(self):
        assert is_instance_ready(make_instance_state("terminated", None, None)) is False

    def test_absent(self):
        assert is_instance_ready(None) is False


class TestWaitForInstanceReady:
    def test_returns_the_state_that_satisfied_the_wait(self):
        clock = FakeClock()
        probe = Probe(
            make_instance_state("pending", None, None),
            make_instance_state("running", None, None),
            make_instance_state(),
        )

        ready = wait_for_instance_ready(
            probe,
            instance_id="i-0123456789abcdef0",
            timeout=600,
            poll_interval=15,
            clock=clock.time,
            sleep=clock.sleep,
        )

        assert ready.private_ip == "10.0.1.42"
        assert probe.calls == 3
        assert clock.slept == [15, 15]

    def test_a_first_probe_that_is_already_ready_never_sleeps(self):
        clock = FakeClock()

        wait_for_instance_ready(
            Probe(make_instance_state()),
            instance_id="i-0123456789abcdef0",
            timeout=600,
            poll_interval=15,
            clock=clock.time,
            sleep=clock.sleep,
        )

        assert clock.slept == []

    def test_absent_from_describe_is_not_a_failure(self):
        clock = FakeClock()
        probe = Probe(None, make_instance_state())

        assert (
            wait_for_instance_ready(
                probe,
                instance_id="i-0123456789abcdef0",
                timeout=600,
                poll_interval=15,
                clock=clock.time,
                sleep=clock.sleep,
            )
            == make_instance_state()
        )

    def test_timeout_names_the_instance_and_what_was_expected(self):
        clock = FakeClock()
        probe = Probe(make_instance_state("pending", None, None))

        with pytest.raises(WaitTimeout) as error:
            wait_for_instance_ready(
                probe,
                instance_id="i-0123456789abcdef0",
                timeout=60,
                poll_interval=15,
                clock=clock.time,
                sleep=clock.sleep,
            )

        message = str(error.value)
        assert "i-0123456789abcdef0" in message
        assert "running" in message
        assert "pending" in message

    def test_timeout_stops_at_the_deadline(self):
        clock = FakeClock()
        probe = Probe(make_instance_state("pending", None, None))

        with pytest.raises(WaitTimeout):
            wait_for_instance_ready(
                probe,
                instance_id="i-0123456789abcdef0",
                timeout=60,
                poll_interval=15,
                clock=clock.time,
                sleep=clock.sleep,
            )

        assert clock.now == 60
        assert probe.calls == 5


class TestWaitForBootstrap:
    def test_running_until_done(self):
        clock = FakeClock()
        probe = Probe(CloudInitStatus.RUNNING, CloudInitStatus.RUNNING, CloudInitStatus.DONE)

        wait_for_bootstrap(
            probe,
            instance_id="i-0123456789abcdef0",
            timeout=1800,
            poll_interval=30,
            clock=clock.time,
            sleep=clock.sleep,
        )

        assert probe.calls == 3
        assert clock.slept == [30, 30]

    def test_an_instance_not_answering_ssh_yet_is_not_a_failure(self):
        # O `sshd` só aceita conexão algum tempo depois de a instância virar
        # `running`: um laço que tratasse a primeira recusa como erro morreria
        # na primeira sonda, em todo lançamento.
        clock = FakeClock()
        probe = Probe(None, None, CloudInitStatus.RUNNING, CloudInitStatus.DONE)

        wait_for_bootstrap(
            probe,
            instance_id="i-0123456789abcdef0",
            timeout=1800,
            poll_interval=30,
            clock=clock.time,
            sleep=clock.sleep,
        )

        assert probe.calls == 4

    def test_error_fails_immediately(self):
        clock = FakeClock()
        probe = Probe(CloudInitStatus.ERROR)

        with pytest.raises(BootstrapError, match="i-0123456789abcdef0"):
            wait_for_bootstrap(
                probe,
                instance_id="i-0123456789abcdef0",
                timeout=1800,
                poll_interval=30,
                clock=clock.time,
                sleep=clock.sleep,
            )

        assert probe.calls == 1
        assert clock.slept == []

    def test_timeout_names_the_instance_and_what_was_expected(self):
        clock = FakeClock()

        with pytest.raises(WaitTimeout) as error:
            wait_for_bootstrap(
                Probe(CloudInitStatus.RUNNING),
                instance_id="i-0123456789abcdef0",
                timeout=90,
                poll_interval=30,
                clock=clock.time,
                sleep=clock.sleep,
            )

        message = str(error.value)
        assert "i-0123456789abcdef0" in message
        assert "cloud-init" in message
        assert "running" in message
        assert clock.now == 90

    def test_timeout_distinguishes_an_instance_that_never_answered_ssh(self):
        clock = FakeClock()

        with pytest.raises(WaitTimeout) as error:
            wait_for_bootstrap(
                Probe(None),
                instance_id="i-0123456789abcdef0",
                timeout=90,
                poll_interval=30,
                clock=clock.time,
                sleep=clock.sleep,
            )

        assert "SSH" in str(error.value)
