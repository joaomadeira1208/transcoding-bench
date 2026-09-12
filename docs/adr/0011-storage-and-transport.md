# Storage e transporte de artefatos

**S3 (mesma região das instâncias)** é o storage central do experimento. Todos os artefatos de cada Execução — incluindo o `.mkv` — são uploadados pro S3 imediatamente após cada run. Limpeza seletiva dos `.mkv` acontece após o Pass de qualidade, conforme ADR-0005.

O bucket S3 é criado pelo Terraform como parte da infra base e **não é destruído com urgência** — o custo de storage é ~$3.40/mês pra ~146 GB (todos os outputs), caindo pra centavos após limpeza dos `.mkv`. Pode ser destruído manualmente ao final do TCC.

**Emenda: são dois buckets, com o mesmo layout.** O piloto (ADR-0022) escreve num bucket **próprio**, criado pelo mesmo Terraform e com a mesma política de retenção. O motivo é o `resume.py`: os `scenario_id` do piloto são os mesmos da campanha, e no mesmo bucket ele daria os blocos do piloto por completos e a campanha os pularia em silêncio; a dedup dos leitores misturaria as duas. O nome do bucket já chega por argumento a todo consumidor, então dois buckets não custam uma linha nos scripts. Os `masters/` do bucket do piloto são **cópia** (`aws s3 sync`, intra-região) dos do bucket da campanha, para que os dois sejam byte-idênticos e a preparação (ADR-0014) rode uma vez só. Um prefixo `pilot/` no mesmo bucket foi rejeitado: quebraria este layout, que todo leitor percorre a partir da raiz.

## Layout de prefixos do bucket (contrato)

```
s3://<bucket>/
  masters/          # 6 masters + manifest.json (preparação, ADR-0014)
  scenarios/        # scenarios.json canônico + fatias por arch (ex.: canonical.json, c7g.json)
  runs/{run_id}/    # raw dirs das Execuções (ADR-0007), warm-ups inclusos
  status/           # progresso e desfecho por Instância
                    #   ({instance_type}_progress, {instance_type}_done, judge_done)
  quality/
    plan.json       # quality_plan.json gerado pelo triage (ADR-0014)
    results/        # resultados VMAF/SSIM do Juiz
```

**Emenda: `masters/manifest.json` é objeto de contrato.** A preparação dos masters (ADR-0014) escreve, ao lado dos seis `.mkv`, um manifesto com nome, tamanho, sha256 e as propriedades observadas pelo `ffprobe` de cada um, mais as versões da imagem que os produziu. Ele tem dois leitores: o pesquisador, que o confere antes de aprovar a campanha (gate da ADR-0012), e o bootstrap de cada instância de encode, que valida o sha256 do master baixado contra ele antes do primeiro Cenário. Nome e caminho fixos porque quem lê recebe o path por argumento, como todo o resto deste layout.

**Emenda: `status/` carrega progresso e desfecho, não presença.** São dois objetos por Instância de encode, os dois escritos pelo `run_all.sh` e lidos pelo Orquestrador a cada 5 minutos sem SSH (ADR-0010).

`status/{instance_type}_progress` é **sobrescrito depois de cada Execução**, entre runs — um `jq -n` e um `s3 cp` de poucos bytes, nunca durante um encode, pela mesma razão que o upload dos artefatos espera o fim do run. Carrega `instance_id`, `block_index` e `block_count`, `run_index` e `run_count` (os índices 1-based, com o total ao lado), o `scenario_id` da Execução que acabou, `runs_total`, `runs_failed`, `elapsed_seconds` e `written_at` em ISO-8601 com offset. O total da fatia contra o qual o Orquestrador o lê vem do plano que ele mesmo subiu para `scenarios/`.

`status/{instance_type}_done` deixa de ser uma linha de `date` e passa a ser JSON com `instance_id`, `finished_at`, `runs_total`, `runs_failed`, `capped` (booleano) e `exit_status`. A chave não muda, o IAM não muda — o papel `encode` já tem `PutObject` em `status/*` (ADR-0016) —, e ele continua sendo o último objeto que a Instância escreve quando o laço termina, inclusive no caminho do teto da ADR-0012. Morto por sinal, o laço não escreve marcador nenhum: é por essa ausência que o Orquestrador distingue uma Instância que acabou de uma que morreu no meio de um run.

O `instance_id` está nos dois porque o `DeleteObject` da matriz da ADR-0016 não alcança `status/`: numa retomada os objetos da tentativa anterior sobrevivem no bucket, e uma instância relançada pareceria pronta na hora zero. Presença deixa de ser sinal; identidade passa a ser. Duas alternativas foram rejeitadas: `tail` do log por SSH, que exige uma conexão por poll e parsing de texto livre, e contar objetos em `runs/`, que devolve chave e tamanho e não diz de qual arquitetura veio cada `runs/{run_id}/`.

Os prefixos são **contrato**, não convenção: a matriz IAM (ADR-0016) escopa permissões por prefixo, e cada bootstrap recebe o path exato do que consome — a instância nunca decide path, coerente com "a seleção mora no lado inteligente" (ADR-0019). Dado de runtime (`scenarios.json`, `quality_plan.json`) viaja **sempre via S3 pro work dir** (ADR-0018), nunca por SCP — um mecanismo, dois consumidores (encode e Juiz).

## Fluxo de upload

Cada `run_scenario.sh`, ao terminar uma Execução, faz:
```
aws s3 cp runs/{run_id}/ s3://bucket/runs/{run_id}/ --recursive
```

Isso inclui: `meta.json`, `time.json`, `perf.json`, `pidstat.txt` (era `pidstat.csv`; emenda da ADR-0007), `ffmpeg.log`, `output.mkv`, `output.sha256`.

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
