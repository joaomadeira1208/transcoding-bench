# Backend de state do Terraform — remoto

O state do Terraform fica em **backend S3 remoto**, com locking nativo (`use_lockfile`, Terraform ≥ 1.10 — sem DynamoDB). O bucket de state é criado **fora-de-banda** (um `aws s3 mb` + versioning, uma vez na mão), não gerenciado por esse mesmo Terraform — isso evita o ovo-galinha, já que o bucket *do experimento* é um recurso criado por esse Terraform.

Remoto, e não local, por dois fatos do contexto que pesam mais que o "um operador, uma máquina":

1. **O tfstate guarda a chave SSH privada em plaintext** (a ADR-0016 usa `tls_private_key`). State local = chave privada num arquivo no disco. Remoto em S3 com SSE = encriptada at rest.
2. **`resume.py` pode dar `terraform apply` de novo** ("se a infra base foi afetada", ADR-0012), numa janela de ~2 dias. Não é estritamente "um apply / um destroy" — há re-aplicações ao longo de um período onde perder o Mac (disco, diretório apagado) deixaria recursos órfãos pra caçar e deletar na mão, pagando até achar.

## Considered Options

- **State local** — rejeitado: ponto único de falha sobre uma janela de 2 dias com re-applies; chave privada em plaintext no disco; sem versionamento pra rollback de um apply corrompido. O ovo-galinha do backend é resolvido com um bucket de state criado à parte, e o locking nativo (TF ≥ 1.10) elimina a necessidade de DynamoDB — então o custo que justificaria "local" praticamente sumiu.

## Consequences

- Existe um bucket de state separado do bucket do experimento, criado manualmente e fora do ciclo `apply`/`destroy`.
- O `destroy` pode ser feito de qualquer máquina (o state não está preso ao Mac), reduzindo o risco de recursos órfãos.
- A versão do provider AWS é pinada pra reprodutibilidade.
