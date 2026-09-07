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
               #   tests/ (pytest, roda só no Mac) + requirements-dev.txt
encode/        # shell do encode, em duas camadas:
               #   HOST (bootstrap): docker, perf, sysstat, perf_event_paranoid
               #   CONTAINER (montado): run_all.sh, run_scenario.sh
judge/         # shell do Juiz: HOST bootstrap (sem perf); CONTAINER (montado): run_quality.sh
docker/        # AMBIENTE de medição, compartilhado encode+judge → Dockerfile (ver ADR-0018)
analysis/      # Python local (pandas/pyarrow): consolidate.py + schema do meta.json (pydantic, ADR-0019) + requirements + notebooks
               #   tests/ (pytest) + requirements-dev.txt
smoke/         # Smoke local (ADR-0022), só do Mac + CI: pytest dirigindo os run_*.sh
               #   com shims de ffmpeg/perf/pidstat/aws. Nunca importa; só invoca.
               #   + requirements-dev.txt próprio (pytest + pydantic)
infra/         # Terraform, só do Mac. Arquivos planos por preocupação. Backend S3 remoto (ADR-0020).
docs/          # adr/ + CONTEXT.md
```

Três escolhas de fronteira que o layout codifica:

- **`config/` separado de `orchestrator/`.** A definição do experimento é a *especificação* (declarativa, auditável, vira anexo do artigo); o orquestrador é a *maquinaria* que a executa. Mesmo instinto de separar contrato-de-dado de código. A spec não fica enterrada na lógica.
- **`docker/` separado de `encode/`.** A imagem é o *ambiente de medição reprodutível* (compartilhado por encode e Juiz); os `run_*.sh` são *código* montado nela. Ver ADR-0018.
- **Cada papel é dono do seu bootstrap.** `user-data.sh`/`bootstrap.sh` moram com o papel; o Terraform e o orquestrador apenas fiam esses arquivos (`templatefile()` / `--user-data`), não os contêm.

## Empacotamento Python

Dois `requirements.txt` de runtime separados (`orchestrator/` quase vazio — stdlib + AWS CLI via `subprocess`; `analysis/` com pandas/pyarrow), não um `pyproject` com extras. Não há pacote a construir nem código compartilhado entre as pontas (o contrato entre `generate_scenarios`/`orchestrator` e `consolidate` é o JSON validado da ADR-0019, não um módulo comum). Python pinado em **3.12** (o que o Ubuntu 24.04 LTS entrega — ADR-0015 — e que tem `tomllib` na stdlib).

Os testes automatizados moram **co-localizados por papel** — `orchestrator/tests/` e `analysis/tests/` — coerente com "o papel é o dono". Só os dois papéis Python têm testes unitários (a fronteira de testabilidade é "núcleo lógico determinístico, sem side-effect externo"); `encode/`/`judge/` (shell) ficam fora do TDD — mas não sem verificação: o smoke local da ADR-0022 (em `smoke/`, que é papel próprio porque quem o roda é o Mac do pesquisador) é onde os `run_*.sh` são exercitados. As **dependências de teste** ficam num `requirements-dev.txt` por papel (runtime + `pytest`), **separado** do `requirements.txt` de runtime: o bootstrap da t3.micro instala só o runtime (ADR-0013), nunca o `-dev` — mantém a instância magra e os testes do orquestrador rodando num ambiente stdlib-only fiel ao dela (um `import pandas` acidental no orquestrador quebra o teste). São três `requirements-dev.txt` ao todo, contando o do `smoke/`. Testes rodam só no Mac (e no CI); as instâncias clonam e ignoram `tests/` e `smoke/` (custo zero, coerente com a organização por papel).

**O quê** ganha teste, **como** o `subprocess`/AWS CLI é isolado, de onde vêm as fixtures e o que o smoke cobre são decisões da ADR-0022, não deste ADR.

## Convenções e linters

Cada linguagem usa o linter/formatter padrão da sua camada, decididos antes do desenvolvimento porque o fluxo é por PR/issue e formato indefinido vira discussão de estilo em cada PR:

- **Python:** `ruff` (lint) + `ruff format` — config em `ruff.toml` no topo.
- **Bash:** `shellcheck` (lint) + `shfmt` (format).
- **Terraform:** `terraform fmt`.

O **`pre-commit` é a casa oficial dos linters** (não opcional): amarra os hooks — `ruff`, `shellcheck`/`shfmt`, `terraform fmt`, `terraform validate` (precedido de `terraform init -backend=false`, que o hook faz internamente), `hadolint` e o `gitleaks` (seção de segredos abaixo) — com as versões pinadas no `.pre-commit-config.yaml`. `terraform validate` e `hadolint` foram promovidos de "extensão aceitável" a decididos pela ADR-0022. O hook `terraform_validate` exige o binário `terraform` no `PATH` (o pre-commit não gerencia esse ambiente), então o workflow do CI ganha um passo de setup. Por isso os linters **não entram em nenhum `requirements`**: o pre-commit gerencia os próprios ambientes. O `ruff.toml` no topo segue sendo a config consumida tanto pelo hook quanto por execução manual.

Um **CI mínimo no GitHub Actions** mecaniza essas fronteiras a cada PR (grátis em repo público — ADR-0021), em **três jobs**: `pre-commit run --all-files` (mesmos hooks pinados, zero drift local/CI); pytest **por papel Python, em venvs separados** — o do `orchestrator/` instala só o seu `requirements-dev.txt`, então um `import pandas` acidental quebra no ambiente limpo do CI, exatamente o modo de falha previsto acima; e o **smoke local** da ADR-0022, que é elegível porque não usa Docker, credencial AWS nem FFmpeg de verdade (tudo shimado). O CI é evidência anexada ao PR, não gate autônomo: merge continua decisão do review (humano + agente). Fora por design: qualquer coisa que exija credencial AWS (a validação de fumaça da ADR-0016/0022 é manual) e build do Docker (`-march=native` em runner de CPU errada, ADR-0013). Com o repo público, o secret scanning + push protection do GitHub (o "bônus server-side" da seção de segredos) ficam ativados.

## Segredos e segurança do versionamento

O repo é clonado em instâncias com IP público (ADR-0016), então nada sensível pode entrar no histórico. Duas camadas, cobrindo vetores ortogonais:

1. **`.gitignore` deny-by-default (allowlist).** Ignora `*` e re-permite só extensões/arquivos de fonte. Arquivo sensível de tipo inesperado (`*.pem`, `.env`, dump de credencial) fica fora por padrão — esquecer passa a ser seguro. O que é sensível por extensão (`*.pem`, `*.tfstate`, `*.tfvars`) nunca está na allowlist. Se fosse pra ter só uma camada, seria essa.

   O default correto tem um custo: **um arquivo que o projeto precisa e que ninguém lembrou de permitir simplesmente não entra, sem aviso.** Três casos apareceram assim — `requirements-dev.txt` (que `!requirements.txt` não casa), o workflow do CI (`.yml` não está na allowlist por extensão) e as fixtures de teste da ADR-0022 (`.json` idem). Todos foram adicionados explicitamente; a exceção das fixtures é escopada a diretórios literalmente chamados `fixtures/`, de modo que `scenarios.json`, `quality_plan.json` e o `meta.json` de runtime continuam fora. Ampliar a allowlist é decisão de ADR, nunca `git add -f`.

   **Emenda: a exceção das fixtures vai além do `.json`.** A camada de aceite manual da ADR-0022 captura as saídas cruas do `/usr/bin/time`, do `perf`, do `pidstat` e do FFmpeg, e são elas que passam a alimentar os testes dos parsers do `analysis/` — a mesma justificativa pela qual o `meta.json` real é âncora, um degrau abaixo: parser testado contra texto que o próprio autor digitou valida o Python contra o Python. `.txt` e `.log` entram ao lado do `.json`, e continua tudo escopado a diretórios literalmente chamados `fixtures/`.

   A exceção segue **enumerada por extensão**, e não escrita como `!**/fixtures/*`. Não é conservadorismo: a captura da camada de aceite tem o `output.mkv` ao lado das saídas de texto, e o curinga poria um vídeo no histórico do repositório que as instâncias clonam. O custo conhecido do default correto continua valendo — uma fixture de tipo novo não entra sem que alguém a permita —, e é o lado certo em que errar.
2. **`gitleaks` no `pre-commit`.** Varre o conteúdo staged e bloqueia segredo embutido dentro de um arquivo *permitido* (ex.: chave colada num `.sh` durante debug) — o vetor que a camada 1 não pega. Incluída porque o `pre-commit` já existe pros linters, então o custo marginal é ≈ zero.

Por design quase nada sensível nasce dentro do repo: chave SSH via SSM → `~/.ssh` (ADR-0016), state remoto (ADR-0020), sem credencial estática (instance profiles). As camadas são rede de segurança, não a defesa primária. Bônus opcional server-side: push protection do GitHub (não burlável por `--no-verify`).

## Considered Options

- **`.gitignore` como blocklist** — rejeitado: depende de lembrar de listar cada segredo; o default errado ("commita tudo exceto o bloqueado") é inaceitável num repo clonado em máquina pública. A allowlist inverte o default pra "nada entra sem permissão".
- **Por linguagem** (`python/`, `bash/`, `terraform/`) — rejeitado: esconde quem roda o quê; uma instância de encode teria que vasculhar `python/` e `bash/` pra montar seu papel. Por papel, o papel é o diretório.
- **Flat (tudo na raiz)** — rejeitado: 4 papéis + spec + infra + análise na raiz vira ruído; perde a fronteira de propriedade.
- **`pyproject.toml` com extras** — rejeitado: sem pacote a distribuir e sem código compartilhado, adicionaria build backend e `pip install -e .[extra]` no bootstrap sem ganho. Config de linter mora em `ruff.toml` no topo.
- **Segredos/artefatos versionados** — rejeitado por construção: `scenarios.json`, `quality_plan.json`, o `meta.json` de runtime e `*.pem` são runtime/segredo e ficam fora pela allowlist (seção acima); o state do Terraform é remoto (ADR-0020). As únicas exceções são as fixtures de contrato sob `fixtures/` (ADR-0022) — o `meta.json` capturado e as saídas cruas das ferramentas de medição —, artefatos de teste commitados de propósito, não dado de campanha.

## Consequences

- Bootstrap do orquestrador vira `git clone` → `venv` → `pip install -r orchestrator/requirements.txt` (nunca o `-dev`) — coerente com a ADR-0013.
- O nome do gerador do `scenarios.json`, dos `.tf` e dos scripts de bootstrap fica em aberto até o desenvolvimento; só os nomes já fixados por ADR (`orchestrator.py`, `resume.py`, `quality_triage.py`, `consolidate.py`, `run_all.sh`, `run_scenario.sh`, `run_quality.sh`, `Dockerfile`) e pela spec (`experiment.toml`) estão cravados.
