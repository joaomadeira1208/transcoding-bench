# O `judge.json` tem dois leitores pelo motivo do `meta.json`: o modelo pydantic
# do `analysis/` e o checador stdlib do `orchestrator/`, que não pode importá-lo
# sem custar a invariante stdlib-only. Discordarem sobre o que é um arquivo
# válido é pior do que qualquer uma das duas políticas sozinha.
#
# Por isso este teste **mora duas vezes**, uma em cada papel: os venvs são
# separados e não há módulo comum. A âncora que o `run_quality.sh` escreveu no
# Pass do piloto é um arquivo, não um módulo, e por isso é uma só, lida pelo
# caminho a partir dos dois papéis.

from __future__ import annotations

import pytest
from conftest import CAPTURES
from judgement import load_judgement
from pydantic import ValidationError

ANCHOR = CAPTURES / "judge.json"

VALID_JUDGE_JSON = ANCHOR.read_text(encoding="utf-8")

# O mesmo arquivo com o `exit_code` escrito como string, que é o que um `--arg`
# no lugar de um `--argjson` faz o `jq` do Juiz escrever. Aceito, ele mandaria o
# `clean` tratar um julgamento falho como bem-sucedido e apagar as cópias
# bit-idênticas de um bitstream que ninguém mediu.
STRING_EXIT_CODE_JSON = VALID_JUDGE_JSON.replace('"exit_code": 0', '"exit_code": "0"')

# E com o digest truncado: é a chave pela qual a retenção acha essas cópias.
TRUNCATED_SHA256_JSON = VALID_JUDGE_JSON.replace(
    '"sha256": "fdb405819b1e10c8671de95d532826019c9bc77e1945f5465777e84d0f57bb38"',
    '"sha256": "fdb40581"',
)

# E com o `finished_at` sem offset, que é o que um `date -Is` trocado por um
# `date +%FT%T` produz: o "último vence" de um Pass repetido ordena instantes.
NAIVE_FINISHED_AT_JSON = VALID_JUDGE_JSON.replace(
    '"finished_at": "2026-10-20T03:45:42+00:00"', '"finished_at": "2026-10-20T03:45:42"'
)


def test_the_judgement_the_bash_wrote_in_the_pilot_is_accepted():
    judgement = load_judgement(VALID_JUDGE_JSON)

    assert judgement.exit_code == 0
    assert judgement.run_id == "87cb0051-f371-48e1-bbd8-7dbcc94eeb7f"


def test_the_exit_code_as_string_is_rejected():
    with pytest.raises(ValidationError):
        load_judgement(STRING_EXIT_CODE_JSON)


def test_the_truncated_sha256_is_rejected():
    with pytest.raises(ValidationError):
        load_judgement(TRUNCATED_SHA256_JSON)


def test_the_naive_finished_at_is_rejected():
    with pytest.raises(ValidationError):
        load_judgement(NAIVE_FINISHED_AT_JSON)
