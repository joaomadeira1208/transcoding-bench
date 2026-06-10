# Schema do dado bruto por Execução

Cada Execução produz um **diretório `runs/{run_id}/`** com os artefatos das ferramentas de instrumentação preservados na forma raw (re-parsáveis), mais um `meta.json` com cenário/versões/timestamps. Pós-experimento, uma **tabela Parquet consolidada** é gerada extraindo agregados de todos os diretórios — esse Parquet é o dataset analítico contra o qual notebooks/scripts operam. Diretórios raw são ground truth; Parquet é projeção.

```
runs/{run_id}/
  meta.json         ← cenário (chave composta), versions, instance_id, timestamps, exit code
  time.json         ← /usr/bin/time -v parseado pra JSON
  perf.json         ← perf stat -j output
  pidstat.csv       ← time series CPU%/RSS a 1 Hz
  ffmpeg.log        ← stderr completo do FFmpeg
  output.mkv        ← o encoded output (retenção seletiva, abaixo)
  output.sha256     ← hash do bitstream codificado (não do container .mkv inteiro)
```

`run_id` é um UUID v4 (à prova de retomada do experimento). `scenario_id` legível também persistido em `meta.json` pra debug humano (ex.: `libx264_2160p_1080p_bbb_c7g_rep1`).

**Tabela consolidada (Parquet)**: uma linha por Execução. Campos = cenário (chave composta) + run metadata + agregados de `time` e `perf` + parseados de FFmpeg + derivados (`ipc`, `cache_miss_rate`, `branch_mispredict_rate`, `cpu_pct_avg`). **Time series do pidstat NÃO entram no Parquet** — ficam nos diretórios raw e são consultadas sob demanda quando análise profunda precisa.

**Retenção dos outputs `.mkv` pós-Pass de qualidade** (ADR-0005):
- Manter apenas: (i) amostra metodológica fixa (~6–10), (ii) outputs de grupos hash-divergentes, (iii) reps usadas no Pass.
- Resto: deletar.
- Como `output.sha256` fica preservado no Parquet por Execução, divergência cross-arch é re-derivável via hashes sem precisar dos arquivos. O `.mkv` só importa pra Pass de qualidade ou inspeção visual pontual.

## Considered Options

- **Linha tabular única por Execução, sem diretório raw** (tudo num CSV/Parquet direto) — rejeitado: parser bugs e schema evolution viram desastre porque os raws não existem mais; logs e time series ficam de fora; nenhuma re-análise possível sem re-rodar 810 encodes.
- **CSV consolidado em vez de Parquet** — rejeitado: sem schema enforcement (colunas faltando passam silenciosamente); typing implícito problemático; compressão ruim em colunas numéricas.
- **JSONL consolidado** — rejeitado: flexível pra schema evolution mas menos eficiente em I/O analítico; pandas/pyarrow são fortes em Parquet.
- **Manter todos os 810 `.mkv` permanentemente** — rejeitado: storage cost desnecessário. Hash + Parquet preservam o sinal de equivalência cross-arch.
- **Deletar todos os `.mkv` pós-Pass** — rejeitado: amostra metodológica precisa permanecer pra alguém querer re-VMAFear ou inspecionar visualmente o que o paper reporta.
- **Run ID = scenario tuple direto, sem UUID** — rejeitado: se o experimento for parcialmente refeito (debug, retomada), tuple colide com runs anteriores. UUID é à prova de retomada.
- **Time series do pidstat no Parquet consolidado** — rejeitado: explode número de linhas (810 × ~600 samples ≈ 500 k linhas só de pidstat); analítica fica menos ergonômica; arquivos separados por Execução é a shape correta pra time series.

## Consequences

- **Storage estimado**: raw dirs sem `.mkv` ~poucos GB total (poucos MB por Execução × 810). Outputs `.mkv` com retenção seletiva ~dezenas de GB. Cabe em S3 standard tier sem dor.
- Qualquer análise nova pós-experimento é **re-consolidação** dos raw dirs — não precisa re-rodar nada.
- **Schema evolution durante o experimento**: se uma métrica nova for descoberta mid-experiment, Parquet pode ganhar coluna nova preenchida onde o raw a contém. Os raws ainda registram o que rolou — re-gerar Parquet com mais campos é barato.
- Time series só são analisadas sob demanda — pra a maioria das análises, a row do Parquet basta. Evita "carregar 500 k linhas pra todo plot".
- Decisão de **onde os runs dirs vivem** (storage backend: S3? EFS? local + sync periódico?) é separada e cai dentro de arquitetura/orquestração da pipeline.
- `output.sha256` precisa ser hash do **bitstream codificado**, não do container `.mkv` inteiro. Containers carregam metadados de mux com timestamps de criação que mudariam o hash sem o bitstream ter mudado. Extrair via `ffmpeg -i input.mkv -c copy -f $codec -` ou similar antes do `sha256sum`.
