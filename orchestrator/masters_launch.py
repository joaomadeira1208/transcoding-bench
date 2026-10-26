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
    ]


def mirror_differences(
    source: Sequence[S3Object],
    destination: Sequence[S3Object],
) -> tuple[str, ...]:
    """Tudo em que as duas listagens divergem, nomeando cada objeto."""
    sizes = _sizes(source)
    mirrored = _sizes(destination)
    differences = [f"{key}: na origem e ausente no destino" for key in sizes if key not in mirrored]
    differences += [
        f"{key}: no destino e ausente na origem" for key in mirrored if key not in sizes
    ]
    differences += [
        f"{key}: tamanho diverge: {size} na origem, {mirrored[key]} no destino"
        for key, size in sizes.items()
        if key in mirrored and mirrored[key] != size
    ]
    return tuple(differences)


def unmirrored(listings: Mapping[str, Sequence[S3Object]], reference: str) -> tuple[str, ...]:
    """Cada bucket cujo `masters/` não é o do de referência, com o que diverge nele.

    Pela listagem inteira, e não pelo manifesto: um `sync` interrompido deixa o
    manifesto no destino e um Master pela metade, e quem o descobre é o
    bootstrap da instância, depois do build.
    """
    return tuple(
        f"{bucket}: {difference}"
        for bucket, listed in listings.items()
        if bucket != reference
        for difference in mirror_differences(listings[reference], listed)
    )


def _sizes(objects: Sequence[S3Object]) -> Mapping[str, int]:
    return {entry.key: entry.size for entry in objects}
