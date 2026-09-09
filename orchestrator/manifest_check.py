"""Checagem do `masters/manifest.json` em stdlib pura — a metade automatizável do
gate humano da ADR-0012/0014.

O manifesto diz o que a preparação observou; o plano dos Masters diz o que a spec
pede, e este módulo é a comparação dos dois.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from experiment_config import SHA256_DIGITS, ExperimentConfig, is_sha256
from masters_plan import build_masters_plan, iter_masters

KNOWN_SCHEMA_VERSIONS = frozenset({"1"})

_PROBED_FIELDS = (
    "video",
    "tier",
    "codec_name",
    "pix_fmt",
    "frame_rate",
    "width",
    "height",
    "frames",
)


def check_manifest(manifest: Any, config: ExperimentConfig) -> tuple[str, ...]:
    """As divergências entre o manifesto parseado e a spec validada, ou nada."""
    if not isinstance(manifest, Mapping):
        return (f"manifest: expected a JSON object, got {type(manifest).__name__}",)

    plan = build_masters_plan(config)
    return (
        *_check_schema_version(manifest),
        *_check_versions(manifest),
        *_check_sources(manifest, plan),
        *_check_masters(manifest, plan),
    )


def _check_schema_version(manifest: Mapping[str, Any]) -> Iterator[str]:
    if "schema_version" not in manifest:
        yield "schema_version: missing required key"
        return

    value = manifest["schema_version"]
    if type(value) is not str or value not in KNOWN_SCHEMA_VERSIONS:
        known = ", ".join(sorted(KNOWN_SCHEMA_VERSIONS))
        yield f"schema_version: must be one of the known versions ({known}), got {value!r}"


def _check_versions(manifest: Mapping[str, Any]) -> Iterator[str]:
    if "versions" not in manifest:
        yield "versions: missing required key"
        return

    versions = manifest["versions"]
    if not isinstance(versions, Mapping) or not versions:
        yield f"versions: must be a non-empty table of tool versions, got {versions!r}"
        return

    for tool, version in versions.items():
        if type(version) is not str or not version:
            yield f"versions: '{tool}' must be a non-empty string, got {version!r}"


def _check_sources(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> Iterator[str]:
    if "sources" not in manifest:
        yield "sources: missing required key"
        return

    sources = manifest["sources"]
    if not isinstance(sources, Mapping):
        yield f"sources: must be a table keyed by video, got {type(sources).__name__}"
        return

    expected = {video["video"]: video["source"] for video in plan["videos"]}
    for slug, source in expected.items():
        if slug not in sources:
            yield f"sources: missing '{slug}'"
            continue
        yield from _check_source(slug, sources[slug], source)

    for slug in sources:
        if slug not in expected:
            yield f"sources: '{slug}' is not a video of the spec"


def _check_source(slug: str, observed: Any, expected: Mapping[str, Any]) -> Iterator[str]:
    where = f"source '{slug}'"
    if not isinstance(observed, Mapping):
        yield f"{where}: must be a table, got {type(observed).__name__}"
        return

    for field, value in expected.items():
        yield from _check_equal(where, field, observed, value)


def _check_masters(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> Iterator[str]:
    if "masters" not in manifest:
        yield "masters: missing required key"
        return

    masters = manifest["masters"]
    if not isinstance(masters, list):
        yield f"masters: must be a list, got {type(masters).__name__}"
        return

    expected = {master["name"]: master for master in iter_masters(plan)}

    seen: set[str] = set()
    for index, master in enumerate(masters):
        where = f"masters[{index}]"
        if not isinstance(master, Mapping):
            yield f"{where}: must be a table, got {type(master).__name__}"
            continue

        name = master.get("name")
        if type(name) is not str or not name:
            yield f"{where}: 'name' must be a non-empty string, got {name!r}"
            continue
        if name in seen:
            yield f"masters: '{name}' is listed twice"
            continue
        seen.add(name)
        if name not in expected:
            yield f"masters: '{name}' is not a master of the spec"
            continue

        yield from _check_master(master, expected[name])

    for name in expected:
        if name not in seen:
            yield f"masters: missing '{name}'"


def _check_master(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> Iterator[str]:
    where = f"master '{expected['name']}'"

    for field in _PROBED_FIELDS:
        yield from _check_equal(where, field, observed, expected[field])

    yield from _check_size(where, observed)
    yield from _check_sha256(where, observed)


def _check_equal(
    where: str, field: str, observed: Mapping[str, Any], expected: Any
) -> Iterator[str]:
    """Tipo exato **e** valor, o tipo sendo o do valor esperado.

    `type` no lugar de `isinstance`, como no `meta_check.py`: é o que barra o
    `true` que o `jq` escreve por acidente onde se esperava um inteiro.
    """
    if field not in observed:
        yield f"{where}: missing required key '{field}'"
        return

    value = observed[field]
    if type(value) is not type(expected):
        yield f"{where}: '{field}' must be a {type(expected).__name__}, got {value!r}"
    elif value != expected:
        yield f"{where}: '{field}' must be {expected!r}, got {value!r}"


def _check_size(where: str, observed: Mapping[str, Any]) -> Iterator[str]:
    """Sem valor esperado — o tamanho é observação —, mas um zero é upload vazio."""
    if "size" not in observed:
        yield f"{where}: missing required key 'size'"
        return

    size = observed["size"]
    if type(size) is not int or size <= 0:
        yield f"{where}: 'size' must be a positive integer, got {size!r}"


def _check_sha256(where: str, observed: Mapping[str, Any]) -> Iterator[str]:
    if "sha256" not in observed:
        yield f"{where}: missing required key 'sha256'"
        return

    digest = observed["sha256"]
    if type(digest) is not str or not is_sha256(digest):
        yield (
            f"{where}: 'sha256' must be {SHA256_DIGITS} lowercase hexadecimal digits, "
            f"got {digest!r}"
        )
