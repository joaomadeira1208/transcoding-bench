# O `judge.json` atravessa a mesma fronteira que o `meta.json` e pelo mesmo
# caminho: `jq` montando JSON num script de shell, Python lendo. O que muda é
# sobre o que o arquivo decide — o `clean` apaga `output.mkv` a partir do
# `exit_code` e do `sha256` daqui, e um deles errado apaga o bitstream que o
# Juiz nunca chegou a julgar.

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ABSENT, JUDGE_SCHEMA_PATH, make_judgement_json
from judgement import SCHEMA_VERSION, load_judgement, render_json_schema
from pydantic import ValidationError


def offending_fields(error: ValidationError) -> set[str]:
    return {".".join(str(part) for part in item["loc"]) for item in error.errors()}


class TestAccepts:
    def test_valid_judgement(self):
        judgement = load_judgement(make_judgement_json())

        assert judgement.schema_version == SCHEMA_VERSION
        assert judgement.run_id == "4b1d8e07-2c36-4a59-9f80-51ac7e2d6b43"
        assert judgement.scenario_id == "libx265_1080p_720p_tos_c7i_rep1"
        assert judgement.exit_code == 0
        assert judgement.frames == 17616
        assert judgement.cell_divergent is False
        assert judgement.instance_type == "c7i.4xlarge"
        assert judgement.versions["libvmaf"] == "v3.0.0"

    def test_the_plan_entry_comes_back_whole(self):
        # É a entrada do plano projetada verbatim (D12): o leitor junta plano e
        # resultado pelo `run_id`, e as duas cópias têm de ser a mesma coisa.
        judgement = load_judgement(make_judgement_json())

        assert [sharer.instance for sharer in judgement.shared_by] == ["c7i", "c7a"]
        assert judgement.shared_by[1].run_id == "7e5a2c91-08bd-4f36-b1c7-3d9e04a82f65"

    def test_timestamps_arrive_as_strings_and_come_out_aware(self):
        judgement = load_judgement(make_judgement_json())

        assert judgement.started_at == datetime(2026, 9, 14, 11, 2, tzinfo=UTC)
        assert judgement.finished_at.utcoffset() is not None

    def test_offset_other_than_utc_normalizes_to_the_same_instant(self):
        # O "último vence" por `run_id` de um Pass repetido (D13) ordena pelo
        # `finished_at`, e ordenar as strings com offsets diferentes ordena ao
        # contrário.
        judgement = load_judgement(make_judgement_json(finished_at="2026-09-14T08:43:18-03:00"))

        assert judgement.finished_at == datetime(2026, 9, 14, 11, 43, 18, tzinfo=UTC)
        assert judgement.finished_at.utcoffset() == timedelta(hours=-3)

    def test_a_failed_judgement(self):
        # FFmpeg que falhou, log ausente ou ilegível é `exit_code != 0` e o laço
        # segue (D12): o arquivo continua válido, e é o `clean` que decide.
        assert load_judgement(make_judgement_json(exit_code=1)).exit_code == 1


class TestRejects:
    def test_exit_code_as_string(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(exit_code="0"))

        assert "exit_code" in offending_fields(caught.value)

    def test_exit_code_as_bool(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(exit_code=True))

        assert "exit_code" in offending_fields(caught.value)

    def test_cell_divergent_as_string(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(cell_divergent="false"))

        assert "cell_divergent" in offending_fields(caught.value)

    def test_missing_required_field(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(sha256=ABSENT))

        assert "sha256" in offending_fields(caught.value)

    def test_unknown_schema_version(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(schema_version="2"))

        assert "schema_version" in offending_fields(caught.value)

    def test_truncated_sha256(self):
        # É a chave pela qual o `clean` acha as cópias bit-idênticas a apagar
        # (D22): um digest truncado casa com nada, e nada é apagado em silêncio.
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(sha256="3d2f7c1a"))

        assert "sha256" in offending_fields(caught.value)

    def test_uppercase_sha256(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(sha256="3D2F7C1A" * 8))

        assert "sha256" in offending_fields(caught.value)

    @pytest.mark.parametrize("field", ["output_width", "output_height", "frames"])
    def test_a_geometry_field_at_zero(self, field):
        # Os mesmos limites do leitor do plano no `orchestrator/`: `frames: 0` é
        # o que um `jq` sobre uma chave ausente escreve, e ele atravessa a média
        # por frame como divisor.
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(**{field: 0}))

        assert field in offending_fields(caught.value)

    def test_negative_frames(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(frames=-1))

        assert "frames" in offending_fields(caught.value)

    def test_naive_finished_at(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(finished_at="2026-09-14T11:43:18"))

        assert "finished_at" in offending_fields(caught.value)

    def test_naive_started_at(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(started_at="2026-09-14T11:02:00"))

        assert "started_at" in offending_fields(caught.value)

    def test_empty_run_id(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(run_id=""))

        assert "run_id" in offending_fields(caught.value)

    def test_empty_shared_by(self):
        # Vazia é o que faria a retenção manter as cinco cópias em silêncio, pelo
        # mesmo motivo que o leitor do plano a recusa.
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(shared_by=[]))

        assert "shared_by" in offending_fields(caught.value)

    def test_shared_by_entry_missing_a_field(self):
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(shared_by=[{"instance": "c7i", "run_id": "x"}]))

        assert "shared_by.0.scenario_id" in offending_fields(caught.value)

    def test_unknown_field(self):
        # O `vmaf_mean` é o exemplo vivo: as métricas saem do `vmaf.json`, que é
        # artefato próprio (D13), e tê-las aqui daria duas fontes do mesmo dado.
        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(vmaf_mean=96.4))

        assert "vmaf_mean" in offending_fields(caught.value)

    def test_unknown_field_inside_shared_by(self):
        sharer = {
            "instance": "c7i",
            "scenario_id": "libx265_1080p_720p_tos_c7i_rep1",
            "run_id": "4b1d8e07-2c36-4a59-9f80-51ac7e2d6b43",
            "sha256": "3d2f7c1a" * 8,
        }

        with pytest.raises(ValidationError) as caught:
            load_judgement(make_judgement_json(shared_by=[sharer]))

        assert "shared_by.0.sha256" in offending_fields(caught.value)

    def test_malformed_json(self):
        with pytest.raises(ValidationError):
            load_judgement("{")


class TestCommittedJsonSchema:
    def test_matches_the_model(self):
        assert JUDGE_SCHEMA_PATH.read_text(encoding="utf-8") == render_json_schema(), (
            "judge.schema.json fora de sincronia com o modelo; regenere com "
            "`python analysis/validate_judge.py --emit-schema > analysis/judge.schema.json`"
        )

    def test_describes_the_same_required_fields(self):
        schema = json.loads(JUDGE_SCHEMA_PATH.read_text(encoding="utf-8"))

        assert set(schema["required"]) == set(json.loads(make_judgement_json()))
