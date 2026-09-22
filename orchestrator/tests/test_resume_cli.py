# Os dois argumentos que o CLI valida antes de tocar o bucket. A leitura da
# árvore sincronizada é do `run_tree`, e os testes dela moram no
# `test_run_tree.py`.

from __future__ import annotations

import argparse

import pytest
from resume import COMMIT_SHA_DIGITS, excluded_commit, fresh_out_dir

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


# O `--out` é a interface com o `run --slices`, que sobe toda fatia que estiver no
# diretório: reusar o da retomada anterior deixaria lá a fatia de uma arquitetura
# que desta vez saiu completa, e o relatório ao lado diria que não há o que retomar.


def test_a_directory_that_does_not_exist_yet_is_the_normal_case(tmp_path):
    out = tmp_path / "resume-1"

    assert fresh_out_dir(str(out)) == out


def test_an_empty_directory_is_accepted(tmp_path):
    assert fresh_out_dir(str(tmp_path)) == tmp_path


def test_a_directory_with_a_slice_is_refused_naming_it(tmp_path):
    (tmp_path / "c7a.json").write_text("{}", encoding="utf-8")

    with pytest.raises(argparse.ArgumentTypeError, match=r"c7a\.json"):
        fresh_out_dir(str(tmp_path))


def test_what_is_not_a_slice_does_not_refuse_the_directory(tmp_path):
    (tmp_path / "notas.txt").write_text("", encoding="utf-8")

    assert fresh_out_dir(str(tmp_path)) == tmp_path
