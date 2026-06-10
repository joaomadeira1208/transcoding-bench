# Versionamento e distribuição do código da campanha

Define como o código chega às instâncias e como ele pode (ou não) mudar durante a campanha de ~46h. Três decisões encadeadas:

## Repo público

O repositório é **público no GitHub**. A postura de segredos do ADR-0017 já foi desenhada como se ele fosse público ("o repo é clonado em instâncias com IP público; nada sensível pode entrar no histórico") — allowlist no `.gitignore`, `gitleaks` no pre-commit, chave via SSM, state remoto, instance profiles. Manter privado desperdiçaria esse investimento e exigiria credencial de leitura nas três instâncias. Público, o `git clone` anônimo do bootstrap (ADR-0013) funciona sem mecânica nova, e o repo vira o artefato citável do TCC — o claim de reprodutibilidade do ADR-0008 ("qualquer pessoa reconstrói o ambiente") só é verdadeiro se a pessoa alcança o Dockerfile.

## Instâncias clonam o SHA do checkout do Orquestrador

Ao lançar qualquer instância (campanha ou retomada), o orquestrador resolve `git rev-parse HEAD` do **seu próprio clone** e passa o SHA como argumento de bootstrap; a instância faz clone + `git checkout <sha>`.

- **Fonte única, zero config:** o pesquisador controla a versão fazendo checkout na instância do Orquestrador antes de disparar. Não há campo de ref pra esquecer de atualizar.
- **Retomada herda a propriedade:** a instância recriada pelo `resume.py` roda o que o Orquestrador roda, não "o que o master estiver naquele minuto".
- **Código não-versionado não vaza pro experimento:** editar um script no working tree do Orquestrador sem commitar não afeta as instâncias — elas clonam o SHA, não o working tree. O caminho é sempre commit → push → pull no Orquestrador → relançar.
- O commit de largada da campanha ganha uma tag (ex.: `campaign-1`) pra citação no artigo; o SHA é o mecanismo, a tag é a etiqueta humana. O `meta.json` de cada run registra o SHA (ADR-0018).

## Política de hotfix mid-campanha — duas classes

O ADR-0018 escolheu scripts bind-mounted justamente pra permitir correção sem rebuild durante a campanha — a porta do hotfix é deliberada. A regra que a disciplina (estende o gate humano da ADR-0012): **fix só entra via commit + relançamento, e o diff é classificado antes de relançar.**

- **Classe 1 — toca o caminho de medição** (seção de encode/instrumentação do `run_scenario.sh`, `Dockerfile`, `experiment.toml`): os cenários completados afetados estão contaminados — são **invalidados** e voltam pra fatia de retomada. Mudança no `Dockerfile` é, na prática, outra campanha (a imagem é o ambiente de medição, ADR-0018).
- **Classe 2 — toca só encanamento** (upload, marcadores, parsing, logging): runs completados não foram afetados pelo bug — retomada normal. Os SHAs por run no `meta.json`/Parquet documentam o drift, e o artigo o reporta se ocorrer.

## Considered Options

- **Repo privado + deploy key read-only via SSM** — rejeitado: espelharia o padrão da chave SSH (ADR-0016), mas adiciona credencial, parâmetro SSM e leitura nas três roles pra proteger um repo cuja postura já é "publicável por construção". Reconsiderar só se houvesse motivo de sigilo até a defesa — não há.
- **Repo privado + fine-grained PAT** — rejeitado: token com expiração no meio de uma janela de 46h é falha silenciosa esperando pra acontecer.
- **Ref fixa em config (ex.: campo no `experiment.toml`)** — rejeitado por auto-referência: o commit pinado precisa conter o arquivo que o nomeia; toda mudança de ref exige um commit que já sabe o próprio nome.
- **Clonar HEAD do master + disciplina de não pushar** — rejeitado: convenção onde dá pra ter estrutura; o mesmo instinto que rejeitou "bash reconstrói a scenario_id" (ADR-0019).
- **SHA imutável pra campanha inteira (proibir hotfix; qualquer fix = campanha nova)** — rejeitado pelo custo assimétrico: um bug de encanamento na hora 40 descartaria ~2 dias e ~$70 de dado válido — runs completados não foram afetados por um bug que só impede execuções futuras. A classe 1 já cobre o caso em que o re-run total é de fato necessário. Resultado provável continua sendo zero hotfixes e a campanha num SHA só; a política existe pro dia ruim.

## Consequences

- O bootstrap de toda instância recebe o SHA como argumento (user-data/SSH) e faz clone + checkout — emenda no fluxo da ADR-0013.
- Tornar o repo público é efetivamente irreversível (histórico clonado/indexado); a allowlist + gitleaks deixam de ser rede de segurança e viram a defesa de fato — já era a premissa da ADR-0017.
- A validação de fumaça (ADR-0016) deve incluir o caminho do clone: instância descartável clona e dá checkout no SHA passado.
