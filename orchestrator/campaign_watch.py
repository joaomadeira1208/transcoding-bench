"""O que o laço de vigilância decide sem perguntar nada (D8, D10 e D25 da Spec 4).

As três perguntas de cada poll, o `terminate-instances` e o relógio moram no
`orchestrator.py`; aqui ficam o prazo, a linha de cada arquitetura e o veredito
final, que é o que decide o código de saída do `run`. Ver `orchestrator/README.md`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from campaign_state import CampaignState, TrackedInstance
from status_check import HOUR_WIDTH, INSTANCE_WIDTH, Progress, hour_of, progress_line
from vigilance import UNANSWERED_POLL_LIMIT, Vigilance

POLL_INTERVAL_SECONDS = 300.0
LIVENESS_TIMEOUT_SECONDS = 30.0
DEADLINE_MARGIN_SECONDS = 3600.0

RESUME_OUT_DIR = "~/work/resume"

ORCHESTRATOR_CLI = "python orchestrator/orchestrator.py"
RESUME_CLI = "python orchestrator/resume.py"


def watch_deadline_seconds(*, total_timeout: int, bootstrap_timeout: float) -> float:
    """O prazo do Orquestrador, contado do começo da vigilância (D10)."""
    return total_timeout + bootstrap_timeout + DEADLINE_MARGIN_SECONDS


def liveness_command(pid: int) -> list[str]:
    """O `kill -0` no PID gravado, por SSH; o `sudo` está no `orchestrator/README.md`."""
    return ["sudo", "kill", "-0", str(pid)]


def poll_line(
    each: TrackedInstance,
    *,
    state: Vigilance,
    progress: Progress | None,
    unanswered_polls: int = 0,
) -> str:
    """A linha daquela arquitetura neste poll — a das mortas e das mudas inclusive."""
    line = progress_line(progress, instance=each.instance, runs_total=each.runs_total)
    if state is Vigilance.RUNNING:
        return line
    return f"{line}  [{_note(state, unanswered_polls)}]"


def settled_line(each: TrackedInstance) -> str:
    """A linha de quem já saiu do laço: runs feitos, falhas, teto e estado final.

    Na coluna das outras, e sem a hora, que é a do `written_at` de um progresso
    que já não anda.
    """
    return f"{'':{HOUR_WIDTH}} {each.instance:<{INSTANCE_WIDTH}} {_summary(each)}"


def summary_lines(instances: Sequence[TrackedInstance]) -> tuple[str, ...]:
    """O resumo do fim, uma linha por arquitetura, na mesma forma do último poll."""
    return tuple(settled_line(each) for each in instances)


def failure_reasons(
    instances: Sequence[TrackedInstance], *, deadline_blown: bool = False
) -> tuple[str, ...]:
    """Vazio é o status zero; qualquer linha é o `run` saindo com erro (D8/D25)."""
    blown = ("o prazo do Orquestrador estourou: o que restava de pé foi terminado",)
    return (blown if deadline_blown else ()) + tuple(
        reason for each in instances for reason in _reasons(each)
    )


def resume_hint(state: CampaignState) -> tuple[str, ...]:
    """Os dois comandos da retomada, já com a definição e o bucket deste lançamento."""
    return (
        f"o próximo passo é o resume.py: {RESUME_CLI} --config {state.config_path} "
        f"--bucket {state.bucket} --out {RESUME_OUT_DIR}",
        f"e depois, sobre o que ele escrever: {ORCHESTRATOR_CLI} --infra <infra.json> "
        f"run --config {state.config_path} --bucket {state.bucket} --slices {RESUME_OUT_DIR}",
    )


def _reasons(each: TrackedInstance) -> Iterator[str]:
    if each.outcome is None:
        yield f"{each.instance}: sem marcador, estado {each.state.value}"
        return
    if each.outcome.runs_failed:
        yield f"{each.instance}: {each.outcome.runs_failed} run(s) da fatia com falha"
    if each.outcome.capped:
        yield f"{each.instance}: o teto do run_all.sh parou a fatia antes do fim"
    if each.outcome.exit_status:
        yield f"{each.instance}: o run_all.sh saiu com status {each.outcome.exit_status}"


def _summary(each: TrackedInstance) -> str:
    if each.outcome is None:
        return (
            f"{each.state.value}: sem marcador, e o que ela deixou em runs/ é o que o "
            f"resume.py enxerga"
        )
    return (
        f"{each.state.value}: {each.outcome.runs_total}/{each.runs_total} runs, "
        f"{each.outcome.runs_failed} falhas, {_cap(each.outcome.capped)}, "
        f"status {each.outcome.exit_status}, marcador de {hour_of(each.outcome.finished_at)}"
    )


def _cap(capped: bool) -> str:
    return "parada pelo teto" if capped else "sem teto"


def _note(state: Vigilance, unanswered_polls: int) -> str:
    if state is Vigilance.UNRESPONSIVE:
        return (
            f"sem resposta no SSH há {unanswered_polls} poll(s), "
            f"morta depois de {UNANSWERED_POLL_LIMIT}"
        )
    return _NOTES[state]


_NOTES = {
    Vigilance.BOOTSTRAPPING: "bootstrap em curso, ainda sem PID",
    Vigilance.READY_TO_TERMINATE: "marcador válido, terminando agora",
    Vigilance.FINISHED: "terminada",
    Vigilance.DEAD: "morta: sem marcador, e não é relançada",
}
