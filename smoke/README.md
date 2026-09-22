# smoke/

O que o Mac do pesquisador roda para saber que o caminho de encode, a preparação
dos Masters e o Pass de qualidade funcionam (ADR-0022). Diretório de topo porque
o critério da ADR-0017 é *quem roda aquilo*, e quem roda isto é o Mac — mesmo
dono de `analysis/` e do futuro `infra/`.

    python -m venv .venv-smoke
    .venv-smoke/bin/pip install -r smoke/requirements-dev.txt
    .venv-smoke/bin/python -m pytest smoke/

Sem Docker, sem credencial AWS e sem FFmpeg: `ffmpeg`, `ffprobe`, `perf`,
`pidstat`, `aws`, `curl`, `unzip` e `/usr/bin/time` são substituídos por shims, e
o ciclo fecha em segundos. O mesmo `pytest smoke/` é o quarto job do CI.

**O smoke nunca importa; só invoca.** O gerador do plano, as CLIs de validação
do `meta.json` e do `judge.json`, os checadores stdlib do orquestrador e as CLIs
da retomada e do triage entram como subprocessos, e o que se inspeciona são os
artefatos. Importar código de outro papel exigiria `sys.path` na marra ou
`pip install -e`, as duas coisas que a ADR-0017 rejeitou — e tratar os outros
papéis como caixa-preta é o correto para um smoke de qualquer forma.

**Um bloco do piloto atravessa o mesmo caminho.** O `config/pilot.toml` é a
segunda definição do repositório (ADR-0019), e o plano dele sai do mesmo CLI,
invocado do mesmo jeito — só o `--config` muda. O primeiro bloco da fatia `c7g`
desse plano é dirigido pelo mesmo `run_all.sh`, com os mesmos shims, e
consolidado pelo mesmo `consolidate.py`; o argv que sai dali é conferido contra o
`pilot.toml`. O que muda entre esse bloco e o da campanha é o plano, e nada mais
— qualquer ramo "só no piloto" é exatamente o que o piloto não testaria
(ADR-0022). Os caminhos de falha não se repetem sobre ele: são propriedade dos
scripts, e o piloto não tem script próprio. Os masters placeholder são nomeados
pela união dos dois planos.

**A preparação dos Masters atravessa o mesmo harness.** O `masters/prepare.sh` é
dirigido com três shims novos — o `curl` entrega um placeholder no lugar dos GB
de cada fonte (`SMOKE_SOURCE_FILE`), o `unzip` copia o que ele baixou — como o
de verdade, que deixa o `.zip` no lugar para o `prepare.sh` apagar — e o
`ffprobe` emite a resposta que o teste preparou para cada Master
(`$SMOKE_PROBE_DIR/<nome>.json`) — mais o `ffmpeg` e o `aws` do encode. O plano
sai do `generate_masters_plan.py` invocado como caixa-preta, e o argv do remux e
de cada downscale é conferido contra a geometria do `config/experiment.toml`.

O único fato daquele arquivo que um download shimado não tem como honrar é o par
`size`/`sha256` de cada fonte, e é só ele que o smoke troca: o plano e o checker
recebem um `experiment.toml` temporário com os do placeholder, e URL, arquivo,
geometria de cada tier, cadência e frames continuam sendo os do repositório. É
contra esse arquivo que a CLI do `validate_manifest.py` confere o manifesto que o
bash acabou de escrever — a âncora cross-language do contrato, com o `jq`
montando o JSON de um lado e o Python estrito o aceitando ou recusando do outro.

Os shims moram em `shims/` como `*.sh` e são instalados com o nome do binário que
substituem num diretório temporário que entra no PATH: a allowlist do
`.gitignore` (ADR-0017) admite fonte por extensão, e um arquivo chamado `ffmpeg`
não entraria no histórico. Cada comportamento induzido é uma variável de ambiente
(`SMOKE_FFMPEG_EXIT`, `SMOKE_FFMPEG_HANG`, `SMOKE_PERF_EXIT`,
`SMOKE_PERF_UNSUPPORTED`, `SMOKE_ENCODER_INVISIBLE`, `SMOKE_BITSTREAM`,
`SMOKE_VMAF`, `SMOKE_AWS_EXIT`); `SMOKE_FFMPEG_NTH` restringe o do `ffmpeg` ao
N-ésimo encode — ou ao N-ésimo julgamento, que conta à parte —, que é como um
run falha no meio de um bloco cujos vizinhos seguem bem, `SMOKE_BITSTREAM_NTH`
restringe o `SMOKE_BITSTREAM` do mesmo jeito — só o N-ésimo encode devolve o
bitstream pedido, e os outros ficam com o default —, que é como uma Replicação
diverge das outras quatro da mesma Instância, e `SMOKE_AWS_FAIL_KEY` restringe o
do `aws` a uma chave, que é como só o objeto de progresso deixa de subir.

O shim do `ffmpeg` atende três invocações, e a do Juiz **não** se discrimina pelo
último argumento: o `-f null -` do `run_quality.sh` termina no mesmo `-` da
extração do bitstream. O que a distingue é o `libvmaf=` no filtergraph, e é de lá
que sai o `log_path` em que o shim escreve. O log tem a forma do `libvmaf` v3 —
`frames[].metrics` com `vmaf` e `float_ssim`, mais o `pooled_metrics` — com o
VMAF de cada frame valendo `SMOKE_VMAF` e o SSIM derivado dele, para que um
grupo equivalente e um divergente sejam encenáveis pela variável. Um julgamento
induzido a falhar **não** escreve log nenhum: um `libvmaf` que não terminou não
deixa série por frame, e escrever uma aqui faria o julgamento falho parecer
legível.

Todo shim registra o argv que recebeu em `$SMOKE_ARGV_DIR/<tool>.argv` e o seu
nome em `$SMOKE_ARGV_DIR/sequence`, a linha do tempo comum entre ferramentas —
é por ela que se vê o upload acontecendo **entre** runs. O do `aws` guarda, além
disso, toda versão de cada objeto subido avulso em
`$SMOKE_ARGV_DIR/versions/<key>/`: o bucket falso conserva só a última, como o
S3, e `status/{instance_type}_progress` é sobrescrito a cada Execução.

O shim do `aws` traduz `s3 cp` — nos dois sentidos —, `s3 sync` no sentido
bucket → disco e `s3api list-objects-v2` em operações sobre
`$SMOKE_S3_ROOT/<bucket>/<key>`. O que se testa com ele é que o layout de
prefixos da ADR-0011 casa entre quem escreve (o bash) e quem lê (o
`list-objects-v2` do Orquestrador e o `s3 sync` da retomada) — nunca semântica do
S3, e por isso sem localstack. O sentido bucket → disco é o do
`encode/fetch_masters.sh`, que baixa um objeto por Master listado no manifesto e
confere o sha256 antes do primeiro Cenário, e é o único do `s3 sync`: o sentido
contrário sai como não shimado. Os `--exclude`/`--include` são aplicados na ordem
em que a CLI de verdade os aplica — o último padrão que casa decide —, porque é
um par deles que define o que a retomada baixa, e um trio o que o triage do Pass
de qualidade baixa. Prefixo sem objeto desce zero arquivos e sai com status zero;
bucket inexistente falha, como o `NoSuchBucket` da CLI, que é o que o `resume.py`
separa de "campanha que ainda não começou".

**A retomada decide sobre o que o bash escreveu.** O `resume.py` é invocado como
caixa-preta sobre o bucket falso que o `run_all.sh` acabou de encher, com o
`--config` que gerou aquele plano: é a única prova de que a completude por bloco
é decidida sobre os `meta.json` de verdade, e não sobre uma árvore montada pelo
teste. São dois caminhos. Com uma Replicação falhada por `SMOKE_FFMPEG_NTH`, o
relatório nomeia o bloco dela como pendente com o motivo "com falha", e o `--out`
recebe uma fatia só, com aquele bloco inteiro e os mesmos 6 runs do canônico; sem
falha nenhuma, são zero pendentes, nenhum arquivo no `--out` e status zero — "não
há o que retomar" é um resultado, não um crash.

O `--config` daí é o `config/pilot.toml` reduzido a um codec e a uma
arquitetura, que é o que faz o plano inteiro caber nos dois blocos que o laço
roda: contra a campanha, todo bloco que laço nenhum do smoke executa sairia
pendente por ausência, e "uma fatia só" deixaria de ser verificável. O rastro que
o preflight deixa em `runs/preflight/<instance-id>/`, sem apagar, entra no bucket
falso ao lado dos blocos e prova o outro lado: relatório e fatias saem idênticos
e sem aviso de Execução sem `meta.json`, porque o `s3 sync` da retomada só traz
`*/meta.json` e não há `meta.json` ali.

**O triage do Pass decide sobre o que três laços escreveram.** O
`quality_triage.py` entra como caixa-preta sobre o bucket falso que três laços do
`run_all.sh` encheram, um por arquitetura declarada, com o `SMOKE_BITSTREAM` do
`c7g` diferente do dos dois x86 — o achado que a ADR-0025 mediu no piloto. É a
única prova de que os grupos, o representante determinístico e o `plan.json` são
decididos sobre os `meta.json` e os `output.sha256` que o bash escreveu. O
`--config` é o `config/pilot.toml` reduzido a um codec, as três arquiteturas
intactas: seis blocos em dois Cenários, que é o menor recorte em que existe
bitstream compartilhado a representar. O relatório conta dois bitstreams por
grupo, o plano nomeia o `c7g` e o `c7i` como representantes e traz as cinco
Replicações do `c7a` em `shared_by`, e dois triages sobre o mesmo bucket escrevem
o mesmo `plan.json` byte a byte. O rastro do preflight entra ali também: os três
padrões do `s3 sync` do triage não o trazem, e relatório e plano saem idênticos.

O bitstream do shim é constante dentro de um laço, então os dois Cenários de uma
arquitetura saem com o mesmo sha256 — o triage decide por grupo, e é por isso que
isso não confunde a contagem.

Os outros dois caminhos repetem um laço sobre o mesmo bucket, como a campanha faz
depois de uma retomada: as Execuções novas vencem a dedup por `started_at`. Com
`SMOKE_BITSTREAM_NTH` numa Replicação de um x86, a célula é nomeada no relatório,
marcada no plano, e o bitstream a mais vira um output a mais. Com um run falhado
(`SMOKE_FFMPEG_EXIT` mais `SMOKE_FFMPEG_NTH`), o triage recusa a matriz
incompleta nomeando o bloco e imprimindo o comando da retomada, e não escreve
plano nenhum — o Pass só decide sobre blocos completos.

**O Juiz julga o plano que o triage escreveu.** O `judge/run_quality.sh` é
dirigido de verdade sobre o `plan.json` daquele triage, com `ffmpeg` e `aws`
shimados, e é o elo que fecha o Pass: o que o Python decidiu vira argv de FFmpeg
sem ninguém transcrever nada no meio. A campanha de três laços e o triage sobre
ela são fixtures do `conftest.py` justamente porque têm dois consumidores.

A asserção central é a do **argv**, e ela é feita contra a **definição** — a
geometria do tier daquele vídeo, o `scale_flags` do `[encode]`, o modelo do
`[quality]` —, nunca contra o plano: comparar com o plano pularia o elo
`pilot.toml` → triage → `jq` → filtergraph que se quer verificar. Junto com ela,
que os dois `-i` chegam na ordem output/Master e que o distorcido é a primeira
entrada do `libvmaf`: invertê-los reporta como qualidade do encoder um número que
não é dele.

O resto é o que o Pass deixa. Cada output deixa `vmaf.json`, `judge.json` e
`ffmpeg.log` sob `quality/results/{run_id}/` no bucket falso; o `judge.json`
passa pela CLI do `analysis/` e pelo checador stdlib do `orchestrator/`, pelos
mesmos subprocessos com que o `meta.json` já passa; o `.mkv` baixado some do work
dir depois de julgado; o progresso é sobrescrito a cada output e o marcador tem a
forma do marcador do encode. Os quatro caminhos — o são, a falha induzida, o
travamento com `--output-timeout` e o teto — rodam cada um sobre uma **cópia** do
bucket: os quatro escrevem os mesmos `quality/results/` e o mesmo par de
`status/`, e no bucket compartilhado o último apagaria a evidência dos outros.

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
bitstream funciona com o muxer que cada um dos três codecs declara; e que os oito
`pmu_events` são nomes que o `perf stat` reconhece — ele recusa o que não
conhece.

O argv, o format string e as flags do `pidstat` não são escritos aqui: saem do
rastro que os shims registraram, na mesma sessão. Transcrevê-los faria a captura
concordar com o teste enquanto divergia do `run_scenario.sh` que a campanha roda.

O que continua fora de alcance é o **PMU**: o Docker no Mac não o expõe ao guest,
então todo contador de hardware volta `<not supported>`. Se cada evento retorna
valor em cada arquitetura é pergunta do `preflight`, que a faz nos três tipos
antes do piloto (ADR-0022), e é o modo de falha mais caro do projeto.

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

O par de `status/` que o `run_all.sh` escreve tem flag e destino próprios,
`--status-capture-dir` — ver `orchestrator/tests/fixtures/README.md`.
