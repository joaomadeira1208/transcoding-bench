# O arquivo de estado que o `run` escreve e o `watch` relê (D12 da Spec 4). O que
# pode falhar em silêncio é o campo lido frouxo: um `instance_id` ausente vira
# `terminate-instances` sem argumento, e um trocado termina a instância errada —
# num arquivo cuja razão de existir é sobreviver à morte de quem o escreveu.

from __future__ import annotations

import json
from typing import Any

import pytest
from campaign_state import StateError, TrackedInstance, parse_state, serialize_state
from conftest import ABSENT, make_campaign_state, make_done_marker, make_tracked_instance
from vigilance import Vigilance

TOP_FIELDS = ("bucket", "config_path", "commit", "total_timeout", "slice_keys", "instances")

TRACKED_FIELDS = (
    "instance",
    "instance_id",
    "instance_type",
    "pid",
    "block_count",
    "runs_total",
    "state",
    "outcome",
)


def deformed(**overrides: Any) -> dict[str, Any]:
    """O arquivo da factory com a **segunda** arquitetura deformada.

    A segunda, e não a primeira: o índice na mensagem é o que diz ao pesquisador
    qual das três linhas do arquivo ele tem de consertar.
    """
    state = make_campaign_state()
    state["instances"][1] = make_tracked_instance(**overrides)
    return state


def message(payload: Any) -> str:
    with pytest.raises(StateError) as raised:
        parse_state(payload)
    return str(raised.value)


class TestTheFileTheRunWrites:
    def test_the_documented_shape_is_accepted(self):
        state = parse_state(make_campaign_state())

        assert state.bucket == "transcoding-bench-123456789012-pilot"
        assert state.config_path == "config/pilot.toml"
        assert state.commit == "ffd4f43a1b2c3d4e5f60718293a4b5c6d7e8f900"
        assert state.slice_keys == ("scenarios/c7g.json", "scenarios/c7i.json")

    def test_the_fields_the_vigilance_asks_the_three_questions_with(self):
        state = parse_state(make_campaign_state())

        assert state.instances[0] == TrackedInstance(
            instance="c7g",
            instance_id="i-0123456789abcdef0",
            instance_type="c7g.xlarge",
            pid=4242,
            block_count=6,
            runs_total=36,
            state=Vigilance.RUNNING,
            outcome=None,
        )

    def test_the_architecture_that_finished_keeps_the_marker_that_ended_it(self):
        listed = [make_tracked_instance(state="finished", outcome=make_done_marker(runs_failed=2))]

        outcome = parse_state(make_campaign_state(instances=listed)).instances[0].outcome

        assert outcome is not None
        assert outcome.runs_failed == 2

    def test_the_cap_the_launch_promised_each_instance_survives_in_the_file(self):
        assert parse_state(make_campaign_state()).total_timeout == 120 * 60 * 60

    def test_an_architecture_launched_and_not_yet_dispatched_has_no_pid(self):
        state = parse_state(make_campaign_state())

        assert state.instances[1].pid is None
        assert state.instances[1].state is Vigilance.BOOTSTRAPPING

    def test_the_file_written_before_the_first_launch_lists_the_slices_and_no_instance(self):
        state = parse_state(make_campaign_state(instances=[]))

        assert state.slice_keys == ("scenarios/c7g.json", "scenarios/c7i.json")
        assert state.instances == ()


class TestTheFieldsItRefuses:
    def test_a_payload_that_is_not_an_object_is_refused(self):
        assert "estado" in message([])

    @pytest.mark.parametrize("field", TOP_FIELDS)
    def test_a_missing_field_is_refused_by_name(self, field):
        assert field in message(make_campaign_state(**{field: ABSENT}))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("bucket", ""),
            ("bucket", None),
            ("config_path", 42),
            ("commit", ""),
            ("total_timeout", "432000"),
            ("total_timeout", 0),
            ("total_timeout", -1),
            ("slice_keys", "scenarios/c7g.json"),
            ("instances", {}),
            ("instances", "c7g"),
        ],
    )
    def test_a_field_of_the_wrong_type_is_refused_by_name(self, field, value):
        assert field in message(make_campaign_state(**{field: value}))

    @pytest.mark.parametrize("key", ["", None, ["scenarios/c7i.json"]])
    def test_a_slice_key_that_is_not_a_key_is_refused_by_index(self, key):
        listed = ["scenarios/c7g.json", key]

        assert "slice_keys[1]" in message(make_campaign_state(slice_keys=listed))

    def test_an_architecture_that_is_not_an_object_is_refused_by_index(self):
        listed = [make_tracked_instance(), "c7i"]

        assert "instances[1]" in message(make_campaign_state(instances=listed))

    @pytest.mark.parametrize("field", TRACKED_FIELDS)
    def test_an_architecture_missing_a_field_is_refused_by_index_and_name(self, field):
        assert f"instances[1].{field}" in message(deformed(**{field: ABSENT}))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("instance", ""),
            ("instance_id", ""),
            ("instance_id", None),
            ("instance_type", 7),
            ("pid", "4242"),
            ("pid", True),
            ("pid", 0),
            ("pid", -1),
            ("block_count", "6"),
            ("block_count", 6.0),
            ("runs_total", True),
            ("state", "acordada"),
            ("state", None),
            ("outcome", "done"),
        ],
    )
    def test_an_architecture_field_of_the_wrong_type_is_refused_by_index_and_name(
        self, field, value
    ):
        assert f"instances[1].{field}" in message(deformed(**{field: value}))

    def test_a_deformed_marker_is_refused_by_the_reader_that_owns_it(self):
        assert "capped" in message(deformed(outcome=make_done_marker(capped="false")))

    def test_the_marker_of_a_previous_attempt_is_refused_instead_of_ignored(self):
        stale = make_done_marker(instance_id="i-0fedcba9876543210")

        assert "outra instância" in message(deformed(outcome=stale))


class TestTheRoundTrip:
    def test_what_the_run_writes_the_watch_reads(self):
        state = parse_state(make_campaign_state())

        assert parse_state(json.loads(serialize_state(state))) == state

    def test_serializing_what_was_parsed_reproduces_the_file(self):
        payload = make_campaign_state()

        assert json.loads(serialize_state(parse_state(payload))) == payload

    def test_the_bytes_do_not_depend_on_the_order_the_file_had(self):
        payload = make_campaign_state()
        shuffled = {
            **{field: payload[field] for field in reversed(TOP_FIELDS)},
            "instances": [dict(reversed(list(each.items()))) for each in payload["instances"]],
        }

        assert serialize_state(parse_state(shuffled)) == serialize_state(parse_state(payload))

    def test_the_marker_survives_the_round_trip_of_a_watch_that_restarted(self):
        listed = [make_tracked_instance(state="finished", outcome=make_done_marker(capped=True))]
        state = parse_state(make_campaign_state(instances=listed))

        assert parse_state(json.loads(serialize_state(state))) == state

    def test_the_file_ends_in_a_newline(self):
        assert serialize_state(parse_state(make_campaign_state())).endswith("}\n")
