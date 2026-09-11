"""O núcleo puro do `prepare-masters`: o comando remoto e a conferência do espelho.

As duas funções recebem dado já buscado e devolvem dado — quem lança a instância,
abre o SSH e lista os prefixos é o `orchestrator.py`, sobre o `external.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from command_output import S3Object

IMAGE_TAG = "transcoding-bench"

SCRIPTS_MOUNT = "/masters"
WORK_MOUNT = "/work"

PREPARE_SCRIPT = f"{SCRIPTS_MOUNT}/prepare.sh"

# O `ENV VERSIONS_FILE` do `docker/Dockerfile`, que é onde a imagem grava as
# versões que o manifesto carrega verbatim.
IMAGE_VERSIONS_FILE = "/opt/transcoding-bench/versions.json"


def prepare_masters_command(
    *,
    plan: str,
    bucket: str,
    repo_dir: str,
    work_dir: str,
) -> list[str]:
    """O `docker run` da preparação, como argv para o SSH citar e o shell remoto abrir."""
    return [
        "sudo",
        "docker",
        "run",
        "--rm",
        "-v",
        f"{repo_dir}/masters:{SCRIPTS_MOUNT}:ro",
        "-v",
        f"{work_dir}:{WORK_MOUNT}",
        IMAGE_TAG,
        "bash",
        PREPARE_SCRIPT,
        "--plan",
        plan,
        "--bucket",
        bucket,
        "--work-dir",
        WORK_MOUNT,
        "--versions-file",
        IMAGE_VERSIONS_FILE,
    ]


def mirror_differences(
    source: Sequence[S3Object],
    destination: Sequence[S3Object],
) -> tuple[str, ...]:
    """Tudo em que as duas listagens divergem, nomeando cada objeto."""
    sizes = _sizes(source)
    mirrored = _sizes(destination)
    differences = [
        f"{key}: no bucket da campanha e ausente no do piloto"
        for key in sizes
        if key not in mirrored
    ]
    differences += [
        f"{key}: no bucket do piloto e ausente no da campanha"
        for key in mirrored
        if key not in sizes
    ]
    differences += [
        f"{key}: tamanho diverge entre os buckets: {size} na campanha, {mirrored[key]} no piloto"
        for key, size in sizes.items()
        if key in mirrored and mirrored[key] != size
    ]
    return tuple(differences)


def _sizes(objects: Sequence[S3Object]) -> Mapping[str, int]:
    return {entry.key: entry.size for entry in objects}
