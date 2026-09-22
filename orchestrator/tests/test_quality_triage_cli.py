# A árvore sincronizada **é** a enumeração, como na retomada, e aqui ela traz dois
# arquivos por Execução. Os modos de falha continuam opostos: um `meta.json`
# inválido derruba o triage, porque decidir sobre ele é decidir sobre um
# bitstream; um diretório sem `meta.json` é ignorado, porque uma Execução sem ele
# não pode ser vencedora. O `output.sha256` não é lido aqui como erro: quem sabe
# se a sua falta importa é o núcleo, que sabe quem venceu.

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from conftest import make_meta_json
from external import RUN_HASH_FILENAME, RUN_META_FILENAME
from meta_check import MetaError
from quality_plan import PLAN_FILENAME
from quality_triage import fresh_out_dir, incomplete_refusal, read_runs
from resume_plan import InstanceResume, Pending, PendingBlock

DIGEST = "3d2f7c1a90b4e5d68f2a1c0b7e9d4a35c6f8091b2d3e4f5061728394a5b6c7d8"


def write_run(runs: Path, run_id: str, raw: str | None = None, digest: str | None = None) -> Path:
    """Uma Execução como o bucket a traz: o diretório tem o nome do `run_id` dela."""
    run_dir = runs / run_id
    run_dir.mkdir(parents=True)
    if raw is not None:
        (run_dir / RUN_META_FILENAME).write_text(raw, encoding="utf-8")
    if digest is not None:
        (run_dir / RUN_HASH_FILENAME).write_text(f"{digest}\n", encoding="utf-8")
    return run_dir


class TestReadingTheTree:
    def test_every_run_comes_back_with_its_meta_and_its_hash(self, tmp_path):
        write_run(tmp_path, "run-a", make_meta_json(run_id="run-a"), DIGEST)
        write_run(tmp_path, "run-b", make_meta_json(run_id="run-b"), DIGEST)

        metas, hashes, warnings = read_runs(tmp_path)

        assert sorted(meta["run_id"] for meta in metas) == ["run-a", "run-b"]
        assert hashes == {"run-a": DIGEST, "run-b": DIGEST}
        assert warnings == []

    def test_an_empty_tree_is_nothing_and_no_warning(self, tmp_path):
        assert read_runs(tmp_path) == ([], {}, [])

    def test_a_run_without_meta_is_ignored_with_a_warning(self, tmp_path):
        write_run(tmp_path, "interrupted")
        write_run(tmp_path, "complete", make_meta_json(), DIGEST)

        metas, _, warnings = read_runs(tmp_path)

        assert len(metas) == 1
        assert warnings == [f"runs/interrupted/{RUN_META_FILENAME}: ausente, Execução ignorada"]

    def test_a_run_without_hash_is_read_without_complaint(self, tmp_path):
        write_run(tmp_path, "failed", make_meta_json(exit_code=1))

        metas, hashes, warnings = read_runs(tmp_path)

        assert len(metas) == 1
        assert hashes == {}
        assert warnings == []

    def test_the_trailing_newline_of_sha256sum_is_stripped(self, tmp_path):
        write_run(tmp_path, "run-a", make_meta_json(run_id="run-a"), DIGEST)

        _, hashes, _ = read_runs(tmp_path)

        assert hashes["run-a"] == DIGEST

    def test_a_malformed_hash_comes_back_as_it_is(self, tmp_path):
        run_dir = write_run(tmp_path, "run-a", make_meta_json(run_id="run-a"))
        (run_dir / RUN_HASH_FILENAME).write_text("a3f1c0de\n", encoding="utf-8")

        _, hashes, _ = read_runs(tmp_path)

        assert hashes == {"run-a": "a3f1c0de"}

    def test_the_hash_is_keyed_by_the_run_id_the_plan_will_carry(self, tmp_path):
        write_run(tmp_path, "0-um-diretorio", make_meta_json(run_id="run-a"), DIGEST)

        _, hashes, _ = read_runs(tmp_path)

        assert hashes == {"run-a": DIGEST}

    def test_an_invalid_meta_brings_the_triage_down_naming_the_key(self, tmp_path):
        write_run(tmp_path, "bad", make_meta_json(warmup="false"), DIGEST)

        with pytest.raises(MetaError, match=f"runs/bad/{RUN_META_FILENAME}"):
            read_runs(tmp_path)

    def test_the_offending_field_survives_in_the_message(self, tmp_path):
        write_run(tmp_path, "bad", make_meta_json(exit_code="0"), DIGEST)

        with pytest.raises(MetaError, match="exit_code"):
            read_runs(tmp_path)

    def test_a_loose_file_at_the_top_is_not_a_run(self, tmp_path):
        (tmp_path / "stray.json").write_text("{}", encoding="utf-8")

        assert read_runs(tmp_path) == ([], {}, [])


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
