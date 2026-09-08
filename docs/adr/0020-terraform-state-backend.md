# Backend de state do Terraform — remoto

O state do Terraform fica em **backend S3 remoto**, com locking nativo (`use_lockfile`, Terraform ≥ 1.10 — sem DynamoDB). O bucket de state é criado **fora-de-banda** (um `aws s3 mb` + versioning, uma vez na mão), não gerenciado por esse mesmo Terraform — isso evita o ovo-galinha, já que o bucket *do experimento* é um recurso criado por esse Terraform.

Remoto, e não local, por dois fatos do contexto que pesam mais que o "um operador, uma máquina":

1. **O tfstate guarda a chave SSH privada em plaintext** (a ADR-0016 usa `tls_private_key`). State local = chave privada num arquivo no disco. Remoto em S3 com SSE = encriptada at rest.
2. **`resume.py` pode dar `terraform apply` de novo** ("se a infra base foi afetada", ADR-0012), numa janela de ~2 dias. Não é estritamente "um apply / um destroy" — há re-aplicações ao longo de um período onde perder o Mac (disco, diretório apagado) deixaria recursos órfãos pra caçar e deletar na mão, pagando até achar.

**Emenda: são dois roots, com duas chaves no mesmo bucket de state.** `infra/storage` cria os dois buckets do experimento (ADR-0011) e nada mais; `infra/compute` cria a rede, o IAM, o key pair, o parâmetro SSM, o orçamento e a instância do Orquestrador, e lê os nomes dos buckets pelo remote state do primeiro. As chaves são `storage/terraform.tfstate` e `compute/terraform.tfstate`, no mesmo bucket fora-de-banda, com o mesmo locking.

A separação é de **tempo de vida**, não de tamanho. O `destroy` do fim da campanha é do compute; o do storage é o "manualmente, no fim do TCC" da ADR-0011 — e os dois buckets são o ground truth do experimento, os únicos artefatos que sobrevivem a ela. Num root só, o `terraform destroy` que encerra a campanha levaria junto o dado que ela produziu; e a defesa disponível, um `prevent_destroy` nos buckets, não protege o bucket — bloqueia o `destroy` inteiro, deixando o pesquisador a remover instâncias na mão, que é exatamente o modo de falha ("recursos órfãos pra caçar") que este ADR existe pra evitar. O "um apply, um destroy" da ADR-0009 falava das instâncias efêmeras e continua valendo **dentro** de cada root.

O acoplamento entre os roots é uma via só: o compute lê o storage por `terraform_remote_state`, o storage não sabe que o compute existe. Logo a ordem é fixa — `apply` do storage antes do compute, `destroy` na ordem inversa —, e está no `infra/README.md`.

## Considered Options

- **Um root só, com `prevent_destroy` nos buckets** — rejeitado: `prevent_destroy` não é escopado ao recurso na hora do `destroy`, ele aborta a operação inteira. O `destroy` do fim da campanha passaria a exigir remover as instâncias na mão, ou remover a flag — e sem a flag o mesmo comando leva os dois buckets junto. Dois roots dão a mesma proteção sem transformar o encerramento da campanha num procedimento manual.
- **State local** — rejeitado: ponto único de falha sobre uma janela de 2 dias com re-applies; chave privada em plaintext no disco; sem versionamento pra rollback de um apply corrompido. O ovo-galinha do backend é resolvido com um bucket de state criado à parte, e o locking nativo (TF ≥ 1.10) elimina a necessidade de DynamoDB — então o custo que justificaria "local" praticamente sumiu.

## Consequences

- Existe um bucket de state separado do bucket do experimento, criado manualmente e fora do ciclo `apply`/`destroy`.
- São duas chaves de state (`storage/` e `compute/`) nesse mesmo bucket, e o `init` de cada root recebe o nome dele por `-backend-config` (o bloco de backend é parcial: nome de bucket é global e pessoal, e o repositório é público).
- O `destroy` pode ser feito de qualquer máquina (o state não está preso ao Mac), reduzindo o risco de recursos órfãos.
- A versão do provider AWS é pinada exata, e o `.terraform.lock.hcl` de cada root é commitado pra que o pin valha (a emenda de allowlist da ADR-0017).
