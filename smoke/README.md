# smoke/

O que o Mac do pesquisador roda para saber que o caminho de encode funciona
(ADR-0022). Diretório de topo porque o critério da ADR-0017 é *quem roda aquilo*,
e quem roda isto é o Mac — mesmo dono de `analysis/` e do futuro `infra/`.

    python -m venv .venv-smoke
    .venv-smoke/bin/pip install -r smoke/requirements-dev.txt
    .venv-smoke/bin/python -m pytest smoke/

Sem Docker, sem credencial AWS e sem FFmpeg: `ffmpeg`, `perf`, `pidstat`, `aws`
e `/usr/bin/time` são substituídos por shims, e o ciclo fecha em segundos. O
mesmo `pytest smoke/` é o quarto job do CI.

**O smoke nunca importa; só invoca.** O gerador do plano, a CLI de validação do
`meta.json` e o checador stdlib do orquestrador entram como subprocessos, e o que
se inspeciona são os artefatos. Importar código de outro papel exigiria `sys.path`
na marra ou `pip install -e`, as duas coisas que a ADR-0017 rejeitou — e tratar
os outros papéis como caixa-preta é o correto para um smoke de qualquer forma.

Os shims moram em `shims/` como `*.sh` e são instalados com o nome do binário que
substituem num diretório temporário que entra no PATH: a allowlist do
`.gitignore` (ADR-0017) admite fonte por extensão, e um arquivo chamado `ffmpeg`
não entraria no histórico. Cada comportamento induzido é uma variável de ambiente
(`SMOKE_FFMPEG_EXIT`, `SMOKE_FFMPEG_HANG`, `SMOKE_PERF_EXIT`,
`SMOKE_PERF_UNSUPPORTED`, `SMOKE_ENCODER_INVISIBLE`, `SMOKE_BITSTREAM`,
`SMOKE_AWS_EXIT`); `SMOKE_FFMPEG_NTH` restringe o do `ffmpeg` ao N-ésimo encode,
que é como um run falha no meio de um bloco cujos vizinhos seguem bem.

Todo shim registra o argv que recebeu em `$SMOKE_ARGV_DIR/<tool>.argv` e o seu
nome em `$SMOKE_ARGV_DIR/sequence`, a linha do tempo comum entre ferramentas —
é por ela que se vê o upload acontecendo **entre** runs.

O shim do `aws` traduz `s3 cp` e `s3api list-objects-v2` em operações sobre
`$SMOKE_S3_ROOT/<bucket>/<key>`. O que se testa com ele é que o layout de
prefixos da ADR-0011 casa entre quem escreve (o bash) e quem lê (o
`list-objects-v2` que o `resume.py` usará) — nunca semântica do S3, e por isso
sem localstack.

O laço fecha do outro lado: a árvore que o `run_all.sh` acabou de escrever é
consolidada invocando `analysis/consolidate.py`, e o Parquet que sai é lido aqui.
É o único lugar em que os parsers dos quatro artefatos encontram texto que não
foi escrito por eles — daí o `pyarrow` no `requirements-dev.txt`. A ponte que
essa asserção guarda é a dos eventos de PMU: trocar um evento no
`config/experiment.toml` sem trocar a coluna do `analysis/` deixaria a métrica
vazia para a campanha inteira, e o `perf stat` não falha quando o evento não
existe.

Eles são a única superfície nova que pode envelhecer mal — fake que diverge do
real —, e a mitigação é o que vem depois: a camada de aceite manual com Docker e
o smoke AWS, que são onde as ferramentas de verdade falam.
