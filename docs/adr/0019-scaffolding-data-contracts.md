# Contratos de dados do scaffolding — config → scenarios.json → meta.json

A pipeline tem uma espinha de contratos de dados que atravessa fronteiras de linguagem (Python escreve / bash lê, e vice-versa) **sem módulo de código compartilhável** entre as pontas. Este ADR fixa essa espinha porque é o ponto de maior risco do scaffolding: se a chave de cenário derivar entre quem escreve e quem lê, `resume.py` e `consolidate.py` falham em silêncio.

## Fonte de verdade: `config/experiment.toml`

A definição canônica do experimento é declarativa, em `config/experiment.toml` (stdlib `tomllib`, zero dependência no orquestrador). Usa **array-of-tables** porque os parâmetros têm dependências (ADR-0002/0004): preset e CRF são *amarrados ao codec* (viajam dentro do registro de cada codec, não são eixos livres) e os pares input→output são um *conjunto recortado* ("nunca upscale" vira dado, não filtro). O gerador Python (no `orchestrator/`) faz o produto cartesiano sobre `codec-records × pair-records × vídeos × instâncias` e o **shuffle dos blocos com seed** (ADR-0003/0010), emitindo o `scenarios.json`.

## `scenarios.json`: aninhado, com run_id cunhado na instância

`scenarios.json` é **aninhado**: blocos de cenário em ordem já embaralhada, cada bloco contendo os 6 runs pré-formados (1 warm-up + 5 reps), com flag `warmup` explícito. A atomicidade do bloco e o descarte do warm-up viram *estrutura do dado*, não convenção posicional que o bash precise respeitar.

- **A `scenario_id` (chave legível) é formada uma vez pelo orquestrador (Python) e ecoada *verbatim* pelo bash** no `meta.json`. O bash nunca reconstrói a chave a partir dos campos — só copia o valor recebido. Como a `scenario_id` aparece nas duas pontas (gerador e `meta.json`), essa é a única forma de garantir que elas casem.
- **O `run_id` (UUID v4) é cunhado pela instância** (`uuidgen` no `run_scenario.sh`) no momento da execução, não pré-alocado pelo orquestrador. Pré-alocar faria uma retomada reusar o mesmo UUID e colidir com o diretório `runs/{run_id}/` anterior no S3 — matando justamente a propriedade "à prova de retomada" da ADR-0007.
- **`resume.py` e `consolidate.py` raciocinam sobre a `scenario_id` (chave lógica)**, não sobre o `run_id` (unicidade física). Completude de um cenário = `scenario_id` presente com `exit_code == 0`.

## `meta.json`: validado na leitura

O `meta.json` é escrito em bash na instância e lido em Python local (`consolidate.py`). Como não há módulo compartilhável, o contrato é fixado por um **schema versionado** e o `consolidate.py` **valida cada `meta.json` na leitura, falhando alto** se faltar campo ou o tipo divergir. A validação mora no Python local (não na instância — "instâncias são burras", ADR-0009). Falhar alto é coerente com a ADR-0007, que rejeitou CSV justamente por "colunas faltando passam em silêncio".

## Considered Options

- **Config como módulo Python** — rejeitado: a separação spec-vs-geração tem retorno alto de auditabilidade num TCC, e o custo de dependência do TOML é nulo (`tomllib`). As dependências de parâmetros são expressas como *registros* (array-of-tables), não gambiarra.
- **`scenarios.json` plano (lista de runs)** — rejeitado: a fronteira do bloco e o descarte do warm-up virariam implícitos por posição.
- **Orquestrador pré-cunha os `run_id`** — rejeitado: colide na retomada, matando a propriedade do UUID.
- **Bash reconstrói a `scenario_id`** — rejeitado: divergência de ordem/separador/normalização (`2160p` vs `4k`, `c7g` vs `c7g.xlarge`) gera chaves que não casam → falha silenciosa.
- **`meta.json` como convenção informal parseada defensivamente** — rejeitado: reintroduz o modo de falha silenciosa que a ADR-0007 rejeitou.

## Consequences

- A `scenario_id` legível — não o UUID — é o verdadeiro contrato de retomada/consolidação.
- `run_scenario.sh` recebe os params como **objeto JSON** (a entrada de run do `scenarios.json`) e os lê com `jq` (por isso `jq` está na imagem, ADR-0018); a `scenario_id` é copiada literal, sem remarshalling.
- Run falho = `exit_code != 0` no `meta.json` + upload do que tem; o laço segue e o `resume.py` o trata como não-completo.
- O schema do `meta.json` é versionado no repo (destino exato — `analysis/` vs `config/` — a definir no desenvolvimento).
