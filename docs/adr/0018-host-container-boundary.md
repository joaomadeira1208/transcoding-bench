# Fronteira host/container e uso do Docker no encode/Juiz

A Execução roda **dentro do container**. `run_all.sh`/`run_scenario.sh` (encode) e `run_quality.sh` (Juiz) executam no namespace do container; a instrumentação (`perf stat`, `pidstat`, `/usr/bin/time`), o FFmpeg e o `aws cli` vivem todos lá dentro. Isso resolve o ponto que a ADR-0016 deixou em aberto: como o `aws s3 cp` roda dentro do container, o **IMDS hop limit do encode = 2** no `run-instances`.

A história de reprodutibilidade fica limpa: o ambiente de encode inteiro **é a imagem Docker pinada** — não um binário extraído rodando contra libs do host. Para um workload CPU-bound, o PMU conta ciclos/instruções independente de namespace, então a integridade da medição não é afetada por rodar dentro do container; `perf` envolve o FFmpeg direto, bastando `--cap-add=PERFMON` (ou equivalente) e o `perf_event_paranoid` já permissivo no host (ADR-0015/0006).

## Imagem = ambiente; scripts e dados montados (bind-mount), de proveniências distintas

A imagem carrega **só o ambiente de medição**: FFmpeg `-march=native` + os três encoders + `libvmaf`, mais `perf`/`sysstat`/`time`/`aws-cli`/`jq`. O que o container vê montado (`docker run -v`), não baked na imagem, vem de **duas proveniências distintas que não compartilham diretório**:

- **Os `run_*.sh` — do diretório clonado no host** (git, commit hash). São *source* versionado, imutável por commit.
- **O `scenarios.json` — de um work dir de runtime fora do clone** (ex.: `~/work/`). É *dado de runtime*: gerado pelo orquestrador a partir do `experiment.toml` + seed, **pré-fatiado por arquitetura** e entregue via S3 (ADR-0019/0011); o `bootstrap.sh` do encode faz `aws s3 cp` pro work dir antes do `docker run`. Nunca entra na árvore do clone — é runtime do nascimento ao consumo. Esse mesmo work dir recebe os `runs/{run_id}/` antes do upload (ADR-0007/0011).

Misturar as duas proveniências num só diretório esconderia que uma é código versionado e a outra é dado de runtime — a separação que o scaffolding preza em todo lugar (config vs orchestrator, imagem vs scripts).

- **Uma imagem só** serve encode e Juiz (encoders + libvmaf juntos); cada papel monta o script que lhe cabe. Coerente com "um Dockerfile" (ADR-0008/0013).
- **Reprodutibilidade em dois eixos separados:** o *ambiente de medição* é reproduzido pela imagem pinada; o *procedimento* (os scripts) pelo git (commit hash) + versões registradas no `meta.json`. Montar o script não borra nenhum eixo.

## Considered Options

- **Build no Docker + binário estático extraído, rodando no host** — rejeitado: exige build estático e `docker cp`, e parte a história de reprodutibilidade ("binário do container, libs de runtime do host"). A integridade de medição seria igual; não compensa a fragilidade.
- **Híbrido: `perf` no host envolvendo `docker run`** — rejeitado: `perf` contando através da fronteira de namespace/cgroup é frágil, justo na métrica que mais importa (IPC/cache/branch).
- **Scripts baked na imagem** — rejeitado: cada ajuste num `run_*.sh` exigiria rebuild (~10–20 min, ADR-0013) — caro ao depurar um cenário que falha na hora 30 de 46. Bind-mount deixa a imagem imutável e os scripts quentes.
- **Duas imagens (encode vs Juiz)** — rejeitado: duplicação sem ganho; uma imagem com encoders + libvmaf cobre os dois.

## Consequences

- O `run-instances` do encode (e do Juiz, se rodar `aws cli` no container) precisa de `--metadata-options HttpPutResponseHopLimit=2`. Fecha o ponto aberto da ADR-0016.
- O `docker run` do encode tem **duas fontes de `-v`**: `<clone>/encode/` (scripts, do git, read-only) e o work dir de runtime (`scenarios.json` de entrada + `runs/` de saída). O `bootstrap.sh` (host) cria o work dir e baixa o `scenarios.json` do S3 antes do `docker run`.
- A imagem só é rebuildada quando o ambiente muda (versões pinadas de FFmpeg/encoders/libvmaf) — raro. Ajustes de script não disparam rebuild.
- `run_scenario.sh` recebe os params como **objeto JSON** (a entrada de run do `scenarios.json`) e os lê com `jq` (ADR-0019), por isso `jq` está na imagem.
- O container precisa da capability de PMU pro `perf`; o `bootstrap.sh` do encode (host) garante `perf_event_paranoid` permissivo.
