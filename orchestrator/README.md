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
sobre o que é um arquivo válido. Ele cobre os campos sobre os quais o papel
decide, e não o arquivo inteiro: `schema_version`, `scenario_id`, `warmup`,
`exit_code`, `run_id`, `started_at` e `commit` — os dois últimos entraram com a
retomada, que desempata a dedup pelo `run_id` e compara o `commit` com o
`--exclude-commit`.

O `status_check.py` é o lado leitor do contrato de `status/` que o
`encode/README.md` documenta campo a campo: `check_done_marker` e
`check_progress` recebem o JSON já parseado e o `instance_id` da instância que
este lançamento subiu, conferem tipo exato em cada campo e devolvem o registro
ou `None`. `None` é "de outra instância", e é um resultado: numa retomada os
objetos da tentativa anterior continuam no bucket, porque o `DeleteObject` da
ADR-0016 não alcança `status/`, e é a identidade que os torna inertes — presença
não é sinal. Campo ausente ou de tipo errado é recusado nomeando o campo, e
recusado **antes** da comparação de identidade: o `instance_id` pelo qual se
compararia é um dos campos a conferir.

Quem **nomeia** os dois objetos é a entrada rastreada, pelo papel dela, e não o
`instance_type` solto: o `StatusKeys.of` devolve `status/{instance_type}_done` e
`status/{instance_type}_progress` para uma entrada `encode`, e `status/judge_done`
— a chave que a ADR-0011 fixou para o Juiz — mais `status/judge_progress` para o
Juiz. O laço pergunta o leitor e o renderizador do progresso à entrada pela
mesma razão, e não ao `status_check` direto: hoje há um leitor só, e o do objeto
que o Juiz escreve entra sem um ramo por papel no poll.

O `progress_line` é a linha por arquitetura que o `run` e o `watch` imprimem a
cada poll, renderizada do objeto mais o **total de runs da fatia**, que é
argumento e nunca sai do objeto — a Instância sabe quantos runs fez, e só o
Orquestrador sabe quantos ela recebeu, porque foi ele quem subiu a fatia:

    14:32:07 c7g  bloco 4/6  run 3/6  libx265_1080p_720p_tos_c7g_rep2      21/36 runs, 0 falhas, 1h10m
             c7i  sem progresso ainda, 0/36 runs reportados

A hora é a do `written_at`, e não a do poll: uma hora que não anda entre dois
polls é a Instância que parou de reportar. A arquitetura que ainda não escreveu
progresso ganha a segunda linha — as três são lidas lado a lado, e uma que
sumisse seria lida como uma arquitetura que não subiu.

As colunas casam entre as três porque cada índice sai na largura do seu total e
a coluna do `scenario_id` acomoda a maior que a campanha gera, que é a do
warm-up: as três passam os quatro dias em pontos diferentes da mesma sequência, e é o
alinhamento que faz três linhas serem lidas de uma vez às 3 da manhã.

Os leitores conferem os campos pelas mesmas primitivas, que moram no
`field_checks.py` e levantam um `FieldError` que cada um embrulha na sua exceção,
e pelo mesmo laço: o `check_fields` percorre os campos do registro exigindo
presença e tipo e devolve os valores crus a quem sabe montá-lo. "Inteiro exato" e
"ISO-8601 com offset" não são regra de contrato nenhum, e a duplicação que a
ADR-0022 licencia é **entre papéis**: aqui é o mesmo papel e o mesmo venv, e
duplicar não compraria verificação independente de nada.

O `vigilance.py` é a decisão de um poll sobre uma arquitetura (D2/D8/D9 da
Spec 4): recebe o que o `describe-instances` disse, o que o `kill -0` no PID
gravado respondeu por SSH, o veredito do `status_check` sobre o marcador e
quantos polls seguidos ficaram sem resposta, e devolve um dos cinco estados —
bootstrapping, rodando, pronta para terminar, morta, sem resposta.

O sexto estado, `finished`, não sai dessa decisão: é o que o laço escreve depois
de o `terminate-instances` de uma arquitetura pronta ter voltado. Ele existe
porque `dead` já significa outra coisa — a arquitetura que morreu antes do
marcador —, e o resumo final e o código de saída distinguem as duas. O que as
une é o `is_standing`, que é o predicado de "ainda fatura": as duas saem do laço,
nenhuma é perguntada de novo, nenhuma é terminada de novo, e nenhuma delas
segura um `run` novo na guarda do arquivo de estado.

A precedência entre as respostas é a decisão inteira. O marcador válido vem antes
de tudo, porque o fim normal é ele com o processo já morto: o `run_all.sh`
escreve o marcador e sai, e perguntar pelo processo primeiro faria toda campanha
bem-sucedida ser lida como morte no meio de um run. Vale qualquer que seja o
`exit_status` que ele carrega (D9) — o trabalho daquela arquitetura acabou, o que
falhou está no `resume.py`, e esperar a última custaria a diferença entre as 63 h
de uma e as 92 h da outra. Vale inclusive contra a instância ausente do
`describe`, que é a única exceção à regra de que ausente é morta: um marcador
válido é o trabalho terminado, e recusá-lo porque o `describe` já esqueceu a
instância mandaria o pesquisador retomar uma fatia completa.

Depois do marcador vem o `describe-instances`: `pending` é bootstrapping,
`running` passa a pergunta adiante, e qualquer outro estado — inclusive a
instância ausente da resposta — é morte. Só então o processo: sem disparo ainda é
bootstrapping, vivo é rodando, morto é morta, e sem resposta é sem resposta até o
`UNANSWERED_POLL_LIMIT`, e morta no poll seguinte.

"Sem disparo ainda" é uma das respostas do `kill -0` porque o bootstrap acontece
com a instância já `running`: são ~25 min de `cloud-init` (as nove corridas do
preflight mediram 19,7 a 24,6 min) em que não há PID pelo qual perguntar, e sem
essa resposta o laço teria de mentir "sem resposta" e abandonar como morta uma
instância que está buildando. É o mesmo intervalo em que o `pid` do arquivo de
estado é nulo.

O progresso não entra na decisão, e é por isso que o `kill -0` existe: um encode
de 4K no x265 leva uma hora, e nesse intervalo o objeto de progresso não muda —
progresso parado com processo vivo é o caso normal. O limite de polls sem
resposta é constante pelo mesmo motivo: com o poll de 5 minutos da ADR-0010, três
polls são ~15 min de silêncio numa instância que o `describe-instances` continua
chamando de `running`; abaixo disso um sshd ocupado viraria morte e a arquitetura
seria abandonada viva, acima a morte de verdade demora a aparecer na tela.

O `campaign_state.py` é o arquivo de estado (D12), o JSON no work dir do
Orquestrador: o bucket, o caminho da definição, o SHA e as chaves das fatias que
este lançamento subiu e, por entrada, o papel — `encode` ou `judge` —, o
`instance_id`, o `instance_type`, o PID remoto, os totais de blocos e de runs da
fatia e o estado corrente, que é o do `vigilance.py`. O `run` o escreve antes do
primeiro lançamento — quando as fatias já subiram e nenhuma arquitetura está de
pé, e é por isso que as chaves são de topo e a lista de arquiteturas nasce
vazia — e o reescreve a cada mudança de estado; o `watch` o lê e volta ao mesmo
laço. Mora ao lado do arquivo de infra, em `~/work/state.json`, e um `run` novo
o sobrescreve.

O papel é o campo mais novo do arquivo, e o `run` escreve `encode` em toda
arquitetura que lança. Um arquivo escrito antes dele — o `state.json` do piloto,
que é evidência daquele lançamento e não se regenera — é lido como `encode` e
reescrito com o campo.

O `total_timeout` está no arquivo porque o prazo do Orquestrador sai dele (D10):
sem esse campo, o `watch` retomado teria de recebê-lo por flag, e uma flag que o
pesquisador tem de lembrar de repetir às 4 da manhã é uma campanha vigiada com um
prazo diferente do que ela prometeu. O `outcome` é o marcador que encerrou aquela
fatia, guardado inteiro no poll em que ele apareceu, e nulo até lá: ele é o que
diz quantos runs a arquitetura fez, quantos falharam e se o teto disparou, e sem
ele um `watch` que retomasse depois de a `c7g` já ter terminado sairia com
status zero sobre uma fatia com falhas. Ele é conferido pelo `status_check` na
leitura, identidade inclusive: um marcador de outra instância guardado ali daria
por completa uma fatia que não rodou.

O `pid` é o único campo que aceita nulo, e é o intervalo entre o lançamento e o
disparo; o `instance_id` não aceita, porque é por ele que a vigilância pergunta e
por ele que ela termina. Nulo é o único valor degenerado que o `pid` aceita: ele
vai para um `kill -0`, onde `0` sinaliza o process group de quem chama e `-1`
todo processo alcançável — os dois respondem vivo para sempre, e a arquitetura
ficaria rodando até o prazo total de D10 estourar. O parse recusa nomeando o
campo com o índice da arquitetura (`instances[1].instance_id`), pelas primitivas
do `field_checks.py`, e a serialização é determinística como a do plano: o
arquivo é reescrito a cada mudança, e o `diff` entre duas versões tem de mostrar
só o que mudou.

O `campaign_launch.py` é o núcleo puro do `run` (D7, D13 e D18): a guarda sobre
a listagem de `runs/`, a projeção do que sobe e de quem é lançado — com e sem
`--slices` — e a decisão depois dos três bootstraps. As três recebem dado já
buscado (a lista de `S3Object`, as fatias já parseadas, o status de cada espera)
e devolvem dado, e são o que ganha teste; o comando de disparo mora no mesmo
módulo, é argv e fica sem teste, como o `prepare_masters_command` (ADR-0022). O
que cada uma decide está na seção do `run`, abaixo.

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

O `instance_launch.py` é a casca que junta as duas metades para subir uma
Instância de encode: a AMI pela `arch` que o registro `[[instance]]` declara — é
ali que o `x86_64` do `experiment.toml` e o `amd64` do arquivo de infra se
encontram —, o user-data fino com o SHA, o `run-instances` com o perfil `encode`,
200 GB gp3 e hop limit 2, e a espera por `running` mais `cloud-init`. Registro,
bucket, chave da fatia, volume e tags são argumentos, de modo que subir uma
instância e subir uma por arquitetura sejam a mesma chamada repetida. A escolha
da AMI e as três tags são funções puras, e é nelas que os testes batem — a tag
`role=encode` porque é ela que torna a instância terminável pela policy da
ADR-0016, e o `Name` porque é ele que separa as três numa listagem de órfãos.

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
junto. São quatro:

    python orchestrator/orchestrator.py --infra ~/work/infra.json prepare-masters
    python orchestrator/orchestrator.py --infra ~/work/infra.json preflight \
        --instance-type c7g.xlarge
    python orchestrator/orchestrator.py --infra ~/work/infra.json run \
        --config config/pilot.toml --bucket <piloto>
    python orchestrator/orchestrator.py --infra ~/work/infra.json watch
    python orchestrator/orchestrator.py --infra ~/work/infra.json watch --abort

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
fatia, Masters validados, os oito eventos de PMU dentro do container e o
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

O `perf stat` do passo 4 é um **encode curto de um Master real**, com o FFmpeg da
própria imagem e no container invocado com os mesmos mounts e a mesma capability
do `launch_container.sh`: o degrau só prova o que roda pelo caminho da campanha, e
um `-- true` que termina em microssegundos não dá tempo de o rodízio da PMU girar
uma volta nem de um contador que responde zero provar coisa alguma (ADR-0006). O
Master, o encoder e a geometria saem do **primeiro run da fatia que o passo acabou
de subir**, truncados a poucos segundos de vídeo e sem output.

O `-e` vem pronto da definição validada, com os pares entre chaves
(`{cycles,instructions},...`) — nunca uma lista transcrita no código, porque trocar
um par no `config/experiment.toml` tem de mudar o que o preflight confere. O
agrupamento é o que faz o `perf` escalonar cada par de forma atômica: mesmo que o
par só veja um terço da execução, os dois membros veem **o mesmo** terço, e a razão
que o artigo reporta continua correta.

O probe roda com `-vv`, e o passo **guarda a saída crua**: o stdout (o
`perf stat -j` inteiro) e o stderr (o dump do `perf_event_attr` de cada evento, com
o `config` nativo que o nome genérico resolveu naquela arquitetura) vão para o log
do Orquestrador e para `runs/preflight/<instance-id>/`, sem apagar — ao contrário
dos dois objetos de prova, que somem, porque o deles é prova de permissão e este é
dado. A saída é guardada **antes** de o veredito ser tomado: a evidência do passo
que reprova é a que importa.

O veredito é sobre o **valor**, não sobre o código de saída: um evento indisponível
naquela PMU não faz o `perf` falhar (ADR-0006), ele reporta e segue. São três
recusas distintas, cada uma nomeando o evento, porque cada uma pede uma decisão
diferente — `<not supported>` (o evento não existe na PMU), `<not counted>` (o
contador abriu e nunca rodou) e **zero em evento de hardware** (o contador respondeu
e não contou). Os quatro eventos de software ficam fora da regra do zero:
`context-switches = 0` é resultado legítimo. Junto vêm as três checagens de
plausibilidade, uma por métrica da ADR-0006, contra o `max_ratio` que o TOML
declara — é o que separa "o contador respondeu" de "o contador mediu", e o que
teria reprovado o c7a da primeira rodada. A recusa junta **todos** os eventos sem
medição, não só o primeiro, porque a próxima tentativa custa outra instância.

Passando, a linha lista os oito valores **com o `pcnt-running` de cada um** — a
fração do tempo em que aquele contador esteve rodando. Abaixo de 100 **não é
recusa**: com os pares, é o regime esperado onde a PMU tem menos contadores que
eventos. É o que o pesquisador lê para saber se o número é contagem ou estimativa,
e se as três arquiteturas estão no mesmo regime.

**Não há dispensa por arquitetura.** Um evento de hardware zerado, uma
plausibilidade reprovada com contador a 100%, ou um par de cache que o `-vv` mostre
não ser o mesmo nível nas três arquiteturas é decisão de desenho experimental sobre
a ADR-0006, tomada antes do piloto — nunca uma exceção na guarda.

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
`perf` — função pura sobre o texto parseado, e sobre a definição que a
configuração real declara, nunca sobre uma cópia dela escrita no teste, com as
três recusas exercidas evento a evento e as três plausibilidades métrica a
métrica —, o `-e` que o argv do `perf stat` carrega e o encode que o probe roda
— desenho experimental, e não argv de sistema, que é o que a ADR-0022 deixa sem
teste — e a montagem da tabela, inclusive o passo que **não** rodou, porque uma
capacidade que ninguém provou não pode sair do relatório como silêncio; mais as
funções puras do `instance_launch.py`. O laço e os dois `docker run` são escritos
direto (ADR-0022). Que o leitor daqui e o do `run_scenario.sh` concordam sobre o
mesmo `perf.json` é asserção do `smoke/`.

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

### O `run`

O terceiro subcomando é a campanha inteira — do plano ao resumo final —, e o
piloto atravessa exatamente este caminho com o `pilot.toml` e o bucket do piloto:

    python orchestrator/orchestrator.py --infra ~/work/infra.json run \
        --config config/pilot.toml --bucket <piloto> \
        [--slices <dir>] [--run-timeout <s>] [--total-timeout <s>]

`--config` e `--bucket` são obrigatórios e sem default: o par é o gate humano
entre piloto e campanha, e um default de bucket é o erro da ADR-0011 esperando
para acontecer (D5). Para este subcomando a definição deixa de ser a constante
do `orchestrator.py`: é o `--config` que gera o plano e que dá os registros
`[[instance]]` e a AMI de cada um. Os dois timeouts são as camadas locais da
ADR-0012, com os mesmos defaults do `run_all.sh` (4 h e 120 h), e viajam até ele
pelo `launch_container.sh`; o piloto roda com os mesmos (ADR-0022).

Em passos:

1. **A auto-checagem**, pela mesma função do preflight — `sts`, `buckets` com o
   manifesto no bucket alvo, `ssm`, `git`, `s3-sync` — e a tabela dela. Falha ali
   é saída sem lançamento (D6).
2. **A guarda do arquivo de estado** (D12). Antes de qualquer escrita, o `run`
   lê o `~/work/state.json` que já esteja lá e recusa se ele listar alguma
   arquitetura ainda de pé, nomeando os `instance_id` e mandando rodar
   o `watch --abort` primeiro. O arquivo é a saída de emergência — é *por causa
   dele* que os ids não são caçados no console —, e um `run` novo que o
   sobrescreva deixa o lançamento anterior de pé sem ninguém que saiba os ids.
   A guarda do `runs/` não cobre esse caso: com `--slices` ela nem roda, e sem
   ela só recusa depois da primeira Execução concluída, isto é, depois do
   bootstrap inteiro (~2 h) mais um encode — uma janela inteira em que um
   segundo `run` passaria e zeraria o arquivo. Um arquivo cujas instâncias
   saíram todas do laço — mortas ou terminadas pelo marcador — passa: é o rastro
   de uma campanha encerrada.
3. **A guarda do bucket** (D13). Sem `--slices`, o `run` lista `runs/` do bucket
   e recusa se houver qualquer objeto **fora de `runs/preflight/`**: cobre o
   `pilot.toml` contra o bucket da campanha e a campanha disparada duas vezes.
   A exceção do prefixo é obrigatória, não detalhe: desde o #82 o preflight
   guarda a saída crua do probe em `runs/preflight/<instance-id>/`, sem apagar,
   e a D13 como a spec a escreveu recusaria o primeiro `run` do piloto.
   A comparação é pelo prefixo com a barra, de modo que um
   `runs/preflight-old/` é Execução. Uma listagem truncada conta como
   **povoado**, e não como erro: o `runs/` de uma campanha passa de mil
   objetos, o parser do adaptador recusa truncamento por desenho, e o `run` lê
   essa recusa (`TruncatedListing`) como "já tem campanha". Com `--slices` a
   guarda não se aplica, porque retomar é justamente escrever num bucket
   povoado.
4. **O plano.** Sem `--slices`, gera o canônico da definição e sobe
   `scenarios/canonical.json` e `scenarios/{id}.json` por registro `[[instance]]`.
   Com `--slices <dir>`, sobe só as fatias presentes no diretório — o `--out` do
   `resume.py` —, sobrescrevendo `scenarios/{id}.json`, sem tocar no
   `canonical.json`, e só essas arquiteturas seguem (D18); `--config` continua
   obrigatório, porque é dele que saem o tipo e a AMI de cada id. O id é o nome
   do arquivo, e um que a definição não declare é recusado nomeando os
   declarados — inclusive o `canonical.json` que um `--slices build/scenarios`
   levaria junto. Uma fatia com blocos de outra arquitetura é recusada também:
   `c7g.json` cheia de blocos do c7i lançaria uma `c7g.xlarge` encodando a
   matriz do c7i. Os totais de blocos e de runs de cada arquitetura são contados
   sobre a fatia que sobe, e não sobre a definição, porque na retomada são os da
   fatia reduzida que a linha de progresso divide.
5. **O arquivo de estado** é escrito antes do primeiro lançamento, com as chaves
   das fatias, o `--total-timeout` deste lançamento e nenhuma arquitetura, e
   reescrito a cada instância lançada (com `pid` nulo e estado `bootstrapping`),
   a cada disparo (com o PID e `running`) e a cada mudança que o laço decide.
6. **Uma Instância de encode por arquitetura**, pelo `instance_launch.py`: AMI
   pela `arch`, perfil `encode`, 200 GB gp3, hop limit 2, e as tags `role`,
   `commit` e `Name=transcoding-bench-encode-{id}` (D14).
7. **A espera pelos três `cloud-init`**, uma de cada vez. **Qualquer** erro ou
   timeout para a espera ali, termina tudo o que subiu — inclusive a que ainda
   não foi esperada, e o relatório a nomeia como tal — e sai com erro antes de
   qualquer disparo (D7): um build quebrado é `Dockerfile`, e `Dockerfile` é outra
   campanha (ADR-0021). A mesma regra vale para o que falhar entre o primeiro
   lançamento e o último bootstrap, um `run-instances` recusado na segunda
   arquitetura inclusive. As terminadas ficam no arquivo de estado como mortas.
8. **O disparo desacoplado** (D1): por SSH, o `launch_container.sh` sob
   `setsid`/`nohup`, stdin de `/dev/null`, stdout e stderr em
   `~/work/launch_container.log` da instância, com os dois timeouts; o comando
   ecoa o PID, que vai para `~/work/launch_container.pid` lá e para o arquivo de
   estado aqui. O `ssh` volta em segundos, e é o `parse_dispatched_pid` que
   recusa um PID que não seja um inteiro positivo — o `0` e o `-1` são os que o
   `kill -0` lê como "vivo para sempre". O `launch_container.sh` termina em `exec
   sudo docker run`, de modo que o PID gravado passa a ser de um processo de
   root: quem perguntar por ele com `kill -0` como `ubuntu` recebe `EPERM`, e a
   pergunta do `watch` tem de ser `sudo kill -0` ou `ps -p`.
9. **A vigilância**, no mesmo processo e com o mesmo laço do `watch`, abaixo.
   A partir do primeiro `ssh` de disparo a regra inverte: nenhuma falha desta
   fase termina instância alguma por si, e uma falha antes do laço sai com erro
   dizendo que o `watch --abort` termina todas. O `run` só volta quando as três
   saíram do laço, ou quando o prazo estoura, e o código de saída dele é o
   veredito sobre o que elas deixaram.

O que ganha teste é o núcleo do `campaign_launch.py`: a guarda sobre a listagem
(vazia, só com o rastro do preflight, com Execução, truncada), a guarda sobre o
arquivo de estado (sem arquitetura, todas mortas, e cada estado de pé recusado
nomeando o id), a projeção do `--slices` (quais chaves sobem, quais arquiteturas
são lançadas, `canonical.json` intocado, id não declarado e fatia de outra
arquitetura recusados) e a decisão de abortar tudo sobre os três status de
bootstrap; mais o parser do PID no `command_output.py`. Laço, comandos remotos
e renderização são escritos direto (ADR-0022).

### O `watch` e o laço de vigilância

O quarto subcomando é o mesmo laço do `run`, começando do arquivo de estado em
vez de começar de um lançamento:

    python orchestrator/orchestrator.py --infra ~/work/infra.json watch

Lê `~/work/state.json`, o valida pelo `campaign_state.py` — campo defeituoso é
recusado pelo nome, e uma vigilância nunca pergunta pela instância errada —,
pula as arquiteturas que o arquivo já dá por terminadas e entra no laço pelas
que sobraram. É isso que torna verdade a propriedade que a ADR-0010 promete: o
`tmux` fechado, a t3.micro reiniciada ou o `run` morto custam o tempo até o
pesquisador reabrir a sessão, e nada mais.

**O poll.** A cada 5 minutos, e por arquitetura ainda de pé, as três perguntas
da D2: o `describe-instances` daquele `instance_id`; o `kill -0` no PID gravado,
por SSH, com teto curto e com **"sem resposta" como resultado**, e não como
exceção — o `sudo` do comando é obrigatório, porque o disparo termina em `exec
sudo docker run` e o PID gravado é de um processo de root; e a listagem de
`status/`, de onde saem o marcador e o objeto de progresso, baixados e lidos
pelo `status_check.py`. A listagem é uma só para as três: são dois objetos por
arquitetura no mesmo prefixo. As respostas vão inteiras para o `decide_vigilance`
e saem como um dos estados acima.

Uma pergunta que **não pôde ser feita** não é resposta: a falha do `describe`,
do download ou da listagem imprime a linha do erro, deixa a arquitetura como
estava e o laço segue. Um `describe-instances` estrangulado uma vez em quatro dias é
exatamente o que não se quer ler como morte às 3 da manhã, e a falha do SSH já
tem lugar próprio na decisão. As outras arquiteturas seguem em qualquer caso
(D8).

**A tela** é uma linha por arquitetura por poll, sempre as três, sempre na mesma
ordem — a das mortas e a das mudas inclusive, porque uma linha que sumisse seria
lida como a arquitetura que nunca subiu. A da que está rodando é a
`progress_line` inteira; qualquer outro estado de pé ganha a mesma linha com a
nota entre colchetes ao fim; e a que já saiu do laço continua na tela com o que
o marcador dela carregava, sem ser perguntada de novo:

    14:32:07 c7g  bloco 4/6  run 3/6  libx265_1080p_720p_tos_c7g_rep2      21/36 runs, 0 falhas, 1h10m
             c7i  sem progresso ainda, 0/36 runs reportados  [bootstrap em curso, ainda sem PID]
             c7a  finished: 36/36 runs, 0 falhas, sem teto, status 0, marcador de 09:12:44

A linha de quem saiu do laço é a mesma que o resumo final imprime: as três
colunas continuam casando, e o que muda entre o penúltimo poll e o resumo é só
quantas arquiteturas ainda têm progresso a mostrar.

**A terminação é por instância, no poll em que ela acaba** (D9): marcador válido
— com falha ou sem, seja qual for o `exit_status` — é `terminate-instances`
naquela hora, e a arquitetura sai do laço como `finished` com o marcador
guardado no arquivo de estado.

Uma arquitetura dada por morta é terminada pelo mesmo motivo e sai como `dead`.
A D8 pede só que ela seja reportada e **não** relançada, e é isso que o laço
faz; terminá-la é a conclusão da mesma conta que a D9 faz, porque uma `xlarge`
que ninguém mais vigia e que o `UNANSWERED_POLL_LIMIT` já deu por abandonada
continuaria faturando as 40 h que faltavam. O risco aceito tem nome: a morte por
silêncio de SSH é um veredito sobre ~15 min sem resposta numa instância que o
`describe-instances` ainda chama de `running`, e se ela estava viva o encode em
curso vai junto. É por isso que o limite é de três polls e não de um, e é por
isso que ele é constante e não flag.

A ordem é terminar e **só então** marcar. Uma arquitetura marcada sobre um
`terminate-instances` que falhou sai do laço e da lista do `watch --abort`, que
é a única coisa que ainda a alcançaria: sobraria uma `xlarge` viva que nenhum
comando termina. Falhou, ela fica de pé no arquivo e o poll seguinte tenta de
novo — a chamada é idempotente. A exceção é o `InvalidInstanceID.NotFound`: aí
não há o que terminar, e insistir seria vigiar uma instância que a API já não
conhece até o prazo estourar.

**O prazo** é o da D10: `--total-timeout` mais o timeout de bootstrap mais uma
margem fixa. O termo do bootstrap é medido, não chutado — as nove corridas do
preflight levaram de 19,7 a 24,6 min do lançamento ao container pronto, e o teto
é o pior caso arredondado para 25 min mais 15 de folga. Ao estourar, o que resta
de pé é terminado e o `run` sai com erro. A aritmética é função pura, e é ela que
ganha teste: um prazo menor que o teto de cada Instância terminaria as três na
véspera do fim, com os quatro dias faturados e nenhum marcador escrito.

O relógio corre **de cada invocação**, e não do lançamento: um `watch` retomado
ganha o prazo inteiro de novo. É deliberado, e o arquivo de estado guarda o
`--total-timeout` justamente para que a conta seja a mesma nas duas pontas. O
que isso não cobre — uma campanha vigiada em três sessões viver mais que um
prazo — já está coberto onde importa: quem tem teto de verdade é o `run_all.sh`
de cada Instância, que é a camada 2 da ADR-0012; o prazo daqui é a garantia de
que o Orquestrador não fica vigiando para sempre, e reabrir o `watch` é o
pesquisador decidindo continuar a vigiar.

**`Ctrl-C` não termina nada** (D11). O SIGINT para só a vigilância e imprime as
duas linhas que importam — o `watch` para voltar e o `watch --abort` para
terminar tudo —, saindo com 130, que é o status que distingue "o pesquisador
parou de olhar" de "a campanha tem pendência". Só o `run` e o `watch` dizem
isso: um `Ctrl-C` no `prepare-masters` ou no `preflight` interrompe um passo que
lança instância e não escreve arquivo de estado nenhum, e a linha de lá manda
conferir no `describe-instances` se a instância daquele passo ficou de pé.

**O resumo e o código de saída.** Ao fim, uma linha por arquitetura com o que o
marcador dela carregava: runs feitos sobre o total da fatia, falhas, teto e
status de saída; a que morreu diz que não tem marcador. O status é zero só se
nenhuma morreu, nenhuma falhou run, nenhuma bateu no teto e o prazo não estourou;
qualquer uma dessas imprime o motivo, o comando do `resume.py` com a definição e
o bucket **deste** lançamento, e o `run --slices` que vem depois dele. A decisão
é função pura e ganha teste pelo mesmo motivo que o prazo: um status zero sobre
uma campanha à qual falta um terço da matriz é a falha que o `resume.py` existe
para ser chamado contra.

O laço, os comandos remotos e os downloads são escritos direto (ADR-0022); o que
ganha teste é o `campaign_watch.py` — o prazo, o veredito final, o resumo e a
linha de cada poll — mais a decisão do `vigilance.py` e o arquivo de estado.

### A saída de emergência: `watch --abort`

    python orchestrator/orchestrator.py --infra ~/work/infra.json watch --abort

Com `--abort` o subcomando não vigia: termina, numa chamada só, todas as
instâncias que o arquivo de estado lista e ainda dá como de pé, marcando-as
mortas em seguida, e sai. É a saída de emergência que não é o console da AWS
(D12). O que já está em `runs/` fica lá, para o `resume.py`. Um arquivo sem
instância de pé é "nada a terminar", com status zero.

## A retomada: `resume.py`

O outro CLI do papel, e o único que **decide sem executar** (ADR-0012). Roda na
instância do Orquestrador, depois de uma campanha que morreu no meio:

    python orchestrator/resume.py --config config/experiment.toml \
        --bucket <campanha> --out ~/work/resume [--exclude-commit <sha>]...

O `--config` é o TOML que gerou o plano daquela campanha — o mesmo par
`--config`/`--bucket` do `run`, pela mesma razão: a completude é medida contra a
matriz que a campanha prometeu rodar, e conferir o bucket do piloto contra o
`experiment.toml` acusaria como pendente tudo o que o piloto nunca teve.

**A entrada é um `s3 sync` filtrado, não uma listagem.** O `s3_sync_run_metas` do
adaptador baixa `runs/*/meta.json` de uma vez para um diretório temporário, e
esse diretório **é** a enumeração das Execuções: o `runs/` de uma campanha passa
de mil objetos e o parser do `list-objects-v2` recusa páginas truncadas por
desenho. Cada arquivo passa pelo `meta_check`, e um inválido derruba a retomada
nomeando a chave no bucket — decidir sobre um `meta.json` é decidir sobre um
bloco, e um `warmup` escrito como string faria um warm-up entrar como
Replicação.

A Execução cujo upload morreu antes do `meta.json` simplesmente não aparece: o
filtro não traz os outros artefatos dela, e sem arquivo nenhum não há diretório
local. É a decisão certa de graça — ela não podia estar completa, e o bloco dela
já cai em pendente por ausência. Diretório de run sem o arquivo, quando aparece,
é ignorado com um aviso no `stderr` em vez de estourar a leitura da árvore
inteira.

**A decisão é por bloco, sobre as Replicações vencedoras.** Dentro de cada
`scenario_id` com `warmup == false` vence o maior `started_at` **comparado como
instante**, com o `run_id` desempatando — a mesma regra do `analysis/`, reescrita
no `resume_plan.py` porque este papel é stdlib-only e não importa o modelo
pydantic de lá. Um bloco é completo quando as suas cinco `scenario_id` de
Replicação têm vencedora, todas com `exit_code == 0`, e nenhuma delas veio de um
commit excluído. O `--exclude-commit` exige o SHA **completo** e recusa qualquer
outra coisa antes de sincronizar: a comparação é de igualdade sobre o campo
`commit`, e a abreviação que o `git log --oneline` mostra sairia com status zero
declarando completos justamente os blocos contaminados. Todo o resto é pendente, com um motivo — `ausente`, `parcial`,
`com falha`, `excluído por commit` —, avaliados nessa ordem, que é o que decide o
rótulo de um bloco que satisfaz mais de um.

**A saída é o relatório e as fatias.** No `stdout`, uma linha por arquitetura com
a contagem de blocos completos e uma linha por bloco pendente com o motivo. Em
`--out`, uma fatia por arquitetura com pendência, com o mesmo nome
(`{id}.json`, vindo da constante do `generate_scenarios` e não de uma cópia
própria) e a mesma forma de topo da fatia original — é a chave
`scenarios/{id}.json` que ela vai sobrescrever, e o `canonical.json` não é
tocado. O relatório **conta** os blocos completos e **nomeia** os pendentes: são
54 blocos por arquitetura na campanha, e listar os completos enterraria os dois
que interessam. Os blocos voltam **inteiros**, warm-up e as cinco Replicações, porque os
`run_id` são cunhados na instância e retomar só as Replicações faltantes rodaria
a frio (ADR-0003/0012). Arquitetura sem pendência não ganha arquivo, e uma
campanha inteira completa é status zero com diretório vazio: "não há o que
retomar" é um resultado. Por isso o `--out` tem de ser um diretório novo a cada
retomada, e o CLI recusa um que já contenha fatias antes de sincronizar coisa
alguma: quem lê o diretório é o `run --slices`, que sobe toda arquitetura
presente nele, e a fatia da retomada anterior mandaria refazer os 54 blocos de
uma arquitetura que desta vez saiu completa. Status 1 é `meta.json` recusado, e 2, configuração
ilegível ou AWS CLI falhando. O comando **não lança instância nenhuma** — quem executa
a fatia reduzida é o `orchestrator.py run --slices`, e o humano entre os dois é o
gate.

A fatia reduzida é projetada pela **mesma** função que gerou a original
(`build_instance_slices`), e não por uma montagem própria: é isso que faz a
retomada de um bucket sem nenhum `meta.json` devolver a fatia original byte a
byte, e é o teste que o afirma sobre os dois planos reais. O `s3_sync_run_metas`
é argv e fica sem teste, como o resto do adaptador; a prova de que a completude é
decidida sobre `meta.json` que o bash de verdade escreveu é a caixa-preta do
`smoke/`.
