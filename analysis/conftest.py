# conftest.py no nível do papel: é ele que torna o núcleo do `analysis/`
# importável pelos testes de `tests/`. O pytest insere no `sys.path` o diretório
# de cada `conftest.py` que coleta (import mode `prepend`), então `import
# run_meta` funciona sem `pyproject.toml`, sem `pip install -e` e sem `sys.path`
# manipulado nos módulos de teste — as três coisas que as ADR-0017 e 0022
# rejeitaram (decisão D9 da Spec 1).
#
# Também é a casa das factories de teste (ADR-0022): fixture/factory
# compartilhada dentro de um papel, tudo bem; helper de asserção compartilhado,
# não. A factory de `meta.json` daqui é **deliberadamente duplicada** em relação
# à do `orchestrator/`: os dois papéis rodam em venvs separados, e um pacote de
# teste comum entre papéis é exatamente o que os dois `requirements-dev.txt`
# existem para impedir.

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROLE_ROOT = Path(__file__).resolve().parent

# O JSON Schema commitado (decisão D12): gerado do modelo, anexado ao artigo, e
# conferido por teste — um schema no anexo divergindo do modelo que valida é
# falha silenciosa de documentação.
META_SCHEMA_PATH = ROLE_ROOT / "meta.schema.json"

# Um `meta.json` válido, com os valores que a Execução de um Cenário real
# produziria — o `libx264_2160p_1080p_bbb_c7g_rep1` do CONTEXT.md. Os testes de
# rejeição sobrescrevem um campo de cada vez, de modo que a falha asserida seja
# a única diferença em relação a um arquivo que passa.
#
# Factory é âncora fraca de propósito: ela é escrita em Python, pelo mesmo
# raciocínio que escreveu o modelo, então valida Python contra Python. A âncora
# de verdade do contrato cross-language é um `meta.json` que o bash produziu, e
# ela chega com o smoke (ADR-0022).
_VALID: dict[str, Any] = {
    "schema_version": "1",
    # Ecoados verbatim do objeto de run (decisão D4 da spec).
    "scenario_id": "libx264_2160p_1080p_bbb_c7g_rep1",
    "warmup": False,
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
    "strip_audio": True,
    "container": "mkv",
    "scale_flags": "lanczos",
    # Cunhados pela Execução.
    "run_id": "9f0c4a2e-6b41-4d5f-8a37-2f1c8de0b7a4",
    "started_at": "2026-08-08T10:00:00+00:00",
    "finished_at": "2026-08-08T10:12:31+00:00",
    "exit_code": 0,
    # Argumentos de bootstrap.
    "commit": "ffd4f43a1b2c3d4e5f60718293a4b5c6d7e8f900",
    "instance_id": "i-0123456789abcdef0",
    "instance_type": "c7g.xlarge",
    # Copiado da imagem.
    "versions": {
        "ffmpeg": "n7.1",
        "libx264": "31e19f92",
        "libx265": "4.1",
        "libsvtav1": "v2.3.0",
        "libvmaf": "v3.0.0",
    },
}


def make_meta(**overrides: Any) -> dict[str, Any]:
    """Um `meta.json` válido como dict, com overrides por campo de topo."""
    meta = copy.deepcopy(_VALID)
    for field, value in overrides.items():
        if value is _ABSENT:
            meta.pop(field, None)
        else:
            meta[field] = value
    return meta


def make_meta_json(**overrides: Any) -> str:
    """O mesmo, já serializado — é sobre os **bytes crus** que o modelo roda.

    Os testes passam por aqui em vez de por `make_meta` porque validar o JSON
    cru, e não `json.load()` + `model_validate()`, é o que a ADR-0022 decidiu: em
    modo estrito o pydantic aplica regras diferentes às duas entradas, e só a
    primeira aceita `str` → `datetime` enquanto recusa `"warmup": "false"`.
    """
    return json.dumps(make_meta(**overrides), indent=2) + "\n"


class _Absent:
    def __repr__(self) -> str:  # pragma: no cover - só aparece em falha de teste
        return "<absent>"


# Sentinela: `make_meta(warmup=ABSENT)` remove o campo em vez de escrevê-lo como
# `null`, porque "campo ausente" e "campo nulo" são modos de falha distintos.
_ABSENT = _Absent()
ABSENT: Any = _ABSENT
