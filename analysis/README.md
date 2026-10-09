# analysis/

Python que roda no Mac do pesquisador (ADR-0017): o lado que **lê** o que as
Instâncias produziram — os contratos do `meta.json` e do `judge.json` e a tabela
analítica que o artigo reporta.

O papel repete o seam do `orchestrator/`. **Núcleo puro**, que recebe dado já
lido e devolve estrutura: `run_meta.py` (o modelo do `meta.json`),
`judgement.py` (o modelo do `judge.json`), `run_artifacts.py` (os parsers do
`time.json`, do `perf.json`, da `pidstat.txt` e do `ffmpeg.log`), `run_table.py`
(os derivados e o construtor da tabela), `vmaf_log.py` (o parser do log do
`libvmaf`) e `quality_table.py` (as métricas e as duas tabelas do Pass).
**Casca fina**, que abre arquivo e traduz erro em código de saída:
`validate_meta.py`, `validate_judge.py`, `consolidate.py` e `quality.py`. O
`conftest.py` deste nível é o que torna o núcleo importável pelos testes sem
`pyproject.toml` nem `sys.path` manipulado.

Os dois modelos e as duas CLIs de validação saem do `json_contract.py`: o modo
estrito, as anotações de campo, o `offending_fields` e a casca de `argparse` são
os mesmos. A duplicação que a ADR-0022 licencia é entre **papéis** — o modelo
daqui e o checador stdlib do `orchestrator/`, que rodam em venvs separados e
verificam o contrato de forma independente. Dentro deste papel, duas cópias do
mesmo `argparse` não verificariam nada duas vezes.

O runtime tem `pydantic`, `pyarrow` e `pandas`, e nada mais: o venv é próprio do papel, e é
isso que faz o job de CI dele ter valor — uma dependência não declarada quebra no
ambiente limpo em vez de passar porque a máquina do pesquisador tinha o pacote.

    python -m venv .venv-analysis
    .venv-analysis/bin/pip install -r analysis/requirements-dev.txt
    .venv-analysis/bin/python -m pytest analysis/
    .venv-analysis/bin/python analysis/validate_meta.py runs/<run_id>/meta.json
    .venv-analysis/bin/python analysis/validate_judge.py \
        quality/results/<run_id>/judge.json
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

## O `judge.json`

O registro de um output julgado, e o segundo arquivo do repositório escrito por
um script de shell e lido em Python. Tem as **mesmas três verificações** do
`meta.json`, pelo mesmo motivo e com a mesma divisão (D14 da Spec 5):

- o modelo `pydantic` estrito de `judgement.py`, validando os bytes crus;
- o checador em stdlib pura do `orchestrator/` (`judgement_check.py`), que cobre
  os cinco campos sobre os quais o `clean` decide — `schema_version`, `run_id`,
  `sha256`, `exit_code` e `finished_at`;
- o `validate_judge.py`, que é por onde o `smoke/` valida — como caixa-preta — o
  `judge.json` que o `run_quality.sh` acabou de escrever.

O arquivo é a **entrada do plano projetada verbatim** (a forma que o triage
escreve em `quality/plan.json`) mais o que o Juiz cunha: `started_at` e
`finished_at` com offset, `exit_code`, o `commit`, o `instance_id` e o
`instance_type` do Juiz — que é decisão operacional e por isso precisa ficar
registrada — e as `versions` da imagem. A projeção verbatim é o que deixa o
leitor juntar plano e resultado pelo `run_id` sem reconstruir campo nenhum.

O `sha256` é validado como digest inteiro e minúsculo nos dois leitores, e não
como string não-vazia: é a chave pela qual a retenção acha as cópias
bit-idênticas a apagar (D22), e um digest truncado casa com nenhuma delas.

O `judge.schema.json` commitado é gerado do modelo e vai para o anexo do artigo,
com o mesmo teste de sincronia do `meta.schema.json`. Para regenerá-lo:

    .venv-analysis/bin/python analysis/validate_judge.py --emit-schema \
        > analysis/judge.schema.json

A âncora real — um `judge.json` que o Juiz escreveu — chega com o Pass do piloto
(D24). Até lá o `test_judge_agreement.py` de cada papel carrega a sua, escrita à
mão, como foi com o `meta.json` antes do piloto.

## A tabela consolidada

    aws s3 sync s3://<bucket>/runs/ runs/
    .venv-analysis/bin/python analysis/consolidate.py --runs runs/ --out runs.parquet

São dois passos, e não um: o `consolidate.py` recebe um diretório **local**. A
ADR-0014 descreve o `sync` antes da consolidação, e separá-los é o que a mantém
testável sem rede e capaz de consolidar a árvore que o `smoke/` acabou de
produzir.

Uma linha por Execução, com o Cenário, os metadados do run, os agregados de
`time` e `perf`, os parseados do FFmpeg e os três derivados — `ipc`,
`branch_mispredict_rate` e `cpu_pct_avg` (ADR-0006/0007). Não há coluna de cache
miss rate: nenhum par de cache conta o mesmo nível nas três arquiteturas, e a
ADR-0006 o tirou das métricas-chave. As séries do `pidstat` ficam **fora**: só o agregado entra, e a série
continua no raw dir, consultada sob demanda, para que a tabela não ganhe centenas
de milhares de linhas.

Cada evento de PMU tem **duas** colunas: `perf_{evento}` com o valor e
`perf_{evento}_pcnt_running` com a fração do tempo em que aquele contador esteve
rodando — o nome do evento em minúsculas e com `-` virando `_`, de modo que
`branch-misses` é `perf_branch_misses` (ADR-0007). Abaixo de 100 o valor é estimativa
extrapolada, não contagem, e é o regime esperado onde a PMU tem menos contadores
que eventos — os pares da ADR-0006 mantêm
a razão correta ali, mas sem a coluna uma estimativa de uma arquitetura e uma
contagem de outra entrariam na mesma comparação indistinguíveis. É o que permite
ao artigo dizer qual dos dois cada número é.

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

O caso feliz de cada parser roda contra a **captura real** em `tests/fixtures/`,
que a camada de aceite do `smoke/` trouxe de dentro da imagem; a factory do
`conftest.py` fica com as variações que a ferramenta não produz sob encomenda. A
outra âncora é o `smoke/` consolidando a árvore que o `run_all.sh` acabou de
escrever (ADR-0022).

## As tabelas do Pass de qualidade

    aws s3 sync s3://<bucket>/quality/ quality/
    .venv-analysis/bin/python analysis/quality.py --results quality/results \
        --config config/pilot.toml \
        --outputs quality_outputs.parquet --groups quality_groups.parquet

O mesmo desenho do `consolidate.py`: o `sync` é do pesquisador, o `quality.py`
recebe o diretório local. O `--config` é a definição do lançamento julgado — dela
saem os frames de cada vídeo, as arquiteturas declaradas e os dois limiares de
`[quality]`. Sai 0 com as duas tabelas escritas, 1 para um `judge.json` inválido
ou ausente — nomeando o arquivo, sem escrever nada — e 2 para diretório,
definição ou Parquet que não se lê ou não se escreve.

Quem entra:

- todo `{run_id}/` sob `--results`, menos `preflight/`: a evidência que o
  `preflight --judge` deixa ali não é julgamento, e nada a apaga (ADR-0016);
- todo `judge.json` é validado pelo modelo estrito, e um inválido **derruba** a
  leitura nomeando o arquivo;
- mais de um julgamento com o mesmo `run_id` é resolvido por "último
  `finished_at` vence", comparando instantes;
- julgamentos com `exit_code != 0` **permanecem**, com métricas nulas.

O `vmaf.json` é parseado frame a frame, e um log truncado, ou um frame sem
`vmaf` ou sem `float_ssim`, é recusado com o motivo: pular o frame encolheria a
série, e a contagem de frames é a guarda. Como no `consolidate.py`, o log
recusado não derruba a leitura — vira métricas nulas e é **relatado** no stderr,
nomeando o run, mas só quando o julgamento terminou bem.

**A guarda de frames (D20).** A contagem de frames do log é conferida contra o
`frames` do vídeo na **definição**, não contra o do `judge.json`, que é a cópia
do plano. Diferente, o output sai com `frames_match = false` e é relatado com as
duas contagens: uma referência desalinhada ou um output truncado produziria um
VMAF plausível e errado.

### Tabela de outputs

Uma linha por `run_id` julgado, ordenada por `scenario_id`.

| coluna | o quê |
|---|---|
| `scenario_id`, `codec`, `encoder`, `input_res`, `output_res`, `video`, `instance` | o Cenário, pelo representante |
| `run_id` | o representante do bitstream |
| `sha256` | o bitstream, e a chave da junção com a tabela principal |
| `cell_divergent` | a célula do representante teve mais de um bitstream (D3) |
| `exit_code` | do julgamento |
| `instance_id`, `instance_type`, `versions` | o Juiz e a imagem |
| `vmaf_mean`, `vmaf_std`, `vmaf_p5` | média, desvio populacional e percentil 5 (interpolação linear) do VMAF por frame |
| `ssim_mean` | média do `float_ssim` por frame |
| `frames` | frames comparados, pelo log |
| `frames_match` | `frames` igual ao do vídeo na definição |

### Tabela de grupos

Uma linha por Cenário (`codec × input_res × output_res × vídeo`), ordenada pelo
nome.

| coluna | o quê |
|---|---|
| `scenario` | o nome do Cenário: a `scenario_id` sem Instância e Replicação |
| `codec`, `encoder`, `input_res`, `output_res`, `video` | o Cenário |
| `bitstreams` | quantos bitstreams distintos foram julgados |
| `sha256_{id}` | uma coluna por `[[instance]]` declarado: a lista ordenada dos hashes que aquela arquitetura produziu — um só, salvo célula divergente; nula se nenhum julgamento a cobre |
| `vmaf_delta`, `ssim_delta` | máximo menos mínimo das médias entre os bitstreams; nulas sem `judged_ok` |
| `judged_ok` | todo bitstream com `exit_code == 0` e `frames_match`, e toda arquitetura coberta |
| `equivalent` | as duas distâncias dentro de `vmaf_delta_max` e `ssim_delta_max`, inclusive; **nulo**, nunca verdadeiro, sem `judged_ok` |

A cobertura por arquitetura está em `judged_ok` porque um resultado que nunca
chegou ao bucket não se anuncia: o grupo teria um bitstream a menos e as
distâncias medidas sobre o que sobrou. A coluna `sha256_{id}` vazia é o que o
denuncia. O limite dessa guarda é a célula divergente (D3): se só um dos dois
bitstreams de uma arquitetura chegar, ela continua coberta pelo outro, e o que
denuncia a falta é `bitstreams` menor que o do relatório do triage — o leitor
não lê o `quality/plan.json`.

Nenhuma das duas tabelas carrega `shared_by`. O VMAF de um bitstream é o VMAF de
toda Execução que o produziu (ADR-0025), e a atribuição é a junção com a tabela
principal:

    runs = pd.read_parquet("runs.parquet")
    outputs = pd.read_parquet("quality_outputs.parquet")
    runs.merge(outputs[["sha256", "vmaf_mean", "ssim_mean"]],
               left_on="output_sha256", right_on="sha256", how="left")

O determinismo é o da tabela consolidada: de conteúdo e ordem, não de bytes.

## O gate, e a projeção da campanha

Dois scripts sobre o Parquet, e nenhum deles é parte da consolidação: eles
**decidem** se um lançamento passou, e é a ADR-0022 que lista o que decidem.

    .venv-analysis/bin/python analysis/gate.py --parquet runs.parquet \
        --config config/pilot.toml --prices analysis/prices.toml --covers piloto \
        --quality quality_groups.parquet

    aws s3 ls s3://<bucket>/runs/ --recursive | grep output.mkv > outputs.txt
    .venv-analysis/bin/python analysis/extrapolate.py --parquet runs.parquet \
        --config config/experiment.toml --outputs outputs.txt

O `gate.py` cobre os itens que o Parquet sozinho responde — os quatro primeiros
da checklist, mais três leituras que a checklist não pede e o piloto mostrou
valerem: o `pcnt-running`, o coeficiente de variação e a concordância de
bitstream. O `--config` é a definição do lançamento que o Parquet mediu: os
frames de cada Master, os eventos e os pares de cada métrica saem dela, não de
uma cópia no script.

O item 5 depende do Juiz, e entra pelo `--quality`, opcional, com a tabela de
grupos do `quality.py`:

    [ok] 5  triage e Juiz: 6 grupos, 13 bitstreams julgados, X/6 equivalentes

O item passa se todo Cenário do `--parquet` tem um grupo, se cada grupo julgou
tantos bitstreams quantos `output_sha256` distintos o Parquet mediu, e se todo
grupo tem `judged_ok`; o que falta é nomeado na linha. Sem a conferência contra
o Parquet, um Cenário cujos outputs ficaram todos depois do teto do
`--total-timeout` sumiria da tabela, e o item passaria com um grupo a menos. A equivalência é **reportada, não exigida** (ADR-0005): os grupos
julgados fora dos limiares saem listados pelo nome, com as duas distâncias, sob
`grupos não equivalentes` — é de lá que sai a subseção de casos divergentes do
artigo. Sem `--quality`, o item sai `[em aberto]` e não entra no código de
saída; nada mais muda.

**Por que o `pcnt-running` é reportado em duas famílias.** Os quatro contadores
de hardware multiplexam onde a PMU tem menos registradores que eventos; os quatro
de software são mantidos pelo kernel e voltam sempre 100%. Uma média sobre os
oito devolveu 75% no Graviton3 na primeira leitura do piloto, número que não
corresponde a nada — o real é 50% nos de hardware.

**O CV intra-célula** é o que sustenta as 5 Replicações da ADR-0003, que as
fixou supondo "CV típico < 5%" sem medir. O piloto mediu **0,17%**, e com n=5
isso detecta diferenças acima de ~0,35% — folga de duas ordens de grandeza sobre
as diferenças arquiteturais observadas.

**A concordância de bitstream** é a prévia barata do Pass de qualidade: um hash
por Cenário entre as três arquiteturas dispensaria VMAF naquele grupo (ADR-0005).
No piloto nenhum Cenário deu os três iguais — a divergência segue a ISA, com
Intel e AMD concordando entre si e o ARM à parte. As Replicações dentro de uma
mesma instância, essas sim, são bit-idênticas.

O `extrapolate.py` responde os itens 7 e 8: o piloto mede um par barato e a
campanha tem nove, então cada par é projetado **por pixels de saída**, como a
ADR-0022 manda, sobre os tempos e tamanhos medidos. A listagem do bucket entra
pelo tamanho de cada `output.mkv`, e nenhum deles é baixado.

Os dois orçamentos são a linha que se confere **antes** de lançar, e nenhum é o
corte que acontece durante:

- **100 h por arquitetura**, contra o teto de 120 h que o `run_all.sh` aplica
  (ADR-0012). A diferença é margem: a projeção por pixels é crua, e um alarme
  colado no teto deixaria passar uma projeção que estoura de verdade.
- **147 GB**, que é o livre do volume de 200 GiB medido nas três Instâncias do
  piloto depois dos Masters (42 GiB), do Docker e do SO.

E uma terceira linha, que não é orçamento e sim o teto da camada 1: a Execução
mais longa do piloto, reescalada para o par de topo, tem de caber nas 4 h do
`--run-timeout`. Estourar ali não para a campanha — o `run_scenario.sh` mata o
encode e o run sobe com `exit_code` 143, um por Replicação daquele bloco.

Sobre o piloto, a projeção deu 92 h e 79,5 GB para a `c7i`, a mais lenta — e foi
ela que mostrou que as ~46 h estimadas na ADR-0012 eram metade do real, com o par
`2160p → 2160p` respondendo sozinho por metade da campanha.

## Os preços

`prices.toml` guarda uma seção `[[quote]]` por consulta à Price List API, e a
análise escolhe pelo campo `covers`. A do piloto não é sobrescrita pela da
campanha: o relatório de cada um cita um custo, e esse número tem de continuar
reproduzível depois de a AWS mexer na tabela. O raciocínio inteiro — por que o
preço não é coluna do `meta.json`, e a diferença entre custo por Cenário e custo
operacional — está na ADR-0024.
