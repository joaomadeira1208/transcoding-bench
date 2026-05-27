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
- Overhead total de instrumentação esperado < 1% wall time; é constante entre arch e não vicia comparação. Verificável com run vs. no-instrumentation em uma instância.
- Sem power direto, "eficiência energética" do ARM é argumentada via wall time × custo proxy. Não é equivalente a watt-hour, mas é o que EC2 permite e é coerente com a pergunta de pesquisa do `.tex` (performance × custo).
- `perf_event_paranoid` precisa estar permissivo o suficiente no host pra `perf stat` ler PMU events — provisioning das instâncias deve garantir.
- Aggregate counters viram colunas no Parquet consolidado (ADR-0007). Time series do pidstat ficam num arquivo separado por Execução (também ADR-0007), consultadas sob demanda.
