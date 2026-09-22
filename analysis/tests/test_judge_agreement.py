# O `judge.json` tem dois leitores pelo motivo do `meta.json`: o modelo pydantic
# do `analysis/` e o checador stdlib do `orchestrator/`, que não pode importá-lo
# sem custar a invariante stdlib-only. Discordarem sobre o que é um arquivo
# válido é pior do que qualquer uma das duas políticas sozinha.
#
# Por isso este teste **mora duas vezes**, uma em cada papel, com as fixtures
# escritas à mão nos dois: os venvs são separados e não há módulo comum. A âncora
# real chega com o Pass do piloto (#103); até lá o válido aqui é o mesmo nos dois
# arquivos, e mantê-los iguais é trabalho de quem mexer em qualquer um deles.

from __future__ import annotations

import pytest
from judgement import load_judgement
from pydantic import ValidationError

VALID_JUDGE_JSON = """{
  "schema_version": "1",
  "run_id": "4b1d8e07-2c36-4a59-9f80-51ac7e2d6b43",
  "scenario_id": "libx265_1080p_720p_tos_c7i_rep1",
  "codec": "h265",
  "encoder": "libx265",
  "input_res": "1080p",
  "output_res": "720p",
  "video": "tos",
  "instance": "c7i",
  "sha256": "3d2f7c1a90b4e5d68f2a1c0b7e9d4a35c6f8091b2d3e4f5061728394a5b6c7d8",
  "master": "tos_1080p.mkv",
  "output_width": 1280,
  "output_height": 720,
  "scale_flags": "lanczos",
  "container": "mkv",
  "frames": 17616,
  "shared_by": [
    {
      "instance": "c7i",
      "scenario_id": "libx265_1080p_720p_tos_c7i_rep1",
      "run_id": "4b1d8e07-2c36-4a59-9f80-51ac7e2d6b43"
    },
    {
      "instance": "c7a",
      "scenario_id": "libx265_1080p_720p_tos_c7a_rep1",
      "run_id": "7e5a2c91-08bd-4f36-b1c7-3d9e04a82f65"
    }
  ],
  "cell_divergent": false,
  "started_at": "2026-09-14T11:02:00+00:00",
  "finished_at": "2026-09-14T11:43:18+00:00",
  "exit_code": 0,
  "commit": "ffd4f43a1b2c3d4e5f60718293a4b5c6d7e8f900",
  "instance_id": "i-0123456789abcdef0",
  "instance_type": "c7i.4xlarge",
  "versions": {
    "ffmpeg": "n7.1",
    "libx264": "31e19f92",
    "libx265": "4.1",
    "libsvtav1": "v2.3.0",
    "libvmaf": "v3.0.0"
  }
}
"""

# O mesmo arquivo com o `exit_code` escrito como string, que é o que um `--arg`
# no lugar de um `--argjson` faz o `jq` do Juiz escrever. Aceito, ele mandaria o
# `clean` tratar um julgamento falho como bem-sucedido e apagar as cópias
# bit-idênticas de um bitstream que ninguém mediu.
STRING_EXIT_CODE_JSON = VALID_JUDGE_JSON.replace('"exit_code": 0', '"exit_code": "0"')

# E com o digest truncado: é a chave pela qual a retenção acha essas cópias.
TRUNCATED_SHA256_JSON = VALID_JUDGE_JSON.replace(
    '"sha256": "3d2f7c1a90b4e5d68f2a1c0b7e9d4a35c6f8091b2d3e4f5061728394a5b6c7d8"',
    '"sha256": "3d2f7c1a"',
)

# E com o `finished_at` sem offset, que é o que um `date -Is` trocado por um
# `date +%FT%T` produz: o "último vence" de um Pass repetido ordena instantes.
NAIVE_FINISHED_AT_JSON = VALID_JUDGE_JSON.replace(
    '"finished_at": "2026-09-14T11:43:18+00:00"', '"finished_at": "2026-09-14T11:43:18"'
)


def test_the_valid_judgement_is_accepted():
    judgement = load_judgement(VALID_JUDGE_JSON)

    assert judgement.exit_code == 0
    assert judgement.run_id == "4b1d8e07-2c36-4a59-9f80-51ac7e2d6b43"


def test_the_exit_code_as_string_is_rejected():
    with pytest.raises(ValidationError):
        load_judgement(STRING_EXIT_CODE_JSON)


def test_the_truncated_sha256_is_rejected():
    with pytest.raises(ValidationError):
        load_judgement(TRUNCATED_SHA256_JSON)


def test_the_naive_finished_at_is_rejected():
    with pytest.raises(ValidationError):
        load_judgement(NAIVE_FINISHED_AT_JSON)
