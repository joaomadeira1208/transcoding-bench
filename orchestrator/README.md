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
o valor do `get-parameter`; o status do `cloud-init`.

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

O `ssh_exec` é bloqueante, recebe o comando remoto como argv e o entrega ao shell
da instância já citado por `shlex.join`, que é o que faz um JSON no argv
sobreviver (ADR-0018). Ele leva `ConnectTimeout` e keep-alive de servidor porque
um `ssh` contra um security group que dropa pacotes pendura indefinidamente, e
enquanto ele pendura o argumento `timeout` do laço de espera é mentira; o probe
do `cloud-init` acrescenta a isso um teto de tempo na própria chamada, por estar
dentro do laço. A chave é a que o bootstrap da instância do Orquestrador
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
parâmetro da chave. O parser que confere a forma inteira é do ticket do
`prepare-masters`.
