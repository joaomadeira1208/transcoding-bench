# Estratégia de testes

A ADR-0017 fixou a *moldura*: testes co-localizados por papel (`orchestrator/tests/`, `analysis/tests/`), só os papéis Python, fronteira de testabilidade = "núcleo lógico determinístico sem side-effect externo", shell fora do TDD, `requirements-dev.txt` por papel, execução no Mac, CI mínimo com lint + pytest por papel em venvs separados. Este ADR fecha o que ficou em aberto: **o quê** ganha teste, **como** o `subprocess`/AWS CLI é isolado, de onde vêm as **fixtures**, se há **smoke test** integrado, e como as camadas **não-Python** são verificadas.

## Critério de inclusão: modo de falha silenciosa

Ganha teste unitário o que pode falhar **em silêncio** — bug que não estoura, e sim produz número errado ou perde dado. O critério não é cobertura; é o mesmo instinto que a ADR-0007 usou pra rejeitar CSV ("colunas faltando passam em silêncio") e que justifica a ADR-0019 inteira ("se a chave derivar, `resume.py` e `consolidate.py` falham em silêncio"). Um `FileNotFoundError` no bootstrap se revela em segundos; um `resume.py` que julga um bloco completo com 4 replicações custa uma linha errada no artigo.

Inventário sob esse critério:

| Alvo | Papel | Falha silenciosa que o teste barra |
|---|---|---|
| Cardinalidade e unicidade do canônico | `orchestrator/` | Produto cartesiano incompleto ou com duplicata → cenário que nunca roda, ou replicação contada duas vezes |
| Shuffle dos blocos com seed | `orchestrator/` | Ordem divergir entre as 3 arquiteturas → o cancelamento de efeitos temporais da ADR-0010 deixa de valer sem sinal |
| Fatiamento por arquitetura | `orchestrator/` | Cenário sumir ou duplicar numa fatia → célula vazia na matriz de comparação |
| Formação da `scenario_id` | `orchestrator/` | Chave não casa com o `meta.json` → ADR-0019 |
| Completude por bloco (`resume.py`) | `orchestrator/` | Bloco incompleto tratado como completo (n errado no t-Student) ou completo re-executado |
| Dedup "último `started_at` vence" | `orchestrator/`, `analysis/` | Run de bloco abortado entrando na média |
| Filtro `warmup == false` | `orchestrator/`, `analysis/` | Warm-up contado como Replicação → média enviesada pra cima (ADR-0003) |
| Agrupamento do triage (270 grupos × 3) | `orchestrator/` | Grupo mal formado → comparação de hash cross-arch inválida |
| Modelo pydantic (estrito) + `schema_version` | `analysis/` | `meta.json` de forma antiga passando batido, ou tipo divergente sendo coagido em silêncio (ADR-0019) |
| Derivados do `consolidate.py` (IPC, cache miss rate, branch mispredict rate) | `analysis/` | Divisão virando `NaN`/`ZeroDivisionError` calado |
| Parsing da saída da AWS CLI | `orchestrator/` | Listagem de `runs/` mal parseada → `resume.py` decide errado |
| Argv do FFmpeg (via smoke, ver abaixo) | `smoke/` | Parâmetro de encode perdido ou deformado na cadeia toml → `jq` → argv → vídeo válido, experimento inválido |

### Cardinalidade do canônico

As invariantes de fatiamento (adiante) são **relativas** ao `scenarios.json` canônico e portanto vacuosas quanto à correção dele: um canônico a que falte uma combinação, ou que tenha uma duplicata, satisfaz "união das fatias == canônico" perfeitamente. O teste do canônico é separado, roda o gerador sobre o `config/experiment.toml` **real**, e tem três camadas — nenhuma sozinha basta.

1. **Contagem** — 162 blocos, 972 runs, 810 replicações reportadas (`warmup == false`), zero `scenario_id` duplicada (ADR-0003/0014). Pega omissão e duplicata.
2. **Conjunto exato dos 9 pares `input_res → output_res`** (ADR-0004). Contagem é invariante sob **substituição**: trocar um par por outro mantém 162/972/810 intactos e passa despercebido. É transcrição da spec no teste, deliberadamente — o mesmo instinto que congela a ordem do shuffle, porque a matriz do experimento é o que o artigo reporta e não pode derivar em silêncio.
3. **Regra estrutural `output_res <= input_res`** — o "nunca upscale" da ADR-0019, asserido como propriedade e não como lista. É a camada que sobrevive a uma mudança legítima do conjunto de pares: se alguém ampliar a matriz no futuro, a camada 2 é atualizada de propósito, mas um upscale que entre por qualquer rota continua barrado.

## Seam do `subprocess`

Um módulo único (`orchestrator/external.py` ou nome equivalente — nomes ficam abertos até o desenvolvimento, ADR-0017) é a **única casa de `subprocess.run`**, com uma função por comando.

O escopo é **todo processo externo**, não só AWS: o orquestrador invoca a AWS CLI (`s3_list_prefix`, `s3_get_object`, `run_instances`, `terminate_instances`, `ssm_get_parameter`), **SSH** (`ssh_exec`, ADR-0010) e **git** (`git_rev_parse`, porque a ADR-0021 faz o orquestrador resolver o SHA do próprio clone pra passar às instâncias). Chamá-lo de `aws.py` seria mentir sobre dois dos três. **Terraform não entra**: a ADR-0009 e o layout da ADR-0017 o mantêm como ferramenta do Mac; o orquestrador nunca o invoca.

O propósito do módulo **não** é tornar o orquestrador testável ponta a ponta: é dar um lugar pra onde empurrar o I/O, de modo que o núcleo puro exista de fato. A regra que ele impõe é **"função pura recebe dado já buscado"** — uma lista de dicts de `meta.json`, nunca um prefixo S3. Se a maioria dos testes não precisar de fake do seam, ele cumpriu seu papel.

O módulo é partido em duas metades de natureza oposta:

- **Construção do argv + `subprocess.run`** — sem teste. Asserir `["aws", "s3api", "list-objects-v2", ...]` é transcrever a implementação e quebra em refatoração inofensiva.
- **Parsing da saída** — testado, e mora no núcleo puro. É transformação determinística com falha silenciosa concreta: listar `runs/` via `aws s3 ls --recursive` devolve texto delimitado por espaço misturado com linhas `PRE`, e o parser artesanal erra quieto. Usa-se `s3api list-objects-v2 --output json`, e o teste come um payload real capturado. A AWS CLI v2 agrega páginas sozinha, então truncamento não é o modo de falha default — passa a ser se alguém introduzir `--page-size`/`--max-items`, e o teste é onde essa premissa fica escrita.

Rejeitou-se `mock.patch("subprocess.run")` sem módulo: acopla o teste ao argv, transformando-o em asserção sobre string de CLI.

## Fixtures: factory por dentro, âncora real por fora

Híbrido, porque as duas formas têm trabalhos diferentes:

- **Factory em código** (`make_meta(**overrides)` no `conftest.py` do papel) pro grosso dos testes — gerar 40 variações de completude/dedup sem 40 arquivos.
- **Um `meta.json` real, capturado do smoke AWS, commitado em `tests/fixtures/`**, como âncora do contrato do modelo pydantic.

A factory tem um vício fatal justamente no `meta.json`: é escrita em Python, pelo mesmo raciocínio que escreveu o modelo pydantic, então valida o Python contra o Python. O `meta.json` é contrato **cross-language** — bash escreve, Python lê (ADR-0019) — e a única coisa que pega drift é um arquivo que o bash de verdade produziu.

A factory é **duplicada** entre `orchestrator/` e `analysis/`: são venvs separados sem módulo compartilhável (ADR-0017), e um pacote de teste comum entre papéis é exatamente o que os dois `requirements-dev.txt` existem pra impedir. A política que acompanha: **fixture/factory compartilhada dentro de um papel, tudo bem; helper de asserção compartilhado, não.** A ADR-0019 escolheu de propósito duplicar a regra do dedup nos dois leitores em vez de compartilhar código; um `assert_dedup(...)` comum reintroduziria o acoplamento pela porta dos fundos e perderia a verificação independente que a duplicação comprava.

## Forma das asserções

**Invariantes escritas à mão** como default; **golden inline** só onde determinismo *é* o requisito; sem `hypothesis`.

- *Fatiamento* → invariante: união das 3 fatias == canônico, interseção vazia, ordem relativa preservada dentro de cada fatia. Golden aqui seria ruim: quebra em reordenação inofensiva e não diz o que quebrou.
- *Shuffle com seed* → o requisito **é** o congelamento (ADR-0003/0010 fazem as 3 instâncias dependerem da mesma ordem), e o que se quer pegar é `random.shuffle` mudando entre versões de Python ou alguém reordenando o produto cartesiano. A lista esperada de `scenario_id` vai **inline no arquivo de teste** (`.py`, sem atrito com a allowlist), sobre um `experiment.toml` mínimo — não um `scenarios.json` golden commitado.

`hypothesis` foi rejeitado: adiciona dependência e curva de aprendizado pra gerar entradas num espaço pequeno e enumerável à mão; as invariantes valiosas cabem em três linhas sem ele.

## Validação do `meta.json` nos três leitores

O `meta.json` tem **três** leitores programáticos, não um: `consolidate.py` (`analysis/`), `resume.py` e `quality_triage.py` (ambos `orchestrator/`, stdlib-only por ADR-0017 e portanto incapazes de importar o modelo pydantic de `analysis/`).

O orquestrador ganha uma **checagem mínima em stdlib**, testada, falhando alto. É a mesma solução de "regra duplicada, não código compartilhado" que a ADR-0019 já adota pro dedup. Ela valida **presença, tipo e valor** — presença sozinha não basta:

| Campo | Verificação |
|---|---|
| `schema_version` | presente e pertencente ao conjunto de versões que o orquestrador conhece |
| `scenario_id` | `str` não-vazia |
| `warmup` | **`bool` de verdade**, não string |
| `exit_code` | `int` |
| `started_at` | parseável por `datetime.fromisoformat` **e timezone-aware** |

Dois modos de falha concretos justificam cada metade. O primeiro é a ausência: o jeito natural de escrever o filtro é `m.get("warmup")`, e com o campo ausente isso vira `None` → falsy → **o warm-up entra na retomada como Replicação válida**, em silêncio, enviesando a média pra cima pela razão que a ADR-0003 documenta. O segundo é o tipo, e é o mais provável dos dois, porque quem escreve o `meta.json` é bash montando JSON à mão (ADR-0019): `"warmup": "false"` é uma **string truthy**, e produz o mesmo desastre passando por qualquer checagem de presença.

### `started_at`: comparar instantes, nunca strings

Exigir que `started_at` seja "parseável" não fecha o problema — só o empurra. A regra de dedup "último `started_at` vence" (ADR-0019) precisa de uma **ordem total correta**, e há duas formas de obtê-la: cravar um formato de wire canônico (ex. UTC com sufixo `Z`) e comparar strings, ou parsear e comparar instantes. **Escolhe-se a segunda.**

Comparar strings é frágil porque o formato nasce no bash, do lado que este ADR não controla por teste: `date -Is` emite offset numérico (`+00:00`), `date -u +...Z` emite sufixo `Z`, e os dois representam o mesmo instante ordenando diferente em comparação lexicográfica. Cravar o formato transferiria a responsabilidade pra uma convenção que só a revisão humana verifica. Parsear é enforceable no lado que valida.

Junto vem uma exigência que o "parseável" sozinho não dá: **timestamp naïve é rejeitado.** `datetime.fromisoformat` aceita alegremente uma string sem timezone, e comparar naïve com aware levanta `TypeError` — ou, pior, comparar dois naïves de fuso desconhecido devolve uma ordem inventada. Se o bash escrever hora local sem offset, a informação necessária pra normalizar **já se perdeu** e nenhuma esperteza no leitor a recupera. Exigir aware no momento da leitura é a única janela em que isso é detectável. (Ubuntu 24.04 em EC2 roda em UTC por default — ADR-0015 —, então na prática o campo vem com offset zero; a validação existe pra que essa premissa seja verificada em vez de assumida.)

A ADR-0019 foi escrita pra matar esse tipo de falha e o matou só na ponta que menos decide: `consolidate.py` roda depois, no Mac; `resume.py` decide o que custa re-executar.

Mover o pydantic pro orquestrador foi rejeitado: custa a invariante stdlib-only que a ADR-0017 desenhou de propósito e que o CI existe pra proteger.

### O modelo pydantic roda em modo estrito

O modelo de `analysis/` usa **`ConfigDict(strict=True)`**, e é validado por `model_validate_json` sobre os bytes crus — **não** por `json.load()` seguido de `model_validate()`.

O modo default do pydantic (*lax*) **coage**: `"false"` vira `False`, `"0"` vira `0`. Isso contradiz frontalmente o texto da ADR-0019, que promete "falhando alto se faltar campo ou o tipo divergir" — em lax, o tipo diverge e nada falha. E o efeito é pior do que a coerção isolada sugere: o leitor stdlib do orquestrador (estrito por construção) **rejeitaria** um `"warmup": "false"` que o `consolidate.py` aceitaria calado. Dois leitores do mesmo arquivo discordando sobre se ele é válido é um estado pior do que qualquer uma das duas políticas adotada sozinha, e some justamente a evidência de que o bash está escrevendo JSON errado.

A escolha do ponto de entrada não é detalhe de implementação. Em modo estrito o pydantic aplica regras **diferentes** para entrada JSON e entrada Python, porque JSON tem menos tipos: validando o JSON cru, `str` → `datetime` continua aceito (JSON não tem tipo de data), enquanto `str` → `bool` é rejeitado (JSON *tem* booleano nativo). É exatamente a combinação desejada — `started_at` como string ISO passa, `"warmup": "false"` estoura. Fazendo `json.load()` antes, a validação vira modo Python estrito e `started_at` passaria a ser rejeitado também, empurrando a implementação de volta pro lax pelo motivo errado.

Teste que ancora isso: um `meta.json` com `"warmup": "false"` tem que ser **rejeitado**, não coagido — e o mesmo caso roda contra o validador stdlib do orquestrador, garantindo que os dois leitores concordam sobre o que é um arquivo válido.

## Smoke em duas camadas

### Camada local — shell com shims, sem Docker, sem AWS

Um diretório `smoke/` no topo, executado por **pytest**. É coerente com o princípio da ADR-0017 (o diretório de topo é a fronteira de "quem roda aquilo") — quem roda o smoke é o Mac do pesquisador, mesmo dono de `infra/` e `analysis/`.

Roda `run_all.sh`/`run_scenario.sh` direto, com `ffmpeg`, `perf`, `pidstat` e `aws` **stubados por shims no `PATH`**. O shim `aws` traduz `s3 cp`/`s3api list-objects-v2` pra operações de filesystem, cobrindo bash e Python com um mecanismo só — sem localstack nem minio, porque não se está testando semântica do S3, e sim que o layout de prefixos da ADR-0011 casa entre quem escreve e quem lê.

O "master" de entrada é um **arquivo placeholder determinístico** escrito pelo próprio teste — alguns KB de bytes fixos, não um vídeo. Como o `ffmpeg` é shimado, nada jamais decodifica esse arquivo; gerá-lo de verdade (`-f lavfi -i testsrc2`) exigiria FFmpeg instalado pra produzir bytes que ninguém lê, e amarraria o job de CI à imagem do runner. Com o placeholder, o smoke tem **zero dependência de binário externo**.

### O argv do FFmpeg é asserção, não revisão humana

Um shim **loga o argv que recebeu**, e o pytest o verifica. Isso torna testável a única coisa que importa dentro do `run_scenario.sh`, e que de outra forma ficaria coberta só por revisão de diff.

A asserção não é transcrever a ADR-0002 no teste; é verificar a **cadeia inteira** `config/experiment.toml` → gerador → `scenarios.json` → `jq` no bash → argv: os valores variáveis (codec, preset, CRF, resolução de saída) têm que chegar ao FFmpeg **iguais aos do TOML que alimentou aquele cenário**. Uma chave errada no `jq` ou uma aspa mal posta derruba o CRF em silêncio e produz vídeo perfeitamente válido com parâmetro errado — falha silenciosa canônica. Os parâmetros fixos que a ADR-0002 crava e que não variam por cenário (GOP, `pix_fmt`, threading) são verificados por presença.

Isso vale sobretudo por causa da ADR-0021: a política de hotfix permite editar `run_scenario.sh` **durante** a campanha, e um fix classe 1 que acidentalmente derrube um flag contamina tudo dali pra frente. A asserção de argv no CI é a única guarda automática contra isso.

Rodar a imagem Docker real no Mac foi rejeitado por três razões. `perf stat` não funciona lá (o PMU não é exposto ao guest do Docker Desktop, e em Apple Silicon a imagem seria arm64 com `-march=native` do M-series, não do Graviton) — ou seja, precisamente o que o container compraria é o que o Mac não consegue rodar. Pior, obrigaria `run_scenario.sh` a ter um modo degradado; na campanha esse script tem que **falhar alto** quando `perf` falha, porque perder IPC é perder o achado principal (ADR-0006), e um flag que desliga instrumentação é uma alavanca que alguém puxa na hora 30. E com shims o ciclo fecha em segundos, virando o loop de desenvolvimento de verdade.

**Restrição de projeto: o smoke nunca importa; só invoca.** Chama `python orchestrator/<gerador>.py` e `bash encode/run_all.sh` como subprocessos e inspeciona artefatos. Importar código do orquestrador exigiria `sys.path` na marra ou `pip install -e`, que a ADR-0017 rejeitou junto com o `pyproject.toml`. Tratar o orquestrador como CLI caixa-preta é o correto pra um smoke de qualquer forma, e mantém `smoke/requirements-dev.txt` em duas linhas (`pytest`, `pydantic`).

Esta camada **entra no CI** como terceiro job: sem Docker, sem credencial AWS, sem FFmpeg de verdade, ela respeita literalmente as duas exclusões da ADR-0017. E ganha uma propriedade que nenhuma outra camada tem: com o `ffmpeg` shimado, **o `output.sha256` é controlável**, então o caminho de grupo hash-divergente do `quality_triage.py` — o ramo pelo qual a ADR-0005 existe e que na campanha real quase certamente nunca dispara — passa a ser exercitável sob demanda.

### Camada AWS — caminho completo, vídeo curto

Amplia a validação de fumaça da ADR-0016 (que já era pré-requisito operacional) e da ADR-0021 (que já mandou incluir o caminho do clone):

1. **`c7g.xlarge` — caminho completo**: `run-instances` → clone + `git checkout <sha>` → `docker build` → um `run_scenario.sh` com `perf` real → upload pro S3 → `quality_triage.py` → Juiz → terminate. É onde `-march=native` no Neoverse, os pacotes arm64 e o `perf` sob Graviton têm mais chance de quebrar de forma exótica.
2. **`c7i.xlarge` e `c7a.xlarge` — verificação de PMU**: um `perf stat` curto dentro do container confirmando que os **dez eventos da ADR-0006 retornam valor** e que nenhum volta `<not supported>`.

"Caminho completo" significa **o caminho inteiro sobre um clip de ~30 s, com 2 runs em vez de 6** — não um Cenário no sentido do CONTEXT.md. O vídeo inteiro existe pela razão metodológica da ADR-0003 (regime estacionário), irrelevante quando não se está medindo nada.

O passo 2 não é opcional e não é redundante com o passo 1. A ADR-0006 registra que os IDs de evento PMU diferem entre Neoverse-V1 e Sapphire Rapids/EPYC; se um evento não estiver disponível numa arquitetura, **`perf stat` não falha** — reporta o evento como não suportado e segue. O resultado seria uma coluna de IPC/cache/branch vazia para uma arquitetura inteira, descoberta no `consolidate.py` depois da campanha terminar. É falha silenciosa, arquitetura-específica, e nenhum teste unitário ou smoke local pode alcançá-la.

Sequenciamento: smoke local verde → smoke AWS → a fixture-âncora sai do `meta.json` do c7g → campanha. Até o smoke AWS existir, o teste do modelo pydantic roda só contra a factory.

## Verificação das camadas não-Python

- **Shell:** apenas `shellcheck` + `shfmt` (ADR-0017) mais o smoke local. Sem `bats` — mas não porque o shell fique sem verificação de comportamento: o smoke já stuba `ffmpeg`/`perf`/`pidstat`/`aws` e já loga o argv, então `bats` faria o mesmo trabalho com um runner a mais, e com asserções piores do que as do pytest sobre os mesmos artefatos. A rejeição é de ferramenta redundante, não de escopo.
- **Terraform:** `terraform init -backend=false && terraform validate` promovido de "extensão aceitável" (ADR-0017) a decidido. São dois comandos: `-backend=false` é flag do `init` (o `validate` precisa do diretório inicializado pra resolver providers), não do `validate`. Sem `tflint`/`checkov` — a superfície é uma dezena de arquivos.
- **Docker:** `hadolint` promovido igualmente. Build fica fora do CI (ADR-0013).

Ambos moram no **pre-commit**, não em job novo: a ADR-0017 já declarou o pre-commit a casa oficial dos linters pela justificativa de zero drift local/CI, e o job `pre-commit run --all-files` existente os pega de graça. O hook `terraform_validate` cuida do `init -backend=false` internamente, mas exige o binário `terraform` no `PATH` (o pre-commit não gerencia esse ambiente), então o workflow ganha um passo de setup.

## Disciplina de TDD

Estrito **apenas** nos itens do inventário acima. O resto — o módulo de seam, glue, parsing de argumentos de CLI, o laço do `orchestrator.py` — é escrito direto.

Escrever teste-primeiro pro módulo de seam é teatro: não há asserção a fazer antes da implementação que não seja o argv que ainda se vai escolher. Já completude por bloco, dedup e agrupamento do triage são o caso canônico onde TDD paga — a regra é sutil, os casos de borda (bloco parcial, `scenario_id` duplicada, `warmup` ausente) são mais fáceis de enunciar como teste do que de ler no código, e o custo de errar é dinheiro ou uma linha errada no artigo.

## Considered Options

- **Cobertura numérica como gate (ex.: 80%)** — rejeitado: premia teste de getter e não diz nada sobre onde o risco está. O critério do projeto é falha silenciosa, e ele já estava escrito na ADR-0007 e na ADR-0019 sem esse nome.
- **`mock.patch("subprocess.run")` sem módulo de seam** — rejeitado: o teste vira asserção sobre a lista de argv e quebra quando se move `--output json` de lugar, sem nada ter quebrado de fato.
- **Fixtures só sintéticas (factory), sem arquivo** — rejeitado pro `meta.json`: valida Python contra Python e não pega drift cross-language, que é o único risco real daquele contrato.
- **Fixtures só em arquivo** — rejeitado: 40 variações de completude/dedup viram 40 arquivos, com diff ilegível.
- **`hypothesis` (property-based)** — rejeitado: dependência e curva de aprendizado pra um espaço de entrada pequeno e enumerável.
- **Golden do `scenarios.json` commitado** — rejeitado: exigiria emenda maior na allowlist pra um artefato de runtime, e quebraria em reordenação inofensiva. A lista inline no `.py` congela o que precisa ser congelado.
- **Mover o pydantic pro `orchestrator/`** — rejeitado: custa a invariante stdlib-only que a ADR-0017 desenhou e que o CI protege.
- **Smoke local com Docker e vídeo real** — rejeitado: `perf` não roda no Mac e `-march=native` seria o do M-series; obrigaria um modo degradado em `run_scenario.sh`, que na campanha precisa falhar alto; e o ciclo de 10–20 min de build mata o loop de desenvolvimento.
- **`bats` pros scripts de shell** — rejeitado por redundância: o smoke local já dirige os `run_*.sh` com shims e já verifica o argv do FFmpeg; `bats` adicionaria um runner sem cobrir nada novo.
- **Validação do `meta.json` por presença de campo apenas** — rejeitado: `"warmup": "false"` é uma string truthy e passa por qualquer checagem de presença, reproduzindo exatamente o desastre que a validação existe pra impedir. Como quem escreve o arquivo é bash montando JSON à mão, erro de tipo é o modo de falha *mais* provável, não o menos.
- **Pydantic em modo default (lax)** — rejeitado: coage `"false"` → `False`, contradizendo o "falha alto se o tipo divergir" da ADR-0019 e fazendo os dois leitores discordarem sobre o que é um `meta.json` válido.
- **Formato de wire canônico pro `started_at` (UTC com `Z`) comparado como string** — rejeitado: transfere a garantia pra uma convenção do lado bash, que nenhum teste deste ADR alcança. Parsear e comparar instantes é enforceable no lado que valida.
- **Cardinalidade do canônico como única verificação da matriz** — rejeitado: contagem é invariante sob substituição de pares; um upscale pode entrar sem mexer em 162/972/810.
- **Invariantes de fatiamento como única verificação do plano** — rejeitado: são relativas ao canônico e portanto vacuosas quanto à correção dele. A cardinalidade precisa de asserção própria contra o `experiment.toml` real.
- **Vídeo de entrada do smoke gerado com FFmpeg real (`-f lavfi`)** — rejeitado: como o `ffmpeg` do encode é shimado, ninguém decodifica esse arquivo; gerá-lo custaria uma dependência de binário no job de CI pra produzir bytes que não são lidos.
- **Smoke AWS mínimo (só sobe, toca S3, termina)** — rejeitado: deixaria a disponibilidade dos eventos PMU por arquitetura descoberta, que é o modo de falha mais caro do projeto.
- **Smoke AWS completo nas três arquiteturas** — rejeitado: o caminho de build/clone/IAM é o mesmo nas três; só o mapeamento de PMU difere, e ele é verificável em segundos.
- **Pacote de teste compartilhado entre papéis** — rejeitado: contraria diretamente a separação de venvs da ADR-0017.
- **`make` como runner** — rejeitado: seria uma quarta ferramenta fora da ADR-0009 pra fazer o que o pytest já faz, e com asserções piores (exit code em vez de verificação de artefato).

## Consequences

- Emenda à allowlist do `.gitignore` (ADR-0017): `requirements-dev.txt`, fixtures `.json` sob `fixtures/`, e o workflow do CI — nenhum dos três estava permitido, apesar de a ADR-0017 depender dos três.
- `smoke/` entra como diretório de topo, com um terceiro `requirements-dev.txt` (`pytest` + `pydantic`). A ADR-0017 falava em dois.
- O CI passa a ter três jobs: `pre-commit run --all-files` (agora incluindo `terraform validate` e `hadolint`), pytest por papel Python em venvs separados, e o smoke local. Continua sendo evidência anexada ao PR, não gate autônomo.
- A ADR-0019 afirmava que "o único leitor programático é o `consolidate.py`"; são três, e dois deles são stdlib-only. Corrigido lá.
- A fixture-âncora do modelo pydantic só existe depois do primeiro smoke AWS — é sequenciamento de desenvolvimento, não detalhe.
- Os shims são a única superfície de manutenção nova que pode envelhecer mal (fake que diverge do real). A mitigação é o smoke AWS ser a fonte de verdade; se a estratégia precisar encolher algum dia, é por aí que se começa — nunca pela verificação de PMU.
- O shim do `ffmpeg` acumula três papéis (produzir artefatos falsos, controlar o `output.sha256`, logar o argv), o que o torna a peça mais carregada do `smoke/`. É deliberado: os três dependem do mesmo ponto de interceptação.
- O smoke local não tem dependência de binário externo, então o job de CI não quebra quando a imagem do runner do GitHub muda.
- Se o smoke revelar um evento PMU indisponível numa arquitetura, a resposta é decisão de desenho experimental (trocar o evento, ou reportar a métrica em duas das três), não de testes. Cai na ADR-0006 no dia em que ocorrer.
- Nomes de arquivo dos módulos de teste, das factories e dos shims ficam abertos até o desenvolvimento, coerente com a ADR-0017.
