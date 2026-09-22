# judge/

Bash do Juiz (ADR-0017/0018), em duas camadas como o `encode/` e o `masters/`: no
**host**, os dois scripts que põem a máquina de pé e dão o `docker run`; **dentro
do container**, o `run_quality.sh`, que executa o Pass de qualidade.

É tão burro quanto o encode. O que julgar, com que modelo e contra que geometria
chega decidido no `quality/plan.json` que o triage escreveu (ADR-0025): este
diretório percorre **todo** output do plano, na ordem do arquivo e sem predicado
de seleção. A seleção mora no Python (ADR-0019).

## O host

O `bootstrap.sh` é o gordo do par user-data/bootstrap (ADR-0013/0017): instala
Docker e a AWS CLI v2, cria o work dir, builda a **mesma** imagem de medição do
encode e baixa o plano e o manifesto **pelas chaves que recebeu** — a Instância
nunca decide path (ADR-0011/0019).

    bash judge/bootstrap.sh \
        --work-dir /home/ubuntu/work --bucket "$bucket" \
        --plan-key quality/plan.json --manifest-key masters/manifest.json \
        --masters-prefix masters/

Sem `perf` e sem `kernel.perf_event_paranoid`, que o bootstrap do encode instala
e ajusta: aqui não se mede tempo nem contador nenhum — o Juiz decodifica dois
vídeos e computa VMAF e SSIM, e a PMU não entra em nada disso. Pela mesma razão o
`launch_container.sh` não dá `--cap-add=PERFMON`.

A imagem é a mesma porque o `libvmaf` que calcula a métrica tem de ser o binário
pinado da ADR-0008 — e é a mesma que o `versions.json` do `judge.json` registra
(ADR-0018).

### O `fetch_masters.sh` vem do `encode/`

O último passo do bootstrap é invocar `encode/fetch_masters.sh` **pelo caminho do
clone**, e é a primeira vez que um papel chama script de outro:

    bash <clone>/encode/fetch_masters.sh \
        --manifest /home/ubuntu/work/manifest.json --bucket "$bucket" \
        --prefix masters/ --dest /home/ubuntu/work/masters

O gate de sha256 de cada Master é um script só, de propósito. Uma cópia em
`judge/` seria a cópia que envelhece — um Master errado não aparece em número
nenhum, vira um VMAF plausível calculado contra a referência errada —, e mover o
script para um diretório comum quebraria a organização por papel da ADR-0017 por
um arquivo. Invocar pelo caminho do clone é o menor mal.

### O `launch_container.sh`

É o `docker run`, e é o comando que o Orquestrador dispara por SSH. Ele monta as
duas proveniências que a ADR-0018 não deixa se misturarem — `<clone>/judge`
read-only em `/opt/judge`, o work dir de runtime em `/work` — e repassa ao
`run_quality.sh` os argumentos que recebeu. O `--plan` é o **nome** do plano
dentro do work dir: o caminho que o laço vê é o de dentro do container, e quem
monta o work dir é quem sabe onde ele fica.

    bash judge/launch_container.sh \
        --work-dir /home/ubuntu/work --plan plan.json --bucket "$bucket" \
        --commit "$sha" --instance-id "$id" --instance-type c7i.4xlarge \
        --threads 16 --output-timeout 14400 --total-timeout 86400

`--threads` e os dois timeouts são opcionais aqui e só viajam quando vêm; na
ausência valem os defaults do laço. Os dois scripts invocam o `docker` com `sudo`
pelo motivo do `encode/README.md`, e o tag da imagem é o `transcoding-bench` do
`docker/README.md`.

## O container

O `run_quality.sh` é o Pass inteiro.

    bash judge/run_quality.sh \
        --plan /work/plan.json --masters-dir /work/masters --work-dir /work \
        --bucket "$bucket" --commit "$sha" --instance-id "$id" \
        --instance-type c7i.4xlarge

Por output, em ordem: um `s3 cp` de `runs/{run_id}/output.{container}` para o
work dir; **uma** passada de FFmpeg com dois `-i` — o output e o Master — e o
`libvmaf`; os três artefatos escritos e subidos; e o `.mkv` local apagado, que é
o que faz o Pass da campanha caber em 100 GB (ADR-0015).

O `.mkv` é apagado em **todo** caminho, inclusive no do output que falhou: só o
do julgamento bem-sucedido sairia do disco, e um Pass com falhas encheria o
volume no meio.

`instance_id`, `instance_type`, `commit`, o bucket e o número de threads chegam
por argumento — nunca por consulta ao IMDS —, e o arquivo de versões vem da
imagem (`VERSIONS_FILE`). O default de `--threads` é o `nproc`, resolvido só
quando o argumento não veio.

### A passada do `libvmaf`

    ffmpeg -nostdin -y \
        -i /work/{run_id}.mkv \
        -i /work/masters/{master} \
        -filter_complex '[1:v]scale={W}:{H}:flags={scale_flags}[ref];
                         [0:v][ref]libvmaf=model=version={vmaf_model}
                                          :feature=name=float_ssim
                                          :n_threads={threads}
                                          :log_fmt=json
                                          :log_path=.../vmaf.json' \
        -f null -

A referência é o **Master da `input_res` do Cenário**, escalado para a geometria
de saída pelo **mesmo** `scale=W:H:flags=` que produziu o output. É o que mede o
encoder em vez da cadeia de downscale (ADR-0005) — e é por isso que o
`scale_flags` não tem declaração própria na tabela `[quality]`: ele é o do
`[encode]`, e uma segunda divergiria em silêncio do filtro real.

O output é a **primeira** entrada e a referência a segunda, que é a ordem em que
o filtro lê distorcido e referência. Trocá-la reporta um número que não é o do
encoder.

Tudo o que aparece entre chaves acima chega pelo plano: o bash copia, nunca
deriva (ADR-0019). O modelo do VMAF e a geometria são dado da definição, não
linha de script, e o SSIM sai da mesma passada porque é praticamente grátis
quando o VMAF já está rodando.

### Os três artefatos

Em `quality/results/{run_id}/`, onde o `run_id` é o do **representante** do
bitstream (ADR-0011/0025):

| arquivo | o que é |
|---|---|
| `vmaf.json` | o log do `libvmaf`, **cru**, com a série por frame de VMAF e SSIM |
| `judge.json` | o registro do julgamento: a entrada do plano mais a proveniência |
| `ffmpeg.log` | o stderr daquela passada |

O `vmaf.json` é o log cru e não um resumo porque é a série por frame que a
ADR-0005 manda persistir: as quatro métricas por output são projeção dele, e um
resumo tornaria qualquer pergunta nova um Pass novo.

O `judge.json` traz `schema_version`, a entrada do plano **projetada verbatim**,
`started_at`, `finished_at` e `exit_code` cunhados aqui, e `commit`,
`instance_id`, `instance_type` e `versions` vindos de argumento e da imagem. O
contrato dele tem os dois leitores do `meta.json` — o modelo `pydantic` de
`analysis/judgement.py` e o checador stdlib de `orchestrator/judgement_check.py`.

## Falha e salvaguardas

Um output falho **não interrompe o Pass**: o próximo acontece, e o status de
saída do `run_quality.sh` (1 se algum falhou ou o teto disparou, 0 se não) é o
que diz ao Orquestrador que há algo para olhar. Nunca é aviso — um Pass que
terminasse "com sucesso" escondendo outputs não medidos é exatamente o que a
ADR-0005 manda documentar. São quatro modos:

- download do `.mkv` que falha;
- FFmpeg com status não-zero, o timeout por output incluído;
- log do `libvmaf` ausente ou ilegível — um FFmpeg que sai zero sem deixar série
  por frame não julgou nada;
- upload do resultado que falha, porque um julgamento que não chegou ao bucket
  não existe para o leitor.

Os três primeiros entram no `judge.json` como `exit_code != 0`. O quarto **não**,
e não por esquecimento: o `judge.json` é escrito antes do upload, porque é ele um
dos arquivos que sobem. Um upload falho conta em `runs_failed`, vai para o
marcador e para o status de saída — mas o `judge.json`, se algum byte dele
chegou ao bucket, ainda diz `exit_code: 0`. A ordem é a mesma do `meta.json` no
`encode/run_scenario.sh`, e quem fecha essa porta é o leitor: um `run_id` do
plano sem resultado em `quality/results/` é output não julgado.

A falha do upload do **progresso** é a única que não conta para nada: telemetria
não derruba medição, e fica no log. A assimetria é a mesma do `encode/README.md`,
e o marcador de término continua sem folga — é o único jeito de o Orquestrador
saber que o Pass acabou.

As duas camadas locais são flags, com defaults operacionais e não do plano:

- `--output-timeout <segundos>` (4 h): o **orçamento do output**, e não de cada
  comando dele. O download, a passada do `libvmaf` e o upload correm sob o que
  resta dele; o que estourar recebe SIGTERM, o output entra no `judge.json` como
  falho e o laço segue. Um teto por comando deixaria um download que consumisse a
  janela inteira seguido de um `libvmaf` com uma janela nova.
- `--total-timeout <segundos>` (24 h): conferido **antes de cada output**; ao
  estourar, o laço para, escreve o marcador com `capped` verdadeiro e sai com 1.

O teto não é o das 120 h da ADR-0012: aquele foi calibrado para uma campanha de
quatro dias, e o Pass inteiro é de horas (ADR-0025) — 120 h aqui seriam cinco
dias de Juiz faturando sem guarda nenhuma. Como no encode, o timeout é um
watchdog em bash e não o `timeout` do coreutils: o Mac do pesquisador não o tem,
e um shim dele seria um fake da própria salvaguarda.

## O contrato do `status/`

Dois objetos, escritos por este laço e lidos pelo Orquestrador a cada poll. Os
dois carregam o `instance_id` porque o `DeleteObject` da ADR-0016 não alcança
`status/`: numa repetição do Pass os objetos anteriores continuam no bucket, e o
que os torna inertes é a identidade, não a ausência. A chave é `judge_*` e não
`{instance_type}_*` — o Juiz é um só, e prendê-lo ao tipo faria a chave mudar se
a ADR-0025 trocasse de instância.

`status/judge_progress` é **sobrescrito depois de cada output**, nunca durante
uma passada:

| campo | tipo | o que é |
|---|---|---|
| `instance_id` | string | o que chegou por `--instance-id` |
| `output_index` | número | o output em curso, 1-based |
| `output_count` | número | outputs do plano |
| `run_id` | string | o representante que **acabou** de ser julgado |
| `scenario_id` | string | o Cenário daquele representante |
| `runs_total` | número | outputs já julgados |
| `runs_failed` | número | quantos deles com `exit_code` não-zero |
| `elapsed_seconds` | número | segundos desde o início do laço |
| `written_at` | string | ISO-8601 **com offset** |

`status/judge_done` é o **último** objeto do Pass, inclusive no caminho do teto,
e tem a **mesma forma** do marcador do encode — `instance_id`, `finished_at`,
`runs_total`, `runs_failed`, `capped`, `exit_status` —, porque cada output
julgado é um run do Juiz. É isso que faz o leitor do marcador e a decisão de
vigilância servirem ao Juiz sem ramo; o objeto de progresso é próprio porque a
linha na tela é outra. Morto por sinal, o laço não escreve marcador nenhum.

## Verificação

Não há TDD aqui (ADR-0017): quem exercita o `run_quality.sh` é o `smoke/`,
escrito junto com ele, que o roda de verdade com `ffmpeg` e `aws` shimados sobre
o plano que o `quality_triage.py` acabou de escrever — sem Docker, sem AWS e sem
FFmpeg. A asserção que importa é a do **argv**, e ela é feita contra a
**definição** e nunca contra o plano: comparar com o plano pularia o elo que se
quer verificar.

    .venv-smoke/bin/python -m pytest smoke/

O `bootstrap.sh` e o `launch_container.sh` ficam de fora, como os homônimos do
`encode/`: um é provisionamento e o outro é o `docker run`, e o que os prova é o
`preflight --judge`, que lança um Juiz descartável e atravessa os dois.

`shellcheck` e `shfmt` rodam no pre-commit, que é a casa oficial dos linters.
