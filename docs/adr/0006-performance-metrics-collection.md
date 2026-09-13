# Coleta de métricas de desempenho durante a Execução

Cada Execução é instrumentada com **quatro fontes em paralelo**: `/usr/bin/time -v` envolvendo o processo FFmpeg (wall/user/sys time, max RSS, page faults, I/O); `perf stat -j` envolvendo o processo (hardware counters via PMU: `cycles, instructions, cache-references, cache-misses, branch-instructions, branch-misses, task-clock, context-switches, cpu-migrations, page-faults` — **Emenda:** o par de cache saiu, e são oito eventos: `cycles, instructions, branch-instructions, branch-misses, task-clock, context-switches, cpu-migrations, page-faults`, pela última seção deste ADR); `pidstat -h -r -u -p $PID 1` rodando em paralelo (time series de CPU% e RSS a 1 Hz); FFmpeg's own stderr `-stats` parseado (frames encoded, fps reportado, output bitrate).

**Métricas-chave derivadas:** **IPC** (instructions/cycles), **cache miss rate** (cache-misses/cache-references; **Emenda:** saiu das métricas-chave, pela última seção deste ADR), **branch mispredict rate** (branch-misses/branch-instructions). Esses três são os principais indicadores arquiteturais — eficiência ALU, memory hierarchy e branch predictor — exatamente onde Neoverse-V1 (Graviton 3), Sapphire Rapids (c7i) e EPYC Genoa (c7a) divergem microarquiteturalmente.

**Power/energia é skip explícito.** AWS Graviton não expõe contadores RAPL/MSR de energia ao guest EC2, impedindo medição cross-arch consistente. Custo entra como proxy no pós-experimento (`instance $/hour × wall_time`), não como métrica por Execução.

## Considered Options

- **Apenas `/usr/bin/time -v` (counters básicos do kernel)** — rejeitado: perde IPC, cache, branch — exatamente os indicadores onde arch difere.
- **`perf record` (sampling profiler / flamegraph)** — rejeitado: overhead maior, valor analítico pro TCC não justifica. Aggregate counters de `perf stat` bastam pra arch comparison. Fica como ferramenta de debug se um cenário tiver comportamento esquisito.
- **eBPF / bpftrace** — rejeitado: overhead e complexidade extra sem ganho metodológico significativo pra `perf stat` já cobrir o necessário.
- **Sub-second sampling (10 Hz, 100 ms) em pidstat** — rejeitado: encoding é bound por complexidade de cena que muda em escala de segundos. 1 Hz captura variação significativa; rate mais fino infla dados sem precisão proporcional.
- **Incluir power/energy via RAPL/turbostat** — rejeitado por restrição de plataforma: ausente em Graviton sob EC2 guest. Manter no escopo só pra x86 violaria simetria experimental.
- **Não instrumentar (parsear só stderr do FFmpeg)** — rejeitado: FFmpeg reporta fps e progress, mas não IPC, cache, detalhes de memória.

## Consequences

- Eventos PMU mapeados via `perf` têm IDs diferentes entre Neoverse-V1 e Sapphire Rapids/EPYC. Contagens são **conceitualmente comparáveis** ("ARM tem X% menos cache misses nesse workload") mas não substituem análise microarquitetural detalhada (fora do escopo do TCC).
- **A disponibilidade dos dez eventos por arquitetura é verificada por smoke antes da campanha** (ADR-0022): um `perf stat` curto dentro do container em cada uma das três instâncias, conferindo que nenhum evento volta `<not supported>`. A verificação existe porque o modo de falha é silencioso — `perf stat` não falha quando um evento é indisponível, apenas o reporta como tal e segue. Sem o smoke, o sintoma seria uma coluna de IPC/cache/branch vazia para uma arquitetura inteira, descoberta no `consolidate.py` só depois de a campanha terminar. Se a verificação reprovar, a resposta é de desenho experimental (trocar o evento ou reportar a métrica em duas das três arquiteturas), e volta pra este ADR.
- Overhead total de instrumentação esperado < 1% wall time; é constante entre arch e não vicia comparação. Verificável com run vs. no-instrumentation em uma instância.
- Sem power direto, "eficiência energética" do ARM é argumentada via wall time × custo proxy. Não é equivalente a watt-hour, mas é o que EC2 permite e é coerente com a pergunta de pesquisa do `.tex` (performance × custo).
- `perf_event_paranoid` precisa estar permissivo o suficiente no host pra `perf stat` ler PMU events — provisioning das instâncias deve garantir.
- Aggregate counters viram colunas no Parquet consolidado (ADR-0007). Time series do pidstat ficam num arquivo separado por Execução (também ADR-0007), consultadas sob demanda.

## Emenda: as razões são medidas em grupo, e o regime de medição é dado

O `preflight` (ADR-0022) rodou nos três tipos contra `d115c16`. **Ele reprovou uma
arquitetura e aprovou duas que entregaram dado inutilizável.**

| Evento | c7g.xlarge (Graviton3) | c7i.xlarge (Sapphire Rapids) | c7a.xlarge (Genoa) |
|---|---|---|---|
| `cycles` | contou | 978.242 | **40.210** |
| `instructions` | contou | 701.410 | 746.971 |
| `cache-references` | contou | **0** | 82.561 |
| `cache-misses` | `<not counted>` | **0** | 20.657 |
| `branch-instructions` | `<not counted>` | 124.157 | 146.708 |
| `branch-misses` | `<not counted>` | 6.495 | **1.382.176** |
| `task-clock` | contou | 31 | 38 |
| `context-switches` | contou | 0 | 0 |
| `cpu-migrations` | contou | 0 | 0 |
| `page-faults` | contou | 51 | 51 |
| **Veredito do passo** | **falhou** | **passou** | **passou** |

As três métricas-chave calculadas sobre isso:

| | c7g | c7i | c7a |
|---|---|---|---|
| IPC | — | 0,72 (plausível) | **18,6 (impossível)** |
| cache miss rate | — | **nulo** (0/0) | 25% (parece plausível) |
| branch mispredict rate | — | 5,2% (plausível) | **942% (impossível)** |

Nenhuma das três microarquiteturas retira mais que ~6 a 8 instruções por ciclo, e
não se erra mais desvios do que se executa. O item mais perigoso da tabela é o
**25% do c7a**: é o único valor que passaria despercebido, construído sobre
contadores que acabaram de provar não ser confiáveis.

A consequência acima previu o sintoma — "uma coluna de IPC/cache/branch vazia para
uma arquitetura inteira" — e errou a forma: ela mandou conferir `<not supported>`,
e nenhuma das duas falhas que passaram tem essa forma. São quatro fatos que este
ADR não conhecia.

**1. O kernel multiplexa o que não cabe nos contadores.** A PMU tem poucos
registradores; pedindo dez eventos soltos, cada um enxerga um trecho diferente da
execução, extrapolado pelo seu próprio fator. As três métricas-chave são
**razões**, e dividir um numerador medido numa janela por um denominador medido em
outra produz o IPC de 18,6. Um encode não é uniforme — I-frame, P-frame e mudança
de cena têm perfis de cache e de branch distintos —, então isso não se dilui com o
tempo.

**2. `<not counted>` e o zero mudo são modos de falha próprios.** `<not supported>`
é o evento que não existe na PMU; `<not counted>` é o contador que abriu e nunca
rodou (foi o c7g); e o zero é o contador que respondeu e não contou (foi o c7i, com
51 page faults e 700 mil instruções a frio — não é ambiguidade, é contador que não
conta naquele guest). O zero é o pior dos três, porque é número válido: passa por
qualquer guarda de string e vira `cache_miss_rate` nulo para uma arquitetura
inteira.

**3. Responder não é medir.** `branch-misses = 1.382.176` contra
`branch-instructions = 146.708` são dois números, os dois não-zero, nenhum é string
de erro. Nenhuma guarda sobre disponibilidade de evento alcança isso.

**4. O nome genérico de cache pode não ser o mesmo nível nas três arquiteturas.**
`cache-references` e `cache-misses` são nomes que cada driver de PMU do kernel
resolve para um evento nativo, e a resolução conhecida é L1D no arm64, LLC na Intel
e L2 na AMD Zen. Se isso se confirmar nos três guests, o "cache miss rate" compara
três coisas distintas.

### A decisão

**As razões passam a ser medidas em grupo.** O `config/experiment.toml` declara,
ao lado de `pmu_events`, uma `[[instrumentation.metric]]` por métrica-chave, com o
par de eventos que o `perf` tem de contar junto: `{instructions, cycles}`,
`{cache-misses, cache-references}`, `{branch-misses, branch-instructions}`. O
`perf` escalona um grupo de forma atômica — ou os dois membros entram nos
contadores, ou nenhum entra —, então mesmo que o par só veja um terço da execução,
os dois membros veem **o mesmo** terço e a razão continua correta.

**Pares, e não o sexteto.** Um grupo que não cabe nos contadores nunca é escalonado
e volta `<not counted>` inteiro, e o c7g mostrou que o orçamento pode ser de três
contadores. Dois por grupo cabe nas três arquiteturas, o que torna a medição
**simétrica por construção** — que é o requisito do Experimento. Os quatro eventos
de software ficam fora dos grupos: não ocupam contador de PMU.

**O regime de medição é registrado junto do valor.** O `pcnt-running` do
`perf stat -j` — a fração do tempo em que cada contador esteve rodando — passa a
aparecer na tabela do `preflight` e a ser coluna no Parquet, uma por evento. Abaixo
de 100% o valor é estimativa extrapolada, não contagem. **Não é recusa**: com os
pares, fração abaixo de 100 é o regime esperado onde a PMU tem menos contadores que
eventos. É o que permite ao artigo dizer se um número é contagem ou estimativa, e
se as três arquiteturas estão no mesmo regime.

**O evento nativo resolvido é registrado por arquitetura.** O probe do `preflight`
roda com `-vv`, que despeja no stderr o `perf_event_attr` de cada evento com o
`config` nativo que o nome genérico resolveu. A saída crua do probe — stdout e
stderr — é guardada em `runs/preflight/<instance-id>/`, sem apagar. É a
rastreabilidade que o artigo precisa de qualquer forma: "IPC no Graviton" tem de
dizer qual evento nativo foi contado.

**A guarda recusa três modos de falha, e confere plausibilidade.** Os dois leitores
de `perf stat` — o `preflight.py` e o `instrumentation_failure_reason` do
`run_scenario.sh` — recusam `<not supported>`, `<not counted>` e zero em evento de
**hardware**, com mensagem que distingue os três, e conferem a coerência interna de
cada razão contra o `max_ratio` declarado no TOML. O teto do IPC é folgado (10) —
acima do que qualquer uma das três microarquiteturas retira, e longe dos 1 a 4 de
um encoder; o das duas taxas é 1, que é dizer que não se erra mais desvios do que
se executa nem se perde mais linhas de cache do que se referencia.

**Sem dispensa por arquitetura.** A regra do zero derruba todo run de uma
arquitetura em que um evento de hardware não conta, e é o comportamento certo — é
por isso que o `preflight` roda antes. Um evento de hardware zerado, uma
plausibilidade reprovada com contador a 100%, ou um par de cache que não é o mesmo
nível nas três arquiteturas é **decisão de desenho experimental**, tomada aqui
antes do piloto, entre trocar o par por eventos que contem nas três (mesmo nível de
cache) ou declarar por instância o evento indisponível — nunca uma exceção na
guarda.

## Emenda: o par de cache sai, porque nenhum par conta o mesmo nível nas três

O `preflight` com os pares rodou nos três tipos contra `40eabee`, com um probe que
encoda 5 s de um Master real. **Ele reprovou o c7i e aprovou o c7g e o c7a**, e
desta vez os três vereditos estão certos: os dois números impossíveis da rodada 1
morreram, e o que os matou foi o grupo. O par `{branch-instructions,
branch-misses}` do c7a passou a dividir `time_running` idêntico — 3.083.673.482 ns
nos dois membros — e o IPC de 18,6 virou 2,843, a taxa de 942 % virou 1,155 %.

O mesmo trabalho nos três (`bbb_2160p` → 480p, libsvtav1, 150 frames):

| | c7g (Neoverse-V1) | c7i (Sapphire Rapids) | c7a (Genoa) |
|---|---|---|---|
| CPUID | `0x411fd401` | `GenuineIntel-6-8F-8` | `AuthenticAMD-25-11-1` |
| instância | `i-02a2ba4032a826f1b` | `i-093a2d08039d19cc5` | `i-0cea56fb19959b1ef` |
| **veredito do passo** | **passou** | **falhou** | **passou** |
| `pcnt-running` (hardware) | **33 %** | **100 %** | **67 %** |
| pares simultâneos | 1 de 3 | 3 de 3 | 2 de 3 |
| `cycles` | 13.645.384.545 *(est.)* | 19.506.134.719 | 13.999.716.778 *(est.)* |
| `instructions` | 44.131.916.894 *(est.)* | 32.047.111.722 | 39.804.479.651 *(est.)* |
| `cache-references` | 15.341.620.256 *(est.)* | **0** | 1.446.934.571 *(est.)* |
| `cache-misses` | 259.185.351 *(est.)* | **0** | 112.536.137 *(est.)* |
| `branch-instructions` | 3.588.784.711 *(est.)* | 2.928.050.381 | 3.659.887.832 *(est.)* |
| `branch-misses` | 26.588.566 *(est.)* | 21.344.054 | 42.261.732 *(est.)* |

| | c7g | c7i | c7a |
|---|---|---|---|
| IPC | 3,234 | 1,643 | 2,843 |
| cache miss rate | 1,69 % | **inexistente** | 7,78 % |
| branch mispredict rate | 0,741 % | 0,729 % | 1,155 % |

**Duas das três métricas-chave estão validadas nas três arquiteturas.** IPC e
branch mispredict rate são as mesmas grandezas em qualquer ISA, contaram nas três,
e as razões são invariantes ao regime: o branch mispredict do c7g (0,741 %, medido
com extrapolação de 3×) bate com o do c7i (0,729 %, contagem direta) na terceira
casa, e a densidade de desvios do c7i (9,14 %) bate com a do c7a (9,19 %) sobre o
mesmo caminho AVX-512 do mesmo código.

**Os três regimes de `pcnt-running` são diferentes, e isso é dado.** Cabem 1, 2 e 3
pares simultâneos em c7g, c7a e c7i: os absolutos do c7g são extrapolação por 3,0,
os do c7a por 1,5, e os do c7i são contagem. As razões não sofrem — é o que os
pares garantem —, mas **todo número absoluto no artigo precisa da coluna
`pcnt-running` ao lado**, que já é coluna do Parquet.

### O defeito 6, fechado com número

A consequência de cima suspeitava que `cache-references` não fosse o mesmo nível
nas três. A rodada 2 mediu:

| | `cache-references` / instrução | cache miss rate | nível implicado |
|---|---|---|---|
| c7g | **0,348** | 1,69 % | **L1D** |
| c7i | **0** | — | **nenhum** |
| c7a | **0,036** | 7,78 % | **L2** |

Uma referência a cada três instruções, mais de uma por ciclo, é taxa de acesso a
L1D e não pode ser outra coisa. Dez vezes menos referências e quatro vezes mais
miss é o que sobra depois de o L1D filtrar. **9,5× de diferença na taxa de
referência não é microarquitetura — é evento diferente.** O "cache miss rate" desta
ADR, como estava declarado, comparava L1D com L2 com nada.

O zero do c7i é um fato à parte e mais forte do que o `<not counted>` da rodada 1:
`time_enabled == time_running`, `pcnt-running` **100 %**, o contador ligado os
6,41 s inteiros de CPU durante um encode que aposentou 32 bilhões de instruções, e
contou zero. O evento abre, roda e não conta.

### O candidato: um par que nomeia o nível

A primeira decisão foi trocar o par por `L1-dcache-loads` /
`L1-dcache-load-misses`, da abstração `PERF_TYPE_HW_CACHE` do `perf`, em que o
nível está no nome em vez de ficar por conta do driver de PMU. O teste de
aceitação **não** era o evento ter contado, e sim `L1-dcache-loads / instructions`
cair na mesma faixa nas três — a de L1D, ~0,2 a 0,4 —, a mesma medida que fechou o
defeito 6. Nada disso é decidível no Mac (ADR-0022), então o candidato foi
declarado no TOML e o `preflight` rodou nos três tipos contra `da9b157`, c7i
primeiro, o mesmo trabalho da rodada 2:

| | c7g (Neoverse-V1) | c7i (Sapphire Rapids) | c7a (Genoa) |
|---|---|---|---|
| instância | `i-0ef3c6647baf40245` | `i-0b7f3b9cb36eec847` | `i-016a56da9f2cf4164` |
| **veredito do passo** | **passou** | **passou** | **falhou** |
| `pcnt-running` (hardware) | 33 % | 100 % | 66 % |
| `cycles` | 13.595.719.511 *(est.)* | 21.869.294.137 | 13.773.153.004 *(est.)* |
| `instructions` | 44.659.490.841 *(est.)* | 32.081.190.804 | 39.316.368.297 *(est.)* |
| `L1-dcache-loads` | 15.333.812.319 *(est.)* | 8.780.555.335 | **0** |
| `L1-dcache-load-misses` | 259.836.366 *(est.)* | 263.624.818 | **0** |
| `branch-instructions` | 3.575.174.643 *(est.)* | 2.935.644.407 | 3.644.849.725 *(est.)* |
| `branch-misses` | 26.409.636 *(est.)* | 21.690.210 | 41.254.686 *(est.)* |

| | c7g | c7i | c7a |
|---|---|---|---|
| `L1-dcache-loads` / instrução | 0,343 | 0,274 | **0** |
| L1D miss rate | 1,69 % | 3,00 % | — |
| IPC | 3,285 | 1,467 | 2,855 |
| branch mispredict rate | 0,739 % | 0,739 % | 1,132 % |

**No c7g e no c7i o candidato funcionou como previsto.** Os dois estão na faixa de
L1D, a Intel abaixo por contar só loads, e o c7g repetiu a rodada 2 na segunda
casa — `L1-dcache-loads` resolve para o mesmo `L1D_CACHE` que `cache-references`
já contava. IPC e branch mispredict repetiram a rodada 2 nas três, dentro da
variação entre corridas.

**No c7a o candidato zerou, dos dois lados.** O grupo foi escalonado (66 % do
tempo, o mesmo `time_running` nos dois membros) e os dois contadores responderam
zero — o modo de falha do c7i na rodada 2, no outro guest. O lado dos loads era o
risco previsto (`0x0040`, ausente das tabelas do `amdzen4`). O lado do miss **não**
era: `0xc860` é o evento `0x60`, e `0xff60` contou 1,45 bilhão no mesmo guest na
rodada 2. A diferença é o umask — `0xff` contou, `0xc8` não —, o que diz que a
vPMU do c7a filtra por par evento/umask, e não por evento. Qualquer candidato
nativo é uma corrida sem resultado previsível.

### A decisão

**O cache miss rate sai das métricas-chave, e o par de cache sai do `pmu_events`.**
Este ADR passa a ter **duas** métricas-chave, IPC e branch mispredict rate, sobre
oito eventos: `{cycles, instructions}`, `{branch-instructions, branch-misses}` e os
quatro de software. O `config/experiment.toml` e o `config/pilot.toml` deixam de
declarar o par e a métrica; o `analysis/run_table.py` deixa de transcrevê-los e o
Parquet perde as colunas `perf_l1_dcache_*` e `cache_miss_rate` (ADR-0007). A
guarda não muda: os três modos de falha e as duas plausibilidades continuam valendo
igual nas três, sem dispensa por arquitetura.

Por que sair, e não insistir:

- **Não há nível de cache que as três vPMUs contem.** LLC está fora no c7i, L1D
  está fora no c7a, e o filtro do c7a é por evento e umask. L2 nunca foi testado e
  seria a mesma aposta.
- **Mesmo um par que contasse não seria a mesma grandeza.** Loads retirados na
  Intel, loads e stores no arm, loads despachados na AMD. O artigo teria uma coluna
  com um nome e três definições — o defeito 6 com outra roupa.
- **Miss em L1D explica pouco sozinho.** O que custa tempo é ciclo parado esperando
  memória, e ligar miss a stall exige eventos ainda menos portáveis. O IPC já é o
  resultado líquido da hierarquia de memória: um guest que sofre mais com cache
  neste workload retira menos instruções por ciclo, e esse número existe nas três,
  medido em grupo e validado.
- **A variável dependente é tempo, throughput e custo (ADR-0005).** A PMU é
  explicação arquitetural, e duas das três explicações sobrevivem — as duas que
  são a mesma grandeza em qualquer ISA.

O que fica no lugar da hierarquia de memória: o IPC, mais o que já é portável e já
é coletado — RSS máximo e page faults do `time`, `page-faults` do `perf`, e a série
do `pidstat`. E um achado para o artigo: as vPMUs da AWS filtram eventos de cache
de forma diferente por família de instância, e isso é resultado de
reprodutibilidade, não rodapé.

**Um rodapé que o artigo deve ao IPC.** O x86 corre o caminho AVX-512 e retira
menos instruções, mais largas; o arm corre NEON de 128 bits e retira mais. O IPC de
1,47 do c7i contra 3,28 do c7g não diz que um é pior — diz que a contagem de
instruções não é a mesma moeda entre ISAs. O IPC é contexto; a comparação é em
tempo e custo.

#### Opções rejeitadas

- **Trocar o par por um que nomeie o nível** (`L1-dcache-loads` /
  `L1-dcache-load-misses`). Foi a primeira decisão desta emenda, e foi **testada**:
  passou no c7g e no c7i, na faixa prevista, e zerou no c7a. Rejeitada pelo número.
- **Declarar o evento nativo por arquitetura no TOML.** Mantém o nível fixo e
  explícito, e é a única opção que cumpre por construção a rastreabilidade que esta
  ADR promete. Custa a neutralidade da declaração (o `event_spec` vira função da
  instância), colunas do Parquet com nome diferente por arquitetura, e, depois do
  zero no `0xc8`, uma corrida de ~22 min por candidato sem forma de prever o
  resultado — para chegar a um par que ainda compararia três definições de "load".
  Continua sendo o caminho se um dia houver um par que conte nas três e uma
  pergunta que precise dele.
- **Declarar o evento indisponível por instância.** Sobrariam c7g e c7i, que agora
  contam o mesmo nível — mas uma métrica-chave em duas de três arquiteturas, sem
  exceção na guarda, exige o mesmo `event_spec` por instância da opção anterior, e
  o artigo compararia duas máquinas numa métrica e três nas outras. É a aparência
  de conserto que a opção anterior custa, sem o dado que ela daria.

### O `-vv` não registra o evento nativo

A emenda anterior afirmou que o probe com `-vv` "despeja no stderr o
`perf_event_attr` de cada evento com o `config` nativo que o nome genérico
resolveu". **Isso não se cumpre.** O que o `perf.stderr.txt` das três arquiteturas
trouxe para `cache-references` foi `config 0x2 (PERF_COUNT_HW_CACHE_REFERENCES)`,
e para `L1-dcache-loads` foi `PERF_TYPE_HW_CACHE` com
`PERF_COUNT_HW_CACHE_L1D | OP_READ | RESULT_ACCESS` — o nome genérico, idêntico nas
três. A resolução para o evento nativo acontece dentro do driver do kernel,
**depois** da syscall; o `perf_event_attr` que o `-vv` despeja é o de antes.

O `-vv` continua no probe, e continua provando algo: é onde se lê o `type` e o
`config` que o `perf` pediu, e onde se confere que cada par abriu como grupo
(`group_fd` apontando para o líder). O que ele não dá é o evento nativo, e a
rastreabilidade que o artigo precisa vem daqui: da tabela abaixo, lida do fonte do
kernel, mais a versão do kernel da AMI.

### O evento nativo, por arquitetura

Tabelas de mapeamento do kernel Linux (`drivers/perf/arm_pmuv3.c`,
`arch/x86/events/intel/core.c`, `arch/x86/events/amd/core.c`), lidas antes de
gastar instância. Os mesmos valores em v6.8 e em v6.14, de modo que a leitura não
depende de qual das duas séries a AMI carrega. O `uname -r` das duas AMIs
(`ami-025d99823a4caad37` para x86_64, `ami-0246d714afcc1d494` para arm64): *a
preencher a partir da instância do Orquestrador e do log do c7g*.

O par que ficou, e o que cada nome resolve — os quatro são eventos arquiteturais
nas duas ISAs x86 e eventos comuns do PMUv3 no arm64, e contaram nas três nas
rodadas 2 e 3:

| | `cycles` | `instructions` | `branch-instructions` | `branch-misses` |
|---|---|---|---|---|
| arm64 (`armv8_pmuv3_perf_map`) | `CPU_CYCLES` `0x0011` | `INST_RETIRED` `0x0008` | `PC_WRITE_RETIRED` `0x000c` | `BR_MIS_PRED` `0x0010` |
| Sapphire Rapids (`intel_perfmon_event_map`) | `CPU_CLK_UNHALTED.THREAD` `0x003c` | `INST_RETIRED.ANY` `0x00c0` | `BR_INST_RETIRED.ALL_BRANCHES` `0x00c4` | `BR_MISP_RETIRED.ALL_BRANCHES` `0x00c5` |
| Genoa (`amd_f17h_perfmon_event_map`) | `0x0076` | `0x00c0` | `0x00c2` | `0x00c3` |

O candidato testado e rejeitado, para o registro de por que zerou onde zerou:

| | `L1-dcache-loads` | `L1-dcache-load-misses` |
|---|---|---|
| arm64 (`armv8_pmuv3_perf_cache_map`) | `L1D_CACHE` `0x0004` — loads **e** stores | `L1D_CACHE_REFILL` `0x0003` |
| Sapphire Rapids (`glc_hw_cache_event_ids`) | `MEM_INST_RETIRED.ALL_LOADS` `0x81d0` — só loads, retirados | `L2_RQSTS.ALL_DEMAND_DATA_RD` `0xe124` |
| Genoa (`amd_hw_cache_event_ids_f17h`) | `0x0040` — acessos ao DC, loads e stores | `0xc860` — requisições de L2 por miss do DC |

E o par original, pelo mesmo motivo: `cache-references` era `L1D_CACHE` `0x0004` no
arm64, `LONGEST_LAT_CACHE.REFERENCE` `0x4f2e` na Intel e `l2_request_g1.all`
`0xff60` na AMD. É a tabela do defeito 6 lida do outro lado.

**Duas notas que custaram uma leitura de fonte.** A primeira: o `0x0040` da AMD é
da era do Zen 1 — `ls_dc_accesses` está nas tabelas de eventos do `amdzen1`, do
`amdzen2` e do `amdzen3` e **não está na do `amdzen4`**, que é o Genoa; o kernel o
programa de qualquer forma, porque `amd_hw_cache_event_ids_f17h` vale para toda
família ≥ 0x17. A fonte foram as tabelas JSON do `perf`
(`tools/perf/pmu-events/arch/x86/amdzen*/`), e o zero da rodada 3 confirmou o que
elas diziam. A segunda: `MEM_INST_RETIRED.ALL_LOADS` **não** puxa evento auxiliar —
o `PMU_FL_MEM_LOADS_AUX` da Sapphire Rapids vale só para o
`0xcd`/`MEM_TRANS_RETIRED.LOAD_LATENCY` —, e a rodada 3 confirmou o grupo de dois
contadores no c7i.

### A hipótese sobre a vPMU do c7i, e o que a rodada 3 disse das três

Os seis eventos genéricos de hardware são todos **arquiteturais** na Intel
(CPUID leaf 0xA), e o guest do c7i contou quatro e zerou os dois de LLC na rodada
2. A hipótese com que o candidato foi escolhido era *só a família LLC está fora, e
o resto da vPMU está inteiro*; nenhum evento não-arquitetural tinha sido
exercitado. **A rodada 3 a confirmou para os dois eventos que importavam:**
`MEM_INST_RETIRED.ALL_LOADS` e `L2_RQSTS.ALL_DEMAND_DATA_RD` contaram a 100 % no
c7i, com valores plausíveis.

O que as três rodadas dizem de cada vPMU, e que o artigo pode registrar:

- **c7g**: contou tudo o que se pediu, genérico e de cache, com o orçamento de um
  par por vez (33 %).
- **c7i**: conta eventos arquiteturais e não-arquiteturais de core; zera a família
  `LONGEST_LAT_CACHE`. Orçamento de três pares (100 %).
- **c7a**: conta os arquiteturais e `0xff60`; zera `0x0040` e `0xc860`. O filtro é
  por evento **e** umask. Orçamento de dois pares (66 %).

### A lista de eventos da análise

O `analysis/run_table.py` transcreve os oito eventos porque o `meta.json` não os
carrega, e lê o par de cada razão por nome. Trocar um par no TOML sem tocar nele
produziria a coluna da razão nula **em silêncio** — o sintoma que esta ADR mais
teme, agora do lado da análise. A transcrição fica, e o
`analysis/tests/test_pmu_events.py` a amarra ao `config/experiment.toml` e ao
`config/pilot.toml`: a lista, a ordem e o par de cada métrica. Ler o TOML em tempo
de execução foi rejeitado porque o `consolidate.py` recebe um diretório `runs/` e
mais nada (ADR-0014), e um TOML novo sobre runs antigos daria um schema errado sem
dizer nada — o mesmo defeito, de cabeça para baixo.
