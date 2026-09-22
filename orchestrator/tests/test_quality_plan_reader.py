# O leitor é a fronteira entre o triage e os dois comandos que agem sobre o plano:
# o `judge`, que sobe uma instância e computa VMAF por entrada, e o `clean`, que
# apaga `output.mkv` pelo que o plano diz ser representante. Um campo ausente que
# passasse aqui viraria um `KeyError` na instância do Juiz, depois do lançamento,
# ou um `s3 rm` decidido sobre um plano que não é o que o Juiz leu.

from __future__ import annotations

from typing import Any

import pytest
from conftest import ABSENT, make_plan_output, make_quality_plan, make_quality_plan_json
from quality_plan import PlanError, check_plan

TEXT_FIELDS = (
    "run_id",
    "scenario_id",
    "codec",
    "encoder",
    "input_res",
    "output_res",
    "video",
    "instance",
    "master",
    "scale_flags",
    "container",
)

POSITIVE_INT_FIELDS = ("output_width", "output_height", "frames")


def plan_with(output: dict[str, Any]) -> str:
    return make_quality_plan_json(outputs=[output])


class TestTheShape:
    def test_the_plan_the_writer_produces_comes_back_parsed(self):
        assert check_plan(make_quality_plan_json()) == make_quality_plan()

    def test_bytes_are_accepted_as_well_as_text(self):
        assert check_plan(make_quality_plan_json().encode()) == make_quality_plan()

    def test_what_is_not_json_is_refused(self):
        with pytest.raises(PlanError, match="JSON"):
            check_plan(make_quality_plan_json()[:40])

    def test_what_is_not_an_object_is_refused(self):
        with pytest.raises(PlanError, match="list"):
            check_plan("[]")


class TestTheTopLevel:
    def test_an_unknown_schema_version_is_refused(self):
        with pytest.raises(PlanError, match="schema_version"):
            check_plan(make_quality_plan_json(schema_version="2"))

    def test_a_missing_schema_version_is_refused(self):
        with pytest.raises(PlanError, match="schema_version"):
            check_plan(make_quality_plan_json(schema_version=ABSENT))

    def test_a_missing_quality_table_is_refused(self):
        with pytest.raises(PlanError, match="quality"):
            check_plan(make_quality_plan_json(quality=ABSENT))

    @pytest.mark.parametrize("field", ("vmaf_model", "vmaf_delta_max", "ssim_delta_max"))
    def test_a_missing_field_of_the_quality_table_is_refused_naming_it(self, field):
        quality = {key: value for key, value in make_quality_plan()["quality"].items()}
        del quality[field]

        with pytest.raises(PlanError, match=field):
            check_plan(make_quality_plan_json(quality=quality))

    def test_a_threshold_that_is_not_a_positive_number_is_refused(self):
        quality = make_quality_plan()["quality"] | {"vmaf_delta_max": "0.5"}

        with pytest.raises(PlanError, match="vmaf_delta_max"):
            check_plan(make_quality_plan_json(quality=quality))

    def test_a_missing_outputs_is_refused(self):
        with pytest.raises(PlanError, match="outputs"):
            check_plan(make_quality_plan_json(outputs=ABSENT))

    def test_an_empty_plan_is_refused(self):
        with pytest.raises(PlanError, match="outputs"):
            check_plan(make_quality_plan_json(outputs=[]))

    def test_an_outputs_that_is_not_a_list_is_refused(self):
        with pytest.raises(PlanError, match="outputs"):
            check_plan(make_quality_plan_json(outputs={}))

    def test_an_entry_that_is_not_an_object_is_refused_naming_its_position(self):
        with pytest.raises(PlanError, match=r"outputs\[0\]"):
            check_plan(make_quality_plan_json(outputs=["libx264_2160p_1080p_bbb_c7g_rep1"]))


class TestTheEntry:
    @pytest.mark.parametrize("field", TEXT_FIELDS)
    def test_a_missing_text_field_is_refused_naming_it(self, field):
        with pytest.raises(PlanError, match=field):
            check_plan(plan_with(make_plan_output(**{field: ABSENT})))

    @pytest.mark.parametrize("field", TEXT_FIELDS)
    def test_a_text_field_that_is_empty_is_refused_naming_it(self, field):
        with pytest.raises(PlanError, match=field):
            check_plan(plan_with(make_plan_output(**{field: ""})))

    @pytest.mark.parametrize("field", POSITIVE_INT_FIELDS)
    def test_a_geometry_field_that_is_not_a_positive_integer_is_refused(self, field):
        with pytest.raises(PlanError, match=field):
            check_plan(plan_with(make_plan_output(**{field: 0})))

    @pytest.mark.parametrize("field", POSITIVE_INT_FIELDS)
    def test_a_geometry_field_written_as_a_string_is_refused(self, field):
        with pytest.raises(PlanError, match=field):
            check_plan(plan_with(make_plan_output(**{field: "1920"})))

    def test_a_sha256_that_is_not_sixty_four_hexadecimals_is_refused(self):
        with pytest.raises(PlanError, match="sha256"):
            check_plan(plan_with(make_plan_output(sha256="a3f1c0de")))

    def test_an_uppercase_sha256_is_refused(self):
        digest = make_plan_output()["sha256"].upper()

        with pytest.raises(PlanError, match="sha256"):
            check_plan(plan_with(make_plan_output(sha256=digest)))

    def test_a_cell_divergent_written_as_a_string_is_refused(self):
        with pytest.raises(PlanError, match="cell_divergent"):
            check_plan(plan_with(make_plan_output(cell_divergent="false")))

    def test_a_missing_cell_divergent_is_refused(self):
        with pytest.raises(PlanError, match="cell_divergent"):
            check_plan(plan_with(make_plan_output(cell_divergent=ABSENT)))

    def test_an_empty_shared_by_is_refused(self):
        with pytest.raises(PlanError, match="shared_by"):
            check_plan(plan_with(make_plan_output(shared_by=[])))

    def test_a_shared_by_that_is_not_a_list_is_refused(self):
        with pytest.raises(PlanError, match="shared_by"):
            check_plan(plan_with(make_plan_output(shared_by={})))

    @pytest.mark.parametrize("field", ("instance", "scenario_id", "run_id"))
    def test_a_sharer_missing_a_field_is_refused_naming_it(self, field):
        sharer = {key: value for key, value in make_plan_output()["shared_by"][0].items()}
        del sharer[field]

        with pytest.raises(PlanError, match=field):
            check_plan(plan_with(make_plan_output(shared_by=[sharer])))

    def test_the_offending_entry_is_named_by_its_position(self):
        outputs = [make_plan_output(), make_plan_output(video="")]

        with pytest.raises(PlanError, match=r"outputs\[1\]"):
            check_plan(make_quality_plan_json(outputs=outputs))
