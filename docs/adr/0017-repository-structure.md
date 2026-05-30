# Estrutura do repositório — organização por papel de execução

O repositório é organizado **por papel de execução**, não por linguagem nem flat. Os diretórios de topo são as fronteiras de "quem é dono / quem roda aquilo": `config/`, `orchestrator/`, `encode/`, `judge/`, `docker/`, `analysis/`, `infra/`.

O princípio organizador vem de dois fatos do projeto:

1. **Toda instância dá `git clone` do repo inteiro** (ADR-0013) e usa só o que precisa. Logo o diretório de topo não é estético — é a fronteira de propriedade/execução. O custo de "diretórios a mais" numa instância é zero, então otimiza-se pra clareza de dono, não pra minimizar o que cada máquina vê.
2. **"Python decide, shell executa"** (ADR-0009) fica materializado fisicamente: Python (`orchestrator/`, `analysis/`) e shell (`encode/`, `judge/`) separados.

```
config/        # SPEC declarativa do experimento → experiment.toml (fonte de verdade)
orchestrator/  # Python na t3.micro: maquinaria que age sobre a spec.
               #   conhecidos: orchestrator.py, resume.py, quality_triage.py
               #   + geração do scenarios.json + bootstrap por papel (user-data fino → bootstrap gordo)
encode/        # shell do encode, em duas camadas:
               #   HOST (bootstrap): docker, perf, sysstat, perf_event_paranoid
               #   CONTAINER (montado): run_all.sh, run_scenario.sh
judge/         # shell do Juiz: HOST bootstrap (sem perf); CONTAINER (montado): run_quality.sh
docker/        # AMBIENTE de medição, compartilhado encode+judge → Dockerfile (ver ADR-0018)
analysis/      # Python local (pandas/pyarrow): consolidate.py + requirements + notebooks
infra/         # Terraform, só do Mac. Arquivos planos por preocupação. Backend S3 remoto (ADR-0020).
docs/          # adr/ + CONTEXT.md
```

Três escolhas de fronteira que o layout codifica:

- **`config/` separado de `orchestrator/`.** A definição do experimento é a *especificação* (declarativa, auditável, vira anexo do artigo); o orquestrador é a *maquinaria* que a executa. Mesmo instinto de separar contrato-de-dado de código. A spec não fica enterrada na lógica.
- **`docker/` separado de `encode/`.** A imagem é o *ambiente de medição reprodutível* (compartilhado por encode e Juiz); os `run_*.sh` são *código* montado nela. Ver ADR-0018.
- **Cada papel é dono do seu bootstrap.** `user-data.sh`/`bootstrap.sh` moram com o papel; o Terraform e o orquestrador apenas fiam esses arquivos (`templatefile()` / `--user-data`), não os contêm.

## Empacotamento Python

Dois `requirements.txt` separados (`orchestrator/` quase vazio — stdlib + AWS CLI via `subprocess`; `analysis/` com pandas/pyarrow), não um `pyproject` com extras. Não há pacote a construir nem código compartilhado entre as pontas (o contrato entre `generate_scenarios`/`orchestrator` e `consolidate` é o JSON validado da ADR-0019, não um módulo comum). Python pinado em **3.12** (o que o Ubuntu 24.04 LTS entrega — ADR-0015 — e que tem `tomllib` na stdlib).

## Considered Options

- **Por linguagem** (`python/`, `bash/`, `terraform/`) — rejeitado: esconde quem roda o quê; uma instância de encode teria que vasculhar `python/` e `bash/` pra montar seu papel. Por papel, o papel é o diretório.
- **Flat (tudo na raiz)** — rejeitado: 4 papéis + spec + infra + análise na raiz vira ruído; perde a fronteira de propriedade.
- **`pyproject.toml` com extras** — rejeitado: sem pacote a distribuir e sem código compartilhado, adicionaria build backend e `pip install -e .[extra]` no bootstrap sem ganho. Config de linter mora em `ruff.toml` no topo.
- **Segredos/artefatos versionados** — rejeitado por construção: o repo é clonado em instâncias com IP público. `scenarios.json`, `quality_plan.json`, `meta.json`, `*.pem` são runtime/segredo e entram no `.gitignore`; o state do Terraform é remoto (ADR-0020).

## Consequences

- Bootstrap do orquestrador vira `git clone` → `venv` → `pip install -r orchestrator/requirements.txt` — coerente com a ADR-0013.
- O nome do gerador do `scenarios.json`, dos `.tf` e dos scripts de bootstrap fica em aberto até o desenvolvimento; só os nomes já fixados por ADR (`orchestrator.py`, `resume.py`, `quality_triage.py`, `consolidate.py`, `run_all.sh`, `run_scenario.sh`, `run_quality.sh`, `Dockerfile`) e pela spec (`experiment.toml`) estão cravados.
