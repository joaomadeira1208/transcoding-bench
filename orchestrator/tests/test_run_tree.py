# A árvore sincronizada **é** a enumeração das Execuções, e é aqui que ela vira
# decisão — na retomada e no triage do Pass, pelo mesmo leitor. Os dois modos de
# falha são opostos: um `meta.json` inválido tem de derrubar quem lê, nomeando o
# arquivo, porque decidir sobre ele é decidir sobre um bloco; e um diretório sem
# `meta.json` não pode derrubar nada, porque uma Execução sem `meta.json` não
# pode estar completa de qualquer forma. O `output.sha256` é o terceiro caso: a
# falta dele não é erro aqui, e o conteúdo vem como está, porque quem sabe
# julgar um hash é o núcleo do Pass.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_meta_json
from external import RUN_HASH_FILENAME, RUN_META_FILENAME
from meta_check import MetaError
from run_tree import read_runs

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


class TestTheMetas:
    def test_every_meta_of_the_tree_comes_back_parsed(self, tmp_path):
        write_run(tmp_path, "run-a", make_meta_json(scenario_id="a_rep1"))
        write_run(tmp_path, "run-b", make_meta_json(scenario_id="b_rep1"))

        metas, _, warnings = read_runs(tmp_path)

        assert sorted(meta["scenario_id"] for meta in metas) == ["a_rep1", "b_rep1"]
        assert warnings == []

    def test_an_empty_tree_is_nothing_and_no_warning(self, tmp_path):
        assert read_runs(tmp_path) == ([], {}, [])

    def test_a_run_without_meta_is_ignored_with_a_warning(self, tmp_path):
        write_run(tmp_path, "interrupted")
        write_run(tmp_path, "complete", make_meta_json(), DIGEST)

        metas, _, warnings = read_runs(tmp_path)

        assert len(metas) == 1
        assert warnings == [f"runs/interrupted/{RUN_META_FILENAME}: ausente, Execução ignorada"]

    def test_an_invalid_meta_brings_the_reader_down_naming_the_key(self, tmp_path):
        write_run(tmp_path, "bad", make_meta_json(warmup="false"), DIGEST)

        with pytest.raises(MetaError, match=f"runs/bad/{RUN_META_FILENAME}"):
            read_runs(tmp_path)

    def test_the_offending_field_survives_in_the_message(self, tmp_path):
        write_run(tmp_path, "bad", make_meta_json(exit_code="0"))

        with pytest.raises(MetaError, match="exit_code"):
            read_runs(tmp_path)

    def test_a_meta_that_is_not_json_brings_the_reader_down(self, tmp_path):
        write_run(tmp_path, "truncated", make_meta_json()[:40])

        with pytest.raises(MetaError, match="truncated"):
            read_runs(tmp_path)

    def test_a_loose_file_at_the_top_is_not_a_run(self, tmp_path):
        (tmp_path / "stray.json").write_text(json.dumps({}), encoding="utf-8")

        assert read_runs(tmp_path) == ([], {}, [])


class TestTheHashes:
    def test_every_run_comes_back_with_its_meta_and_its_hash(self, tmp_path):
        write_run(tmp_path, "run-a", make_meta_json(run_id="run-a"), DIGEST)
        write_run(tmp_path, "run-b", make_meta_json(run_id="run-b"), DIGEST)

        metas, hashes, warnings = read_runs(tmp_path)

        assert sorted(meta["run_id"] for meta in metas) == ["run-a", "run-b"]
        assert hashes == {"run-a": DIGEST, "run-b": DIGEST}
        assert warnings == []

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

    def test_a_hash_that_is_not_even_utf_8_comes_back_as_it_is(self, tmp_path):
        run_dir = write_run(tmp_path, "run-a", make_meta_json(run_id="run-a"))
        (run_dir / RUN_HASH_FILENAME).write_bytes(b"\xff\xfe\x00")

        _, hashes, _ = read_runs(tmp_path)

        assert hashes["run-a"] != DIGEST

    def test_the_hash_is_keyed_by_the_run_id_the_plan_will_carry(self, tmp_path):
        write_run(tmp_path, "0-um-diretorio", make_meta_json(run_id="run-a"), DIGEST)

        _, hashes, _ = read_runs(tmp_path)

        assert hashes == {"run-a": DIGEST}
