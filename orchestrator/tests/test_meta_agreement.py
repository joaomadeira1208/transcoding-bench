# Dois leitores do mesmo arquivo discordando sobre se ele é válido é um estado
# pior do que qualquer uma das duas políticas sozinha, e some justamente a
# evidência de que o bash está escrevendo JSON errado.
#
# Por isso este teste **mora duas vezes**, uma em cada papel, com as fixtures
# escritas à mão nos dois: os venvs são separados e não há módulo comum.

from __future__ import annotations

import pytest
from meta_check import MetaError, check_meta

# Escrito à mão, não vindo da factory do papel: a factory é Python validando
# Python, e o que este teste persegue é a divergência entre os dois leitores.
VALID_META_JSON = """{
  "schema_version": "1",
  "scenario_id": "libx264_2160p_1080p_bbb_c7g_rep1",
  "warmup": false,
  "seed": 20260808,
  "codec": "h264",
  "encoder": "libx264",
  "input_res": "2160p",
  "output_res": "1080p",
  "video": "bbb",
  "instance": "c7g",
  "master": "bbb_2160p.mkv",
  "output_width": 1920,
  "output_height": 1080,
  "preset": "medium",
  "crf": 23,
  "encoder_args": ["-sc_threshold", "0"],
  "threads": 0,
  "gop_size": 48,
  "pix_fmt": "yuv420p",
  "strip_audio": true,
  "container": "mkv",
  "scale_flags": "lanczos",
  "run_id": "9f0c4a2e-6b41-4d5f-8a37-2f1c8de0b7a4",
  "started_at": "2026-08-08T10:00:00+00:00",
  "finished_at": "2026-08-08T10:12:31+00:00",
  "exit_code": 0,
  "commit": "ffd4f43a1b2c3d4e5f60718293a4b5c6d7e8f900",
  "instance_id": "i-0123456789abcdef0",
  "instance_type": "c7g.xlarge",
  "versions": {
    "ffmpeg": "n7.1",
    "libx264": "31e19f92",
    "libx265": "4.1",
    "libsvtav1": "v2.3.0",
    "libvmaf": "v3.0.0"
  }
}
"""

# O mesmo arquivo, com o `warmup` escrito como string.
INVALID_META_JSON = VALID_META_JSON.replace('"warmup": false', '"warmup": "false"')


def test_the_valid_meta_is_accepted():
    assert check_meta(VALID_META_JSON)["warmup"] is False


def test_the_invalid_meta_is_rejected():
    with pytest.raises(MetaError, match="warmup"):
        check_meta(INVALID_META_JSON)
