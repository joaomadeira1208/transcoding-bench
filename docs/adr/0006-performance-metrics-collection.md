# Coleta de métricas de desempenho durante a Execução

Cada Execução é instrumentada com **quatro fontes em paralelo**: `/usr/bin/time -v` envolvendo o processo FFmpeg (wall/user/sys time, max RSS, page faults, I/O); `perf stat -j` envolvendo o processo (hardware counters via PMU: `cycles, instructions, cache-references, cache-misses, branch-instructions, branch-misses, task-clock, context-switches, cpu-migrations, page-faults`); `pidstat -h -r -u -p $PID 1` rodando em paralelo (time series de CPU% e RSS a 1 Hz); FFmpeg's own stderr `-stats` parseado (frames encoded, fps reportado, output bitrate).

**Métricas-chave derivadas:** **IPC** (instructions/cycles), **cache miss rate** (cache-misses/cache-references), **branch mispredict rate** (branch-misses/branch-instructions). Esses três são os principais indicadores arquiteturais — eficiência ALU, memory hierarchy e branch predictor — exatamente onde Neoverse-V1 (Graviton 3), Sapphire Rapids (c7i) e EPYC Genoa (c7a) divergem microarquiteturalmente.

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
