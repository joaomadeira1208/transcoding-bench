# encode/

Bash que roda **dentro do container**, na Instância de encode (ADR-0017/0018). É
o papel burro da pipeline: recebe dado já decidido e o executa. Nenhuma seleção,
nenhuma derivação, nenhuma consulta ao IMDS — a Instância nunca decide nada
(ADR-0009/0019).

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
plano (ADR-0019). Ao terminar escreve `s3://{bucket}/status/{instance_type}_done`,
que é como o Orquestrador detecta o fim sem SSH interativo (ADR-0010/0011).

    bash encode/run_all.sh \
        --plan /work/c7g.json \
        --masters-dir /work/masters --runs-dir /work/runs --bucket "$bucket" \
        --commit "$sha" --instance-id "$id" --instance-type c7g.xlarge

`instance_id`, `instance_type`, `commit` e o bucket chegam por argumento —
nunca por consulta ao IMDS —, e o arquivo de versões vem da imagem
(`VERSIONS_FILE`). É essa ausência de descoberta que torna o caminho rodável no
Mac: não existe modo degradado, existe um argumento.

## Salvaguardas

As duas camadas locais da ADR-0012 são flags do `run_all.sh`, com os valores da
ADR por default — são limite operacional, não desenho experimental, e por isso
não viajam no plano:

- `--run-timeout <segundos>` (4 h): cada Execução recebe SIGTERM ao estourar. O
  `run_scenario.sh` mata a árvore do encode, fecha o `meta.json` com
  `exit_code` 143 e sobe o que tem — um run morto no meio não some do
  `resume.py`.
- `--total-timeout <segundos>` (72 h): conferido **antes de cada Cenário**; ao
  estourar, o laço para, escreve o marcador de término e sai com status 1.

O timeout é um watchdog em bash, e não o `timeout` do coreutils: o Mac do
pesquisador não o tem, e um shim dele seria um fake da própria salvaguarda.

Um run falho **não interrompe o laço**: o próximo Cenário acontece, e o status
de saída do `run_all.sh` (1 se algum run falhou ou o teto disparou, 0 se não)
é o que diz ao Orquestrador que há algo para o `resume.py` olhar.

## Upload

O upload acontece **entre** runs, nunca durante um encode (ADR-0011): é o
`run_scenario.sh` que o faz, logo depois do `meta.json`, tanto no run
bem-sucedido quanto no falho. O `meta.json` fecha o run antes da subida, e um
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

    .venv-smoke/bin/python -m pytest smoke/

`shellcheck` e `shfmt` rodam no pre-commit, que é a casa oficial dos linters.
