# infra/

Terraform, rodado **só do Mac do pesquisador** (ADR-0017). Um diretório por
root, arquivos planos por preocupação dentro de cada um. O `apply` e o `destroy`
são do pesquisador: o CI verifica formato e validade dos `.tf` (`terraform_fmt`
e `terraform_validate` no pre-commit) e nada mais — nenhum job tem credencial
AWS.

## Os dois roots

| Root | Cria | Tempo de vida |
|---|---|---|
| `storage/` | os dois buckets do experimento (campanha e piloto) | destruído **manualmente, no fim do TCC** (ADR-0011) |
| `compute/` | rede, IAM, key pair, parâmetro SSM, orçamento e a instância do Orquestrador | destruído **no fim da campanha** |

São dois porque os tempos de vida são dois (ADR-0020): os buckets são o ground
truth do experimento e precisam sobreviver ao `destroy` que encerra a campanha.
O `compute/` lê os nomes dos buckets pelo remote state do `storage/`, então a
ordem é fixa: `apply` do storage antes do compute, `destroy` na ordem inversa.

## Toolchain

O binário `terraform` tem que ser exatamente a versão que o CI pina em
`.github/workflows/ci.yml` e que o `required_version` de cada root exige — as
duas são a mesma, e é lá que ela mora. Pelo asdf, com ela no lugar de `<versão>`:

    asdf plugin add terraform
    asdf install terraform <versão>
    asdf set --home terraform <versão>

Sem o `asdf set`, o shim existe mas não resolve, e o `pre-commit` falha nos dois
hooks de Terraform — o binário instalado não basta, é preciso selecioná-lo.

O `.terraform.lock.hcl` de cada root é commitado, com os hashes de
`darwin_arm64`, `linux_amd64` e `linux_arm64` — o pin do provider que a ADR-0020
pede. Ele entra no repositório por uma exceção nomeada da allowlist do
`.gitignore` (ADR-0017); `.terraform/`, `*.tfstate` e `*.tfvars` continuam fora.

## Antes do primeiro `init`: o bucket de state

O state fica em S3 remoto, e o bucket dele é criado **fora de banda** — uma vez,
na mão, fora do ciclo `apply`/`destroy` de qualquer root (ADR-0020). O nome é
seu (nome de bucket é global); em `us-east-1`, com versionamento para poder
voltar um state corrompido:

    aws s3api create-bucket --bucket <state-bucket> --region us-east-1
    aws s3api put-bucket-versioning --bucket <state-bucket> \
        --versioning-configuration Status=Enabled
    aws s3api put-public-access-block --bucket <state-bucket> \
        --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

Esse nome **não está no repositório**, que é público: o bloco de backend é
parcial (fixa região, chave, `encrypt` e `use_lockfile`) e o nome chega por
`-backend-config` a cada `init`. O `encrypt` e o versionamento acima são o que a
ADR-0020 pede do state: ele guarda a chave SSH privada em plaintext, então tem
que estar encriptado at rest e tem que dar para voltar uma versão.

## Variáveis

`storage/` tem uma:

| Variável | Default | O que é |
|---|---|---|
| `bucket_prefix` | `transcoding-bench` | primeiro segmento do nome dos dois buckets |

Os nomes saem de `<bucket_prefix>-<account_id>-campaign` e
`<bucket_prefix>-<account_id>-pilot`, com o account id vindo do
`aws_caller_identity` — o default serve como está, e só precisa mudar se colidir
com um bucket seu.

`compute/` tem sete. As três sem default têm que chegar em todo `plan` e todo
`apply`, por `TF_VAR_*` ou por um `terraform.tfvars` local (que a allowlist do
`.gitignore` mantém fora do repositório):

| Variável | Default | O que é |
|---|---|---|
| `state_bucket` | — | o bucket de state; o mesmo nome que o `-backend-config` do `init` recebe |
| `researcher_ssh_cidr` | — | de onde a porta 22 do Orquestrador aceita conexão; o seu IP público em `/32` |
| `budget_notification_email` | — | quem recebe os dois alertas do orçamento (ADR-0012) |
| `allowed_instance_types` | `["c7g.xlarge", "c7i.xlarge", "c7a.xlarge", "t3.micro"]` | os tipos que o Orquestrador pode lançar (ADR-0016); o tipo do Juiz entra aqui quando a spec do Pass o fixar |
| `orchestrator_ami_id` | `ami-025d99823a4caad37` | Ubuntu 24.04 LTS amd64 do Orquestrador |
| `encode_amd64_ami_id` | `ami-025d99823a4caad37` | Ubuntu 24.04 LTS amd64 das efêmeras c7i e c7a |
| `encode_arm64_ami_id` | `ami-0246d714afcc1d494` | Ubuntu 24.04 LTS arm64 da efêmera c7g e da preparação dos Masters |

O `state_bucket` aparece duas vezes porque o backend parcial e o
`terraform_remote_state` do `storage/` são configurados por caminhos diferentes:
o primeiro pelo `-backend-config` do `init`, o segundo por variável.

O `researcher_ssh_cidr` recusa `0.0.0.0/0` — a porta 22 restrita é o que a
ADR-0015 pede da única camada de rede que as instâncias têm. O IP de casa muda;
quando mudar, é reaplicar a variável.

As três AMIs são **ids literais**, resolvidos uma vez do parâmetro público da
Canonical em 2026-09-08 e datados na descrição de cada variável. Resolvê-las a
cada `apply` foi rejeitado (ADR-0015, D3 da spec): um `apply` de retomada
trocaria o kernel das instâncias de encode no meio da campanha. Para resolver
uma versão nova, quando for hora de trocar de propósito:

    aws ssm get-parameters --region us-east-1 \
        --names /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
        --query 'Parameters[0].Value' --output text

## `storage/`: apply

    terraform -chdir=infra/storage init -backend-config="bucket=<state-bucket>"
    terraform -chdir=infra/storage plan
    terraform -chdir=infra/storage apply

Os outputs são os dois nomes e os dois ARNs (`campaign_bucket_name`,
`campaign_bucket_arn`, `pilot_bucket_name`, `pilot_bucket_arn`). É por eles que
o `compute/` e, depois, os argumentos `--bucket` dos scripts descobrem para onde
escrever.

Os buckets nascem **vazios**: nenhum objeto do layout de prefixos da ADR-0011 é
criado pelo Terraform, os prefixos aparecem no primeiro upload. Não têm
versionamento (a dedup do experimento é lógica, por `scenario_id`) e não têm
`force_destroy`.

## `compute/`: apply

Depois do `storage/`, e nunca antes — o `compute/` lê os nomes e os ARNs dos
buckets pelo remote state dele:

    terraform -chdir=infra/compute init -backend-config="bucket=<state-bucket>"
    terraform -chdir=infra/compute plan
    terraform -chdir=infra/compute apply

Sobem a VPC com uma subnet pública, o internet gateway, a route table, o
gateway endpoint de S3, os dois security groups, as quatro roles com instance
profile, o key pair com a privada no SSM, o orçamento e a **instância do
Orquestrador**. A AZ da subnet não é escolhida na mão: um data source de ofertas
por tipo devolve as zonas de cada tipo da allowlist, e a subnet fica na primeira
zona, em ordem, que oferece **todos** eles.

Os outputs são `orchestrator_public_ip` — o endereço por onde se entra na
instância — mais o que o user-data dela injeta: `subnet_id`, os dois security
groups, os quatro instance profiles, `key_pair_name`, as três AMIs, os dois
buckets e `ssh_private_key_parameter_name`.

**A partir deste `apply` a conta passa a ter custo recorrente**: a t3.micro
fatura continuamente, com o volume de 16 GB, até o `destroy` do `compute/`. É o
orçamento de $150 acima quem a vigia.

## A instância do Orquestrador

t3.micro com AMI amd64 e 16 GB de gp3, no security group do Orquestrador, com o
instance profile `orchestrator` e o key pair do Terraform. Sem Elastic IP: ela
não é parada durante o Experimento, então o IP público que o output imprime vale
por toda a campanha.

O user-data dela é o template compartilhado (`orchestrator/user-data.sh`),
renderizado por `templatefile()` com o SHA do **HEAD do branch padrão no momento
do `apply`**, lido da API do GitHub. Ele clona o repositório em
`/home/ubuntu/transcoding-bench`, dá `checkout` nesse SHA e chama o
`orchestrator/bootstrap.sh`, que instala o AWS CLI v2, `tmux`, `jq` e o venv de
runtime, grava `/home/ubuntu/work/infra.json` e escreve a chave privada em
`~/.ssh/transcoding-bench.pem`.

O `user_data` está em `ignore_changes`: o SHA que a instância clonou é história
do boot dela, e depois disso quem troca a versão do código é o pesquisador, por
`checkout` no clone (ADR-0021). Sem o `ignore_changes`, o primeiro `apply` depois
de um push no master recriaria a instância — no meio da campanha isso mata o
`tmux` e deixa as efêmeras órfãs.

## A chave privada, e como entrar

A chave é um SecureString no SSM, e é o mesmo par que a instância usa para falar
com as efêmeras. Do Mac, uma vez:

    aws ssm get-parameter --region us-east-1 \
        --name /transcoding-bench/orchestrator/ssh-private-key \
        --with-decryption --query 'Parameter.Value' --output text \
        > ~/.ssh/transcoding-bench.pem
    chmod 600 ~/.ssh/transcoding-bench.pem

O nome do parâmetro é o output `ssh_private_key_parameter_name`. O arquivo é
`.pem` e a allowlist do `.gitignore` nunca o admitiria no repositório — mas o
caminho acima é fora dele de qualquer forma.

    ssh -i ~/.ssh/transcoding-bench.pem \
        ubuntu@"$(terraform -chdir=infra/compute output -raw orchestrator_public_ip)"

A conexão só fecha do `researcher_ssh_cidr`; se o IP de casa mudou, é reaplicar a
variável antes. E o bootstrap leva alguns minutos: até ele terminar, o SSH pode
recusar conexão. Na instância, o que confere que o provisionamento foi inteiro:

    cloud-init status --wait          # `done`; o log é /var/log/cloud-init-output.log
    cat /home/ubuntu/work/infra.json  # o arquivo de infra, completo
    /home/ubuntu/transcoding-bench/.venv/bin/python -V
    aws sts get-caller-identity       # responde pelo instance profile

## Trocar o SHA da campanha

O `apply` fixa o SHA só do boot. Daí em diante a versão do código que a campanha
roda é a do clone da instância — é ele que o Orquestrador lê com `git rev-parse
HEAD` e passa às efêmeras (ADR-0021):

    cd /home/ubuntu/transcoding-bench
    git fetch origin
    git checkout <sha>

O caminho é sempre commit → push → `fetch` + `checkout` aqui → relançar: editar o
working tree da instância sem commitar não afeta instância nenhuma.

A chave SSH privada **não sai em output**: ela é um SecureString no SSM, lido
pelo Orquestrador no bootstrap e pelo pesquisador quando precisar entrar na
instância.

## `destroy`

Cada root tem o seu, e os tempos de vida não são o mesmo momento:

    # fim da campanha
    terraform -chdir=infra/compute destroy

O do `compute/` derruba a instância do Orquestrador junto com a rede e o resto, e
é ele que encerra o custo recorrente. Instância efêmera que tenha ficado de pé
não é dele: quem as termina é o Orquestrador, e uma órfã aparece na lista do
`describe-instances` pela tag `role`.

    # fim do TCC, depois de o dado já ter saído dos buckets
    terraform -chdir=infra/storage destroy

O `destroy` do `storage/` **falha** enquanto os buckets tiverem objetos — não
há `force_destroy`, e isso é deliberado: o comando que apagaria o ground truth
do experimento tem que esbarrar em alguma coisa. Esvaziá-los é um `aws s3 rm
--recursive` explícito, feito depois de conferir que o Parquet consolidado e o
que mais for para o artigo já estão fora.
