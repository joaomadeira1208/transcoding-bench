"""A retenção seletiva da ADR-0007: que `output.{container}` o `clean` mantém e qual apaga.

A decisão é função pura sobre o plano que o Juiz leu, os `meta.json` e os
`output.sha256` de `runs/` e os `judge.json` de `quality/results/`, e não conhece
`--apply`: quem apaga é o `orchestrator.py clean`, depois de imprimi-la.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from external import QUALITY_RESULTS_PREFIX, RUNS_PREFIX
from judgement_check import JUDGE_FILENAME, JudgementError, check_judgement
from resume_plan import winning_replications

OUTPUT_STEM = "output"


class CleanError(Exception):
    """O que o `clean` recusa antes de decidir, com a chave ofensora nomeada."""


class Verdict(Enum):
    """O destino do `output.{container}` de uma Execução, e o motivo dele."""

    KEEP_JUDGED = "manter: representante julgado com exit_code 0"
    KEEP_JUDGEMENT_FAILED = "manter: bitstream julgado com falha"
    KEEP_UNJUDGED = "manter: bitstream sem julgamento"
    KEEP_NO_HASH = "manter: sem output.sha256"
    DELETE_COPY = "apagar: cópia bit-idêntica de um representante julgado"
    DELETE_WARMUP = "apagar: warm-up"
    DELETE_FAILED = "apagar: run falho"
    DELETE_SUPERSEDED = "apagar: superado pela dedup"

    @property
    def deletes(self) -> bool:
        return self in _DELETING


_DELETING = frozenset(
    {
        Verdict.DELETE_COPY,
        Verdict.DELETE_WARMUP,
        Verdict.DELETE_FAILED,
        Verdict.DELETE_SUPERSEDED,
    }
)


@dataclass(frozen=True)
class Retention:
    """Uma chave sob `runs/` e o que o `clean` faz com ela."""

    key: str
    verdict: Verdict


def decide(
    plan: Mapping[str, Any],
    metas: Iterable[Mapping[str, Any]],
    hashes: Mapping[str, str],
    judgements: Mapping[str, Mapping[str, Any]],
) -> tuple[Retention, ...]:
    """Um veredito por Execução do bucket, na ordem dos `meta.json`.

    O representante julgado vence qualquer outro motivo: é o `output.mkv` que o
    Juiz mediu, e mesmo superado por uma retomada posterior ao triage ele é o
    que o artigo reporta.
    """
    metas = list(metas)
    present = {meta["run_id"] for meta in metas}
    judged: dict[tuple[str, str], str] = {}
    failed: set[tuple[str, str]] = set()
    for output in plan["outputs"]:
        bitstream = (scenario_of(output["scenario_id"]), output["sha256"])
        judgement = judgements.get(output["run_id"])
        _require_same_bitstream(output, present, hashes, judgement)
        if judgement is None:
            continue
        if judgement["exit_code"] == 0:
            judged[bitstream] = output["run_id"]
        else:
            failed.add(bitstream)

    representatives = frozenset(judged.values())
    winners = winning_replications(metas)
    return tuple(
        Retention(
            key=output_key(meta),
            verdict=_verdict(meta, winners, hashes, representatives, judged, failed),
        )
        for meta in metas
    )


def deletions(decision: Sequence[Retention]) -> list[str]:
    return [each.key for each in decision if each.verdict.deletes]


def require_finished_pass(
    marker_finished_at: str, judgements: Mapping[str, Mapping[str, Any]]
) -> None:
    """Recusa o `status/judge_done` mais velho que algum `judge.json` do bucket.

    Numa repetição do Pass o marcador do anterior continua no bucket até o novo
    terminar, e é o `judge.json` escrito depois dele que denuncia o Juiz no meio.
    """
    finished = datetime.fromisoformat(marker_finished_at)
    for run_id, judgement in judgements.items():
        if datetime.fromisoformat(judgement["finished_at"]) > finished:
            raise CleanError(
                f"{_judgement_key(run_id)}: terminou em {judgement['finished_at']}, depois do "
                f"status/judge_done ({marker_finished_at}) — há um Pass em andamento"
            )


def render_decision(decision: Sequence[Retention]) -> str:
    lines: list[str] = []
    for verdict in Verdict:
        keys = [each.key for each in decision if each.verdict is verdict]
        if keys:
            lines.append(f"{verdict.value} ({len(keys)})")
            lines.extend(f"  {key}" for key in keys)
    deleted = len(deletions(decision))
    lines.append(f"{len(decision) - deleted} a manter, {deleted} a apagar")
    return "\n".join(lines)


def read_judgements(results: Path) -> dict[str, dict[str, Any]]:
    """Os `judge.json` de `quality/results/{run_id}/` que o `s3 sync` baixou.

    Só o nível do `run_id`: a evidência do `preflight` mora um nível abaixo, em
    `quality/results/preflight/<instance-id>/`, e não julgou output do plano.
    """
    judgements: dict[str, dict[str, Any]] = {}
    if not results.is_dir():
        return judgements
    for path in sorted(results.glob(f"*/{JUDGE_FILENAME}")):
        key = _judgement_key(path.parent.name)
        try:
            judgement = check_judgement(path.read_bytes())
        except JudgementError as error:
            raise JudgementError(f"{key}: {error}") from error
        if judgement["run_id"] != path.parent.name:
            raise JudgementError(f"{key}: run_id {judgement['run_id']!r} é o de outro diretório")
        judgements[judgement["run_id"]] = judgement
    return judgements


def output_key(meta: Mapping[str, Any]) -> str:
    """`runs/{run_id}/output.{container}`: a única forma de chave que o `clean` apaga."""
    container = meta.get("container")
    if type(container) is not str or not container.isalnum():
        raise CleanError(
            f"{RUNS_PREFIX}{meta['run_id']}/meta.json: container: esperava uma extensão "
            f"alfanumérica, veio {container!r}"
        )
    return f"{RUNS_PREFIX}{meta['run_id']}/{OUTPUT_STEM}.{container}"


def scenario_of(scenario_id: str) -> str:
    """O Cenário da ADR-0025: a `scenario_id` sem a arquitetura e sem o sufixo."""
    stem, _, _ = scenario_id.rpartition("_")
    stem, _, _ = stem.rpartition("_")
    return stem


def _verdict(
    meta: Mapping[str, Any],
    winners: Mapping[str, Mapping[str, Any]],
    hashes: Mapping[str, str],
    representatives: frozenset[str],
    judged: Mapping[tuple[str, str], str],
    failed: set[tuple[str, str]],
) -> Verdict:
    if meta["run_id"] in representatives:
        return Verdict.KEEP_JUDGED
    if meta["warmup"]:
        return Verdict.DELETE_WARMUP
    if meta["exit_code"] != 0:
        return Verdict.DELETE_FAILED
    if winners[meta["scenario_id"]] is not meta:
        return Verdict.DELETE_SUPERSEDED

    digest = hashes.get(meta["run_id"])
    if digest is None:
        return Verdict.KEEP_NO_HASH
    bitstream = (scenario_of(meta["scenario_id"]), digest)
    if bitstream in judged:
        return Verdict.DELETE_COPY
    if bitstream in failed:
        return Verdict.KEEP_JUDGEMENT_FAILED
    return Verdict.KEEP_UNJUDGED


def _require_same_bitstream(
    output: Mapping[str, Any],
    present: set[str],
    hashes: Mapping[str, str],
    judgement: Mapping[str, Any] | None,
) -> None:
    """O plano, o bucket e o Juiz têm de falar do mesmo bitstream, ou nada é decidido.

    Um `--plan` de outro triage ou de outro bucket casaria `sha256` com as
    Execuções erradas, e as cópias apagadas seriam as de um representante que o
    Juiz nunca mediu.
    """
    run_id = output["run_id"]
    where = f"{RUNS_PREFIX}{run_id}"
    if run_id not in present:
        raise CleanError(f"{where}: representante do plano sem meta.json neste bucket")
    if hashes.get(run_id) != output["sha256"]:
        raise CleanError(
            f"{where}/output.sha256: {hashes.get(run_id)!r} não é o bitstream que o plano "
            f"nomeia ({output['sha256']})"
        )
    if judgement is not None and judgement["sha256"] != output["sha256"]:
        raise CleanError(
            f"{_judgement_key(run_id)}: julgou {judgement['sha256']}, e o plano nomeia "
            f"{output['sha256']}"
        )


def _judgement_key(run_id: str) -> str:
    return f"{QUALITY_RESULTS_PREFIX}{run_id}/{JUDGE_FILENAME}"
