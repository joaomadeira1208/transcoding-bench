# Storage e transporte de artefatos

**S3 (mesma região das instâncias)** é o storage central do experimento. Todos os artefatos de cada Execução — incluindo o `.mkv` — são uploadados pro S3 imediatamente após cada run. Limpeza seletiva dos `.mkv` acontece após o Pass de qualidade, conforme ADR-0005.

O bucket S3 é criado pelo Terraform como parte da infra base e **não é destruído com urgência** — o custo de storage é ~$3.40/mês pra ~146 GB (todos os outputs), caindo pra centavos após limpeza dos `.mkv`. Pode ser destruído manualmente ao final do TCC.

## Fluxo de upload

Cada `run_scenario.sh`, ao terminar uma Execução, faz:
```
aws s3 cp runs/{run_id}/ s3://bucket/runs/{run_id}/ --recursive
```

Isso inclui: `meta.json`, `time.json`, `perf.json`, `pidstat.csv`, `ffmpeg.log`, `output.mkv`, `output.sha256`.

Upload acontece **entre runs** (não durante o encode), portanto não contamina métricas de performance.

## Custos estimados

| Item | Custo |
|---|---|
| Storage (146 GB × $0.023/GB/mês) | ~$3.40/mês |
| Upload (EC2 → S3 mesma região) | grátis |
| PUT requests (~972 uploads) | < $0.01 |
| Download pro Juiz (mesma região) | grátis |

Pra contexto: o compute total do experimento custa ~$70. S3 é < 5% disso.

## Considered Options

- **Disco local + sync periódico pro S3** — rejeitado: janela de perda se instância morre entre syncs. Upload imediato garante durabilidade a cada run.
- **EFS (filesystem compartilhado)** — rejeitado: latência de I/O pode afetar métricas de encode; custo por GB mais alto que S3; complexidade de setup desproporcional.
- **Upload seletivo (só .mkv que irão pro Pass de qualidade)** — rejeitado: pra decidir quais reter, precisa do hash-first triage cross-arch, que só é possível após as 3 instâncias terminarem. Complexidade de upload seletivo não justifica a economia (~$3.40/mês).

## Consequences

- Storage total durante o experimento: ~146 GB. Após limpeza seletiva pós-Pass: poucos GB (JSONs, CSVs, logs, amostra de .mkv).
- O bucket S3 é o "ground truth" do experimento — todos os raw dirs vivem lá. Consolidação do Parquet (local) é projeção re-gerada a qualquer momento.
- Instâncias de encode precisam de IAM role com permissão `s3:PutObject` no bucket.
