# A árvore sincronizada **é** a enumeração das Execuções, e é aqui que ela vira
# decisão. Os dois modos de falha são opostos: um `meta.json` inválido tem de
# derrubar a retomada nomeando o arquivo, porque decidir sobre ele é decidir
# sobre um bloco; e um diretório sem `meta.json` não pode derrubar nada, porque
# uma Execução sem `meta.json` não pode estar completa de qualquer forma.

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from conftest import make_meta_json
from external import RUN_META_FILENAME
from meta_check import MetaError
from resume import COMMIT_SHA_DIGITS, excluded_commit, read_metas


def write_run(runs: Path, run_id: str, raw: str | None = None) -> Path:
    run_dir = runs / run_id
    run_dir.mkdir(parents=True)
    if raw is not None:
        (run_dir / RUN_META_FILENAME).write_text(raw, encoding="utf-8")
    return run_dir


def test_every_meta_of_the_tree_comes_back_parsed(tmp_path):
    write_run(tmp_path, "run-a", make_meta_json(scenario_id="a_rep1"))
    write_run(tmp_path, "run-b", make_meta_json(scenario_id="b_rep1"))

    metas, warnings = read_metas(tmp_path)

    assert sorted(meta["scenario_id"] for meta in metas) == ["a_rep1", "b_rep1"]
    assert warnings == []


def test_an_empty_tree_is_no_meta_and_no_warning(tmp_path):
    assert read_metas(tmp_path) == ([], [])


def test_a_run_without_meta_is_ignored_with_a_warning(tmp_path):
    write_run(tmp_path, "interrupted")
    write_run(tmp_path, "complete", make_meta_json())

    metas, warnings = read_metas(tmp_path)

    assert len(metas) == 1
    assert warnings == [f"runs/interrupted/{RUN_META_FILENAME}: ausente, Execução ignorada"]


def test_an_invalid_meta_brings_the_resume_down_naming_the_file(tmp_path):
    write_run(tmp_path, "bad", make_meta_json(warmup="false"))

    with pytest.raises(MetaError, match=f"runs/bad/{RUN_META_FILENAME}"):
        read_metas(tmp_path)


def test_the_offending_field_survives_in_the_message(tmp_path):
    write_run(tmp_path, "bad", make_meta_json(exit_code="0"))

    with pytest.raises(MetaError, match="exit_code"):
        read_metas(tmp_path)


def test_a_meta_that_is_not_json_brings_the_resume_down(tmp_path):
    write_run(tmp_path, "truncated", make_meta_json()[:40])

    with pytest.raises(MetaError, match="truncated"):
        read_metas(tmp_path)


def test_a_loose_file_at_the_top_is_not_a_run(tmp_path):
    (tmp_path / "stray.json").write_text(json.dumps({}), encoding="utf-8")

    assert read_metas(tmp_path) == ([], [])


# `git log --oneline` mostra o SHA abreviado, e é dali que o pesquisador o copia:
# um `--exclude-commit` abreviado não casaria com `commit` nenhum, e a retomada
# sairia zero declarando completos exatamente os blocos que o hotfix contaminou.

FULL_SHA = "ffd4f43a1b2c3d4e5f60718293a4b5c6d7e8f900"


def test_the_full_sha_is_accepted():
    assert excluded_commit(FULL_SHA) == FULL_SHA


def test_the_abbreviation_of_git_log_is_refused():
    with pytest.raises(argparse.ArgumentTypeError, match=str(COMMIT_SHA_DIGITS)):
        excluded_commit(FULL_SHA[:7])


def test_an_uppercase_sha_is_refused():
    with pytest.raises(argparse.ArgumentTypeError):
        excluded_commit(FULL_SHA.upper())


def test_a_non_hexadecimal_string_of_the_right_length_is_refused():
    with pytest.raises(argparse.ArgumentTypeError):
        excluded_commit("z" * COMMIT_SHA_DIGITS)
