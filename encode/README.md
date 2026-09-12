# encode/

Bash da Instância de encode (ADR-0017/0018), em duas camadas: no **host**, os
três scripts que põem a máquina de pé; **dentro do container**, os dois que
executam o plano. É o papel burro da pipeline: recebe dado já decidido e o
executa. Nenhuma seleção, nenhuma derivação, nenhuma consulta ao IMDS — a
Instância nunca decide nada (ADR-0009/0019).

## O host

O `bootstrap.sh` é o gordo do par user-data/bootstrap (ADR-0013/0017): o
user-data do `orchestrator/` clona o SHA e o chama com os argumentos do papel.
Ele instala Docker (o `docker.io` do Ubuntu — a versão do engine não toca a
medição, e o que está pinado é a imagem), o `linux-tools` do kernel corrente e a
AWS CLI v2 do instalador oficial; persiste `kernel.perf_event_paranoid=-1` em
`/etc/sysctl.d/`, que é o que a ADR-0006 pede do host para o `perf stat` de
dentro do container ler a PMU; cria o work dir e builda a imagem; e baixa a fatia
do `scenarios.json` e o manifesto **pelas chaves que recebeu**, porque a
Instância nunca decide path (ADR-0011/0019).

    bash encode/bootstrap.sh \
        --work-dir /home/ubuntu/work --bucket "$bucket" \
        --plan-key scenarios/c7g.json --manifest-key masters/manifest.json \
        --masters-prefix masters/

O `fetch_masters.sh` é o último passo dele, e é script à parte por dois motivos:
o smoke o exercita sozinho, e é ele a guarda contra Master corrompido — um
Master errado não aparece em número nenhum, vira seis Execuções medidas sobre a
entrada errada. Para cada Master que o manifesto lista, um `s3 cp` **por objeto**
e um `sha256sum` contra o manifesto: o papel não tem `ListBucket` na matriz da
ADR-0016, de modo que o manifesto *é* a lista. A primeira divergência para com
status não-zero nomeando o Master, e nada além dele é considerado válido.

    bash encode/fetch_masters.sh \
        --manifest /home/ubuntu/work/manifest.json --bucket "$bucket" \
        --prefix masters/ --dest /home/ubuntu/work/masters

O `launch_container.sh` é o `docker run`, e é o comando que o Orquestrador
dispara por SSH bloqueante. Ele monta as duas proveniências que a ADR-0018 não
deixa se misturarem — `<clone>/encode` read-only em `/opt/encode`, o work dir de
runtime em `/work` —, dá `--cap-add=PERFMON` e repassa ao `run_all.sh` os
argumentos que recebeu. O `--plan` é o **nome** da fatia dentro do work dir: o
caminho que o laço vê é o de dentro do container, e quem monta o work dir é quem
sabe onde ele fica. Os dois timeouts da ADR-0012 são opcionais aqui e só viajam
quando vêm — `--run-timeout` e `--total-timeout` chegam ao laço como chegaram, e
na ausência valem os defaults dele.

    bash encode/launch_container.sh \
        --work-dir /home/ubuntu/work --plan c7g.json --bucket "$bucket" \
        --commit "$sha" --instance-id "$id" --instance-type c7g.xlarge \
        --run-timeout 14400 --total-timeout 259200

O work dir é o contrato entre os três: o `bootstrap.sh` deixa lá a fatia, o
manifesto e `masters/`; o `launch_container.sh` os encontra por esses nomes, e o
`runs/` nasce dentro dele. Os dois invocam o `docker` com `sudo` em vez de
`usermod -aG docker`: a sessão que o bootstrap já tem não ganharia o grupo, e o
`docker build` dele falharia. O tag da imagem é o `transcoding-bench` do
`docker/README.md`, e os dois têm de concordar sobre ele.

## O container

O `run_scenario.sh` é uma Execução. Ele recebe o objeto de run do plano como
JSON, cunha o `run_id`, monta o argv do FFmpeg **só copiando** o que veio no
objeto, envolve o encode nas quatro fontes de instrumentação da ADR-0006,
escreve `runs/{run_id}/` com os sete artefatos da ADR-0007 e sobe o diretório
inteiro para `s3://{bucket}/runs/{run_id}/` logo em seguida. A primeira linha
do stdout é o diretório que ele criou, escrita antes de qualquer coisa poder
falhar.

    bash encode/run_scenario.sh \
        --run "$(jq -c '.blocks[0].runs[0]' c7g.json)" \
        --masters-dir /work/masters --runs-dir /work/runs --bucket "$bucket" \
        --commit "$sha" --instance-id "$id" --instance-type c7g.xlarge

O `run_all.sh` é o bloco, e a fatia inteira: percorre **todo** bloco do arquivo
que recebeu, na ordem do arquivo e com o warm-up primeiro, sem predicado de
seleção — a seleção por arquitetura já aconteceu no Python que pré-fatiou o
plano (ADR-0019). Depois de cada Execução ele sobrescreve
`s3://{bucket}/status/{instance_type}_progress`, e ao terminar escreve
`s3://{bucket}/status/{instance_type}_done`: é por esses dois objetos que o
Orquestrador sabe onde a Instância está e que ela acabou, sem SSH interativo
(ADR-0010/0011).

    bash encode/run_all.sh \
        --plan /work/c7g.json \
        --masters-dir /work/masters --runs-dir /work/runs --bucket "$bucket" \
        --commit "$sha" --instance-id "$id" --instance-type c7g.xlarge

`instance_id`, `instance_type`, `commit` e o bucket chegam por argumento —
nunca por consulta ao IMDS —, e o arquivo de versões vem da imagem
(`VERSIONS_FILE`). É essa ausência de descoberta que torna o caminho rodável no
Mac: não existe modo degradado, existe um argumento.

## O contrato do `status/`

Dois objetos por Instância, os dois escritos pelo `run_all.sh` e lidos pelo
Orquestrador a cada 5 minutos. Os dois carregam o `instance_id` porque o
`DeleteObject` da ADR-0016 não alcança `status/`: numa retomada os objetos da
tentativa anterior continuam no bucket, e o que os torna inertes é a identidade,
não a ausência.

`status/{instance_type}_progress` é **sobrescrito depois de cada Execução**,
entre runs e nunca durante um encode (ADR-0011) — um `jq -n` e um `s3 cp` de
poucos bytes:

| campo | tipo | o que é |
|---|---|---|
| `instance_id` | string | o que chegou por `--instance-id` |
| `block_index` | número | o bloco em curso, 1-based |
| `block_count` | número | blocos da fatia |
| `run_index` | número | a Execução dentro do bloco, 1-based |
| `run_count` | número | Execuções do bloco em curso |
| `scenario_id` | string | o Cenário que **acabou** de rodar |
| `runs_total` | número | Execuções já feitas na fatia inteira |
| `runs_failed` | número | quantas delas com status não-zero |
| `elapsed_seconds` | número | segundos desde o início do laço |
| `written_at` | string | ISO-8601 **com offset** |

Os índices são 1-based e viajam com o total ao lado para que a linha do
Orquestrador seja `bloco {block_index}/{block_count}` sem aritmética do lado de
quem lê. O total de runs da fatia contra o qual ele mostra `runs_total` vem do
plano que ele mesmo subiu, não daqui. Um upload de progresso que falhe é
registrado no log e o laço segue: telemetria não derruba medição.

`status/{instance_type}_done` é o **último** objeto que a Instância escreve
quando o laço termina, inclusive no caminho do teto. Morto por sinal, o laço não
escreve marcador nenhum — é por essa ausência que o Orquestrador distingue uma
Instância que acabou de uma que morreu no meio de um run:

| campo | tipo | o que é |
|---|---|---|
| `instance_id` | string | o que chegou por `--instance-id` |
| `finished_at` | string | ISO-8601 com offset |
| `runs_total` | número | Execuções da fatia |
| `runs_failed` | número | quantas com status não-zero |
| `capped` | booleano | se o `--total-timeout` parou o laço |
| `exit_status` | número | o status com que o `run_all.sh` sai |

A chave dos dois não muda e o IAM não muda: o papel `encode` já tem `PutObject`
em `status/*` (ADR-0016).

A assimetria entre os dois uploads é de propósito. O do progresso é telemetria:
falhou, fica o log e o laço segue. O do marcador não tem folga porque não é
telemetria — é o único jeito de o Orquestrador saber que a fatia acabou, e uma
Instância sem marcador é exatamente o que ele lê como morta. Um marcador que não
sobe derruba o laço, e é o `resume.py` que recolhe o que já está em `runs/`.

## Salvaguardas

As duas camadas locais da ADR-0012 são flags do `run_all.sh`, repassadas pelo
`launch_container.sh` quando o Orquestrador as declara, com os valores da ADR por
default — são limite operacional, não desenho experimental, e por isso
não viajam no plano:

- `--run-timeout <segundos>` (4 h): cada Execução recebe SIGTERM ao estourar. O
  `run_scenario.sh` mata a árvore do encode, fecha o `meta.json` com
  `exit_code` 143 e sobe o que tem — um run morto no meio não some do
  `resume.py`.
- `--total-timeout <segundos>` (72 h): conferido **antes de cada Cenário**; ao
  estourar, o laço para, escreve o marcador de término com `capped` verdadeiro e
  sai com status 1.

O timeout é um watchdog em bash, e não o `timeout` do coreutils: o Mac do
pesquisador não o tem, e um shim dele seria um fake da própria salvaguarda.

Um run falho **não interrompe o laço**: o próximo Cenário acontece, e o status
de saída do `run_all.sh` (1 se algum run falhou ou o teto disparou, 0 se não)
é o que diz ao Orquestrador que há algo para o `resume.py` olhar.

## Upload

O upload acontece **entre** runs, nunca durante um encode (ADR-0011): é o
`run_scenario.sh` que o faz, logo depois do `meta.json`, tanto no run
bem-sucedido quanto no falho. O objeto de progresso do laço sobe na mesma
janela, depois dele. O `meta.json` fecha o run antes da subida, e um
upload que falhe não o reabre — a cópia local segue íntegra, e é o status de
saída (72) que carrega a falha para o log do laço.

Falha de instrumentação — o `perf` estourando, um evento voltando `<not
supported>`, o PID do FFmpeg não resolvido — é **falha do run**, não aviso: nunca
existe run "bem-sucedido" sem os contadores que são o achado principal, e não há
flag que desligue a medição (ADR-0022). Um run falho registra `exit_code != 0` no
`meta.json` e preserva o que tem, para que o `resume.py` o trate como
não-completo em vez de ele sumir.

## Verificação

Não há TDD aqui (ADR-0017): quem exercita estes scripts é o `smoke/`, escrito
junto com eles, que os roda de verdade com `ffmpeg`, `perf`, `pidstat`, `aws` e
`/usr/bin/time` shimados no PATH — sem Docker, sem AWS e sem FFmpeg. A asserção
que importa é a do argv, porque a ADR-0021 permite editar este diretório
**durante** a campanha; e o smoke é o quarto job do CI justamente para que ela
seja guarda automática, não revisão de diff.

O `fetch_masters.sh` entra por ali também, sobre um manifesto montado pelo teste
e Masters placeholder no bucket falso. O `bootstrap.sh` e o
`launch_container.sh` ficam de fora: um é provisionamento e o outro é o `docker
run`, e o que os prova é o preflight, que lança uma instância descartável na AWS
para atravessá-los.

    .venv-smoke/bin/python -m pytest smoke/

O que os shims não podem dizer — que o encoder aceita o preset e os
`encoder_args` declarados, que o `/usr/bin/time` emite JSON com aquele format
string — é a camada de aceite do `smoke/` que diz, opt-in e fora do CI
(`pytest smoke/ --docker`).

`shellcheck` e `shfmt` rodam no pre-commit, que é a casa oficial dos linters.
