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
real —, e a mitigação é o que vem depois: a camada de aceite abaixo e o smoke
AWS, que são onde as ferramentas de verdade falam.

## A camada de aceite

O degrau seguinte da escada (ADR-0022): a mesma imagem que a Instância builda,
buildada aqui, e dentro dela o FFmpeg, o `/usr/bin/time`, o `pidstat` e o `perf`
**de verdade** em volta do argv que o plano gerou, sobre um clip de 5 s.

    .venv-smoke/bin/python -m pytest smoke/ --docker

É **opt-in** e fica desmarcado por padrão: sem o flag, o que está marcado com
`docker` é desselecionado na coleta e `pytest smoke/` segue sendo o laço de
segundos que o CI roda, sem Docker, sem FFmpeg e sem credencial. O build da
imagem leva de 10 a 20 min na primeira vez; o resto são minutos.

O que ele exercita e a camada com shims não alcança: que o encoder aceita o
preset, o CRF e os `encoder_args` que o `config/experiment.toml` declara para
ele; que o `/usr/bin/time` emite JSON com aquele format string; que o `pidstat`
escreve a coluna `%CPU` que o parser procura pelo nome; que a extração de
bitstream funciona com o muxer que cada um dos três codecs declara; e que os dez
`pmu_events` são nomes que o `perf stat` reconhece — ele recusa o que não
conhece.

O argv, o format string e as flags do `pidstat` não são escritos aqui: saem do
rastro que os shims registraram, na mesma sessão. Transcrevê-los faria a captura
concordar com o teste enquanto divergia do `run_scenario.sh` que a campanha roda.

O que continua fora de alcance é o **PMU**: o Docker no Mac não o expõe ao guest,
então todo contador de hardware volta `<not supported>`. Se cada evento retorna
valor em cada arquitetura é pergunta do smoke AWS, e é o modo de falha mais caro
do projeto.

O `run_scenario.sh` **não** é invocado: não se está medindo nada, e um harness que
o chamasse precisaria de um modo degradado sem `perf` — a alavanca que a campanha
não pode ter. O `acceptance.sh` é esse harness, e chega ao container pelo stdin do
`bash -s`, sem bind-mount: quais diretórios do Mac a VM do Docker compartilha
varia de máquina para máquina.

### Regenerar as fixtures

    .venv-smoke/bin/python -m pytest smoke/ --docker \
        --capture-dir=analysis/tests/fixtures

Copia as saídas cruas capturadas para onde os testes dos parsers do `analysis/`
as leem. Sem o flag, a captura morre no `tmp_path` e nada de runtime chega perto
do histórico. É passo manual, e a hora de rodá-lo é quando um pin do
`docker/Dockerfile` muda.
