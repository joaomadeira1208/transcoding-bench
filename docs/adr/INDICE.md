# Índice de ADRs — Transcoding Bench

## Experimento

Decisões sobre o desenho experimental: o que medir, como medir, com quais inputs, em quais instâncias.

| ADR | Título | Descrição |
|---|---|---|
| [0001](0001-instance-types.md) | Tipos de instância EC2 | Escolha de c7g/c7i/c7a xlarge (4 vCPU) pra isolar arquitetura como variável independente |
| [0002](0002-codec-encoder-configuration.md) | Configuração dos encoders | libx264/libx265/libsvtav1 com presets, CRF, GOP, threading fixos |
| [0003](0003-experimental-design.md) | Desenho experimental | Cenário, execução, replicação: 6 runs por cenário (1 warm-up + 5 reportadas), ordem randomizada |
| [0004](0004-input-preparation.md) | Preparação dos inputs | Masters 4K/1080p/720p via downscale Lanczos FFV1; 9 pares input→output |
| [0005](0005-quality-measurement.md) | Medição de qualidade | Qualidade como validação amostral (não variável dependente); hash-first triage; Juiz separado |
| [0006](0006-performance-metrics-collection.md) | Coleta de métricas de desempenho | time -v, perf stat, pidstat 1 Hz, FFmpeg stderr; IPC/cache/branch como indicadores-chave |
| [0007](0007-raw-data-schema.md) | Schema do dado bruto | Diretório runs/{run_id}/ com artefatos raw + Parquet consolidado como projeção analítica |
| [0008](0008-ffmpeg-build-strategy.md) | Build do FFmpeg | Compilação from source com -march=native via Docker multi-arch pra exercer SIMD paths nativos |

## Arquitetura

Decisões sobre como a pipeline é construída: tooling, orquestração, storage, resiliência.

| ADR | Título | Descrição |
|---|---|---|
| [0009](0009-tooling-and-languages.md) | Tooling e linguagens | Terraform pra infra estática, Python+AWS CLI pra orquestração, shell dentro das instâncias |
| [0010](0010-orchestration-model.md) | Modelo de orquestração | Instância de controle (t3.micro); instâncias auto-dirigem; SSH + marcador S3; mesma seed |
| [0011](0011-storage-and-transport.md) | Storage e transporte | S3 como storage central; upload de todos artefatos após cada run; limpeza seletiva pós-Pass |
| [0012](0012-resilience-and-safeguards.md) | Resiliência e salvaguardas | Timeout por run (4h), timeout total (72h), budget alert ($150), retomada semi-automática |
| [0013](0013-docker-build-strategy.md) | Bootstrap e build do Docker | Git clone em todas as instâncias; Docker build local no encode; ~$0.18 total; garante -march=native correto |
| [0014](0014-quality-pass-orchestration.md) | Orquestração do Pass de qualidade | Triage no orquestrador, execução no Juiz; bootstrap de masters no controle; Parquet local |
| [0015](0015-aws-infrastructure-config.md) | Configuração da infra AWS | us-east-1; subnets públicas; Ubuntu 24.04 LTS; Docker/AWS CLI instalados no bootstrap |
