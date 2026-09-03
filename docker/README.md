# docker/

O **ambiente de medição** (ADR-0017/0018): uma imagem só, servindo encode e Juiz.
Dois Dockerfiles divergiriam, e a `libvmaf` que só o Juiz usa custa alguns
minutos de build a mais — barato perto de manter dois ambientes em sincronia.

A imagem carrega **só o ambiente**: FFmpeg com `libx264`, `libx265` e
`libsvtav1` compilados from source com `-march=native`, mais `libvmaf`, `perf`,
`sysstat` (`pidstat`), `/usr/bin/time`, `jq` e a AWS CLI v2. Os `run_*.sh` e o
work dir chegam por bind-mount na hora do `docker run`, de duas proveniências
distintas (ADR-0018): script é código versionado, `scenarios.json` é dado de
runtime.

    docker build -t transcoding-bench docker/
    docker run --rm transcoding-bench ffmpeg -hide_banner -encoders

Na campanha o build roda **na própria instância**, logo depois do `git clone`, e
fora do CI (ADR-0013). O que o CI cobre é o `hadolint`; o resto é o build local e
a camada de aceite manual da Spec 2.

## Pins

Nada aqui aponta para branch: o `master` de qualquer um destes projetos faria o
mesmo Dockerfile produzir ambientes diferentes em dias diferentes, que é o
oposto do que a ADR-0008 quer.

| Componente | Pin | Forma |
|---|---|---|
| Imagem base | `ubuntu:24.04@sha256:33ceb719…` | digest de índice multi-arch |
| FFmpeg | `n8.1.2` | tag |
| x264 | `b35605ac…` | commit (o projeto não publica tags) |
| x265 | `4.2` | tag |
| SVT-AV1 | `v4.1.0` | tag |
| libvmaf | `v3.1.0` | tag |
| AWS CLI | `2.36.38` | versão no instalador oficial |

São `ARG` com default, e o default é o que a campanha reporta: trocar de versão
no meio dela invalida a comparação cross-arch.

O FFmpeg está no `8.1.x` e não no `9.0.x`, e cada encoder na última versão
estável anterior ao lançamento desse `8.1.2`: é a combinação com que aquele
release conviveu. Um major de encoder mais novo que o FFmpeg pinado é onde as
remoções de API aparecem.

## O arquivo de versões

O build grava `/opt/transcoding-bench/versions.json` (também em `$VERSIONS_FILE`)
a partir dos mesmos `ARG` com que compilou:

```json
{
  "base_image": "ubuntu:24.04@sha256:33ceb719…",
  "ffmpeg": "n8.1.2",
  ...
}
```

Uma chave por componente da tabela acima. O `run_scenario.sh` o copia
**verbatim** para o campo `versions` do `meta.json` (decisão D3 da Spec 2) —
objeto de string para string, como o modelo de `analysis/run_meta.py` exige.
