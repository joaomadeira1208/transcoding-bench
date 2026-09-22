"""A leitura da árvore `runs/` sincronizada: os `meta.json` e os `output.sha256`.

O diretório temporário que o `s3 sync` preencheu **é** a enumeração das Execuções
(ADR-0012), e os dois CLIs que decidem sobre o bucket — a retomada e o triage do
Pass — leem a mesma árvore. A leitura mora aqui uma vez só: é o mesmo papel e o
mesmo venv, e a duplicação que a ADR-0022 licencia é entre papéis, não dentro de
um.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from external import RUN_HASH_FILENAME, RUN_META_FILENAME, RUNS_PREFIX
from meta_check import MetaError, check_meta


def read_runs(runs: Path) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """Os `meta.json` e os `output.sha256` da árvore, com um aviso por Execução sem meta.

    O `output.sha256` ausente não é erro aqui: a retomada nem chega a baixá-lo, e
    no triage quem sabe se a falta importa é o núcleo, que sabe quais Execuções
    venceram a dedup e saíram com `exit_code` 0. Cada hash é chaveado pelo
    `run_id` do `meta.json` ao lado dele, e não pelo nome do diretório: é o
    `run_id` que o plano carrega e pelo qual o núcleo pergunta.

    O arquivo ofensor é nomeado pela **chave no bucket**, que é o que sobrevive ao
    diretório temporário.
    """
    metas: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    warnings: list[str] = []
    for run_dir in sorted(path for path in runs.iterdir() if path.is_dir()):
        key = f"{RUNS_PREFIX}{run_dir.name}/{RUN_META_FILENAME}"
        meta_path = run_dir / RUN_META_FILENAME
        if not meta_path.is_file():
            warnings.append(f"{key}: ausente, Execução ignorada")
            continue
        try:
            meta = check_meta(meta_path.read_bytes())
        except MetaError as error:
            raise MetaError(f"{key}: {error}") from error
        metas.append(meta)
        hash_path = run_dir / RUN_HASH_FILENAME
        if hash_path.is_file():
            # `read_text` levantaria `UnicodeDecodeError` — que não é `OSError`, e
            # que nenhum dos dois CLIs pega — em vez de deixar o núcleo recusar o
            # hash malformado nomeando o run.
            hashes[meta["run_id"]] = hash_path.read_bytes().decode("utf-8", "replace").strip()
    return metas, hashes, warnings
