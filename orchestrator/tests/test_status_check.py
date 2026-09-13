# Os dois objetos de `status/` que o `run_all.sh` escreve, do lado de quem os lê
# a cada poll (D3/D4 da Spec 4), e a linha que o `run` e o `watch` imprimem.

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import pytest
from conftest import (
    ABSENT,
    captured,
    make_done_marker,
    make_progress,
    real_config,
    real_pilot_config,
)
from scenario_plan import build_canonical_plan
from status_check import (
    LONGEST_SCENARIO_ID,
    DoneMarker,
    Progress,
    StatusError,
    check_done_marker,
    check_progress,
    progress_line,
)

LAUNCHED = "i-0123456789abcdef0"
PREVIOUS_ATTEMPT = "i-0fedcba9876543210"

MARKER_FIELDS = ("instance_id", "finished_at", "runs_total", "runs_failed", "capped", "exit_status")

PROGRESS_FIELDS = (
    "instance_id",
    "block_index",
    "block_count",
    "run_index",
    "run_count",
    "scenario_id",
    "runs_total",
    "runs_failed",
    "elapsed_seconds",
    "written_at",
)


class TestDoneMarker:
    def test_the_marker_of_the_launched_instance_is_valid(self):
        assert check_done_marker(make_done_marker(), instance_id=LAUNCHED) == DoneMarker(
            instance_id=LAUNCHED,
            finished_at="2026-09-07T14:32:07+00:00",
            runs_total=36,
            runs_failed=0,
            capped=False,
            exit_status=0,
        )

    def test_the_marker_of_the_previous_attempt_is_not_a_verdict_about_this_one(self):
        assert check_done_marker(make_done_marker(), instance_id=PREVIOUS_ATTEMPT) is None

    @pytest.mark.parametrize("field", MARKER_FIELDS)
    def test_a_missing_field_is_refused_by_name(self, field):
        with pytest.raises(StatusError, match=field):
            check_done_marker(make_done_marker(**{field: ABSENT}), instance_id=LAUNCHED)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("instance_id", ""),
            ("instance_id", None),
            ("finished_at", "2026-09-07T14:32:07"),
            ("finished_at", "ontem à noite"),
            ("finished_at", 1757255527),
            ("runs_total", "36"),
            ("runs_failed", True),
            ("capped", "false"),
            ("capped", 0),
            ("exit_status", True),
            ("exit_status", "0"),
        ],
    )
    def test_a_field_of_the_wrong_type_is_refused_by_name(self, field, value):
        with pytest.raises(StatusError, match=field):
            check_done_marker(make_done_marker(**{field: value}), instance_id=LAUNCHED)

    def test_a_marker_that_is_not_an_object_is_refused(self):
        with pytest.raises(StatusError, match="marcador"):
            check_done_marker(["2026-09-07T14:32:07+00:00"], instance_id=LAUNCHED)

    def test_the_field_is_refused_before_the_identity_is_compared(self):
        with pytest.raises(StatusError, match="capped"):
            check_done_marker(
                make_done_marker(instance_id=PREVIOUS_ATTEMPT, capped="false"),
                instance_id=LAUNCHED,
            )

    def test_the_capped_marker_is_valid_and_says_so(self):
        marker = check_done_marker(
            make_done_marker(capped=True, exit_status=1, runs_total=18), instance_id=LAUNCHED
        )

        assert marker is not None
        assert marker.capped is True
        assert marker.exit_status == 1


class TestProgress:
    def test_the_progress_of_the_launched_instance_is_accepted(self):
        assert check_progress(make_progress(), instance_id=LAUNCHED) == Progress(
            instance_id=LAUNCHED,
            block_index=4,
            block_count=6,
            run_index=3,
            run_count=6,
            scenario_id="libx265_1080p_720p_tos_c7g_rep2",
            runs_total=21,
            runs_failed=0,
            elapsed_seconds=4200,
            written_at="2026-09-07T14:32:07+00:00",
        )

    def test_the_progress_of_the_previous_attempt_is_ignored(self):
        assert check_progress(make_progress(), instance_id=PREVIOUS_ATTEMPT) is None

    @pytest.mark.parametrize("field", PROGRESS_FIELDS)
    def test_a_missing_field_is_refused_by_name(self, field):
        with pytest.raises(StatusError, match=field):
            check_progress(make_progress(**{field: ABSENT}), instance_id=LAUNCHED)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("instance_id", ""),
            ("block_index", "4"),
            ("block_count", 6.0),
            ("run_index", True),
            ("run_count", None),
            ("scenario_id", ""),
            ("scenario_id", 42),
            ("runs_total", "21"),
            ("runs_failed", True),
            ("elapsed_seconds", "4200"),
            ("written_at", "2026-09-07T14:32:07"),
            ("written_at", "agora há pouco"),
        ],
    )
    def test_a_field_of_the_wrong_type_is_refused_by_name(self, field, value):
        with pytest.raises(StatusError, match=field):
            check_progress(make_progress(**{field: value}), instance_id=LAUNCHED)

    def test_a_progress_that_is_not_an_object_is_refused(self):
        with pytest.raises(StatusError, match="progresso"):
            check_progress("21/36", instance_id=LAUNCHED)


# A forma que a Spec 4 mostra, congelada. A coluna do `scenario_id` é três casas
# mais larga que a da ilustração de lá, que só traz Replicações: encolhê-la para
# casar com a ilustração desalinha a linha do warm-up, que é um run em cada seis.
SPEC_LINE = (
    "14:32:07 c7g  bloco 4/6  run 3/6  "
    "libx265_1080p_720p_tos_c7g_rep2      21/36 runs, 0 falhas, 1h10m"
)

RUNS_COLUMN = SPEC_LINE.index("21/36 runs")

HOUR = re.compile(r"^\d{2}:\d{2}:\d{2} ")


def progress_of(**overrides: Any) -> Progress:
    """O progresso da factory, já aceito — um `None` aqui seria erro do teste."""
    progress = check_progress(make_progress(**overrides), instance_id=LAUNCHED)
    assert progress is not None
    return progress


def capture[T](name: str, check: Callable[..., T | None]) -> T:
    """A fixture que o bash escreveu, pelo leitor que ela ancora."""
    accepted = check(json.loads(captured(name)), instance_id=LAUNCHED)
    assert accepted is not None
    return accepted


class TestProgressLine:
    def test_the_line_has_the_shape_the_spec_shows(self):
        assert progress_line(progress_of(), instance="c7g", runs_total=36) == SPEC_LINE

    def test_the_column_fits_every_scenario_id_the_two_definitions_generate(self):
        # Sobre as definições reais: um codec de slug mais comprido entraria no
        # `config/` e desalinharia a coluna sem que nada mais reclamasse.
        generated = [
            run["scenario_id"]
            for config in (real_config(), real_pilot_config())
            for block in build_canonical_plan(config)["blocks"]
            for run in block["runs"]
        ]

        assert max(len(scenario_id) for scenario_id in generated) == len(LONGEST_SCENARIO_ID)

    def test_the_longest_scenario_id_keeps_the_columns_aligned(self):
        line = progress_line(
            progress_of(scenario_id=LONGEST_SCENARIO_ID), instance="c7g", runs_total=36
        )

        assert line.index("21/36 runs") == RUNS_COLUMN

    def test_two_architectures_in_blocks_of_different_widths_keep_the_columns_aligned(self):
        # A campanha tem 54 blocos e 324 runs por fatia, e as três arquiteturas
        # passam as 46 h em pontos diferentes da mesma sequência.
        ninth = progress_line(
            progress_of(block_index=9, block_count=54, runs_total=54),
            instance="c7g",
            runs_total=324,
        )
        tenth = progress_line(
            progress_of(block_index=10, block_count=54, runs_total=108),
            instance="c7a",
            runs_total=324,
        )

        assert ninth.index("runs,") == tenth.index("runs,")
        assert " 9/54" in ninth
        assert " 54/324 runs" in ninth

    def test_the_total_comes_from_the_slice_and_not_from_the_object(self):
        progress = progress_of()

        assert "21/36 runs" in progress_line(progress, instance="c7g", runs_total=36)
        assert "21/90 runs" in progress_line(progress, instance="c7g", runs_total=90)

    def test_the_hour_is_the_one_the_instance_wrote(self):
        line = progress_line(
            progress_of(written_at="2026-09-07T03:07:59-03:00"), instance="c7g", runs_total=36
        )

        assert line.startswith("03:07:59 ")

    @pytest.mark.parametrize(
        ("elapsed_seconds", "expected"),
        [(0, "0m"), (59, "0m"), (60, "1m"), (3599, "59m"), (3600, "1h00m"), (165600, "46h00m")],
    )
    def test_the_elapsed_time_is_rendered_for_a_human(self, elapsed_seconds, expected):
        line = progress_line(
            progress_of(elapsed_seconds=elapsed_seconds), instance="c7g", runs_total=36
        )

        assert line.endswith(f", {expected}")

    def test_the_failures_are_on_the_line(self):
        line = progress_line(progress_of(runs_failed=2), instance="c7g", runs_total=36)

        assert "2 falhas" in line

    def test_an_architecture_without_progress_still_gets_a_line(self):
        line = progress_line(None, instance="c7i", runs_total=36)

        assert line == "         c7i  sem progresso ainda, 0/36 runs reportados"


# O que só o bash diz: o `jq -n` do `run_all.sh` escreveu estes dois objetos no
# bucket falso do smoke, no laço de um bloco. Uma fixture montada aqui seria o
# autor do leitor conferindo o que ele mesmo imaginou que o bash produz — é o
# instinto do `test_meta_agreement.py` (ADR-0022).
class TestWhatTheBashWrote:
    def test_the_captured_progress_is_accepted(self):
        progress = capture("status-progress.json", check_progress)

        assert (progress.block_index, progress.block_count) == (1, 1)
        assert (progress.run_index, progress.run_count) == (6, 6)
        assert progress.runs_total == 6

    def test_the_captured_progress_renders_a_line(self):
        progress = capture("status-progress.json", check_progress)

        line = progress_line(progress, instance="c7g", runs_total=6)

        assert HOUR.match(line)
        assert line.endswith("6/6 runs, 0 falhas, 0m")

    def test_the_captured_marker_is_valid(self):
        marker = capture("status-done.json", check_done_marker)

        assert marker.capped is False
        assert marker.exit_status == 0
        assert marker.runs_total == 6
