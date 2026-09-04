# analysis/

Python que roda no Mac do pesquisador (ADR-0017): o lado que **lê** o que as
Instâncias produziram — o contrato do `meta.json` e a tabela analítica que o
artigo reporta.

O papel repete o seam do `orchestrator/`. **Núcleo puro**, que recebe dado já
lido e devolve estrutura: `run_meta.py` (o modelo do `meta.json`),
`run_artifacts.py` (os parsers do `time.json`, do `perf.json`, da `pidstat.txt` e
do `ffmpeg.log`) e `run_table.py` (os derivados e o construtor da tabela).
**Casca fina**, que abre arquivo e traduz erro em código de saída:
`validate_meta.py` e `consolidate.py`. O `conftest.py` deste nível é o que torna
o núcleo importável pelos testes sem `pyproject.toml` nem `sys.path` manipulado.

O runtime tem `pydantic` e `pyarrow`, e nada mais: o venv é próprio do papel, e é
isso que faz o job de CI dele ter valor — uma dependência não declarada quebra no
ambiente limpo em vez de passar porque a máquina do pesquisador tinha o pacote.

    python -m venv .venv-analysis
    .venv-analysis/bin/pip install -r analysis/requirements-dev.txt
    .venv-analysis/bin/python -m pytest analysis/
    .venv-analysis/bin/python analysis/validate_meta.py runs/<run_id>/meta.json
    .venv-analysis/bin/python analysis/consolidate.py --runs runs/ --out runs.parquet

O nome do venv é outro que o do `orchestrator/` de propósito: os dois papéis
rodam em ambientes separados (ADR-0017), e um `.venv` só serviria a um deles.

## O `meta.json`

É o artefato mais perigoso do repositório: atravessa a fronteira de linguagem no
sentido mais frágil possível — bash montando JSON à mão, Python lendo — e não há
módulo compartilhável entre as pontas (ADR-0019). O que existe são **três
verificações independentes** do mesmo contrato, de propósito:

- o modelo `pydantic` estrito daqui, validando os bytes crus;
- o checador em stdlib pura do `orchestrator/` (`meta_check.py`), que cobre os
  cinco campos sobre os quais aquele papel decide;
- o `validate_meta.py`, que é por onde o `smoke/` valida — como caixa-preta — o
  `meta.json` que o bash acabou de escrever.

O `meta.schema.json` commitado é gerado do modelo e vai para o anexo do artigo;
um teste regenera e compara, porque um schema no anexo divergindo do modelo que
valida é falha silenciosa de documentação. Para regenerá-lo:

    .venv-analysis/bin/python analysis/validate_meta.py --emit-schema \
        > analysis/meta.schema.json

## A tabela consolidada

    aws s3 sync s3://<bucket>/runs/ runs/
    .venv-analysis/bin/python analysis/consolidate.py --runs runs/ --out runs.parquet

São dois passos, e não um: o `consolidate.py` recebe um diretório **local**. A
ADR-0014 descreve o `sync` antes da consolidação, e separá-los é o que a mantém
testável sem rede e capaz de consolidar a árvore que o `smoke/` acabou de
produzir.

Uma linha por Execução, com o Cenário, os metadados do run, os agregados de
`time` e `perf`, os parseados do FFmpeg e os quatro derivados — `ipc`,
`cache_miss_rate`, `branch_mispredict_rate` e `cpu_pct_avg` (ADR-0006/0007). As
séries do `pidstat` ficam **fora**: só o agregado entra, e a série continua no
raw dir, consultada sob demanda, para que a tabela não ganhe centenas de milhares
de linhas.

Quem entra:

- todo `meta.json` é validado na leitura, e um inválido **derruba** a
  consolidação nomeando o arquivo — nenhuma linha errada entra em silêncio;
- `warmup == true` sai **pelo campo**, nunca por parsing da `scenario_id`;
- `scenario_id` repetida é resolvida por "último `started_at` vence", comparando
  instantes: a comparação de strings com offsets diferentes ordena ao contrário
  (ADR-0019);
- runs com `exit_code != 0` **permanecem**, com `exit_code` como coluna — removê-los
  faria o Parquet mentir por omissão —, e a consolidação relata quantos são.

Denominador zero ou ausente produz **nulo explícito** naquela célula: nem um
`NaN` que se mistura aos ausentes legítimos, nem um `ZeroDivisionError` que
derruba a consolidação por causa de um run defeituoso. Pela mesma razão, o
`meta.json` é o **único** artefato que derruba: sem ele não há linha, enquanto um
`perf.json` ilegível custa quatro células. Ele vira nulo e a consolidação o
**relata**, nomeando o run e o arquivo — mas só quando a Execução terminou bem,
porque num `exit_code` não-zero o artefato torto é o estado esperado. Um parser
que envelheceu aparece como uma pilha de artefatos relatados, não como uma coluna
vazia e calada.

O determinismo prometido é de **conteúdo e ordem, não de bytes**: Parquet não é
byte-reproduzível entre versões do `pyarrow`, que gravam metadado próprio. A
mesma árvore produz as mesmas linhas, na mesma ordem — por `scenario_id`, chave
única depois do dedup —, com as mesmas colunas e os mesmos tipos.

Os parsers são testados contra a factory do `conftest.py`. A âncora real é o
`smoke/`, que consolida a árvore que o `run_all.sh` acabou de escrever; a captura
crua das ferramentas de verdade chega com a camada de aceite manual (ADR-0022).
