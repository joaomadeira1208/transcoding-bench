# orchestrator/

Python que roda na t3.micro (ADR-0017): a maquinaria que age sobre a spec de
`config/`. Gera o plano canônico de Cenários e suas fatias por arquitetura,
dispara as Instâncias e decide o que retomar.

Runtime é **stdlib-only** (mais a AWS CLI por `subprocess`): `requirements.txt`
lista só o runtime e o bootstrap da instância nunca instala o `-dev`, de modo que
um `import pandas` acidental quebre no ambiente limpo do CI.

Nomes já cravados por ADR: `orchestrator.py`, `resume.py`, `quality_triage.py`.
Testes em `tests/`, rodando só no Mac e no CI.

O `meta_check.py` é o segundo leitor do `meta.json`: `resume.py` e
`quality_triage.py` decidem sobre esse arquivo e não podem importar o modelo
pydantic do `analysis/` sem custar a invariante stdlib-only. É regra duplicada,
não código compartilhado (ADR-0019/0022) — e o `tests/test_meta_agreement.py`,
que mora igual nos dois papéis, é o que impede os dois leitores de divergirem
sobre o que é um arquivo válido.

O gerador do plano está partido em núcleo puro e casca: `experiment_config.py`
valida a configuração já parseada e `scenario_plan.py` a transforma no plano
canônico (as duas são funções puras, e é nelas que os testes batem);
`generate_scenarios.py` é o CLI que lê o TOML do disco, escreve os artefatos e
traduz erro em código de saída. Uma invocação emite os quatro: `canonical.json`,
o registro do Experimento, e uma fatia por arquitetura (`c7g.json`, `c7i.json`,
`c7a.json`), que é o que cada Instância de encode consome — ela roda todo bloco
do arquivo que recebeu, sem predicado de seleção no bash (ADR-0019). O
`conftest.py` deste nível é o que torna o núcleo importável pelos testes sem
`pyproject.toml` nem `sys.path` manipulado.

    python -m venv .venv
    .venv/bin/pip install -r orchestrator/requirements-dev.txt
    .venv/bin/python -m pytest orchestrator/
    .venv/bin/python orchestrator/generate_scenarios.py \
        --config config/experiment.toml --out build/scenarios
    .venv/bin/python orchestrator/generate_scenarios.py \
        --config config/pilot.toml --out build/pilot
    .venv/bin/python orchestrator/generate_masters_plan.py \
        --config config/experiment.toml

O diretório de saída é argumento porque o plano é artefato de runtime (ADR-0017):
ele não entra no repositório, e gerá-lo de novo a partir do mesmo TOML produz
bytes idênticos.

A segunda invocação é o plano do piloto (ADR-0022), e é a mesma invocação: o
gerador recebe um `--config` e não sabe qual das duas definições de `config/`
está lendo. Saem dela os mesmos quatro nomes de artefato — o bucket do piloto tem
o layout `scenarios/` da campanha (ADR-0011) —, com 18 blocos no canônico e 6 por
fatia.

O plano dos Masters é o mesmo desenho, num terceiro par: `masters_plan.py` é a
função pura que projeta a configuração validada — por vídeo, a fonte pinada, o
Master 4K e, por tier derivado, o nome, a geometria e as propriedades que o
`ffprobe` da preparação tem de encontrar (codec, `pix_fmt`, frame rate e
frames) —, e `generate_masters_plan.py` é o CLI. Os nomes saem da mesma função
que o plano de Cenários usa em `master`: o que a preparação materializa e o que a
Execução abre não podem divergir. Sobre o `experiment.toml` são seis Masters, os
seis que o plano de Cenários cita.

O plano vai para o stdout por default, e o `--out` é conveniência: quem o consome
é o argv do SSH que o `prepare-masters` monta (ADR-0018). Os tiers derivados são
os que aparecem como `input_res` de algum par — o 480p é só saída e nunca vira
Master (ADR-0023) —, e o 4K entra sempre, porque é ele que se remuxa. Por isso o
plano do piloto, cujo único par é `1080p → 720p`, tem quatro Masters e não seis:
o bucket do piloto recebe os seis da campanha por cópia (o `s3 sync` do
`prepare-masters`), não por uma preparação própria.

O manifesto é o outro lado desse par: `masters/manifest.json` é o que a
preparação escreve com `jq` depois de subir os seis Masters, e é o que o gate
humano da ADR-0012 confere antes de a campanha começar. A forma tem quatro
chaves. `schema_version` é eixo próprio, `"1"`, e não acompanha o do plano;
`versions` é o arquivo de versões da imagem copiado verbatim, como no
`meta.json`; `sources` é uma tabela por vídeo com `url`, `file`, `size` e
`sha256` observados no download; e `masters` é a lista, cada Master com onze
campos — os nove do plano (`name`, `video`, `tier`, `width`, `height`,
`codec_name`, `pix_fmt`, `frame_rate`, `frames`) mais o `size` e o `sha256` do
objeto que subiu. São três leitores: o bootstrap do encode (bash com `jq`, que
tira dali o nome e o sha256 de cada objeto a baixar), o checker abaixo e o
pesquisador no gate.

O `manifest_check.py` é o núcleo puro desse gate, e o `validate_manifest.py`, o
CLI:

    .venv/bin/python orchestrator/validate_manifest.py \
        masters/manifest.json --config config/experiment.toml

O checker recebe o manifesto já parseado e a configuração validada e devolve
todas as divergências de uma vez, cada uma nomeando o Master e o campo — parar na
primeira faria o pesquisador descobrir seis defeitos em seis rodadas. Confere
forma e tipos (tipo exato, sha256 de 64 dígitos hexadecimais minúsculos, tamanhos
positivos) e a semântica contra o plano dos Masters: os seis nomes, exatamente, e
por Master a geometria do seu tier naquele vídeo, a cadência, os frames, o codec
e o `pix_fmt`, mais as fontes contra as que o TOML declara. O `--config` é o da
campanha mesmo para o manifesto que veio do bucket do piloto: lá os seis Masters
chegam por cópia, e conferi-lo contra o `pilot.toml` acusaria os dois que o
piloto não usa. Silêncio e status 0 é o veredito de aceite; 1 é manifesto
recusado, e 2, arquivo ilegível ou configuração inválida.

O `external.py` é a única casa de `subprocess.run` do papel (ADR-0022): uma
função por comando, cobrindo os três processos externos do Orquestrador — a AWS
CLI, o SSH (ADR-0010) e o git (ADR-0021). Terraform não entra: ele é ferramenta
do Mac (ADR-0009) e o Orquestrador nunca o invoca. O módulo é partido em duas
metades de natureza oposta. O argv e a chamada ao processo ficam **sem teste**,
por decisão — asserir a lista de argv transcreve a implementação e quebra em
refatoração inofensiva. A leitura da saída mora no `command_output.py`, é função
pura e é onde os testes batem: id da instância do `run-instances`; estado e os
dois endereços do `describe-instances`; chaves e tamanhos do `list-objects-v2`,
inclusive a saída vazia que a CLI v2 imprime quando o prefixo não casa com nada;
o valor do `get-parameter`; o ARN do `get-caller-identity`; o status do
`cloud-init`.

O endereço que a prontidão exige é o **privado**, e é por ele que o SSH abre: a
regra de ingress das efêmeras referencia o security group do Orquestrador
(ADR-0015), e uma referência a security group só casa tráfego que chega por
dentro da VPC — o que é enviado ao IP público sai pelo internet gateway e volta
com o IP público de origem, que a referência não bate. O público continua no
`DescribedInstance` porque as efêmeras precisam dele para sair para a internet
(não há NAT); ele só não é o alvo do SSH.

A regra que o seam impõe a quem o usa é **"função pura recebe dado já buscado"**:
uma lista de `S3Object`, nunca um prefixo a listar. O `instance_wait.py` é a
mesma regra aplicada ao tempo — "pronta" é um predicado sobre o estado parseado
(`running` **com** IP privado) e "bootstrap concluído" é o status do `cloud-init`
parseado; o laço só chama o probe que recebeu, consulta o relógio e dorme, com
timeout por argumento e relógio injetável, e é isso que o torna exercível sem AWS
e sem `sleep`. O probe do bootstrap roda `cloud-init status` **sem** `--wait`: é
o timeout do laço que precisa valer, e o estado `running`, o que distingue "ainda
subindo" de "falhou", só existe sem ele.

Nesse probe o código de saída é **estado, não falha**: o `cloud-init status` sai
1 em `error` e 2 em `degraded`, de modo que um adaptador que exigisse exit 0
transformaria a instância que falhou o bootstrap num erro de comando genérico e
deixaria o `BootstrapError` do laço inalcançável. E o `ssh` sai 255 quando é ele
que não conseguiu conectar, o que é o caso normal nos primeiros segundos depois
de a instância virar `running`: o `sshd` sobe depois do estado. O probe traduz
esse 255 em `None`, e o laço trata `None` como espera — simétrico ao
`describe-instances` sem reservas do laço de prontidão, e a razão de o timeout do
bootstrap dizer se o último estado foi um status do `cloud-init` ou silêncio no
SSH.

O probe da prontidão tem a sua própria tradução, pela mesma razão: o id que o
`run-instances` acabou de devolver leva alguns segundos para aparecer no
`describe-instances`, e no intervalo a CLI o recusa com
`InvalidInstanceID.NotFound`. O `described_instance` devolve `None` nesse caso e
propaga qualquer outro erro — um `AccessDenied` continua matando o passo em vez
de virar espera até o timeout.

O `ssh_exec` é bloqueante, recebe o comando remoto como argv e o entrega ao shell
da instância já citado por `shlex.join`, que é o que faz um JSON no argv
sobreviver (ADR-0018). Ele leva `ConnectTimeout` e keep-alive de servidor porque
um `ssh` contra um security group que dropa pacotes pendura indefinidamente, e
enquanto ele pendura o argumento `timeout` do laço de espera é mentira; o probe
do `cloud-init` acrescenta a isso um teto de tempo na própria chamada, por estar
dentro do laço.

O keep-alive cobre a rede que morre, e não o comando remoto que trava com a
conexão viva: o `prepare.sh` baixa 7,4 GB de fonte externa com `curl` sem
`--max-time`, e um download que estola é indistinguível das ~2 h de silêncio do
caso normal. Por isso a chamada da preparação também leva um `timeout` próprio,
generoso o bastante para não cortar uma corrida lenta — o que ele compra é a
falha cair no `except` e a instância ser terminada sozinha, em vez de ficar
faturando até alguém reparar. A chave é a que o bootstrap da instância do Orquestrador
grava em `~/.ssh` a partir do SSM (ADR-0016): o caminho é constante do módulo e
argumento default, e tem de casar com o nome que aquele bootstrap escreve.

## O user-data fino, um template para todos os papéis

`user-data.sh` é o user-data de **todo** papel (ADR-0013/0017): instala git,
clona o repositório público em `/home/ubuntu/transcoding-bench`, faz `checkout`
no SHA e chama o `bootstrap.sh` do papel com os argumentos dele. Ele mora neste
diretório, e não em cada papel, porque é um arquivo só e é este papel que o
renderiza para os outros: o Terraform o renderiza por `templatefile()` para a
instância do Orquestrador (com o SHA do HEAD do branch padrão no momento do
`apply`), e o Orquestrador o renderiza por `string.Template` para as efêmeras
(com o SHA do próprio clone, ADR-0021). O `bootstrap.sh` de cada papel segue
sendo do papel.

Os placeholders são de cifrão-e-chaves porque é a única sintaxe que os dois
renderizadores resolvem, e é daí que sai a regra do arquivo: **nenhum cifrão fora
deles**. Uma variável de shell ali dentro é placeholder para o `string.Template`,
e a renderização em Python passa a levantar. O que precisa de variável mora no
`bootstrap.sh`, que é script e não template.

O `bootstrap.sh` daqui é o do Orquestrador (ADR-0016), chamado como o usuário
`ubuntu` — o que precisa de root pede `sudo` linha a linha, e o venv, a chave e o
work dir nascem do dono da máquina, que é quem dá SSH nela. Ele instala o AWS CLI
v2 do instalador oficial, `git`, `jq`, `tmux` e o `python3.12-venv`; cria o venv
em `.venv` do clone com o `requirements.txt` de runtime, **nunca** o `-dev`;
grava o arquivo de infra no work dir; e escreve a chave privada lida do parâmetro
SSM em `~/.ssh/transcoding-bench.pem` com permissão 600 — o mesmo caminho que o
`external.py` abre por default. Docker não entra: o Orquestrador não mede nada.

Nem o user-data nem o bootstrap têm teste automatizado, por decisão: o que eles
fazem é provisionamento, e quem os verifica é o pesquisador no `apply`, pelo
runbook do `infra/README.md`.

## O arquivo de infra

O Terraform injeta no user-data os ids que o Orquestrador precisa conhecer e o
bootstrap os grava em `<work-dir>/infra.json` — hoje `/home/ubuntu/work/infra.json`.
Todo subcomando recebe o caminho por `--infra`; descoberta por tag foi rejeitada
(ADR-0019), a seleção é explícita e mora no lado inteligente. A forma:

    {
      "subnet_id": "subnet-0a1b2c3d4e5f60718",
      "security_groups": {
        "orchestrator": "sg-0a1b2c3d4e5f60718",
        "ephemeral": "sg-0b2c3d4e5f6071829"
      },
      "instance_profiles": {
        "orchestrator": "transcoding-bench-orchestrator",
        "encode": "transcoding-bench-encode",
        "judge": "transcoding-bench-judge",
        "masters": "transcoding-bench-masters"
      },
      "key_pair_name": "transcoding-bench",
      "amis": {
        "orchestrator": "ami-025d99823a4caad37",
        "encode_amd64": "ami-025d99823a4caad37",
        "encode_arm64": "ami-0246d714afcc1d494"
      },
      "buckets": {
        "campaign": "transcoding-bench-123456789012-campaign",
        "pilot": "transcoding-bench-123456789012-pilot"
      },
      "ssh_private_key_parameter_name": "/transcoding-bench/orchestrator/ssh-private-key"
    }

O bootstrap só o valida como JSON (é o `jq` que o escreve) e lê dele o nome do
parâmetro da chave. Quem confere a forma inteira é o `infra_config.py`, função
pura sobre o arquivo já parseado: cada campo tem de estar lá e ser uma string
não-vazia, **inclusive os aninhados** — o `security_groups.ephemeral` e o
`instance_profiles.masters` ausentes só apareceriam como `KeyError` no meio do
laço, depois de a instância estar de pé. Chave que o Terraform passe a emitir e o
parser não conheça é ignorada: o arquivo é gerado por este mesmo repositório, e
recusá-la amarraria um `apply` novo a um checkout novo do Orquestrador.

## O CLI: `orchestrator.py`

Um subcomando por passo da campanha, e o `--infra` antes dele, com o caminho do
arquivo acima. Roda na instância do Orquestrador, **dentro de `tmux`**: os
subcomandos bloqueiam por horas e a sessão SSH que cair não pode levar o passo
junto. São dois:

    python orchestrator/orchestrator.py --infra ~/work/infra.json prepare-masters
    python orchestrator/orchestrator.py --infra ~/work/infra.json preflight \
        --instance-type c7g.xlarge

A escada em que eles se encaixam, cada degrau disparado pelo pesquisador
(ADR-0022): smoke local → aceite com Docker → preparação dos Masters → **gate
humano** sobre o manifesto → preflight nos três tipos → piloto e **gate humano**
sobre o relatório → campanha. O `prepare-masters` é o primeiro exercício real de
`PassRole`, condição de tipo e chave via SSM; o preflight prova o resto do
caminho do encode, incluindo a validação dos Masters, que só existe com Masters
no bucket. Não há degrau entre o preflight e o piloto: o smoke AWS saiu da
escada, e a primeira Execução real do projeto é o primeiro bloco do piloto.

Nada é descoberto por tag (ADR-0019): a subnet, o security group das efêmeras, o
perfil, a AMI e os dois buckets saem do `--infra`, e o SHA que as instâncias
clonam sai do `git rev-parse` do clone que contém o próprio `orchestrator.py`
(ADR-0021) — o `-C` do adaptador é o que impede o SHA de ser o do diretório de
onde o pesquisador invocou o comando.

O `prepare-masters` é o passo que roda uma vez, antes de qualquer medição, e
leva cerca de duas horas:

1. projeta o plano dos Masters do `config/experiment.toml` e resolve o SHA;
2. lança uma `c7g.xlarge` arm64 com o perfil `masters`, 100 GB gp3, hop limit 2
   do IMDS (o `aws` roda dentro do container, ADR-0018) e as tags `role`,
   `commit` e `Name`, com o user-data fino renderizado por `string.Template`;
3. espera `running` com IP privado, depois o `cloud-init` — status de erro é
   falha do lançamento, não paciência;
4. dispara por SSH **bloqueante** o `docker run` da preparação, com o plano JSON
   no argv: o papel `masters` não tem `GetObject` (ADR-0016), então o plano não
   pode chegar pelo S3. O arquivo de versões não vai no argv — quem o nomeia é o
   `ENV VERSIONS_FILE` da imagem, como no `run_scenario.sh`, e repeti-lo aqui
   faria mexer no `Dockerfile` quebrar a preparação;
5. `s3 sync` de `masters/` da campanha para o do piloto, lista os dois prefixos e
   compara nome e tamanho por função pura — divergência é erro;
6. baixa o `manifest.json` da campanha **para o lado do arquivo de infra**
   (`~/work/manifest.json`) e roda o checker sobre ele.

**Em todo caminho de saída**, sucesso ou exceção, a instância lançada é
terminada. As duas funções puras — a montagem do comando remoto e a comparação
das duas listagens — moram no `masters_launch.py` e são o que ganha teste; o laço
do subcomando e a renderização do template são escritos direto (ADR-0022).

O que segue não é opcional e não é do programa: o **gate humano da ADR-0012**. O
pesquisador lê o `~/work/manifest.json` contra a tabela de fontes da ADR-0004 e a
geometria por vídeo e tier da ADR-0023 — os seis nomes, a largura e a altura de
cada Master naquele vídeo, o `codec_name`, a cadência e a contagem de frames —
antes de a campanha medir qualquer coisa. O checker aceita a forma e a semântica
contra o TOML; quem confere se o TOML é o experimento que o artigo descreve é o
pesquisador.

### O `preflight`

O segundo subcomando prova, antes de haver uma instância faturando por dois dias,
que `PassRole`, condição de tipo, chave via SSM, hop limit, clone no SHA, build,
fatia, Masters validados, os dez eventos de PMU dentro do container e o
`PutObject` do encode funcionam **juntos**. Ele não mede nada — conferir que um
contador abre não é medi-lo —, e é ele que responde à pergunta que custa mais
caro do que qualquer outra: se cada evento da definição existe **naquela**
arquitetura (ADR-0006, ADR-0022).

Cada execução é uma instância descartável de poucos minutos, e o tipo é
argumento — `--instance-type c7i.xlarge` roda o mesmo caminho no x86, sem código
por arquitetura: a AMI sai do arquivo de infra pela `arch` que o
`experiment.toml` declara para aquele tipo. O `--bucket` escolhe quem recebe a
fatia e o objeto de prova, e o default é o do piloto. Exige que os Masters já
existam.

A tabela que ele imprime tem uma linha por capacidade provada, e é ela o
resultado do passo — o código de saída é não-zero se alguma falhou. As dez linhas
mais o `terminate` são os seis passos nomeados da spec. `sts`, `buckets`, `ssm`,
`git` e `s3-sync` são a auto-checagem, que roda inteira **antes de qualquer
lançamento** e pega credencial ou infra errada de graça; `buckets` lista os dois
e exige o `masters/manifest.json` naquele que a instância vai ler, porque sem os
Masters ela só descobriria a ausência depois de pagar o `docker build`. `ami` e
`launch` sobem a fatia para `scenarios/` e lançam a instância com o perfil de
encode, 200 GB gp3, hop limit 2 e o user-data fino real; `bootstrap` espera
`running` e o `cloud-init`, o que prova o clone no SHA, o build, a fatia, o
manifesto e os Masters baixados e validados; `perf-stat` e `s3-put` são os dois
`docker run` do passo 4; e `terminate` roda **em todo caminho de saída**.

O `s3-sync` da auto-checagem é uma emenda ao passo 1, e o motivo é que o D25
original não exercitava `s3_sync` em lugar nenhum: o único caminho de IAM que
roda S3→S3 é o espelho do fim do `prepare-masters`, e ele estreava depois de uma
corrida de ~2 h já paga. Aqui ele copia um objeto de poucos bytes entre os dois
buckets, pela mesma função do adaptador, e o apaga dos dois em seguida — o objeto
vive sob `runs/preflight/`, que é onde o `DeleteObject` da ADR-0016 alcança.

O `perf stat` do passo 4 pede os `pmu_events` da definição validada — os dez
juntos, nunca uma lista transcrita no código, porque trocar um evento no
`config/experiment.toml` tem de mudar o que o preflight confere — e decide sobre
o **valor**, não sobre o código de saída: um evento indisponível naquela PMU não
faz o `perf` falhar (ADR-0006), ele reporta `<not supported>` e segue. Cada um
dos dez tem de vir com valor numérico; o que falta, volta `<not supported>` ou
não é número derruba o passo **nomeando o evento**, e é esse nome que o
pesquisador lê na tabela para decidir, antes do piloto, entre trocar o evento na
definição e registrar a coluna ausente. Passando, a linha lista os dez valores.

O objeto que o container escreve em `runs/preflight/` é listado e apagado pelo
Orquestrador, o que fecha `PutObject` do encode, `ListBucket` e `DeleteObject` no
mesmo passo.

O que o preflight **não** apaga é a fatia. Os dois objetos de prova vivem sob
`runs/preflight/` e somem; `scenarios/{id}.json` fica no bucket, e o
`DeleteObject` da ADR-0016 nem alcança `scenarios/`. O que sobra ali é a fatia da
**campanha inteira** daquela arquitetura, não um resto do preflight: quem lançar o
piloto sobrescreve essa chave, e ninguém deve ler um objeto já presente nela como
se o próprio lançamento o tivesse posto.

O que ganha teste é o núcleo do `preflight.py`: o veredito sobre a saída do
`perf` — função pura sobre o texto parseado, e sobre a lista que a configuração
real declara, não sobre uma cópia dela no teste —,
a montagem da tabela — inclusive o passo que **não** rodou, porque uma capacidade
que ninguém provou não pode sair do relatório como silêncio — e a escolha da AMI
pela arquitetura do tipo pedido, que é onde o `x86_64` do `experiment.toml` e o
`amd64` do arquivo de infra se encontram. O laço e os dois `docker run` são
escritos direto (ADR-0022).

**O primeiro preflight real é a hora de capturar os payloads da AWS CLI.** As
fixtures do adaptador em `conftest.py` — `run-instances`, `describe-instances`,
`list-objects-v2`, `get-parameter`, `get-caller-identity` — são escritas à mão no
formato documentado, e a ADR-0022 pede a âncora real. Rodando o preflight, o
pesquisador captura a saída de cada um desses comandos (`aws ec2 run-instances`,
`aws ec2 describe-instances`, `aws s3api list-objects-v2`, `aws ssm get-parameter`
— **sem** o valor, que é a chave privada —, `aws sts get-caller-identity`) e
substitui os payloads da factory pelos capturados. É passo manual do pesquisador,
fora do ticket que escreveu o subcomando.

### O `--copy-props` do `s3 sync`

O espelho do passo 5 é uma cópia **S3→S3**, e ali o default da CLI é
`--copy-props default`, que copia **tags** além da metadata — e para isso chama
`GetObjectTagging` na origem mesmo quando não há tag alguma. A matriz da
ADR-0016 não concede `s3:GetObjectTagging`, então o sync sairia com um
`AccessDenied` no pior lugar possível: o último passo, depois de os seis Masters
já estarem no bucket da campanha e a corrida de ~2 h já estar paga.

Por isso o `s3_sync` do adaptador passa `--copy-props metadata-directive`, o
degrau imediatamente abaixo do default: preserva `content-type`,
`content-language`, `content-encoding`, `content-disposition`, `cache-control`,
`--expires` e metadata, e não toca em tag. Conceder `GetObjectTagging` foi
rejeitado — tag em objeto de Master não significa nada neste Experimento, e a
permissão compraria uma capacidade que ninguém quer; `--copy-props none` também
resolveria, mas descartaria a metadata junto. Os três valores válidos na CLI que
a instância instala (`aws-cli/2.36.40`) são `none`, `metadata-directive` e
`default`, e a flag **só se aplica a cópia S3→S3**: o `s3 cp` local↔S3 do resto
do sistema não vê diferença. O argv fica sem teste, como o resto do adaptador
(ADR-0022).
