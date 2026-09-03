# Testes da CLI de validação, exercitada como **processo** e não por import: é
# assim que o `smoke/` a vai chamar (decisão D11), e o que se verifica aqui é o
# contrato dela com quem a invoca — código de saída e mensagem que nomeia o campo
# ofensor. Um teste que importasse `main()` não cobriria nem um nem outro.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import ROLE_ROOT, make_meta_json

CLI = ROLE_ROOT / "validate_meta.py"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def write_meta(tmp_path: Path, **overrides: object) -> str:
    path = tmp_path / "meta.json"
    path.write_text(make_meta_json(**overrides), encoding="utf-8")
    return str(path)


def test_valid_meta_exits_zero(tmp_path):
    result = run_cli(write_meta(tmp_path))

    assert result.returncode == 0, result.stderr


def test_invalid_meta_exits_non_zero_naming_the_field(tmp_path):
    result = run_cli(write_meta(tmp_path, warmup="false"))

    assert result.returncode != 0
    assert "warmup" in result.stderr


def test_missing_file_exits_non_zero(tmp_path):
    result = run_cli(str(tmp_path / "ausente.json"))

    assert result.returncode != 0
    assert "ausente.json" in result.stderr


def test_emit_schema_matches_the_committed_file():
    # A regeneração do anexo é o outro lado do teste de sincronia: se o schema
    # commitado só pudesse ser reproduzido por um `python -c` decorado de
    # memória, ele divergiria do modelo na primeira mudança.
    result = run_cli("--emit-schema")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (ROLE_ROOT / "meta.schema.json").read_text(encoding="utf-8")
