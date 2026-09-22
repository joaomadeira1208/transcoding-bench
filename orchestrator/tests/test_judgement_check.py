# Cinco campos, que são aqueles sobre os quais este papel decide: o `clean` exige
# `status/judge_done` e depois lê cada `judge.json` para escolher o que apagar.
# `"exit_code": "0"` é string truthy comparada a zero, e um `sha256` truncado
# casa com nenhuma das cópias bit-idênticas — os dois apagam a coisa errada sem
# levantar nada.

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import ABSENT, make_judgement_json
from judgement_check import JudgementError, check_judgement


class TestAccepts:
    def test_valid_judgement_comes_back_parsed(self):
        judgement = check_judgement(make_judgement_json())

        assert judgement["run_id"] == "9f0c4a2e-6b41-4d5f-8a37-2f1c8de0b7a4"
        assert judgement["exit_code"] == 0
        assert judgement["scenario_id"] == "libx264_2160p_1080p_bbb_c7g_rep1"

    def test_failed_judgement(self):
        # Julgamento falho é dado, não erro de contrato: o `clean` o lê e mantém
        # o `output.mkv` do bitstream que ninguém mediu (D22).
        assert check_judgement(make_judgement_json(exit_code=1))["exit_code"] == 1

    def test_offset_other_than_utc(self):
        judgement = check_judgement(make_judgement_json(finished_at="2026-09-14T08:43:18-03:00"))

        assert datetime.fromisoformat(judgement["finished_at"]) == datetime(
            2026, 9, 14, 11, 43, 18, tzinfo=UTC
        )


class TestRejects:
    @pytest.mark.parametrize(
        "field", ["schema_version", "run_id", "sha256", "exit_code", "finished_at"]
    )
    def test_a_missing_field_is_refused_by_name(self, field):
        with pytest.raises(JudgementError, match=field):
            check_judgement(make_judgement_json(**{field: ABSENT}))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("schema_version", "2"),
            ("schema_version", 1),
            ("run_id", ""),
            ("run_id", 42),
            ("sha256", "3d2f7c1a"),
            ("sha256", "3D2F7C1A" * 8),
            ("sha256", None),
            ("exit_code", "0"),
            ("exit_code", True),
            ("exit_code", 1.0),
            ("finished_at", "2026-09-14T11:43:18"),
            ("finished_at", "ontem à noite"),
            ("finished_at", 1789389798),
        ],
    )
    def test_a_field_of_the_wrong_value_is_refused_by_name(self, field, value):
        with pytest.raises(JudgementError, match=field):
            check_judgement(make_judgement_json(**{field: value}))

    def test_malformed_json(self):
        with pytest.raises(JudgementError):
            check_judgement("{")

    def test_json_that_is_not_an_object(self):
        with pytest.raises(JudgementError):
            check_judgement("[]")
