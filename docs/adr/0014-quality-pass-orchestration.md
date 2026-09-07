# Orquestração do Pass de qualidade

O Pass de qualidade (ADR-0005) é orquestrado em duas etapas: **triage no orquestrador** e **execução no Juiz**.

## Fluxo

1. As 3 instâncias de encode terminam → orquestrador detecta (SSH + marcador S3)
2. Orquestrador termina as instâncias de encode (`aws ec2 terminate-instances`)
3. Orquestrador roda `quality_triage.py` na instância do Orquestrador:
   - Lista `runs/` e baixa os `meta.json` (~972 arquivos de KBs)
   - Filtra `warmup == false` + `exit_code == 0` e deduplica por `scenario_id` (último `started_at` vence — ADR-0019)
   - Baixa os 810 `output.sha256` correspondentes
   - Agrupa por `(codec × pair × video × rep)` → 270 grupos de 3 outputs (um por arch)
   - Compara hashes dentro de cada grupo
   - Seleciona amostra metodológica fixa (~6–10 outputs estratificados por codec × output_res)
   - Gera `quality_plan.json` (outputs a processar + paths dos masters de referência) e sobe pra `s3://bucket/quality/plan.json` (layout-contrato, ADR-0011)
4. Orquestrador cria o Juiz (`aws ec2 run-instances`)
5. `bootstrap.sh` do Juiz baixa `quality/plan.json` pro work dir — mesmo mecanismo do `scenarios.json` no encode (ADR-0018); orquestrador dispara via SSH: `bash run_quality.sh --plan <work dir>/plan.json`
6. Juiz baixa do S3 os `.mkv` listados no plano + masters de referência
7. Juiz roda VMAF/SSIM, sobe resultados pro S3
8. Orquestrador detecta término, termina o Juiz

## Juiz como decisão operacional

O Juiz é uma instância compute-optimized (tipo exato a definir no momento do experimento) usada exclusivamente pro Pass de qualidade. É decisão **operacional**, não experimental — basta documentar qual instância foi usada pra reprodutibilidade. O requisito metodológico é que seja **a mesma instância pra todos os outputs amostrados**, isolando variância arquitetural do cálculo da métrica.

Estimativa de tempo do Pass: ~2–4h (VMAF de ~10–20 outputs, mix de resoluções). Custo: ~$0.50–1.00.

## Bootstrap dos masters

Os masters (4K, 1080p, 720p × 2 vídeos = 6 arquivos) são gerados na **instância do Orquestrador** como etapa de bootstrap antes do experimento. Downscale Lanczos lossless (FFV1) do master 4K canônico, com validação automática via `ffprobe` (resolução, codec, duração). Uploadados pro S3 uma vez e consumidos por todas as instâncias.

### Emenda: execução própria, instância própria, gate humano

O parágrafo acima deixava duas coisas implícitas, e as duas mudam.

**É uma execução separada da campanha, com o pesquisador no meio.** A preparação dos masters é disparada sozinha, termina sozinha, e a campanha só começa depois que o pesquisador **aprova** o que ela produziu — é o gate humano da ADR-0012 aplicado antes da primeira instância de encode subir. Dois dias de compute sobre um master errado (geometria trocada, download truncado, fps diferente do pinado) é o custo que o gate evita, e nenhuma validação automática substitui olhar o resultado uma vez.

**Não roda na t3.micro; roda numa instância efêmera dedicada.** O Orquestrador lança uma `c7g.xlarge` só para este passo — o mesmo `run-instances`/`terminate-instances` que ele já usa para as instâncias de encode (ADR-0010/0016) — e a termina ao fim. A t3.micro tem 1 GB de RAM e 2 vCPUs burstable, e o passo decodifica H.264 4K e encoda FFV1 por ~20 minutos de vídeo, deixando 50–70 GB de FFV1 em disco; fazer isso nela exigiria inflar o disco de uma instância que persiste dois dias e aceitar risco de OOM. A arquitetura da instância de preparação é irrelevante para o Experimento: os masters são produzidos **uma vez** e consumidos pelas três arquiteturas, então nenhuma diferença de SIMD path do scaler entra na comparação. Custo: 1–2 h a ~$0.15/h, menos de $0.50.

O passo roda **dentro da imagem de medição** (ADR-0018): o FFmpeg que faz o remux, o Lanczos e o FFV1 é o mesmo binário pinado da ADR-0008, e as versões que o `versions.json` da imagem registra valem também para a proveniência dos masters.

O que o passo faz, em ordem:

1. baixa os dois sources da Blender pelas URLs da ADR-0004 e confere o **sha256** de cada um depois do `unzip` — download truncado ou arquivo trocado param aqui;
2. reempacota os dois 4K em Matroska com `-c copy` (sem re-encode) como `bbb_2160p.mkv` e `tos_2160p.mkv`;
3. gera os quatro derivados (`*_1080p.mkv`, `*_720p.mkv`) por Lanczos + FFV1 nas geometrias da ADR-0023;
4. valida cada um dos seis com `ffprobe` — geometria, codec, `pix_fmt`, frame rate e contagem de frames — contra o que as ADRs 0004 e 0023 fixam, e falha alto na primeira divergência;
5. sobe os seis para `masters/` e escreve `masters/manifest.json` (ADR-0011): nome, tamanho, sha256 e as propriedades observadas pelo `ffprobe` de cada master, mais as versões da imagem que os produziu.

O que o pesquisador confere antes de aprovar: o manifesto contra as tabelas das ADRs 0004 e 0023; a listagem de `masters/` com tamanhos iguais aos do manifesto; e a instância de preparação terminada. As instâncias de encode, por sua vez, conferem o sha256 do master que baixaram contra o manifesto **antes** do primeiro Cenário, e param se divergir — um master corrompido no download nunca vira 6 Execuções medidas sobre a entrada errada.


## Consolidação do Parquet

Acontece **na máquina local** do pesquisador, pós-experimento. Um script `consolidate.py` faz `aws s3 sync` dos JSONs/CSVs dos raw dirs e gera a tabela Parquet analítica. Rodar localmente permite iteração rápida (re-consolidar, ajustar colunas, derivar métricas) sem depender de instância.

## Considered Options

- **Juiz decide sozinho o que processar** — rejeitado: hash-first triage é lógica de decisão (agrupamento, comparação, seleção de amostra) — pertence ao orquestrador Python, não a um shell script no Juiz. Mantém o princípio "Python decide, shell executa".
- **Juiz provisionado no início junto com instâncias de encode** — rejeitado: ficaria idle ~46h, desperdiçando ~$8. Provisionado sob demanda via AWS CLI quando o Pass começa.
- **Consolidação do Parquet na instância do Orquestrador** — rejeitado: o Parquet é o dataset de análise — o pesquisador vai iterar nele muitas vezes (plots, tabelas, artigo). Manter localmente é mais natural.
- **Bootstrap dos masters na máquina local** — rejeitado: upload de masters FFV1 4K (dezenas de GB) depende da conexão doméstica. Na instância do Orquestrador, download do source + geração + upload pro S3 usa rede AWS interna.
- **Bootstrap dos masters na t3.micro do Orquestrador** — era o texto original; superado pela emenda acima: 1 GB de RAM e disco de dois dias para 50–70 GB de FFV1, por um passo que uma instância efêmera faz em 1–2 h por menos de $0.50.
- **Orquestrador maior, para absorver o passo** — rejeitado: a diferença de preço vale durante as ~46 h+ de campanha (uma `c7g.xlarge` ligada dois dias são ~$7), por um passo de 1–2 h.
- **Preparação emendada na campanha, sem gate** — rejeitado: a validação automática pega o que se previu (sha256, geometria, fps); o que não se previu custa dois dias de compute. Olhar o manifesto uma vez é barato.

## Consequences

- O Juiz é a última instância a rodar e a última a ser destruída. Após ele, só resta o bucket S3 e a instância do Orquestrador.
- A instância do Orquestrador tem dupla função: orquestração do experimento + bootstrap dos masters. Ambas são one-shot e não concorrem. **Emenda:** o bootstrap passa a ser lançado pelo Orquestrador numa instância efêmera, e a campanha ganha uma fase anterior a ela, com gate humano entre as duas.
- A instância de preparação é um quarto ator com papel IAM próprio (ADR-0016) e disco próprio (ADR-0015); o `manifest.json` é objeto de contrato do layout (ADR-0011).
- Limpeza seletiva dos `.mkv` no S3 acontece após o Pass: orquestrador deleta os outputs que não fazem parte da amostra retida (ADR-0007), incluindo os `.mkv` de warm-up — sobem uniformemente (ADR-0011) e nunca são amostrados. Requer o `s3:DeleteObject` escopado a `runs/*` (ADR-0016).
