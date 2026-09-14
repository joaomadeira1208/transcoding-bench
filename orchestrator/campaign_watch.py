"""O que o laço de vigilância decide sem perguntar nada (D8, D10 e D25 da Spec 4).

As três perguntas de cada poll, o `terminate-instances` e o relógio moram no
`orchestrator.py`; aqui ficam o prazo, a linha de cada arquitetura e o veredito
final, que é o que decide o código de saída do `run`. Ver `orchestrator/README.md`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from campaign_state import CampaignState, TrackedInstance
from status_check import INSTANCE_WIDTH, Progress, hour_of, progress_line
from vigilance import UNANSWERED_POLL_LIMIT, Vigilance

# O intervalo que a ADR-0010 fixou para o polling, e o que o `kill -0` aceita de
# espera antes de virar "sem resposta": o teto é curto porque um poll que demora
# é um poll que não aconteceu.
POLL_INTERVAL_SECONDS = 300.0
LIVENESS_TIMEOUT_SECONDS = 30.0

# A folga do prazo de D10 sobre os dois tempos que ele já cobre: o disparo, os
# 5 minutos de granularidade do poll e os `terminate-instances` do fim.
DEADLINE_MARGIN_SECONDS = 3600.0

RESUME_OUT_DIR = "~/work/resume"

# Os dois CLIs como o `orchestrator/README.md` os invoca: as mensagens que
# mandam o pesquisador rodar alguma coisa dizem o comando inteiro.
ORCHESTRATOR_CLI = "python orchestrator/orchestrator.py"
RESUME_CLI = "python orchestrator/resume.py"


def watch_deadline_seconds(
    *,
    total_timeout: int,
    bootstrap_timeout: float,
    margin: float = DEADLINE_MARGIN_SECONDS,
) -> float:
    """O prazo do Orquestrador, contado do começo da vigilância (D10).

    Os três termos, e não só o teto de cada Instância: o `run` começa a contar
    antes do bootstrap, e o `run_all.sh` só começa a contar o dele quando o
    container sobe. Um prazo de exatamente `total_timeout` terminaria as três na
    véspera do fim, com as 46 h faturadas e nenhum marcador escrito.
    """
    return total_timeout + bootstrap_timeout + margin


def liveness_command(pid: int) -> list[str]:
    """O `kill -0` no PID gravado, por SSH.

    Com `sudo` porque o `launch_container.sh` termina em `exec sudo docker run`:
    o PID que o disparo ecoou é de um processo de root, e perguntar por ele como
    `ubuntu` recebe `EPERM` — que é indistinguível de "morto" no status de saída.
    """
    return ["sudo", "kill", "-0", str(pid)]


def poll_line(
    *,
    instance: str,
    state: Vigilance,
    progress: Progress | None,
    runs_total: int,
    unanswered_polls: int = 0,
) -> str:
    """A linha daquela arquitetura neste poll — a das mortas e das mudas inclusive.

    Sempre as três, sempre na mesma ordem: uma linha que sumisse por a
    arquitetura ter morrido seria lida às 3 da manhã como a arquitetura que
    nunca subiu.
    """
    line = progress_line(progress, instance=instance, runs_total=runs_total)
    if state is Vigilance.RUNNING:
        return line
    return f"{line}  [{_note(state, unanswered_polls)}]"


def summary_lines(instances: Sequence[TrackedInstance]) -> tuple[str, ...]:
    """O resumo do fim, uma linha por arquitetura: runs feitos, falhas, teto e estado."""
    return tuple(f"{each.instance:<{INSTANCE_WIDTH}} {_summary(each)}" for each in instances)


def failure_reasons(
    instances: Sequence[TrackedInstance], *, deadline_blown: bool = False
) -> tuple[str, ...]:
    """Vazio é o status zero; qualquer linha é o `run` saindo com erro (D8/D25).

    Uma campanha em que uma arquitetura morreu, falhou runs ou bateu no teto
    continua sendo uma campanha incompleta, e o status zero a arquivaria como
    pronta — a falha que o `resume.py` existe para ser chamado contra.
    """
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
        return f"sem resposta no SSH ({unanswered_polls}/{UNANSWERED_POLL_LIMIT} polls)"
    return _NOTES[state]


_NOTES = {
    Vigilance.BOOTSTRAPPING: "bootstrap em curso, ainda sem PID",
    Vigilance.READY_TO_TERMINATE: "marcador válido, terminando agora",
    Vigilance.FINISHED: "terminada",
    Vigilance.DEAD: "morta: sem marcador, e não é relançada",
}
