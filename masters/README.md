# masters/

O papel que roda **uma vez**, antes de qualquer Instância de encode subir: ele
produz os seis Masters que o Experimento inteiro consome (ADR-0004/0014). Em
duas camadas, como o encode (ADR-0017/0018): `bootstrap.sh` no host da instância
de preparação e `prepare.sh` dentro da imagem de medição.

O diretório coexiste com o `masters/sources/` local do pesquisador — as fontes
baixadas à mão para a inspeção da ADR-0004 —, que a allowlist do `.gitignore`
mantém fora do histórico.

## Host: `bootstrap.sh`

Docker, AWS CLI v2, work dir e o `docker build` da imagem de medição.

    bash masters/bootstrap.sh --work-dir ~/work

É o que o user-data fino chama depois do clone no SHA (ADR-0017/0021). Sem
`perf` e sem `perf_event_paranoid`, que o bootstrap do encode instala e ajusta:
aqui não se mede nada — a instância baixa, remuxa, escala e sobe, e o contador de
PMU não entra em nada disso. O bootstrap **para no build**; quem dá o
`docker run` é o `prepare-masters` do Orquestrador, por SSH bloqueante.

## Container: `prepare.sh`

    docker run --rm \
        -v <clone>/masters:/masters:ro -v ~/work:/work \
        transcoding-bench \
        bash /masters/prepare.sh \
            --plan "$(cat masters.json)" --bucket "$bucket" --work-dir /work

Dentro da imagem porque o FFmpeg do remux e do Lanczos tem que ser o binário
pinado da ADR-0008 — o mesmo que a Execução usa. O plano chega por **argv**, e
não pelo S3: o papel `masters` não tem `GetObject` (ADR-0016), e o plano é o JSON
de dois vídeos que o `generate_masters_plan.py` projetou da spec. O arquivo de
versões vem da imagem (`VERSIONS_FILE`), como no `run_scenario.sh`.

Ele **copia, nunca deriva** (ADR-0019): nome, URL, sha256, geometria, codec,
`pix_fmt`, cadência e contagem de frames de cada Master chegam no plano, e nada é
reconstruído do nome do arquivo nem lido do `experiment.toml`. A ordem:

1. por vídeo — `curl` da URL, `unzip`, `sha256sum` contra o plano, remux do 4K
   com `-c copy` (sem re-encode) e, por tier derivado, o downscale
   `-vf scale=W:H:flags=lanczos -c:v ffv1 -pix_fmt … -an`;
2. `ffprobe` em JSON dos seis, e a comparação de largura, altura, `codec_name`,
   `pix_fmt`, frame rate e contagem de frames com o plano;
3. os seis uploads, por `s3 cp` objeto a objeto;
4. o manifesto, por último.

As três fases são separadas porque a segunda é um **gate**: qualquer divergência
sai não-zero nomeando o Master e o campo, e nenhum dos seis chegou ao bucket
ainda — um Master errado nunca vira as seis Execuções medidas sobre a entrada
errada. O sha256 da fonte para ainda antes, no vídeo em que o download truncou.

O `-an` do derivado é o mesmo do encode: o Master derivado não carrega áudio que
a Execução descartaria. O remux não o leva — o Master 4K é a versão canônica
intacta, e o que sobra do container é dado da fonte, não escolha da preparação.

**Duas coisas não saem do plano.** O `lanczos` é literal aqui: quem o fixa é a
ADR-0004, e o plano só carrega o que varia de Master para Master. E a contagem de
frames sai de `-count_packets`/`nb_read_packets`, não de `nb_frames` (que o
Matroska devolve vazio) nem de `-count_frames` (que decodificaria os seis
Masters inteiros para reproduzir um número que o plano já tem).

## O manifesto

`masters/manifest.json` é o contrato entre este papel e quem vem depois
(ADR-0019): o bootstrap do encode tira dele o nome e o sha256 de cada objeto a
baixar, o `validate_manifest.py` do orquestrador o confere contra a spec, e o
pesquisador o lê no gate humano da ADR-0012. O bash o monta com `jq` a partir do
que observou — `schema_version`, o `versions` da imagem copiado verbatim, as
`sources` com tamanho e sha256 do download, e os `masters` com os onze campos —,
e quem o lê é Python estrito. A forma está no `orchestrator/README.md`.

Por último, sempre: o manifesto existir é o que diz que os seis subiram.

## Verificação

Não há TDD aqui (ADR-0017): quem exercita estes scripts é o `smoke/`, que roda o
`prepare.sh` de verdade com `curl`, `unzip`, `ffmpeg`, `ffprobe` e `aws`
shimados — sem Docker, sem AWS e sem FFmpeg — e confere o argv do remux e de cada
downscale contra o `config/experiment.toml`, os seis objetos com o manifesto por
último, e o manifesto que o bash escreveu pela CLI do checker.

    .venv-smoke/bin/python -m pytest smoke/

`shellcheck` e `shfmt` rodam no pre-commit, que é a casa oficial dos linters.
