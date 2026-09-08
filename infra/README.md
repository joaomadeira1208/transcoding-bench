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
| `compute/` | rede, IAM, key pair, parâmetro SSM e orçamento | destruído **no fim da campanha** |

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
profile, o key pair com a privada no SSM e o orçamento. A AZ da subnet não é
escolhida na mão: um data source de ofertas por tipo devolve as zonas de cada
tipo da allowlist, e a subnet fica na primeira zona, em ordem, que oferece
**todos** eles.

Os outputs são o que o user-data das instâncias injeta: `subnet_id`, os dois
security groups, os quatro instance profiles, `key_pair_name`, as três AMIs, os
dois buckets e `ssh_private_key_parameter_name`.

A chave SSH privada **não sai em output**: ela é um SecureString no SSM, lido
pelo Orquestrador no bootstrap e pelo pesquisador quando precisar entrar na
instância.

## `destroy`

Cada root tem o seu, e os tempos de vida não são o mesmo momento:

    # fim da campanha
    terraform -chdir=infra/compute destroy

    # fim do TCC, depois de o dado já ter saído dos buckets
    terraform -chdir=infra/storage destroy

O `destroy` do `storage/` **falha** enquanto os buckets tiverem objetos — não
há `force_destroy`, e isso é deliberado: o comando que apagaria o ground truth
do experimento tem que esbarrar em alguma coisa. Esvaziá-los é um `aws s3 rm
--recursive` explícito, feito depois de conferir que o Parquet consolidado e o
que mais for para o artigo já estão fora.
