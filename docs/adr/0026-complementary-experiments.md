# Experimentos complementares: topologia da Intel, repetibilidade entre hosts e contadores do Graviton

A campanha `campaign-1` (#104) fechou a comparação primária, e os dados dela levantaram dois problemas de desenho que o artigo não resolve só com o que existe. Um terceiro já estava declarado em aberto. A parte experimental fecha com **três experimentos complementares**, cada um com a sua definição em `config/` e o seu bucket, rodados pelo mesmo código da campanha. Depois deles, o que resta é análise e escrita.

## Os três problemas

**A Intel roda com metade dos núcleos físicos.** Pela API da EC2 (`describe-instance-types`), a `c7g.xlarge` e a `c7a.xlarge` têm 4 núcleos com 1 thread cada, e a `c7i.xlarge` tem 2 núcleos com 2 threads (SMT). A ADR-0001 e a ADR-0002 diziam que a `c7a` também era 2 × 2, e estavam erradas (as duas ganham emenda). A comparação com a Intel mistura o núcleo com o número de núcleos físicos: o IPC por thread da `c7i` fica em ~0,66× o da `c7a` nos três codecs, o que é consistente com SMT, mas não está isolado.

**A frequência da Intel mudou no meio da campanha.** A frequência efetiva (`perf_cycles` ÷ `perf_task_clock`) da `c7i` ficou em ~3,55–3,67 GHz nas primeiras ~20 h e em ~3,0 GHz nas ~45 h seguintes, com o IPC e as instruções por quadro intactos. A razão de tempo Intel/AMD foi 1,42 no período rápido e 1,65 no lento. No piloto, a `c7a` e a `c7g` reproduziram os mesmos Cenários em outras instâncias dentro de ±0,5%, e a `c7i` variou de −8% a +9%. O IC de cada Cenário, com 5 Replicações no mesmo bloco e no mesmo host, não captura essa variação.

**Os contadores do Graviton são multiplexados.** No `perf.json` da `c7g`, os pares `{cycles, instructions}` e `{branch-instructions, branch-misses}` se revezam, cada um ativo em ~50% do tempo (ADR-0006). A frequência medida (~2,39 GHz) fica abaixo dos 2,6 GHz sustentados que a API informa, e o artigo não compara instruções por quadro entre ARM e x86.

## 1. Topologia: a Intel com e sem SMT

Duas `c7i.2xlarge` lançadas **juntas**, pelo mesmo `run`, cada uma com um `CpuOptions` que deixa 4 vCPUs:

- `c7i-smt`: `CoreCount=2, ThreadsPerCore=2`, a topologia da `c7i.xlarge` da campanha;
- `c7i-nosmt`: `CoreCount=4, ThreadsPerCore=1`, a topologia de `c7g` e `c7a`.

Os dois valores são válidos para a `c7i.2xlarge` (`ValidCores` 1–4, `ValidThreadsPerCore` 1–2). A matriz é a da campanha inteira, 54 Cenários por braço, com o mesmo `[encode]`, os mesmos pares e vídeos e a mesma `seed` (`config/topologia.toml`).

**A métrica primária é ciclos por quadro**, que não depende da frequência. É ela que isola o efeito do SMT sobre o núcleo: dois threads dividindo um pipeline andam mais devagar cada um, e o encode leva mais ciclos por quadro. O tempo bruto é a segunda leitura, sempre ao lado da frequência efetiva de cada braço, porque é ele que a frequência contamina.

**Os dois braços sobem juntos** para que a frequência, se mudar, mude para os dois na mesma janela de relógio. Em lançamentos separados, a mudança que a campanha viu na `c7i` poderia cair inteira num braço e virar "efeito do SMT".

**O braço `c7i-smt` é a ponte com a campanha.** A topologia dele é a da `c7i.xlarge`, então os ciclos por quadro dele devem bater com os dela. Se baterem, o efeito do SMT medido aqui pode ser aplicado à `c7i` da campanha. Se não baterem, a `2xlarge` é outra máquina e o artigo diz isso.

O `run` confere, pelo `describe-instances`, que o `CpuOptions` aplicado é o declarado, e o grava na entrada do `state.json`. Se divergir, termina tudo e falha nomeando o id. O `preflight` de cada braço confere de dentro do container que o `nproc` é 4 e que o `lscpu` dá o `Thread(s) per core` declarado.

## 2. Repetibilidade entre hosts

Três instâncias novas de cada tipo (`c7g-r1` … `c7a-r3`), no mesmo `run`, sobre os 3 codecs × `1080p → 720p` × Big Buck Bunny, com o bloco de 6 (`config/repetibilidade.toml`). São Cenários que o piloto e a campanha já rodaram, então cada tipo passa a ter **5 hosts** neles. O que se mede é a dispersão do tempo entre máquinas físicas de cada tipo, a frequência efetiva em cada uma, e se os hashes repetem os da campanha.

O host é o `instance_id`, e não o id da definição: a `c7g` do piloto e a da campanha são duas máquinas com o mesmo id. A dispersão é calculada sobre a média de cada host, para que o host com mais Replicações não pese mais.

## 3. Contadores do Graviton sem multiplexação

Uma `c7g.xlarge` (`c7g-pmu`) com o `[instrumentation]` reduzido a `cycles`, `instructions` e os quatro eventos de software, e só a métrica `ipc` (`config/contadores.toml`). Com um único par de hardware, o orçamento da vPMU do Graviton (ADR-0006) comporta tudo e nada é multiplexado. O escopo é o da repetibilidade. O que se mede é a frequência real da ARM durante o encode, e as instruções por quadro sem extrapolação, comparáveis com as das x86 e com as contagens multiplexadas da campanha.

Os bitstreams não dependem dos eventos de PMU, então os hashes da `c7g-pmu` devem repetir os da `c7g` da campanha.

## Em todos: os hashes contra a campanha

Cada instância dos complementares descende de uma da campanha, e o id dela é o prefixo antes do hífen (`c7i-nosmt` → `c7i`, `c7g-r2` → `c7g`, `c7g-pmu` → `c7g`). O `analysis/complementares.py` compara o conjunto de hashes de cada (instância, Cenário) com o da instância de que ela descende, no mesmo Cenário. Os dois braços da topologia são x86 e comparam com a `c7i`.

## Como a maquinaria muda

- **`CpuOptions` é dado da definição.** `[[instance]]` aceita `core_count` e `threads_per_core`, opcionais e só os dois juntos. O `run_instances` os repassa como `--cpu-options`. Sem os campos, o comportamento é o de antes.
- **Dois registros podem ter o mesmo tipo.** O `run` lança cada um pelo próprio registro, e o `preflight` escolhe a instância pelo id (`--config … --instance c7i-nosmt`). Pelo tipo, a resolução recusa um tipo que dois registros declaram, em vez de escolher um deles em silêncio.
- **Os ids não levam `_`.** A `scenario_id` junta os campos com `_`, e a retenção tira a Instância dela por `rpartition`.
- **Os objetos de `status/` são nomeados pelo id curto da Instância**, e não pelo tipo (emenda da ADR-0011). Pelo tipo, as três `c7g.xlarge` da repetibilidade escreveriam o mesmo marcador, e o da última a terminar apagaria o das outras.
- **Um bucket por experimento.** A guarda do `run` recusa um bucket cujo `runs/` já tem Execuções, então cada experimento tem o seu (`topologia`, `repetibilidade`, `contadores`). O mapa `buckets` do `infra.json` aceita qualquer número de entradas, e o passo `buckets` da auto-checagem confere que o `masters/` de cada um é o da campanha.
- **As colunas de PMU do Parquet saem da definição** (emenda da ADR-0006). O `consolidate.py` recebe `--config`, e o `gate.py` confere as colunas que a definição declara e imprime a frequência efetiva por instância e ao longo do lançamento.
- **O `--cpu-options` ganha teste de argv**, exceção à linha "construção do argv — sem teste" da ADR-0022. O critério dela é a falha silenciosa, e o `run-instances` sem o `CpuOptions` não falha: sobe a topologia do tipo, e o braço sem SMT mede SMT. O `orchestrator/tests/test_external.py` substitui o `_run` do adaptador e confere só a presença e o valor do `--cpu-options`; o resto do argv continua sem teste.
- **A `c7i.2xlarge` entra na allowlist** de `ec2:InstanceType` (ADR-0016). A AZ não muda: em 2026-10-26, os seis tipos da allowlist eram oferecidos nas mesmas cinco zonas de `us-east-1`.

## Considered Options

- **Comparar a `c7i` com uma `c7i.2xlarge` de 8 vCPUs** — rejeitado: dobra os threads do encoder e mede escalabilidade, não topologia. O `CpuOptions` mantém os 4 vCPUs e troca só o que eles são.
- **Desligar o SMT de dentro do sistema operacional** (`nosmt`, ou offline dos irmãos) — rejeitado: muda a imagem e o kernel do boot, que a ADR-0015 congela, e o hypervisor continua alocando os núcleos com irmãos. O `CpuOptions` é a topologia que a EC2 entrega ao guest.
- **Os dois braços em `run`s separados** — rejeitado: a mudança de frequência que a campanha viu cairia num braço só.
- **Tempo como métrica primária da topologia** — rejeitado: é a métrica que a frequência contamina, e a campanha mostrou a frequência mudando sozinha.
- **Repetibilidade sobre a matriz inteira** — rejeitado: nove instâncias por ~60 h para responder uma pergunta que três Cenários já respondem, e os três já têm dois hosts por tipo no piloto e na campanha.
- **Reduzir o `perf stat` da campanha toda a um par por arquitetura** — rejeitado: a campanha já rodou, e o par de branch é dado do artigo. O `contadores.toml` responde à dúvida sobre a extrapolação sem refazer nada.
- **Os três experimentos num bucket só** — rejeitado: a guarda do `run` recusa um bucket com Execuções, e afrouxá-la reabriria o modo de falha que ela fecha (a campanha dentro do piloto).

## Consequences

- O artigo passa a reportar o efeito do SMT em ciclos por quadro, a dispersão entre hosts de cada tipo e as instruções por quadro do Graviton sem multiplexação, ao lado da comparação primária.
- O custo é de ~US$ 45 e ~2,5 dias de relógio, dominados pela topologia: US$ 0,357/h por `c7i.2xlarge` (`analysis/prices.toml`, `covers = "complementares"`).
- A quota de vCPU on-demand standard passa de 32 com os três experimentos juntos (16 + 36 + 4, mais as 2 do Orquestrador). Sem aumento, a repetibilidade roda depois da topologia.
- Os três `run`s podem rodar em paralelo, cada um com o próprio diretório de trabalho: o `state.json` e o probe da auto-checagem ficam no diretório do `--infra` (`orchestrator/README.md`).
