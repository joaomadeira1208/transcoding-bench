# O que o CLI do triage decide por conta própria: a guarda do `--out`, que roda
# antes do `s3 sync`, e a recusa por matriz incompleta. A leitura da árvore
# sincronizada é do `run_tree`, e os testes dela moram no `test_run_tree.py`.

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from quality_plan import PLAN_FILENAME
from quality_triage import fresh_out_dir, incomplete_refusal
from resume_plan import InstanceResume, Pending, PendingBlock


class TestTheOutputDirectory:
    def test_a_directory_that_does_not_exist_yet_is_the_normal_case(self, tmp_path):
        out = tmp_path / "quality"

        assert fresh_out_dir(str(out)) == out

    def test_an_empty_directory_is_accepted(self, tmp_path):
        assert fresh_out_dir(str(tmp_path)) == tmp_path

    def test_a_directory_that_already_holds_a_plan_is_refused(self, tmp_path):
        (tmp_path / PLAN_FILENAME).write_text("{}", encoding="utf-8")

        with pytest.raises(argparse.ArgumentTypeError, match=PLAN_FILENAME):
            fresh_out_dir(str(tmp_path))

    def test_what_is_not_a_plan_does_not_refuse_the_directory(self, tmp_path):
        (tmp_path / "notas.txt").write_text("", encoding="utf-8")

        assert fresh_out_dir(str(tmp_path)) == tmp_path


def complete(instance: str = "c7g") -> InstanceResume:
    return InstanceResume(instance=instance, complete=("libx264_1080p_1080p_bbb_c7g",), pending=())


def pending(instance: str = "c7i") -> InstanceResume:
    return InstanceResume(
        instance=instance,
        complete=(),
        pending=(PendingBlock("libx264_1080p_1080p_bbb_c7i", Pending.PARTIAL),),
    )


class TestTheCompletenessGuard:
    def test_a_complete_matrix_is_not_refused(self):
        assert incomplete_refusal([complete()], Path("config/pilot.toml"), "pilot") is None

    def test_no_architecture_at_all_is_not_refused(self):
        assert incomplete_refusal([], Path("config/pilot.toml"), "pilot") is None

    def test_a_pending_block_is_refused_with_the_resume_report(self):
        refusal = incomplete_refusal([complete(), pending()], Path("config/pilot.toml"), "pilot")

        assert refusal is not None
        assert "libx264_1080p_1080p_bbb_c7i" in refusal
        assert Pending.PARTIAL.value in refusal

    def test_the_refusal_carries_the_command_that_resolves_it(self):
        refusal = incomplete_refusal([pending()], Path("config/pilot.toml"), "meu-bucket")

        assert "resume.py" in refusal
        assert "--config config/pilot.toml" in refusal
        assert "--bucket meu-bucket" in refusal
