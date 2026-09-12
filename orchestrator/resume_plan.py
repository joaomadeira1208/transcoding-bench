"""O núcleo da retomada: o que está completo, o que falta e a fatia reduzida.

Funções puras sobre o plano canônico e os `meta.json` já validados pelo
`meta_check`. Quem baixa o `runs/` do bucket, imprime e escreve os arquivos é o
`resume.py`; o que este módulo não faz é lançar coisa alguma (ADR-0012).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from scenario_plan import build_instance_slices


class Pending(Enum):
    """Por que um bloco volta inteiro para a fatia reduzida, na ordem de avaliação."""

    MISSING = "ausente"
    PARTIAL = "parcial"
    FAILED = "com falha"
    EXCLUDED = "excluído por commit"


@dataclass(frozen=True)
class PendingBlock:
    block_id: str
    reason: Pending


@dataclass(frozen=True)
class InstanceResume:
    instance: str
    complete: tuple[str, ...]
    pending: tuple[PendingBlock, ...]


def block_id(block: Mapping[str, Any]) -> str:
    """O nome do bloco: a `scenario_id` das suas Execuções sem o sufixo.

    Tirada das `scenario_id` que o plano carrega, e não remontada dos campos de
    identidade do bloco: o `slug` do codec entra na `scenario_id` e não sobrevive
    no bloco, então remontar inventaria um nome que não é o que está no bucket
    assim que um `slug` deixar de coincidir com o `encoder`.
    """
    stem, _, _ = block["runs"][0]["scenario_id"].rpartition("_")
    return stem


def resume(
    plan: Mapping[str, Any],
    metas: Iterable[Mapping[str, Any]],
    *,
    excluded_commits: Iterable[str] = (),
) -> tuple[InstanceResume, ...]:
    """O que cada arquitetura do plano já tem e o que falta, na ordem do plano."""
    winners = _winners(metas)
    excluded = frozenset(excluded_commits)

    verdicts: dict[str, list[tuple[str, Pending | None]]] = {}
    for block in plan["blocks"]:
        verdicts.setdefault(block["instance"], []).append(
            (block_id(block), _reason(block, winners, excluded))
        )

    return tuple(
        InstanceResume(
            instance=instance,
            complete=tuple(name for name, reason in blocks if reason is None),
            pending=tuple(
                PendingBlock(name, reason) for name, reason in blocks if reason is not None
            ),
        )
        for instance, blocks in verdicts.items()
    )


def reduced_slices(
    plan: Mapping[str, Any], resumes: Sequence[InstanceResume]
) -> dict[str, dict[str, Any]]:
    """Uma fatia por arquitetura com pendência, com os blocos pendentes inteiros."""
    pending = {block.block_id for each in resumes for block in each.pending}
    return build_instance_slices(
        {**plan, "blocks": [block for block in plan["blocks"] if block_id(block) in pending]}
    )


def render_report(resumes: Sequence[InstanceResume]) -> str:
    return "\n".join(_report_lines(resumes))


def _report_lines(resumes: Sequence[InstanceResume]) -> Iterable[str]:
    for each in resumes:
        total = len(each.complete) + len(each.pending)
        yield (
            f"{each.instance}: {len(each.complete)}/{total} blocos completos, "
            f"{len(each.pending)} pendentes"
        )
        for block in each.pending:
            yield f"  {block.block_id}  {block.reason.value}"


def _reason(
    block: Mapping[str, Any],
    winners: Mapping[str, Mapping[str, Any]],
    excluded: frozenset[str],
) -> Pending | None:
    replications = [run["scenario_id"] for run in block["runs"] if not run["warmup"]]
    observed = [winners[name] for name in replications if name in winners]

    if not observed:
        return Pending.MISSING
    if len(observed) < len(replications):
        return Pending.PARTIAL
    if any(meta["exit_code"] != 0 for meta in observed):
        return Pending.FAILED
    if any(meta["commit"] in excluded for meta in observed):
        return Pending.EXCLUDED
    return None


def _winners(metas: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Uma Replicação por `scenario_id`: o maior `started_at` vence, `run_id` desempata."""
    latest: dict[str, Mapping[str, Any]] = {}
    for meta in metas:
        if meta["warmup"]:
            continue
        current = latest.get(meta["scenario_id"])
        if current is None or _instant(meta) > _instant(current):
            latest[meta["scenario_id"]] = meta
    return latest


def _instant(meta: Mapping[str, Any]) -> tuple[datetime, str]:
    return datetime.fromisoformat(meta["started_at"]), meta["run_id"]
