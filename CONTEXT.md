# Contexto do Projeto — TCC: Comparação de Transcoding ARM vs. x86

Pesquisa empírica que compara o desempenho de *workloads* de transcoding de vídeo (FFmpeg) entre arquiteturas ARM (AWS Graviton) e x86 (Intel Xeon / AMD EPYC) em instâncias EC2, considerando tempo, throughput, utilização de recursos, qualidade do vídeo gerado (SSIM/VMAF) e custo operacional.

## Convenção de linguagem

- **Código, identificadores, scripts, commits, logs:** inglês.
- **Artigo (TeX) e este glossário:** português.
- Cada termo abaixo lista o equivalente em inglês entre backticks — é o nome canônico usado no código.

## Hierarquia de documentação

Quando houver discrepância entre **o artigo (`.tex`) e os ADRs** deste repositório, **os ADRs prevalecem**. O artigo está em desenvolvimento contínuo e pode conter decisões antigas ou ainda em revisão; os ADRs refletem o estado decidido e justificado do projeto. Ao detectar inconsistência: ajustar o artigo, nunca o ADR.

Este `CONTEXT.md` segue a mesma regra: prevalece sobre o artigo na questão de terminologia canônica.

## Glossário

**Pipeline** (`pipeline`):
O arcabouço experimental que provisiona instâncias, executa o transcoding via FFmpeg, coleta métricas e armazena resultados de forma reproduzível.
_Não confundir com_: pipeline de transcoding de produção (ABR, segmentação por GOP), que aparece só como motivação no referencial teórico, e nem com a invocação isolada do FFmpeg (que é uma **Execução**, abaixo).

**Cenário** (`scenario`):
Uma combinação fixa de parâmetros do experimento: `codec × input_res × output_res × vídeo × instância`. É o eixo X da análise.
_Avoid_: combinação, configuração, condição.

**Execução** (`run`):
Uma única invocação do FFmpeg sobre um cenário. Produz uma linha de métricas brutas.
_Avoid_: rodada, trial.

**Replicação** (`replication`):
Re-execução do mesmo cenário pra estimativa estatística. Cada cenário tem 6 execuções na mesma instância EC2; a primeira é descartada (warm-up) e as 5 seguintes são as replicações reportadas.
_Avoid_: repetição.

**Experimento** (`experiment`):
A campanha completa: todos os cenários × todas as replicações.
_Avoid_: estudo, teste.

**Master** (`master`):
Vídeo de entrada antes de qualquer transcoding pela pipeline. Existe em três resoluções (4K, 1080p, 720p), todas derivadas do source 4K canônico por downscale Lanczos lossless (FFV1).
_Avoid_: source (ambíguo), input bruto.

**Pass de qualidade** (`quality_pass`):
Etapa pós-encode em que se calculam métricas de qualidade (SSIM/VMAF) sobre uma **amostra estratificada** dos outputs. Sua finalidade é **validar a premissa** de que arquiteturas distintas produzem qualidade equivalente sob params fixos (encoder, preset, CRF, thread count), **não** tratar qualidade como variável dependente do experimento. Executado em instância separada das de encode (**Juiz**) pra eliminar variância arquitetural na própria computação da métrica.
_Avoid_: avaliação de qualidade (sugere variável dependente), validação visual (sugere humanos).

**Juiz** (`judge`):
Instância EC2 dedicada ao **Pass de qualidade**. Mesma arquitetura/instância para todos os outputs amostrados, independentemente da instância que gerou cada output. Isola o cálculo de SSIM/VMAF da variável arquitetural.
_Avoid_: avaliador, validador.

## Relacionamentos

- Um **Experimento** consiste em N **Cenários** × 5 **Replicações** reportadas
- Um **Cenário** é uma tupla de parâmetros; cada **Execução** materializa um Cenário
- Cada **Cenário** consome o **Master** que corresponde à sua `input_res`
- A **Pipeline** orquestra Experimentos: prepara Masters, executa Cenários, coleta métricas, e executa o **Pass de qualidade** no **Juiz** sobre uma amostra dos outputs

## Exemplo de diálogo

> **Pesquisador:** "Pro cenário `(libsvtav1, 4K→1080p, BBB, c7g.xlarge)`, quantas execuções contam pra análise?"
> **Eng:** "5. A pipeline roda 6 execuções no mesmo c7g.xlarge; descarta a primeira (warm-up); as 5 seguintes viram replicações reportadas."
> **Pesquisador:** "E o master 4K e o master 1080p são arquivos diferentes?"
> **Eng:** "Sim. O master 1080p é gerado uma vez, antes do experimento começar, por downscale Lanczos lossless do master 4K. Cada cenário consome o master que corresponde à sua `input_res`."

## Ambiguidades resolvidas

- **"pipeline"** era usado no artigo em três sentidos (experimental, transcoding de produção, comando FFmpeg) — resolvido: no projeto, **Pipeline** refere-se exclusivamente ao arcabouço experimental.
- **"workload"** aparecia indistinto entre "execução individual" e "campanha completa" — resolvido: usamos **Execução** e **Experimento** respectivamente.
- **"source"** era usado pra denotar tanto o arquivo de origem 4K canônico quanto o input de uma execução — resolvido: usamos **Master** pro input de qualquer execução (incluindo 1080p e 720p, derivados); o source 4K canônico não tem termo próprio porque não aparece em código (é só artefato de bootstrap).
- **"qualidade do vídeo gerado"** apareceu no artigo como variável dependente ao lado de tempo/CPU/custo — resolvido: com encoder e CRF fixos, qualidade é esperada invariante entre arquiteturas; entra como **validação amostral da premissa** via **Pass de qualidade** rodando no **Juiz**, não como variável dependente. ADR a criar quando os parâmetros de amostragem estiverem fechados.
