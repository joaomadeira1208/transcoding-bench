# Orquestração do Pass de qualidade

O Pass de qualidade (ADR-0005) é orquestrado em duas etapas: **triage no orquestrador** e **execução no Juiz**.

## Fluxo

1. As 3 instâncias de encode terminam → orquestrador detecta (SSH + marcador S3)
2. Orquestrador termina as instâncias de encode (`aws ec2 terminate-instances`)
3. Orquestrador roda `quality_triage.py` na instância do Orquestrador:
   - Baixa os 810 `output.sha256` do S3
   - Agrupa por `(codec × pair × video × rep)` → 270 grupos de 3 outputs (um por arch)
   - Compara hashes dentro de cada grupo
   - Seleciona amostra metodológica fixa (~6–10 outputs estratificados por codec × output_res)
   - Gera `quality_plan.json`: lista de outputs a processar + paths dos masters de referência
4. Orquestrador cria o Juiz (`aws ec2 run-instances`)
5. SSH pro Juiz: `bash run_quality.sh --plan quality_plan.json`
6. Juiz baixa do S3 os `.mkv` listados no plano + masters de referência
7. Juiz roda VMAF/SSIM, sobe resultados pro S3
8. Orquestrador detecta término, termina o Juiz

## Juiz como decisão operacional

O Juiz é uma instância compute-optimized (tipo exato a definir no momento do experimento) usada exclusivamente pro Pass de qualidade. É decisão **operacional**, não experimental — basta documentar qual instância foi usada pra reprodutibilidade. O requisito metodológico é que seja **a mesma instância pra todos os outputs amostrados**, isolando variância arquitetural do cálculo da métrica.

Estimativa de tempo do Pass: ~2–4h (VMAF de ~10–20 outputs, mix de resoluções). Custo: ~$0.50–1.00.

## Bootstrap dos masters

Os masters (4K, 1080p, 720p × 2 vídeos = 6 arquivos) são gerados na **instância do Orquestrador** como etapa de bootstrap antes do experimento. Downscale Lanczos lossless (FFV1) do master 4K canônico, com validação automática via `ffprobe` (resolução, codec, duração). Uploadados pro S3 uma vez e consumidos por todas as instâncias.

## Consolidação do Parquet

Acontece **na máquina local** do pesquisador, pós-experimento. Um script `consolidate.py` faz `aws s3 sync` dos JSONs/CSVs dos raw dirs e gera a tabela Parquet analítica. Rodar localmente permite iteração rápida (re-consolidar, ajustar colunas, derivar métricas) sem depender de instância.

## Considered Options

- **Juiz decide sozinho o que processar** — rejeitado: hash-first triage é lógica de decisão (agrupamento, comparação, seleção de amostra) — pertence ao orquestrador Python, não a um shell script no Juiz. Mantém o princípio "Python decide, shell executa".
- **Juiz provisionado no início junto com instâncias de encode** — rejeitado: ficaria idle ~46h, desperdiçando ~$8. Provisionado sob demanda via AWS CLI quando o Pass começa.
- **Consolidação do Parquet na instância do Orquestrador** — rejeitado: o Parquet é o dataset de análise — o pesquisador vai iterar nele muitas vezes (plots, tabelas, artigo). Manter localmente é mais natural.
- **Bootstrap dos masters na máquina local** — rejeitado: upload de masters FFV1 4K (dezenas de GB) depende da conexão doméstica. Na instância do Orquestrador, download do source + geração + upload pro S3 usa rede AWS interna.

## Consequences

- O Juiz é a última instância a rodar e a última a ser destruída. Após ele, só resta o bucket S3 e a instância do Orquestrador.
- A instância do Orquestrador tem dupla função: orquestração do experimento + bootstrap dos masters. Ambas são one-shot e não concorrem.
- Limpeza seletiva dos `.mkv` no S3 acontece após o Pass: orquestrador deleta os outputs que não fazem parte da amostra retida (ADR-0007).
