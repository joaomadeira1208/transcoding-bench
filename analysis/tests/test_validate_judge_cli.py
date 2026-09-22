# Exercitada como **processo** pelo motivo do `test_validate_meta_cli.py`: é
# assim que o `smoke/` a vai chamar sobre o `judge.json` que o `run_quality.sh`
# acabou de escrever, e o que se verifica é o contrato com quem a invoca.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import ROLE_ROOT, make_judgement_json

CLI = ROLE_ROOT / "validate_judge.py"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def write_judge(tmp_path: Path, **overrides: object) -> str:
    path = tmp_path / "judge.json"
    path.write_text(make_judgement_json(**overrides), encoding="utf-8")
    return str(path)


def test_valid_judgement_exits_zero(tmp_path):
    result = run_cli(write_judge(tmp_path))

    assert result.returncode == 0, result.stderr


def test_invalid_judgement_exits_non_zero_naming_the_field(tmp_path):
    result = run_cli(write_judge(tmp_path, exit_code="0"))

    assert result.returncode != 0
    assert "exit_code" in result.stderr


def test_missing_file_exits_non_zero(tmp_path):
    result = run_cli(str(tmp_path / "ausente.json"))

    assert result.returncode != 0
    assert "ausente.json" in result.stderr


def test_emit_schema_matches_the_committed_file():
    result = run_cli("--emit-schema")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (ROLE_ROOT / "judge.schema.json").read_text(encoding="utf-8")
