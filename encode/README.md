# encode/

Bash que roda **dentro do container**, na Instância de encode (ADR-0017/0018). É
o papel burro da pipeline: recebe dado já decidido e o executa. Nenhuma seleção,
nenhuma derivação, nenhuma consulta ao IMDS — a Instância nunca decide nada
(ADR-0009/0019).

O `run_scenario.sh` é uma Execução. Ele recebe o objeto de run do plano como
JSON, cunha o `run_id`, monta o argv do FFmpeg **só copiando** o que veio no
objeto, envolve o encode nas quatro fontes de instrumentação da ADR-0006 e
escreve `runs/{run_id}/` com os sete artefatos da ADR-0007. A primeira linha do
stdout é o diretório que ele criou, escrita antes de qualquer coisa poder falhar.

    bash encode/run_scenario.sh \
        --run "$(jq -c '.blocks[0].runs[0]' c7g.json)" \
        --masters-dir /work/masters --runs-dir /work/runs \
        --commit "$sha" --instance-id "$id" --instance-type c7g.xlarge

`instance_id`, `instance_type` e `commit` chegam por argumento — nunca por
consulta ao IMDS —, e o arquivo de versões vem da imagem (`VERSIONS_FILE`). É
essa ausência de descoberta que torna o caminho rodável no Mac: não existe modo
degradado, existe um argumento.

Falha de instrumentação — o `perf` estourando, um evento voltando `<not
supported>`, o PID do FFmpeg não resolvido — é **falha do run**, não aviso: nunca
existe run "bem-sucedido" sem os contadores que são o achado principal, e não há
flag que desligue a medição (ADR-0022). Um run falho registra `exit_code != 0` no
`meta.json` e preserva o que tem, para que o `resume.py` o trate como
não-completo em vez de ele sumir.

## Verificação

Não há TDD aqui (ADR-0017): quem exercita estes scripts é o `smoke/`, escrito
junto com eles, que os roda de verdade com `ffmpeg`, `perf`, `pidstat` e
`/usr/bin/time` shimados no PATH — sem Docker, sem AWS e sem FFmpeg. A asserção
que importa é a do argv, porque a ADR-0021 permite editar este diretório
**durante** a campanha.

    .venv-smoke/bin/python -m pytest smoke/

`shellcheck` e `shfmt` rodam no pre-commit, que é a casa oficial dos linters.
