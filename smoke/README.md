# smoke/

O que o Mac do pesquisador roda para saber que o caminho de encode funciona
(ADR-0022). Diretório de topo porque o critério da ADR-0017 é *quem roda aquilo*,
e quem roda isto é o Mac — mesmo dono de `analysis/` e do futuro `infra/`.

    python -m venv .venv-smoke
    .venv-smoke/bin/pip install -r smoke/requirements-dev.txt
    .venv-smoke/bin/python -m pytest smoke/

Sem Docker, sem credencial AWS e sem FFmpeg: `ffmpeg`, `perf`, `pidstat` e
`/usr/bin/time` são substituídos por shims, e o ciclo fecha em segundos.

**O smoke nunca importa; só invoca.** O gerador do plano, a CLI de validação do
`meta.json` e o checador stdlib do orquestrador entram como subprocessos, e o que
se inspeciona são os artefatos. Importar código de outro papel exigiria `sys.path`
na marra ou `pip install -e`, as duas coisas que a ADR-0017 rejeitou — e tratar
os outros papéis como caixa-preta é o correto para um smoke de qualquer forma.

Os shims moram em `shims/` como `*.sh` e são instalados com o nome do binário que
substituem num diretório temporário que entra no PATH: a allowlist do
`.gitignore` (ADR-0017) admite fonte por extensão, e um arquivo chamado `ffmpeg`
não entraria no histórico. Cada comportamento induzido é uma variável de ambiente
(`SMOKE_FFMPEG_EXIT`, `SMOKE_PERF_EXIT`, `SMOKE_PERF_UNSUPPORTED`,
`SMOKE_ENCODER_INVISIBLE`, `SMOKE_BITSTREAM`).

Eles são a única superfície nova que pode envelhecer mal — fake que diverge do
real —, e a mitigação é o que vem depois: a camada de aceite manual com Docker e
o smoke AWS, que são onde as ferramentas de verdade falam.
